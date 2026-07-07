# Maintainability & Scalability Backlog

Output of a 6-dimension adversarial review (2026-07-07): 50 findings, 46
survived verification, 4 refuted. The safe, low-risk fixes were applied
immediately (see CHANGELOG 0.10.0). This file records what was **deliberately
deferred** — findings whose verified fix was judged unsafe to apply as a
drive-by (`fix_is_safe=false`), almost all for the same reason: the naive fix
is bash-3-hostile or would regress the wrapper contract.

## The dominant theme: hardcoded CLI roster (one root cause, ~10 findings)

`SCALE-1/3/4/5/7/8`, `CONS-01/02/03`, `cfg-path-inline`, `cfg-read-fallback-dup`,
`config-key-source-of-truth` are **not independent** — they are all symptoms of
one thing: the squad roster (`gemini` / `codex` / `grok`) is re-hardcoded across
~10 files instead of declared once. Adding a 4th CLI touches routing.sh
branches, cli-detect.sh, session-start.sh status blocks, show-status.sh,
usage.sh summary + capacity columns, state.sh stats keys, and update-config
validation.

**Why deferred, not drive-by fixed:** the obvious fix — a `declare -A` squad
manifest — does not exist in bash 3.2 (macOS default, a hard constraint). A
bash-3-safe manifest (newline-delimited rows parsed with `case`/`grep`/`cut`)
is a real design task, not a mechanical edit, and it must preserve the exact
per-cell command strings and limits that feed the wrapper-contract path
(`test_routing.sh` only asserts `recommended_agent`, so a careless rewrite
could silently change `command`/`reason` strings).

**Recommended approach:** this is the natural first build phase of the accepted
**ADR-001 (contract-and-ledger core)**, whose adapter-manifest concept already
anticipates a declared roster. Do it there, with a manifest as the single
source of truth (`plugin/lib/squad.manifest` or similar: one row per CLI with
binary, agent prefix, model pref key, capacity slot), and expand test_routing
to assert the full JSON output (not just the agent) before touching it.

## Deferred findings (verified real; fix needs its own pass)

| ID | File | Issue | Why deferred |
|----|------|-------|--------------|
| SCALE-1 | routing.sh + ~10 files | No squad manifest; CLI roster re-hardcoded | Root cause above; do via ADR-001 |
| SCALE-2 | routing.sh | category×CLI as nested if/elif ladders | bash-3: no assoc-array table; regression risk on command strings |
| SCALE-3 | usage.sh | `get_usage_summary` loops agents then emits hardcoded 3-CLI JSON | Part of manifest work |
| SCALE-4 | usage.sh | capacity uses fixed positional columns; grok has no slot | Schema change touches every reader; manifest work |
| SCALE-5 | update-config.sh | route values as literal 4-way test in two branches | Fold into manifest-derived validation |
| SCALE-6 | agents/ | 8 near-duplicate agent .md files, no template; asymmetric roles | A generator is possible but agents are prose+examples; low ROI vs risk of a bad template |
| SCALE-7 | session-start.sh | Squad Status hand-written per-CLI blocks | Part of manifest work |
| SCALE-8 | state.sh | init templates hardcode per-CLI stats/route keys | Part of manifest work |
| DUP-02 | show-config.sh / generate-claude-md.sh | duplicated default_routes jq+grep parse | Both read many fields in one block; isolating one field would worsen coherence — do with manifest |
| DUP-03 | *-wrapper.sh | word/line-limit-from-config copy-pasted across 3 wrappers | Real, but each wrapper's limit semantics differ slightly (word vs line); consolidation into adapter needs care |
| CONS-01 | detect-environment.sh | omits grok; redefines cli-detect fallbacks | Fix is real (add grok, drop redefs); bundle with manifest so the CLI list has one source |
| CONS-02 | show-status.sh | brittle positional grep in jq-less usage parse | Improve with the jq-less summary rework |
| CONS-03 | skill scripts | PLUGIN_ROOT bootstrap re-implemented per script | A shared bootstrap snippet is possible but each script's fallback chain differs intentionally (arg vs env vs derive) |
| DSQ-003 | routing.sh | no-jq JSON path emits invalid JSON if task_desc has a newline | Real edge case; low likelihood (task descriptions rarely contain literal newlines); fix = strip/escape newlines in the manual-JSON branch |
| DSQ-006 | adapter.sh / state.sh | fixed 120s cooldown ignores CLI-reported Retry-After | Enhancement, not a defect; needs per-CLI retry-window parsing |
| DSQ-007 | gemini-wrapper.sh | `invoke_gemini_with_files` word-splits `files_arg`, breaks on paths with spaces | Real, but `files_arg` is a space-delimited `@a @b` list by design — supporting spaces-in-paths needs a delimiter change across the agent-doc call convention too |

## Deferred test gaps (real, additive, no code risk — just not yet written)

`tc-06` (acceptance-rate math), `tc-08` (cooldown expiry unit test),
`tc-12` (generate-skill slug sanitization), `tc-01` (catalog drift-detection
diff), `tc-03` (holdout cksum arm-mapping stability). Each is a cheap offline
test; batch them in a follow-up. The highest-value gaps (`tc-04` holdout
reconcile, config validation, zone math, catalog staleness, savings, git-health)
were written and are in the suite.

## Refuted (4) — no action

`DSQ-004` (awk fallback cannot emit non-numeric JSON — tested false),
`DSQ-005` (`catalog_is_stale` in an `if` condition, `set -e` suppressed — no
crash), plus two others the verifiers found were already correct code.
