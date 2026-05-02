"""Tests for spaice_agent.triggers."""
from __future__ import annotations

import pytest

from spaice_agent.config import AgentConfig
from spaice_agent.triggers import (
    _COMPILED,
    consensus_triggered,
    matched_consensus_reason,
    matched_search_reason,
    search_triggered,
)


@pytest.fixture(autouse=True)
def _reset_pattern_cache():
    _COMPILED.clear()
    yield
    _COMPILED.clear()


@pytest.fixture
def config() -> AgentConfig:
    """Build a minimal but complete AgentConfig in-memory (no disk)."""
    return AgentConfig.model_validate({
        "agent_id": "test-trig",
        "memory_root": "~/test",
        "credentials": {
            "openrouter_api_key_env": "OR_KEY",
            "exa_api_key_env": "EXA_KEY",
            "brave_api_key_env": "BRAVE_KEY",
        },
        "hook": {"total_timeout_s": 11.0},
        "memory": {
            "enabled": True,
            "entity_cache_path": "/tmp/e.json",
            "stage_timeout_s": 1.5,
            "recall_max_hits_per_entity": 3,
            "recall_snippet_chars": 200,
            "live_capture_dir": "/tmp/live",
        },
        "search": {
            "enabled": True,
            "daily_fire_cap": 30,
            "stage_timeout_s": 3.0,
            "providers": [
                {"name": "exa", "endpoint": "https://api.exa.ai/search",
                 "max_results": 5, "per_request_timeout_s": 2.5},
                {"name": "brave", "endpoint": "https://api.search.brave.com",
                 "max_results": 5, "per_request_timeout_s": 2.5},
            ],
            "merge": {"method": "rrf", "k": 60},
            "triggers": {
                "phrase_anchors": [
                    r"^\s*(look up|search for|find me|research the|google)",
                    r"\b(current|latest)\s+(price|cost|version|release|status|news)\b",
                    r"\bwhat is the (current|latest|new)\b",
                ],
                "url_at_end": True,
            },
        },
        "consensus": {
            "enabled": True,
            "daily_fire_cap": 10,
            "pipeline_timeout_s": 8.0,
            "pipeline": [
                {"stage": "proposer", "model": "anthropic/claude-opus-4.7",
                 "stage_timeout_s": 3.0, "max_tokens": 1500,
                 "truncate_output_chars": 6000, "system": "p"},
                {"stage": "critic", "model": "openai/gpt-5-codex",
                 "stage_timeout_s": 3.0, "max_tokens": 1200,
                 "truncate_output_chars": 2000, "system": "c"},
                {"stage": "reviewer", "model": "deepseek/deepseek-v4-pro",
                 "stage_timeout_s": 2.0, "max_tokens": 2000,
                 "truncate_output_chars": 8000, "system": "r"},
            ],
            "triggers": {
                "words": ["plan", "decide", "research", "analyse", "analyze",
                          "review", "critique", "audit"],
                "phrases": ["ask codex", "second opinion", "sanity check"],
            },
        },
        "scrubber": {"enabled": True, "leak_patterns": [r"\bchair\s+[123]\b"]},
    })


@pytest.fixture
def config_search_disabled(config) -> AgentConfig:
    return config.model_copy(update={"search": config.search.model_copy(update={"enabled": False})})


@pytest.fixture
def config_consensus_disabled(config) -> AgentConfig:
    return config.model_copy(update={"consensus": config.consensus.model_copy(update={"enabled": False})})


# ============================================================
# SEARCH triggers
# ============================================================

class TestSearchTriggers:
    def test_look_up_phrase(self, config):
        assert search_triggered("look up the current Exa pricing", config)
        assert matched_search_reason("look up the current Exa pricing", config)

    def test_search_for_phrase(self, config):
        assert search_triggered("search for Control4 EA-3 datasheet", config)

    def test_find_me_phrase(self, config):
        assert search_triggered("find me a good 4K camera for outdoor", config)

    def test_research_the_phrase(self, config):
        assert search_triggered("research the latest DeepSeek V4 benchmarks", config)

    def test_google_phrase(self, config):
        assert search_triggered("google Sentrol reed switches for exterior doors", config)

    def test_current_price(self, config):
        assert search_triggered("what's the current price of Inception panels these days", config)

    def test_latest_release(self, config):
        assert search_triggered("show me the latest release for basalte keypads", config)

    def test_what_is_the_current(self, config):
        assert search_triggered("what is the current pricing tier for Exa?", config)

    def test_url_at_end(self, config):
        assert search_triggered("check this out https://example.com/doc", config)

    def test_url_at_end_trailing_punct(self, config):
        assert search_triggered("check this out https://example.com/doc.", config)

    def test_url_not_at_end(self, config):
        # URL present but message continues — must NOT trigger
        # (Unless another phrase anchor matches; here it shouldn't)
        assert not search_triggered("before https://example.com/doc and then more text", config)

    def test_too_short(self, config):
        assert not search_triggered("find me", config)

    def test_no_trigger(self, config):
        assert not search_triggered("thanks, that's all for now", config)

    def test_past_tense_research_no_hit(self, config):
        # "researched" should not trigger `research` search word — but actually,
        # search uses `research the` phrase anchor, not the word alone. So
        # "I researched the question" should not fire.
        # Our anchor is r"^\s*(look up|search for|find me|research the|google)"
        # — anchored to start. "I researched the" doesn't match because of ^\s*.
        assert not search_triggered("I researched the question yesterday", config)

    def test_disabled(self, config_search_disabled):
        assert not search_triggered("look up the price", config_search_disabled)


