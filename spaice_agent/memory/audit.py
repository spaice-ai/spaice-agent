from __future__ import annotations

import datetime as dt
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditFinding:
    severity: Literal["info", "warn", "error"]
    path: str            # relative path from vault_root (or `<vault>` for global)
    message: str

    def __lt__(self, other: AuditFinding) -> bool:
        order = {"error": 0, "warn": 1, "info": 2}
        if order[self.severity] != order[other.severity]:
            return order[self.severity] < order[other.severity]
        return self.path < other.path


@dataclass(frozen=True)
class AuditReport:
    findings: List[AuditFinding]
    counts: Dict[str, int]   # error, warn, info
    ts: str                  # UTC ISO 8601 timestamp

    def __post_init__(self) -> None:
        # Ensure counts match contents – defensive
        expected = {"error": 0, "warn": 0, "info": 0}
        for f in self.findings:
            expected[f.severity] += 1
        if self.counts != expected:
            raise ValueError("counts do not match findings")


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _scan_md_files(vault_root: Path) -> List[Path]:
    """Fast walk to collect all .md files (exclude hidden dirs)."""
    files: List[Path] = []
    try:
        for path in vault_root.rglob("*.md"):
            # Skip hidden directories
            if any(part.startswith(".") for part in path.relative_to(vault_root).parts):
                continue
            # Skip _archive if we don't want to check it? We'll still walk but some checks ignore.
            files.append(path)
    except PermissionError as _audit_exc:
        logger.debug("audit: caught %s: %s", type(_audit_exc).__name__, _audit_exc)
    return files


def _build_path_index(vault_root: Path) -> Tuple[Dict[str, Path], Dict[str, Path]]:
    """
    Returns (rel_path_map, stem_map)
    rel_path_map: maps relative posix string to full Path
    stem_map: maps stem (filename without .md) to full Path (first match wins)
    """
    rel_path_map: Dict[str, Path] = {}
    stem_map: Dict[str, Path] = {}
    for full in _scan_md_files(vault_root):
        rel = full.relative_to(vault_root).as_posix()
        rel_path_map[rel] = full
        stem = full.stem  # without .md
        if stem not in stem_map:
            stem_map[stem] = full
        # also consider stem as potential target for wikilinks
    return rel_path_map, stem_map


def _extract_wikilinks(content: str) -> List[str]:
    """Return list of targets from [[...]] wikilinks, ignoring aliases."""
    targets: List[str] = []
    for m in re.finditer(r"\[\[([^\]]+)\]\]", content):
        raw = m.group(1)
        # Split on |, #, /? Simple: use everything before first | or #
        target = raw.split("|")[0].split("#")[0].strip()
        if target:
            targets.append(target)
    return targets


# ---------------------------------------------------------------------------
# Built-in checks
# ---------------------------------------------------------------------------

def check_orphaned_inbox(vault_root: Path) -> List[AuditFinding]:
    """Flag _inbox/*.md older than 7 days."""
    findings: List[AuditFinding] = []
    inbox_dir = vault_root / "_inbox"
    if not inbox_dir.is_dir():
        return findings
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(days=7)
    for md_file in inbox_dir.glob("*.md"):
        if any(part.startswith(".") for part in md_file.parts):
            continue
        try:
            mtime = dt.datetime.fromtimestamp(md_file.stat().st_mtime, tz=dt.timezone.utc)
        except OSError as _audit_exc:
            logger.debug("audit orphaned_inbox: stat failed: %s", _audit_exc)
            continue
        if mtime < cutoff:
            age_days = (now - mtime).days
            findings.append(AuditFinding(
                severity="warn",
                path=md_file.relative_to(vault_root).as_posix(),
                message=f"Orphaned inbox draft older than 7 days (age: {age_days}d)",
            ))
    return findings


