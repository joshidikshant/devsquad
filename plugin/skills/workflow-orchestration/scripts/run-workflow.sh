#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# run-workflow.sh — DevSquad Workflow Orchestration Skill
# Reads a JSON workflow definition and executes steps sequentially,
# with permission gates, checkpoint commits, and post-workflow validation.
# ---------------------------------------------------------------------------

# Prevent hook re-entry: hooks check DEVSQUAD_HOOK_DEPTH and skip if >= 1
export DEVSQUAD_HOOK_DEPTH=1

# Resolve plugin root from script location — do NOT rely on CLAUDE_PLUGIN_ROOT
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PLUGIN_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

# Source required libraries
source "${PLUGIN_ROOT}/lib/state.sh"
source "${SCRIPT_DIR}/lib-workflow.sh"

# ---------------------------------------------------------------------------
# Argument parsing (while-loop — consistent with Phase 2 locked decision)
# ---------------------------------------------------------------------------
WORKFLOW_FILE=""
DRY_RUN=false
SKIP_GATES=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workflow)
      shift
      WORKFLOW_FILE="${1:-}"
      shift
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --skip-gates)
      SKIP_GATES=true
      shift
      ;;
    --help|-h)
      echo "Usage: run-workflow.sh --workflow <path> [--dry-run] [--skip-gates]"
      echo ""
      echo "Arguments:"
      echo "  --workflow <path>  Path to JSON workflow definition file (required)"
      echo "  --dry-run          Print steps without executing them"
      echo "  --skip-gates       Skip permission gates for destructive steps"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

# Validate required argument
if [[ -z "$WORKFLOW_FILE" ]]; then
  echo "Error: --workflow is required." >&2
  echo "Usage: run-workflow.sh --workflow <path> [--dry-run] [--skip-gates]" >&2
  exit 1
fi

if [[ ! -f "$WORKFLOW_FILE" ]]; then
  echo "Error: Workflow file not found: ${WORKFLOW_FILE}" >&2
  exit 1
fi

# Check jq dependency
if ! command -v jq &>/dev/null; then
  echo "Error: jq is required but not found. Install jq to use workflow orchestration." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Parse workflow definition
# ---------------------------------------------------------------------------
WORKFLOW_NAME=$(jq -r '.name // "unnamed-workflow"' "$WORKFLOW_FILE")
WORKFLOW_DESC=$(jq -r '.description // ""' "$WORKFLOW_FILE")
TEST_COMMAND=$(jq -r '.test_command // ""' "$WORKFLOW_FILE")
STEP_COUNT=$(jq '.steps | length' "$WORKFLOW_FILE")

echo ""
echo "=== DevSquad Workflow Orchestration ==="
echo "Workflow : ${WORKFLOW_NAME}"
echo "Steps    : ${STEP_COUNT}"
[[ -n "$WORKFLOW_DESC" ]] && echo "Info     : ${WORKFLOW_DESC}"
[[ "$DRY_RUN" == "true" ]] && echo "Mode     : DRY RUN (no changes)"
[[ "$SKIP_GATES" == "true" ]] && echo "Gates    : DISABLED"
echo ""

# ---------------------------------------------------------------------------
# Initialize state
# ---------------------------------------------------------------------------
STATE_DIR=$(init_state_dir)
export STATE_DIR
WORKFLOW_FAILED_STEPS=()

# Write initial workflow state BEFORE executing any step (compaction-resilient)
if command -v jq &>/dev/null && [[ -f "${STATE_DIR}/state.json" ]]; then
  CURRENT=$(cat "${STATE_DIR}/state.json" 2>/dev/null || echo "{}")
  UPDATED=$(echo "$CURRENT" | jq \
    --arg name "$WORKFLOW_NAME" \
    --arg file "$WORKFLOW_FILE" \
    '. + {"workflow": {"name": $name, "file": $file, "status": "running", "current_step": "", "checkpoints": {}}}')
  echo "$UPDATED" > "${STATE_DIR}/state.json.tmp.$$" && mv "${STATE_DIR}/state.json.tmp.$$" "${STATE_DIR}/state.json"
