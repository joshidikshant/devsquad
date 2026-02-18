#!/usr/bin/env bash
# check-symlinks.sh -- Find broken symlinks in the repository
# Outputs: one line per broken symlink: LINK_PATH -> TARGET_PATH
# Called by git-health.sh with PROJECT_DIR set as first argument

set -euo pipefail

PROJECT_DIR="${1:-.}"

# Resolve to absolute path
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"

# Find all symlinks whose targets do not exist
# Using find -L to follow symlinks and -type l to find dangling ones
# This works on both macOS and Linux
broken_count=0
broken_links=""

# find without -L only lists symlinks; with -L, broken ones show as type l
# We use: find symlinks (-maxdepth for safety), then test each target
while IFS= read -r link; do
  # Skip .git internals to avoid false positives with git pack symlinks
  case "$link" in
    */.git/objects/*) continue ;;
    */.git/refs/*)    continue ;;
  esac

  target=$(readlink "$link" 2>/dev/null || echo "")
  if [[ -z "$target" ]]; then
    continue
  fi

  # Resolve relative targets relative to the symlink's directory
  link_dir="$(dirname "$link")"
  if [[ "$target" != /* ]]; then
    resolved="${link_dir}/${target}"
  else
    resolved="$target"
  fi

  if [[ ! -e "$resolved" ]]; then
    broken_count=$((broken_count + 1))
    if [[ -z "$broken_links" ]]; then
      broken_links="${link} -> ${target}"
    else
      broken_links="${broken_links}
${link} -> ${target}"
    fi
  fi
done < <(find "$PROJECT_DIR" -maxdepth 10 -type l 2>/dev/null)

# Output structured result
echo "count=${broken_count}"
if [[ -n "$broken_links" ]]; then
  echo "$broken_links"
fi
