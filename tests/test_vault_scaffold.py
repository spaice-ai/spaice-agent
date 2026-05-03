"""Tests for spaice_agent/memory/vault.py — vault scaffold module (Phase 2C)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from spaice_agent.memory.paths import VaultPaths
from spaice_agent.memory.vault import (
    ScaffoldAction,
    ScaffoldReport,
    VaultScaffoldError,
    _TEMPLATES,
    is_scaffolded,
    scaffold_vault,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault_paths(tmp_path: Path) -> VaultPaths:
    """Return a VaultPaths with skeleton already created."""
    vp = VaultPaths.for_vault(tmp_path, agent_id="_testagent")
    vp.ensure_skeleton()
    return vp


# ---------------------------------------------------------------------------
# Core behaviour
# ---------------------------------------------------------------------------


def test_fresh_vault_creates_all_21_files(vault_paths: VaultPaths):
    report = scaffold_vault(vault_paths)

    # Spec: 3 vault-root files + 9 shelf READMEs + 4 special READMEs + 4 templates + 1 identity/SOUL.md = 21
    assert len(_TEMPLATES) == 21, (
        f"scaffold template count changed — expected 21, got {len(_TEMPLATES)}"
    )
    assert report.created_count == 21
    assert report.skipped_count == 0
    assert report.overwrote_count == 0
    assert report.dry_run is False
    # Every file physically exists
    for rel_path in _TEMPLATES:
        assert (vault_paths.vault_root / rel_path).is_file()


def test_vault_root_missing_raises(tmp_path: Path):
    # Craft a VaultPaths pointing to a real dir, then remove it
    vp = VaultPaths.for_vault(tmp_path, agent_id="_testagent")
    # Can't easily destroy vault_root on a frozen dataclass — simulate by
    # creating a VaultPaths with a path we delete:
    (tmp_path / "bogus").mkdir()
    vp_bogus = VaultPaths.for_vault(tmp_path / "bogus", agent_id="_testagent")
    (tmp_path / "bogus").rmdir()
    with pytest.raises(VaultScaffoldError, match="Vault root does not exist"):
        scaffold_vault(vp_bogus)


def test_skeleton_missing_inbox_raises(tmp_path: Path):
    # Skeleton created, then _inbox removed
    vp = VaultPaths.for_vault(tmp_path, agent_id="_testagent")
    vp.ensure_skeleton()
    (tmp_path / "_inbox").rmdir()
    with pytest.raises(VaultScaffoldError, match="Inbox missing"):
        scaffold_vault(vp)


def test_idempotent_second_run_skips_all(vault_paths: VaultPaths):
    first = scaffold_vault(vault_paths)
    assert first.created_count == 21

    second = scaffold_vault(vault_paths)
    assert second.created_count == 0
    assert second.skipped_count == 21
    assert second.overwrote_count == 0
    assert all(a.action == "skipped_existed" for a in second.actions)


def test_overwrite_flag_replaces_existing(vault_paths: VaultPaths):
    scaffold_vault(vault_paths)
    # Mutate a file
    target = vault_paths.vault_root / "CONVENTIONS.md"
    target.write_text("USER EDIT — should be overwritten\n")

    report = scaffold_vault(vault_paths, overwrite=True)
    assert report.overwrote_count == 21
    assert report.skipped_count == 0
    assert "USER EDIT" not in target.read_text()
    assert "Required frontmatter" in target.read_text()


def test_default_preserves_user_edits(vault_paths: VaultPaths):
    scaffold_vault(vault_paths)
    target = vault_paths.vault_root / "corrections/README.md"
    target.write_text("USER EDIT — must survive\n")

    report = scaffold_vault(vault_paths)  # default overwrite=False
    assert report.overwrote_count == 0
    assert "USER EDIT" in target.read_text()


def test_dry_run_creates_no_files(vault_paths: VaultPaths):
    report = scaffold_vault(vault_paths, dry_run=True)

    assert report.dry_run is True
    assert all(a.action == "would_create" for a in report.actions)
    # No file was written
    for rel_path in _TEMPLATES:
        assert not (vault_paths.vault_root / rel_path).exists()


def test_is_scaffolded_false_before_true_after(vault_paths: VaultPaths):
    assert is_scaffolded(vault_paths) is False
    scaffold_vault(vault_paths)
    assert is_scaffolded(vault_paths) is True


def test_atomic_write_failure_leaves_no_tmp(
    vault_paths: VaultPaths, monkeypatch: pytest.MonkeyPatch
):
    """If os.replace raises, the tmp file must not leak."""
    import os as _os

    from spaice_agent.memory import dashboards as dashboards_mod

    real_replace = _os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("simulated replace failure")
        return real_replace(src, dst)

    monkeypatch.setattr(dashboards_mod.os, "replace", flaky_replace)

    with pytest.raises(OSError):
        scaffold_vault(vault_paths)

    # Walk vault for any leftover .tmp-* files
    leftovers = list(vault_paths.vault_root.rglob(".*tmp-*"))
    assert leftovers == [], f"tmp file leaked: {leftovers}"


def test_action_ordering_is_deterministic(tmp_path: Path):
    """Two fresh scaffolds produce actions in the same order."""
    vp1_root = tmp_path / "a"
    vp1_root.mkdir()
    vp1 = VaultPaths.for_vault(vp1_root, agent_id="_a")
    vp1.ensure_skeleton()

    vp2_root = tmp_path / "b"
    vp2_root.mkdir()
    vp2 = VaultPaths.for_vault(vp2_root, agent_id="_b")
    vp2.ensure_skeleton()

    order1 = [str(a.path.relative_to(vp1.vault_root)) for a in scaffold_vault(vp1).actions]
    order2 = [str(a.path.relative_to(vp2.vault_root)) for a in scaffold_vault(vp2).actions]
    assert order1 == order2


def test_content_has_no_business_strings(vault_paths: VaultPaths):
    """Scaffold content must be generic — no personal / client business strings.

    Note: the framework name 'spaice-agent' is allowed because the shipped
    templates legitimately reference the package's own CLI (e.g.
    `spaice-agent recall`). This test guards against leaking *user-specific*
    business data (SPAICE company, Tron, Jozef) and adjacent platform names
    (Hermes) into a generic scaffold.
    """
    scaffold_vault(vault_paths)
    banned = ("SPAICE", "Tron", "Jozef", "Hermes")
    for rel_path in _TEMPLATES:
        content = (vault_paths.vault_root / rel_path).read_text()
        for bad in banned:
            assert bad not in content, (
                f"business-specific string {bad!r} found in {rel_path}"
            )


def test_bundled_skills_have_no_business_strings():
    """Every bundled skill ships to end users — no user-specific strings
    allowed in the SKILL.md or its README/references.

    'Hermes' is allowed here because some bundled skills legitimately
    describe the runtime (spaice-agent rides on Hermes). The stricter
    rules apply to company/person names that leak internal identity.

    Case-insensitive match: we ban the tokens in any case (`SPAICE`,
    `spaice`, `Spaice` all fail). Exception: lowercase `spaice` as
    part of the `spaice-agent` package name is allowed via a regex
    word boundary check (substring `spaice-agent` OR `spaice_agent`
    is fine; standalone `spaice` as company reference is not).
    """
    import re as _re
    from pathlib import Path as _Path
    skills_dir = _Path(__file__).resolve().parent.parent / "spaice_agent" / "bundled_skills"

    # Case-insensitive banned tokens. Each entry is (token, allowed_contexts).
    # `spaice` is allowed in these contexts:
    #   - `spaice-agent` / `spaice_agent` (the package's own name)
    #   - `spaice-build-stack`, `spaice-continuity`, `spaice-memory`,
    #     `spaice-conventions`, etc. — bundled skill names.
    # Explicitly DENIED despite matching the skill-name pattern:
    #   - `spaice-ai` (company/org name)
    #   - `spaice.ai` (domain)
    #   - `spaice-*` as a glob pattern is allowed (documentation usage)
    # The allow pattern is a whitelist of safe forms; the deny list below
    # runs first to kick out known company/domain references regardless.
    banned_tokens = [
        ("spaice", [
            _re.compile(r"spaice[-_][a-z][a-z-_]+", _re.IGNORECASE),
            _re.compile(r"spaice-\*", _re.IGNORECASE),
        ]),
        ("tron", []),
        ("jozef", []),
    ]

    # Hard deny list — forms that match the allow pattern but are
    # explicitly business-branding. Checked first, overrides the allow.
    hard_deny_patterns = [
        _re.compile(r"spaice-ai", _re.IGNORECASE),
        _re.compile(r"spaice\.ai", _re.IGNORECASE),
    ]

    # Upstream MIT-imported skills (third-party content) preserved verbatim.
    # joe-guidelines is NOT exempt — it ships under our package umbrella
    # and is scanned. Upstream office-suite + gmail retain their original
    # upstream content which may reference their project names.
    upstream_exempt = {"antigravity", "pdf", "docx", "xlsx", "pptx", "gmail"}

    offenders: list[tuple[str, str, str]] = []
    for skill_dir in skills_dir.iterdir():
        if not skill_dir.is_dir() or skill_dir.name in upstream_exempt:
            continue
        for md_file in skill_dir.rglob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            rel = md_file.relative_to(skills_dir)

            for token, allowed in banned_tokens:
                # Find all case-insensitive matches of the token as a word
                pattern = _re.compile(r"\b" + _re.escape(token) + r"\b", _re.IGNORECASE)
                for match in pattern.finditer(content):
                    # Check if this match is inside any allowed context
                    match_line_start = content.rfind("\n", 0, match.start()) + 1
                    match_line_end = content.find("\n", match.end())
                    if match_line_end == -1:
                        match_line_end = len(content)
                    line = content[match_line_start:match_line_end]

                    relative_start = match.start() - match_line_start

                    # Hard-deny runs FIRST — any line containing the deny
                    # patterns is an offender regardless of allow status
                    denied = False
                    for deny_pat in hard_deny_patterns:
                        for deny_match in deny_pat.finditer(line):
                            if deny_match.start() <= relative_start < deny_match.end():
                                denied = True
                                break
                        if denied:
                            break
                    if denied:
                        offenders.append((str(rel), token, line.strip()))
                        break  # one offender per token per file is enough signal

                    # Otherwise: is this match covered by an allowed pattern?
                    covered = False
                    for allow_pat in allowed:
                        for allow_match in allow_pat.finditer(line):
                            if allow_match.start() <= relative_start < allow_match.end():
                                covered = True
                                break
                        if covered:
                            break
                    if covered:
                        continue

                    offenders.append((str(rel), token, line.strip()))
                    break  # one offender per token per file is enough signal

    assert not offenders, (
        "Business-specific strings leaked into bundled skills "
        f"(banned tokens: {[t[0] for t in banned_tokens]}, case-insensitive):\n" +
        "\n".join(f"  {path}: {tok!r} → {line[:120]}"
                  for path, tok, line in offenders)
    )


def test_concurrent_scaffolds_both_succeed(tmp_path: Path):
    """Two threads scaffolding different vaults concurrently — both succeed."""
    errors: list[Exception] = []

    def worker(sub: str):
        try:
            root = tmp_path / sub
            root.mkdir()
            vp = VaultPaths.for_vault(root, agent_id=f"_{sub}")
            vp.ensure_skeleton()
            scaffold_vault(vp)
        except Exception as exc:  # noqa: BLE001 — test harness
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(s,)) for s in ("alpha", "beta")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"concurrent scaffold raised: {errors}"
    for s in ("alpha", "beta"):
        assert is_scaffolded(
            VaultPaths.for_vault(tmp_path / s, agent_id=f"_{s}")
        )


# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------


def test_summary_line_formats(vault_paths: VaultPaths):
    report = scaffold_vault(vault_paths)
    assert report.summary_line() == "created 21"

    report2 = scaffold_vault(vault_paths)
    assert report2.summary_line() == "skipped 21"


def test_category_attribution(vault_paths: VaultPaths):
    report = scaffold_vault(vault_paths)
    cats = {str(a.path.relative_to(vault_paths.vault_root)): a.category for a in report.actions}
    assert cats["README.md"] == "convention"
    assert cats["CONVENTIONS.md"] == "convention"
    assert cats["CATEGORISATION.md"] == "convention"
    assert cats["identity/README.md"] == "shelf_readme"
    assert cats["_inbox/README.md"] == "special_readme"
    assert cats["_templates/note.md"] == "template"
