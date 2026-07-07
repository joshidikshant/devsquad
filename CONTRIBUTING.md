# Contributing to DevSquad

## Dev setup

```bash
git clone https://github.com/joshidikshant/devsquad.git
cd devsquad
bash test/run.sh    # no network, no real CLIs required — should be all green
```

The test suite (`test/run.sh`) is the contract. It runs offline against fake
CLI binaries, is bash-3 compatible, and exercises the jq-absent fallback paths.
**Run it before every commit.** It has caught real latent bugs on its first
run more than once (`write_state`, `record_rate_limit` — both missing-dir
crashes; the `grep -c || echo 0` double-zero arithmetic bug).

## Ground rules for shell code

- **bash 3.2 compatible** — macOS ships bash 3.2. No associative arrays
  (`declare -A`), no `${var,,}`, no `mapfile`. Newline-delimited strings +
  `case`/`grep`/`cut` instead of maps.
- **jq is optional** — every code path that uses `jq` needs a fallback. Test
  both (see `test_routing.sh` Group 5 for the PATH-shim technique).
- **Hooks must be fast and network-free** — anything in `plugin/hooks/scripts/`
  runs on the critical path with a 15s budget. No network calls; spawn
  detached background work instead (see the model-catalog refresh in
  `session-start.sh`).
- **The wrapper contract is enforced** — `test/test_wrapper_contract.sh` pins
  the error taxonomy (`RATE_LIMITED | AUTH_ERROR | TIMEOUT | CLI_ERROR`),
  auth-before-rate classification, telemetry, and bounded execution across all
  CLI wrappers. Don't break it.
- Match the existing style: `set -euo pipefail`, `local` in functions, atomic
  `tmp`+`mv` state writes, `${BASH_SOURCE[0]:-$0}` for script-dir resolution.

## Adding a 4th CLI

The full recipe lives in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#adding-a-4th-cli).
In short: copy `grok-wrapper.sh` (a thin adapter config), add a row to the
contract test's CLI list, add the agents/routing/detection/status touchpoints,
and file a dated entry in [ROUTING-CHANGELOG.md](ROUTING-CHANGELOG.md). Defaults
change only with evidence.

## Architecture & decisions

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — layers, flow diagram, wrapper
  contract, deployment modes (including the versioned-cache-path trap).
- [docs/adr/](docs/adr/) — architecture decision records.
- [ROUTING-CHANGELOG.md](ROUTING-CHANGELOG.md) — every routing-table change,
  dated, with rationale.

## Releasing

Bump the version in `plugin/.claude-plugin/plugin.json` and both entries in
`.claude-plugin/marketplace.json`, add a `CHANGELOG.md` entry, then after
pushing run `claude plugin update devsquad@devsquad-marketplace`. Never point
hook commands at a versioned cache dir — that freezes hooks at install time.
