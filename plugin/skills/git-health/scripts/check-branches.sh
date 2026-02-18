#!/usr/bin/env bash
# check-branches.sh -- Detect orphaned branches (merged or stale)
# Usage: check-branches.sh [PROJECT_DIR] [--days N]
# Output: count=N followed by one line per orphaned branch:
#   BRANCH_NAME|DAYS_OLD|REASON
# Reason: "merged" or "stale"

set -euo pipefail

PROJECT_DIR="${1:-.}"
STALE_DAYS=30

# Parse --days argument
shift 2>/dev/null || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --days) shift; STALE_DAYS="${1:-30}"; shift ;;
    *)      shift ;;
  esac
done

PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"

# Must be a git repo
if ! git -C "$PROJECT_DIR" rev-parse --git-dir &>/dev/null 2>&1; then
  echo "count=0"
  echo "ERROR: not a git repository"
  exit 0
fi

# Determine default branch (main or master)
DEFAULT_BRANCH=""
for candidate in main master; do
  if git -C "$PROJECT_DIR" rev-parse --verify "$candidate" &>/dev/null 2>&1; then
    DEFAULT_BRANCH="$candidate"
    break
  fi
done

# Get current branch (to exclude it from orphan candidates)
CURRENT_BRANCH=$(git -C "$PROJECT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "HEAD")

orphan_count=0
orphan_lines=""

# Iterate over local branches (exclude current and default)
while IFS= read -r branch; do
  branch=$(echo "$branch" | xargs)  # trim whitespace
  [[ -z "$branch" ]] && continue
  [[ "$branch" == "$CURRENT_BRANCH" ]] && continue
  [[ "$branch" == "$DEFAULT_BRANCH" ]] && continue
  [[ "$branch" == HEAD ]] && continue

  # Get last commit date for this branch (Unix timestamp)
  last_commit_ts=$(git -C "$PROJECT_DIR" log -1 --format="%ct" "$branch" 2>/dev/null || echo "0")
  if [[ -z "$last_commit_ts" || "$last_commit_ts" == "0" ]]; then
    continue
  fi

  now_ts=$(date +%s)
  days_old=$(( (now_ts - last_commit_ts) / 86400 ))

  # Check if merged into default branch
  is_merged=false
  if [[ -n "$DEFAULT_BRANCH" ]]; then
    if git -C "$PROJECT_DIR" merge-base --is-ancestor "$branch" "$DEFAULT_BRANCH" 2>/dev/null; then
      is_merged=true
    fi
  fi

  # Classify: orphaned if merged OR stale (older than threshold)
  reason=""
  if [[ "$is_merged" == "true" ]]; then
    reason="merged"
  elif [[ "$days_old" -ge "$STALE_DAYS" ]]; then
    reason="stale"
  else
    continue  # branch is active and not merged
  fi

  orphan_count=$((orphan_count + 1))
  line="${branch}|${days_old}|${reason}"
  if [[ -z "$orphan_lines" ]]; then
    orphan_lines="$line"
  else
    orphan_lines="${orphan_lines}
${line}"
  fi

done < <(git -C "$PROJECT_DIR" branch --format='%(refname:short)' 2>/dev/null)

echo "count=${orphan_count}"
if [[ -n "$orphan_lines" ]]; then
  echo "$orphan_lines"
fi
