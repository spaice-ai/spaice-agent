"""spaice-agent memory subsystem.

Generic memory engines — vault paths, fact capture, recall, classification,
inbox triage, and session mining. Domain-specific knowledge (brand lists,
client names, product matrices) is NEVER baked into these modules; it lives
in per-agent config/vault.

Public API:
    from spaice_agent.memory import (
        VaultPaths,
        capture_fact,
        Recaller,
        Classifier,
        Triager,
        Miner,
    )
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
from spaice_agent.memory.classify import (
    Classifier,
    Classification,
    ClassifierConfig,
    ClassifierError,
    ClassifierConfigError,
    ClassifierAPIError,
    ClassifierResponseError,
)
from spaice_agent.memory.triage import (
    Triager,
    TriageReport,
    TriageResult,
    TriageConfig,
)
from spaice_agent.memory.mine import (
    Miner,
    MineReport,
    MineConfig,
)

__all__ = [
    # paths
    "VaultPaths",
    "VaultNotFoundError",
    "VaultStructureError",
    "CANONICAL_SHELVES",
    "SPECIAL_DIRS",
    # capture
    "capture_fact",
    "InboxEntry",
    "InvalidInboxEntryError",
    # recall
    "Recaller",
    "RecallHit",
    "InvalidTriggersConfigError",
    # classify
    "Classifier",
    "Classification",
    "ClassifierConfig",
    "ClassifierError",
    "ClassifierConfigError",
    "ClassifierAPIError",
    "ClassifierResponseError",
    # triage
    "Triager",
    "TriageReport",
    "TriageResult",
    "TriageConfig",
    # mine
    "Miner",
    "MineReport",
    "MineConfig",
]
