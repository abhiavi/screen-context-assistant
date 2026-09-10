#!/usr/bin/env bash
# Installs and starts the floating avatar companion on THIS machine
# (adraca-mini only - see plan v4 re-scope). Run as the desktop-session user
# (abhishek), not root. Assumes the capture agent is already installed
# (scripts/install_capture_agent.sh) - the avatar reuses its venv.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== system deps (pacman) =="
# pyside6 + layer-shell-qt were already present on mini when this was built
# (2026-09-09) - included here for reproducibility on a fresh machine. cmake
# is for the real-blur QML plugin build below (wayland_blur/). portaudio is
# for the voice loop's sounddevice (app/avatar/voice.py).
sudo pacman -S --needed --noconfirm python-pyside6 layer-shell-qt cmake portaudio

if [ ! -d .venv ]; then
  echo "== python venv (--system-site-packages so pacman's pyside6/dbus are visible) =="
  python3 -m venv --system-site-packages .venv
fi
# Outside the [ ! -d .venv ] guard above on purpose - pip install is
# idempotent, and this needs to actually run on every invocation so a
# re-run of this script (not just a from-scratch install) picks up
# newly-added dependencies. It didn't, before 2026-09-10 - a real bug,
# found while adding the voice deps below and realizing they'd never
# install on mini's already-existing venv.
source .venv/bin/activate
pip install -q httpx imagehash pillow python-dotenv faster-whisper sounddevice piper-tts

echo "== downloading voice models (push-to-talk STT/TTS, app/avatar/voice.py) =="
# faster-whisper's model downloads lazily from Hugging Face on first real
# use (no separate step needed here) - only Piper's voice needs an
# explicit fetch.
mkdir -p ~/.local/share/screen-context-assistant/voice_models
python3 -m piper.download_voices --download-dir ~/.local/share/screen-context-assistant/voice_models en_US-lessac-medium || \
  echo "piper voice download failed - voice output will stay silent until this is retried, see README"

echo "== building real-blur QML plugin (see README: Real compositor blur-behind) =="
# Falls back to simulated glass automatically if this doesn't succeed - not
# fatal to the rest of setup, so failures here don't abort the script.
(
  cd app/avatar/wayland_blur
  cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH=/usr/lib/qt6
  cmake --build build -j"$(nproc)"
) || echo "wayland_blur plugin build failed - avatar will fall back to simulated glass, see README"

echo "== enabling KWin's Blur effect (required for the plugin above to do anything) =="
kwriteconfig6 --file kwinrc --group Plugins --key blurEnabled true
qdbus6 org.kde.KWin /KWin reconfigure || true

echo "== installing systemd user unit =="
mkdir -p ~/.config/systemd/user
cp deploy/screen-context-avatar.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now screen-context-avatar.service

echo "== status =="
systemctl --user status screen-context-avatar.service --no-pager || true
echo
echo "Left-click the avatar to expand/recall; Ctrl+click to move it to the next corner."
echo "Edit ~/.config/screen-context-assistant/avatar.json to tune idle threshold / vault-write interval."
