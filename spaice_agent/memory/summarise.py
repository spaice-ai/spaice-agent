from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from spaice_agent.config import AgentConfig
from spaice_agent.openrouter_client import ChatResult, OpenRouterClient

logger = logging.getLogger(__name__)

MODEL = "google/gemini-2.5-flash"
MAX_TOKENS = 1500
TEMPERATURE = 0.2
MAX_INPUT_CHARS = 40_000

SYSTEM_PROMPT = """You are summarising a Hermes session transcript for the spaice-agent memory system. Write a compact continuity record using EXACTLY these sections (H2):

## Goal
## Key decisions
## Outstanding threads
## Artefacts

Rules:
- Keep total output under 500 words.
- Focus on facts, decisions, and concrete outputs (files, commits, URLs, SKUs).
- Do not include pleasantries, emotional content, or tool noise.
- If the session is trivial (no real work), output exactly: TRIVIAL"""


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    date: str  # YYYY-MM-DD
    summary_md: str
    word_count: int
    cost_usd: float


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_api_key(cfg: AgentConfig) -> str:
    env_var = cfg.credentials.openrouter_api_key_env
    key = os.environ.get(env_var, "")
    if not key:
        logger.warning("OpenRouter API key not found in environment variable %s", env_var)
    return key


def _load_session_transcript(session_id: str, db_path: Path) -> str:
    """Read and flatten a session transcript from the Hermes SQLite database."""
    if not db_path.exists():
        logger.warning("Session DB not found at %s", db_path)
        return ""
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.execute("SELECT messages FROM sessions WHERE id = ?", (session_id,))
        row = cur.fetchone()
        if row is None:
            logger.warning("Session %s not found in DB", session_id)
            return ""
        messages_json = row[0]
    except Exception as exc:
        logger.error("Failed to read session %s from DB: %s", session_id, exc)
        return ""
    finally:
        # Codex 5.3 finding: close connection on every exit path
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    try:
        messages = json.loads(messages_json)
    except json.JSONDecodeError:
        logger.error("Invalid JSON for session %s", session_id)
        return ""

    lines: list[str] = []
    for m in messages:
        # Codex 5.3 finding: defend against non-dict entries in the JSON array
        # (malformed transcripts seen in the wild — skip rather than crash).
        if not isinstance(m, dict):
            continue
        role = m.get("role", "")
        if role not in ("user", "assistant"):
            continue
        content = m.get("content", "")
        if isinstance(content, list):
            # Anthropic-style content blocks
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    parts.append(block)
            content = "\n".join(parts)
        content = str(content).strip()
        if not content:
            continue

        # Skip system-injected blocks that add no value
        if re.search(r"\[IMPORTANT:\s*You are running as a scheduled cron", content, re.I):
            continue
        if re.search(r"\[CONTEXT COMPACTION", content, re.I):
            continue
        if re.search(r"^Review the conversation above and consider", content, re.I | re.M):
            continue
        if re.search(r"^\[SYSTEM NOTE:", content, re.I | re.M):
            continue

        tag = "USER" if role == "user" else "JARVIS"
        lines.append(f"=== {tag} ===\n{content}\n")

    transcript = "\n".join(lines)

    # If too big, keep the tail (most recent turns are most relevant)
    if len(transcript) > MAX_INPUT_CHARS:
        transcript = "[...earlier turns elided...]\n\n" + transcript[-MAX_INPUT_CHARS:]
    return transcript


def _fallback_summary(session_id: str) -> SessionSummary:
    """Return a minimal record when LLM summarisation fails."""
    return SessionSummary(
        session_id=session_id,
        date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        summary_md=(
            "## Goal\n- Summary unavailable (LLM error)\n\n"
            "## Key decisions\n- N/A\n\n"
            "## Outstanding threads\n- N/A\n\n"
            "## Artefacts\n- N/A"
        ),
        word_count=20,
        cost_usd=0.0,
    )


