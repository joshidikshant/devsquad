# DevSquad: engineering team assessment and path to usability

Date: 2026-09-06

Baseline: main at fa68f68621dcad166ab24cf4b3db379ecbe819ac, plugin 0.10.0

Status: Assessment and proposed delivery plan. This document does not supersede ADR-001 or claim the proposed features are implemented.

## Verdict

DevSquad is worth continuing as a personal AI engineering team coordinator. It has useful provider integration code and regression tests, but it is not yet a dependable team that owns engineering outcomes or makes effective use of subscription capacity.

The intended product has two connected objectives: better engineering through complementary capabilities and independent judgment; and more accepted work from the subscriptions already available. Information access is one specialization alongside architecture, implementation, debugging, design, testing, and review.

The current product grew around preserving Claude's context. Its hooks, agent prompts, measurement, and capacity messages still express that earlier objective. Those choices explain much of the present mismatch. The next iteration should make a complete, verified engineering task the unit of work and measurement.

Keep the adapter experience, error taxonomy, compatibility tests, and the accepted shared-core direction. Deliver a small working team before extending platform scope. Five delivery gates below define that path; each requires observable behavior rather than completion checkboxes.

## Scope and evidence

Reviewed the local source, complete Git history, relevant local planning and verification records, GitHub branches/releases/workflows, installed plugin metadata and files, scoped DevSquad configuration/telemetry, and current official provider documentation. Earlier in this review, all 177 assertions in nine offline test files passed under Bash 3.2. Those tests establish selected implementation contracts, not live provider compatibility or output superiority.

GitHub main matches the baseline. The holdout-protocol branch is fully merged. Installed 0.10.0 plugin source matches the current plugin source. GitHub currently has no Actions workflows or published releases; the legacy v1.0 tag references a manifest numbered 0.1.0. Runtime code has not changed since July 7; later commits concern presentation, documentation, and motion assets.

The local probes inspected CLI help, versions, model listings, and model resolution. They did not execute paid model tasks or benchmark engineering quality. Grok reported expired authentication. The cached model list dates to July 6 and differs from the current Antigravity model list. Usage evidence is scoped to the directories inspected; its age does not prove that no AI work occurred elsewhere.

Historical planning records under .planning are local and gitignored. They were read as historical evidence, not treated as current instructions or proof that their claims remain true. Findings from those records are summarized here so the plan does not depend on readers possessing them.

## How DevSquad arrived here

| Period | What shipped or was recorded | What it teaches |
| --- | --- | --- |
| February 13 | Claude-oriented delegation plugin, marketplace restructuring, hook fixes, capacity reporting, acceptance tracking, and estimated token savings | Installation and execution wiring were part of the product problem from the beginning. Counting suggestions is different from completing delegated work. |
| February 18–19 | Git-health helpers, Gemini-to-Codex skill generation, and a real sequential shell workflow runner with gates/checkpoints | These are substantive utilities, but the unit of delivery became scripts and templates rather than completed engineering outcomes. |
| February 19 verification | A completion summary described the workflow as working end-to-end after syntax, JSON, grep, and dry-run checks. A separate verification record still called for real execution; a milestone audit identified cross-component runtime breaks | Structural verification was promoted into a stronger claim than the evidence supported. Future milestones need a recorded real run and its accepted artifact. |
| February 20–25 | Version renumbering, packaging corrections, and repeated validator/audit fixes through 0.3.0 | A source checkout working correctly does not establish that an installed plugin works in another project. |
| April 16 | 0.4.0 repaired new-project hook registration, paths, and noninteractive wrapper behavior | Fresh installation and ordinary project use must be acceptance tests, not post-release discoveries. |
| July 5–7 | Holdout experiment, Antigravity transition, Grok integration, common adapter, model catalog, expanded tests, and accepted ADR-001 | This is the strongest reusable engineering foundation. It also documents deployment drift, duplicated hooks, sparse observed delegation, and the need for a shared core. |
| July 7 onward | 0.10.0 maintenance release followed by README, growth, and motion work; August's main change is README-only | The accepted core/manifest/dispatch plan did not reach implementation. The next milestone should be smaller and centered on use. |

