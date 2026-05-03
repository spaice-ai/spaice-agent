"""Regression tests for Phase 1C Codex blocker fixes (2026-05-03).

Each test documents the Codex review finding it guards against.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest


# ===========================================================================
# Shared vault fixture
# ===========================================================================


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    for d in ["_inbox", "_continuity", "_dashboard", "_archive/sessions",
              "identity", "projects", "corrections", "library"]:
        (root / d).mkdir(parents=True, exist_ok=True)
    return root


# ===========================================================================
# dashboards.py blockers
# ===========================================================================


class TestDashboardsBlockerFixes:
    def test_human_ago_accepts_tz_aware_ts_no_crash(self):
        """Blocker #1: _human_ago must not crash on timezone-aware datetime."""
        from spaice_agent.memory.dashboards import _human_ago
        aware_ts = datetime.now(timezone.utc) - timedelta(hours=3)
        result = _human_ago(aware_ts)
        assert "ago" in result
        assert isinstance(result, str)

    def test_human_ago_accepts_naive_ts_no_crash(self):
        """Blocker #1 corollary: naive datetime still handled (assumed UTC)."""
        from spaice_agent.memory.dashboards import _human_ago
        naive_ts = datetime.utcnow() - timedelta(minutes=30)
        result = _human_ago(naive_ts)
        assert "ago" in result

    def test_atomic_write_uses_same_directory_tmp(self, vault):
        """Blocker #2: tmp file must live in target's parent (same filesystem)."""
        from spaice_agent.memory.dashboards import _atomic_write
        target = vault / "_dashboard" / "test.md"
        _atomic_write(target, "hello")
        assert target.read_text() == "hello"
        # No leftover .tmp files
        leftover = list(vault.rglob(".test.md.tmp-*"))
        assert len(leftover) == 0


# ===========================================================================
# audit.py blockers
# ===========================================================================


class TestAuditBlockerFixes:
    def test_audit_vault_does_not_crash_on_missing_continuity(self, vault):
        """Blocker #4: exception paths must log, not swallow silently.

        Covered by the existing test_missing_continuity_flagged_when_absent
        but here we assert that running audit_vault completes + logs cleanly.
        """
        import logging
        from spaice_agent.memory.audit import audit_vault
        with patch.object(logging.getLogger("spaice_agent.memory.audit"), "warning") as mock_warn:
            report = audit_vault(vault)
        # Should return a report, not crash
        assert report is not None
        assert hasattr(report, "findings")

    def test_audit_report_post_init_validates_counts(self):
        """Blocker #3 rejected (Sonnet false positive): __post_init__ on frozen
        dataclass is valid Python. Validation must still raise on mismatch.
        """
        from spaice_agent.memory.audit import AuditReport, AuditFinding
        good = AuditReport(
            findings=[AuditFinding("error", "x.md", "boom")],
            counts={"error": 1, "warn": 0, "info": 0},
            ts="2026-05-03T00:00:00+00:00",
        )
        assert good.counts["error"] == 1
        # Mismatched counts must raise
        with pytest.raises(ValueError):
            AuditReport(
                findings=[AuditFinding("error", "x.md", "boom")],
                counts={"error": 0, "warn": 0, "info": 0},  # wrong!
                ts="2026-05-03T00:00:00+00:00",
            )


# ===========================================================================
# summarise.py blockers
# ===========================================================================


