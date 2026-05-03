"""spaice_agent.orchestrator — recall + search in pre-turn hook, emit consensus advisory (FW-1 v3).

Entry point for an inbound user message. Does in order:
  1. Memory recall (always, time-boxed per-op)
  2. Search (triggered, time-boxed per-op)
  3. Consensus advisory emission (pure string, no fire) – tool executes later
  4. Fallback reply if search produced results, otherwise None

Consensus is no longer run inline. The `use_consensus` tool is registered separately.
Suppression of repeated advisories is managed via a file-locked state counter.
Per-op timeouts are caught explicitly and recorded as structured `skipped[]` entries.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .advisory import (
    advance_suppression_counter,
    build_advisory as _build_advisory,
    is_suppressed as _suppressed,
)
from .budget import BudgetExceeded, DailyCounter
from .config import AgentConfig
from .consensus import ConsensusResult  # type only, not called here
from .memory_recall import RecallResult, recall
from .search import SearchError, SearchResult, run_search
from .triggers import (
    matched_consensus_reason,
    matched_search_reason,
)

__all__ = [
    "OrchestratorResult",
    "handle_message",
]

logger = logging.getLogger(__name__)

# Words that force a search (beyond config phrase anchors)
FORCE_SEARCH_WORDS = {"research", "analyse", "analyze"}
_WORD_BOUNDARY = re.compile(r"\b([A-Za-z]+)\b")


@dataclass
class OrchestratorResult:
    """End-to-end result; ``reply`` is what Hermes should send upstream."""
    user_message: str
    reply: Optional[str] = None                # None → fall through to default handler
    recall: Optional[RecallResult] = None
    search: Optional[SearchResult] = None
    consensus: Optional[ConsensusResult] = None  # kept for interface compatibility, always None in FW-1
    fired: List[str] = field(default_factory=list)
    skipped: Dict[str, str] = field(default_factory=dict)
    total_cost_usd: float = 0.0
    error: Optional[str] = None
    # FW-1 additions
    consensus_advisory: Optional[str] = None   # advisory string if triggered and not suppressed
    advisory_suppressed: bool = False



# Advisory helpers live in advisory.py — imported at top of this module.
# (Extracted 2026-05-03 per FW-1 spec Part B so use_consensus tool and
# orchestrator can share suppression-state logic.)

# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def _has_force_search_word(message: str) -> Optional[str]:
    """Return the matched word if the message contains a force-search trigger."""
    for m in _WORD_BOUNDARY.finditer(message):
        word = m.group(1).lower()
        if word in FORCE_SEARCH_WORDS:
            return word
    return None


async def handle_message(
    message: str,
    config: AgentConfig,
    *,
    counter: Optional[DailyCounter] = None,
) -> OrchestratorResult:
    """FW-1 pre-turn hook: recall + search + consensus advisory.

    Never raises on expected failure paths — always returns an
    OrchestratorResult. Truly unexpected exceptions bubble up.
    """
    result = OrchestratorResult(
        user_message=message,
        reply=None,
    )
    if not isinstance(message, str) or not message.strip():
        return result

    counter = counter or DailyCounter(config.agent_id)

    # Deadline computed AFTER all sync setup (v3 finding 1)
    deadline = time.monotonic() + config.hook.total_timeout_s

    # ------------------------------------------------------------------
    # 1. Memory recall — always fires, time-boxed per-op
    # ------------------------------------------------------------------
    if config.memory.enabled:
        budget = deadline - time.monotonic()
        if budget <= 0:
            result.skipped["recall"] = "envelope_exhausted_before_recall"
        else:
            stage_timeout = config.memory.stage_timeout_s
            recall_timeout = min(stage_timeout, budget)
            try:
                result.recall = await asyncio.wait_for(
                    recall(message), timeout=recall_timeout
                )
                result.fired.append("recall")
            except asyncio.TimeoutError:
                result.skipped["recall"] = f"per_op_timeout: {stage_timeout}s"
                logger.warning("recall hit per-op timeout")
            except Exception as exc:  # noqa: BLE001
                result.skipped["recall"] = f"error: {exc}"
                logger.warning("memory_recall failed: %s", exc)

    # ------------------------------------------------------------------
    # 2. Search — fires on trigger or force-search word, time-boxed
    # ------------------------------------------------------------------
    search_reason = matched_search_reason(message, config)
    force_word = _has_force_search_word(message)
    if (search_reason or force_word) and config.search.enabled:
        try:
            if not counter.check_and_fire("search", config.search.daily_fire_cap):
                result.skipped["search"] = "daily cap exhausted"
            else:
                budget = deadline - time.monotonic()
                if budget <= 0:
                    result.skipped["search"] = "envelope_exhausted_before_search"
                else:
                    stage_timeout = config.search.stage_timeout_s
                    search_timeout = min(stage_timeout, budget)

                    # Resolve credentials (fast, inside envelope)
                    creds: Dict[str, str] = {}
                    for provider in config.search.providers:
                        field_name = f"{provider.name}_api_key"
                        try:
                            creds[provider.name] = config.get_credential(field_name)
                        except Exception as exc:  # noqa: BLE001
                            logger.info(
                                "search provider %s unavailable: %s",
                                provider.name, exc,
                            )

                    try:
                        result.search = await asyncio.wait_for(
                            run_search(config, message, credentials=creds),
                            timeout=search_timeout,
                        )
                        reason_str = search_reason or f"word:{force_word}"
                        result.fired.append(f"search({reason_str})")
                        result.total_cost_usd += result.search.cost_usd
                    except asyncio.TimeoutError:
                        result.skipped["search"] = f"per_op_timeout: {stage_timeout}s"
                        logger.warning("search hit per-op timeout")
                    except SearchError as exc:
                        result.skipped["search"] = str(exc)
                    except Exception as exc:  # noqa: BLE001
                        result.skipped["search"] = f"error: {exc}"
        except BudgetExceeded as exc:
            result.skipped["search"] = f"counter lock: {exc}"

    # ------------------------------------------------------------------
    # 3. Consensus advisory (pure function, no I/O)
    # ------------------------------------------------------------------
    consensus_reason = matched_consensus_reason(message, config)
    # Force-consensus trigger: "analyse"/"analyze" also fires advisory
    consensus_triggered = consensus_reason or (
        force_word and force_word in {"analyse", "analyze"}
    )
    if consensus_triggered and config.consensus.enabled:
        reason = consensus_reason if consensus_reason else f"word:{force_word}"
        if _suppressed(config):
            result.advisory_suppressed = True
            logger.info(
                "consensus_advisory suppressed: reason=%s suppression=recent_call",
                reason,
            )
        else:
            result.consensus_advisory = _build_advisory(reason, config)
            logger.info("consensus_advisory emitted: reason=%s", reason)

    # ------------------------------------------------------------------
    # 4. Advance suppression counter (every turn)
    # ------------------------------------------------------------------
    try:
        advance_suppression_counter(config)
    except Exception as exc:  # noqa: BLE001 — counter is best-effort
        logger.warning("failed to increment suppression counter: %s", exc)

    # ------------------------------------------------------------------
    # 5. Fallback reply shape — search results handback
    # ------------------------------------------------------------------
    if result.reply is None and result.search and result.search.hits:
        result.reply = result.search.to_markdown()

    return result