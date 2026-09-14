"""Conflict rules. The highest-stakes logic here, and entirely deterministic --
these tests construct records directly and assert on rule behaviour, no model
anywhere near them."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from intake.domain.conflicts import (
    RULE_ADVERSE_PARTY_IS_CLIENT,
    RULE_PROSPECT_WAS_ADVERSE_PARTY,
    RULE_SENDER_DOMAIN_MATCHES_CLIENT,
    ConflictContext,
    run_all,
)
from intake.domain.enums import (
    ClientType,
    ConflictSeverity,
    MatterStatus,
    PartyRole,
    PracticeArea,
)
from intake.domain.models import (
    ClientRecord,
    Email,
    Extraction,
    MatterRecord,
    Party,
    TracedField,
)
from intake.domain.records import FirmRecords


def client(cid: str, name: str, domains=(), emails=()) -> ClientRecord:
    return ClientRecord(
        id=cid, display_name=name, client_type=ClientType.ORGANIZATION,
        emails=list(emails), domains=list(domains), opened_on=date(2021, 1, 1),
    )


def matter(mid: str, client_id: str, adverse: list[str], status=MatterStatus.OPEN) -> MatterRecord:
    return MatterRecord(
        id=mid, client_id=client_id, caption=f"{client_id} matter",
        practice_area=PracticeArea.COMMERCIAL_LITIGATION, status=status,
        opened_on=date(2022, 1, 1),
        closed_on=date(2023, 6, 1) if status == MatterStatus.CLOSED else None,
        responsible_attorney_id="atty-01", adverse_parties=adverse,
    )


def party(name: str, role: PartyRole, confidence: float = 0.9) -> Party:
    return Party(name=TracedField[str](value=name, confidence=confidence), role=role)


def email(from_email: str = "someone@stranger.example") -> Email:
    return Email(
        id="em-test", received_at=datetime(2026, 3, 1, 9, 0), from_name="A Sender",
        from_email=from_email, to_address="info@firm.example",
        subject="Inquiry", body="We have a dispute.",
    )


def check(parties: list[Party], clients, matters, from_email="someone@stranger.example"):
    extraction = Extraction(
        email_id="em-test", parties=parties,
        matter_type=TracedField[PracticeArea](value=None, confidence=0.1),
        jurisdiction=TracedField[str](value=None, confidence=0.1),
    )
    return run_all(
        ConflictContext(
            email=email(from_email),
            extraction=extraction,
            records=FirmRecords(clients, matters),
        )
    )


# --------------------------------------------------------------------------- #
# Rule 1: we would be acting against our own client
# --------------------------------------------------------------------------- #


def test_adverse_party_matching_a_current_client_is_probable() -> None:
    hits = check(
        [party("Meridian Manufacturing Corp.", PartyRole.OPPOSING)],
        [client("cli-1", "Meridian Manufacturing Corp.")],
        [matter("mat-1", "cli-1", ["Someone Else"])],
    )
    assert len(hits) == 1
    assert hits[0].rule_id == RULE_ADVERSE_PARTY_IS_CLIENT
    assert hits[0].severity == ConflictSeverity.PROBABLE
    assert hits[0].matched_record_id == "cli-1"


def test_an_abbreviated_corporate_name_still_matches() -> None:
    """The planted CORP_NAME_VARIANT trap in its abbreviation form."""
    hits = check(
        [party("Meridian Mfg. Corp.", PartyRole.OPPOSING)],
        [client("cli-1", "Meridian Manufacturing Corp.")],
        [matter("mat-1", "cli-1", [])],
    )
    assert hits and hits[0].severity == ConflictSeverity.PROBABLE


def test_a_dropped_corporate_suffix_still_matches() -> None:
    hits = check(
        [party("Oakhaven Staffing", PartyRole.OPPOSING)],
        [client("cli-1", "Oakhaven Staffing PLLC")],
        [matter("mat-1", "cli-1", [])],
    )
    assert hits and hits[0].severity == ConflictSeverity.PROBABLE


def test_a_former_client_is_graded_lower_but_still_raised() -> None:
    """'We closed that file' is a reason a human might clear the conflict. It is
    never a reason not to raise it."""
    hits = check(
        [party("Meridian Manufacturing Corp.", PartyRole.OPPOSING)],
        [client("cli-1", "Meridian Manufacturing Corp.")],
        [matter("mat-1", "cli-1", [], status=MatterStatus.CLOSED)],
    )
    assert len(hits) == 1
    assert hits[0].severity == ConflictSeverity.POSSIBLE
    assert "former client" in hits[0].explanation


def test_a_different_entity_type_is_not_claimed_as_the_same_client() -> None:
    """Regression guard: 'Summit Ridge Systems Ltd.' vs '... LLP' was once reported
    as PROBABLE at score 0.95. Different registrations; still worth raising,
    but not as an identification."""
    hits = check(
        [party("Summit Ridge Systems Ltd.", PartyRole.OPPOSING)],
        [client("cli-1", "Summit Ridge Systems LLP")],
        [matter("mat-1", "cli-1", [])],
    )
    assert len(hits) == 1
    assert hits[0].severity == ConflictSeverity.POSSIBLE
    assert "different entity type" in hits[0].explanation
    assert "LTD vs LLP" in hits[0].explanation


def test_an_unrelated_company_raises_nothing() -> None:
    assert check(
        [party("Northwind Logistics LLC", PartyRole.OPPOSING)],
        [client("cli-1", "Meridian Manufacturing Corp.")],
        [matter("mat-1", "cli-1", [])],
    ) == []


def test_our_own_prospective_client_is_not_an_adverse_conflict() -> None:
    """Rule 1 must look at opposing parties only. A prospective client who is
    already a client is a match, not a conflict."""
    hits = check(
        [party("Meridian Manufacturing Corp.", PartyRole.PROSPECTIVE_CLIENT)],
        [client("cli-1", "Meridian Manufacturing Corp.")],
        [matter("mat-1", "cli-1", [])],
    )
    assert not any(h.rule_id == RULE_ADVERSE_PARTY_IS_CLIENT for h in hits)


# --------------------------------------------------------------------------- #
# Rule 2: we would be acting for someone we acted against
# --------------------------------------------------------------------------- #


def test_prospect_who_was_an_adverse_party_is_probable() -> None:
    hits = check(
        [party("Granite Bay Textiles L.L.C.", PartyRole.PROSPECTIVE_CLIENT)],
        [client("cli-1", "Curtis Amador")],
        [matter("mat-1", "cli-1", ["Granite Bay Textiles L.L.C."])],
    )
    assert len(hits) == 1
    assert hits[0].rule_id == RULE_PROSPECT_WAS_ADVERSE_PARTY
    assert hits[0].severity == ConflictSeverity.PROBABLE
    assert hits[0].matched_record_id == "mat-1"


def test_a_closed_matter_does_not_reduce_the_severity() -> None:
    """The planted ADVERSE_PARTY_CLOSED_MATTER trap, and the single most important
    invariant in this module. Grading these lower because a file was closed is
    exactly the mistake the rule exists to prevent."""
    open_hits = check(
        [party("Granite Bay Textiles L.L.C.", PartyRole.PROSPECTIVE_CLIENT)],
        [client("cli-1", "Curtis Amador")],
        [matter("mat-1", "cli-1", ["Granite Bay Textiles L.L.C."], status=MatterStatus.OPEN)],
    )
    closed_hits = check(
        [party("Granite Bay Textiles L.L.C.", PartyRole.PROSPECTIVE_CLIENT)],
        [client("cli-1", "Curtis Amador")],
        [matter("mat-1", "cli-1", ["Granite Bay Textiles L.L.C."], status=MatterStatus.CLOSED)],
    )
    assert len(closed_hits) == len(open_hits) == 1
    assert closed_hits[0].severity == open_hits[0].severity == ConflictSeverity.PROBABLE
    assert "closed matter does not clear this" in closed_hits[0].explanation


def test_a_party_with_no_stated_role_is_still_checked_as_a_prospect() -> None:
    """An extraction that could not assign a role must not silently skip the
    conflict check for that name."""
    hits = check(
        [party("Granite Bay Textiles L.L.C.", PartyRole.UNKNOWN)],
        [client("cli-1", "Curtis Amador")],
        [matter("mat-1", "cli-1", ["Granite Bay Textiles L.L.C."], status=MatterStatus.CLOSED)],
    )
    assert any(h.rule_id == RULE_PROSPECT_WAS_ADVERSE_PARTY for h in hits)


# --------------------------------------------------------------------------- #
# Rule 3: the domain signal
# --------------------------------------------------------------------------- #


def test_sender_domain_matching_a_client_is_weak_but_raised() -> None:
    hits = check(
        [party("Presidio Orthopedics PLLC", PartyRole.OPPOSING)],
        [client("cli-1", "Vantage Property Group LLP", domains=["vantagepropertygroup.com"])],
        [matter("mat-1", "cli-1", [])],
        from_email="emmett@vantagepropertygroup.com",
    )
    domain_hits = [h for h in hits if h.rule_id == RULE_SENDER_DOMAIN_MATCHES_CLIENT]
    assert len(domain_hits) == 1
    assert domain_hits[0].severity == ConflictSeverity.WEAK
    assert domain_hits[0].matched_value == "vantagepropertygroup.com"


def test_a_mailbox_provider_domain_never_fires_the_rule() -> None:
    """Otherwise every gmail sender conflicts with every gmail client."""
    hits = check(
        [party("Somebody Else Inc.", PartyRole.OPPOSING)],
        [client("cli-1", "Some Client LLC", domains=["gmail.com"])],
        [matter("mat-1", "cli-1", [])],
        from_email="prospect@gmail.com",
    )
    assert not any(h.rule_id == RULE_SENDER_DOMAIN_MATCHES_CLIENT for h in hits)


def test_the_domain_rule_is_suppressed_when_a_name_already_matches() -> None:
    """When the client is identified by name, the domain adds nothing but noise."""
    hits = check(
        [party("Vantage Property Group LLP", PartyRole.PROSPECTIVE_CLIENT)],
        [client("cli-1", "Vantage Property Group LLP", domains=["vantagepropertygroup.com"])],
        [matter("mat-1", "cli-1", [])],
        from_email="felicity@vantagepropertygroup.com",
    )
    assert not any(h.rule_id == RULE_SENDER_DOMAIN_MATCHES_CLIENT for h in hits)


def test_the_domain_rule_never_clears_itself() -> None:
    """It cannot distinguish the client's GC writing about new work from an
    employee writing about something adverse to their employer, so it always
    reaches a human."""
    hits = check(
        [],
        [client("cli-1", "Vantage Property Group LLP", domains=["vantagepropertygroup.com"])],
        [matter("mat-1", "cli-1", [])],
        from_email="anyone@vantagepropertygroup.com",
    )
    assert len(hits) == 1
    assert hits[0].severity == ConflictSeverity.WEAK


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def test_every_hit_cites_a_record_a_field_and_a_value() -> None:
    """'The system flagged a conflict' is useless to whoever has to clear it."""
    hits = check(
        [party("Meridian Mfg. Corp.", PartyRole.OPPOSING)],
        [client("cli-1", "Meridian Manufacturing Corp.")],
        [matter("mat-1", "cli-1", [])],
    )
    for hit in hits:
        assert hit.rule_id and hit.matched_record_id and hit.matched_field
        assert hit.matched_value and hit.inquiry_party
        assert len(hit.explanation) > 60


def test_hits_are_ordered_most_serious_first() -> None:
    hits = check(
        [party("Meridian Manufacturing Corp.", PartyRole.OPPOSING),
         party("Granite Bay Textiles L.L.C.", PartyRole.PROSPECTIVE_CLIENT)],
        [client("cli-1", "Meridian Manufacturing Corp.", domains=["stranger.example"])],
        [matter("mat-1", "cli-1", ["Granite Bay Textiles L.L.C."])],
    )
    ranks = [h.severity.rank for h in hits]
    assert ranks == sorted(ranks, reverse=True)


def test_two_rules_firing_on_one_inquiry_are_both_reported() -> None:
    hits = check(
        [party("Meridian Manufacturing Corp.", PartyRole.OPPOSING),
         party("Granite Bay Textiles L.L.C.", PartyRole.PROSPECTIVE_CLIENT)],
        [client("cli-1", "Meridian Manufacturing Corp.")],
        [matter("mat-1", "cli-1", ["Granite Bay Textiles L.L.C."])],
    )
    assert {h.rule_id for h in hits} == {
        RULE_ADVERSE_PARTY_IS_CLIENT,
        RULE_PROSPECT_WAS_ADVERSE_PARTY,
    }
