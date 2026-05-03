from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LibraryEntry:
    """Blocker fix (Codex Phase 1C #10): real stdlib frozen dataclass — truly immutable."""
    path: str
    title: str
    tags: tuple[str, ...]
    summary: str
    backlinks: tuple[str, ...]
    mtime: float


@dataclass(frozen=True)
class LibraryIndex:
    """Blocker fix (Codex Phase 1C #10): real stdlib frozen dataclass — truly immutable."""
    entries: Tuple[LibraryEntry, ...]
    ts_built: datetime


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_EXCLUDED_DIR_NAMES = frozenset(
    {'.git', '_dashboard', '_archive', '_continuity', '_inbox', 'node_modules', '.obsidian'}
)

_WIKILINK_PATTERN = re.compile(r'\[\[([^\]|#]+)(?:[|#][^\]]+)?\]\]')


def _is_excluded(path: Path, vault_root: Path) -> bool:
    """True if *path* should be skipped during scanning."""
    return any(part in _EXCLUDED_DIR_NAMES for part in path.relative_to(vault_root).parts)


def _parse_frontmatter(text: str) -> dict:
    """Extract YAML frontmatter from *text*, return dict (empty on failure)."""
    if not text.startswith('---\n'):
        return {}
    end = text.find('\n---', 4)
    if end == -1:
        return {}
    try:
        data = yaml.safe_load(text[4:end])
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError:
        logger.debug("Invalid YAML frontmatter, ignoring.", exc_info=True)
        return {}


def _extract_summary(text: str, max_chars: int = 200) -> str:
    """Return the first non-heading paragraph, truncated to *max_chars* chars."""
    # Skip frontmatter
    if text.startswith('---'):
        end = text.find('\n---', 4)
        if end != -1:
            text = text[end + 4:]

    lines = text.strip().splitlines()
    candidate_lines = []
    in_paragraph = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('#'):
            if in_paragraph:
                break
            continue
        if stripped == '':
            if in_paragraph:
                break
            continue
        # Non-heading, non-empty line
        in_paragraph = True
        candidate_lines.append(stripped)

    summary = ' '.join(candidate_lines)
    if len(summary) > max_chars:
        summary = summary[:max_chars].rsplit(' ', 1)[0].rstrip()  # don't cut words rudely
    return summary


def _entry_path_to_stem(rel_path: str) -> str:
    """Return the stem of a relative path (filename without .md)."""
    return Path(rel_path).stem


def _scan_backlinks(vault_root: Path) -> dict[str, list[str]]:
    """
    Walk the whole vault and collect all [[wikilinks]].

    Returns
        dict mapping target stem (filename without .md) -> list of source relative paths (strings).
    """
    backlink_map: dict[str, list[str]] = {}
    for md_file in vault_root.rglob('*.md'):
        if _is_excluded(md_file, vault_root):
            continue
        try:
            content = md_file.read_text(encoding='utf-8', errors='replace')
        except Exception:
            logger.debug("Skipping unreadable file %s", md_file, exc_info=True)
            continue
        source_rel = md_file.relative_to(vault_root).as_posix()
        for match in _WIKILINK_PATTERN.finditer(content):
            target_stem = match.group(1).strip()
            # Normalise: wikilinks often point to the file stem
            # e.g., [[My Note]] refers to 'My Note.md'
            backlink_map.setdefault(target_stem, []).append(source_rel)
    return backlink_map


