#!/bin/bash
# Installs the QuickBooks read-only MCP server for the Claude desktop app (macOS).
# Copies the server to a local folder (so it never runs from a synced or cloud folder) and
# adds it to Claude's config, keeping a backup of the old config.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)/qbo_mcp.py"
DEST_DIR="$HOME/Library/Application Support/qbo-readonly"
CONFIG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
PY="/usr/bin/python3"

[ -f "$SRC" ] || { echo "qbo_mcp.py not found next to install.sh"; exit 1; }
"$PY" -c 'import sys; assert sys.version_info >= (3, 8)' || { echo "Needs python3 3.8 or later (xcode-select --install)"; exit 1; }

mkdir -p "$DEST_DIR"
cp "$SRC" "$DEST_DIR/qbo_mcp.py"
echo "Copied server to: $DEST_DIR/qbo_mcp.py"

mkdir -p "$(dirname "$CONFIG")"
[ -f "$CONFIG" ] && cp "$CONFIG" "$CONFIG.bak-$(date +%Y%m%d-%H%M%S)" && echo "Backed up Claude config."

"$PY" - "$CONFIG" "$PY" "$DEST_DIR/qbo_mcp.py" <<'EOF'
import json, os, sys
path, py, server = sys.argv[1:4]
cfg = {}
if os.path.exists(path) and os.path.getsize(path):
    with open(path) as f:
        cfg = json.load(f)
cfg.setdefault("mcpServers", {})["qbo-readonly"] = {"command": py, "args": [server]}
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
print("Added 'qbo-readonly' to", path)
EOF

echo
echo "Next: if you haven't yet, run:  $PY \"$DEST_DIR/qbo_mcp.py\" setup"
echo "Then quit Claude completely (Cmd-Q) and reopen it."
