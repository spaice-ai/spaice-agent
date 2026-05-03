from __future__ import annotations

import datetime as dt
import os
import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List

import yaml

# ---------------------------------------------------------------------------
# Public type-alias for generator functions
# ---------------------------------------------------------------------------
DashboardGenerator = Callable[[Path], List[Dict[str, str]]]

# ---------------------------------------------------------------------------
# Public frozen data-classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Dashboard:
    """Description of a dashboard: its name, output path (relative to vault_root),
    and a generator function that returns row dicts."""

    name: str
    path: Path
    generator_fn: DashboardGenerator


@dataclass(frozen=True)
class DashboardResult:
    """Result of a single dashboard regeneration."""

    name: str
    rows: List[Dict[str, str]]
    ts_generated: str  # UTC ISO-8601
    ok: bool


# ---------------------------------------------------------------------------
# Internal helpers (file-system, text parsing)
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> Dict[str, Any]:
    """Extract YAML frontmatter from ``text`` (first ``---`` block)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = i
            break
    if end is None:
        return {}
    yaml_block = "\n".join(lines[1:end])
    try:
        result = yaml.safe_load(yaml_block)
        return result if isinstance(result, dict) else {}
    except yaml.YAMLError:
        return {}


def _first_heading(text: str) -> str:
    """Return the title of the first ``# ...`` heading, or ``""``."""
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _first_prose_line(text: str) -> str:
    """Return the first non-frontmatter, non-heading, non-code prose line (≤140 chars)."""
    in_front = False
    for line in text.splitlines():
        s = line.strip()
        if s == "---":
            in_front = not in_front
            continue
        if in_front or not s or s.startswith("#") or s.startswith("```"):
            continue
        return s[:140]
    return ""


def _human_ago(ts: dt.datetime) -> str:
    """Return a human-readable 'Xd ago', 'Xh ago', 'Xm ago' form.

    Blocker fix (Codex Phase 1C #1): use timezone-aware `now()` to prevent
    TypeError when `ts` has tzinfo.  If `ts` is naive, assume UTC.
    """
    now = dt.datetime.now(dt.timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.timezone.utc)
    delta = now - ts
    if delta.days > 1:
        return f"{delta.days}d ago"
    if delta.days == 1:
        return "1d ago"
    hours = delta.seconds // 3600
    if hours >= 1:
        return f"{hours}h ago"
    return f"{delta.seconds // 60}m ago"


def _atomic_write(file_path: Path, content: str) -> None:
    """Write *content* atomically using a same-directory temp file and ``os.replace``.

    Blocker fix (Codex Phase 1C #2): use PID-suffixed tmp in the target's
    parent directory so replace is always same-filesystem atomic. Windows
    os.replace still fails if target is held open by another process, but
    at least cross-fs copies are eliminated.
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = file_path.parent / f".{file_path.name}.tmp-{os.getpid()}"
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(str(tmp), str(file_path))
    except Exception:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# Built-in dashboard generators  (each takes vault_root -> list of row dicts)
# ---------------------------------------------------------------------------


def _gen_continuity(vault_root: Path) -> List[Dict[str, str]]:
    """Continuity dashboard: mtime of ``_continuity/LATEST.md`` + last ``## Next step``."""
    latest = vault_root / "_continuity" / "LATEST.md"
    if not latest.exists():
        return []

    mtime = dt.datetime.fromtimestamp(latest.stat().st_mtime).isoformat()
    try:
        text = latest.read_text(encoding="utf-8")
    except Exception:
        return []

    # Extract the line following the last "## Next step" heading
    next_step = ""
    in_block = False
    for line in text.splitlines():
        if line.strip().startswith("## Next step"):
            in_block = True
            continue
        if in_block:
            stripped = line.strip()
            if stripped.startswith("##") or stripped == "":
                continue
            next_step = stripped
            break

    return [{"mtime": mtime, "next_step": next_step or "—"}]


