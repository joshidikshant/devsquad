#!/usr/bin/env bash
# Tests for git-health symlink detection (check-symlinks.sh) and the
# JSON aggregation in git-health.sh (total_issues).
# bash-3 compatible, no network, no real external CLIs beyond core utils.
# The git-health.sh --json path builds JSON by hand (no jq); we exercise it
# under a PATH shim that contains only core utils (no jq) to prove the
# jq-less path works — mirroring test_routing.sh Group 5.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SCRIPTS_DIR="${REPO_ROOT}/plugin/skills/git-health/scripts"
CHECK_SYMLINKS="${SCRIPTS_DIR}/check-symlinks.sh"
GIT_HEALTH="${SCRIPTS_DIR}/git-health.sh"

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

assert_ge() {
  local label="$1" got="$2" min="$3"
  if [ "$got" -ge "$min" ] 2>/dev/null; then
    PASS=$((PASS + 1))
  else
    FAIL=$((FAIL + 1))
    echo "  FAIL: $label — got '[${got}]', expected >= ${min}"
  fi
}

assert_contains() {
  local label="$1" haystack="$2" needle="$3"
  case "$haystack" in
    *"$needle"*) PASS=$((PASS + 1)) ;;
    *)
      FAIL=$((FAIL + 1))
      echo "  FAIL: $label — '[${needle}]' not found in output"
      ;;
  esac
}

assert_not_contains() {
  local label="$1" haystack="$2" needle="$3"
  case "$haystack" in
    *"$needle"*)
      FAIL=$((FAIL + 1))
      echo "  FAIL: $label — '[${needle}]' unexpectedly present in output"
      ;;
    *) PASS=$((PASS + 1)) ;;
  esac
}

# Extract the count=N number emitted by check-symlinks.sh / check-*.sh.
extract_count() {
  # $1 = raw output; prints the number after "count="
  printf '%s\n' "$1" | grep '^count=' | head -1 | cut -d= -f2
}

# ---------------------------------------------------------------------------
# Fixture: a temp dir with a real file, a valid symlink, a dangling symlink,
# and a symlink buried inside a fake .git/objects/ subdir (must be skipped).
# ---------------------------------------------------------------------------
make_fixture() {
  local d
  d=$(mktemp -d)
  # Real file + valid symlink to it (relative target)
  printf 'hello\n' > "$d/real.txt"
  ln -s "real.txt" "$d/valid_link"           # -> real.txt (exists)
  # Dangling symlink to a non-existent absolute path
  ln -s "/nonexistent/does/not/exist" "$d/dangling_link"
  # Symlink inside .git/objects/ that dangles — must be EXCLUDED by the script
  mkdir -p "$d/.git/objects/ab"
  ln -s "/nonexistent/git/object" "$d/.git/objects/ab/gitobj_link"
  printf '%s' "$d"
}

# ===========================================================================
# Group 1: check-symlinks.sh detection semantics
# ===========================================================================
FIX=$(make_fixture)

RAW=$(bash "$CHECK_SYMLINKS" "$FIX")
CNT=$(extract_count "$RAW")

# Exactly one broken link: the dangling one. Valid link not counted; the
# .git/objects/ dangling link is excluded.
assert_eq "symlink count == 1 (only the dangling link)" "$CNT" "1"
assert_ge "symlink count >= 1"                          "$CNT" "1"

# The dangling link must appear in the detail output; the valid one must not;
# the .git/objects link must not (it was skipped).
assert_contains     "dangling link reported"        "$RAW" "dangling_link"
assert_not_contains "valid link NOT reported"       "$RAW" "valid_link"
assert_not_contains ".git/objects link SKIPPED"     "$RAW" "gitobj_link"

# ===========================================================================
# Group 2: a dir with only a valid symlink -> zero broken
# ===========================================================================
CLEAN=$(mktemp -d)
printf 'x\n' > "$CLEAN/target.txt"
ln -s "target.txt" "$CLEAN/good_link"
RAW_CLEAN=$(bash "$CHECK_SYMLINKS" "$CLEAN")
assert_eq "clean dir: broken count == 0" "$(extract_count "$RAW_CLEAN")" "0"

