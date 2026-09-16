"""Span grounding is the mechanism the auditability claim rests on. If a quote can
be 'verified' without appearing in the email, every confidence score is theatre."""

from __future__ import annotations

import pytest

from intake.domain.enums import SpanStatus
from intake.domain.spans import excerpt, ground_quote, is_weak

SOURCE = (
    "Encroachment issue\n\n"
    "Good morning,\n\n"
    "We signed on January 28, 2026 at a price of $559,000 and we are set to close\n"
    "April 22, 2026. The property is in Fulton County, Georgia.\n"
)


def test_exact_quote_is_verified_with_correct_offsets() -> None:
    span = ground_quote(SOURCE, "January 28, 2026")
    assert span.status == SpanStatus.VERIFIED
    assert SOURCE[span.start : span.end] == "January 28, 2026"


def test_offsets_always_slice_back_to_the_quote() -> None:
    for quote in ["$559,000", "Fulton County, Georgia", "Good morning", "close\nApril 22"]:
        span = ground_quote(SOURCE, quote)
        assert span.located
        assert SOURCE[span.start : span.end].lower().split() == span.quote.lower().split()


def test_rewrapped_quote_is_normalized_not_rejected() -> None:
    """A model that re-wraps a line has still pointed at real text."""
    span = ground_quote(SOURCE, "set to close April 22, 2026")
    assert span.status == SpanStatus.NORMALIZED
    assert span.located


def test_recased_quote_is_normalized() -> None:
    span = ground_quote(SOURCE, "FULTON COUNTY, GEORGIA")
    assert span.status == SpanStatus.NORMALIZED
    assert SOURCE[span.start : span.end] == "Fulton County, Georgia"


def test_invented_quote_is_not_found_and_is_preserved() -> None:
    """The hallucinated text is kept so a reviewer can see what was claimed."""
    span = ground_quote(SOURCE, "the seller has agreed to indemnify us")
    assert span.status == SpanStatus.NOT_FOUND
    assert not span.located
    assert span.quote == "the seller has agreed to indemnify us"
    assert span.start == -1


def test_a_paraphrase_does_not_ground() -> None:
    """The whole point: close-but-not-exact must fail, or the check is worthless."""
    span = ground_quote(SOURCE, "we signed in late January for about $560,000")
    assert span.status == SpanStatus.NOT_FOUND


@pytest.mark.parametrize("quote", [None, "", "   ", "\n"])
def test_absent_quote_returns_no_span(quote) -> None:
    assert ground_quote(SOURCE, quote) is None


def test_occurrences_are_counted() -> None:
    span = ground_quote("alpha beta alpha beta alpha", "alpha")
    assert span.occurrences == 3


def test_short_or_repeated_quotes_are_weak() -> None:
    assert is_weak(ground_quote(SOURCE, "on"))
    assert is_weak(ground_quote("x. y. z. w. v.", "."))
    assert not is_weak(ground_quote(SOURCE, "Fulton County, Georgia"))
    assert not is_weak(ground_quote(SOURCE, "nowhere in this text"))


def test_excerpt_shows_surrounding_context() -> None:
    span = ground_quote(SOURCE, "$559,000")
    text = excerpt(SOURCE, span, padding=20)
    assert "$559,000" in text
    assert len(text) > len("$559,000")
    assert excerpt(SOURCE, ground_quote(SOURCE, "not here")) == ""


def test_the_model_sees_exactly_the_text_spans_index_into() -> None:
    """A regression guard. The prompt once rendered its own copy of the email
    including headers, while spans were grounded against subject+body only -- so a
    quote naming the sender could never verify, and correct extractions were
    penalised for evidence they actually had."""
    from datetime import datetime

    from intake.domain.models import Email
    from intake.llm.prompts import build_classification_request

    email = Email(
        id="em-x",
        received_at=datetime(2026, 3, 1, 9, 0),
        from_name="Victor Kavanagh",
        from_email="victor@example.com",
        to_address="info@firm.example",
        subject="Fired",
        body="I was let go.",
    )
    assert email.searchable_text in build_classification_request(email).user
    assert ground_quote(email.searchable_text, "Victor Kavanagh").located
