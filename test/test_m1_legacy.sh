#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); }
bad() { FAIL=$((FAIL + 1)); echo "  FAIL: $1"; }

T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/home" "$T/project/.devsquad"
cat > "$T/bin/codex" <<'EOF'
#!/usr/bin/env bash
case "${FAKE_MODE:-fast}" in
  fast) printf 'ok' ;;
  error) printf 'deliberate offline failure\n' >&2; exit 7 ;;
  tree)
    sh -c 'trap "" TERM; echo $$ > "$DESC_PID_FILE"; while :; do sleep 1; done' &
    wait
    ;;
  root_ignore)
    trap '' TERM
    printf '%s\n' "$$" > "$ROOT_PID_FILE"
    : > "$ROOT_READY_FILE"
    while :; do sleep 1; done
    ;;
esac
EOF
chmod +x "$T/bin/codex"

# Make process inspection deliberately expensive for the deadline regression.
# The old watchdog counted probes rather than elapsed time, so every delayed
# ps call extended the configured timeout. Normal test calls remain unchanged.
cat > "$T/bin/ps" <<'EOF'
#!/usr/bin/env bash
if [[ "${FAKE_PROBE_DELAY:-0}" != "0" ]]; then
  /bin/sleep "$FAKE_PROBE_DELAY"
fi
exec /bin/ps "$@"
EOF
chmod +x "$T/bin/ps"

# A fake provider and its descendant must not retain the private FIFO FD.
# Brief real work also exercises cancellation while retaining the existing
# sub-second fast-call contract.
cat > "$T/bin/fast-echo" <<'EOF'
#!/usr/bin/env bash
[[ ! -p /dev/fd/9 ]] || exit 33
/bin/bash -c '[[ ! -p /dev/fd/9 ]]' || exit 34
/bin/sleep 0.05
exec /bin/echo "$@"
EOF
chmod +x "$T/bin/fast-echo"

