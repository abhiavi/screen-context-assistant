"""Capture agent (plan §4.1) - runs on desktop/laptop, NOT on aws-01.

Watches Plasma Activities via DBus for the track signal and takes
Wayland-native screenshots via KWin's ScreenShot2 interface, triggered on
window-focus-change (detected by polling the active window id via kdotool)
or a heartbeat timer, perceptual-hash-deduping near-identical frames before
POSTing to the ingest service on aws-01.

Screenshot wire format (verified empirically against a live KWin 6.7.4
session on adraca-desktop, and cross-checked against the kwin-mcp project's
implementation of the same interface): `CaptureActiveWindow` streams raw
ARGB32_Premultiplied pixel data (native/little-endian byte order, i.e.
stored as BGRA) to the pipe fd, with width/height/stride given in the
`results` map returned by the D-Bus call itself.

Window title / app (window class) name come from `kdotool`
(https://github.com/jinliu/kdotool) - the KWin-Wayland equivalent of
xdotool, installed via AUR on both desktop and laptop.

NOTE: this module needs a live Plasma/KWin D-Bus session and cannot be
exercised on aws-01 (headless, no desktop session).
"""
from __future__ import annotations

import io
import os
import subprocess
import time
from dataclasses import dataclass

import httpx
import imagehash
from PIL import Image

try:
    import dbus
    import dbus.mainloop.glib
except ImportError:  # not available on aws-01; expected there
    dbus = None

ACTIVITY_MANAGER_SERVICE = "org.kde.ActivityManager"
ACTIVITY_MANAGER_PATH = "/ActivityManager/Activities"
ACTIVITY_MANAGER_IFACE = "org.kde.ActivityManager.Activities"

KWIN_SERVICE = "org.kde.KWin.ScreenShot2"
KWIN_PATH = "/org/kde/KWin/ScreenShot2"
KWIN_IFACE = "org.kde.KWin.ScreenShot2"

PHASH_DEDUP_THRESHOLD = 4  # hamming distance below which a frame is a near-dup
KDOTOOL_TIMEOUT = 3.0


@dataclass
class CaptureConfig:
    ingest_base_url: str  # e.g. http://100.96.7.56:8088
    host_name: str  # "desktop" | "laptop"
    activity_track_map: dict[str, str]  # KDE Activity id -> track_id
    sensitive_tracks: set[str]  # track_ids to flag sensitive=True (fully local, see plan §5)
    poll_interval_seconds: float = 2.0  # how often to check for a focus change
    heartbeat_seconds: float = 60.0  # force a capture even if focus hasn't changed


def _kdotool(*args: str) -> str:
    result = subprocess.run(
        ["kdotool", *args], capture_output=True, text=True, timeout=KDOTOOL_TIMEOUT, check=False,
    )
    return result.stdout.strip()


class CaptureAgent:
    def __init__(self, config: CaptureConfig):
        if dbus is None:
            raise RuntimeError("python-dbus not installed - this must run on the desktop, not aws-01")
        self.config = config
        self._last_hash: imagehash.ImageHash | None = None
        self._last_window_id: str | None = None
        self._last_capture_time: float = 0.0
        self._client = httpx.Client(base_url=config.ingest_base_url, timeout=10.0)

        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self._bus = dbus.SessionBus()
        self._activity_proxy = self._bus.get_object(ACTIVITY_MANAGER_SERVICE, ACTIVITY_MANAGER_PATH)
        self._kwin_proxy = self._bus.get_object(KWIN_SERVICE, KWIN_PATH)
        self._kwin_iface = dbus.Interface(self._kwin_proxy, dbus_interface=KWIN_IFACE)

    def current_activity_id(self) -> str:
        return str(self._activity_proxy.CurrentActivity(dbus_interface=ACTIVITY_MANAGER_IFACE))

    def active_window_info(self) -> tuple[str | None, str, str]:
        """Returns (window_id, title, app_name) via kdotool. Empty strings if
        no window is focused or kdotool is unavailable."""
        try:
            window_id = _kdotool("getactivewindow")
            if not window_id:
                return None, "", ""
            title = _kdotool("getwindowname", window_id)
            app_name = _kdotool("getwindowclassname", window_id)
            return window_id, title, app_name
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            return None, "", ""

    def screenshot_active_window(self) -> bytes:
        """Capture the focused window via KWin.ScreenShot2 and return PNG bytes."""
        read_fd, write_fd = os.pipe()
        try:
            results = self._kwin_iface.CaptureActiveWindow(
                dbus.Dictionary({}, signature="sv"),
                dbus.types.UnixFd(write_fd),
            )
        finally:
            os.close(write_fd)

        chunks = []
        try:
            while True:
                chunk = os.read(read_fd, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            os.close(read_fd)
        raw = b"".join(chunks)

        width = int(results["width"])
        height = int(results["height"])
        stride = int(results["stride"])

        img = Image.frombytes("RGBA", (width, height), raw, "raw", "BGRA", stride)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

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
                "sensitive": str(sensitive).lower(),
            },
            files={"frame": ("frame.png", frame_bytes, "image/png")},
        )

    def poll_once(self) -> None:
        window_id, title, app_name = self.active_window_info()
        now = time.monotonic()
        focus_changed = window_id is not None and window_id != self._last_window_id
        heartbeat_due = (now - self._last_capture_time) >= self.config.heartbeat_seconds

        if window_id is None or not (focus_changed or heartbeat_due):
            return

        self._last_window_id = window_id
        self._last_capture_time = now
        frame = self.screenshot_active_window()
        self.maybe_send_frame(frame, app_name=app_name, window_title=title)

    def run_forever(self) -> None:
        while True:
            try:
                self.poll_once()
            except Exception:
                pass  # capture agent must not crash the desktop session; log upstream
            time.sleep(self.config.poll_interval_seconds)