Supporting commits include [initial implementation](https://github.com/joshidikshant/devsquad/commit/ac797bb), [February milestone](https://github.com/joshidikshant/devsquad/commit/a480e5a), [April fixes](https://github.com/joshidikshant/devsquad/commit/b98558e), [shared adapter](https://github.com/joshidikshant/devsquad/commit/5e6b08b), [ADR acceptance](https://github.com/joshidikshant/devsquad/commit/3a96bef), and [0.10.0](https://github.com/joshidikshant/devsquad/commit/5afc2f0).

The July 5 routing record reports four completed delegation usage records plus test artifacts across its examined history. ADR-001 uses a different scope and describes approximately one meaningful completion and zero accepted suggestions out of 80. These are dated historical observations, not a new September adoption count. They support demanding better completion evidence; they do not establish that users or models reject the team concept. See [routing history](../../ROUTING-CHANGELOG.md) and [ADR-001](../adr/ADR-001-contract-and-ledger-core.md).

My interpretation: DevSquad has accumulated substantial integration knowledge, but the feedback loop from ordinary use to accepted outcomes is weaker than the planning and review loop. More architectural discussion alone will not close that gap.

## Readiness against the engineering team objective

| Requirement | Current state | Practical consequence |
| --- | --- | --- |
| Reliable provider execution | Three thin wrappers and a common adapter; incident tests exist, but process cleanup and semantic success need work | Useful foundation, not yet a reliable unattended execution boundary |
| Interchangeable team leadership | Claude-specific hooks and eight Claude Sonnet relay agents; no neutral job command or Claude worker adapter | Codex/Astra cannot use the same team interface symmetrically |
| Roles matched to capabilities | Coarse keyword routes, hardcoded hook choices, mixed model families behind Gemini role names | Actual tools and model diversity do not reliably determine assignment |
| Complete implementation ownership | Agents are framed around short drafts; workflow runner executes shell strings | A coding agent is underused when a task needs sustained implementation and validation |
| Shared work and handoffs | Project state and text responses; no durable task/result contract or dependency-aware handoff | Recovery and integration depend on the lead reconstructing context |
| Independent review and QA | No explicit reviewer lane or acceptance gate in the core | An exit code or plausible explanation can be mistaken for a successful outcome |
| Capacity allocation | Manual usage cache and advisory messages; fixed cooldowns; Grok omitted from capacity schema | Capacity reporting does not yet allocate jobs or maximize usable subscription work |
| Safe parallel engineering | Some session-scoped counters, but shared mutable state and whole-tree checkpoint behavior remain | Parallel writers can collide or lose accounting; isolation must precede concurrency |
| Quality and value measurement | Character counts, suggestion outcomes, and Claude-focused holdout reconciliation | There is no evidence yet that the proposed team improves accepted engineering output |

## Immediate blockers

1. **Model identity is unreliable.** With the inspected local catalog/configuration, Gemini researcher/developer frontier pins resolve to Claude Opus 4.6 through Antigravity. The algorithm ranks cross-family numeric version strings. Preserve model family intent and distinguish harness, model provider, requested model, observed model, available tools, and account pool. See [model-catalog.sh](../../plugin/lib/model-catalog.sh), especially resolve_model_tier.
2. **Hook routing bypasses configured routing.** Reading and WebSearch are assigned to Gemini and tests to Codex directly. Unify this policy with workflow/manual dispatch. The lead can specify a role and required capabilities explicitly, avoiding brittle natural-language classification without adding another routing model. See [pre-tool-use.sh](../../plugin/hooks/scripts/pre-tool-use.sh) and [routing.sh](../../plugin/lib/routing.sh).
3. **Directory context is incomplete.** The file whitelist omits TSX, JSX, and other common source types; it would omit all nine TSX files in this project's motion source directory. String-split file arguments also mishandle spaces. Use an explicit file manifest, honor ignore rules, and report omissions and size limits. See [gemini-wrapper.sh](../../plugin/lib/gemini-wrapper.sh).
4. **The portable watchdog can add its full timeout to successful captured calls.** This Mac has no timeout/gtimeout binary. An immediate fake CLI response took 2.025 seconds with a two-second bound because the watchdog's sleep retained the capture pipe. Fix process/descriptor cleanup and verify elapsed-time behavior, timeout termination, and cancellation. See [adapter.sh](../../plugin/lib/adapter.sh).
5. **Process success is not task success.** Output may be empty, a tool may be denied, tests may never execute, or the requested artifact may not exist despite exit zero. Normalize execution status separately from acceptance, preserve provider events where supported, and require verifiable artifacts/checks.
6. **State and Git checkpoints need ownership.** JSON array rewrites can lose concurrent updates. Project-wide pending suggestions and workflow state mix sessions. Checkpoints stage all files and suppress commit failure. Use isolated run state, serialized event writes, scoped worktrees, and truthful checkpoint outcomes. See [usage.sh](../../plugin/lib/usage.sh), [enforcement.sh](../../plugin/lib/enforcement.sh), and [lib-workflow.sh](../../plugin/skills/workflow-orchestration/scripts/lib-workflow.sh).
7. **Installed-version behavior needs a health check.** Grok needs reauthentication. Local Antigravity/Grok versions must be checked against the features used, rather than inheriting assumptions from current documentation. Local duplicate DevSquad hook registration is presently absent; the installer/onboarding registration paths still need an idempotence test so this historical defect is not reintroduced.

These blockers justify targeted fixes, not a wholesale rewrite. The exact affected behavior should have a regression test before being labeled repaired.

## What “usable” must mean

The first release should let the user state a bounded engineering objective from Codex or Claude, then receive a tested patch and an independent review without manually copying prompts between products. A provider becoming limited or unavailable must preserve the work, select a suitable alternative when available, and explain the resulting state. A restart must resume or truthfully report the interrupted task.

Minimum observable experience:

1. Inspect the available team once: runtime versions, authentication state, verified capabilities, and capacity freshness.
2. Give the lead an issue, a repository, and acceptance criteria in ordinary language.
3. See a compact plan with accountable roles; only independent work runs concurrently.
4. Receive progress when a result, failure, handoff, or decision matters.
5. Receive the patch, reviewer findings/dispositions, checks executed, unresolved limitations, and a compact capacity receipt.
6. Continue that task from the same saved artifacts if execution stops or leadership changes.

The user should not have to choose every model, prepare workflow JSON for each task, re-explain the repository after every handoff, or repeatedly enter usage percentages. Natural-language interaction belongs to the active lead; the core should execute explicit structured jobs beneath it.

The first workflow should be one real issue to implementation, independent review, correction, and test verification. Use two model families initially; add Grok or Gemini specializations when the issue benefits from them. A mandatory four-provider chain is not a requirement. A passing dry-run, generated skill scaffold, or impressive research report is not this milestone.

## Minimum architecture

Keep the runtime in the installed plugin package. Expose the existing wrappers through a small executable interface; thin Claude and Codex integrations call the same core. Existing native harnesses continue to own their agent loops, credentials, tool execution, and provider sessions. DevSquad owns job assignment, state, policy, artifacts, acceptance, and capacity accounting.

Proposed interfaces, not current commands: squad doctor; squad invoke; squad run; squad status; squad resume; squad report. A thin Codex skill can use the CLI first. MCP is an optional later transport when actual host integration needs it, not a prerequisite.

Four small contracts are sufficient to start:

| Contract | Essential fields |
| --- | --- |
| Task | task/run ID, objective, repository and base revision, role, required capabilities, input artifacts, acceptance criteria, allowed operations, dependencies, deadline/budget constraints |
| Adapter capability | harness and installed version, model provider/family, requested/observed model, tool capability, verification status/date, auth route, account pool, output format, permission profile |
| Result/handoff | task/attempt ID, execution status, provider session ID if available, model/tool evidence, worktree and patch/artifact references, checks and results, reviewer findings, remaining work, source evidence when relevant, duration and measured usage |
| Capacity observation | account pool, applicable limit windows, remaining/used value when available, reset time when known, observed time, source, freshness, confidence, unavailable reason |

Separate execution states from acceptance states. An attempt can exit successfully while its task remains unverified or needs revision. Distinguish failed, interrupted, waiting for quota, blocked capability, and awaiting a user decision. Do not turn every failure into a generic text response.

Use one worktree and one writer at a time per implementation task, with a read-only reviewer over a recorded revision. Additional independent tasks can get their own worktrees. A handoff includes the base revision, current diff, relevant decisions, completed checks, outstanding failures, and next action; it does not pretend that hidden model context transfers between providers.

Store per-run artifacts and events. A JSONL file is not automatically a concurrency solution: choose a serialized writer or explicit locking, stable event IDs, and crash-safe updates. Derived dashboards or reports can be rebuilt from those records.

## Subscription capacity is a scheduling input

Current capacity reporting asks users for quartile ranges and stores their midpoints. The schema omits Grok; recommendation logic does not use Codex's weekly value to choose its displayed zone and does not connect to job routing. Missing values can appear as zero usage. This is a reporting aid, not an allocator. See [capacity command](../../plugin/commands/capacity.md) and [usage.sh](../../plugin/lib/usage.sh).

The next scheduler should:

- Establish capability and minimum quality eligibility first, then consider fresh availability, relevant limit windows, resets, latency, and the role preference order.
- Group executors by their actual account/limit pool. Codex app and CLI are not two budgets merely because they are two interfaces. Model identity also does not establish that two runtimes share a pool.
- Represent unknown capacity as unknown. Distinguish subscription limits, model context occupancy, per-call tokens, temporary cooldowns, and paid API spending.
- Prefer supported automatic observations. Accept a timestamped manual observation when necessary, with honest precision; do not require user input before every dispatch.
- Track account-level capacity across projects, while keeping task state per run. Coordinate concurrent launches and use conservative concurrency caps when remaining capacity cannot be measured precisely.
- Preserve capacity for the lead and final review when the user needs them. Fill independent work with other capable providers where useful; do not spend quotas simply to achieve utilization.
- On rate limit, checkpoint before a compatible handoff; on authentication failure, mark the adapter unavailable until repaired. If a required unique capability has no alternative, report that limitation rather than silently substituting an unsupported answer.
- Keep subscription execution and separately billed API paths explicit. Adding a video API connector should not silently change the payment route of ordinary work.

The objective is accepted engineering work per available subscription window, under a quality floor. Neither raw token savings nor equal use of every provider captures that objective.

## Five delivery gates

These are acceptance gates, not five large releases or calendar promises. Gates 1–3 can ship together as one narrow vertical slice using two already-working harnesses: repair the execution/context defects that affect that task, add only its required invocation/results boundary, and finish the task. A read-only branch-review command can provide utility even earlier. Full registry migration, all-provider support, and a new Claude worker adapter must not block that first loop; add the Claude worker when the chosen team needs it. Complete the wider provider matrix, installation hardening, and CI before the daily-use release. Gate 3 provides an early usable engineering loop; Gate 4 delivers the combined team-plus-capacity proposition. Gate 5 determines whether it is ready to become the default daily workflow.

| Gate | Deliverable | Required acceptance evidence |
| --- | --- | --- |
| 1. Trustworthy execution | Repair execution/context/model issues exercised by the first two harnesses; report their health and use explicit role permissions | Existing suite passes; targeted regressions cover the selected path's elapsed time, cancellation, source manifest, model identity, and denied/empty output; supported-version smoke runs are recorded |
| 2. Shared jobs and results | Package a neutral executable around the needed adapters, with minimal manifests, structured task/results, and durable per-run artifacts | The same bounded job is callable from Claude and Codex; actual/unknown model identity is reported honestly; failure retains artifacts; the packaged interface works outside the DevSquad checkout |
| 3. Complete engineering loop | One issue to plan, isolated implementation, different-model review, bounded revision, and executed checks | A real patch satisfies its predefined criteria; review findings are resolved or explicitly dispositioned; failed prerequisites block dependents; no manual prompt ferrying is required |
| 4. Capacity and recovery | Pool-aware eligibility, fresh/unknown capacity, bounded fallback, checkpointed handoffs, resume, and controlled concurrency | Simulated rate limit and authentication failure are distinguished; a handoff preserves existing edits and checks; a shared pool is not double-counted; required capabilities survive fallback; interrupted tasks resume without duplicate application |
| 5. Daily-use validation and release | Representative task trials, doctor/CI coverage for all advertised adapters, coherent install/version/release procedure, and user-facing evidence | Clean-install smoke verifies one hook registration; advertised provider paths are checked; pilot records quality, completion, rework, intervention, time, and capacity evidence; runtime/package versions match; limitations are published with a tagged release |

Use isolated fake providers for failure-injection tests. Live smoke calls establish what the real installed providers can execute; they should not deliberately exhaust quotas or invalidate working credentials. Repeated real use is necessary to establish reliability beyond either kind of test.

The shortest useful proof is a modest bug fix or feature in an existing repository. Avoid using DevSquad's own skill generator as the only proof: that narrows evaluation to scaffolding, mixes generated content with plugin internals, and fails to exercise ordinary engineering ownership.

## ADR-001: preserve the foundation, revise the product assumptions

Preserve its Bash-compatible runtime, executable JSON boundary, in-plugin packaging, thin adapters, deterministic policy, ledger, and evidence before learned routing. Existing callers should remain compatible while the new path is tested.

Propose a short follow-up ADR covering these changes rather than editing the accepted record to imply past agreement:

1. Both Claude and Codex may lead; either may serve as an external worker/reviewer.
2. The first product workflow is engineering delivery and independent verification. GrowthSquad and a compulsory three-vendor demo move later.
3. Task acceptance and subscription utility become first-class outcomes. D1 remains an experiment about Claude delegation behavior, not the survival criterion for the whole team product.
4. Fallback preserves capability requirements. Unconditional terminal self-answering is not acceptable when the task requires evidence or operations the lead cannot perform.
5. Role-specific permissions replace blanket approval-bypass defaults; scoped acceptance policies should respect the user's already-authorized work.
6. Model choice uses identity, actual capabilities, and evidence rather than cross-family version sorting or a fixed historical latency floor.

Do not pre-approve a language rewrite, licensing change, separate repository, marketplace, hosted service, universal provider registry, learned model router, or parallel multi-writer system. Add each only when observed use creates a specific need. Role templates can start small; a generator is not required for the first team loop.

## How to test the combination thesis

Start with roughly 10–15 representative tasks across bug fixes, small features, refactors, test improvements, and an occasional task requiring external evidence. This is a usability pilot, not a statistically conclusive benchmark.

Compare DevSquad with the strongest single-agent workflow the user already uses. Keep task scope, starting revision, acceptance criteria, and available evidence comparable. Review resulting patches/reports without provider labels where practical. Judge criteria established before seeing the outputs; account for repeated-task learning when interpreting results.

Record accepted completion, defects/omissions, human correction and intervention, time to a verified result, work preserved after interruption, and available quota observations. Capture provider usage when exposed, otherwise mark it unknown. Do not infer consumed subscription percentage directly from character counts.

Test three separate sources of benefit: complementary tool access, independent design/review judgment, and continuity when one provider is constrained. A failed token-saving experiment does not refute all three; conversely, using more models does not prove any of them.

Release decisions should name task classes where the team helps, where the direct single-agent path is preferable, and where the evidence is insufficient. Keep that fast direct path as part of the product. If review repeatedly adds no useful findings, reduce that lane for the relevant task class. If a provider contributes unique evidence or preserves progress under limits, record that value explicitly.

## Provider facts relevant to the implementation

Current official documentation supports native headless execution for all four families of harness. Exact flags, event dialects, permissions, and telemetry vary by installed version; the adapter must verify that contract rather than assume interchangeability. [Claude headless](https://code.claude.com/docs/en/headless), [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode), [Grok Build headless](https://docs.x.ai/build/cli/headless-scripting), [Antigravity headless](https://antigravity.google/docs/cli/headless/).

Grok Build documents X search. Gemini video understanding through its API supports YouTube URLs, but that does not establish the same path in the installed Antigravity CLI. Record separate web, X, transcript, and video capabilities. [Grok changelog](https://x.ai/build/changelog), [Gemini video input](https://ai.google.dev/gemini-api/docs/video-understanding).

The repository's blanket statement that Gemini CLI was decommissioned is too broad: Google's June transition affected individual free/AI Pro/Ultra access, while enterprise and paid API access remain supported. Correct the account-specific wording when updating setup documentation. [Google transition announcement](https://developers.googleblog.com/en/an-important-update-transitioning-gemini-cli-to-antigravity-cli/).

## Recommended immediate next implementation

Begin with Gate 1 against an isolated fixture repository, then the shared job/result interface and one issue-to-reviewed-patch workflow. Seed the new tracked delivery record with the five gates above and their evidence links. Keep February's local planning records as history, not the active completion dashboard. Make installed-package verification and one real accepted run part of every milestone that claims end-to-end usability.

The next proof should be a user task delivered by the team with less coordination burden, preserved work during a provider handoff, and a result that passes independent checks. That is the path from the existing delegation plugin to a usable engineering team.
