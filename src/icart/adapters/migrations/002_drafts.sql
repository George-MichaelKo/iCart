CREATE TABLE icart_drafts (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    state TEXT NOT NULL CHECK (state IN ('needs_details', 'needs_review', 'confirmed')),
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    stored_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX icart_drafts_owner ON icart_drafts(owner_id, id);
