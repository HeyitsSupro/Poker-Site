-- Schema for the poker site. Safe to run on every boot: everything is
-- IF NOT EXISTS, and the server runs it at startup so a fresh Neon project
-- needs no manual migration step.
--
-- Three kinds of data, as required by the build instructions section 4.2:
--   tables   - live state, overwritten in place (includes the hand in progress)
--   events   - append-only log / ledger
--   hands    - append-only hand history

CREATE TABLE IF NOT EXISTS tables (
    table_id        TEXT PRIMARY KEY,
    format_version  INTEGER     NOT NULL,
    seq             BIGINT      NOT NULL DEFAULT 0,
    state           JSONB       NOT NULL,
    closed          BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS tables_open_idx ON tables (updated_at) WHERE NOT closed;

-- Append-only. `seq` is the table's monotonic action counter, so an event row
-- and the state version it produced always line up; `idx` orders the events
-- that one action produced.
CREATE TABLE IF NOT EXISTS events (
    table_id  TEXT        NOT NULL REFERENCES tables (table_id) ON DELETE CASCADE,
    seq       BIGINT      NOT NULL,
    idx       INTEGER     NOT NULL DEFAULT 0,
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind      TEXT        NOT NULL,
    actor     TEXT,
    payload   JSONB       NOT NULL DEFAULT '{}'::JSONB,
    PRIMARY KEY (table_id, seq, idx)
);

-- Append-only hand history. `data` holds the whole hand (actions, board,
-- showdown, payouts) in one compact document so the free 1 GB lasts.
CREATE TABLE IF NOT EXISTS hands (
    table_id       TEXT        NOT NULL REFERENCES tables (table_id) ON DELETE CASCADE,
    hand_no        INTEGER     NOT NULL,
    format_version INTEGER     NOT NULL,
    data           JSONB       NOT NULL,
    ended_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (table_id, hand_no)
);

-- Every client action carries an id. A duplicate arriving after a reconnect
-- hits this unique key and is answered from the already-committed state
-- instead of being applied twice.
CREATE TABLE IF NOT EXISTS applied_actions (
    table_id  TEXT        NOT NULL,
    action_id TEXT        NOT NULL,
    seq       BIGINT      NOT NULL,
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (table_id, action_id)
);

CREATE INDEX IF NOT EXISTS applied_actions_at_idx ON applied_actions (at);
