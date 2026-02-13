#!/usr/bin/env bash
set -o pipefail

# Test script for validating read counter and threshold detection
# Tests DELEG-01, DELEG-02, DELEG-03

PROJECT_ROOT="/Users/Dikshant/Desktop/Projects/devsquad"
export CLAUDE_PROJECT_DIR="$PROJECT_ROOT"
export DEVSQUAD_HOOK_DEPTH=0

HOOK_SCRIPT="${PROJECT_ROOT}/plugin/hooks/scripts/pre-tool-use.sh"
READ_COUNT_FILE="${PROJECT_ROOT}/.devsquad/read_count"
COMPLIANCE_LOG="${PROJECT_ROOT}/.devsquad/logs/compliance.log"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

PASS_COUNT=0
FAIL_COUNT=0

# Helper functions
pass() {
  echo -e "${GREEN}✓ PASS${NC}: $1"
  ((PASS_COUNT++))
}

fail() {
  echo -e "${RED}✗ FAIL${NC}: $1"
  ((FAIL_COUNT++))
}

info() {
  echo -e "${YELLOW}INFO${NC}: $1"
}

# Setup: Reset read counter
reset_counter() {
  mkdir -p "$(dirname "$READ_COUNT_FILE")"
  echo 0 > "$READ_COUNT_FILE"
  info "Reset read counter to 0"
}

# Setup: Ensure log directory exists
setup_logs() {
  mkdir -p "$(dirname "$COMPLIANCE_LOG")"
  touch "$COMPLIANCE_LOG"
}

# Setup: Set enforcement mode to advisory for testing
set_advisory_mode() {
  local config_file="${PROJECT_ROOT}/.devsquad/config.json"
  if [[ -f "$config_file" ]]; then
    # Backup original config
    cp "$config_file" "${config_file}.backup"
    # Set to advisory mode
    if command -v jq &>/dev/null; then
      jq '.enforcement_mode = "advisory"' "$config_file" > "${config_file}.tmp" && mv "${config_file}.tmp" "$config_file"
    fi
    info "Set enforcement mode to advisory"
  fi
}

# Restore original config
restore_config() {
  local config_file="${PROJECT_ROOT}/.devsquad/config.json"
  if [[ -f "${config_file}.backup" ]]; then
    mv "${config_file}.backup" "$config_file"
    info "Restored original config"
  fi
}

# Test function: simulate Read tool call
simulate_read() {
  local file_num=$1
  local input_json=$(cat <<EOF
{"tool_name": "Read", "tool_input": {"file_path": "/tmp/test-file-${file_num}.txt"}}
EOF
)

  echo "$input_json" | bash "$HOOK_SCRIPT" 2>&1
}

# Main test execution
echo "========================================="
echo "Test: Read Counter and Threshold Detection"
echo "========================================="
echo ""

setup_logs
set_advisory_mode
reset_counter

# Trap to ensure config is restored on exit
trap restore_config EXIT

# Test calls 1-3: Should not trigger suggestion
echo "Testing calls 1-3 (below threshold)..."
for i in 1 2 3; do
  info "Simulating Read call $i"
  OUTPUT=$(simulate_read "$i")

  # Check that no permission decision is in output
  if echo "$OUTPUT" | grep -q "permissionDecision"; then
    fail "Call $i should not have suggestion (below threshold)"
  else
    pass "Call $i has no suggestion"
  fi

  # Verify counter incremented
  CURRENT_COUNT=$(cat "$READ_COUNT_FILE")
  if [[ "$CURRENT_COUNT" == "$i" ]]; then
    pass "Counter correctly incremented to $i"
  else
    fail "Counter should be $i but is $CURRENT_COUNT"
  fi
done

echo ""
echo "Testing call 4 (at threshold)..."
OUTPUT=$(simulate_read 4)

# Check for permission decision
if echo "$OUTPUT" | grep -q '"permissionDecision"'; then
  pass "Call 4 has permission decision"

  # Check it's "allow" (advisory mode)
  if echo "$OUTPUT" | grep -q '"permissionDecision"[[:space:]]*:[[:space:]]*"allow"'; then
    pass "Permission decision is 'allow' (advisory mode)"
  else
    fail "Permission decision should be 'allow' in advisory mode"
  fi

  # Check for delegation message
  if echo "$OUTPUT" | grep -q "Delegate bulk reading"; then
    pass "Suggestion contains delegation advice"
  else
    fail "Suggestion should contain 'Delegate bulk reading'"
  fi

  # Check for @gemini-reader command
  if echo "$OUTPUT" | grep -q "@gemini-reader"; then
    pass "Suggestion includes @gemini-reader command"
  else
    fail "Suggestion should include @gemini-reader command"
  fi
else
  fail "Call 4 should have permission decision (at threshold)"
fi

# Verify final counter value
FINAL_COUNT=$(cat "$READ_COUNT_FILE")
if [[ "$FINAL_COUNT" == "4" ]]; then
  pass "Final counter value is 4"
else
  fail "Final counter should be 4 but is $FINAL_COUNT"
fi

# Check compliance log for advisory entry
if grep -q "advisory_suggested" "$COMPLIANCE_LOG"; then
  pass "Compliance log contains advisory_suggested entry"
else
  fail "Compliance log should contain advisory_suggested entry"
fi

# Summary
echo ""
echo "========================================="
echo "Test Summary"
echo "========================================="
echo -e "Passed: ${GREEN}${PASS_COUNT}${NC}"
echo -e "Failed: ${RED}${FAIL_COUNT}${NC}"
echo ""

if [[ $FAIL_COUNT -eq 0 ]]; then
  echo -e "${GREEN}All tests passed!${NC}"
  exit 0
else
  echo -e "${RED}Some tests failed. Review output above.${NC}"
  exit 1
fi
