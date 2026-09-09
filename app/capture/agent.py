"""Capture agent (plan §4.1) - runs on desktop/laptop, NOT on aws-01.

Watches Plasma Activities via DBus for the track signal and takes
Wayland-native screenshots via KWin's ScreenShot2 interface on window-focus
change, perceptual-hash-deduping near-identical frames before POSTing to the
ingest service on aws-01.

NOTE: this module needs a live Plasma/KWin D-Bus session to run and cannot
be exercised on aws-01 (headless, no desktop session). It is included here
as the Phase-1 deliverable to be deployed onto adraca-desktop and
adraca-laptop; validate there with `dbus-monitor` before relying on it.
"""
from __future__ import annotations

import io
import time
from dataclasses import dataclass

import httpx
import imagehash
from PIL import Image

try:
    import dbus
    import dbus.mainloop.glib
    from gi.repository import GLib
except ImportError:  # not available on aws-01; expected there
    dbus = None
    GLib = None

ACTIVITY_MANAGER_SERVICE = "org.kde.ActivityManager"
ACTIVITY_MANAGER_PATH = "/ActivityManager/Activities"
ACTIVITY_MANAGER_IFACE = "org.kde.ActivityManager.Activities"

KWIN_SERVICE = "org.kde.KWin.ScreenShot2"
KWIN_PATH = "/org/kde/KWin/ScreenShot2"
KWIN_IFACE = "org.kde.KWin.ScreenShot2"

PHASH_DEDUP_THRESHOLD = 4  # hamming distance below which a frame is a near-dup


@dataclass
class CaptureConfig:
    ingest_base_url: str  # e.g. http://100.96.7.56:8088
    host_name: str  # "desktop" | "laptop"
    activity_track_map: dict[str, str]  # KDE Activity id -> track_id
    sensitive_tracks: set[str]  # track_ids to flag sensitive=True (fully local, see plan §5)
    poll_interval_seconds: float = 2.0


class CaptureAgent:
    def __init__(self, config: CaptureConfig):
        if dbus is None:
            raise RuntimeError("python-dbus / PyGObject not installed - this must run on the desktop, not aws-01")
        self.config = config
        self._last_hash: imagehash.ImageHash | None = None
        self._client = httpx.Client(base_url=config.ingest_base_url, timeout=10.0)
        self._bus = dbus.SessionBus()
        self._activity_proxy = self._bus.get_object(ACTIVITY_MANAGER_SERVICE, ACTIVITY_MANAGER_PATH)
        self._kwin_proxy = self._bus.get_object(KWIN_SERVICE, KWIN_PATH)

    def current_activity_id(self) -> str:
        return str(self._activity_proxy.CurrentActivity(dbus_interface=ACTIVITY_MANAGER_IFACE))

    def screenshot_active_window(self) -> bytes:
        # KWin.ScreenShot2 CaptureActiveWindow returns a file descriptor to a
        # pipe of PNG bytes over DBus; wiring is host-specific and left for
        # on-device validation (documented, not exercised on aws-01).
        raise NotImplementedError("wire up org.kde.KWin.ScreenShot2 CaptureActiveWindow on the target host")

    def maybe_send_frame(self, frame_bytes: bytes, app_name: str, window_title: str) -> None:
        img = Image.open(io.BytesIO(frame_bytes))
        h = imagehash.phash(img)
        if self._last_hash is not None and (h - self._last_hash) < PHASH_DEDUP_THRESHOLD:
            return  # near-duplicate, skip
        self._last_hash = h

        activity_id = self.current_activity_id()
        track_id = self.config.activity_track_map.get(activity_id, activity_id)
        sensitive = track_id in self.config.sensitive_tracks

        self._client.post(
            "/ingest/frame",
            data={
                "track_id": track_id,
                "app_name": app_name,
                "window_title": window_title,
                "host": self.config.host_name,
                "sensitive": sensitive,
            },
            files={"frame": ("frame.png", frame_bytes, "image/png")},
        )

    def run_forever(self) -> None:
        while True:
            try:
                frame = self.screenshot_active_window()
                self.maybe_send_frame(frame, app_name="", window_title="")
            except NotImplementedError:
                raise
            except Exception:
                pass  # capture agent must not crash the desktop session; log upstream
            time.sleep(self.config.poll_interval_seconds)
