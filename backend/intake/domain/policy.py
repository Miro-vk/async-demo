"""When the system acts, and when it asks.

Every threshold in the system is in this file. Nothing else compares a confidence
against a number. That is the point: the firm's appetite for automation is a single
diff here, and a reviewer asking "why did this reach me?" gets an answer that cites
a constant they can read.

The thresholds are not guesses. They were set against the real distribution of the
corpus under Claude Opus 5, which is sharply bimodal: extracted fields land either
above 0.88 (75% of them) or below 0.60, with very little in between. The cut sits in
that gap. Classification behaves the same way -- correct labels cluster at 0.94, the
one wrong label came back at 0.45 -- so 0.75 separates them with room on both sides.

Two principles shape the rules:

  A threshold is a statement about consequences, not about the model. A shaky party
  name gates the conflicts check and is held to a higher bar than a shaky
  jurisdiction, which gates nothing automatic. One global number would be easier to
  explain and worse at its job.

  Absence of evidence is not evidence. An empty conflicts list from a check that had
  no names to look up is not a clearance, and is treated as a reason to ask.
"""

from __future__ import annotations

from dataclasses import dataclass

from intake.domain.conflicts import CLIENT_SIDE_ROLES, CONFLICT_RELEVANT_ROLES
from intake.domain.enums import (
    ConflictSeverity,
    MatchMethod,
    DecisionAction,
    EmailClass,
    PracticeArea,
    PartyRole,
    ReviewReasonCode,
    SpanStatus,
    ValidatorStatus,
)
from intake.domain.models import (
    Classification,
    Decision,
    Dispatch,
    Extraction,
    ParseFailure,
    Reason,
    Resolution,
    TracedField,
)


@dataclass(frozen=True)
class Thresholds:
    """The firm's caution dial. Raising these sends more mail to humans."""

    classification: float = 0.75
    """Below this, the label itself is in doubt. Sits in the gap between the
    corpus's wrong label (0.45) and the bottom of its confident cluster (0.80)."""

    party_name: float = 0.70
    """Party names feed the conflicts check, so they are held highest. A name the
    extractor is unsure of produces a conflicts result nobody should rely on."""

    matter_type: float = 0.60
    """Matter type routes the email to an attorney. Getting it wrong wastes a
    lawyer's morning; it does not create an ethical problem."""

    conflict_floor: ConflictSeverity = ConflictSeverity.WEAK
    """Any conflict hit at all reaches a human -- including WEAK. A domain-only
    match cannot tell a client's GC from an employee adverse to their employer,
    and the cost of asking is an email; the cost of guessing is a disqualification."""


DEFAULT = Thresholds()


def decide(
    classification: Classification | ParseFailure | None,
    extraction: Extraction | ParseFailure | None,
    resolution: Resolution | None,
    dispatch: Dispatch | None,
    thresholds: Thresholds = DEFAULT,
) -> Decision:
    """Collect every reason a human should see this, then pick an action.

    Reasons accumulate rather than short-circuit. A reviewer opening the queue
    should see all of what is wrong with an email, not the first thing that
    tripped -- fixing one problem and resurfacing with the next is how review
    queues become hated.
    """
    if not isinstance(classification, Classification):
        return Decision(
            action=DecisionAction.REVIEW,
            reasons=[
                Reason(
                    code=ReviewReasonCode.MODEL_PARSE_FAILURE,
                    message=(
                        f"Classification could not be read: "
                        f"{classification.reason if classification else 'stage did not run'}."
                    ),
                    field_path="classification",
                )
            ],
        )

    label = classification.label.value
    reasons = _classification_reasons(classification, thresholds)

    # A solicitation the classifier is sure about has no parties, no matter type
    # and no jurisdiction, and that is correct rather than deficient. Running the
    # extraction checks over it would bury real review items under spam.
    confident_spam = (
        label == EmailClass.VENDOR_OR_SPAM
        and classification.label.confidence >= thresholds.classification
    )

    if not confident_spam:
        reasons += _extraction_reasons(extraction, label, resolution, thresholds)
        reasons += _conflict_reasons(resolution, label)
        reasons += _dispatch_reasons(dispatch, label)

    if reasons:
        return Decision(action=DecisionAction.REVIEW, reasons=reasons)
    if label == EmailClass.VENDOR_OR_SPAM:
        return Decision(action=DecisionAction.STOP, reasons=[])
    return Decision(action=DecisionAction.PROCEED, reasons=[])


# --------------------------------------------------------------------------- #
# Stage 1
# --------------------------------------------------------------------------- #