fi

# ---------------------------------------------------------------------------
# Execute steps
# ---------------------------------------------------------------------------
for i in $(seq 0 $((STEP_COUNT - 1))); do
  STEP_ID=$(jq -r ".steps[$i].id" "$WORKFLOW_FILE")
  STEP_SKILL=$(jq -r ".steps[$i].skill" "$WORKFLOW_FILE")
  STEP_ARGS=$(jq -r ".steps[$i].args // \"\"" "$WORKFLOW_FILE")
  STEP_DESTRUCTIVE=$(jq -r ".steps[$i].destructive // false" "$WORKFLOW_FILE")
  STEP_CHECKPOINT=$(jq -r ".steps[$i].checkpoint // false" "$WORKFLOW_FILE")
  STEP_COMMIT_MSG=$(jq -r ".steps[$i].commit_message // \"\"" "$WORKFLOW_FILE")

  # Expand env vars in args and commit message safely (no eval — envsubst or perl fallback)
  export WORKFLOW_NAME STEP_ID
  if command -v envsubst &>/dev/null; then
    STEP_ARGS=$(echo "$STEP_ARGS" | envsubst)
    [[ -n "$STEP_COMMIT_MSG" ]] && STEP_COMMIT_MSG=$(echo "$STEP_COMMIT_MSG" | envsubst)
  elif command -v perl &>/dev/null; then
    # perl -pe replaces $VAR and ${VAR} from ENV — no shell execution, safe substitution
    # Leaves undefined variables as-is (defined check prevents empty replacement)
    STEP_ARGS=$(echo "$STEP_ARGS" | perl -pe 's/\$\{?(\w+)\}?/defined $ENV{$1} ? $ENV{$1} : $&/ge')
    [[ -n "$STEP_COMMIT_MSG" ]] && STEP_COMMIT_MSG=$(echo "$STEP_COMMIT_MSG" | perl -pe 's/\$\{?(\w+)\}?/defined $ENV{$1} ? $ENV{$1} : $&/ge')
  else
    # Bash-native substitution for WORKFLOW_NAME and STEP_ID only (no eval)
    STEP_ARGS="${STEP_ARGS//\$WORKFLOW_NAME/${WORKFLOW_NAME}}"
    STEP_ARGS="${STEP_ARGS//\$STEP_ID/${STEP_ID}}"
    STEP_ARGS="${STEP_ARGS//\$\{WORKFLOW_NAME\}/${WORKFLOW_NAME}}"
    STEP_ARGS="${STEP_ARGS//\$\{STEP_ID\}/${STEP_ID}}"
    if [[ -n "$STEP_COMMIT_MSG" ]]; then
      STEP_COMMIT_MSG="${STEP_COMMIT_MSG//\$WORKFLOW_NAME/${WORKFLOW_NAME}}"
      STEP_COMMIT_MSG="${STEP_COMMIT_MSG//\$STEP_ID/${STEP_ID}}"
      STEP_COMMIT_MSG="${STEP_COMMIT_MSG//\$\{WORKFLOW_NAME\}/${WORKFLOW_NAME}}"
      STEP_COMMIT_MSG="${STEP_COMMIT_MSG//\$\{STEP_ID\}/${STEP_ID}}"
    fi
  fi

  STEP_NUM=$((i + 1))
  echo "[${STEP_NUM}/${STEP_COUNT}] Step: ${STEP_ID}"
  echo "        Skill: ${STEP_SKILL} ${STEP_ARGS}"

  # Update current_step in state before execution
  if [[ -f "${STATE_DIR}/state.json" ]]; then
    CURRENT=$(cat "${STATE_DIR}/state.json" 2>/dev/null || echo "{}")
    echo "$CURRENT" | jq --arg s "$STEP_ID" '.workflow.current_step = $s' \
      > "${STATE_DIR}/state.json.tmp.$$" && mv "${STATE_DIR}/state.json.tmp.$$" "${STATE_DIR}/state.json"
  fi

  # Permission gate for destructive steps
  if [[ "$STEP_DESTRUCTIVE" == "true" && "$SKIP_GATES" == "false" && "$DRY_RUN" == "false" ]]; then
    if ! workflow_gate "$STEP_ID" "${STEP_SKILL} ${STEP_ARGS}"; then
      echo "  Skipping step '${STEP_ID}' — rejected by user."
      WORKFLOW_FAILED_STEPS+=("${STEP_ID}:skipped")
      continue
    fi
  fi

  # Dry-run: print and continue
  if [[ "$DRY_RUN" == "true" ]]; then
    echo "  [DRY RUN] Would execute: ${STEP_SKILL} ${STEP_ARGS}"
    continue
  fi

  # Execute step
  STEP_EXIT=0
  if ! ${STEP_SKILL} ${STEP_ARGS} 2>&1; then
    STEP_EXIT=$?
    echo "  ERROR: Step '${STEP_ID}' failed (exit ${STEP_EXIT})." >&2
    WORKFLOW_FAILED_STEPS+=("${STEP_ID}:failed")

    # Print rollback hints for all recorded checkpoints
    echo ""
    echo "  Rollback suggestions (run manually to revert):"
    if [[ -f "${STATE_DIR}/state.json" ]]; then
      jq -r '.workflow.checkpoints // {} | to_entries[] | "    git reset --hard \(.value.hash)  # revert to checkpoint: \(.key)"' \
        "${STATE_DIR}/state.json" 2>/dev/null || true
    fi
    echo ""
    # Continue to next step rather than abort — partial workflows are logged
  fi

  # Checkpoint: commit and record hash
  if [[ "$STEP_CHECKPOINT" == "true" && "$STEP_EXIT" -eq 0 ]]; then
    local_msg="${STEP_COMMIT_MSG:-workflow checkpoint: ${STEP_ID}}"
    workflow_checkpoint "$STEP_ID" "$local_msg"
  fi

  echo ""
