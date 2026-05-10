#!/usr/bin/env bash
# ============================================================================
# spaice-agent — one-liner bootstrap
# ============================================================================
# Installs Hermes + spaice-agent end-to-end on a fresh machine.
#
# Opinionated defaults — you only provide:
#   1. Agent name (what your vault dir is called)
#   2. OpenRouter API key  (main + code + review + auxiliary, all on one key)
#   3. Exa API key         (web search)
#   4. Telegram bot token  (message platform)
#   5. Telegram user ID    (who is allowed to talk to the bot)
#
# Everything else is pre-configured:
#   - Main model:     anthropic/claude-opus-4.7         (framework / reasoning)
#   - Code author:    deepseek/deepseek-v4-pro          (≥200 LOC code gen)
#   - Code reviewer:  openai/gpt-5.3-codex              (adversarial review)
#
#   Consensus pipeline (dual-mode per Jozef 2026-05-06):
#     thinking: deepseek/deepseek-v4-pro → openai/gpt-5.5 → deepseek/deepseek-v4-pro → opus-4.7
#     coding:   anthropic/claude-opus-4.7 → openai/gpt-5.3-codex → deepseek/deepseek-v4-pro → opus-4.7
#
#   - Auxiliary:      auto (Gemini Flash via OpenRouter)
#   - Platform:       Telegram, CLI
#   - Terminal:       local
#   - Vault:          ~/<agent>/ (Dewey 8-layer memory system)
#
# Source of truth (canonical):
#   This bootstrap and the agent itself live on PUBLIC GitHub:
#     https://github.com/spaice-ai/spaice-agent
#   That repo IS the Jarvis release — what every fresh install pulls from.
#   Some operators also run a local Gitea mirror (http://127.0.0.1:8300/)
#   for offline-survival; that is a *secondary mirror only*. Never set
#   SPAICE_REPO_URL or SPAICE_INSTALLER_URL to a local Gitea path — the
#   installer should always pull from the canonical GitHub origin so the
#   running agent stays in lockstep with what was security-reviewed and
#   tagged.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/spaice-ai/spaice-agent/v0.3.3/bootstrap.sh | bash
#
# Non-interactive (CI / automation):
#   Export these BEFORE piping to bash:
#     SPAICE_AGENT_NAME, SPAICE_OPENROUTER_KEY, SPAICE_EXA_KEY,
#     SPAICE_TELEGRAM_BOT_TOKEN, SPAICE_TELEGRAM_ALLOWED_USER
#   Example:
#     export SPAICE_AGENT_NAME=jarvis
#     export SPAICE_OPENROUTER_KEY=sk-or-...
#     ... (etc)
#     curl -fsSL .../bootstrap.sh | bash
#
# ============================================================================
set -eu
set -o pipefail 2>/dev/null || true   # bash 3.2 on macOS supports this; POSIX sh may not.

# Global: restore terminal echo on any abnormal exit from prompt_secret.
ECHO_DISABLED=0
restore_tty() {
  if [ "$ECHO_DISABLED" = "1" ] && [ -r /dev/tty ]; then
    stty echo < /dev/tty 2>/dev/null || true
    ECHO_DISABLED=0
  fi
}
trap restore_tty EXIT INT TERM HUP

# ---------- colour helpers ----------
if [ -t 1 ]; then
  C_BLUE=$'\033[0;34m'; C_GREEN=$'\033[0;32m'; C_YELLOW=$'\033[0;33m'
  C_RED=$'\033[0;31m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'; C_NC=$'\033[0m'
else
  C_BLUE=''; C_GREEN=''; C_YELLOW=''; C_RED=''; C_BOLD=''; C_DIM=''; C_NC=''
fi

info()  { printf '%s→%s %s\n'  "$C_BLUE"   "$C_NC" "$*"; }
ok()    { printf '%s✓%s %s\n'  "$C_GREEN"  "$C_NC" "$*"; }
warn()  { printf '%s⚠%s %s\n'  "$C_YELLOW" "$C_NC" "$*"; }
fail()  { printf '%s✗%s %s\n'  "$C_RED"    "$C_NC" "$*" >&2; exit 1; }
step()  { printf '\n%s%s[%s]%s %s\n' "$C_BOLD" "$C_BLUE" "$1" "$C_NC" "$2"; }

