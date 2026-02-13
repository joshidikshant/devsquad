#!/usr/bin/env bash
# show-status.sh -- Display DevSquad status with token usage, zone, and delegation compliance

set -euo pipefail

# Resolve script directory and plugin root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

# Source required libraries
source "${PLUGIN_ROOT}/lib/state.sh"
source "${PLUGIN_ROOT}/lib/usage.sh"

# Initialize state directory
init_state_dir

# Get usage summary JSON
usage_json=$(get_usage_summary)

# Check if --json flag is passed
if [[ "${1:-}" == "--json" ]]; then
  echo "$usage_json"
  exit 0
fi

# Parse usage JSON for human-readable output
if command -v jq &>/dev/null; then
  # Extract values using jq
  output_tokens=$(echo "$usage_json" | jq -r '.claude.output_tokens // 0')
  message_count=$(echo "$usage_json" | jq -r '.claude.message_count // 0')
  tool_call_count=$(echo "$usage_json" | jq -r '.claude.tool_call_count // 0')
  zone=$(echo "$usage_json" | jq -r '.claude.zone // "unknown"')
  gemini_count=$(echo "$usage_json" | jq -r '.gemini.invocations // 0')
  gemini_chars=$(echo "$usage_json" | jq -r '.gemini.total_response_chars // 0')
  codex_count=$(echo "$usage_json" | jq -r '.codex.invocations // 0')
  codex_chars=$(echo "$usage_json" | jq -r '.codex.total_response_chars // 0')
else
  # Fallback: parse manually
  output_tokens=$(echo "$usage_json" | grep -o '"output_tokens":[[:space:]]*[0-9]*' | grep -o '[0-9]*$' || echo "0")
  message_count=$(echo "$usage_json" | grep -o '"message_count":[[:space:]]*[0-9]*' | grep -o '[0-9]*$' || echo "0")
  tool_call_count=$(echo "$usage_json" | grep -o '"tool_call_count":[[:space:]]*[0-9]*' | grep -o '[0-9]*$' || echo "0")
  zone=$(echo "$usage_json" | grep -o '"zone":[[:space:]]*"[^"]*"' | sed 's/.*"\([^"]*\)".*/\1/' || echo "unknown")
  gemini_count=$(echo "$usage_json" | grep -o '"invocations":[[:space:]]*[0-9]*' | head -1 | grep -o '[0-9]*$' || echo "0")
  gemini_chars=$(echo "$usage_json" | grep -o '"total_response_chars":[[:space:]]*[0-9]*' | head -1 | grep -o '[0-9]*$' || echo "0")
  codex_count=$(echo "$usage_json" | grep -o '"invocations":[[:space:]]*[0-9]*' | tail -1 | grep -o '[0-9]*$' || echo "0")
  codex_chars=$(echo "$usage_json" | grep -o '"total_response_chars":[[:space:]]*[0-9]*' | tail -1 | grep -o '[0-9]*$' || echo "0")
fi

# Calculate percentage of daily budget (200K limit for Red zone)
output_percent=$(echo "scale=1; $output_tokens * 100 / 200000" | bc)

# Determine zone display with uppercase
zone_upper=$(echo "$zone" | tr '[:lower:]' '[:upper:]')

# Get self_calls from state.json
project_dir="${CLAUDE_PROJECT_DIR:-.}"
state_file="${project_dir}/.devsquad/state.json"
self_calls=0
if [[ -f "$state_file" ]]; then
  if command -v jq &>/dev/null; then
    self_calls=$(jq -r '.stats.self_calls // 0' "$state_file" 2>/dev/null || echo "0")
  else
    self_calls=$(grep -o '"self_calls":[[:space:]]*[0-9]*' "$state_file" 2>/dev/null | grep -o '[0-9]*$' || echo "0")
  fi
fi

# Read delegation compliance from logs
delegation_dir="${project_dir}/.devsquad/logs"
suggestions_made=0
overrides=0

