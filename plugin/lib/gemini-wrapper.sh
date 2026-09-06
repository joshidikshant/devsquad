#!/usr/bin/env bash
# lib/gemini-wrapper.sh -- Antigravity (agy) adapter configuration.
# Sourced by Gemini agent system prompts. Do not execute directly.
# Shared invocation core lives in lib/adapter.sh (D4 contract).
#
# The gemini ROLE is served by the Antigravity CLI only: Google decommissioned
# the open-source Gemini CLI on 2026-06-18; a `gemini` binary on PATH no
# longer implies a working service.
set -euo pipefail

# Load NVM if available so CLIs installed via NVM are on PATH in
# non-interactive subshells where .zshrc/.bash_profile are not sourced.
export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
# shellcheck source=/dev/null
[ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh" --no-use 2>/dev/null || true
if [ -d "$NVM_DIR/versions/node" ]; then
  for _nvm_node_dir in "$NVM_DIR"/versions/node/*/bin; do
    case ":${PATH}:" in
      *":${_nvm_node_dir}:"*) ;;
      *) export PATH="${_nvm_node_dir}:${PATH}" ;;
    esac
  done
  unset _nvm_node_dir
fi

_GEMINI_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${_GEMINI_LIB_DIR}/adapter.sh"

# Model: agent-specific (agent_models.<DEVSQUAD_AGENT>) > preferences.gemini_model
# > Antigravity session default. Antigravity multiplexes Gemini/Claude/GPT-OSS
# models — list with `agy models`. NOTE: agy silently ignores unknown --model
# values, which is why update-config validates names against `agy models`.
_resolve_gemini_model() {
  _adapter_resolve_model "gemini_model"
}

_gemini_configure_adapter() {
  ADAPTER_AGENT="gemini"
  ADAPTER_PREF_MODEL_KEY="gemini_model"
  ADAPTER_AUTH_HINT="Open Antigravity and sign in, then retry."
  ADAPTER_FALLBACK="Use @codex-developer for code, @codex-tester for tests, or handle synthesis yourself."
  ADAPTER_MISSING_MSG="Antigravity CLI not installed (legacy Gemini CLI is decommissioned). Install: brew install --cask antigravity-cli"
  ADAPTER_EXTRA_AUTH_RE=""
  ADAPTER_STDIN_FILE=""
  ADAPTER_EXTRA_CHARS_IN=0

  _adapter_resolve_cli() {
    if command -v agy &>/dev/null; then
      echo "agy"
    elif command -v antigravity &>/dev/null; then
      echo "antigravity"
    else
      echo ""
    fi
  }

  _adapter_build_args() {
    # --print-timeout: agy-native bound (default 5m) — belt and suspenders
    # alongside the adapter's watchdog
    ADAPTER_ARGS=("--dangerously-skip-permissions" "-p" "$1" "--output-format" "text" "--print-timeout" "${3}s")
    if [[ -n "$2" ]]; then
      ADAPTER_ARGS+=("--model" "$2")
    fi
  }
}

# Resolve word limit: caller override > config > 300; append bound if absent
_gemini_final_prompt() {
  local prompt="$1"
  local caller_limit="${2:-}"
  local word_limit=""
  if [[ -n "$caller_limit" ]]; then
    word_limit="$caller_limit"
  else
    local config_file="${CLAUDE_PROJECT_DIR:-.}/.devsquad/config.json"
    if command -v jq &>/dev/null && [[ -f "$config_file" ]]; then
      word_limit=$(jq -r '.preferences.gemini_word_limit // empty' "$config_file" 2>/dev/null)
    fi
    word_limit="${word_limit:-300}"
  fi
  local final_prompt="$prompt"
  if [[ "$word_limit" -gt 0 ]] 2>/dev/null; then
    if ! echo "$prompt" | grep -iE 'under [0-9]+ words|[0-9]+ words max' &>/dev/null; then
      final_prompt="${prompt}. Under ${word_limit} words."
    fi
  fi
  printf '%s' "$final_prompt"
}

# Main invocation function
# Usage: invoke_gemini "prompt" [word_limit] [timeout_secs]
# Returns 0 on success (stdout contains response), 1 on failure.
# Error prefixes: RATE_LIMITED, AUTH_ERROR, TIMEOUT, CLI_ERROR
invoke_gemini() {
  local prompt="$1"
  local caller_limit="${2:-}"
  local timeout_secs="${3:-60}"

  local final_prompt
  final_prompt=$(_gemini_final_prompt "$prompt" "$caller_limit")

  _gemini_configure_adapter
  _adapter_invoke "$final_prompt" "$timeout_secs"
}

# File-based invocation. Context arguments are newline-delimited so paths with
# spaces remain intact. A single legacy whitespace-delimited argument remains
# accepted only when every token resolves, preserving existing callers.
# Usage: invoke_gemini_with_files $'@src/auth/\n@src/models/user.ts' "prompt" [word_limit] [timeout_secs]
invoke_gemini_with_files() {
  local files_arg="$1"
  local prompt="$2"
  local word_limit="${3:-}"
  local timeout_secs="${4:-90}"

  # Build stdin content with REAL newlines (never printf %b: it would also
  # expand backslash escapes INSIDE file contents, corrupting code)
  local nl=$'\n'
  local file_content=""
  local token path f manifest="$files_arg"
  local project_root="${CLAUDE_PROJECT_DIR:-.}"
  local max_bytes="${DEVSQUAD_CONTEXT_MAX_BYTES:-1048576}"
  local max_file_bytes="${DEVSQUAD_CONTEXT_MAX_FILE_BYTES:-262144}"
  local used_bytes=0 file_bytes header_bytes
  project_root=$(cd "$project_root" 2>/dev/null && pwd -P) || {
    echo "CONTEXT_OMITTED: project root is unavailable: ${project_root}" >&2
    return 1
  }
  if [[ "$files_arg" != *$'\n'* ]]; then
    local all_tokens_resolve="true"
    for token in $files_arg; do
      path="${token#@}"
      [[ "$token" == @* && ( -f "$project_root/$path" || -d "$project_root/$path" ) ]] || all_tokens_resolve="false"
    done
    if [[ "$all_tokens_resolve" == "true" ]]; then
      manifest=$(printf '%s\n' $files_arg)
    fi
  fi
  while IFS= read -r token; do
    if [[ "$token" == @* ]]; then
      path="${token#@}"
      case "$path" in
        ""|/*|../*|*/../*|*/..) echo "CONTEXT_OMITTED: path escapes project scope: ${path}" >&2; continue ;;
      esac
      path="${path#./}"
      if [[ -L "$project_root/$path" ]]; then
        echo "CONTEXT_OMITTED: symlink input is not followed: ${path}" >&2
        continue
      fi

      local matched="false"
      while IFS= read -r -d '' f; do
        matched="true"
        [[ -L "$project_root/$f" ]] && { echo "CONTEXT_OMITTED: symlink input is not followed: ${f}" >&2; continue; }
        case "/$f" in
          */.devsquad/*|*/.env|*/.env.*|*/credentials.json|*.pem|*.key)
            echo "CONTEXT_OMITTED: sensitive or runtime path excluded: ${f}" >&2; continue ;;
        esac
        if git -C "$project_root" check-ignore --no-index -q -- "$f" 2>/dev/null; then
          echo "CONTEXT_OMITTED: ignored path excluded: ${f}" >&2
          continue
        fi
        if ! grep -Iq . "$project_root/$f" 2>/dev/null && [[ -s "$project_root/$f" ]]; then
          echo "CONTEXT_OMITTED: binary file excluded: ${f}" >&2
          continue
        fi
        file_bytes=$(wc -c < "$project_root/$f" | tr -d ' ')
        if [[ "$file_bytes" -gt "$max_file_bytes" ]]; then
          echo "CONTEXT_OMITTED: file exceeds ${max_file_bytes} byte limit: ${f} (${file_bytes} bytes)" >&2
          continue
        fi
        header_bytes=$(( ${#f} + 10 ))
        if [[ $(( used_bytes + file_bytes + header_bytes )) -gt "$max_bytes" ]]; then
          echo "CONTEXT_OMITTED: total context exceeds ${max_bytes} byte limit before: ${f}" >&2
          continue
        fi
        file_content+="=== ${f} ===${nl}$(cat "$project_root/$f")${nl}${nl}"
        used_bytes=$(( used_bytes + file_bytes + header_bytes ))
      done < <(git -C "$project_root" ls-files -z -- "$path" 2>/dev/null)
      if [[ "$matched" == "false" ]]; then
        echo "CONTEXT_OMITTED: no tracked files in scope: ${path}" >&2
      fi
    fi
  done <<< "$manifest"

  local final_prompt
  final_prompt=$(_gemini_final_prompt "$prompt" "$word_limit")

  if [[ -z "$file_content" ]]; then
    # No files resolved — plain invocation (records its own telemetry)
    _gemini_configure_adapter
    _adapter_invoke "$final_prompt" "$timeout_secs"
    return $?
  fi

  local content_file
  content_file=$(mktemp)
  printf '%s' "$file_content" > "$content_file"

  _gemini_configure_adapter
  ADAPTER_STDIN_FILE="$content_file"
  ADAPTER_EXTRA_CHARS_IN=${#file_content}

  local rc=0
  _adapter_invoke "$final_prompt" "$timeout_secs" || rc=$?
  rm -f "$content_file"
  return $rc
}
