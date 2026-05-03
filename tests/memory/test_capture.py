"""Tests for spaice_agent.memory.capture."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest
import yaml

from spaice_agent.memory.capture import (
    InboxEntry,
    capture_fact,
    InvalidInboxEntryError,
    MAX_TEXT_BYTES,
    _entry_id,
    _filename,
)
from spaice_agent.memory.paths import VaultNotFoundError


SYDNEY = timezone(timedelta(hours=10))  # AEST


# -- InboxEntry validation --------------------------------------------------


def test_entry_rejects_empty_text():
    with pytest.raises(InvalidInboxEntryError, match="non-empty"):
        InboxEntry(text="", source="test")


def test_entry_rejects_whitespace_only_text():
    with pytest.raises(InvalidInboxEntryError, match="non-empty"):
        InboxEntry(text="   \n   ", source="test")


def test_entry_rejects_too_large_text():
    big = "x" * (MAX_TEXT_BYTES + 1)
    with pytest.raises(InvalidInboxEntryError, match="exceeds"):
        InboxEntry(text=big, source="test")


def test_entry_rejects_empty_source():
    with pytest.raises(InvalidInboxEntryError, match="source"):
        InboxEntry(text="fact", source="")


def test_entry_accepts_just_under_limit():
    # Should not raise
    big = "x" * (MAX_TEXT_BYTES - 10)
    e = InboxEntry(text=big, source="test")
    assert e.text == big


# -- ID determinism ---------------------------------------------------------


def test_entry_id_is_deterministic():
    ts = datetime(2026, 5, 3, 19, 30, tzinfo=SYDNEY)
    a = _entry_id("hello", ts)
    b = _entry_id("hello", ts)
    assert a == b
    assert len(a) == 12


def test_entry_id_differs_by_text():
    ts = datetime(2026, 5, 3, 19, 30, tzinfo=SYDNEY)
    a = _entry_id("hello", ts)
    b = _entry_id("world", ts)
    assert a != b


def test_entry_id_5min_bucket():
    """Same text within 5-min window → same ID (idempotency)."""
    a_ts = datetime(2026, 5, 3, 19, 31, tzinfo=SYDNEY)
    b_ts = datetime(2026, 5, 3, 19, 34, tzinfo=SYDNEY)
    assert _entry_id("fact", a_ts) == _entry_id("fact", b_ts)


def test_entry_id_across_buckets_differ():
    a_ts = datetime(2026, 5, 3, 19, 34, tzinfo=SYDNEY)
    b_ts = datetime(2026, 5, 3, 19, 35, tzinfo=SYDNEY)  # crosses 5-min boundary
    assert _entry_id("fact", a_ts) != _entry_id("fact", b_ts)


# -- filename format --------------------------------------------------------


def test_filename_format():
    ts = datetime(2026, 5, 3, 19, 32, tzinfo=SYDNEY)
    name = _filename(ts, "abc123def456")
    assert name == "2026-05-03-19h32m-abc123def456.md"


# -- capture_fact end-to-end ------------------------------------------------


def _setup_vault(tmp_path, monkeypatch, agent_id="testbot"):
    """Create a minimal vault and config for an agent; returns (vault, paths module)."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    vault = tmp_path / agent_id
    vault.mkdir()
    (vault / "_inbox").mkdir()
    return vault


def test_capture_fact_writes_file(tmp_path, monkeypatch):
    vault = _setup_vault(tmp_path, monkeypatch)
    entry = InboxEntry(
        text="BrandOne widget-X capacitive — no engraving",
        source="telegram",
        created_at=datetime(2026, 5, 3, 19, 32, tzinfo=SYDNEY),
    )
    path = capture_fact(entry, agent_id="testbot")
    assert path.exists()
    assert path.parent == vault / "_inbox"
    assert path.suffix == ".md"


def test_capture_fact_frontmatter_parseable(tmp_path, monkeypatch):
    _setup_vault(tmp_path, monkeypatch)
    entry = InboxEntry(
        text="A fact",
        source="conversation",
        category="product",
        tags=("keypad", "basalte"),
        created_at=datetime(2026, 5, 3, 19, 32, tzinfo=SYDNEY),
    )
    path = capture_fact(entry, agent_id="testbot")
    content = path.read_text()

    # Split frontmatter
    assert content.startswith("---\n")
    _, fm, body = content.split("---", 2)
    meta = yaml.safe_load(fm)

    assert len(meta["id"]) == 12
    assert meta["source"] == "conversation"
    assert meta["category"] == "product"
    assert meta["tags"] == ["keypad", "basalte"]
    assert meta["status"] == "pending"
    assert "A fact" in body.strip()


def test_capture_fact_null_category(tmp_path, monkeypatch):
    _setup_vault(tmp_path, monkeypatch)
    entry = InboxEntry(text="Fact", source="cron", category=None)
    path = capture_fact(entry, agent_id="testbot")
    content = path.read_text()
    _, fm, _ = content.split("---", 2)
    meta = yaml.safe_load(fm)
    assert meta["category"] is None


def test_capture_fact_empty_tags(tmp_path, monkeypatch):
    _setup_vault(tmp_path, monkeypatch)
    entry = InboxEntry(text="Fact", source="cron")
    path = capture_fact(entry, agent_id="testbot")
    content = path.read_text()
    _, fm, _ = content.split("---", 2)
    meta = yaml.safe_load(fm)
    assert meta["tags"] == []


