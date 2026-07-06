# Routing Changelog

Per decision D2 (2026-07-05 architecture evaluation): the routing table is a
static, human-edited artifact. Every change to categories, keywords, or
default routes gets a dated entry here with the reason. Review monthly.
Learned routing is rejected until real volume justifies it (kill rule:
<50 routing decisions/day sustained; measured 2026-07-05 at well under that —
see entry #1).

Format per entry: date, what changed, why, evidence if any.

---

## #1 — 2026-07-05 — Baseline: document the current table

**Table as shipped** (`plugin/lib/routing.sh` `route_task()`, first match wins,
case-insensitive substring):

| Category | Keywords (the routing contract) | Default agent | Config key |
|---|---|---|---|
| research | `research`, `investigate`, `find out`, `look up`, `search for` | gemini-researcher | `default_routes.research` (gemini/codex/self) |
| reading | `read file`, `read the`, `analyze file`, `understand codebase`, `summarize`, `review code` | gemini-reader | `default_routes.reading` (gemini/self) |
| testing | `write test`, `test coverage`, `add test`, `unit test`, `integration test` | codex-tester | `default_routes.testing` (codex/gemini/self) |
| development | `implement`, `refactor`, `code change`, `modify code` | gemini-developer | `default_routes.development` (gemini/codex/self) |
| code_generation | `generate`, `boilerplate`, `scaffold`, `create template`, `prototype` | codex-developer | `default_routes.code_generation` (codex/gemini/self) |
| synthesis (+ no match) | `synthesize`, `decide`, `integrate`, `architect`, `final review`, everything else | self | `default_routes.synthesis` |

`claude` in config is normalized to `self`.

**Rationale for static table:** solo-operator decision volume. Measured
2026-07-05 across all projects with DevSquad state: 10 active days ever;
median ~38 advisory suggestions/day, and virtually all of those are repeated
threshold nags, not distinct routing decisions. Actual completed delegations
with usage records: 4 (Cortex, 2026-04-09) plus test artifacts. This is two
orders of magnitude below any volume where a learned router could converge,
and the targets (Gemini/Codex/Claude behavior) shift quarterly. Locked
decision L3; revisit only per D2 kill rule.

**Changes in this entry:** `development` added to `config-defaults.json`,
`ensure_config`, and the config schema docs (it existed in code but was
missing from every config surface — F5). No keyword changes.

---

## #2 — 2026-07-06 — Per-agent model routing (`agent_models`)

Antigravity CLI multiplexes multiple model families (`agy models` on this
machine: Gemini 3.5 Flash Low/Medium/High, Gemini 3.1 Pro Low/High, Claude
Sonnet 4.6, Claude Opus 4.6, GPT-OSS 120B). New `agent_models` config map
routes each agent to its own external model; resolution is per-agent >
global (`preferences.gemini_model` / `preferences.codex_model`) > CLI default.

**Recommended starting tier map** (config suggestions, not defaults — the
shipped default remains empty, i.e. Antigravity's own session default):

| Agent | Suggested model | Why |
|---|---|---|
| gemini-reader | Gemini 3.5 Flash (Low/Medium) | bulk summarization wants cheap + huge context, not brilliance |
| gemini-researcher | Gemini 3.1 Pro (High) | synthesis-quality research |
| gemini-developer | Gemini 3.1 Pro (High) or Claude Sonnet 4.6 | code changes need judgment |
| gemini-tester | Gemini 3.5 Flash (High) | test generation is pattern-heavy |
| codex-developer / codex-tester | via `preferences.codex_model` (e.g. gpt-5.3-codex) | codex CLI's own tiering |

Claude *shells* are a separate dimension: all six agents run `model: sonnet`
frontmatter (dispatch + light judgment; `inherit` billed at session-model
prices, measured ~14.5K tokens overhead per delegation).

**Caveats measured 2026-07-06:** agy silently ignores unknown `--model`
values (no error — hence exact-name validation in `/devsquad:config`), and
one wrapper call hung past 3 minutes before `--print-timeout` was added to
every invocation. Change one tier at a time; judge by contracts.log
violation rate, acceptance rate, and holdout outcomes.

---

## #3 — 2026-07-06 — Grok added as a routing target (defaults unchanged)

Grok Build CLI (`grok`, v0.2.11, ~/.grok/bin) joins the squad as the third
external CLI — the D4 trigger, so the shared wrapper contract is now
enforced by `test/test_wrapper_contract.sh` across all three wrappers.

`default_routes` values now accept `grok`:
- `research=grok` → grok-researcher (live web/X search — Grok's edge over
  Gemini for current-events/sentiment; Gemini keeps the 1M-context edge for
  codebase reading, which is why `reading` remains gemini|self only)
- `development=grok` / `code_generation=grok` / `testing=grok` → grok-developer

**Defaults are unchanged** (research/reading→gemini, code_generation/testing→
codex): adding a third backend is capacity and capability optionality, not a
default-routing change. Promote grok in a future dated entry only with
evidence (contracts.log, acceptance, holdout). Grok model per agent:
`agent_models.grok-researcher` etc.; global: `preferences.grok_model`
(default model: grok-build; list with `grok models` after `grok login`).
