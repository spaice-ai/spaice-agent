from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import portalocker

__all__ = ["Ledger", "DailyCounter", "BudgetExceeded"]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------
class BudgetExceeded(RuntimeError):
    """Raised when a cumulative cost exceeds a hard daily ceiling."""


# ---------------------------------------------------------------------------
# Ledger – JSONL cost tracking with concurrent-write safety
# ---------------------------------------------------------------------------
class Ledger:
    """Append-only JSONL ledger for OpenRouter call costs.

    Thread- and process-safe via ``portalocker`` exclusive locks.
    """

    DEFAULT_BASE_DIR = Path("~/.hermes/logs/spaice_agent/").expanduser()

    def __init__(self, agent_id: str, base_dir: Optional[Path] = None) -> None:
        self.agent_id = agent_id
        base = Path(base_dir) if base_dir else self.DEFAULT_BASE_DIR
        self.ledger_path = (base / agent_id / "cost_ledger.jsonl").expanduser()
        self._ensure_file_exists()

    def _ensure_file_exists(self) -> None:
        """Create parent directories and ledger file if missing."""
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.ledger_path.exists():
            self.ledger_path.touch()

    @staticmethod
    def _now_iso() -> str:
        """Timestamp in ISO-8601 with local timezone offset."""
        return datetime.now().astimezone().isoformat(timespec="seconds")

    def record(self, entry: dict[str, Any]) -> None:
        """Append one cost entry as a JSON line.

        Acquires an exclusive lock so concurrent writers don't interleave
        bytes. On disk-full / permission errors the error is logged and
        execution continues — no exception is raised.
        """
        entry = dict(entry)  # don't mutate caller's dict
        if "ts" not in entry:
            entry["ts"] = self._now_iso()
        line = json.dumps(entry, sort_keys=True) + "\n"

        try:
            with portalocker.Lock(
                self.ledger_path, mode="a", timeout=5,
                flags=portalocker.LOCK_EX,
            ) as fh:
                fh.write(line)
                fh.flush()
        except portalocker.exceptions.LockException as exc:
            logger.warning(
                "Ledger lock timed out for agent '%s': %s", self.agent_id, exc,
            )
        except OSError as exc:
            logger.warning(
                "Failed to write cost ledger entry for agent '%s': %s",
                self.agent_id, exc,
            )

    def read_since(self, since: datetime) -> list[dict[str, Any]]:
        """Return all entries with ``ts >= since``.

        Uses a shared lock. Malformed lines are skipped with a warning.
        """
        if not self.ledger_path.exists():
            return []

        results: list[dict[str, Any]] = []
        try:
            with portalocker.Lock(
                self.ledger_path, mode="r", timeout=5,
                flags=portalocker.LOCK_SH,
            ) as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning(
                            "Skipping malformed line in ledger '%s'",
                            self.ledger_path,
                        )
                        continue
                    ts_str = entry.get("ts")
                    if not ts_str:
                        continue
                    try:
                        entry_ts = datetime.fromisoformat(ts_str)
                    except ValueError:
                        logger.warning(
                            "Skipping ledger line with bad timestamp: %s", ts_str,
                        )
                        continue
                    # Normalise naive vs aware — compare conservatively
                    if entry_ts.tzinfo is None and since.tzinfo is not None:
                        entry_ts = entry_ts.replace(tzinfo=since.tzinfo)
                    elif entry_ts.tzinfo is not None and since.tzinfo is None:
                        since = since.replace(tzinfo=entry_ts.tzinfo)
                    if entry_ts >= since:
                        results.append(entry)
        except portalocker.exceptions.LockException as exc:
            logger.warning(
                "Ledger read lock timed out for '%s': %s", self.agent_id, exc,
            )
        except OSError as exc:
            logger.warning(
                "Failed to read ledger for agent '%s': %s", self.agent_id, exc,
            )
        return results

    def total_cost_usd(self, since: datetime) -> float:
        """Sum of ``cost_usd`` across all entries since *since*."""
        return sum(
            float(e.get("cost_usd", 0.0)) for e in self.read_since(since)
        )


