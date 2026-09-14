"""Stage 3 end to end: matching, conflicts, and -- most importantly -- what the
resolution says about its own completeness."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from intake.domain.enums import (
    ClientType,
    MatchMethod,
    MatterStatus,
    PartyRole,
    PracticeArea,
)
from intake.domain.models import (
    ClientRecord,
    Email,
    Extraction,
    MatterRecord,
    ParseFailure,
    Party,
    TracedField,
)
from intake.domain.records import FirmRecords
from intake.domain.resolve import resolve

CLIENTS = [
    ClientRecord(id="cli-1", display_name="Meridian Manufacturing Corp.",
                 client_type=ClientType.ORGANIZATION,
                 emails=["legal@meridianmanufacturing.com"],
                 domains=["meridianmanufacturing.com"], opened_on=date(2021, 1, 1)),
    ClientRecord(id="cli-2", display_name="Dana Vasquez", client_type=ClientType.INDIVIDUAL,
                 emails=["dana.vasquez@gmail.com"], domains=[], opened_on=date(2022, 5, 1)),
]

MATTERS = [
    MatterRecord(id="mat-1", client_id="cli-1", caption="Meridian v. Someone",
                 practice_area=PracticeArea.COMMERCIAL_LITIGATION, status=MatterStatus.OPEN,
                 opened_on=date(2023, 1, 1), responsible_attorney_id="atty-01",
                 adverse_parties=["Rival Holdings LLC"]),
    MatterRecord(id="mat-2", client_id="cli-1", caption="Meridian employment",
                 practice_area=PracticeArea.EMPLOYMENT, status=MatterStatus.CLOSED,
                 opened_on=date(2021, 6, 1), closed_on=date(2022, 8, 1),
                 responsible_attorney_id="atty-03", adverse_parties=["Terrence Vasquez"]),
]


@pytest.fixture
def records() -> FirmRecords:
    return FirmRecords(CLIENTS, MATTERS)


def make_email(from_email="stranger@elsewhere.example") -> Email:
    return Email(
        id="em-1", received_at=datetime(2026, 3, 1, 9, 0), from_name="A Person",
        from_email=from_email, to_address="info@firm.example",
        subject="Inquiry", body="We would like advice.",
    )


def make_extraction(*parties: Party) -> Extraction:
    return Extraction(
        email_id="em-1", parties=list(parties),
        matter_type=TracedField[PracticeArea](value=None, confidence=0.2),
        jurisdiction=TracedField[str](value=None, confidence=0.2),
    )


def party(name: str, role=PartyRole.PROSPECTIVE_CLIENT, confidence=0.9) -> Party:
    return Party(name=TracedField[str](value=name, confidence=confidence), role=role)


# --------------------------------------------------------------------------- #
# The vacuous-check problem
# --------------------------------------------------------------------------- #


def test_a_check_with_no_names_is_vacuous_not_clean(records) -> None:
    """The single most important distinction in this stage. An empty conflicts
    list means either 'we checked and found nothing' or 'we had nothing to
    check', and those must never render the same way to a human."""
    result = resolve(make_email(), make_extraction(), records)
    assert result.conflicts == []
    assert result.is_vacuous is True
    assert result.completeness_notes


def test_a_real_check_that_finds_nothing_is_not_vacuous(records) -> None:
    result = resolve(make_email(), make_extraction(party("Totally Unrelated Ltd.")), records)
    assert result.conflicts == []
    assert result.is_vacuous is False
    assert result.checked_party_names == ["Totally Unrelated Ltd."]


def test_a_failed_extraction_produces_a_vacuous_result_that_says_so(records) -> None:
    result = resolve(make_email(), None, records)
    assert result.is_vacuous
    assert any("Extraction failed" in note for note in result.completeness_notes)


def test_a_parse_failure_is_treated_as_no_extraction(records) -> None:
    from intake.pipeline.runner import run_resolve

    failure = ParseFailure(stage="extract", reason="bad JSON")
    result = run_resolve(make_email(), failure, records).output
    assert result.is_vacuous


def test_a_name_too_short_to_look_up_is_skipped_and_reported(records) -> None:
    result = resolve(make_email(), make_extraction(party("Jo")), records)
    assert result.checked_party_names == []
    assert result.is_vacuous
    assert any("too short" in note for note in result.completeness_notes)


def test_a_low_confidence_name_is_still_checked_but_flagged(records) -> None:
    """A conflict found from a shaky extraction is still a conflict. The shakiness
    is recorded so nobody reads a clean result as stronger than it is."""
    result = resolve(
        make_email(),
        make_extraction(party("Rival Holdings LLC", confidence=0.3)),
        records,
    )
    assert "Rival Holdings LLC" in result.checked_party_names
    assert any("confidence was 0.30" in note for note in result.completeness_notes)


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #


def test_the_sender_address_identifies_a_client_without_any_extraction(records) -> None:
    """Address matching does not depend on the model having worked."""
    result = resolve(make_email("legal@meridianmanufacturing.com"), None, records)
    assert [m.client_id for m in result.client_matches] == ["cli-1"]
    assert result.client_matches[0].method == MatchMethod.EMAIL_ADDRESS


def test_an_unknown_sender_is_reported_as_unmatched(records) -> None:
    result = resolve(make_email(), make_extraction(party("Somebody Ltd.")), records)
    assert any("matches no client" in note for note in result.completeness_notes)


def test_matters_are_surfaced_for_strongly_identified_clients(records) -> None:
    result = resolve(make_email("legal@meridianmanufacturing.com"), None, records)
    assert result.matter_ids == ["mat-1", "mat-2"]


def test_a_domain_only_match_does_not_pull_in_matters(records) -> None:
    """A weak identification must not start attaching the inquiry to live files."""
    result = resolve(make_email("someone.else@meridianmanufacturing.com"), None, records)
    assert result.client_matches[0].method == MatchMethod.EMAIL_DOMAIN
    assert result.matter_ids == []


def test_duplicate_matches_are_collapsed_to_the_strongest(records) -> None:
    result = resolve(
        make_email("legal@meridianmanufacturing.com"),
        make_extraction(party("Meridian Manufacturing Corp."), party("Meridian Mfg. Corp.")),
        records,
    )
    pairs = [(m.inquiry_party, m.client_id) for m in result.client_matches]
    assert len(pairs) == len(set(pairs))


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


def test_resolution_is_reproducible(records) -> None:
    """No clock, no model, no randomness. A conflicts result that cannot be
    reproduced cannot be argued with."""
    extraction = make_extraction(
        party("Terrence Vasquez"), party("Rival Holdings LLC", PartyRole.OPPOSING)
    )
    first = resolve(make_email(), extraction, records)
    second = resolve(make_email(), extraction, records)
    assert first == second


def test_the_whole_pipeline_finds_the_closed_matter_conflict(records) -> None:
    result = resolve(make_email(), make_extraction(party("Terrence Vasquez")), records)
    assert len(result.conflicts) == 1
    assert result.conflicts[0].matched_record_id == "mat-2"
    assert "closed" in result.conflicts[0].explanation
