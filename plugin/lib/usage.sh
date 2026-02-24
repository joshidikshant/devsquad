#!/usr/bin/env bash
# lib/usage.sh -- Usage tracking and zone calculation for DevSquad
# Sourced by hooks and other scripts. Do not execute directly.

# Record an agent invocation to usage files
# Usage: record_usage agent_name prompt_chars response_chars
# Creates/appends to .devsquad/usage/{agent}.json (or .jsonl as fallback)
record_usage() {
  local agent="$1"
  local prompt_len="$2"
  local response_len="$3"

  local project_dir="${CLAUDE_PROJECT_DIR:-.}"
  local usage_dir="${project_dir}/.devsquad/usage"
  local timestamp
  timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

  # Ensure usage directory exists
  mkdir -p "$usage_dir"

  # Create usage record object
  local record
  record="{\"timestamp\":\"${timestamp}\",\"prompt_chars\":${prompt_len},\"response_chars\":${response_len}}"

  if command -v jq &>/dev/null; then
    # jq available: maintain proper JSON array with atomic writes
    local usage_file="${usage_dir}/${agent}.json"
    local temp_file="${usage_file}.tmp.$$"

    if [[ -f "$usage_file" ]]; then
      # Append to existing array
      jq --argjson rec "$record" '. += [$rec]' "$usage_file" > "$temp_file"
    else
      # Create new array
      echo "[$record]" > "$temp_file"
    fi

    mv "$temp_file" "$usage_file"
  else
    # No jq: use JSONL (newline-delimited JSON) as fallback
    local usage_file="${usage_dir}/${agent}.jsonl"
    echo "$record" >> "$usage_file"
  fi
}