# ---------- banner ----------
cat <<BANNER

${C_BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${C_NC}
${C_BOLD}  spaice-agent bootstrap${C_NC}  ${C_DIM}· memory-first AI · v0.3.3${C_NC}
${C_BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${C_NC}

  Installs Hermes + spaice-agent end-to-end.
  You'll be asked for 5 values. Everything else is pre-configured.

BANNER

# ---------- interactive-input detection ----------
# When piped (curl | bash), stdin is the pipe, not the terminal. We read from
# /dev/tty so prompts still work. Fall back to env-var-only mode if /dev/tty
# is unavailable (CI, Docker-without-tty).
if [ -r /dev/tty ]; then
  TTY_IN=/dev/tty
  INTERACTIVE=1
else
  TTY_IN=""
  INTERACTIVE=0
  info "Non-interactive mode — reading config from environment only."
fi

prompt_value() {
  # prompt_value VARNAME "Question" [default]
  local var="$1" question="$2" default="${3:-}"
  local current
  eval "current=\${$var:-}"
  if [ -n "$current" ]; then
    ok "$var provided via environment"
    return 0
  fi
  if [ "$INTERACTIVE" = "0" ]; then
    if [ -n "$default" ]; then
      eval "$var='$default'"
      ok "$var=$default (default)"
      return 0
    fi
    fail "$var not set and no terminal for prompt. Export $var before running."
  fi
  local answer
  if [ -n "$default" ]; then
    printf '  %s [%s]: ' "$question" "$default" > /dev/tty
  else
    printf '  %s: ' "$question" > /dev/tty
  fi
  IFS= read -r answer < "$TTY_IN" || answer=""
  if [ -z "$answer" ]; then
    answer="$default"
  fi
  if [ -z "$answer" ]; then
    fail "$var is required — no default available."
  fi
  eval "$var='$(printf '%s' "$answer" | sed "s/'/'\\\\''/g")'"
}

prompt_secret() {
  # prompt_secret VARNAME "Question"
  local var="$1" question="$2"
  local current
  eval "current=\${$var:-}"
  if [ -n "$current" ]; then
    ok "$var provided via environment"
    return 0
  fi
  if [ "$INTERACTIVE" = "0" ]; then
    fail "$var not set and no terminal for prompt. Export $var before running."
  fi
  local answer
  printf '  %s: ' "$question" > /dev/tty
  if stty -echo < "$TTY_IN" 2>/dev/null; then
    ECHO_DISABLED=1
  fi
  IFS= read -r answer < "$TTY_IN" || answer=""
  if [ "$ECHO_DISABLED" = "1" ]; then
    stty echo  < "$TTY_IN" 2>/dev/null || true
    ECHO_DISABLED=0
  fi
  printf '\n' > /dev/tty
  if [ -z "$answer" ]; then
    fail "$var is required."
  fi
  eval "$var='$(printf '%s' "$answer" | sed "s/'/'\\\\''/g")'"
}

# ---------- collect 5 values ----------
echo "  ${C_BOLD}Configuration${C_NC}"
SPAICE_AGENT_NAME="${SPAICE_AGENT_NAME:-}"
prompt_value SPAICE_AGENT_NAME "Agent name (lowercase, letters/digits/hyphens)" "jarvis"

# Validate agent name
if ! printf '%s' "$SPAICE_AGENT_NAME" | grep -qE '^[a-z][a-z0-9-]*$'; then
  fail "Agent name must start with a letter and contain only lowercase letters, digits, hyphens. Got: $SPAICE_AGENT_NAME"
fi

SPAICE_OPENROUTER_KEY="${SPAICE_OPENROUTER_KEY:-}"
prompt_secret SPAICE_OPENROUTER_KEY "OpenRouter API key (sk-or-...)"

SPAICE_EXA_KEY="${SPAICE_EXA_KEY:-}"
prompt_secret SPAICE_EXA_KEY "Exa API key"

SPAICE_TELEGRAM_BOT_TOKEN="${SPAICE_TELEGRAM_BOT_TOKEN:-}"
prompt_secret SPAICE_TELEGRAM_BOT_TOKEN "Telegram bot token (from @BotFather)"

SPAICE_TELEGRAM_ALLOWED_USER="${SPAICE_TELEGRAM_ALLOWED_USER:-}"
prompt_value SPAICE_TELEGRAM_ALLOWED_USER "Telegram user ID allowed to talk to this bot (find via @userinfobot)"

if ! printf '%s' "$SPAICE_TELEGRAM_ALLOWED_USER" | grep -qE '^-?[0-9]+$'; then
  fail "Telegram user ID must be numeric. Got: $SPAICE_TELEGRAM_ALLOWED_USER"
fi

# ---------- sanity-check keys look plausible ----------
case "$SPAICE_OPENROUTER_KEY" in
  sk-or-*) ok "OpenRouter key format OK" ;;
  *) warn "OpenRouter key doesn't start with sk-or-. Continuing, but this may fail." ;;
