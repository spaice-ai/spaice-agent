#!/usr/bin/env bash
# SPAICE Agent Framework — one-line installer
#
# Usage:
#   curl -sSL https://spaice.ai/install.sh | sh -s <agent_id>
#   curl -sSL https://spaice.ai/install.sh | sh -s jarvis
#   curl -sSL https://spaice.ai/install.sh | sh -s jarvis v0.1.0  # pin version
#   NO_ANTIGRAVITY=1 curl -sSL ... | sh -s jarvis  # skip skill library (saves 66MB + 1443 skills)
#
# What it does:
#   1. Locates the Hermes venv
#   2. pip installs spaice-agent into that venv
#   3. Runs `spaice-agent install --with-config <agent_id>`
#   4. Installs the antigravity-awesome-skills library (1,443+ skills)
#   5. Runs `spaice-agent doctor <agent_id>` and prints next steps
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

# ---------- step 3: install hook + config ----------
echo ""
echo "→ Step 3/5: Installing hook + config scaffold for $AGENT_ID..."

"$VENV_CLI" install "$AGENT_ID" --with-config

# ---------- step 4: antigravity-awesome-skills (optional, default on) ----------
echo ""
echo "→ Step 4/5: Installing skill library (antigravity-awesome-skills)..."
HERMES_SKILLS_DIR="$HOME/.hermes/skills/antigravity"
if [ "${NO_ANTIGRAVITY:-0}" = "1" ]; then
  echo "  Skipped (NO_ANTIGRAVITY=1)"
elif [ -d "$HERMES_SKILLS_DIR" ] && [ "$(ls -A "$HERMES_SKILLS_DIR" 2>/dev/null | head -1)" ]; then
  echo "  Skipped (already installed at $HERMES_SKILLS_DIR)"
  echo "  To refresh: rm -rf $HERMES_SKILLS_DIR && re-run installer"
elif ! command -v npx >/dev/null 2>&1; then
  echo "  ⚠ npx not found — skipping skill library"
  echo "    Install Node.js (brew install node / apt install nodejs) and re-run"
else
  if npx --yes antigravity-awesome-skills --path "$HERMES_SKILLS_DIR" 2>&1 | tail -5; then
    echo "  ✓ Skill library installed at $HERMES_SKILLS_DIR"
  else
    echo "  ⚠ Skill library install failed — agent works without it, but with fewer skills"
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
echo "  To upgrade later: $VENV_CLI upgrade"
echo "  To list agents:   $VENV_CLI list"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
