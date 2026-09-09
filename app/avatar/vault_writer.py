"""Distilled-summary vault writer (plan v4: old Phase 4 folded into the
avatar). Runs on mini only - the avatar has direct filesystem access to
~/ObsidianVault, unlike aws-01.

NOTE: the real vault has an established per-project folder taxonomy
(01_Adraca_Enterprise, 02_Lambda_Consulting, 03_Academic_Research,
05_Sovereign_Infra, Bounty_Programs, ...) that does not map 1:1 or
unambiguously onto the 7 KDE Activity track_ids (adraca, career, darkside,
home-server, lambda, personal, research) - guessing that mapping risks
scattering notes into the wrong place in an already-organized personal
vault. Until the Operator confirms per-track destinations, everything
writes to one clearly-labeled journal file instead."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

VAULT_ROOT = Path.home() / "ObsidianVault"
JOURNAL_DIR = VAULT_ROOT / "10_Agent_Embassies" / "Screen_Context_Journal"


def append_entry(track_id: str, app_name: str | None, summary: str, when: datetime | None = None) -> Path:
    when = when or datetime.now()
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    note_path = JOURNAL_DIR / f"{when:%Y-%m-%d}.md"

    if not note_path.exists():
        note_path.write_text(f"# Screen Context Journal — {when:%Y-%m-%d}\n\n")

    with note_path.open("a") as f:
        f.write(f"- **{when:%H:%M}** [{track_id}{f' / {app_name}' if app_name else ''}] {summary}\n")

    return note_path
