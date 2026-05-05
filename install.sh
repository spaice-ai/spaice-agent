#!/usr/bin/env bash
# SPAICE Agent Framework — one-line installer
#
# Usage:
#   curl -sSL https://spaice.ai/install.sh | sh -s <agent_id>
#   curl -sSL https://spaice.ai/install.sh | sh -s jarvis v0.3.2  # pin version
#
#   # Optional flags (prefix with --):
#   curl -sSL https://spaice.ai/install.sh | sh -s <agent_id> <version> --with-vault
#   curl -sSL https://spaice.ai/install.sh | sh -s <agent_id> <version> --full
#
# Flags:
#   --with-vault  Also create the vault root (~/<agent_id>/) and scaffold
#                 it with conventions, shelf READMEs, and starter templates
#                 (20 files). Skipped by default so existing installs aren't
#                 disturbed.
#   --full        Shorthand for --with-vault (and future --with-* flags).
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

if [ -z "$AGENT_ID" ]; then
  echo "usage: $0 <agent_id> [version_spec] [--with-vault | --full]"
  echo "example: $0 jarvis"
  echo "example: $0 scope-bot v0.2.0"
  echo "example: $0 jarvis v0.2.0 --with-vault"
  exit 1
fi
shift

# Second positional is optional version_spec (only if it doesn't start with --).
VERSION_SPEC="main"
if [ $# -gt 0 ] && [ "${1#--}" = "$1" ]; then
  VERSION_SPEC="$1"
  shift
fi

# Remaining args are flags.
WITH_VAULT=0
while [ $# -gt 0 ]; do
  case "$1" in
    --with-vault) WITH_VAULT=1 ;;
    --full)       WITH_VAULT=1 ;;
    *)
      echo "ERROR: unknown flag: $1" >&2
      echo "usage: $0 <agent_id> [version_spec] [--with-vault | --full]" >&2
      exit 1
      ;;
  esac
  shift
done

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
echo "→ Step 1/7: Locating Hermes venv..."

HERMES_VENV=""

# Env-var override first — lets users point at non-standard installs.
if [ -n "${SPAICE_HERMES_VENV:-}" ]; then
  if [ -x "$SPAICE_HERMES_VENV/bin/python" ]; then
    HERMES_VENV="$SPAICE_HERMES_VENV"
  else
    echo "✗ SPAICE_HERMES_VENV=$SPAICE_HERMES_VENV does not contain bin/python"
    exit 1
  fi
fi

if [ -z "$HERMES_VENV" ]; then
  for candidate in \
    "$HOME/.Hermes/hermes-agent/venv" \
    "$HOME/.hermes/hermes-agent/venv" \
    "$HOME/.Hermes/venv"; do
    if [ -x "$candidate/bin/python" ]; then
      HERMES_VENV="$candidate"
      break
    fi
  done
fi

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
  echo "  Checked: ~/.Hermes/hermes-agent/venv, ~/.hermes/hermes-agent/venv, ~/.Hermes/venv"
  echo ""
  echo "  spaice-agent is a Hermes plugin — you need Hermes installed first."
  echo "  If Hermes IS installed but at a non-standard location, point us at it:"
  echo "      SPAICE_HERMES_VENV=/path/to/venv curl -sSL ... | sh -s $AGENT_ID"
  exit 1
fi

VENV_PIP="$HERMES_VENV/bin/pip"
VENV_PY="$HERMES_VENV/bin/python"
VENV_CLI="$HERMES_VENV/bin/spaice-agent"

echo "  Found: $HERMES_VENV"

# ---------- step 2: pip install ----------
echo ""
echo "→ Step 2/7: Installing spaice-agent package..."
echo "  $VENV_PIP install --upgrade \"$PKG_SPEC\""

if ! "$VENV_PIP" install --upgrade --quiet "$PKG_SPEC"; then
  echo "✗ pip install failed."
  exit 1
fi

INSTALLED_VER=$("$VENV_CLI" version)
echo "  ✓ Installed spaice-agent $INSTALLED_VER"

