# Typed decision classifiers: Jev, Laya and DevSquad

Decision date: **2026-09-26**. Status: **source evaluation and planning
amendment; runtime integration and performance evaluation are pending**.

## Recommendation

Evaluate a small typed classifier as an optional **decision helper**, not a
replacement lead, coding worker or authority boundary. Per the user's September
26 direction, start with a **single capped Jev synthetic pilot** because it
avoids Laya's local model setup. The pilot is limited to one billable request,
no retries, a $0.01 ceiling and no repository/private task content. Move to a
local, default-off Laya trial if Jev becomes materially costlier, cannot be
accessed, or fails the quality/latency gate. Existing paid coding subscriptions
do not establish access to the Jev API. Do not add a mandatory model dependency
or delay M5's delivery/revision loop after the bounded probe.

The current [router](../../../plugin/core/src/devsquad/router.py) is deterministic
and makes no model call. A classifier adds routing latency. Its potential value
is better task/profile matching, fewer unnecessary tool loads, less irrelevant
context and less downstream rework. No DevSquad speedup, allowance saving or
number of saved subscription windows has been measured.

The initial evaluation inspected public documentation, benchmark reports and
pinned Laya source. It did **not** install weights or run Laya inference. The
tracked [Jev pilot specification](experiments/jev-pilot-v1.json) and validated
[probe](../../../test/core/probes/jev_decision_eval.py) are now ready, but no
TypeSafe request has run because this environment has no `TYPESAFE_API_KEY` and
the console is at its login screen. The live result remains pending.

## What was verified

| Candidate | Relevant capability | Constraint for this project |
|---|---|---|
| **Jev, TypeSafe System One** | Hosted typed choice, rubric-score and boolean decisions; multiple questions over shared state | Not a code generator. A separate network/API dependency with separate billing and privacy decisions |
| **Laya** | Apache-2.0 local typed decision models with English, multilingual and typed-workflow variants | Optional heavy inference environment and model assets; local latency, memory, calibration and engineering-task quality are unverified |

