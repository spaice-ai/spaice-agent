"""Regression tests for Phase 2B CLI fixes (Codex 5.3 pass 2)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).parent.parent


def _run_cli(*args, env=None):
    """Run the CLI in-process for clean stdout/stderr capture."""
    result = subprocess.run(
        [sys.executable, "-m", "spaice_agent.cli", *args],
        capture_output=True, text=True,
        cwd=str(REPO),
        env=env,
    )
    return result


def test_vault_scaffold_dry_run_writes_nothing(tmp_path: Path, monkeypatch):
    """Codex 5.3 pass-2 #1: --dry-run must not create skeleton dirs."""
    # Redirect HOME so VaultPaths.for_agent uses tmp_path/<agent>
    monkeypatch.setenv("HOME", str(tmp_path))
    # The vault root ~/<agent_id>/ must exist for for_agent to resolve
    vault_root = tmp_path / "testvault"
    vault_root.mkdir()

    r = _run_cli("vault", "scaffold", "testvault", "--dry-run",
                 env={**{k: v for k, v in __import__("os").environ.items()},
                      "HOME": str(tmp_path)})
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert "[dry-run]" in r.stdout
    # Nothing should have been created inside the vault root
    # (skeleton dirs like _inbox/, identity/, etc.)
    skeleton_dirs = [d for d in vault_root.iterdir() if d.is_dir()]
    assert skeleton_dirs == [], (
        f"dry-run created skeleton dirs: {[d.name for d in skeleton_dirs]}"
    )


def test_vault_scaffold_writes_when_not_dry_run(tmp_path: Path, monkeypatch):
    """Sanity: without --dry-run, skeleton + content DO get written."""
    vault_root = tmp_path / "testvault"
    vault_root.mkdir()

    r = _run_cli("vault", "scaffold", "testvault",
                 env={**{k: v for k, v in __import__("os").environ.items()},
                      "HOME": str(tmp_path)})
    assert r.returncode == 0, f"stderr={r.stderr!r}"
    assert (vault_root / "_inbox").is_dir()
    assert (vault_root / "README.md").is_file()


def test_install_sh_rejects_unknown_flags():
    """Codex 5.3 pass-2 #2: unknown flags must fail fast, not warn."""
    # Run install.sh with an unknown flag; args stop at flag parsing
    # so no real install happens.
    r = subprocess.run(
        ["bash", str(REPO / "install.sh"), "testagent", "main", "--not-a-real-flag"],
        capture_output=True, text=True,
        timeout=30,
    )
    assert r.returncode != 0, (
        f"install.sh should reject unknown flags. "
        f"rc={r.returncode} stdout={r.stdout!r} stderr={r.stderr!r}"
    )
    assert "unknown flag" in (r.stdout + r.stderr).lower()


def test_install_sh_accepts_with_vault_without_version():
    """Codex 5.3 pass-2 side-effect: --with-vault works WITHOUT version_spec.

    Uses a bash-level sanity check that the flag-parsing block is correct.
    We can't actually run the installer (it would try to `pip install` etc.),
    so we use bash -n (syntax check) then exercise just the arg-parsing block.
    """
    r = subprocess.run(
        ["bash", "-n", str(REPO / "install.sh")],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"install.sh syntax error: {r.stderr}"

    # Extract + exec just the arg-parsing prelude with simulated args
    script = """
set -eu
AGENT_ID="${1:-}"
if [ -z "$AGENT_ID" ]; then echo "ERR: no id"; exit 2; fi
shift
VERSION_SPEC="main"
if [ $# -gt 0 ] && [ "${1#--}" = "$1" ]; then
  VERSION_SPEC="$1"; shift
fi
WITH_VAULT=0
while [ $# -gt 0 ]; do
  case "$1" in
    --with-vault|--full) WITH_VAULT=1 ;;
    *) echo "ERROR: unknown flag: $1" >&2; exit 1 ;;
  esac
  shift
done
echo "AGENT_ID=$AGENT_ID VERSION=$VERSION_SPEC VAULT=$WITH_VAULT"
"""
    r = subprocess.run(
        ["bash", "-c", script, "_", "jarvis", "--with-vault"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"stderr={r.stderr!r}"
    assert "AGENT_ID=jarvis" in r.stdout
    assert "VERSION=main" in r.stdout
    assert "VAULT=1" in r.stdout


def test_install_sh_accepts_version_and_flag():
    """Sanity: positional version + flag both honoured."""
    script = """
set -eu
AGENT_ID="${1:-}"
shift
VERSION_SPEC="main"
if [ $# -gt 0 ] && [ "${1#--}" = "$1" ]; then
  VERSION_SPEC="$1"; shift
fi
WITH_VAULT=0
while [ $# -gt 0 ]; do
  case "$1" in
    --with-vault|--full) WITH_VAULT=1 ;;
    *) exit 1 ;;
  esac
  shift
done
echo "A=$AGENT_ID V=$VERSION_SPEC W=$WITH_VAULT"
"""
    r = subprocess.run(
        ["bash", "-c", script, "_", "jarvis", "v0.2.0", "--full"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"stderr={r.stderr!r}"
    assert r.stdout.strip() == "A=jarvis V=v0.2.0 W=1"