def _gen_inbox(vault_root: Path) -> List[Dict[str, str]]:
    """Inbox dashboard: count drafts in ``_inbox/*.md`` grouped by ``_tag``."""
    inbox_dir = vault_root / "_inbox"
    if not inbox_dir.exists():
        return []

    tag_counts: Dict[str, int] = {}
    for file in inbox_dir.glob("*.md"):
        try:
            text = file.read_text(encoding="utf-8")
        except Exception:
            continue
        fm = _parse_frontmatter(text)
        tag = str(fm.get("_tag", "untagged")).strip()
        if not tag:
            tag = "untagged"
        tag_counts[tag] = tag_counts.get(tag, 0) + 1

    return [{"tag": tag, "count": str(count)} for tag, count in sorted(tag_counts.items())]


def _gen_projects(vault_root: Path) -> List[Dict[str, str]]:
    """Projects dashboard: every ``projects/**/*.md`` except INDEX.md,
    with frontmatter status and last-modified timestamp."""
    projects_dir = vault_root / "projects"
    if not projects_dir.exists():
        return []

    rows: List[Dict[str, str]] = []
    for file in projects_dir.glob("**/*.md"):
        if file.name == "INDEX.md":
            continue
        try:
            text = file.read_text(encoding="utf-8")
        except Exception:
            continue

        fm = _parse_frontmatter(text)
        status = fm.get("status", "unknown")
        if isinstance(status, list):
            status = ", ".join(status)
        title = _first_heading(text) or file.stem
        mtime = dt.datetime.fromtimestamp(file.stat().st_mtime).isoformat()
        rel = file.relative_to(vault_root)
        wikilink = f"[[{rel.as_posix()}|{file.stem}]]"
        rows.append(
            {
                "project": wikilink,
                "status": status,
                "title": title,
                "last_modified": mtime,
            }
        )

    rows.sort(key=lambda r: r["last_modified"], reverse=True)
    return rows


def _gen_doctrines(vault_root: Path) -> List[Dict[str, str]]:
    """Doctrines dashboard: one row per ``doctrines/**/*.md``,
    counts rules by device-class from frontmatter ``device_class`` and ``rules``."""
    doctrines_dir = vault_root / "doctrines"
    if not doctrines_dir.exists():
        return []

    rows: List[Dict[str, str]] = []
    for file in doctrines_dir.glob("**/*.md"):
        if file.name == "INDEX.md":
            continue
        try:
            text = file.read_text(encoding="utf-8")
        except Exception:
            continue

        fm = _parse_frontmatter(text)
        device_class = fm.get("device_class", "none")
        if isinstance(device_class, list):
            device_class = ", ".join(device_class)
        rules = fm.get("rules", [])
        rule_count = len(rules) if isinstance(rules, list) else 0
        title = fm.get("title") or _first_heading(text) or file.stem
        rel = file.relative_to(vault_root)
        wikilink = f"[[{rel.as_posix()}|{title}]]"
        rows.append(
            {
                "doctrine": wikilink,
                "device_class": device_class,
                "rule_count": str(rule_count),
            }
        )

    return rows


def _gen_corrections(vault_root: Path) -> List[Dict[str, str]]:
    """Corrections dashboard: all ``corrections/*.md`` sorted by numeric prefix,
    with reinforcement count and status extracted from the file body."""
    corrections_dir = vault_root / "corrections"
    if not corrections_dir.exists():
        return []

    rows: List[Dict[str, str]] = []
    files = sorted(p for p in corrections_dir.glob("*.md") if p.name != "README.md")
    for file in files:
        try:
            text = file.read_text(encoding="utf-8")
        except Exception:
            continue

        # Numeric prefix from the filename  NNN-something.md
        num_match = re.match(r"^(\d+)-", file.stem)
        num = num_match.group(1) if num_match else "?"

        title = _first_heading(text) or file.stem

        rc_match = re.search(r"reinforcement_count:\s*(\d+)", text)
        rc = rc_match.group(1) if rc_match else "0"

        status_match = re.search(r"^status:\s*(\w+)", text, re.MULTILINE)
        status = status_match.group(1) if status_match else "active"

        wikilink = f"[[{file.relative_to(vault_root).as_posix()}|{title}]]"
        rows.append(
            {
                "number": num,
                "title": wikilink,
                "reinforcement_count": rc,
                "status": status,
            }
        )

    return rows


