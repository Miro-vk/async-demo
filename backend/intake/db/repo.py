"""SQLite persistence.

This module is allowed to import domain models; the domain is not allowed to import
this module. Dependencies point inward, and `tests/test_layering.py` enforces it.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

from intake.domain.enums import (
    ClientType,
    DecisionAction,
    EmailClass,
    MatterStatus,
    PracticeArea,
    ReviewOutcome,
)
from intake.domain.models import (
    Attorney,
    FieldEdit,
    PipelineResult,
    ReviewAction,
    ClientRecord,
    Corpus,
    Email,
    GroundTruth,
    MatterRecord,
)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(db_path: str | Path) -> sqlite3.Connection:
    # check_same_thread=False because FastAPI runs a sync dependency and the sync
    # endpoint it feeds on two different worker threads, so a per-request
    # connection is opened on one and used on the next. That is a handoff, not
    # sharing -- one request owns its connection for its whole life and closes it
    # -- but sqlite3's default guard cannot tell the two apart and 500s on the
    # difference, intermittently, depending on which thread the pool hands out.
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


def _j(value) -> str:
    return json.dumps(value, sort_keys=False)


# --------------------------------------------------------------------------- #
# Writes
# --------------------------------------------------------------------------- #


# Child-to-parent order: pipeline_runs and review_actions both reference emails,
# so re-seeding has to clear them first or the foreign key rejects the delete.
# Their contents describe the corpus being replaced, so keeping them would be
# wrong anyway -- a run that points at an email that no longer exists is worse
# than no run.
_WIPE_ORDER = (
    "review_actions",
    "pipeline_runs",
    "ground_truth",
    "matters",
    "emails",
    "clients",
    "attorneys",
    "schema_meta",
)


def insert_corpus(conn: sqlite3.Connection, corpus: Corpus) -> None:
    """Replace the corpus wholesale. Idempotent for a given seed, and atomic.

    `with conn` makes the whole replacement one transaction. The earlier version
    used executescript, which commits as it goes: a foreign key rejection partway
    through left the database with its emails intact and its ground truth gone,
    which is a far worse outcome than refusing to start.
    """
    with conn:
        for table in _WIPE_ORDER:
            conn.execute(f"DELETE FROM {table}")
        _insert_all(conn, corpus)


def _insert_all(conn: sqlite3.Connection, corpus: Corpus) -> None:
    conn.executemany(
        "INSERT INTO attorneys VALUES (?,?,?,?,?,?)",
        [
            (a.id, a.name, a.email, _j([p.value for p in a.practice_areas]),
             a.capacity, a.current_load)
            for a in corpus.attorneys
        ],
    )
    conn.executemany(
        "INSERT INTO clients VALUES (?,?,?,?,?,?)",
        [
            (c.id, c.display_name, c.client_type.value, _j(c.emails), _j(c.domains),
             c.opened_on.isoformat())
            for c in corpus.clients
        ],
    )
    conn.executemany(
        "INSERT INTO matters VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (m.id, m.client_id, m.caption, m.practice_area.value, m.status.value,
             m.opened_on.isoformat(), m.closed_on.isoformat() if m.closed_on else None,
             m.responsible_attorney_id, _j(m.adverse_parties))
            for m in corpus.matters
        ],
    )
    conn.executemany(
        "INSERT INTO emails VALUES (?,?,?,?,?,?,?,?)",
        [
            (e.id, e.received_at.isoformat(), e.from_name, e.from_email, e.to_address,
             e.subject, e.body, e.prose_source)
            for e in corpus.emails
        ],
    )
    conn.executemany(
        "INSERT INTO ground_truth VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (g.email_id, g.label.value,
             g.practice_area.value if g.practice_area else None,
             g.jurisdiction, g.prospective_client, _j(g.opposing_parties),
             _j(g.key_dates), _j(g.amounts), _j(g.planted_trap_rules), _j(g.trap_record_ids),
             g.expected_action.value, int(g.is_ambiguous), g.notes)
            for g in corpus.ground_truth
        ],
    )
    conn.executemany(
        "INSERT INTO schema_meta VALUES (?,?)",
        [("corpus_seed", str(corpus.seed)), ("email_count", str(len(corpus.emails)))],
    )


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


def _attorney(row: sqlite3.Row) -> Attorney:
    return Attorney(
        id=row["id"], name=row["name"], email=row["email"],
        practice_areas=[PracticeArea(p) for p in json.loads(row["practice_areas"])],
        capacity=row["capacity"], current_load=row["current_load"],
    )


def _client(row: sqlite3.Row) -> ClientRecord:
    return ClientRecord(
        id=row["id"], display_name=row["display_name"],
        client_type=ClientType(row["client_type"]),
        emails=json.loads(row["emails"]), domains=json.loads(row["domains"]),
        opened_on=date.fromisoformat(row["opened_on"]),
    )


def _matter(row: sqlite3.Row) -> MatterRecord:
    return MatterRecord(
        id=row["id"], client_id=row["client_id"], caption=row["caption"],
        practice_area=PracticeArea(row["practice_area"]),
        status=MatterStatus(row["status"]),
        opened_on=date.fromisoformat(row["opened_on"]),
        closed_on=date.fromisoformat(row["closed_on"]) if row["closed_on"] else None,
        responsible_attorney_id=row["responsible_attorney_id"],
        adverse_parties=json.loads(row["adverse_parties"]),
    )


def _email(row: sqlite3.Row) -> Email:
    return Email(
        id=row["id"], received_at=datetime.fromisoformat(row["received_at"]),
        from_name=row["from_name"], from_email=row["from_email"],
        to_address=row["to_address"], subject=row["subject"], body=row["body"],
        prose_source=row["prose_source"],
    )


def _ground_truth(row: sqlite3.Row) -> GroundTruth:
    return GroundTruth(
        email_id=row["email_id"], label=EmailClass(row["label"]),
        practice_area=PracticeArea(row["practice_area"]) if row["practice_area"] else None,
        jurisdiction=row["jurisdiction"], prospective_client=row["prospective_client"],
        opposing_parties=json.loads(row["opposing_parties"]),
        key_dates=json.loads(row["key_dates"]), amounts=json.loads(row["amounts"]),
        planted_trap_rules=json.loads(row["planted_trap_rules"]),
        trap_record_ids=json.loads(row["trap_record_ids"]),
        expected_action=DecisionAction(row["expected_action"]),
        is_ambiguous=bool(row["is_ambiguous"]), notes=row["notes"],
    )


def list_attorneys(conn: sqlite3.Connection) -> list[Attorney]:
    return [_attorney(r) for r in conn.execute("SELECT * FROM attorneys ORDER BY id")]


def list_clients(conn: sqlite3.Connection) -> list[ClientRecord]:
    return [_client(r) for r in conn.execute("SELECT * FROM clients ORDER BY id")]


def list_matters(conn: sqlite3.Connection) -> list[MatterRecord]:
    return [_matter(r) for r in conn.execute("SELECT * FROM matters ORDER BY id")]


def list_emails(conn: sqlite3.Connection) -> list[Email]:
    return [_email(r) for r in conn.execute("SELECT * FROM emails ORDER BY id")]


def get_email(conn: sqlite3.Connection, email_id: str) -> Email | None:
    row = conn.execute("SELECT * FROM emails WHERE id = ?", (email_id,)).fetchone()
    return _email(row) if row else None


def list_ground_truth(conn: sqlite3.Connection) -> list[GroundTruth]:
    return [_ground_truth(r) for r in conn.execute("SELECT * FROM ground_truth ORDER BY email_id")]


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM schema_meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


# --------------------------------------------------------------------------- #
# Pipeline runs and review actions
# --------------------------------------------------------------------------- #


def save_run(conn: sqlite3.Connection, result: PipelineResult, processed_at: datetime) -> None:
    """Upsert a run. Re-processing an email replaces its run; the review log is
    a separate table precisely so it is not replaced with it."""
    provider = result.traces[0].provider if result.traces else "unknown"
    model = result.traces[0].model if result.traces else "unknown"
    conn.execute(
        "INSERT INTO pipeline_runs VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(email_id) DO UPDATE SET "
        "processed_at=excluded.processed_at, action=excluded.action, "
        "reviewed=excluded.reviewed, provider=excluded.provider, "
        "model=excluded.model, result_json=excluded.result_json",
        (
            result.email_id,
            processed_at.isoformat(),
            result.decision.action.value,
            int(result.review is not None),
            provider,
            model,
            result.model_dump_json(),
        ),
    )
    conn.commit()


def get_run(conn: sqlite3.Connection, email_id: str) -> PipelineResult | None:
    row = conn.execute(
        "SELECT result_json FROM pipeline_runs WHERE email_id = ?", (email_id,)
    ).fetchone()
    return PipelineResult.model_validate_json(row["result_json"]) if row else None


def list_runs(
    conn: sqlite3.Connection,
    action: DecisionAction | None = None,
    pending_only: bool = False,
) -> list[PipelineResult]:
    sql = "SELECT result_json FROM pipeline_runs"
    clauses, params = [], []
    if action is not None:
        clauses.append("action = ?")
        params.append(action.value)
    if pending_only:
        clauses.append("reviewed = 0")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY email_id"
    return [PipelineResult.model_validate_json(r["result_json"]) for r in conn.execute(sql, params)]


def record_review(conn: sqlite3.Connection, action: ReviewAction) -> None:
    """Append to the audit log. Never updates an existing row."""
    conn.execute(
        "INSERT INTO review_actions (email_id, outcome, reviewer, note, edits_json, acted_at) "
        "VALUES (?,?,?,?,?,?)",
        (
            action.email_id,
            action.outcome.value,
            action.reviewer,
            action.note,
            _j([e.model_dump(mode="json") for e in action.edits]),
            action.acted_at.isoformat(),
        ),
    )
    conn.commit()


def list_review_actions(conn: sqlite3.Connection, email_id: str | None = None) -> list[ReviewAction]:
    sql = "SELECT * FROM review_actions"
    params: list = []
    if email_id:
        sql += " WHERE email_id = ?"
        params.append(email_id)
    sql += " ORDER BY id"
    return [
        ReviewAction(
            email_id=r["email_id"],
            outcome=ReviewOutcome(r["outcome"]),
            reviewer=r["reviewer"],
            note=r["note"],
            edits=[FieldEdit.model_validate(e) for e in json.loads(r["edits_json"])],
            acted_at=datetime.fromisoformat(r["acted_at"]),
        )
        for r in conn.execute(sql, params)
    ]


def queue_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT action, reviewed, COUNT(*) AS n FROM pipeline_runs GROUP BY action, reviewed"
    )
    counts: dict[str, int] = {}
    for row in rows:
        key = f"{row['action']}{'' if row['reviewed'] else '_pending'}"
        counts[key] = counts.get(key, 0) + row["n"]
    return counts
