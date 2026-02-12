#!/usr/bin/env bash
# skills/environment-detection/scripts/detect-plugins.sh
# Discovers installed Claude Code plugins and outputs a JSON report to stdout.
# All warnings/debug messages go to stderr. Only JSON goes to stdout.
# Compatible with bash 3+ (no associative arrays).
set -euo pipefail

PLUGINS_FILE="$HOME/.claude/plugins/installed_plugins.json"
GSD_DIR="$HOME/.claude/get-shit-done"
PLUGIN_CACHE_DIR="$HOME/.claude/plugins/cache"

jq_available="false"
if command -v jq &>/dev/null; then
  jq_available="true"
fi

# --- Discover plugins ---
# Use a newline-delimited string to track unique plugin names (bash 3 compatible)
seen_names=""

# Helper: check if name is already seen
is_seen() {
  local name="$1"
  echo "$seen_names" | grep -q "^${name}$" 2>/dev/null
}

# Helper: add name if not seen
add_seen() {
  local name="$1"
  if ! is_seen "$name"; then
    if [[ -z "$seen_names" ]]; then
      seen_names="$name"
    else
      seen_names="${seen_names}
${name}"
    fi
  fi
}

# Check GSD specifically
if [[ -d "$GSD_DIR" ]]; then
  add_seen "gsd"
fi

# Check installed_plugins.json
registry_plugins=""
if [[ -f "$PLUGINS_FILE" && "$jq_available" == "true" ]]; then
  registry_plugins=$(jq -r '.plugins // {} | keys[]' "$PLUGINS_FILE" 2>/dev/null || true)
  if [[ -n "$registry_plugins" ]]; then
    while IFS= read -r pname; do
      [[ -z "$pname" ]] && continue
      add_seen "$pname"
    done <<< "$registry_plugins"
  fi
fi

# Check known plugin directories in cache
known_plugins="superpowers gsd tribe claude-engineer cline"
if [[ -d "$PLUGIN_CACHE_DIR" ]]; then
  for plugin_name in $known_plugins; do
    if [[ -d "$PLUGIN_CACHE_DIR/$plugin_name" ]]; then
      add_seen "$plugin_name"
    fi
  done
fi

# --- Build JSON output ---
plugins_json=""
ip_json=""
first_plugin=true
first_ip=true

if [[ -n "$seen_names" ]]; then
  while IFS= read -r pname; do
    [[ -z "$pname" ]] && continue

    # --- Plugin entry ---
    if [[ "$first_plugin" == "true" ]]; then
      first_plugin=false
    else
      plugins_json+=","
    fi

    # Determine source
    source_type="unknown"
    if [[ -n "$registry_plugins" ]] && echo "$registry_plugins" | grep -q "^${pname}$"; then
      source_type="marketplace"
    elif [[ "$pname" == "gsd" ]]; then
      source_type="custom"
    fi

    plugins_json+="
    {\"name\": \"${pname}\", \"source\": \"${source_type}\", \"active\": true}"

    # --- Interaction points ---
    if [[ "$first_ip" == "true" ]]; then
      first_ip=false
    else
      ip_json+=","
    fi

    case "$pname" in
      gsd)
        ip_json+="
    \"gsd\": {\"commands\": \"/gsd:*\", \"state_dir\": \".planning/\", \"coexistence\": \"safe\"}"
        ;;
      superpowers)
        ip_json+="
    \"superpowers\": {\"commands\": \"/brainstorm, /execute-plan, /write-plan\", \"coexistence\": \"safe\"}"
        ;;
      tribe)
        ip_json+="
    \"tribe\": {\"commands\": \"/tribe:*\", \"state_dir\": \".tribe/\", \"coexistence\": \"safe\"}"
        ;;
      *)
        ip_json+="
    \"${pname}\": {\"commands\": \"unknown\", \"coexistence\": \"unknown\"}"
        ;;
    esac
  done <<< "$seen_names"
fi

# --- Output JSON ---
cat <<ENDJSON
{
  "plugins": [${plugins_json}
  ],
  "interaction_points": {${ip_json}
  }
}
ENDJSON
