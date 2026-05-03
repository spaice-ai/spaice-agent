"""spaice_agent.orchestrator — fire triggers, run pipelines, return reply.

Entry point for an inbound user message. Does in order:
  1. Always fire memory_recall (free, per correction 006)
  2. If search_triggered() OR message contains the `research` word, fire search
  3. Decide the reply strategy:
       - If consensus_triggered(): run 3-chair + Opus synthesis
       - Else if search fired: return a "search-results-only" markdown block
       - Else: return None (caller falls back to its default Opus path)
  4. Enforce daily fire caps via the DailyCounter — caps exceed ⇒ skip that
     pipeline, log, continue.

This module is the glue. Hermes middleware wiring is separate (wrapping
this function in an inbound-message hook) and is tracked as a follow-up
blocker — wiring into the gateway needs either a Hermes plugin point or
a forked gateway handler.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .budget import BudgetExceeded, DailyCounter
from .config import AgentConfig
from .consensus import ConsensusResult, run_consensus
from .credentials import resolve_credential
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

# Words that force a search (beyond the config's phrase anchors)
# Per correction 006 additions — kept here rather than in config because
# changing it requires a doctrine edit, not a YAML tweak.
FORCE_SEARCH_WORDS = {"research", "analyse", "analyze"}
_WORD_BOUNDARY = re.compile(r"\b([A-Za-z]+)\b")


@dataclass
class OrchestratorResult:
    """End-to-end result; ``reply`` is what Hermes should send upstream."""
    user_message: str
    reply: Optional[str]                      # None → fall through to default handler
    recall: Optional[RecallResult] = None
    search: Optional[SearchResult] = None
    consensus: Optional[ConsensusResult] = None
    fired: List[str] = field(default_factory=list)
    skipped: Dict[str, str] = field(default_factory=dict)
    total_cost_usd: float = 0.0
    error: Optional[str] = None


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
    """Main entry point. Never raises on expected failure paths — always
    returns an OrchestratorResult. Truly unexpected exceptions bubble up."""
    result = OrchestratorResult(user_message=message, reply=None)
    if not isinstance(message, str) or not message.strip():
        result.reply = None
        return result

    counter = counter or DailyCounter(config.agent_id)

    # ------------------------------------------------------------------
    # 1. Memory recall — always free, always fires
    # ------------------------------------------------------------------
    if config.memory.enabled:
        try:
            result.recall = await recall(message)
            result.fired.append("recall")
        except Exception as exc:  # noqa: BLE001 - recall must never break pipeline
            logger.warning("memory_recall failed: %s", exc)
            result.skipped["recall"] = str(exc)

    # ------------------------------------------------------------------
    # 2. Search — fires on keyword/phrase/URL or force-search word
    # ------------------------------------------------------------------
    search_reason = matched_search_reason(message, config)
    force_word = _has_force_search_word(message)
    if (search_reason or force_word) and config.search.enabled:
        try:
            if not counter.check_and_fire(
                "search", config.search.daily_fire_cap,
            ):
                result.skipped["search"] = "daily cap exhausted"
            else:
                creds: Dict[str, str] = {}
                for provider in config.search.providers:
                    field_name = f"{provider.name}_api_key"
                    try:
                        creds[provider.name] = resolve_credential(
                            config, field_name,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.info(
                            "search provider %s unavailable: %s",
                            provider.name, exc,
                        )
                try:
                    result.search = await run_search(
                        config, message, credentials=creds,
                    )
                    result.fired.append(
                        f"search({search_reason or 'word:' + force_word})"
                    )
                    result.total_cost_usd += result.search.cost_usd
                except SearchError as exc:
                    result.skipped["search"] = str(exc)
        except BudgetExceeded as exc:
            result.skipped["search"] = f"counter lock: {exc}"

    # ------------------------------------------------------------------
    # 3. Consensus — fires on trigger words/phrases, or force-analyse word
    # ------------------------------------------------------------------
    consensus_reason = matched_consensus_reason(message, config)
    # Force-consensus: `analyse`/`analyze` trigger the panel even if the
    # word-regex already caught it (the two overlap — belt and braces).
    if (consensus_reason or (force_word and force_word in {"analyse", "analyze"})) \
            and config.consensus.enabled:
        try:
            if not counter.check_and_fire(
                "consensus", config.consensus.daily_fire_cap,
            ):
                result.skipped["consensus"] = "daily cap exhausted"
            else:
                try:
                    api_key = resolve_credential(config, "openrouter_api_key")
                except Exception as exc:  # noqa: BLE001
                    result.skipped["consensus"] = (
                        f"openrouter credential missing: {exc}"
                    )
                else:
                    # Compose context_md from recall + search
                    context_bits: List[str] = []
                    if result.recall and result.recall.hits:
                        context_bits.append(result.recall.to_markdown())
                    if result.search and result.search.hits:
                        context_bits.append(result.search.to_markdown())
                    context_md = "\n\n".join(context_bits)
                    result.consensus = await run_consensus(
                        config,
                        api_key=api_key,
                        user_message=message,
                        context_md=context_md,
                    )
                    result.fired.append(
                        f"consensus({consensus_reason or 'word:' + force_word})"
                    )
                    result.total_cost_usd += result.consensus.total_cost_usd
                    if result.consensus.error:
                        result.error = result.consensus.error
                    result.reply = result.consensus.final_reply or None
        except BudgetExceeded as exc:
            result.skipped["consensus"] = f"counter lock: {exc}"

    # ------------------------------------------------------------------
    # 4. Fallback reply shape
    # ------------------------------------------------------------------
    # If consensus didn't fire but search did, hand the user the search
    # results directly — per Jozef 2026-05-03 "make research fire then
    # search, then I can decide if I need to analyse further."
    if result.reply is None and result.search and result.search.hits:
        result.reply = result.search.to_markdown()

    return result
