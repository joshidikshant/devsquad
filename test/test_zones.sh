#!/usr/bin/env bash
# Tests for the pure zone functions in plugin/lib/usage.sh:
#   calculate_zone         -- daily budget zone from output-token volume
#   calculate_context_zone -- context-occupancy zone from a transcript (JSONL)
# The context-zone assertions run twice: once with jq on PATH and once via a
# PATH shim WITHOUT jq, to exercise both the jq path and the grep/awk fallback.
# No network, no real external CLIs, bash-3 compatible.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)/plugin"
USAGE_LIB="${PLUGIN_ROOT}/lib/usage.sh"

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

# usage.sh runs under 'set -euo pipefail' and may source other libs, so call
# each function in a fresh bash. CLAUDE_PROJECT_DIR is set to a temp dir so any
# path defaults (e.g. CAPACITY_CACHE_FILE) resolve somewhere harmless.
SANDBOX=$(mktemp -d)

# call_fn [extra_path] fn args...
#   extra_path: PATH to run under (empty = inherit current PATH)
call_fn() {
  local extra_path="$1"; shift
  local fn="$1"; shift
  local args="" a
  for a in "$@"; do
    args="${args} '$(printf '%s' "$a" | sed "s/'/'\\\\''/g")'"
  done
  if [ -n "$extra_path" ]; then
    PATH="$extra_path" CLAUDE_PROJECT_DIR="$SANDBOX" \
      bash -c "source '$USAGE_LIB'; ${fn}${args}" 2>/dev/null
  else
    CLAUDE_PROJECT_DIR="$SANDBOX" \
      bash -c "source '$USAGE_LIB'; ${fn}${args}" 2>/dev/null
  fi
}

# --- Group 1: calculate_zone boundaries (daily budget, output tokens) ---
# green: <100K, yellow: 100K-<200K, red: >=200K. input_tokens is unused.
assert_eq "zone 99999 -> green"   "$(call_fn '' calculate_zone 0 99999)"  "green"
assert_eq "zone 100000 -> yellow" "$(call_fn '' calculate_zone 0 100000)" "yellow"
assert_eq "zone 199999 -> yellow" "$(call_fn '' calculate_zone 0 199999)" "yellow"
assert_eq "zone 200000 -> red"    "$(call_fn '' calculate_zone 0 200000)" "red"

# --- Fixtures for calculate_context_zone ---
# ctx = input_tokens + cache_read_input_tokens (+ cache_creation, 0 here).
# One JSONL line each; the function reads the tail of the transcript.
FIXDIR=$(mktemp -d)

# 119999 -> green (100000 + 19999)
printf '%s\n' '{"message":{"usage":{"input_tokens":100000,"cache_read_input_tokens":19999}}}' > "$FIXDIR/green.jsonl"
# 120000 -> yellow (100000 + 20000)
printf '%s\n' '{"message":{"usage":{"input_tokens":100000,"cache_read_input_tokens":20000}}}' > "$FIXDIR/yellow.jsonl"
# 160000 -> red (150000 + 10000)
printf '%s\n' '{"message":{"usage":{"input_tokens":150000,"cache_read_input_tokens":10000}}}' > "$FIXDIR/red.jsonl"
# all-zero usage -> unknown (ctx == 0)
printf '%s\n' '{"message":{"usage":{"input_tokens":0,"cache_read_input_tokens":0}}}' > "$FIXDIR/zero.jsonl"
MISSING="$FIXDIR/does-not-exist.jsonl"

# run_context_zone_suite <label> <extra_path>
# Exercises all context-zone cases; called once per jq/no-jq branch.
run_context_zone_suite() {
  local tag="$1" extra_path="$2"
  assert_eq "ctx ${tag} 119999 -> green"  "$(call_fn "$extra_path" calculate_context_zone "$FIXDIR/green.jsonl")"  "green"
  assert_eq "ctx ${tag} 120000 -> yellow" "$(call_fn "$extra_path" calculate_context_zone "$FIXDIR/yellow.jsonl")" "yellow"
  assert_eq "ctx ${tag} 160000 -> red"    "$(call_fn "$extra_path" calculate_context_zone "$FIXDIR/red.jsonl")"    "red"
  assert_eq "ctx ${tag} missing -> unknown" "$(call_fn "$extra_path" calculate_context_zone "$MISSING")" "unknown"
  assert_eq "ctx ${tag} all-zero -> unknown" "$(call_fn "$extra_path" calculate_context_zone "$FIXDIR/zero.jsonl")" "unknown"
}

# --- Group 2: context zone WITH jq on PATH (jq branch) ---
# Guard: this branch is only meaningful if jq actually exists in this env.
if command -v jq >/dev/null 2>&1; then
  run_context_zone_suite "jq" ""
else
  echo "  NOTE: jq not found — skipping jq-branch context-zone assertions"
fi

# --- Group 3: context zone via a PATH shim WITHOUT jq (grep/awk fallback) ---
# Symlink only core utils into the shim dir; deliberately omit jq so the
# fallback branch runs. Mirrors test_routing.sh Group 5.
SHIM=$(mktemp -d)
for cmd in bash sh cat grep sed tr head tail cut awk wc mkdir rm ls env date dirname; do
  p=$(command -v "$cmd" 2>/dev/null) && ln -s "$p" "$SHIM/$cmd" 2>/dev/null
done
# Sanity: confirm the shim really has no jq before relying on the fallback path.
if PATH="$SHIM" command -v jq >/dev/null 2>&1; then
  FAIL=$((FAIL + 1)); echo "  FAIL: shim PATH unexpectedly contains jq"
else
  PASS=$((PASS + 1))
fi
run_context_zone_suite "nojq" "$SHIM"

echo "  zones: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ]
