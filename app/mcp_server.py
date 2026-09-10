"""Read-only MCP server (upgrade roadmap "Next" phase) exposing this app's
screen-context data to other agents on the fleet over the Tailscale mesh -
so a Claude/AGY session on another node can ask "what has the Operator
been working on" directly instead of SSHing in and reconstructing it by
hand from Postgres/Qdrant, which is how every prior session (including
this one) has had to do it.

Deliberately read-only: every tool here is a thin proxy around an existing
GET endpoint on rag_service.py (or POST for /query, which is still a pure
read - it doesn't mutate anything, just costs a synthesis call). There is
no tool that pauses/resumes capture, changes config, or writes anything.
Full autonomous *action* (an agent that can click/type on the Operator's
desktop) is a much bigger, deliberately-not-taken step - see the upgrade
roadmap's UI-TARS section - this MCP server does not do that and should
not quietly grow into it without that being a conscious decision.

Security model matches the rest of the backend (see README: "Bound to the
Tailscale IP only") - no additional auth layer, since Tailscale mesh
membership is already this app's access-control boundary everywhere else.
Run this only on aws-01, alongside ingest_service/rag_service.

    PYTHONPATH=. .venv/bin/python -m app.mcp_server
"""
from __future__ import annotations

import httpx

from app.config import settings
from mcp.server.mcpserver import MCPServer

server = MCPServer(
    name="screen-context-assistant",
    instructions=(
        "Read-only access to the Operator's own on-screen activity, already "
        "OCR'd and redacted before it ever reached this server - captured "
        "from their own machine with their own consent, not surveillance of "
        "anyone else. Use track_id to scope to one KDE Activity/project "
        "(e.g. 'home-server', 'research', 'adraca') or omit it for "
        "everything. Prefer sessions/clusters for structured facts you'll "
        "reason about yourself; prefer recall/digest/query when you want "
        "the Operator's own already-synthesized narrative instead."
    ),
)

_client = httpx.Client(base_url=f"http://{settings.bind_host}:8089", timeout=30.0)


@server.tool()
def recall(track_id: str | None = None) -> dict:
    """What the Operator was just doing - reconstructs the single most
    recent contiguous session on the fly. Omit track_id for whatever's
    most recent across all tracks."""
    params = {"track_id": track_id} if track_id else {}
    resp = _client.get("/recall", params=params)
    resp.raise_for_status()
    return resp.json()


@server.tool()
def query(question: str, track_id: str | None = None) -> dict:
    """Semantic search + synthesis over the Operator's captured activity -
    for a specific question, not just "what happened last." Costs one
    embedding call and one synthesis call."""
    body = {"question": question}
    if track_id:
        body["track_id"] = track_id
    resp = _client.post("/query", json=body)
    resp.raise_for_status()
    return resp.json()


@server.tool()
def digest(hours: int = 24) -> list[dict]:
    """Broader "here's the day" recap across every session in the trailing
    window, per track - not just the latest one. Same data the avatar
    writes into the Operator's Obsidian vault once a day."""
    resp = _client.get("/digest", params={"hours": hours})
    resp.raise_for_status()
    return resp.json()


@server.tool()
def list_sessions(track_id: str | None = None, hours: int = 24) -> list[dict]:
    """Raw session rows (app, window title, start/end, which recurring
    project cluster they belong to) - structured facts, no synthesis call,
    cheap to call repeatedly."""
    params = {"hours": hours}
    if track_id:
        params["track_id"] = track_id
    resp = _client.get("/sessions", params=params)
    resp.raise_for_status()
    return resp.json()


@server.tool()
def list_clusters(track_id: str | None = None, limit: int = 20) -> list[dict]:
    """Recognized recurring projects - sessions grouped by embedding
    similarity regardless of time gaps (see scripts/cluster_sessions.py).
    A project needs at least 2 sessions to show up here; a one-off session
    never gets a cluster."""
    params = {"limit": limit}
    if track_id:
        params["track_id"] = track_id
    resp = _client.get("/clusters", params=params)
    resp.raise_for_status()
    return resp.json()


@server.tool()
def graph_search(query: str, track_id: str | None = None, num_results: int = 10) -> list[dict]:
    """Temporal knowledge-graph search (Graphiti + Neo4j) - facts and
    relationships extracted from daily digests, each with a validity
    window (when something changed, not just what's true now). Use this
    for "what changed" or "how does X relate to Y" questions; use `query`
    instead for "what was on screen about X"."""
    params = {"query": query, "num_results": num_results}
    if track_id:
        params["track_id"] = track_id
    resp = _client.get("/graph_search", params=params)
    resp.raise_for_status()
    return resp.json()


@server.tool()
def conversation_history(track_id: str | None = None, limit: int = 50) -> list[dict]:
    """Past recall/query exchanges already shown to the Operator in the
    avatar's own panel, newest first - what they've already been told,
    not raw activity."""
    params = {"limit": limit}
    if track_id:
        params["track_id"] = track_id
    resp = _client.get("/history", params=params)
    resp.raise_for_status()
    return resp.json()


if __name__ == "__main__":
    server.run(transport="streamable-http", host=settings.bind_host, port=8090)
