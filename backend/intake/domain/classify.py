"""Stage 1: what kind of email is this?

Split in two on purpose. `build_classification_request` describes what to ask;
`parse_classification` turns the answer into typed data. Only the second half is
domain logic, and it is a pure function of (email, response text) -- testable with a
fixture string, no network, no API key, no mocking.
"""

from __future__ import annotations

from intake.domain import confidence, spans
from intake.domain.enums import EmailClass, ValidatorStatus
from intake.domain.models import Classification, Email, ParseFailure, TracedField
from intake.domain.parsing import as_float, as_str, excerpt_for_failure, parse_model_json

STAGE = "classify"

LABEL_ALIASES = {
    "new matter": EmailClass.NEW_MATTER,
    "new_matter": EmailClass.NEW_MATTER,
    "newmatter": EmailClass.NEW_MATTER,
    "existing client": EmailClass.EXISTING_CLIENT,
    "existing_client": EmailClass.EXISTING_CLIENT,
    "vendor": EmailClass.VENDOR_OR_SPAM,
    "spam": EmailClass.VENDOR_OR_SPAM,
    "vendor_or_spam": EmailClass.VENDOR_OR_SPAM,
    "vendor or spam": EmailClass.VENDOR_OR_SPAM,
    "unclear": EmailClass.UNCLEAR,
    "unknown": EmailClass.UNCLEAR,
}


def coerce_label(value: object) -> EmailClass | None:
    """Accept the label spellings a model actually produces.

    Note what this does not do: it does not map an unrecognised label onto
    UNCLEAR. UNCLEAR is a judgement the classifier makes, and quietly substituting
    it for "the model said something we don't understand" would blur the one
    distinction this system is built to keep sharp.
    """
    if not isinstance(value, str):
        return None
    return LABEL_ALIASES.get(value.strip().lower())


def parse_classification(email: Email, raw: str) -> Classification | ParseFailure:
    data, error = parse_model_json(raw)
    if error is not None:
        return ParseFailure(stage=STAGE, reason=error, raw_excerpt=excerpt_for_failure(raw))

    label = coerce_label(data.get("label"))
    if label is None:
        return ParseFailure(
            stage=STAGE,
            reason=f"unrecognised label {data.get('label')!r}",
            raw_excerpt=excerpt_for_failure(raw),
        )

    span = spans.ground_quote(email.searchable_text, as_str(data.get("quote")))
    self_reported = as_float(data.get("confidence"))

    return Classification(
        email_id=email.id,
        label=TracedField[EmailClass](
            value=label,
            self_reported=_clamp(self_reported),
            span=span,
            validator=ValidatorStatus.NOT_APPLICABLE,
            confidence=confidence.score(
                self_reported, span, ValidatorStatus.NOT_APPLICABLE, spans.is_weak(span)
            ),
        ),
        rationale=as_str(data.get("rationale")) or "",
    )


def _clamp(value: float | None) -> float | None:
    if value is None:
        return None
    return max(0.0, min(1.0, value))
