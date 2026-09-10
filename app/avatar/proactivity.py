"""Pure decision logic for the avatar's proactive ("welcome back") recall,
deliberately kept free of PySide6/dbus imports so it can be unit-tested
from anywhere (see tests/test_proactivity.py) - app/avatar/main.py itself
can only be imported on a machine with PySide6 + dbus installed (mini),
never on aws-01, where the rest of this project's test suite runs.
"""
from __future__ import annotations


def should_fire_proactive_recall(
    just_returned: bool, seconds_since_last: float | None, cooldown_seconds: float,
) -> bool:
    """The underlying idle->active signal (IdleWatcher.just_returned in
    main.py, wrapping systemd-logind's IdleHint) has been observed
    flapping rapidly in real usage - 6 proactive recalls fired within 68
    seconds on 2026-09-10, each one a real synthesis call and vault write.
    Not root-caused (OS/session-manager behavior, not this app's logic),
    so the fix is a cooldown here rather than chasing IdleHint upstream."""
    if not just_returned:
        return False
    if seconds_since_last is not None and seconds_since_last < cooldown_seconds:
        return False
    return True
