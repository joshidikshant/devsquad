#!/usr/bin/env python3
"""Offline Claude stream fixture: repair only the installed-usability seed."""

import json
import os
from pathlib import Path
import sys


if "--version" in sys.argv:
    print("2.1.220 (Claude Code)")
    raise SystemExit(0)
if sys.argv[1:3] == ["auth", "status"]:
    print(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty"}))
    raise SystemExit(0)
if os.environ.get("DEVSQUAD_WORKER") != "1" or "--print" not in sys.argv or "--" not in sys.argv:
    raise SystemExit("fixture accepts only a fenced offline writer invocation")

path = Path("src/app.py")
seed = "def add(a, b):\n    return a - b\n"
if path.is_symlink() or not path.is_file() or path.read_text() != seed:
    raise SystemExit("fixture writer requires the exact seeded defect")
path.write_text("def add(a, b):\n    return a + b\n")
model, session = "claude-sonnet-4-6", "offline-installed-writer-session"
records = [{
    "type": "assistant", "session_id": session, "parent_tool_use_id": None,
    "message": {"role": "assistant", "model": model},
}, {
    "type": "result", "subtype": "success", "is_error": False,
    "session_id": session, "result": "Corrected the bounded addition defect.",
    "usage": {"input_tokens": 12, "output_tokens": 7},
    "modelUsage": {model: {"inputTokens": 12, "outputTokens": 7, "provider": "firstParty"}},
}]
for record in records:
    print(json.dumps(record), flush=True)
