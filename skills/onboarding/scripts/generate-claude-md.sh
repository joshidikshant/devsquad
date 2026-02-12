#!/usr/bin/env bash
set -euo pipefail

# generate-claude-md.sh -- Generate adaptive CLAUDE.md snippet from config
# Can be invoked standalone or via onboarding/config commands

# Parse arguments
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-}"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-.}"
INSERT_MODE=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --plugin-root)
      PLUGIN_ROOT="$2"
      shift 2
      ;;
    --project-dir)
      PROJECT_DIR="$2"
      shift 2
      ;;
    --insert)
      INSERT_MODE=true
      shift
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

# Resolve plugin root if not provided
if [[ -z "$PLUGIN_ROOT" ]]; then
  # Script is in skills/onboarding/scripts/, plugin root is 3 levels up
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  PLUGIN_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
fi

# Source libraries
source "${PLUGIN_ROOT}/lib/state.sh"
source "${PLUGIN_ROOT}/lib/cli-detect.sh"

# Paths
CONFIG_FILE="${PROJECT_DIR}/.devsquad/config.json"
ENV_FILE="${PROJECT_DIR}/.devsquad/environment.json"
TEMPLATE_FILE="${PLUGIN_ROOT}/skills/onboarding/templates/claude-md-snippet.md"

# Default values (used if config missing)
ENFORCEMENT_MODE="advisory"
RESEARCH_ROUTE="gemini"
READING_ROUTE="gemini"
CODEGEN_ROUTE="codex"
TESTING_ROUTE="codex"
IMPL_ROUTE="codex"
GEMINI_AVAIL="false"
CODEX_AVAIL="false"
DETECTED_PLUGINS=""
CLAUDE_MD_MANAGED="false"

# Read config.json if exists
if [[ -f "$CONFIG_FILE" ]]; then
  if command -v jq &>/dev/null; then
    ENFORCEMENT_MODE=$(jq -r '.enforcement_mode // "advisory"' "$CONFIG_FILE")
    RESEARCH_ROUTE=$(jq -r '.default_routes.research // "gemini"' "$CONFIG_FILE")
    READING_ROUTE=$(jq -r '.default_routes.reading // "gemini"' "$CONFIG_FILE")
    CODEGEN_ROUTE=$(jq -r '.default_routes.code_generation // "codex"' "$CONFIG_FILE")
    TESTING_ROUTE=$(jq -r '.default_routes.testing // "codex"' "$CONFIG_FILE")
    IMPL_ROUTE="$CODEGEN_ROUTE"  # Implementation uses same route as code generation
    CLAUDE_MD_MANAGED=$(jq -r '.claude_md_managed // false' "$CONFIG_FILE")
  else
    # grep fallback
    ENFORCEMENT_MODE=$(grep -o '"enforcement_mode"[[:space:]]*:[[:space:]]*"[^"]*"' "$CONFIG_FILE" 2>/dev/null | cut -d'"' -f4)
    ENFORCEMENT_MODE="${ENFORCEMENT_MODE:-advisory}"
    RESEARCH_ROUTE=$(grep -o '"research"[[:space:]]*:[[:space:]]*"[^"]*"' "$CONFIG_FILE" 2>/dev/null | cut -d'"' -f4)
    RESEARCH_ROUTE="${RESEARCH_ROUTE:-gemini}"
    READING_ROUTE=$(grep -o '"reading"[[:space:]]*:[[:space:]]*"[^"]*"' "$CONFIG_FILE" 2>/dev/null | cut -d'"' -f4)
    READING_ROUTE="${READING_ROUTE:-gemini}"
    CODEGEN_ROUTE=$(grep -o '"code_generation"[[:space:]]*:[[:space:]]*"[^"]*"' "$CONFIG_FILE" 2>/dev/null | cut -d'"' -f4)
    CODEGEN_ROUTE="${CODEGEN_ROUTE:-codex}"
    TESTING_ROUTE=$(grep -o '"testing"[[:space:]]*:[[:space:]]*"[^"]*"' "$CONFIG_FILE" 2>/dev/null | cut -d'"' -f4)
    TESTING_ROUTE="${TESTING_ROUTE:-codex}"
    IMPL_ROUTE="$CODEGEN_ROUTE"
    CLAUDE_MD_MANAGED=$(grep -o '"claude_md_managed"[[:space:]]*:[[:space:]]*[^,}]*' "$CONFIG_FILE" 2>/dev/null | awk '{print $NF}')
    CLAUDE_MD_MANAGED="${CLAUDE_MD_MANAGED:-false}"
  fi