# ---------- step 2b: memory database init ----------
echo ""
echo "→ Step 2b/7: Initialising memory database schema..."
if "$VENV_CLI" memory init; then
  echo "  ✓ Memory schema ready"
else
  echo "  ⚠ Memory schema init failed — recall pipeline may be degraded."
  echo "  Run 'spaice-agent memory init' manually to retry."
fi

# ---------- step 2c: spatial index build ----------
echo ""
echo "→ Step 2c/7: Building spatial index (cross-layer links)..."
if "$VENV_CLI" memory index; then
  echo "  ✓ Spatial index built"
else
  echo "  ⚠ Spatial index build failed — multi-hop retrieval may be degraded."
  echo "  Run 'spaice-agent memory index' manually to retry."
fi

# ---------- step 3: install hook + config ----------
echo ""
echo "→ Step 3/7: Installing hook + config scaffold for $AGENT_ID..."
"$VENV_CLI" install "$AGENT_ID" --with-config

# ---------- step 4: bundled vetted skills ----------
echo ""
echo "→ Step 4/7: Installing bundled vetted skills..."
"$VENV_CLI" skills bundled-install

# ---------- step 5: antigravity vendored bundle (standardised) ----------
echo ""
echo "→ Step 5/7: Installing vendored antigravity skill library..."
"$VENV_CLI" skills antigravity-install

