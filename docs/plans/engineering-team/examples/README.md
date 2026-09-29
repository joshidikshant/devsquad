# Design fixtures

These task files show the implemented strict v1 shape. They are not directly
runnable as checked in: their repository paths, refs, routing files and test
commands deliberately name disposable fixtures. M3 and M5 exercise equivalent
tasks end to end against temporary Git repositories.

- [branch-review.json](branch-review.json): a host lead receives the review packet; a failing report-only check becomes a finding.
- [issue-delivery.json](issue-delivery.json): a headless lead resolves a bounded implementation/review workflow; required tests must pass.

The fixture test harness must install a `devsquad/profiles.json` and
`devsquad/policy.json` inside its temporary repository, with two fictional
model families and fake executables. The profiles file is a strict
`{schema_version, profiles, bindings}` registry; the policy declares each
account pool's allowed billing modes and local concurrency limit. Runtime
configuration uses exact locally verified models and effort settings. These
examples intentionally avoid embedding today's provider model names or
pretending that fixture profiles are proven.

The CLI and MCP service validate equivalent task objects against the same
packaged contract. The generated command/schema reference is
[docs/generated/core-reference.md](../../../generated/core-reference.md).
Integration tests resolve paths and refs against temporary repositories;
ordinary schema tests validate shape without touching the filesystem.
