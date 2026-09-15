"""Stage 2. The behaviour worth pinning down is what happens when the model is
partly wrong: a missing field is not a failure, a malformed list entry is dropped
rather than repaired, and neither one silently disappears from the trace."""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from intake.domain.enums import PartyRole, PracticeArea, SpanStatus, ValidatorStatus
from intake.domain.extract import parse_amount_value, parse_date_value, parse_extraction
from intake.domain.models import Email, ParseFailure

BODY = (
    "I was let go from Ironwood Aggregates PLLC on January 21, 2026 and I believe "
    "it was retaliation. On November 9, 2025 I reported the falsified logs to HR.\n\n"
    "My salary was $118,000. I'm in King County, Washington."
)


@pytest.fixture
def email() -> Email:
    return Email(
        id="em-test",
        received_at=datetime(2026, 2, 1, 9, 0),
        from_name="Victor Kavanagh",
        from_email="victor@example.com",
        to_address="info@firm.example",
        subject="Fired after reporting falsified safety logs",
        body=BODY,
    )


FULL = {
    "parties": [
        {"name": "Victor Kavanagh", "role": "prospective_client",
         "is_organization": False, "confidence": 0.9, "quote": "Victor Kavanagh"},
        {"name": "Ironwood Aggregates PLLC", "role": "opposing",
         "is_organization": True, "confidence": 0.95, "quote": "Ironwood Aggregates PLLC"},
    ],
    "matter_type": {"value": "employment", "confidence": 0.9, "quote": "I was let go from"},
    "jurisdiction": {"value": "King County, Washington", "confidence": 0.95,
                     "quote": "King County, Washington"},
    "key_dates": [
        {"label": "termination", "value": "2026-01-21", "confidence": 0.9,
         "quote": "January 21, 2026"},
    ],
    "amounts": [
        {"label": "salary", "value": 118000, "confidence": 0.9, "quote": "$118,000"},
    ],
    "summary": "Terminated after a safety report.",
}


def test_a_clean_response_grounds_every_field(email) -> None:
    result = parse_extraction(email, json.dumps(FULL))
    assert result.matter_type.value == PracticeArea.EMPLOYMENT
    assert result.jurisdiction.value == "King County, Washington"
    assert len(result.parties) == 2
    assert result.parse_warnings == []

    for field in [result.matter_type, result.jurisdiction] + [p.name for p in result.parties]:
        assert field.span.status == SpanStatus.VERIFIED
        assert field.confidence >= 0.85


def test_opposing_parties_are_reachable_by_role(email) -> None:
    result = parse_extraction(email, json.dumps(FULL))
    assert [p.name.value for p in result.opposing_parties] == ["Ironwood Aggregates PLLC"]
    assert [p.name.value for p in result.client_side_parties] == ["Victor Kavanagh"]
    assert result.parties[1].role == PartyRole.OPPOSING


def test_dates_and_amounts_carry_their_own_spans(email) -> None:
    result = parse_extraction(email, json.dumps(FULL))
    assert result.key_dates[0].value.value == date(2026, 1, 21)
    assert result.key_dates[0].value.span.status == SpanStatus.VERIFIED
    assert result.amounts[0].value.value == 118000.0
    assert result.amounts[0].value.span.status == SpanStatus.VERIFIED


# --------------------------------------------------------------------------- #
# Missing is not malformed
# --------------------------------------------------------------------------- #


def test_an_empty_object_is_a_valid_extraction_that_found_nothing(email) -> None:
    """Every field absent is a legitimate answer for an email with no facts in it.
    It must not look like a broken response."""
    result = parse_extraction(email, "{}")
    assert not isinstance(result, ParseFailure)
    assert result.matter_type.value is None
    assert result.jurisdiction.value is None
    assert result.parties == []
    assert result.matter_type.confidence < 0.5


def test_a_null_field_is_low_confidence_not_an_error(email) -> None:
    result = parse_extraction(email, json.dumps({"jurisdiction": {"value": None}}))
    assert result.jurisdiction.value is None
    assert result.jurisdiction.confidence < 0.5


def test_a_bare_scalar_field_is_accepted_and_grounded_from_its_value(email) -> None:
    """When the model gives a value but no quote, the system looks for the value
    itself. The characters really are in the email, so throwing that evidence away
    would be wasteful -- but the span is marked derived and scores below a quote
    the model actually produced."""
    result = parse_extraction(email, json.dumps({"jurisdiction": "King County, Washington"}))
    assert result.jurisdiction.value == "King County, Washington"
    assert result.jurisdiction.span is not None
    assert result.jurisdiction.span.derived is True
    assert result.jurisdiction.span.located
    text = email.searchable_text
    assert text[result.jurisdiction.span.start : result.jurisdiction.span.end] == (
        "King County, Washington"
    )


