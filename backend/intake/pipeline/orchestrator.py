"""Run all four stages over one email and decide what happens to it.

Every email goes through every stage, including the solicitations. Uniform traces
make the UI legible -- four stages, always, some of them reporting that they found
nothing -- and the cost of running deterministic rules over a spam email is zero.
The decision, not the routing, is what differs.

The orchestrator holds no policy of its own. It sequences stages, collects traces,
and hands everything to `domain.policy` to judge.
"""

from __future__ import annotations

from intake.domain import policy
from intake.domain.dispatch import dispatch as run_dispatch
from intake.domain.models import (
    Attorney,
    Classification,
    Email,
    Extraction,
    ParseFailure,
    PipelineResult,
    StageTrace,
)
from intake.domain.policy import Thresholds
from intake.domain.records import FirmRecords
from intake.llm.client import Provider
from intake.pipeline.runner import run_classify, run_extract, run_resolve


def process_email(
    email: Email,
    provider: Provider,
    records: FirmRecords,
    attorneys: list[Attorney],
    thresholds: Thresholds = policy.DEFAULT,
) -> PipelineResult:
    traces: list[StageTrace] = []
    failures: list[ParseFailure] = []

    classify = run_classify(email, provider)
    traces.append(classify.trace)
    classification = classify.output if isinstance(classify.output, Classification) else None
    if classification is None:
        failures.append(classify.output)

    extract = run_extract(email, provider)
    traces.append(extract.trace)
    extraction = extract.output if isinstance(extract.output, Extraction) else None
    if extraction is None:
        failures.append(extract.output)

    resolve = run_resolve(email, extraction, records)
    traces.append(resolve.trace)
    resolution = resolve.output

    dispatch_result = run_dispatch(
        email, classification, extraction, resolution, records, attorneys
    )
    traces.append(
        StageTrace(
            email_id=email.id,
            stage="dispatch",
            provider="deterministic",
            model="rules+template",
            ok=True,
        )
    )

    decision = policy.decide(
        classification=classify.output,
        extraction=extract.output,
        resolution=resolution,
        dispatch=dispatch_result,
        thresholds=thresholds,
    )

    return PipelineResult(
        email_id=email.id,
        classification=classification,
        extraction=extraction,
        resolution=resolution,
        dispatch=dispatch_result,
        decision=decision,
        traces=traces,
        failures=failures,
    )
