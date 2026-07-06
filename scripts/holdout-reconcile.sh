#!/usr/bin/env bash
# holdout-reconcile.sh -- D1 verdict data: join holdout arm assignments with
# measured per-session Claude token usage from Claude Code transcripts.
#
# Usage:
#   bash scripts/holdout-reconcile.sh [project_dir ...] [--failures C=N T=M]
#
# Defaults to the current directory. For each project with a
# .devsquad/logs/holdout.log, maps session -> arm, finds each session's
# transcript under ~/.claude/projects/<encoded-path>/<session_id>.jsonl, and
# sums assistant output tokens (grouped by message.id — Claude Code writes
# one line per content block, duplicating usage).
#
# Decision rule (pre-registered, 2026-07-05 handoff D1): treatment must show
# >=25% Claude-token savings net of subagent overhead with <=1 additional
# task failure vs control, over >=20 sessions. Failures are supplied
# manually via --failures (hook-side logs cannot see task outcomes).
set -u

FAIL_CONTROL=""
FAIL_TREATMENT=""
PROJECTS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --failures)
      shift
      while [[ $# -gt 0 && "$1" == *=* ]]; do
        case "$1" in
          C=*|control=*) FAIL_CONTROL="${1#*=}" ;;
          T=*|treatment=*) FAIL_TREATMENT="${1#*=}" ;;
        esac
        shift
      done
      ;;
    *) PROJECTS+=("$1"); shift ;;
  esac
done
if [[ ${#PROJECTS[@]} -eq 0 ]]; then
  PROJECTS=(".")
fi

if ! command -v python3 &>/dev/null; then
  echo "Error: python3 required for transcript parsing"
  exit 1
fi

python3 - "$FAIL_CONTROL" "$FAIL_TREATMENT" "${PROJECTS[@]}" <<'PYEOF'
import json, os, re, sys, statistics

fail_c, fail_t = sys.argv[1], sys.argv[2]
projects = [os.path.abspath(p) for p in sys.argv[3:]]
claude_projects = os.path.expanduser("~/.claude/projects")

def encode(path):
    return re.sub(r"[^A-Za-z0-9]", "-", path)

def session_tokens(transcript):
    """Sum output tokens per unique assistant message id (one line per
    content block duplicates usage — count each API call once)."""
    seen = {}
    try:
        with open(transcript) as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = obj.get("message") or {}
                usage = msg.get("usage") or {}
                out = usage.get("output_tokens")
                if out is None:
                    continue
                mid = msg.get("id") or obj.get("uuid") or id(line)
                seen[mid] = max(seen.get(mid, 0), out)
    except OSError:
        return None
    return sum(seen.values()) if seen else None

arms = {}          # session -> arm (first seen)
events = {"control": 0, "treatment": 0}
missing = []

for proj in projects:
    log = os.path.join(proj, ".devsquad", "logs", "holdout.log")
    if not os.path.isfile(log):
        continue
    tdir = os.path.join(claude_projects, encode(proj))
    with open(log) as f:
        for line in f:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 3:
                continue
            sid, arm = parts[1], parts[2]
            if arm not in events:
                continue
            events[arm] += 1
            if sid not in arms:
                arms[sid] = {"arm": arm, "proj": proj, "tdir": tdir}

per_arm = {"control": [], "treatment": []}
for sid, info in arms.items():
    t = os.path.join(info["tdir"], f"{sid}.jsonl")
    tokens = session_tokens(t) if os.path.isfile(t) else None
    if tokens is None:
        missing.append(sid)
    else:
        per_arm[info["arm"]].append(tokens)

def stats(v):
    if not v:
        return "n=0"
    return (f"n={len(v)}  total={sum(v):,}  mean={int(statistics.mean(v)):,}  "
            f"median={int(statistics.median(v)):,}")

n_total = len(per_arm["control"]) + len(per_arm["treatment"])
print("D1 holdout reconciliation")
print("=" * 60)
print(f"projects scanned: {len(projects)}")
print(f"suggestion events: control={events['control']} treatment={events['treatment']}")
print(f"sessions with transcripts: {n_total} "
      f"(control={len(per_arm['control'])}, treatment={len(per_arm['treatment'])})")
if missing:
    print(f"sessions without locatable transcripts: {len(missing)}")
print()
print("Claude output tokens per session:")
print(f"  control:   {stats(per_arm['control'])}")
print(f"  treatment: {stats(per_arm['treatment'])}")

if per_arm["control"] and per_arm["treatment"]:
    mc = statistics.mean(per_arm["control"])
    mt = statistics.mean(per_arm["treatment"])
    savings = (mc - mt) / mc * 100 if mc else 0.0
    print(f"\n  treatment vs control mean: {savings:+.1f}% "
          f"({'saves' if savings > 0 else 'COSTS'} tokens)")

print()
if fail_c or fail_t:
    print(f"reported task failures: control={fail_c or '?'} treatment={fail_t or '?'}")
else:
    print("task failures: none reported (--failures C=N T=M to supply)")

print()
if n_total < 20:
    print(f"VERDICT: insufficient data ({n_total}/20 sessions) — keep working.")
else:
    print("VERDICT INPUTS READY — apply the rule: treatment needs >=25% mean")
    print("savings (net of subagent overhead) with <=1 extra failure.")
PYEOF
