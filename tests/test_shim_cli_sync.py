"""Regression guard for Phase 4B shim fix.

The spaice-agent shell shim at /Users/<home>/.local/bin/spaice-agent
hardcodes a SPAICE_SUBCOMMANDS array to decide which commands route to
the Python CLI vs pass through to Hermes. If a subparser is added to
cli.py but not to the shim, that command silently falls through to
Hermes and users see a confusing "invalid choice" error.

This test compares the two lists and fails if they drift.

If the shim isn't installed (CI, fresh dev checkout), the test skips.
The authoritative shim source ships in-repo at packaging/spaice-agent-shim.sh
once 4B lands there; for now the only deployed copy is ~/.local/bin/
and the test is best-effort.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

# The canonical source of truth for spaice-agent's own subcommands.
# Matches the list in spaice_agent/cli.py's build_parser().
EXPECTED_SPAICE_SUBCOMMANDS = {
    "install", "uninstall", "list", "upgrade", "version",
    "doctor", "skills", "vault",
    "mine", "triage", "summarise", "dashboards", "recall", "audit",
}


def _find_installed_shim() -> Path | None:
    """Return the installed shim path if present, else None."""
    candidate = Path.home() / ".local" / "bin" / "spaice-agent"
    if candidate.exists() and candidate.is_file():
        return candidate
    return None


def _parse_shim_subcommands(shim_path: Path) -> set[str]:
    """Extract the SPAICE_SUBCOMMANDS bash array from the shim."""
    text = shim_path.read_text(encoding="utf-8")
    match = re.search(
        r"SPAICE_SUBCOMMANDS=\(\s*([^)]+)\)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        pytest.fail(
            "Could not find SPAICE_SUBCOMMANDS=(...) in shim. "
            "Did the shim format change?"
        )
    # Strip comments, split whitespace, drop empty tokens
    raw = match.group(1)
    tokens = []
    for line in raw.splitlines():
        line = line.split("#", 1)[0]
        tokens.extend(line.split())
    return {t for t in tokens if t}


def _cli_subparsers() -> set[str]:
    """Extract the subparser names from cli.py's build_parser()."""
    cli_path = Path(__file__).resolve().parent.parent / "spaice_agent" / "cli.py"
    text = cli_path.read_text(encoding="utf-8")
    # Match: sub.add_parser("NAME", ...)
    return set(re.findall(r'sub\.add_parser\(\s*["\']([a-z-]+)["\']', text))


def test_shim_subcommands_match_expected():
    """Guards against the shim falling out of sync with EXPECTED list.

    The EXPECTED set is the authoritative list maintained in THIS test.
    When you add a new spaice-agent subcommand:
      1. Add the subparser in spaice_agent/cli.py
      2. Add the name to EXPECTED_SPAICE_SUBCOMMANDS above
      3. Add the name to SPAICE_SUBCOMMANDS in packaging/spaice-agent-shim.sh
    """
    cli_cmds = _cli_subparsers()
    missing = EXPECTED_SPAICE_SUBCOMMANDS - cli_cmds
    extra = cli_cmds - EXPECTED_SPAICE_SUBCOMMANDS
    assert not missing, (
        f"Subcommands in EXPECTED_SPAICE_SUBCOMMANDS but NOT in cli.py's "
        f"build_parser(): {sorted(missing)}. Either add them to cli.py or "
        f"remove from EXPECTED."
    )
    assert not extra, (
        f"Subcommands in cli.py but NOT in EXPECTED_SPAICE_SUBCOMMANDS: "
        f"{sorted(extra)}. Add them to EXPECTED (and to the shim's "
        f"SPAICE_SUBCOMMANDS array) so the shim routes them correctly."
    )


def test_installed_shim_has_all_expected_subcommands():
    """If the shim is installed locally, assert it knows every expected sub.

    Skips cleanly when the shim isn't present (CI, fresh checkout).
    """
    shim = _find_installed_shim()
    if shim is None:
        pytest.skip("spaice-agent shim not installed at ~/.local/bin/")
    shim_cmds = _parse_shim_subcommands(shim)
    missing = EXPECTED_SPAICE_SUBCOMMANDS - shim_cmds
    assert not missing, (
        f"Installed shim {shim} is missing these subcommands: "
        f"{sorted(missing)}. Update SPAICE_SUBCOMMANDS in the shim "
        f"source at spaice_agent/packaging/spaice-agent-shim.sh (or the "
        f"copy at {shim})."
    )
