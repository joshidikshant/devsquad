#!/usr/bin/env bash
# Table-driven tests for route_task() in plugin/lib/routing.sh
# Covers: every category's keywords, config overrides, missing config,
# missing jq, claude->self normalization, and the development route (F5).
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)/plugin"

PASS=0
FAIL=0

# make_env [config_json] -- fresh CLAUDE_PROJECT_DIR, optional config
make_env() {
  TEST_DIR=$(mktemp -d)
  export CLAUDE_PROJECT_DIR="$TEST_DIR"
  if [ -n "${1:-}" ]; then
    mkdir -p "$TEST_DIR/.devsquad"
    printf '%s' "$1" > "$TEST_DIR/.devsquad/config.json"
  fi
}

# assert_route "task description" expected_agent [extra_path]
# Runs route_task in a fresh bash so routing.sh's set -e cannot kill the runner.
assert_route() {
  local desc="$1" expected="$2" extra_path="${3:-}"
  local out agent
  if [ -n "$extra_path" ]; then
    out=$(PATH="$extra_path" bash -c "source '$PLUGIN_ROOT/lib/routing.sh'; route_task \"\$1\"" _ "$desc" 2>/dev/null)
  else
    out=$(bash -c "source '$PLUGIN_ROOT/lib/routing.sh'; route_task \"\$1\"" _ "$desc" 2>/dev/null)
  fi
  # Parse without requiring jq (grep works in both modes)
  agent=$(printf '%s' "$out" | grep -o '"recommended_agent":[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
  if [ "$agent" = "$expected" ]; then
    PASS=$((PASS + 1))
  else
    FAIL=$((FAIL + 1))
    echo "  FAIL: '$desc' -> got '${agent:-<none>}', expected '$expected'"
  fi
}

# --- Group 1: hardcoded fallbacks, no config file ---
make_env ""
assert_route "research the best rate limiting strategy" "gemini-researcher"
assert_route "investigate why the build is flaky" "gemini-researcher"
assert_route "look up the aws sdk docs" "gemini-researcher"
assert_route "read the auth module and explain the flow" "gemini-reader"
assert_route "summarize what core/ exports" "gemini-reader"
assert_route "analyze file src/index.ts" "gemini-reader"
assert_route "write tests for the parser" "codex-tester"
assert_route "add test coverage for validators" "codex-tester"
assert_route "refactor the user service to async" "gemini-developer"
assert_route "implement pagination on the list endpoint" "gemini-developer"
assert_route "generate CRUD boilerplate for products" "codex-developer"
assert_route "scaffold a new express middleware" "codex-developer"
assert_route "decide between redis and in-memory cache" "self"
assert_route "integrate the payment module" "self"
assert_route "colorless green ideas sleep furiously" "self"

# --- Group 2: config overrides ---
make_env '{"default_routes":{"development":"codex","testing":"gemini","research":"codex"}}'
assert_route "refactor the user service to async" "codex-developer"
assert_route "write tests for the parser" "gemini-tester"
assert_route "research graphql subscriptions" "codex-developer"

# --- Group 2b: grok as a config route target ---
make_env '{"default_routes":{"research":"grok","development":"grok","code_generation":"grok"}}'
assert_route "research the latest npm supply chain attacks" "grok-researcher"
assert_route "refactor the queue worker" "grok-developer"
assert_route "generate a CRUD scaffold" "grok-developer"

# --- Group 3: claude -> self normalization ---
make_env '{"default_routes":{"reading":"claude"}}'
assert_route "read the config loader" "self"

# --- Group 4: development key present in default config templates (F5) ---
for cfg in "$PLUGIN_ROOT/skills/onboarding/templates/config-defaults.json"; do
  if grep -q '"development"' "$cfg"; then
    PASS=$((PASS + 1))
  else
    FAIL=$((FAIL + 1))
    echo "  FAIL: development route missing from $(basename "$cfg")"
  fi
done
if grep -q '"development": "gemini"' "$PLUGIN_ROOT/lib/state.sh"; then
  PASS=$((PASS + 1))
else
  FAIL=$((FAIL + 1))
  echo "  FAIL: development route missing from ensure_config in state.sh"
fi

# --- Group 5: missing jq (fallback JSON construction) ---
SHIM=$(mktemp -d)
for cmd in bash sh cat grep sed tr head cut date mkdir mv wc dirname find sort xargs awk uname rm ls env; do
  p=$(command -v "$cmd" 2>/dev/null) && ln -s "$p" "$SHIM/$cmd" 2>/dev/null
done
make_env ""
assert_route "investigate flaky integration tests" "gemini-researcher" "$SHIM"
assert_route "unknown gibberish task xyzzy" "self" "$SHIM"

echo "  routing: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ]
