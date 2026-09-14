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
