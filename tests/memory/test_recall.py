"""Tests for spaice_agent.memory.recall."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from spaice_agent.memory.recall import (
    Recaller,
    RecallHit,
    InvalidTriggersConfigError,
)


# -- fixtures ---------------------------------------------------------------


@pytest.fixture
def empty_vault(tmp_path):
    """Vault with canonical shelves but no content."""
    vault = tmp_path / "vault"
    vault.mkdir()
    for name in ("identity", "projects", "sites", "products", "patterns"):
        (vault / name).mkdir()
    (vault / "_inbox").mkdir()
    return vault


@pytest.fixture
def populated_vault(empty_vault):
    """Vault seeded with some test content."""
    (empty_vault / "sites" / "hanna.md").write_text(
        "# Hanna site\nSwing doors use FSH FES21 mortice strike.\n"
    )
    (empty_vault / "projects" / "scope.md").write_text(
        "# Scope project\nAzure deploy at wonderfulbeach-f279a011.\n"
    )
    (empty_vault / "identity" / "jozef.md").write_text(
        "# Identity\nJozef runs SPAICE — sells Inception + 2N + Basalte.\n"
    )
    (empty_vault / "patterns" / "unrelated.md").write_text(
        "# Patterns\nSome generic text without proper nouns.\n"
    )
    return empty_vault


def _write_triggers(vault_or_agent_dir: Path, config: dict):
    """Helper: write triggers.yaml into the right spot for a test."""
    vault_or_agent_dir.mkdir(parents=True, exist_ok=True)
    (vault_or_agent_dir / "triggers.yaml").write_text(yaml.safe_dump(config))


# -- trigger config loading -------------------------------------------------


def test_recaller_works_with_empty_triggers(empty_vault):
    """No triggers.yaml → still functions, uses regex + capitalised fallback."""
    r = Recaller.for_vault(empty_vault)
    triggers = r.extract_triggers("No known names here, just plain text.")
    # May pick up capitalised fallback or regex matches; must not crash
    assert isinstance(triggers, list)


def test_recaller_loads_client_names(tmp_path, empty_vault):
    triggers_yaml = tmp_path / "trig.yaml"
    triggers_yaml.write_text(yaml.safe_dump({
        "client_names": ["Hanna", "Wahby"],
    }))
    r = Recaller.for_vault(empty_vault, triggers_yaml=triggers_yaml)
    triggers = r.extract_triggers("What about Hanna's gate?")
    assert "hanna" in triggers


def test_recaller_loads_brand_names(tmp_path, empty_vault):
    triggers_yaml = tmp_path / "trig.yaml"
    triggers_yaml.write_text(yaml.safe_dump({
        "brand_names": ["Basalte", "Lockwood"],
    }))
    r = Recaller.for_vault(empty_vault, triggers_yaml=triggers_yaml)
    triggers = r.extract_triggers("Basalte Sentido keypad order")
    assert "basalte" in triggers


def test_recaller_rejects_malformed_triggers(tmp_path, empty_vault):
    triggers_yaml = tmp_path / "bad.yaml"
    triggers_yaml.write_text("::: not valid :::")
    with pytest.raises(InvalidTriggersConfigError):
        Recaller.for_vault(empty_vault, triggers_yaml=triggers_yaml)


def test_recaller_rejects_non_mapping_yaml(tmp_path, empty_vault):
    triggers_yaml = tmp_path / "list.yaml"
    triggers_yaml.write_text("- not\n- a\n- mapping\n")
    with pytest.raises(InvalidTriggersConfigError, match="mapping"):
        Recaller.for_vault(empty_vault, triggers_yaml=triggers_yaml)


def test_recaller_rejects_bad_regex(tmp_path, empty_vault):
    triggers_yaml = tmp_path / "bad_regex.yaml"
    triggers_yaml.write_text(yaml.safe_dump({
        "id_patterns": [{"pattern": "[unclosed"}],
    }))
    with pytest.raises(InvalidTriggersConfigError, match="invalid regex"):
        Recaller.for_vault(empty_vault, triggers_yaml=triggers_yaml)


# -- trigger extraction -----------------------------------------------------


def test_extract_triggers_finds_sku_pattern(empty_vault):
    r = Recaller.for_vault(empty_vault)
    triggers = r.extract_triggers("Order ES9100 for the job")
    assert "es9100" in triggers


def test_extract_triggers_finds_url(empty_vault):
    r = Recaller.for_vault(empty_vault)
    triggers = r.extract_triggers("Check https://example.com/ds.pdf for details")
    assert any("example.com" in t for t in triggers)


def test_extract_triggers_finds_ip(empty_vault):
    r = Recaller.for_vault(empty_vault)
    triggers = r.extract_triggers("Router at 192.168.1.1 is down")
    assert "192.168.1.1" in triggers


def test_extract_triggers_strips_stopwords(empty_vault):
    r = Recaller.for_vault(empty_vault)
    triggers = r.extract_triggers("The quick brown fox")
    # "The" is a stopword; must not appear
    assert "the" not in triggers


def test_extract_triggers_dedupes(tmp_path, empty_vault):
    triggers_yaml = tmp_path / "t.yaml"
    triggers_yaml.write_text(yaml.safe_dump({"client_names": ["hanna"]}))
    r = Recaller.for_vault(empty_vault, triggers_yaml=triggers_yaml)
    triggers = r.extract_triggers("Hanna Hanna hanna HANNA")
    assert triggers.count("hanna") == 1


def test_extract_triggers_priority_order(tmp_path, empty_vault):
    """Client names (prio 0) come before brand names (prio 2)."""
    triggers_yaml = tmp_path / "t.yaml"
    triggers_yaml.write_text(yaml.safe_dump({
        "client_names": ["hanna"],
        "brand_names": ["basalte"],
    }))
    r = Recaller.for_vault(empty_vault, triggers_yaml=triggers_yaml)
    triggers = r.extract_triggers("Basalte keypad for Hanna")
    assert triggers.index("hanna") < triggers.index("basalte")


# -- scan -------------------------------------------------------------------


def test_scan_empty_triggers_returns_empty(populated_vault):
    r = Recaller.for_vault(populated_vault)
    assert r.scan([], max_hits=10) == []


def test_scan_finds_matches(tmp_path, populated_vault):
    triggers_yaml = tmp_path / "t.yaml"
    triggers_yaml.write_text(yaml.safe_dump({"client_names": ["hanna"]}))
    r = Recaller.for_vault(populated_vault, triggers_yaml=triggers_yaml)
    triggers = r.extract_triggers("What strike for Hanna?")
    hits = r.scan(triggers)
    assert any(h.rel_path.startswith("sites/hanna") for h in hits)


def test_scan_respects_max_hits(populated_vault):
    # Seed 20 hits
    for i in range(20):
        (populated_vault / "projects" / f"f{i}.md").write_text("Scope work here\n")
    triggers_yaml = populated_vault.parent / "t.yaml"
    triggers_yaml.write_text(yaml.safe_dump({"project_names": ["scope"]}))
    r = Recaller.for_vault(populated_vault, triggers_yaml=triggers_yaml)
    triggers = r.extract_triggers("Scope status?")
    hits = r.scan(triggers, max_hits=5)
    assert len(hits) == 5


def test_scan_ranks_by_score_desc(tmp_path, populated_vault):
    """Higher score = earlier result."""
    # Add a doc that mentions hanna 5 times
    (populated_vault / "sites" / "hanna-rich.md").write_text(
        "hanna " * 5 + "\n"
    )
    triggers_yaml = tmp_path / "t.yaml"
    triggers_yaml.write_text(yaml.safe_dump({"client_names": ["hanna"]}))
    r = Recaller.for_vault(populated_vault, triggers_yaml=triggers_yaml)
    hits = r.scan(["hanna"])
    # hanna-rich should outrank the original hanna.md (more matches)
    rich = [h for h in hits if "hanna-rich" in h.rel_path]
    orig = [h for h in hits if h.rel_path == "sites/hanna.md"]
    assert rich and orig
    assert rich[0].score >= orig[0].score


def test_scan_ranks_by_shelf_on_score_tie(tmp_path, empty_vault):
    """Equal scores → earlier shelf wins."""
    (empty_vault / "identity" / "a.md").write_text("testterm\n")
    (empty_vault / "projects" / "b.md").write_text("testterm\n")

    triggers_yaml = tmp_path / "t.yaml"
    triggers_yaml.write_text(yaml.safe_dump({"client_names": ["testterm"]}))
    r = Recaller.for_vault(empty_vault, triggers_yaml=triggers_yaml)
    hits = r.scan(["testterm"])
    # identity (priority 0) ranks before projects (priority 7)
    identity_idx = next(i for i, h in enumerate(hits) if "identity" in h.rel_path)
    projects_idx = next(i for i, h in enumerate(hits) if "projects" in h.rel_path)
    assert identity_idx < projects_idx


def test_scan_skips_default_dirs(tmp_path, empty_vault):
    """_archive, _inbox, __pycache__ are skipped by default."""
    (empty_vault / "_archive").mkdir(exist_ok=True)
    (empty_vault / "_archive" / "old.md").write_text("hanna stuff\n")
    (empty_vault / "_inbox" / "pending.md").write_text("hanna stuff\n")

    triggers_yaml = tmp_path / "t.yaml"
    triggers_yaml.write_text(yaml.safe_dump({"client_names": ["hanna"]}))
    r = Recaller.for_vault(empty_vault, triggers_yaml=triggers_yaml)
    hits = r.scan(["hanna"])
    for h in hits:
        assert "_archive" not in h.rel_path
        assert "_inbox" not in h.rel_path


def test_scan_only_scans_supported_extensions(tmp_path, empty_vault):
    (empty_vault / "sites" / "data.md").write_text("hanna in md\n")
    (empty_vault / "sites" / "data.json").write_text('{"h": "hanna in json"}\n')
    (empty_vault / "sites" / "data.log").write_text("hanna in log\n")

    triggers_yaml = tmp_path / "t.yaml"
    triggers_yaml.write_text(yaml.safe_dump({"client_names": ["hanna"]}))
    r = Recaller.for_vault(empty_vault, triggers_yaml=triggers_yaml)
    hits = r.scan(["hanna"])
    paths = [h.rel_path for h in hits]
    assert any("data.md" in p for p in paths)
    assert not any(p.endswith(".json") for p in paths)
    assert not any(p.endswith(".log") for p in paths)


def test_scan_returns_recall_hit_dataclass(tmp_path, populated_vault):
    triggers_yaml = tmp_path / "t.yaml"
    triggers_yaml.write_text(yaml.safe_dump({"client_names": ["hanna"]}))
    r = Recaller.for_vault(populated_vault, triggers_yaml=triggers_yaml)
    hits = r.scan(["hanna"])
    assert hits
    assert isinstance(hits[0], RecallHit)
    assert hits[0].score > 0
    assert hits[0].preview  # preview populated
    assert hits[0].rel_path  # path populated


# -- format_output ----------------------------------------------------------


def test_format_output_no_triggers():
    md = Recaller.format_output([], [])
    assert "no triggers detected" in md


def test_format_output_no_hits():
    md = Recaller.format_output(["hanna"], [])
    assert "No hits" in md
    assert "hanna" in md


def test_format_output_with_hits():
    hits = [
        RecallHit(shelf_priority=1, score=10, rel_path="sites/hanna.md",
                  preview="Strike: FSH FES21"),
    ]
    md = Recaller.format_output(["hanna"], hits)
    assert "sites/hanna.md" in md
    assert "score 10" in md
    assert "FSH FES21" in md
    assert "1 relevant file" in md


# -- extension hooks (v0.2.1 stubs) ----------------------------------------


def test_merge_results_returns_bm25_unchanged(empty_vault):
    """v0.2.0: _merge_results is a no-op (returns bm25 hits as-is)."""
    r = Recaller.for_vault(empty_vault)
    bm25 = [
        RecallHit(shelf_priority=0, score=5, rel_path="a.md", preview="a"),
    ]
    merged = r._merge_results(bm25, [RecallHit(0, 99, "b.md", "b")])
    assert merged == bm25  # vector hits ignored in v0.2.0


def test_rerank_returns_hits_unchanged(empty_vault):
    """v0.2.0: _rerank is a no-op."""
    r = Recaller.for_vault(empty_vault)
    hits = [
        RecallHit(shelf_priority=0, score=5, rel_path="a.md", preview="a"),
        RecallHit(shelf_priority=1, score=3, rel_path="b.md", preview="b"),
    ]
    reranked = r._rerank(hits, "query")
    assert reranked == hits


# -- regression guards from Codex review 2026-05-03 -----------------------


def test_hyphenated_sku_matches_as_unit(tmp_path, empty_vault):
    """Regression guard: Codex recall #5.

    Hyphenated terms like 'fsh-123' must match the full unit, not get
    broken into 'fsh' + '123' by word-boundary regex. Previous `\\b`
    approach failed because `\\b` fires at the hyphen.
    """
    (empty_vault / "sites" / "hanna.md").write_text(
        "Used FSH-FES21 strike on the front door.\n"
    )
    triggers_yaml = tmp_path / "t.yaml"
    triggers_yaml.write_text(yaml.safe_dump({"brand_names": ["fsh-fes21"]}))
    r = Recaller.for_vault(empty_vault, triggers_yaml=triggers_yaml)
    hits = r.scan(["fsh-fes21"])
    assert len(hits) == 1
    assert "hanna" in hits[0].rel_path


def test_horizontal_rule_mid_document_not_mistaken_for_frontmatter(
    tmp_path, empty_vault,
):
    """Regression guard: Codex recall #6.

    Markdown documents with '---' horizontal rules mid-content shouldn't
    have those lines treated as frontmatter delimiters. The preview
    fallback must track frontmatter state (first --- opens, second closes,
    subsequent --- are content).
    """
    target = empty_vault / "sites" / "example.md"
    target.write_text(
        "---\n"
        "title: Example\n"
        "---\n"
        "\n"
        "# Section one\n"
        "\n"
        "Some content before a rule.\n"
        "\n"
        "---\n"  # horizontal rule — MUST be treated as content below
        "\n"
        "This line should be findable.\n"
    )
    r = Recaller.for_vault(empty_vault)
    # Manually call preview_fallback (simulating case with no trigger match)
    preview = r._preview_fallback(target)
    # Must find "# Section one" (content after frontmatter), not stop at
    # the mid-document '---'.
    assert "Section one" in preview