# ---------- step 6: install CLI dispatcher shim ----------
#
# The Hermes venv ships its own `spaice-agent` entry point (Python
# console-script). But users typically have `~/.local/bin` earlier in
# $PATH than the venv bin dir — so a *shim* there routes spaice-agent-
# owned subcommands to the Python CLI and passes everything else
# through to Hermes. Without it, `spaice-agent mine` and friends get
# swallowed by Hermes' top-level argparser and return an "invalid
# choice" error.
#
# Source of truth for the shim lives in the package at
# packaging/spaice-agent-shim.sh. We extract that file from the
# installed site-packages location and copy it to ~/.local/bin/.
echo ""
echo "→ Step 6/7: Installing CLI dispatcher shim to ~/.local/bin/..."
SHIM_SOURCE=$("$VENV_PY" -c "
import pathlib, sys
try:
    import spaice_agent
    pkg_root = pathlib.Path(spaice_agent.__file__).parent
    shim = pkg_root / 'packaging' / 'spaice-agent-shim.sh'
    if shim.is_file():
        print(shim)
        sys.exit(0)
except Exception:
    pass
sys.exit(1)
" 2>/dev/null || echo "")

if [ -z "$SHIM_SOURCE" ] || [ ! -f "$SHIM_SOURCE" ]; then
  echo "  ⚠ Shim source not found in package. Subcommand routing may be"
  echo "    broken — fall back to calling: $VENV_CLI <command>"
else
  LOCAL_BIN="$HOME/.local/bin"
  mkdir -p "$LOCAL_BIN"
  SHIM_DEST="$LOCAL_BIN/spaice-agent"

  if [ -e "$SHIM_DEST" ] || [ -L "$SHIM_DEST" ]; then
    # Guard against pathological shape — reject if it's a directory or
    # some other non-regular-file node. Backup logic only handles files
    # and symlinks; anything else, bail with a clear error so the user
    # can resolve manually rather than silently continuing.
    if [ -d "$SHIM_DEST" ] && [ ! -L "$SHIM_DEST" ]; then
      echo ""
      echo "  ✗ $SHIM_DEST is a DIRECTORY, not a file or symlink."
      echo "    Installer cannot safely overwrite this. Inspect manually:"
      echo "        ls -la $SHIM_DEST"
      echo "    Then remove or rename it and re-run the installer."
      exit 1
    fi
    if [ ! -L "$SHIM_DEST" ] && [ ! -f "$SHIM_DEST" ]; then
      echo ""
      echo "  ✗ $SHIM_DEST exists but is neither a file nor a symlink."
      echo "    Installer refuses to touch it. Inspect with: ls -la $SHIM_DEST"
      exit 1
    fi

    if ! grep -q "spaice_agent.cli" "$SHIM_DEST" 2>/dev/null; then
      # Existing file/symlink doesn't route to our Python CLI.
      BACKUP="$SHIM_DEST.pre-shim-$(date +%Y%m%d-%H%M%S)"
      if [ -L "$SHIM_DEST" ]; then
        # It's a symlink. Move it, don't follow it — protects the symlink
        # target from being clobbered by the subsequent cp.
        mv "$SHIM_DEST" "$BACKUP"
        echo "  Backed up existing symlink → $BACKUP"
      else
        cp "$SHIM_DEST" "$BACKUP"
        rm -f "$SHIM_DEST"
        echo "  Backed up existing shim → $BACKUP"
      fi
    else
      # Already our shim — remove to avoid cp-into-symlink edge case
      rm -f "$SHIM_DEST"
    fi
  fi

  cp "$SHIM_SOURCE" "$SHIM_DEST"
  chmod +x "$SHIM_DEST"
  echo "  ✓ Shim installed: $SHIM_DEST"

  # PATH sanity check.
  #
  # The shim is the primary entry point for spaice-agent's own
  # subcommands. If ~/.local/bin isn't on $PATH, the shim is installed
  # but unreachable — `spaice-agent list` will return "command not
  # found" and users will think the install failed.
  #
  # We don't exit 1 here because (a) the install is otherwise complete
  # (venv CLI works via full path) and (b) some users deliberately skip
  # ~/.local/bin. But we DO make the warning extremely loud, and we
  # offer a one-line fix plus a fallback, so nobody hits "command not
  # found" without knowing what to do.
  if ! echo "$PATH" | tr ':' '\n' | grep -qx "$LOCAL_BIN"; then
    echo ""
    echo "  ════════════════════════════════════════════════════════════════"
    echo "  ⚠  ACTION REQUIRED: $LOCAL_BIN is NOT in your \$PATH"
    echo "  ════════════════════════════════════════════════════════════════"
    echo ""
    echo "  The spaice-agent CLI shim was installed, but you won't be able"
    echo "  to run 'spaice-agent <cmd>' from your shell until you add"
    echo "  $LOCAL_BIN to \$PATH."
    echo ""
    echo "  Fix — add this line to your shell rc (~/.zshrc or ~/.bashrc):"
    echo ""
    echo "      export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo ""
    echo "  Then restart your shell or run: source ~/.zshrc"
    echo ""
    echo "  Or, use the venv CLI directly (works right now, no PATH change):"
    echo ""
    echo "      $VENV_CLI <command>"
    echo ""
    echo "  ════════════════════════════════════════════════════════════════"
  elif ! echo "$PATH" | tr ':' '\n' | head -5 | grep -qx "$LOCAL_BIN"; then
    echo ""
    echo "  ⚠ $LOCAL_BIN is in \$PATH but not near the front."
    echo "    Another 'spaice-agent' earlier in PATH may shadow this shim."
    echo "    Consider moving $LOCAL_BIN to the front of \$PATH in your shell rc."
  fi
fi

# ---------- optional step: vault scaffold ----------
if [ "$WITH_VAULT" = "1" ]; then
  echo ""
  echo "→ Optional: Creating + scaffolding vault for $AGENT_ID..."
  VAULT_ROOT="$HOME/$AGENT_ID"
  if [ ! -d "$VAULT_ROOT" ]; then
    mkdir -p "$VAULT_ROOT"
    echo "  Created vault root: $VAULT_ROOT"
  fi
  # Do NOT swallow failures — the user explicitly asked for vault setup.
  # If scaffold fails, fail loudly so they know the install is incomplete.
  if ! "$VENV_CLI" vault scaffold "$AGENT_ID"; then
    echo ""
    echo "✗ Vault scaffold failed. Your agent is installed but the vault"
    echo "  was NOT initialised. Inspect with:"
    echo "    $VENV_CLI vault check $AGENT_ID"
    echo "  Then retry:"
    echo "    $VENV_CLI vault scaffold $AGENT_ID"
    exit 1
  fi
fi

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
