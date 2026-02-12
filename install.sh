#!/usr/bin/env bash
# DevSquad Installer — registers marketplace and installs plugin
set -euo pipefail

REPO_URL="https://github.com/joshidikshant/devsquad.git"
MARKETPLACE="devsquad-marketplace"
PLUGIN="devsquad@${MARKETPLACE}"

echo "=== DevSquad Installer ==="
echo

# Check claude is available
if ! command -v claude &>/dev/null; then
  echo "Error: Claude Code CLI not found. Install it first:"
  echo "  https://docs.anthropic.com/en/docs/claude-code"
  exit 1
fi

# Step 1: Register marketplace
echo "[1/3] Registering marketplace..."
if claude plugin marketplace list 2>/dev/null | grep -q "$MARKETPLACE"; then
  echo "  Marketplace already registered, updating..."
  claude plugin marketplace update "$MARKETPLACE"
else
  claude plugin marketplace add "$REPO_URL"
fi

# Step 2: Install plugin
echo "[2/3] Installing plugin..."
if claude plugin list 2>/dev/null | grep -q "devsquad@"; then
  echo "  Plugin already installed, updating..."
  claude plugin update "$PLUGIN" 2>/dev/null || true
else
  claude plugin install "$PLUGIN"
fi

# Step 3: Enable plugin
echo "[3/3] Enabling plugin..."
claude plugin enable "$PLUGIN" 2>/dev/null || true

echo
echo "Done! Restart Claude Code, then run:"
echo "  /devsquad:setup"
