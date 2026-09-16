"""The human's half: approve, reject, and correct a field then approve."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from intake.domain.enums import (
    DecisionAction,
    DraftStatus,
    PartyRole,
    PracticeArea,
    ReviewOutcome,
    SpanStatus,
    ValidatorStatus,
)
from intake.domain.models import (
    AcknowledgmentDraft,
    Decision,
    Dispatch,
    Extraction,
    FieldEdit,
    KeyDate,
    MonetaryAmount,
    Party,
    PipelineResult,
    ReviewAction,
    Span,
    TracedField,
)
from intake.domain.review import UneditableField, apply_edit, apply_review

NOW = datetime(2026, 3, 2, 11, 0)


def extraction() -> Extraction:
    return Extraction(
        email_id="em-1",
        parties=[Party(
            name=TracedField[str](
                value="Delphine Winslo", confidence=0.55, self_reported=0.55,
                span=Span(start=0, end=15, quote="Delphine Winslo", status=SpanStatus.VERIFIED),
            ),
            role=PartyRole.PROSPECTIVE_CLIENT,
        )],
        matter_type=TracedField[PracticeArea](value=PracticeArea.UNKNOWN, confidence=0.3),
        jurisdiction=TracedField[str](value="Wakanda", confidence=0.2,
                                      validator=ValidatorStatus.FAILED),
        key_dates=[KeyDate(label="closing",
                           value=TracedField[date](value=date(2026, 4, 23), confidence=0.9))],
        amounts=[MonetaryAmount(label="price",
                                value=TracedField[float](value=660000.0, confidence=0.9))],
    )


def result() -> PipelineResult:
    return PipelineResult(
        email_id="em-1",
        extraction=extraction(),
        dispatch=Dispatch(email_id="em-1", attorney_id="atty-02", routing_reason="real estate",
                          acknowledgment=AcknowledgmentDraft(subject="Re: x", body="Dear ...")),
        decision=Decision(action=DecisionAction.REVIEW, reasons=[]),
    )


def edit(path: str, original, corrected) -> FieldEdit:
    return FieldEdit(field_path=path, original_value=original, corrected_value=corrected,
                     edited_by="dana@firm.example", edited_at=NOW)


def action(outcome=ReviewOutcome.APPROVED, edits=()) -> ReviewAction:
    return ReviewAction(email_id="em-1", outcome=outcome, reviewer="dana@firm.example",
                        note="looks right", edits=list(edits), acted_at=NOW)


# --------------------------------------------------------------------------- #
# Outcomes
# --------------------------------------------------------------------------- #


def test_approving_marks_the_draft_approved_but_not_sent() -> None:
    """The last state this system has. There is no send path."""
    reviewed = apply_review(result(), action(ReviewOutcome.APPROVED))
    assert reviewed.dispatch.acknowledgment.status == DraftStatus.APPROVED_NOT_SENT
    assert reviewed.review.reviewer == "dana@firm.example"
    assert reviewed.awaiting_review is False


def test_rejecting_marks_the_draft_rejected() -> None:
    reviewed = apply_review(result(), action(ReviewOutcome.REJECTED))
    assert reviewed.dispatch.acknowledgment.status == DraftStatus.REJECTED


def test_an_unreviewed_review_item_is_awaiting_review() -> None:
    assert result().awaiting_review is True


# --------------------------------------------------------------------------- #
# Corrections
# --------------------------------------------------------------------------- #


def test_a_corrected_party_name_replaces_the_value() -> None:
    reviewed = apply_review(
        result(),
        action(ReviewOutcome.APPROVED_WITH_EDITS,
               [edit("extraction.parties[0].name", "Delphine Winslo", "Delphine Winslow")]),
    )
    assert reviewed.extraction.parties[0].name.value == "Delphine Winslow"


def test_a_correction_keeps_what_the_model_said() -> None:
    """An audit trail that loses the machine's original answer is not an audit
    trail."""
    reviewed = apply_review(
        result(),
        action(ReviewOutcome.APPROVED_WITH_EDITS,
               [edit("extraction.parties[0].name", "Delphine Winslo", "Delphine Winslow")]),
    )
    note = reviewed.extraction.parties[0].name.validator_note
    assert "Delphine Winslo" in note
    assert "dana@firm.example" in note
    assert reviewed.review.edits[0].original_value == "Delphine Winslo"


def test_a_corrected_field_is_marked_as_human_not_model() -> None:
    """Confidence 1.0 because a person looked at it, and no span because their
    correction is not quoted from the email. Laundering a human value into
    something the model appears to have said would corrupt the trace."""
    reviewed = apply_review(
        result(),
        action(ReviewOutcome.APPROVED_WITH_EDITS,
               [edit("extraction.parties[0].name", "Delphine Winslo", "Delphine Winslow")]),
    )
    field = reviewed.extraction.parties[0].name
    assert field.confidence == 1.0
    assert field.span is None
    assert field.self_reported is None


def test_correcting_a_practice_area() -> None:
    reviewed = apply_review(
        result(),
        action(ReviewOutcome.APPROVED_WITH_EDITS,
               [edit("extraction.matter_type", "unknown", "real_estate")]),
    )
    assert reviewed.extraction.matter_type.value == PracticeArea.REAL_ESTATE


def test_correcting_a_jurisdiction() -> None:
    reviewed = apply_review(
        result(),
        action(ReviewOutcome.APPROVED_WITH_EDITS,
               [edit("extraction.jurisdiction", "Wakanda", "Cook County, Illinois")]),
    )
    assert reviewed.extraction.jurisdiction.value == "Cook County, Illinois"
    assert reviewed.extraction.jurisdiction.validator == ValidatorStatus.PASSED


def test_correcting_a_date_and_an_amount() -> None:
    reviewed = apply_review(
        result(),
        action(ReviewOutcome.APPROVED_WITH_EDITS, [
            edit("extraction.key_dates[0].value", "2026-04-23", "May 8, 2026"),
            edit("extraction.amounts[0].value", "660000.0", "$941,000"),
        ]),
    )
    assert reviewed.extraction.key_dates[0].value.value == date(2026, 5, 8)
    assert reviewed.extraction.amounts[0].value.value == 941000.0


def test_several_corrections_apply_together() -> None:
    reviewed = apply_review(
        result(),
        action(ReviewOutcome.APPROVED_WITH_EDITS, [
            edit("extraction.matter_type", "unknown", "real_estate"),
            edit("extraction.jurisdiction", "Wakanda", "Cook County, Illinois"),
        ]),
    )
    assert reviewed.extraction.matter_type.value == PracticeArea.REAL_ESTATE
    assert reviewed.extraction.jurisdiction.value == "Cook County, Illinois"
    assert reviewed.dispatch.acknowledgment.status == DraftStatus.APPROVED_NOT_SENT


def test_approving_without_edits_leaves_the_extraction_alone() -> None:
    original = result()
    reviewed = apply_review(original, action(ReviewOutcome.APPROVED))
    assert reviewed.extraction == original.extraction


# --------------------------------------------------------------------------- #
# Guard rails
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "path", ["extraction.summary", "classification.label", "decision.action", "nonsense"]
)
def test_fields_outside_the_editable_set_are_refused(path) -> None:
    """A reviewer corrects what the extractor read. Letting them rewrite the
    decision or the classification would make the audit trail meaningless."""
    with pytest.raises(UneditableField):
        apply_edit(extraction(), edit(path, "x", "y"))


def test_a_correction_that_is_not_a_date_is_refused() -> None:
    with pytest.raises(UneditableField, match="not a date"):
        apply_edit(extraction(), edit("extraction.key_dates[0].value", "x", "sometime"))


def test_a_correction_that_is_not_a_practice_area_is_refused() -> None:
    with pytest.raises(UneditableField, match="not a practice area"):
        apply_edit(extraction(), edit("extraction.matter_type", "x", "maritime salvage"))


def test_editing_without_an_extraction_is_refused() -> None:
    bare = PipelineResult(email_id="em-1", decision=Decision(action=DecisionAction.REVIEW))
    with pytest.raises(UneditableField):
        apply_review(bare, action(ReviewOutcome.APPROVED_WITH_EDITS,
                                  [edit("extraction.matter_type", "x", "family")]))
