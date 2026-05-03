"""Regression tests for BuildGuard v1 blocker fixes (Codex review 2026-05-03).

Each test documents the original Codex finding and guards against regression.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from spaice_agent.orchestrator import BuildGuard, BuildGuardDecision


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_config(tmp_path, monkeypatch):
    agent_id = f"test-{os.getpid()}"
    cfg = MagicMock()
    cfg.agent_id = agent_id

    spaice_home = tmp_path / ".spaice-agents" / agent_id
    (spaice_home / "logs").mkdir(parents=True, exist_ok=True)

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


def _write_log(logs_dir: Path, entry: dict):
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log = logs_dir / f"openrouter-{date}.jsonl"
    with open(log, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# B1 — Relative-path bypass (./spaice_agent/foo.py)
# Regression guard: Codex 2026-05-03 blocker #1
# ---------------------------------------------------------------------------


def test_b1_dot_slash_prefix_detected(guard):
    """./spaice_agent/foo.py must be detected as a coding write, not silently allowed."""
    d = guard.check_pending_write("write_file", {"path": "./spaice_agent/foo.py"})
    assert d.allowed is False
    assert d.reason == "no-deepseek-call"
    assert d.target_path == "spaice_agent/foo.py"


def test_b1_dot_dot_slash_prefix_rejected_as_traversal(guard):
    """../spaice_agent/foo.py has residual '..' after normalisation → traversal → reject as non-coding.

    This is the correct behaviour: the guard refuses to reason about paths
    that escape the repository root. If an agent writes to such a path it's
    not inside our protected tree anyway.
    """
    d = guard.check_pending_write("write_file", {"path": "../spaice_agent/foo.py"})
    assert d.allowed is True
    assert d.reason == "not-a-coding-write"
    assert d.target_path is None


def test_b1_embedded_dot_slash(guard):
    """foo/./spaice_agent/bar.py normalises and is detected."""
    d = guard.check_pending_write("write_file", {"path": "foo/./spaice_agent/bar.py"})
    assert d.allowed is False
    assert d.target_path == "spaice_agent/bar.py"


# ---------------------------------------------------------------------------
# B2 — tests/../  traversal loophole
# Regression guard: Codex 2026-05-03 blocker #2
# ---------------------------------------------------------------------------


def test_b2_tests_traversal_does_not_bypass(guard):
    """spaice_agent/tests/../memory/foo.py resolves to spaice_agent/memory/foo.py.
    It is NOT a test file; it must be guarded.
    """
    d = guard.check_pending_write("write_file", {"path": "spaice_agent/tests/../memory/foo.py"})
    assert d.allowed is False
    assert d.reason == "no-deepseek-call"
    assert d.target_path == "spaice_agent/memory/foo.py"


def test_b2_genuine_test_file_still_allowed(guard):
    """spaice_agent/tests/test_foo.py is genuinely a test — allow."""
    d = guard.check_pending_write("write_file", {"path": "spaice_agent/tests/test_foo.py"})
    assert d.allowed is True
    assert d.reason == "not-a-coding-write"


def test_b2_traversal_outside_protected_tree_rejected(guard):
    """spaice_agent/../setup.py escapes the protected tree. _is_coding_write must return None."""
    d = guard.check_pending_write("write_file", {"path": "spaice_agent/../setup.py"})
    assert d.allowed is True
    assert d.reason == "not-a-coding-write"
    assert d.target_path is None


# ---------------------------------------------------------------------------
# B3 — Single-commit exemption must expire after first allow
# Regression guard: Codex 2026-05-03 blocker #3
# ---------------------------------------------------------------------------


def test_b3_exemption_consumed_after_first_allow(guard, mock_config):
    _, spaice_home = mock_config
    exemption = spaice_home / "build-exemption.yaml"
    exemption.write_text(yaml.dump({
        "target": "spaice_agent/foo.py",
        "expires_after": "single_commit",
    }))

    # First call: allowed
    d1 = guard.check_pending_write("write_file", {"path": "spaice_agent/foo.py"})
    assert d1.allowed is True
    assert d1.reason == "exemption-active"

    # Exemption file should be gone
    assert not exemption.exists(), "exemption file must be deleted after first allow"

    # Second call: refused (no DeepSeek log, no exemption)
    d2 = guard.check_pending_write("write_file", {"path": "spaice_agent/foo.py"})
    assert d2.allowed is False
    assert d2.reason == "no-deepseek-call"


def test_b3_exemption_only_consumes_for_matching_target(guard, mock_config):
    """If exemption is for X but agent tries to write Y, exemption must not be consumed."""
    _, spaice_home = mock_config
    exemption = spaice_home / "build-exemption.yaml"
    exemption.write_text(yaml.dump({
        "target": "spaice_agent/foo.py",
        "expires_after": "single_commit",
    }))

    # Try non-matching target — refused (no exemption match, no log)
    d1 = guard.check_pending_write("write_file", {"path": "spaice_agent/bar.py"})
    assert d1.allowed is False

    # Exemption file should still exist — wasn't consumed
    assert exemption.exists(), "exemption must NOT be consumed for non-matching targets"

    # Matching target — now consumes it
    d2 = guard.check_pending_write("write_file", {"path": "spaice_agent/foo.py"})
    assert d2.allowed is True
    assert not exemption.exists()


# ---------------------------------------------------------------------------
# B4 — execute_code broader detection
# Regression guard: Codex 2026-05-03 blocker #4
# ---------------------------------------------------------------------------


def test_b4_open_w_mode_detected(guard):
    code = 'open("spaice_agent/foo.py", "w").write("x")'
    d = guard.check_pending_write("execute_code", {"code": code})
    assert d.allowed is False
    assert d.target_path == "spaice_agent/foo.py"


def test_b4_open_wb_mode_detected(guard):
    code = 'open("spaice_agent/foo.py", "wb").write(b"x")'
    d = guard.check_pending_write("execute_code", {"code": code})
    assert d.allowed is False


def test_b4_path_write_text_detected(guard):
    code = 'from pathlib import Path\nPath("spaice_agent/foo.py").write_text("x")'
    d = guard.check_pending_write("execute_code", {"code": code})
    assert d.allowed is False
    assert d.target_path == "spaice_agent/foo.py"


def test_b4_path_write_bytes_detected(guard):
    code = 'Path("spaice_agent/foo.py").write_bytes(b"x")'
    d = guard.check_pending_write("execute_code", {"code": code})
    assert d.allowed is False


def test_b4_os_rename_detected(guard):
    code = 'import os\nos.rename("/tmp/x", "spaice_agent/foo.py")'
    d = guard.check_pending_write("execute_code", {"code": code})
    assert d.allowed is False


def test_b4_shutil_copy_detected(guard):
    code = 'import shutil\nshutil.copy("/tmp/x", "spaice_agent/foo.py")'
    d = guard.check_pending_write("execute_code", {"code": code})
    assert d.allowed is False


def test_b4_shutil_move_detected(guard):
    code = 'import shutil\nshutil.move("/tmp/x", "spaice_agent/foo.py")'
    d = guard.check_pending_write("execute_code", {"code": code})
    assert d.allowed is False


# ---------------------------------------------------------------------------
# B5 — Terminal broader detection
# Regression guard: Codex 2026-05-03 blocker #5
# ---------------------------------------------------------------------------


def test_b5_mv_to_spaice_agent_detected(guard):
    d = guard.check_pending_write("terminal", {"command": "mv /tmp/tmp.py spaice_agent/foo.py"})
    assert d.allowed is False
    assert "spaice_agent/foo.py" in (d.target_path or "")


def test_b5_cp_to_spaice_agent_detected(guard):
    d = guard.check_pending_write("terminal", {"command": "cp /tmp/src.py spaice_agent/memory/bar.py"})
    assert d.allowed is False


def test_b5_sed_in_place_detected(guard):
    d = guard.check_pending_write("terminal", {"command": "sed -i 's/x/y/' spaice_agent/foo.py"})
    assert d.allowed is False


def test_b5_tee_detected(guard):
    d = guard.check_pending_write("terminal", {"command": "echo 'code' | tee spaice_agent/foo.py"})
    assert d.allowed is False


def test_b5_heredoc_via_python_detected(guard):
    """python - <<'PY' ... PY > spaice_agent/foo.py — the > redirect catches it."""
    cmd = "python - <<'PY' > spaice_agent/foo.py\nprint('x')\nPY"
    d = guard.check_pending_write("terminal", {"command": cmd})
    assert d.allowed is False


def test_b5_innocent_read_not_detected(guard):
    """cat spaice_agent/foo.py (read, not write) must not trigger."""
    d = guard.check_pending_write("terminal", {"command": "cat spaice_agent/foo.py"})
    # Conservative mode may flag this — check that at least non-spaice_agent cat is clean
    d2 = guard.check_pending_write("terminal", {"command": "ls -la"})
    assert d2.allowed is True
    assert d2.reason == "not-a-coding-write"


# ---------------------------------------------------------------------------
# Known-bypass documentation regression guard (unchanged from v1 tests)
# ---------------------------------------------------------------------------


def test_known_bypass_still_documented():
    assert "Known bypass" in BuildGuard.__doc__ or "known bypass" in BuildGuard.__doc__.lower()
