"""Retention enforcement (plan §4.3): purge frame-level Qdrant vectors older
than FRAME_VECTOR_RETENTION_DAYS. Run daily via cron on aws-01.
Daily per-track rollups into Postgres are a Phase-4+ follow-on once the
Obsidian sync writer's summaries exist to roll up from.
"""
from app.config import settings
from app.storage import qdrant_store

if __name__ == "__main__":
    qdrant_store.purge_older_than(settings.frame_vector_retention_days)
    print(f"purged frame vectors older than {settings.frame_vector_retention_days} days")
