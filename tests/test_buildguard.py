"""Tests for BuildGuard — middleware-enforced coding pipeline.

Coverage:
- BuildGuardDecision dataclass shape + immutability
- _is_coding_write detection: write_file, patch, terminal, execute_code
- _normalize_target_path: abs/rel, tests/ exclusion, non-py rejection
- _deepseek_call_log_has_target: positive, negative, malformed lines, lookback window
- _check_exemption: valid, wrong target, expired style, missing file, malformed YAML
- Full check_pending_write flow: allow/refuse/exempt branches
- Banner emission: stderr + JSONL log file
- Nonce stability (same instance reuses), nonce uniqueness (different instances)
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from spaice_agent.orchestrator import BuildGuard, BuildGuardDecision


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_config(tmp_path, monkeypatch):
    """AgentConfig with an isolated agent_id so logs land in tmp_path."""
    agent_id = f"test-{os.getpid()}"
    cfg = MagicMock()
    cfg.agent_id = agent_id

    # Redirect the build log dir to tmp_path
    spaice_home = tmp_path / ".spaice-agents" / agent_id
    spaice_home.mkdir(parents=True, exist_ok=True)
    (spaice_home / "logs").mkdir(exist_ok=True)

    # Monkey-patch Path.expanduser so the guard's paths resolve into tmp_path
    real_expanduser = Path.expanduser

    def fake_expanduser(self):
        s = str(self)
        if s.startswith("~/.spaice-agents/"):
            return tmp_path / s[2:]
        return real_expanduser(self)

    monkeypatch.setattr(Path, "expanduser", fake_expanduser)
    return cfg, spaice_home


@pytest.fixture
def guard(mock_config):
    cfg, _ = mock_config
    return BuildGuard(cfg)


# ---------------------------------------------------------------------------
# BuildGuardDecision dataclass
# ---------------------------------------------------------------------------


def test_decision_is_frozen():
    d = BuildGuardDecision(allowed=True, reason="x", nonce="abc", target_path=None)
    with pytest.raises((AttributeError, Exception)):
        d.allowed = False


def test_decision_default_target_path_is_none():
    d = BuildGuardDecision(allowed=True, reason="x", nonce="abc")
    assert d.target_path is None


# ---------------------------------------------------------------------------
# _is_coding_write / _normalize_target_path
# ---------------------------------------------------------------------------


def test_write_file_to_spaice_agent_is_coding(guard):
    assert guard._is_coding_write("write_file", {"path": "spaice_agent/foo.py"}) == "spaice_agent/foo.py"


def test_write_file_nested_path(guard):
    assert guard._is_coding_write("write_file", {"path": "spaice_agent/memory/bar.py"}) == "spaice_agent/memory/bar.py"


def test_write_file_absolute_path_normalized(guard):
    abs_path = "/Users/jarvis/Developer/spaice-agent/spaice_agent/foo.py"
    assert guard._is_coding_write("write_file", {"path": abs_path}) == "spaice_agent/foo.py"


def test_write_file_tests_excluded(guard):
    assert guard._is_coding_write("write_file", {"path": "spaice_agent/tests/test_foo.py"}) is None


def test_write_file_outside_spaice_agent(guard):
    assert guard._is_coding_write("write_file", {"path": "/tmp/random.py"}) is None


def test_write_file_non_py_rejected(guard):
    assert guard._is_coding_write("write_file", {"path": "spaice_agent/foo.txt"}) is None


def test_patch_also_detected(guard):
    assert guard._is_coding_write("patch", {"path": "spaice_agent/foo.py"}) == "spaice_agent/foo.py"


def test_patch_accepts_file_path_key(guard):
    assert guard._is_coding_write("patch", {"file_path": "spaice_agent/foo.py"}) == "spaice_agent/foo.py"


def test_terminal_heredoc_redirect_detected(guard):
    cmd = 'cat > spaice_agent/foo.py <<EOF\nprint(1)\nEOF'
    assert guard._is_coding_write("terminal", {"command": cmd}) == "spaice_agent/foo.py"


def test_terminal_append_redirect_detected(guard):
    cmd = 'echo "x" >> spaice_agent/foo.py'
    assert guard._is_coding_write("terminal", {"command": cmd}) == "spaice_agent/foo.py"


def test_terminal_innocent_ls_not_detected(guard):
    assert guard._is_coding_write("terminal", {"command": "ls spaice_agent/"}) is None


def test_execute_code_write_file_call_detected(guard):
    code = 'from hermes_tools import write_file\nwrite_file("spaice_agent/foo.py", "print(1)")'
    assert guard._is_coding_write("execute_code", {"code": code}) == "spaice_agent/foo.py"


def test_execute_code_innocent_script_not_detected(guard):
    code = 'print("hello")'
    assert guard._is_coding_write("execute_code", {"code": code}) is None


def test_unknown_tool_not_a_coding_write(guard):
    assert guard._is_coding_write("browser_click", {"ref": "@e5"}) is None


# ---------------------------------------------------------------------------
# _deepseek_call_log_has_target
# ---------------------------------------------------------------------------


def _write_log_entry(log_dir: Path, entry: dict, date: str = None):
    date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = log_dir / f"openrouter-{date}.jsonl"
    with open(log_file, "a") as f:
        f.write(json.dumps(entry) + "\n")


def test_deepseek_log_positive_by_path(guard, mock_config):
    _, spaice_home = mock_config
    _write_log_entry(spaice_home / "logs", {
        "model": "deepseek/deepseek-v4-pro",
        "messages": [{"role": "user", "content": "Write spaice_agent/foo.py please"}],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    assert guard._deepseek_call_log_has_target("spaice_agent/foo.py") is True


def test_deepseek_log_positive_by_spec_file(guard, mock_config):
    _, spaice_home = mock_config
    _write_log_entry(spaice_home / "logs", {
        "model": "deepseek/deepseek-v4-pro",
        "messages": [{"role": "user", "content": "Spec in reviews/foo-framework.md here"}],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    assert guard._deepseek_call_log_has_target("spaice_agent/foo.py") is True


def test_deepseek_log_wrong_model_rejected(guard, mock_config):
    _, spaice_home = mock_config
    _write_log_entry(spaice_home / "logs", {
        "model": "openai/gpt-5-codex",
        "messages": [{"role": "user", "content": "Write spaice_agent/foo.py"}],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    assert guard._deepseek_call_log_has_target("spaice_agent/foo.py") is False


def test_deepseek_log_wrong_path_rejected(guard, mock_config):
    _, spaice_home = mock_config
    _write_log_entry(spaice_home / "logs", {
        "model": "deepseek/deepseek-v4-pro",
        "messages": [{"role": "user", "content": "Write spaice_agent/bar.py"}],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    assert guard._deepseek_call_log_has_target("spaice_agent/foo.py") is False


def test_deepseek_log_malformed_lines_skipped(guard, mock_config):
    _, spaice_home = mock_config
    log_file = spaice_home / "logs" / f"openrouter-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"
    log_file.write_text(
        "not-json-at-all\n"
        + json.dumps({
            "model": "deepseek/deepseek-v4-pro",
            "messages": [{"role": "user", "content": "spaice_agent/foo.py"}],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        + "\n"
        "another-broken-line\n"
    )
    assert guard._deepseek_call_log_has_target("spaice_agent/foo.py") is True


def test_deepseek_log_empty_when_no_file(guard):
    assert guard._deepseek_call_log_has_target("spaice_agent/foo.py") is False


def test_deepseek_log_prompt_key_variant(guard, mock_config):
    """Some loggers use a flat 'prompt' key instead of messages."""
    _, spaice_home = mock_config
    _write_log_entry(spaice_home / "logs", {
        "model": "deepseek/deepseek-v4-pro",
        "prompt": "Build spaice_agent/foo.py",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    assert guard._deepseek_call_log_has_target("spaice_agent/foo.py") is True


def test_deepseek_log_old_entry_outside_lookback(guard, mock_config):
    """Entries older than lookback_hours should be rejected."""
    _, spaice_home = mock_config
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    _write_log_entry(spaice_home / "logs", {
        "model": "deepseek/deepseek-v4-pro",
        "messages": [{"role": "user", "content": "spaice_agent/foo.py"}],
        "timestamp": old_ts,
    })
    # Lookback of 1 hour — entry is 5 hours old
    assert guard._deepseek_call_log_has_target("spaice_agent/foo.py", lookback_hours=1) is False


# ---------------------------------------------------------------------------
# _check_exemption
# ---------------------------------------------------------------------------


def test_exemption_valid(guard, mock_config):
    _, spaice_home = mock_config
    exemption = spaice_home / "build-exemption.yaml"
    exemption.write_text(yaml.dump({
        "target": "spaice_agent/foo.py",
        "expires_after": "single_commit",
        "granted_by": "jozef",
    }))
    assert guard._check_exemption("spaice_agent/foo.py") is True


def test_exemption_wrong_target(guard, mock_config):
    _, spaice_home = mock_config
    exemption = spaice_home / "build-exemption.yaml"
    exemption.write_text(yaml.dump({
        "target": "spaice_agent/bar.py",
        "expires_after": "single_commit",
    }))
    assert guard._check_exemption("spaice_agent/foo.py") is False


def test_exemption_wrong_expires_style(guard, mock_config):
    _, spaice_home = mock_config
    exemption = spaice_home / "build-exemption.yaml"
    exemption.write_text(yaml.dump({
        "target": "spaice_agent/foo.py",
        "expires_after": "forever",
    }))
    assert guard._check_exemption("spaice_agent/foo.py") is False


def test_exemption_missing_file(guard):
    assert guard._check_exemption("spaice_agent/foo.py") is False


def test_exemption_malformed_yaml(guard, mock_config):
    _, spaice_home = mock_config
    exemption = spaice_home / "build-exemption.yaml"
    exemption.write_text("not: valid: yaml: {{{")
    assert guard._check_exemption("spaice_agent/foo.py") is False


# ---------------------------------------------------------------------------
# check_pending_write full flow
# ---------------------------------------------------------------------------


def test_check_not_a_coding_write_allowed(guard):
    d = guard.check_pending_write("browser_click", {"ref": "@e1"})
    assert d.allowed is True
    assert d.reason == "not-a-coding-write"
    assert d.target_path is None


def test_check_refused_when_no_deepseek_call(guard):
    d = guard.check_pending_write("write_file", {"path": "spaice_agent/foo.py", "content": "x"})
    assert d.allowed is False
    assert d.reason == "no-deepseek-call"
    assert d.target_path == "spaice_agent/foo.py"


def test_check_allowed_when_deepseek_call_present(guard, mock_config):
    _, spaice_home = mock_config
    _write_log_entry(spaice_home / "logs", {
        "model": "deepseek/deepseek-v4-pro",
        "messages": [{"role": "user", "content": "Write spaice_agent/foo.py"}],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    d = guard.check_pending_write("write_file", {"path": "spaice_agent/foo.py", "content": "x"})
    assert d.allowed is True
    assert d.reason == "deepseek-call-found"


def test_check_allowed_when_exemption_present(guard, mock_config):
    _, spaice_home = mock_config
    (spaice_home / "build-exemption.yaml").write_text(yaml.dump({
        "target": "spaice_agent/foo.py",
        "expires_after": "single_commit",
    }))
    d = guard.check_pending_write("write_file", {"path": "spaice_agent/foo.py", "content": "x"})
    assert d.allowed is True
    assert d.reason == "exemption-active"


def test_exemption_takes_priority_over_deepseek_check(guard, mock_config):
    """Even if no DeepSeek call exists, exemption allows."""
    _, spaice_home = mock_config
    (spaice_home / "build-exemption.yaml").write_text(yaml.dump({
        "target": "spaice_agent/foo.py",
        "expires_after": "single_commit",
    }))
    d = guard.check_pending_write("write_file", {"path": "spaice_agent/foo.py", "content": "x"})
    assert d.allowed is True
    assert d.reason == "exemption-active"


def test_check_tests_file_allowed_without_deepseek(guard):
    """Writing test files does NOT require DeepSeek."""
    d = guard.check_pending_write("write_file", {"path": "spaice_agent/tests/test_foo.py"})
    assert d.allowed is True
    assert d.reason == "not-a-coding-write"


# ---------------------------------------------------------------------------
# Banner emission
# ---------------------------------------------------------------------------


def test_banner_written_to_stderr(guard, capsys):
    guard.check_pending_write("write_file", {"path": "spaice_agent/foo.py"})
    captured = capsys.readouterr()
    assert "BUILD-GUARD" in captured.err
    assert "nonce=" in captured.err
    assert "REFUSE" in captured.err
    assert "spaice_agent/foo.py" in captured.err


def test_banner_jsonl_appended(guard, mock_config):
    _, spaice_home = mock_config
    guard.check_pending_write("write_file", {"path": "spaice_agent/foo.py"})
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = spaice_home / "logs" / f"builds-{today}.log"
    assert log_file.exists()
    line = log_file.read_text().strip().split("\n")[-1]
    entry = json.loads(line)
    assert entry["decision"] == "REFUSE"
    assert entry["target"] == "spaice_agent/foo.py"
    assert entry["reason"] == "no-deepseek-call"
    assert entry["nonce"] == guard._nonce


def test_allow_decision_also_logged(guard, mock_config, capsys):
    """ALLOW decisions must be logged too (audit requirement)."""
    _, spaice_home = mock_config
    guard.check_pending_write("browser_click", {"ref": "@e1"})
    captured = capsys.readouterr()
    assert "ALLOW" in captured.err


# ---------------------------------------------------------------------------
# Nonce behaviour
# ---------------------------------------------------------------------------


def test_nonce_stable_across_calls(guard):
    d1 = guard.check_pending_write("write_file", {"path": "spaice_agent/a.py"})
    d2 = guard.check_pending_write("write_file", {"path": "spaice_agent/b.py"})
    assert d1.nonce == d2.nonce


def test_nonce_unique_per_instance(mock_config):
    cfg, _ = mock_config
    g1 = BuildGuard(cfg)
    g2 = BuildGuard(cfg)
    assert g1._nonce != g2.nonce if hasattr(g2, "nonce") else g1._nonce != g2._nonce


def test_nonce_is_8_hex_chars(guard):
    assert len(guard._nonce) == 8
    int(guard._nonce, 16)  # must parse as hex


# ---------------------------------------------------------------------------
# Known-bypass documentation (regression guard for v2 reminder)
# ---------------------------------------------------------------------------


def test_known_bypass_documented_in_docstring():
    """v1 limitation must remain documented so v2 upgrade isn't forgotten."""
    assert "Known bypass" in BuildGuard.__doc__
    assert "v0.3.0" in BuildGuard.__doc__
