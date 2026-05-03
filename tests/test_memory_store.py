"""Tests for spaice_agent.memory_store — inbox fact router."""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from spaice_agent.memory_store import (
    MemoryStoreError,
    StoredFact,
    store_fact,
)


def _read_fact(path: Path) -> tuple[dict, str]:
    """Split a stored file into (frontmatter_dict, body)."""
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "frontmatter missing"
    _, front, body = text.split("---\n", 2)
    meta = yaml.safe_load(front)
    return meta, body.strip()


def test_store_fact_happy(tmp_path):
    out = store_fact(
        "Jozef prefers AU spelling",
        source="test-suite",
        tags=["preference"],
        inbox_dir=tmp_path,
        now=datetime(2026, 5, 3, 12, 30, 45),
    )
    assert isinstance(out, StoredFact)
    assert out.path.exists()
    assert out.path.parent == tmp_path
    meta, body = _read_fact(out.path)
    assert body == "Jozef prefers AU spelling"
    assert meta["source"] == "test-suite"
    assert meta["tags"] == ["preference"]
    assert meta["captured_at"].startswith("2026-05-03T12:30:45")


def test_store_fact_extra_meta_preserved(tmp_path):
    out = store_fact(
        "Some fact",
        inbox_dir=tmp_path,
        extra_meta={"confidence": 0.9, "channel": "telegram"},
    )
    meta, _ = _read_fact(out.path)
    assert meta["confidence"] == 0.9
    assert meta["channel"] == "telegram"


def test_store_fact_reserved_keys_overridden(tmp_path):
    out = store_fact(
        "Some fact",
        source="real-source",
        inbox_dir=tmp_path,
        extra_meta={"source": "fake-source", "captured_at": "fake-ts"},
    )
    meta, _ = _read_fact(out.path)
    assert meta["source"] == "real-source"
    assert meta["captured_at"] != "fake-ts"


def test_store_fact_slug_derivation(tmp_path):
    out = store_fact(
        "The quick brown fox jumps over the lazy dog",
        inbox_dir=tmp_path,
    )
    assert "the-quick-brown-fox" in out.slug
    assert out.path.name.endswith(".md")


def test_store_fact_slug_strips_non_alnum(tmp_path):
    out = store_fact(
        "Weird !@#$%^ chars & things!!!",
        inbox_dir=tmp_path,
    )
    assert re.fullmatch(r"[a-z0-9-]+", out.slug)


def test_store_fact_long_text_slug_capped(tmp_path):
    out = store_fact("x" * 500, inbox_dir=tmp_path)
    assert len(out.slug) <= 80


def test_store_fact_all_punctuation_falls_back_to_default(tmp_path):
    out = store_fact("!!!  ???  ...", inbox_dir=tmp_path)
    assert out.slug == "fact"


def test_store_fact_rejects_empty(tmp_path):
    with pytest.raises(MemoryStoreError):
        store_fact("   \n\t  ", inbox_dir=tmp_path)


def test_store_fact_rejects_non_string(tmp_path):
    with pytest.raises(MemoryStoreError):
        store_fact(12345, inbox_dir=tmp_path)  # type: ignore[arg-type]


def test_store_fact_collision_gets_ms_suffix(tmp_path):
    ts = datetime(2026, 5, 3, 12, 30, 45, 123_000)
    first = store_fact("Same text", inbox_dir=tmp_path, now=ts)
    second = store_fact("Same text", inbox_dir=tmp_path, now=ts)
    assert first.path != second.path
    assert ".123" in second.path.name


def test_store_fact_creates_inbox_dir(tmp_path):
    target = tmp_path / "deeply" / "nested" / "inbox"
    out = store_fact("fact", inbox_dir=target)
    assert target.is_dir()
    assert out.path.parent == target


def test_store_fact_atomic_no_tmp_leftover(tmp_path):
    store_fact("fact", inbox_dir=tmp_path)
    # No .tmp files remain
    assert not any(p.name.endswith(".tmp") for p in tmp_path.iterdir())


def test_store_fact_readable_unicode(tmp_path):
    out = store_fact(
        "Café résumé — œuvre",
        inbox_dir=tmp_path,
        extra_meta={"note": "naïve"},
    )
    meta, body = _read_fact(out.path)
    assert body == "Café résumé — œuvre"
    assert meta["note"] == "naïve"
