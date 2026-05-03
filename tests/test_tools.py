"""Tests for spaice_agent.tools.use_consensus — the on-demand consensus tool."""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from spaice_agent.tools.use_consensus import build_use_consensus_tool


def test_build_tool_descriptor_shape():
    tool = build_use_consensus_tool("jarvis")
    assert tool["name"] == "use_consensus"
    assert "parameters" in tool
    assert "question" in tool["parameters"]["required"]
    assert callable(tool["handler"])


def test_build_tool_description_includes_cost_latency():
    tool = build_use_consensus_tool("jarvis")
    assert "Cost" in tool["description"]
    assert "Latency" in tool["description"]


@pytest.mark.asyncio
async def test_handler_returns_structured_dict_on_config_failure(tmp_path, monkeypatch):
    """Missing config → structured error, not exception."""
    tool = build_use_consensus_tool("nonexistent-agent-abcd-12345")
    result = await tool["handler"](question="test?")
    assert isinstance(result, dict)
    assert result["ok"] is False
    assert result["error"] is not None
    assert "config_load_failed" in result["error"]


@pytest.mark.asyncio
async def test_handler_never_raises():
    """Tool contract: never raise under any input."""
    # Use a non-existent agent so config load fails fast → structured error
    tool = build_use_consensus_tool("nonexistent-never-raises-test-xyz")
    # Try to break it with weird inputs — should always return dict
    for q in ["", "?", "a" * 10000, "\x00null"]:
        try:
            result = await tool["handler"](question=q)
            assert isinstance(result, dict)
            assert "ok" in result
            assert result["ok"] is False  # config missing → structured error
        except Exception as exc:
            pytest.fail(f"handler raised on question={q!r}: {exc}")


def test_ledger_append_writes_jsonl(tmp_path, monkeypatch):
    """Ledger writes a JSONL entry with all expected fields."""
    from spaice_agent import ledger

    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    ledger.append_ledger(
        "testagent",
        cost_usd=0.25,
        latency_s=45.3,
        stages_ran=4,
        ok=True,
        trigger_reason="tool_call",
    )

    ledger_path = tmp_path / ".spaice-agents" / "testagent" / "state" / "consensus_ledger.jsonl"
    assert ledger_path.exists()
    entry = json.loads(ledger_path.read_text().strip())
    assert entry["cost_usd"] == 0.25
    assert entry["latency_s"] == 45.3
    assert entry["stages_ran"] == 4
    assert entry["ok"] is True
    assert entry["trigger_reason"] == "tool_call"
    assert "ts" in entry
    assert "turn_id" in entry


def test_ledger_append_includes_error_when_provided(tmp_path, monkeypatch):
    from spaice_agent import ledger

    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    ledger.append_ledger(
        "testagent",
        cost_usd=0.0,
        latency_s=5.0,
        stages_ran=0,
        ok=False,
        trigger_reason="tool_call",
        error="pipeline_timeout",
    )

    ledger_path = tmp_path / ".spaice-agents" / "testagent" / "state" / "consensus_ledger.jsonl"
    entry = json.loads(ledger_path.read_text().strip())
    assert entry["error"] == "pipeline_timeout"


def test_ledger_appends_multiple_entries(tmp_path, monkeypatch):
    from spaice_agent import ledger

    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    for i in range(3):
        ledger.append_ledger(
            "testagent",
            cost_usd=float(i),
            latency_s=float(i * 10),
            stages_ran=i,
            ok=True,
            trigger_reason="tool_call",
        )

    ledger_path = tmp_path / ".spaice-agents" / "testagent" / "state" / "consensus_ledger.jsonl"
    lines = ledger_path.read_text().strip().split("\n")
    assert len(lines) == 3
    for i, line in enumerate(lines):
        entry = json.loads(line)
        assert entry["cost_usd"] == float(i)
