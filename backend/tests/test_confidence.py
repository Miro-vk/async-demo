"""Confidence must never be the model's self-report passed through unchanged."""

from __future__ import annotations

from intake.domain.confidence import UNGROUNDED_CEILING, score
from intake.domain.enums import SpanStatus, ValidatorStatus
from intake.domain.models import Span


def span(status: SpanStatus) -> Span:
    return Span(start=0, end=5, quote="hello", status=status)


def test_a_verified_span_preserves_a_confident_self_report() -> None:
    assert score(0.92, span(SpanStatus.VERIFIED)) == 0.92


def test_an_ungrounded_value_can_never_look_confident() -> None:
    """The headline behaviour: the model insisting it is certain does not matter
    if it cannot point at the text. However sure it claims to be, the score stays
    under the ceiling -- which sits below every review threshold in policy."""
    for self_reported in [0.5, 0.8, 0.95, 1.0]:
        ungrounded = score(self_reported, span(SpanStatus.NOT_FOUND))
        assert ungrounded <= UNGROUNDED_CEILING
        assert ungrounded < score(self_reported, span(SpanStatus.VERIFIED))


def test_a_missing_quote_costs_less_than_a_fabricated_one() -> None:
    assert score(0.9, None) > score(0.9, span(SpanStatus.NOT_FOUND))


def test_a_failed_validator_drags_the_score_down(  ) -> None:
    grounded = span(SpanStatus.VERIFIED)
    assert score(0.9, grounded, ValidatorStatus.FAILED) < 0.4
    assert score(0.9, grounded, ValidatorStatus.PASSED) == 0.9


def test_a_normalized_span_is_only_slightly_discounted() -> None:
    assert 0.85 < score(0.9, span(SpanStatus.NORMALIZED)) < 0.9


def test_a_weak_quote_is_penalised() -> None:
    grounded = span(SpanStatus.VERIFIED)
    assert score(0.9, grounded, weak_span=True) < score(0.9, grounded)


def test_missing_self_report_does_not_default_to_certain() -> None:
    assert score(None, span(SpanStatus.VERIFIED)) == 0.5


def test_output_is_always_a_probability() -> None:
    for self_reported in [-5.0, 0.0, 0.5, 1.0, 17.0, None]:
        for status in SpanStatus:
            for validator in ValidatorStatus:
                value = score(self_reported, span(status), validator)
                assert 0.0 <= value <= 1.0
