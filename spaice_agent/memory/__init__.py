"""spaice-agent memory subsystem.

Generic memory engines — vault path abstraction, fact capture, and recall.
Domain-specific knowledge (brand lists, client names, product matrices) is
NEVER baked into these modules; it lives in per-agent config/vault.

Public API:
    from spaice_agent.memory import VaultPaths, capture_fact, Recaller
"""
from __future__ import annotations

from spaice_agent.memory.paths import (
    VaultPaths,
    VaultNotFoundError,
    VaultStructureError,
    CANONICAL_SHELVES,
    SPECIAL_DIRS,
)
from spaice_agent.memory.capture import (
    capture_fact,
    InboxEntry,
    InvalidInboxEntryError,
)
from spaice_agent.memory.recall import (
    Recaller,
    RecallHit,
    InvalidTriggersConfigError,
)

__all__ = [
    "VaultPaths",
    "VaultNotFoundError",
    "VaultStructureError",
    "CANONICAL_SHELVES",
    "SPECIAL_DIRS",
    "capture_fact",
    "InboxEntry",
    "InvalidInboxEntryError",
    "Recaller",
    "RecallHit",
    "InvalidTriggersConfigError",
]
