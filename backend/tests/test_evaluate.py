"""The eval harness, and one guard on the claim the whole design rests on."""

from __future__ import annotations

import pytest

from intake.db.seed import build_corpus, seed_database
from intake.llm.client import ResponseCache
from intake.paths import LLM_CACHE_PATH
from intake.pipeline.evaluate import evaluate


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    """Scores the configuration the demo actually ships.

    `replay` means real cached Claude output, and `build_corpus` means the
    naturalized prose -- measuring the stub against template prose would produce
    confident numbers about an artefact nobody runs.
    """
    if not LLM_CACHE_PATH.exists() or not len(ResponseCache(LLM_CACHE_PATH)):
        pytest.skip("no response cache checked in; run `make cache` with an API key")
    db_path = tmp_path_factory.mktemp("eval") / "intake.sqlite3"
    seed_database(build_corpus(20260517, verbose=False), db_path)
    return evaluate(db_path, "replay")


def test_every_email_is_scored(report) -> None:
    assert report.total == 51
    assert report.classify_failures == 0
    assert report.extract_failures == 0


def test_confidence_separates_right_answers_from_wrong_ones(report) -> None:
    """The load-bearing claim. If confidence on correct classifications is not
    meaningfully higher than on incorrect ones, the number is decorative and no
    abstention threshold can do anything useful with it.

    It holds under the stub too, because the separation comes from the composite
    rule -- span grounding and validators -- not from the model's self-report.
    """
    right = sum(report.conf_when_right) / len(report.conf_when_right)
    wrong = sum(report.conf_when_wrong) / len(report.conf_when_wrong)
    assert right - wrong > 0.05, f"confidence is not informative: {right:.3f} vs {wrong:.3f}"


def test_the_system_is_less_confident_on_deliberately_ambiguous_mail(report) -> None:
    ambiguous = sum(report.conf_on_ambiguous) / len(report.conf_on_ambiguous)
    clear = sum(report.conf_on_clear) / len(report.conf_on_clear)
    assert clear > ambiguous, f"no hesitation on hard mail: {clear:.3f} vs {ambiguous:.3f}"


def test_most_extracted_values_are_grounded_in_the_source(report) -> None:
    total = sum(report.span_status.values())
    grounded = report.span_status["verified"] + report.span_status["normalized"]
    assert total > 100
    assert grounded / total > 0.75, "too many values cannot be pointed at"


def test_evaluation_is_reproducible(report, tmp_path) -> None:
    db_path = tmp_path / "intake.sqlite3"
    seed_database(build_corpus(20260517, verbose=False), db_path)
    first = evaluate(db_path, "replay")
    second = evaluate(db_path, "replay")
    assert first.classify_correct == second.classify_correct
    assert first.span_status == second.span_status


def test_the_conflict_rules_catch_every_planted_trap(report) -> None:
    """With a perfect extractor, all three trap shapes must be caught. This
    isolates the rules from the extractor: if this fails, the conflicts logic is
    broken, not the model."""
    assert report.traps_total == 7
    assert report.traps_caught_oracle == report.traps_total, (
        f"rules missed: {report.trap_misses_oracle}"
    )


def test_end_to_end_trap_misses_are_extraction_failures_only(report) -> None:
    """Traps the pipeline misses end-to-end should be names that were never
    extracted -- a different bug, in a different module."""
    assert set(report.trap_misses_oracle) == set()
    assert report.traps_caught <= report.traps_caught_oracle


def test_the_rules_do_not_assert_identity_promiscuously(report) -> None:
    """Conflicts on untrapped emails are expected -- the generator seeds real
    ones. What must stay small is the number graded PROBABLE, which asserts that
    two names are the same entity."""
    assert report.untrapped_probable <= 6, (
        f"{report.untrapped_probable} untrapped emails claim a definite identity match"
    )


def test_the_policy_never_acts_when_it_should_have_asked(report) -> None:
    """The asymmetry the whole design rests on. Over-abstaining costs a reviewer a
    minute; under-abstaining is the failure this system exists to prevent, so it
    is asserted at zero rather than folded into an accuracy average."""
    assert report.under_abstained == [], (
        f"proceeded on emails that should have reached a human: {report.under_abstained}"
    )


def test_the_policy_is_not_abstaining_on_everything(report) -> None:
    """A system that sends every email to review is not exercising judgement."""
    assert report.decision_agree >= int(report.total * 0.85)
    assert len(report.over_abstained) <= 8
