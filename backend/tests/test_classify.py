"""Stage 1 is a pure function of (email, response text). No network, no mocks."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from intake.domain.classify import coerce_label, parse_classification
from intake.domain.enums import EmailClass, SpanStatus
from intake.domain.models import Email, ParseFailure


@pytest.fixture
def email() -> Email:
    return Email(
        id="em-test",
        received_at=datetime(2026, 3, 1, 9, 0),
        from_name="Delphine Winslow",
        from_email="d.winslow@example.com",
        to_address="info@firm.example",
        subject="survey problem - we close April 23, 2026",
        body="We are under contract on 3784 Dunmore St and the survey came back wrong.",
    )


def response(**kwargs) -> str:
    return json.dumps({"label": "new_matter", "confidence": 0.9, **kwargs})


def test_a_clean_response_produces_a_grounded_classification(email) -> None:
    result = parse_classification(email, response(quote="under contract", rationale="Buyer."))
    assert result.label.value == EmailClass.NEW_MATTER
    assert result.label.span.status == SpanStatus.VERIFIED
    assert result.label.confidence == 0.9
    assert result.rationale == "Buyer."


def test_code_fences_and_preamble_are_tolerated(email) -> None:
    raw = 'Sure! Here is my analysis:\n```json\n{"label": "new_matter", "confidence": 0.8}\n```\nHope that helps.'
    result = parse_classification(email, raw)
    assert not isinstance(result, ParseFailure)
    assert result.label.value == EmailClass.NEW_MATTER


@pytest.mark.parametrize(
    "spelling,expected",
    [
        ("new_matter", EmailClass.NEW_MATTER),
        ("New Matter", EmailClass.NEW_MATTER),
        ("  EXISTING_CLIENT ", EmailClass.EXISTING_CLIENT),
        ("spam", EmailClass.VENDOR_OR_SPAM),
        ("vendor or spam", EmailClass.VENDOR_OR_SPAM),
        ("unclear", EmailClass.UNCLEAR),
    ],
)
def test_label_spellings_models_actually_produce(spelling, expected) -> None:
    assert coerce_label(spelling) == expected


def test_an_unrecognised_label_is_not_quietly_turned_into_unclear() -> None:
    """UNCLEAR means the classifier judged the email to fit nothing. Mapping
    garbage onto it would blur that with 'the response was broken', and those two
    route differently."""
    assert coerce_label("probably a new matter?") is None
    assert coerce_label(None) is None
    assert coerce_label(42) is None


def test_a_fabricated_quote_survives_but_destroys_confidence(email) -> None:
    result = parse_classification(
        email, response(confidence=0.99, quote="the client has already retained us")
    )
    assert result.label.value == EmailClass.NEW_MATTER
    assert result.label.span.status == SpanStatus.NOT_FOUND
    assert result.label.self_reported == 0.99
    assert result.label.confidence <= 0.4


def test_a_missing_quote_is_allowed_but_costs_confidence(email) -> None:
    result = parse_classification(email, response(confidence=0.9))
    assert result.label.span is None
    assert result.label.confidence < 0.9


def test_the_quote_may_be_found_in_the_subject(email) -> None:
    result = parse_classification(email, response(quote="survey problem"))
    assert result.label.span.status == SpanStatus.VERIFIED


@pytest.mark.parametrize(
    "raw,reason_fragment",
    [
        ("", "empty"),
        ("I think this is probably a new matter.", "no JSON object"),
        ('{"label": "new_matter", ', "truncated"),
        ('{"label": "new_matter" "confidence": 1}', "not valid JSON"),
        ('["new_matter"]', "expected a JSON object"),
        ('{"label": "banana"}', "unrecognised label"),
        ('{"confidence": 0.9}', "unrecognised label"),
    ],
)
def test_bad_responses_become_failures_not_exceptions(email, raw, reason_fragment) -> None:
    result = parse_classification(email, raw)
    assert isinstance(result, ParseFailure)
    assert result.stage == "classify"
    assert reason_fragment in result.reason


def test_a_failure_keeps_the_raw_response_for_investigation(email) -> None:
    result = parse_classification(email, "total nonsense from the model")
    assert result.raw_excerpt == "total nonsense from the model"


def test_percentage_confidence_is_accepted(email) -> None:
    result = parse_classification(email, '{"label": "new_matter", "confidence": "85%"}')
    assert result.label.self_reported == pytest.approx(0.85)


def test_out_of_range_confidence_is_clamped(email) -> None:
    result = parse_classification(email, '{"label": "new_matter", "confidence": 7.5}')
    assert 0.0 <= result.label.confidence <= 1.0
    assert result.label.self_reported == 1.0
