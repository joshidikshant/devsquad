#!/usr/bin/env bash
# lib/model-catalog.sh -- machine-global model catalog + tier resolution.
# The anti-churn layer: configs pin INTENT (tier:fast / tier:frontier) or an
# exact model name; the catalog maps intent to the best currently-available
# model at invocation time, so model churn requires zero config edits.
#
# Catalog refresh does network I/O — NEVER call refresh synchronously from a
# hook. Hooks read the cached file only; session-start spawns a DETACHED
# refresh when the catalog is stale (>24h). Missing/stale catalog degrades
# to "" (CLI default model) — tier resolution never blocks or fails a call.
#
# May be sourced (functions) or executed:
#   bash model-catalog.sh refresh | list | resolve <cli> <tier>
set -euo pipefail

DEVSQUAD_CATALOG_DIR="${DEVSQUAD_CATALOG_DIR:-$HOME/.devsquad}"
CATALOG_FILE="${DEVSQUAD_CATALOG_DIR}/models.json"
CATALOG_LOG="${DEVSQUAD_CATALOG_DIR}/models-changelog.log"
CATALOG_DRIFT="${DEVSQUAD_CATALOG_DIR}/models-drift.pending"

# Strip CLI list decorations and non-model lines
_catalog_clean() {
  sed -e 's/^[[:space:]]*[*-][[:space:]]*//' -e 's/ (default)$//' \
    | grep -viE 'authenticated|default model|available models|logged in' \
    | grep -vE '^[[:space:]]*$' || true
}

# True (exit 0) when the catalog is missing or older than 24h
catalog_is_stale() {
  [[ -f "$CATALOG_FILE" ]] || return 0
  local age
  age=$(( $(date +%s) - $(stat -f%m "$CATALOG_FILE" 2>/dev/null || stat -c%Y "$CATALOG_FILE" 2>/dev/null || echo 0) ))
  [[ "$age" -gt 86400 ]]
}

# Query each CLI for its live model list and write the catalog atomically.
# Appends added/removed models to the changelog and stages a one-shot drift
# note that session-start surfaces in the next session.
refresh_model_catalog() {
  mkdir -p "$DEVSQUAD_CATALOG_DIR"
  local lock="${DEVSQUAD_CATALOG_DIR}/.refresh-lock"
  if ! mkdir "$lock" 2>/dev/null; then
    return 0  # another refresh is running
  fi
  trap 'rmdir "'"$lock"'" 2>/dev/null || true' EXIT

  command -v jq &>/dev/null || return 0

  local ts g_models="" g_status="missing-cli" k_models="" k_status="missing-cli"
  ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

  if command -v agy &>/dev/null || command -v antigravity &>/dev/null; then
    local agy_bin="agy"
    command -v agy &>/dev/null || agy_bin="antigravity"
    g_models=$("$agy_bin" models 2>/dev/null | _catalog_clean || true)
    if [[ -n "$g_models" ]]; then g_status="ok"; else g_status="error"; fi
  fi

  if command -v grok &>/dev/null; then
    k_models=$(grok models 2>/dev/null | _catalog_clean || true)
    if [[ -n "$k_models" ]]; then k_status="ok"; else k_status="error"; fi
  fi

  local new_json
  new_json=$(jq -n \
    --arg ts "$ts" \
    --arg gs "$g_status" --arg g "$g_models" \
    --arg ks "$k_status" --arg k "$k_models" \
    '{
      fetched_at: $ts,
      gemini: { status: $gs, models: ($g | split("\n") | map(select(length > 0))) },
      grok:   { status: $ks, models: ($k | split("\n") | map(select(length > 0))) },
      codex:  { status: "unlistable", models: [] }
    }')

  # Diff against the previous catalog; log and stage drift notes
  if [[ -f "$CATALOG_FILE" ]]; then
    local cli added removed
    for cli in gemini grok; do
      added=$(comm -13 \
        <(jq -r --arg c "$cli" '.[$c].models // [] | .[]' "$CATALOG_FILE" 2>/dev/null | sort) \
        <(printf '%s' "$new_json" | jq -r --arg c "$cli" '.[$c].models // [] | .[]' | sort) || true)
      removed=$(comm -23 \
        <(jq -r --arg c "$cli" '.[$c].models // [] | .[]' "$CATALOG_FILE" 2>/dev/null | sort) \
        <(printf '%s' "$new_json" | jq -r --arg c "$cli" '.[$c].models // [] | .[]' | sort) || true)
      if [[ -n "$added" ]]; then
        printf '%s\n' "$added" | while IFS= read -r m; do
          echo "${ts} | ${cli} | added   | ${m}" >> "$CATALOG_LOG"
          echo "MODEL DRIFT: ${cli} added '${m}'" >> "$CATALOG_DRIFT"
        done
      fi
      if [[ -n "$removed" ]]; then
        printf '%s\n' "$removed" | while IFS= read -r m; do
          echo "${ts} | ${cli} | removed | ${m}" >> "$CATALOG_LOG"
          echo "MODEL DRIFT: ${cli} REMOVED '${m}' — check pinned names in agent_models/preferences" >> "$CATALOG_DRIFT"
        done
      fi
    done
  fi

  local tmp="${CATALOG_FILE}.tmp.$$"
  printf '%s\n' "$new_json" > "$tmp"
  mv "$tmp" "$CATALOG_FILE"
}

