#!/usr/bin/env bash
# SPAICE Agent Framework — one-line installer
#
# Usage:
#   curl -sSL https://spaice.ai/install.sh | sh -s <agent_id>
#   curl -sSL https://spaice.ai/install.sh | sh -s jarvis v0.1.0  # pin version
#
# Standardised install (no flags). Every SPAICE agent gets:
#   1. spaice-agent package into the Hermes venv
#   2. Hook shim + scaffolded config.yaml for <agent_id>
#   3. Bundled VETTED skills (gsd, self-improvement, instinct-learner,
#      pdf, docx, xlsx, pptx, gmail)
#   4. Antigravity skill library (1,443 skills, MIT-licensed, vendored at
#      a pinned upstream commit — reviewable, offline-capable, auditable)
#
# Design: Hermes skills run code. Every skill installed means every skill
# trusted. All skills ship inside the package at frozen versions; upgrades
# only happen via `spaice-agent upgrade` (which pulls a new package version
# that we — the maintainers — have re-vetted before release).
set -eu

# ---------- args ----------
AGENT_ID="${1:-}"
VERSION_SPEC="${2:-main}"   # git ref/tag/branch — default: main

if [ -z "$AGENT_ID" ]; then
  echo "usage: $0 <agent_id> [version_spec]"
  echo "example: $0 jarvis"
  echo "example: $0 scope-bot v0.2.0"
  exit 1
fi

# Validate agent_id: lowercase alnum + hyphens only
if ! echo "$AGENT_ID" | grep -qE '^[a-z][a-z0-9-]*$'; then
  echo "ERROR: agent_id must be lowercase alphanumeric + hyphens (got: $AGENT_ID)"
  exit 1
fi

# ---------- config ----------
REPO_URL="${SPAICE_REPO_URL:-https://github.com/spaice-ai/spaice-agent.git}"
PKG_SPEC="spaice-agent @ git+${REPO_URL}@${VERSION_SPEC}"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  SPAICE Agent Framework installer"
echo "  Agent ID: $AGENT_ID"
echo "  Version:  $VERSION_SPEC"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ---------- step 1: find Hermes venv ----------
echo ""
echo "→ Step 1/5: Locating Hermes venv..."

HERMES_VENV=""
for candidate in \
  "$HOME/.Hermes/hermes-agent/venv" \
  "$HOME/.hermes/hermes-agent/venv" \
  "$HOME/.Hermes/venv"; do
  if [ -x "$candidate/bin/python" ]; then
    HERMES_VENV="$candidate"
    break
  fi
done

# Fall back to following the `hermes-agent` executable's shebang
if [ -z "$HERMES_VENV" ] && command -v hermes-agent >/dev/null 2>&1; then
  shebang=$(head -1 "$(command -v hermes-agent)" | sed 's/^#!//')
  venv_python="${shebang% *}"
  if [ -x "$venv_python" ]; then
    HERMES_VENV="$(dirname "$(dirname "$venv_python")")"
  fi
fi

if [ -z "$HERMES_VENV" ]; then
  echo "✗ Could not locate Hermes venv."
  echo "  Checked: ~/.Hermes/hermes-agent/venv, ~/.hermes/hermes-agent/venv"
  echo "  Install Hermes first: https://github.com/..."
  exit 1
fi

VENV_PIP="$HERMES_VENV/bin/pip"
VENV_PY="$HERMES_VENV/bin/python"
VENV_CLI="$HERMES_VENV/bin/spaice-agent"

echo "  Found: $HERMES_VENV"

# ---------- step 2: pip install ----------
echo ""
echo "→ Step 2/5: Installing spaice-agent package..."
echo "  $VENV_PIP install --upgrade \"$PKG_SPEC\""

if ! "$VENV_PIP" install --upgrade --quiet "$PKG_SPEC"; then
  echo "✗ pip install failed."
  exit 1
fi

INSTALLED_VER=$("$VENV_CLI" version)
echo "  ✓ Installed spaice-agent $INSTALLED_VER"

# ---------- step 3: install hook + config ----------
echo ""
echo "→ Step 3/5: Installing hook + config scaffold for $AGENT_ID..."
"$VENV_CLI" install "$AGENT_ID" --with-config

# ---------- step 4: bundled vetted skills ----------
echo ""
echo "→ Step 4/5: Installing bundled vetted skills..."
"$VENV_CLI" skills bundled-install

# ---------- step 5: antigravity vendored bundle (standardised) ----------
echo ""
echo "→ Step 5/5: Installing vendored antigravity skill library..."
"$VENV_CLI" skills antigravity-install

# ---------- doctor ----------
echo ""
echo "→ Running doctor..."
"$VENV_CLI" doctor "$AGENT_ID" || true

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Install complete."
echo ""
echo "  NEXT STEPS:"
echo "    1. Edit ~/.spaice-agents/$AGENT_ID/config.yaml"
echo "    2. Add credentials to ~/.Hermes/credentials/"
echo "    3. Restart Hermes so the hook loads"
echo "    4. Verify: $VENV_CLI doctor $AGENT_ID"
echo ""
echo "  To upgrade (package + all skills): $VENV_CLI upgrade"
echo "  To list agents:                    $VENV_CLI list"
echo "  To inspect skill state:            $VENV_CLI skills status"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
