"""Regression tests for Phase 1C Codex 5.3 retroactive review findings.

Each test is named for the blocker/major it guards against. These guard
regressions that the original Phase 1C smoke tests + Sonnet-pass blocker
tests did NOT cover but Codex 5.3 caught on re-review.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
from pathlib import Path

import pytest
import yaml


# ---------------------------------------------------------------------------
# audit.py — Codex 5.3 blockers #1, #2, #3, #4
# ---------------------------------------------------------------------------


def test_audit_orphaned_inbox_skips_relative_to_vault_not_absolute(tmp_path):
    """Codex 5.3 audit-major #4: hidden-file filter must be relative to vault_root.

    If vault lives under an absolute path with a dot-prefixed ancestor
    (e.g. ~/.config/vault/), the old absolute-parts filter would skip every
    inbox file. Fix checks parts relative to vault_root.
    """
    from spaice_agent.memory.audit import check_orphaned_inbox

    # Simulate a vault under a dot-prefixed ancestor
    hidden_ancestor = tmp_path / ".hidden_ancestor" / "vault"
    hidden_ancestor.mkdir(parents=True)
    inbox = hidden_ancestor / "_inbox"
    inbox.mkdir()

    old_note = inbox / "orphan.md"
    old_note.write_text("old")
    # Backdate mtime > 7 days
    import os
    import time

    old_ts = time.time() - 8 * 86400
    os.utime(old_note, (old_ts, old_ts))

    findings = check_orphaned_inbox(hidden_ancestor)
    # Should find the orphan; old code would SKIP it because of .hidden_ancestor
    assert any("orphan.md" in f.path for f in findings), (
        "Orphan note must be detected even under dot-prefixed ancestor; "
        "filter must be relative to vault_root."
    )


def test_audit_vault_error_handler_uses_correct_loop_var(tmp_path, monkeypatch):
    """Codex 5.3 audit-blocker #1: error handler referenced undefined check_name.

    Force a check to raise and verify audit_vault returns an AuditReport
    instead of propagating NameError.
    """
    from spaice_agent.memory import audit as audit_mod
    from spaice_agent.memory.audit import audit_vault

    (tmp_path / "identity").mkdir()
    (tmp_path / "_continuity").mkdir()
    (tmp_path / "_continuity" / "LATEST.md").write_text("x")

    def _boom(_):
        raise RuntimeError("synthetic failure for regression test")

    # Patch the CHECKS dict entry — the dict holds a reference captured at
    # import time, not the module attribute.
    original_checks = dict(audit_mod.CHECKS)
    audit_mod.CHECKS["orphaned_inbox"] = _boom
    try:
        report = audit_vault(tmp_path)  # must not raise NameError
    finally:
        audit_mod.CHECKS.clear()
        audit_mod.CHECKS.update(original_checks)
    # The boom check should be recorded as an internal-error finding
    assert any("Internal error" in f.message for f in report.findings), (
        "audit_vault must surface an internal-error finding when a check "
        "raises, not crash with NameError."
    )


def test_audit_frontmatter_except_path_is_clean(tmp_path):
    """Codex 5.3 audit-blocker #2: except path referenced undefined `f`.

    Create an unreadable file (permission 000) and verify check_missing_frontmatter
    doesn't crash with NameError.
    """
    import os
    import stat

    from spaice_agent.memory.audit import check_missing_frontmatter

    identity = tmp_path / "identity"
    identity.mkdir()
    bad_file = identity / "unreadable.md"
    bad_file.write_text("content")

    # On POSIX, chmod 000 so open raises OSError
    try:
        os.chmod(bad_file, 0)
        # Must not raise NameError — previous code referenced undefined `f`
        findings = check_missing_frontmatter(tmp_path)
        # Either we got back a list cleanly or we skipped — both fine
        assert isinstance(findings, list)
    finally:
        os.chmod(bad_file, stat.S_IRUSR | stat.S_IWUSR)


def test_audit_wikilinks_except_path_is_clean(tmp_path):
    """Codex 5.3 audit-blocker #3: except path referenced undefined `md`.

    Same shape as frontmatter regression but for broken-wikilink check.
    """
    import os
    import stat

    from spaice_agent.memory.audit import check_broken_wikilinks

    projects = tmp_path / "projects"
    projects.mkdir()
    bad_file = projects / "unreadable.md"
    bad_file.write_text("# [[nonexistent]]")

    try:
        os.chmod(bad_file, 0)
        findings = check_broken_wikilinks(tmp_path)
        assert isinstance(findings, list)
    finally:
        os.chmod(bad_file, stat.S_IRUSR | stat.S_IWUSR)


# ---------------------------------------------------------------------------
# dashboards.py — Codex 5.3 major #1 (section boundary)
# ---------------------------------------------------------------------------


def test_dashboards_continuity_next_step_stops_at_next_section(tmp_path):
    """Codex 5.3 dashboards-major #1: empty Next step must not bleed into Notes."""
    from spaice_agent.memory.dashboards import _gen_continuity

    cont_dir = tmp_path / "_continuity"
    cont_dir.mkdir()
    latest = cont_dir / "LATEST.md"
    latest.write_text(
        "# Session latest\n\n"
        "## Next step\n"
        "\n"
        "## Notes\n"
        "This should NOT be picked as the next step.\n",
        encoding="utf-8",
    )

    rows = _gen_continuity(tmp_path)
    assert rows, "must return at least one row"
    next_step = rows[0]["next_step"]
    assert "should NOT" not in next_step, (
        "empty Next step section must not bleed into Notes content"
    )
    assert next_step == "—", (
        f"empty Next step should render as em-dash, got {next_step!r}"
    )


