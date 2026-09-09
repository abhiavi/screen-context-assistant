"""RAG query API (plan §4.5). question -> top-k from Qdrant + Postgres rows
-> synthesis via SYNTHESIS_MODEL. Bound to the Tailscale IP only."""
from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI
from pydantic import BaseModel

from app.ingest import gateway
from app.storage import postgres_store, qdrant_store

app = FastAPI(title="screen-context-assistant RAG")


class QueryRequest(BaseModel):
    question: str
    track_id: str | None = None
    at: datetime | None = None


class QueryResponse(BaseModel):
    answer: str
    sources: list[dict]


@app.get("/health")
def health():
    return {"status": "ok"}


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
