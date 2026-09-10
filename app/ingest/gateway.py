"""LiteLLM gateway client. Every function here is an off-fleet network call
and MUST only ever receive already-redacted text (plan §5 transmission
boundary). Each entry point re-asserts cleanliness as a hard gate - this is
deliberate defense-in-depth, not redundancy to be "simplified" away.

Every call is also recorded to the audit log (app/ingest/audit_log.py)
after this module already knows it passed the redaction gate - see that
module for why.
"""
from __future__ import annotations

import time

import httpx

from app.config import settings
from app.ingest import audit_log
from app.ingest.redact import assert_clean

_client = httpx.Client(
    base_url=settings.litellm_base_url,
    headers={"Authorization": f"Bearer {settings.litellm_api_key}"},
    timeout=30.0,
)


def _post(path: str, json_body: dict) -> httpx.Response:
    """The gateway has shown intermittent connection-refused/reset
    failures under normal (non-bursty) call volume - seen independently in
    three unrelated call sites in one day (2026-09-10: a manual segment
    test, the /digest endpoint, and earlier the ragas eval harness's much
    higher-volume case, which already got its own local retry). One retry
    after a short pause covers the common case without masking a real,
    sustained outage - a second failure still raises normally."""
    try:
        return _client.post(path, json=json_body)
    except httpx.TransportError:
        time.sleep(1.5)
        return _client.post(path, json=json_body)


def embed(text: str) -> list[float]:
    assert_clean(text)
    resp = _post("/embeddings", {"model": settings.embedding_model, "input": text})
    ok = resp.is_success
    body = resp.json() if ok else None
    audit_log.record("embed", settings.embedding_model, text, body, ok)
    resp.raise_for_status()
    return body["data"][0]["embedding"]


def synthesize(prompt: str, *, max_tokens: int = 300) -> str:
    assert_clean(prompt)
    resp = _post("/chat/completions", {
        "model": settings.synthesis_model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    })
    ok = resp.is_success
    body = resp.json() if ok else None
    audit_log.record("synthesize", settings.synthesis_model, prompt, body, ok)
    resp.raise_for_status()
    return body["choices"][0]["message"]["content"]


def describe_redacted_frame(redacted_frame_b64_png: str, question: str) -> str:
    """Vision call on an ALREADY-REDACTED frame only (plan §4.2b). Callers
    are responsible for redacting the image itself (e.g. blackout boxes)
    before this is invoked - this function only guards the text prompt."""
    assert_clean(question)
    resp = _post("/chat/completions", {
        "model": settings.vision_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{redacted_frame_b64_png}"},
                    },
                ],
            }
        ],
        "max_tokens": 200,
    })
    ok = resp.is_success
    body = resp.json() if ok else None
    # NOTE: audits the text prompt only, same as every other entry - the
    # image itself is the caller's redaction responsibility (see docstring)
    # and isn't hashed/logged here.
    audit_log.record("vision", settings.vision_model, question, body, ok)
    resp.raise_for_status()
    return body["choices"][0]["message"]["content"]
