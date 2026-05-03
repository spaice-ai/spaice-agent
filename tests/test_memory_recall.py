"""Tests for spaice_agent.memory_recall — recall_scan.py wrapper."""
from __future__ import annotations

import asyncio
import os
import stat
import textwrap
from pathlib import Path

import pytest

from spaice_agent.memory_recall import (
    RecallHit,
    RecallResult,
    _parse_output,
    recall,
)


# ---------------------------------------------------------------------------
# _parse_output — unit tests on the stdout parser
# ---------------------------------------------------------------------------


def test_parse_empty_output():
    assert _parse_output("") == []


def test_parse_em_dash_format():
    stdout = textwrap.dedent("""
        identity/jozef.md — Jozef is Sydney based, AU spelling
        spaice/products/lock.md — RG80 garage roller deadbolt
    """).strip()
    hits = _parse_output(stdout)
    assert len(hits) == 2
    assert hits[0].path == "identity/jozef.md"
    assert hits[0].preview.startswith("Jozef is Sydney")
    assert hits[1].path == "spaice/products/lock.md"


def test_parse_ascii_dash_fallback():
    stdout = "some/file.md - ASCII dash separator"
    hits = _parse_output(stdout)
    assert len(hits) == 1
    assert hits[0].path == "some/file.md"


def test_parse_skips_comments_and_blanks():
    stdout = textwrap.dedent("""
        # this is a comment
        
        identity/a.md — fact
        
        # another comment
    """).strip()
    hits = _parse_output(stdout)
    assert len(hits) == 1


def test_parse_skips_lines_without_separator():
    stdout = textwrap.dedent("""
        identity/a.md — good
        no separator here at all
        identity/b.md — also good
    """).strip()
    hits = _parse_output(stdout)
    assert len(hits) == 2


def test_parse_strips_backticks_around_path():
    stdout = "`identity/a.md` — wrapped in backticks"
    hits = _parse_output(stdout)
    assert hits[0].path == "identity/a.md"


# ---------------------------------------------------------------------------
# recall() — integration with a fake recall_scan.py
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_recall_script(tmp_path):
    """Build a bash script pretending to be recall_scan.py."""
    script = tmp_path / "recall_scan.py"

    def _write(stdout: str, exit_code: int = 0, sleep: float = 0.0):
        script.write_text(textwrap.dedent(f"""\
            #!/usr/bin/env python3
            import sys, time
            time.sleep({sleep})
            sys.stdout.write({stdout!r})
            sys.exit({exit_code})
        """))
        os.chmod(script, 0o755)
        return script

    return _write


@pytest.mark.asyncio
async def test_recall_happy(fake_recall_script):
    script = fake_recall_script(
        "identity/jozef.md — Sydney, AU spelling\n"
        "spaice/products/rg80.md — garage roller deadbolt\n"
    )
    result = await recall("Sydney locks", script_path=script)
    assert result.error is None
    assert len(result.hits) == 2
    assert result.hits[0].path == "identity/jozef.md"
    assert result.elapsed_s >= 0


@pytest.mark.asyncio
async def test_recall_empty_message(fake_recall_script):
    script = fake_recall_script("unused output")
    result = await recall("   ", script_path=script)
    assert result.error is None
    assert result.hits == []


@pytest.mark.asyncio
async def test_recall_missing_script(tmp_path):
    """Missing script is silent (no error surfaced to caller's prompt)."""
    absent = tmp_path / "does-not-exist.py"
    result = await recall("anything", script_path=absent)
    assert result.hits == []
    assert result.error is None  # silent per contract


@pytest.mark.asyncio
async def test_recall_nonzero_exit_reports_error(fake_recall_script):
    script = fake_recall_script("partial", exit_code=2)
    result = await recall("x", script_path=script)
    assert result.hits == []
    assert result.error is not None
    assert "exit 2" in result.error


@pytest.mark.asyncio
async def test_recall_timeout(fake_recall_script):
    script = fake_recall_script("late output", sleep=0.8)
    result = await recall("x", script_path=script, timeout_s=0.2)
    assert result.hits == []
    assert result.error is not None
    assert "timed out" in result.error


@pytest.mark.asyncio
async def test_recall_respects_max_hits(fake_recall_script):
    lines = "\n".join(f"path/{i}.md — preview {i}" for i in range(20))
    script = fake_recall_script(lines + "\n")
    result = await recall("x", script_path=script, max_hits=5)
    assert len(result.hits) == 5


# ---------------------------------------------------------------------------
# RecallResult.to_markdown
# ---------------------------------------------------------------------------


def test_to_markdown_no_hits():
    md = RecallResult(hits=[], elapsed_s=0.0, error=None).to_markdown()
    assert "no matches" in md


def test_to_markdown_with_error():
    md = RecallResult(hits=[], elapsed_s=0.0, error="boom").to_markdown()
    assert "failed" in md and "boom" in md


def test_to_markdown_with_hits():
    md = RecallResult(
        hits=[
            RecallHit(path="a.md", preview="hello"),
            RecallHit(path="b.md", preview="world"),
        ],
        elapsed_s=0.01,
        error=None,
    ).to_markdown()
    assert "**Memory recall hits:**" in md
    assert "a.md" in md
    assert "hello" in md


def test_to_markdown_preview_truncated():
    long_preview = "x" * 500
    md = RecallResult(
        hits=[RecallHit(path="a.md", preview=long_preview)],
        elapsed_s=0,
        error=None,
    ).to_markdown()
    # Should be capped at 200 chars
    assert len(md) < 400
