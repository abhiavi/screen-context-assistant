"""Capture agent entrypoint. Run as `python -m app.capture.run` on
desktop/laptop only (see deploy/screen-context-capture.service).

Reads host-local config from ~/.config/screen-context-assistant/capture.json,
created by scripts/generate_capture_config.py during install.
"""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

from app.capture.agent import CaptureAgent, CaptureConfig

_DEFAULT_EXCLUDED_APPS = ["keepassxc", "bitwarden", "1password", "org.kde.kwalletmanager5"]

CONFIG_PATH = Path.home() / ".config" / "screen-context-assistant" / "capture.json"


def load_config() -> CaptureConfig:
    if not CONFIG_PATH.exists():
        print(f"missing config: {CONFIG_PATH} - run scripts/generate_capture_config.py first", file=sys.stderr)
        sys.exit(1)

    data = json.loads(CONFIG_PATH.read_text())
    return CaptureConfig(
        ingest_base_url=data["ingest_base_url"],
        host_name=data.get("host_name") or socket.gethostname(),
        activity_track_map=data["activity_track_map"],
        sensitive_tracks=set(data.get("sensitive_tracks", [])),
        poll_interval_seconds=float(data.get("poll_interval_seconds", 2.0)),
        heartbeat_seconds=float(data.get("heartbeat_seconds", 60.0)),
        excluded_apps=data.get("excluded_apps", _DEFAULT_EXCLUDED_APPS),
    )


def main() -> None:
    config = load_config()
    agent = CaptureAgent(config)
    agent.run_forever()


if __name__ == "__main__":
    main()