else
  echo "WARNING: config.json not found, using defaults" >&2
fi

# Read environment.json if exists
if [[ -f "$ENV_FILE" ]]; then
  if command -v jq &>/dev/null; then
    GEMINI_AVAIL=$(jq -r '.clis.gemini.available // false' "$ENV_FILE" 2>/dev/null || echo "false")
    CODEX_AVAIL=$(jq -r '.clis.codex.available // false' "$ENV_FILE" 2>/dev/null || echo "false")
    DETECTED_PLUGINS=$(jq -r '.plugins[].name // empty' "$ENV_FILE" 2>/dev/null | tr '\n' ', ' | sed 's/,$//')
  else
    # grep fallback
    if grep -q '"gemini"' "$ENV_FILE" 2>/dev/null && grep -q '"available"[[:space:]]*:[[:space:]]*true' "$ENV_FILE" 2>/dev/null; then
      GEMINI_AVAIL="true"
    fi
    if grep -q '"codex"' "$ENV_FILE" 2>/dev/null && grep -q '"available"[[:space:]]*:[[:space:]]*true' "$ENV_FILE" 2>/dev/null; then
      CODEX_AVAIL="true"
    fi
  fi
else
  echo "WARNING: environment.json not found, assuming no CLIs available" >&2
fi

# Build SQUAD_TABLE
SQUAD_TABLE="| Agent | Status | Role |
|-------|--------|------|"

if [[ "$GEMINI_AVAIL" == "true" ]]; then
  SQUAD_TABLE="$SQUAD_TABLE
| Gemini | Available | Research, reading, web search |"
elif [[ "$RESEARCH_ROUTE" == "gemini" ]] || [[ "$READING_ROUTE" == "gemini" ]]; then
  SQUAD_TABLE="$SQUAD_TABLE
| Gemini | Not installed | Research, reading, web search |"
fi

if [[ "$CODEX_AVAIL" == "true" ]]; then
  SQUAD_TABLE="$SQUAD_TABLE
| Codex | Available | Code generation, testing |"
elif [[ "$CODEGEN_ROUTE" == "codex" ]] || [[ "$TESTING_ROUTE" == "codex" ]]; then
  SQUAD_TABLE="$SQUAD_TABLE
| Codex | Not installed | Code generation, testing |"
fi

SQUAD_TABLE="$SQUAD_TABLE
| Claude | Active | Synthesis, integration, decisions |"

# Build ENFORCEMENT_DESCRIPTION
if [[ "$ENFORCEMENT_MODE" == "strict" ]]; then
  ENFORCEMENT_DESCRIPTION="In strict mode, non-compliant tool use is blocked. Claude must use the suggested delegation command."
else
  ENFORCEMENT_DESCRIPTION="In advisory mode, delegation is suggested but Claude can override when direct handling is more efficient."
fi

# Build PLUGINS_LINE
if [[ -n "$DETECTED_PLUGINS" ]]; then
  PLUGINS_LINE="- Discovered plugins: $DETECTED_PLUGINS"
else
  PLUGINS_LINE=""
fi

# Generate timestamp
GENERATED_TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Read template
if [[ ! -f "$TEMPLATE_FILE" ]]; then
  echo "ERROR: Template file not found: $TEMPLATE_FILE" >&2
  exit 1
fi

TEMPLATE=$(cat "$TEMPLATE_FILE")

# Perform substitutions
TEMPLATE="${TEMPLATE//\{\{SQUAD_TABLE\}\}/$SQUAD_TABLE}"
TEMPLATE="${TEMPLATE//\{\{ENFORCEMENT_MODE\}\}/$ENFORCEMENT_MODE}"
TEMPLATE="${TEMPLATE//\{\{ENFORCEMENT_DESCRIPTION\}\}/$ENFORCEMENT_DESCRIPTION}"
TEMPLATE="${TEMPLATE//\{\{RESEARCH_ROUTE\}\}/$RESEARCH_ROUTE}"
TEMPLATE="${TEMPLATE//\{\{READING_ROUTE\}\}/$READING_ROUTE}"
TEMPLATE="${TEMPLATE//\{\{CODEGEN_ROUTE\}\}/$CODEGEN_ROUTE}"
TEMPLATE="${TEMPLATE//\{\{TESTING_ROUTE\}\}/$TESTING_ROUTE}"
TEMPLATE="${TEMPLATE//\{\{IMPL_ROUTE\}\}/$IMPL_ROUTE}"
TEMPLATE="${TEMPLATE//\{\{GENERATED_TIMESTAMP\}\}/$GENERATED_TIMESTAMP}"

