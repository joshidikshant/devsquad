#!/usr/bin/env bash
# Tests for:
#  (A) lib/model-catalog.sh catalog_is_stale() — missing / fresh / aged catalog
#  (B) lib/enforcement.sh estimate_token_savings / estimate_session_savings
# Offline, filesystem only. bash-3 compatible (no assoc arrays, no ${var,,}).
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PLUGIN_ROOT="${REPO_ROOT}/plugin"
CATALOG="${PLUGIN_ROOT}/lib/model-catalog.sh"
ENFORCE="${PLUGIN_ROOT}/lib/enforcement.sh"

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

# assert_stale <label> <catalog_dir> <expected_rc>
# Sources model-catalog.sh with DEVSQUAD_CATALOG_DIR set, calls catalog_is_stale,
# and reports its exit code (0 = stale/missing, 1 = fresh). Runs in a subshell so
# the target's `set -e` cannot abort our harness.
assert_stale() {
  local label="$1" catdir="$2" want="$3" rc
  rc=$(DEVSQUAD_CATALOG_DIR="$catdir" bash -c \
    'source "'"$CATALOG"'"; if catalog_is_stale; then echo 0; else echo 1; fi' 2>/dev/null)
  assert_eq "$label" "$rc" "$want"
}

# --- Group A: catalog_is_stale ---

# (1) no models.json -> stale (rc 0)
A1=$(mktemp -d)
assert_stale "A1 missing catalog -> stale" "$A1" "0"

# (2) fresh models.json -> not stale (rc 1)
A2=$(mktemp -d)
: > "$A2/models.json"          # create, mtime = now
assert_stale "A2 fresh catalog -> not stale" "$A2" "1"

# (3) mtime aged ~2 days back -> stale (rc 0)
A3=$(mktemp -d)
: > "$A3/models.json"
# 2 days ago, portable across BSD/GNU touch: touch -t takes [[CC]YY]MMDDhhmm.
# Compute an explicit past timestamp so the >24h threshold is crossed.
if date -v-2d '+%Y%m%d%H%M' >/dev/null 2>&1; then
  TS=$(date -v-2d '+%Y%m%d%H%M')          # BSD/macOS date
else
  TS=$(date -d '2 days ago' '+%Y%m%d%H%M') # GNU date
fi
touch -t "$TS" "$A3/models.json"
assert_stale "A3 aged catalog -> stale" "$A3" "0"

# --- Group B: estimate_token_savings / estimate_session_savings ---

# Helper: call a savings fn in a subshell with a temp CLAUDE_PROJECT_DIR.
# The target sources under `set -euo pipefail`; wrapping the call in a subshell
# keeps our harness alive regardless.
call_enforce() { # $1=CLAUDE_PROJECT_DIR  $2...=command to eval after sourcing
  local pdir="$1"; shift
  CLAUDE_PROJECT_DIR="$pdir" bash -c 'source "'"$ENFORCE"'"; '"$*" 2>/dev/null
}

BDIR=$(mktemp -d)

# ~4000 bytes -> 4000/4 = 1000 tokens -> "~1K tokens"
F4000="$BDIR/f4000"
head -c 4000 /dev/zero > "$F4000"
assert_eq "B1 ~4000 bytes -> ~1K tokens" \
  "$(call_enforce "$BDIR" 'estimate_token_savings "'"$F4000"'"')" "~1K tokens"

# ~8000 bytes -> 2000 tokens -> "~2K tokens"
F8000="$BDIR/f8000"
head -c 8000 /dev/zero > "$F8000"
assert_eq "B2 ~8000 bytes -> ~2K tokens" \
  "$(call_enforce "$BDIR" 'estimate_token_savings "'"$F8000"'"')" "~2K tokens"

# small file (<4000 bytes, non-zero) -> "~N tokens" (plain, no K).
# 400 bytes -> 100 tokens -> "~100 tokens"
F400="$BDIR/f400"
head -c 400 /dev/zero > "$F400"
assert_eq "B3 ~400 bytes -> ~100 tokens" \
  "$(call_enforce "$BDIR" 'estimate_token_savings "'"$F400"'"')" "~100 tokens"

# 0-byte file -> "~5-20K tokens" (the missing/empty floor)
F0="$BDIR/f0"
: > "$F0"
assert_eq "B4 0-byte file -> floor" \
  "$(call_enforce "$BDIR" 'estimate_token_savings "'"$F0"'"')" "~5-20K tokens"

# missing file -> stat yields 0 -> "~5-20K tokens"
assert_eq "B5 missing file -> floor" \
  "$(call_enforce "$BDIR" 'estimate_token_savings "'"$BDIR"'/does-not-exist"')" "~5-20K tokens"

# estimate_session_savings threshold=20, count=25 -> excess 5 -> 5*8 = "~40K tokens"
assert_eq "B6 session count above threshold" \
  "$(call_enforce "$BDIR" 'estimate_session_savings 20 25')" "~40K tokens"

# excess <= 0 (count == threshold) -> floor "~5-20K tokens"
assert_eq "B7 session count at threshold -> floor" \
  "$(call_enforce "$BDIR" 'estimate_session_savings 20 20')" "~5-20K tokens"

# count below threshold -> excess negative -> floor
assert_eq "B8 session count below threshold -> floor" \
  "$(call_enforce "$BDIR" 'estimate_session_savings 20 5')" "~5-20K tokens"

# count read from .devsquad/read_count when not passed: 30 - 20 = 10 -> "~80K tokens"
PDIR=$(mktemp -d); mkdir -p "$PDIR/.devsquad"
printf '30' > "$PDIR/.devsquad/read_count"
assert_eq "B9 session count from read_count file" \
  "$(call_enforce "$PDIR" 'estimate_session_savings 20')" "~80K tokens"

# --- Group C: jq-less path for estimate_session_savings ---
# estimate_session_savings does no jq work itself, but enforcement.sh is sourced
# whole. Exercise the source + call with a PATH stripped of jq to prove the
# functions under test run with only core utils available (as test_routing.sh
# Group 5 does). Symlink just the coreutils the functions touch.
SHIM=$(mktemp -d)
for u in bash sh cat stat head date dirname mktemp printf grep sed cut; do
  src=$(command -v "$u" 2>/dev/null) || continue
  ln -sf "$src" "$SHIM/$u"
done
# Confirm jq is genuinely absent on the shimmed PATH.
if PATH="$SHIM" command -v jq >/dev/null 2>&1; then
  FAIL=$((FAIL + 1)); echo "  FAIL: C jq should be absent on shim PATH"
else
  PASS=$((PASS + 1))
fi
got=$(PATH="$SHIM" CLAUDE_PROJECT_DIR="$BDIR" \
  "$SHIM/bash" -c 'source "'"$ENFORCE"'"; estimate_session_savings 20 25' 2>/dev/null)
assert_eq "C jq-less session savings" "$got" "~40K tokens"
got=$(PATH="$SHIM" CLAUDE_PROJECT_DIR="$BDIR" \
  "$SHIM/bash" -c 'source "'"$ENFORCE"'"; estimate_token_savings "'"$F4000"'"' 2>/dev/null)
assert_eq "C jq-less token savings" "$got" "~1K tokens"

echo "  catalog_and_savings: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ]
