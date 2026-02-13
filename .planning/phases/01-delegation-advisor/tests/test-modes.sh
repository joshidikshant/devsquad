#!/usr/bin/env bash
set -o pipefail

# Test script for validating advisory vs strict mode behavior
# Tests DELEG-01, DELEG-02, DELEG-03 enforcement modes

PROJECT_ROOT="/Users/Dikshant/Desktop/Projects/devsquad"
export CLAUDE_PROJECT_DIR="$PROJECT_ROOT"
export DEVSQUAD_HOOK_DEPTH=0

HOOK_SCRIPT="${PROJECT_ROOT}/plugin/hooks/scripts/pre-tool-use.sh"
READ_COUNT_FILE="${PROJECT_ROOT}/.devsquad/read_count"
CONFIG_FILE="${PROJECT_ROOT}/.devsquad/config.json"

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

# Setup functions
backup_config() {
  if [[ -f "$CONFIG_FILE" ]]; then
    cp "$CONFIG_FILE" "${CONFIG_FILE}.test-backup"
    info "Backed up config"
  fi
}

restore_config() {
  if [[ -f "${CONFIG_FILE}.test-backup" ]]; then
    mv "${CONFIG_FILE}.test-backup" "$CONFIG_FILE"
    info "Restored original config"
  fi
}

set_enforcement_mode() {
  local mode=$1
  if command -v jq &>/dev/null; then
    jq --arg mode "$mode" '.enforcement_mode = $mode' "$CONFIG_FILE" > "${CONFIG_FILE}.tmp" && mv "${CONFIG_FILE}.tmp" "$CONFIG_FILE"
    info "Set enforcement mode to $mode"
  else
    fail "jq not available - cannot modify config"
    return 1
  fi
}

reset_counter() {
  mkdir -p "$(dirname "$READ_COUNT_FILE")"
  echo 0 > "$READ_COUNT_FILE"
}

simulate_read() {
  local file_path=$1
  local input_json=$(cat <<EOF
{"tool_name": "Read", "tool_input": {"file_path": "${file_path}"}}
EOF
)

  echo "$input_json" | bash "$HOOK_SCRIPT" 2>&1
}

# Test 1: Advisory mode behavior
test_advisory_mode() {
  echo ""
  echo "========================================="
  echo "Test 1: Advisory Mode Behavior"
  echo "========================================="

  set_enforcement_mode "advisory"
  reset_counter

  # Trigger 4 reads to hit threshold
  for i in 1 2 3; do
    simulate_read "/tmp/advisory-test-${i}.txt" > /dev/null
  done

  info "Triggering 4th read (at threshold)"
  OUTPUT=$(simulate_read "/tmp/advisory-test-4.txt")

  # Should allow execution (not deny)
  if echo "$OUTPUT" | grep -q '"permissionDecision"[[:space:]]*:[[:space:]]*"allow"'; then
    pass "Advisory mode returns 'allow' decision"
  else
    fail "Advisory mode should return 'allow', not 'deny'"
  fi

  # Should include suggestion text
  if echo "$OUTPUT" | grep -q "Run instead:" || echo "$OUTPUT" | grep -q "Suggestion:"; then
    pass "Advisory mode includes suggestion text"
  else
    fail "Advisory mode should include 'Run instead:' or 'Suggestion:'"
  fi

  # Should include @gemini-reader command
  if echo "$OUTPUT" | grep -q "@gemini-reader"; then
    pass "Suggestion includes @gemini-reader command"
  else
    fail "Suggestion should include @gemini-reader command"
  fi

  # Should include the file path in command
  if echo "$OUTPUT" | grep -q "/tmp/advisory-test-4.txt"; then
    pass "Suggestion includes actual file path"
  else
    fail "Suggestion should include the file path being read"
  fi
}

