"""Regression test for the proactive-recall cooldown (2026-09-10): found
live that systemd-logind's IdleHint can flap rapidly, firing 6 proactive
recalls in 68 seconds in real usage. app.avatar.proactivity has zero
PySide6/dbus imports specifically so this is importable/testable here on
aws-01, even though app.avatar.main itself only ever runs on mini."""
from app.avatar.proactivity import should_fire_proactive_recall


def test_no_recall_without_a_transition():
    assert should_fire_proactive_recall(False, None, 600) is False
    assert should_fire_proactive_recall(False, 9999, 600) is False


def test_first_ever_transition_fires():
    assert should_fire_proactive_recall(True, None, 600) is True


def test_transition_within_cooldown_is_suppressed():
    assert should_fire_proactive_recall(True, 30, 600) is False


def test_transition_at_exactly_the_cooldown_boundary_fires():
    assert should_fire_proactive_recall(True, 600, 600) is True


def test_transition_after_cooldown_fires():
    assert should_fire_proactive_recall(True, 900, 600) is True


def test_flapping_pattern_only_fires_once():
    """Reproduces the actual observed bug: rapid idle<->active flapping
    should fire exactly once, not once per flap."""
    cooldown = 600
    last: float | None = None
    fired = 0
    # 6 transitions ~13s apart (matches the real 68s/6 pattern observed)
    for t in [0, 13, 26, 39, 52, 65]:
        seconds_since_last = (t - last) if last is not None else None
        if should_fire_proactive_recall(True, seconds_since_last, cooldown):
            fired += 1
            last = t
    assert fired == 1