esac

case "$SPAICE_TELEGRAM_BOT_TOKEN" in
  *:*) ok "Telegram bot token format OK" ;;
  *) warn "Telegram bot token doesn't match expected format (digits:alphanum). Continuing." ;;
esac

# ---------- summary ----------
echo ""
echo "  ${C_BOLD}Ready to install${C_NC}"
echo "    Agent:        ${C_GREEN}$SPAICE_AGENT_NAME${C_NC}"
echo "    Vault:        ${C_DIM}~/$SPAICE_AGENT_NAME/${C_NC}"
echo "    OpenRouter:   ${C_DIM}${SPAICE_OPENROUTER_KEY%${SPAICE_OPENROUTER_KEY#????????}}…${C_NC}"
echo "    Exa:          ${C_DIM}${SPAICE_EXA_KEY%${SPAICE_EXA_KEY#????????}}…${C_NC}"
echo "    Telegram:     ${C_DIM}bot ${SPAICE_TELEGRAM_BOT_TOKEN%%:*}  user $SPAICE_TELEGRAM_ALLOWED_USER${C_NC}"
echo ""

if [ "$INTERACTIVE" = "1" ]; then
  printf '  Continue? [Y/n]: ' > /dev/tty
  IFS= read -r confirm < "$TTY_IN" || confirm=""
  case "$confirm" in
    n|N|no|NO) fail "Aborted by user." ;;
  esac
fi

# ============================================================================
# STEP 1: Install Hermes (if not present)
# ============================================================================
step "1/5" "Hermes agent"

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
HERMES_VENV_CANDIDATES="$HERMES_HOME/hermes-agent/venv $HOME/.Hermes/hermes-agent/venv $HOME/.Hermes/venv"
HERMES_VENV=""
for candidate in $HERMES_VENV_CANDIDATES; do
  if [ -x "$candidate/bin/python" ]; then
    HERMES_VENV="$candidate"
    break
  fi
done

if [ -n "$HERMES_VENV" ]; then
  ok "Hermes already installed at $HERMES_VENV — skipping install"
else
  info "Installing Hermes (this takes 2-3 min)..."
  # --skip-setup: we'll write config.yaml + .env ourselves, no interactive wizard.
  # SECURITY: download first to a tempfile, verify non-empty + valid shebang,
  # THEN execute. Avoids executing a truncated script if curl fails mid-stream.
  HERMES_INSTALLER=$(mktemp -t hermes-install-XXXXXX) || fail "mktemp failed"
  # shellcheck disable=SC2064
  trap "rm -f '$HERMES_INSTALLER'; restore_tty" EXIT INT TERM HUP
  if ! curl -fsSL https://hermes-agent.nousresearch.com/install.sh -o "$HERMES_INSTALLER"; then
    rm -f "$HERMES_INSTALLER"
    fail "Download failed: https://hermes-agent.nousresearch.com/install.sh"
  fi
  if [ ! -s "$HERMES_INSTALLER" ]; then
    rm -f "$HERMES_INSTALLER"
    fail "Hermes installer downloaded empty. Retry or check network."
  fi
  if ! head -1 "$HERMES_INSTALLER" | grep -q '^#!.*\(bash\|sh\)'; then
    rm -f "$HERMES_INSTALLER"
    fail "Hermes installer doesn't look like a shell script. Refusing to execute."
  fi
  if ! bash "$HERMES_INSTALLER" --skip-setup; then
    rm -f "$HERMES_INSTALLER"
    fail "Hermes install failed. Try running manually:
      curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash"
  fi
  rm -f "$HERMES_INSTALLER"
  # Re-install the narrower trap.
  # shellcheck disable=SC2064
  trap restore_tty EXIT INT TERM HUP
  # Re-scan for venv
  for candidate in $HERMES_VENV_CANDIDATES; do
    if [ -x "$candidate/bin/python" ]; then
      HERMES_VENV="$candidate"
      break
    fi
  done
  if [ -z "$HERMES_VENV" ]; then
    fail "Hermes installed but venv not found in expected locations: $HERMES_VENV_CANDIDATES"
  fi
  ok "Hermes installed at $HERMES_VENV"
