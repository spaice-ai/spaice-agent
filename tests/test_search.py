"""Tests for spaice_agent.search — Exa+Brave fan-out and RRF merge."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Dict

import httpx
import pytest

from spaice_agent.config import load_agent_config, AgentConfig
from spaice_agent.search import (
    SearchError,
    SearchHit,
    SearchResult,
    _merge_rrf,
    run_search,
)


# ---------------------------------------------------------------------------
# _merge_rrf — unit tests
# ---------------------------------------------------------------------------


def _hit(url: str, rank: int, provider: str = "exa") -> SearchHit:
    return SearchHit(
        url=url, title=url, snippet="", provider=provider, raw_rank=rank,
    )


def test_merge_empty_lists():
    assert _merge_rrf([], k=60) == []
    assert _merge_rrf([[], []], k=60) == []


def test_merge_single_provider():
    hits = [_hit("https://a", 1), _hit("https://b", 2)]
    merged = _merge_rrf([hits], k=60)
    assert [h.url for h in merged] == ["https://a", "https://b"]


def test_merge_deduplicates_by_url():
    exa = [_hit("https://a", 1, "exa"), _hit("https://b", 2, "exa")]
    brave = [_hit("https://a", 1, "brave"), _hit("https://c", 2, "brave")]
    merged = _merge_rrf([exa, brave], k=60)
    urls = [h.url for h in merged]
    assert len(urls) == len(set(urls))
    # 'a' appears in both at rank 1 — should be ranked highest
    assert urls[0] == "https://a"


def test_merge_trailing_slash_insensitive():
    exa = [_hit("https://a/", 1, "exa")]
    brave = [_hit("https://a", 1, "brave")]
    merged = _merge_rrf([exa, brave], k=60)
    assert len(merged) == 1


def test_merge_case_insensitive_url():
    exa = [_hit("https://Example.com/X", 1, "exa")]
    brave = [_hit("https://example.com/x", 1, "brave")]
    merged = _merge_rrf([exa, brave], k=60)
    assert len(merged) == 1


def test_merge_first_seen_wins_for_metadata():
    exa = [_hit("https://a", 1, "exa")]
    brave = [_hit("https://a", 1, "brave")]
    merged = _merge_rrf([exa, brave], k=60)
    # Exa listed first → its provider name wins
    assert merged[0].provider == "exa"


def test_merge_rejects_bad_k():
    with pytest.raises(ValueError):
        _merge_rrf([[_hit("https://a", 1)]], k=0)


def test_merge_score_ordering():
    """Higher rank in multiple providers > single-provider hits."""
    exa = [
        _hit("https://a", 1),  # 1/61 in exa
        _hit("https://b", 2),  # 1/62 in exa
    ]
    brave = [
        _hit("https://a", 1, "brave"),  # add 1/61
        _hit("https://c", 1, "brave"),  # 1/61 single
    ]
    merged = _merge_rrf([exa, brave], k=60)
    urls = [h.url for h in merged]
    # a wins (both providers), then c (single first), then b
    assert urls[0] == "https://a"
    # b and c both at 1/61 vs 1/62 — c should beat b
    assert urls.index("https://c") < urls.index("https://b")


# ---------------------------------------------------------------------------
# run_search — with MockTransport
# ---------------------------------------------------------------------------


@pytest.fixture
def jarvis_config(tmp_path) -> AgentConfig:
    """Build an AgentConfig with both Exa and Brave providers."""
    from spaice_agent.config import AgentConfig
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
    - name: exa
      endpoint: https://api.exa.ai/search
      max_results: 5
      per_request_timeout_s: 3.0
    - name: brave
      endpoint: https://api.search.brave.com/res/v1/web/search
      max_results: 5
      per_request_timeout_s: 3.0
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
    - {{stage: proposer, model: m1, stage_timeout_s: 30, max_tokens: 1024, truncate_output_chars: 1000, system: s1}}
    - {{stage: critic, model: m2, stage_timeout_s: 30, max_tokens: 1024, truncate_output_chars: 1000, system: s2}}
    - {{stage: reviewer, model: m3, stage_timeout_s: 30, max_tokens: 1024, truncate_output_chars: 1000, system: s3}}
  triggers:
    words: [plan]
    phrases: [ask codex]
scrubber:
  enabled: true
  leak_patterns: []
"""
    raw = yaml.safe_load(cfg_yaml)
    return AgentConfig(**raw)


