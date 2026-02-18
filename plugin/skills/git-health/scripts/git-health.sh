#!/usr/bin/env bash
# git-health.sh -- DevSquad Git Health Check main runner
# Usage: git-health.sh [--json] [--check symlinks|branches|changes]

set -euo pipefail

# Resolve paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PLUGIN_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

# Source state library for project dir resolution
source "${PLUGIN_ROOT}/lib/state.sh"

# Determine project directory
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-.}"
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"

# Parse arguments
OUTPUT_JSON=false
CHECK_FILTER=""
STALE_DAYS=30

while [[ $# -gt 0 ]]; do
  case "$1" in
    --json)    OUTPUT_JSON=true; shift ;;
    --check)   shift; CHECK_FILTER="${1:-}"; shift ;;
    --check=*) CHECK_FILTER="${1#--check=}"; shift ;;
    --days)    shift; STALE_DAYS="${1:-30}"; shift ;;
    *)         shift ;;
  esac
done

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# ---- Run Symlink Check ----
run_symlink_check() {
  bash "${SCRIPT_DIR}/check-symlinks.sh" "$PROJECT_DIR" 2>/dev/null
}

# ---- Branch Check ----
run_branch_check() {
  local days="${STALE_DAYS:-30}"
  bash "${SCRIPT_DIR}/check-branches.sh" "$PROJECT_DIR" --days "$days" 2>/dev/null
}

# ---- Changes Check ----
run_changes_check() {
  bash "${SCRIPT_DIR}/check-changes.sh" "$PROJECT_DIR" 2>/dev/null
}

# ---- Generate cleanup suggestion for a broken symlink ----
symlink_suggestion() {
  local link_path="$1"
  local target="$2"
  # Heuristic: if target is in node_modules area, suggest npm install
  # Otherwise suggest git rm
  if echo "$target" | grep -q "node_modules"; then
    echo "REINSTALL: npm install"
  elif echo "$link_path" | grep -q ".git/hooks"; then
    echo "REMOVE: git rm \"${link_path}\""
  else
    echo "REMOVE: rm \"${link_path}\""
  fi
}

# ---- Collect results ----
SYMLINK_RAW=$(run_symlink_check)
BRANCH_RAW=$(run_branch_check)
CHANGES_RAW=$(run_changes_check)

SYMLINK_COUNT=$(echo "$SYMLINK_RAW" | grep "^count=" | cut -d= -f2 || echo "0")
BRANCH_COUNT=$(echo "$BRANCH_RAW" | grep "^count=" | cut -d= -f2 || echo "0")
CHANGES_COUNT=$(echo "$CHANGES_RAW" | grep "^count=" | cut -d= -f2 || echo "0")

TOTAL_ISSUES=$((SYMLINK_COUNT + BRANCH_COUNT + CHANGES_COUNT))

# ---- JSON output ----
if [[ "$OUTPUT_JSON" == "true" ]]; then
  # Build symlink issues JSON array
  SYMLINK_JSON_ITEMS=""
  while IFS= read -r line; do
    [[ -z "$line" || "$line" == count=* ]] && continue
    # line format: LINK -> TARGET
    link_path=$(echo "$line" | awk -F' -> ' '{print $1}')
    target=$(echo "$line" | awk -F' -> ' '{print $2}')
    suggestion=$(symlink_suggestion "$link_path" "$target")
    escaped_link=$(printf '%s' "$link_path" | sed 's/"/\\"/g')
    escaped_target=$(printf '%s' "$target" | sed 's/"/\\"/g')
    escaped_suggestion=$(printf '%s' "$suggestion" | sed 's/"/\\"/g')
    item="{\"path\": \"${escaped_link}\", \"target\": \"${escaped_target}\", \"suggestion\": \"${escaped_suggestion}\"}"
    if [[ -z "$SYMLINK_JSON_ITEMS" ]]; then
      SYMLINK_JSON_ITEMS="$item"
    else
      SYMLINK_JSON_ITEMS="${SYMLINK_JSON_ITEMS}, ${item}"
    fi
  done <<< "$SYMLINK_RAW"

  # Build branch JSON items
  BRANCH_JSON_ITEMS=""
  while IFS= read -r line; do
    [[ -z "$line" || "$line" == count=* ]] && continue
    branch_name=$(echo "$line" | cut -d'|' -f1)
    days_old=$(echo "$line" | cut -d'|' -f2)
    reason=$(echo "$line" | cut -d'|' -f3)
    if [[ "$reason" == "merged" ]]; then
      suggestion="git branch -d \"${branch_name}\""
    else
      suggestion="git branch -D \"${branch_name}\""
    fi
    escaped_name=$(printf '%s' "$branch_name" | sed 's/"/\\"/g')
    escaped_sugg=$(printf '%s' "$suggestion" | sed 's/"/\\"/g')
    item="{\"branch\": \"${escaped_name}\", \"days_old\": ${days_old}, \"reason\": \"${reason}\", \"suggestion\": \"${escaped_sugg}\"}"
    if [[ -z "$BRANCH_JSON_ITEMS" ]]; then
      BRANCH_JSON_ITEMS="$item"
    else
      BRANCH_JSON_ITEMS="${BRANCH_JSON_ITEMS}, ${item}"
    fi
  done <<< "$BRANCH_RAW"

  # Build changes JSON items
  CHANGES_JSON_ITEMS=""
  while IFS= read -r line; do
    [[ -z "$line" || "$line" == count=* ]] && continue
    category=$(echo "$line" | cut -d'|' -f1)
    file_path=$(echo "$line" | cut -d'|' -f2)
    warning=$(echo "$line" | cut -d'|' -f3)
    escaped_cat=$(printf '%s' "$category" | sed 's/"/\\"/g')
    escaped_file=$(printf '%s' "$file_path" | sed 's/"/\\"/g')
    escaped_warn=$(printf '%s' "$warning" | sed 's/"/\\"/g')
    item="{\"category\": \"${escaped_cat}\", \"path\": \"${escaped_file}\", \"warning\": \"${escaped_warn}\"}"
    if [[ -z "$CHANGES_JSON_ITEMS" ]]; then
      CHANGES_JSON_ITEMS="$item"
    else
      CHANGES_JSON_ITEMS="${CHANGES_JSON_ITEMS}, ${item}"
    fi
  done <<< "$CHANGES_RAW"

  cat <<JSON
{
  "timestamp": "${TIMESTAMP}",
  "checks": {
    "symlinks": { "count": ${SYMLINK_COUNT}, "issues": [${SYMLINK_JSON_ITEMS:-}] },
    "branches": { "count": ${BRANCH_COUNT}, "issues": [${BRANCH_JSON_ITEMS:-}] },
    "changes":  { "count": ${CHANGES_COUNT}, "issues": [${CHANGES_JSON_ITEMS:-}] }
  },
  "total_issues": ${TOTAL_ISSUES}
}
JSON
  exit 0
