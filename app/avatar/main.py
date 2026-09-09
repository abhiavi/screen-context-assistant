"""Floating avatar companion (plan v4 re-scope). Runs on adraca-mini only -
needs a live KDE/KWin Wayland session, cannot run on aws-01.

Behaviors:
  - floating, always-on-top, draggable overlay (org.kde.layershell QML)
  - click -> on-demand recall from the aws-01 backend's /recall endpoint
  - idle-return -> proactive recall ("here's what you were doing")
  - periodic distilled-summary write into the ObsidianVault journal
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import dbus
from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWebEngineQuick import QtWebEngineQuick

from app.avatar.vault_writer import append_entry

CONFIG_PATH = Path.home() / ".config" / "screen-context-assistant" / "avatar.json"

DEFAULT_CONFIG = {
    "backend_base_url": "http://100.96.7.56:8089",
    "idle_poll_seconds": 5,
    "idle_threshold_seconds": 300,
    "vault_write_interval_seconds": 900,
}


def load_config() -> dict:
    if CONFIG_PATH.exists():
        return {**DEFAULT_CONFIG, **json.loads(CONFIG_PATH.read_text())}
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n")
    return dict(DEFAULT_CONFIG)


class IdleWatcher:
    """Wraps systemd-logind's IdleHint - DE-agnostic, more reliable than any
    single compositor's screensaver interface (KWin's org.freedesktop.
    ScreenSaver.GetSessionIdleTime is NOT supported on this KWin/platform,
    verified empirically on mini).

    Can't resolve "our own" session via GetSessionByPID: a systemd --user
    service runs in a different cgroup than the interactive login session,
    so logind's PID-based lookup raises NoSessionForPID (learned by
    crashing under the real systemd unit, worked fine in an interactive SSH
    test which misled the first pass). Enumerate sessions instead and pick
    the actual seat0 graphical one for the current user."""

    def __init__(self):
        self._bus = dbus.SystemBus()
        manager = self._bus.get_object("org.freedesktop.login1", "/org/freedesktop/login1")
        self._manager = dbus.Interface(manager, "org.freedesktop.login1.Manager")
        self._session = self._bus.get_object("org.freedesktop.login1", self._find_graphical_session())
        self._props = dbus.Interface(self._session, "org.freedesktop.DBus.Properties")
        self._was_idle = False

    def _find_graphical_session(self) -> str:
        uid = os.getuid()
        for _session_id, _uid, _user, seat, path in self._manager.ListSessions():
            if int(_uid) != uid or not str(seat):
                continue
            props = dbus.Interface(
                self._bus.get_object("org.freedesktop.login1", path), "org.freedesktop.DBus.Properties"
            )
            if str(props.Get("org.freedesktop.login1.Session", "Type")) in ("wayland", "x11"):
                return str(path)
        raise RuntimeError("no graphical login session found for idle detection")

    def is_idle(self) -> bool:
        return bool(self._props.Get("org.freedesktop.login1.Session", "IdleHint"))

    def just_returned(self) -> bool:
        """True exactly once, on the idle->active transition."""
        idle_now = self.is_idle()
        returned = self._was_idle and not idle_now
        self._was_idle = idle_now
        return returned


class Backend(QObject):
    recallReady = Signal(str, str, str)  # summary, track_id, app_name
    recallFailed = Signal(str)
    recallRequested = Signal()  # fires immediately, before the network reply lands
    answerReady = Signal(str)  # answer to a typed question
    answerFailed = Signal(str)

    def __init__(self, config: dict):
        super().__init__()
        self.config = config
        self._net = QNetworkAccessManager(self)
        self._idle = IdleWatcher()

        self._idle_timer = QTimer(self)
        self._idle_timer.setInterval(int(config["idle_poll_seconds"] * 1000))
        self._idle_timer.timeout.connect(self._check_idle_return)
        self._idle_timer.start()

        self._vault_timer = QTimer(self)
        self._vault_timer.setInterval(int(config["vault_write_interval_seconds"] * 1000))
        self._vault_timer.timeout.connect(lambda: self.requestRecall("", for_vault=True))
        self._vault_timer.start()

    def _check_idle_return(self) -> None:
        if self._idle.just_returned():
            self.requestRecall("", proactive=True)

    @Slot(str)
    def requestRecall(self, track_id: str = "", proactive: bool = False, for_vault: bool = False) -> None:
        if not for_vault:
            self.recallRequested.emit()
        url = f"{self.config['backend_base_url']}/recall"
        if track_id:
            url += f"?track_id={track_id}"
        request = QNetworkRequest(QUrl(url))
        reply = self._net.get(request)
        reply.finished.connect(lambda: self._on_recall_reply(reply, for_vault))

    def _on_recall_reply(self, reply: QNetworkReply, for_vault: bool) -> None:
        if reply.error() != QNetworkReply.NetworkError.NoError:
            self.recallFailed.emit(reply.errorString())
            reply.deleteLater()
            return
        try:
            data = json.loads(bytes(reply.readAll().data()))
        except Exception as exc:  # noqa: BLE001
            self.recallFailed.emit(str(exc))
            reply.deleteLater()
            return
        reply.deleteLater()

        summary = data.get("summary", "")
        track_id = data.get("track_id") or ""
        app_name = data.get("app_name") or ""

        if for_vault:
            if data.get("frame_count", 0) > 0:
                append_entry(track_id, app_name, summary)
        else:
            self.recallReady.emit(summary, track_id, app_name)

    @Slot(str)
    def askQuestion(self, question: str) -> None:
        """User-typed question (the ask bar) -> POST /query on the RAG
        service, which does a real Qdrant similarity search + synthesis
        (unlike /recall, which just replays the most recent session)."""
        if not question.strip():
            return
        self.recallRequested.emit()
        request = QNetworkRequest(QUrl(f"{self.config['backend_base_url']}/query"))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        body = json.dumps({"question": question}).encode()
        reply = self._net.post(request, body)
        reply.finished.connect(lambda: self._on_query_reply(reply))

    def _on_query_reply(self, reply: QNetworkReply) -> None:
        if reply.error() != QNetworkReply.NetworkError.NoError:
            self.answerFailed.emit(reply.errorString())
            reply.deleteLater()
            return
        try:
            data = json.loads(bytes(reply.readAll().data()))
        except Exception as exc:  # noqa: BLE001
            self.answerFailed.emit(str(exc))
            reply.deleteLater()
            return
        reply.deleteLater()
        self.answerReady.emit(data.get("answer", ""))


def main() -> None:
    QtWebEngineQuick.initialize()
    config = load_config()
    app = QGuiApplication(sys.argv)
    backend = Backend(config)

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", backend)
    qml_path = Path(__file__).parent / "avatar.qml"
    engine.load(QUrl.fromLocalFile(str(qml_path)))

    if not engine.rootObjects():
        print("failed to load avatar.qml", file=sys.stderr)
        sys.exit(1)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
