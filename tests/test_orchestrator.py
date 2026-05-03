"""Tests for spaice_agent.orchestrator — triggers → pipelines → reply."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from spaice_agent.config import AgentConfig
from spaice_agent.orchestrator import (
    OrchestratorResult,
    _has_force_search_word,
    handle_message,
)


# ---------------------------------------------------------------------------
# _has_force_search_word
# ---------------------------------------------------------------------------


def test_force_search_word_research():
    assert _has_force_search_word("please research quantum stuff") == "research"


def test_force_search_word_analyse():
    assert _has_force_search_word("analyse the trace") == "analyse"


def test_force_search_word_analyze_us_spelling():
    assert _has_force_search_word("analyze the trace") == "analyze"


def test_force_search_word_case_insensitive():
    assert _has_force_search_word("RESEARCH this") == "research"


def test_force_search_word_not_substring():
    # 'researched' should NOT match 'research' — word boundary
    assert _has_force_search_word("I researched it") is None


def test_force_search_word_none():
    assert _has_force_search_word("hello there") is None


# ---------------------------------------------------------------------------
# handle_message — integration, with all dependencies mocked
# ---------------------------------------------------------------------------


@pytest.fixture
def config(tmp_path) -> AgentConfig:
    import yaml
    cfg = f"""
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
    phrase_anchors: ["\\\\blook up\\\\b"]
    url_at_end: true
consensus:
  enabled: true
  daily_fire_cap: 50
  pipeline_timeout_s: 90.0
  pipeline:
    - {{stage: proposer, model: m1, stage_timeout_s: 30, max_tokens: 1024, truncate_output_chars: 4000, system: s}}
    - {{stage: critic, model: m2, stage_timeout_s: 30, max_tokens: 1024, truncate_output_chars: 4000, system: s}}
    - {{stage: reviewer, model: m3, stage_timeout_s: 30, max_tokens: 1024, truncate_output_chars: 4000, system: s}}
  triggers:
    words: [plan, analyse, analyze, review]
    phrases: [ask codex]
scrubber:
  enabled: true
  leak_patterns: []
