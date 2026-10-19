CREATE TABLE icart_missions (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    event_id TEXT NOT NULL CHECK (length(event_id) BETWEEN 1 AND 128),
    request_hash TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (owner_id, event_id),
    UNIQUE (id, owner_id)
);

CREATE TABLE icart_offers (
    ref TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    mission_id TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (mission_id, owner_id) REFERENCES icart_missions (id, owner_id)
);
CREATE INDEX icart_offers_mission ON icart_offers (mission_id, owner_id);

CREATE TABLE icart_jobs (
    id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL UNIQUE REFERENCES icart_missions (id),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'succeeded', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 3),
    available_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    lease_token TEXT,
    lease_until TIMESTAMPTZ,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK ((status = 'running') = (lease_token IS NOT NULL AND lease_until IS NOT NULL))
);
CREATE INDEX icart_jobs_pending ON icart_jobs (available_at, created_at) WHERE status = 'pending';
CREATE INDEX icart_jobs_running ON icart_jobs (lease_until) WHERE status = 'running';

CREATE TABLE icart_outbox (
    id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL UNIQUE REFERENCES icart_missions (id),
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sending', 'delivered')),
    lease_token TEXT,
    lease_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK ((status = 'sending') = (lease_token IS NOT NULL AND lease_until IS NOT NULL))
);
CREATE INDEX icart_outbox_pending ON icart_outbox (created_at) WHERE status = 'pending';
CREATE INDEX icart_outbox_sending ON icart_outbox (lease_until) WHERE status = 'sending';

-- Local delivery simulator, not a claim of exactly-once external messaging.
CREATE TABLE icart_mailbox (
    outbox_id TEXT PRIMARY KEY REFERENCES icart_outbox (id),
    delivered_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
