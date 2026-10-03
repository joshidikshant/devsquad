#!/usr/bin/env bash
# M7 documentation, generator and installer compatibility checks.
# Bash 3.2 compatible; no network or provider CLIs.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
PASS=0
FAIL=0

pass() { PASS=$((PASS + 1)); }
fail() { FAIL=$((FAIL + 1)); echo "  FAIL: $1"; }

if /bin/bash -n "$REPO_ROOT/install.sh" "$REPO_ROOT/scripts/install-core.sh"; then
  pass
else
  fail "install scripts must parse under the system Bash"
fi

if /bin/bash "$REPO_ROOT/install.sh" --help 2>/dev/null | grep -q -- '--core-only'; then
  pass
else
  fail "composite installer help must document standalone mode"
fi

if /bin/bash "$REPO_ROOT/scripts/install-core.sh" --help 2>/dev/null | grep -q -- '--mcp-wheelhouse'; then
  pass
else
  fail "core installer help must document offline MCP installation"
fi

if python3 "$REPO_ROOT/scripts/generate-core-reference.py" --check >/dev/null 2>&1; then
  pass
else
  fail "generated command/schema reference must be current"
fi

if grep -q 'scripts/install-core.sh --status --json' "$REPO_ROOT/docs/RUNTIME-GUIDE.md"; then
  pass
else
  fail "runtime guide must document drift inspection"
fi

if grep -q 'native Claude-to-Codex transcript import' "$REPO_ROOT/docs/RUNTIME-GUIDE.md"; then
  pass
else
  fail "runtime guide must state the native transcript-import boundary"
fi

if python3 - "$REPO_ROOT/plugin/core/schemas" <<'PY' >/dev/null 2>&1
import json
from pathlib import Path
import sys
paths = sorted(Path(sys.argv[1]).glob("*.schema.json"))
assert paths
for path in paths:
    value = json.loads(path.read_text())
    assert value["$id"].startswith("https://devsquad.local/schemas/")
PY
then
  pass
else
  fail "all packaged schemas must parse and retain stable identifiers"
fi

if python3 - "$REPO_ROOT/plugin" <<'PY' >/dev/null 2>&1
import json
from pathlib import Path
import sys
root = Path(sys.argv[1])
expected = {
    "SessionStart": ("", "session-start.sh"),
    "PreCompact": ("", "pre-compact.sh"),
    "PreToolUse": ("Read|WebSearch|Bash|Task", "pre-tool-use.sh"),
    "Stop": ("", "stop.sh"),
}

def validate(value):
    assert set(value) == {"hooks"}
    events = value["hooks"]
    assert isinstance(events, dict) and set(events) == set(expected)
    for event, (matcher, script) in expected.items():
        assert events[event] == [{
            "matcher": matcher,
            "hooks": [{"type": "command", "command":
                       "bash ${CLAUDE_PLUGIN_ROOT}/hooks/scripts/" + script,
                       "timeout": 15}],
        }]
        assert (root / "hooks" / "scripts" / script).is_file()

value = json.loads((root / "hooks" / "hooks.json").read_text())
validate(value)
# The pre-0.11.1 event-only file must fail, not silently lose all hooks.
try:
    validate(value["hooks"])
except AssertionError:
    pass
else:
    raise AssertionError("legacy event-only hook file was accepted")
PY
then
  pass
else
  fail "plugin hooks need the loader's hooks envelope and exactly four stable registrations"
fi

if python3 - "$REPO_ROOT" <<'PY' >/dev/null 2>&1
import json
from pathlib import Path
import sys
root = Path(sys.argv[1])
plugin = json.loads((root / "plugin/.claude-plugin/plugin.json").read_text())
marketplace = json.loads((root / ".claude-plugin/marketplace.json").read_text())
entry = next(item for item in marketplace["plugins"] if item["name"] == plugin["name"])
assert plugin["version"] == entry["version"] == marketplace["metadata"]["version"]
PY
then
  pass
else
  fail "plugin and both marketplace versions must match"
fi

echo "  m7_packaging: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ]
