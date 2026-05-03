"""Vault path abstraction — single source of truth for where an agent stores memory.

Replaces the `~/jarvis/`-hardcoded pattern in Jozef's scripts with a config-driven
layer. Each spaice-agent instance has:

  - A vault root (user-facing content, e.g. ~/jarvis/)
  - An agent config dir (runtime artefacts, ~/.spaice-agents/<id>/)
    - triggers.yaml     — per-agent recall triggers (starts empty)
    - entity_cache.json — classifier cache (built by classify.py, Phase 1B)
    - config.yaml       — agent config (memory_root, platform wiring, etc.)

The vault is user-curated markdown. Runtime artefacts are elsewhere so the
vault stays clean in git.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:  # pragma: no cover — PyYAML is a hard dep declared in pyproject
    yaml = None  # type: ignore


# Canonical shelf names in priority order (earlier = more authoritative).
# These match Jozef's jarvis/ layout exactly. Ships as an EMPTY skeleton;
# the user's content accumulates into these dirs as they work.
CANONICAL_SHELVES: tuple[str, ...] = (
    "identity",        # who the user is
    "personal",        # non-work context
    "corrections",     # user-taught rules ("don't do that again")
    "patterns",        # reusable solution shapes
    "learnings",       # field knowledge
    "integrations",    # services the user works with
    "infrastructure",  # user's hosts / infra
    "projects",        # user's workstreams
    "sites",           # user's client/location records
)

# Special directories (all start with underscore, sorted before shelves).
SPECIAL_DIRS: tuple[str, ...] = (
    "_inbox",        # miner deposits facts here; triage consumes
    "_continuity",   # LATEST.md — "continue" pickup point
    "_dashboard",    # auto-regenerated dashboards
    "_templates",    # markdown templates
    "_archive",      # retired content
)


class VaultNotFoundError(FileNotFoundError):
    """Raised when the vault root doesn't exist and caller didn't ask to create."""


class VaultStructureError(ValueError):
    """Raised when the vault exists but required skeleton dirs are missing."""


@dataclass(frozen=True)
class VaultPaths:
    """Immutable path bundle for a single spaice-agent instance."""

    agent_id: str
    vault_root: Path
    agent_config_dir: Path

    # -- constructors ------------------------------------------------------

    @classmethod
    def for_agent(
        cls, agent_id: str, *, create_agent_dir: bool = False,
    ) -> "VaultPaths":
        """Load paths for a named agent.

        Resolution order:
          1. ~/.spaice-agents/<agent_id>/config.yaml → memory.memory_root
          2. ~/<agent_id>/ (convention — the vault lives in $HOME/<id>)

        Raises VaultNotFoundError if neither resolves to an existing directory.
        """
        if not agent_id:
            raise ValueError("agent_id must be a non-empty string")

        agent_config_dir = Path.home() / ".spaice-agents" / agent_id
        config_path = agent_config_dir / "config.yaml"
        vault_root: Optional[Path] = None

        if config_path.exists() and yaml is not None:
            try:
                data = yaml.safe_load(config_path.read_text()) or {}
                mem_root = (
                    data.get("memory", {}).get("memory_root")
                    if isinstance(data, dict) else None
                )
                if mem_root:
                    vault_root = Path(mem_root).expanduser().resolve()
            except (yaml.YAMLError, OSError):
                # Fall through to convention fallback
                vault_root = None

        if vault_root is None:
            # Convention fallback: ~/<agent_id>/
            vault_root = Path.home() / agent_id

        if not vault_root.exists():
            raise VaultNotFoundError(
                f"Vault for agent '{agent_id}' not found at {vault_root}. "
                f"Create it by calling `VaultPaths.for_agent(..., create_agent_dir=True)` "
                f"then `.ensure_skeleton()`, or set memory.memory_root in "
                f"{config_path} to an existing directory."
            )

        if create_agent_dir:
            agent_config_dir.mkdir(parents=True, exist_ok=True)

        return cls(
            agent_id=agent_id,
            vault_root=vault_root.resolve(),
            agent_config_dir=agent_config_dir,
        )

    @classmethod
    def for_vault(
        cls, vault_root: Path, *, agent_id: str = "_standalone",
    ) -> "VaultPaths":
        """Build paths from an explicit vault root (useful for tests + tooling)."""
        if not agent_id:
            raise ValueError("agent_id must be a non-empty string")
        vault_root = Path(vault_root).expanduser().resolve()
        if not vault_root.exists():
            raise VaultNotFoundError(f"Vault root does not exist: {vault_root}")
        return cls(
            agent_id=agent_id,
            vault_root=vault_root,
            agent_config_dir=Path.home() / ".spaice-agents" / agent_id,
        )

    # -- directory accessors ----------------------------------------------

    @property
    def inbox(self) -> Path:
        return self.vault_root / "_inbox"

    @property
    def continuity(self) -> Path:
        return self.vault_root / "_continuity"

    @property
    def dashboard(self) -> Path:
        return self.vault_root / "_dashboard"

    @property
    def templates(self) -> Path:
        return self.vault_root / "_templates"

    @property
    def archive(self) -> Path:
        return self.vault_root / "_archive"

    @property
    def triggers_yaml(self) -> Path:
        """Per-agent recall triggers config.

        Lives in the agent config dir (NOT the vault) because it's a runtime
        artefact, not user-curated content.
        """
        return self.agent_config_dir / "triggers.yaml"

    @property
    def entity_cache(self) -> Path:
        """Classifier-built entity cache (Phase 1B)."""
        return self.agent_config_dir / "entity_cache.json"

    @property
    def shelves(self) -> tuple[str, ...]:
        """Canonical shelf names in priority order."""
        return CANONICAL_SHELVES

    def shelf_path(self, name: str) -> Path:
        """Return the path for a named shelf.

        Raises ValueError if name is not a canonical shelf.
        """
        if name not in CANONICAL_SHELVES:
            raise ValueError(
                f"'{name}' is not a canonical shelf. "
                f"Valid: {', '.join(CANONICAL_SHELVES)}"
            )
        return self.vault_root / name

    # -- lifecycle ---------------------------------------------------------

    def ensure_skeleton(self) -> None:
        """Create all canonical shelves + special dirs. Idempotent.

        Does NOT write any content (no README, no CONVENTIONS.md). That's
        the vault scaffolder's job in Phase 2.
        """
        self.vault_root.mkdir(parents=True, exist_ok=True)
        for name in CANONICAL_SHELVES:
            (self.vault_root / name).mkdir(exist_ok=True)
        for name in SPECIAL_DIRS:
            (self.vault_root / name).mkdir(exist_ok=True)
        self.agent_config_dir.mkdir(parents=True, exist_ok=True)

    def validate(self) -> None:
        """Check skeleton is complete. Raises VaultStructureError if not.

        Required: vault root exists, _inbox exists (it's the critical write
        target). Shelves are nice-to-have but the agent can function without
        them — it just won't have anywhere organised to file triage output.
        """
        if not self.vault_root.exists():
            raise VaultStructureError(f"Vault root missing: {self.vault_root}")
        if not self.inbox.exists():
            raise VaultStructureError(
                f"Inbox missing: {self.inbox}. "
                f"Run `vault_paths.ensure_skeleton()` to create."
            )
