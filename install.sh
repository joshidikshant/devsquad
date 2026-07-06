#!/usr/bin/env bash
# DevSquad Installer — registers marketplace, installs plugin, and wires hooks
set -euo pipefail

REPO_URL="https://github.com/joshidikshant/devsquad.git"
MARKETPLACE="devsquad-marketplace"
PLUGIN="devsquad@${MARKETPLACE}"
SETTINGS="$HOME/.claude/settings.json"
PLUGIN_INSTALL_DIR="$HOME/.claude/plugins/marketplaces/${MARKETPLACE}"

echo "=== DevSquad Installer ==="
echo

# Check claude is available
if ! command -v claude &>/dev/null; then
  echo "Error: Claude Code CLI not found. Install it first:"
  echo "  https://docs.anthropic.com/en/docs/claude-code"
  exit 1
fi

# Step 1: Register marketplace
echo "[1/4] Registering marketplace..."
if claude plugin marketplace list 2>/dev/null | grep -q "$MARKETPLACE"; then
  echo "  Marketplace already registered, updating..."
  claude plugin marketplace update "$MARKETPLACE"
else
  claude plugin marketplace add "$REPO_URL"
fi

# Step 2: Install plugin
echo "[2/4] Installing plugin..."
if claude plugin list 2>/dev/null | grep -q "devsquad@"; then
  echo "  Plugin already installed, updating..."
  claude plugin update "$PLUGIN" 2>/dev/null || true
else
  claude plugin install "$PLUGIN"
fi

# Step 3: Enable plugin
echo "[3/4] Enabling plugin..."
claude plugin enable "$PLUGIN" 2>/dev/null || true

# Step 4: Register hooks into ~/.claude/settings.json (global)
# Hooks point at the MARKETPLACE CLONE (a git checkout that `claude plugin
# marketplace update` refreshes) — never at a versioned cache dir, which
# freezes hooks at install-time and silently drops every later fix.
# Developers hacking on DevSquad itself can point these commands at their
# source checkout instead to run hooks-at-HEAD (see docs/ARCHITECTURE.md).
# Note: per-project hook registration happens during /devsquad:setup (onboarding skill Step 3.5).
echo "[4/4] Registering hooks into global settings.json..."

if [[ ! -f "$SETTINGS" ]]; then
  echo "  Creating $SETTINGS..."
  echo '{"hooks":{}}' > "$SETTINGS"
fi

if ! command -v python3 &>/dev/null; then
  echo "  Warning: python3 not found. Skipping hook registration."
  echo "  Hooks must be added to $SETTINGS manually."
else
  python3 - <<PYEOF
import json, os, sys

settings_path = os.path.expanduser("$SETTINGS")
plugin_root = os.path.expanduser("$PLUGIN_INSTALL_DIR/plugin")

try:
    with open(settings_path, "r") as f:
        settings = json.load(f)
except (json.JSONDecodeError, FileNotFoundError):
    settings = {}

hooks = settings.setdefault("hooks", {})

def hook_command(script):
    return f"CLAUDE_PLUGIN_ROOT={plugin_root} bash {plugin_root}/hooks/scripts/{script}"

def already_registered(entries, script_name):
    """Check if hook script is already in any entry's hooks list."""
    for entry in entries:
        for h in entry.get("hooks", []):
            if script_name in h.get("command", ""):
                return True
    return False

new_hooks = [
    ("SessionStart", "", "session-start.sh", 15),
    ("PreToolUse",   "Read|WebSearch|Bash|Task", "pre-tool-use.sh", 15),
    ("PreCompact",   "", "pre-compact.sh", 15),
    ("Stop",         "", "stop.sh", 15),
]

added = []
for event, matcher, script, timeout in new_hooks:
    entries = hooks.setdefault(event, [])
    if already_registered(entries, script):
        continue
    entry = {"hooks": [{"type": "command", "command": hook_command(script), "timeout": timeout}]}
    if matcher:
        entry["matcher"] = matcher
    entries.append(entry)
    added.append(script)

with open(settings_path, "w") as f:
    json.dump(settings, f, indent=2)

if added:
    print(f"  Registered: {', '.join(added)}")
else:
    print("  All hooks already registered (no changes needed).")

PYEOF
fi

echo
echo "Done! Restart Claude Code, then run /devsquad:setup in each project."
echo "  /devsquad:setup registers project-scoped hooks into .claude/settings.json"
