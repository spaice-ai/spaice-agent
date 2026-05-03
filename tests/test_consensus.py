"""Tests for spaice_agent.consensus — 3-chair pipeline + synthesis."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from typing import List

import pytest

from spaice_agent.config import AgentConfig
from spaice_agent.consensus import (
    ConsensusError,
    ConsensusResult,
    _compose_user_prompt,
    _truncate,
    run_consensus,
)
from spaice_agent.openrouter_client import (
    AuthError,
    ChatResult,
    OpenRouterError,
    RateLimitError,
    ServerError,
)


# ---------------------------------------------------------------------------
# _compose_user_prompt — deterministic template
# ---------------------------------------------------------------------------


def test_compose_prompt_proposer_stage():
    """First chair sees only user request + context + task."""
    prompt = _compose_user_prompt(
        user_message="do the thing",
        context_md="",
        prior_stages=[],
        stage_instruction="propose an answer",
    )
    assert "--- ORIGINAL USER REQUEST ---" in prompt
    assert "do the thing" in prompt
    assert "--- CONTEXT" not in prompt  # no context → no section
    assert "--- CHAIR 1" not in prompt
    assert "--- YOUR TASK ---" in prompt
    assert "propose an answer" in prompt


def test_compose_prompt_with_context():
    prompt = _compose_user_prompt(
        user_message="q",
        context_md="**Search hit:** a.com",
        prior_stages=[],
        stage_instruction="go",
    )
    assert "--- CONTEXT (memory recall / search hits) ---" in prompt
    assert "a.com" in prompt


def test_compose_prompt_includes_prior_stages_in_order():
    from spaice_agent.consensus import StageOutput

    proposer = StageOutput(
        stage="proposer", model="m1", text="proposal-text",
        input_tokens=1, output_tokens=1, cost_usd=0, latency_s=0,
    )
    critic = StageOutput(
        stage="critic", model="m2", text="critique-text",
        input_tokens=1, output_tokens=1, cost_usd=0, latency_s=0,
    )
    prompt = _compose_user_prompt(
        user_message="q", context_md="",
        prior_stages=[proposer, critic],
        stage_instruction="review now",
    )
    # Proposer block before critic block
    assert prompt.index("--- CHAIR 1 (PROPOSER) ---") < prompt.index(
        "--- CHAIR 2 (CRITIC) ---"
    )
    assert "proposal-text" in prompt
    assert "critique-text" in prompt


# ---------------------------------------------------------------------------
# _truncate
# ---------------------------------------------------------------------------


def test_truncate_respects_limit():
    out = _truncate("a" * 100, 50)
    assert len(out) > 50  # marker appended
    assert "truncated" in out


def test_truncate_passthrough_when_under_limit():
    assert _truncate("hello", 100) == "hello"


def test_truncate_zero_means_no_truncate():
    assert _truncate("a" * 1000, 0) == "a" * 1000


# ---------------------------------------------------------------------------
# run_consensus with mocked OpenRouterClient
# ---------------------------------------------------------------------------


@pytest.fixture
def config(tmp_path) -> AgentConfig:
    import yaml
    cfg_yaml = f"""
agent_id: jarvis
memory_root: {tmp_path}
credentials:
  openrouter_api_key_env: OPENROUTER_API_KEY
  exa_api_key_env: EXA_API_KEY
  brave_api_key_env: BRAVE_API_KEY
hook:
  total_timeout_s: 60.0
memory:
  enabled: true
  entity_cache_path: {tmp_path}/cache.json
  stage_timeout_s: 2.0
  recall_max_hits_per_entity: 3
  recall_snippet_chars: 240
  live_capture_dir: {tmp_path}/_inbox
