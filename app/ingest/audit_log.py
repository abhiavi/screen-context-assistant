"""Append-only audit log of every off-fleet call this app makes (upgrade
roadmap "Now" item 2/6: "a local, append-only log of every outbound LLM
call - feature, key, token count, redaction-pass hash, cost").

This is a trust artifact, not a debug log: it exists so the transmission
boundary (plan §5 / app/ingest/redact.py's assert_clean gate) can be
audited after the fact, not just trusted at call time. Deliberately a
plain append-only JSONL file rather than a mutable DB table - a row that
can be UPDATEd or DELETEd is a weaker trust artifact than one that can
only ever be appended to. Records the SHA-256 of the exact text that left
the device, never the text itself, so the log can't become a second copy
of anything sensitive even though the text was already redacted before
reaching here.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "audit_log.jsonl"


def record(call: str, model: str, text_sent: str, response: dict[str, Any] | None, ok: bool) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    usage = (response or {}).get("usage") or {}
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "call": call,
        "model": model,
        "chars_sent": len(text_sent),
        "redacted_sha256": hashlib.sha256(text_sent.encode()).hexdigest(),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "ok": ok,
    }
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")
