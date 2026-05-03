"""Tests for spaice_agent.credentials — file-based credential store."""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from spaice_agent.config import MissingCredentialError
from spaice_agent.credentials import (
    CredentialPermissionError,
    read_credential,
    resolve_credential,
)


# ---------------------------------------------------------------------------
# read_credential — happy path, perm checks, slug guard
# ---------------------------------------------------------------------------


def _write_key(path: Path, value: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value + "\n", encoding="utf-8")
    os.chmod(path, mode)


def test_read_credential_happy(tmp_path):
    _write_key(tmp_path / "openrouter.key", "sk-abc123")
    assert read_credential("openrouter", base_dir=tmp_path) == "sk-abc123"


def test_read_credential_strips_whitespace(tmp_path):
    path = tmp_path / "exa.key"
    path.write_text("  xx-key-yy  \n\n", encoding="utf-8")
    os.chmod(path, 0o600)
    assert read_credential("exa", base_dir=tmp_path) == "xx-key-yy"


def test_read_credential_missing_file(tmp_path):
    with pytest.raises(MissingCredentialError):
        read_credential("brave", base_dir=tmp_path)


def test_read_credential_empty_file(tmp_path):
    path = tmp_path / "empty.key"
    path.write_text("", encoding="utf-8")
    os.chmod(path, 0o600)
    with pytest.raises(MissingCredentialError):
        read_credential("empty", base_dir=tmp_path)


def test_read_credential_rejects_group_readable(tmp_path):
    _write_key(tmp_path / "openrouter.key", "sk-xxx", mode=0o640)
    with pytest.raises(CredentialPermissionError):
        read_credential("openrouter", base_dir=tmp_path)


def test_read_credential_rejects_world_readable(tmp_path):
    _write_key(tmp_path / "openrouter.key", "sk-xxx", mode=0o604)
    with pytest.raises(CredentialPermissionError):
        read_credential("openrouter", base_dir=tmp_path)


def test_read_credential_rejects_path_traversal(tmp_path):
    with pytest.raises(MissingCredentialError):
        read_credential("../etc/passwd", base_dir=tmp_path)


def test_read_credential_rejects_leading_dot(tmp_path):
    _write_key(tmp_path / ".hidden.key", "x")
    with pytest.raises(MissingCredentialError):
        read_credential(".hidden", base_dir=tmp_path)


def test_read_credential_rejects_nested_slash(tmp_path):
    with pytest.raises(MissingCredentialError):
        read_credential("a/b", base_dir=tmp_path)


def test_read_credential_accepts_0400(tmp_path):
    _write_key(tmp_path / "locked.key", "v", mode=0o400)
    assert read_credential("locked", base_dir=tmp_path) == "v"


# ---------------------------------------------------------------------------
# resolve_credential — with AgentConfig fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def jarvis_config(tmp_path, monkeypatch):
    """Build a minimal AgentConfig via the real loader."""
    from spaice_agent.config import load_agent_config

    load_agent_config.cache_clear()
    cfg_dir = tmp_path / ".spaice-agents" / "jarvis"
    cfg_dir.mkdir(parents=True)
    memory_root = tmp_path / "jarvis_vault"
    memory_root.mkdir()
    config_yaml = f"""
agent_id: jarvis
memory_root: {memory_root}
credentials:
  openrouter_api_key_env: OPENROUTER_API_KEY
  exa_api_key_env: EXA_API_KEY
  brave_api_key_env: BRAVE_API_KEY
hook:
  total_timeout_s: 60.0
memory:
  enabled: true
  entity_cache_path: {memory_root}/cache.json
  stage_timeout_s: 2.0
  recall_max_hits_per_entity: 3
  recall_snippet_chars: 240
  live_capture_dir: {memory_root}/_inbox
search:
  enabled: true
  daily_fire_cap: 50
  stage_timeout_s: 8.0
  providers:
    - name: exa
      endpoint: https://api.exa.ai/search
      max_results: 8
      per_request_timeout_s: 5.0
  merge:
    method: rrf
    k: 60
  triggers:
    phrase_anchors: []
    url_at_end: true
consensus:
  enabled: true
  daily_fire_cap: 50
  pipeline_timeout_s: 90.0
  pipeline:
    - stage: proposer
      model: anthropic/claude-opus-4.7
      stage_timeout_s: 30.0
      max_tokens: 2048
      truncate_output_chars: 8000
      system: "proposer sys"
    - stage: critic
      model: openai/gpt-5-codex
      stage_timeout_s: 45.0
      max_tokens: 2048
      truncate_output_chars: 8000
      system: "critic sys"
    - stage: reviewer
      model: deepseek/deepseek-v3.1-terminus
      stage_timeout_s: 45.0
      max_tokens: 3072
      truncate_output_chars: 12000
      system: "reviewer sys"
  triggers:
    words: [plan, decide]
    phrases: [ask codex]
scrubber:
  enabled: true
  leak_patterns: []
"""
    (cfg_dir / "config.yaml").write_text(config_yaml)
    monkeypatch.setenv("HOME", str(tmp_path))
    # Because load_agent_config uses expanduser() at module scope, we need
    # to patch its resolved path too. Simplest: monkeypatch the loader to
    # use our tmp dir.
    import spaice_agent.config as cfg_module
    original_loader = cfg_module.load_agent_config.__wrapped__  # type: ignore[attr-defined]

    def _loader(agent_id: str):
        from pathlib import Path
        cfg_path = tmp_path / f".spaice-agents/{agent_id}/config.yaml"
        import yaml
        raw = yaml.safe_load(cfg_path.read_text())
        return cfg_module.AgentConfig(**raw)

    return _loader("jarvis")


def test_resolve_credential_uses_file_store_first(
    tmp_path, monkeypatch, jarvis_config,
):
    creds_dir = tmp_path / "cred-store"
    _write_key(creds_dir / "openrouter.key", "file-wins")
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-loses")
    out = resolve_credential(
        jarvis_config, "openrouter_api_key", base_dir=creds_dir,
    )
    assert out == "file-wins"


def test_resolve_credential_falls_back_to_env(
    tmp_path, monkeypatch, jarvis_config,
):
    creds_dir = tmp_path / "empty-cred-dir"
    creds_dir.mkdir()
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-value")
    out = resolve_credential(
        jarvis_config, "openrouter_api_key", base_dir=creds_dir,
    )
    assert out == "env-value"


def test_resolve_credential_raises_when_neither_available(
    tmp_path, monkeypatch, jarvis_config,
):
    creds_dir = tmp_path / "empty-cred-dir"
    creds_dir.mkdir()
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(MissingCredentialError):
        resolve_credential(
            jarvis_config, "openrouter_api_key", base_dir=creds_dir,
        )


def test_resolve_credential_surfaces_unsafe_perms(
    tmp_path, monkeypatch, jarvis_config,
):
    creds_dir = tmp_path / "cred-store"
    _write_key(creds_dir / "openrouter.key", "insecure", mode=0o644)
    # Even if env is set, unsafe file perms must surface
    monkeypatch.setenv("OPENROUTER_API_KEY", "would-win-if-fallback")
    with pytest.raises(CredentialPermissionError):
        resolve_credential(
            jarvis_config, "openrouter_api_key", base_dir=creds_dir,
        )


def test_resolve_credential_missing_field(tmp_path, jarvis_config):
    with pytest.raises(MissingCredentialError):
        resolve_credential(
            jarvis_config, "does_not_exist", base_dir=tmp_path,
        )
