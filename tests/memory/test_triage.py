"""Tests for spaice_agent.memory.triage."""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

from spaice_agent.memory.triage import (
    Triager,
    TriageConfig,
    TriageReport,
    TriageResult,
    DEFAULT_HIGH_CONFIDENCE,
    DEFAULT_MID_CONFIDENCE,
    DEFAULT_PROTECTED_SHELVES,
    DRY_RUN_FLAG_NAME,
    LOW_CONF_SUBDIR,
    ACTION_AUTO_FILE,
    ACTION_ESCALATE,
    ACTION_LOW_CONF,
    ACTION_PROTECTED,
    ACTION_SKIP,
)


# -- fixtures ---------------------------------------------------------------


def _setup_agent(tmp_path, monkeypatch, agent_id="testbot", config: dict = None):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    vault = tmp_path / agent_id
    vault.mkdir()
    (vault / "_inbox").mkdir()
    for shelf in ("identity", "projects", "sites", "integrations", "infrastructure"):
        (vault / shelf).mkdir()
    (vault / "doctrines").mkdir()  # Protected by default

    agent_dir = tmp_path / ".spaice-agents" / agent_id
    agent_dir.mkdir(parents=True)
    if config is not None:
        (agent_dir / "config.yaml").write_text(yaml.safe_dump(config))
    return vault


def _make_inbox_file(
    vault: Path,
    name: str = "sample.md",
    confidence: float = 0.9,
    target: str = "sites/hanna.md",
    section: str = None,
    age_hours: float = 10,
    body: str = "Swing doors use FSH FES21.",
    classifier_status: str = None,
    frontmatter_override: str = None,
) -> Path:
    """Write an _inbox file with miner-style frontmatter."""
    fm_lines = ["---"]
    fm_lines.append(f"classifier_target: {target}" if target is not None else "")
    if section is not None:
        fm_lines.append(f"classifier_section: {section}")
    if confidence is not None:
        fm_lines.append(f"classifier_confidence: {confidence}")
    if classifier_status:
        fm_lines.append(f"classifier_status: {classifier_status}")
    fm_lines.append("---")
    fm = "\n".join(l for l in fm_lines if l)

    if frontmatter_override is not None:
        content = frontmatter_override + "\n\n" + body + "\n"
    else:
        content = fm + "\n\n" + body + "\n"

    path = vault / "_inbox" / name
    path.write_text(content)
    # Set mtime to make it sufficiently old
    old_time = time.time() - (age_hours * 3600)
    os.utime(path, (old_time, old_time))
    return path


# -- config loading ---------------------------------------------------------


def test_config_defaults():
    cfg = TriageConfig.from_config_dict({})
    assert cfg.high_confidence == DEFAULT_HIGH_CONFIDENCE
    assert cfg.mid_confidence == DEFAULT_MID_CONFIDENCE
    assert cfg.protected_shelves == DEFAULT_PROTECTED_SHELVES


def test_config_override_protected_shelves():
    cfg = TriageConfig.from_config_dict({
        "memory": {"triage": {"protected_shelves": ["doctrines", "my_protected"]}},
    })
    assert "my_protected" in cfg.protected_shelves
    assert "doctrines" in cfg.protected_shelves


def test_config_rejects_non_list_protected():
    with pytest.raises(ValueError, match="list"):
        TriageConfig.from_config_dict({
            "memory": {"triage": {"protected_shelves": "not-a-list"}},
        })


# -- auto-file path ---------------------------------------------------------


def test_auto_file_high_confidence(tmp_path, monkeypatch):
    vault = _setup_agent(tmp_path, monkeypatch)
    (vault / "sites" / "hanna.md").write_text("# Hanna\n\n## Access\n\n")
    _make_inbox_file(vault, target="sites/hanna.md", section="Access", confidence=0.95)

    triager = Triager.for_agent("testbot")
    report = triager.run(dry_run=False)

    assert len(report.filed) == 1
    assert report.filed[0].target_file == "sites/hanna.md"
    assert report.filed[0].section == "Access"
    # Inbox file was deleted
    assert not (vault / "_inbox" / "sample.md").exists()
    # Target file has the fact appended
    content = (vault / "sites" / "hanna.md").read_text()
    assert "Swing doors use FSH FES21." in content


