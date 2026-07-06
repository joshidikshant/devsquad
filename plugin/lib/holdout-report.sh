#!/usr/bin/env bash
# holdout-report.sh -- Compare control vs treatment arms of the T3 holdout.
# Usage: bash plugin/lib/holdout-report.sh   (from repo/project root)
#
# What this CAN measure from hook-side logs:
#   - suggestion events and unique sessions per arm (holdout.log)
#   - accepted/declined/unresolved outcomes (compliance.log; treatment arm
#     only by construction — control suppresses suggestions)
#   - delegated usage records during the window (usage/*.json)
# What it CANNOT measure (record manually per task, or reconcile from
# transcripts): per-task Claude tokens, wall time, retries, task outcome.
# The D1 decision rule (>=25% Claude-token savings net of overhead, <=1
# extra failure over 20 tasks) needs those manual/transcript columns.
set -u

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-.}"
LOG="${PROJECT_DIR}/.devsquad/logs/holdout.log"

if [[ ! -f "$LOG" ]]; then
  echo "No holdout data at ${LOG}. Enable with: /devsquad:config holdout_mode=true"
  exit 0
fi

# grep -c prints its own 0 on no-match but exits 1; swallow the exit code
count_arm() { local n; n=$(grep -c "| $1 |" "$LOG" 2>/dev/null || true); echo "${n:-0}"; }
sessions_arm() {
  awk -F' \\| ' -v arm="$1" '$3 == arm { print $2 }' "$LOG" | sort -u | wc -l | tr -d ' '
}

CTRL_EVENTS=$(count_arm control)
TREAT_EVENTS=$(count_arm treatment)
CTRL_SESSIONS=$(sessions_arm control)
TREAT_SESSIONS=$(sessions_arm treatment)
TOTAL_SESSIONS=$((CTRL_SESSIONS + TREAT_SESSIONS))

COMPLIANCE="${PROJECT_DIR}/.devsquad/logs/compliance.log"
ACCEPTED=0; DECLINED=0; UNRESOLVED=0
if [[ -f "$COMPLIANCE" ]]; then
  ACCEPTED=$(grep -c advisory_accepted "$COMPLIANCE" 2>/dev/null || true)
  DECLINED=$(grep -c advisory_declined "$COMPLIANCE" 2>/dev/null || true)
  UNRESOLVED=$(grep -c advisory_unresolved "$COMPLIANCE" 2>/dev/null || true)
  ACCEPTED=${ACCEPTED:-0}; DECLINED=${DECLINED:-0}; UNRESOLVED=${UNRESOLVED:-0}
fi

echo "Holdout report — $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "-----------------------------------------------"
printf "%-12s %10s %10s\n" "" "control" "treatment"
printf "%-12s %10s %10s\n" "sessions" "$CTRL_SESSIONS" "$TREAT_SESSIONS"
printf "%-12s %10s %10s\n" "suggestions" "$CTRL_EVENTS" "$TREAT_EVENTS"
echo ""
echo "Treatment-arm outcomes (compliance.log):"
echo "  accepted=${ACCEPTED} declined=${DECLINED} unresolved=${UNRESOLVED}"
echo ""
if [[ "$TOTAL_SESSIONS" -lt 20 ]]; then
  echo "STATUS: ${TOTAL_SESSIONS}/20 sessions — insufficient data, verdict pending."
else
  echo "STATUS: >=20 sessions reached. Reconcile per-task Claude tokens and"
  echo "task outcomes from transcripts before rendering the D1 verdict."
fi
