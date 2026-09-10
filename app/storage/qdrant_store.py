"""Qdrant client for the `screen-context` collection. This is a SEPARATE
Qdrant instance running on aws-01 (plan §0.3) - never point this at
azure-01's shared Qdrant, which holds `india_open_data` on a disk-tight box.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from app.config import settings

EMBEDDING_DIM = 768  # text-embedding-004


def get_client() -> QdrantClient:
    return QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)


def ensure_collection(client: QdrantClient | None = None) -> None:
    client = client or get_client()
    existing = [c.name for c in client.get_collections().collections]
    if settings.qdrant_collection in existing:
        return
    client.create_collection(
        collection_name=settings.qdrant_collection,
        vectors_config=qm.VectorParams(size=EMBEDDING_DIM, distance=qm.Distance.COSINE),
    )
    # Payload indexes for the fields we filter/query on (plan §4.3).
    for field_name, schema in (
        ("track_id", qm.PayloadSchemaType.KEYWORD),
        ("app_name", qm.PayloadSchemaType.KEYWORD),
        ("timestamp", qm.PayloadSchemaType.INTEGER),
        ("sensitive", qm.PayloadSchemaType.BOOL),
    ):
        client.create_payload_index(
            collection_name=settings.qdrant_collection,
            field_name=field_name,
            field_schema=schema,
        )


def upsert_frame(
    *,
    embedding: list[float],
    track_id: str,
    app_name: str,
    window_title_redacted: str,
    ocr_text_redacted: str,
    timestamp: datetime,
    sensitive: bool = False,
    client: QdrantClient | None = None,
) -> str:
    client = client or get_client()
    point_id = str(uuid.uuid4())
    client.upsert(
        collection_name=settings.qdrant_collection,
        points=[
            qm.PointStruct(
                id=point_id,
                vector=embedding,
                payload={
                    "track_id": track_id,
                    "app_name": app_name,
                    "window_title": window_title_redacted,
                    "ocr_text": ocr_text_redacted,
                    "timestamp": int(timestamp.astimezone(timezone.utc).timestamp()),
                    "sensitive": sensitive,
                },
            )
        ],
    )
    return point_id


def search(
    *,
    query_embedding: list[float],
    track_id: str | None = None,
    limit: int = 8,
    client: QdrantClient | None = None,
):
    client = client or get_client()
    query_filter = None
    if track_id:
        query_filter = qm.Filter(must=[qm.FieldCondition(key="track_id", match=qm.MatchValue(value=track_id))])
    return client.query_points(
        collection_name=settings.qdrant_collection,
        query=query_embedding,
        query_filter=query_filter,
        limit=limit,
    ).points


def recent_points(
    *,
    track_id: str | None = None,
    limit: int = 300,
    client: QdrantClient | None = None,
):
    """Most recent frames, newest first. Used to reconstruct "the last
    session" on the fly for avatar recall (plan v4). scripts/segment_sessions.py
    now also populates the Postgres sessions table on a cron schedule, but
    this on-the-fly path stays as the fallback for anything in the last
    ~15min the cron job hasn't segmented yet - kept deliberately independent
    of Postgres so /recall never depends on the cron job having run."""
    client = client or get_client()
    query_filter = None
    if track_id:
        query_filter = qm.Filter(must=[qm.FieldCondition(key="track_id", match=qm.MatchValue(value=track_id))])
    points, _ = client.scroll(
        collection_name=settings.qdrant_collection,
        scroll_filter=query_filter,
        limit=limit,
        with_payload=True,
        with_vectors=False,
    )
    return sorted(points, key=lambda p: p.payload.get("timestamp", 0), reverse=True)


def points_since(cutoff_timestamp: int, client: QdrantClient | None = None):
    """All frames (any track) newer than cutoff_timestamp, oldest-first.
    Paginates through Qdrant's scroll API rather than relying on a single
    large limit - used by scripts/segment_sessions.py, which may cover a
    24h window across every track at once."""
    client = client or get_client()
    query_filter = qm.Filter(must=[qm.FieldCondition(key="timestamp", range=qm.Range(gte=cutoff_timestamp))])
    all_points = []
    offset = None
    while True:
        points, next_offset = client.scroll(
            collection_name=settings.qdrant_collection,
            scroll_filter=query_filter,
            limit=500,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        all_points.extend(points)
        if next_offset is None:
            break
        offset = next_offset
    return sorted(all_points, key=lambda p: p.payload.get("timestamp", 0))


def purge_older_than(days: int, client: QdrantClient | None = None) -> None:
    """Retention enforcement (plan §4.3): frame-level vectors ~30 days."""
    client = client or get_client()
    cutoff = int(datetime.now(timezone.utc).timestamp()) - days * 86400
    client.delete(
        collection_name=settings.qdrant_collection,
        points_selector=qm.FilterSelector(
            filter=qm.Filter(must=[qm.FieldCondition(key="timestamp", range=qm.Range(lt=cutoff))])
        ),
    )