fi

# ============================================================================
# STEP 2: Write Hermes config + credentials
# ============================================================================
step "2/5" "Hermes config + credentials"

HERMES_CONF_YAML="$HERMES_HOME/config.yaml"
HERMES_ENV="$HERMES_HOME/.env"
HERMES_CREDS="$HOME/.Hermes/credentials"

mkdir -p "$HERMES_HOME"
mkdir -p "$HERMES_CREDS"
chmod 700 "$HERMES_CREDS"

# --- config.yaml ---
if [ -f "$HERMES_CONF_YAML" ]; then
  backup="$HERMES_CONF_YAML.pre-bootstrap-$(date +%Y%m%d-%H%M%S)"
  cp "$HERMES_CONF_YAML" "$backup"
  ok "Backed up existing config → $backup"
fi

cat > "$HERMES_CONF_YAML" <<'YAML_EOF'
# Hermes config — written by spaice-agent bootstrap
# Models pinned to the spaice-agent build stack.

model:
  default: "anthropic/claude-opus-4.7"
  provider: "openrouter"
  base_url: "https://openrouter.ai/api/v1"

compression:
  enabled: true
  threshold: 0.50
  target_ratio: 0.20
  protect_last_n: 20

prompt_caching:
  cache_ttl: "5m"

terminal:
  backend: "local"
  cwd: "."
  timeout: 180
  lifetime_seconds: 300

browser:
  inactivity_timeout: 120

platform_toolsets:
  cli: [hermes-cli]
  telegram: [hermes-telegram]

platforms:
  telegram:
    enabled: true
    channel_prompts: {}

agent:
  max_turns: 60
  reasoning_effort: "medium"

skills:
  creation_nudge_interval: 15

stt:
  enabled: true
  local:
    model: "base"

session_reset:
  mode: both
  idle_minutes: 1440
  at_hour: 4

display:
  tool_progress: all
  interim_assistant_messages: true
  streaming: true

code_execution:
  timeout: 300
  max_tool_calls: 50

delegation:
  max_iterations: 50

# Memory — tuned for the spaice-agent vault (Dewey 8-layer shelves).
# spaice-agent owns the richer memory system on top of this — the miner
# pulls from session JSONLs and writes classifier drafts into _inbox/.
# These values pace Hermes' in-process memory to play nicely with that.
memory_char_limit: 2200
user_char_limit: 1375
nudge_interval: 10
flush_min_turns: 6
YAML_EOF
ok "Wrote $HERMES_CONF_YAML"

# --- .env (keys + telegram cfg) ---
# SECURITY: existing .env may contain secrets. Back it up under umask 077
# so the backup inherits 0600 permissions. We deliberately umask BEFORE cp.
if [ -f "$HERMES_ENV" ]; then
  backup="$HERMES_ENV.pre-bootstrap-$(date +%Y%m%d-%H%M%S)"
  (umask 077 && cp "$HERMES_ENV" "$backup" && chmod 600 "$backup")
  ok "Backed up existing .env → $backup (chmod 600)"
fi