# ---------------------------------------------------------------------------
# Tmp-collision (4-file shared) — Codex 5.3 majors #2 / continuity #1 / ...
# ---------------------------------------------------------------------------


def test_dashboards_atomic_write_tmp_is_unique_per_call(tmp_path):
    """Codex 5.3 dashboards-major #2: concurrent same-PID writes must not collide.

    Invoke _atomic_write from two threads targeting the same file; both must
    succeed without either seeing a 'missing tmp' error.
    """
    from spaice_agent.memory.dashboards import _atomic_write

    target = tmp_path / "out.md"
    errors: list[Exception] = []

    def worker(tag: str):
        try:
            for i in range(20):
                _atomic_write(target, f"thread {tag} iter {i}")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in ("A", "B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent _atomic_write raised: {errors}"
    assert target.exists()
    # Final content comes from one of the threads — either is valid
    assert target.read_text().startswith("thread ")


def test_continuity_write_latest_tmp_is_unique_per_call(tmp_path):
    """Codex 5.3 continuity-blocker #1: concurrent write_latest must not collide."""
    from spaice_agent.memory.continuity import ContinuityBlock, write_latest

    errors: list[Exception] = []

    def worker(tag: str):
        try:
            for i in range(15):
                block = ContinuityBlock(
                    goal=f"goal {tag}",
                    progress=f"p {i}",
                    open_threads=[],
                    next_step=f"step {i}",
                    ts="2026-05-03T00:00:00+00:00",
                )
                write_latest(block, tmp_path)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in ("A", "B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent write_latest raised: {errors}"
    assert (tmp_path / "_continuity" / "LATEST.md").exists()


# ---------------------------------------------------------------------------
# summarise.py — Codex 5.3 blocker #1, major #2
# ---------------------------------------------------------------------------


def test_summarise_transcript_skips_non_dict_entries(tmp_path):
    """Codex 5.3 summarise-blocker #1: non-dict entries in messages must skip, not crash."""
    from spaice_agent.memory.summarise import _load_session_transcript

    db_path = tmp_path / "hermes.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, messages TEXT)")
    # Mixed: valid dict, bare string (malformed), valid dict
    messages = [
        {"role": "user", "content": "hello"},
        "some orphan string that should not crash",
        42,  # number
        {"role": "assistant", "content": "hi"},
    ]
    conn.execute(
        "INSERT INTO sessions (id, messages) VALUES (?, ?)",
        ("s1", json.dumps(messages)),
    )
    conn.commit()
    conn.close()

    # Must not raise AttributeError on the string or int entries
    transcript = _load_session_transcript("s1", db_path)
    assert "hello" in transcript
    assert "hi" in transcript
    assert "orphan string" not in transcript  # skipped, not crashed


def test_summarise_transcript_closes_sqlite_on_exception(tmp_path, monkeypatch):
    """Codex 5.3 summarise-major #2: sqlite conn closes even on query failure."""
    from spaice_agent.memory import summarise as summarise_mod

    db_path = tmp_path / "broken.sqlite"
    # Create a db with wrong schema so the SELECT raises
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE wrong_schema (foo TEXT)")
    conn.commit()
    conn.close()

    closed_count = [0]
    real_connect = sqlite3.connect

    class _TrackingConn:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, *a, **kw):
            return self._inner.execute(*a, **kw)

        def close(self):
            closed_count[0] += 1
            self._inner.close()

    def tracking_connect(path, *a, **kw):
        return _TrackingConn(real_connect(path, *a, **kw))

    monkeypatch.setattr(summarise_mod.sqlite3, "connect", tracking_connect)

    # SELECT on missing 'sessions' table raises → must still close conn
    result = summarise_mod._load_session_transcript("any", db_path)
    assert result == ""
    assert closed_count[0] == 1, (
        "sqlite connection must close on the exception path"
    )


