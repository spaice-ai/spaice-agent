"""Tests for spaice_agent.cli — install/upgrade/doctor commands."""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from spaice_agent.cli import cmd_install, cmd_doctor, cmd_version, main


class FakeArgs:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_install_creates_shim_and_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    exit_code = cmd_install(FakeArgs(agent_id="testbot", force=False))
    assert exit_code == 0

    hook_dir = tmp_path / ".Hermes" / "hooks" / "spaice-testbot"
    assert hook_dir.exists()
    assert (hook_dir / "handler.py").exists()
    assert (hook_dir / "HOOK.yaml").exists()
    assert (hook_dir / ".spaice-install.json").exists()

    # Shim should import from spaice_agent.hook
    shim = (hook_dir / "handler.py").read_text()
    assert "from spaice_agent.hook import make_hook" in shim
    assert 'AGENT_ID = "testbot"' in shim

    # Manifest should have agent_id + version
    manifest = json.loads((hook_dir / ".spaice-install.json").read_text())
    assert manifest["agent_id"] == "testbot"
    assert "package_version" in manifest
    assert "installed_at" in manifest


def test_install_backs_up_existing_hook(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    # First install
    cmd_install(FakeArgs(agent_id="testbot", force=False))
    hook_dir = tmp_path / ".Hermes" / "hooks" / "spaice-testbot"
    (hook_dir / "handler.py").write_text("custom edit that should be backed up")

    # Second install without --force should back up
    cmd_install(FakeArgs(agent_id="testbot", force=False))

    # Find backup dir
    hooks_root = tmp_path / ".Hermes" / "hooks"
    backups = [p for p in hooks_root.iterdir() if "backup" in p.name]
    assert len(backups) >= 1
    backup_shim = backups[0] / "handler.py"
    assert "custom edit" in backup_shim.read_text()


def test_install_force_overwrites_no_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    cmd_install(FakeArgs(agent_id="testbot", force=False))
    cmd_install(FakeArgs(agent_id="testbot", force=True))

    hooks_root = tmp_path / ".Hermes" / "hooks"
    backups = [p for p in hooks_root.iterdir() if "backup" in p.name]
    assert len(backups) == 0  # --force means no backup


def test_doctor_reports_missing_hook(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    exit_code = cmd_doctor(FakeArgs(agent_id="notinstalled"))
    assert exit_code == 1  # unhealthy
    captured = capsys.readouterr()
    assert "Hook dir exists" in captured.out


def test_doctor_healthy_install(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cmd_install(FakeArgs(agent_id="testbot", force=False))

    exit_code = cmd_doctor(FakeArgs(agent_id="testbot"))
    captured = capsys.readouterr()
    # Hook files should all be present
    assert "✓ Hook dir exists" in captured.out
    assert "✓ handler.py exists" in captured.out
    assert "✓ HOOK.yaml exists" in captured.out
    assert "✓ Install manifest exists" in captured.out
    # exit_code may be 1 if agent config isn't set up, but install health is fine


def test_version_command(capsys):
    exit_code = cmd_version(FakeArgs())
    assert exit_code == 0
    captured = capsys.readouterr()
    # Should print something (even if "unknown")
    assert captured.out.strip()


def test_main_requires_command():
    with pytest.raises(SystemExit):
        main([])


def test_main_install_routes_correctly(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    exit_code = main(["install", "testbot"])
    assert exit_code == 0
    assert (tmp_path / ".Hermes" / "hooks" / "spaice-testbot" / "handler.py").exists()