def check_duplicate_files(vault_root: Path) -> List[AuditFinding]:
    """Detect identical filenames in different vault directories."""
    findings: List[AuditFinding] = []
    # Group by filename (the last path component, e.g., 'note.md')
    name_map: Dict[str, List[Path]] = defaultdict(list)
    for full in _scan_md_files(vault_root):
        name = full.name
        name_map[name].append(full)

    for fname, paths in name_map.items():
        if len(paths) <= 1:
            continue
        # Check if they reside in at least two distinct top-level dirs
        top_dirs = {p.relative_to(vault_root).parts[0] for p in paths if len(p.relative_to(vault_root).parts) >= 1}
        if len(top_dirs) < 2:
            continue
        for p in paths:
            rel = p.relative_to(vault_root).as_posix()
            findings.append(AuditFinding(
                severity="warn",
                path=rel,
                message=f"Duplicate filename '{fname}' found in multiple shelves: {', '.join(sorted(top_dirs))}",
            ))
    return findings


def check_missing_frontmatter(vault_root: Path) -> List[AuditFinding]:
    """Flag .md in identity/, projects/, sites/ without YAML frontmatter."""
    findings: List[AuditFinding] = []
    target_dirs = {"identity", "projects", "sites"}
    for full in _scan_md_files(vault_root):
        # Only inside those top-level directories
        parts = full.relative_to(vault_root).parts
        if not parts or parts[0] not in target_dirs:
            continue
        # Check if file starts with '---'
        try:
            with open(full, encoding="utf-8") as fh:
                first_line = fh.readline().strip()
                if first_line != "---":
                    findings.append(AuditFinding(
                        severity="warn",
                        path=full.relative_to(vault_root).as_posix(),
                        message="Missing YAML frontmatter block (no leading '---')",
                    ))
        except (OSError, UnicodeDecodeError) as _audit_exc:
            logger.debug("audit frontmatter: read failed for %s: %s", f, _audit_exc)
            # can't read? not a text file - skip
    return findings


def check_broken_wikilinks(vault_root: Path) -> List[AuditFinding]:
    """Flag [[links]] that don't resolve to any existing .md file."""
    rel_paths, stems = _build_path_index(vault_root)
    findings: List[AuditFinding] = []

    for full in _scan_md_files(vault_root):
        try:
            content = full.read_text(encoding="utf-8", errors="ignore")
        except OSError as _audit_exc:
            logger.debug("audit wikilinks: read failed for %s: %s", md, _audit_exc)
            continue
        targets = _extract_wikilinks(content)
        for target in targets:
            # Resolve: exact rel path, or target.md, or stem match
            # Target might contain directory separators: 'sub/file'
            target_path = target
            if target_path.endswith(".md"):
                target_path = target_path
            else:
                target_path = target + ".md"
            # Check as full relative posix path
            if target_path in rel_paths:
                continue
            # Check if target (without .md) matches any known stem
            if target in stems:
                continue
            # Also try if target starts with './' etc.
            clean = target.lstrip("./")
            if clean + ".md" in rel_paths or clean in stems:
                continue
            # broken
            rel = full.relative_to(vault_root).as_posix()
            findings.append(AuditFinding(
                severity="warn",
                path=rel,
                message=f"Broken wikilink: [[{target}]] — target file not found",
            ))
    return findings