async def _write_summary_file(summary: SessionSummary, vault_root: Path) -> Path:
    """Atomically write the summary to vault_root/_archive/sessions/YYYY-MM-DD-<session_id>.md.

    Blocker fix (Codex Phase 1C #6): PID-suffixed tmp name to prevent concurrent
    writes colliding when two summarisers run on the same session_id.
    """
    archive_dir = vault_root / "_archive" / "sessions"
    await asyncio.to_thread(archive_dir.mkdir, parents=True, exist_ok=True)

    # Blocker fix (Codex Phase 1C #7): reject empty session_id — otherwise
    # filename becomes "YYYY-MM-DD-.md" which collides across calls.
    raw_id = summary.session_id.strip() if summary.session_id else ""
    if not raw_id:
        raw_id = "adhoc"
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", raw_id)
    filename = f"{summary.date}-{safe_id}.md"
    tmp_path = archive_dir / f".{filename}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    final_path = archive_dir / filename

    header = f"<!-- Generated by spaice-agent memory summarise at {datetime.now(timezone.utc).isoformat()} -->\n"
    full_content = header + summary.summary_md

    def _write():
        tmp_path.write_text(full_content, encoding="utf-8")
        os.replace(tmp_path, final_path)

    await asyncio.to_thread(_write)
    return final_path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def summarise_from_text(
    transcript: str,
    cfg: AgentConfig,
) -> SessionSummary:
    """Summarise a raw session transcript using the cheap LLM.

    Returns a SessionSummary with session_id="".
    """
    if not transcript.strip():
        return SessionSummary(
            session_id="",
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            summary_md="TRIVIAL",
            word_count=1,
            cost_usd=0.0,
        )

    api_key = _get_api_key(cfg)
    if not api_key:
        logger.error("Cannot summarise: no OpenRouter API key")
        return _fallback_summary("")

    client = OpenRouterClient(
        api_key=api_key,
        referer="https://spaice.local/",
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": transcript},
    ]

    try:
        result: ChatResult = await client.chat(
            messages=messages,
            model=MODEL,
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
        )
    except Exception as exc:
        logger.warning("LLM summarisation failed: %s", exc)
        return _fallback_summary("")

    summary_md = result.text.strip()
    # Enforce 500‑word limit
    words = summary_md.split()
    if len(words) > 500:
        summary_md = " ".join(words[:500]) + " ..."
    word_count = len(summary_md.split())
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    return SessionSummary(
        session_id="",
        date=date_str,
        summary_md=summary_md,
        word_count=word_count,
        cost_usd=result.cost_usd if result.cost_usd is not None else 0.0,
    )


async def summarise_session(
    session_id: str,
    cfg: AgentConfig,
) -> SessionSummary:
    """Summarise one Hermes session and write the record into the vault.

    Reads the session transcript from Hermes SQLite (path from cfg),
    summarises it, and atomically writes the result to
    ``<vault_root>/_archive/sessions/YYYY-MM-DD-<session_id>.md``.
    """
    # Resolve database path (default kept for backwards‑compatibility)
    db_path: Path
    if hasattr(cfg.memory, "session_db_path"):
        db_path = Path(cfg.memory.session_db_path)
    else:
        db_path = Path.home() / ".hermes" / "sessions.db"

    transcript = await asyncio.to_thread(_load_session_transcript, session_id, db_path)

    if not transcript.strip():
        summary = SessionSummary(
            session_id=session_id,
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            summary_md="TRIVIAL",
            word_count=1,
            cost_usd=0.0,
        )
        await _write_summary_file(summary, cfg.memory.root)
        return summary

    # Call the pure‑text summariser
    partial = await summarise_from_text(transcript, cfg)
    summary = SessionSummary(
        session_id=session_id,
        date=partial.date,
        summary_md=partial.summary_md,
        word_count=partial.word_count,
        cost_usd=partial.cost_usd,
    )

    # Persist to the vault
    await _write_summary_file(summary, cfg.memory.root)
    return summary