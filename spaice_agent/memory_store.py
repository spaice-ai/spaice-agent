"""spaice_agent.memory_store — route captured facts to ~/jarvis/_inbox/.

Writes a markdown file with YAML frontmatter per captured fact. The hourly
miner (``~/jarvis/scripts/mine_sessions.py``) picks up _inbox files and
classifies them into shelves — this module just routes, it does NOT
classify.

Filename: ``<YYYY-MM-DDTHHMMSS>-<slug>.md``. Slug is derived from the first
80 chars of the fact text with non-alnum replaced by ``-``. Collisions
(same second + same slug) get a millisecond suffix.

Writes are atomic: temp file + os.replace. The miner only reads files that
are fully written, so atomicity is important.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

__all__ = ["MemoryStoreError", "StoredFact", "store_fact"]

logger = logging.getLogger(__name__)

DEFAULT_INBOX = Path("~/jarvis/_inbox").expanduser()
SLUG_MAX_LEN = 80
_SLUG_NORMALISE = re.compile(r"[^a-z0-9]+")


class MemoryStoreError(RuntimeError):
    """Raised when a fact cannot be persisted."""


@dataclass(frozen=True)
class StoredFact:
    path: Path              # Absolute path to the written file
    slug: str               # Filename slug without timestamp prefix
    captured_at: datetime   # Timestamp used in filename + frontmatter


def _slugify(text: str) -> str:
    """Return a filesystem-safe slug from the fact text."""
    lower = text.lower().strip()
    cleaned = _SLUG_NORMALISE.sub("-", lower).strip("-")
    if not cleaned:
        return "fact"
    return cleaned[:SLUG_MAX_LEN].rstrip("-")


def _resolve_unique_path(base_dir: Path, ts: datetime, slug: str) -> Path:
    """Build a filename; if it already exists, append a disambiguator."""
    stamp = ts.strftime("%Y-%m-%dT%H%M%S")
    candidate = base_dir / f"{stamp}-{slug}.md"
    if not candidate.exists():
        return candidate
    # Add millisecond suffix and, if still colliding, counter up to 99
    ms = f"{ts.microsecond // 1000:03d}"
    with_ms = base_dir / f"{stamp}.{ms}-{slug}.md"
    if not with_ms.exists():
        return with_ms
    for i in range(1, 100):
        bumped = base_dir / f"{stamp}.{ms}.{i:02d}-{slug}.md"
        if not bumped.exists():
            return bumped
    raise MemoryStoreError(
        f"Could not resolve unique path for inbox file under {base_dir}"
    )


def store_fact(
    text: str,
    *,
    source: str = "agent",
    tags: Optional[list[str]] = None,
    extra_meta: Optional[Dict[str, Any]] = None,
    inbox_dir: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> StoredFact:
    """Persist ``text`` as a markdown file inside the inbox.

    Args:
        text: The fact body. Required, must be non-whitespace.
        source: Who captured it. Freeform short string.
        tags: Optional tag list (stored in frontmatter).
        extra_meta: Extra key/value pairs for frontmatter. Reserved keys
            (``captured_at``, ``source``, ``tags``) are overwritten.
        inbox_dir: Override ``~/jarvis/_inbox``. Used in tests.
        now: Inject timestamp. Used in tests.

    Returns:
        StoredFact with the path, slug, and timestamp.

    Raises:
        MemoryStoreError: empty text, or filesystem / yaml write failure.
    """
    if not isinstance(text, str):
        raise MemoryStoreError(f"text must be str, got {type(text).__name__}")
    body = text.strip()
    if not body:
        raise MemoryStoreError("text is empty after strip")

    target_dir = Path(inbox_dir).expanduser() if inbox_dir else DEFAULT_INBOX
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise MemoryStoreError(
            f"Cannot create inbox dir {target_dir}: {exc}"
        ) from exc

    ts = (now or datetime.now()).astimezone()
    slug = _slugify(body)
    path = _resolve_unique_path(target_dir, ts, slug)

    meta: Dict[str, Any] = dict(extra_meta or {})
    meta["captured_at"] = ts.isoformat(timespec="seconds")
    meta["source"] = source
    if tags is not None:
        # Defensive copy, ensure plain list of strings
        meta["tags"] = [str(t) for t in tags]

    try:
        front = yaml.safe_dump(
            meta, sort_keys=True, allow_unicode=True, default_flow_style=False,
        ).strip()
    except yaml.YAMLError as exc:
        raise MemoryStoreError(
            f"Could not serialise frontmatter: {exc}"
        ) from exc

    content = f"---\n{front}\n---\n\n{body}\n"
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except OSError as exc:
        # Cleanup temp file — if that ALSO fails log it so orphans are
        # at least visible, rather than silent accumulation.
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError as cleanup_exc:
            logger.warning(
                "Failed to clean up temp file %s after write error: %s",
                tmp_path, cleanup_exc,
            )
        raise MemoryStoreError(
            f"Failed to write inbox file {path}: {exc}"
        ) from exc

    return StoredFact(path=path, slug=slug, captured_at=ts)
