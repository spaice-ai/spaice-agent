"""
spaice_agent/config.py — Pydantic v2 configuration loader for SPAICE Agent Framework.

Loads and validates per-agent YAML configuration, supports environment-variable
indirection for credentials, and provides a frozen, immutable configuration object.

Public API:
    load_agent_config(agent_id: str) -> AgentConfig
    AgentConfig
    ConfigError
    ConfigNotFoundError
    MissingCredentialError
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, List, Literal

import yaml
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

# ---------------------------------------------------------------------------
# Custom errors
# ---------------------------------------------------------------------------


class ConfigError(RuntimeError):
    """Base error raised for configuration problems."""


class ConfigNotFoundError(ConfigError):
    """Raised when a config file is not found at the expected path."""


class MissingCredentialError(ConfigError):
    """Raised when a required credential environment variable is missing."""


# ---------------------------------------------------------------------------
# Path expansion helper for Pydantic
# ---------------------------------------------------------------------------


def _expand_path(v: Any) -> Path:
    """Expand '~' and return a :class:`pathlib.Path`."""
    return Path(v).expanduser()


ExpandedPath = Annotated[Path, BeforeValidator(_expand_path)]

# ---------------------------------------------------------------------------
# Pydantic models for sub-sections
# ---------------------------------------------------------------------------


class _Credentials(BaseModel):
    """Credentials indirection – stores environment variable names."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    openrouter_api_key_env: str
    exa_api_key_env: str
    brave_api_key_env: str


class _Hook(BaseModel):
    """Hook stage timeout."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    total_timeout_s: float = Field(gt=0)


class _Memory(BaseModel):
    """Memory subsystem configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
    entity_cache_path: ExpandedPath
    stage_timeout_s: float = Field(gt=0)
    recall_max_hits_per_entity: int = Field(gt=0)
    recall_snippet_chars: int = Field(gt=0)
    live_capture_dir: ExpandedPath


class _Provider(BaseModel):
    """Search provider definition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Literal["exa", "brave"]
    endpoint: str
    max_results: int = Field(gt=0)
    per_request_timeout_s: float = Field(gt=0)


class _Merge(BaseModel):
    """Merge configuration for search results."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    method: Literal["rrf"]
    k: int = Field(gt=0)


class _SearchTriggers(BaseModel):
    """Triggers for the search stage."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    phrase_anchors: List[str]
    url_at_end: bool


class _Search(BaseModel):
    """Search subsystem configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
    daily_fire_cap: int = Field(ge=0)
    stage_timeout_s: float = Field(gt=0)
    providers: List[_Provider]
    merge: _Merge
    triggers: _SearchTriggers

    @model_validator(mode="after")
    def _check_providers_when_enabled(self) -> _Search:
        if self.enabled and not self.providers:
            raise ValueError(
                "search.enabled=true but no providers configured; "
                "add at least one provider or set enabled=false"
            )
        return self


class _PipelineStage(BaseModel):
    """A single stage inside the consensus pipeline."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: Literal["proposer", "critic", "reviewer"]
    model: str
    stage_timeout_s: float = Field(gt=0)
    max_tokens: int = Field(gt=0)
    truncate_output_chars: int = Field(gt=0)
    system: str


class _ConsensusTriggers(BaseModel):
    """Triggers for the consensus stage."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    words: List[str]
    phrases: List[str]


class _Consensus(BaseModel):
    """Consensus subsystem configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
    daily_fire_cap: int = Field(ge=0)
    pipeline_timeout_s: float = Field(gt=0)
    pipeline: List[_PipelineStage]
    triggers: _ConsensusTriggers

    @model_validator(mode="after")
    def _check_pipeline_order(self) -> _Consensus:
        if len(self.pipeline) != 3:
            raise ValueError(
                f"Consensus pipeline must contain exactly 3 stages, got {len(self.pipeline)}"
            )
        expected = ["proposer", "critic", "reviewer"]
        actual = [s.stage for s in self.pipeline]
        if actual != expected:
            raise ValueError(
                f"Consensus pipeline stages must appear in order {expected}, got {actual}"
            )
        return self


class _Scrubber(BaseModel):
    """Scrubber subsystem configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
    leak_patterns: List[str]