# Replace PLUGINS_LINE (handle empty case)
if [[ -n "$PLUGINS_LINE" ]]; then
  TEMPLATE="${TEMPLATE//\{\{PLUGINS_LINE\}\}/$PLUGINS_LINE}"
else
  # Remove the entire line containing {{PLUGINS_LINE}}
  TEMPLATE=$(echo "$TEMPLATE" | grep -v '{{PLUGINS_LINE}}')
fi

# Handle conditional sections
if [[ "$GEMINI_AVAIL" == "false" ]]; then
  # Remove lines between IF_GEMINI_AVAILABLE and END_IF_GEMINI_AVAILABLE (inclusive)
  TEMPLATE=$(echo "$TEMPLATE" | sed '/{{IF_GEMINI_AVAILABLE}}/,/{{END_IF_GEMINI_AVAILABLE}}/d')
else
  # Remove just the marker lines, keep content
  TEMPLATE=$(echo "$TEMPLATE" | grep -v '{{IF_GEMINI_AVAILABLE}}' | grep -v '{{END_IF_GEMINI_AVAILABLE}}')
fi

if [[ "$CODEX_AVAIL" == "false" ]]; then
  # Remove lines between IF_CODEX_AVAILABLE and END_IF_CODEX_AVAILABLE (inclusive)
  TEMPLATE=$(echo "$TEMPLATE" | sed '/{{IF_CODEX_AVAILABLE}}/,/{{END_IF_CODEX_AVAILABLE}}/d')
else
  # Remove just the marker lines, keep content
  TEMPLATE=$(echo "$TEMPLATE" | grep -v '{{IF_CODEX_AVAILABLE}}' | grep -v '{{END_IF_CODEX_AVAILABLE}}')
fi

# Validate no leftover markers
if echo "$TEMPLATE" | grep -q '{{'; then
  LEFTOVER=$(echo "$TEMPLATE" | grep -o '{{[^}]*}}' | sort -u | tr '\n' ', ' | sed 's/,$//')
  echo "ERROR: Unresolved template variables: $LEFTOVER" >&2
  exit 1
fi

# Output or insert
if [[ "$INSERT_MODE" == "false" ]]; then
  # Default mode: output to stdout
  echo "$TEMPLATE"
else
  # Insert mode: modify CLAUDE.md

  # Check claude_md_managed setting
  if [[ "$CLAUDE_MD_MANAGED" == "false" ]]; then
    echo "CLAUDE.md management is disabled (opt-out). Skipping." >&2
    exit 0
  fi

  CLAUDE_MD="${PROJECT_DIR}/CLAUDE.md"

  if [[ -f "$CLAUDE_MD" ]]; then
    if grep -q '<!-- DEVSQUAD-START -->' "$CLAUDE_MD"; then
      # Replace between markers using awk
      TEMP_FILE="${CLAUDE_MD}.tmp.$$"
      SNIPPET_FILE="${CLAUDE_MD}.snippet.$$"

      echo "$TEMPLATE" > "$SNIPPET_FILE"

      awk -v sf="$SNIPPET_FILE" '
        /<!-- DEVSQUAD-START -->/ { while((getline line < sf) > 0) print line; close(sf); skip=1; next }
        /<!-- DEVSQUAD-END -->/ { skip=0; next }
        !skip { print }
      ' "$CLAUDE_MD" > "$TEMP_FILE"

      mv "$TEMP_FILE" "$CLAUDE_MD"
      rm -f "$SNIPPET_FILE"
    else
      # Append to existing file
      printf '\n%s\n' "$TEMPLATE" >> "$CLAUDE_MD"
    fi
  else
    # Create new file
    echo "$TEMPLATE" > "$CLAUDE_MD"
  fi
fi
