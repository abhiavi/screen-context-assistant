"""Temporal knowledge-graph memory via Graphiti + Neo4j (upgrade roadmap
"Next" phase). Distinct from Qdrant/HDBSCAN: those answer "what happened
when" (semantic similarity, session clustering); this answers "what facts
and relationships hold, and how have they changed over time" - e.g. a
config value that used to be X and changed to Y, tracked as an
invalidated-then-superseding fact, not just two unrelated similar-looking
frames.

Fed from daily digests (scripts triggered via GET /digest), not raw
frames or every session - one coherent narrative per track per day is
enough signal for entity/relationship extraction without multiplying
Graphiti's own LLM extraction calls by the frame or session count.

Wiring this to our LiteLLM gateway required working around two real
incompatibilities with graphiti-core 0.30.2's defaults, found by testing
live (2026-09-10), not by reading docs:

1. The default `OpenAIClient` uses OpenAI's newer /v1/responses API
   (`client.responses.parse`) for structured output - our gateway only
   implements the classic /v1/chat/completions surface, so this returns a
   bare 404. Fixed by using `OpenAIGenericClient` instead, which targets
   chat completions with `response_format` for structured output - built
   into graphiti-core specifically for non-OpenAI-native gateways like
   this one.
2. The openai Python SDK's `.embeddings.create()` unconditionally sends
   `encoding_format` (defaults to 'base64' if omitted, or forwards
   whatever value is given) - litellm's vertex_ai passthrough for
   text-embedding-004 rejects the key outright regardless of value
   ("does not support parameters: {'encoding_format': ...}"). There's no
   SDK-level way to omit it. Fixed with a small embedder subclass that
   bypasses the SDK's typed client entirely and does a raw httpx POST,
   exactly like app/ingest/gateway.py's own embed() already does
   successfully against this same model.

Telemetry: graphiti-core sends anonymous usage events to PostHog by
default (`GRAPHITI_TELEMETRY_ENABLED` env var, checked at import time)
and Neo4j itself reports anonymous usage data by default too (disabled
via NEO4J_dbms_usage__report_enabled in docker-compose.yml) - both
inconsistent with this app's privacy stance, so GRAPHITI_TELEMETRY_ENABLED
must be set to false in the environment (.env / crontab) before this
module (or graphiti_core) is ever imported.
"""
from __future__ import annotations

import os

# graphiti_core checks this env var (not our Settings/.env parsing, which
# only populates pydantic fields, never real os.environ) each time it
# would report a telemetry event - set explicitly here rather than relying
# on every launch script (nohup, cron, systemd, whatever comes next) to
# remember to export it. setdefault so an explicit override still wins.
os.environ.setdefault("GRAPHITI_TELEMETRY_ENABLED", "false")

from datetime import datetime

import httpx
from graphiti_core import Graphiti
from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
from graphiti_core.edges import EntityEdge
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
from graphiti_core.nodes import EpisodeType

from app.config import settings

EMBEDDING_DIM = 768  # text-embedding-004, same as qdrant_store.EMBEDDING_DIM


class _RawHttpxEmbedder(OpenAIEmbedder):
    def __init__(self, config: OpenAIEmbedderConfig):
        super().__init__(config=config)  # satisfies the parent's AsyncOpenAI(api_key=...) construction; unused below
        self._httpx = httpx.AsyncClient(
            base_url=settings.litellm_base_url,
            headers={"Authorization": f"Bearer {settings.litellm_api_key}"},
            timeout=30.0,
        )

    async def _embed_many(self, inputs: list[str]) -> list[list[float]]:
        resp = await self._httpx.post(
            "/embeddings", json={"model": self.config.embedding_model, "input": inputs}
        )
        resp.raise_for_status()
        body = resp.json()
        return [d["embedding"][: self.config.embedding_dim] for d in body["data"]]

    async def create(self, input_data) -> list[float]:
        inputs = [input_data] if isinstance(input_data, str) else list(input_data)
        return (await self._embed_many(inputs))[0]

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        return await self._embed_many(input_data_list)


_graphiti: Graphiti | None = None


def get_graphiti() -> Graphiti:
    """Lazily-initialized singleton - holds a live Neo4j driver connection,
    reused across calls rather than reconnecting every time."""
    global _graphiti
    if _graphiti is not None:
        return _graphiti

    llm_config = LLMConfig(
        api_key=settings.litellm_api_key,
        model=settings.synthesis_model,
        small_model=settings.synthesis_model,
        base_url=settings.litellm_base_url,
    )
    _graphiti = Graphiti(
        settings.neo4j_bolt_uri,
        settings.neo4j_user,
        settings.neo4j_password,
        llm_client=OpenAIGenericClient(config=llm_config, structured_output_mode="json_object"),
        embedder=_RawHttpxEmbedder(config=OpenAIEmbedderConfig(
            embedding_dim=EMBEDDING_DIM,
            embedding_model=settings.embedding_model,
            api_key=settings.litellm_api_key,
            base_url=settings.litellm_base_url,
        )),
        cross_encoder=OpenAIRerankerClient(config=llm_config),
    )
    return _graphiti


async def ensure_indices() -> None:
    await get_graphiti().build_indices_and_constraints()


async def add_daily_episode(track_id: str, day: str, summary: str) -> None:
    """One episode per track per day, fed from GET /digest's synthesized
    summary - not raw frames, not every session. group_id=track_id keeps
    each track's graph naturally partitioned (searching "research" never
    surfaces "home-server" facts unless explicitly asked across tracks)."""
    graphiti = get_graphiti()
    await graphiti.add_episode(
        name=f"{track_id}-{day}",
        episode_body=summary,
        source=EpisodeType.text,
        source_description=f"screen-context-assistant daily digest ({track_id})",
        reference_time=datetime.now(),
        group_id=track_id,
    )


async def search_graph(query: str, track_id: str | None = None, num_results: int = 10) -> list[dict]:
    graphiti = get_graphiti()
    group_ids = [track_id] if track_id else None
    edges: list[EntityEdge] = await graphiti.search(query, group_ids=group_ids, num_results=num_results)
    return [
        {
            "fact": e.fact,
            "valid_at": e.valid_at.isoformat() if e.valid_at else None,
            "invalid_at": e.invalid_at.isoformat() if e.invalid_at else None,
        }
        for e in edges
    ]
