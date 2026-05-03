"""spaice_agent.memory_recall — read-only proper-noun lookup over ~/jarvis/.

Wraps the existing ``~/jarvis/scripts/recall_scan.py`` tool. If the tool is
absent (e.g. in CI), returns empty hits silently — recall is a "free fire"
trigger per correction 006 and MUST NOT block the agent.

The hits are formatted as a compact markdown block suitable for injection
into a downstream chair prompt or search context.
"""
from __future__ import annotations

import asyncio
import logging
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

__all__ = ["RecallHit", "RecallResult", "recall"]

logger = logging.getLogger(__name__)

DEFAULT_RECALL_SCRIPT = Path("~/jarvis/scripts/recall_scan.py").expanduser()
DEFAULT_TIMEOUT_S = 2.0
DEFAULT_MAX_HITS = 10


@dataclass(frozen=True)
class RecallHit:
    path: str       # Relative path inside ~/jarvis/
    preview: str    # First matching snippet


@dataclass(frozen=True)
class RecallResult:
    hits: List[RecallHit]
    elapsed_s: float
    error: Optional[str]  # None on success

    def to_markdown(self) -> str:
        """Render as a compact markdown block for prompt injection."""
        if self.error:
            return f"_memory recall failed: {self.error}_"
        if not self.hits:
            return "_memory recall: no matches in vault_"
        lines = ["**Memory recall hits:**"]
        for h in self.hits:
            preview = h.preview.replace("\n", " ")[:200]
            lines.append(f"- `{h.path}` — {preview}")
        return "\n".join(lines)


async def recall(
    message: str,
    *,
    script_path: Path = DEFAULT_RECALL_SCRIPT,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_hits: int = DEFAULT_MAX_HITS,
) -> RecallResult:
    """Fire ``recall_scan.py --text <message>`` and parse its output.

    Returns an empty, error-marked result on any failure — never raises.
    Recall fires on every relevant message and must never break the main
    pipeline, per correction 006 ("memory triggers fire free").
    """
    start = asyncio.get_event_loop().time()

    if not script_path.is_file():
        # Silent per contract: missing tool must not surface an error to
        # the caller's prompt. Log at debug only.
        logger.debug("recall script missing at %s", script_path)
        return RecallResult(hits=[], elapsed_s=0.0, error=None)
    if not message.strip():
        return RecallResult(hits=[], elapsed_s=0.0, error=None)

    cmd = [
        "python3",
        str(script_path),
        "--text",
        message,
        "--max",
        str(max_hits),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            elapsed = asyncio.get_event_loop().time() - start
            return RecallResult(
                hits=[],
                elapsed_s=elapsed,
                error=f"recall timed out after {timeout_s}s",
            )
        elapsed = asyncio.get_event_loop().time() - start
        if proc.returncode != 0:
            msg = stderr.decode("utf-8", errors="replace").strip()[:200]
            return RecallResult(
                hits=[], elapsed_s=elapsed,
                error=f"recall_scan exit {proc.returncode}: {msg}",
            )
        hits = _parse_output(stdout.decode("utf-8", errors="replace"))[:max_hits]
        return RecallResult(hits=hits, elapsed_s=elapsed, error=None)
    except (OSError, ValueError) as exc:
        elapsed = asyncio.get_event_loop().time() - start
        return RecallResult(
            hits=[],
            elapsed_s=elapsed,
            error=f"recall exec failed: {exc}",
        )


def _parse_output(stdout: str) -> List[RecallHit]:
    """Parse recall_scan.py output into structured hits.

    Output format (per recall_scan.py docstring):
        <relative_path> — <preview>

    Each hit is one line. Blank lines and lines without the em-dash
    separator are skipped.
    """
    hits: List[RecallHit] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Split on em-dash (U+2014) — primary separator
        # Fall back to ' - ' (ASCII) for robustness
        if "—" in line:
            path, _, preview = line.partition("—")
        elif " - " in line:
            path, _, preview = line.partition(" - ")
        else:
            continue
        path = path.strip().strip("`")
        preview = preview.strip()
        if not path:
            continue
        hits.append(RecallHit(path=path, preview=preview))
    return hits
