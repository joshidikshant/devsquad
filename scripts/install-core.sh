#!/usr/bin/env bash
# Install DevSquad's dependency-free core into an immutable local release.
# Bash 3.2 compatible. The default path performs no network access.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd -P)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd -P)"
SOURCE_CORE="${DEVSQUAD_SOURCE_CORE:-${REPO_ROOT}/plugin/core}"
INSTALL_ROOT="${DEVSQUAD_INSTALL_ROOT:-${HOME:?HOME is required}/.devsquad}"
BIN_DIR="${DEVSQUAD_BIN_DIR:-${HOME:?HOME is required}/.local/bin}"
PYTHON_REQUEST="${DEVSQUAD_PYTHON:-python3}"
MCP_WHEELHOUSE="${DEVSQUAD_MCP_WHEELHOUSE:-}"
WITH_MCP=0
STATUS_ONLY=0
JSON_OUTPUT=0

usage() {
  cat <<'EOF'
Usage: scripts/install-core.sh [options]

Install the local DevSquad core without requiring Claude or network access.

Options:
  --source-core PATH     Core source directory (default: plugin/core)
  --install-root PATH    Release root (default: ~/.devsquad)
  --bin-dir PATH         Stable launcher directory (default: ~/.local/bin)
  --python PATH          Python 3.11+ used for the isolated runtime
  --with-mcp             Install the optional MCP environment offline
  --mcp-wheelhouse PATH  Directory containing every locked MCP wheel
  --status               Report source/plugin/installed drift; do not install
  --json                 Emit one JSON object
  -h, --help             Show this help

The MCP option never downloads packages. It requires --mcp-wheelhouse (or
DEVSQUAD_MCP_WHEELHOUSE) and installs requirements-mcp.lock with --no-index.
EOF
}

fail() {
  printf 'devsquad install: %s\n' "$*" >&2
  exit 1
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --source-core)
      [ "$#" -ge 2 ] || fail "--source-core requires a path"
      SOURCE_CORE="$2"; shift 2 ;;
    --install-root)
      [ "$#" -ge 2 ] || fail "--install-root requires a path"
      INSTALL_ROOT="$2"; shift 2 ;;
    --bin-dir)
      [ "$#" -ge 2 ] || fail "--bin-dir requires a path"
      BIN_DIR="$2"; shift 2 ;;
    --python)
      [ "$#" -ge 2 ] || fail "--python requires a path"
      PYTHON_REQUEST="$2"; shift 2 ;;
    --with-mcp) WITH_MCP=1; shift ;;
    --mcp-wheelhouse)
      [ "$#" -ge 2 ] || fail "--mcp-wheelhouse requires a path"
      MCP_WHEELHOUSE="$2"; shift 2 ;;
    --status) STATUS_ONLY=1; shift ;;
    --json) JSON_OUTPUT=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown option: $1" ;;
  esac
done