# Map a tier to the best available model for a CLI, from the cached catalog.
# tier:fast     -> cheap/fast family (flash|fast|mini|lite|haiku),
#                  highest version, prefer (Medium) then (Low)
# tier:frontier -> non-fast family matching pro|opus|max|ultra (fallback:
#                  any non-fast, then anything), highest version, prefer
#                  (High) then Thinking
# Echoes "" when unresolvable — callers fall back to the CLI default.
resolve_model_tier() {
  local cli="$1" tier="$2"
  [[ -f "$CATALOG_FILE" ]] || { echo ""; return 0; }
  command -v jq &>/dev/null || { echo ""; return 0; }

  local models
  models=$(jq -r --arg c "$cli" '.[$c].models // [] | .[]' "$CATALOG_FILE" 2>/dev/null || true)
  [[ -n "$models" ]] || { echo ""; return 0; }

  local pool
  if [[ "$tier" == "fast" ]]; then
    pool=$(printf '%s\n' "$models" | grep -iE 'flash|fast|mini|lite|haiku' || true)
    [[ -n "$pool" ]] || pool="$models"
  else
    pool=$(printf '%s\n' "$models" | grep -ivE 'flash|fast|mini|lite|haiku' | grep -iE 'pro|opus|max|ultra' || true)
    if [[ -z "$pool" ]]; then
      pool=$(printf '%s\n' "$models" | grep -ivE 'flash|fast|mini|lite|haiku' || true)
    fi
    [[ -n "$pool" ]] || pool="$models"
  fi

  printf '%s\n' "$pool" | awk -v tier="$tier" '
    BEGIN { best = -1; pick = "" }
    {
      ver = 0
      if (match($0, /[0-9]+\.[0-9]+/)) ver = substr($0, RSTART, RLENGTH) + 0
      bonus = 0
      if (tier == "fast") {
        if ($0 ~ /\(Medium\)/) bonus = 2
        else if ($0 ~ /\(Low\)/) bonus = 1
      } else {
        if ($0 ~ /\(High\)/) bonus = 2
        else if ($0 ~ /Thinking/) bonus = 1
      }
      score = ver * 100 + bonus
      if (score > best) { best = score; pick = $0 }
    }
    END { print pick }
  '
}

# One-line catalog summary + any staged drift notes (consumed on read).
# Read-only network-wise; safe for hooks.
catalog_context_note() {
  if [[ ! -f "$CATALOG_FILE" ]]; then
    echo "Model catalog: not yet built (refreshing in background)"
    return 0
  fi
  local g k when
  g=$(jq -r '.gemini.models | length' "$CATALOG_FILE" 2>/dev/null || echo 0)
  k=$(jq -r '.grok.models | length' "$CATALOG_FILE" 2>/dev/null || echo 0)
  when=$(jq -r '.fetched_at // "unknown"' "$CATALOG_FILE" 2>/dev/null)
  echo "Model catalog: ${g} gemini-role, ${k} grok models (as of ${when})"
  if [[ -f "$CATALOG_DRIFT" ]]; then
    cat "$CATALOG_DRIFT"
    rm -f "$CATALOG_DRIFT"
  fi
}

# Executable mode
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  case "${1:-list}" in
    refresh)
      refresh_model_catalog
      echo "Catalog refreshed:"
      cat "$CATALOG_FILE" 2>/dev/null || echo "(no catalog)"
      ;;
    list)
      cat "$CATALOG_FILE" 2>/dev/null || echo "No catalog yet. Run: bash $0 refresh"
      ;;
    resolve)
      resolve_model_tier "${2:?cli}" "${3:?tier}"
      ;;
    *)
      echo "Usage: bash $0 refresh|list|resolve <cli> <fast|frontier>"
      exit 1
      ;;
  esac
fi