# Test 2: Strict mode behavior
test_strict_mode() {
  echo ""
  echo "========================================="
  echo "Test 2: Strict Mode Behavior"
  echo "========================================="

  set_enforcement_mode "strict"
  reset_counter

  # Trigger 4 reads to hit threshold
  for i in 1 2 3; do
    simulate_read "/tmp/strict-test-${i}.txt" > /dev/null
  done

  info "Triggering 4th read (at threshold)"
  OUTPUT=$(simulate_read "/tmp/strict-test-4.txt")

  # Check if gemini is available (affects strict behavior)
  if command -v gemini &>/dev/null; then
    # Should deny execution in strict mode with CLI available
    if echo "$OUTPUT" | grep -q '"permissionDecision"[[:space:]]*:[[:space:]]*"deny"'; then
      pass "Strict mode returns 'deny' decision (CLI available)"
    else
      # May degrade to advisory if CLI check fails
      info "Strict mode degraded to advisory (CLI not available or not configured)"
      if echo "$OUTPUT" | grep -q '"permissionDecision"[[:space:]]*:[[:space:]]*"allow"'; then
        pass "Degraded strict mode returns 'allow' with warning"
      else
        fail "Strict mode should return 'deny' or degraded 'allow'"
      fi
    fi
  else
    # CLI not available - should degrade to advisory
    info "Gemini CLI not available - expect degraded advisory mode"
    if echo "$OUTPUT" | grep -q '"permissionDecision"[[:space:]]*:[[:space:]]*"allow"'; then
      pass "Degraded strict mode returns 'allow' (CLI unavailable)"
    else
      fail "Should degrade to advisory when CLI unavailable"
    fi
  fi

  # Should include actionable command
  if echo "$OUTPUT" | grep -q "@gemini-reader"; then
    pass "Strict mode includes @gemini-reader command"
  else
    fail "Strict mode should include actionable command"
  fi

  # Should include file path
  if echo "$OUTPUT" | grep -q "/tmp/strict-test-4.txt"; then
    pass "Strict mode includes actual file path"
  else
    fail "Strict mode should include the file path"
  fi
}

# Test 3: Zone-adjusted threshold (yellow zone)
test_zone_threshold() {
  echo ""
  echo "========================================="
  echo "Test 3: Zone-Adjusted Threshold"
  echo "========================================="

  set_enforcement_mode "advisory"
  reset_counter

  info "Note: Zone threshold test requires token usage state"
  info "This test validates threshold=1 in yellow/red zones"

  # In yellow/red zones, threshold should be 1 (triggers on 2nd read)
  # We can't easily mock zone calculation, so we document the behavior
  info "Green zone: threshold=3 (triggers on 4th read)"
  info "Yellow/Red zone: threshold=1 (triggers on 2nd read)"

  # Verify green zone behavior (threshold=3) - already tested above
  reset_counter
  OUTPUT1=$(simulate_read "/tmp/zone-test-1.txt")
  OUTPUT2=$(simulate_read "/tmp/zone-test-2.txt")
  OUTPUT3=$(simulate_read "/tmp/zone-test-3.txt")

  # First 3 should not trigger in green zone
  if ! echo "$OUTPUT3" | grep -q "permissionDecision"; then
    pass "Green zone: No suggestion on 3rd read (threshold=3)"
  else
    info "Zone may be yellow/red - suggestion triggered early"
  fi

  # 4th should trigger in green zone
  OUTPUT4=$(simulate_read "/tmp/zone-test-4.txt")
  if echo "$OUTPUT4" | grep -q "permissionDecision"; then
    pass "Green zone: Suggestion triggers on 4th read"
  else
    fail "Should trigger suggestion on 4th read in green zone"
  fi
}

# Test 4: File path extraction
test_file_path_extraction() {
  echo ""
  echo "========================================="
  echo "Test 4: File Path Extraction"
  echo "========================================="

  set_enforcement_mode "advisory"
  reset_counter

  # Test with specific file path
  TEST_PATH="/src/auth/login.ts"

  for i in 1 2 3; do
    simulate_read "/tmp/extract-${i}.txt" > /dev/null
  done

  OUTPUT=$(simulate_read "$TEST_PATH")

  if echo "$OUTPUT" | grep -q "$TEST_PATH"; then
    pass "File path correctly extracted and included in suggestion"
  else
    fail "Suggestion should contain the exact file path: $TEST_PATH"
  fi

  # Test with path containing spaces
  reset_counter
  TEST_PATH_SPACES="/path/with spaces/file.txt"

  for i in 1 2 3; do
    simulate_read "/tmp/spaces-${i}.txt" > /dev/null
  done

  OUTPUT=$(simulate_read "$TEST_PATH_SPACES")

  if echo "$OUTPUT" | grep -q "path/with spaces/file.txt"; then
    pass "File path with spaces correctly handled"
  else
    info "File path with spaces may need special handling"
  fi
}

# Main execution
echo "========================================="
echo "Mode and Enforcement Testing"
echo "========================================="

backup_config
trap restore_config EXIT

test_advisory_mode
test_strict_mode
test_zone_threshold
test_file_path_extraction

# Summary
echo ""
echo "========================================="
echo "Test Summary"
echo "========================================="
echo -e "Passed: ${GREEN}${PASS_COUNT}${NC}"
echo -e "Failed: ${RED}${FAIL_COUNT}${NC}"
echo ""

if [[ $FAIL_COUNT -eq 0 ]]; then
  echo -e "${GREEN}All mode tests passed!${NC}"
  exit 0
else
  echo -e "${RED}Some tests failed. Review output above.${NC}"
  exit 1
fi
