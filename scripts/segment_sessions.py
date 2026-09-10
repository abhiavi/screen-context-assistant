"""Populates the Postgres `sessions` table from recent Qdrant frames
(upgrade-roadmap "Now" item 5). The `sessions` table has existed in
schema.sql since the v3 build but nothing ever wrote to it - `/recall` has
been reconstructing "the last session" on the fly from Qdrant's timestamped
payloads instead (see qdrant_store.recent_points's docstring).

This is a periodic batch job, not something called inline from the capture
agent's per-frame poll loop - the agent stays simple/stateless per frame,
and re-clustering a trailing window on a schedule is easy to make
idempotent (see the ON CONFLICT upsert in postgres_store.upsert_session).

Run daily via cron on aws-01, alongside retention_rollup.py. Gap-clustering
logic mirrors rag_service.recall()'s SESSION_GAP_SECONDS exactly - if that
threshold ever changes, change it in both places.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from app.storage import postgres_store, qdrant_store

SESSION_GAP_SECONDS = 5 * 60
LOOKBACK_HOURS = 24  # trailing window re-clustered on every run; ON CONFLICT
                      # makes overlap with the previous run's window a no-op
                      # / extend-in-place rather than a duplicate.
HOST = "mini"  # only mini captures as of the v4 re-scope


def _cluster(points: list) -> list[list]:
    """Points must be sorted oldest-first. Returns clusters of points, each
    oldest-first, split wherever the gap between consecutive frames exceeds
    SESSION_GAP_SECONDS."""
    clusters: list[list] = []
    current: list = []
    prev_ts: int | None = None
    for p in points:
        ts = p.payload.get("timestamp", 0)
        if prev_ts is not None and ts - prev_ts > SESSION_GAP_SECONDS:
            clusters.append(current)
            current = []
        current.append(p)
        prev_ts = ts
    if current:
        clusters.append(current)
    return clusters


def run() -> int:
    cutoff = int((datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)).timestamp())
    points = qdrant_store.points_since(cutoff)
    if not points:
        print("no frames in the lookback window")
        return 0

    by_track: dict[str, list] = {}
    for p in points:
        by_track.setdefault(p.payload.get("track_id", "unknown"), []).append(p)

    written = 0
    for track_id, track_points in by_track.items():
        track_points.sort(key=lambda p: p.payload.get("timestamp", 0))
        postgres_store.ensure_track(track_id, display_name=track_id)
        for cluster in _cluster(track_points):
            app_counts = Counter(p.payload.get("app_name") for p in cluster if p.payload.get("app_name"))
            app_name = app_counts.most_common(1)[0][0] if app_counts else None
            started_at = datetime.fromtimestamp(cluster[0].payload["timestamp"], tz=timezone.utc)
            ended_at = datetime.fromtimestamp(cluster[-1].payload["timestamp"], tz=timezone.utc)
            sensitive = any(p.payload.get("sensitive") for p in cluster)
            postgres_store.upsert_session(
                track_id=track_id,
                host=HOST,
                app_name=app_name,
                window_title_redacted=cluster[-1].payload.get("window_title"),
                started_at=started_at,
                ended_at=ended_at,
                frame_count=len(cluster),
                sensitive=sensitive,
            )
            written += 1

    print(f"segmented {written} session(s) across {len(by_track)} track(s)")
    return written


if __name__ == "__main__":
    run()
