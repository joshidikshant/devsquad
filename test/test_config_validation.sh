#!/usr/bin/env bash
# Tests for update-config.sh config validation:
# plugin/skills/devsquad-config/scripts/update-config.sh
# Each case copies the onboarding config-defaults.json into a fresh
# CLAUDE_PROJECT_DIR/.devsquad/config.json, runs one key=value update, and
# asserts the exit code plus the resulting jq value/type. bash-3 compatible,
# no network, all jq-local. Model-name validation is skipped so the offline
# tier/model cases never reach out to agy.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PLUGIN_ROOT="${REPO_ROOT}/plugin"
UPDATE="${PLUGIN_ROOT}/skills/devsquad-config/scripts/update-config.sh"
DEFAULTS="${PLUGIN_ROOT}/skills/onboarding/templates/config-defaults.json"

PASS=0
FAIL=0

pass() { PASS=$((PASS + 1)); }
fail() { FAIL=$((FAIL + 1)); echo "  FAIL: $1"; }

# Fresh project dir with a pristine copy of the default config.
fresh_project() {
  local d
  d=$(mktemp -d)
  mkdir -p "$d/.devsquad"
  cp "$DEFAULTS" "$d/.devsquad/config.json"
  echo "$d"
}

# Run one update against a fresh config. Sets globals:
#   RC  = exit code, OUT = combined stdout/stderr, CFG = config path
run_update() {
  local kv="$1"
  local proj
  proj=$(fresh_project)
  CFG="$proj/.devsquad/config.json"
  OUT=$(CLAUDE_PROJECT_DIR="$proj" DEVSQUAD_SKIP_MODEL_VALIDATION=1 \
    bash "$UPDATE" "$kv" 2>&1)
  RC=$?
}

# Assert the update exits non-zero (rejected).
assert_reject() {
  local label="$1" kv="$2"
  run_update "$kv"
  if [ "$RC" -ne 0 ]; then pass; else fail "$label — expected non-zero exit, got 0 (out: $OUT)"; fi
}

# Assert the update exits 0 and a jq expression over the resulting config
# equals an expected value.
assert_accept_jq() {
  local label="$1" kv="$2" jqexpr="$3" want="$4"
  run_update "$kv"
  if [ "$RC" -ne 0 ]; then
    fail "$label — expected exit 0, got $RC (out: $OUT)"
    return
  fi
  local got
  got=$(jq -r "$jqexpr" "$CFG" 2>/dev/null)
  if [ "$got" = "$want" ]; then pass; else fail "$label — jq '$jqexpr' got '[$got]', want '[$want]'"; fi
}

# --- enforcement_mode ---------------------------------------------------
assert_reject    "enforcement_mode=banana rejected" "enforcement_mode=banana"
assert_accept_jq "enforcement_mode=strict accepted" "enforcement_mode=strict" ".enforcement_mode" "strict"

# --- preferences.gemini_word_limit (number-typed key) -------------------
assert_reject    "gemini_word_limit=abc rejected" "preferences.gemini_word_limit=abc"
assert_accept_jq "gemini_word_limit=200 accepted" "preferences.gemini_word_limit=200" ".preferences.gemini_word_limit" "200"
assert_accept_jq "gemini_word_limit stays a number" "preferences.gemini_word_limit=200" "(.preferences.gemini_word_limit|type)" "number"

# --- holdout_mode (boolean-typed key) -----------------------------------
assert_accept_jq "holdout_mode=true accepted" "holdout_mode=true" ".holdout_mode" "true"
assert_accept_jq "holdout_mode stays a boolean" "holdout_mode=true" "(.holdout_mode|type)" "boolean"

# --- default_routes.* (enum: gemini|codex|grok|self) --------------------
assert_accept_jq "default_routes.reading=grok accepted" "default_routes.reading=grok" ".default_routes.reading" "grok"
assert_reject    "default_routes.reading=banana rejected" "default_routes.reading=banana"

# --- unknown key --------------------------------------------------------
run_update "bogus_key=1"
if [ "$RC" -ne 0 ]; then
  case "$OUT" in
    *"Unknown config key"*) pass ;;
    *) fail "bogus_key=1 rejected but message missing 'Unknown config key' (out: $OUT)" ;;
  esac
else
  fail "bogus_key=1 — expected non-zero exit, got 0 (out: $OUT)"
fi

# --- agent_models tier pins (created key) --------------------------------
assert_accept_jq "agent_models tier:frontier accepted" "agent_models.gemini-reader=tier:frontier" '.agent_models["gemini-reader"]' "tier:frontier"
assert_reject    "agent_models tier:banana rejected"   "agent_models.gemini-reader=tier:banana"

# --- jq-less path: script must exit non-zero when jq is unavailable ------
# Build a PATH shim with only core utils (no jq) symlinked, mirroring the
# routing suite's Group 5. update-config.sh checks for jq up front and errors.
SHIM=$(mktemp -d)
for cmd in bash sh cat grep sed tr head cut date mkdir mv wc dirname find sort xargs awk uname rm ls env cp; do
  p=$(command -v "$cmd" 2>/dev/null) && ln -s "$p" "$SHIM/$cmd" 2>/dev/null
done
JQLESS_PROJ=$(fresh_project)
if PATH="$SHIM" CLAUDE_PROJECT_DIR="$JQLESS_PROJ" DEVSQUAD_SKIP_MODEL_VALIDATION=1 \
   bash "$UPDATE" "enforcement_mode=strict" >/dev/null 2>&1; then
  fail "jq-less path — expected non-zero exit when jq missing"
else
  pass
fi

echo "  config_validation: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ]
