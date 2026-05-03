"""spaice_agent/advisory.py — Consensus advisory helpers (FW-1 v3).

Extracted from orchestrator.py. Manages suppression state via file-locked
atomic writes under config.memory_root / "state". All functions are
concurrency-safe and never raise — I/O errors are logged and swallowed.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import AgentConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _state_paths(config: "AgentConfig") -> tuple[Path, Path, Path]:
    """Return (state_dir, lock_path, state_path)."""
    state_dir = config.memory_root / "state"
    lock_path = state_dir / ".suppress.lock"
    state_path = state_dir / "last_consensus_turn.json"
    return state_dir, lock_path, state_path


def _read_state(state_path: Path) -> dict | None:
    """Read suppression state dict, or None on any error."""
    try:
        if not state_path.exists():
            return None
        return json.loads(state_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read suppression state: %s", exc)
        return None


def _write_state_atomic(state_path: Path, data: dict) -> None:
    """Atomically write state dict to disk. Logs and swallows errors."""
    state_dir = state_path.parent
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(state_dir))
        try:
            with os.fdopen(tmp_fd, "w") as fh:
                json.dump(data, fh)
            os.replace(tmp_path, state_path)
        except Exception:
            os.unlink(tmp_path)
            raise
    except OSError as exc:
        logger.warning("Failed to write suppression state: %s", exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_advisory(reason: str, config: "AgentConfig") -> str:
    """Return a pure-text advisory message for the LLM (no I/O)."""
    cost_est = "0.10–0.30"
    latency_est = "30–90s"
    suppress = getattr(config.consensus, "advisory_suppress_turns", 3)
    return (
        "\n\n**[Consensus Advisory]**\n"
        f"Reason: {reason}\n"
        f"Estimated cost: ${cost_est} USD. Estimated latency: {latency_est}.\n"
        f" (Call `use_consensus` tool if you want a multi-chair panel; "
        f"it will not fire again as an advisory for the next {suppress} turns.)"
    )


def is_suppressed(config: "AgentConfig") -> bool:
    """Return True if consensus advisory should be suppressed this turn.

    Uses fcntl.flock with one retry on contention (v3 finding 3).
    Fail-open on persistent contention: returns False (advisory allowed).
    """
    state_dir, lock_path, state_path = _state_paths(config)
    state_dir.mkdir(parents=True, exist_ok=True)

    suppress_turns = getattr(config.consensus, "advisory_suppress_turns", 3)

    for attempt in range(2):
        try:
            with open(lock_path, "a") as lock_fh:
                if attempt == 1:
                    time.sleep(0.01)
                # Non-blocking on BOTH attempts — fail-open on persistent contention
                # per spec. Previously used blocking flock on attempt 1 which could
                # hang indefinitely, contradicting fail-open semantics.
                flags = fcntl.LOCK_EX | fcntl.LOCK_NB

                try:
                    fcntl.flock(lock_fh, flags)
                except BlockingIOError:
                    if attempt == 0:
                        continue
                    return False  # fail-open: allow advisory

                try:
                    state = _read_state(state_path)
                    if state is None:
                        return False
                    turns_since = state.get("turns_since_call", 999)
                    return turns_since < suppress_turns
                finally:
                    fcntl.flock(lock_fh, fcntl.LOCK_UN)

        except OSError:
            if attempt == 0:
                continue
            return False

    return False


def advance_suppression_counter(config: "AgentConfig") -> None:
    """Increment the turns-since-last-consensus counter by 1.

    Called every hook invocation. Uses blocking exclusive lock + atomic write.
    """
    state_dir, lock_path, state_path = _state_paths(config)
    state_dir.mkdir(parents=True, exist_ok=True)

    try:
        with open(lock_path, "a") as lock_fh:
            fcntl.flock(lock_fh, fcntl.LOCK_EX)
            try:
                current = _read_state(state_path) or {"turns_since_call": 0}
                current["turns_since_call"] = current.get("turns_since_call", 0) + 1
                _write_state_atomic(state_path, current)
            finally:
                fcntl.flock(lock_fh, fcntl.LOCK_UN)
    except OSError as exc:
        logger.warning("Failed to advance suppression counter: %s", exc)


def reset_suppression_counter(config: "AgentConfig") -> None:
    """Reset the suppression counter to 0 (called after a successful consensus tool call)."""
    state_dir, lock_path, state_path = _state_paths(config)
    state_dir.mkdir(parents=True, exist_ok=True)

    try:
        with open(lock_path, "a") as lock_fh:
            fcntl.flock(lock_fh, fcntl.LOCK_EX)
            try:
                _write_state_atomic(state_path, {"turns_since_call": 0})
            finally:
                fcntl.flock(lock_fh, fcntl.LOCK_UN)
    except OSError as exc:
        logger.warning("Failed to reset suppression counter: %s", exc)