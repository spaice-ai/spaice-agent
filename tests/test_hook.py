"""Tests for spaice_agent.hook — the product entry point."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from spaice_agent.hook import _safe_to_markdown, make_hook, _shape_response


class FakeRecall:
    def to_markdown(self) -> str:
        return "- recall line 1\n- recall line 2"


class FakeRecallEmpty:
    def to_markdown(self) -> str:
        return ""


class FakeRecallRaises:
    def to_markdown(self) -> str:
        raise RuntimeError("boom")


class FakeSearch:
    def to_markdown(self) -> str:
        return "1. [title](https://example.com)"


class FakeResult:
    reply = None
    recall = None
    search = None
    consensus_advisory = None


def test_safe_to_markdown_none():
    assert _safe_to_markdown(None) is None


def test_safe_to_markdown_no_method():
    class NoMethod:
        pass
    assert _safe_to_markdown(NoMethod()) is None


def test_safe_to_markdown_empty_string():
    assert _safe_to_markdown(FakeRecallEmpty()) is None


def test_safe_to_markdown_raises():
    # Should return None, not propagate
    assert _safe_to_markdown(FakeRecallRaises()) is None


def test_safe_to_markdown_valid():
    md = _safe_to_markdown(FakeRecall())
    assert "recall line 1" in md


def test_shape_response_reply_wins():
    r = FakeResult()
    r.reply = "# Search handback"
    r.recall = FakeRecall()  # ignored when reply is set
    assert _shape_response(r) == {"reply": "# Search handback"}


def test_shape_response_context_blocks():
    r = FakeResult()
    r.recall = FakeRecall()
    r.search = FakeSearch()
    r.consensus_advisory = "advisory text"
    out = _shape_response(r)
    assert out is not None
    assert "## Memory recall" in out["context"]
    assert "## Research" in out["context"]
    assert "## Consensus advisory" in out["context"]


def test_shape_response_empty_returns_none():
    r = FakeResult()
    assert _shape_response(r) is None


def test_shape_response_whitespace_reply_skipped():
    r = FakeResult()
    r.reply = "   \n  "
    r.recall = FakeRecall()
    # Whitespace-only reply is not a real reply, but recall block is valid
    out = _shape_response(r)
    assert out is not None
    assert "## Memory recall" in out["context"]


def test_make_hook_returns_callables():
    handle, register_tools = make_hook("testagent")
    assert callable(handle)
    assert callable(register_tools)


@pytest.mark.asyncio
async def test_handle_no_message_returns_none():
    handle, _ = make_hook("jarvis")
    assert await handle("agent:start", {}) is None
    assert await handle("agent:start", {"message": ""}) is None
    assert await handle("agent:start", {"message": "   "}) is None


@pytest.mark.asyncio
async def test_handle_never_raises_on_config_failure():
    handle, _ = make_hook("nonexistent-agent-xyz-12345")
    # No config file exists — should return None, not raise
    result = await handle("agent:start", {"message": "hello"})
    assert result is None


def test_register_tools_never_raises_on_bad_registry():
    _, register_tools = make_hook("jarvis")

    class BrokenRegistry:
        def register(self, tool):
            raise RuntimeError("nope")

    # Should log warning, not raise
    register_tools(BrokenRegistry())