done

# ---------------------------------------------------------------------------
# Update final workflow status
# ---------------------------------------------------------------------------
FINAL_STATUS="complete"
[[ ${#WORKFLOW_FAILED_STEPS[@]} -gt 0 ]] && FINAL_STATUS="partial"

if [[ -f "${STATE_DIR}/state.json" ]]; then
  CURRENT=$(cat "${STATE_DIR}/state.json" 2>/dev/null || echo "{}")
  echo "$CURRENT" | jq --arg s "$FINAL_STATUS" '.workflow.status = $s | .workflow.current_step = ""' \
    > "${STATE_DIR}/state.json.tmp.$$" && mv "${STATE_DIR}/state.json.tmp.$$" "${STATE_DIR}/state.json"
fi

# ---------------------------------------------------------------------------
# Post-workflow validation (ORCH-04)
# ---------------------------------------------------------------------------
if [[ "$DRY_RUN" == "false" ]]; then
  workflow_validate "$PLUGIN_ROOT" "$TEST_COMMAND"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "=== Workflow Complete ==="
echo "Status : ${FINAL_STATUS}"
echo "Steps  : $((STEP_COUNT - ${#WORKFLOW_FAILED_STEPS[@]}))/${STEP_COUNT} succeeded"
if [[ ${#WORKFLOW_FAILED_STEPS[@]} -gt 0 ]]; then
  echo "Issues :"
  for entry in "${WORKFLOW_FAILED_STEPS[@]}"; do
    echo "  - ${entry}"
  done
fi
echo ""

[[ "$FINAL_STATUS" == "complete" ]] && exit 0 || exit 1