def test_auto_file_section_insertion(tmp_path, monkeypatch):
    vault = _setup_agent(tmp_path, monkeypatch)
    # Target has existing section
    (vault / "sites" / "hanna.md").write_text(
        "# Hanna\n\n## Access\n\nExisting content.\n\n## Other\n\n"
    )
    _make_inbox_file(vault, target="sites/hanna.md", section="Access",
                     body="New fact!", confidence=0.95)

    triager = Triager.for_agent("testbot")
    triager.run(dry_run=False)

    content = (vault / "sites" / "hanna.md").read_text()
    # New fact should appear AFTER the ## Access header but before ## Other
    access_idx = content.index("## Access")
    new_fact_idx = content.index("New fact!")
    other_idx = content.index("## Other")
    assert access_idx < new_fact_idx < other_idx


def test_auto_file_no_section_appends_to_end(tmp_path, monkeypatch):
    vault = _setup_agent(tmp_path, monkeypatch)
    (vault / "sites" / "hanna.md").write_text("# Hanna\n\nSome text.\n")
    _make_inbox_file(vault, target="sites/hanna.md", section=None, confidence=0.95,
                     body="End fact")
    triager = Triager.for_agent("testbot")
    triager.run(dry_run=False)
    content = (vault / "sites" / "hanna.md").read_text()
    assert content.strip().endswith("_Source: `_inbox/sample.md`_")
    assert "End fact" in content


def test_auto_file_missing_target_escalates(tmp_path, monkeypatch):
    vault = _setup_agent(tmp_path, monkeypatch)
    _make_inbox_file(vault, target="sites/nonexistent.md", confidence=0.95)
    triager = Triager.for_agent("testbot")
    report = triager.run(dry_run=False)
    assert len(report.filed) == 0
    assert len(report.escalated) == 1
    assert "does not exist" in report.escalated[0].reason


# -- escalate path ----------------------------------------------------------


def test_mid_confidence_escalates(tmp_path, monkeypatch):
    vault = _setup_agent(tmp_path, monkeypatch)
    _make_inbox_file(vault, confidence=0.7, target="sites/hanna.md")
    (vault / "sites" / "hanna.md").write_text("")
    triager = Triager.for_agent("testbot")
    report = triager.run(dry_run=False)
    assert len(report.escalated) == 1
    # Inbox file still there (not moved)
    assert (vault / "_inbox" / "sample.md").exists()


def test_missing_target_escalates(tmp_path, monkeypatch):
    vault = _setup_agent(tmp_path, monkeypatch)
    _make_inbox_file(vault, target=None, confidence=0.95)
    triager = Triager.for_agent("testbot")
    report = triager.run(dry_run=False)
    assert len(report.escalated) == 1
    assert "no classifier_target" in report.escalated[0].reason


def test_unparseable_confidence_escalates(tmp_path, monkeypatch):
    vault = _setup_agent(tmp_path, monkeypatch)
    _make_inbox_file(
        vault, target="sites/hanna.md", confidence=None,
        frontmatter_override="---\nclassifier_target: sites/hanna.md\nclassifier_confidence: not-a-number\n---",
    )
    triager = Triager.for_agent("testbot")
    report = triager.run(dry_run=False)
    assert len(report.escalated) == 1
    assert "unparseable confidence" in report.escalated[0].reason


# -- low-confidence / demote path ------------------------------------------