# ---------------------------------------------------------------------------
# DailyCounter – tool fire-cap enforcement
# ---------------------------------------------------------------------------
class DailyCounter:
    """Enforce a maximum number of daily invocations per tool.

    State persisted to a small JSON file. Rolls over at local midnight.
    Increments are atomic via an exclusive lock on a dedicated lockfile
    (state file itself is written via temp+rename, so can't be locked
    reliably during replacement).
    """

    DEFAULT_BASE_DIR = Path("~/.spaice-agents/").expanduser()

    def __init__(self, agent_id: str, base_dir: Optional[Path] = None) -> None:
        self.agent_id = agent_id
        base = Path(base_dir) if base_dir else self.DEFAULT_BASE_DIR
        agent_state_dir = (base / agent_id / "state").expanduser()
        agent_state_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = agent_state_dir / "daily_counter.json"
        self.lock_path = agent_state_dir / "daily_counter.lock"
        # Ensure lockfile exists so portalocker can open it
        if not self.lock_path.exists():
            self.lock_path.touch()

    @staticmethod
    def _today_iso() -> str:
        """Today's date in the system's local timezone."""
        return datetime.now().date().isoformat()

    def _read_state_unlocked(self) -> dict[str, Any]:
        """Read state file without acquiring a lock.

        Always call while holding the exclusive lock, OR in read-only
        paths where one-stale-read is acceptable.
        """
        if not self.state_path.exists():
            return {"date": "", "counters": {}}
        try:
            raw = self.state_path.read_text()
            if not raw.strip():
                return {"date": "", "counters": {}}
            state = json.loads(raw)
            if not isinstance(state, dict) or "counters" not in state:
                return {"date": "", "counters": {}}
            # Normalise shape
            state.setdefault("date", "")
            state.setdefault("counters", {})
            if not isinstance(state["counters"], dict):
                state["counters"] = {}
            return state
        except json.JSONDecodeError:
            logger.warning(
                "Corrupt daily_counter state for agent '%s', resetting",
                self.agent_id,
            )
            return {"date": "", "counters": {}}
        except OSError as exc:
            logger.warning(
                "Could not read daily_counter for agent '%s': %s",
                self.agent_id, exc,
            )
            return {"date": "", "counters": {}}

    def _apply_rollover(self, state: dict[str, Any]) -> dict[str, Any]:
        """Reset counters if the stored date isn't today."""
        today = self._today_iso()
        if state.get("date") != today:
            state["date"] = today
            state["counters"] = {}
        return state

    def _write_state_atomic(self, state: dict[str, Any]) -> None:
        """Write state via temp-file + os.replace for atomicity."""
        tmp_path = self.state_path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w") as f:
                json.dump(state, f, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.state_path)
        except OSError as exc:
            logger.warning(
                "Failed to persist daily_counter for agent '%s': %s",
                self.agent_id, exc,
            )
            # Clean up temp on failure
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass

    def can_fire(self, tool: str, cap: int) -> bool:
        """Return True if *tool* is below its *cap* today.

        cap == 0 means disabled — always returns False.
        cap < 0 treated as unlimited — always returns True.
        """
        if cap == 0:
            return False
        if cap < 0:
            return True
        state = self._apply_rollover(self._read_state_unlocked())
        current = int(state["counters"].get(tool, 0))
        return current < cap

    def increment(self, tool: str) -> int:
        """Atomically increment the counter for *tool* and return the new count."""
        try:
            with portalocker.Lock(
                self.lock_path, mode="a", timeout=5,
                flags=portalocker.LOCK_EX,
            ):
                state = self._apply_rollover(self._read_state_unlocked())
                state["counters"][tool] = int(state["counters"].get(tool, 0)) + 1
                new_count = state["counters"][tool]
                self._write_state_atomic(state)
                return new_count
        except portalocker.exceptions.LockException as exc:
            logger.warning(
                "daily_counter lock timed out for agent '%s': %s",
                self.agent_id, exc,
            )
            # Degraded — return best-effort current+1 without persisting
            state = self._apply_rollover(self._read_state_unlocked())
            return int(state["counters"].get(tool, 0)) + 1

    def current_count(self, tool: str) -> int:
        """Current day's count for *tool*, rollover-aware."""
        state = self._apply_rollover(self._read_state_unlocked())
        return int(state["counters"].get(tool, 0))