if [[ -f "${delegation_dir}/delegation.log" ]]; then
  suggestions_made=$(wc -l < "${delegation_dir}/delegation.log" 2>/dev/null | tr -d ' ' || echo "0")
fi

if [[ -f "${delegation_dir}/compliance.log" ]]; then
  # Count actual overrides (advisory_override events only, not advisory_suggested)
  overrides=$(grep -c 'advisory_override' "${delegation_dir}/compliance.log" 2>/dev/null || echo "0")
fi

# Calculate compliance rate (use bc for floating point precision)
compliance_rate=100
if [[ $suggestions_made -gt 0 ]]; then
  compliance_rate=$(echo "scale=1; 100 - ($overrides * 100 / $suggestions_made)" | bc)
fi

# Check squad availability
gemini_available="not available"
codex_available="not available"

if command -v gemini &>/dev/null; then
  gemini_available="available"
fi

if command -v codex &>/dev/null; then
  codex_available="available"
fi

# Format numbers with commas (simple approach for readability)
format_number() {
  local num="$1"
  # Use printf to add commas to numbers (works for numbers up to 999,999,999)
  if [[ $num -ge 1000000 ]]; then
    printf "%d,%03d,%03d" $((num / 1000000)) $(((num / 1000) % 1000)) $((num % 1000))
  elif [[ $num -ge 1000 ]]; then
    printf "%d,%03d" $((num / 1000)) $((num % 1000))
  else
    printf "%d" "$num"
  fi
}

output_formatted=$(format_number "$output_tokens")
gemini_chars_formatted=$(format_number "$gemini_chars")
codex_chars_formatted=$(format_number "$codex_chars")

# Read enforcement mode from config
config_file="${project_dir}/.devsquad/config.json"
enforcement_mode="advisory"
if [[ -f "$config_file" ]]; then
  if command -v jq &>/dev/null; then
    enforcement_mode=$(jq -r '.enforcement_mode // "advisory"' "$config_file" 2>/dev/null)
  else
    enforcement_mode=$(grep -o '"enforcement_mode"[[:space:]]*:[[:space:]]*"[^"]*"' "$config_file" 2>/dev/null | cut -d'"' -f4)
    enforcement_mode="${enforcement_mode:-advisory}"
  fi
fi

# Build warnings array if strict mode is enabled
warnings=()
if [[ "$enforcement_mode" == "strict" ]]; then
  # Check for jq
  if ! command -v jq &>/dev/null; then
    warnings+=("STRICT MODE DEGRADED: jq not installed. Strict enforcement requires jq to emit deny responses. Currently falling back to advisory mode. Install jq to restore full strict enforcement.")
  fi
  # Check for gemini CLI
  if ! command -v gemini &>/dev/null; then
    warnings+=("STRICT MODE DEGRADED: gemini CLI not installed. Delegation to Gemini agents will fall back to advisory.")
  fi
  # Check for codex CLI
  if ! command -v codex &>/dev/null; then
    warnings+=("STRICT MODE DEGRADED: codex CLI not installed. Delegation to Codex agents will fall back to advisory.")
  fi
fi

# Display formatted status
cat <<STATUS

=== DevSquad Status ===

Claude Usage (today):
  Zone: ${zone_upper} (${output_percent}%)
  Output tokens: ${output_formatted}
  Messages: ${message_count}
  Tool calls: ${tool_call_count}

Squad Usage (this session):
  Gemini: ${gemini_count} invocations, ~${gemini_chars_formatted} chars response
  Codex:  ${codex_count} invocations, ~${codex_chars_formatted} chars response
  Self:   ${self_calls} calls

Delegation Compliance:
  Suggestions made: ${suggestions_made}
  User overrides: ${overrides}
  Compliance rate: ${compliance_rate}%

Squad Availability:
  Gemini CLI: ${gemini_available}
  Codex CLI:  ${codex_available}

STATUS

# Display warnings section if any warnings exist
if [[ ${#warnings[@]} -gt 0 ]]; then
  echo "Warnings:"
  for warning in "${warnings[@]}"; do
    echo "  ! ${warning}"
  done
  echo
fi
