"""Groups sessions into thematic projects via HDBSCAN over embeddings
(upgrade roadmap, "Next" phase item, promoted ahead of schedule 2026-09-10
as the one clear no-hardware-mismatch win from the second roadmap doc).

The existing `sessions` table (scripts/segment_sessions.py) is still purely
time-based: one row per contiguous block of activity. That's correct for
"what was I just doing," but wrong for "what have I been doing on this
project" - a 30-minute research tangent in the middle of a coding session
currently reads as three unrelated sessions (code, research, code) with no
link between the two coding halves, and no link to the *same* project
worked on two days ago in a different sitting.

This script adds a second, coarser layer: activity_clusters groups
sessions by embedding similarity regardless of time gap. Clustering is
per-track (comparing embeddings across tracks/Activities isn't
meaningful) and per-session, not per-frame - a session's representative
vector is the mean of its frames' embeddings, since HDBSCAN over
individual frames would just rediscover time-based sessions (consecutive
frames within one sitting are trivially similar) rather than finding
cross-session thematic links.

Run daily via cron - unlike segment_sessions.py (every 15min, needs to be
fresh for /recall), a project grouping doesn't need to update faster than
new sessions accumulate.

    PYTHONPATH=. .venv/bin/python scripts/cluster_sessions.py
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
from sklearn.cluster import HDBSCAN

from app.ingest import gateway
from app.storage import postgres_store, qdrant_store

LOOKBACK_DAYS = 14
MIN_CLUSTER_SIZE = 2  # a "project" must recur across at least 2 sessions


def _session_vector(session: dict) -> list[float] | None:
    start_ts = int(session["started_at"].timestamp())
    end_ts = int((session["ended_at"] or session["started_at"]).timestamp())
    vectors = qdrant_store.vectors_in_range(session["track_id"], start_ts, end_ts)
    if not vectors:
        return None
    return list(np.mean(vectors, axis=0))


def _label_cluster(sessions: list[dict]) -> tuple[str, str]:
    sample = sessions[:8]
    lines = [f"- [{s['app_name']}] {s['window_title'] or ''}" for s in sample]
    prompt = (
        "These are app/window titles from several separate work sessions "
        "that an embedding-similarity model grouped together as the same "
        "underlying project or task. Give this project a short name (3-6 "
        "words) and a one-sentence description of what the work is.\n\n"
        + "\n".join(lines)
        + "\n\nRespond in exactly this format:\nLABEL: <short name>\nSUMMARY: <one sentence>"
    )
    raw = gateway.synthesize(prompt, max_tokens=100)
    label, summary = "Untitled project", ""
    for line in raw.splitlines():
        if line.strip().upper().startswith("LABEL:"):
            label = line.split(":", 1)[1].strip()
        elif line.strip().upper().startswith("SUMMARY:"):
            summary = line.split(":", 1)[1].strip()
    return label, summary


def run() -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    all_sessions = postgres_store.sessions_since(cutoff)
    if not all_sessions:
        print("no sessions in the lookback window")
        return 0

    by_track: dict[str, list[dict]] = {}
    for s in all_sessions:
        by_track.setdefault(s["track_id"], []).append(s)

    clusters_written = 0
    for track_id, sessions in by_track.items():
        if len(sessions) < MIN_CLUSTER_SIZE:
            continue

        vectors, kept_sessions = [], []
        for s in sessions:
            v = _session_vector(s)
            if v is not None:
                vectors.append(v)
                kept_sessions.append(s)
        if len(kept_sessions) < MIN_CLUSTER_SIZE:
            continue

        matrix = np.array(vectors)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        matrix = matrix / norms  # L2-normalize so euclidean distance ranks like cosine

        labels = HDBSCAN(min_cluster_size=MIN_CLUSTER_SIZE, metric="euclidean", copy=True).fit_predict(matrix)

        by_label: dict[int, list[dict]] = {}
        for session, label in zip(kept_sessions, labels):
            if label == -1:
                continue  # noise - a one-off, not a recurring project
            by_label.setdefault(int(label), []).append(session)

        for member_sessions in by_label.values():
            member_sessions.sort(key=lambda s: s["started_at"])
            name, summary = _label_cluster(member_sessions)
            postgres_store.upsert_cluster(
                track_id=track_id,
                label=name,
                summary=summary,
                first_seen=member_sessions[0]["started_at"],
                last_seen=member_sessions[-1]["ended_at"] or member_sessions[-1]["started_at"],
                session_ids=[s["id"] for s in member_sessions],
            )
            clusters_written += 1
            print(f"[{track_id}] {name!r} - {len(member_sessions)} sessions")

    print(f"\n{clusters_written} cluster(s) written across {len(by_track)} track(s)")
    return clusters_written


if __name__ == "__main__":
    run()