def test_low_confidence_demoted(tmp_path, monkeypatch):
    vault = _setup_agent(tmp_path, monkeypatch)
    _make_inbox_file(vault, confidence=0.3, target="sites/hanna.md")
    triager = Triager.for_agent("testbot")
    report = triager.run(dry_run=False)
    assert len(report.demoted) == 1
    # File moved to _low-confidence/
    assert not (vault / "_inbox" / "sample.md").exists()
    assert (vault / "_inbox" / LOW_CONF_SUBDIR / "sample.md").exists()


# -- protected shelves ------------------------------------------------------


def test_protected_shelf_escalates(tmp_path, monkeypatch):
    vault = _setup_agent(tmp_path, monkeypatch)
    _make_inbox_file(vault, target="doctrines/something.md", confidence=0.95)
    triager = Triager.for_agent("testbot")
    report = triager.run(dry_run=False)
    # Even at high confidence, protected targets never auto-file
    assert len(report.filed) == 0
    assert len(report.escalated) == 1
    assert "protected" in report.escalated[0].reason.lower()


# -- skip / malformed ------------------------------------------------------


def test_readme_skipped(tmp_path, monkeypatch):
    vault = _setup_agent(tmp_path, monkeypatch)
    (vault / "_inbox" / "README.md").write_text("# Inbox readme")
    triager = Triager.for_agent("testbot")
    report = triager.run(dry_run=False)
    assert report.skipped_count == 1


def test_too_fresh_skipped(tmp_path, monkeypatch):
    vault = _setup_agent(tmp_path, monkeypatch)
    _make_inbox_file(vault, confidence=0.95, age_hours=1)  # < 4h default
    triager = Triager.for_agent("testbot")
    report = triager.run(dry_run=False)
    assert report.skipped_count == 1
    assert len(report.filed) == 0


def test_malformed_frontmatter_skipped(tmp_path, monkeypatch):
    vault = _setup_agent(tmp_path, monkeypatch)
    _make_inbox_file(
        vault,
        frontmatter_override="not valid frontmatter at all",
    )
    triager = Triager.for_agent("testbot")
    report = triager.run(dry_run=False)
    assert report.skipped_count == 1


def test_test_artefact_skipped(tmp_path, monkeypatch):
    vault = _setup_agent(tmp_path, monkeypatch)
    _make_inbox_file(
        vault, target="sites/hanna.md", confidence=0.95,
        classifier_status="test_artefact",
    )
    triager = Triager.for_agent("testbot")
    report = triager.run(dry_run=False)
    assert report.skipped_count == 1


# -- dry-run ---------------------------------------------------------------


def test_dry_run_no_side_effects(tmp_path, monkeypatch):
    vault = _setup_agent(tmp_path, monkeypatch)
    (vault / "sites" / "hanna.md").write_text("# Hanna\n")
    _make_inbox_file(vault, target="sites/hanna.md", confidence=0.95)
    _make_inbox_file(vault, name="low.md", target="sites/hanna.md", confidence=0.3)

    triager = Triager.for_agent("testbot")
    report = triager.run(dry_run=True)
    assert report.dry_run is True
    assert len(report.filed) == 1  # reported as filed, but file still there
    assert len(report.demoted) == 1
    # Nothing actually moved
    assert (vault / "_inbox" / "sample.md").exists()
    assert (vault / "_inbox" / "low.md").exists()
    assert not (vault / "_inbox" / LOW_CONF_SUBDIR).exists()
    # Target file unchanged
    assert (vault / "sites" / "hanna.md").read_text() == "# Hanna\n"


def test_dry_run_via_flag_file(tmp_path, monkeypatch):
    vault = _setup_agent(tmp_path, monkeypatch)
    (vault / DRY_RUN_FLAG_NAME).touch()
    (vault / "sites" / "hanna.md").write_text("")
    _make_inbox_file(vault, target="sites/hanna.md", confidence=0.95)

    triager = Triager.for_agent("testbot")
    report = triager.run()  # no explicit dry_run
    assert report.dry_run is True
    # File still in inbox
    assert (vault / "_inbox" / "sample.md").exists()


