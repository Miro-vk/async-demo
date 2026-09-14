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
