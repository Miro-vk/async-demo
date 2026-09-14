"""Stage 2: pull the facts out, with a confidence and a source span on every one.

Same split as classify: the request is built elsewhere, and this module is a pure
function from (email, response text) to typed data.

Two behaviours worth knowing about:

  A missing field is not a failure. If the model returns no jurisdiction, that is a
  jurisdiction with value None, no span, and a low confidence -- which the policy
  stage turns into a review reason. Only a response that cannot be parsed at all
  becomes a ParseFailure.

  A malformed entry inside a list is dropped, never repaired. If one of five parties
  has no name, the other four survive and the drop is recorded in parse_warnings, so
  the trace shows "we discarded one party" rather than silently showing four.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from intake.domain import confidence, spans, validators
from intake.domain.enums import PartyRole, PracticeArea, ValidatorStatus
from intake.domain.models import (
    Email,
    Extraction,
    KeyDate,
    MonetaryAmount,
    ParseFailure,
    Party,
    TracedField,
)
from intake.domain.parsing import as_float, as_str, excerpt_for_failure, parse_model_json

STAGE = "extract"

ROLE_ALIASES = {
    "prospective_client": PartyRole.PROSPECTIVE_CLIENT,
    "prospective client": PartyRole.PROSPECTIVE_CLIENT,
    "client": PartyRole.PROSPECTIVE_CLIENT,
    "inquirer": PartyRole.PROSPECTIVE_CLIENT,
    "opposing": PartyRole.OPPOSING,
    "opposing_party": PartyRole.OPPOSING,
    "adverse": PartyRole.OPPOSING,
    "defendant": PartyRole.OPPOSING,
    "third_party": PartyRole.THIRD_PARTY,
    "third party": PartyRole.THIRD_PARTY,
    "witness": PartyRole.THIRD_PARTY,
}

AREA_ALIASES = {
    "commercial_litigation": PracticeArea.COMMERCIAL_LITIGATION,
    "commercial litigation": PracticeArea.COMMERCIAL_LITIGATION,
    "contract": PracticeArea.COMMERCIAL_LITIGATION,
    "employment": PracticeArea.EMPLOYMENT,
    "labor": PracticeArea.EMPLOYMENT,
    "real_estate": PracticeArea.REAL_ESTATE,
    "real estate": PracticeArea.REAL_ESTATE,
    "property": PracticeArea.REAL_ESTATE,
    "intellectual_property": PracticeArea.INTELLECTUAL_PROPERTY,
    "intellectual property": PracticeArea.INTELLECTUAL_PROPERTY,
    "ip": PracticeArea.INTELLECTUAL_PROPERTY,
    "trademark": PracticeArea.INTELLECTUAL_PROPERTY,
    "family": PracticeArea.FAMILY,
    "divorce": PracticeArea.FAMILY,
    "personal_injury": PracticeArea.PERSONAL_INJURY,
    "personal injury": PracticeArea.PERSONAL_INJURY,
    "unknown": PracticeArea.UNKNOWN,
}

DATE_FORMATS = ["%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%m/%d/%Y", "%d %B %Y", "%d %b %Y"]


# --------------------------------------------------------------------------- #
# Coercion helpers
# --------------------------------------------------------------------------- #


def _field_parts(raw: Any) -> tuple[Any, float | None, str | None]:
    """Unpack {"value":..,"confidence":..,"quote":..}, tolerating a bare scalar.

    A bare scalar is accepted because models do it, but it arrives with no quote,
    which means an ABSENT span and a lower score. Tolerated, not rewarded.
    """
    if isinstance(raw, dict):
        return raw.get("value"), as_float(raw.get("confidence")), as_str(raw.get("quote"))
    return raw, None, None


def parse_date_value(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = as_str(value)
    if text is None:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_amount_value(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = as_str(value)
    if text is None:
        return None
    cleaned = text.replace("$", "").replace(",", "").replace("USD", "").strip()
    multiplier = 1.0
    if cleaned[-1:].lower() == "k":
        multiplier, cleaned = 1_000.0, cleaned[:-1]
    elif cleaned[-1:].lower() == "m":
        multiplier, cleaned = 1_000_000.0, cleaned[:-1]
    try:
        return float(cleaned) * multiplier
    except ValueError:
        return None


def _traced(
    source: str,
    value: Any,
    self_reported: float | None,
    quote: str | None,
    validator: ValidatorStatus,
    note: str | None,
) -> tuple[Any, float, Any, ValidatorStatus, str | None]:
    span = spans.ground_quote(source, quote)
    return (
        value,
        confidence.score(self_reported, span, validator, spans.is_weak(span)),
        span,
        validator,
        note,
    )


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def parse_extraction(email: Email, raw: str) -> Extraction | ParseFailure:
    data, error = parse_model_json(raw)
    if error is not None:
        return ParseFailure(stage=STAGE, reason=error, raw_excerpt=excerpt_for_failure(raw))

    source = email.searchable_text
    reference = email.received_at.date()
    warnings: list[str] = []

    parties = _parse_parties(source, data.get("parties"), warnings)
    key_dates = _parse_key_dates(source, data.get("key_dates"), reference, warnings)
    amounts = _parse_amounts(source, data.get("amounts"), warnings)

    return Extraction(
        email_id=email.id,
        parties=parties,
        matter_type=_parse_matter_type(source, data.get("matter_type")),
        jurisdiction=_parse_jurisdiction(source, data.get("jurisdiction")),
        key_dates=key_dates,
        amounts=amounts,
        summary=as_str(data.get("summary")) or "",
        parse_warnings=warnings,
    )


def _parse_matter_type(source: str, raw: Any) -> TracedField[PracticeArea]:
    value, self_reported, quote = _field_parts(raw)
    text = as_str(value)
    area = AREA_ALIASES.get(text.strip().lower()) if text else None

    if text and area is None:
        validator, note = ValidatorStatus.FAILED, f"'{text}' is not a practice area we handle"
    elif area is None:
        validator, note = ValidatorStatus.NOT_APPLICABLE, None
    else:
        validator, note = ValidatorStatus.PASSED, None

    span = spans.ground_quote(source, quote)
    return TracedField[PracticeArea](
        value=area,
        self_reported=self_reported,
        span=span,
        validator=validator,
        validator_note=note,
        confidence=confidence.score(self_reported, span, validator, spans.is_weak(span)),
    )


def _parse_jurisdiction(source: str, raw: Any) -> TracedField[str]:
    value, self_reported, quote = _field_parts(raw)
    text = as_str(value)
    validator, note = validators.validate_jurisdiction(text)
    span = spans.ground_quote(source, quote)
    return TracedField[str](
        value=text,
        self_reported=self_reported,
        span=span,
        validator=validator,
        validator_note=note,
        confidence=confidence.score(self_reported, span, validator, spans.is_weak(span)),
    )


def _parse_parties(source: str, raw: Any, warnings: list[str]) -> list[Party]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        warnings.append(f"'parties' was {type(raw).__name__}, not a list; ignored")
        return []

    parties: list[Party] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            warnings.append(f"party[{index}] was not an object; dropped")
            continue
        name = as_str(entry.get("name"))
        if name is None:
            warnings.append(f"party[{index}] had no usable name; dropped")
            continue

        validator, note = validators.validate_party_name(name)
        self_reported = as_float(entry.get("confidence"))
        span = spans.ground_quote(source, as_str(entry.get("quote")))
        role = ROLE_ALIASES.get(str(entry.get("role", "")).strip().lower(), PartyRole.UNKNOWN)
        if entry.get("role") is not None and role == PartyRole.UNKNOWN:
            warnings.append(f"party[{index}] had unrecognised role {entry.get('role')!r}")

        parties.append(
            Party(
                name=TracedField[str](
                    value=name,
                    self_reported=self_reported,
                    span=span,
                    validator=validator,
                    validator_note=note,
                    confidence=confidence.score(
                        self_reported, span, validator, spans.is_weak(span)
                    ),
                ),
                role=role,
                email=as_str(entry.get("email")),
                is_organization=bool(entry.get("is_organization", False)),
            )
        )
    return parties


def _parse_key_dates(
    source: str, raw: Any, reference: date, warnings: list[str]
) -> list[KeyDate]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        warnings.append(f"'key_dates' was {type(raw).__name__}, not a list; ignored")
        return []

    dates: list[KeyDate] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            warnings.append(f"key_dates[{index}] was not an object; dropped")
            continue
        parsed = parse_date_value(entry.get("value"))
        if parsed is None:
            warnings.append(
                f"key_dates[{index}] value {entry.get('value')!r} is not a date; dropped"
            )
            continue

        validator, note = validators.validate_date(parsed, reference)
        self_reported = as_float(entry.get("confidence"))
        span = spans.ground_quote(source, as_str(entry.get("quote")))
        dates.append(
            KeyDate(
                label=as_str(entry.get("label")) or "unlabelled",
                value=TracedField[date](
                    value=parsed,
                    self_reported=self_reported,
                    span=span,
                    validator=validator,
                    validator_note=note,
                    confidence=confidence.score(
                        self_reported, span, validator, spans.is_weak(span)
                    ),
                ),
            )
        )
    return dates


def _parse_amounts(source: str, raw: Any, warnings: list[str]) -> list[MonetaryAmount]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        warnings.append(f"'amounts' was {type(raw).__name__}, not a list; ignored")
        return []

    amounts: list[MonetaryAmount] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            warnings.append(f"amounts[{index}] was not an object; dropped")
            continue
        parsed = parse_amount_value(entry.get("value"))
        if parsed is None:
            warnings.append(
                f"amounts[{index}] value {entry.get('value')!r} is not a number; dropped"
            )
            continue

        validator, note = validators.validate_amount(parsed)
        self_reported = as_float(entry.get("confidence"))
        span = spans.ground_quote(source, as_str(entry.get("quote")))
        amounts.append(
            MonetaryAmount(
                label=as_str(entry.get("label")) or "unlabelled",
                currency=as_str(entry.get("currency")) or "USD",
                value=TracedField[float](
                    value=parsed,
                    self_reported=self_reported,
                    span=span,
                    validator=validator,
                    validator_note=note,
                    confidence=confidence.score(
                        self_reported, span, validator, spans.is_weak(span)
                    ),
                ),
            )
        )
    return amounts
