"""Tests for spaice_agent.cli — install/upgrade/uninstall/list/doctor commands."""
from __future__ import annotations

import json
import pytest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from spaice_agent.cli import (
    cmd_install, cmd_doctor, cmd_version, cmd_uninstall, cmd_list, main,
    _load_config_template,
)


class FakeArgs:
    def __init__(self, **kwargs):
        # Sensible defaults for every flag
        self.agent_id = kwargs.get("agent_id", "testbot")
        self.force = kwargs.get("force", False)
        self.with_config = kwargs.get("with_config", False)
        self.purge = kwargs.get("purge", False)
        self.keep_backup = kwargs.get("keep_backup", False)
        self.skip_pip = kwargs.get("skip_pip", False)
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_install_creates_shim_and_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    exit_code = cmd_install(FakeArgs(agent_id="testbot"))
    assert exit_code == 0

    hook_dir = tmp_path / ".Hermes" / "hooks" / "spaice-testbot"
    assert hook_dir.exists()
    assert (hook_dir / "handler.py").exists()
    assert (hook_dir / "HOOK.yaml").exists()
    assert (hook_dir / ".spaice-install.json").exists()

    shim = (hook_dir / "handler.py").read_text()
    assert "from spaice_agent.hook import make_hook" in shim
    assert 'AGENT_ID = "testbot"' in shim

    manifest = json.loads((hook_dir / ".spaice-install.json").read_text())
    assert manifest["agent_id"] == "testbot"


