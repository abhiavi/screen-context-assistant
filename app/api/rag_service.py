"""RAG query API (plan §4.5) + avatar recall (plan v4 re-scope). question ->
top-k from Qdrant + Postgres rows -> synthesis via SYNTHESIS_MODEL. Bound to
the Tailscale IP only."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from pydantic import BaseModel

from app.ingest import gateway
from app.storage import postgres_store, qdrant_store

app = FastAPI(title="screen-context-assistant RAG")

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


@app.get("/health")
def health():
    return {"status": "ok"}


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

    prompt = (
        "The user stepped away and just came back. In 1-2 short, friendly "
        "sentences, remind them what they were doing, based only on this "
        "already-redacted on-screen text captured from their own screen "
        f"(track: {resolved_track_id}, apps: {', '.join(app_names) or 'unknown'}):\n\n{joined}"
    )
    summary = gateway.synthesize(prompt, max_tokens=150)

    return RecallResponse(
        track_id=resolved_track_id,
        app_name=", ".join(app_names) if app_names else None,
        started_at=started_at,
        ended_at=ended_at,
        summary=summary,
        frame_count=len(session_points),
    )


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
        "Answer the question using only the context below, which is "
        "already-redacted on-screen activity captured from the user's own "
        "machines. Be specific about time and app when you can.\n\n"
        f"Context:\n{chr(10).join(context_parts)}\n\nQuestion: {req.question}"
    )
    answer = gateway.synthesize(prompt, max_tokens=400)

    return QueryResponse(answer=answer, sources=sources)
