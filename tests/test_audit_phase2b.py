"""Regression tests for Phase 2B audit improvements.

Two fixes to audit.py driven by dogfooding the CLI against a scaffold:

1. check_duplicate_files now exempts README.md / index.md / INDEX.md
   (those are intentionally per-directory files).
2. _extract_wikilinks strips fenced code blocks + inline code spans
   before scanning, so wikilink-syntax examples in CONVENTIONS.md
   don't produce broken-link false positives.
"""

from __future__ import annotations

from pathlib import Path


def test_duplicate_files_exempts_readme(tmp_path: Path):
    from spaice_agent.memory.audit import check_duplicate_files

    # Two shelves each with their own README.md
    (tmp_path / "identity").mkdir()
    (tmp_path / "projects").mkdir()
    (tmp_path / "identity" / "README.md").write_text("identity readme")
    (tmp_path / "projects" / "README.md").write_text("projects readme")

    # A non-exempt filename duplicated across shelves should still be flagged
    (tmp_path / "identity" / "note.md").write_text("x")
    (tmp_path / "projects" / "note.md").write_text("x")

    findings = check_duplicate_files(tmp_path)
    # Only note.md (2 rows), not README.md
    paths = [f.path for f in findings]
    assert all("README.md" not in p for p in paths), (
        f"README.md should not be flagged as duplicate, got: {paths}"
    )
    assert any("note.md" in p for p in paths), (
        "note.md should be flagged as duplicate across shelves"
    )


def test_duplicate_files_exempts_index_md(tmp_path: Path):
    from spaice_agent.memory.audit import check_duplicate_files

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "index.md").write_text("x")
    (tmp_path / "b" / "index.md").write_text("x")

    findings = check_duplicate_files(tmp_path)
    assert findings == [], "index.md duplicates should be exempt"


def test_extract_wikilinks_skips_fenced_code(tmp_path: Path):
    from spaice_agent.memory.audit import _extract_wikilinks

    content = (
        "See [[real-target]] for details.\n\n"
        "Syntax example:\n"
        "```\n"
        "[[fake-in-fence]] is how you link.\n"
        "```\n"
        "Also [[another-real-target]].\n"
    )
    targets = _extract_wikilinks(content)
    assert "real-target" in targets
    assert "another-real-target" in targets
    assert "fake-in-fence" not in targets, (
        "Wikilinks inside fenced code blocks must not be extracted"
    )


def test_extract_wikilinks_skips_inline_code(tmp_path: Path):
    from spaice_agent.memory.audit import _extract_wikilinks

    content = "Use `[[target]]` for wikilinks; real link: [[actual-page]]."
    targets = _extract_wikilinks(content)
    assert "actual-page" in targets
    assert "target" not in targets, (
        "Wikilinks inside inline code spans must not be extracted"
    )


def test_extract_wikilinks_handles_tilde_fences(tmp_path: Path):
    from spaice_agent.memory.audit import _extract_wikilinks

    content = (
        "~~~\n"
        "[[tilde-fenced]] should be skipped\n"
        "~~~\n"
        "[[real-tilde-link]] should be found.\n"
    )
    targets = _extract_wikilinks(content)
    assert "real-tilde-link" in targets
    assert "tilde-fenced" not in targets
