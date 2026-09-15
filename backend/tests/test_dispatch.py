"""Routing, the matter stub, and the acknowledgment template."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from intake.domain.dispatch import (
    build_matter_stub,
    draft_acknowledgment,
    dispatch as run_dispatch,
    route,
)
from intake.domain.enums import (
    ClientType,
    DraftStatus,
    EmailClass,
    MatterStatus,
    PartyRole,
    PracticeArea,
    SpanStatus,
)
from intake.domain.models import (
    Attorney,
    Classification,
    ClientRecord,
    Email,
    Extraction,
    MatterRecord,
    Party,
    Resolution,
    Span,
    TracedField,
)
from intake.domain.records import FirmRecords

ATTORNEYS = [
    Attorney(id="atty-01", name="Harriet Vance", email="h@f.example",
             practice_areas=[PracticeArea.COMMERCIAL_LITIGATION], capacity=10, current_load=9),
    Attorney(id="atty-02", name="Sylvia Okonkwo", email="s@f.example",
             practice_areas=[PracticeArea.REAL_ESTATE], capacity=10, current_load=2),
    Attorney(id="atty-03", name="Gerald Lindqvist", email="g@f.example",
             practice_areas=[PracticeArea.REAL_ESTATE], capacity=10, current_load=8),
    Attorney(id="atty-04", name="Owen Trebuchet", email="o@f.example",
             practice_areas=[PracticeArea.FAMILY], capacity=5, current_load=5),
]

CLIENTS = [ClientRecord(id="cli-1", display_name="Acme LLC", client_type=ClientType.ORGANIZATION,
                        emails=["legal@acme.com"], domains=["acme.com"], opened_on=date(2021, 1, 1))]
MATTERS = [
    MatterRecord(id="mat-1", client_id="cli-1", caption="Acme lease",
                 practice_area=PracticeArea.REAL_ESTATE, status=MatterStatus.OPEN,
                 opened_on=date(2024, 1, 1), responsible_attorney_id="atty-03",
                 adverse_parties=["Someone Else LLC"]),
    MatterRecord(id="mat-2", client_id="cli-1", caption="Acme old dispute",
                 practice_area=PracticeArea.COMMERCIAL_LITIGATION, status=MatterStatus.CLOSED,
                 opened_on=date(2020, 1, 1), closed_on=date(2021, 1, 1),
                 responsible_attorney_id="atty-01", adverse_parties=[]),
]


@pytest.fixture
def records() -> FirmRecords:
    return FirmRecords(CLIENTS, MATTERS)


def extraction(area=PracticeArea.REAL_ESTATE, parties=None) -> Extraction:
    return Extraction(
        email_id="em-1",
        parties=parties if parties is not None else [
            Party(name=TracedField[str](value="Delphine Winslow", confidence=0.95),
                  role=PartyRole.PROSPECTIVE_CLIENT),
            Party(name=TracedField[str](value="Summit Ridge Systems Ltd.", confidence=0.9),
                  role=PartyRole.OPPOSING),
        ],
        matter_type=TracedField[PracticeArea](value=area, confidence=0.9),
        jurisdiction=TracedField[str](value="Cook County, Illinois", confidence=0.9),
    )


def email() -> Email:
    return Email(
        id="em-1", received_at=datetime(2026, 3, 1, 9, 0), from_name="Delphine Winslow",
        from_email="d@example.com", to_address="info@f.example",
        subject="survey problem before closing", body="We are under contract.",
    )


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #


def test_a_new_matter_routes_to_the_attorney_with_most_headroom(records) -> None:
    attorney, why = route(EmailClass.NEW_MATTER, extraction(), None, records, ATTORNEYS)
    assert attorney.id == "atty-02"
    assert "capacity" in why


def test_routing_never_picks_an_attorney_who_is_full(records) -> None:
    attorney, why = route(
        EmailClass.NEW_MATTER, extraction(PracticeArea.FAMILY), None, records, ATTORNEYS
    )
    assert attorney is None
    assert "capacity" in why


def test_an_uncovered_practice_area_routes_nowhere(records) -> None:
    attorney, why = route(
        EmailClass.NEW_MATTER, extraction(PracticeArea.PERSONAL_INJURY), None, records, ATTORNEYS
    )
    assert attorney is None
    assert "no attorney covers" in why


def test_an_unknown_practice_area_routes_nowhere(records) -> None:
    attorney, why = route(
        EmailClass.NEW_MATTER, extraction(PracticeArea.UNKNOWN), None, records, ATTORNEYS
    )
    assert attorney is None


def test_existing_client_mail_goes_to_whoever_owns_the_file(records) -> None:
    """Reassigning on practice area would cut across a relationship the firm has."""
    resolution = Resolution(email_id="em-1", matter_ids=["mat-1", "mat-2"])
    attorney, why = route(
        EmailClass.EXISTING_CLIENT, extraction(), resolution, records, ATTORNEYS
    )
    assert attorney.id == "atty-03", "the open matter's attorney, not the least loaded"
    assert "mat-1" in why


def test_closed_matters_do_not_claim_the_routing(records) -> None:
    resolution = Resolution(email_id="em-1", matter_ids=["mat-2"])
    attorney, _ = route(EmailClass.EXISTING_CLIENT, extraction(), resolution, records, ATTORNEYS)
    assert attorney.id != "atty-01"


def test_spam_and_unclear_route_nowhere(records) -> None:
    for label in (EmailClass.VENDOR_OR_SPAM, EmailClass.UNCLEAR):
        attorney, why = route(label, extraction(), None, records, ATTORNEYS)
        assert attorney is None
        assert why


def test_routing_is_reproducible(records) -> None:
    first = route(EmailClass.NEW_MATTER, extraction(), None, records, ATTORNEYS)
    second = route(EmailClass.NEW_MATTER, extraction(), None, records, ATTORNEYS)
    assert first == second


# --------------------------------------------------------------------------- #
# Acknowledgment
# --------------------------------------------------------------------------- #


def test_the_acknowledgment_disclaims_representation() -> None:
    """The most dangerous piece of writing a firm sends. It must not imply an
    engagement the firm has not agreed to."""
    draft = draft_acknowledgment(email(), extraction(), ATTORNEYS[1])
    assert "not yet agreed to represent you" in draft.body
    assert "no attorney-client relationship is" in draft.body
    assert "do not send us" in draft.body


def test_the_acknowledgment_starts_as_a_draft() -> None:
    assert draft_acknowledgment(email(), extraction(), ATTORNEYS[1]).status == DraftStatus.DRAFT


def test_there_is_no_sent_status_anywhere() -> None:
    """The system has no send path, and the enum has no state for one."""
    assert {s.value for s in DraftStatus} == {"draft", "approved_not_sent", "rejected"}


def test_the_acknowledgment_gives_no_legal_advice() -> None:
    body = draft_acknowledgment(email(), extraction(), ATTORNEYS[1]).body.lower()
    for phrase in ["you should", "we recommend", "your claim is", "you are entitled"]:
        assert phrase not in body


def test_an_unrouted_inquiry_still_gets_a_neutral_draft() -> None:
    draft = draft_acknowledgment(email(), extraction(), None)
    assert "the appropriate attorney" in draft.body


# --------------------------------------------------------------------------- #
# Matter stub
# --------------------------------------------------------------------------- #


def test_the_stub_captions_from_the_parties() -> None:
    stub = build_matter_stub(extraction())
    assert stub.caption == "Delphine Winslow adv. Summit Ridge Systems Ltd."
    assert stub.practice_area == PracticeArea.REAL_ESTATE
    assert stub.opposing_parties == ["Summit Ridge Systems Ltd."]


def test_the_stub_says_so_when_the_client_is_unidentified() -> None:
    stub = build_matter_stub(extraction(parties=[]))
    assert "Unidentified" in stub.caption


# --------------------------------------------------------------------------- #
# Whole stage
# --------------------------------------------------------------------------- #


def test_spam_gets_no_stub_and_no_acknowledgment(records) -> None:
    classification = Classification(
        email_id="em-1",
        label=TracedField[EmailClass](value=EmailClass.VENDOR_OR_SPAM, confidence=0.95),
        rationale="marketing",
    )
    result = run_dispatch(email(), classification, extraction(), None, records, ATTORNEYS)
    assert result.attorney_id is None
    assert result.acknowledgment is None
    assert result.matter_stub is None
