#!/usr/bin/env bash
# Tests for scripts/holdout-reconcile.sh -- the D1 verdict engine.
# Covers: per-message.id dedup (max), insufficient-data gate (<20 sessions),
# epoch exclude: lines dropping sessions, and the saves/COSTS sign.
# No network, no real external CLIs. python3 is the script's own hard dep.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RECONCILE="${REPO_ROOT}/scripts/holdout-reconcile.sh"

PASS=0
FAIL=0

assert_contains() {
  local label="$1" haystack="$2" needle="$3"
  case "$haystack" in
    *"$needle"*) PASS=$((PASS + 1)) ;;
    *)
      FAIL=$((FAIL + 1))
      echo "  FAIL: $label — expected to find '[${needle}]' in output:"
      echo "$haystack" | sed 's/^/      | /'
      ;;
  esac
}

assert_not_contains() {
  local label="$1" haystack="$2" needle="$3"
  case "$haystack" in
    *"$needle"*)
      FAIL=$((FAIL + 1))
      echo "  FAIL: $label — did NOT expect '[${needle}]' in output:"
      echo "$haystack" | sed 's/^/      | /'
      ;;
    *) PASS=$((PASS + 1)) ;;
  esac
}

# Python replicates the script's own path encoding: re.sub('[^A-Za-z0-9]','-', abspath)
encode_path() {
  python3 - "$1" <<'PYEOF'
import re, os, sys
print(re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(sys.argv[1])))
PYEOF
}

# Emit one assistant JSONL line with the given message id and output_tokens.
emit_line() { # $1=msg_id $2=output_tokens
  printf '{"type":"assistant","message":{"id":"%s","usage":{"output_tokens":%s}}}\n' "$1" "$2"
}

# Build a self-contained project + fake HOME with transcript fixtures.
# Args: project_dir, fake_home. Caller writes holdout.log and transcripts after.
setup_project() { # $1=projdir $2=home
  mkdir -p "$1/.devsquad/logs"
  local enc; enc="$(encode_path "$1")"
  mkdir -p "$2/.claude/projects/${enc}"
}

# Path to a session's transcript inside the fake HOME.
transcript_path() { # $1=projdir $2=home $3=sid
  local enc; enc="$(encode_path "$1")"
  echo "$2/.claude/projects/${enc}/$3.jsonl"
}

run_reconcile() { # $1=projdir $2=home
  HOME="$2" bash "$RECONCILE" "$1" 2>&1
}

# ---------------------------------------------------------------------------
# Group 1: message.id dedup — two lines, same id, output_tokens=100 => 100 once
# ---------------------------------------------------------------------------
P1=$(mktemp -d); H1=$(mktemp -d)
setup_project "$P1" "$H1"
# 2 control + 2 treatment sessions
{
  echo "ts | c1 | control | Task | agent | zones | mode"
  echo "ts | c2 | control | Task | agent | zones | mode"
  echo "ts | t1 | treatment | Task | agent | zones | mode"
  echo "ts | t2 | treatment | Task | agent | zones | mode"
} > "$P1/.devsquad/logs/holdout.log"

# c1: SAME message.id repeated across two lines, output_tokens=100 each.
# Correct dedup => 100 total, NOT 200.
{
  emit_line "msg_dup" 100
  emit_line "msg_dup" 100
} > "$(transcript_path "$P1" "$H1" c1)"
# c2: single message, 300 tokens -> control values {100, 300}, mean 200
emit_line "msg_c2" 300 > "$(transcript_path "$P1" "$H1" c2)"
# treatment sessions
emit_line "msg_t1" 100 > "$(transcript_path "$P1" "$H1" t1)"
emit_line "msg_t2" 100 > "$(transcript_path "$P1" "$H1" t2)"

OUT1="$(run_reconcile "$P1" "$H1")"
# control total should be 100 (c1 deduped) + 300 (c2) = 400, not 500.
assert_contains "dedup: control total=400 (not 500)" "$OUT1" "control:   n=2  total=400"
assert_not_contains "dedup: c1 not double-counted (500)" "$OUT1" "total=500"

# ---------------------------------------------------------------------------
# Group 2: <20 sessions prints insufficient data (N/20)
# ---------------------------------------------------------------------------
# Reuse Group 1 project: 4 sessions with transcripts.
assert_contains "insufficient data gate" "$OUT1" "insufficient data (4/20 sessions)"

# ---------------------------------------------------------------------------
# Group 3: epoch 'exclude:<sid>' line drops that session
# ---------------------------------------------------------------------------
P3=$(mktemp -d); H3=$(mktemp -d)
setup_project "$P3" "$H3"
{
  echo "ts | c1 | control | Task | agent | zones | mode"
  echo "ts | c2 | control | Task | agent | zones | mode"
  echo "ts | t1 | treatment | Task | agent | zones | mode"
  echo "ts | t2 | treatment | Task | agent | zones | mode"
} > "$P3/.devsquad/logs/holdout.log"
emit_line "m_c1" 100 > "$(transcript_path "$P3" "$H3" c1)"
emit_line "m_c2" 100 > "$(transcript_path "$P3" "$H3" c2)"
emit_line "m_t1" 100 > "$(transcript_path "$P3" "$H3" t1)"
emit_line "m_t2" 100 > "$(transcript_path "$P3" "$H3" t2)"