# ---------------------------------------------------------------------------
# library_index.py — Codex 5.3 blocker #1, major #3
# ---------------------------------------------------------------------------


def test_library_index_load_tolerates_non_dict_entries(tmp_path):
    """Codex 5.3 library_index-blocker #1: malformed entries must not crash load."""
    from spaice_agent.memory.library_index import load_library_index

    dash = tmp_path / "_dashboard"
    dash.mkdir()
    index_path = dash / "library-index.yaml"
    # Mix of good entry and garbage string entry
    index_path.write_text(
        yaml.safe_dump(
            {
                "entries": [
                    {
                        "path": "projects/alpha.md",
                        "title": "Alpha",
                        "tags": ["t"],
                        "summary": "s",
                        "backlinks": [],
                        "mtime": 0.0,
                    },
                    "this is a bare string — malformed",
                    None,
                    42,
                ],
                "ts_built": "2026-05-03T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    index = load_library_index(tmp_path)
    assert index is not None
    entries = list(index.entries)
    assert len(entries) == 1
    assert entries[0].path == "projects/alpha.md"


def test_library_index_reused_backlinks_are_sorted_and_deduped(tmp_path, monkeypatch):
    """Codex 5.3 library_index-major #3: reused entries must normalise backlinks.

    The fresh-parse path already sorts + dedups; the reuse path must match so
    index output is stable across rebuilds.
    """
    from spaice_agent.memory import library_index as lib_mod
    from spaice_agent.memory.library_index import (
        LibraryEntry,
        _build_entries,
    )

    # Craft a vault with one file
    note_dir = tmp_path / "projects"
    note_dir.mkdir()
    note = note_dir / "alpha.md"
    note.write_text("---\ntitle: Alpha\n---\nbody\n")

    # Existing entry reports mtime equal to current → reuse path fires
    mtime = note.stat().st_mtime
    existing = {
        "projects/alpha.md": LibraryEntry(
            path="projects/alpha.md",
            title="Alpha",
            tags=(),
            summary="",
            backlinks=("stale",),  # old backlinks
            mtime=mtime,
        )
    }

    # Backlink map has duplicated/unordered entries; post-normalise must sort+dedup
    backlink_map = {"alpha": ["zzz", "aaa", "aaa", "mmm"]}

    entries = _build_entries(tmp_path, existing, backlink_map)
    assert len(entries) == 1
    assert entries[0].backlinks == ("aaa", "mmm", "zzz"), (
        f"reused backlinks must be sorted and deduped, got {entries[0].backlinks!r}"
    )