def _classification_reasons(
    classification: Classification, t: Thresholds
) -> list[Reason]:
    reasons: list[Reason] = []
    field = classification.label

    if field.value == EmailClass.UNCLEAR:
        # Distinct from low confidence: the classifier is confidently reporting
        # that the email fits none of the categories. Both route to a human, and
        # the queue shows them as different problems.
        reasons.append(
            Reason(
                code=ReviewReasonCode.CLASSIFIER_SAID_UNCLEAR,
                message=(
                    "The classifier judged this email to fit none of the intake "
                    "categories. Someone should read it and decide."
                ),
                field_path="classification.label",
            )
        )
    elif field.confidence < t.classification:
        reasons.append(
            Reason(
                code=ReviewReasonCode.LOW_CONFIDENCE_CLASSIFICATION,
                message=(
                    f"Classified as '{field.value.value}' with confidence "
                    f"{field.confidence:.2f}, below the {t.classification:.2f} "
                    f"threshold. {_span_note(field)}"
                ),
                field_path="classification.label",
            )
        )
    return reasons


def _span_note(field: TracedField) -> str:
    if field.span is None:
        return "The model offered no supporting quote."
    if field.span.status == SpanStatus.NOT_FOUND:
        return f"Its supporting quote ({field.span.quote!r}) is not in the email."
    return ""


# --------------------------------------------------------------------------- #
# Stage 2
# --------------------------------------------------------------------------- #


def _extraction_reasons(
    extraction: Extraction | ParseFailure | None,
    label: EmailClass,
    resolution: Resolution | None,
    t: Thresholds,
) -> list[Reason]:
    if not isinstance(extraction, Extraction):
        return [
            Reason(
                code=ReviewReasonCode.MODEL_PARSE_FAILURE,
                message=(
                    f"Extraction could not be read: "
                    f"{extraction.reason if extraction else 'stage did not run'}."
                ),
                field_path="extraction",
            )
        ]

    reasons: list[Reason] = []

    for warning in extraction.parse_warnings:
        reasons.append(
            Reason(
                code=ReviewReasonCode.PARTIAL_PARSE,
                message=f"Part of the extraction was discarded: {warning}",
                field_path="extraction",
            )
        )

    # Every extracted value, checked for evidence and for sanity.
    for path, field in _traced_fields(extraction):
        if field.value is None:
            continue
        if field.span is not None and field.span.status == SpanStatus.NOT_FOUND:
            reasons.append(
                Reason(
                    code=ReviewReasonCode.UNVERIFIED_SPAN,
                    message=(
                        f"{path} = {field.value!r} is supported by a quote that does "
                        f"not appear in the email: {field.span.quote!r}."
                    ),
                    field_path=path,
                )
            )
        if field.validator == ValidatorStatus.FAILED:
            reasons.append(
                Reason(
                    code=ReviewReasonCode.VALIDATOR_FAILED,
                    message=f"{path} = {field.value!r} failed validation: {field.validator_note}",
                    field_path=path,
                )
            )

    reasons += _party_reasons(extraction, label, resolution, t)
    reasons += _matter_type_reasons(extraction, label, t)
    return reasons


def _traced_fields(extraction: Extraction) -> list[tuple[str, TracedField]]:
    fields: list[tuple[str, TracedField]] = [
        ("extraction.matter_type", extraction.matter_type),
        ("extraction.jurisdiction", extraction.jurisdiction),
    ]
    fields += [(f"extraction.parties[{i}].name", p.name) for i, p in enumerate(extraction.parties)]
    fields += [(f"extraction.key_dates[{i}].value", d.value) for i, d in enumerate(extraction.key_dates)]
    fields += [(f"extraction.amounts[{i}].value", a.value) for i, a in enumerate(extraction.amounts)]
    return fields