# Safely shell-quote a value for embedding in a POSIX .env file.
# Produces a single-quoted string with embedded ' escaped as '\''.
shell_quote() {
  local s="$1"
  # Escape any single quotes: ' → '\''
  printf "'%s'" "$(printf '%s' "$s" | sed "s/'/'\\\\''/g")"
}

umask 077
{
  printf '# Written by spaice-agent bootstrap on %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '# Protect this file — it contains API keys. chmod 600.\n\n'
  printf '# LLM provider — OpenRouter covers all four models in the build stack.\n'
  printf 'OPENROUTER_API_KEY=%s\n\n' "$(shell_quote "$SPAICE_OPENROUTER_KEY")"
  printf '# Web search\n'
  printf 'EXA_API_KEY=%s\n\n' "$(shell_quote "$SPAICE_EXA_KEY")"
  printf '# Messaging platform\n'
  printf 'TELEGRAM_BOT_TOKEN=%s\n' "$(shell_quote "$SPAICE_TELEGRAM_BOT_TOKEN")"
  printf 'TELEGRAM_ALLOWED_USERS=%s\n' "$(shell_quote "$SPAICE_TELEGRAM_ALLOWED_USER")"
  printf 'TELEGRAM_HOME_CHANNEL=%s\n' "$(shell_quote "$SPAICE_TELEGRAM_ALLOWED_USER")"
} > "$HERMES_ENV"
chmod 600 "$HERMES_ENV"
ok "Wrote $HERMES_ENV (chmod 600, values shell-quoted)"
umask 022

# --- mirror keys into credentials store (spaice-agent convention) ---
write_cred_key() {
  local filename="$1" value="$2"
  local path="$HERMES_CREDS/$filename"
  (umask 077 && printf '%s' "$value" > "$path" && chmod 600 "$path")
}
# JSON-escape a value using Python. Prefer the Hermes venv's python
# (guaranteed present — we depend on the venv anyway), fall back to python3
# on PATH. Escapes ", \, control chars, newlines, etc.
json_escape() {
  local py
  if [ -x "$HERMES_VENV/bin/python" ]; then
    py="$HERMES_VENV/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    py=python3
  elif command -v python >/dev/null 2>&1; then
    py=python
  else
    fail "No python interpreter found — cannot safely escape credential JSON."
  fi
  "$py" -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"
}
write_cred_json() {
  local filename="$1" value="$2"
  local path="$HERMES_CREDS/$filename"
  local escaped
  escaped=$(json_escape "$value")
  (umask 077 && printf '{"api_key": %s}\n' "$escaped" > "$path" && chmod 600 "$path")
}

write_cred_key  openrouter.key  "$SPAICE_OPENROUTER_KEY"
write_cred_json exa.json        "$SPAICE_EXA_KEY"
ok "Mirrored keys to $HERMES_CREDS/"

# ============================================================================
# STEP 3: Validate OpenRouter key
# ============================================================================
step "3/5" "Validating OpenRouter key"

# SECURITY: put the auth header in a file (mode 600) and point curl at it via
# --header @file, so the secret never appears in argv. Cleaned up on exit.
AUTH_HEADER_FILE=$(mktemp -t spaice-auth-XXXXXX) || fail "mktemp failed"
chmod 600 "$AUTH_HEADER_FILE"
# shellcheck disable=SC2064
trap "rm -f '$AUTH_HEADER_FILE'; restore_tty" EXIT INT TERM HUP
printf 'Authorization: Bearer %s\n' "$SPAICE_OPENROUTER_KEY" > "$AUTH_HEADER_FILE"

