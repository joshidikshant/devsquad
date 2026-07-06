#!/usr/bin/env bash
# DevSquad test runner. Usage: bash test/run.sh
# Plain bash asserts, bash-3 compatible, no network, jq optional
# (the jq-less paths are exercised explicitly by the tests).
set -u

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

total=0
failed=0
for t in "$DIR"/test_*.sh; do
  total=$((total + 1))
  echo "== $(basename "$t")"
  if ! bash "$t"; then
    failed=$((failed + 1))
  fi
  echo ""
done

echo "----------------------------------------"
if [ "$failed" -gt 0 ]; then
  echo "SUITE FAILED: ${failed}/${total} test files had failures"
  exit 1
fi
echo "SUITE PASSED: ${total} test files"