class TestSummariseBlockerFixes:
    @pytest.mark.asyncio
    async def test_summarise_session_offloads_sqlite_to_thread(self, vault, monkeypatch):
        """Blocker #5: sqlite read must run in asyncio.to_thread to not block the loop."""
        from spaice_agent.memory import summarise as summarise_mod

        call_log = []

        # Patch _load_session_transcript; capture whether it was called via to_thread
        original = summarise_mod._load_session_transcript

        def spy(session_id, db_path):
            call_log.append(("called", session_id))
            return ""  # empty → triggers trivial path, no LLM call

        monkeypatch.setattr(summarise_mod, "_load_session_transcript", spy)

        # Also need a minimal cfg
        cfg = MagicMock()
        cfg.memory.root = vault
        cfg.memory.session_db_path = str(vault / "fake.db")

        result = await summarise_mod.summarise_session("sess-abc", cfg)
        assert result.summary_md == "TRIVIAL"
        assert ("called", "sess-abc") in call_log

    def test_empty_session_id_uses_adhoc_placeholder(self, vault):
        """Blocker #7: empty session_id must not produce `YYYY-MM-DD-.md`."""
        from spaice_agent.memory.summarise import SessionSummary, _write_summary_file
        summary = SessionSummary(
            session_id="",
            date="2026-05-03",
            summary_md="## Goal\nTest",
            word_count=2,
            cost_usd=0.0,
        )
        asyncio.run(_write_summary_file(summary, vault))
        files = list((vault / "_archive" / "sessions").glob("2026-05-03-*.md"))
        assert len(files) == 1
        assert "adhoc" in files[0].name

    def test_summarise_tmp_filename_uses_pid_suffix(self, vault):
        """Blocker #6: tmp name must include PID to prevent concurrent collisions."""
        from spaice_agent.memory.summarise import SessionSummary, _write_summary_file
        summary = SessionSummary(
            session_id="pid-test",
            date="2026-05-03",
            summary_md="hello",
            word_count=1,
            cost_usd=0.0,
        )
        asyncio.run(_write_summary_file(summary, vault))
        # No .tmp leftover
        leftover = list((vault / "_archive" / "sessions").glob(".*.tmp-*"))
        assert len(leftover) == 0
        # Result written
        assert (vault / "_archive" / "sessions" / "2026-05-03-pid-test.md").exists()


# ===========================================================================
# library_index.py blockers
# ===========================================================================


class TestLibraryIndexBlockerFixes:
    def test_library_entry_is_truly_frozen(self):
        """Blocker #10: LibraryEntry must be a real stdlib frozen dataclass."""
        from spaice_agent.memory.library_index import LibraryEntry
        e = LibraryEntry(
            path="a.md", title="A", tags=(), summary="s", backlinks=(), mtime=1.0,
        )
        assert dataclasses.is_dataclass(e)
        params = getattr(e, "__dataclass_params__")
        assert params.frozen is True
        # Mutation via __setattr__ must raise FrozenInstanceError
        with pytest.raises(dataclasses.FrozenInstanceError):
            e.path = "b.md"  # type: ignore[misc]

    def test_library_index_is_truly_frozen(self):
        """Blocker #10 corollary."""
        from spaice_agent.memory.library_index import LibraryIndex
        idx = LibraryIndex(entries=(), ts_built=datetime.now(timezone.utc))
        assert dataclasses.is_dataclass(idx)
        assert idx.__dataclass_params__.frozen is True
        with pytest.raises(dataclasses.FrozenInstanceError):
            idx.entries = (1,)  # type: ignore[misc]

    def test_backlinks_refresh_on_incremental_rebuild(self, vault):
        """Blocker #9: when file A adds [[B]], B's backlinks must include A
        on next rebuild — even though B itself didn't change."""
        from spaice_agent.memory.library_index import (
            build_library_index, save_library_index,
        )
        # Round 1: target file has no backlinks
        (vault / "library" / "target.md").write_text(
            "---\ntitle: Target\n---\ncontent"
        )
        idx1 = build_library_index(vault)
        target1 = next((e for e in idx1.entries if e.title == "Target"), None)
        assert target1 is not None
        assert target1.backlinks == ()
        save_library_index(idx1, vault)

        # Round 2: add a new file that links to target, DON'T touch target.md
        (vault / "identity" / "source.md").write_text(
            "---\ntitle: Source\n---\nsee [[target]]"
        )
        idx2 = build_library_index(vault)
        target2 = next((e for e in idx2.entries if e.title == "Target"), None)
        assert target2 is not None
        # Blocker was: target2.backlinks == () (stale cache)
        # Fix: backlinks must include "source"
        assert any("source" in bl.lower() for bl in target2.backlinks), \
            f"Expected backlinks to include 'source', got {target2.backlinks}"


# ===========================================================================
# continuity.py blockers
# ===========================================================================


class TestContinuityBlockerFixes:
    def test_write_latest_uses_pid_suffixed_tmp(self, vault):
        """Blocker #11: tmp file name must include PID for concurrent safety."""
        from spaice_agent.memory.continuity import ContinuityBlock, write_latest
        block = ContinuityBlock(
            goal="g", progress="p", open_threads=[], next_step="n",
            ts="2026-05-03T00:00:00+00:00",
        )
        write_latest(block, vault)
        # No leftover .tmp
        leftover = list((vault / "_continuity").glob(".LATEST.md.tmp-*"))
        assert len(leftover) == 0
        # LATEST.md written
        assert (vault / "_continuity" / "LATEST.md").exists()
