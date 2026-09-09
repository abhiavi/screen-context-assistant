"""LiteLLM gateway client. Every function here is an off-fleet network call
and MUST only ever receive already-redacted text (plan §5 transmission
boundary). Each entry point re-asserts cleanliness as a hard gate - this is
deliberate defense-in-depth, not redundancy to be "simplified" away.
"""
from __future__ import annotations

import httpx

from app.config import settings
from app.ingest.redact import assert_clean

_client = httpx.Client(
    base_url=settings.litellm_base_url,
    headers={"Authorization": f"Bearer {settings.litellm_api_key}"},
    timeout=30.0,
)


def embed(text: str) -> list[float]:
    assert_clean(text)
    resp = _client.post(
        "/embeddings",
        json={"model": settings.embedding_model, "input": text},
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


def synthesize(prompt: str, *, max_tokens: int = 300) -> str:
    assert_clean(prompt)
    resp = _client.post(
        "/chat/completions",
        json={
            "model": settings.synthesis_model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        },
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def describe_redacted_frame(redacted_frame_b64_png: str, question: str) -> str:
    """Vision call on an ALREADY-REDACTED frame only (plan §4.2b). Callers
    are responsible for redacting the image itself (e.g. blackout boxes)
    before this is invoked - this function only guards the text prompt."""
    assert_clean(question)
    resp = _client.post(
        "/chat/completions",
        json={
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
        },
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]
