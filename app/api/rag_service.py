"""RAG query API (plan §4.5) + avatar recall (plan v4 re-scope). question ->
top-k from Qdrant + Postgres rows -> synthesis via SYNTHESIS_MODEL. Bound to
the Tailscale IP only."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import FastAPI
from pydantic import BaseModel

from app.graph import client as graph_client
from app.ingest import gateway
from app.storage import postgres_store, qdrant_store

app = FastAPI(title="screen-context-assistant RAG")


@app.on_event("startup")
async def _startup() -> None:
    await graph_client.ensure_indices()

SESSION_GAP_SECONDS = 5 * 60  # a gap this long between frames = session boundary
SESSION_MAX_POINTS = 40  # cap how much context/tokens one recall pulls in


class QueryRequest(BaseModel):
    question: str
    track_id: str | None = None
    at: datetime | None = None


class QueryResponse(BaseModel):
    answer: str
    sources: list[dict]


class RecallResponse(BaseModel):
    track_id: str | None
    app_name: str | None
    started_at: datetime | None
    ended_at: datetime | None
    summary: str
    frame_count: int
    project_label: str | None = None
    project_summary: str | None = None


class DigestEntry(BaseModel):
    track_id: str
    app_names: list[str]
    session_count: int
    total_seconds: int
    summary: str


class GraphFact(BaseModel):
    fact: str
    valid_at: str | None
    invalid_at: str | None


class SessionEntry(BaseModel):
    id: int
    track_id: str
    app_name: str | None
    window_title: str | None
    started_at: datetime
    ended_at: datetime | None
    frame_count: int
    cluster_id: int | None


class ClusterEntry(BaseModel):
    id: int
    track_id: str
    label: str
    summary: str | None
    session_count: int
    first_seen: datetime
    last_seen: datetime


class ConversationEntry(BaseModel):
    id: int
    track_id: str | None
    kind: str
    question: str | None
    answer: str
    created_at: datetime


EXPERT_PERSONA_INSTRUCTION = (
    "Infer the relevant domain of expertise from the context below (e.g. "
    "software engineering, security research, business strategy, academic "
    "research, home infrastructure - whatever actually fits the captured "
    "activity) and answer in that domain expert's voice: use the field's "
    "own terminology and give the kind of specific, opinionated guidance a "
    "real expert would give, not generic advice. "
)
MARKDOWN_INSTRUCTION = (
    "Format the answer as clean Markdown (short paragraphs, bullet lists, "
    "**bold** for key terms, `code` for identifiers) - it will be rendered "
    "as Markdown, not shown as plain text."
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/sessions", response_model=list[SessionEntry])
def sessions(track_id: str | None = None, hours: int = 24) -> list[SessionEntry]:
    """Raw session rows in the trailing window - read-only, no synthesis.
    Added for app/mcp_server.py (upgrade roadmap "Next": MCP read-only
    agent), so another agent can inspect what actually happened without
    triggering a synthesis call for every question."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = postgres_store.sessions_since(cutoff, track_id=track_id)
    return [SessionEntry(**row) for row in rows]


@app.get("/clusters", response_model=list[ClusterEntry])
def clusters(track_id: str | None = None, limit: int = 20) -> list[ClusterEntry]:
    """Recognized recurring projects (scripts/cluster_sessions.py) - same
    read-only rationale as /sessions above."""
    rows = postgres_store.list_clusters(track_id=track_id, limit=limit)
    return [ClusterEntry(**row) for row in rows]


@app.get("/history", response_model=list[ConversationEntry])
def history(track_id: str | None = None, limit: int = 50) -> list[ConversationEntry]:
    """Past recall/query exchanges, newest first - lets the avatar's panel
    be scrolled back through instead of only ever showing the latest
    answer (plan v4: "different discussion on different work")."""
    rows = postgres_store.recent_conversations(track_id=track_id, limit=limit)
    return [ConversationEntry(**row) for row in rows]


