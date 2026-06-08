#!/usr/bin/env bash
# install.sh — install the LIFX Beam controller and wire up Claude Code hooks.
# Safe to re-run: it replaces any previous lifx_beam.py hooks without touching
# your other settings. Run on each Mac after cloning this repo.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="$HOME/.claude"
HOOK_DIR="$CLAUDE_DIR/lifx_hooks"
SETTINGS="$CLAUDE_DIR/settings.json"

echo "Installing LIFX Beam controller to $HOOK_DIR"
mkdir -p "$HOOK_DIR"
cp "$REPO_DIR/lifx_beam.py" "$HOOK_DIR/lifx_beam.py"
chmod +x "$HOOK_DIR/lifx_beam.py"

if [ ! -f "$SETTINGS" ]; then
  echo '{}' > "$SETTINGS"
fi

echo "Merging hooks into $SETTINGS (a backup is written to settings.json.bak)"
cp "$SETTINGS" "$SETTINGS.bak"

python3 - "$SETTINGS" "$REPO_DIR/hooks.json" <<'PY'
import json, sys

settings_path, hooks_path = sys.argv[1], sys.argv[2]

with open(settings_path) as f:
    settings = json.load(f)
with open(hooks_path) as f:
    new_hooks = json.load(f)["hooks"]

hooks = settings.setdefault("hooks", {})

def is_lifx(entry):
    for h in entry.get("hooks", []):
        if "lifx_beam.py" in h.get("command", ""):
            return True
    return False

for event, entries in new_hooks.items():
    existing = hooks.get(event, [])
    # drop any prior lifx entries so re-running stays idempotent
    existing = [e for e in existing if not is_lifx(e)]
    hooks[event] = existing + entries

with open(settings_path, "w") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")

print("  hooks installed for:", ", ".join(new_hooks))
PY

echo
echo "Scanning the network for your LIFX light(s)..."
python3 "$HOOK_DIR/lifx_beam.py" scan || true
echo
echo "Done. Now restart Claude Code (or open /hooks once) to load the new hooks."
echo "If a light's IP later changes, re-run: python3 ~/.claude/lifx_hooks/lifx_beam.py scan"
