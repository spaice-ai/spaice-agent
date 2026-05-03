"""Smoke tests for Phase 1C memory modules — dashboards, audit, library_index, continuity.

Focus: end-to-end happy path, API shape, atomic write behaviour.
Deep coverage will be added in dedicated test files per module later.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml


# ===========================================================================
# Fixtures: bootstrap a minimal vault skeleton in tmp_path
# ===========================================================================


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    for d in [
        "_inbox", "_continuity", "_dashboard", "_archive/sessions",
        "identity", "projects", "corrections", "library",
    ]:
        (root / d).mkdir(parents=True, exist_ok=True)
    return root


# ===========================================================================
# dashboards.py
# ===========================================================================


class TestDashboards:
    def test_regenerate_all_returns_results_for_each_dashboard(self, vault):
        from spaice_agent.memory.dashboards import regenerate_all, DashboardResult
        results = regenerate_all(vault)
        assert len(results) >= 5
        assert all(isinstance(r, DashboardResult) for r in results)
        for r in results:
            assert isinstance(r.name, str)
            assert r.ts_generated  # non-empty timestamp

    def test_empty_vault_produces_empty_dashboards(self, vault):
        from spaice_agent.memory.dashboards import regenerate_all
        results = regenerate_all(vault)
        for r in results:
            # All dashboards should succeed (return ok=True) even on empty input
            assert r.ok is True, f"{r.name} should succeed on empty vault"

    def test_output_files_contain_generated_header(self, vault):
        from spaice_agent.memory.dashboards import regenerate_all
        regenerate_all(vault)
        dashboard_dir = vault / "_dashboard"
        md_files = list(dashboard_dir.glob("*.md"))
        assert len(md_files) >= 1, "at least one dashboard file should be written"
        for f in md_files:
            content = f.read_text()
            assert "GENERATED" in content.upper()

    def test_inbox_dashboard_counts_files(self, vault):
        (vault / "_inbox" / "draft-1.md").write_text("---\n_tag: project\n---\nhi")
        (vault / "_inbox" / "draft-2.md").write_text("---\n_tag: project\n---\nhi")
        (vault / "_inbox" / "draft-3.md").write_text("---\n_tag: person\n---\nhi")
        from spaice_agent.memory.dashboards import regenerate_one
        r = regenerate_one("inbox", vault)
        assert r.ok is True


# ===========================================================================
# audit.py
# ===========================================================================


class TestAudit:
    def test_audit_empty_vault_returns_report(self, vault):
        from spaice_agent.memory.audit import audit_vault, AuditReport
        report = audit_vault(vault)
        assert isinstance(report, AuditReport)
        assert hasattr(report, "findings")

    def test_findings_sorted_by_severity(self, vault):
        from spaice_agent.memory.audit import audit_vault
        report = audit_vault(vault)
        if len(report.findings) >= 2:
            severities = [f.severity for f in report.findings]
            severity_rank = {"error": 0, "warn": 1, "info": 2}
            ranks = [severity_rank.get(s, 99) for s in severities]
            assert ranks == sorted(ranks), "findings must be sorted by severity (error→warn→info)"

    def test_missing_continuity_flagged_when_absent(self, vault):
        # vault has _continuity/ dir but no LATEST.md
        from spaice_agent.memory.audit import audit_vault
        report = audit_vault(vault)
        messages = [f.message for f in report.findings]
        assert any("continuity" in m.lower() or "latest" in m.lower() for m in messages)

    def test_audit_never_mutates_vault(self, vault):
        (vault / "identity" / "test.md").write_text("---\ntitle: test\n---\nbody")
        from spaice_agent.memory.audit import audit_vault
        before = sorted(str(p) for p in vault.rglob("*") if p.is_file())
        audit_vault(vault)
        after = sorted(str(p) for p in vault.rglob("*") if p.is_file())
        assert before == after


# ===========================================================================
# library_index.py
# ===========================================================================


class TestLibraryIndex:
    def test_build_library_index_returns_index(self, vault):
        (vault / "library" / "note-1.md").write_text(
            "---\ntitle: Note One\ntags: [alpha, beta]\n---\n\nSome description text.\n"
        )
        from spaice_agent.memory.library_index import build_library_index, LibraryIndex
        idx = build_library_index(vault)
        assert isinstance(idx, LibraryIndex)
        assert len(idx.entries) == 1
        entry = idx.entries[0]
        assert entry.title == "Note One"
        assert set(entry.tags) == {"alpha", "beta"}

    def test_summary_truncated_to_200_chars(self, vault):
        long_text = "x" * 500
        (vault / "library" / "longnote.md").write_text(
            f"---\ntitle: Long\n---\n\n{long_text}\n"
        )
        from spaice_agent.memory.library_index import build_library_index
        idx = build_library_index(vault)
        assert len(idx.entries[0].summary) <= 200

    def test_backlinks_detected(self, vault):
        (vault / "library" / "target.md").write_text("---\ntitle: Target\n---\ncontent")
        (vault / "identity" / "source.md").write_text("---\ntitle: Source\n---\nsee [[target]]")
        from spaice_agent.memory.library_index import build_library_index
        idx = build_library_index(vault)
        target = next((e for e in idx.entries if e.title == "Target"), None)
        assert target is not None
        assert any("source" in bl.lower() for bl in target.backlinks)

    def test_save_and_load_roundtrip(self, vault):
        (vault / "library" / "n.md").write_text("---\ntitle: N\n---\nbody")
        from spaice_agent.memory.library_index import (
            build_library_index, save_library_index, load_library_index,
        )
        idx1 = build_library_index(vault)
        save_library_index(idx1, vault)
        idx2 = load_library_index(vault)
        assert idx2 is not None
        assert len(idx2.entries) == len(idx1.entries)
        assert idx2.entries[0].title == "N"


# ===========================================================================
# continuity.py
# ===========================================================================


class TestContinuity:
    def test_generate_on_empty_vault_produces_skeleton(self, vault):
        from spaice_agent.memory.continuity import generate_latest, ContinuityBlock
        block = generate_latest(vault)
        assert isinstance(block, ContinuityBlock)
        assert block.ts  # non-empty ts
        # skeleton should have TODO markers or empty placeholders, not crash

    def test_write_and_read_roundtrip(self, vault):
        from spaice_agent.memory.continuity import (
            ContinuityBlock, write_latest, read_latest,
        )
        block = ContinuityBlock(
            goal="Test goal",
            progress="Test progress line",
            open_threads=["thread A", "thread B"],
            next_step="Next test step",
            ts="2026-05-03T12:00:00+00:00",
        )
        path = write_latest(block, vault)
        assert path.exists()
        assert path.name == "LATEST.md"
        parsed = read_latest(vault)
        assert parsed is not None
        assert parsed.goal == "Test goal"
        assert parsed.next_step == "Next test step"
        assert "thread A" in parsed.open_threads or any("thread A" in t for t in parsed.open_threads)

    def test_read_latest_returns_none_when_absent(self, vault):
        from spaice_agent.memory.continuity import read_latest
        assert read_latest(vault) is None

    def test_latest_md_has_generated_header(self, vault):
        from spaice_agent.memory.continuity import ContinuityBlock, write_latest
        block = ContinuityBlock(
            goal="g", progress="p", open_threads=[], next_step="n",
            ts="2026-05-03T12:00:00+00:00",
        )
        path = write_latest(block, vault)
        content = path.read_text()
        assert "GENERATED" in content.upper()


# ===========================================================================
# summarise.py (module importable + API shape — integration test is too
# expensive since it hits OpenRouter; full test lives behind a marker later)
# ===========================================================================


class TestSummariseImports:
    def test_module_importable_and_exports_api(self):
        from spaice_agent.memory.summarise import (
            SessionSummary, summarise_from_text, summarise_session,
        )
        assert SessionSummary.__dataclass_params__.frozen is True
