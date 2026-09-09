"""Introspects real KDE Activities via D-Bus and writes
~/.config/screen-context-assistant/capture.json for the capture agent.

Run this ONCE per machine (desktop/laptop) during install. It defaults every
discovered Activity to non-sensitive with a slugified track_id - edit the
generated file afterwards to mark any sensitive tracks (plan §5: e.g.
bug-bounty/credentials work should stay fully local, no hosted call at all)
before starting the capture service.
"""
from __future__ import annotations

import json
import re
import socket
import sys
from pathlib import Path

import dbus

ACTIVITY_MANAGER_SERVICE = "org.kde.ActivityManager"
ACTIVITY_MANAGER_PATH = "/ActivityManager/Activities"
ACTIVITY_MANAGER_IFACE = "org.kde.ActivityManager.Activities"

CONFIG_PATH = Path.home() / ".config" / "screen-context-assistant" / "capture.json"
DEFAULT_INGEST_URL = "http://100.96.7.56:8088"


def slugify(name: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", name.lower())).strip("-")


def main() -> None:
    bus = dbus.SessionBus()
    proxy = bus.get_object(ACTIVITY_MANAGER_SERVICE, ACTIVITY_MANAGER_PATH)
    iface = dbus.Interface(proxy, dbus_interface=ACTIVITY_MANAGER_IFACE)

    activity_ids = [str(a) for a in iface.ListActivities()]
    activity_track_map = {}
    for activity_id in activity_ids:
        name = str(iface.ActivityName(activity_id))
        activity_track_map[activity_id] = slugify(name) or activity_id

    host_name = sys.argv[1] if len(sys.argv) > 1 else socket.gethostname()

    config = {
        "ingest_base_url": DEFAULT_INGEST_URL,
        "host_name": host_name,
        "activity_track_map": activity_track_map,
        "sensitive_tracks": [],
        "poll_interval_seconds": 2.0,
        "heartbeat_seconds": 60.0,
    }

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n")

    print(f"wrote {CONFIG_PATH}")
    print("discovered activities:")
    for activity_id, track_id in activity_track_map.items():
        name = str(iface.ActivityName(activity_id))
        print(f"  {name!r:20s} -> track_id={track_id!r} (activity_id={activity_id})")
    print("\nEdit sensitive_tracks in the config file before starting the capture service.")


if __name__ == "__main__":
    main()