search:
  enabled: true
  daily_fire_cap: 50
  stage_timeout_s: 8.0
  providers:
    - {{name: exa, endpoint: https://api.exa.ai/search, max_results: 5, per_request_timeout_s: 3.0}}
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
      max_tokens: 1024
      truncate_output_chars: 4000
      system: proposer-sys
    - stage: critic
      model: openai/gpt-5-codex
      stage_timeout_s: 30.0
      max_tokens: 1024
      truncate_output_chars: 4000
      system: critic-sys
    - stage: reviewer
      model: deepseek/deepseek-v3.1-terminus
      stage_timeout_s: 30.0
      max_tokens: 2048
      truncate_output_chars: 6000
      system: reviewer-sys
  triggers:
    words: [plan]
    phrases: [ask codex]
scrubber:
  enabled: true
  leak_patterns: []
"""
    raw = yaml.safe_load(cfg_yaml)
    return AgentConfig(**raw)


def _chat_result(text: str, model: str = "m", cost: float = 0.01) -> ChatResult:
    return ChatResult(
        text=text,
        input_tokens=100,
        output_tokens=50,
        cost_usd=cost,
        model=model,
        finish_reason="stop",
        latency_s=0.5,
    )


@pytest.mark.asyncio
async def test_run_consensus_happy_path(config):
    """All four stages fire; synthesis output is the final reply."""
    client = MagicMock()
    client.aclose = AsyncMock()
    responses = [
        _chat_result("proposal", "anthropic/claude-opus-4.7"),
        _chat_result("critique", "openai/gpt-5-codex"),
        _chat_result("review", "deepseek/deepseek-v3.1-terminus"),
        _chat_result("final reply", "anthropic/claude-opus-4.7", cost=0.05),
    ]
    client.chat = AsyncMock(side_effect=responses)

    result = await run_consensus(
        config,
        api_key="test-key",
        user_message="Should we lock Forge at v0.4?",
        client=client,
    )
    assert result.error is None
    assert result.final_reply == "final reply"
    assert len(result.stages) == 4
    assert [s.stage for s in result.stages] == [
        "proposer", "critic", "reviewer", "synthesis",
    ]
    # Total cost = 0.01 * 3 + 0.05
    assert result.total_cost_usd == pytest.approx(0.08, rel=0.01)


@pytest.mark.asyncio
async def test_run_consensus_empty_message_short_circuits(config):
    client = MagicMock()
    client.chat = AsyncMock()
    client.aclose = AsyncMock()
    result = await run_consensus(
        config, api_key="k", user_message="   ", client=client,
    )
    assert result.error == "empty user_message"
    assert result.final_reply == ""
    client.chat.assert_not_called()


@pytest.mark.asyncio
async def test_run_consensus_chair_failure_aborts_pipeline(config):
    """If critic fails, reviewer + synthesis should NOT run."""
    client = MagicMock()
    client.aclose = AsyncMock()
    client.chat = AsyncMock(side_effect=[
        _chat_result("proposal"),
        AuthError("bad key", status=401, body={}),
    ])

    result = await run_consensus(
        config, api_key="k", user_message="q", client=client,
    )
    assert result.error is not None
    assert "critic" in result.error
    assert "unrecoverable" in result.error
    assert result.final_reply == ""
    assert len(result.stages) == 1
    assert client.chat.await_count == 2


@pytest.mark.asyncio
async def test_run_consensus_synthesis_failure_surfaces(config):
    """Proposer+critic+reviewer succeed but synthesis fails — error set."""
    client = MagicMock()
    client.aclose = AsyncMock()
    client.chat = AsyncMock(side_effect=[
        _chat_result("proposal"),
        _chat_result("critique"),
        _chat_result("review"),
        ServerError("upstream busy", status=503, body={}),
    ])
    result = await run_consensus(
        config, api_key="k", user_message="q", client=client,
    )
    assert "synthesis failed" in (result.error or "")
    assert result.final_reply == ""
    # Three chairs should still be recorded
    assert len(result.stages) == 3


@pytest.mark.asyncio
async def test_run_consensus_uses_context_md(config):
    """context_md should appear in every stage's prompt."""
    client = MagicMock()
    client.aclose = AsyncMock()
    client.chat = AsyncMock(side_effect=[
        _chat_result("p"), _chat_result("c"),
        _chat_result("r"), _chat_result("final"),
    ])
    await run_consensus(
        config,
        api_key="k",
        user_message="q",
        context_md="**Search:** abc.com",
        client=client,
    )
    # Every call should have the context in the user message
    for call in client.chat.await_args_list:
        kwargs = call.kwargs
        user_msg = kwargs["messages"][-1]["content"]
        assert "abc.com" in user_msg


@pytest.mark.asyncio
async def test_run_consensus_truncates_chair_output(config):
    """Chair output longer than truncate_output_chars gets capped."""
    client = MagicMock()
    client.aclose = AsyncMock()
    long_proposal = "x" * 5000  # > 4000 truncate_output_chars
    client.chat = AsyncMock(side_effect=[
        _chat_result(long_proposal),
        _chat_result("critique"),
        _chat_result("review"),
        _chat_result("final"),
    ])
    result = await run_consensus(
        config, api_key="k", user_message="q", client=client,
    )
    # Proposer stage text should be truncated
    proposer = next(s for s in result.stages if s.stage == "proposer")
    assert len(proposer.text) < 5000
    assert "truncated" in proposer.text


@pytest.mark.asyncio
async def test_run_consensus_synthesis_not_truncated(config):
    """Synthesis output (user-facing) must never be truncated."""
    client = MagicMock()
    client.aclose = AsyncMock()
    long_final = "y" * 10000
    client.chat = AsyncMock(side_effect=[
        _chat_result("p"), _chat_result("c"), _chat_result("r"),
        _chat_result(long_final),
    ])
    result = await run_consensus(
        config, api_key="k", user_message="q", client=client,
    )
    synthesis = result.stages[-1]
    assert synthesis.stage == "synthesis"
    # Not truncated — full length preserved
    assert len(synthesis.text) == 10000
    assert "truncated" not in synthesis.text


@pytest.mark.asyncio
async def test_result_as_dict_audit_safe(config):
    """as_dict() must not expose raw chair text — only lengths."""
    client = MagicMock()
    client.aclose = AsyncMock()
    client.chat = AsyncMock(side_effect=[
        _chat_result("secret proposal"),
        _chat_result("secret critique"),
        _chat_result("secret review"),
        _chat_result("public reply"),
    ])
    result = await run_consensus(
        config, api_key="k", user_message="q", client=client,
    )
    audit = result.as_dict()
    for stage in audit["stages"]:
        assert "text" not in stage  # full text must not be in audit
        assert "text_len" in stage
