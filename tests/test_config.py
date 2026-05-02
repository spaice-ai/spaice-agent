"""Tests for spaice_agent.config."""
from __future__ import annotations

import os
import re
from pathlib import Path
from textwrap import dedent

import pytest

from spaice_agent.config import (
    AgentConfig,
    ConfigError,
    ConfigNotFoundError,
    MissingCredentialError,
    load_agent_config,
)


VALID_YAML = dedent("""
    agent_id: test-agent
    memory_root: ~/test-memory
    credentials:
      openrouter_api_key_env: OPENROUTER_API_KEY
      exa_api_key_env: EXA_API_KEY
      brave_api_key_env: BRAVE_API_KEY

    hook:
      total_timeout_s: 11.0

    memory:
      enabled: true
      entity_cache_path: ~/.spaice-agents/test-agent/cache/entities.json
      stage_timeout_s: 1.5
      recall_max_hits_per_entity: 3
      recall_snippet_chars: 200
      live_capture_dir: _inbox/_live

    search:
      enabled: true
      daily_fire_cap: 30
      stage_timeout_s: 3.0
      providers:
        - name: exa
          endpoint: https://api.exa.ai/search
          max_results: 5
          per_request_timeout_s: 2.5
        - name: brave
          endpoint: https://api.search.brave.com/res/v1/web/search
          max_results: 5
          per_request_timeout_s: 2.5
      merge:
        method: rrf
        k: 60
      triggers:
        phrase_anchors:
          - '^\\s*(look up|search for)'
        url_at_end: true

    consensus:
      enabled: true
      daily_fire_cap: 10
      pipeline_timeout_s: 8.0
      pipeline:
        - stage: proposer
          model: anthropic/claude-opus-4.7
          stage_timeout_s: 3.0
          max_tokens: 1500
          truncate_output_chars: 6000
          system: "proposer prompt"
        - stage: critic
          model: openai/gpt-5-codex
          stage_timeout_s: 3.0
          max_tokens: 1200
          truncate_output_chars: 2000
          system: "critic prompt"
        - stage: reviewer
          model: deepseek/deepseek-v4-pro
          stage_timeout_s: 2.0
          max_tokens: 2000
          truncate_output_chars: 8000
          system: "reviewer prompt"
      triggers:
        words: [plan, decide, research]
        phrases: [ask codex, second opinion]

    scrubber:
      enabled: true
      leak_patterns:
        - '\\b(chair\\s+[123])\\b'
""")


