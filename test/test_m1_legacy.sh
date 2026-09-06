#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); }
bad() { FAIL=$((FAIL + 1)); echo "  FAIL: $1"; }

T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/project/.devsquad"
cat > "$T/bin/codex" <<'EOF'
#!/usr/bin/env bash
case "${FAKE_MODE:-fast}" in
  fast) printf 'ok' ;;
  tree)
    sh -c 'trap "" TERM; echo $$ > "$DESC_PID_FILE"; while :; do sleep 1; done' &
    wait
    ;;
esac
EOF
chmod +x "$T/bin/codex"

# Force the portable path even on systems with timeout/gtimeout installed.
start=$(date +%s)
PATH="$T/bin:/usr/bin:/bin" CLAUDE_PROJECT_DIR="$T/project" FAKE_MODE=fast \
  bash -c 'source "$1/plugin/lib/codex-wrapper.sh"; invoke_codex hello 10 2' _ "$ROOT" > "$T/out" 2> "$T/err"
fast_elapsed=$(( $(date +%s) - start ))
[[ "$(cat "$T/out")" == "ok" ]] && ok || bad "portable watchdog output"
[[ "$fast_elapsed" -lt 2 ]] && ok || bad "portable fast call waited ${fast_elapsed}s for watchdog"

# Model lookup remains optional when jq is absent from PATH.
mkdir -p "$T/nojq"
ln -s /bin/bash "$T/nojq/bash"
ln -s /usr/bin/dirname "$T/nojq/dirname"
PATH="$T/nojq" CLAUDE_PROJECT_DIR="$T/project" /bin/bash -c \
  'source "$1/plugin/lib/codex-wrapper.sh"; [[ -z "$(_resolve_codex_model)" ]]' _ "$ROOT" && ok || bad "jq-absent model fallback"

DESC_PID_FILE="$T/desc.pid"; export DESC_PID_FILE
start=$(date +%s)
PATH="$T/bin:/usr/bin:/bin" CLAUDE_PROJECT_DIR="$T/project" FAKE_MODE=tree \
  bash -c 'source "$1/plugin/lib/codex-wrapper.sh"; invoke_codex hello 10 1' _ "$ROOT" > "$T/tree.out" 2> "$T/tree.err" || rc=$?
elapsed=$(( $(date +%s) - start ))
[[ "${rc:-0}" -eq 1 ]] && grep -q '^TIMEOUT:' "$T/tree.err" && ok || bad "portable timeout classification"
[[ "$elapsed" -lt 4 ]] && ok || bad "portable timeout elapsed ${elapsed}s"
if [[ -s "$DESC_PID_FILE" ]] && kill -0 "$(cat "$DESC_PID_FILE")" 2>/dev/null; then
  bad "portable timeout left descendant alive"
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

echo "  m1_legacy: ${PASS} passed, ${FAIL} failed"
[[ "$FAIL" -eq 0 ]]