def _gen_library(vault_root: Path) -> List[Dict[str, str]]:
    """Library dashboard: reads the pre-generated library-index.yaml from _dashboard."""
    index_path = vault_root / "_dashboard" / "library-index.yaml"
    if not index_path.exists():
        return []

    try:
        raw = yaml.safe_load(index_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    if isinstance(raw, list):
        entries = raw
    elif isinstance(raw, dict):
        entries = raw.get("entries", [])
    else:
        entries = []

    rows: List[Dict[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path", "")
        title = entry.get("title") or (Path(path).stem if path else "?")
        tags = entry.get("tags", [])
        if isinstance(tags, list):
            tags_str = ", ".join(str(t) for t in tags)
        else:
            tags_str = str(tags)
        summary = entry.get("summary", "")
        if isinstance(summary, str) and len(summary) > 200:
            summary = summary[:200] + "…"
        wikilink = f"[[{path}|{title}]]" if path else title
        rows.append(
            {
                "title": wikilink,
                "tags": tags_str,
                "summary": summary or "",
            }
        )

    return rows


# ---------------------------------------------------------------------------
# Registry of built-in dashboards – populated at import time
# ---------------------------------------------------------------------------

DASHBOARDS: Dict[str, Dashboard] = {
    "continuity": Dashboard(
        name="continuity",
        path=Path("_dashboard") / "continuity.md",
        generator_fn=_gen_continuity,
    ),
    "inbox": Dashboard(
        name="inbox",
        path=Path("_dashboard") / "inbox.md",
        generator_fn=_gen_inbox,
    ),
    "projects": Dashboard(
        name="projects",
        path=Path("_dashboard") / "projects.md",
        generator_fn=_gen_projects,
    ),
    "doctrines": Dashboard(
        name="doctrines",
        path=Path("_dashboard") / "doctrines.md",
        generator_fn=_gen_doctrines,
    ),
    "corrections": Dashboard(
        name="corrections",
        path=Path("_dashboard") / "corrections.md",
        generator_fn=_gen_corrections,
    ),
    "library": Dashboard(
        name="library",
        path=Path("_dashboard") / "library.md",
        generator_fn=_gen_library,
    ),
}


# ---------------------------------------------------------------------------
# Public API – regeneration logic
# ---------------------------------------------------------------------------


def _render_table(rows: List[Dict[str, str]]) -> str:
    """Turn a list of uniform dicts into a Markdown table string."""
    if not rows:
        return ""
    keys = list(rows[0].keys())
    header = "| " + " | ".join(keys) + " |"
    sep = "| " + " | ".join("---" for _ in keys) + " |"
    lines = [header, sep]
    for row in rows:
        vals = [str(row.get(k, "")).replace("|", "\\|") for k in keys]
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def regenerate_all(vault_root: Path) -> List[DashboardResult]:
    """Regenerate every registered dashboard inside *vault_root*."""
    results: List[DashboardResult] = []
    for name in DASHBOARDS:
        results.append(regenerate_one(name, vault_root))
    return results


def regenerate_one(name: str, vault_root: Path) -> DashboardResult:
    """Regenerate a single dashboard by *name*.

    Returns a :class:`DashboardResult` detailing the outcome.  A failing
    generator does **not** raise; instead ``ok`` is set to ``False`` and a
    warning is emitted.
    """
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    dashboard = DASHBOARDS.get(name)
    if dashboard is None:
        warnings.warn(f"Dashboard '{name}' is not registered – skipping")
        return DashboardResult(name=name, rows=[], ts_generated=now, ok=False)

    # Invoke generator, wrapped to never propagate exceptions
    try:
        rows = dashboard.generator_fn(vault_root)
    except Exception as exc:
        warnings.warn(f"Dashboard '{name}' generator failed: {exc}")
        return DashboardResult(name=name, rows=[], ts_generated=now, ok=False)

    # Build Markdown content
    comment = "<!-- GENERATED by spaice-agent dashboards; do not edit -->\n"
    ts_line = f"<!-- Generated at: {now} -->\n\n"
    table = _render_table(rows)
    content = comment + ts_line + table + "\n"

    # Write atomically
    output_path = vault_root / dashboard.path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _atomic_write(output_path, content)
    except Exception as exc:
        warnings.warn(f"Failed to write dashboard '{name}' to {output_path}: {exc}")
        # Return partial success – the rows were generated but not persisted
        return DashboardResult(name=name, rows=rows, ts_generated=now, ok=False)

    return DashboardResult(name=name, rows=rows, ts_generated=now, ok=True)