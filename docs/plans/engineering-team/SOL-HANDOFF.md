# Sol execution handoff — build, test and make DevSquad usable

This is the full execution prompt for Sol. It is an implementation assignment; the underlying runtime is still pending at handoff creation. Copy this document into Sol, or ask Sol to read this file and execute it in full.

## Objective and persistence

Build the complete DevSquad engineering-team plan into a simple, reliable local product I can actually use from terminal, Codex App, Claude Code App, Antigravity and Grok Build. Treat this as a persistent implementation goal. Execute, test, repair and document it through completion; do not stop after planning, scaffolding, M1, the first successful demo or passing existing tests.

Workspace: `/Users/Dikshant/Desktop/Projects/devsquad`.
Canonical build branch after cleanup: `codex/engineering-team` (local and GitHub).
Complete architecture and original handoff checkpoint: `ff1fa60`.
Latest architecture amendment before this handoff: `bfaa390`.

Inspect current Git state and files first. Continue `codex/engineering-team`, or use an isolated Codex worktree based on that branch. Verify `git merge-base --is-ancestor ff1fa60 HEAD` and preserve existing work. GitHub `main` remains the published runtime baseline and does not contain this build plan. Do not restart from `main`, resurrect an archived branch or merge the unrelated February backup history. Read the [branch consolidation record](../../audits/2026-09-06-branch-consolidation.md), repository instructions and `CONTRIBUTING.md`. Use one integration branch for milestone checkpoints; remove temporary task branches/worktrees after their work is integrated and preserved.

## Read the complete specification

Read these files relative to the repository, in order:

1. `docs/plans/engineering-team/START-HERE.md`
2. `docs/adr/ADR-002-surface-independent-engineering-team.md`
3. `docs/plans/engineering-team/CONTRACTS.md`
4. `docs/plans/engineering-team/IMPLEMENTATION.md`
5. `docs/plans/engineering-team/SELECTION-AND-COUNCIL.md`
6. `docs/plans/engineering-team/MODEL-LIFECYCLE-AND-NATIVE-ADAPTERS.md`
7. `docs/plans/engineering-team/backlog.json` and `docs/plans/engineering-team/examples/`

Use `docs/audits/2026-09-06-engineering-team-assessment.md` for verified starting defects/history. ADR-001 and the old `.planning/` records are historical context; they do not supersede the September decisions or prove implementation completion. Recheck source and installed provider capabilities instead of trusting dated model examples.

**Scope clarification:** This assignment includes M1–M7 and C1. Council is optional to invoke in the product, but its implementation and acceptance gates are included in this delivery. M3 is the first usable checkpoint, not the finish line. Later amendments override older conflicting details. The simple task-entry requirement below extends the earlier deferral of natural-language task preparation; it does not authorize a general workflow engine or a second planner.

Resolve ordinary implementation choices yourself. Where current evidence requires a contract change, make the smallest coherent amendment and update affected schemas, docs and tests together. Do not restart the architecture exercise or quietly remove hard requirements to obtain green tests.

## Deliver the whole engineering team

- Implement the shared Python runner, transactional event/state store, isolated worktrees, durable jobs and common CLI/MCP service. Preserve legacy Bash 3.2 wrapper callers, jq-absent behavior and the four error prefixes.
- Use a verified native Codex app-server adapter, keeping legacy CLI compatibility. Support Claude, Antigravity and Grok through tested adapters. Discover actual models, efforts, tools and permissions from each installed harness; preserve requested versus observed settings and unknown values.
- Deliver branch review and bounded issue implementation → different-model review → correction → tests → lead disposition. Bind acceptance evidence to the exact candidate. Keep one writer per worktree, one lead per run and bounded retries. Implement ordinary and adversarial review distinctly.
- Make every listed local surface operate on the same saved runs. Closing a client must not lose work. Provide status, results, events, cancel, safe resume and fenced host handoffs. Native transcript import is a capability-gated convenience; portable artifact handoffs are required across hosts.
- Route automatically among eligible model/effort/tool profiles, with exact per-role pins and explicit fallback. Account for shared subscription pools, every applicable quota window, latency, unknown/stale observations and local concurrency. Maximize verified outcomes and useful capacity; do not force every provider into every task.
- Implement stable profile aliases, automatic catalog refresh, last-good snapshot retention, bounded qualification/trials, guarded promotion and rollback. Model discovery alone must not change defaults. Freeze run bindings and concrete pins. Ship/test `guarded_auto` as an opt-in after calibration; permission or billing changes stay outside it.
- Record all attempts, failures, fallbacks, reviewer contributions, lead repairs and later corrections. Generate receipts, handoffs, evaluation/decision reports and ongoing documentation. Separate worker invocations from native internal model calls and quota usage. Proposed improvements need comparable evidence.
- Implement C1 using independent proposals, a distinct critic and the existing lead. Preserve dissent, evidence and budget limits. Test manual Council use; keep automatic triggering disabled until its evaluation gate and policy authorize it.

## Make ordinary use simple

Ship a guided local setup that discovers existing installations/authentication, explains readiness and configures DevSquad's required local integration without asking me to maintain a model matrix. Preserve unrelated app settings and credentials. Provide a clean readiness report when a capability cannot be verified.

Deliver this small human-facing command surface over the same service and contracts:

```text
squad setup
squad doctor
squad review --base main
squad fix "the bounded issue to resolve"
squad council "the decision to evaluate"
squad status [RUN]
squad result [RUN]
squad cancel RUN
squad resume RUN
```

These are target commands to implement, not commands that exist at handoff creation. Preserve the specified low-level JSON/API operations for automation. Optional omitted run IDs resolve only when the current project has an unambiguous relevant run; otherwise present the choices. Never silently target an unrelated job.

