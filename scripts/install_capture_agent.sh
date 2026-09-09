#!/usr/bin/env bash
# Installs and starts the capture agent on THIS machine (desktop or laptop).
# Run as the desktop-session user (abhishek), not root. Assumes the repo has
# already been synced to ~/screen-context-assistant (e.g. via git clone/pull
# or rsync from aws-01 - the capture agent code has no secrets in it).
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== system deps (pacman) =="
sudo pacman -S --needed --noconfirm python-dbus python-gobject

echo "== aur deps (kdotool - window title/app-name lookup) =="
if ! command -v kdotool >/dev/null; then
  yay -S --noconfirm --removemake kdotool
fi

echo "== python venv (--system-site-packages so pacman's python-dbus is visible) =="
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -q httpx imagehash pillow python-dotenv

PYTHON_EXEC="$(readlink -f .venv/bin/python3)"

echo "== KWin screenshot authorization (.desktop file) =="
# KWin's org.kde.KWin.ScreenShot2 interface refuses CaptureActiveWindow
# unless the calling process's executable path matches the Exec= of an
# installed .desktop file that declares
# X-KDE-DBUS-Restricted-Interfaces=org.kde.KWin.ScreenShot2. Verified live
# against KWin 6.7.4 on adraca-desktop on 2026-09-09 - without this the call
# fails with org.kde.KWin.ScreenShot2.Error.NoAuthorized.
mkdir -p ~/.local/share/applications
sed "s|__PYTHON_EXEC__|${PYTHON_EXEC}|" deploy/screen-context-capture.desktop.template \
  > ~/.local/share/applications/screen-context-capture.desktop
update-desktop-database ~/.local/share/applications 2>/dev/null || true

echo "== generating per-host capture config =="
python scripts/generate_capture_config.py "$(hostname)"

echo "== installing systemd user unit =="
# NOTE: ExecStart here deliberately keeps the .venv/bin/python3 SYMLINK path
# (systemd expands %h itself) rather than the resolved interpreter path -
# Python's venv activation depends on finding pyvenv.cfg relative to the
# invoked (symlink) path, not the kernel-resolved /proc/pid/exe target that
# the .desktop file above had to use instead.
mkdir -p ~/.config/systemd/user
cp deploy/screen-context-capture.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now screen-context-capture.service

echo "== status =="
systemctl --user status screen-context-capture.service --no-pager || true
echo
echo "Edit ~/.config/screen-context-assistant/capture.json to mark sensitive_tracks,"
echo "then: systemctl --user restart screen-context-capture.service"
