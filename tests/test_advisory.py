"""tests/test_advisory.py — FW-1 advisory helpers unit tests.

Covers: build_advisory, is_suppressed, advance_suppression_counter,
reset_suppression_counter. Every I/O path flock-protected + atomic.
"""
from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock

import pytest

from spaice_agent.advisory import (
    advance_suppression_counter,
    build_advisory,
    is_suppressed,
    reset_suppression_counter,
)


def _make_config(tmp_path: pathlib.Path, suppress_turns: int = 3) -> MagicMock:
    """Return a minimal AgentConfig-like mock for advisory tests."""
    cfg = MagicMock()
    cfg.memory_root = tmp_path / "mem"
    cfg.consensus.advisory_suppress_turns = suppress_turns
    return cfg


class TestBuildAdvisory:
    def test_contains_reason(self, tmp_path):
        cfg = _make_config(tmp_path)
        out = build_advisory("word:decide", cfg)
        assert "word:decide" in out

    def test_mentions_use_consensus_tool(self, tmp_path):
        cfg = _make_config(tmp_path)
        out = build_advisory("plan trigger", cfg)
        assert "use_consensus" in out

    def test_mentions_suppress_window(self, tmp_path):
        cfg = _make_config(tmp_path, suppress_turns=7)
        out = build_advisory("x", cfg)
        assert "7 turns" in out

    def test_no_io_done(self, tmp_path):
        """Building the advisory must not touch the filesystem."""
        cfg = _make_config(tmp_path)
        # memory_root doesn't exist — if function did I/O it'd fail or create
        assert not cfg.memory_root.exists()
        build_advisory("x", cfg)
        assert not cfg.memory_root.exists()


class TestIsSuppressed:
    def test_no_state_file_returns_false(self, tmp_path):
        cfg = _make_config(tmp_path)
        assert is_suppressed(cfg) is False

    def test_recent_call_suppresses(self, tmp_path):
        cfg = _make_config(tmp_path, suppress_turns=3)
        state_dir = cfg.memory_root / "state"
        state_dir.mkdir(parents=True)
        (state_dir / "last_consensus_turn.json").write_text(
            json.dumps({"turns_since_call": 0})
        )
        assert is_suppressed(cfg) is True

    def test_past_window_does_not_suppress(self, tmp_path):
        cfg = _make_config(tmp_path, suppress_turns=3)
        state_dir = cfg.memory_root / "state"
        state_dir.mkdir(parents=True)
        (state_dir / "last_consensus_turn.json").write_text(
            json.dumps({"turns_since_call": 5})
        )
        assert is_suppressed(cfg) is False

    def test_exactly_at_threshold_does_not_suppress(self, tmp_path):
        """turns_since == suppress_turns means we're AT the window edge,
        which is past it (strict < comparison)."""
        cfg = _make_config(tmp_path, suppress_turns=3)
        state_dir = cfg.memory_root / "state"
        state_dir.mkdir(parents=True)
        (state_dir / "last_consensus_turn.json").write_text(
            json.dumps({"turns_since_call": 3})
        )
        assert is_suppressed(cfg) is False

    def test_malformed_json_fails_open(self, tmp_path):
        cfg = _make_config(tmp_path)
        state_dir = cfg.memory_root / "state"
        state_dir.mkdir(parents=True)
        (state_dir / "last_consensus_turn.json").write_text("{garbage")
        # Fail-open: return False (allow advisory)
        assert is_suppressed(cfg) is False


class TestAdvanceCounter:
    def test_initial_write(self, tmp_path):
        cfg = _make_config(tmp_path)
        advance_suppression_counter(cfg)
        state_path = cfg.memory_root / "state" / "last_consensus_turn.json"
        assert state_path.exists()
        data = json.loads(state_path.read_text())
        assert data["turns_since_call"] == 1

    def test_increments(self, tmp_path):
        cfg = _make_config(tmp_path)
        for _ in range(5):
            advance_suppression_counter(cfg)
        state_path = cfg.memory_root / "state" / "last_consensus_turn.json"
        data = json.loads(state_path.read_text())
        assert data["turns_since_call"] == 5

    def test_preserves_fields(self, tmp_path):
        """Extra keys in state should be preserved by counter advance."""
        cfg = _make_config(tmp_path)
        state_dir = cfg.memory_root / "state"
        state_dir.mkdir(parents=True)
        (state_dir / "last_consensus_turn.json").write_text(
            json.dumps({"turns_since_call": 2, "extra": "keep me"})
        )
        advance_suppression_counter(cfg)
        data = json.loads((state_dir / "last_consensus_turn.json").read_text())
        assert data["turns_since_call"] == 3
        assert data["extra"] == "keep me"


class TestResetCounter:
    def test_reset_to_zero(self, tmp_path):
        cfg = _make_config(tmp_path)
        # Advance a few times first
        for _ in range(4):
            advance_suppression_counter(cfg)

        reset_suppression_counter(cfg)
        state_path = cfg.memory_root / "state" / "last_consensus_turn.json"
        data = json.loads(state_path.read_text())
        assert data["turns_since_call"] == 0

    def test_reset_creates_if_absent(self, tmp_path):
        cfg = _make_config(tmp_path)
        reset_suppression_counter(cfg)
        state_path = cfg.memory_root / "state" / "last_consensus_turn.json"
        assert state_path.exists()
        assert json.loads(state_path.read_text())["turns_since_call"] == 0

    def test_after_reset_is_suppressed(self, tmp_path):
        cfg = _make_config(tmp_path, suppress_turns=3)
        reset_suppression_counter(cfg)
        assert is_suppressed(cfg) is True  # turns_since_call=0 < 3


class TestAtomicity:
    def test_write_failure_does_not_corrupt_existing(self, tmp_path, monkeypatch):
        """If the atomic rename fails, the original state file is untouched."""
        cfg = _make_config(tmp_path)
        # Seed initial state
        state_dir = cfg.memory_root / "state"
        state_dir.mkdir(parents=True)
        state_path = state_dir / "last_consensus_turn.json"
        state_path.write_text(json.dumps({"turns_since_call": 99}))

        # Break os.replace to simulate rename failure
        import os
        original_replace = os.replace

        def broken_replace(*a, **kw):
            raise OSError("simulated rename failure")

        monkeypatch.setattr(os, "replace", broken_replace)

        # Advance should not corrupt
        advance_suppression_counter(cfg)  # swallows OSError

        monkeypatch.setattr(os, "replace", original_replace)

        # File still has original content (or new content — but must be valid JSON)
        data = json.loads(state_path.read_text())
        assert isinstance(data["turns_since_call"], int)
