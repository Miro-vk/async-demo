"""Wiring: provider in, typed stage output plus a trace out.

This is the impure half of each stage. It makes the network call, records what
happened, and hands the raw text to a pure parser in `intake.domain`. All the
decisions live on the other side of that handoff, which is why they are testable
without any of this.

Note that a ParseFailure is returned, not raised. The pipeline is expected to
encounter malformed model output; the answer is to route the email to a human, and
an exception thrown from the middle of a batch would take the other fifty with it.
"""

from __future__ import annotations

from dataclasses import dataclass

from intake.domain.classify import parse_classification
from intake.domain.extract import parse_extraction
from intake.domain.models import (
    Classification,
    Email,
    Extraction,
    ParseFailure,
    Resolution,
    StageTrace,
)
from intake.llm.client import Provider
from intake.llm.prompts import build_classification_request, build_extraction_request


@dataclass(frozen=True)
class StageResult:
    """A stage's output alongside the record of how it was produced."""

    output: Classification | Extraction | Resolution | ParseFailure
    trace: StageTrace

    @property
    def failed(self) -> bool:
        return isinstance(self.output, ParseFailure)


def _run(email: Email, provider: Provider, spec, parse) -> StageResult:
    response = provider.complete(spec)
    parsed = parse(email, response.text)
    failed = isinstance(parsed, ParseFailure)
    return StageResult(
        output=parsed,
        trace=StageTrace(
            email_id=email.id,
            stage=spec.stage,
            provider=response.provider,
            model=response.model,
            cached=response.cached,
            latency_ms=response.latency_ms,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            raw_response=response.text,
            ok=not failed,
            failure_reason=parsed.reason if failed else None,
        ),
    )


def run_classify(email: Email, provider: Provider) -> StageResult:
    return _run(email, provider, build_classification_request(email), parse_classification)


def run_extract(email: Email, provider: Provider) -> StageResult:
    return _run(email, provider, build_extraction_request(email), parse_extraction)


def run_resolve(
    email: Email, extraction: Extraction | ParseFailure | None, records
) -> StageResult:
    """Stage 3. Deterministic -- no provider, no network, no model.

    Still produces a trace, because the UI shows all four stages side by side and
    "this conclusion came from rules, not from a model" is one of the more useful
    things a reviewer can know about a step.
    """
    from intake.domain.resolve import resolve

    usable = extraction if isinstance(extraction, Extraction) else None
    resolution = resolve(email, usable, records)
    return StageResult(
        output=resolution,
        trace=StageTrace(
            email_id=email.id,
            stage="resolve",
            provider="deterministic",
            model="rules",
            cached=False,
            raw_response="",
            ok=True,
        ),
    )