def _party_reasons(
    extraction: Extraction,
    label: EmailClass,
    resolution: Resolution | None,
    t: Thresholds,
) -> list[Reason]:
    reasons: list[Reason] = []
    named = [p for p in extraction.parties if p.name.value]

    # An inquiry with nobody's name in it cannot be conflict-checked. The sender's
    # address is an acceptable substitute only when it actually matched a client.
    identified_by_sender = bool(resolution and resolution.client_matches)
    client_side = [p for p in named if p.role in CLIENT_SIDE_ROLES]

    if not identified_by_sender and label != EmailClass.VENDOR_OR_SPAM:
        if not named:
            reasons.append(
                Reason(
                    code=ReviewReasonCode.MISSING_REQUIRED_FIELD,
                    message=(
                        "No party was named and the sender matches no client record, "
                        "so there is nobody to run a conflicts check against."
                    ),
                    field_path="extraction.parties",
                )
            )
        elif not client_side:
            # Subtler and more dangerous than naming nobody: the email names people,
            # so the conflicts check runs and comes back clean, but none of them is
            # the person seeking representation. Someone writing on a friend's behalf
            # produces exactly this -- a green result about the wrong party.
            others = ", ".join(f"'{p.name.value}' ({p.role.value})" for p in named)
            reasons.append(
                Reason(
                    code=ReviewReasonCode.MISSING_REQUIRED_FIELD,
                    message=(
                        f"The email names {others}, but none of them is the "
                        f"prospective client. The person who would actually be "
                        f"represented was never identified, so the conflicts check "
                        f"does not cover them and a clean result means nothing."
                    ),
                    field_path="extraction.parties",
                )
            )

    # An exact match on the sender's address identifies the client far more
    # strongly than a name read out of the body. Asking a human to confirm a
    # hesitant name when we already know who wrote in is noise, and noise is what
    # makes a review queue get skimmed.
    identified_outright = bool(
        resolution
        and any(m.method == MatchMethod.EMAIL_ADDRESS for m in resolution.client_matches)
    )

    for index, party in enumerate(named):
        if party.role not in CONFLICT_RELEVANT_ROLES:
            # Not read by any conflict rule, so its confidence gates nothing.
            continue
        if identified_outright and party.role in CONFLICT_RELEVANT_ROLES - {PartyRole.OPPOSING}:
            continue
        if party.name.confidence < t.party_name:
            reasons.append(
                Reason(
                    code=ReviewReasonCode.LOW_CONFIDENCE_FIELD,
                    message=(
                        f"Party '{party.name.value}' ({party.role.value}) was extracted "
                        f"with confidence {party.name.confidence:.2f}, below the "
                        f"{t.party_name:.2f} bar for names that drive a conflicts "
                        f"check. {_span_note(party.name)}"
                    ),
                    field_path=f"extraction.parties[{index}].name",
                )
            )
    return reasons


def _matter_type_reasons(
    extraction: Extraction, label: EmailClass, t: Thresholds
) -> list[Reason]:
    # Only new matters need a practice area read off the text. Existing-client mail
    # inherits it from the matter it references.
    if label != EmailClass.NEW_MATTER:
        return []

    field = extraction.matter_type
    if field.value is None or field.value == PracticeArea.UNKNOWN:
        return [
            Reason(
                code=ReviewReasonCode.AMBIGUOUS_MATTER_TYPE,
                message=(
                    "No practice area could be determined, so the inquiry cannot be "
                    "routed to the right attorney."
                ),
                field_path="extraction.matter_type",
            )
        ]
    if field.confidence < t.matter_type:
        return [
            Reason(
                code=ReviewReasonCode.AMBIGUOUS_MATTER_TYPE,
                message=(
                    f"Practice area read as '{field.value.value}' with confidence "
                    f"{field.confidence:.2f}, below {t.matter_type:.2f}. The facts may "
                    f"fit more than one area. {_span_note(field)}"
                ),
                field_path="extraction.matter_type",
            )
        ]
    return []


# --------------------------------------------------------------------------- #
# Stage 3
# --------------------------------------------------------------------------- #


def _conflict_reasons(resolution: Resolution | None, label: EmailClass) -> list[Reason]:
    if resolution is None:
        return [
            Reason(
                code=ReviewReasonCode.CONFLICT_CHECK_VACUOUS,
                message="The conflicts check did not run.",
                field_path="resolution",
            )
        ]

    reasons = [
        Reason(
            code=ReviewReasonCode.CONFLICT_HIT,
            message=hit.explanation,
            rule_id=hit.rule_id,
            field_path=f"resolution.conflicts[{index}]",
        )
        for index, hit in enumerate(resolution.conflicts)
    ]

    # The distinction this whole stage exists to preserve: nothing found, versus
    # nothing looked for.
    if resolution.is_vacuous and label != EmailClass.VENDOR_OR_SPAM:
        detail = " ".join(resolution.completeness_notes) or "No names were checked."
        reasons.append(
            Reason(
                code=ReviewReasonCode.CONFLICT_CHECK_VACUOUS,
                message=(
                    f"No conflicts were found because no party names were checked. "
                    f"This is not a clearance. {detail}"
                ),
                field_path="resolution",
            )
        )
    return reasons


# --------------------------------------------------------------------------- #
# Stage 4
# --------------------------------------------------------------------------- #


def _dispatch_reasons(dispatch: Dispatch | None, label: EmailClass) -> list[Reason]:
    if label not in (EmailClass.NEW_MATTER, EmailClass.EXISTING_CLIENT):
        return []
    if dispatch is None or dispatch.attorney_id is None:
        detail = dispatch.routing_reason if dispatch else "routing did not run"
        return [
            Reason(
                code=ReviewReasonCode.NO_ATTORNEY_AVAILABLE,
                message=f"No attorney could be assigned: {detail}.",
                field_path="dispatch.attorney_id",
            )
        ]
    return []
