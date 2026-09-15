-- Corpus tables: the firm's records and the inbox the demo runs against.
-- Pipeline, decision, and review tables are added by later build steps.
--
-- List-valued columns (emails, domains, adverse_parties, practice_areas) are stored
-- as JSON text. For 200 clients and 150 matters that is entirely adequate and keeps
-- the schema readable; a real system would normalize these into join tables.

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attorneys (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    email          TEXT NOT NULL,
    practice_areas TEXT NOT NULL,   -- JSON array
    capacity       INTEGER NOT NULL,
    current_load   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS clients (
    id           TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    client_type  TEXT NOT NULL,
    emails       TEXT NOT NULL,     -- JSON array
    domains      TEXT NOT NULL,     -- JSON array
    opened_on    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS matters (
    id                      TEXT PRIMARY KEY,
    client_id               TEXT NOT NULL REFERENCES clients(id),
    caption                 TEXT NOT NULL,
    practice_area           TEXT NOT NULL,
    status                  TEXT NOT NULL,
    opened_on               TEXT NOT NULL,
    closed_on               TEXT,
    responsible_attorney_id TEXT NOT NULL REFERENCES attorneys(id),
    adverse_parties         TEXT NOT NULL  -- JSON array
);

CREATE TABLE IF NOT EXISTS emails (
    id           TEXT PRIMARY KEY,
    received_at  TEXT NOT NULL,
    from_name    TEXT NOT NULL,
    from_email   TEXT NOT NULL,
    to_address   TEXT NOT NULL,
    subject      TEXT NOT NULL,
    body         TEXT NOT NULL,
    prose_source TEXT NOT NULL
);

-- Ground truth is what the generator planted. The pipeline never reads this table;
-- only the eval harness does. Keeping it in the same database is a demo convenience,
-- and `tests/test_layering.py` guards the separation on the code side.
CREATE TABLE IF NOT EXISTS ground_truth (
    email_id           TEXT PRIMARY KEY REFERENCES emails(id),
    label              TEXT NOT NULL,
    practice_area      TEXT,
    jurisdiction       TEXT,
    prospective_client TEXT,
    opposing_parties   TEXT NOT NULL,  -- JSON array
    key_dates          TEXT NOT NULL,  -- JSON array
    amounts            TEXT NOT NULL,  -- JSON array
    planted_trap_rules TEXT NOT NULL,  -- JSON array
    trap_record_ids    TEXT NOT NULL,  -- JSON array of client/matter ids
    expected_action    TEXT NOT NULL,
    is_ambiguous       INTEGER NOT NULL,
    notes              TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_matters_client   ON matters(client_id);
CREATE INDEX IF NOT EXISTS idx_matters_status   ON matters(status);
CREATE INDEX IF NOT EXISTS idx_emails_received  ON emails(received_at);

-- Pipeline output. The whole PipelineResult is stored as JSON: the API serves it
-- to the UI unchanged, and the trace view wants every field anyway. The cost is
-- that you cannot query inside a run from SQL -- fine at 51 emails, the first
-- thing to normalize at 51,000.
CREATE TABLE IF NOT EXISTS pipeline_runs (
    email_id     TEXT PRIMARY KEY REFERENCES emails(id),
    processed_at TEXT NOT NULL,
    action       TEXT NOT NULL,     -- proceed | review | stop
    reviewed     INTEGER NOT NULL,  -- 1 once a human has acted
    provider     TEXT NOT NULL,
    model        TEXT NOT NULL,
    result_json  TEXT NOT NULL
);

-- Append-only log of what humans did. Separate from pipeline_runs because a run
-- gets rewritten when it is re-processed and this must not: "who approved this,
-- when, and what did they change" has to survive a re-run.
CREATE TABLE IF NOT EXISTS review_actions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id   TEXT NOT NULL REFERENCES emails(id),
    outcome    TEXT NOT NULL,
    reviewer   TEXT NOT NULL,
    note       TEXT NOT NULL,
    edits_json TEXT NOT NULL,
    acted_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_action    ON pipeline_runs(action, reviewed);
CREATE INDEX IF NOT EXISTS idx_reviews_email  ON review_actions(email_id);