def test_a_value_that_is_not_in_the_email_does_not_ground(email) -> None:
    result = parse_extraction(email, json.dumps({"jurisdiction": "Cook County, Illinois"}))
    assert result.jurisdiction.value == "Cook County, Illinois"
    assert result.jurisdiction.span is None
    assert result.jurisdiction.confidence < 0.6


def test_a_supplied_quote_that_is_missing_is_never_backfilled(email) -> None:
    """The one case backfill must not touch. A model that cited text which does
    not exist has done the thing this mechanism exists to catch; quietly finding
    the value elsewhere would hide it."""
    payload = {
        "jurisdiction": {
            "value": "King County, Washington",
            "confidence": 0.95,
            "quote": "venue is agreed to be King County",
        }
    }
    result = parse_extraction(email, json.dumps(payload))
    assert result.jurisdiction.span.status == SpanStatus.NOT_FOUND
    assert result.jurisdiction.span.derived is False
    assert result.jurisdiction.confidence <= 0.4


# --------------------------------------------------------------------------- #
# Partial failure
# --------------------------------------------------------------------------- #


def test_one_bad_party_does_not_lose_the_others(email) -> None:
    payload = dict(FULL)
    payload["parties"] = [FULL["parties"][0], {"role": "opposing"}, FULL["parties"][1]]
    result = parse_extraction(email, json.dumps(payload))
    assert len(result.parties) == 2
    assert any("party[1]" in w for w in result.parse_warnings)


def test_a_dropped_entry_is_always_recorded(email) -> None:
    """Silently returning four of five parties is worse than returning four and
    saying so."""
    payload = {"key_dates": [{"label": "x", "value": "sometime last spring"}]}
    result = parse_extraction(email, json.dumps(payload))
    assert result.key_dates == []
    assert result.parse_warnings
    assert "sometime last spring" in result.parse_warnings[0]


def test_a_list_field_sent_as_a_scalar_is_reported(email) -> None:
    result = parse_extraction(email, json.dumps({"parties": "Victor Kavanagh"}))
    assert result.parties == []
    assert any("not a list" in w for w in result.parse_warnings)


def test_an_unrecognised_role_is_flagged_not_guessed(email) -> None:
    payload = {"parties": [{"name": "Victor Kavanagh", "role": "the aggrieved"}]}
    result = parse_extraction(email, json.dumps(payload))
    assert result.parties[0].role == PartyRole.UNKNOWN
    assert any("unrecognised role" in w for w in result.parse_warnings)


# --------------------------------------------------------------------------- #
# Validators feeding confidence
# --------------------------------------------------------------------------- #


def test_a_placeholder_party_name_fails_validation(email) -> None:
    payload = {"parties": [{"name": "Unknown", "role": "opposing", "confidence": 0.9,
                            "quote": "Ironwood Aggregates PLLC"}]}
    result = parse_extraction(email, json.dumps(payload))
    party = result.parties[0]
    assert party.name.validator == ValidatorStatus.FAILED
    assert party.name.confidence < 0.4
    assert "placeholder" in party.name.validator_note


def test_an_implausible_date_fails_validation(email) -> None:
    payload = {"key_dates": [{"label": "incident", "value": "1908-04-02", "confidence": 0.9}]}
    result = parse_extraction(email, json.dumps(payload))
    assert result.key_dates[0].value.validator == ValidatorStatus.FAILED
    assert result.key_dates[0].value.confidence < 0.3


def test_a_practice_area_we_do_not_handle_fails_validation(email) -> None:
    payload = {"matter_type": {"value": "maritime_salvage", "confidence": 0.9}}
    result = parse_extraction(email, json.dumps(payload))
    assert result.matter_type.value is None
    assert result.matter_type.validator == ValidatorStatus.FAILED


def test_an_unrecognised_jurisdiction_fails_validation(email) -> None:
    payload = {"jurisdiction": {"value": "Wakanda", "confidence": 0.9}}
    result = parse_extraction(email, json.dumps(payload))
    assert result.jurisdiction.validator == ValidatorStatus.FAILED
    assert result.jurisdiction.confidence < 0.4


# --------------------------------------------------------------------------- #
# Value coercion
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw,expected",
    [("2026-01-21", date(2026, 1, 21)), ("January 21, 2026", date(2026, 1, 21)),
     ("Jan 21, 2026", date(2026, 1, 21)), ("1/21/2026", date(2026, 1, 21)),
     ("21 January 2026", date(2026, 1, 21)), ("next Tuesday", None), (None, None)],
)
def test_date_surface_forms(raw, expected) -> None:
    assert parse_date_value(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [(118000, 118000.0), ("$118,000", 118000.0), ("118k", 118000.0),
     ("$2.5m", 2500000.0), ("a lot", None), (True, None), (None, None)],
)
def test_amount_surface_forms(raw, expected) -> None:
    assert parse_amount_value(raw) == expected


def test_unparseable_response_is_a_failure(email) -> None:
    result = parse_extraction(email, "I found a few things but here they are in prose.")
    assert isinstance(result, ParseFailure)
    assert result.stage == "extract"
