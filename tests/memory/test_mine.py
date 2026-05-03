"""Tests for spaice_agent.memory.mine."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import yaml

from spaice_agent.memory.mine import (
    Miner,
    MineReport,
    MineConfig,
    DEFAULT_SKIP_PREFIXES,
    STATE_FILENAME,
)
from spaice_agent.memory.classify import Classification


# -- fixtures ---------------------------------------------------------------


def _setup_agent(tmp_path, monkeypatch, agent_id="testbot"):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    vault = tmp_path / agent_id
    vault.mkdir()
    (vault / "_inbox").mkdir()
    agent_dir = tmp_path / ".spaice-agents" / agent_id
    agent_dir.mkdir(parents=True)
    # Use a fake session source under tmp_path
    sessions_dir = tmp_path / ".hermes" / "sessions"
    sessions_dir.mkdir(parents=True)
    (agent_dir / "config.yaml").write_text(yaml.safe_dump({
        "memory": {"mine": {"session_source": str(sessions_dir)}},
    }))
    return vault, sessions_dir


def _write_session(
    sessions_dir: Path,
    name: str,
    messages: list[dict],
    age_seconds: float = 0,
) -> Path:
    """Write a fake Hermes session JSON file."""
    path = sessions_dir / name
    path.write_text(json.dumps({"messages": messages}))
    if age_seconds:
        t = time.time() - age_seconds
        os.utime(path, (t, t))
    return path


def _classification(
    target: str = "sites/hanna.md",
    section: str = "Access",
    confidence: float = 0.9,
    used_fallback: bool = False,
) -> Classification:
    return Classification(
        target_file=target,
        section=section,
        dewey_layer="500",
        priority=2,
        rule_matched="test-rule",
        cross_references=(),
        confidence=confidence,
        reasoning="test reasoning",
        model_used="google/gemini-2.5-flash",
        used_fallback=used_fallback,
    )


# -- config loading ---------------------------------------------------------


def test_config_defaults_when_no_yaml():
    cfg = MineConfig.from_config_dict({})
    assert cfg.session_prefix == "session_"
    assert cfg.skip_session_prefixes == DEFAULT_SKIP_PREFIXES
    assert cfg.max_utterances_per_run == 50
    assert cfg.min_utterance_chars == 20


def test_config_custom_skip_prefixes():
    cfg = MineConfig.from_config_dict({
        "memory": {"mine": {"skip_session_prefixes": ["session_batch_", "ephemeral_"]}},
    })
    assert cfg.skip_session_prefixes == ("session_batch_", "ephemeral_")


def test_config_rejects_non_list_skip_prefixes():
    with pytest.raises(ValueError, match="list"):
        MineConfig.from_config_dict({
            "memory": {"mine": {"skip_session_prefixes": "not-a-list"}},
        })


# -- session discovery ------------------------------------------------------


def test_sessions_since_respects_cutoff(tmp_path, monkeypatch):
    vault, sessions_dir = _setup_agent(tmp_path, monkeypatch)
    _write_session(sessions_dir, "session_recent.json", [
        {"role": "user", "content": "We need Lockwood ES9100 for the job"},
    ])
    _write_session(sessions_dir, "session_old.json", [
        {"role": "user", "content": "Old content"},
    ], age_seconds=86400 * 7)  # 7 days old

    miner = Miner.for_agent("testbot")
    with patch.object(miner, "_ensure_classifier") as mock_cls:
        mock_cls.return_value.classify.return_value = _classification()
        report = miner.run(since=timedelta(hours=6))

    # Only the recent session is mined
    assert report.sessions_scanned == 1


def test_cron_sessions_skipped(tmp_path, monkeypatch):
    vault, sessions_dir = _setup_agent(tmp_path, monkeypatch)
    _write_session(sessions_dir, "session_cron_abc.json", [
        {"role": "user", "content": "Cron prompt content"},
    ])
    _write_session(sessions_dir, "session_normal.json", [
        {"role": "user", "content": "Lockwood ES9100 order"},
    ])

    miner = Miner.for_agent("testbot")
    with patch.object(miner, "_ensure_classifier") as mock_cls:
        mock_cls.return_value.classify.return_value = _classification()
        report = miner.run(since=timedelta(hours=24))

    assert report.sessions_scanned == 1  # only the normal one


# -- utterance extraction --------------------------------------------------


def test_extracts_user_utterances_only(tmp_path, monkeypatch):
    vault, sessions_dir = _setup_agent(tmp_path, monkeypatch)
    _write_session(sessions_dir, "session_a.json", [
        {"role": "system", "content": "You are..."},
        {"role": "user", "content": "Lockwood ES9100 for Hanna"},
        {"role": "assistant", "content": "Got it"},
        {"role": "user", "content": "x"},  # too short
    ])

    miner = Miner.for_agent("testbot")
    utts = miner._extract_user_utterances(sessions_dir / "session_a.json")
    # Both the short "x" (< min_chars) and the long one
    assert len(utts) == 1
    assert "Lockwood" in utts[0].content


def test_utterances_skipped_by_pattern(tmp_path, monkeypatch):
    vault, sessions_dir = _setup_agent(tmp_path, monkeypatch)
    _write_session(sessions_dir, "session_a.json", [
        {"role": "user", "content": "[CONTEXT COMPACTION — REFERENCE ONLY] something"},
        {"role": "user", "content": "[IMPORTANT: You are running as a scheduled cron mining pass]"},
        {"role": "user", "content": "Lockwood ES9100 for Hanna - real content"},
    ])
    miner = Miner.for_agent("testbot")
    utts = miner._extract_user_utterances(sessions_dir / "session_a.json")
    assert len(utts) == 1
    assert "Lockwood" in utts[0].content


def test_utterances_truncated_to_max_chars(tmp_path, monkeypatch):
    vault, sessions_dir = _setup_agent(tmp_path, monkeypatch)
    long_content = "Lockwood ES9100 " + ("x" * 8000)
    _write_session(sessions_dir, "session_a.json", [
        {"role": "user", "content": long_content},
    ])
    miner = Miner.for_agent("testbot")
    utts = miner._extract_user_utterances(sessions_dir / "session_a.json")
    assert len(utts[0].content) == miner.config.max_utterance_chars


# -- signal detection ------------------------------------------------------


def test_fact_signal_detected(tmp_path, monkeypatch):
    _setup_agent(tmp_path, monkeypatch)
    miner = Miner.for_agent("testbot")
    # Should match vendor+SKU pattern (Bosch PIR-2036)
    assert miner._has_fileable_signal("Bosch PIR-2036 for Hanna")
    # Currency
    assert miner._has_fileable_signal("The quote came back at $12,500.00")
    # Decision verb
    assert miner._has_fileable_signal("That's confirmed — go ahead and order")


def test_no_signal_skipped(tmp_path, monkeypatch):
    _setup_agent(tmp_path, monkeypatch)
    miner = Miner.for_agent("testbot")
    # Bland chat
    assert not miner._has_fileable_signal("sure thing, let me know when ready")


# -- end-to-end run --------------------------------------------------------


def test_run_end_to_end(tmp_path, monkeypatch):
    vault, sessions_dir = _setup_agent(tmp_path, monkeypatch)
    _write_session(sessions_dir, "session_a.json", [
        {"role": "user", "content": "Bosch PIR-2036 confirmed for Hanna"},
    ])

    miner = Miner.for_agent("testbot")
    with patch.object(miner, "_ensure_classifier") as mock_cls:
        mock_cls.return_value.classify.return_value = _classification()
        report = miner.run()

    assert report.sessions_scanned == 1
    assert report.candidates_found == 1
    assert report.facts_filed == 1
    # Inbox has one draft
    inbox_files = list((vault / "_inbox").glob("*.md"))
    assert len(inbox_files) == 1
    content = inbox_files[0].read_text()
    assert "classifier_target: sites/hanna.md" in content
    assert "Bosch PIR-2036" in content


def test_dry_run_no_side_effects(tmp_path, monkeypatch):
    vault, sessions_dir = _setup_agent(tmp_path, monkeypatch)
    _write_session(sessions_dir, "session_a.json", [
        {"role": "user", "content": "Bosch PIR-2036 confirmed"},
    ])

    miner = Miner.for_agent("testbot")
    with patch.object(miner, "_ensure_classifier") as mock_cls:
        mock_cls.return_value.classify.return_value = _classification()
        report = miner.run(dry_run=True)

    assert report.candidates_found == 1
    assert report.facts_filed == 0  # dry run
    # No inbox files written
    assert list((vault / "_inbox").glob("*.md")) == []
    # No state file written
    assert not (vault / "_inbox" / STATE_FILENAME).exists()


# -- state / idempotency --------------------------------------------------


def test_state_file_created_on_run(tmp_path, monkeypatch):
    vault, sessions_dir = _setup_agent(tmp_path, monkeypatch)
    _write_session(sessions_dir, "session_a.json", [
        {"role": "user", "content": "Bosch PIR-2036 confirmed"},
    ])
    miner = Miner.for_agent("testbot")
    with patch.object(miner, "_ensure_classifier") as mock_cls:
        mock_cls.return_value.classify.return_value = _classification()
        miner.run()

    state_file = vault / "_inbox" / STATE_FILENAME
    assert state_file.exists()
    state = json.loads(state_file.read_text())
    assert "mined_sessions" in state
    assert "session_a.json" in state["mined_sessions"]
    assert state["last_run_filed"] == 1


def test_state_skips_already_mined_sessions(tmp_path, monkeypatch):
    vault, sessions_dir = _setup_agent(tmp_path, monkeypatch)
    session_path = _write_session(sessions_dir, "session_a.json", [
        {"role": "user", "content": "Bosch PIR-2036 confirmed"},
    ])
    # Pre-populate state saying we've already processed this size
    state_file = vault / "_inbox" / STATE_FILENAME
    state_file.write_text(json.dumps({
        "mined_sessions": {"session_a.json": session_path.stat().st_size},
        "last_run": None,
    }))

    miner = Miner.for_agent("testbot")
    with patch.object(miner, "_ensure_classifier") as mock_cls:
        mock_cls.return_value.classify.return_value = _classification()
        report = miner.run()

    # Session not re-mined
    assert report.candidates_found == 0
    assert report.facts_filed == 0


def test_corrupt_state_file_backed_up(tmp_path, monkeypatch):
    vault, sessions_dir = _setup_agent(tmp_path, monkeypatch)
    state_file = vault / "_inbox" / STATE_FILENAME
    state_file.write_text("not valid json {[")

    miner = Miner.for_agent("testbot")
    # Trigger state load via run()
    with patch.object(miner, "_ensure_classifier") as mock_cls:
        mock_cls.return_value.classify.return_value = _classification()
        miner.run()

    # Corrupt file was backed up
    backup = state_file.with_suffix(".json.corrupt")
    assert backup.exists()


# -- error handling --------------------------------------------------------


def test_classifier_failure_recorded_in_errors(tmp_path, monkeypatch):
    vault, sessions_dir = _setup_agent(tmp_path, monkeypatch)
    _write_session(sessions_dir, "session_a.json", [
        {"role": "user", "content": "Bosch PIR-2036 confirmed for Hanna"},
    ])

    from spaice_agent.memory.classify import ClassifierAPIError

    miner = Miner.for_agent("testbot")
    with patch.object(miner, "_ensure_classifier") as mock_cls:
        mock_cls.return_value.classify.side_effect = ClassifierAPIError("rate limit")
        report = miner.run()

    assert report.facts_filed == 0
    assert len(report.errors) == 1
    assert "rate limit" in report.errors[0]


def test_session_read_error_skipped(tmp_path, monkeypatch):
    vault, sessions_dir = _setup_agent(tmp_path, monkeypatch)
    (sessions_dir / "session_broken.json").write_text("not valid json [[{")
    miner = Miner.for_agent("testbot")
    with patch.object(miner, "_ensure_classifier") as mock_cls:
        mock_cls.return_value.classify.return_value = _classification()
        report = miner.run()
    # Broken session doesn't crash the run
    assert report.sessions_scanned == 1
    assert report.candidates_found == 0


# -- caps ------------------------------------------------------------------


def test_max_utterances_cap_enforced(tmp_path, monkeypatch):
    vault, sessions_dir = _setup_agent(tmp_path, monkeypatch)
    messages = [
        {"role": "user", "content": f"Lockwood ES{9100 + i} confirmed order"}
        for i in range(20)
    ]
    _write_session(sessions_dir, "session_a.json", messages)

    miner = Miner.for_agent("testbot")
    with patch.object(miner, "_ensure_classifier") as mock_cls:
        mock_cls.return_value.classify.return_value = _classification()
        report = miner.run(max_utterances=5)

    assert report.candidates_found == 5
    assert report.facts_filed == 5


# -- low-confidence tracking ----------------------------------------------


def test_low_confidence_counted(tmp_path, monkeypatch):
    vault, sessions_dir = _setup_agent(tmp_path, monkeypatch)
    _write_session(sessions_dir, "session_a.json", [
        {"role": "user", "content": "Bosch PIR-2036 mentioned in passing"},
    ])
    miner = Miner.for_agent("testbot")
    with patch.object(miner, "_ensure_classifier") as mock_cls:
        mock_cls.return_value.classify.return_value = _classification(confidence=0.2)
        report = miner.run()

    assert report.facts_filed == 1  # still filed, just marked
    assert report.low_confidence_count == 1


# -- regression guards from Codex Phase 1B review 2026-05-03 ---------------


def test_multimodal_content_extracts_text_parts(tmp_path, monkeypatch):
    """Regression: Codex mine #7 — list-type content was JSON-dumped.

    Multimodal messages arrive as [{"type":"text","text":...},
    {"type":"image",...}]. Previously these were json.dumps'd, producing
    garbage that broke fact pattern matching. Must extract only text parts.
    """
    vault, sessions_dir = _setup_agent(tmp_path, monkeypatch)
    _write_session(sessions_dir, "session_a.json", [
        {"role": "user", "content": [
            {"type": "text", "text": "Bosch PIR-2036 confirmed"},
            {"type": "image", "url": "..."},
            {"type": "text", "text": "for the Hanna job"},
        ]},
    ])

    miner = Miner.for_agent("testbot")
    with patch.object(miner, "_ensure_classifier") as mock_cls:
        mock_cls.return_value.classify.return_value = _classification()
        report = miner.run()

    assert report.candidates_found == 1
    assert report.facts_filed == 1
    # The draft should contain the actual text
    drafts = list((vault / "_inbox").glob("*.md"))
    assert len(drafts) == 1
    content = drafts[0].read_text()
    assert "Bosch PIR-2036 confirmed" in content
    assert "Hanna job" in content


def test_user_fact_patterns_from_config_applied(tmp_path, monkeypatch):
    """Regression: Codex mine #5 — fact_patterns config was declared but not loaded."""
    vault, sessions_dir = _setup_agent(tmp_path, monkeypatch)
    # Override config with custom fact pattern
    agent_dir = tmp_path / ".spaice-agents" / "testbot"
    (agent_dir / "config.yaml").write_text(yaml.safe_dump({
        "memory": {"mine": {
            "session_source": str(sessions_dir),
            "fact_patterns": [r"\bCUSTOM-[0-9]+\b"],  # custom ticket ID pattern
        }},
    }))
    # Write a session with content only the custom pattern would catch
    _write_session(sessions_dir, "session_a.json", [
        {"role": "user", "content": "Ticket CUSTOM-2036 needs review"},
    ])

    miner = Miner.for_agent("testbot")
    assert len(miner.fact_patterns) == len(
        __import__("spaice_agent.memory.mine", fromlist=["DEFAULT_FACT_PATTERNS"]).DEFAULT_FACT_PATTERNS
    ) + 1

    with patch.object(miner, "_ensure_classifier") as mock_cls:
        mock_cls.return_value.classify.return_value = _classification()
        report = miner.run()

    # Without the custom pattern, "Ticket CUSTOM-2036" wouldn't match any
    # default (no vendor name, no $, etc.) — but now it does
    assert report.candidates_found == 1


def test_invalid_fact_pattern_rejected_at_config_load(tmp_path, monkeypatch):
    """Regression: Codex mine #5 — bad regex must fail loud, not on first cron fire."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / "testbot").mkdir()
    (tmp_path / "testbot" / "_inbox").mkdir()
    agent_dir = tmp_path / ".spaice-agents" / "testbot"
    agent_dir.mkdir(parents=True)
    (agent_dir / "config.yaml").write_text(yaml.safe_dump({
        "memory": {"mine": {"fact_patterns": ["[unclosed"]}},
    }))
    with pytest.raises(ValueError, match="invalid regex"):
        Miner.for_agent("testbot")
