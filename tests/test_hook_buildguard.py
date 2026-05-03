"""Tests for BuildGuard wiring in hook.py pre_tool_call branch."""
from __future__ import annotations

import os
import pytest
import yaml
from pathlib import Path
from unittest.mock import MagicMock, patch

pytestmark = pytest.mark.asyncio


@pytest.fixture
def mock_config_load(tmp_path, monkeypatch):
    """Mock load_agent_config and Path.expanduser for an isolated BuildGuard."""
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

    # Clear the module-level guard cache between tests
    import spaice_agent.hook as hook_mod
    hook_mod._GUARDS.clear()

    return agent_id, cfg, spaice_home


async def test_pre_tool_call_write_to_spaice_agent_without_deepseek_refuses(mock_config_load):
    agent_id, cfg, _ = mock_config_load
    from spaice_agent.hook import make_hook

    with patch("spaice_agent.config.load_agent_config", return_value=cfg):
        handle, _ = make_hook(agent_id)
        result = await handle("pre_tool_call", {
            "tool_name": "write_file",
            "tool_args": {"path": "spaice_agent/foo.py", "content": "x"},
        })

    assert result is not None
    assert "BUILD-GUARD" in result["reply"]
    assert "spaice_agent/foo.py" in result["reply"]
    assert "DeepSeek" in result["reply"]


async def test_pre_tool_call_test_file_passes_through(mock_config_load):
    agent_id, cfg, _ = mock_config_load
    from spaice_agent.hook import make_hook

    with patch("spaice_agent.config.load_agent_config", return_value=cfg):
        handle, _ = make_hook(agent_id)
        result = await handle("pre_tool_call", {
            "tool_name": "write_file",
            "tool_args": {"path": "spaice_agent/tests/test_foo.py"},
        })

    assert result is None  # test files pass through


async def test_pre_tool_call_non_coding_tool_passes_through(mock_config_load):
    agent_id, cfg, _ = mock_config_load
    from spaice_agent.hook import make_hook

    with patch("spaice_agent.config.load_agent_config", return_value=cfg):
        handle, _ = make_hook(agent_id)
        result = await handle("pre_tool_call", {
            "tool_name": "browser_click",
            "tool_args": {"ref": "@e5"},
        })

    assert result is None


async def test_pre_tool_call_with_exemption_passes_through(mock_config_load):
    agent_id, cfg, spaice_home = mock_config_load
    exemption = spaice_home / "build-exemption.yaml"
    exemption.write_text(yaml.dump({
        "target": "spaice_agent/foo.py",
        "expires_after": "single_commit",
    }))

    from spaice_agent.hook import make_hook
    with patch("spaice_agent.config.load_agent_config", return_value=cfg):
        handle, _ = make_hook(agent_id)
        result = await handle("pre_tool_call", {
            "tool_name": "write_file",
            "tool_args": {"path": "spaice_agent/foo.py"},
        })

    assert result is None  # exemption allows it


async def test_pre_tool_call_handler_never_raises(mock_config_load):
    """Even if context is totally broken, handler must return None, not raise."""
    agent_id, cfg, _ = mock_config_load
    from spaice_agent.hook import make_hook

    with patch("spaice_agent.config.load_agent_config", return_value=cfg):
        handle, _ = make_hook(agent_id)

        # Missing tool_name
        assert await handle("pre_tool_call", {}) is None
        # Non-string tool_name
        assert await handle("pre_tool_call", {"tool_name": 123}) is None
        # None context
        assert await handle("pre_tool_call", None) is None  # type: ignore


async def test_pre_turn_still_works(mock_config_load):
    """Adding pre_tool_call branch must not break existing pre_turn flow."""
    agent_id, cfg, _ = mock_config_load
    from spaice_agent.hook import make_hook

    # The existing pre_turn branch passes through when no message
    with patch("spaice_agent.config.load_agent_config", return_value=cfg):
        handle, _ = make_hook(agent_id)
        # Empty message → None (matches existing behaviour)
        result = await handle("pre_turn", {"message": ""})
        assert result is None


async def test_guard_cached_across_calls(mock_config_load):
    """Same agent_id must reuse the same BuildGuard instance (stable nonce)."""
    agent_id, cfg, _ = mock_config_load
    import spaice_agent.hook as hook_mod
    hook_mod._GUARDS.clear()

    from spaice_agent.hook import make_hook

    with patch("spaice_agent.config.load_agent_config", return_value=cfg):
        handle, _ = make_hook(agent_id)
        await handle("pre_tool_call", {
            "tool_name": "write_file",
            "tool_args": {"path": "spaice_agent/a.py"},
        })
        first_guard = hook_mod._GUARDS.get(agent_id)
        first_nonce = first_guard._nonce

        await handle("pre_tool_call", {
            "tool_name": "write_file",
            "tool_args": {"path": "spaice_agent/b.py"},
        })
        second_guard = hook_mod._GUARDS.get(agent_id)
        second_nonce = second_guard._nonce

    assert first_guard is second_guard
    assert first_nonce == second_nonce