# ===========================================================================
# Group 3: git-health.sh --json aggregates the symlink count into
#          a numeric total_issues.  Run inside a real (local) git repo so the
#          branch/changes checks operate normally; no remote, no network.
# ===========================================================================
REPO=$(mktemp -d)
(
  cd "$REPO" || exit 1
  git init -q -b main 2>/dev/null || git init -q 2>/dev/null
  git config user.email "test@example.com"
  git config user.name  "Test"
  # Commit a clean baseline so uncommitted-changes noise is deterministic.
  printf 'root\n' > README.md
  git add README.md
  git commit -q -m "initial" 2>/dev/null
  # Now introduce exactly one broken symlink (dangling) + one valid symlink
  # + one excluded .git/objects symlink.
  printf 'data\n' > payload.txt
  git add payload.txt
  git commit -q -m "payload" 2>/dev/null
  ln -s "payload.txt" valid_link
  ln -s "/nonexistent/broken/target" broken_link
  mkdir -p .git/objects/cd
  ln -s "/nonexistent/git/obj" .git/objects/cd/should_skip
)

# Build a PATH shim containing ONLY core utils (deliberately NO jq) so the
# hand-rolled JSON construction path in git-health.sh is exercised. git is
# included because the branch/changes sub-checks require it.
SHIM=$(mktemp -d)
for cmd in bash sh cat grep sed tr head cut date mkdir mv wc dirname basename \
           find sort xargs awk uname rm ls env printf git readlink; do
  p=$(command -v "$cmd" 2>/dev/null) && ln -s "$p" "$SHIM/$cmd" 2>/dev/null
done

# Sanity: jq must NOT be resolvable via the shim (proves jq-less path).
JQ_IN_SHIM=$(PATH="$SHIM" command -v jq 2>/dev/null || true)
assert_eq "jq absent from PATH shim (jq-less path exercised)" "$JQ_IN_SHIM" ""

# Run git-health --json against the repo via CLAUDE_PROJECT_DIR, under the shim.
JSON=$(CLAUDE_PROJECT_DIR="$REPO" PATH="$SHIM" bash "$GIT_HEALTH" --json 2>/dev/null)

# The symlink sub-check, run standalone against the same repo, should report 1
# (the dangling one; valid + .git/objects excluded).
SYMLINK_CNT=$(extract_count "$(bash "$CHECK_SYMLINKS" "$REPO")")
assert_eq "repo standalone symlink count == 1" "$SYMLINK_CNT" "1"

# Parse total_issues out of the JSON without jq (grep/sed only).
TOTAL=$(printf '%s\n' "$JSON" | grep '"total_issues"' | sed 's/[^0-9]//g')
# And the symlinks.count field the JSON embeds.
JSON_SYMLINK_CNT=$(printf '%s\n' "$JSON" \
  | grep '"symlinks"' \
  | sed 's/.*"count": *\([0-9][0-9]*\).*/\1/')

assert_contains "JSON contains total_issues field" "$JSON" "\"total_issues\""
assert_eq       "JSON symlinks.count == 1"         "$JSON_SYMLINK_CNT" "1"

# total_issues must be numeric and at least as large as the symlink count
# (it also folds in branch + change issues, which are >= 0).
case "$TOTAL" in
  ''|*[!0-9]*)
    FAIL=$((FAIL + 1))
    echo "  FAIL: total_issues is not a plain integer — got '[${TOTAL}]'"
    ;;
  *) PASS=$((PASS + 1)) ;;
esac
assert_ge "total_issues >= symlink count (folds in symlinks)" "${TOTAL:-0}" "$JSON_SYMLINK_CNT"
assert_ge "total_issues >= 1 (broken symlink present)"        "${TOTAL:-0}" "1"

# ---------------------------------------------------------------------------
echo "  git_health: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ]