@app.get("/recall", response_model=RecallResponse)
def recall(track_id: str | None = None) -> RecallResponse:
    """"Here's what you were doing / where you left off" - avatar's proactive
    recall (plan v4). Reconstructs the last continuous session on the fly
    from recent Qdrant frames (see qdrant_store.recent_points) rather than
    requiring a typed question."""
    points = qdrant_store.recent_points(track_id=track_id, limit=SESSION_MAX_POINTS * 3)
    if not points:
        return RecallResponse(
            track_id=track_id, app_name=None, started_at=None, ended_at=None,
            summary="No recent activity captured yet.", frame_count=0,
        )

    session_points = [points[0]]
    for prev, cur in zip(points, points[1:]):
        gap = prev.payload.get("timestamp", 0) - cur.payload.get("timestamp", 0)
        if gap > SESSION_GAP_SECONDS or len(session_points) >= SESSION_MAX_POINTS:
            break
        session_points.append(cur)

    resolved_track_id = session_points[0].payload.get("track_id")
    app_names = {p.payload.get("app_name") for p in session_points if p.payload.get("app_name")}
    started_at = datetime.fromtimestamp(session_points[-1].payload["timestamp"], tz=timezone.utc)
    ended_at = datetime.fromtimestamp(session_points[0].payload["timestamp"], tz=timezone.utc)

    ocr_snippets = [p.payload.get("ocr_text", "") for p in reversed(session_points) if p.payload.get("ocr_text")]
    joined = "\n---\n".join(ocr_snippets)[:4000]

    # Thematic project context (upgrade roadmap, HDBSCAN clustering,
    # scripts/cluster_sessions.py) - a recurring project spanning multiple
    # sessions, possibly with gaps of hours or days, not just this one
    # contiguous block of activity. Only mention it if it actually recurs
    # (session_count > 1) - a cluster of one session is just this session.
    cluster = postgres_store.latest_cluster_for_track(resolved_track_id) if resolved_track_id else None
    project_context = ""
    if cluster and cluster["session_count"] > 1:
        project_context = (
            f"\n\nThis also connects to a recurring project you've worked on across "
            f"{cluster['session_count']} separate sessions: \"{cluster['label']}\" - "
            f"{cluster['summary'] or ''} Weave a brief mention of that broader "
            "project into your answer if it's genuinely relevant to the next step."
        )

    prompt = (
        "The user stepped away and just came back. In 2-4 short, friendly "
        "sentences, remind them what they were doing and, as a domain "
        f"expert would, suggest the concrete next step. {EXPERT_PERSONA_INSTRUCTION}"
        "Base this only on the already-redacted on-screen text below, captured "
        f"from their own screen (track: {resolved_track_id}, apps: "
        f"{', '.join(app_names) or 'unknown'}). {MARKDOWN_INSTRUCTION}"
        f"{project_context}\n\n{joined}"
    )
    summary = gateway.synthesize(prompt, max_tokens=220)
    postgres_store.save_conversation(resolved_track_id, "recall", None, summary)

    return RecallResponse(
        track_id=resolved_track_id,
        app_name=", ".join(app_names) if app_names else None,
        started_at=started_at,
        ended_at=ended_at,
        summary=summary,
        frame_count=len(session_points),
        project_label=cluster["label"] if cluster and cluster["session_count"] > 1 else None,
        project_summary=cluster["summary"] if cluster and cluster["session_count"] > 1 else None,
    )


