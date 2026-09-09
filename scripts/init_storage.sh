#!/usr/bin/env bash
# Bring up Postgres + Qdrant and initialize the Qdrant collection.
# Postgres schema is applied automatically on first container start via
# docker-entrypoint-initdb.d (app/storage/schema.sql).
set -euo pipefail
cd "$(dirname "$0")/.."

docker compose up -d postgres qdrant
echo "waiting for postgres..."
until docker exec sca-postgres pg_isready -U "$(grep ^POSTGRES_USER= .env | cut -d= -f2)" >/dev/null 2>&1; do
  sleep 1
done

source .venv/bin/activate
python -c "from app.storage import qdrant_store; qdrant_store.ensure_collection()"
echo "storage ready: postgres schema applied, qdrant collection ensured"
