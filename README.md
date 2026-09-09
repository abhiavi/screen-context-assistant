# screen-context-assistant

Always-on desktop context assistant: watches KDE Activities + screen content
across Abhishek's work tracks, keeps a searchable, privacy-bounded record of
what he was doing and when, and answers questions like "what was I doing on
the CRS doc around 2pm yesterday" via RAG.

Full design: `docs/screen-context-assistant-plan.md` (build spec, phased plan,
infra-review resolutions).

## Hard constraints (do not re-open — see plan §0)

1. AI gateway is `adraca-azure-01:4000` only. Never `adraca-pve:4000` (zombie
   LiteLLM instance). Fallback: `adraca-oracle-01:4000`.
2. Runs entirely on `adraca-aws-01` — own Postgres + own Qdrant, **not**
   azure-01's shared Qdrant.
3. Privacy is a **transmission** boundary: local OCR + secret-redaction runs
   on aws-01 before anything is sent off-fleet. Raw frames are never
   persisted or uploaded. Enforced in code (`app/ingest/redact.py`,
   `app/ingest/gateway.py`), not just config.
4. Services bind to the Tailscale IP only, never `0.0.0.0`.

## Status

- **Phase 0 (infra + guardrails): done.** Embeddings gate confirmed live
  against the gateway (`text-embedding-004`, 768-dim); vision alias
  (`qwen-vl-ocr`) and synthesis alias (`glm-4.7`) confirmed reachable.
  Local Qdrant + Postgres running in Docker, bound to the Tailscale IP.
- **Phase 2/3 (ingest, redaction, storage): done and end-to-end tested** —
  see `tests/test_redact.py` and the manual verification below. Redaction
  operates on OCR'd text; realistic (anti-aliased) screenshot text OCRs
  cleanly and redacts correctly, verified against fake AWS keys and emails
  actually being stripped before the point lands in Qdrant.
- **Phase 1 (capture agent): scaffolded, not deployed.** `app/capture/agent.py`
  needs a live Plasma/KWin D-Bus session — it cannot run on aws-01 (headless).
  Deploy to `adraca-desktop` / `adraca-laptop` and wire
  `KWin.ScreenShot2.CaptureActiveWindow` for the target host before use.
- **Phase 4 (Obsidian sync writer): not started.** `pipeline.summarize_session`
  exists as the synthesis primitive; the poll-and-write-to-vault loop is not
  built yet.
- **Phase 5 (RAG API): MVP done.** `POST /query` on `app/api/rag_service.py`
  works end-to-end against real ingested data.
- **Phase 6 (permanence/Ansible): not started.**

## Architecture

```
capture agent (desktop/laptop)     ingest service (aws-01:8088)         RAG API (aws-01:8089)
  DBus Activities + KWin      -->    OCR (tesseract, local)
  screenshot + phash dedup           |
                                      v
                                    redact (regex + entropy)
                                      |
                                      v
                              [sensitive track?] --yes--> stop, local only
                                      | no
                                      v
                              embed (gateway, redacted text only)
                                      |
                                      v
                              Qdrant `screen-context` + Postgres sessions
```

## Running

```bash
cp .env.example .env   # fill in LITELLM_API_KEY, POSTGRES_PASSWORD
bash scripts/init_storage.sh          # docker compose up postgres+qdrant, apply schema, ensure collection
source .venv/bin/activate
python -m uvicorn app.api.ingest_service:app --host $BIND_HOST --port 8088
python -m uvicorn app.api.rag_service:app --host $BIND_HOST --port 8089
```

Retention (cron daily): `python scripts/retention_rollup.py`

## Testing

```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

`tests/test_redact.py` is the code-level evidence for the MVP privacy
acceptance criterion (plan §6): fake AWS keys, GitHub tokens, private key
blocks, emails, phone numbers, JWTs, and high-entropy bare tokens are all
stripped before `assert_clean()` (the hard gate in front of every gateway
call) would allow them through.

## Known limitations

- Redaction operates on OCR output, not the raw pixels — an OCR
  misread can occasionally fragment or garble a secret enough to dodge a
  regex (verified: crisp, normally-rendered screenshot text OCRs cleanly;
  a degenerate test with a tiny bitmap font did not). The entropy-based
  fallback catches most unstructured high-entropy tokens, but the
  per-Activity `sensitive` flag (fully local, no hosted call at all) is the
  real backstop for high-risk tracks (bug bounty / credentials work) — set
  it, don't rely on redaction alone for those.
- Vision-on-redacted-frame path (`gateway.describe_redacted_frame`) is wired
  but not used by the MVP pipeline; OCR text is enough for text-heavy
  screens, and doing image-level redaction (blackout boxes) before any
  vision call is unbuilt.
