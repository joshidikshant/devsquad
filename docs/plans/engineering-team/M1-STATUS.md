# M1 implementation status

M1 is implemented at checkpoint `572e452` and remains **in progress** pending
independent review of the complete gate. M2 process ownership has not started.

| Requirement | Evidence | Status |
|---|---|---|
| Python 3.11+ package, launcher, strict v1 inputs | `plugin/core`, 13 standard-library unit tests, source and installed-wheel launch | verified |
| Requested and observed identity remain separate | `ExecutionIdentity`, `LaunchSpec`, `NormalizedResult` | verified offline |
| Execution, artifact and acceptance states remain separate | normalized-result schema and classifier tests | verified offline |
| Explicit model/effort/permission preparation | manifest builders reject unknown effort pairs; argv preserves spaces | verified offline |
| Codex native metadata and lifecycle dialect | installed 0.135.0 schema generation, live paginated `model/list`, fake event-state tests | verified |
| Catalog last-good retention and unqualified discovery | catalog unit and legacy shell tests | verified offline |
| Legacy wrapper API and four error prefixes | existing discovery suite, 38 wrapper assertions | verified offline |
| Portable watchdog prompt return and descendant cleanup | forced portable-path Bash 3.2 tests | verified offline |
| Tracked bounded Antigravity context | Git inventory tests cover spaces, TSX/JSX, ignored/binary/symlink/escape/size omissions | verified offline |
| Bounded real starting-profile smoke | Codex `gpt-5.5`, low effort, read-only, ephemeral JSONL invocation | verified live |
| Independent gate review | Root review is active; findings were folded into the checkpoint | pending final review |

The Python bridge returns argv and parser policy only. It does not spawn a
worker or implement a watchdog. M2 remains the sole owner of process sessions,
timeouts, cancellation, draining and reaping for new runs.
