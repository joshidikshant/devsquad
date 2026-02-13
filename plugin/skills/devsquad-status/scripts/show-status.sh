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
# Ensure output_tokens is numeric and not empty
output_tokens=${output_tokens:-0}
if [[ "$output_tokens" =~ ^[0-9]+$ ]]; then
  output_percent=$(echo "scale=1; $output_tokens * 100 / 200000" | bc)
else
  output_percent=0
fi

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
  suggestions_made=$(wc -l < "${delegation_dir}/delegation.log" 2>/dev/null | tr -d ' \n' || echo "0")
fi

if [[ -f "${delegation_dir}/compliance.log" ]]; then
  # Count actual overrides (advisory_override events only, not advisory_suggested)
  overrides=$(grep -c 'advisory_override' "${delegation_dir}/compliance.log" 2>/dev/null | tr -d ' \n' || echo "0")
fi

# Sanitize numeric values (remove any whitespace/newlines)
suggestions_made=$(echo "$suggestions_made" | tr -d ' \n')
overrides=$(echo "$overrides" | tr -d ' \n')

# Calculate compliance rate (use bc for floating point precision)
compliance_rate=100
if [[ $suggestions_made -gt 0 ]] && [[ $overrides =~ ^[0-9]+$ ]]; then
  compliance_rate=$(echo "scale=1; 100 - ($overrides * 100 / $suggestions_made)" | bc)
fi

# Read cached capacity data (user-reported via /devsquad:capacity)
capacity_json=$(read_capacity_cache)

if command -v jq &>/dev/null; then
  claude_pct=$(echo "$capacity_json" | jq -r '.claude_pct // 0')
  gemini_pct=$(echo "$capacity_json" | jq -r '.gemini_pct // 0')
  codex_5hr=$(echo "$capacity_json" | jq -r '.codex_5hr_pct // 0')
  codex_weekly=$(echo "$capacity_json" | jq -r '.codex_weekly_pct // 0')
  capacity_stale=$(echo "$capacity_json" | jq -r 'if .stale == null then "true" else (.stale | tostring) end')
  capacity_age=$(echo "$capacity_json" | jq -r '.age_seconds // 0')
  capacity_ts=$(echo "$capacity_json" | jq -r '.timestamp // "never"')
else
  claude_pct=0; gemini_pct=0; codex_5hr=0; codex_weekly=0
  capacity_stale="true"; capacity_age=0; capacity_ts="never"
fi

# Format age as human-readable
if [[ "$capacity_ts" == "never" ]]; then
  capacity_age_str="never reported"
elif [[ "$capacity_stale" == "true" ]]; then
  capacity_age_min=$(( capacity_age / 60 ))
  capacity_age_str="${capacity_age_min}m ago (STALE)"
else
  capacity_age_min=$(( capacity_age / 60 ))
  capacity_age_str="${capacity_age_min}m ago"
fi

# Check squad availability
gemini_available_str="❌ not available"
codex_available_str="❌ not available"
if command -v gemini &>/dev/null; then
  gemini_available_str="✅ available"
fi
if command -v codex &>/dev/null; then
  codex_available_str="✅ available"
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

# Determine capacity zones
claude_zone_icon="🟢"
gemini_zone_icon="🟢"
codex_zone_icon="🟢"

if [[ $claude_pct -ge 75 ]]; then claude_zone_icon="🔴"
elif [[ $claude_pct -ge 50 ]]; then claude_zone_icon="🟡"; fi

if [[ $gemini_pct -ge 80 ]]; then gemini_zone_icon="🔴"
elif [[ $gemini_pct -ge 50 ]]; then gemini_zone_icon="🟡"; fi

if [[ $codex_5hr -ge 80 ]]; then codex_zone_icon="🔴"
elif [[ $codex_5hr -ge 50 ]]; then codex_zone_icon="🟡"; fi

# Generate recommendation
recommendation=""
if [[ "$capacity_ts" == "never" ]]; then
  recommendation="Run /devsquad:capacity to report your CLI usage for smart delegation"
elif [[ "$capacity_stale" == "true" ]]; then
  recommendation="Capacity data is stale — run /devsquad:capacity to refresh"
elif [[ "$claude_zone_icon" == "🔴" ]]; then
  recommendation="CRITICAL: Claude at ${claude_pct}% — delegate ALL work to Gemini/Codex"
elif [[ "$claude_zone_icon" == "🟡" ]]; then
  if [[ "$gemini_zone_icon" == "🟢" ]]; then
    recommendation="Claude at ${claude_pct}% — route research to Gemini (${gemini_pct}% used)"
  elif [[ "$codex_zone_icon" == "🟢" ]]; then
    recommendation="Claude at ${claude_pct}% — route code generation to Codex (${codex_5hr}% used)"
  else
    recommendation="All agents above 50% — proceed carefully, synthesis only"
  fi
elif [[ "$gemini_zone_icon" == "🔴" ]]; then
  recommendation="Gemini at capacity — use Codex or Haiku subagents for delegation"
else
  recommendation="All systems green — normal operation"
fi

# Display formatted status
cat <<STATUS

╔══════════════════════════════════════════════════════════════════════════════╗
║                           DevSquad Status Report                            ║
╚══════════════════════════════════════════════════════════════════════════════╝

📊 Capacity (reported ${capacity_age_str})
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ${claude_zone_icon} Claude:  ${claude_pct}% used
  ${gemini_zone_icon} Gemini:  ${gemini_pct}% used
  ${codex_zone_icon} Codex:   ${codex_5hr}% (5hr) | ${codex_weekly}% (weekly)

📈 Session Activity
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Claude:  ${output_formatted} output tokens | ${message_count} msgs | ${tool_call_count} tool calls
  Gemini:  ${gemini_count} invocations | ~${gemini_chars_formatted} chars
  Codex:   ${codex_count} invocations | ~${codex_chars_formatted} chars

💼 Engineering Manager
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Direct work:      ${self_calls} self-calls
  Delegation rate:  ${compliance_rate}% compliant (${suggestions_made} suggestions, ${overrides} overrides)

🛠️  Squad
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Gemini CLI:  ${gemini_available_str}
  Codex CLI:   ${codex_available_str}

🎯 ${recommendation}

STATUS

# Display warnings section if any warnings exist
if [[ ${#warnings[@]} -gt 0 ]]; then
  echo "Warnings:"
  for warning in "${warnings[@]}"; do
    echo "  ! ${warning}"
  done
  echo
fi
