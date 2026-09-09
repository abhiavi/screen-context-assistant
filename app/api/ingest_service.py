"""Ingest API (plan §4.2). Capture agents on desktop/laptop POST frames here
over Tailscale. Bound to the Tailscale IP only - see run with
`--host $BIND_HOST`, never 0.0.0.0 (plan §7)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI, File, Form, UploadFile

from app.ingest.pipeline import FrameEvent, process_frame

app = FastAPI(title="screen-context-assistant ingest")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/ingest/frame")
async def ingest_frame(
    frame: UploadFile = File(...),
    track_id: str = Form(...),
    app_name: str = Form(""),
    window_title: str = Form(""),
    host: str = Form(...),
    sensitive: bool = Form(False),
    timestamp: str | None = Form(None),
):
    frame_bytes = await frame.read()
    ts = datetime.fromisoformat(timestamp) if timestamp else datetime.now(timezone.utc)

    result = process_frame(
        FrameEvent(
            frame_bytes=frame_bytes,
            track_id=track_id,
            app_name=app_name,
            window_title=window_title,
            host=host,
            timestamp=ts,
            sensitive=sensitive,
        )
    )
    return {
        "point_id": result.point_id,
        "redacted_categories": result.redacted_categories,
        "skipped_reason": result.skipped_reason,
    }