Normal review/fix/council entry must not require manually writing JSON. In an app, the current lead constructs the task. In terminal, use the configured single headless lead, where necessary, for one bounded task-preparation step against approved templates and allowed checks. Record that preparation, its budget and output. Validate criteria, scope and commands before execution; generated task text cannot expand permission or spending authority. Reuse the same planning authority rather than spawning a second lead. Retain committed-input constraints unless explicitly amended with equivalent tested snapshot guarantees.

Show the selected roles/profiles, why they were chosen, progress, the run ID and a clear next action. Provide one verified command per normal operation. Errors should say what failed, whether work is still running and how to recover. Keep model IDs, raw schemas and protocol details out of the normal user flow unless they help resolve an issue.

Do not add a dashboard, cloud service, arbitrary DAG builder or extra configuration layer merely to present these features.

## Execute in small verified increments

Follow the dependency graph. Demonstrate M3 early, then keep going through M4/M5/M6/M7 and C1. Delegate bounded implementation/review/test tasks when useful; isolate concurrent edits and keep ownership clear. A delegated failure does not remove the requirement: continue independent work and use available execution paths.

Use offline fake CLIs/protocol servers for development and fault injection. Use existing authenticated subscription harnesses for bounded real smoke tests and the required live acceptance demonstrations. Recheck current official capabilities at integration time. Do not purchase credits, consume usage resets, silently switch to paid APIs, publish, push, merge, deploy, send external messages or change unrelated account settings under this assignment.

Local implementation dependencies, an isolated DevSquad installation and required local MCP registrations are part of delivery. Make them reversible/idempotent and preserve unrelated configuration. Handle authentication through normal provider flows. If quota/authentication or an unavailable host blocks a live gate, record the exact blocker, finish all independent work, and ask only for the missing action needed to complete that gate. Do not hammer a limited provider or substitute fixture results for live proof.

## Test behavior thoroughly

Derive a requirement-to-evidence matrix before implementing. Preserve every milestone gate in that matrix; tests must establish behavior, not mirror code structure. Run the existing `bash test/run.sh` before every commit as required, plus meaningful new tests for the changed behavior. The historical 177 assertions are a regression baseline, not evidence that the new product works.

Required coverage includes:

1. **Adapters:** argv/path handling including spaces and TSX; model/effort validation; auth/rate/timeout classification; empty/malformed/denied exit-0 results; native protocol events, disconnects and interruption; permissions and recursion guards.
2. **Durability:** concurrent idempotent starts, conflicting bodies, transactional events/artifacts, migrations, supervisor crash with a live child, reused PID protection, no duplicate writer, cancel/reap, restart/resume and stale host claims. Use real controlled subprocesses where fake clocks cannot prove cleanup.
3. **Engineering outcomes:** seeded defects detected by independent review, bounded repairs, mandatory failing tests blocking acceptance, report-only failures remaining visible, stale-patch evidence rejection, scope enforcement and preservation of the user's checkout.
4. **Routing/capacity:** deterministic selection from identical snapshots; exact pins/fallback; two projects sharing one pool; short and weekly windows; stale/unknown data; external account consumption; blocked paid-API fallback; native usage versus worker-launch counts.
5. **Model lifecycle/learning:** pagination, incomplete discovery preserving last-good data, affected-profile revalidation, same-ID uncertainty, qualified promotion, insufficient evidence, new-run-only binding changes, rollback, failed attempts later repaired and escaped-defect feedback.
6. **Council:** isolated first proposals, no author judging their own proposal, valid label mapping, missing participants, invalid critiques, recorded dissent and votes unable to override objective failure. Run the predeclared comparison and keep automatic use disabled when evidence is inconclusive.
7. **Packaging and real hosts:** fresh standalone install without Claude, plugin package contents, reinstall/update without duplicate hooks/MCP servers, active runs surviving package/client changes, and actual operation from terminal, Codex App, Claude Code App local Code tab, Antigravity and Grok Build. Parsing a config or passing an MCP unit test does not prove an app integration.

Run one genuine bounded issue through at least two harnesses with a different verified review model. Save exact candidate/check/review evidence. Exercise a real cross-surface start → observe → handoff/finish and a cancel/recovery flow. Each provider adapter and each named local surface needs its own supported-operation smoke receipt; do not force all providers into one job to satisfy that coverage.

Do a fresh-install usability walkthrough using only the quickstart and normal commands, without hand-editing task JSON. Fix setup friction, misleading status, confusing errors and documentation commands that fail. Obtain an independent implementation/UX review when available and resolve actionable findings; do not claim an independent review that did not occur.

## Completion and handoff back to me

Update `backlog.json` after each verified checkpoint with revision, command/action, result and portable redacted evidence. Keep a requirement matrix and concise implementation status beside it. Commit small verified increments, never stash work, and end with a clean tree. Keep secrets/raw private task content out of tracked evidence.

Before declaring completion, audit the full requested scope against the actual installed product. Fix failures and rerun the affected checks. A partial implementation, unavailable live gate or unsupported required host is incomplete even if offline tests pass. Record precise residual blockers rather than weakening the gate or marking it done. Do not wait indefinitely when no process is live; continue remaining independent work.

Deliver working code and local setup; reproducible tests and live receipts; an updated architecture/contract reference; a concise quickstart with one workflow visual; and a short recovery/troubleshooting guide. My final summary should state what works, exact commands to start using it, the evidence location, remaining limitations and the implementation branch/commits. Prefer a compact readiness table over a long narrative.

Start by inspecting the current state and implementing the earliest unmet dependency. Continue until this whole assignment is complete or the remaining requirements are explicitly blocked by external state that you cannot resolve.
