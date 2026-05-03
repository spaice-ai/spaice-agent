"""Consensus ledger — append-only JSONL log of consensus pipeline invocations.

Moved out of the hook into the package so it's testable and versioned.
Location: ``${state_root}/consensus_ledger.jsonl`` where state_root is
``~/.spaice-agents/<agent_id>/state``.
"""
from __future__ import annotations

import datetime
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def append_ledger(
    agent_id: str,
    *,
    cost_usd: float,
    latency_s: float,
    stages_ran: int,
    ok: bool,
    trigger_reason: str,
    error: Optional[str] = None,
) -> None:
    """Append a JSON line to the consensus ledger.

    Best-effort — callers should wrap in try/except; this function
    logs and re-raises on write failure so tests can catch it, but in
    production the tool.py caller silences the exception.
    """
    ledger_dir = Path.home() / ".spaice-agents" / agent_id / "state"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = ledger_dir / "consensus_ledger.jsonl"

    entry: Dict[str, Any] = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "turn_id": uuid.uuid4().hex[:12],
        "cost_usd": cost_usd,
        "latency_s": latency_s,
        "stages_ran": stages_ran,
        "ok": ok,
        "trigger_reason": trigger_reason,
    }
    if error:
        entry["error"] = error

    with open(ledger_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
