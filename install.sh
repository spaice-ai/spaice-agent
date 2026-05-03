#!/usr/bin/env bash
# SPAICE Agent Framework — one-line installer
#
# Usage:
#   curl -sSL https://spaice.ai/install.sh | sh -s <agent_id>
#   curl -sSL https://spaice.ai/install.sh | sh -s jarvis v0.1.0  # pin version
#
# By default, installs:
#   1. spaice-agent package into the Hermes venv
#   2. Hook shim + scaffolded config.yaml for <agent_id>
#   3. Bundled VETTED skills: gsd, self-improvement, instinct-learner
#      (shipped with the package, version-pinned, auditable in one repo)
#
# Optional extras (not installed by default — explicit opt-in required):
#   WITH_ANTIGRAVITY=1   Add the 1,443-skill community library
#                        (github.com/sickn33/antigravity-awesome-skills)
#                        — UNVETTED community skills, review before use.
#
# Design note: Hermes skills run code. Every skill installed means every
# skill trusted. Defaults stay small and audited; expansion is opt-in.
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
echo "→ Step 1/4: Locating Hermes venv..."

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
echo "→ Step 2/4: Installing spaice-agent package..."
echo "  $VENV_PIP install --upgrade \"$PKG_SPEC\""

if ! "$VENV_PIP" install --upgrade --quiet "$PKG_SPEC"; then
  echo "✗ pip install failed."
  exit 1
fi

INSTALLED_VER=$("$VENV_CLI" version)
echo "  ✓ Installed spaice-agent $INSTALLED_VER"

# ---------- step 3: install hook + config + bundled skills ----------
echo ""
echo "→ Step 3/5: Installing hook + config scaffold for $AGENT_ID..."
"$VENV_CLI" install "$AGENT_ID" --with-config

echo ""
echo "→ Step 4/5: Installing bundled vetted skills (gsd, self-improvement, instinct-learner)..."
"$VENV_CLI" skills bundled-install

# ---------- step 4b: antigravity-awesome-skills (OPT-IN ONLY) ----------
if [ "${WITH_ANTIGRAVITY:-0}" = "1" ]; then
  echo ""
  echo "→ Step 4b/5: Installing antigravity-awesome-skills (1,443+ community skills)..."
  echo "  WARNING: these are UNVETTED community skills. Review before use."
  HERMES_SKILLS_DIR="$HOME/.hermes/skills/antigravity"
  if [ -d "$HERMES_SKILLS_DIR" ] && [ "$(ls -A "$HERMES_SKILLS_DIR" 2>/dev/null | head -1)" ]; then
    echo "  Skipped (already installed at $HERMES_SKILLS_DIR)"
  elif ! command -v npx >/dev/null 2>&1; then
    echo "  ⚠ npx not found — skipping. Install Node.js and run: spaice-agent skills antigravity-install"
  else
    npx --yes antigravity-awesome-skills --path "$HERMES_SKILLS_DIR" 2>&1 | tail -3
    echo "  ✓ Antigravity library installed. Review skills before use."
  fi
fi

# ---------- step 5: doctor ----------
echo ""
echo "→ Step 5/5: Running doctor..."
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
echo "  To upgrade later:  $VENV_CLI upgrade"
echo "  To list agents:    $VENV_CLI list"
if [ "${WITH_ANTIGRAVITY:-0}" != "1" ]; then
  echo ""
  echo "  Optional: add the 1,443-skill community library (unvetted):"
  echo "    $VENV_CLI skills antigravity-install"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
