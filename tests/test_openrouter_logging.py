"""Tests for OpenRouter call JSONL logging (BuildGuard dependency).

BuildGuard in orchestrator.py reads these logs to verify a DeepSeek-V4-Pro
call was made before allowing a write to spaice_agent/**/*.py. This module
tests that the client writes the log correctly.
"""
from __future__ import annotations

import json
import os
import pytest
import httpx
from datetime import datetime, timezone
from pathlib import Path

from spaice_agent.openrouter_client import OpenRouterClient


pytestmark = pytest.mark.asyncio


def _mock_client_with_logging(handler, agent_id, home_override):
    """Build a client with agent_id set (enables logging)."""
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        transport=transport,
        headers={
            "Authorization": "Bearer sk-test",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://test.local",
            "X-Title": "test",
        },
    )
    return OpenRouterClient(
        api_key="sk-test",
        max_retries=2,
        client=http_client,
        agent_id=agent_id,
    )


def _success_body(text="ok", cost=0.0123, in_tok=123, out_tok=456):
    return {
        "id": "test-id",
        "model": "deepseek/deepseek-v4-pro",
        "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": in_tok, "completion_tokens": out_tok, "cost": cost},
    }


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Point Path.home() at tmp_path so the log lands in isolation."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # Path.home() uses HOME env var on POSIX
    return tmp_path


# ---------------------------------------------------------------------------
# Logging disabled when agent_id is None
# ---------------------------------------------------------------------------


async def test_logging_disabled_when_no_agent_id(fake_home):
    def handler(req):
        return httpx.Response(200, json=_success_body())

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    client = OpenRouterClient(api_key="sk-test", client=http_client)  # no agent_id

    await client.chat(model="deepseek/deepseek-v4-pro",
                      messages=[{"role": "user", "content": "spaice_agent/foo.py"}],
                      max_tokens=100)
    await client.aclose()

    log_dir = fake_home / ".spaice-agents"
    assert not log_dir.exists() or not any(log_dir.rglob("openrouter-*.jsonl"))


# ---------------------------------------------------------------------------
# Successful call is logged with all required fields
# ---------------------------------------------------------------------------


async def test_successful_call_logged_with_all_fields(fake_home):
    def handler(req):
        return httpx.Response(200, json=_success_body(text="hello", cost=0.0123, in_tok=200, out_tok=300))

    client = _mock_client_with_logging(handler, agent_id="test-agent", home_override=fake_home)
    messages = [{"role": "user", "content": "Write spaice_agent/foo.py please"}]
    await client.chat(model="deepseek/deepseek-v4-pro", messages=messages, max_tokens=100)
    await client.aclose()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = fake_home / ".spaice-agents" / "test-agent" / "logs" / f"openrouter-{today}.jsonl"
    assert log_file.exists(), f"log file should exist at {log_file}"

    line = log_file.read_text().strip()
    entry = json.loads(line)

    assert entry["status"] == "ok"
    assert entry["error"] is None
    assert entry["model"] == "deepseek/deepseek-v4-pro"
    assert entry["messages"] == messages
    assert entry["tokens_in"] == 200
    assert entry["tokens_out"] == 300
    assert entry["cost_usd"] == 0.0123
    assert "latency_s" in entry
    assert "timestamp" in entry
    # ISO 8601 with timezone
    datetime.fromisoformat(entry["timestamp"])


async def test_full_prompt_preserved_in_log(fake_home):
    """BuildGuard substring-matches on the prompt — it must be logged verbatim."""
    def handler(req):
        return httpx.Response(200, json=_success_body())

    client = _mock_client_with_logging(handler, agent_id="test-agent", home_override=fake_home)
    prompt = "Implement spaice_agent/memory/foo.py per the framework spec"
    messages = [{"role": "user", "content": prompt}]
    await client.chat(model="deepseek/deepseek-v4-pro", messages=messages, max_tokens=100)
    await client.aclose()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = fake_home / ".spaice-agents" / "test-agent" / "logs" / f"openrouter-{today}.jsonl"
    entry = json.loads(log_file.read_text().strip())
    assert entry["messages"][0]["content"] == prompt
    assert "spaice_agent/memory/foo.py" in entry["messages"][0]["content"]


# ---------------------------------------------------------------------------
# Failed call is also logged
# ---------------------------------------------------------------------------


async def test_failed_call_logged_as_error(fake_home):
    def handler(req):
        return httpx.Response(400, json={"error": "bad request"})

    client = _mock_client_with_logging(handler, agent_id="test-agent", home_override=fake_home)
    with pytest.raises(Exception):
        await client.chat(
            model="deepseek/deepseek-v4-pro",
            messages=[{"role": "user", "content": "x"}],
            max_tokens=100,
        )
    await client.aclose()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = fake_home / ".spaice-agents" / "test-agent" / "logs" / f"openrouter-{today}.jsonl"
    assert log_file.exists()
    entry = json.loads(log_file.read_text().strip())
    assert entry["status"] == "error"
    assert entry["error"]  # non-empty


# ---------------------------------------------------------------------------
# Log append behaviour (multiple calls, one file)
# ---------------------------------------------------------------------------


async def test_multiple_calls_appended_to_same_file(fake_home):
    def handler(req):
        return httpx.Response(200, json=_success_body())

    client = _mock_client_with_logging(handler, agent_id="test-agent", home_override=fake_home)
    for i in range(3):
        await client.chat(model="deepseek/deepseek-v4-pro",
                          messages=[{"role": "user", "content": f"call {i}"}],
                          max_tokens=100)
    await client.aclose()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = fake_home / ".spaice-agents" / "test-agent" / "logs" / f"openrouter-{today}.jsonl"
    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 3
    for i, line in enumerate(lines):
        entry = json.loads(line)
        assert entry["messages"][0]["content"] == f"call {i}"


# ---------------------------------------------------------------------------
# Log failure must not break the chat call
# ---------------------------------------------------------------------------


async def test_log_write_failure_does_not_break_chat(fake_home, monkeypatch):
    """Make the log directory unwriteable — chat should still succeed."""
    def handler(req):
        return httpx.Response(200, json=_success_body())

    # Create the logs dir as a file (so mkdir fails)
    bad_path = fake_home / ".spaice-agents" / "test-agent" / "logs"
    bad_path.parent.mkdir(parents=True, exist_ok=True)
    bad_path.write_text("i am a file, not a dir")  # mkdir will fail

    client = _mock_client_with_logging(handler, agent_id="test-agent", home_override=fake_home)
    # Should not raise, should return ChatResult
    result = await client.chat(model="deepseek/deepseek-v4-pro",
                                messages=[{"role": "user", "content": "x"}],
                                max_tokens=100)
    await client.aclose()
    assert result.text == "ok"
