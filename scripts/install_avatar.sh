#!/usr/bin/env bash
# Installs and starts the floating avatar companion on THIS machine
# (adraca-mini only - see plan v4 re-scope). Run as the desktop-session user
# (abhishek), not root. Assumes the capture agent is already installed
# (scripts/install_capture_agent.sh) - the avatar reuses its venv.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== system deps (pacman) =="
# pyside6 + layer-shell-qt were already present on mini when this was built
# (2026-09-09) - included here for reproducibility on a fresh machine.
sudo pacman -S --needed --noconfirm python-pyside6 layer-shell-qt

if [ ! -d .venv ]; then
  echo "== python venv (--system-site-packages so pacman's pyside6/dbus are visible) =="
  python3 -m venv --system-site-packages .venv
  source .venv/bin/activate
  pip install -q httpx imagehash pillow python-dotenv
fi

echo "== installing systemd user unit =="
mkdir -p ~/.config/systemd/user
cp deploy/screen-context-avatar.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now screen-context-avatar.service

echo "== status =="
systemctl --user status screen-context-avatar.service --no-pager || true
echo
echo "Left-click the avatar to recall; right-click to move it to the next corner."
echo "Edit ~/.config/screen-context-assistant/avatar.json to tune idle threshold / vault-write interval."