validation=$(curl -fsS -H "@$AUTH_HEADER_FILE" \
  https://openrouter.ai/api/v1/auth/key 2>/dev/null || echo "")
rm -f "$AUTH_HEADER_FILE"
# Restore the narrower trap.
# shellcheck disable=SC2064
trap restore_tty EXIT INT TERM HUP
if [ -z "$validation" ]; then
  warn "Could not validate OpenRouter key (network or auth issue)."
  warn "Continuing — models will fail on first call if key is invalid."
else
  label=$(printf '%s' "$validation" | grep -o '"label"[^,]*' | head -1 || echo "")
  ok "OpenRouter key is valid ${label:+($label)}"
fi

# ============================================================================
# STEP 4: Install spaice-agent on top of Hermes
# ============================================================================
step "4/5" "spaice-agent framework"

SPAICE_INSTALLER_URL="${SPAICE_INSTALLER_URL:-https://raw.githubusercontent.com/spaice-ai/spaice-agent/v0.3.3/install.sh}"

# Inject the Hermes venv path so install.sh skips its own discovery
export SPAICE_HERMES_VENV="$HERMES_VENV"

# SECURITY: download-then-execute pattern — no curl|sh.
SPAICE_INSTALLER=$(mktemp -t spaice-install-XXXXXX) || fail "mktemp failed"
# shellcheck disable=SC2064
trap "rm -f '$SPAICE_INSTALLER'; restore_tty" EXIT INT TERM HUP
if ! curl -fsSL "$SPAICE_INSTALLER_URL" -o "$SPAICE_INSTALLER"; then
  rm -f "$SPAICE_INSTALLER"
  fail "Download failed: $SPAICE_INSTALLER_URL"
fi
if [ ! -s "$SPAICE_INSTALLER" ]; then
  rm -f "$SPAICE_INSTALLER"
  fail "spaice-agent installer downloaded empty. Retry."
fi
SPAICE_SHEBANG=$(head -1 "$SPAICE_INSTALLER")
case "$SPAICE_SHEBANG" in
  *bash*) SPAICE_INTERP=bash ;;
  *sh*)   SPAICE_INTERP=sh ;;
  *)
    rm -f "$SPAICE_INSTALLER"
    fail "spaice-agent installer doesn't look like a shell script (shebang: $SPAICE_SHEBANG)"
    ;;
esac
if ! "$SPAICE_INTERP" "$SPAICE_INSTALLER" "$SPAICE_AGENT_NAME" v0.3.3 --full; then
  rm -f "$SPAICE_INSTALLER"
  fail "spaice-agent install failed. Hermes is installed + configured; re-run:
      curl -fsSL $SPAICE_INSTALLER_URL | $SPAICE_INTERP -s $SPAICE_AGENT_NAME v0.3.3 --full"
fi
rm -f "$SPAICE_INSTALLER"
# shellcheck disable=SC2064
trap restore_tty EXIT INT TERM HUP

# ============================================================================
# STEP 5: Start the gateway (Telegram)
# ============================================================================
step "5/5" "Next steps"

cat <<NEXT

${C_BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${C_NC}
${C_BOLD}  Install complete.${C_NC}
${C_BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${C_NC}

${C_BOLD}What's installed:${C_NC}
  • Hermes agent at $HERMES_HOME
  • spaice-agent package in $HERMES_VENV
  • Vault at ~/$SPAICE_AGENT_NAME/
  • Bundled skills in ~/.hermes/skills/
  • Config: $HERMES_CONF_YAML
  • Keys:   $HERMES_ENV (chmod 600)

${C_BOLD}Start the Telegram gateway:${C_NC}
  ${C_GREEN}hermes gateway start telegram${C_NC}

  Then DM your bot. If you're not sure which bot, find it via:
  ${C_DIM}@BotFather → /mybots${C_NC}

${C_BOLD}Local CLI (instead of Telegram):${C_NC}
  ${C_GREEN}hermes chat${C_NC}

${C_BOLD}Verify install:${C_NC}
  ${C_GREEN}spaice-agent doctor $SPAICE_AGENT_NAME${C_NC}
  ${C_GREEN}spaice-agent list${C_NC}

${C_BOLD}Memory loop (run on demand or via cron):${C_NC}
  ${C_GREEN}spaice-agent mine $SPAICE_AGENT_NAME${C_NC}        # pull facts from sessions
  ${C_GREEN}spaice-agent triage $SPAICE_AGENT_NAME${C_NC}      # promote _inbox/ → shelves
  ${C_GREEN}spaice-agent summarise $SPAICE_AGENT_NAME${C_NC}   # write _continuity/LATEST.md

${C_DIM}If 'spaice-agent' or 'hermes' isn't found, add ~/.local/bin to \$PATH:
  export PATH="\$HOME/.local/bin:\$PATH"${C_NC}

NEXT
