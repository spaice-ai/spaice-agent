"""spaice_agent.orchestrator — recall + search in pre-turn hook, emit consensus advisory (FW-1 v3).

Entry point for an inbound user message. Does in order:
  1. Memory recall (always, time-boxed per-op)
  2. Search (triggered, time-boxed per-op)
  3. Consensus advisory emission (pure string, no fire) – tool executes later
  4. Fallback reply if search produced results, otherwise None

Consensus is no longer run inline. The `use_consensus` tool is registered separately.
Suppression of repeated advisories is managed via a file-locked state counter.
Per-op timeouts are caught explicitly and recorded as structured `skipped[]` entries.

BuildGuard v1.1 is a middleware-enforced policy that prevents coding writes to
``spaice_agent/**/*.py`` (excluding ``tests/``) without a prior DeepSeek-V4-Pro
implementation call.  It is consumed by the Hermes tool-call interceptor layer
and does not alter the existing ``handle_message()`` flow.

**v1.1 hardens** the original implementation against five blocker findings from
Codex review (relative-path bypasses, execute_code/terminal coverage,
one-shot exemption expiry, concurrency build-log writes).  The known
prompt-aliasing bypass (injecting a target path into a DeepSeek prompt without
producing real code) remains documented for v0.3.0 diff-based enforcement.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Tuple, Union

try:
    import portalocker

    PORTALOCKER_AVAILABLE = True
except ImportError:
    PORTALOCKER_AVAILABLE = False

import yaml

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
    "BuildGuard",
    "BuildGuardDecision",
    "OrchestratorResult",
    "handle_message",
]

logger = logging.getLogger(__name__)

# Words that force a search (beyond config phrase anchors)
FORCE_SEARCH_WORDS = {"research", "analyse", "analyze"}
_WORD_BOUNDARY = re.compile(r"\b([A-Za-z]+)\b")

# ---------------------------------------------------------------------------
# BuildGuard
# ---------------------------------------------------------------------------

_BUILD_LOG_DIR = "~/.spaice-agents/{agent_id}/logs"
_EXEMPTION_PATH = "~/.spaice-agents/{agent_id}/build-exemption.yaml"
_OPENROUTER_LOG_GLOB = "openrouter-*.jsonl"
_LOOKBACK_HOURS_DEFAULT = 1

# Tools that may write .py files to the spaice_agent directory
_CODING_TOOL_NAMES = {"write_file", "patch", "terminal", "execute_code"}

# ---------------------------------------------------------------------------
# execute‑code detection helpers (B4)
# ---------------------------------------------------------------------------

# Each tuple: (compiled pattern, group number containing the .py path)
_EXEC_CODE_PATTERNS: list[Tuple[re.Pattern, int]] = [
    (re.compile(r"write_file\s*\(\s*[\"']([^\"']+\.py)[\"']", re.DOTALL), 1),
    (
        re.compile(
            r"open\s*\(\s*[\"']([^\"']+\.py)[\"']\s*,\s*[\"'][wab][ab]?\+?[\"']",
            re.DOTALL,
        ),
        1,
    ),
    (
        re.compile(
            r"os\.rename\s*\(\s*[\"'][^\"']*[\"']\s*,\s*[\"']([^\"']+\.py)[\"']",
            re.DOTALL,
        ),
        1,
    ),
    (
        re.compile(
            r"shutil\.(?:copy2?|move)\s*\(\s*[\"'][^\"']*[\"']\s*,\s*[\"']([^\"']+\.py)[\"']",
            re.DOTALL,
        ),
        1,
    ),
    (
        re.compile(
            r"(?:pathlib\.)?Path\s*\(\s*[\"']([^\"']+\.py)[\"']\s*\)\s*\.\s*(?:write_text|write_bytes)",
            re.DOTALL,
        ),
        1,
    ),
    (
        re.compile(r"\.rename\s*\(\s*[\"']([^\"']+\.py)[\"']\s*\)", re.DOTALL),
        1,
    ),
]

# ---------------------------------------------------------------------------
# terminal‑command detection helpers (B5)
# ---------------------------------------------------------------------------

# Written commands that may create/modify .py files inside spaice_agent.
_TERMINAL_WRITE_VERBS = frozenset({"mv", "cp", "sed", "tee", "install"})

# First, explicit patterns that capture the destination .py path.
_TERMINAL_EXPLICIT_PATTERNS: list[Tuple[re.Pattern, int]] = [
    (re.compile(r"(?:>|>>|of=)\s*(\S+\.py)", re.IGNORECASE), 1),
    (re.compile(r"mv\s+.*\s+(\S+\.py)", re.IGNORECASE), 1),
    (re.compile(r"cp\s+.*\s+(\S+\.py)", re.IGNORECASE), 1),
    (re.compile(r"sed\s+-i[^\s]*\s+.*\s+(\S+\.py)", re.IGNORECASE), 1),
    (re.compile(r"tee\s+(?:-a\s+)?(\S+\.py)", re.IGNORECASE), 1),
    (re.compile(r"install\s+.*\s+(\S+\.py)", re.IGNORECASE), 1),
]


@dataclass(frozen=True)
class BuildGuardDecision:
    """Outcome of a build guard check.

    Attributes:
        allowed: Whether the pending write is permitted.
        reason: Human-readable reason for the decision.
        nonce: Session-random nonce emitted in the banner.
        target_path: The target .py path inside spaice_agent/ (if detected), else None.
    """

    allowed: bool
    reason: str
    nonce: str
    target_path: Optional[str] = None


class BuildGuard:
    """Middleware policy that enforces DeepSeek-V4-Pro before spaice_agent/*.py writes.

    **Known bypass (v1.1):** The implementation-call check is a prompt-string
    match.  An attacker could craft a DeepSeek prompt that mentions the target
    path without producing real code.  Diff-based enforcement is planned for
    v0.3.0.
    """

    def __init__(self, config: AgentConfig) -> None:
        self._config = config
        self._agent_id = config.agent_id
        self._nonce = uuid.uuid4().hex[:8]
        self._build_log_dir = Path(
            _BUILD_LOG_DIR.format(agent_id=self._agent_id)
        ).expanduser()
        self._exemption_path = Path(
            _EXEMPTION_PATH.format(agent_id=self._agent_id)
        ).expanduser()
        # Ensure log directory exists
        self._build_log_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def check_pending_write(
        self, tool_name: str, tool_args: dict
    ) -> BuildGuardDecision:
        """Evaluate whether a pending coding tool call is permitted.

        Returns a ``BuildGuardDecision``; if ``allowed`` is ``False`` the
        caller must refuse the tool invocation entirely.
        """
        target = self._is_coding_write(tool_name, tool_args)
        if target is None:
            decision = BuildGuardDecision(
                allowed=True,
                reason="not-a-coding-write",
                nonce=self._nonce,
            )
            self._emit_banner(decision)
            return decision

        # Exemption check takes priority
        if self._check_exemption(target):
            decision = BuildGuardDecision(
                allowed=True,
                reason="exemption-active",
                nonce=self._nonce,
                target_path=target,
            )
            self._emit_banner(decision)
            return decision

        # DeepSeek call log check
        if self._deepseek_call_log_has_target(target):
            decision = BuildGuardDecision(
                allowed=True,
                reason="deepseek-call-found",
                nonce=self._nonce,
                target_path=target,
            )
        else:
            decision = BuildGuardDecision(
                allowed=False,
                reason="no-deepseek-call",
                nonce=self._nonce,
                target_path=target,
            )

        self._emit_banner(decision)
        return decision

    # ------------------------------------------------------------------
    # Internal detection helpers
    # ------------------------------------------------------------------

    def _is_coding_write(
        self, tool_name: str, tool_args: dict
    ) -> Optional[str]:
        """Return the target .py path if this tool call would write inside
        spaice_agent/ (excluding tests/).
        """
        if tool_name not in _CODING_TOOL_NAMES:
            return None

        candidate = None
        if tool_name in ("write_file", "patch"):
            candidate = tool_args.get("path") or tool_args.get("file_path")
        elif tool_name == "terminal":
            command = tool_args.get("command", "")
            candidate = self._extract_py_path_from_terminal_command(command)
        elif tool_name == "execute_code":
            code = tool_args.get("code", "")
            candidate = self._extract_py_path_from_execute_code(code)

        if not candidate:
            return None
        return self._normalize_target_path(candidate)

    @staticmethod
    def _extract_py_path_from_terminal_command(command: str) -> Optional[str]:
        """Extract the best-guess destination .py path from a terminal command.

        Uses explicit patterns first, then falls back to a conservative
        spaice_agent keyword scan if a write verb is present.
        """
        # Explicit patterns
        for pattern, group in _TERMINAL_EXPLICIT_PATTERNS:
            match = pattern.search(command)
            if match:
                return match.group(group)

        # Conservative fallback: spaice_agent/ token + write signal
        if "spaice_agent/" in command:
            # Check for any write-verb or redirection
            has_write_signal = any(
                verb in command for verb in _TERMINAL_WRITE_VERBS
            ) or any(
                sym in command for sym in (">", ">>", "of=")
            )
            if has_write_signal:
                # Extract the last spaice_agent/*.py token
                py_tokens = re.findall(r"\S+\.py", command)
                for token in reversed(py_tokens):
                    if "spaice_agent/" in token:
                        return token
        return None

    @staticmethod
    def _extract_py_path_from_execute_code(code: str) -> Optional[str]:
        """Find a .py destination inside execute_code script code.

        Handles: write_file, open, os.rename, shutil.copy/move,
        Path.write_text/bytes, .rename.
        """
        for pattern, group in _EXEC_CODE_PATTERNS:
            match = pattern.search(code)
            if match:
                return match.group(group)
        return None

    @staticmethod
    def _normalize_target_path(raw: str) -> Optional[str]:
        """Return a relative ``spaice_agent/**/*.py`` path or ``None`` if the
        path is not inside the protected tree.

        Normalisation is performed purely lexically (PurePosixPath) so the
        guard never touches the filesystem.  Traversal attempts (residual
        ``..``) are rejected.
        """
        if not raw:
            return None

        # Work exclusively with POSIX semantics (framework requires
        # macOS / Linux).
        try:
            posix = PurePosixPath(raw)
        except ValueError:
            return None  # malformed path

        # Manually resolve . and .. without filesystem access.
        resolved: list[str] = []
        for part in posix.parts:
            if part == ".":
                continue
            if part == "..":
                if resolved and resolved[-1] != "..":
                    resolved.pop()
                else:
                    resolved.append("..")
            else:
                resolved.append(part)

        # Reject paths that attempt to escape the root.
        if any(p == ".." for p in resolved):
            return None

        # Locate the spaice_agent anchor anywhere in the resolved list.
        try:
            anchor = resolved.index("spaice_agent")
        except ValueError:
            # Absolute paths may contain a root marker; after resolution
            # the component "spaice_agent" might not be present at all.
            return None

        subpath = resolved[anchor:]

        if len(subpath) < 2:
            return None
        if not subpath[-1].endswith(".py"):
            return None

        # Exclude tests/ directory.
        if "tests" in subpath[1:]:
            return None

        # Reassemble into a forward‑slash string.
        return "/".join(subpath)

    # ------------------------------------------------------------------
    # DeepSeek call log inspection
    # ------------------------------------------------------------------

    def _deepseek_call_log_has_target(
        self,
        target_path: str,
        lookback_hours: int = _LOOKBACK_HOURS_DEFAULT,
    ) -> bool:
        """Return True if the OpenRouter call log contains a recent DeepSeek call
        mentioning *target_path* or its derived framework spec file.
        """
        spec_file = self._derive_spec_filename(target_path)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

        log_files = self._get_recent_openrouter_log_files(cutoff)
        for log_path in log_files:
            if not log_path.is_file():
                continue
            try:
                with open(log_path, "r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            logger.debug(
                                "Skipping malformed JSON line in %s", log_path
                            )
                            continue

                        # Model must match exactly
                        if entry.get("model") != "deepseek/deepseek-v4-pro":
                            continue

                        # Check prompt for target path or spec file
                        prompt_text = self._extract_prompt_text(entry)
                        if (
                            target_path in prompt_text
                            or spec_file in prompt_text
                        ):
                            # Optional timestamp check
                            ts = self._entry_timestamp(entry)
                            if ts is None or ts >= cutoff:
                                return True
            except OSError as exc:
                logger.warning(
                    "Failed to read call log %s: %s", log_path, exc
                )

        return False

    @staticmethod
    def _derive_spec_filename(target_path: str) -> str:
        """Derive the expected framework spec filename from a source path.

        ``spaice_agent/memory/foo.py`` → ``reviews/memory-foo-framework.md``.
        """
        rel = target_path
        if rel.startswith("spaice_agent/"):
            rel = rel[len("spaice_agent/"):]
        module = rel.removesuffix(".py").replace("/", "-")
        return f"reviews/{module}-framework.md"

    @staticmethod
    def _extract_prompt_text(entry: dict) -> str:
        """Flatten the prompt/messages from a log entry into a single string."""
        if "prompt" in entry:
            return str(entry["prompt"])
        messages = entry.get("messages", [])
        parts = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role != "system" and content:
                if isinstance(content, list):
                    parts.extend(
                        str(c.get("text", ""))
                        for c in content
                        if "text" in c
                    )
                else:
                    parts.append(str(content))
        return " ".join(parts)

    @staticmethod
    def _entry_timestamp(entry: dict) -> Optional[datetime]:
        """Extract an aware UTC datetime from a log entry, if present."""
        for key in ("timestamp", "created", "created_at", "ts"):
            raw = entry.get(key)
            if not raw:
                continue
            try:
                if isinstance(raw, (int, float)):
                    return datetime.fromtimestamp(raw, tz=timezone.utc)
                dt = datetime.fromisoformat(
                    str(raw).replace("Z", "+00:00")
                )
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except (ValueError, TypeError):
                continue
        return None

    def _get_recent_openrouter_log_files(
        self, cutoff: datetime
    ) -> list[Path]:
        """Return the list of openrouter-*.jsonl files that span the lookback
        window.
        """
        log_dir = Path(
            _BUILD_LOG_DIR.format(agent_id=self._agent_id)
        ).expanduser()
        if not log_dir.is_dir():
            return []

        candidates = sorted(log_dir.glob(_OPENROUTER_LOG_GLOB))
        result: list[Path] = []

        date_pattern = re.compile(r"openrouter-(\d{4}-\d{2}-\d{2})\.jsonl$")
        today = datetime.now(timezone.utc).date()  # not used directly
        cutoff_date = cutoff.date()

        for path in candidates:
            match = date_pattern.match(path.name)
            if not match:
                result.append(path)  # fallback: include any matching glob
                continue
            try:
                file_date = datetime.strptime(
                    match.group(1), "%Y-%m-%d"
                ).date()
                if file_date >= cutoff_date:
                    result.append(path)
            except ValueError:
                result.append(path)
        return result

    # ------------------------------------------------------------------
    # Exemption
    # ------------------------------------------------------------------

    def _check_exemption(self, target_path: str) -> bool:
        """Return True if a valid one-shot build exemption exists for
        *target_path*, and atomically consume it (delete the file) to enforce
        the single-commit rule.

        Exemption file format (YAML):
            target: spaice_agent/orchestrator.py
            expires_after: single_commit
        """
        if not self._exemption_path.is_file():
            return False

        try:
            data = yaml.safe_load(
                self._exemption_path.read_text(encoding="utf-8")
            )
        except (yaml.YAMLError, OSError) as exc:
            logger.warning(
                "Failed to read exemption file %s: %s",
                self._exemption_path,
                exc,
            )
            return False

        if not isinstance(data, dict):
            return False
        if data.get("target") != target_path:
            return False
        if data.get("expires_after") != "single_commit":
            return False

        # Consume the exemption: delete the file to prevent reuse.
        try:
            self._exemption_path.unlink()
        except FileNotFoundError:
            # Race — another thread already consumed it; that's fine.
            pass
        except OSError as exc:
            logger.warning(
                "Could not delete exemption file %s: %s",
                self._exemption_path,
                exc,
            )

        return True

    # ------------------------------------------------------------------
    # Banner & logging
    # ------------------------------------------------------------------

    def _emit_banner(self, decision: BuildGuardDecision) -> None:
        """Write the ASCII banner to stderr and append a JSONL entry to the
        build log, using a file lock when available (N4).
        """
        decision_str = "ALLOW" if decision.allowed else "REFUSE"
        target_str = decision.target_path or "-"
        banner = (
            f"━━━ BUILD-GUARD [nonce={decision.nonce}] ━━━\n"
            f"target: {target_str}\n"
            f"decision: {decision_str}\n"
            f"reason: {decision.reason}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        )
        sys.stderr.write(banner)
        sys.stderr.flush()

        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "nonce": decision.nonce,
            "target": decision.target_path,
            "decision": decision_str,
            "reason": decision.reason,
            "agent_id": self._agent_id,
        }
        log_file = (
            self._build_log_dir
            / f"builds-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.log"
        )

        if PORTALOCKER_AVAILABLE:
            try:
                with portalocker.Lock(log_file, "a", timeout=2) as fh:
                    fh.write(
                        json.dumps(log_entry, ensure_ascii=False) + "\n"
                    )
            except (OSError, portalocker.exceptions.LockException) as exc:
                logger.warning(
                    "Failed to write build-guard log %s: %s", log_file, exc
                )
        else:
            try:
                with open(log_file, "a", encoding="utf-8") as fh:
                    fh.write(
                        json.dumps(log_entry, ensure_ascii=False) + "\n"
                    )
            except OSError as exc:
                logger.warning(
                    "Failed to write build-guard log %s: %s", log_file, exc
                )


# ---------------------------------------------------------------------------
# Existing orchestrator helpers
# ---------------------------------------------------------------------------

@dataclass
class OrchestratorResult:
    """End-to-end result; ``reply`` is what Hermes should send upstream."""

    user_message: str
    reply: Optional[str] = None  # None → fall through to default handler
    recall: Optional[RecallResult] = None
    search: Optional[SearchResult] = None
    consensus: Optional[ConsensusResult] = (
        None  # kept for interface compatibility, always None in FW-1
    )
    fired: List[str] = field(default_factory=list)
    skipped: Dict[str, str] = field(default_factory=dict)
    total_cost_usd: float = 0.0
    error: Optional[str] = None
    # FW-1 additions
    consensus_advisory: Optional[str] = (
        None  # advisory string if triggered and not suppressed
    )
    advisory_suppressed: bool = False


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
                result.skipped["recall"] = (
                    f"per_op_timeout: {stage_timeout}s"
                )
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
            if not counter.check_and_fire(
                "search", config.search.daily_fire_cap
            ):
                result.skipped["search"] = "daily cap exhausted"
            else:
                budget = deadline - time.monotonic()
                if budget <= 0:
                    result.skipped[
                        "search"
                    ] = "envelope_exhausted_before_search"
                else:
                    stage_timeout = config.search.stage_timeout_s
                    search_timeout = min(stage_timeout, budget)

                    # Resolve credentials (fast, inside envelope)
                    creds: Dict[str, str] = {}
                    for provider in config.search.providers:
                        field_name = f"{provider.name}_api_key"
                        try:
                            creds[provider.name] = config.get_credential(
                                field_name
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.info(
                                "search provider %s unavailable: %s",
                                provider.name,
                                exc,
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
                        result.skipped["search"] = (
                            f"per_op_timeout: {stage_timeout}s"
                        )
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
        reason = (
            consensus_reason
            if consensus_reason
            else f"word:{force_word}"
        )
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