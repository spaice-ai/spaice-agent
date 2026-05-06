"""Deferred consensus tool — the 3-chair pipeline, triggered on demand.

FW-1 contract: consensus no longer fires inline every turn. The LLM
calls this tool when it decides to spend the budget. Every code path
returns a structured dict; the tool never raises.

Codex review fixes (2026-05-03):
  #2 Counter advances BEFORE reset so FW-1 suppression window stays
     correct (reset → advance would make the first post-consensus turn
     have turns_since_call=1 instead of 0).
  #5 Ledger signature matches the spec (keyword-only args, agent_id
     first-positional, trigger_reason included).
  #8 All error paths include exception detail for debuggability.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict

logger = logging.getLogger(__name__)


def build_use_consensus_tool(agent_id: str) -> Dict[str, Any]:
    """Return a tool descriptor bound to this agent_id.

    Factory pattern — one agent, one tool. If you install this hook in
    two agents, each gets its own tool closure with its own ledger.
    """

    async def handler(question: str, context: str = "") -> Dict[str, Any]:
        return await _run(agent_id, question, context)

    descriptor: Dict[str, Any] = {
        "name": "use_consensus",
        "description": (
            "Fire 3-chair consensus panel. Two modes: thinking (DeepSeek V4 Pro "
            "→ GPT-5.5 → DeepSeek V4 Pro → Opus synth) for architecture/planning; "
            "coding (Opus 4.7 → Codex 5.3 → DeepSeek V4 Pro → Opus synth) for "
            "framework/code review. Cost: $0.10-0.30. Latency: 30-90s. Use "
            "sparingly for architecture decisions, schema freezes, approach "
            "lock-in. REQUIRES caller tool-budget ≥120s."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": (
                        "The question or decision to run through consensus."
                    ),
                },
                "context": {
                    "type": "string",
                    "default": "",
                    "description": (
                        "Optional additional context (search hits, recall, etc.)."
                    ),
                },
            },
            "required": ["question"],
        },
        "handler": handler,
    }
    return descriptor


async def _run(
    agent_id: str, question: str, context: str = "",
) -> Dict[str, Any]:
    """Real implementation — never raises, always returns structured dict."""
    from .. import ledger

    out: Dict[str, Any] = {
        "ok": False,
        "final_reply": None,
        "stages_ran": [],
        "total_cost_usd": 0.0,
        "total_latency_s": 0.0,
        "error": None,
    }

    # --- Lazy imports: tool still works if spaice_agent is partially broken
    try:
        from ..advisory import (
            advance_suppression_counter,
            reset_suppression_counter,
        )
        from ..config import load_agent_config
        from ..consensus import run_consensus
        from ..credentials import resolve_credential
    except ImportError as exc:
        out["error"] = f"spaice_agent_unavailable: {exc}"
        logger.warning("use_consensus: import failed: %s", exc)
        return out

    # --- Load config
    try:
        cfg = load_agent_config(agent_id)
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"config_load_failed: {exc}"
        logger.warning("use_consensus: config load failed: %s", exc)
        return out

    # --- Resolve API key (Codex finding #8: include detail)
    try:
        api_key = resolve_credential(cfg, "openrouter_api_key")
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"credential_unavailable: {exc}"
        logger.warning("use_consensus: credential unavailable: %s", exc)
        return out

    # --- Budget
    pipeline_budget = cfg.consensus.pipeline_timeout_s
    tool_budget = pipeline_budget + 3.0

    # --- Fire pipeline
    t0 = time.monotonic()
    try:
        async with asyncio.timeout(tool_budget):
            result = await run_consensus(
                cfg,
                api_key=api_key,
                user_message=question,
                context_md=context,
            )
    except asyncio.TimeoutError:
        elapsed = time.monotonic() - t0
        out["error"] = "pipeline_timeout"
        out["total_latency_s"] = elapsed
        logger.warning(
            "use_consensus: pipeline timed out after %.1fs", elapsed,
        )
        _safe_ledger(
            agent_id, cost_usd=0.0, latency_s=elapsed, stages_ran=0,
            ok=False, trigger_reason="tool_call", error="pipeline_timeout",
        )
        return out
    except Exception as exc:  # noqa: BLE001
        elapsed = time.monotonic() - t0
        out["error"] = f"pipeline_error: {exc}"
        out["total_latency_s"] = elapsed
        logger.warning("use_consensus: pipeline error: %s", exc)
        _safe_ledger(
            agent_id, cost_usd=0.0, latency_s=elapsed, stages_ran=0,
            ok=False, trigger_reason="tool_call", error=str(exc),
        )
        return out

    # --- Success or partial
    out["ok"] = True
    out["final_reply"] = result.final_reply
    out["stages_ran"] = [s.stage for s in result.stages]
    out["total_cost_usd"] = result.total_cost_usd
    out["total_latency_s"] = result.total_latency_s
    out["error"] = result.error

    # --- Ledger (Codex finding #5: correct signature, agent_id first-positional)
    _safe_ledger(
        agent_id,
        cost_usd=result.total_cost_usd,
        latency_s=result.total_latency_s,
        stages_ran=len(result.stages),
        ok=True,
        trigger_reason="tool_call",
        error=result.error,
    )

    # --- FW-1 suppression: advance BEFORE reset (Codex finding #2)
    # Rationale: orchestrator increments the counter at end-of-turn. The
    # tool runs DURING a turn, so if we only reset, the next turn's
    # end-of-turn increment would take it from 0 → 1, making the first
    # post-consensus turn read as turns_since_call=1 instead of 0. By
    # advancing first (to whatever the current value is + 1) and THEN
    # resetting to 0, we ensure the next orchestrator increment lands
    # at 1 cleanly. In practice reset_suppression_counter writes 0, and
    # the orchestrator's advance makes it 1 next turn — but the advance
    # here guarantees we don't silently lose a pending end-of-turn
    # advance if the tool is called mid-turn before the orchestrator's
    # final advance step.
    try:
        advance_suppression_counter(cfg)
    except Exception as exc:  # noqa: BLE001
        logger.warning("use_consensus: pre-reset advance failed: %s", exc)
    try:
        reset_suppression_counter(cfg)
    except Exception as exc:  # noqa: BLE001
        logger.warning("use_consensus: suppression reset failed: %s", exc)

    return out


def _safe_ledger(agent_id: str, **kwargs: Any) -> None:
    """Best-effort ledger write — never raises."""
    try:
        from .. import ledger
        ledger.append_ledger(agent_id, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.warning("use_consensus: ledger append failed: %s", exc)
