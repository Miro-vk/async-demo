"""When the system acts and when it asks.

Pure function of typed inputs -- these construct stage outputs directly and assert
on the decision. No model, no database, no orchestrator.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from intake.domain.enums import (
    ConflictSeverity,
    DecisionAction,
    EmailClass,
    MatchMethod,
    PartyRole,
    PracticeArea,
    ReviewReasonCode,
    SpanStatus,
    ValidatorStatus,
)
from intake.domain.models import (
    AcknowledgmentDraft,
    Classification,
    ConflictHit,
    Dispatch,
    Extraction,
    ParseFailure,
    Party,
    PartyMatch,
    Resolution,
    Span,
    TracedField,
)
from intake.domain.policy import DEFAULT, Thresholds, decide


def classification(label=EmailClass.NEW_MATTER, confidence=0.95) -> Classification:
    return Classification(
        email_id="em-1",
        label=TracedField[EmailClass](
            value=label,
            confidence=confidence,
            self_reported=confidence,
            span=Span(start=0, end=5, quote="hello", status=SpanStatus.VERIFIED),
        ),
        rationale="because",
    )


def party(name="Delphine Winslow", role=PartyRole.PROSPECTIVE_CLIENT, confidence=0.95) -> Party:
    return Party(
        name=TracedField[str](
            value=name,
            confidence=confidence,
            span=Span(start=0, end=5, quote="hello", status=SpanStatus.VERIFIED),
            validator=ValidatorStatus.PASSED,
        ),
        role=role,
    )


def extraction(parties=None, area=PracticeArea.REAL_ESTATE, area_conf=0.9, warnings=None) -> Extraction:
    return Extraction(
        email_id="em-1",
        parties=[party()] if parties is None else parties,
        matter_type=TracedField[PracticeArea](
            value=area,
            confidence=area_conf,
            span=Span(start=0, end=5, quote="hello", status=SpanStatus.VERIFIED),
            validator=ValidatorStatus.PASSED,
        ),
        jurisdiction=TracedField[str](
            value="Cook County, Illinois",
            confidence=0.95,
            span=Span(start=0, end=5, quote="hello", status=SpanStatus.VERIFIED),
            validator=ValidatorStatus.PASSED,
        ),
        parse_warnings=warnings or [],
    )


def resolution(conflicts=None, checked=("Delphine Winslow",), matches=()) -> Resolution:
    return Resolution(
        email_id="em-1",
        conflicts=list(conflicts or []),
        checked_party_names=list(checked),
        client_matches=list(matches),
    )


def dispatch(attorney_id="atty-05") -> Dispatch:
    return Dispatch(
        email_id="em-1",
        attorney_id=attorney_id,
        routing_reason="real estate",
        acknowledgment=AcknowledgmentDraft(subject="Re: x", body="..."),
    )


def conflict(severity=ConflictSeverity.PROBABLE, rule="ADVERSE_PARTY_IS_CLIENT") -> ConflictHit:
    return ConflictHit(
        rule_id=rule, severity=severity, inquiry_party="Acme LLC",
        matched_record_kind="client", matched_record_id="cli-1",
        matched_field="display_name", matched_value="Acme LLC",
        score=0.95,
        explanation=(
            "The inquiry names 'Acme LLC' as an opposing party, and that is an "
            "existing client of the firm (cli-1)."
        ),
    )


def codes(decision) -> set[str]:
    return {r.code.value for r in decision.reasons}


# --------------------------------------------------------------------------- #
# The happy path has to exist
# --------------------------------------------------------------------------- #


def test_a_clean_confident_inquiry_proceeds() -> None:
    """A system that abstains on everything is not abstaining, it is refusing."""
    result = decide(classification(), extraction(), resolution(), dispatch())
    assert result.action == DecisionAction.PROCEED
    assert result.reasons == []


def test_confident_spam_stops_without_review() -> None:
    """Solicitations have no parties and no matter type, and that is correct
    rather than deficient -- running the extraction checks over them would bury
    the real queue under spam."""
    result = decide(
        classification(EmailClass.VENDOR_OR_SPAM, 0.96),
        extraction(parties=[], area=None, area_conf=0.1),
        resolution(checked=()),
        Dispatch(email_id="em-1", attorney_id=None, routing_reason="solicitation"),
    )
    assert result.action == DecisionAction.STOP
    assert result.reasons == []


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #


def test_low_confidence_classification_goes_to_review() -> None:
    result = decide(classification(confidence=0.45), extraction(), resolution(), dispatch())
    assert result.action == DecisionAction.REVIEW
    assert ReviewReasonCode.LOW_CONFIDENCE_CLASSIFICATION.value in codes(result)


def test_unclear_is_reported_separately_from_low_confidence() -> None:
    """The two mean different things and the queue shows them differently: one is
    the classifier judging the email fits nothing, the other is it being unsure."""
    unclear = decide(classification(EmailClass.UNCLEAR, 0.95), extraction(), resolution(), dispatch())
    unsure = decide(classification(EmailClass.NEW_MATTER, 0.40), extraction(), resolution(), dispatch())

    assert ReviewReasonCode.CLASSIFIER_SAID_UNCLEAR.value in codes(unclear)
    assert ReviewReasonCode.LOW_CONFIDENCE_CLASSIFICATION.value not in codes(unclear)
    assert ReviewReasonCode.LOW_CONFIDENCE_CLASSIFICATION.value in codes(unsure)
    assert ReviewReasonCode.CLASSIFIER_SAID_UNCLEAR.value not in codes(unsure)


def test_low_confidence_spam_is_reviewed_not_binned() -> None:
    """Binning a real inquiry because it looked like marketing loses a client."""
    result = decide(
        classification(EmailClass.VENDOR_OR_SPAM, 0.55),
        extraction(), resolution(), dispatch(),
    )
    assert result.action == DecisionAction.REVIEW


def test_an_unreadable_classification_reviews_rather_than_crashing() -> None:
    result = decide(ParseFailure(stage="classify", reason="bad JSON"), None, None, None)
    assert result.action == DecisionAction.REVIEW
    assert ReviewReasonCode.MODEL_PARSE_FAILURE.value in codes(result)


def test_an_unreadable_extraction_reviews() -> None:
    result = decide(
        classification(), ParseFailure(stage="extract", reason="truncated"),
        resolution(), dispatch(),
    )
    assert result.action == DecisionAction.REVIEW
    assert ReviewReasonCode.MODEL_PARSE_FAILURE.value in codes(result)


# --------------------------------------------------------------------------- #
# Conflicts
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "severity",
    [ConflictSeverity.WEAK, ConflictSeverity.POSSIBLE, ConflictSeverity.PROBABLE],
)
def test_any_conflict_at_all_reaches_a_human(severity) -> None:
    """Including WEAK. The cost of asking is an email; the cost of guessing is a
    disqualification."""
    result = decide(
        classification(), extraction(), resolution(conflicts=[conflict(severity)]), dispatch()
    )
    assert result.action == DecisionAction.REVIEW
    assert ReviewReasonCode.CONFLICT_HIT.value in codes(result)


def test_a_conflict_reason_carries_the_rule_that_fired() -> None:
    result = decide(
        classification(), extraction(), resolution(conflicts=[conflict()]), dispatch()
    )
    hit = next(r for r in result.reasons if r.code == ReviewReasonCode.CONFLICT_HIT)
    assert hit.rule_id == "ADVERSE_PARTY_IS_CLIENT"
    assert hit.message


def test_a_vacuous_conflict_check_is_not_a_clearance() -> None:
    """An empty conflicts list from a check with nothing to look up must never be
    treated as 'no conflicts found'."""
    result = decide(
        classification(), extraction(parties=[]), resolution(checked=()), dispatch()
    )
    assert result.action == DecisionAction.REVIEW
    assert ReviewReasonCode.CONFLICT_CHECK_VACUOUS.value in codes(result)


# --------------------------------------------------------------------------- #
# Extraction quality
# --------------------------------------------------------------------------- #


def test_an_ungrounded_value_goes_to_review() -> None:
    bad = Extraction(
        email_id="em-1",
        parties=[party()],
        matter_type=TracedField[PracticeArea](
            value=PracticeArea.EMPLOYMENT, confidence=0.3, self_reported=0.99,
            span=Span(start=-1, end=-1, quote="invented", status=SpanStatus.NOT_FOUND),
        ),
        jurisdiction=TracedField[str](value=None, confidence=0.2),
    )
    result = decide(classification(), bad, resolution(), dispatch())
    assert ReviewReasonCode.UNVERIFIED_SPAN.value in codes(result)


def test_a_failed_validator_goes_to_review() -> None:
    bad = extraction()
    bad = bad.model_copy(
        update={
            "jurisdiction": bad.jurisdiction.model_copy(
                update={"validator": ValidatorStatus.FAILED, "validator_note": "not a US state"}
            )
        }
    )
    result = decide(classification(), bad, resolution(), dispatch())
    assert ReviewReasonCode.VALIDATOR_FAILED.value in codes(result)


def test_an_unknown_practice_area_on_a_new_matter_goes_to_review() -> None:
    result = decide(
        classification(), extraction(area=PracticeArea.UNKNOWN), resolution(), dispatch()
    )
    assert ReviewReasonCode.AMBIGUOUS_MATTER_TYPE.value in codes(result)


def test_an_unknown_practice_area_on_existing_client_mail_does_not() -> None:
    """A billing question does not state a practice area, and should not be
    punished for it -- resolve gets the area from the matched matter."""
    result = decide(
        classification(EmailClass.EXISTING_CLIENT),
        extraction(area=PracticeArea.UNKNOWN),
        resolution(matches=[PartyMatch(
            inquiry_party="a@b.com", inquiry_role=PartyRole.PROSPECTIVE_CLIENT,
            client_id="cli-1", client_name="Acme", method=MatchMethod.EMAIL_ADDRESS, score=1.0,
        )]),
        dispatch(),
    )
    assert ReviewReasonCode.AMBIGUOUS_MATTER_TYPE.value not in codes(result)


def test_a_shaky_party_name_goes_to_review() -> None:
    result = decide(
        classification(), extraction(parties=[party(confidence=0.4)]), resolution(), dispatch()
    )
    assert ReviewReasonCode.LOW_CONFIDENCE_FIELD.value in codes(result)


def test_a_shaky_third_party_does_not() -> None:
    """Third parties are not read by any conflict rule, so their confidence gates
    nothing and flagging them is pure noise."""
    result = decide(
        classification(),
        extraction(parties=[party(), party("A Witness", PartyRole.THIRD_PARTY, 0.3)]),
        resolution(), dispatch(),
    )
    assert ReviewReasonCode.LOW_CONFIDENCE_FIELD.value not in codes(result)


def test_naming_people_who_are_not_the_client_still_goes_to_review() -> None:
    """The dangerous case: someone writes on a friend's behalf. Parties exist, so
    the conflicts check runs and comes back clean -- about the wrong person. The
    system proceeded on this before the rule existed."""
    result = decide(
        classification(),
        extraction(parties=[party("Felicity Bramble", PartyRole.THIRD_PARTY, 0.95)]),
        resolution(checked=("Felicity Bramble",)),
        dispatch(),
    )
    assert result.action == DecisionAction.REVIEW
    assert ReviewReasonCode.MISSING_REQUIRED_FIELD.value in codes(result)
    message = " ".join(r.message for r in result.reasons)
    assert "Felicity Bramble" in message, "name the people it did find"
    assert "third party" in message, "and say what it took them to be"


def test_discarded_extraction_output_is_surfaced() -> None:
    result = decide(
        classification(), extraction(warnings=["party[2] had no usable name; dropped"]),
        resolution(), dispatch(),
    )
    assert ReviewReasonCode.PARTIAL_PARSE.value in codes(result)


# --------------------------------------------------------------------------- #
# Dispatch and shape
# --------------------------------------------------------------------------- #


def test_no_available_attorney_goes_to_review() -> None:
    result = decide(
        classification(), extraction(), resolution(),
        Dispatch(email_id="em-1", attorney_id=None, routing_reason="all at capacity"),
    )
    assert ReviewReasonCode.NO_ATTORNEY_AVAILABLE.value in codes(result)


def test_reasons_accumulate_rather_than_short_circuit() -> None:
    """A reviewer should see everything wrong with an email at once. Fixing one
    problem and resurfacing with the next is how queues get abandoned."""
    result = decide(
        classification(confidence=0.4),
        extraction(parties=[party(confidence=0.3)], area=PracticeArea.UNKNOWN),
        resolution(conflicts=[conflict()]),
        Dispatch(email_id="em-1", attorney_id=None, routing_reason="no area"),
    )
    assert len(codes(result)) >= 4


def test_every_reason_points_at_a_field_or_a_rule() -> None:
    result = decide(
        classification(confidence=0.4), extraction(area=PracticeArea.UNKNOWN),
        resolution(conflicts=[conflict()]), dispatch(),
    )
    for reason in result.reasons:
        assert reason.field_path or reason.rule_id
        assert len(reason.message) > 20


def test_thresholds_are_the_caution_dial() -> None:
    """Raising the bar sends more mail to humans, and it is one argument."""
    inputs = (classification(confidence=0.80), extraction(), resolution(), dispatch())
    assert decide(*inputs, DEFAULT).action == DecisionAction.PROCEED
    assert decide(*inputs, Thresholds(classification=0.9)).action == DecisionAction.REVIEW


# --------------------------------------------------------------------------- #
# Reasons are read by people
# --------------------------------------------------------------------------- #


def test_no_reason_message_leaks_wire_format() -> None:
    """Enum values and field paths are how this system talks to itself. A reviewer
    seeing "(prospective_client)" or "extraction.parties[0].name" is being shown
    the inside of the program.

    Checked across every reason the whole corpus produces, not a sample, because
    this is the kind of thing that creeps back in one message at a time.
    """
    import re

    from intake.db import repo
    from intake.paths import DATABASE_PATH

    if not DATABASE_PATH.exists():
        pytest.skip("no processed database; run `make seed && make process`")

    conn = repo.connect(DATABASE_PATH)
    try:
        runs = repo.list_runs(conn)
    finally:
        conn.close()

    snake = re.compile(r"\b[a-z]+(?:_[a-z]+)+\b")
    path = re.compile(r"\b(?:extraction|classification|resolution|dispatch)\.[a-z_]")
    # "party[2]" has no underscore and no dot, and is just as much wire format.
    indexed = re.compile(r"\b[a-z_]+\[\d+\]")
    # "PROSPECT_WAS_ADVERSE_PARTY" and "cli-0195" are the other two shapes: a
    # constant shouted at a reviewer, and a row id loose in a sentence.
    shouting = re.compile(r"\b[A-Z]{2,}(?:_[A-Z]+)+\b")
    row_id = re.compile(r"\b(?:cli|mat|em|atty)-\d+\b")

    def leaks(text: str) -> bool:
        return bool(
            snake.search(text)
            or path.search(text)
            or indexed.search(text)
            or shouting.search(text)
            or row_id.search(text)
        )

    offenders: list[str] = []
    for run in runs:
        for reason in run.decision.reasons:
            if leaks(reason.message):
                offenders.append(f"{run.email_id} [{reason.code.value}]: {reason.message}")
            if leaks(reason.rule_label):
                offenders.append(f"{run.email_id} rule label: {reason.rule_label}")
        for hit in run.resolution.conflicts if run.resolution else []:
            if leaks(hit.explanation):
                offenders.append(f"{run.email_id} [{hit.rule_label}]: {hit.explanation}")
            for label in (hit.rule_label, hit.matched_field_label, hit.matched_record_label):
                if leaks(label):
                    offenders.append(f"{run.email_id} label: {label}")

    assert not offenders, "wire format in text a human reads:\n" + "\n".join(offenders[:5])


def test_every_reason_offers_a_readable_field_label() -> None:
    result = decide(
        classification(confidence=0.4),
        extraction(area=PracticeArea.UNKNOWN, parties=[party(confidence=0.2)]),
        resolution(conflicts=[conflict()]),
        Dispatch(email_id="em-1", attorney_id=None, routing_reason="no area"),
    )
    assert result.reasons
    for reason in result.reasons:
        assert reason.field_label
        assert "_" not in reason.field_label
        assert "." not in reason.field_label
        assert reason.field_label[0].isupper()
