#!/usr/bin/env bash
# spaice-agent — CLI dispatcher for the spaice-agent package.
#
# Routes commands to one of two backends:
#   (a) The Python CLI at spaice_agent.cli:main — for spaice-agent's own
#       subcommands (install, list, vault, mine, triage, summarise,
#       dashboards, recall, audit, doctor, skills, upgrade, version,
#       etc.)
#   (b) The Hermes binary — for agent-runtime commands (chat, gateway,
#       sessions, hooks, config, etc.). Hermes is the underlying runtime
#       that spaice-agent installs on top of.
#
# The SPAICE-specific subcommand list is maintained here and must track
# the argparse subparsers in spaice_agent/cli.py. If a new subcommand
# ships, add it to SPAICE_SUBCOMMANDS below.

set -e

SPAICE_SUBCOMMANDS=(
  install uninstall list upgrade version
  doctor skills vault
  mine triage summarise dashboards recall audit
)

# Locate a Python that has spaice_agent importable. Prefer the Hermes
# venv (where `pip install -e .` typically lands during development);
# fall back to the first python3 in PATH.
SPAICE_PY=""
if [ -x "$HOME/.hermes/hermes-agent/venv/bin/python" ] && \
   "$HOME/.hermes/hermes-agent/venv/bin/python" -c "import spaice_agent" 2>/dev/null; then
  SPAICE_PY="$HOME/.hermes/hermes-agent/venv/bin/python"
elif command -v python3 >/dev/null 2>&1 && python3 -c "import spaice_agent" 2>/dev/null; then
  SPAICE_PY="$(command -v python3)"
fi

# Locate the Hermes binary for pass-through commands.
HERMES_BIN=""
if command -v hermes >/dev/null 2>&1; then
  HERMES_BIN="$(command -v hermes)"
elif [ -x "$HOME/.hermes/hermes-agent/venv/bin/hermes" ]; then
  HERMES_BIN="$HOME/.hermes/hermes-agent/venv/bin/hermes"
fi

# --help / -h / --version with no subcommand: show spaice-agent's own
# help if we have the Python CLI available, else fall through to hermes.
if [ $# -eq 0 ] || [ "$1" = "--help" ] || [ "$1" = "-h" ] || [ "$1" = "--version" ]; then
  if [ -n "$SPAICE_PY" ]; then
    exec "$SPAICE_PY" -c "from spaice_agent.cli import main; import sys; sys.exit(main(sys.argv[1:]))" "$@"
  elif [ -n "$HERMES_BIN" ]; then
    exec "$HERMES_BIN" "$@"
  else
    echo "spaice-agent: neither spaice_agent python package nor hermes binary found." >&2
    exit 127
  fi
fi

# Route first arg: is it a spaice-agent-owned subcommand?
FIRST_ARG="$1"
for cmd in "${SPAICE_SUBCOMMANDS[@]}"; do
  if [ "$FIRST_ARG" = "$cmd" ]; then
    if [ -z "$SPAICE_PY" ]; then
      echo "spaice-agent: cannot find a python with spaice_agent installed." >&2
      echo "Try: pip install -e /path/to/spaice-agent" >&2
      exit 127
    fi
    exec "$SPAICE_PY" -c "from spaice_agent.cli import main; import sys; sys.exit(main(sys.argv[1:]))" "$@"
  fi
done

# Not a spaice-agent subcommand — pass through to Hermes.
if [ -z "$HERMES_BIN" ]; then
  echo "spaice-agent: '$FIRST_ARG' is not a spaice-agent subcommand and hermes is not installed." >&2
  echo "Spaice subcommands: ${SPAICE_SUBCOMMANDS[*]}" >&2
  exit 127
fi

exec "$HERMES_BIN" "$@"