@pytest.fixture(autouse=True)
def _clear_lru_cache():
    """lru_cache survives between tests; clear it to avoid cross-contamination."""
    load_agent_config.cache_clear()
    yield
    load_agent_config.cache_clear()


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Point ~/ at a temp dir."""
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def valid_config_file(fake_home):
    """Write a valid config file at the expected path."""
    p = fake_home / ".spaice-agents" / "test-agent" / "config.yaml"
    p.parent.mkdir(parents=True)
    p.write_text(VALID_YAML)
    return p


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_load_valid_config(valid_config_file):
    cfg = load_agent_config("test-agent")
    assert cfg.agent_id == "test-agent"
    assert cfg.memory_root == Path.home() / "test-memory"
    assert cfg.hook.total_timeout_s == 11.0
    assert cfg.memory.enabled is True
    assert len(cfg.search.providers) == 2
    assert cfg.search.providers[0].name == "exa"
    assert cfg.search.merge.method == "rrf"
    assert cfg.consensus.daily_fire_cap == 10
    assert len(cfg.consensus.pipeline) == 3
    assert cfg.consensus.pipeline[0].stage == "proposer"
    assert cfg.consensus.pipeline[1].stage == "critic"
    assert cfg.consensus.pipeline[2].stage == "reviewer"
    assert cfg.consensus.pipeline[2].model == "deepseek/deepseek-v4-pro"
    assert cfg.scrubber.enabled is True


def test_path_expansion_applies(valid_config_file):
    cfg = load_agent_config("test-agent")
    # Tilde must be expanded
    assert "~" not in str(cfg.memory_root)
    assert "~" not in str(cfg.memory.entity_cache_path)


def test_immutability(valid_config_file):
    cfg = load_agent_config("test-agent")
    with pytest.raises(Exception):  # pydantic raises ValidationError on frozen
        cfg.agent_id = "changed"  # type: ignore[misc]


def test_lru_caches_per_agent(valid_config_file):
    a = load_agent_config("test-agent")
    b = load_agent_config("test-agent")
    assert a is b  # same object from cache


# ---------------------------------------------------------------------------
# Credential indirection
# ---------------------------------------------------------------------------


def test_get_credential_reads_env(valid_config_file, monkeypatch):
    cfg = load_agent_config("test-agent")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    assert cfg.get_credential("openrouter_api_key") == "sk-test-123"


def test_get_credential_missing_raises(valid_config_file, monkeypatch):
    cfg = load_agent_config("test-agent")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(MissingCredentialError):
        cfg.get_credential("openrouter_api_key")


def test_get_credential_empty_raises(valid_config_file, monkeypatch):
    cfg = load_agent_config("test-agent")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    with pytest.raises(MissingCredentialError):
        cfg.get_credential("openrouter_api_key")


def test_get_credential_unknown_field_raises(valid_config_file):
    cfg = load_agent_config("test-agent")
    with pytest.raises(MissingCredentialError):
        cfg.get_credential("bogus")


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_missing_file_raises(fake_home):
    with pytest.raises(ConfigNotFoundError) as excinfo:
        load_agent_config("nonexistent")
    assert "nonexistent" in str(excinfo.value)


def test_invalid_yaml_raises(fake_home):
    p = fake_home / ".spaice-agents" / "broken" / "config.yaml"
    p.parent.mkdir(parents=True)
    p.write_text("this: is: not: valid: yaml: ][")
    with pytest.raises(ConfigError):
        load_agent_config("broken")


def test_yaml_not_mapping_raises(fake_home):
    p = fake_home / ".spaice-agents" / "list-only" / "config.yaml"
    p.parent.mkdir(parents=True)
    p.write_text("- a\n- b\n")
    with pytest.raises(ConfigError):
        load_agent_config("list-only")


def test_missing_required_field(fake_home):
    p = fake_home / ".spaice-agents" / "incomplete" / "config.yaml"
    p.parent.mkdir(parents=True)
    # Missing consensus block entirely
    p.write_text(VALID_YAML.replace("consensus:", "xxxconsensus:"))
    with pytest.raises(ConfigError):
        load_agent_config("incomplete")


def test_extra_field_rejected(fake_home):
    p = fake_home / ".spaice-agents" / "extra" / "config.yaml"
    p.parent.mkdir(parents=True)
    p.write_text(VALID_YAML + "\nunexpected_field: 42\n")
    with pytest.raises(ConfigError):
        load_agent_config("extra")


def test_agent_id_mismatch_raises(fake_home):
    """File's agent_id must match requested agent_id."""
    p = fake_home / ".spaice-agents" / "mismatch" / "config.yaml"
    p.parent.mkdir(parents=True)
    p.write_text(VALID_YAML)  # file says "test-agent"
    with pytest.raises(ConfigError) as excinfo:
        load_agent_config("mismatch")
    assert "doesn't match" in str(excinfo.value)


def test_negative_timeout_rejected(fake_home):
    p = fake_home / ".spaice-agents" / "bad-timeout" / "config.yaml"
    p.parent.mkdir(parents=True)
    p.write_text(VALID_YAML.replace("total_timeout_s: 11.0", "total_timeout_s: -1.0")
                 .replace("agent_id: test-agent", "agent_id: bad-timeout"))
    with pytest.raises(ConfigError):
        load_agent_config("bad-timeout")


def test_wrong_pipeline_stage_order_rejected(fake_home):
    p = fake_home / ".spaice-agents" / "bad-order" / "config.yaml"
    p.parent.mkdir(parents=True)
    bad = VALID_YAML.replace("agent_id: test-agent", "agent_id: bad-order")
    # Swap critic and proposer stage names → order [critic, proposer, reviewer]
    bad = bad.replace("- stage: proposer", "- stage: TEMP1", 1)
    bad = bad.replace("- stage: critic", "- stage: proposer", 1)
    bad = bad.replace("- stage: TEMP1", "- stage: critic", 1)
    p.write_text(bad)
    with pytest.raises(ConfigError) as excinfo:
        load_agent_config("bad-order")
    assert "order" in str(excinfo.value).lower()


def test_pipeline_wrong_stage_count_rejected(fake_home):
    p = fake_home / ".spaice-agents" / "bad-count" / "config.yaml"
    p.parent.mkdir(parents=True)
    bad = VALID_YAML.replace("agent_id: test-agent", "agent_id: bad-count")
    # Remove the reviewer stage (cut from "- stage: reviewer" through end of that block)
    idx = bad.find("- stage: reviewer")
    end = bad.find("triggers:", idx)
    bad = bad[:idx] + "      " + bad[end:]
    p.write_text(bad)
    with pytest.raises(ConfigError):
        load_agent_config("bad-count")


