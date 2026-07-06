#!/usr/bin/env bash
# D4 conformance: every CLI wrapper honors one contract.
#   invoke_<name>(prompt, limit, timeout) -> response on stdout, exit 0
#   failure -> exit 1 with stderr prefix RATE_LIMITED|AUTH_ERROR|TIMEOUT|CLI_ERROR
#   telemetry: usage/<agent>.json record per call; contracts.log entry on success
#   classification: auth is checked BEFORE rate ('migrate' must never
#   classify as RATE_LIMITED — the bug that masked the Gemini decommission)
# Runs offline against fake CLI binaries.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)/plugin"

PASS=0
FAIL=0

ok()   { PASS=$((PASS + 1)); }
bad()  { FAIL=$((FAIL + 1)); echo "  FAIL: $1"; }

FAKE=$(mktemp -d)
for bin in agy codex grok; do
  cat > "$FAKE/$bin" <<'FAKESH'
#!/bin/bash
case "${FAKE_MODE:-success}" in
  success) echo "FAKE-RESPONSE-OK" ;;
  rate)    echo "HTTP 429 Too Many Requests" >&2; exit 1 ;;
  auth)    echo "401 unauthorized request" >&2; exit 1 ;;
  migrate) echo "please migrate to the new suite: IneligibleTierError while authenticating" >&2; exit 1 ;;
  banner)  echo "Signing in with Grok..." ;;
esac
FAKESH
  chmod +x "$FAKE/$bin"
done

# run_case wrapper_file fn agent_name mode
# Sets OUT, ERR_TXT, EC. Fresh project dir per call; HOME is faked so the
# grok wrapper's ~/.grok/bin PATH bootstrap cannot resurrect the real CLI.
run_case() {
  local wrapper="$1" fn="$2" mode="$3"
  TDIR=$(mktemp -d)
  local errf="$TDIR/err.txt"
  OUT=$(FAKE_MODE="$mode" HOME="$TDIR" CLAUDE_PROJECT_DIR="$TDIR" PATH="$FAKE:$PATH" \
    bash -c "source '$PLUGIN_ROOT/lib/$wrapper'; $fn 'Ping test. Under 30 words.' 30 10" 2>"$errf")
  EC=$?
  ERR_TXT=$(cat "$errf" 2>/dev/null)
}

for spec in "gemini-wrapper.sh:invoke_gemini:gemini" "codex-wrapper.sh:invoke_codex:codex" "grok-wrapper.sh:invoke_grok:grok"; do
  wrapper="${spec%%:*}"; rest="${spec#*:}"; fn="${rest%%:*}"; agent="${rest#*:}"

  # 1. success: stdout + exit 0 + usage record + contract log
  run_case "$wrapper" "$fn" success
  [ "$EC" -eq 0 ] && ok || bad "$agent success exit ($EC)"
  printf '%s' "$OUT" | grep -q "FAKE-RESPONSE-OK" && ok || bad "$agent success stdout"
  [ -f "$TDIR/.devsquad/usage/$agent.json" ] && ok || bad "$agent success usage record"
  grep -q "| $agent | word_limit" "$TDIR/.devsquad/logs/contracts.log" 2>/dev/null \
    && ok || bad "$agent success contract log"

  # 2. auth failure: exit 1 + AUTH_ERROR + no cooldown
  run_case "$wrapper" "$fn" auth
  [ "$EC" -ne 0 ] && ok || bad "$agent auth exit"
  printf '%s' "$ERR_TXT" | grep -q "AUTH_ERROR" && ok || bad "$agent auth prefix"

  # 3. rate limit: exit 1 + RATE_LIMITED + cooldown armed
  run_case "$wrapper" "$fn" rate
  printf '%s' "$ERR_TXT" | grep -q "RATE_LIMITED" && ok || bad "$agent rate prefix"
  [ -f "$TDIR/.devsquad/cooldown_$agent" ] && ok || bad "$agent rate cooldown file"

  # 4. classification order: 'migrate...authenticating' => AUTH, never RATE
  run_case "$wrapper" "$fn" migrate
  printf '%s' "$ERR_TXT" | grep -q "AUTH_ERROR" && ok || bad "$agent migrate=>auth"
  printf '%s' "$ERR_TXT" | grep -q "RATE_LIMITED" && bad "$agent migrate misclassified as rate" || ok
  [ ! -f "$TDIR/.devsquad/cooldown_$agent" ] && ok || bad "$agent migrate armed bogus cooldown"

  # 5. failures also recorded in telemetry
  [ -f "$TDIR/.devsquad/usage/$agent.json" ] && ok || bad "$agent failure usage record"
done

# grok-specific: unauthenticated CLI exits 0 with a sign-in banner — the
# wrapper must classify that as AUTH_ERROR, not success
run_case grok-wrapper.sh invoke_grok banner
[ "$EC" -ne 0 ] && ok || bad "grok banner treated as success (exit 0)"
printf '%s' "$ERR_TXT" | grep -q "AUTH_ERROR" && ok || bad "grok banner auth prefix"

echo "  contract: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ]