case "$INSTALL_ROOT" in /*) ;; *) fail "install root must be absolute" ;; esac
case "$BIN_DIR" in /*) ;; *) fail "bin directory must be absolute" ;; esac
[ -d "$SOURCE_CORE/src/devsquad" ] || fail "core source is incomplete: $SOURCE_CORE"
[ -f "$SOURCE_CORE/pyproject.toml" ] || fail "core source has no pyproject.toml: $SOURCE_CORE"

if [ -x "$PYTHON_REQUEST" ]; then
  PYTHON="$PYTHON_REQUEST"
else
  PYTHON="$(command -v "$PYTHON_REQUEST" 2>/dev/null || true)"
fi
[ -n "$PYTHON" ] && [ -x "$PYTHON" ] || fail "Python executable not found: $PYTHON_REQUEST"

PYTHON_INFO="$("$PYTHON" - <<'PY'
import json
import platform
import re
import sys

if sys.version_info < (3, 11):
    raise SystemExit("DevSquad requires Python 3.11+")
cache_tag = sys.implementation.cache_tag or "python"
safe_tag = re.sub(r"[^A-Za-z0-9._-]+", "-", cache_tag)
print(json.dumps({
    "executable": sys.executable,
    "version": platform.python_version(),
    "version_id": "%d%d%d" % sys.version_info[:3],
    "cache_tag": safe_tag,
}, sort_keys=True))
PY
)" || fail "Python 3.11+ is required"

core_info() {
  "$PYTHON" - "$1" <<'PY'
import hashlib
import json
from pathlib import Path
import re
import sys
import tomllib

root = Path(sys.argv[1]).resolve(strict=True)
excluded_names = {".DS_Store", ".pytest_cache", "build", "dist", "__pycache__"}
members = []
for path in root.rglob("*"):
    relative = path.relative_to(root)
    if any(part in excluded_names or part.endswith(".egg-info") for part in relative.parts):
        continue
    if path.is_symlink():
        raise SystemExit(f"core source may not contain symlinks: {relative}")
    if path.is_file() and path.suffix != ".pyc":
        members.append((relative, path))
digest = hashlib.sha256()
for relative, path in sorted(members, key=lambda item: item[0].as_posix()):
    digest.update(relative.as_posix().encode("utf-8") + b"\0")
    digest.update(path.read_bytes())
configuration = tomllib.loads((root / "pyproject.toml").read_text())
version = configuration["project"]["version"]
init_text = (root / "src/devsquad/__init__.py").read_text()
match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', init_text, re.M)
if not match or match.group(1) != version:
    raise SystemExit("pyproject and package versions differ")
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", version):
    raise SystemExit("core version is not release-path safe")
print(json.dumps({
    "digest": digest.hexdigest(),
    "path": str(root),
    "version": version,
}, sort_keys=True))
PY
}

SOURCE_INFO="$(core_info "$SOURCE_CORE")" || fail "cannot fingerprint core source"
PLUGIN_CORE="${REPO_ROOT}/plugin/core"
if [ -d "$PLUGIN_CORE/src/devsquad" ]; then
  PLUGIN_INFO="$(core_info "$PLUGIN_CORE")" || fail "cannot fingerprint plugin core"
else
  PLUGIN_INFO='null'
fi

emit_report() {
  "$PYTHON" - "$1" "$SOURCE_INFO" "$PLUGIN_INFO" "$INSTALL_ROOT" "$BIN_DIR" "$PYTHON_INFO" "$2" "$3" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

mode, source_raw, plugin_raw, install_raw, bin_raw, python_raw, changed_raw, error = sys.argv[1:]
source = json.loads(source_raw)
plugin = json.loads(plugin_raw)
python = json.loads(python_raw)
install_root = Path(install_raw)
current = install_root / "current"
launcher = Path(bin_raw) / "squad"
installed = None
installed_payload_digest = None
current_target = None
manifest_error = None

def payload_digest(root):
    excluded_names = {".DS_Store", ".pytest_cache", "build", "dist", "__pycache__"}
    members = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in excluded_names or part.endswith(".egg-info") for part in relative.parts):
            continue
        if path.is_symlink():
            raise OSError(f"installed core contains a symlink: {relative}")
        if path.is_file() and path.suffix != ".pyc":
            members.append((relative, path))
    digest = hashlib.sha256()
    for relative, path in sorted(members, key=lambda item: item[0].as_posix()):
        digest.update(relative.as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()

try:
    if current.is_symlink():
        current_target = str(current.resolve(strict=True))
        installed = json.loads((current / "release.json").read_text())
        installed_payload_digest = payload_digest(current / "core")
        if installed.get("source_digest") != installed_payload_digest:
            manifest_error = "installed release payload differs from its manifest"
    elif current.exists():
        manifest_error = "current selector is not a symlink"
except (OSError, UnicodeError, json.JSONDecodeError) as exc:
    manifest_error = f"cannot read installed release: {type(exc).__name__}"
source_digest = source["digest"]
plugin_digest = plugin["digest"] if plugin else None
installed_digest = installed_payload_digest
report = {
    "schema_version": 1,
    "mode": mode,
    "changed": changed_raw == "1",
    "source": source,
    "plugin": plugin,
    "installed": installed,
    "installed_payload_digest": installed_payload_digest,
    "installed_manifest_matches": bool(
        installed is not None
        and installed.get("source_digest") == installed_payload_digest
    ),
    "current_target": current_target,
    "launcher": str(launcher),
    "launcher_ready": launcher.is_file() and launcher.stat().st_mode & 0o111 != 0,
    "python": python,
    "drift": {
        "source_plugin": plugin_digest is not None and source_digest != plugin_digest,
        "source_installed": installed_digest is None or source_digest != installed_digest,
        "plugin_installed": plugin_digest is None or installed_digest is None or plugin_digest != installed_digest,
    },
    "error": error or manifest_error,
}
print(json.dumps(report, sort_keys=True, separators=(",", ":")))
PY
}

if [ "$STATUS_ONLY" -eq 1 ]; then
  REPORT="$(emit_report status 0 '')"
  if [ "$JSON_OUTPUT" -eq 1 ]; then
    printf '%s\n' "$REPORT"
  else
    "$PYTHON" - "$REPORT" <<'PY'
import json, sys
r = json.loads(sys.argv[1])
state = "not installed" if r["installed"] is None else r["installed"]["release_id"]
print(f"DevSquad standalone: {state}")
print(f"  launcher: {r['launcher']} ({'ready' if r['launcher_ready'] else 'missing'})")
print("  drift: source/plugin={source_plugin} source/installed={source_installed} plugin/installed={plugin_installed}".format(**r["drift"]))
if r["error"]:
    print(f"  error: {r['error']}")
PY
  fi
  exit 0
fi

if [ "$WITH_MCP" -eq 1 ]; then
  [ -n "$MCP_WHEELHOUSE" ] || fail "--with-mcp requires --mcp-wheelhouse"
  [ -d "$MCP_WHEELHOUSE" ] || fail "MCP wheelhouse is not a directory: $MCP_WHEELHOUSE"
fi

mkdir -p "$INSTALL_ROOT/releases" "$BIN_DIR"
INSTALL_ROOT="$(cd "$INSTALL_ROOT" && pwd -P)"
BIN_DIR="$(cd "$BIN_DIR" && pwd -P)"
SOURCE_CORE="$(cd "$SOURCE_CORE" && pwd -P)"

LOCK_DIR="$INSTALL_ROOT/.install.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  fail "another install is active or left $LOCK_DIR behind"
fi
TEMP_DIR=""
cleanup() {
  if [ -n "$TEMP_DIR" ] && [ -d "$TEMP_DIR" ]; then
    rm -rf "$TEMP_DIR"
  fi
  rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup EXIT HUP INT TERM

SOURCE_DIGEST="$($PYTHON -c 'import json,sys; print(json.loads(sys.argv[1])["digest"])' "$SOURCE_INFO")"
SOURCE_VERSION="$($PYTHON -c 'import json,sys; print(json.loads(sys.argv[1])["version"])' "$SOURCE_INFO")"
PYTHON_VERSION_ID="$($PYTHON -c 'import json,sys; print(json.loads(sys.argv[1])["version_id"])' "$PYTHON_INFO")"
PYTHON_CACHE_TAG="$($PYTHON -c 'import json,sys; print(json.loads(sys.argv[1])["cache_tag"])' "$PYTHON_INFO")"
FLAVOR="core"
MCP_ENV_DIGEST=""
if [ "$WITH_MCP" -eq 1 ]; then
  MCP_ENV_DIGEST="$($PYTHON - "$SOURCE_CORE/requirements-mcp.lock" "$MCP_WHEELHOUSE" <<'PY'
import hashlib
from pathlib import Path
import sys

digest = hashlib.sha256()
for root in (Path(sys.argv[1]), Path(sys.argv[2])):
    paths = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
    for path in paths:
        digest.update(path.name.encode("utf-8") + b"\0" + path.read_bytes())
print(digest.hexdigest())
PY
)"
  FLAVOR="mcp-${MCP_ENV_DIGEST%${MCP_ENV_DIGEST#????????????}}"
fi
SHORT_DIGEST="${SOURCE_DIGEST%${SOURCE_DIGEST#????????????}}"
RELEASE_ID="${SOURCE_VERSION}-py${PYTHON_VERSION_ID}-${SHORT_DIGEST}-${FLAVOR}"
RELEASE_DIR="$INSTALL_ROOT/releases/$RELEASE_ID"
RELEASE_CREATED=0

if [ -e "$RELEASE_DIR" ]; then
  [ -d "$RELEASE_DIR" ] && [ -f "$RELEASE_DIR/release.json" ] || fail "release path is incomplete: $RELEASE_DIR"
  RELEASE_CORE_INFO="$(core_info "$RELEASE_DIR/core")" || fail "existing release payload is unreadable"
  RELEASE_CORE_DIGEST="$($PYTHON -c 'import json,sys; print(json.loads(sys.argv[1])["digest"])' "$RELEASE_CORE_INFO")"
  [ "$RELEASE_CORE_DIGEST" = "$SOURCE_DIGEST" ] || fail "existing release payload digest differs: $RELEASE_DIR"
  "$PYTHON" - "$RELEASE_DIR/release.json" "$RELEASE_ID" "$SOURCE_DIGEST" "$PYTHON_CACHE_TAG" "$WITH_MCP" "$MCP_ENV_DIGEST" <<'PY'
import json
from pathlib import Path
import sys

path, release_id, digest, cache_tag, with_mcp, mcp_digest = sys.argv[1:]
value = json.loads(Path(path).read_text())
expected = {
    "release_id": release_id,
    "source_digest": digest,
    "python_cache_tag": cache_tag,
    "mcp": with_mcp == "1",
    "mcp_environment_digest": mcp_digest or None,
}
for key, item in expected.items():
    if value.get(key) != item:
        raise SystemExit(f"existing release manifest mismatch: {key}")
venv_python = Path(path).parent / "venv/bin/python"
if not venv_python.is_file():
    raise SystemExit("existing release has no runtime Python")
PY
else
  TEMP_DIR="$(mktemp -d "$INSTALL_ROOT/.install.XXXXXX")"
  BUILD_RELEASE="$TEMP_DIR/release"
  mkdir -p "$BUILD_RELEASE"
  "$PYTHON" -m venv "$BUILD_RELEASE/venv"
  VENV_PYTHON="$BUILD_RELEASE/venv/bin/python"
  [ -x "$VENV_PYTHON" ] || fail "virtual environment did not provide bin/python"
  PURELIB="$($VENV_PYTHON -c 'import sysconfig; print(sysconfig.get_path("purelib"))')"
  "$VENV_PYTHON" - "$SOURCE_CORE" "$BUILD_RELEASE/core" "$PURELIB" "$SOURCE_VERSION" <<'PY'
from pathlib import Path
import os
import shutil
import sys

source = Path(sys.argv[1])
installed_core = Path(sys.argv[2])
purelib = Path(sys.argv[3])
version = sys.argv[4]
ignore = shutil.ignore_patterns(
    "__pycache__", "*.pyc", ".DS_Store", ".pytest_cache", "build", "dist", "*.egg-info",
)
shutil.copytree(source, installed_core, ignore=ignore)
relative_source = os.path.relpath(installed_core / "src", purelib)
(purelib / "devsquad-core.pth").write_text(relative_source + "\n")
dist = purelib / f"devsquad_core-{version}.dist-info"
dist.mkdir()
(dist / "METADATA").write_text(
    "Metadata-Version: 2.1\nName: devsquad-core\nVersion: " + version + "\n"
)
(dist / "WHEEL").write_text(
    "Wheel-Version: 1.0\nGenerator: devsquad-install-core\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
)
(dist / "entry_points.txt").write_text("[console_scripts]\nsquad = devsquad.cli:main\n")
(dist / "RECORD").write_text("")
PY
  if [ "$WITH_MCP" -eq 1 ]; then
    "$VENV_PYTHON" -m pip install --quiet --disable-pip-version-check --no-index \
      --only-binary=:all: --find-links "$MCP_WHEELHOUSE" \
      -r "$SOURCE_CORE/requirements-mcp.lock"
    "$VENV_PYTHON" -m pip check >/dev/null
    "$VENV_PYTHON" - <<'PY'
from importlib import metadata
import mcp
assert metadata.version("mcp") == "2.2.0"
PY
  fi
  "$VENV_PYTHON" -P "$BUILD_RELEASE/core/bin/squad" --version >/dev/null
  "$VENV_PYTHON" -P - <<'PY'
from devsquad.integrations import load_integrations
assert {item.id for item in load_integrations()} == {"codex", "claude-code", "antigravity", "grok"}
PY
  "$PYTHON" - "$BUILD_RELEASE/release.json" "$RELEASE_ID" "$SOURCE_VERSION" "$SOURCE_DIGEST" "$PYTHON_INFO" "$WITH_MCP" "$MCP_ENV_DIGEST" <<'PY'
import json
from pathlib import Path
import sys

path, release_id, version, digest, python_raw, with_mcp, mcp_digest = sys.argv[1:]
python = json.loads(python_raw)
value = {
    "schema_version": 1,
    "release_id": release_id,
    "version": version,
    "source_digest": digest,
    "python_version": python["version"],
    "python_cache_tag": python["cache_tag"],
    "mcp": with_mcp == "1",
    "mcp_environment_digest": mcp_digest or None,
}
Path(path).write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
PY
  mv "$BUILD_RELEASE" "$RELEASE_DIR"
  RELEASE_CREATED=1
fi

CURRENT_CHANGED=0
CURRENT_TARGET="releases/$RELEASE_ID"
if [ -L "$INSTALL_ROOT/current" ] && [ "$(readlink "$INSTALL_ROOT/current")" = "$CURRENT_TARGET" ]; then
  :
elif [ -e "$INSTALL_ROOT/current" ] || [ -L "$INSTALL_ROOT/current" ]; then
  [ -L "$INSTALL_ROOT/current" ] || fail "current selector is not a symlink: $INSTALL_ROOT/current"
  ln -s "$CURRENT_TARGET" "$INSTALL_ROOT/.current.$$"
  "$PYTHON" - "$INSTALL_ROOT/.current.$$" "$INSTALL_ROOT/current" <<'PY'
import os, sys
os.replace(sys.argv[1], sys.argv[2])
PY
  CURRENT_CHANGED=1
else
  ln -s "$CURRENT_TARGET" "$INSTALL_ROOT/.current.$$"
  "$PYTHON" - "$INSTALL_ROOT/.current.$$" "$INSTALL_ROOT/current" <<'PY'
import os, sys
os.replace(sys.argv[1], sys.argv[2])
PY
  CURRENT_CHANGED=1
fi

LAUNCHER="$BIN_DIR/squad"
if { [ -e "$LAUNCHER" ] || [ -L "$LAUNCHER" ]; } \
    && ! grep -q '^# managed-by: devsquad-install-core-v1$' "$LAUNCHER" 2>/dev/null; then
  if [ -L "$LAUNCHER" ]; then
    "$PYTHON" - "$LAUNCHER" "$INSTALL_ROOT/releases" <<'PY' || fail "refusing to replace unmanaged launcher: $LAUNCHER"
from pathlib import Path
import sys

launcher = Path(sys.argv[1])
releases = Path(sys.argv[2]).resolve(strict=True)
target = launcher.resolve(strict=True)
try:
    relative = target.relative_to(releases)
except ValueError as exc:
    raise SystemExit("legacy launcher target is outside the release root") from exc
if len(relative.parts) != 4 or relative.parts[-2:] != ("bin", "squad") or relative.parts[-3] != "venv":
    raise SystemExit("legacy launcher target is not a DevSquad release command")
PY
  else
    fail "refusing to replace unmanaged launcher: $LAUNCHER"
  fi
fi
LAUNCHER_TEMP="$BIN_DIR/.squad.$$"
"$PYTHON" - "$LAUNCHER_TEMP" "$INSTALL_ROOT" <<'PY'
from pathlib import Path
import shlex
import sys

path = Path(sys.argv[1])
python = Path(sys.argv[2]) / "current/venv/bin/python"
entry = Path(sys.argv[2]) / "current/core/bin/squad"
path.write_text(
    "#!/bin/sh\n"
    "# managed-by: devsquad-install-core-v1\n"
    "exec " + shlex.quote(str(python)) + " -P " + shlex.quote(str(entry)) + " \"$@\"\n"
)
path.chmod(0o755)
PY
LAUNCHER_CHANGED=0
if [ -f "$LAUNCHER" ] && cmp -s "$LAUNCHER_TEMP" "$LAUNCHER"; then
  rm -f "$LAUNCHER_TEMP"
else
  mv -f "$LAUNCHER_TEMP" "$LAUNCHER"
  LAUNCHER_CHANGED=1
fi

CHANGED=0
if [ "$RELEASE_CREATED" -eq 1 ] || [ "$CURRENT_CHANGED" -eq 1 ] || [ "$LAUNCHER_CHANGED" -eq 1 ]; then
  CHANGED=1
fi
REPORT="$(emit_report install "$CHANGED" '')"
STATE_TEMP="$INSTALL_ROOT/.install-state.$$"
"$PYTHON" - "$STATE_TEMP" "$REPORT" <<'PY'
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
report = json.loads(sys.argv[2])
state = {
    "schema_version": report["schema_version"],
    "source": report["source"],
    "plugin": report["plugin"],
    "installed": report["installed"],
    "installed_payload_digest": report["installed_payload_digest"],
    "installed_manifest_matches": report["installed_manifest_matches"],
    "current_target": report["current_target"],
    "launcher": report["launcher"],
    "python": report["python"],
    "drift": report["drift"],
}
path.write_text(json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n")
PY
if [ -f "$INSTALL_ROOT/install-state.json" ] && cmp -s "$STATE_TEMP" "$INSTALL_ROOT/install-state.json"; then
  rm -f "$STATE_TEMP"
else
  mv -f "$STATE_TEMP" "$INSTALL_ROOT/install-state.json"
fi

if [ "$JSON_OUTPUT" -eq 1 ]; then
  printf '%s\n' "$REPORT"
else
  "$PYTHON" - "$REPORT" <<'PY'
import json, sys
r = json.loads(sys.argv[1])
print(f"DevSquad {r['installed']['version']} installed")
print(f"  release: {r['current_target']}")
print(f"  launcher: {r['launcher']}")
print(f"  changed: {str(r['changed']).lower()}")
print("  drift: source/plugin={source_plugin} source/installed={source_installed} plugin/installed={plugin_installed}".format(**r["drift"]))
PY
fi