# ============================================================
# CONSENSUS triggers
# ============================================================

class TestConsensusTriggers:
    def test_plan_keyword(self, config):
        assert consensus_triggered("let's plan the new site deployment", config)
        assert matched_consensus_reason("let's plan the new site deployment", config) == "plan"

    def test_decide_keyword(self, config):
        assert consensus_triggered("we need to decide on the camera brand now", config)

    def test_review_keyword(self, config):
        assert consensus_triggered("please review this specification carefully", config)

    def test_critique_keyword(self, config):
        assert consensus_triggered("let's critique the proposal in detail", config)

    def test_audit_keyword(self, config):
        assert consensus_triggered("we should audit the current deployment", config)

    def test_phrase_ask_codex(self, config):
        assert consensus_triggered("can you ask codex about this piece of code", config)
        assert matched_consensus_reason("ask codex about this code snippet please", config) == "ask codex"

    def test_phrase_second_opinion(self, config):
        assert consensus_triggered("I need a second opinion on this plan", config)

    def test_phrase_sanity_check(self, config):
        assert consensus_triggered("run a sanity check on this schema", config)

    # --- Past-tense exclusions (critical false-positive guards) ---

    def test_past_tense_decided_no_fire(self, config):
        # "decided" must NOT trigger "decide"
        assert not consensus_triggered("we already decided this last week", config)

    def test_past_tense_planned_no_fire(self, config):
        assert not consensus_triggered("we already planned the whole thing", config)

    def test_past_tense_reviewed_no_fire(self, config):
        assert not consensus_triggered("that was reviewed by the team already", config)

    def test_past_tense_analysed_no_fire(self, config):
        assert not consensus_triggered("we analysed this finding last month", config)

    def test_past_tense_analyzed_no_fire(self, config):
        assert not consensus_triggered("we analyzed this finding last month", config)

    def test_past_tense_researched_no_fire(self, config):
        assert not consensus_triggered("we researched the vendor options already", config)

    def test_past_tense_critiqued_no_fire(self, config):
        assert not consensus_triggered("the design was critiqued last week", config)

    def test_past_tense_audited_no_fire(self, config):
        assert not consensus_triggered("the spec was audited last quarter", config)

    # --- Code/quote exclusions ---

    def test_decide_inside_backticks_no_fire(self, config):
        # Message refers to code; should NOT fire consensus
        msg = "the function is called `decide` and returns a bool, any thoughts"
        assert not consensus_triggered(msg, config)

    def test_plan_inside_backticks_no_fire(self, config):
        msg = "the variable `plan` holds the deployment target, is that a problem"
        assert not consensus_triggered(msg, config)

    def test_decide_in_quoted_line_no_fire(self, config):
        msg = "looking at her message:\n> we should decide on this next year\nthoughts?"
        assert not consensus_triggered(msg, config)

    def test_keyword_outside_backticks_still_fires(self, config):
        msg = "the function is called `execute` and we need to decide whether to use it"
        assert consensus_triggered(msg, config)

    # --- Edge cases ---

    def test_too_short(self, config):
        assert not consensus_triggered("plan it", config)  # <20 chars

    def test_no_trigger(self, config):
        assert not consensus_triggered("thanks for the help earlier today", config)

    def test_phrase_inside_larger_word_no_fire(self, config):
        # "baskcodex" should not match "ask codex"
        assert not consensus_triggered("the baskcodex library is pretty interesting", config)

    def test_disabled(self, config_consensus_disabled):
        assert not consensus_triggered("let's decide on the camera brand now", config_consensus_disabled)

    def test_case_insensitive(self, config):
        assert consensus_triggered("LET'S DECIDE ON THE CAMERA BRAND NOW", config)


# ============================================================
# Pattern-cache behaviour
# ============================================================


class TestPatternCache:
    def test_cache_populated_after_first_call(self, config):
        _COMPILED.clear()
        assert config.agent_id not in _COMPILED
        consensus_triggered("let's plan the deployment carefully", config)
        assert config.agent_id in _COMPILED

    def test_cache_reused(self, config):
        consensus_triggered("let's plan the deployment carefully", config)
        first = _COMPILED[config.agent_id]
        consensus_triggered("we need to decide soon or miss the window", config)
        assert _COMPILED[config.agent_id] is first
