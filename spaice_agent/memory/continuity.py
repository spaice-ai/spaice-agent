from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


@dataclass(frozen=True)
class ContinuityBlock:
    """Immutable representation of the continuity pickup point."""

    goal: str
    progress: str
    open_threads: List[str]
    next_step: str
    ts: str  # ISO 8601 UTC


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _most_recent_session_file(vault_root: Path) -> Optional[Path]:
    """Return the most-recent .md file under _archive/sessions, or None."""
    sessions_dir = vault_root / "_archive" / "sessions"
    if not sessions_dir.is_dir():
        return None
    files = list(sessions_dir.glob("*.md"))
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def _parse_session_summary(filepath: Path) -> tuple[str, List[str]]:
    """Extract Goal text and Outstanding-thread items from a session summary."""
    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception:
        return "", []

    lines = content.splitlines()
    goal_lines: List[str] = []
    thread_lines: List[str] = []
    in_goal = False
    in_threads = False

    for line in lines:
        if line.startswith("## Goal"):
            in_goal = True
            in_threads = False
            continue
        if line.startswith("## Outstanding threads"):
            in_goal = False
            in_threads = True
            continue
        # Any other ## / # heading ends the current section
        if line.startswith("## ") or line.startswith("# "):
            in_goal = False
            in_threads = False
        if in_goal:
            goal_lines.append(line)
        elif in_threads:
            stripped = line.lstrip()
            if stripped.startswith("- "):
                thread_lines.append(stripped[2:].strip())

    goal = "\n".join(goal_lines).strip()
    return goal, thread_lines


def _top_inbox_summaries(vault_root: Path, top: int = 3) -> List[str]:
    """One-line summaries for the top `top` inbox files by mtime."""
    inbox_dir = vault_root / "_inbox"
    if not inbox_dir.is_dir():
        return []

    md_files = sorted(inbox_dir.glob("*.md"),
                      key=lambda p: p.stat().st_mtime, reverse=True)[:top]
    summaries: List[str] = []

    for f in md_files:
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue

        # Strip YAML frontmatter if present
        if text.startswith("---"):
            idx = text.find("---", 3)
            if idx != -1:
                text = text[idx + 3:]

        # First meaningful line that isn't a heading
        first_line = f.stem  # fallback
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                first_line = stripped
                break

        summary = first_line[:120]
        summaries.append(f"Inbox: {f.stem} — {summary}")

    return summaries


def _top_dashboard_todos(vault_root: Path, top: int = 5) -> List[str]:
    """Collect top `top` unchecked TODO items from dashboard files."""
    dash_dir = vault_root / "_dashboard"
    if not dash_dir.is_dir():
        return []

    todo_lines: List[str] = []
    for mdf in sorted(dash_dir.glob("*.md")):
        try:
            content = mdf.read_text(encoding="utf-8")
        except Exception:
            continue
        for line in content.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("- [ ] "):
                todo_lines.append(stripped[6:].strip())
                if len(todo_lines) >= top:
                    break
        if len(todo_lines) >= top:
            break

    return todo_lines


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_latest(vault_root: Path) -> ContinuityBlock:
    """Build a :class:`ContinuityBlock` from the current vault state.

    Fusion rules (order of precedence):
    1. Most-recent session summary (``_archive/sessions``) →
       ``## Goal`` and ``## Outstanding threads``.
    2. Top 3 inbox files by mtime, each summarised as ``Inbox: <stem> — <summary>``.
    3. Up to 5 ``- [ ] `` unchecked-todo lines from ``_dashboard/*.md``.

    If no session summary exists a skeleton with ``TODO`` markers is returned.
    """
    session_file = _most_recent_session_file(vault_root)
    now_ts = datetime.now(timezone.utc).isoformat()

    if session_file is not None:
        goal, session_threads = _parse_session_summary(session_file)
        open_threads: List[str] = list(session_threads)
        progress = f"Latest session recorded: {session_file.name}"
    else:
        # Skeleton
        goal = "TODO: Set session goal"
        progress = "TODO: Record progress"
        open_threads = []

    # Inbox threads
    open_threads.extend(_top_inbox_summaries(vault_root))

    # Dashboard TODOs
    open_threads.extend(_top_dashboard_todos(vault_root))

    # Decide next step
    if session_file is not None:
        next_step = open_threads[0] if open_threads else "Review open threads."
    else:
        next_step = "TODO: Identify next action"

    return ContinuityBlock(
        goal=goal,
        progress=progress,
        open_threads=open_threads,
        next_step=next_step,
        ts=now_ts,
    )


def write_latest(block: ContinuityBlock, vault_root: Path) -> Path:
    """Atomically write *block* to ``<vault_root>/_continuity/LATEST.md``."""
    continuity_dir = vault_root / "_continuity"
    continuity_dir.mkdir(parents=True, exist_ok=True)

    threads_md = "\n".join(f"- {t}" for t in block.open_threads) if block.open_threads else "- (none)"

    content = f"""# LATEST — continuity pickup point

<!-- GENERATED by spaice-agent continuity; editable by agent but regenerated on mine -->

Last updated: {block.ts}

## Goal
{block.goal}

## Progress
{block.progress}

## Open threads
{threads_md}

## Next step
{block.next_step}
"""
    target = continuity_dir / "LATEST.md"
    # Blocker fix (Codex Phase 1C #11): PID-suffixed tmp name prevents concurrent
    # writers from colliding on a shared `.tmp` file.
    tmp = continuity_dir / f".LATEST.md.tmp-{os.getpid()}"
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, target)
    return target


def read_latest(vault_root: Path) -> Optional[ContinuityBlock]:
    """Parse ``LATEST.md`` back into a :class:`ContinuityBlock`, if it exists."""
    target = vault_root / "_continuity" / "LATEST.md"
    if not target.is_file():
        return None

    try:
        text = target.read_text(encoding="utf-8")
    except Exception:
        return None

    lines = text.splitlines()

    # Parse sections
    goal = ""
    progress = ""
    open_threads: List[str] = []
    next_step = ""
    current_section: Optional[str] = None
    section_content: List[str] = []

    def _flush_section() -> None:
        nonlocal goal, progress, open_threads, next_step
        if current_section == "goal":
            goal = "\n".join(section_content).strip()
        elif current_section == "progress":
            progress = "\n".join(section_content).strip()
        elif current_section == "threads":
            for l in section_content:
                stripped = l.strip()
                if stripped.startswith("- ") and stripped != "- (none)":
                    open_threads.append(stripped[2:].strip())
        elif current_section == "next_step":
            next_step = "\n".join(section_content).strip()
        section_content.clear()

    for line in lines:
        # Detect section headers
        if line.startswith("## Goal"):
            _flush_section()
            current_section = "goal"
        elif line.startswith("## Progress"):
            _flush_section()
            current_section = "progress"
        elif line.startswith("## Open threads"):
            _flush_section()
            current_section = "threads"
        elif line.startswith("## Next step"):
            _flush_section()
            current_section = "next_step"
        elif line.startswith("## "):
            # Any OTHER H2 heading stops processing further.
            # H1 (# LATEST ...) is the document title — ignore it, don't break.
            break
        else:
            if current_section is not None:
                section_content.append(line)

    _flush_section()  # capture the last section

    # Extract timestamp
    ts = ""
    for line in lines:
        if line.startswith("Last updated: "):
            ts = line[len("Last updated: "):].strip()
            break

    return ContinuityBlock(
        goal=goal,
        progress=progress,
        open_threads=open_threads,
        next_step=next_step,
        ts=ts,
    )