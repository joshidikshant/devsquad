#!/usr/bin/env bash
# check-changes.sh -- Detect uncommitted and unpushed changes
# Usage: check-changes.sh [PROJECT_DIR]
# Output: count=N followed by lines:
#   CATEGORY|FILE_PATH[|WARNING]
# Categories: STAGED, MODIFIED, UNTRACKED, AHEAD

set -euo pipefail

PROJECT_DIR="${1:-.}"
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"

# Must be a git repo
if ! git -C "$PROJECT_DIR" rev-parse --git-dir &>/dev/null 2>&1; then
  echo "count=0"
  exit 0
fi

issue_count=0
issue_lines=""

add_issue() {
  local line="$1"
  issue_count=$((issue_count + 1))
  if [[ -z "$issue_lines" ]]; then
    issue_lines="$line"
  else
    issue_lines="${issue_lines}
${line}"
  fi
}

# Check for commits ahead of remote (unpushed work — highest severity)
current_branch=$(git -C "$PROJECT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
if [[ -n "$current_branch" && "$current_branch" != "HEAD" ]]; then
  # Check if tracking remote
  remote_ref=$(git -C "$PROJECT_DIR" for-each-ref --format='%(upstream:short)' "refs/heads/${current_branch}" 2>/dev/null || echo "")
  if [[ -n "$remote_ref" ]]; then
    ahead_count=$(git -C "$PROJECT_DIR" rev-list --count "${remote_ref}..HEAD" 2>/dev/null || echo "0")
    if [[ "$ahead_count" -gt 0 ]]; then
      add_issue "AHEAD|${ahead_count} commit(s) not pushed to ${remote_ref}"
    fi
  fi
fi

# Check staged files (git index vs HEAD)
while IFS= read -r file; do
  [[ -z "$file" ]] && continue
  add_issue "STAGED|${file}"
done < <(git -C "$PROJECT_DIR" diff --name-only --cached 2>/dev/null)

# Check modified tracked files (working tree vs index)
while IFS= read -r file; do
  [[ -z "$file" ]] && continue
  add_issue "MODIFIED|${file}"
done < <(git -C "$PROJECT_DIR" diff --name-only 2>/dev/null)

# Check untracked files (not ignored)
while IFS= read -r file; do
  [[ -z "$file" ]] && continue
  # Flag potential secret files
  warning=""
  case "$(basename "$file")" in
    .env|.env.*|*.pem|*.key|*_rsa|*_dsa|credentials*|secrets*)
      warning="|WARNING: may contain secrets"
      ;;
  esac
  add_issue "UNTRACKED|${file}${warning}"
done < <(git -C "$PROJECT_DIR" ls-files --others --exclude-standard 2>/dev/null)

echo "count=${issue_count}"
if [[ -n "$issue_lines" ]]; then
  echo "$issue_lines"
fi
