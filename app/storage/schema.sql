-- screen-context-assistant Postgres schema
-- Session-level rows for cheap "what was I doing at 3pm" SQL lookups.
-- Frame-level detail (embeddings, redacted OCR text) lives in Qdrant, not here.

CREATE TABLE IF NOT EXISTS tracks (
    track_id        TEXT PRIMARY KEY,       -- maps to a KDE Activity id
    registry_project_id TEXT,               -- link into Master Project Registry (mini)
    display_name    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id              BIGSERIAL PRIMARY KEY,
    track_id        TEXT NOT NULL REFERENCES tracks(track_id),
    host            TEXT NOT NULL,          -- desktop | laptop
    app_name        TEXT,
    window_title    TEXT,                   -- redacted before insert
    started_at      TIMESTAMPTZ NOT NULL,
    ended_at        TIMESTAMPTZ,
    sensitive       BOOLEAN NOT NULL DEFAULT FALSE,  -- per-Activity sensitive flag (plan §5)
    frame_count     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sessions_track_time ON sessions (track_id, started_at);
CREATE INDEX IF NOT EXISTS idx_sessions_started_at ON sessions (started_at);

CREATE TABLE IF NOT EXISTS app_switch_events (
    id              BIGSERIAL PRIMARY KEY,
    session_id      BIGINT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    app_name        TEXT,
    window_title    TEXT,                   -- redacted before insert
    occurred_at     TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_app_switch_time ON app_switch_events (occurred_at);

CREATE TABLE IF NOT EXISTS track_summaries (
    id              BIGSERIAL PRIMARY KEY,
    session_id      BIGINT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    summary         TEXT NOT NULL,          -- distilled, synthesized via SYNTHESIS_MODEL
    vault_note_path TEXT,                   -- e.g. Adraca/2026-09-09.md
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Daily per-track rollups (frame vectors age out of Qdrant after
-- FRAME_VECTOR_RETENTION_DAYS; rollups are the long-term retained artifact).
CREATE TABLE IF NOT EXISTS track_daily_rollups (
    id              BIGSERIAL PRIMARY KEY,
    track_id        TEXT NOT NULL REFERENCES tracks(track_id),
    day             DATE NOT NULL,
    total_seconds   INTEGER NOT NULL DEFAULT 0,
    session_count   INTEGER NOT NULL DEFAULT 0,
    summary         TEXT,
    UNIQUE (track_id, day)
);

-- Avatar conversation history (plan v4 avatar): every recall/query shown
-- in the avatar's panel, so the Operator can scroll back through past
-- exchanges across different tracks/work sessions.
CREATE TABLE IF NOT EXISTS avatar_conversations (
    id              BIGSERIAL PRIMARY KEY,
    track_id        TEXT,
    kind            TEXT NOT NULL,          -- 'recall' | 'query'
    question        TEXT,                   -- null for recall (no typed question)
    answer          TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_avatar_conversations_created ON avatar_conversations (created_at);
CREATE INDEX IF NOT EXISTS idx_avatar_conversations_track ON avatar_conversations (track_id);
