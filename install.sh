#!/usr/bin/env bash
# install.sh: add Terminal Launcher to the app menu (user-level only, no sudo).
# Usage: bash install.sh            install or update (also replaces the old "Claude Launcher" entry)
#        bash install.sh remove     remove the menu entry and the start-at-login entry (keeps your list)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
APPS="$HOME/.local/share/applications"
DESK="$APPS/net.vyechi.TerminalLauncher.desktop"
OLD_DESK="$APPS/net.vyechi.ClaudeLauncher.desktop"            # from before the rename
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/net.vyechi.TerminalLauncher.desktop"

if [ "${1:-install}" = remove ]; then
  rm -f "$DESK" "$AUTOSTART"
  update-desktop-database "$APPS" 2>/dev/null || true
  echo "Removed the menu and start-at-login entries (your list in ~/.config/terminal-launcher is kept)."
  exit 0
fi
chmod +x "$HERE/terminal-launcher.py"
mkdir -p "$APPS"
cat > "$DESK" << DESKTOP
[Desktop Entry]
Type=Application
Name=Terminal Launcher
Comment=Open your saved terminals and coding agents (Claude Code, omp, opencode)
Exec=python3 "$HERE/terminal-launcher.py"
Icon=$HERE/assets/terminal-launcher.svg
Terminal=false
Categories=Development;Utility;
StartupWMClass=net.vyechi.TerminalLauncher
DESKTOP
if [ -f "$OLD_DESK" ]; then rm -f "$OLD_DESK"; echo "Removed the old Claude Launcher menu entry."; fi
update-desktop-database "$APPS" 2>/dev/null || true
# Rewrite the start-at-login entry so it points at this folder (only if some entry is marked).
python3 - "$HERE/terminal-launcher.py" << 'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("tl", sys.argv[1]); tl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tl)
tl.sync_autostart(tl.load_sessions())
PY
echo "Installed: $DESK"
echo "Find \"Terminal Launcher\" in the app menu (you can pin it to the dock)."
