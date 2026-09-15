"""The human's half of the loop.

A reviewer can approve, reject, or -- the case that actually matters in a firm --
correct a field and then approve. An intake clerk who can only bounce the whole
email back is going to stop using the queue by Thursday.

Corrections never overwrite silently. The model's original value is kept on the
edit record, the corrected field is marked as human-asserted rather than
model-extracted, and the span is cleared because a person's correction is not
supported by a quote from the email. An audit trail that loses what the machine
said is not an audit trail.

Approving does not send anything. It moves the draft to APPROVED_NOT_SENT, which
is the last state this system has.
"""

from __future__ import annotations

from datetime import date

from intake.domain.enums import (
    DraftStatus,
    PracticeArea,
    ReviewOutcome,
    ValidatorStatus,
)
from intake.domain.extract import AREA_ALIASES, parse_amount_value, parse_date_value
from intake.domain.models import (
    Extraction,
    FieldEdit,
    PipelineResult,
    ReviewAction,
    TracedField,
)

EDITABLE_PATHS = (
    "extraction.matter_type",
    "extraction.jurisdiction",
    "extraction.parties[*].name",
    "extraction.key_dates[*].value",
    "extraction.amounts[*].value",
)


class UneditableField(ValueError):
    """Raised for a path the reviewer is not allowed to rewrite."""


def apply_review(result: PipelineResult, action: ReviewAction) -> PipelineResult:
    """Record a reviewer's decision and return the updated result."""
    extraction = result.extraction
    if action.outcome == ReviewOutcome.APPROVED_WITH_EDITS:
        if extraction is None:
            raise UneditableField("there is no extraction to edit")
        for edit in action.edits:
            extraction = apply_edit(extraction, edit)

    dispatch = result.dispatch
    if dispatch is not None and dispatch.acknowledgment is not None:
        status = (
            DraftStatus.REJECTED
            if action.outcome == ReviewOutcome.REJECTED
            else DraftStatus.APPROVED_NOT_SENT
        )
        dispatch = dispatch.model_copy(
            update={"acknowledgment": dispatch.acknowledgment.model_copy(update={"status": status})}
        )

    return result.model_copy(
        update={"extraction": extraction, "dispatch": dispatch, "review": action}
    )


def apply_edit(extraction: Extraction, edit: FieldEdit) -> Extraction:
    """Replace one extracted value with a reviewer's correction."""
    path = edit.field_path
    corrected = edit.corrected_value

    if path == "extraction.matter_type":
        area = AREA_ALIASES.get((corrected or "").strip().lower())
        if corrected is not None and area is None:
            raise UneditableField(f"'{corrected}' is not a practice area")
        return extraction.model_copy(
            update={"matter_type": _human_field(PracticeArea, area, edit)}
        )

    if path == "extraction.jurisdiction":
        return extraction.model_copy(
            update={"jurisdiction": _human_field(str, corrected, edit)}
        )

    index, attribute = _indexed(path)

    if attribute == "parties":
        parties = list(extraction.parties)
        party = parties[index]
        parties[index] = party.model_copy(update={"name": _human_field(str, corrected, edit)})
        return extraction.model_copy(update={"parties": parties})

    if attribute == "key_dates":
        value = parse_date_value(corrected)
        if corrected is not None and value is None:
            raise UneditableField(f"'{corrected}' is not a date")
        dates = list(extraction.key_dates)
        dates[index] = dates[index].model_copy(update={"value": _human_field(date, value, edit)})
        return extraction.model_copy(update={"key_dates": dates})

    if attribute == "amounts":
        value = parse_amount_value(corrected)
        if corrected is not None and value is None:
            raise UneditableField(f"'{corrected}' is not an amount")
        amounts = list(extraction.amounts)
        amounts[index] = amounts[index].model_copy(
            update={"value": _human_field(float, value, edit)}
        )
        return extraction.model_copy(update={"amounts": amounts})

    raise UneditableField(f"{path} is not editable; allowed: {', '.join(EDITABLE_PATHS)}")


def _human_field(kind, value, edit: FieldEdit) -> TracedField:
    """A value a person asserted.

    Confidence is 1.0 because a human looked at it, and the span is cleared
    because their correction is not quoted from the email. The note names them, so
    the trace shows a human value as a human value rather than laundering it into
    something the model appears to have said.
    """
    return TracedField[kind](
        value=value,
        confidence=1.0,
        self_reported=None,
        span=None,
        validator=ValidatorStatus.PASSED,
        validator_note=(
            f"corrected by {edit.edited_by} (was {edit.original_value!r})"
        ),
    )


def _indexed(path: str) -> tuple[int, str]:
    try:
        head, rest = path.split("[", 1)
        index_text, _ = rest.split("]", 1)
        return int(index_text), head.split(".")[-1]
    except (ValueError, IndexError) as exc:
        raise UneditableField(f"{path} is not an editable field path") from exc
