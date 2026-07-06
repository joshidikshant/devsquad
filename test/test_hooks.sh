#!/usr/bin/env bash
# Fixture tests for hooks/scripts/pre-tool-use.sh:
# input parsing, read threshold, test-command detection, WebSearch routing,
# Task-based acceptance resolution (F4 fix), context-zone escalation (F2 fix).
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)/plugin"
HOOK="$PLUGIN_ROOT/hooks/scripts/pre-tool-use.sh"

PASS=0
FAIL=0

fresh_env() {
  TEST_DIR=$(mktemp -d)
  FAKE_HOME=$(mktemp -d)   # no stats-cache.json -> daily zone green
  export CLAUDE_PROJECT_DIR="$TEST_DIR"
  export HOME="$FAKE_HOME"
  unset DEVSQUAD_HOOK_DEPTH 2>/dev/null || true
}

run_hook() {
  printf '%s' "$1" | bash "$HOOK" 2>/dev/null
  return 0
}

assert_contains() {
  local label="$1" haystack="$2" needle="$3"
  if printf '%s' "$haystack" | grep -qF "$needle"; then
    PASS=$((PASS + 1))
  else
    FAIL=$((FAIL + 1))
    echo "  FAIL: $label — output did not contain '$needle'"
  fi
}

assert_empty() {
  local label="$1" value="$2"
  if [ -z "$value" ]; then
    PASS=$((PASS + 1))
  else
    FAIL=$((FAIL + 1))
    echo "  FAIL: $label — expected no output, got: $(printf '%s' "$value" | head -c 120)"
  fi
}

init_state() {
  bash -c "source '$PLUGIN_ROOT/lib/state.sh'; d=\$(init_state_dir); init_session_state \"\$d\"" >/dev/null
}

# --- Group 1: benign Bash is silent; test commands are intercepted ---
fresh_env
OUT=$(run_hook '{"tool_name":"Bash","tool_input":{"command":"ls -la"}}')
assert_empty "bash ls silent" "$OUT"
OUT=$(run_hook '{"tool_name":"Bash","tool_input":{"command":"npm test"}}')
assert_contains "npm test intercepted" "$OUT" "codex-tester"
assert_contains "npm test advisory" "$OUT" '"permissionDecision": "allow"'

# --- Group 2: read threshold (green zone = 20) ---
fresh_env
READ_FIXTURE='{"tool_name":"Read","tool_input":{"file_path":"/etc/hosts"}}'
OUT=""
for i in $(seq 1 20); do OUT=$(run_hook "$READ_FIXTURE"); done
assert_empty "read 20 silent" "$OUT"
OUT=$(run_hook "$READ_FIXTURE")
assert_contains "read 21 suggests" "$OUT" "gemini-reader"
assert_contains "read 21 labeled estimate" "$OUT" "heuristic, not reconciled"

# --- Group 3: WebSearch always suggests ---
fresh_env
OUT=$(run_hook '{"tool_name":"WebSearch","tool_input":{"query":"best rate limiter"}}')
assert_contains "websearch suggests" "$OUT" "gemini-researcher"

# --- Group 4: Task-based acceptance resolution (F4) ---
fresh_env
init_state
run_hook '{"tool_name":"WebSearch","tool_input":{"query":"q1"}}' >/dev/null
run_hook '{"tool_name":"Task","tool_input":{"subagent_type":"devsquad:gemini-researcher"}}' >/dev/null
LOG="$TEST_DIR/.devsquad/logs/compliance.log"
assert_contains "matching Task = accepted" "$(cat "$LOG" 2>/dev/null)" "advisory_accepted"

fresh_env
init_state
run_hook '{"tool_name":"WebSearch","tool_input":{"query":"q2"}}' >/dev/null
run_hook '{"tool_name":"Task","tool_input":{"subagent_type":"code-reviewer"}}' >/dev/null
LOG="$TEST_DIR/.devsquad/logs/compliance.log"
assert_contains "non-matching Task = unresolved" "$(cat "$LOG" 2>/dev/null)" "advisory_unresolved"

fresh_env
init_state
run_hook '{"tool_name":"WebSearch","tool_input":{"query":"q3"}}' >/dev/null
run_hook '{"tool_name":"WebSearch","tool_input":{"query":"q4"}}' >/dev/null
LOG="$TEST_DIR/.devsquad/logs/compliance.log"
assert_contains "same tool = declined" "$(cat "$LOG" 2>/dev/null)" "advisory_declined"

fresh_env
init_state
run_hook '{"tool_name":"WebSearch","tool_input":{"query":"q5"}}' >/dev/null
OUT=$(run_hook '{"tool_name":"Task","tool_input":{"subagent_type":"anything"}}')
assert_empty "Task itself never gets delegation output" "$OUT"

