"""Distilled-summary vault writer (plan v4: old Phase 4 folded into the avatar).
Runs on mini only - the avatar has direct filesystem access to ~/ObsidianVault.

Per-track routing (Operator-confirmed 2026-09-10): the vault folders already mirror the
KDE Activities, so each track_id maps to a `_Session_Journal.md` INSIDE its established
folder (config: avatar.json `vault_track_folders`) - recaps sit beside the real work
without polluting curated notes. Unmapped track_ids fall back to a single dated journal.
Distilled summaries only; raw frames never touch the vault.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

VAULT_ROOT = Path.home() / "ObsidianVault"
JOURNAL_DIR = VAULT_ROOT / "10_Agent_Embassies" / "Screen_Context_Journal"  # fallback for unmapped tracks
CONFIG = Path.home() / ".config" / "screen-context-assistant" / "avatar.json"


def _track_folders() -> dict:
    """track_id -> vault-relative path of its `_Session_Journal.md` (from avatar.json)."""
    try:
        return json.loads(CONFIG.read_text()).get("vault_track_folders", {}) or {}
    except Exception:
        return {}


def append_entry(track_id: str, app_name: str | None, summary: str, when: datetime | None = None) -> Path:
    when = when or datetime.now()
    rel = _track_folders().get(track_id)

    if rel:
        # per-track journal inside the track's real vault folder
        note_path = (VAULT_ROOT / rel).resolve()
        note_path.parent.mkdir(parents=True, exist_ok=True)
        if not note_path.exists():
            note_path.write_text(
                f"# Session Journal — {track_id}\n\n"
                "_Auto-generated recaps from the on-screen assistant. Distilled summaries only._\n\n"
            )
    else:
        # fallback: one dated journal for tracks with no mapping
        JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
        note_path = JOURNAL_DIR / f"{when:%Y-%m-%d}.md"
        if not note_path.exists():
            note_path.write_text(f"# Screen Context Journal — {when:%Y-%m-%d}\n\n")

    with note_path.open("a") as f:
        f.write(f"- **{when:%Y-%m-%d %H:%M}** [{track_id}{f' / {app_name}' if app_name else ''}] {summary}\n")

    return note_path
