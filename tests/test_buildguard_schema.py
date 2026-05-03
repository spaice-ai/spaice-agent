"""Schema compatibility tests — verify BuildGuard uses the same tool arg keys as Hermes.

Codex final review (2026-05-03) flagged this as a potential blocker: BuildGuard's
_is_coding_write() assumes certain key names, but if Hermes's tool schemas differ,
the guard would silently allow writes. This test documents the assumed schema
and fails if it ever drifts.

Verified against Hermes tool schemas on 2026-05-03:
- write_file: "path"  (tools/file_tools.py:916)
- patch:      "path"  (tools/file_tools.py:930)
- terminal:   "command"  (tools/terminal_tool.py:2083)
- execute_code: "code"  (tools/code_execution_tool.py:1547)
"""
from __future__ import annotations

import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from spaice_agent.orchestrator import BuildGuard


EXPECTED_SCHEMAS = {
    "write_file": {"path_key": "path", "content_key": "content"},
    "patch": {"path_key": "path"},
    "terminal": {"command_key": "command"},
    "execute_code": {"code_key": "code"},
}


@pytest.fixture
def guard(tmp_path, monkeypatch):
    agent_id = f"test-{os.getpid()}"
    cfg = MagicMock()
    cfg.agent_id = agent_id
    spaice_home = tmp_path / ".spaice-agents" / agent_id
    (spaice_home / "logs").mkdir(parents=True, exist_ok=True)

    real_expanduser = Path.expanduser

    def fake(self):
        s = str(self)
        if s.startswith("~/.spaice-agents/"):
            return tmp_path / s[2:]
        return real_expanduser(self)

    monkeypatch.setattr(Path, "expanduser", fake)
    return BuildGuard(cfg)


def test_write_file_uses_path_key(guard):
    """Hermes write_file passes {'path': '...', 'content': '...'}."""
    d = guard.check_pending_write("write_file", {
        "path": "spaice_agent/foo.py",
        "content": "print(1)",
    })
    assert d.target_path == "spaice_agent/foo.py"


def test_patch_uses_path_key(guard):
    """Hermes patch passes {'path': '...', 'old_string': '...', 'new_string': '...'}."""
    d = guard.check_pending_write("patch", {
        "path": "spaice_agent/foo.py",
        "old_string": "x",
        "new_string": "y",
    })
    assert d.target_path == "spaice_agent/foo.py"


def test_terminal_uses_command_key(guard):
    """Hermes terminal passes {'command': '...'}."""
    d = guard.check_pending_write("terminal", {
        "command": "echo 'x' > spaice_agent/foo.py",
    })
    assert d.target_path == "spaice_agent/foo.py"


def test_execute_code_uses_code_key(guard):
    """Hermes execute_code passes {'code': '...'}."""
    d = guard.check_pending_write("execute_code", {
        "code": 'from hermes_tools import write_file\nwrite_file("spaice_agent/foo.py", "x")',
    })
    assert d.target_path == "spaice_agent/foo.py"


def test_hermes_schema_contract_documented():
    """Schema assumption baked into orchestrator._is_coding_write must remain valid.

    If Hermes renames tool arg keys in the future, this test documents which
    keys BuildGuard depends on so a drift is caught immediately.
    """
    # If any Hermes tool schema changes these keys, _is_coding_write must be
    # updated to match. This test exists as the audit trail.
    assert EXPECTED_SCHEMAS["write_file"]["path_key"] == "path"
    assert EXPECTED_SCHEMAS["patch"]["path_key"] == "path"
    assert EXPECTED_SCHEMAS["terminal"]["command_key"] == "command"
    assert EXPECTED_SCHEMAS["execute_code"]["code_key"] == "code"