# --- Group 5: context zone escalates threshold (F2) ---
fresh_env
FAKE_TRANSCRIPT="$TEST_DIR/transcript.jsonl"
printf '%s\n' '{"type":"assistant","message":{"usage":{"input_tokens":150000,"cache_read_input_tokens":20000,"output_tokens":50}}}' > "$FAKE_TRANSCRIPT"
CTX_FIXTURE="{\"tool_name\":\"Read\",\"tool_input\":{\"file_path\":\"/etc/hosts\"},\"transcript_path\":\"$FAKE_TRANSCRIPT\"}"
OUT=""
for i in $(seq 1 8); do OUT=$(run_hook "$CTX_FIXTURE"); done
assert_empty "ctx read 8 silent (threshold 8 not exceeded)" "$OUT"
OUT=$(run_hook "$CTX_FIXTURE")
assert_contains "ctx read 9 suggests under red context" "$OUT" "CONTEXT red"

# Small context -> normal threshold of 3 still applies
fresh_env
FAKE_TRANSCRIPT="$TEST_DIR/transcript.jsonl"
printf '%s\n' '{"type":"assistant","message":{"usage":{"input_tokens":9000,"cache_read_input_tokens":1000,"output_tokens":50}}}' > "$FAKE_TRANSCRIPT"
CTX_FIXTURE="{\"tool_name\":\"Read\",\"tool_input\":{\"file_path\":\"/etc/hosts\"},\"transcript_path\":\"$FAKE_TRANSCRIPT\"}"
OUT=$(run_hook "$CTX_FIXTURE"); assert_empty "small ctx read 1 silent" "$OUT"
OUT=$(run_hook "$CTX_FIXTURE"); assert_empty "small ctx read 2 silent" "$OUT"

# --- Group 6: advisory back-off cap (3 shown suggestions per session) ---
fresh_env
init_state
OUT=""
for i in 1 2 3; do
  OUT=$(run_hook "{\"tool_name\":\"WebSearch\",\"tool_input\":{\"query\":\"q$i\"}}")
done
assert_contains "3rd suggestion still shown" "$OUT" "gemini-researcher"
OUT=$(run_hook '{"tool_name":"WebSearch","tool_input":{"query":"q4"}}')
assert_empty "4th suggestion suppressed by cap" "$OUT"
assert_contains "cap event logged" "$(cat "$TEST_DIR/.devsquad/logs/compliance.log" 2>/dev/null)" "advisory_capped"

# --- Group 7: per-session read counters (session_id isolation) ---
fresh_env
RA='{"tool_name":"Read","tool_input":{"file_path":"/etc/hosts"},"session_id":"sess-aaaa"}'
RB='{"tool_name":"Read","tool_input":{"file_path":"/etc/hosts"},"session_id":"sess-bbbb"}'
OUT=""
for i in $(seq 1 20); do OUT=$(run_hook "$RA"); done
OUT=$(run_hook "$RB")
assert_empty "session B unaffected by session A count" "$OUT"
OUT=$(run_hook "$RA")
assert_contains "session A crosses its own threshold" "$OUT" "gemini-reader"

# --- Group 8b: holdout protocol (D1) — arm assignment by session_id parity ---
# cksum parities: "aaab" -> even (control), "aaaa" -> odd (treatment)
fresh_env
mkdir -p "$TEST_DIR/.devsquad"
printf '%s' '{"enforcement_mode":"advisory","holdout_mode":true}' > "$TEST_DIR/.devsquad/config.json"
OUT=$(run_hook '{"tool_name":"WebSearch","tool_input":{"query":"hq1"},"session_id":"aaab"}')
assert_empty "holdout control arm suppressed" "$OUT"
OUT=$(run_hook '{"tool_name":"WebSearch","tool_input":{"query":"hq2"},"session_id":"aaaa"}')
assert_contains "holdout treatment arm normal" "$OUT" "gemini-researcher"
HLOG="$TEST_DIR/.devsquad/logs/holdout.log"
assert_contains "control logged" "$(cat "$HLOG" 2>/dev/null)" "| aaab | control |"
assert_contains "treatment logged" "$(cat "$HLOG" 2>/dev/null)" "| aaaa | treatment |"

# holdout_mode absent -> behavior unchanged (L4)
fresh_env
OUT=$(run_hook '{"tool_name":"WebSearch","tool_input":{"query":"hq3"},"session_id":"aaab"}')
assert_contains "no holdout config -> suggestion shown" "$OUT" "gemini-researcher"

# --- Group 8: state dir self-gitignore ---
fresh_env
init_state
assert_contains "state dir self-ignores" "$(cat "$TEST_DIR/.devsquad/.gitignore" 2>/dev/null)" "*"

echo "  hooks: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ]
