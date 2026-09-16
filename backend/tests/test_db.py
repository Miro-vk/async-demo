"""Round-tripping the corpus through SQLite must not lose or reshape anything."""

from __future__ import annotations

import pytest

from intake.corpus.generate import generate_corpus
from intake.db import repo
from intake.db.seed import seed_database


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    corpus = generate_corpus(4242)
    db_path = tmp_path_factory.mktemp("db") / "intake.sqlite3"
    seed_database(corpus, db_path)
    conn = repo.connect(db_path)
    yield corpus, conn
    conn.close()


def test_every_record_survives_the_round_trip(seeded) -> None:
    corpus, conn = seeded
    assert repo.list_attorneys(conn) == sorted(corpus.attorneys, key=lambda a: a.id)
    assert repo.list_clients(conn) == sorted(corpus.clients, key=lambda c: c.id)
    assert repo.list_matters(conn) == sorted(corpus.matters, key=lambda m: m.id)
    assert repo.list_emails(conn) == sorted(corpus.emails, key=lambda e: e.id)


def test_ground_truth_round_trips_including_trap_references(seeded) -> None:
    corpus, conn = seeded
    stored = {g.email_id: g for g in repo.list_ground_truth(conn)}
    for gt in corpus.ground_truth:
        assert stored[gt.email_id] == gt


def test_seed_is_recorded_in_the_database(seeded) -> None:
    corpus, conn = seeded
    assert repo.get_meta(conn, "corpus_seed") == str(corpus.seed)


def test_get_email_returns_none_for_unknown_id(seeded) -> None:
    _, conn = seeded
    assert repo.get_email(conn, "em-does-not-exist") is None


def test_reseeding_is_idempotent(tmp_path) -> None:
    corpus = generate_corpus(99)
    db_path = tmp_path / "intake.sqlite3"
    seed_database(corpus, db_path)
    seed_database(corpus, db_path)
    conn = repo.connect(db_path)
    try:
        assert len(repo.list_emails(conn)) == len(corpus.emails)
        assert len(repo.list_clients(conn)) == len(corpus.clients)
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Pipeline runs and the review audit log
# --------------------------------------------------------------------------- #


def _sample_result(email_id="em-001", action=None):
    from intake.domain.enums import DecisionAction, ReviewReasonCode
    from intake.domain.models import Decision, PipelineResult, Reason, StageTrace

    return PipelineResult(
        email_id=email_id,
        decision=Decision(
            action=action or DecisionAction.REVIEW,
            reasons=[Reason(code=ReviewReasonCode.CONFLICT_HIT, message="they are our client",
                            rule_id="ADVERSE_PARTY_IS_CLIENT")],
        ),
        traces=[StageTrace(email_id=email_id, stage="classify", provider="replay", model="m")],
    )


def test_a_run_round_trips(seeded) -> None:
    from datetime import datetime

    _, conn = seeded
    result = _sample_result()
    repo.save_run(conn, result, datetime(2026, 3, 2, 10, 0))
    assert repo.get_run(conn, "em-001") == result


def test_reprocessing_replaces_the_run(seeded) -> None:
    from datetime import datetime

    from intake.domain.enums import DecisionAction

    _, conn = seeded
    repo.save_run(conn, _sample_result(action=DecisionAction.REVIEW), datetime(2026, 3, 2, 10, 0))
    repo.save_run(conn, _sample_result(action=DecisionAction.PROCEED), datetime(2026, 3, 2, 11, 0))
    assert repo.get_run(conn, "em-001").decision.action == DecisionAction.PROCEED
    assert len(repo.list_runs(conn)) == 1


def test_the_review_log_is_append_only(seeded) -> None:
    """A run is rewritten when an email is re-processed. Who approved what, and
    what they changed, must survive that."""
    from datetime import datetime

    from intake.domain.enums import ReviewOutcome
    from intake.domain.models import FieldEdit, ReviewAction

    _, conn = seeded
    for outcome in (ReviewOutcome.REJECTED, ReviewOutcome.APPROVED_WITH_EDITS):
        repo.record_review(
            conn,
            ReviewAction(
                email_id="em-002", outcome=outcome, reviewer="dana@firm.example",
                note="second look",
                edits=[FieldEdit(field_path="extraction.jurisdiction", original_value="Wakanda",
                                 corrected_value="Cook County, Illinois",
                                 edited_by="dana@firm.example",
                                 edited_at=datetime(2026, 3, 2, 11, 0))],
                acted_at=datetime(2026, 3, 2, 11, 0),
            ),
        )
    log = repo.list_review_actions(conn, "em-002")
    assert len(log) == 2, "recording a second review must not overwrite the first"
    assert log[0].outcome == ReviewOutcome.REJECTED
    assert log[1].edits[0].original_value == "Wakanda"


