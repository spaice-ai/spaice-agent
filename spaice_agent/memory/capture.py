"""Generic inbox-append writer.

Drops a fact into the agent's `_inbox/` as a markdown file with YAML
frontmatter. The triage module (Phase 1B) consumes these and routes them
to canonical shelves.

This is intentionally SPAICE-agnostic: no product-matrix validation, no
xlsx regeneration, no shelf routing. Those live in domain-specific writers
(e.g. Jozef's `capture_fact.py` in ~/jarvis/scripts/ is a layer on top).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from spaice_agent.memory.paths import VaultPaths

# Max text size for a single inbox entry (reject pathological blobs).
MAX_TEXT_BYTES = 100 * 1024  # 100KB

# Max source length; prevents frontmatter corruption from unbounded tags.
MAX_SOURCE_CHARS = 128


class InvalidInboxEntryError(ValueError):
    """Raised when an inbox entry violates content constraints."""


@dataclass(frozen=True)
class InboxEntry:
    """A fact to be filed into the agent's inbox.

    Attributes:
        text: The fact itself. Plain markdown. Must be non-empty and <100KB.
        source: Free-form origin tag (e.g. "telegram", "cron:mine",
                "manual", "conversation"). Used by triage + audit.
        category: Optional classification hint (e.g. "product", "correction").
                  None = unclassified; triage decides at filing time.
        tags: Optional list of string tags for searchability.
        created_at: Override timestamp (tests + deterministic replay only).
                    If None, uses `datetime.now().astimezone()`.
    """

    text: str
    source: str
    category: Optional[str] = None
    tags: tuple[str, ...] = field(default_factory=tuple)
    created_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if not self.text or not self.text.strip():
            raise InvalidInboxEntryError("InboxEntry.text must be non-empty")
        if len(self.text.encode("utf-8")) > MAX_TEXT_BYTES:
            raise InvalidInboxEntryError(
                f"InboxEntry.text exceeds {MAX_TEXT_BYTES} bytes; "
                f"split into multiple entries"
            )
        if not self.source or not self.source.strip():
            raise InvalidInboxEntryError("InboxEntry.source must be non-empty")
        # Reject newlines/control chars — they would corrupt frontmatter
        if any(ord(c) < 32 for c in self.source):
            raise InvalidInboxEntryError(
                "InboxEntry.source must not contain control characters"
            )
        if len(self.source) > MAX_SOURCE_CHARS:
            raise InvalidInboxEntryError(
                f"InboxEntry.source exceeds {MAX_SOURCE_CHARS} chars"
            )


def _entry_id(text: str, created_at: datetime) -> str:
    """Deterministic 12-char hex ID for dedup + filename.

    Rounds `created_at` to the nearest 5-minute bucket, so the same text
    captured within a 5-minute window maps to the same ID (idempotent).
    """
    bucket_minutes = (created_at.minute // 5) * 5
    bucket = created_at.replace(minute=bucket_minutes, second=0, microsecond=0)
    key = f"{bucket.isoformat()}|{text.strip()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def _filename(created_at: datetime, entry_id: str) -> str:
    """Human-readable chronological filename: `YYYY-MM-DD-HHhMMm-<id>.md`."""
    stamp = created_at.strftime("%Y-%m-%d-%Hh%Mm")
    return f"{stamp}-{entry_id}.md"


def _render_frontmatter(
    entry_id: str,
    created_at: datetime,
    source: str,
    category: Optional[str],
    tags: tuple[str, ...],
    status: str = "pending",
    extra: Optional[dict] = None,
) -> str:
    """Render the YAML frontmatter block with stable key order.

    Standard keys (id, created_at, source, category, tags, status) ALWAYS
    appear in that order first. If `extra` is provided, its keys are
    appended afterwards (sorted by key for deterministic output).
    """
    # Manually compose to preserve key order without relying on YAML's
    # default_flow_style quirks. This is YAML 1.2 compatible.
    lines = ["---"]
    lines.append(f"id: {entry_id}")
    lines.append(f"created_at: {created_at.isoformat()}")
    lines.append(f"source: {_yaml_scalar(source)}")
    if category is None:
        lines.append("category: null")
    else:
        lines.append(f"category: {_yaml_scalar(category)}")
    if tags:
        tags_fmt = ", ".join(_yaml_scalar(t) for t in tags)
        lines.append(f"tags: [{tags_fmt}]")
    else:
        lines.append("tags: []")
    lines.append(f"status: {status}")
    if extra:
        for k in sorted(extra.keys()):
            # Reject keys that would collide with the standard set
            if k in {"id", "created_at", "source", "category", "tags", "status"}:
                raise InvalidInboxEntryError(
                    f"extra_frontmatter key {k!r} collides with standard key"
                )
            lines.append(f"{k}: {_render_extra_value(extra[k])}")
    lines.append("---")
    return "\n".join(lines)


def _render_extra_value(v) -> str:
    """Render a single extra-frontmatter value with YAML-safe formatting."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (list, tuple)):
        if not v:
            return "[]"
        return "[" + ", ".join(_yaml_scalar(str(x)) for x in v) + "]"
    # Default: string scalar
    return _yaml_scalar(str(v))


# Unquoted YAML scalars: allow alphanumerics, underscore, hyphen, dot, slash only.
# Exclude ':' (YAML key-value separator) + whitespace. If either is present,
# quote the string to avoid frontmatter parse errors.
_SAFE_SCALAR = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_\-./]*$")


def _yaml_scalar(s: str) -> str:
    """Quote a string for YAML only if it contains special chars."""
    if s and _SAFE_SCALAR.match(s):
        return s
    # Use double-quoted form, escape backslashes + double quotes
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def capture_fact(
    entry: InboxEntry,
    agent_id: str,
    *,
    extra_frontmatter: Optional[dict] = None,
) -> Path:
    """Write an inbox entry to disk. Returns the Path of the written file.

    Behaviour:
    - Creates `_inbox/` if missing (via ensure on the parent vault)
    - Filename is deterministic from (5-min bucket of created_at, text)
    - Idempotent: repeated calls with the same (text, source, 5-min window)
      overwrite the same file (safe — content is identical)

    Args:
        entry: The InboxEntry to write.
        agent_id: Which spaice-agent instance to write to.
        extra_frontmatter: Optional dict of additional frontmatter keys
            (e.g. classifier metadata). Keys may not collide with the
            standard set (id/created_at/source/category/tags/status).

    Returns:
        Path to the written file.

    Raises:
        VaultNotFoundError: The agent's vault root doesn't exist.
        InvalidInboxEntryError: Entry violates content constraints OR
            extra_frontmatter contains a reserved key.
    """
    paths = VaultPaths.for_agent(agent_id)
    paths.inbox.mkdir(parents=True, exist_ok=True)

    created_at = entry.created_at or datetime.now().astimezone()
    # Bucket the filename timestamp to 5-min granularity so repeated captures
    # of the same text/source within a bucket produce the SAME filename
    # (true idempotency, not just same ID).
    bucket_minutes = (created_at.minute // 5) * 5
    filename_ts = created_at.replace(minute=bucket_minutes, second=0, microsecond=0)
    entry_id = _entry_id(entry.text, created_at)

    target = paths.inbox / _filename(filename_ts, entry_id)

    frontmatter = _render_frontmatter(
        entry_id=entry_id,
        created_at=created_at,
        source=entry.source,
        category=entry.category,
        tags=entry.tags,
        extra=extra_frontmatter,
    )

    body = entry.text.strip() + "\n"
    target.write_text(f"{frontmatter}\n\n{body}")
    return target