def _build_exa_response(urls: list[str]) -> dict:
    return {
        "results": [
            {
                "url": u,
                "title": f"Title {i}",
                "text": f"Snippet {i} for {u}",
            }
            for i, u in enumerate(urls)
        ]
    }


def _build_brave_response(urls: list[str]) -> dict:
    return {
        "web": {
            "results": [
                {
                    "url": u,
                    "title": f"Brave {i}",
                    "description": f"brave-desc-{i}",
                }
                for i, u in enumerate(urls)
            ]
        }
    }


@pytest.mark.asyncio
async def test_run_search_fan_out_merges_results(jarvis_config):
    def handler(request: httpx.Request) -> httpx.Response:
        if "exa.ai" in str(request.url):
            return httpx.Response(
                200, json=_build_exa_response(["https://x.com", "https://y.com"]),
            )
        if "brave.com" in str(request.url):
            return httpx.Response(
                200, json=_build_brave_response(["https://x.com", "https://z.com"]),
            )
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await run_search(
            jarvis_config,
            "test query",
            credentials={"exa": "exa-key", "brave": "brave-key"},
            client=client,
        )
    assert result.hits
    urls = [h.url for h in result.hits]
    # x.com appears in both -> should be first
    assert urls[0] == "https://x.com"
    assert "https://y.com" in urls
    assert "https://z.com" in urls
    assert set(result.providers_used) == {"exa", "brave"}


@pytest.mark.asyncio
async def test_run_search_one_provider_missing_credentials(jarvis_config):
    def handler(request: httpx.Request) -> httpx.Response:
        if "exa.ai" in str(request.url):
            return httpx.Response(
                200, json=_build_exa_response(["https://x.com"]),
            )
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        # Brave key absent — should still succeed via Exa alone
        result = await run_search(
            jarvis_config, "test",
            credentials={"exa": "key"},
            client=client,
        )
    assert [h.url for h in result.hits] == ["https://x.com"]
    assert result.providers_used == ["exa"]


@pytest.mark.asyncio
async def test_run_search_no_credentials_at_all(jarvis_config):
    async with httpx.AsyncClient() as client:
        with pytest.raises(SearchError, match="no providers had credentials"):
            await run_search(
                jarvis_config, "x",
                credentials={},
                client=client,
            )


@pytest.mark.asyncio
async def test_run_search_provider_http_error_is_recorded(jarvis_config):
    def handler(request: httpx.Request) -> httpx.Response:
        if "exa.ai" in str(request.url):
            return httpx.Response(429, json={"error": "rate limited"})
        if "brave.com" in str(request.url):
            return httpx.Response(
                200, json=_build_brave_response(["https://a.com"]),
            )
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await run_search(
            jarvis_config, "q",
            credentials={"exa": "k1", "brave": "k2"},
            client=client,
        )
    assert [h.url for h in result.hits] == ["https://a.com"]
    assert "exa" in result.provider_errors
    assert "429" in result.provider_errors["exa"]


@pytest.mark.asyncio
async def test_run_search_empty_query_raises(jarvis_config):
    with pytest.raises(SearchError):
        await run_search(jarvis_config, "   ", credentials={"exa": "k"})


@pytest.mark.asyncio
async def test_run_search_disabled_raises(jarvis_config):
    import dataclasses
    # Clone config with search disabled; AgentConfig is frozen so we have
    # to round-trip through dict
    raw = jarvis_config.model_dump()
    raw["search"]["enabled"] = False
    cfg2 = type(jarvis_config)(**raw)
    with pytest.raises(SearchError, match="disabled"):
        await run_search(cfg2, "q", credentials={"exa": "k"})


# ---------------------------------------------------------------------------
# SearchResult.to_markdown
# ---------------------------------------------------------------------------


def test_to_markdown_no_hits():
    r = SearchResult(
        query="q", hits=[], provider_errors={"exa": "500"},
        providers_used=[], elapsed_s=0,
    )
    md = r.to_markdown()
    assert "no hits" in md


def test_to_markdown_with_hits():
    r = SearchResult(
        query="q",
        hits=[
            SearchHit("https://a", "A", "snippet A", "exa", 1),
            SearchHit("https://b", "B", "snippet B", "brave", 1),
        ],
        provider_errors={},
        providers_used=["exa", "brave"],
        elapsed_s=0,
    )
    md = r.to_markdown()
    assert "https://a" in md
    assert "A" in md
    assert "_(exa)_" in md