def test_the_queue_can_be_filtered_to_pending_review(seeded) -> None:
    from datetime import datetime

    from intake.domain.enums import DecisionAction

    _, conn = seeded
    conn.execute("DELETE FROM pipeline_runs")
    repo.save_run(conn, _sample_result("em-001", DecisionAction.REVIEW), datetime(2026, 3, 2, 10, 0))
    repo.save_run(conn, _sample_result("em-002", DecisionAction.PROCEED), datetime(2026, 3, 2, 10, 0))
    pending = repo.list_runs(conn, action=DecisionAction.REVIEW, pending_only=True)
    assert [r.email_id for r in pending] == ["em-001"]
    assert repo.queue_counts(conn)["review_pending"] == 1


def test_reseeding_after_processing_works(tmp_path) -> None:
    """A regression guard on a bug that corrupted the database.

    pipeline_runs holds a foreign key to emails, so wiping the corpus after the
    inbox had been processed was rejected -- and because the wipe ran as a script
    rather than a transaction, it deleted ground_truth first and only then hit the
    constraint. The result was a database with its emails intact and its ground
    truth gone, which every downstream tool read as a corpus with no answers.
    """
    from intake.domain.enums import DecisionAction
    from intake.pipeline.process import process_inbox

    db_path = tmp_path / "intake.sqlite3"
    seed_database(generate_corpus(20260517), db_path)
    process_inbox(db_path, provider_mode="stub", verbose=False)

    conn = repo.connect(db_path)
    try:
        assert len(repo.list_runs(conn)) == 51
    finally:
        conn.close()

    seed_database(generate_corpus(20260517), db_path)

    conn = repo.connect(db_path)
    try:
        assert len(repo.list_ground_truth(conn)) == 51, "ground truth must survive a re-seed"
        assert len(repo.list_emails(conn)) == 51
        assert repo.list_runs(conn) == [], "runs describing the old corpus must go"
    finally:
        conn.close()


def test_a_failed_seed_leaves_the_database_untouched(tmp_path, monkeypatch) -> None:
    """All or nothing. A half-applied seed is worse than a refused one."""
    db_path = tmp_path / "intake.sqlite3"
    corpus = generate_corpus(20260517)
    seed_database(corpus, db_path)

    def explode(*_args, **_kwargs):
        raise RuntimeError("boom, midway through")

    monkeypatch.setattr(repo, "_insert_all", explode)

    conn = repo.connect(db_path)
    try:
        with pytest.raises(RuntimeError):
            repo.insert_corpus(conn, corpus)
        assert len(repo.list_emails(conn)) == 51
        assert len(repo.list_ground_truth(conn)) == 51
    finally:
        conn.close()


def test_a_connection_survives_being_handed_to_another_thread(tmp_path) -> None:
    """FastAPI runs a sync dependency and the sync endpoint it feeds on two
    different threadpool threads, so a per-request connection is opened on one and
    used on the next. That is a handoff -- one request owns the connection for its
    whole life -- but sqlite3's default guard cannot tell a handoff from sharing
    and raises ProgrammingError, which surfaces as an intermittent 500 depending
    on which thread the pool happens to hand out.

    TestClient serves every request from one thread and so never reproduces it;
    this asserts the property directly instead of hoping a request trips it.
    """
    import threading

    db = tmp_path / "handoff.sqlite3"
    conn = repo.connect(db)
    repo.initialize(conn)

    failure: list[BaseException] = []

    def read_from_another_thread() -> None:
        try:
            conn.execute("SELECT 1").fetchone()
        except BaseException as exc:  # noqa: BLE001 - re-raised on the main thread
            failure.append(exc)

    worker = threading.Thread(target=read_from_another_thread)
    worker.start()
    worker.join()
    conn.close()

    assert not failure, f"connection is thread-bound: {failure[0]!r}"