def test_unknown_provider_rejected(fake_home):
    p = fake_home / ".spaice-agents" / "bad-provider" / "config.yaml"
    p.parent.mkdir(parents=True)
    bad = VALID_YAML.replace("agent_id: test-agent", "agent_id: bad-provider") \
                    .replace("name: exa", "name: google")
    p.write_text(bad)
    with pytest.raises(ConfigError):
        load_agent_config("bad-provider")


def test_invalid_agent_id_pattern(fake_home):
    p = fake_home / ".spaice-agents" / "BadCaps" / "config.yaml"
    p.parent.mkdir(parents=True)
    p.write_text(VALID_YAML.replace("agent_id: test-agent", "agent_id: BadCaps"))
    with pytest.raises(ConfigError):
        load_agent_config("BadCaps")


# ---------------------------------------------------------------------------
# Codex 5.3 retroactive review — high-severity fixes
# ---------------------------------------------------------------------------


def test_path_traversal_agent_id_rejected_before_disk_read(fake_home, tmp_path):
    """Codex 2026-05-03: 'agent_id="../aurora"' must be rejected BEFORE
    the filesystem is touched. Path traversal guards Jarvis/Aurora isolation.
    """
    # Seed a file the traversal WOULD reach
    neighbour = fake_home / "aurora" / "config.yaml"
    neighbour.parent.mkdir(parents=True)
    neighbour.write_text(VALID_YAML)  # irrelevant — we must reject before reaching it

    # Caller passes a traversal slug
    with pytest.raises(ConfigError) as excinfo:
        load_agent_config("../aurora")
    assert "invalid agent_id" in str(excinfo.value).lower()


def test_agent_id_slash_rejected(fake_home):
    with pytest.raises(ConfigError):
        load_agent_config("foo/bar")


def test_agent_id_absolute_path_rejected(fake_home):
    with pytest.raises(ConfigError):
        load_agent_config("/etc/passwd")


def test_agent_id_empty_rejected(fake_home):
    with pytest.raises(ConfigError):
        load_agent_config("")


def test_agent_id_none_rejected(fake_home):
    with pytest.raises(ConfigError):
        load_agent_config(None)  # type: ignore[arg-type]


def test_search_enabled_with_empty_providers_rejected(fake_home):
    """Codex 2026-05-03: contract violation — search.enabled=true
    requires at least one provider, else the pipeline crashes at runtime.
    """
    import yaml as _yaml
    p = fake_home / ".spaice-agents" / "noprov" / "config.yaml"
    p.parent.mkdir(parents=True)
    data = _yaml.safe_load(VALID_YAML)
    data["agent_id"] = "noprov"
    data["search"]["providers"] = []
    p.write_text(_yaml.safe_dump(data))
    with pytest.raises(ConfigError) as excinfo:
        load_agent_config("noprov")
    assert "provider" in str(excinfo.value).lower()


def test_unreadable_config_raises_config_error(fake_home, monkeypatch):
    """Codex 2026-05-03: OSError on file read must become ConfigError,
    not leak as raw exception."""
    p = fake_home / ".spaice-agents" / "unreadable" / "config.yaml"
    p.parent.mkdir(parents=True)
    p.write_text(VALID_YAML)

    original_open = Path.open
    def flaky_open(self, *args, **kwargs):
        if self == p:
            raise PermissionError("simulated chmod 000")
        return original_open(self, *args, **kwargs)
    monkeypatch.setattr(Path, "open", flaky_open)

    with pytest.raises(ConfigError) as excinfo:
        load_agent_config("unreadable")
    assert "could not read" in str(excinfo.value).lower()


def test_non_utf8_config_raises_config_error(fake_home):
    """Codex 2026-05-03: UnicodeDecodeError on Windows/non-UTF locale
    must become ConfigError, not leak."""
    p = fake_home / ".spaice-agents" / "notutf8" / "config.yaml"
    p.parent.mkdir(parents=True)
    # Write Latin-1 bytes that contain a non-UTF-8 sequence
    p.write_bytes(b"agent_id: notutf8\nname: caf\xe9\n")
    with pytest.raises(ConfigError) as excinfo:
        load_agent_config("notutf8")
    msg = str(excinfo.value).lower()
    assert "utf-8" in msg or "unicode" in msg or "invalid" in msg
