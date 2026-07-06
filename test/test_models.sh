#!/usr/bin/env bash
# Tests for per-agent model resolution (_resolve_gemini_model /
# _resolve_codex_model) and update-config model-key handling.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PLUGIN_ROOT="${REPO_ROOT}/plugin"

PASS=0
FAIL=0

assert_eq() {
  local label="$1" got="$2" want="$3"
  if [ "$got" = "$want" ]; then
    PASS=$((PASS + 1))
  else
    FAIL=$((FAIL + 1))
    echo "  FAIL: $label — got '[${got}]', expected '[${want}]'"
  fi
}

resolve() { # $1=wrapper $2=fn $3=agent-or-empty
  local export_line=""
  if [ -n "$3" ]; then export_line="export DEVSQUAD_AGENT='$3';"; fi
  bash -c "${export_line} source '$PLUGIN_ROOT/lib/$1'; $2" 2>/dev/null
}

# --- Group 1: resolution precedence across all three wrappers ---
T=$(mktemp -d); mkdir -p "$T/.devsquad"
printf '%s' '{"preferences":{"gemini_model":"G-GLOBAL","codex_model":"C-GLOBAL","grok_model":"K-GLOBAL"},"agent_models":{"gemini-reader":"G-READER","codex-tester":"C-TESTER","grok-researcher":"K-RES"}}' > "$T/.devsquad/config.json"
export CLAUDE_PROJECT_DIR="$T"

assert_eq "agent override wins"        "$(resolve gemini-wrapper.sh _resolve_gemini_model gemini-reader)"     "G-READER"
assert_eq "no agent entry -> global"   "$(resolve gemini-wrapper.sh _resolve_gemini_model gemini-researcher)" "G-GLOBAL"
assert_eq "no DEVSQUAD_AGENT -> global" "$(resolve gemini-wrapper.sh _resolve_gemini_model '')"               "G-GLOBAL"
assert_eq "codex agent override"       "$(resolve codex-wrapper.sh _resolve_codex_model codex-tester)"        "C-TESTER"
assert_eq "codex fallback to global"   "$(resolve codex-wrapper.sh _resolve_codex_model codex-developer)"     "C-GLOBAL"
assert_eq "grok agent override"        "$(resolve grok-wrapper.sh _resolve_grok_model grok-researcher)"       "K-RES"
assert_eq "grok fallback to global"    "$(resolve grok-wrapper.sh _resolve_grok_model grok-developer)"        "K-GLOBAL"

# --- Group 2: no config -> empty (CLI default) ---
T2=$(mktemp -d)
export CLAUDE_PROJECT_DIR="$T2"
assert_eq "no config -> empty" "$(resolve gemini-wrapper.sh _resolve_gemini_model gemini-reader)" ""

# --- Group 3: update-config handles hyphenated agent_models keys ---
T3=$(mktemp -d); mkdir -p "$T3/.devsquad"
cp "$PLUGIN_ROOT/skills/onboarding/templates/config-defaults.json" "$T3/.devsquad/config.json"
export CLAUDE_PROJECT_DIR="$T3"
export DEVSQUAD_SKIP_MODEL_VALIDATION=1
bash "$PLUGIN_ROOT/skills/devsquad-config/scripts/update-config.sh" 'agent_models.gemini-reader=Some Model (X)' >/dev/null 2>&1
got=$(jq -r '.agent_models["gemini-reader"] // "MISSING"' "$T3/.devsquad/config.json" 2>/dev/null)
assert_eq "hyphenated key stored" "$got" "Some Model (X)"
# empty model name rejected even with validation skipped
if bash "$PLUGIN_ROOT/skills/devsquad-config/scripts/update-config.sh" 'agent_models.gemini-reader=' >/dev/null 2>&1; then
  FAIL=$((FAIL + 1)); echo "  FAIL: empty model name should be rejected"
else
  PASS=$((PASS + 1))
fi
unset DEVSQUAD_SKIP_MODEL_VALIDATION

# --- Group 4: tier resolution against a fixture catalog ---
CATDIR=$(mktemp -d)
export DEVSQUAD_CATALOG_DIR="$CATDIR"
cat > "$CATDIR/models.json" <<'CATEOF'
{"fetched_at":"2026-07-06T00:00:00Z",
 "gemini":{"status":"ok","models":["Gemini 3.5 Flash (Medium)","Gemini 3.5 Flash (High)","Gemini 4.0 Flash (Medium)","Gemini 3.1 Pro (High)","Claude Opus 4.6 (Thinking)","GPT-OSS 120B (Medium)"]},
 "grok":{"status":"ok","models":["grok-composer-2.5-fast","grok-build"]},
 "codex":{"status":"unlistable","models":[]}}
CATEOF

CAT="$PLUGIN_ROOT/lib/model-catalog.sh"
assert_eq "tier fast picks newest flash"      "$(bash "$CAT" resolve gemini fast)"     "Gemini 4.0 Flash (Medium)"
assert_eq "tier frontier picks highest ver"   "$(bash "$CAT" resolve gemini frontier)" "Claude Opus 4.6 (Thinking)"
assert_eq "grok fast"                         "$(bash "$CAT" resolve grok fast)"       "grok-composer-2.5-fast"
assert_eq "grok frontier non-fast fallback"   "$(bash "$CAT" resolve grok frontier)"   "grok-build"

# Adapter integration: tier pin in agent_models resolves through the catalog
T4=$(mktemp -d); mkdir -p "$T4/.devsquad"
printf '%s' '{"agent_models":{"gemini-reader":"tier:fast"}}' > "$T4/.devsquad/config.json"
export CLAUDE_PROJECT_DIR="$T4"
assert_eq "adapter resolves tier pin" "$(resolve gemini-wrapper.sh _resolve_gemini_model gemini-reader)" "Gemini 4.0 Flash (Medium)"

# Missing catalog degrades to empty (CLI default), never fails
export DEVSQUAD_CATALOG_DIR=$(mktemp -d)
assert_eq "missing catalog -> CLI default" "$(resolve gemini-wrapper.sh _resolve_gemini_model gemini-reader)" ""
export DEVSQUAD_CATALOG_DIR="$CATDIR"

# update-config: tier values accepted, bogus tier rejected
T5=$(mktemp -d); mkdir -p "$T5/.devsquad"
cp "$PLUGIN_ROOT/skills/onboarding/templates/config-defaults.json" "$T5/.devsquad/config.json"
export CLAUDE_PROJECT_DIR="$T5"
if bash "$PLUGIN_ROOT/skills/devsquad-config/scripts/update-config.sh" 'agent_models.gemini-reader=tier:frontier' >/dev/null 2>&1; then
  PASS=$((PASS + 1))
else
  FAIL=$((FAIL + 1)); echo "  FAIL: tier:frontier should be accepted"
fi
if bash "$PLUGIN_ROOT/skills/devsquad-config/scripts/update-config.sh" 'agent_models.gemini-reader=tier:banana' >/dev/null 2>&1; then
  FAIL=$((FAIL + 1)); echo "  FAIL: tier:banana should be rejected"
else
  PASS=$((PASS + 1))
fi

echo "  models: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ]