def _build_entries(
    vault_root: Path,
    existing_entries: dict[str, LibraryEntry],
    backlink_map: dict[str, list[str]],
) -> list[LibraryEntry]:
    """Scan the library (or whole vault) and produce a list of LibraryEntry."""
    library_path = vault_root / 'library'
    scan_root = library_path if library_path.is_dir() else vault_root
    entries: list[LibraryEntry] = []

    for md_file in sorted(scan_root.rglob('*.md')):
        if _is_excluded(md_file, vault_root):
            continue
        rel_path = md_file.relative_to(vault_root).as_posix()
        try:
            mtime = md_file.stat().st_mtime
        except OSError:
            logger.warning("Cannot stat %s, skipping.", md_file)
            continue

        # Incremental reuse: if file unchanged and already indexed.
        # Blocker fix (Codex Phase 1C #9): ALWAYS refresh backlinks even on
        # reuse — the file's own content hasn't changed, but OTHER files may
        # now link to it. We rebuild the entry with the fresh backlink tuple
        # while keeping the parsed fields (title/tags/summary).
        if rel_path in existing_entries:
            existing = existing_entries[rel_path]
            if abs(existing.mtime - mtime) < 1.0:  # within 1 sec tolerance
                fresh_backlinks = tuple(backlink_map.get(_entry_path_to_stem(rel_path), []))
                if fresh_backlinks != existing.backlinks:
                    # Re-emit with fresh backlinks
                    entries.append(LibraryEntry(
                        path=existing.path,
                        title=existing.title,
                        tags=existing.tags,
                        summary=existing.summary,
                        backlinks=fresh_backlinks,
                        mtime=existing.mtime,
                    ))
                else:
                    entries.append(existing)
                continue

        # --- Fresh parse ---
        try:
            raw = md_file.read_text(encoding='utf-8', errors='replace')
        except Exception:
            logger.warning("Could not read %s, skipping.", md_file)
            continue

        fm = _parse_frontmatter(raw)

        # Title
        title = fm.get('title')
        if not title:
            # Try first H1
            for line in raw.splitlines():
                line = line.strip()
                if line.startswith('# ') and not line.startswith('## '):
                    title = line[2:].strip()
                    break
        if not title:
            title = md_file.stem

        # Tags
        raw_tags = fm.get('tags', [])
        if isinstance(raw_tags, str):
            tags = tuple(t.strip() for t in raw_tags.split(',') if t.strip())
        elif isinstance(raw_tags, (list, tuple)):
            tags = tuple(str(t).strip() for t in raw_tags if t is not None)
        else:
            tags = ()

        # Summary
        summary = _extract_summary(raw)

        # Backlinks
        stem = _entry_path_to_stem(rel_path)
        backlinks = tuple(sorted(set(backlink_map.get(stem, []))))

        entry = LibraryEntry(
            path=rel_path,
            title=title,
            tags=tags,
            summary=summary,
            backlinks=backlinks,
            mtime=mtime,
        )
        entries.append(entry)

    return entries


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_library_index(vault_root: Path) -> LibraryIndex:
    """
    Build a LibraryIndex by scanning markdown files in the vault.
    Incremental: reuses previous entries for unchanged files.
    """
    if not vault_root.is_dir():
        logger.warning("vault_root %s is not a directory, returning empty index.", vault_root)
        return LibraryIndex(entries=(), ts_built=datetime.now(timezone.utc))

    existing = load_library_index(vault_root)
    existing_entries: dict[str, LibraryEntry] = {}
    if existing is not None:
        for entry in existing.entries:
            existing_entries[entry.path] = entry

    backlink_map = _scan_backlinks(vault_root)
    entries = _build_entries(vault_root, existing_entries, backlink_map)

    return LibraryIndex(entries=tuple(entries), ts_built=datetime.now(timezone.utc))


def load_library_index(vault_root: Path) -> Optional[LibraryIndex]:
    """
    Load the previously saved library index from <vault>/_dashboard/library-index.yaml.
    Returns None if the file is missing or invalid.
    """
    index_path = vault_root / '_dashboard' / 'library-index.yaml'
    if not index_path.is_file():
        return None
    try:
        with index_path.open('r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError) as exc:
        logger.warning("Failed to load library index from %s: %s", index_path, exc)
        return None

    if not isinstance(data, dict) or 'entries' not in data:
        logger.warning("Library index file %s has unexpected structure.", index_path)
        return None

    raw_entries = data.get('entries', [])
    if not isinstance(raw_entries, list):
        return None

    entries: list[LibraryEntry] = []
    for item in raw_entries:
        try:
            # Convert mtime to float if stored as string/float
            mtime = float(item.get('mtime', 0.0))
            entry = LibraryEntry(
                path=item.get('path', ''),
                title=item.get('title', ''),
                tags=tuple(item.get('tags', [])),
                summary=item.get('summary', ''),
                backlinks=tuple(item.get('backlinks', [])),
                mtime=mtime,
            )
            entries.append(entry)
        except (TypeError, ValueError) as exc:
            logger.debug("Skipping malformed entry in index: %s", exc)
            continue

    ts_built = data.get('ts_built')
    if isinstance(ts_built, str):
        try:
            # Parse ISO 8601 with optional timezone
            ts_built = datetime.fromisoformat(ts_built)
        except (ValueError, TypeError):
            ts_built = datetime.now(timezone.utc)
    else:
        ts_built = datetime.now(timezone.utc)

    return LibraryIndex(entries=tuple(entries), ts_built=ts_built)


def save_library_index(index: LibraryIndex, vault_root: Path) -> None:
    """
    Save the library index atomically to <vault>/_dashboard/library-index.yaml.
    """
    dash_dir = vault_root / '_dashboard'
    dash_dir.mkdir(parents=True, exist_ok=True)
    index_path = dash_dir / 'library-index.yaml'
    tmp_path = index_path.with_suffix('.tmp')

    serialized = {
        'entries': [
            {
                'path': e.path,
                'title': e.title,
                'tags': list(e.tags),
                'summary': e.summary,
                'backlinks': list(e.backlinks),
                'mtime': e.mtime,
            }
            for e in index.entries
        ],
        'ts_built': index.ts_built.isoformat(),
    }

    try:
        tmp_path.write_text(yaml.dump(serialized, default_flow_style=False, allow_unicode=True), encoding='utf-8')
        os.replace(tmp_path, index_path)  # atomic on POSIX
    except OSError:
        logger.exception("Failed to save library index to %s", index_path)