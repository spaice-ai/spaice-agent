"""Reusable hook handler — product-shaped entry point.

The Hermes-side hook file should be a thin shim that does:

    from spaice_agent.hook import make_hook

    AGENT_ID = "jarvis"
    handle, register_tools = make_hook(AGENT_ID)

All business logic lives in this package, so bugs get fixed once and
shipped via pip install -U spaice-agent to every agent that uses it.

Contract:
  - handle(event_type, context) -> dict | None
  - handler MUST NEVER raise; all failure paths return None
  - returns {"reply": str} to short-circuit the agent, {"context": str}
    to inject markdown into the prompt, or None to pass through.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional, Tuple

logger = logging.getLogger(__name__)

HandleFn = Callable[[str, dict], Awaitable[Optional[dict]]]
RegisterFn = Callable[[Any], None]

# ---------------------------------------------------------------------------
# BuildGuard instance cache (keyed by agent_id, survives a session)
# ---------------------------------------------------------------------------
_GUARDS: dict[str, Any] = {}


def _get_guard(agent_id: str, cfg: Any) -> Optional[Any]:
    """Return a cached :class:`BuildGuard` for *agent_id*, or create one.

    Returns ``None`` when the ``BuildGuard`` import is unavailable so that the
    caller can degrade without crashing the host agent.
    """
    guard = _GUARDS.get(agent_id)
    if guard is not None:
        return guard

    try:
        from spaice_agent.orchestrator import BuildGuard
    except ImportError:
        logger.warning("BuildGuard unavailable; guard disabled for %s", agent_id)
        return None

    guard = BuildGuard(cfg)
    _GUARDS[agent_id] = guard
    return guard


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------

def make_hook(agent_id: str) -> Tuple[HandleFn, RegisterFn]:
    """Return (handle, register_tools) closures bound to this agent_id.

    Factory pattern — one line at the hook site picks up the whole
    pipeline. If ``spaice_agent`` deps are broken, both returned
    callables degrade to no-ops so the host agent survives.
    """

    async def handle(event_type: str, context: dict) -> Optional[dict]:
        return await _handle(agent_id, event_type, context)

    def register_tools(registry: Any) -> None:
        _register_tools(agent_id, registry)

    return handle, register_tools


# ---------------------------------------------------------------------------
# Internal: handle implementation
# ---------------------------------------------------------------------------

async def _handle(
    agent_id: str, event_type: str, context: dict,
) -> Optional[dict]:
    """Real handler — imports are deferred so a missing dep → no-op.

    MUST NEVER raise.
    """
    # fmt: off
    # ------------------------------------------------------------------
    # Deferred imports – if they fail the whole handler is a no-op
    # ------------------------------------------------------------------
    try:
        from spaice_agent.config import load_agent_config
        from spaice_agent.orchestrator import handle_message
    except Exception as exc:  # noqa: BLE001 — broad on purpose
        logger.warning(
            "spaice_agent import failed; middleware disabled: %s", exc,
        )
        return None
    # fmt: on

    # ------------------------------------------------------------------
    # NEW – pre_tool_call guard (BuildGuard)
    # ------------------------------------------------------------------
    if event_type == "pre_tool_call":
        try:
            tool_name = context.get("tool_name")
            tool_args = context.get("tool_args", {})

            if not isinstance(tool_name, str) or not tool_name.strip():
                logger.debug("pre_tool_call without valid tool_name; ignored")
                return None

            cfg = load_agent_config(agent_id)          # already imported above
            guard = _get_guard(agent_id, cfg)
            if guard is None:
                # BuildGuard module missing – safe pass-through
                return None

            decision = guard.check_pending_write(tool_name, tool_args)

            if decision.allowed:
                return None  # pass-through

            # Build the refusal message
            target = tool_args.get("path", "unknown target")
            reason = getattr(decision, "reason", "policy violation")
            reply = (
                f"BUILD-GUARD refused write to {target}: {reason}. "
                "Fire DeepSeek V4 Pro for this module via OpenRouter first."
            )
            return {"reply": reply}

        except Exception as exc:  # noqa: BLE001 – defensive
            logger.warning("BuildGuard pre_tool_call handler failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Existing pre-turn / pre-message handling (unchanged)
    # ------------------------------------------------------------------
    message = (context or {}).get("message") or ""
    if not isinstance(message, str) or not message.strip():
        return None

    try:
        cfg = load_agent_config(agent_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("SPAICE config load failed for %s: %s", agent_id, exc)
        return None

    try:
        result = await handle_message(message, cfg)
    except Exception as exc:  # noqa: BLE001
        logger.warning("SPAICE orchestrator failed: %s", exc)
        return None

    if result is None:
        return None

    return _shape_response(result)


def _shape_response(result: Any) -> Optional[dict]:
    """Turn a HandleResult into Hermes's {reply|context|None} protocol.

    Every field is treated as optional — the orchestrator's dataclass
    contract is strong, but this boundary is stronger ("never raise").
    """
    try:
        reply = getattr(result, "reply", None)
        if isinstance(reply, str) and reply.strip():
            logger.info(
                "SPAICE override: search handback reply (%d chars)", len(reply),
            )
            return {"reply": reply}

        context_blocks: list[str] = []

        recall = getattr(result, "recall", None)
        recall_md = _safe_to_markdown(recall)
        if recall_md:
            context_blocks.append("## Memory recall\n" + recall_md)

        search = getattr(result, "search", None)
        search_md = _safe_to_markdown(search)
        if search_md:
            context_blocks.append("## Research\n" + search_md)

        advisory = getattr(result, "consensus_advisory", None)
        if isinstance(advisory, str) and advisory.strip():
            context_blocks.append("## Consensus advisory\n" + advisory)

        if context_blocks:
            combined = "\n\n".join(context_blocks)
            logger.info(
                "SPAICE context inject: %d blocks (%d chars)",
                len(context_blocks), len(combined),
            )
            return {"context": combined}

    except Exception as exc:  # noqa: BLE001 — defensive boundary
        logger.warning("SPAICE response shaping failed: %s", exc)
        return None

    return None


def _safe_to_markdown(obj: Any) -> Optional[str]:
    """Call obj.to_markdown() defensively — return None on any failure.

    Codex finding #4 fix: explicit None/hasattr check instead of
    ``getattr(obj, "to_markdown", lambda: None)()`` which fires the
    lambda on truthy-but-no-method objects.
    """
    if obj is None:
        return None
    if not hasattr(obj, "to_markdown"):
        return None
    try:
        md = obj.to_markdown()
    except Exception:  # noqa: BLE001
        return None
    if isinstance(md, str) and md.strip():
        return md
    return None


# ---------------------------------------------------------------------------
# Internal: tool registration
# ---------------------------------------------------------------------------

def _register_tools(agent_id: str, registry: Any) -> None:
    """Register the ``use_consensus`` tool bound to this agent_id."""
    try:
        from spaice_agent.tools import build_use_consensus_tool
        tool = build_use_consensus_tool(agent_id)
        registry.register(tool)
        logger.info("registered use_consensus tool for %s", agent_id)
    except ImportError as exc:
        logger.warning(
            "use_consensus tool not registered (spaice_agent unavailable): %s",
            exc,
        )
    except Exception as exc:  # noqa: BLE001 — defensive
        logger.warning("use_consensus tool registration failed: %s", exc)