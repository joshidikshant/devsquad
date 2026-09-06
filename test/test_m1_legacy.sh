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

# Record the portable watchdog's timer PID so the success path can prove it
# did not orphan the sleep process. Other sleep durations use the real binary.
cat > "$T/bin/sleep" <<'EOF'
#!/usr/bin/env bash
if [[ "$1" == "2" && -n "${WATCHDOG_SLEEP_PID_FILE:-}" ]]; then
  printf '%s\n' "$$" > "$WATCHDOG_SLEEP_PID_FILE"
fi
exec /bin/sleep "$@"
EOF
chmod +x "$T/bin/sleep"

# Force the portable path even on systems with timeout/gtimeout installed.
WATCHDOG_SLEEP_PID_FILE="$T/watchdog-sleep.pid"; export WATCHDOG_SLEEP_PID_FILE
FAST_ELAPSED_FILE="$T/fast-elapsed"; export FAST_ELAPSED_FILE
NOW_BIN="$T/bin/now"; export NOW_BIN
cat > "$NOW_BIN" <<'EOF'
#!/usr/bin/perl
use Time::HiRes qw(time);
printf "%.6f", time;
EOF
chmod +x "$NOW_BIN"
HOME="$T/home" PATH="/usr/bin:/bin" DEVSQUAD_FORCE_PORTABLE_TIMEOUT=1 DEVSQUAD_TEST_ADAPTER_EXECUTABLE="/bin/echo" CLAUDE_PROJECT_DIR="$T/project" FAKE_MODE=fast \
  bash -c 'source "$1/plugin/lib/codex-wrapper.sh"; s=$($NOW_BIN); invoke_codex hello 10 2; f=$($NOW_BIN); awk -v s="$s" -v f="$f" '\''BEGIN { printf "%.3f", f-s }'\'' > "$FAST_ELAPSED_FILE"' _ "$ROOT" > "$T/out" 2> "$T/err"
fast_elapsed=$(cat "$FAST_ELAPSED_FILE")
grep -q 'exec hello' "$T/out" && ok || bad "portable watchdog output"
awk -v e="$fast_elapsed" 'BEGIN { exit !(e < 1.0) }' && ok || bad "portable fast call took ${fast_elapsed}s"
if [[ -s "$WATCHDOG_SLEEP_PID_FILE" ]] && kill -0 "$(cat "$WATCHDOG_SLEEP_PID_FILE")" 2>/dev/null; then
  bad "portable success left watchdog sleep alive"
else
  ok
fi

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

echo "  m1_legacy: ${PASS} passed, ${FAIL} failed"
[[ "$FAIL" -eq 0 ]]
