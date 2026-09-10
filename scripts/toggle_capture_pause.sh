#!/usr/bin/env bash
# Toggles the capture agent's pause flag (upgrade roadmap "Now" item 3/6).
# Bind this to a KDE global keyboard shortcut: System Settings ->
# Shortcuts -> Custom Shortcuts -> new Command/URL, point it at this
# script's full path. No custom KGlobalAccel/D-Bus registration needed -
# KDE's own shortcut system already runs arbitrary commands on a hotkey.
set -euo pipefail
FLAG="$HOME/.config/screen-context-assistant/paused"
mkdir -p "$(dirname "$FLAG")"

if [ -e "$FLAG" ]; then
  rm -f "$FLAG"
  notify-send "Screen Context Assistant" "Capture resumed" 2>/dev/null || echo "capture resumed"
else
  touch "$FLAG"
  notify-send "Screen Context Assistant" "Capture paused" 2>/dev/null || echo "capture paused"
fi