def test_install_with_config_scaffolds_yaml(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    exit_code = cmd_install(FakeArgs(agent_id="scopebot", with_config=True))
    assert exit_code == 0

    cfg = tmp_path / ".spaice-agents" / "scopebot" / "config.yaml"
    assert cfg.exists()
    content = cfg.read_text()
    assert "agent_id: scopebot" in content
    assert "memory_root: ~/scopebot" in content
    # Template placeholders should all be rendered
    assert "{agent_id}" not in content
    assert "{version}" not in content

    captured = capsys.readouterr()
    assert "NEXT STEPS" in captured.out


def test_install_with_config_skips_existing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Pre-create config
    cfg_dir = tmp_path / ".spaice-agents" / "scopebot"
    cfg_dir.mkdir(parents=True)
    cfg = cfg_dir / "config.yaml"
    cfg.write_text("# hand-edited, don't clobber")

    cmd_install(FakeArgs(agent_id="scopebot", with_config=True))

    # Existing config should be preserved
    assert "hand-edited" in cfg.read_text()
    captured = capsys.readouterr()
    assert "skipped" in captured.out.lower() or "already exists" in captured.out.lower()


def test_install_backs_up_existing_hook(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    cmd_install(FakeArgs(agent_id="testbot"))
    hook_dir = tmp_path / ".Hermes" / "hooks" / "spaice-testbot"
    (hook_dir / "handler.py").write_text("custom edit")

    cmd_install(FakeArgs(agent_id="testbot"))  # no --force

    hooks_root = tmp_path / ".Hermes" / "hooks"
    backups = [p for p in hooks_root.iterdir() if "backup" in p.name]
    assert len(backups) >= 1
    assert "custom edit" in (backups[0] / "handler.py").read_text()


def test_install_force_overwrites_no_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    cmd_install(FakeArgs(agent_id="testbot"))
    cmd_install(FakeArgs(agent_id="testbot", force=True))

    hooks_root = tmp_path / ".Hermes" / "hooks"
    backups = [p for p in hooks_root.iterdir() if "backup" in p.name]
    assert len(backups) == 0


def test_uninstall_removes_hook_keeps_config(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cmd_install(FakeArgs(agent_id="testbot", with_config=True))

    hook_dir = tmp_path / ".Hermes" / "hooks" / "spaice-testbot"
    cfg_dir = tmp_path / ".spaice-agents" / "testbot"
    assert hook_dir.exists() and cfg_dir.exists()

    cmd_uninstall(FakeArgs(agent_id="testbot"))

    assert not hook_dir.exists()
    assert cfg_dir.exists()  # config preserved by default

    captured = capsys.readouterr()
    assert "Kept" in captured.out


def test_uninstall_purge_removes_both(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cmd_install(FakeArgs(agent_id="testbot", with_config=True))

    cmd_uninstall(FakeArgs(agent_id="testbot", purge=True))

    assert not (tmp_path / ".Hermes" / "hooks" / "spaice-testbot").exists()
    assert not (tmp_path / ".spaice-agents" / "testbot").exists()


def test_uninstall_keep_backup_renames(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cmd_install(FakeArgs(agent_id="testbot"))

    cmd_uninstall(FakeArgs(agent_id="testbot", keep_backup=True))

    hooks_root = tmp_path / ".Hermes" / "hooks"
    backups = [p for p in hooks_root.iterdir() if "uninstall" in p.name]
    assert len(backups) == 1


def test_uninstall_nonexistent_does_not_crash(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    exit_code = cmd_uninstall(FakeArgs(agent_id="neverexisted"))
    assert exit_code == 0


def test_list_empty(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    exit_code = cmd_list(FakeArgs())
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "No spaice-* agents" in captured.out


def test_list_shows_installed(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cmd_install(FakeArgs(agent_id="alpha", with_config=True))
    cmd_install(FakeArgs(agent_id="beta"))

    cmd_list(FakeArgs())
    captured = capsys.readouterr()
    assert "alpha" in captured.out
    assert "beta" in captured.out
    assert "config" in captured.out  # alpha has config
    assert "NO CONFIG" in captured.out  # beta doesn't


def test_doctor_reports_missing_hook(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    exit_code = cmd_doctor(FakeArgs(agent_id="notinstalled"))
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Hook dir exists" in captured.out


def test_doctor_healthy_hook_missing_config(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cmd_install(FakeArgs(agent_id="testbot"))  # no --with-config

    cmd_doctor(FakeArgs(agent_id="testbot"))
    captured = capsys.readouterr()
    assert "✓ Hook dir exists" in captured.out
    assert "✓ handler.py exists" in captured.out
    # Config should be flagged as missing
    assert "✗ Config file exists" in captured.out


def test_doctor_healthy_with_config(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cmd_install(FakeArgs(agent_id="testbot", with_config=True))

    cmd_doctor(FakeArgs(agent_id="testbot"))
    captured = capsys.readouterr()
    assert "✓ Hook dir exists" in captured.out
    assert "✓ Config file exists" in captured.out


def test_version_command(capsys):
    exit_code = cmd_version(FakeArgs())
    assert exit_code == 0
    captured = capsys.readouterr()
    assert captured.out.strip()


def test_main_requires_command():
    with pytest.raises(SystemExit):
        main([])


def test_main_install_routes_correctly(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    exit_code = main(["install", "testbot"])
    assert exit_code == 0
    assert (tmp_path / ".Hermes" / "hooks" / "spaice-testbot" / "handler.py").exists()


def test_main_install_with_config_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    exit_code = main(["install", "testbot", "--with-config"])
    assert exit_code == 0
    assert (tmp_path / ".spaice-agents" / "testbot" / "config.yaml").exists()


def test_main_list_and_uninstall_flow(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    main(["install", "alpha"])
    main(["install", "beta", "--with-config"])
    main(["list"])
    captured = capsys.readouterr()
    assert "alpha" in captured.out and "beta" in captured.out

    main(["uninstall", "alpha", "--purge"])
    capsys.readouterr()  # drop uninstall output

    main(["list"])
    captured = capsys.readouterr()
    # After purge, alpha shouldn't be in the list section
    assert "Installed agents" in captured.out
    # Extract just the list portion (after the header)
    list_section = captured.out.split("Installed agents", 1)[1]
    assert "alpha" not in list_section
    assert "beta" in list_section


def test_config_template_loads():
    """Ensure the packaged template resolves via importlib.resources."""
    template = _load_config_template()
    assert "{agent_id}" in template
    assert "{version}" in template
    assert "{generated_at}" in template


def test_agent_id_with_hyphens_works(tmp_path, monkeypatch):
    """Agent IDs with hyphens (e.g. scope-bot) must work end-to-end."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    exit_code = cmd_install(FakeArgs(agent_id="scope-bot", with_config=True))
    assert exit_code == 0
    assert (tmp_path / ".Hermes" / "hooks" / "spaice-scope-bot").exists()
    assert (tmp_path / ".spaice-agents" / "scope-bot" / "config.yaml").exists()


# ---------- skills (antigravity library) ----------

def test_skills_status_not_installed(tmp_path, monkeypatch, capsys):
    from spaice_agent.cli import cmd_skills
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    exit_code = cmd_skills(FakeArgs(skills_action="status"))
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "NOT INSTALLED" in captured.out


def test_skills_status_when_installed(tmp_path, monkeypatch, capsys):
    from spaice_agent.cli import cmd_skills
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Fake a minimal install
    antigravity = tmp_path / ".hermes" / "skills" / "antigravity"
    (antigravity / "skill-a").mkdir(parents=True)
    (antigravity / "skill-a" / "SKILL.md").write_text("---\nname: skill-a\ndescription: test\n---\nbody")
    (antigravity / "skill-b").mkdir(parents=True)
    (antigravity / "skill-b" / "SKILL.md").write_text("---\nname: skill-b\ndescription: test\n---\nbody")

    exit_code = cmd_skills(FakeArgs(skills_action="status"))
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "INSTALLED" in captured.out
    assert "Skills: 2" in captured.out


def test_skills_install_idempotent(tmp_path, monkeypatch, capsys):
    """Second `skills install` without --force should skip, not error."""
    from spaice_agent.cli import cmd_skills
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    antigravity = tmp_path / ".hermes" / "skills" / "antigravity"
    (antigravity / "dummy").mkdir(parents=True)
    (antigravity / "dummy" / "SKILL.md").write_text("x")

    exit_code = cmd_skills(FakeArgs(skills_action="install", force=False))
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Already installed" in captured.out


def test_skills_remove_nonexistent(tmp_path, monkeypatch, capsys):
    from spaice_agent.cli import cmd_skills
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    exit_code = cmd_skills(FakeArgs(skills_action="remove"))
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "nothing to remove" in captured.out.lower() or "not installed" in captured.out.lower()


def test_skills_remove_existing(tmp_path, monkeypatch, capsys):
    from spaice_agent.cli import cmd_skills
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    antigravity = tmp_path / ".hermes" / "skills" / "antigravity"
    (antigravity / "dummy").mkdir(parents=True)

    exit_code = cmd_skills(FakeArgs(skills_action="remove"))
    assert exit_code == 0
    assert not antigravity.exists()


def test_doctor_reports_antigravity_when_present(tmp_path, monkeypatch, capsys):
    """Doctor should show an ✓ line for antigravity when installed."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cmd_install(FakeArgs(agent_id="testbot", with_config=True))
    antigravity = tmp_path / ".hermes" / "skills" / "antigravity"
    (antigravity / "skill-a").mkdir(parents=True)
    (antigravity / "skill-a" / "SKILL.md").write_text("x")

    cmd_doctor(FakeArgs(agent_id="testbot"))
    captured = capsys.readouterr()
    assert "Antigravity skill library installed" in captured.out
    assert "1 skills" in captured.out


def test_doctor_notes_antigravity_when_absent(tmp_path, monkeypatch, capsys):
    """Doctor should print a ○ line when antigravity not installed."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cmd_install(FakeArgs(agent_id="testbot", with_config=True))

    cmd_doctor(FakeArgs(agent_id="testbot"))
    captured = capsys.readouterr()
    assert "Antigravity skill library" in captured.out
    assert "not installed" in captured.out
