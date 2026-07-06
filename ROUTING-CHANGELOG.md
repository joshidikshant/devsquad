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
