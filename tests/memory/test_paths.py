"""Tests for spaice_agent.memory.paths."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from spaice_agent.memory.paths import (
    VaultPaths,
    VaultNotFoundError,
    VaultStructureError,
    CANONICAL_SHELVES,
    SPECIAL_DIRS,
)


# -- for_agent resolution ---------------------------------------------------


def test_for_agent_reads_memory_root_from_config(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Create a vault at a custom location
    custom_vault = tmp_path / "elsewhere" / "my-vault"
    custom_vault.mkdir(parents=True)
    # Write config.yaml pointing to it
    cfg_dir = tmp_path / ".spaice-agents" / "alice"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.yaml").write_text(yaml.safe_dump({
        "memory": {"memory_root": str(custom_vault)}
    }))

    paths = VaultPaths.for_agent("alice")
    assert paths.vault_root == custom_vault.resolve()
    assert paths.agent_id == "alice"


def test_for_agent_falls_back_to_convention(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # No config.yaml, but ~/bob exists
    (tmp_path / "bob").mkdir()

    paths = VaultPaths.for_agent("bob")
    assert paths.vault_root == (tmp_path / "bob").resolve()


def test_for_agent_raises_when_no_vault(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    with pytest.raises(VaultNotFoundError) as exc:
        VaultPaths.for_agent("ghost")
    assert "ghost" in str(exc.value)


def test_for_agent_rejects_empty_id(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    with pytest.raises(ValueError):
        VaultPaths.for_agent("")


def test_for_agent_create_agent_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / "carol").mkdir()
    paths = VaultPaths.for_agent("carol", create_agent_dir=True)
    assert paths.agent_config_dir.exists()


def test_for_agent_malformed_config_falls_back(tmp_path, monkeypatch):
    """If config.yaml is broken, we should fall back to convention rather than crash."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / "dave").mkdir()
    cfg_dir = tmp_path / ".spaice-agents" / "dave"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.yaml").write_text("::: not valid yaml :::")

    # Should not crash; should use convention fallback
    paths = VaultPaths.for_agent("dave")
    assert paths.vault_root == (tmp_path / "dave").resolve()


# -- for_vault constructor (tooling) ----------------------------------------


def test_for_vault_explicit_root(tmp_path):
    vault = tmp_path / "standalone"
    vault.mkdir()
    paths = VaultPaths.for_vault(vault)
    assert paths.vault_root == vault.resolve()
    assert paths.agent_id == "_standalone"


def test_for_vault_missing_raises(tmp_path):
    with pytest.raises(VaultNotFoundError):
        VaultPaths.for_vault(tmp_path / "never-created")


# -- directory accessors ----------------------------------------------------


def test_directory_accessors(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    paths = VaultPaths.for_vault(vault, agent_id="test")
    assert paths.inbox == vault / "_inbox"
    assert paths.continuity == vault / "_continuity"
    assert paths.dashboard == vault / "_dashboard"
    assert paths.templates == vault / "_templates"
    assert paths.archive == vault / "_archive"


def test_triggers_yaml_lives_in_agent_dir_not_vault(tmp_path, monkeypatch):
    """triggers.yaml is runtime artefact — belongs in ~/.spaice-agents/, NOT vault."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / "eve").mkdir()
    paths = VaultPaths.for_agent("eve")
    assert paths.triggers_yaml == tmp_path / ".spaice-agents" / "eve" / "triggers.yaml"
    assert str(paths.triggers_yaml).startswith(str(tmp_path / ".spaice-agents"))
    assert not str(paths.triggers_yaml).startswith(str(paths.vault_root))


def test_entity_cache_lives_in_agent_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / "frank").mkdir()
    paths = VaultPaths.for_agent("frank")
    assert paths.entity_cache.parent == tmp_path / ".spaice-agents" / "frank"


# -- shelves ----------------------------------------------------------------


def test_shelves_are_canonical_order(tmp_path):
    vault = tmp_path / "v"
    vault.mkdir()
    paths = VaultPaths.for_vault(vault)
    assert paths.shelves == CANONICAL_SHELVES
    # First shelf must be identity (Jarvis convention)
    assert paths.shelves[0] == "identity"


def test_shelf_path_valid(tmp_path):
    vault = tmp_path / "v"
    vault.mkdir()
    paths = VaultPaths.for_vault(vault)
    assert paths.shelf_path("projects") == vault / "projects"


def test_shelf_path_rejects_unknown(tmp_path):
    vault = tmp_path / "v"
    vault.mkdir()
    paths = VaultPaths.for_vault(vault)
    with pytest.raises(ValueError, match="not a canonical shelf"):
        paths.shelf_path("arbitrary")


# -- skeleton lifecycle -----------------------------------------------------


def test_ensure_skeleton_creates_all_dirs(tmp_path):
    vault = tmp_path / "new-vault"
    vault.mkdir()
    paths = VaultPaths.for_vault(vault, agent_id="skel-test")
    paths.ensure_skeleton()

    for name in CANONICAL_SHELVES:
        assert (vault / name).is_dir(), f"shelf {name} not created"
    for name in SPECIAL_DIRS:
        assert (vault / name).is_dir(), f"special dir {name} not created"


def test_ensure_skeleton_is_idempotent(tmp_path):
    vault = tmp_path / "idem"
    vault.mkdir()
    paths = VaultPaths.for_vault(vault, agent_id="idem-test")
    paths.ensure_skeleton()
    paths.ensure_skeleton()  # second call must not raise
    assert (vault / "identity").is_dir()


def test_validate_passes_on_good_vault(tmp_path):
    vault = tmp_path / "good"
    vault.mkdir()
    paths = VaultPaths.for_vault(vault, agent_id="good-test")
    paths.ensure_skeleton()
    paths.validate()  # no exception


def test_validate_raises_on_missing_inbox(tmp_path):
    vault = tmp_path / "noinbox"
    vault.mkdir()
    paths = VaultPaths.for_vault(vault, agent_id="noinbox-test")
    # Skeleton not created → _inbox missing
    with pytest.raises(VaultStructureError, match="Inbox missing"):
        paths.validate()


# -- agent isolation --------------------------------------------------------


def test_two_agents_have_isolated_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / "alice").mkdir()
    (tmp_path / "bob").mkdir()

    alice = VaultPaths.for_agent("alice")
    bob = VaultPaths.for_agent("bob")

    assert alice.vault_root != bob.vault_root
    assert alice.agent_config_dir != bob.agent_config_dir
    assert alice.triggers_yaml != bob.triggers_yaml