Jev's documented current version is `jev-1.13.0`; `jev-latest` is mutable.
Published pricing is $0.042 per million input tokens, with output tokens free.
The documented limits are 64k aggregate context and 32k for state plus the
longest question. These are provider claims/current specifications, not local
measurements. Pin the concrete model and recheck terms at integration time.
[Jev models and pricing](https://docs.typesafe.ai/models).

Choice/score confidence describes the output distribution; it is not proof
that a decision is correct. Jev documents weaknesses in exact numeric tasks,
indirection and adversarial input. Its coding-agent guidance explicitly
distinguishes typed decisions from generation and tool execution.
[Confidence](https://docs.typesafe.ai/confidence),
[known weaknesses](https://docs.typesafe.ai/model-jaggedness/jev-1.13),
[coding-agent boundary](https://docs.typesafe.ai/introduction/coding-agents).

The inspected Laya source is package `0.3.20`, commit
`4066d5d5fbf08b66c6757ddeedbd797bd7655bc0`. Its dependencies include PyTorch and
Transformers, unlike DevSquad's dependency-free core. Revision/hash pinning is
supported but must be selected explicitly. Its `Router` chooses among Laya
checkpoints using language/script and optional question-ID heuristics; it is
**not a coding-provider selector**. The bundled Hub revision observed was
`55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851`; pin individual assets in the eventual
experiment manifest, not just a moving model name.
[Package](https://github.com/NandhaKishorM/laya/blob/4066d5d5fbf08b66c6757ddeedbd797bd7655bc0/pyproject.toml),
[router](https://github.com/NandhaKishorM/laya/blob/4066d5d5fbf08b66c6757ddeedbd797bd7655bc0/laya/router.py),
[revision handling](https://github.com/NandhaKishorM/laya/blob/4066d5d5fbf08b66c6757ddeedbd797bd7655bc0/laya/revisions.py),
[model](https://huggingface.co/convaiinnovations/laya).

Laya's published 32.8 ms T4 one-question median is not Mac end-to-end latency.
Its Jev comparison uses third-party numbers, not a matched head-to-head run.
The reported model-routing task measures domain classification, not which
coding model delivers a correct patch. Results also show language/calibration
weaknesses and degradation with many labels. Its own guidance suggests small
option sets. These findings justify evaluation, not automatic adoption.
[Pinned benchmark report](https://github.com/NandhaKishorM/laya/blob/4066d5d5fbf08b66c6757ddeedbd797bd7655bc0/BENCHMARKS.md).

Laya can truncate state/question content; long-input aggregation retains a
window's confidence, not a calibrated document-level probability. Choice/score
entropy confidence and maximum answer probability are different fields. Keep
raw scores and our separately evaluated calibration, rather than normalizing
every provider's “confidence” into a supposed universal success probability.
[Pinned inference code](https://github.com/NandhaKishorM/laya/blob/4066d5d5fbf08b66c6757ddeedbd797bd7655bc0/laya/agent.py).

## Other places to use this pattern

Priority is an evaluation order, not a promise to enable every use case.
P1/P2/P3 below indicate sequence, not defect severity.

| Priority / use | Bounded classifier output | Potential benefit and required boundary |
|---|---|---|
| P1 — Task intake and routing hints | Task family, complexity band, ambiguity/missing-information flags; rank already-qualified profiles | Improve template/profile matching. The lead validates the task; hints cannot lower its quality floor, change scope or invent capabilities |
| P1 — Skill and tool shortlist | Up to a few relevant IDs, including `none`/`uncertain`, from an approved catalog | Avoid irrelevant tool/skill loading. Read selected instructions fully; retain catalog access, validate actual arguments and keep worker permissions unchanged |
| P1 — Context and retrieval ranking | Relevance labels over bounded file, diff, log or retrieved-passage candidates | Reduce irrelevant context. Never remove mandatory instructions, acceptance criteria, failed checks or review dissent; retain source references and measure evidence recall |
| P2 — Failure triage | Semantic category for otherwise unclassified diagnostics, plus suggested next action | Group unknown failures for investigation. Exact auth/rate/timeout codes, exit status and protocol evidence remain authoritative; no automatic retry or paid fallback |
| P2 — Review and test attention | Finding clusters, subsystem/risk tags, relevant optional test IDs | Focus a reviewer and suggest extra checks. Preserve original findings and provenance; never suppress a blocker, waive independent review or skip required tests |
| P2 — Outcome learning and documentation | Proposed repair/failure labels, affected requirement/doc IDs | Make comparable cohorts and identify documentation gaps. Labels need evidence and correction; they cannot declare acceptance, author documentation or promote defaults |
| P3 — Council/escalation suggestions | Disagreement/ambiguity flags linked to evidence | Help the existing lead spot cases worth deliberating. No classifier-only council trigger, extra worker launch or change to C1's explicit budget and evaluation gate |

Skill suggestion has unusually relevant prior evidence: TypeSafe reports fewer
wrong and unnecessary skill loads in a 488-request, 182-skill experiment. It
used Jev 1.12 and synthetic requests, not DevSquad; the agent retained access to
the full skill index. Treat this as a testable hypothesis, not a reproduced gain.
[Official skill-suggestion experiment](https://docs.typesafe.ai/cookbooks/skill_suggestion).

The target is catalogs passed to DevSquad-managed workers. This does not allow
DevSquad to bypass the host's skill instructions, alter global AI settings, or
replace provider-native internal tool selection it cannot control. Context
selection is ranking, not generative summarization; complicated synthesis and
patch writing remain lead/worker tasks.

Do **not** use a probabilistic classifier for quota arithmetic/reset times,
authentication truth, observed model identity, capability verification, lock
ownership, crash recovery, Git/candidate integrity, permission grants, mandatory
check acceptance, or the sole security/redaction gate. Existing exact checks
are cheaper and authoritative for these jobs.

## Minimal architecture amendment

This extends M6 evaluation and M7 optional packaging. It does not reopen M1–M5
or remove any existing acceptance gate. Automatic self-training and unreviewed
learned policy changes remain deferred.

1. **Three explicit modes:** `off` (default, current behavior), `shadow`
   (save suggestions without changing execution), and `advisory` (only a
   reviewed, versioned policy after the use-case gate passes). Classification
   is optional preprocessing; the router consumes frozen validated data and
   remains deterministic. A cache miss never silently enables inference.
2. **Constrain before ranking:** exact policy filters establish permissions,
   billing, capabilities, verified identity, quality and current capacity.
   Only permitted candidates may be scored. Honor pins and explicit fallback;
   revalidate volatile capacity at reservation. Suggestions cannot change task
   class/requirements or expand the eligible set. No eligible candidate still
   means blocked, not “let the classifier choose.”
3. **Typed adapter contract:** versioned purpose/question/rubric, input and
   evidence hashes, candidate IDs, model/runtime revision, language and
   truncation metadata; output labels, raw probability vector, provider-specific
   confidence, optional separately versioned calibration and an abstain reason.
   Validate schema, known IDs, finite/ranged scores and distributions before
   use. Invalid, unsupported, truncated, late or uncertain output falls back
   to the unchanged deterministic behavior and is recorded.
4. **Freeze and replay:** save observations with the run/experiment. Cache keys
   cover scope/evidence, candidate and catalog hashes, question/options/rubric,
   schema, model/runtime, calibration and language. Changed inputs invalidate
   reuse; resume reuses the saved observation instead of paying again. Keep
   raw private content outside tracked evidence. Predictions are untrusted
   data and cannot supply shell commands, policy text or new permissions.
   An interrupted external call with an unknown outcome is not proof of no
   charge: record it as indeterminate and abstain rather than automatically
   resubmitting on resume. Any explicit retry needs its own budget reservation.
5. **Bound the extra work:** explicitly budget calls, input size, wall time,
   local memory and any authorized API cost. Account for classifier calls,
   worker launches and native usage separately. Batch related questions only
   within budget; use bounded retries and cancellation. A timeout must not
   outlive its run or consume the worker's entire remaining deadline.
6. **Keep installation optional:** no heavy imports in core CLI or hooks; no
   hook network calls, inference or model downloads. Use an isolated optional
   environment and pinned assets. An owned bounded process can reuse a loaded
   model within a run; no permanent service/extra MCP server is required. Model
   downloads are explicit setup, never an automatic fallback. Missing extras,
   unsupported hardware or a failed helper must preserve ordinary operation.

Jev experiments additionally require an approved endpoint/model, allowed data
classes, redaction policy, spending ceiling and explicit retry policy. Its API
supports typed responses and reports usage; record actual returned usage,
errors and concrete model identity rather than inferring usage from a request.
[Official API](https://docs.typesafe.ai/api).

### Immediate capped Jev pilot

The v1 pilot batches eight synthetic task cases and 24 task-family,
execution-tier and specialist-skill choices into **one** Jev 1.13 request. The
dry run is 19,219 request bytes. At the frozen published price, even the model's
documented 64k aggregate context limit would cost about $0.002688, below the
$0.01 ceiling. This calculation is only a preflight cap; the receipt must use
the API's returned `input_tokens`. The probe has no retry path, never accepts a
key on the command line and emits only case IDs, choices, probability vectors,
latency and usage—not task text.

```bash
python3 test/core/probes/jev_decision_eval.py
# Fill TYPESAFE_API_KEY in the Git-ignored local .env using your editor.
# For a fresh checkout, copy the blank .env.example to .env first.
chmod 600 .env
# This validates local configuration without calling the API or printing the key:
python3 test/core/probes/jev_decision_eval.py --env-file .env
# Live execution is a separate, explicitly selected step after pricing review:
python3 test/core/probes/jev_decision_eval.py --env-file .env --execute \
  --output "$HOME/.devsquad/private-probes/jev-pilot-v1.json"
```

The probe supports a single-line plain or quoted `TYPESAFE_API_KEY` assignment
and optional `export`; it ignores unrelated variables, rejects duplicate key
assignments, and never evaluates shell substitutions. The env file must be a
private regular UTF-8 file, at most 16 KiB, with no group/other permissions.
An already exported non-empty key takes precedence. Files are loaded only
when `--env-file` is supplied; this does not enable the runtime classifier or
pass the key to coding workers. A dry run reports only whether a key is present,
not whether TypeSafe has authenticated it. The local `.env` and `.env.*` files
are Git-ignored; `.env.example` contains no secret.

Do not copy the key or private receipt into Git. If the provider's current price
makes one maximum-context request exceed $0.01, if returned usage breaches the
cap, or if access requires purchasing a larger commitment, stop without retry
and start the pinned Laya local trial. A smoke result only answers whether Jev
can follow this schema on synthetic cases; it cannot enable runtime routing.

## Spec-based iterations and acceptance

These are small work packages within M6's existing experiments/learning work,
not new prerequisite milestones. M4's external live-host blocker stays open;
independent offline evaluation can proceed once the M5 evidence shape is stable.
M5 disposition, bounded revisions and receipts remain the immediate next work.

| Package | Deliverable | Required proof before advancing |
|---|---|---|
| **M6-D1 — Contract and baseline** | Strict optional decision schema, fake adapter, run/cache accounting, redacted labeled corpus and frozen experiment spec | Default-off equivalence; malformed/unknown/NaN output, candidate/pin/quality/permission attacks, input drift, cancellation and crash/resume tests. Zero unauthorized selections or duplicated paid calls on replay |
| **M6-D2 — Capped Jev pilot** | One-request synthetic smoke, then a larger shadow comparison only if separately budgeted and justified | Exact model/usage/latency/cost receipt; strict response validation; no retry, task disclosure or runtime effect. Missing access, cost breach or poor results select the Laya fallback rather than weakening the gate |
| **M6-D3 — Local fallback and adoption decision** | Pinned optional Laya trial when triggered, followed by evidence-backed keep-off or narrowly scoped advisory policy and rollback receipt | Same cases and end-to-end accounting for any Jev/Laya comparison; use-case quality/cost gate before opt-in. Failed/inconclusive experiments remain off; model/rubric/calibration changes rerun held-out and boundary tests |

Freeze the dataset split, metrics, thresholds, sample-size rationale and resource
ceilings **before** evaluating the held-out set. Split by issue/repository family
to prevent near-duplicate leakage. Include real engineering tasks, no-match
cases, long/noisy inputs, English/Hinglish/other supported languages, adversarial
instructions and quota-constrained candidate sets. Synthetic fixtures test
mechanics; they do not establish user-facing quality.

Measure task-family/shortlist accuracy, abstention coverage and calibration,
context evidence recall, inappropriate downgrade rate, actual check/review
outcomes, escaped defects, lead repairs, total latency, peak memory and observed
worker/token usage. The cheapest sufficient profile is established from verified
outcomes, not a model-brand label or another classifier's answer. Report sample
sizes, uncertainty intervals and unavailable usage; do not convert worker counts
or tokens into fixed Plus-window consumption.

Adoption requires all authority/integrity tests passing, no observed critical
evidence loss or constraint violation, quality meeting the predeclared
non-inferiority margin, and a material end-to-end benefit after helper overhead.
Use a predeclared target (initial proposal: at least 10% less observed scarce
worker usage **or** lead rework time) with adequate uncertainty bounds; a tiny
pilot cannot prove the production gate. Scarce usage must be observed, not an
invented quota conversion. Limited or inconclusive data means keep shadow/off.
Jev and Laya need the same inputs/options and end-to-end accounting to support
any head-to-head claim.

Expand to P2/P3 only through separate small specs and measurements. A successful
skill selector does not establish a safe model router, failure handler or judge.
