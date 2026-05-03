"""spaice_agent.consensus — 3-chair sequential consensus pipeline.

Implements correction 007: proposer → critic → reviewer → synthesis.
Each stage prompt carries ALL prior-stage outputs verbatim (no summaries
between stages). The synthesis stage always runs on Opus — the reviewer
chair does NOT produce the user-facing reply.

Public entry point: ``run_consensus(config, credential, user_message,
context_md="")`` returns ``ConsensusResult``. ``context_md`` is injected
after the user message before any stage fires — used to pass search hits
or recall hits into chair 1.

Per-stage failures: if a stage times out or errors, the pipeline aborts
and returns a ``ConsensusResult`` with ``error`` set. Opus synthesis still
runs if at least reviewer output exists — we never silently skip to a
partial reply.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from .config import AgentConfig
from .openrouter_client import (
    BadRequestError,
    AuthError,
    ChatResult,
    OpenRouterClient,
    OpenRouterError,
)

__all__ = [
    "ConsensusError",
    "StageOutput",
    "ConsensusResult",
    "run_consensus",
]

logger = logging.getLogger(__name__)


class ConsensusError(RuntimeError):
    """Pipeline-level failure (can't recover to a user-facing reply)."""


@dataclass(frozen=True)
class StageOutput:
    stage: str              # "proposer" | "critic" | "reviewer" | "synthesis"
    model: str
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_s: float


@dataclass
class ConsensusResult:
    user_message: str
    stages: List[StageOutput]         # Ordered: proposer, critic, reviewer, synthesis
    final_reply: str                  # Synthesis output — what the user sees
    total_cost_usd: float
    total_latency_s: float
    error: Optional[str] = None       # None on success

    def as_dict(self) -> dict:
        """Serialise to a JSON-safe dict for audit logging."""
        return {
            "user_message": self.user_message,
            "stages": [
                {
                    "stage": s.stage,
                    "model": s.model,
                    "text_len": len(s.text),
                    "input_tokens": s.input_tokens,
                    "output_tokens": s.output_tokens,
                    "cost_usd": s.cost_usd,
                    "latency_s": s.latency_s,
                }
                for s in self.stages
            ],
            "total_cost_usd": self.total_cost_usd,
            "total_latency_s": self.total_latency_s,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Prompt composition
# ---------------------------------------------------------------------------


def _compose_user_prompt(
    user_message: str,
    context_md: str,
    prior_stages: List[StageOutput],
    stage_instruction: str,
) -> str:
    """Build the ``user`` message body for a stage.

    Format (deterministic template from corr 007):

        --- ORIGINAL USER REQUEST ---
        {user_message}

        --- CONTEXT (memory recall / search hits) ---       # only if non-empty
        {context_md}

        --- CHAIR 1 (PROPOSER) ---                          # only if present
        {chair_1_output}

        --- CHAIR 2 (CRITIC) ---                            # only if present
        {chair_2_output}

        --- CHAIR 3 (REVIEWER) ---                          # only if present
        {chair_3_output}

        --- YOUR TASK ---
        {stage_instruction}
    """
    parts = [
        "--- ORIGINAL USER REQUEST ---",
        user_message.strip(),
    ]
    if context_md.strip():
        parts.extend(["", "--- CONTEXT (memory recall / search hits) ---",
                      context_md.strip()])
    chair_labels = {
        "proposer": "CHAIR 1 (PROPOSER)",
        "critic":   "CHAIR 2 (CRITIC)",
        "reviewer": "CHAIR 3 (REVIEWER)",
    }
    for prior in prior_stages:
        label = chair_labels.get(prior.stage)
        if not label:
            continue
        parts.extend(["", f"--- {label} ---", prior.text.strip()])
    parts.extend(["", "--- YOUR TASK ---", stage_instruction.strip()])
    return "\n".join(parts)


def _truncate(text: str, max_chars: int) -> str:
    """Truncate text at ``max_chars`` with a marker."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    cut = text[:max_chars].rstrip()
    return cut + f"\n\n_[truncated at {max_chars} chars by stage policy]_"


# ---------------------------------------------------------------------------
# Stage runner
# ---------------------------------------------------------------------------


async def _run_stage(
    client: OpenRouterClient,
    stage_name: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    stage_timeout_s: float,
    truncate_output_chars: int,
) -> StageOutput:
    """Run one stage. Raises ConsensusError on recoverable failure with
    an explanatory message so the caller can abort the pipeline cleanly.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    try:
        result: ChatResult = await asyncio.wait_for(
            client.chat(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                deadline_s=stage_timeout_s,
            ),
            timeout=stage_timeout_s + 1.0,
        )
    except asyncio.TimeoutError as exc:
        raise ConsensusError(
            f"stage '{stage_name}' timed out after {stage_timeout_s}s"
        ) from exc
    except (AuthError, BadRequestError) as exc:
        raise ConsensusError(
            f"stage '{stage_name}' unrecoverable: {exc}"
        ) from exc
    except OpenRouterError as exc:
        raise ConsensusError(
            f"stage '{stage_name}' failed: {exc}"
        ) from exc

    text = _truncate(result.text, truncate_output_chars)
    return StageOutput(
        stage=stage_name,
        model=result.model,
        text=text,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=result.cost_usd,
        latency_s=result.latency_s,
    )


# ---------------------------------------------------------------------------
# Synthesis stage spec (lives in code, not config — always Opus, always last)
# ---------------------------------------------------------------------------

SYNTHESIS_SYSTEM_PROMPT = (
    "You are the final synthesis stage. The user will see YOUR output as "
    "Jarvis's reply. You have the original user request plus three chair "
    "outputs (proposer, critic, reviewer).\n\n"
    "Integrate the reviewer's revision as the primary content. Preserve "
    "any critique points the reviewer dismissed that you still think "
    "matter. Trust your own editorial judgement over the reviewer's when "
    "they conflict.\n\n"
    "VOICE: Direct, peer-to-peer. Australian spelling (organise, colour, "
    "adaptors, optimise). No preamble, no \"great question\", no \"as we "
    "discussed\", no apologies. Terse; Telegram-fit where possible. "
    "Markdown for structure. No emoji unless the user used one first. "
    "No profanity.\n\n"
    "DO NOT mention chair 1, chair 2, chair 3, the critique, the review, "
    "the pipeline, consensus, or any other models. DO NOT say \"after "
    "review\" or \"the critique noted\". The user sees this as your "
    "single reply produced by a single mind."
)
SYNTHESIS_INSTRUCTION = (
    "Compose the single reply to the user. Integrate the reviewer's "
    "revision as the primary content. Preserve any critique points the "
    "reviewer dismissed that you still think matter. Write in Jarvis "
    "voice. No mention of chairs, critique, or pipeline."
)
SYNTHESIS_MODEL = "anthropic/claude-opus-4.7"
SYNTHESIS_MAX_TOKENS = 2048
# Synthesis is always Opus per correction 007. If overall pipeline_timeout
# has room, give synthesis a dedicated window rather than sharing the
# whole remaining budget — keeps a rogue reviewer from eating the budget
# and starving the synthesis call.
SYNTHESIS_TIMEOUT_S = 60.0


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run_consensus(
    config: AgentConfig,
    api_key: str,
    user_message: str,
    *,
    context_md: str = "",
    client: Optional[OpenRouterClient] = None,
) -> ConsensusResult:
    """Fire the full 3-chair pipeline + Opus synthesis.

    Args:
        config: AgentConfig with a validated 3-stage consensus.pipeline.
        api_key: OpenRouter API key (already resolved by credentials module).
        user_message: Raw user text.
        context_md: Optional pre-formatted markdown of recall/search hits.
        client: Injected OpenRouterClient; if None one is created and closed.

    Returns:
        ConsensusResult. ``final_reply`` is the synthesised answer; empty
        if ``error`` is set.
    """
    if not user_message or not user_message.strip():
        return ConsensusResult(
            user_message=user_message,
            stages=[],
            final_reply="",
            total_cost_usd=0.0,
            total_latency_s=0.0,
            error="empty user_message",
        )
    if len(config.consensus.pipeline) != 3:
        return ConsensusResult(
            user_message=user_message,
            stages=[],
            final_reply="",
            total_cost_usd=0.0,
            total_latency_s=0.0,
            error=(
                f"expected 3-stage pipeline, got "
                f"{len(config.consensus.pipeline)}"
            ),
        )

    owns_client = client is None
    or_client = client or OpenRouterClient(api_key=api_key)

    stages: List[StageOutput] = []
    total_cost = 0.0
    start = asyncio.get_event_loop().time()
    error: Optional[str] = None

    # Per-stage user-prompt imperatives — the system prompt already
    # carries the full role instruction, so the task line is a short
    # pointer. Making these stage-specific (rather than a single generic
    # "produce your {stage}") helps cheaper models that skim the system
    # prompt and rely on the user-message to ground the task.
    _STAGE_INSTRUCTIONS = {
        "proposer": (
            "Draft your first-pass answer now. Be thorough but do not "
            "over-polish; gaps are the critic's job. Do NOT write in "
            "final-reply voice."
        ),
        "critic": (
            "List every weakness in the proposer's answer. Numbered "
            "findings only — factual errors, hidden assumptions, missed "
            "risks, incomplete specs, wrong claims, premature "
            "optimisations, spec gaps. Do NOT rewrite the answer."
        ),
        "reviewer": (
            "Review the proposal and critique. Decide which critique "
            "points are valid. Produce a revised version of the proposal "
            "that absorbs valid critiques and explicitly rejects invalid "
            "ones with a one-line reason. Do NOT write in final-reply "
            "voice — your output feeds the synthesis stage."
        ),
    }

    try:
        # --- Three chairs from config ---
        for stage_cfg in config.consensus.pipeline:
            stage_instruction = _STAGE_INSTRUCTIONS.get(
                stage_cfg.stage,
                f"Produce your {stage_cfg.stage} output now.",
            )
            user_prompt = _compose_user_prompt(
                user_message=user_message,
                context_md=context_md,
                prior_stages=stages,
                stage_instruction=stage_instruction,
            )
            try:
                out = await _run_stage(
                    client=or_client,
                    stage_name=stage_cfg.stage,
                    model=stage_cfg.model,
                    system_prompt=stage_cfg.system,
                    user_prompt=user_prompt,
                    max_tokens=stage_cfg.max_tokens,
                    stage_timeout_s=stage_cfg.stage_timeout_s,
                    truncate_output_chars=stage_cfg.truncate_output_chars,
                )
            except ConsensusError as exc:
                error = str(exc)
                break
            stages.append(out)
            total_cost += out.cost_usd

        # --- Synthesis: ONLY if we have reviewer output (consensus rule, corr 007) ---
        if (
            error is None
            and len(stages) == 3
            and stages[-1].stage == "reviewer"
        ):
            synth_prompt = _compose_user_prompt(
                user_message=user_message,
                context_md=context_md,
                prior_stages=stages,
                stage_instruction=SYNTHESIS_INSTRUCTION,
            )
            try:
                synth_out = await _run_stage(
                    client=or_client,
                    stage_name="synthesis",
                    model=SYNTHESIS_MODEL,
                    system_prompt=SYNTHESIS_SYSTEM_PROMPT,
                    user_prompt=synth_prompt,
                    max_tokens=SYNTHESIS_MAX_TOKENS,
                    stage_timeout_s=min(
                        SYNTHESIS_TIMEOUT_S,
                        config.consensus.pipeline_timeout_s,
                    ),
                    truncate_output_chars=0,  # never truncate the user-facing reply
                )
                stages.append(synth_out)
                total_cost += synth_out.cost_usd
                final_reply = synth_out.text.strip()
            except ConsensusError as exc:
                error = f"synthesis failed: {exc}"
                final_reply = ""
        else:
            final_reply = ""
            if error is None:
                error = "pipeline incomplete — missing chair outputs"

    finally:
        if owns_client:
            await or_client.aclose()

    total_latency = asyncio.get_event_loop().time() - start
    return ConsensusResult(
        user_message=user_message,
        stages=stages,
        final_reply=final_reply,
        total_cost_usd=total_cost,
        total_latency_s=total_latency,
        error=error,
    )