"""
    raw = yaml.safe_load(cfg)
    return AgentConfig(**raw)


@pytest.fixture
def counter(tmp_path):
    from spaice_agent.budget import DailyCounter
    return DailyCounter("jarvis", base_dir=tmp_path)


@pytest.mark.asyncio
async def test_idle_message_no_triggers(config, counter, monkeypatch):
    """Plain message: recall fires (free), nothing else."""
    with patch(
        "spaice_agent.orchestrator.recall",
        new=AsyncMock(return_value=_recall_empty()),
    ):
        result = await handle_message(
            "hello there", config, counter=counter,
        )
    assert result.reply is None  # no override — fall through to default Opus
    assert "recall" in result.fired
    assert "consensus" not in " ".join(result.fired)
    assert "search" not in " ".join(result.fired)


@pytest.mark.asyncio
async def test_research_word_fires_search_only(
    config, counter, tmp_path, monkeypatch,
):
    """`research` word: search fires, consensus does NOT."""
    # Write fake Exa credential
    creds_dir = tmp_path / "cred-store"
    creds_dir.mkdir()
    key = creds_dir / "exa.key"
    key.write_text("exa-key", encoding="utf-8")
    import os
    os.chmod(key, 0o600)
    monkeypatch.setattr(
        "spaice_agent.credentials.CREDENTIAL_DIR", creds_dir,
    )

    fake_search = _search_with_one_hit()
    with (
        patch(
            "spaice_agent.orchestrator.recall",
            new=AsyncMock(return_value=_recall_empty()),
        ),
        patch(
            "spaice_agent.orchestrator.run_search",
            new=AsyncMock(return_value=fake_search),
        ),
        patch(
            "spaice_agent.orchestrator.run_consensus",
            new=AsyncMock(),
        ) as mock_consensus,
    ):
        result = await handle_message(
            "please research fast ethernet switches",
            config, counter=counter,
        )
    assert any("search" in s for s in result.fired)
    assert result.search is fake_search
    assert result.consensus is None
    mock_consensus.assert_not_called()
    # Reply should be the search results markdown block
    assert result.reply is not None
    assert "example.com" in result.reply


@pytest.mark.asyncio
async def test_analyse_word_fires_both_pipelines(
    config, counter, tmp_path, monkeypatch,
):
    """`analyse` word: both search AND consensus fire."""
    creds_dir = tmp_path / "cred-store"
    creds_dir.mkdir()
    for name, value in [("exa", "e"), ("openrouter", "o")]:
        k = creds_dir / f"{name}.key"
        k.write_text(value)
        import os
        os.chmod(k, 0o600)
    monkeypatch.setattr(
        "spaice_agent.credentials.CREDENTIAL_DIR", creds_dir,
    )

    with (
        patch(
            "spaice_agent.orchestrator.recall",
            new=AsyncMock(return_value=_recall_empty()),
        ),
        patch(
            "spaice_agent.orchestrator.run_search",
            new=AsyncMock(return_value=_search_with_one_hit()),
        ),
        patch(
            "spaice_agent.orchestrator.run_consensus",
            new=AsyncMock(return_value=_consensus_result("consensus reply")),
        ),
    ):
        result = await handle_message(
            "please analyse the dmarc policy", config, counter=counter,
        )
    assert any("search" in s for s in result.fired)
    assert any("consensus" in s for s in result.fired)
    # Consensus reply wins over search markdown
    assert result.reply == "consensus reply"


@pytest.mark.asyncio
async def test_consensus_daily_cap_skips(config, counter, tmp_path, monkeypatch):
    """When consensus cap is exhausted, skipped recorded + reply falls back."""
    # Pre-exhaust the counter
    for _ in range(config.consensus.daily_fire_cap):
        counter.check_and_fire("consensus", config.consensus.daily_fire_cap)

    with (
        patch(
            "spaice_agent.orchestrator.recall",
            new=AsyncMock(return_value=_recall_empty()),
        ),
        patch(
            "spaice_agent.orchestrator.run_consensus",
            new=AsyncMock(),
        ) as mock_consensus,
    ):
        result = await handle_message(
            "please review this plan", config, counter=counter,
        )
    assert "consensus" in result.skipped
    assert "cap exhausted" in result.skipped["consensus"]
    mock_consensus.assert_not_called()


@pytest.mark.asyncio
async def test_empty_message_noop(config, counter):
    result = await handle_message("", config, counter=counter)
    assert result.reply is None
    assert result.fired == []


@pytest.mark.asyncio
async def test_recall_failure_does_not_abort_pipeline(
    config, counter, tmp_path, monkeypatch,
):
    """recall raising should not prevent consensus from firing."""
    creds_dir = tmp_path / "cred-store"
    creds_dir.mkdir()
    k = creds_dir / "openrouter.key"
    k.write_text("o")
    import os
    os.chmod(k, 0o600)
    monkeypatch.setattr(
        "spaice_agent.credentials.CREDENTIAL_DIR", creds_dir,
    )

    async def broken_recall(*a, **kw):
        raise RuntimeError("disk full")

    with (
        patch(
            "spaice_agent.orchestrator.recall", new=broken_recall,
        ),
        patch(
            "spaice_agent.orchestrator.run_consensus",
            new=AsyncMock(return_value=_consensus_result("ok")),
        ),
    ):
        result = await handle_message(
            "please review it", config, counter=counter,
        )
    assert "recall" in result.skipped
    assert "disk full" in result.skipped["recall"]
    assert result.reply == "ok"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _recall_empty():
    from spaice_agent.memory_recall import RecallResult
    return RecallResult(hits=[], elapsed_s=0.01, error=None)


def _search_with_one_hit():
    from spaice_agent.search import SearchHit, SearchResult
    return SearchResult(
        query="test",
        hits=[SearchHit(
            url="https://example.com",
            title="Example",
            snippet="an example",
            provider="exa",
            raw_rank=1,
        )],
        provider_errors={},
        providers_used=["exa"],
        elapsed_s=0.5,
    )


def _consensus_result(reply: str):
    from spaice_agent.consensus import ConsensusResult
    return ConsensusResult(
        user_message="q",
        stages=[],
        final_reply=reply,
        total_cost_usd=0.15,
        total_latency_s=10.0,
        error=None,
    )