# -- LOG.md summaries ------------------------------------------------------


def test_log_md_created(tmp_path, monkeypatch):
    vault = _setup_agent(tmp_path, monkeypatch)
    (vault / "sites" / "hanna.md").write_text("")
    _make_inbox_file(vault, target="sites/hanna.md", confidence=0.95)

    triager = Triager.for_agent("testbot")
    triager.run(dry_run=False)
    log_md = vault / "LOG.md"
    assert log_md.exists()
    content = log_md.read_text()
    assert "## Filing pass" in content
    assert "`sample.md`" in content


def test_log_md_escalations_recorded(tmp_path, monkeypatch):
    vault = _setup_agent(tmp_path, monkeypatch)
    _make_inbox_file(vault, target="doctrines/x.md", confidence=0.95)  # protected
    triager = Triager.for_agent("testbot")
    triager.run(dry_run=False)
    content = (vault / "LOG.md").read_text()
    assert "## Inbox pending review" in content


# -- atomic writes ---------------------------------------------------------


def test_no_tmp_files_left_behind(tmp_path, monkeypatch):
    vault = _setup_agent(tmp_path, monkeypatch)
    (vault / "sites" / "hanna.md").write_text("# Hanna\n")
    _make_inbox_file(vault, target="sites/hanna.md", confidence=0.95)
    triager = Triager.for_agent("testbot")
    triager.run(dry_run=False)
    # No .tmp or .tmp-* files
    for p in (vault / "sites").rglob("*"):
        assert not p.name.endswith(".tmp")


# -- empty inbox -----------------------------------------------------------


def test_empty_inbox_zero_counts(tmp_path, monkeypatch):
    _setup_agent(tmp_path, monkeypatch)
    triager = Triager.for_agent("testbot")
    report = triager.run(dry_run=False)
    assert report.filed == ()
    assert report.escalated == ()
    assert report.demoted == ()
    assert report.skipped_count == 0


