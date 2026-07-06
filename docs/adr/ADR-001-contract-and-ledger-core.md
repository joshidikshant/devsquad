# ADR-001: Contract-and-Ledger Core (response to the Prebid-style platform brief)

- **Status:** Proposed (awaiting DJ's review)
- **Date:** 2026-07-06
- **Author:** Fable 5 (Claude Code), synthesizing 4 independent architecture proposals + adversarial critiques (13 agents, ~1.16M tokens) against the v0.8.0 codebase
- **Input:** Grok's "DevSquad → Prebid-Style Multi-Model AI Engineering Platform" brief (2026-07-06)

---

## 1. Verdict on the brief

The direction is right; the analogy needs surgery. Adopt Prebid's **governance mechanics** — thin deterministic core, adapter contract, conformance suite as the standard-setting artifact, config-driven composition. Reject Prebid's **adoption physics** — they don't transfer:

1. **Bids are commensurable; model outputs are not.** Prebid's core compares dollar scalars mechanically. There is no quality scalar to route on here — the repo has recorded ~1 real completed delegation with meaningful output, 0 accepted suggestions out of 80, and a live experiment (D1) that may show enforcement doesn't pay. A "scored router" today would be noise laundered as intelligence.
2. **Prebid's adapters were written by vendors chasing revenue.** Our vendors are at best indifferent and at worst hostile (headless CLI driving is ToS-gray; Gemini CLI was decommissioned mid-project). Adapter supply must come from a community that doesn't exist yet. The conformance suite is a *test suite* until ≥3 external contributors make it a *standard*.
3. **agy already multiplexes 3 vendors' models behind one CLI.** Per-vendor API translation is being commoditized. The durable adapter value is NOT translation — it is the **error taxonomy, bounded execution, cooldowns, accounting, and quirk knowledge** that agy does not give you (it silently ignores unknown model names — the opposite of a contract).

**The central bet, therefore: the durable spine is the CONTRACT plus the LEDGER — not routing intelligence, not enforcement.** Bounded, classified, conformance-tested invocation across heterogeneous subscription CLIs, with append-only per-call accounting. That spine is valuable under **either** D1 outcome: on PASS it powers strict enforcement; on FAIL it *is* the honest capacity/budget manager the repo's own architecture doc names as the fallback. Enforcement is the falsifiable hypothesis under test; do not build the platform on it.

## 2. Assessment: DevSquad v0.8.0 vs the vision

DevSquad is **~30% of the vision by code but ~70% by hard-won knowledge**. All four review lenses converged on this inventory.

### Already the Prebid-core in embryo (keep, promote)

| Asset | Why it matters |
|---|---|
| `plugin/lib/adapter.sh` (182 lines) | Genuinely vendor-neutral invocation: cooldown gate, tier-aware model resolution, bounded exec (timeout binary or portable watchdog, 143→124 normalization), auth-before-rate classification, per-call telemetry. This IS an adapter interface — just expressed in bash dynamic scope. |
| D4 result contract + `test/test_wrapper_contract.sh` | 38 offline assertions against fake CLIs, pinning incident-derived behavior (the 'mig-rate' ordering bug, grok's exit-0 sign-in banner). **The single most Prebid-like artifact in the repo** — a real conformance suite. |
| `plugin/lib/model-catalog.sh` | Undersold everywhere: tier:fast/tier:frontier intent-pinning against a live machine-local catalog with drift changelog and detached refresh. The anti-churn layer a multi-vendor ecosystem needs; nobody else ships it. |
| The measurement stack | usage records, contracts.log, compliance.log, the pre-registered D1 holdout with a published decision rule. The experiment discipline itself is a brand asset no funded competitor has. |
| `routing.sh`'s 3-layer split | keyword→category, category→vendor (config `default_routes`), vendor→agent. The layers are right; only the last is hardcoded. |
| `run-workflow.sh` | The most vendor-neutral component: atomic state, checkpoint hashes, dry-run, continue-past-failure. Right bones, starved of step types. |

### Missing (the actual gap to the vision)

1. **An exec/JSON process boundary.** The contract is bash-source-only with dynamic-scoped globals — unusable from any other language, host, or even another shell safely.
2. **Adapter self-description.** Adding a CLI touches 7–10 files per `docs/ARCHITECTURE.md`'s own recipe. Capabilities, latency floors, quirks, PATH bootstraps, and binary aliases are tribal knowledge smeared across cli-detect.sh, wrappers, and prose.
3. **Routing as data.** The 5-category × 4-vendor case matrix lives in code, with **four** divergent copies (routing.sh, the stale devsquad-dispatch SKILL.md, pre-tool-use.sh's hardcoded suggestions, enforcement.sh's agent→CLI map).
4. **Workflow ↔ router connection.** run-workflow.sh's only step type is `eval` of a shell string; a workflow cannot say "route this to the configured researcher."
5. **Role/vendor de-fusion.** 8 near-duplicate agent .md files fuse role×vendor into identity.
6. **Latency as a routing input.** grok's measured ~150s floor exists only as a default timeout argument, not as data a router can filter on.
7. **Outcome measurement.** Hooks structurally cannot see whether a delegation succeeded; D1 failures are a manual flag. Only a workflow engine that invokes adapters itself can close the loop automatically.

### Throw away (with two corrections from adversarial review)

- routing.sh's case matrix (→ data), the SKILL.md routing copy, the 8 fused agent files (→ generated), capacity.md's manual quota quartiles (12/37/62/87 midpoint theater).
- **Correction 1:** do NOT simply delete the `~/.claude/stats-cache.json` reader. It is confirmed dead (no writes since 2026-06-16; daily zone permanently green) — but it is the only Claude-side *daily budget* signal, and the D1-FAIL product ("capacity/budget manager") needs one. Replace with a measured source, then delete.
- **Correction 2:** do NOT delete `stop.sh` — repurpose it. The acceptance-tracking blind spot (final suggestion never resolves) can only be closed at Stop/SessionEnd; PostToolUse cannot observe what Claude does *next*.

### Verified-broken TODAY (found and confirmed during this review, 2026-07-06)

These invalidate the current D1 sample and gate everything else:

1. **Triple hook registration, double firing.** Global `~/.claude/settings.json` registers dev-mode `pre-tool-use.sh`; the installed plugin's `hooks.json` registers it again; `devsquad/.claude/settings.local.json` points at a nonexistent `production/` path. Live evidence: Projects-root `holdout.log` shows paired duplicate entries in the same second. Consequences: read counters increment twice (the 20-read threshold is effectively 10), treatment sessions pay double context injection, holdout events double-log.
2. **D1 sample is tiny and skewed:** n=2 sessions, both control; this architecture-review session itself flooded the log with unrepresentative workload. `compliance.log` lines carry no session_id, so per-session conditioning is impossible.
3. **Dead model pin:** `.devsquad/config.json` pins `gemini_model: "gemini-1.5-flash"` — a dead name agy silently ignores on every call. The ledger's model labels are unverifiable for the most important adapter (agy multiplexes 3 vendors; a per-adapter quality score conflates Gemini Flash, Claude Opus, and GPT-OSS).
4. **The referenced 2026-07-05 decision record (D1–D4, L3)** is cited from five places and exists nowhere in the repo.
5. **usage/<agent>.json is a jq-rewritten JSON array** — O(n) per call, last-writer-wins under concurrency. A lossy store cannot be the telemetry moat.
6. **Packaging boundary:** the installed plugin cache contains ONLY `plugin/` contents. Any `core/` at repo root is invisible to installed agents/skills. This must be decided before any extraction: **the core lives INSIDE `plugin/`**.

## 3. Decision: target architecture

### 3.1 Runtime: bash stays; the boundary is the innovation

The core remains bash 3.2 + jq, exposed through one new artifact: **`plugin/core/bin/squad`** — an exec entrypoint wrapping the existing sourced functions as subcommands emitting JSON envelopes. Two independent proposals argued for Go/TypeScript rewrites; both critiques broke them on the same facts: a 10–14-week solo rewrite against a repo whose history shows bursty availability (a 5-month production freeze at 0.3.0; v0.5.0–0.8.0 all dated the same day), losing the incident-hardened code that is the asset. The exec/JSON boundary makes the language swappable later, per module, if evidence ever demands it. **The concession to the future is architectural, not linguistic.**

### 3.2 The adapter contract (brief question #2)

An adapter is a directory: `adapter.json` (manifest) + executable. The existing bash wrappers are **wrapped, never rewritten**.

```
plugin/core/adapters/grok/adapter.json
{
  "schema": 1,
  "name": "grok",
  "binary_candidates": ["grok"],
  "path_bootstrap": ["~/.grok/bin"],
  "capabilities": { "kinds": ["research","generate"], "file_context": false, "web_search": true },
  "cost":   { "kind": "subscription", "marginal_usd_per_call": 0 },
  "latency": { "floor_ms": 150000, "default_timeout_s": 240 },
  "output": { "unit": "words", "default_limit": 300 },
  "models": { "listable": true, "list_cmd": "grok models", "validates_model_arg": true },
  "auth":   { "hint": "run: grok login", "banner_re": "signing in with grok|not authenticated" }
}
```

- The manifest absorbs everything currently duplicated as tribal knowledge: agy/antigravity aliasing, PATH bootstraps, the words-vs-lines split (declared, not silently unified), latency floors, and agy's `validates_model_arg: false` (its silent-ignore pathology becomes checked metadata).
- Invocation: `squad invoke <adapter> --prompt-file <f> [--json]`. Without `--json`: exactly today's D4 behavior, so every existing agent prompt and test keeps working. With `--json`: one envelope —
  `{"ok":true,"adapter":"grok","model_requested":"grok-build","model_echo":"unknown|verified","output":"…","latency_ms":152340,"chars_in":812,"chars_out":1490}`
  or `{"ok":false,"error":{"code":"RATE_LIMITED","message":"…","retry_after_s":120,"fallbacks":["agy","self"]}}`.
- **The four-value error enum `RATE_LIMITED|AUTH_ERROR|TIMEOUT|CLI_ERROR` is frozen. It is the ecosystem seam.**
- `model_echo` is mandatory because of agy: a ledger that records unverifiable model labels is not "fully replayable." Model names validate against the live catalog (`~/.devsquad/models.json`), never against static lists in manifests (declared numbers rot).
- One behavioral fix ported from review: **AUTH_ERROR excludes the adapter for the session; RATE_LIMITED gets the cooldown.** Today's uniform 120s cooldown pretends auth heals.
- Conformance: `test_wrapper_contract.sh` becomes **discovery-driven** (`for a in adapters/*/adapter.json`) instead of enumerating wrappers. A conforming adapter = manifest validates against a checked-in JSON Schema + passes the full fake-binary suite. Adding a CLI = one directory, two files, one green test run — the Prebid submission model. Honest note: this is a *port*, not an extraction; the 38 assertions certify bash-sourced functions today and must be rewritten against the exec protocol. What carries over is the case taxonomy — which is the valuable part.

### 3.3 Routing (brief question #3)

**Filter → static table → fallback chain. No scoring function. No learned router.** The D2 kill rule (<50 decisions/day sustained → stay static) already decided this on evidence; measured executed-delegation volume is ~0.4/day, ~25× under any router-worthiness bar.

- The table moves from code to data: `plugin/core/routes/routes.json` — categories with keywords, ordered adapter preference, limits, and a `needs` capability list. Extraction ships **behavior-identical** to today's defaults; any route change is a separate, dated ROUTING-CHANGELOG entry (extraction ≠ route change).
- Filter pipeline per decision: adapter installed → not in cooldown → capabilities ⊇ needs → **`latency.floor_ms` ≤ the caller's latency budget**. That last filter is the single highest-value new rule: it mechanically encodes "grok never gets interactive work, stays eligible for 300–600s batch steps" as data instead of tribal knowledge.
- Every decision appends to `.devsquad/logs/decisions.jsonl` (`decision_id`, candidates considered, reason). `squad outcome --decision-id … --result success|failure` closes the loop; workflow-invoked steps close it automatically.
- **Learning is offline and human-governed:** `squad report` joins decisions to outcomes and prints per-category × adapter success/latency/cost tables; a human edits routes.json citing the report in a dated changelog entry. Pre-registered gate for ever building a learned router: ≥50 decisions/day sustained for a month AND a persistent measured-vs-table divergence a human keeps correcting.
- Runtime fallback: RATE_LIMITED/TIMEOUT walks the decision's fallback chain; AUTH_ERROR excludes for the session; terminal fallback is always `self` — never a dead CLI (the Gemini-CLI decommission is doctrine).
- Host translation: `squad route` returns neutral JSON; the claude-code host renders `@agent-name` strings. The @-mention syntax leaves the core.

### 3.4 Workflows (brief question #4)

Extend `run-workflow.sh` in place — its bones (atomic state, checkpoint hashes, dry-run) are right. Five changes, all back-compat (absent `schema` = today's shape):

1. `"schema": 1` + declared `"inputs"` (validated presence; no more ambient env vars).
2. A **`dispatch` step type** — the missing link: `{"type":"dispatch","category":"research","prompt":"…","latency_budget_s":300,"output":"notes"}` runs `squad route` + `squad invoke`, and **auto-records the outcome** — the first automatic quality signal in the system.
3. Step outputs passed as files (`${steps.<id>.output_file}`), not string interpolation — note the existing `_expand_vars` (envsubst/perl) cannot expand dotted names; a small custom expander is required and budgeted.
4. Approval as policy: `--approve <ids>` / `DEVSQUAD_APPROVE_ALL=1` / policy file alongside the `/dev/tty` read (which silently skips every gate headless today). Declined steps exit 2, failed exit 1 (today both conflate into "partial").
5. Argv-array exec steps as the recommended form, retiring `eval`-string injection over time.

Deliberately NO registry, NO include/extends, NO marketplace until two real workflows are in weekly use. **GrowthSquad is not a second codebase — it is one file, `workflows/growth-weekly.json`** (trend-scan on grok with a 600s budget → research on agy → synthesis → draft), plus a `trend_analysis` routing category. If the same core runs an SDLC workflow and a marketing workflow for a month, "two squads share the harness" is demonstrated, not asserted. DAG parallelism is added when GrowthSquad demands it (a serial 4-step grok workflow is ~10 minutes; that demo would damage credibility).

**GrowthSquad model→moat mapping (brief question #5):** trend detection = grok (realtime X/web; latency-tolerant batch lane), research/audience synthesis = agy:gemini tiers (search/YouTube grounding, 1M context), creative strategy & final synthesis = Claude (the calling session — `self` routing; long-horizon reasoning), copy variation = agy:gpt-oss (cheap variation). Distribution/measurement steps are exec steps invoking existing tooling — NOT fictional adapters. (An honest `claude` adapter is possible later as headless `claude -p` with a recursion guard and its real cost declared; a `squad` binary cannot re-enter the calling session's Task tool, and skills are not executables. Any MVP demo claiming otherwise is fiction.)

### 3.5 Folder structure (end state; everything inside `plugin/` so installed copies work)

```
devsquad/
  plugin/                        # the Claude Code plugin = host package (whole dir is what ships)
    core/                        # ← the platform core (packaged; reachable by installed agents)
      bin/squad                  # exec entrypoint: invoke|route|adapters|outcome|report|workflow|catalog
      lib/                       # adapter.sh, model-catalog.sh, state.sh, usage.sh, routing.sh(→interpreter)
      adapters/{agy,codex,grok}/ # adapter.json + wrapper (existing *.sh, wrapped not rewritten)
      routes/routes.json         # the routing table as data
      schemas/                   # adapter.schema.json, envelope.schema.json, workflow.schema.json
    hooks/                       # host shim: parse Claude Code JSON, call squad, render @-mentions
    agents/                      # GENERATED from 4 role templates × adapter registry (checked in)
    commands/  skills/
  workflows/                     # workflow modules (pr-review.json, growth-weekly.json, …)
  test/                          # conformance suite (adapter-discovering) + hooks/routing/models tests
  docs/adr/                      # this file; the reconstructed 2026-07-05 decision record
```

Host boundary rule: transcript parsing, context zones, holdout split, acceptance tracking, @-mention rendering, and hook JSON codecs are **host code** (hooks/), never core. The core's only host assumption is `DEVSQUAD_PROJECT_DIR` (defaulting from `CLAUDE_PROJECT_DIR`).

## 4. Migration plan (brief question #1)

Every phase ships in days, keeps `bash test/run.sh` green, and is individually valuable even if the platform vision is abandoned. The D1 posture is **inverted** from all four original proposals, on evidence: the experiment is not sacrosanct — its instrument is broken (double-firing, double counters, no session_id, n=2). Protecting it as-is protects a corrupted measurement.

- **Phase 0 — Fix the instrument + hygiene (2–3 days).**
  Consolidate hook registration to ONE surface (dev-mode: global settings.json only; delete the dead `production/` project entry; document the choice vs plugin hooks.json and pin it). Add session_id to compliance.log lines. Repurpose stop.sh as the acceptance resolver for the final-suggestion blind spot. Fix the dead `gemini_model` pin (tier:fast). Check in the reconstructed 2026-07-05 decision record. Single-source the triplicated version string. **Declare the D1 clock restarted** (annotate holdout.log; exclude pre-fix sessions and this review session). CI for the offline suite.
- **Phase 1 — The exec boundary (2–3 days).**
  `plugin/core/bin/squad` wrapping existing functions: `invoke`, `adapters`, `usage`, `--json` envelopes. Convert usage store to append-only JSONL (migrate the arrays once). Start `decisions.jsonl` + `squad outcome`. No existing file changes behavior.
- **Phase 2 — Manifests + registry (2–3 days).**
  Write the three `adapter.json` files; registry-driven cli-detect/session-start/show-status; port the conformance suite to discovery + manifest schema validation; move PATH bootstraps and aliasing into manifests; add `model_echo` verification against the catalog.
- **Phase 3 — routes.json + latency filter (2–3 days).**
  Behavior-identical extraction of the case matrix; add installed/cooldown/capability/latency filters; collapse all four routing-table copies (including pre-tool-use.sh's hardcoded suggestions and enforcement.sh's map — safe now, because Phase 0 restarted the D1 clock on a clean instrument).
- **Phase 4 — Workflow dispatch steps (3–4 days).**
  Schema, inputs, dispatch type, file outputs + custom expander, policy approvals, exit-code split. Ship `workflows/pr-review.json` and `workflows/growth-weekly.json`, run weekly — this is what generates real outcome records.
- **Phase 5 — Role de-fusion (2 days).**
  Generate the 8 agent files from 4 role templates × registry (generated outputs checked in; update acceptance-tracking name matching and `agent_models` config keys in the same change).
- **Phase 6 — D1 fork (asynchronous trigger, NOT a serial gate).**
  When the restarted holdout reaches n≥20: PASS → strict mode + contract enforcement in the host shim; FAIL → `squad report` becomes the headline product, and the hook dials to observe/budget mode. Phases 1–5 are identical work under either verdict — robustness is structural, not rhetorical.
- **Phase 7 — Open-source split (gated on pull, not calendar).**
  Trigger: growth-weekly has run monthly AND ≥1 outsider asked to use `squad`. Extract core to its own repo, Apache-2.0, conformance suite as the contribution gate. Launch content: the pre-registered D1 writeup — published on either branch of the verdict — plus a recorded MVP run. Before that trigger fires, publishing a solo-maintained core buys maintenance burden, not ecosystem.

## 5. MVP (brief question #7)

**"One workflow, three vendors, one receipt"** — end of Phase 4, ~2–3 calendar weeks part-time.

`squad workflow run workflows/pr-review.json --approve all` against a real repo: dispatch(research, 240s budget) → grok (the latency filter visibly *selects* it because the budget allows the 150s floor); dispatch(reading, needs file_context) → agy with files piped via the existing stdin mechanism; dispatch(testing) → codex; shell → git-health. Then the per-step receipt from the ledger: adapter, **model_echo**, latency_ms, chars in/out, est. tokens at the measured 2.7 chars/token — with the "Claude tokens avoided" column explicitly labeled **estimate** (per the estimate-from-measured-data doctrine). Second pass: kill grok auth mid-run and show the clean fallback walk instead of a hang.

Why this beats the named competitors' demos: Cursor/Claude Code route within one vendor's stack and publish no routing rationale; Augment's Prism routes API calls inside its own margin — it structurally cannot arbitrage flat-rate subscriptions the user already owns (this demo's marginal cost: $0); CrewAI/LangGraph have no conformance contract, no failure taxonomy, no cooldowns, no receipt. **The receipt is the money shot — it is simultaneously the enforcement story (D1 PASS) and the budget-manager story (D1 FAIL).**

Kill criterion, pre-registered in the project's own culture: if after 4 weeks of dogfooding the ledger shows no routing deltas worth acting on and D1 fails, the correct conclusion is "the platform should not be built" — and the MVP was cheap enough that this is a fine outcome.

## 6. Moats (brief question #6), ranked by what a solo builder can actually mint

1. **The conformance suite as the certifying artifact** — incident-pinned cases (mig-rate ordering, exit-0 banners, watchdog normalization) competitors must rediscover the hard way. Whoever owns the certifying test owns the interface — *once there are parties who need certifying*.
2. **Subscription-level neutrality** — routing CLIs the user already pays for. Uncopyable by vendors (each wants lock-in) and by margin-taking API routers. Survives even if the ecosystem never materializes.
3. **Methodological credibility** — the pre-registered D1 holdout with a public FAIL branch. In a space drowning in unfalsifiable routing claims, "we tried to falsify ourselves" is the one marketing asset no funded competitor will copy.
4. **The quirk catalog** — manifests + model-catalog drift tracking as a living, tested database of vendor CLI pathologies.
5. **The telemetry corpus** — demoted from co-equal moat to *future option*: today it is one operator's ~30-records-a-fortnight; real as an accumulating asset only after workflow volume exists.

## 7. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| D1 FAIL (treatment self-dilutes via 3-suggestion cap; base-rate volume low) | Medium-high | Enforcement story dies | Structural: Phases 1–5 verdict-independent; FAIL branch is a designed product (budget manager), not a retreat |
| D1 verdict takes months (n≥20 at ~10 active days/5 months historically) | High | Blocks nothing if async | Phase 6 is a trigger, not a serial gate; restart clock on clean instrument now |
| Vendor kills/fences a CLI (Gemini CLI precedent; ToS-gray headless flags) | Medium | Adapter dies overnight | Adapters bound blast radius; terminal fallback `self`; support only CLIs DJ personally uses until contributors exist |
| Claude Code internals churn (hook schemas, transcript JSONL, plugin cache paths) | Medium | Silent breakage (hooks exit 0 on parse failure) | All host knowledge isolated in hooks/; session-start canary self-test; the 0.3.0 freeze is the precedent |
| Rewrite trap / second-system stall | Low (by design) | Two half-systems | No rewrite: bash stays, wrappers wrapped; every phase reversible by deleting new files |
| Deployment drift (dev-mode hooks vs installed plugin vs core version) | Medium | The 0.3.0 failure class | Core inside plugin/ (one package); one registration surface (Phase 0); `squad --version` in status output |
| Acting on tiny-n routing data | Medium | Noise laundered as intelligence | Human-governed routes.json + dated changelog; learned-router gate pre-registered (≥50/day sustained) |
| Solo-maintainer schedule (bursty availability, competing projects) | High | Phases slip | Every phase independently shippable; stopping at any phase leaves the system strictly better |
| Open-sourcing into a vacuum | High if premature | Maintenance burden, no ecosystem | Phase 7 gated on demonstrated external pull |

## 8. Orchestrator prompt templates (brief deliverable #6)

**Dispatch (hook suggestion or manual), rendered by the host from `squad route` output:**
> Delegate this to `{agent}` — {reason from routes.json}. Command: `@{agent} "{task}. Under {limit} {unit}."` If it returns `RATE_LIMITED`/`TIMEOUT`, walk the fallback chain: {fallbacks}; on `AUTH_ERROR`, drop {adapter} for this session and continue with the next candidate; terminal fallback: do it yourself and note the ledger will record `self`.

**Workflow-step synthesis (the calling model after dispatch steps complete):**
> You are the synthesis step. Inputs are files: {steps.*.output_file list}. Read them; do not re-derive their contents. Produce {deliverable}. Cite which step's output supports each conclusion. If two steps conflict, say so explicitly rather than averaging them.

**Council mode (architecture decisions; replaces the CLAUDE.md prose ritual):**
> Run `squad invoke agy --prompt-file q.txt --limit 300` and `squad invoke codex --prompt-file q.txt --limit 200` with the same question, then synthesize: where they agree, state it as consensus; where they disagree, name the crux and make one recommendation. Append decision + rationale to the project decision log.

## 9. Roadmap

- **~Month 1:** Phases 0–4 shipped; MVP demo recorded; pr-review + growth-weekly running weekly; restarted D1 accumulating on a clean instrument.
- **Months 2–3:** Phase 5; ≥30 decision records with outcome coverage from workflow steps; first `squad report`-cited routing changelog entry (change or explicit no-change memo); D1 verdict if volume allows → Phase 6 branch executes.
- **Months 4–6:** GrowthSquad module matured (DAG parallelism if needed); D1 writeup published either way; Phase 7 evaluated against its pull-gate — split + Apache-2.0 launch only if it fires.
- **Month 12 (vision checkpoint):** if external contributors exist — conformance-certified community adapters, registry.json → real registry, hosted report/leaderboard as the proprietary layer. If not — DevSquad is a excellent personal multi-CLI harness with receipts, and that was worth building on its own.

---

### Appendix: review provenance

Four independent proposals (protocol-purist / pragmatic-incrementalist / skeptic / ecosystem-strategist), each adversarially critiqued against the live repo. Notable: nearly every code-level claim in the pragmatic and skeptic proposals survived hostile verification (line numbers, assertion counts, incident history). The purist (Go rewrite) and strategist (npm core + trend-to-post MVP) proposals failed on: D1 contamination, packaging boundary, unshippable demo steps (claude-task re-entry, exec-ing skills), and capacity-vs-history. Their strongest parts — the ledger-as-derived-views principle, adapter-declared per-error cooldowns, the host codec isolation, exit-code honesty, manifest quirks-as-data — are folded in above.
