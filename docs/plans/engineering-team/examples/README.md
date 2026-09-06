# Design fixtures

These task files show the v1 shape. They are **not runnable against today's DevSquad**. Repository paths, refs, task classes and test commands refer to a future disposable fixture repository. M1 creates the schemas; M3/M5 create the corresponding repositories and executable tests.

- [branch-review.json](branch-review.json): a host lead receives the review packet; a failing report-only check becomes a finding.
- [issue-delivery.json](issue-delivery.json): a headless lead resolves a bounded implementation/review workflow; required tests must pass.

The fixture test harness must install a `devsquad/profiles.json` and `devsquad/policy.json` inside its temporary repository, with two fictional model families and fake executables. Runtime configuration uses exact locally verified models and effort settings. These examples intentionally avoid embedding today's provider model names or pretending that fixture profiles are proven.

The CLI and MCP service must validate equivalent task objects against the same generated schema. Validate paths/refs against the temporary repository only in integration tests; ordinary schema tests validate shape without touching the filesystem.