# ---------------------------------------------------------------------------
# Top-level AgentConfig
# ---------------------------------------------------------------------------

AGENT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,31}$")


class AgentConfig(BaseModel):
    """Immutable, validated configuration for a single SPAICE agent.

    All fields are required and readonly after construction.
    Credentials are accessed via :meth:`get_credential`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: str = Field(
        min_length=2,
        max_length=32,
        pattern=AGENT_ID_PATTERN,
    )
    memory_root: ExpandedPath
    credentials: _Credentials
    hook: _Hook
    memory: _Memory
    search: _Search
    consensus: _Consensus
    scrubber: _Scrubber

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def get_credential(self, field_name: str) -> str:
        """Return the value of the environment variable named by the
        corresponding ``*_env`` configuration field.

        Args:
            field_name: Base credential name (e.g. ``"openrouter_api_key"``).
                The full configuration field name is derived by appending
                ``"_env"``.

        Returns:
            The value from ``os.environ``.

        Raises:
            MissingCredentialError: If the environment variable is not set
                or is empty.
        """
        env_attr = f"{field_name}_env"
        env_var: str | None = getattr(self.credentials, env_attr, None)  # type: ignore[arg-type]
        if not env_var:
            raise MissingCredentialError(
                f"No credential env-var configured for '{field_name}'"
            )
        value = os.environ.get(env_var)
        if not value:
            raise MissingCredentialError(
                f"Environment variable {env_var} (credential '{field_name}') is not set"
            )
        return value


# ---------------------------------------------------------------------------
# Public loader
# ---------------------------------------------------------------------------


@lru_cache(maxsize=4)
def load_agent_config(agent_id: str) -> AgentConfig:
    """Load and validate the YAML config for *agent_id*.

    The config is expected at
    ``~/.spaice-agents/<agent_id>/config.yaml``.  ``~`` is expanded
    automatically.

    Args:
        agent_id: The agent identifier (must match ``AGENT_ID_PATTERN``).

    Returns:
        An immutable :class:`AgentConfig` instance.

    Raises:
        ConfigNotFoundError: If the config file does not exist.
        ConfigError: If the YAML is syntactically or semantically invalid,
            if the ``agent_id`` inside the file does not match the
            requested *agent_id*, or if *agent_id* fails the slug pattern
            (path-traversal guard).
    """
    # ---- Path-traversal guard: validate BEFORE touching the filesystem.
    # A caller passing "../aurora" would otherwise walk up the tree and
    # read a neighbouring agent's config — breaking Jarvis/Aurora isolation.
    if not isinstance(agent_id, str) or not AGENT_ID_PATTERN.match(agent_id):
        raise ConfigError(
            f"Invalid agent_id {agent_id!r}: must match {AGENT_ID_PATTERN.pattern}"
        )

    config_path = Path(f"~/.spaice-agents/{agent_id}/config.yaml").expanduser()
    if not config_path.is_file():
        raise ConfigNotFoundError(f"Config file not found: {config_path}")

    try:
        with config_path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {config_path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(
            f"Config file {config_path} is not valid UTF-8: {exc}"
        ) from exc
    except OSError as exc:
        raise ConfigError(
            f"Could not read {config_path}: {exc}"
        ) from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"Top-level YAML in {config_path} must be a mapping")

    try:
        config = AgentConfig(**raw)
    except ValidationError as exc:
        raise ConfigError(
            f"Invalid config in {config_path}:\n{exc}"
        ) from exc

    # Safety check: the agent_id inside the file must match the one
    # we were asked for, otherwise the cache key is misleading.
    if config.agent_id != agent_id:
        raise ConfigError(
            f"Config agent_id '{config.agent_id}' doesn't match "
            f"requested agent_id '{agent_id}'"
        )

    return config