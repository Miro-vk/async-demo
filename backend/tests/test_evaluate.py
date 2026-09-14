"""The eval harness, and one guard on the claim the whole design rests on."""

from __future__ import annotations

import pytest

from intake.db.seed import build_corpus, seed_database
from intake.pipeline.evaluate import evaluate


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("eval") / "intake.sqlite3"
    # build_corpus, not generate_corpus: the demo runs on naturalized prose, and
    # measuring the template prose would score an artefact nobody ships.
    seed_database(build_corpus(20260517, verbose=False), db_path)
    return evaluate(db_path, "stub")


def test_every_email_is_scored(report) -> None:
    assert report.total == 51
    assert report.classify_failures == 0
    assert report.extract_failures == 0


def test_confidence_separates_right_answers_from_wrong_ones(report) -> None:
    """The load-bearing claim. If confidence on correct classifications is not
    meaningfully higher than on incorrect ones, the number is decorative and no
    abstention threshold can do anything useful with it.

    Note this holds even with the stub provider, because the separation comes from
    the composite rule -- span grounding and validators -- rather than from the
    model's self-report.
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


def test_evaluation_is_reproducible(tmp_path) -> None:
    db_path = tmp_path / "intake.sqlite3"
    seed_database(build_corpus(20260517, verbose=False), db_path)
    first = evaluate(db_path, "stub")
    second = evaluate(db_path, "stub")
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
