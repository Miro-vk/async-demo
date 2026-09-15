"""How a confidence score is arrived at.

The model's self-reported confidence is one input, not the answer. LLM self-reports
are weakly calibrated and systematically optimistic, and a system that surfaced them
as "confidence" to a lawyer would be lying with a number.

The score attached to every field is therefore computed here from three things:

    final = self_report x span_factor x validator_factor

  self_report      what the model said, defaulting to 0.5 when it said nothing
  span_factor      whether the model could point at text that actually exists
  validator_factor whether the value survived a field-specific sanity check

Multiplicative, so any one bad signal drags the result down, and every term is
visible in the trace: a reviewer can see 0.9 x 0.35 x 1.0 and understand instantly
that the number is low because the quote could not be found.

These constants are the system's caution dial, alongside the thresholds in
domain.policy. They are deliberately in one place.
"""

from __future__ import annotations

from intake.domain.enums import SpanStatus, ValidatorStatus
from intake.domain.models import Span

DEFAULT_SELF_REPORT = 0.5

SPAN_FACTOR = {
    SpanStatus.VERIFIED: 1.00,
    SpanStatus.NORMALIZED: 0.95,   # re-wrapped or re-cased, but real text
    SpanStatus.ABSENT: 0.60,       # no evidence offered
    SpanStatus.NOT_FOUND: 0.30,    # evidence offered and it does not exist
}

VALIDATOR_FACTOR = {
    ValidatorStatus.PASSED: 1.00,
    ValidatorStatus.NOT_APPLICABLE: 1.00,
    ValidatorStatus.FAILED: 0.35,
}

# A value whose quote is not in the email never reads as confident, however sure
# the model claimed to be.
UNGROUNDED_CEILING = 0.40

# A quote that is a couple of characters long, or that appears all over the email,
# grounds without really being evidence.
WEAK_SPAN_PENALTY = 0.85

# The system found the value in the source itself because the model offered no
# quote. The characters are genuinely there, so this beats no evidence at all --
# but the model never showed it was reading them, so it trails a real citation.
DERIVED_SPAN_FACTOR = 0.85


def score(
    self_reported: float | None,
    span: Span | None,
    validator: ValidatorStatus = ValidatorStatus.NOT_APPLICABLE,
    weak_span: bool = False,
) -> float:
    """Combine the three signals into the number a human will actually see."""
    base = DEFAULT_SELF_REPORT if self_reported is None else _clamp(self_reported)
    span_status = span.status if span is not None else SpanStatus.ABSENT

    span_factor = (
        DERIVED_SPAN_FACTOR
        if span is not None and span.derived
        else SPAN_FACTOR[span_status]
    )
    result = base * span_factor * VALIDATOR_FACTOR[validator]
    if weak_span:
        result *= WEAK_SPAN_PENALTY
    if span_status == SpanStatus.NOT_FOUND:
        result = min(result, UNGROUNDED_CEILING)
    return round(_clamp(result), 4)


def explain(
    self_reported: float | None,
    span: Span | None,
    validator: ValidatorStatus = ValidatorStatus.NOT_APPLICABLE,
    weak_span: bool = False,
) -> str:
    """One line a reviewer can read to see where the number came from."""
    base = DEFAULT_SELF_REPORT if self_reported is None else _clamp(self_reported)
    span_status = span.status if span is not None else SpanStatus.ABSENT
    parts = [
        f"self-reported {base:.2f}",
        (
            f"span derived x{DERIVED_SPAN_FACTOR:.2f}"
            if span is not None and span.derived
            else f"span {span_status.value} x{SPAN_FACTOR[span_status]:.2f}"
        ),
        f"validator {validator.value} x{VALIDATOR_FACTOR[validator]:.2f}",
    ]
    if weak_span:
        parts.append(f"weak quote x{WEAK_SPAN_PENALTY:.2f}")
    return " | ".join(parts) + f" = {score(self_reported, span, validator, weak_span):.2f}"


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