# Observe only the adapter's cancellation seam: record the actual timer job
# and its private paths, call the real helper, then check its wait/cleanup.
# The gated timer-entry seam waits before FIFO open until cancellation is written.
cat > "$T/fast-call.sh" <<'EOF'
#!/usr/bin/env bash
[[ "${IGNORE_TERM:-0}" != "1" ]] || trap '' TERM
source "$1/plugin/lib/codex-wrapper.sh"
eval "$(declare -f _adapter_stop_timer | sed '1s/_adapter_stop_timer/_test_stop_timer/')"
_adapter_stop_timer() {
  [[ -n "${1:-}" ]] || return 0
  printf '%s\n' "$1" > "$OBSERVATION_DIR/timer.pid"
  printf '%s\n' "$2" > "$OBSERVATION_DIR/control.path"
  if [[ "$(LC_ALL=C ls -ld "$2" | awk '{ print substr($1, 1, 10) }')" == "prw-------" ]] &&
     [[ "$(LC_ALL=C ls -ld "${2%/*}" | awk '{ print substr($1, 1, 10) }')" == "drwx------" ]]; then
    : > "$OBSERVATION_DIR/private"
  fi
  _test_stop_timer "$@"
}
printf() {
  builtin printf "$@" || return $?
  if [[ "${DELAY_TIMER_OPEN:-0}" == "1" && "${1:-}" == 'cancel\n' && ! -f "$OBSERVATION_DIR/open-started" ]]; then
    : > "$OBSERVATION_DIR/cancel-before-open"
  fi
}
trap() {
  builtin trap "$@"
  # Gate the exact timer-entry seam before any FIFO redirection (not inside
  # read(), whose redirect would already be applied). Release only after the
  # real cancellation write succeeds, while the parent retains duplex FD9.
  if [[ "${DELAY_TIMER_OPEN:-0}" == "1" && "${1:-}" == "-" && "${2:-}" == "EXIT" ]]; then
    local test_polls=0
    : > "$OBSERVATION_DIR/delay-started"
    while [[ ! -f "$OBSERVATION_DIR/cancel-before-open" && "$test_polls" -lt 200 ]]; do
      /bin/sleep 0.01
      test_polls=$((test_polls + 1))
    done
    [[ -f "$OBSERVATION_DIR/cancel-before-open" ]] || exit 19
    : > "$OBSERVATION_DIR/open-started"
  fi
}
kill() { printf '%s\n' "$*" >> "$OBSERVATION_DIR/signals"; builtin kill "$@"; }
if [[ "${TRIGGER_POLL_EXIT:-0}" == "1" ]]; then
  _adapter_job_running() { exit 17; }
fi
exec 9>"$OBSERVATION_DIR/caller-fd9"
s=$($NOW_BIN)
if invoke_codex hello 10 2; then call_rc=0; else call_rc=$?; fi
f=$($NOW_BIN)
awk -v s="$s" -v f="$f" 'BEGIN { printf "%.3f", f-s }' > "$OBSERVATION_DIR/elapsed"
printf 'restored\n' >&9
jobs -pr > "$OBSERVATION_DIR/remaining-jobs"
exit "$call_rc"
EOF
chmod +x "$T/fast-call.sh"

# Force the portable path even on systems with timeout/gtimeout installed.
NOW_BIN="$T/bin/now"; export NOW_BIN
cat > "$NOW_BIN" <<'EOF'
#!/usr/bin/perl
use Time::HiRes qw(time);
printf "%.6f", time;
EOF
chmod +x "$NOW_BIN"
mkdir "$T/fast-observed"
HOME="$T/home" PATH="$T/bin:/usr/bin:/bin" OBSERVATION_DIR="$T/fast-observed" DEVSQUAD_FORCE_PORTABLE_TIMEOUT=1 DEVSQUAD_TEST_ADAPTER_EXECUTABLE="$T/bin/fast-echo" CLAUDE_PROJECT_DIR="$T/project" FAKE_MODE=fast \
  /bin/bash "$T/fast-call.sh" "$ROOT" > "$T/out" 2> "$T/err"
fast_elapsed=$(cat "$T/fast-observed/elapsed")
grep -q 'exec hello' "$T/out" && ok || bad "portable watchdog output"
awk -v e="$fast_elapsed" 'BEGIN { exit !(e < 1.0) }' && ok || bad "portable fast call took ${fast_elapsed}s"
[[ -s "$T/fast-observed/timer.pid" ]] && ok || bad "portable success timer was not observed"
if [[ -s "$T/fast-observed/remaining-jobs" ]] ||
   { [[ -s "$T/fast-observed/timer.pid" ]] && kill -0 "$(cat "$T/fast-observed/timer.pid")" 2>/dev/null; }; then
  bad "portable success left watchdog timer alive"
else
  ok
fi
[[ -f "$T/fast-observed/private" ]] && ok || bad "timer FIFO or directory permissions are not private"
[[ -s "$T/fast-observed/control.path" && ! -e "$(dirname "$(cat "$T/fast-observed/control.path")")" ]] && ok || bad "timer private directory was not removed"
grep -qx 'restored' "$T/fast-observed/caller-fd9" && ok || bad "timer cancellation damaged caller FD9"
[[ ! -s "$T/fast-observed/signals" ]] && ok || bad "fast timer cancellation signalled a numeric PID"

# An ignored TERM disposition is inherited by the timer. Cancellation must
# still reap it immediately rather than waiting out the whole two-second limit.
mkdir "$T/ignored-observed"
HOME="$T/home" PATH="$T/bin:/usr/bin:/bin" OBSERVATION_DIR="$T/ignored-observed" IGNORE_TERM=1 DEVSQUAD_FORCE_PORTABLE_TIMEOUT=1 DEVSQUAD_TEST_ADAPTER_EXECUTABLE="$T/bin/fast-echo" CLAUDE_PROJECT_DIR="$T/project" \
  /bin/bash "$T/fast-call.sh" "$ROOT" > "$T/ignored.out" 2> "$T/ignored.err"
ignored_elapsed=$(cat "$T/ignored-observed/elapsed")
grep -q 'exec hello' "$T/ignored.out" && ok || bad "ignored-TERM fast output"
awk -v e="$ignored_elapsed" 'BEGIN { exit !(e < 1.0) }' && ok || bad "ignored-TERM fast call took ${ignored_elapsed}s"
[[ -s "$T/ignored-observed/timer.pid" ]] && ok || bad "ignored-TERM timer was not observed"
if [[ -s "$T/ignored-observed/remaining-jobs" ]] ||
   { [[ -s "$T/ignored-observed/timer.pid" ]] && kill -0 "$(cat "$T/ignored-observed/timer.pid")" 2>/dev/null; }; then
  bad "ignored-TERM success left watchdog timer alive"
else
  ok
fi

mkdir "$T/early-observed"
HOME="$T/home" PATH="$T/bin:/usr/bin:/bin" OBSERVATION_DIR="$T/early-observed" DELAY_TIMER_OPEN=1 DEVSQUAD_FORCE_PORTABLE_TIMEOUT=1 DEVSQUAD_TEST_ADAPTER_EXECUTABLE="$T/bin/fast-echo" CLAUDE_PROJECT_DIR="$T/project" \
  /bin/bash "$T/fast-call.sh" "$ROOT" > "$T/early.out" 2> "$T/early.err"
early_elapsed=$(cat "$T/early-observed/elapsed")
grep -q 'exec hello' "$T/early.out" && ok || bad "early timer cancellation lost CLI output"
awk -v e="$early_elapsed" 'BEGIN { exit !(e < 1.0) }' && ok || bad "cancellation before timer read took ${early_elapsed}s"
[[ -s "$T/early-observed/timer.pid" && ! -s "$T/early-observed/remaining-jobs" ]] && ok || bad "early cancellation did not reap timer"
[[ -s "$T/early-observed/control.path" && ! -e "$(dirname "$(cat "$T/early-observed/control.path")")" ]] && ok || bad "early timer cancellation left its FIFO"
[[ -f "$T/early-observed/cancel-before-open" && -f "$T/early-observed/open-started" ]] && ok || bad "cancellation-before-timer-open seam was not exercised"

# Cancellation after a timer has naturally exited/reaped is safe, and must
# never fall back to signalling its former numeric PID. The helper restores
# the caller's FD9 even when there is no longer another FIFO reader.
TIMER_FIXTURE_DIR="$T/exited-timer"; export TIMER_FIXTURE_DIR
mkdir -m 700 "$TIMER_FIXTURE_DIR"
mkfifo -m 600 "$TIMER_FIXTURE_DIR/control"
/bin/bash -c '
  source "$1/plugin/lib/adapter.sh"
  kill() { printf "%s\n" "$*" >> "$TIMER_FIXTURE_DIR/signals"; return 99; }
  exec 9>"$TIMER_FIXTURE_DIR/caller-fd9"
  (trap - EXIT; if IFS= read -r -t 1 message <>"$TIMER_FIXTURE_DIR/control"; then exit 8; else printf "timeout\n" > "$TIMER_FIXTURE_DIR/deadline"; fi) </dev/null >/dev/null 2>&1 &
  timer_pid=$!
  wait "$timer_pid"
  _adapter_stop_timer "$timer_pid" "$TIMER_FIXTURE_DIR/control"
  printf "restored\n" >&9
  jobs -pr > "$TIMER_FIXTURE_DIR/remaining-jobs"
' _ "$ROOT" > "$T/exited.out" 2> "$T/exited.err"
grep -qx 'timeout' "$TIMER_FIXTURE_DIR/deadline" && ok || bad "timer did not reach its natural deadline"
[[ ! -s "$TIMER_FIXTURE_DIR/signals" && ! -s "$TIMER_FIXTURE_DIR/remaining-jobs" ]] && ok || bad "late timer cancellation signalled a PID or retained a job"
grep -qx 'restored' "$TIMER_FIXTURE_DIR/caller-fd9" && ok || bad "late timer cancellation damaged caller FD9"

# Actual CLI exit status, not timer completion, determines ordinary failures.
mkdir "$T/error-observed"
HOME="$T/home" PATH="$T/bin:/usr/bin:/bin" OBSERVATION_DIR="$T/error-observed" DEVSQUAD_FORCE_PORTABLE_TIMEOUT=1 DEVSQUAD_TEST_ADAPTER_EXECUTABLE="$T/bin/codex" CLAUDE_PROJECT_DIR="$T/project" FAKE_MODE=error \
  /bin/bash "$T/fast-call.sh" "$ROOT" > "$T/error.out" 2> "$T/error.err" || error_rc=$?
[[ "${error_rc:-0}" -eq 1 ]] && grep -q '^CLI_ERROR:.*exit 7.*deliberate offline failure' "$T/error.err" && ok || bad "portable watchdog lost CLI failure status/classification"
[[ -s "$T/error-observed/control.path" && ! -e "$(dirname "$(cat "$T/error-observed/control.path")")" && ! -s "$T/error-observed/remaining-jobs" ]] && ok || bad "ordinary CLI failure left a timer or FIFO"

# The invocation's EXIT cleanup must not survive its local scope and resolve
# caller globals. Preserve a prior caller EXIT hook on success and failure,
# on both the portable branch and the timeout-binary compatibility branch.
mkdir -p "$T/gnu/bin"
cat > "$T/gnu/bin/timeout" <<'EOF'
#!/usr/bin/env bash
shift
exec "$@"
EOF
chmod +x "$T/gnu/bin/timeout"
cat > "$T/caller-scope.sh" <<'EOF'
#!/usr/bin/env bash
source "$1/plugin/lib/codex-wrapper.sh"
timer_dir="$CALLER_STATE"
timer_pid=""
timer_control_file=""
trap 'printf "restored\n" > "$CALLER_STATE/prior-exit"' EXIT
if invoke_codex hello 10 2; then call_rc=0; else call_rc=$?; fi
[[ -f "$timer_dir/control" && -f "$timer_dir/deadline" && -f "$timer_dir/processes" ]] || exit 18
exit "$call_rc"
EOF
for caller_case in portable-success gnu-success portable-failure; do
  caller_state="$T/caller-$caller_case"
  mkdir "$caller_state"
  printf 'caller\n' > "$caller_state/control"
  printf 'caller\n' > "$caller_state/deadline"
  printf 'caller\n' > "$caller_state/processes"
  caller_path="$T/bin:/usr/bin:/bin" caller_force=1 caller_mode=fast caller_expected=0
  if [[ "$caller_case" == "gnu-success" ]]; then caller_path="$T/gnu/bin:$caller_path"; caller_force=0; fi
  if [[ "$caller_case" == "portable-failure" ]]; then caller_mode=error; caller_expected=1; fi
  if HOME="$T/home" PATH="$caller_path" CALLER_STATE="$caller_state" DEVSQUAD_FORCE_PORTABLE_TIMEOUT="$caller_force" DEVSQUAD_TEST_ADAPTER_EXECUTABLE="$T/bin/codex" CLAUDE_PROJECT_DIR="$T/project" FAKE_MODE="$caller_mode" \
    /bin/bash "$T/caller-scope.sh" "$ROOT" > "$T/$caller_case.out" 2> "$T/$caller_case.err"; then caller_rc=0; else caller_rc=$?; fi
  [[ "$caller_rc" -eq "$caller_expected" && -f "$caller_state/control" && -f "$caller_state/deadline" && -f "$caller_state/processes" ]] && ok || bad "$caller_case EXIT cleanup deleted caller-owned paths"
  [[ -f "$caller_state/prior-exit" ]] && grep -qx restored "$caller_state/prior-exit" && ok || bad "$caller_case failed to restore caller EXIT trap"
done

# An exit while invocation locals are still live must cancel/reap its timer
# and remove its private FIFO, rather than simply dropping the cleanup trap.
mkdir "$T/aborted-observed"
HOME="$T/home" PATH="$T/bin:/usr/bin:/bin" OBSERVATION_DIR="$T/aborted-observed" TRIGGER_POLL_EXIT=1 DEVSQUAD_FORCE_PORTABLE_TIMEOUT=1 DEVSQUAD_TEST_ADAPTER_EXECUTABLE="$T/bin/fast-echo" CLAUDE_PROJECT_DIR="$T/project" \
  /bin/bash "$T/fast-call.sh" "$ROOT" > "$T/aborted.out" 2> "$T/aborted.err" || aborted_rc=$?
[[ "${aborted_rc:-0}" -eq 17 && -s "$T/aborted-observed/timer.pid" ]] && ok || bad "active-scope exit cleanup seam was not exercised"
if [[ -s "$T/aborted-observed/timer.pid" ]] && kill -0 "$(cat "$T/aborted-observed/timer.pid")" 2>/dev/null; then
  bad "active-scope exit left watchdog timer alive"
else
  ok
fi
[[ -s "$T/aborted-observed/control.path" && ! -e "$(dirname "$(cat "$T/aborted-observed/control.path")")" ]] && ok || bad "active-scope exit left private timer paths"

# Model lookup remains optional when jq is absent from PATH.
mkdir -p "$T/nojq"
ln -s /bin/bash "$T/nojq/bash"
ln -s /usr/bin/dirname "$T/nojq/dirname"
PATH="$T/nojq" CLAUDE_PROJECT_DIR="$T/project" /bin/bash -c \
  'source "$1/plugin/lib/codex-wrapper.sh"; [[ -z "$(_resolve_codex_model)" ]]' _ "$ROOT" && ok || bad "jq-absent model fallback"

DESC_PID_FILE="$T/desc.pid"; export DESC_PID_FILE
start=$(date +%s)
PATH="$T/bin:/usr/bin:/bin" DEVSQUAD_FORCE_PORTABLE_TIMEOUT=1 CLAUDE_PROJECT_DIR="$T/project" FAKE_MODE=tree \
  bash -c 'source "$1/plugin/lib/codex-wrapper.sh"; invoke_codex hello 10 1' _ "$ROOT" > "$T/tree.out" 2> "$T/tree.err" || rc=$?
elapsed=$(( $(date +%s) - start ))
[[ "${rc:-0}" -eq 1 ]] && grep -q '^TIMEOUT:' "$T/tree.err" && ok || bad "portable timeout classification"
[[ "$elapsed" -lt 4 ]] && ok || bad "portable timeout elapsed ${elapsed}s"
if [[ -s "$DESC_PID_FILE" ]] && kill -0 "$(cat "$DESC_PID_FILE")" 2>/dev/null; then
  bad "portable timeout left descendant alive"
else
  ok
fi

# A root process that ignores TERM must still be KILLed by the watchdog itself;
# escalation after wait would deadlock forever. The readiness marker proves the
# fake entered its signal-resistant loop before the deadline.
ROOT_PID_FILE="$T/root.pid" ROOT_READY_FILE="$T/root.ready"
export ROOT_PID_FILE ROOT_READY_FILE
start=$(perl -MTime::HiRes=time -e 'printf "%.6f", time')
HOME="$T/home" PATH="/usr/bin:/bin" DEVSQUAD_FORCE_PORTABLE_TIMEOUT=1 DEVSQUAD_TEST_ADAPTER_EXECUTABLE="$T/bin/codex" CLAUDE_PROJECT_DIR="$T/project" FAKE_MODE=root_ignore \
  /bin/bash -c 'source "$1/plugin/lib/codex-wrapper.sh"; invoke_codex hello 10 3' _ "$ROOT" > "$T/root.out" 2> "$T/root.err" || root_rc=$?
finish=$(perl -MTime::HiRes=time -e 'printf "%.6f", time')
root_elapsed=$(awk -v s="$start" -v f="$finish" 'BEGIN { printf "%.3f", f-s }')
[[ -f "$ROOT_READY_FILE" ]] && ok || bad "TERM-ignoring root never reached readiness"
[[ "${root_rc:-0}" -eq 1 ]] && grep -q '^TIMEOUT:' "$T/root.err" && ok || bad "TERM-ignoring root timeout classification"
awk -v e="$root_elapsed" 'BEGIN { exit !(e >= 2.8 && e < 5.0) }' && ok || bad "TERM-ignoring root took ${root_elapsed}s"
if [[ -s "$ROOT_PID_FILE" ]] && kill -0 "$(cat "$ROOT_PID_FILE")" 2>/dev/null; then
  bad "portable timeout left TERM-ignoring root alive"
else
  ok
fi

# A costly inspection must not extend a one-second real deadline. Keep the
# existing <4s timeout assertion, and verify the resistant root is still gone.
start=$(perl -MTime::HiRes=time -e 'printf "%.6f", time')
HOME="$T/home" PATH="$T/bin:/usr/bin:/bin" DEVSQUAD_FORCE_PORTABLE_TIMEOUT=1 DEVSQUAD_TEST_ADAPTER_EXECUTABLE="$T/bin/codex" CLAUDE_PROJECT_DIR="$T/project" FAKE_MODE=root_ignore FAKE_PROBE_DELAY=0.2 ROOT_PID_FILE="$T/slow-root.pid" ROOT_READY_FILE="$T/slow-root.ready" \
  /bin/bash -c 'source "$1/plugin/lib/codex-wrapper.sh"; invoke_codex hello 10 1' _ "$ROOT" > "$T/slow.out" 2> "$T/slow.err" || slow_rc=$?
finish=$(perl -MTime::HiRes=time -e 'printf "%.6f", time')
slow_elapsed=$(awk -v s="$start" -v f="$finish" 'BEGIN { printf "%.3f", f-s }')
[[ -f "$T/slow-root.ready" ]] && ok || bad "slow-probe root never reached readiness"
[[ "${slow_rc:-0}" -eq 1 ]] && grep -q '^TIMEOUT:' "$T/slow.err" && ok || bad "slow-probe timeout classification"
awk -v e="$slow_elapsed" 'BEGIN { exit !(e >= 0.8 && e < 4.0) }' && ok || bad "slow process probes extended one-second deadline to ${slow_elapsed}s"
if [[ -s "$T/slow-root.pid" ]] && kill -0 "$(cat "$T/slow-root.pid")" 2>/dev/null; then
  bad "slow-probe timeout left TERM-ignoring root alive"
else
  ok
fi

# Newline manifests preserve spaces and enumerate TSX.
mkdir -p "$T/project/ui/My Folder"
printf 'export const Card = 1;\n' > "$T/project/ui/My Folder/Card.tsx"
git -C "$T/project" init -q
git -C "$T/project" add "ui/My Folder/Card.tsx"
cat > "$T/bin/agy" <<'EOF'
#!/usr/bin/env bash
cat
printf '{"ok":true}\n'
EOF
chmod +x "$T/bin/agy"
PATH="$T/bin:/usr/bin:/bin" CLAUDE_PROJECT_DIR="$T/project" \
  bash -c 'cd "$1"; source "$2/plugin/lib/gemini-wrapper.sh"; invoke_gemini_with_files "@ui/My Folder" inspect 10 2' _ "$T/project" "$ROOT" > "$T/context" 2>/dev/null
grep -q 'Card.tsx' "$T/context" && ok || bad "TSX path with spaces omitted"

# Ignored, oversized, escaping and symlink inputs are reported rather than
# silently read or truncated.
printf 'ignored.txt\n' > "$T/project/.gitignore"
printf 'secret\n' > "$T/project/ignored.txt"
printf '123456789\n' > "$T/project/large.ts"
ln -s /etc/passwd "$T/project/escape.ts"
git -C "$T/project" add .gitignore large.ts
git -C "$T/project" add -f ignored.txt escape.ts
PATH="$T/bin:/usr/bin:/bin" CLAUDE_PROJECT_DIR="$T/project" DEVSQUAD_CONTEXT_MAX_FILE_BYTES=4 \
  bash -c 'source "$1/plugin/lib/gemini-wrapper.sh"; invoke_gemini_with_files "$2" inspect 10 2' _ "$ROOT" \
  $'@ignored.txt\n@large.ts\n@escape.ts\n@../outside' > "$T/omitted.out" 2> "$T/omitted.err"
grep -q 'ignored path excluded: ignored.txt' "$T/omitted.err" && ok || bad "ignored file omission not reported"
grep -q 'file exceeds 4 byte limit: large.ts' "$T/omitted.err" && ok || bad "byte limit omission not reported"
grep -q 'symlink input is not followed: escape.ts' "$T/omitted.err" && ok || bad "symlink omission not reported"
grep -q 'path escapes project scope: ../outside' "$T/omitted.err" && ok || bad "path escape not reported"

# Replacing a tracked directory with a symlink must not let a tracked path read
# bytes outside the project through a symlink ancestor.
mkdir -p "$T/project/safe" "$T/outside"
printf 'inside\n' > "$T/project/safe/code.ts"
git -C "$T/project" add safe/code.ts
mv "$T/project/safe" "$T/project/safe.real"
printf 'OUTSIDE_MARKER\n' > "$T/outside/code.ts"
ln -s "$T/outside" "$T/project/safe"
PATH="$T/bin:/usr/bin:/bin" CLAUDE_PROJECT_DIR="$T/project" \
  bash -c 'source "$1/plugin/lib/gemini-wrapper.sh"; _adapter_invoke() { cat "$ADAPTER_STDIN_FILE"; }; invoke_gemini_with_files "@safe/code.ts" inspect 10 2' _ "$ROOT" > "$T/ancestor.out" 2> "$T/ancestor.err"
grep -q 'OUTSIDE_MARKER' "$T/ancestor.out" && bad "symlink ancestor leaked outside content" || ok
grep -q 'symlink ancestor escapes project scope: safe/code.ts' "$T/ancestor.err" && ok || bad "symlink ancestor omission not reported"

echo "  watchdog timing: fast=${fast_elapsed}s ignored-TERM=${ignored_elapsed}s early-cancel=${early_elapsed}s tree=${elapsed}s resistant=${root_elapsed}s slow-probe=${slow_elapsed}s"
echo "  m1_legacy: ${PASS} passed, ${FAIL} failed"
[[ "$FAIL" -eq 0 ]]