fi

# ---- Human-readable output ----
echo "=== Git Health Check ==="
echo ""

# Symlinks section
if [[ -z "$CHECK_FILTER" || "$CHECK_FILTER" == "symlinks" ]]; then
  echo "Broken Symlinks: ${SYMLINK_COUNT} issue(s) found"
  if [[ "$SYMLINK_COUNT" -gt 0 ]]; then
    while IFS= read -r line; do
      [[ -z "$line" || "$line" == count=* ]] && continue
      link_path=$(echo "$line" | awk -F' -> ' '{print $1}')
      target=$(echo "$line" | awk -F' -> ' '{print $2}')
      suggestion=$(symlink_suggestion "$link_path" "$target")
      echo "  ${link_path} -> ${target}  [${suggestion}]"
    done <<< "$SYMLINK_RAW"
  fi
  echo ""
fi

# Branches section
if [[ -z "$CHECK_FILTER" || "$CHECK_FILTER" == "branches" ]]; then
  echo "Orphaned Branches: ${BRANCH_COUNT} issue(s) found"
  if [[ "$BRANCH_COUNT" -gt 0 ]]; then
    while IFS= read -r line; do
      [[ -z "$line" || "$line" == count=* ]] && continue
      # Parse pipe-delimited: BRANCH|DAYS|REASON
      branch_name=$(echo "$line" | cut -d'|' -f1)
      days_old=$(echo "$line" | cut -d'|' -f2)
      reason=$(echo "$line" | cut -d'|' -f3)
      suggestion=""
      if [[ "$reason" == "merged" ]]; then
        suggestion="SAFE DELETE: git branch -d \"${branch_name}\""
      else
        suggestion="REVIEW THEN DELETE: git branch -D \"${branch_name}\""
      fi
      echo "  ${branch_name} (last commit: ${days_old} days ago, ${reason})  [${suggestion}]"
    done <<< "$BRANCH_RAW"
  fi
  echo ""
fi

# Changes section
if [[ -z "$CHECK_FILTER" || "$CHECK_FILTER" == "changes" ]]; then
  echo "Uncommitted Changes: ${CHANGES_COUNT} issue(s) found"
  if [[ "$CHANGES_COUNT" -gt 0 ]]; then
    while IFS= read -r line; do
      [[ -z "$line" || "$line" == count=* ]] && continue
      category=$(echo "$line" | cut -d'|' -f1)
      file_path=$(echo "$line" | cut -d'|' -f2)
      warning=$(echo "$line" | cut -d'|' -f3)
      warning_str=""
      [[ -n "$warning" ]] && warning_str="  $warning"
      case "$category" in
        AHEAD)    echo "  [AHEAD]     ${file_path}" ;;
        STAGED)   echo "  [STAGED]    ${file_path}" ;;
        MODIFIED) echo "  [MODIFIED]  ${file_path}  [COMMIT or STASH: git add ${file_path} && git commit]" ;;
        UNTRACKED)echo "  [UNTRACKED] ${file_path}${warning_str}" ;;
      esac
    done <<< "$CHANGES_RAW"
  fi
  echo ""
fi

echo "Summary: ${TOTAL_ISSUES} issue(s) across 3 check(s). Run with --fix to apply suggestions."
