"""Ingest pipeline orchestration (plan §4.2 / §5).

Stage order is fixed and intentional:
  (a) local OCR + redaction FIRST, on-fleet, no network call
  (b) optional vision call, only on a redacted frame, only if configured
  (c) embed the redacted text, then persist

Raw frame bytes are held in memory only for the duration of this call and
are never written to disk or transmitted unless UPLOAD_RAW_FRAMES is
explicitly true (default false, plan §5) - that flag does not exist in this
codebase's control flow at all today; it is a documented future extension
point, not a bypass.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.config import settings
from app.ingest import gateway
from app.ingest.ocr import extract_text
from app.ingest.redact import redact_text
from app.storage import postgres_store, qdrant_store


@dataclass
class FrameEvent:
    frame_bytes: bytes
    track_id: str
    app_name: str
    window_title: str
    host: str
    timestamp: datetime
    sensitive: bool = False


@dataclass
class IngestResult:
    point_id: str | None
    redacted_categories: list[str]
    skipped_reason: str | None = None


def process_frame(event: FrameEvent) -> IngestResult:
    # (a) local OCR + redaction, on-fleet, before anything else touches this data
    raw_text = extract_text(event.frame_bytes)
    ocr_result = redact_text(raw_text)
    title_result = redact_text(event.window_title)

    # Sensitive tracks (e.g. bug-bounty/credentials work) never get a hosted
    # call at all - fully local handling (plan §5).
    if event.sensitive:
        return IngestResult(point_id=None, redacted_categories=ocr_result.categories,
                             skipped_reason="sensitive_track_local_only")

    if not ocr_result.text.strip():
        return IngestResult(point_id=None, redacted_categories=[], skipped_reason="no_text_extracted")

    # (b) vision call is opt-in and out of scope for the MVP text pipeline -
    # left as a documented extension point in gateway.describe_redacted_frame.

    # (c) embed the redacted text (off-fleet call - gateway.embed asserts
    # cleanliness again as a hard gate before it leaves the process).
    embedding = gateway.embed(ocr_result.text)

    point_id = qdrant_store.upsert_frame(
        embedding=embedding,
        track_id=event.track_id,
        app_name=event.app_name,
        window_title_redacted=title_result.text,
        ocr_text_redacted=ocr_result.text,
        timestamp=event.timestamp,
        sensitive=event.sensitive,
    )

    # `event.frame_bytes` and `raw_text` fall out of scope here and are
    # never persisted - RETAIN_RAW_FRAMES / UPLOAD_RAW_FRAMES stay false by
    # default per plan §5.

    return IngestResult(point_id=point_id, redacted_categories=ocr_result.categories + title_result.categories)


def summarize_session(session_id: int, ocr_texts_redacted: list[str], track_display_name: str) -> str:
    """Synthesize a short per-track summary from already-redacted OCR text
    for the Obsidian sync writer (plan §4.4). Batched per session/interval,
    never per-frame."""
    joined = "\n".join(t for t in ocr_texts_redacted if t.strip())[:4000]
    prompt = (
        f"Summarize in 2-3 sentences what the user was doing on the track "
        f"'{track_display_name}', based on this redacted on-screen text "
        f"(OCR'd from screenshots, secrets already stripped):\n\n{joined}"
    )
    summary = gateway.synthesize(prompt)
    postgres_store.save_summary(session_id, summary)
    return summary