# Read Claude's stats cache file for today's metrics
# Returns JSON object with today's token counts and activity, or zeros if unavailable
# Output: {"input_tokens":0,"output_tokens":N,"cache_read_tokens":0,"cache_write_tokens":0,"message_count":N,"tool_call_count":N}
# Note: input_tokens and cache tokens are not available in daily stats (always 0)
read_claude_stats() {
  local stats_file="${HOME}/.claude/stats-cache.json"

  if [[ ! -f "$stats_file" ]]; then
    echo '{"input_tokens":0,"output_tokens":0,"cache_read_tokens":0,"cache_write_tokens":0,"message_count":0,"tool_call_count":0}'
    return
  fi

  local today
  today=$(date +%Y-%m-%d)

  if command -v jq &>/dev/null; then
    # Get today's output tokens (sum all models for today)
    local output_tokens
    output_tokens=$(jq --arg d "$today" '
      [.dailyModelTokens[] | select(.date == $d) | .tokensByModel | to_entries[].value] | add // 0
    ' "$stats_file" 2>/dev/null || echo "0")

    # Get today's activity metrics
    local message_count tool_call_count
    message_count=$(jq --arg d "$today" '
      [.dailyActivity[] | select(.date == $d) | .messageCount] | add // 0
    ' "$stats_file" 2>/dev/null || echo "0")
    tool_call_count=$(jq --arg d "$today" '
      [.dailyActivity[] | select(.date == $d) | .toolCallCount] | add // 0
    ' "$stats_file" 2>/dev/null || echo "0")

    echo "{\"input_tokens\":0,\"output_tokens\":${output_tokens},\"cache_read_tokens\":0,\"cache_write_tokens\":0,\"message_count\":${message_count},\"tool_call_count\":${tool_call_count}}"
  else
    # No jq: use grep+awk to extract today's values
    # Find today's section in dailyModelTokens and sum token values
    local output_tokens=0
    local section
    section=$(awk -v d="$today" '
      $0 ~ "\"date\".*\"" d "\"" { found=1; next }
      found && /tokensByModel/ { in_tokens=1; next }
      found && in_tokens && /[0-9]+/ { gsub(/[^0-9]/, ""); if($0+0 > 0) sum+=$0+0 }
      found && /\}/ && in_tokens { in_tokens=0 }
      found && /\}/ && !in_tokens { found=0 }
      END { print sum+0 }
    ' "$stats_file" 2>/dev/null)
    output_tokens="${section:-0}"

    # Get message count and tool call count from dailyActivity for today
    local message_count=0
    local tool_call_count=0
    local activity_section
    activity_section=$(awk -v d="$today" '
      $0 ~ "\"date\".*\"" d "\"" && /messageCount/ {
        gsub(/.*messageCount.*:/, ""); gsub(/[^0-9]/, ""); mc=$0+0
        getline; getline; gsub(/.*toolCallCount.*:/, ""); gsub(/[^0-9]/, ""); tc=$0+0
        print mc, tc; exit
      }
    ' "$stats_file" 2>/dev/null)
    if [[ -n "$activity_section" ]]; then
      message_count=$(echo "$activity_section" | awk '{print $1}')
      tool_call_count=$(echo "$activity_section" | awk '{print $2}')
    fi

    echo "{\"input_tokens\":0,\"output_tokens\":${output_tokens},\"cache_read_tokens\":0,\"cache_write_tokens\":0,\"message_count\":${message_count:-0},\"tool_call_count\":${tool_call_count:-0}}"
  fi
}

# Calculate budget zone based on daily output token volume
# Usage: calculate_zone input_tokens output_tokens
# Note: input_tokens is unused (not available in stats-cache.json)
# Zone thresholds based on daily output volume:
#   green: < 100K output tokens today
#   yellow: 100K-200K output tokens today
#   red: > 200K output tokens today
calculate_zone() {
  local input_tokens="${1:-0}"
  local output_tokens="${2:-0}"

  if [[ $output_tokens -lt 100000 ]]; then
    echo "green"
  elif [[ $output_tokens -lt 200000 ]]; then
    echo "yellow"
  else
    echo "red"
  fi
}

# Get comprehensive usage summary
# Returns JSON summary of Claude, Gemini, and Codex usage
get_usage_summary() {
  local project_dir="${CLAUDE_PROJECT_DIR:-.}"
  local usage_dir="${project_dir}/.devsquad/usage"

  # Read Claude stats
  local claude_stats
  claude_stats=$(read_claude_stats)

  if command -v jq &>/dev/null; then
    # Extract Claude token counts and activity metrics
    local input_tokens output_tokens message_count tool_call_count
    input_tokens=$(echo "$claude_stats" | jq -r '.input_tokens // 0')
    output_tokens=$(echo "$claude_stats" | jq -r '.output_tokens // 0')
    message_count=$(echo "$claude_stats" | jq -r '.message_count // 0')
    tool_call_count=$(echo "$claude_stats" | jq -r '.tool_call_count // 0')

    # Calculate zone
    local zone
    zone=$(calculate_zone "$input_tokens" "$output_tokens")

    # Read agent usage stats from JSON or JSONL files
    local gemini_count=0 gemini_total=0 gemini_input=0
    local codex_count=0 codex_total=0 codex_input=0

    for _agent in gemini codex; do
      local _count=0 _total=0 _input=0
      if [[ -f "${usage_dir}/${_agent}.json" ]]; then
        _count=$(jq 'length' "${usage_dir}/${_agent}.json" 2>/dev/null || echo "0")
        _total=$(jq '[.[].response_chars] | add // 0' "${usage_dir}/${_agent}.json" 2>/dev/null || echo "0")
        _input=$(jq '[.[].prompt_chars] | add // 0 | . / 4 | floor' "${usage_dir}/${_agent}.json" 2>/dev/null || echo "0")
      elif [[ -f "${usage_dir}/${_agent}.jsonl" ]]; then
        _count=$(wc -l < "${usage_dir}/${_agent}.jsonl" 2>/dev/null | tr -d ' ' || echo "0")
        _total=$(awk -F'[,:}]' '{for(i=1;i<=NF;i++){if($i~/response_chars/){print $(i+1)}}}' "${usage_dir}/${_agent}.jsonl" 2>/dev/null | awk '{s+=$1}END{print s+0}')
        _input=$(awk -F'[,:}]' '{for(i=1;i<=NF;i++){if($i~/prompt_chars/){print $(i+1)}}}' "${usage_dir}/${_agent}.jsonl" 2>/dev/null | awk '{s+=$1}END{print int(s/4)}')
      fi
      eval "${_agent}_count=\$_count; ${_agent}_total=\$_total; ${_agent}_input=\$_input"
    done

    # Build JSON summary
    cat <<USAGE_JSON
{
  "claude": {
    "input_tokens": ${input_tokens},
    "output_tokens": ${output_tokens},
    "message_count": ${message_count},
    "tool_call_count": ${tool_call_count},
    "zone": "${zone}"
  },
  "gemini": {
    "invocations": ${gemini_count},
    "input_tokens": ${gemini_input},
    "total_response_chars": ${gemini_total}
  },
  "codex": {
    "invocations": ${codex_count},
    "input_tokens": ${codex_input},
    "total_response_chars": ${codex_total}
  }
}
USAGE_JSON
  else
    # No jq: return simplified text format
    local input_tokens output_tokens
    input_tokens=$(echo "$claude_stats" | grep -o '"input_tokens":[0-9]*' | grep -o '[0-9]*$' || echo "0")
    output_tokens=$(echo "$claude_stats" | grep -o '"output_tokens":[0-9]*' | grep -o '[0-9]*$' || echo "0")

    local zone
    zone=$(calculate_zone "$input_tokens" "$output_tokens")

    # Count lines for invocations (simplified)
    local gemini_count=0
    local codex_count=0
    if [[ -f "${usage_dir}/gemini.jsonl" ]]; then
      gemini_count=$(wc -l < "${usage_dir}/gemini.jsonl" 2>/dev/null | tr -d ' ' || echo "0")
    fi
    if [[ -f "${usage_dir}/codex.jsonl" ]]; then
      codex_count=$(wc -l < "${usage_dir}/codex.jsonl" 2>/dev/null | tr -d ' ' || echo "0")
    fi

    cat <<USAGE_TEXT
Claude: ${input_tokens} input, ${output_tokens} output tokens (zone: ${zone})
Gemini: ${gemini_count} invocations
Codex: ${codex_count} invocations
USAGE_TEXT
  fi
}

# Capacity cache file location
# Stores user-reported usage percentages from CLI /stats and /status commands
# Format: {"claude_pct":32,"gemini_pct":9,"codex_5hr_pct":3,"codex_weekly_pct":5,"timestamp":"2026-02-13T18:30:00Z"}
CAPACITY_CACHE_FILE="${CLAUDE_PROJECT_DIR:-.}/.devsquad/capacity.json"
CAPACITY_STALE_MINUTES=30

# Save user-reported capacity to cache
# Usage: save_capacity claude_pct gemini_pct codex_5hr_pct codex_weekly_pct
save_capacity() {
  local claude_pct="${1:-0}"
  local gemini_pct="${2:-0}"
  local codex_5hr_pct="${3:-0}"
  local codex_weekly_pct="${4:-0}"
  local timestamp
  timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

  local project_dir="${CLAUDE_PROJECT_DIR:-.}"
  mkdir -p "${project_dir}/.devsquad"

  if command -v jq &>/dev/null; then
    jq -n \
      --argjson claude "$claude_pct" \
      --argjson gemini "$gemini_pct" \
      --argjson codex_5hr "$codex_5hr_pct" \
      --argjson codex_weekly "$codex_weekly_pct" \
      --arg ts "$timestamp" \
      '{claude_pct:$claude, gemini_pct:$gemini, codex_5hr_pct:$codex_5hr, codex_weekly_pct:$codex_weekly, timestamp:$ts}' \
      > "$CAPACITY_CACHE_FILE"
  else
    cat > "$CAPACITY_CACHE_FILE" <<CACHE
{"claude_pct":${claude_pct},"gemini_pct":${gemini_pct},"codex_5hr_pct":${codex_5hr_pct},"codex_weekly_pct":${codex_weekly_pct},"timestamp":"${timestamp}"}
CACHE
  fi
}

# Read cached capacity data
# Returns JSON from cache file, or defaults with stale=true if expired/missing
read_capacity_cache() {
  local project_dir="${CLAUDE_PROJECT_DIR:-.}"
  local cache_file="${project_dir}/.devsquad/capacity.json"

  if [[ ! -f "$cache_file" ]]; then
    echo '{"claude_pct":0,"gemini_pct":0,"codex_5hr_pct":0,"codex_weekly_pct":0,"timestamp":"never","stale":true}'
    return
  fi

  # Check staleness (older than CAPACITY_STALE_MINUTES)
  local file_mtime
  file_mtime=$(stat -f%m "$cache_file" 2>/dev/null || stat -c%Y "$cache_file" 2>/dev/null || echo "0")
  local file_age_seconds=$(( $(date +%s) - file_mtime ))
  local stale_threshold=$(( CAPACITY_STALE_MINUTES * 60 ))

  local is_stale="false"
  [[ $file_age_seconds -gt $stale_threshold ]] && is_stale="true"

  if command -v jq &>/dev/null; then
    jq --argjson stale "$is_stale" --argjson age "$file_age_seconds" \
      '. + {stale: $stale, age_seconds: $age}' "$cache_file"
  else
    # Simple fallback: read file, append stale field
    local content
    content=$(cat "$cache_file")
    echo "${content%\}},\"stale\":${is_stale},\"age_seconds\":${file_age_seconds}}"
  fi
}

# Get capacity-aware delegation recommendation
# Returns: "green", "yellow", or "red" for each agent
# Usage: get_delegation_recommendation
get_delegation_recommendation() {
  local capacity
  capacity=$(read_capacity_cache)

  if command -v jq &>/dev/null; then
    local claude_pct gemini_pct codex_5hr_pct is_stale
    claude_pct=$(echo "$capacity" | jq -r '.claude_pct // 0')
    gemini_pct=$(echo "$capacity" | jq -r '.gemini_pct // 0')
    codex_5hr_pct=$(echo "$capacity" | jq -r '.codex_5hr_pct // 0')
    is_stale=$(echo "$capacity" | jq -r '.stale // true')

    local claude_zone="green" gemini_zone="green" codex_zone="green"

    if [[ $claude_pct -ge 75 ]]; then claude_zone="red"
    elif [[ $claude_pct -ge 50 ]]; then claude_zone="yellow"; fi

    if [[ $gemini_pct -ge 80 ]]; then gemini_zone="red"
    elif [[ $gemini_pct -ge 50 ]]; then gemini_zone="yellow"; fi

    if [[ $codex_5hr_pct -ge 80 ]]; then codex_zone="red"
    elif [[ $codex_5hr_pct -ge 50 ]]; then codex_zone="yellow"; fi

    cat <<REC_JSON
{
  "claude": {"pct": ${claude_pct}, "zone": "${claude_zone}"},
  "gemini": {"pct": ${gemini_pct}, "zone": "${gemini_zone}"},
  "codex": {"pct": ${codex_5hr_pct}, "zone": "${codex_zone}"},
  "stale": ${is_stale},
  "recommendation": "$(
    if [[ "$claude_zone" == "red" ]]; then
      echo "CRITICAL: Claude at ${claude_pct}% — delegate ALL work to Gemini/Codex"
    elif [[ "$claude_zone" == "yellow" && "$gemini_zone" == "green" ]]; then
      echo "Claude at ${claude_pct}% — route research to Gemini (${gemini_pct}% used)"
    elif [[ "$claude_zone" == "yellow" && "$codex_zone" == "green" ]]; then
      echo "Claude at ${claude_pct}% — route code generation to Codex (${codex_5hr_pct}% used)"
    elif [[ "$claude_zone" == "yellow" ]]; then
      echo "All agents above 50% — proceed carefully, synthesis only"
    else
      echo "All systems green — normal operation"
    fi
  )"
}
REC_JSON
  else
    echo '{"claude":{"pct":0,"zone":"green"},"gemini":{"pct":0,"zone":"green"},"codex":{"pct":0,"zone":"green"},"stale":true,"recommendation":"Run /devsquad:capacity to report usage"}'
  fi
}

# Reset usage tracking for new session
# Creates session start marker without deleting historical data
reset_usage_session() {
  local project_dir="${CLAUDE_PROJECT_DIR:-.}"
  local usage_dir="${project_dir}/.devsquad/usage"
  local timestamp
  timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

  mkdir -p "$usage_dir"
  echo "$timestamp" > "${usage_dir}/session_start"
}