def test_missing_inbox_dir_zero_counts(tmp_path, monkeypatch):
    """If _inbox doesn't exist at all, run succeeds with zero counts."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / "testbot").mkdir()
    # NOTE: no _inbox
    agent_dir = tmp_path / ".spaice-agents" / "testbot"
    agent_dir.mkdir(parents=True)

    triager = Triager.for_agent("testbot")
    report = triager.run(dry_run=False)
    assert report.filed == ()
    assert report.escalated == ()


# -- sorting ---------------------------------------------------------------


def test_inbox_files_processed_in_sorted_order(tmp_path, monkeypatch):
    vault = _setup_agent(tmp_path, monkeypatch)
    (vault / "sites" / "hanna.md").write_text("")
    _make_inbox_file(vault, name="c.md", target="sites/hanna.md", confidence=0.95)
    _make_inbox_file(vault, name="a.md", target="sites/hanna.md", confidence=0.95)
    _make_inbox_file(vault, name="b.md", target="sites/hanna.md", confidence=0.95)

    triager = Triager.for_agent("testbot")
    report = triager.run(dry_run=False)
    names = [r.inbox_file for r in report.filed]
    assert names == sorted(names) == ["a.md", "b.md", "c.md"]


# -- regression guards from Codex Phase 1B review 2026-05-03 ---------------


def test_path_traversal_blocked(tmp_path, monkeypatch):
    """Regression: Codex triage #5 — SECURITY.

    A malicious inbox file with `classifier_target: "../../../etc/passwd"`
    must be blocked. Must never write outside vault_root.
    """
    vault = _setup_agent(tmp_path, monkeypatch)
    # Create a target outside the vault
    outside = tmp_path / "outside-passwd.txt"
    outside.write_text("original content")

    _make_inbox_file(
        vault,
        target="../outside-passwd.txt",
        confidence=0.95,
        body="MALICIOUS CONTENT",
    )
    triager = Triager.for_agent("testbot")
    report = triager.run(dry_run=False)

    # Must be escalated, not filed
    assert len(report.filed) == 0
    assert len(report.escalated) == 1
    assert "escapes vault" in report.escalated[0].reason
    # Critical: outside file NOT modified
    assert outside.read_text() == "original content"


def test_section_false_match_in_body_ignored(tmp_path, monkeypatch):
    """Regression: Codex triage #3 — section detection was substring-based.

    A target file with body text mentioning the section name (e.g.
    "The Access rules are..." in prose) must NOT be mistaken for the
    `## Access` header.
    """
    vault = _setup_agent(tmp_path, monkeypatch)
    # Target has body text mentioning "Access" but no H2 section
    (vault / "sites" / "hanna.md").write_text(
        "# Hanna\n\nThe Access rules are complex.\n\n## Other\n\nUnrelated content.\n"
    )
    _make_inbox_file(
        vault, target="sites/hanna.md", section="Access",
        confidence=0.95, body="New access fact",
    )
    triager = Triager.for_agent("testbot")
    triager.run(dry_run=False)

    content = (vault / "sites" / "hanna.md").read_text()
    # Fact should be appended to EOF (no matching section), not mid-document
    assert content.rstrip().endswith("_Source: `_inbox/sample.md`_")
    # "The Access rules" still before any inserted content
    access_idx = content.index("The Access rules")
    new_fact_idx = content.index("New access fact")
    assert access_idx < new_fact_idx


def test_log_md_section_false_match_ignored(tmp_path, monkeypatch):
    """Regression: Codex triage #3 — LOG.md section detection.

    If body text contains the section phrase, previous substring search
    would splice new content mid-paragraph. Line-anchored regex must
    only match headers.
    """
    vault = _setup_agent(tmp_path, monkeypatch)
    (vault / "LOG.md").write_text(
        "# LOG\n\n## Other\n\nThe filing pass completed earlier today.\n"
    )
    (vault / "sites" / "hanna.md").write_text("")
    _make_inbox_file(vault, target="sites/hanna.md", confidence=0.95)

    triager = Triager.for_agent("testbot")
    triager.run(dry_run=False)

    content = (vault / "LOG.md").read_text()
    # Body text preserved, new "## Filing pass" header appended
    assert "The filing pass completed earlier today." in content
    assert "## Filing pass" in content


def test_dry_run_does_not_write_log_md(tmp_path, monkeypatch):
    """Regression: Codex triage #6 — dry-run must be fully side-effect-free."""
    vault = _setup_agent(tmp_path, monkeypatch)
    (vault / "sites" / "hanna.md").write_text("")
    _make_inbox_file(vault, target="sites/hanna.md", confidence=0.95)

    triager = Triager.for_agent("testbot")
    triager.run(dry_run=True)

    # LOG.md must not exist (even though we'd have results to log)
    assert not (vault / "LOG.md").exists()


def test_empty_string_section_treated_as_no_section(tmp_path, monkeypatch):
    """Regression: Codex triage #7 — empty string must normalise to None."""
    vault = _setup_agent(tmp_path, monkeypatch)
    (vault / "sites" / "hanna.md").write_text("# Hanna\n\n## Access\n\nExisting.\n")
    _make_inbox_file(
        vault, target="sites/hanna.md", section="",  # empty string
        confidence=0.95, body="Fact without section",
    )
    triager = Triager.for_agent("testbot")
    triager.run(dry_run=False)

    content = (vault / "sites" / "hanna.md").read_text()
    # Should NOT insert under ## Access; append to EOF
    access_idx = content.index("## Access")
    new_fact_idx = content.index("Fact without section")
    existing_idx = content.index("Existing.")
    # New fact comes AFTER the existing content (EOF append), not under Access
    assert existing_idx < new_fact_idx