# Baseline: no exclude -> 4 sessions, control n=2
OUT3_BASE="$(run_reconcile "$P3" "$H3")"
assert_contains "epoch baseline: 4 sessions" "$OUT3_BASE" "sessions with transcripts: 4"
assert_contains "epoch baseline: control n=2" "$OUT3_BASE" "control:   n=2"

# Now exclude c2 via the epoch marker.
printf 'exclude:c2\n' > "$P3/.devsquad/holdout-epoch"
OUT3_EXCL="$(run_reconcile "$P3" "$H3")"
assert_contains "epoch exclude: down to 3 sessions" "$OUT3_EXCL" "sessions with transcripts: 3"
assert_contains "epoch exclude: control n=1" "$OUT3_EXCL" "control:   n=1"

# ---------------------------------------------------------------------------
# Group 4: saves/COSTS sign correctness
# ---------------------------------------------------------------------------
# 4a: treatment mean < control mean => "saves"
P4=$(mktemp -d); H4=$(mktemp -d)
setup_project "$P4" "$H4"
{
  echo "ts | c1 | control | Task | agent | zones | mode"
  echo "ts | c2 | control | Task | agent | zones | mode"
  echo "ts | t1 | treatment | Task | agent | zones | mode"
  echo "ts | t2 | treatment | Task | agent | zones | mode"
} > "$P4/.devsquad/logs/holdout.log"
# control mean 1000, treatment mean 500 -> saves +50.0%
emit_line "s_c1" 1000 > "$(transcript_path "$P4" "$H4" c1)"
emit_line "s_c2" 1000 > "$(transcript_path "$P4" "$H4" c2)"
emit_line "s_t1" 500 > "$(transcript_path "$P4" "$H4" t1)"
emit_line "s_t2" 500 > "$(transcript_path "$P4" "$H4" t2)"
OUT4A="$(run_reconcile "$P4" "$H4")"
assert_contains "sign: treatment<control -> saves" "$OUT4A" "+50.0% (saves tokens)"
assert_not_contains "sign: saves case not COSTS" "$OUT4A" "COSTS"

# 4b: treatment mean > control mean => "COSTS"
P4B=$(mktemp -d); H4B=$(mktemp -d)
setup_project "$P4B" "$H4B"
{
  echo "ts | c1 | control | Task | agent | zones | mode"
  echo "ts | c2 | control | Task | agent | zones | mode"
  echo "ts | t1 | treatment | Task | agent | zones | mode"
  echo "ts | t2 | treatment | Task | agent | zones | mode"
} > "$P4B/.devsquad/logs/holdout.log"
# control mean 500, treatment mean 1000 -> COSTS -100.0%
emit_line "x_c1" 500 > "$(transcript_path "$P4B" "$H4B" c1)"
emit_line "x_c2" 500 > "$(transcript_path "$P4B" "$H4B" c2)"
emit_line "x_t1" 1000 > "$(transcript_path "$P4B" "$H4B" t1)"
emit_line "x_t2" 1000 > "$(transcript_path "$P4B" "$H4B" t2)"
OUT4B="$(run_reconcile "$P4B" "$H4B")"
assert_contains "sign: treatment>control -> COSTS" "$OUT4B" "COSTS tokens)"
assert_contains "sign: COSTS is negative pct" "$OUT4B" "-100.0%"

# ---------------------------------------------------------------------------
# Group 5: jq-less path — run under a PATH shim with only core utils.
# The script's transcript parsing is pure python3 (no jq), so it must still
# produce a verdict when jq is absent from PATH.
# ---------------------------------------------------------------------------
SHIM=$(mktemp -d)
for util in python3 env bash sh cat mktemp printf sed dirname basename cut expr; do
  real="$(command -v "$util" 2>/dev/null || true)"
  if [ -n "$real" ]; then
    ln -sf "$real" "$SHIM/$util"
  fi
done
# Deliberately do NOT symlink jq into the shim.
if command -v jq >/dev/null 2>&1 && [ -e "$SHIM/jq" ]; then
  FAIL=$((FAIL + 1)); echo "  FAIL: shim unexpectedly contains jq"
fi
OUT5="$(PATH="$SHIM" HOME="$H1" bash "$RECONCILE" "$P1" 2>&1)"
assert_contains "jq-less: still computes control total" "$OUT5" "control:   n=2  total=400"
assert_contains "jq-less: still emits verdict" "$OUT5" "insufficient data (4/20 sessions)"

echo "  holdout_reconcile: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ]