def test_capture_fact_idempotent_within_5min(tmp_path, monkeypatch):
    _setup_vault(tmp_path, monkeypatch)
    ts1 = datetime(2026, 5, 3, 19, 31, tzinfo=SYDNEY)
    ts2 = datetime(2026, 5, 3, 19, 33, tzinfo=SYDNEY)  # within same 5-min bucket
    e1 = InboxEntry(text="dup fact", source="test", created_at=ts1)
    e2 = InboxEntry(text="dup fact", source="test", created_at=ts2)
    p1 = capture_fact(e1, agent_id="testbot")
    p2 = capture_fact(e2, agent_id="testbot")
    assert p1 == p2  # same filename → overwrote safely


def test_capture_fact_different_bucket_new_file(tmp_path, monkeypatch):
    _setup_vault(tmp_path, monkeypatch)
    ts1 = datetime(2026, 5, 3, 19, 34, tzinfo=SYDNEY)
    ts2 = datetime(2026, 5, 3, 19, 40, tzinfo=SYDNEY)  # different bucket
    e1 = InboxEntry(text="fact", source="test", created_at=ts1)
    e2 = InboxEntry(text="fact", source="test", created_at=ts2)
    p1 = capture_fact(e1, agent_id="testbot")
    p2 = capture_fact(e2, agent_id="testbot")
    assert p1 != p2


def test_capture_fact_raises_when_vault_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # NOTE: no vault created
    entry = InboxEntry(text="fact", source="test")
    with pytest.raises(VaultNotFoundError):
        capture_fact(entry, agent_id="ghost")


def test_capture_fact_creates_inbox_if_missing(tmp_path, monkeypatch):
    """If vault exists but _inbox doesn't, capture creates it."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    vault = tmp_path / "noibx"
    vault.mkdir()
    # Note: _inbox NOT created
    entry = InboxEntry(text="fact", source="test")
    path = capture_fact(entry, agent_id="noibx")
    assert path.parent == vault / "_inbox"
    assert path.exists()


def test_capture_fact_quotes_special_chars_in_source(tmp_path, monkeypatch):
    """Source with spaces/colons must be YAML-quoted."""
    _setup_vault(tmp_path, monkeypatch)
    entry = InboxEntry(text="Fact", source="cron:mine sessions")
    path = capture_fact(entry, agent_id="testbot")
    content = path.read_text()
    _, fm, _ = content.split("---", 2)
    meta = yaml.safe_load(fm)
    assert meta["source"] == "cron:mine sessions"


def test_capture_fact_body_preserves_content(tmp_path, monkeypatch):
    _setup_vault(tmp_path, monkeypatch)
    payload = "First line\n\nSecond paragraph with **bold**."
    entry = InboxEntry(text=payload, source="test")
    path = capture_fact(entry, agent_id="testbot")
    content = path.read_text()
    _, _, body = content.split("---", 2)
    assert payload in body


def test_capture_fact_filename_chronological_sorts(tmp_path, monkeypatch):
    """Filenames sort lexicographically = chronologically."""
    _setup_vault(tmp_path, monkeypatch)
    ts1 = datetime(2026, 5, 3, 10, 0, tzinfo=SYDNEY)
    ts2 = datetime(2026, 5, 3, 14, 0, tzinfo=SYDNEY)
    ts3 = datetime(2026, 5, 4, 8, 0, tzinfo=SYDNEY)
    for i, ts in enumerate((ts3, ts1, ts2)):  # out-of-order insert
        capture_fact(
            InboxEntry(text=f"fact {i}", source="t", created_at=ts),
            agent_id="testbot",
        )

    names = sorted(p.name for p in (tmp_path / "testbot" / "_inbox").iterdir())
    # Earliest timestamp should come first
    assert names[0].startswith("2026-05-03-10h")
    assert names[1].startswith("2026-05-03-14h")
    assert names[2].startswith("2026-05-04-08h")


# -- regression guards from Codex review 2026-05-03 -----------------------


def test_source_with_newline_rejected(tmp_path, monkeypatch):
    """Regression guard: Codex capture #5.

    Newlines in source would corrupt the YAML frontmatter block (source
    value would spill across multiple YAML lines, breaking the parse).
    """
    _setup_vault(tmp_path, monkeypatch)
    with pytest.raises(InvalidInboxEntryError, match="control characters"):
        InboxEntry(text="fact", source="telegram\nmalicious: yaml")


def test_source_too_long_rejected(tmp_path, monkeypatch):
    """Regression guard: Codex capture #5 (length bound)."""
    _setup_vault(tmp_path, monkeypatch)
    with pytest.raises(InvalidInboxEntryError, match="exceeds"):
        InboxEntry(text="fact", source="x" * 1000)


def test_source_with_url_is_properly_quoted(tmp_path, monkeypatch):
    """Regression guard: Codex capture #4.

    A source value containing ':' (like a URL) must be quoted in YAML
    frontmatter, else YAML treats ':' as a key-value separator in flow
    context.
    """
    _setup_vault(tmp_path, monkeypatch)
    entry = InboxEntry(text="fact", source="http://example.com/feed")
    path = capture_fact(entry, agent_id="testbot")
    content = path.read_text()
    _, fm, _ = content.split("---", 2)
    meta = yaml.safe_load(fm)
    # If not quoted, yaml.safe_load would misparse or raise
    assert meta["source"] == "http://example.com/feed"
