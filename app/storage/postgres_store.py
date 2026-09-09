"""Postgres store for session-level rows (plan §4.3). Cheap SQL for "what
was I doing at 3pm" without a vector search."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime

import psycopg2
import psycopg2.extras

from app.config import settings


@contextmanager
def connect():
    conn = psycopg2.connect(settings.postgres_dsn)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_track(track_id: str, display_name: str, registry_project_id: str | None = None) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tracks (track_id, display_name, registry_project_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (track_id) DO UPDATE SET display_name = EXCLUDED.display_name
            """,
            (track_id, display_name, registry_project_id),
        )


def start_session(
    track_id: str, host: str, app_name: str, window_title_redacted: str,
    started_at: datetime, sensitive: bool = False,
) -> int:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sessions (track_id, host, app_name, window_title, started_at, sensitive)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (track_id, host, app_name, window_title_redacted, started_at, sensitive),
        )
        return cur.fetchone()[0]


def end_session(session_id: int, ended_at: datetime, frame_count: int) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE sessions SET ended_at = %s, frame_count = %s WHERE id = %s",
            (ended_at, frame_count, session_id),
        )


def log_app_switch(session_id: int, app_name: str, window_title_redacted: str, occurred_at: datetime) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO app_switch_events (session_id, app_name, window_title, occurred_at)
            VALUES (%s, %s, %s, %s)
            """,
            (session_id, app_name, window_title_redacted, occurred_at),
        )


def save_summary(session_id: int, summary: str, vault_note_path: str | None = None) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO track_summaries (session_id, summary, vault_note_path) VALUES (%s, %s, %s)",
            (session_id, summary, vault_note_path),
        )


def sessions_at(when: datetime, window_minutes: int = 30):
    with connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT s.*, t.display_name FROM sessions s
            JOIN tracks t ON t.track_id = s.track_id
            WHERE s.started_at <= %(when)s
              AND (s.ended_at IS NULL OR s.ended_at >= %(when)s)
              OR (s.started_at BETWEEN %(when)s - (%(window)s || ' minutes')::interval
                                    AND %(when)s + (%(window)s || ' minutes')::interval)
            ORDER BY s.started_at
            """,
            {"when": when, "window": window_minutes},
        )
        return cur.fetchall()