@app.get("/digest", response_model=list[DigestEntry])
async def digest(hours: int = 24) -> list[DigestEntry]:
    """Broader-scope "here's your day" recap across every session in the
    trailing window, for every track that had any - unlike /recall (which
    only ever reconstructs the single most recent contiguous session),
    this covers everything, session by session, and is meant to be called
    once a day rather than on-demand. Populates track_daily_rollups
    (existed in schema.sql since the v3 build, never written to before
    this) as a side effect; the caller (main.py on mini, which has vault
    filesystem access this backend doesn't) is responsible for writing the
    returned summaries into the Obsidian vault. Also feeds the day's
    summary into the Graphiti temporal knowledge graph (app/graph/client.py,
    upgrade roadmap "Next" phase) - one episode per track per day, not per
    session or per frame, to keep Graphiti's own LLM extraction cost
    bounded. `async def` specifically for this - everything else in this
    endpoint is still sync httpx (gateway.py), which is fine to block
    inside an async def for a job that only runs once a day."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    track_ids = postgres_store.known_tracks_with_recent_sessions(cutoff)

    entries = []
    for track_id in track_ids:
        sessions = [s for s in postgres_store.sessions_since(cutoff, track_id=track_id)]
        if not sessions:
            continue

        total_seconds = sum(
            int(((s["ended_at"] or s["started_at"]) - s["started_at"]).total_seconds())
            for s in sessions
        )
        app_names = sorted({s["app_name"] for s in sessions if s["app_name"]})

        lines = [
            f"- {s['started_at']:%H:%M}-{(s['ended_at'] or s['started_at']):%H:%M} "
            f"[{s['app_name']}] {s['window_title'] or ''}"
            for s in sessions
        ]
        cluster_names = sorted({
            postgres_store.cluster_for_session(s["id"])["label"]
            for s in sessions
            if postgres_store.cluster_for_session(s["id"])
        })
        cluster_context = (
            f"\n\nRecurring projects touched today: {', '.join(cluster_names)}."
            if cluster_names else ""
        )

        prompt = (
            "Write a short end-of-day recap (3-6 sentences, or a brief "
            "bullet list if the day covered clearly distinct chunks of "
            f"work) of this activity. {EXPERT_PERSONA_INSTRUCTION}"
            "Base this only on the already-redacted session log below, "
            f"captured from the user's own screen (track: {track_id})."
            f"{cluster_context} {MARKDOWN_INSTRUCTION}\n\n" + "\n".join(lines)
        )
        summary = gateway.synthesize(prompt, max_tokens=280)
        today = date.today()

        postgres_store.upsert_daily_rollup(
            track_id=track_id,
            day=today,
            total_seconds=total_seconds,
            session_count=len(sessions),
            summary=summary,
        )

        try:
            await graph_client.add_daily_episode(track_id, today.isoformat(), summary)
        except Exception:  # noqa: BLE001
            # Graph memory is a supplement, not load-bearing - a failure
            # here (Neo4j down, extraction error) shouldn't break the
            # digest itself, which the vault write depends on. Still
            # logged (not silently swallowed) so a real problem is visible.
            logging.exception("graph_client.add_daily_episode failed for track %s", track_id)

        entries.append(DigestEntry(
            track_id=track_id,
            app_names=app_names,
            session_count=len(sessions),
            total_seconds=total_seconds,
            summary=summary,
        ))

    return entries


@app.get("/graph_search", response_model=list[GraphFact])
async def graph_search(query: str, track_id: str | None = None, num_results: int = 10) -> list[GraphFact]:
    """Temporal knowledge-graph search (app/graph/client.py, Graphiti +
    Neo4j) - facts and relationships extracted from daily digests, with
    validity windows (valid_at/invalid_at) when something changed over
    time. Complements /query (Qdrant semantic search over raw frames): use
    this for "what changed" or "what's the relationship between X and Y"
    questions, /query for "what was on screen about X"."""
    facts = await graph_client.search_graph(query, track_id=track_id, num_results=num_results)
    return [GraphFact(**f) for f in facts]


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest) -> QueryResponse:
    query_embedding = gateway.embed(req.question)
    hits = qdrant_store.search(query_embedding=query_embedding, track_id=req.track_id, limit=8)

    sources = [
        {
            "score": h.score,
            "track_id": h.payload.get("track_id"),
            "app_name": h.payload.get("app_name"),
            "window_title": h.payload.get("window_title"),
            "ocr_text": h.payload.get("ocr_text"),
            "timestamp": h.payload.get("timestamp"),
        }
        for h in hits
    ]

    session_rows = postgres_store.sessions_at(req.at) if req.at else []

    context_parts = [
        f"[{s['app_name']}] {s['window_title']}: {s['ocr_text']}" for s in sources
    ]
    if session_rows:
        context_parts.append(
            "Session log: "
            + "; ".join(f"{r['display_name']} on {r['host']} from {r['started_at']}" for r in session_rows)
        )

    prompt = (
        f"Answer the question using only the context below, which is "
        "already-redacted on-screen activity captured from the user's own "
        f"machines. Be specific about time and app when you can. {EXPERT_PERSONA_INSTRUCTION}"
        f"{MARKDOWN_INSTRUCTION}\n\n"
        f"Context:\n{chr(10).join(context_parts)}\n\nQuestion: {req.question}"
    )
    answer = gateway.synthesize(prompt, max_tokens=500)
    postgres_store.save_conversation(req.track_id, "query", req.question, answer)

    return QueryResponse(answer=answer, sources=sources)