def check_stale_dashboard(vault_root: Path) -> List[AuditFinding]:
    """Check if dashboard mtime is older than source data max mtime."""
    findings: List[AuditFinding] = []
    dash_dir = vault_root / "_dashboard"
    if not dash_dir.is_dir():
        return findings

    # Map dashboard stem to source directory patterns
    mapping = {
        "continuity": "_continuity",
        "inbox": "_inbox",
        "projects": "projects",
        "doctrines": "doctrines",
        "corrections": "corrections",
        "library": "library",
    }

    for dash_file in dash_dir.glob("*.md"):
        stem = dash_file.stem
        source_dir_name = mapping.get(stem)
        if not source_dir_name:
            continue
        source_dir = vault_root / source_dir_name
        if not source_dir.is_dir():
            continue
        try:
            dash_mtime = dash_file.stat().st_mtime
        except OSError as _audit_exc:
            logger.debug("audit stale_dashboard: dash stat failed: %s", _audit_exc)
            continue

        # Find newest mtime inside source directory (recursive)
        newest_source_mtime = 0.0
        try:
            for src_file in source_dir.rglob("*.md"):
                if any(part.startswith(".") for part in src_file.relative_to(vault_root).parts):
                    continue
                src_mtime = src_file.stat().st_mtime
                if src_mtime > newest_source_mtime:
                    newest_source_mtime = src_mtime
        except (OSError, PermissionError) as _audit_exc:
            logger.debug("audit stale_dashboard: src walk failed: %s", _audit_exc)
            continue

        if newest_source_mtime > dash_mtime:
            findings.append(AuditFinding(
                severity="warn",
                path=dash_file.relative_to(vault_root).as_posix(),
                message=(
                    f"Dashboard is stale (source data in '{source_dir_name}/' "
                    f"is newer than dashboard)"
                ),
            ))
    return findings


def check_missing_continuity(vault_root: Path) -> List[AuditFinding]:
    """Ensure _continuity/LATEST.md exists."""
    latest = vault_root / "_continuity" / "LATEST.md"
    if not latest.is_file():
        return [AuditFinding(
            severity="error",
            path="_continuity/LATEST.md",
            message="Missing _continuity/LATEST.md — session-resume point not available",
        )]
    return []


def check_empty_shelves(vault_root: Path) -> List[AuditFinding]:
    """Flag content shelves that contain no files at all (excluding _inbox, _archive)."""
    findings: List[AuditFinding] = []
    exclude = {"_inbox", "_archive"}
    try:
        for entry in vault_root.iterdir():
            if not entry.is_dir():
                continue
            name = entry.name
            if name in exclude or name.startswith("."):
                continue
            # Check if it contains *any* files (recursively)
            has_files = False
            try:
                for _ in entry.rglob("*"):
                    if _.is_file():
                        has_files = True
                        break
            except PermissionError as _audit_exc:
                logger.debug("audit empty_shelves: iterdir failed: %s", _audit_exc)
                continue
            if not has_files:
                findings.append(AuditFinding(
                    severity="warn",
                    path=f"{name}/",
                    message=f"Empty shelf '{name}' — no files found",
                ))
    except PermissionError as _audit_exc:
        logger.debug("audit: caught %s: %s", type(_audit_exc).__name__, _audit_exc)
    return findings


# ---------------------------------------------------------------------------
# Registry of all built-in checks
# ---------------------------------------------------------------------------

CHECKS: Dict[str, callable] = {
    "orphaned_inbox": check_orphaned_inbox,
    "duplicate_files": check_duplicate_files,
    "missing_frontmatter": check_missing_frontmatter,
    "broken_wikilinks": check_broken_wikilinks,
    "stale_dashboard": check_stale_dashboard,
    "missing_continuity": check_missing_continuity,
    "empty_shelves": check_empty_shelves,
}


# ---------------------------------------------------------------------------
# Main audit entry point
# ---------------------------------------------------------------------------

def audit_vault(vault_root: Path) -> AuditReport:
    """Run all registered checks and return a sorted report."""
    all_findings: List[AuditFinding] = []
    for name, check_fn in CHECKS.items():
        try:
            findings = check_fn(vault_root)
        except Exception as _audit_exc:
            logger.warning("audit: check %s raised: %s", check_name, _audit_exc, exc_info=True)
            # We must not raise; treat as error finding for the check itself
            findings = [AuditFinding(
                severity="error",
                path=f"<vault> (check: {name})",
                message=f"Internal error while running audit check '{name}'",
            )]
        all_findings.extend(findings)

    # Sort by severity, then path
    all_findings.sort()

    counts = {"error": 0, "warn": 0, "info": 0}
    for f in all_findings:
        counts[f.severity] += 1

    ts = dt.datetime.now(dt.timezone.utc).isoformat()

    return AuditReport(findings=all_findings, counts=counts, ts=ts)