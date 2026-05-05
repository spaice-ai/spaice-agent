"""spaice_agent.memory_recall — multi-source recall over vault + database.

Primary path: fast ILIKE text search over memory_entries (sub-100ms).
Enhancement: pgvector semantic search runs in background, merged if complete
before the hook deadline. File-based BM25 vault scan runs concurrently.

The orchestrator receives a unified RecallResult with inline context blocks.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

__all__ = ["RecallHit", "RecallResult", "recall"]

logger = logging.getLogger(__name__)

DEFAULT_RECALL_SCRIPT = Path("~/jarvis/scripts/recall_scan.py").expanduser()
DEFAULT_TIMEOUT_S = 3.0
DEFAULT_MAX_HITS = 10


@dataclass(frozen=True)
class RecallHit:
    path: str
    preview: str
    source: str       # "vector", "text", "file"
    score: float = 0.0


@dataclass(frozen=True)
class RecallResult:
    hits: List[RecallHit]
    elapsed_s: float
    error: Optional[str]
    db_enabled: bool = False
    db_hits: int = 0
    vector_enhanced: bool = False

    def to_markdown(self) -> str:
        if self.error and not self.hits:
            return f"_memory recall failed: {self.error}_"
        if not self.hits:
            return "_memory recall: no matches in vault or database_"

        lines = ["**Memory recall hits:**"]
        for h in self.hits:
            icon = {"vector": "🧠", "text": "💾", "file": "📁"}.get(h.source, "")
            preview = h.preview.replace("\n", " ")[:200]
            lines.append(f"- {icon} `{h.path}` — {preview}")
        if self.vector_enhanced:
            lines.append(f"\n_{self.db_hits} results (vector-enhanced)_")
        return "\n".join(lines)


async def recall(
    message: str,
    *,
    script_path: Path = DEFAULT_RECALL_SCRIPT,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_hits: int = DEFAULT_MAX_HITS,
    db_enabled: bool = True,
    workspace_id: str = "jozef",
) -> RecallResult:
    """Multi-source recall for the pre-turn hook.

    Strategy:
    1. Fire ILIKE (fast, ~30ms) and file BM25 concurrently.
    2. ILIKE always completes first — use results immediately.
    3. pgvector semantic search fires in background with remaining budget.
    4. Vector results merged in if they complete before deadline.

    Never raises. Per correction 006: recall fires free, never blocks.
    """
    start = asyncio.get_event_loop().time()

    if not message.strip():
        return RecallResult(hits=[], elapsed_s=0.0, error=None)

    db_hits: List[RecallHit] = []
    vector_enhanced = False
    errors: List[str] = []

    if db_enabled:
        # Fire ILIKE + vector concurrently
        ilike_task = asyncio.create_task(
            _db_recall_fast(message, max_hits)
        )
        vector_task = asyncio.create_task(
            _db_recall_vector(message, max_hits)
        )

        # ILIKE always completes first — use it immediately
        try:
            fast_results = await asyncio.wait_for(ilike_task, timeout=min(1.0, timeout_s * 0.3))
            db_hits = _to_recall_hits(fast_results, source="text")
        except asyncio.TimeoutError:
            errors.append("db ILIKE timed out")
        except Exception as exc:
            errors.append(f"db ILIKE: {exc}")

        # Vector: merge if it completes within remaining budget
        elapsed = asyncio.get_event_loop().time() - start
        remaining = timeout_s - elapsed
        if remaining > 0.5:
            try:
                vector_results = await asyncio.wait_for(vector_task, timeout=remaining)
                if vector_results:
                    vector_hits = _to_recall_hits(vector_results, source="vector")
                    db_hits = _merge_hits(db_hits, vector_hits)[:max_hits]
                    vector_enhanced = True
            except asyncio.TimeoutError:
                vector_task.cancel()
            except Exception as exc:
                logger.debug("vector enhancement failed: %s", exc)
        else:
            vector_task.cancel()

    # File recall — if script is available
    file_hits: List[RecallHit] = []
    if script_path.is_file():
        elapsed = asyncio.get_event_loop().time() - start
        remaining = timeout_s - elapsed
        if remaining > 0.05:  # at least try, even with tight budget
            try:
                file_hits, file_err = await _file_recall(
                    message, script_path, max_hits,
                    timeout=min(remaining, 3.0),
                )
                if file_err:
                    errors.append(f"file: {file_err}")
            except Exception as exc:
                errors.append(f"file recall: {exc}")

    # Merge: DB hits first, file hits append (can't overwrite DB)
    # Simple dedup append — file hits only added if path not already in DB
    seen_paths = {h.path.lower() for h in db_hits}
    for h in file_hits:
        if h.path.lower() not in seen_paths:
            db_hits.append(h)
            seen_paths.add(h.path.lower())
    merged = db_hits[:max_hits]
    elapsed = asyncio.get_event_loop().time() - start

    return RecallResult(
        hits=merged,
        elapsed_s=elapsed,
        error="; ".join(errors) if errors else None,
        db_enabled=db_enabled and len(db_hits) > 0,
        db_hits=len(db_hits),
        vector_enhanced=vector_enhanced and bool(db_hits),
    )


def _merge_hits(
    primary: List[RecallHit],
    secondary: List[RecallHit],
) -> List[RecallHit]:
    """Merge two hit lists, deduplicating by path.

    Secondary hits REPLACE primary hits with the same path when secondary
    has higher signal (e.g., vector replaces text), then append remaining.
    Caller is responsible for ordering: pass lower-priority first when you
    want overwrite semantics, higher-priority first when you want append-only.
    """
    merged: dict[str, RecallHit] = {}
    for h in primary:
        merged[h.path.lower()] = h
    for h in secondary:
        key = h.path.lower()
        if key in merged:
            # Replace only if secondary has higher score (vector > text)
            if h.score > merged[key].score:
                merged[key] = h
        else:
            merged[key] = h
    # Preserve primary order, append new secondary entries
    result: List[RecallHit] = []
    seen: set[str] = set()
    for h in primary:
        key = h.path.lower()
        if key in merged and key not in seen:
            result.append(merged[key])
            seen.add(key)
    for h in secondary:
        key = h.path.lower()
        if key in merged and key not in seen:
            result.append(merged[key])
            seen.add(key)
    return result


def _to_recall_hits(
    results: list[dict],
    *,
    source: str = "text",
) -> List[RecallHit]:
    """Convert db_store result dicts to RecallHit list."""
    hits: List[RecallHit] = []
    for r in results:
        score = r.get("score", r.get("similarity", 0.0))
        namespace = r.get("namespace", "unknown")
        summary = r.get("summary", "") or ""
        layer = r.get("layer", "?")
        eid = r.get("id", "")[:8]

        hits.append(RecallHit(
            path=f"db:{namespace}/{eid}",
            preview=f"[L{layer}] {summary[:180]}",
            source=source,
            score=score,
        ))
    return hits


# ---------------------------------------------------------------------------
# DB recall backends
# ---------------------------------------------------------------------------


async def _db_recall_fast(
    message: str,
    max_hits: int = 10,
) -> list[dict]:
    """Fast ILIKE text search — matched words → ranked by confidence."""
    import re
    from spaice_agent.memory.db_store import _get_pool

    words = [w for w in re.split(r"\W+", message.lower()) if len(w) > 1]
    if not words:
        return []

    pool = _get_pool()
    conn = pool.getconn()
    try:
        clauses = []
        params = []
        for w in words[:8]:
            clauses.append(
                "(LOWER(content) LIKE %s OR LOWER(summary) LIKE %s "
                "OR LOWER(namespace) LIKE %s)"
            )
            params.extend([f"%{w}%", f"%{w}%", f"%{w}%"])

        query = (
            "SELECT id, layer, namespace, content, summary, confidence "
            "FROM memory_entries "
            "WHERE workspace_id = 'jozef' AND user_validated = TRUE "
            f"AND ({' OR '.join(clauses)}) "
            "ORDER BY confidence DESC, updated_at DESC "
            "LIMIT %s"
        )
        params.append(max_hits)

        cur = conn.cursor()
        cur.execute(query, params)
        rows = cur.fetchall()
        cur.close()

        return [
            {
                "id": r[0], "layer": r[1], "namespace": r[2],
                "content": r[3], "summary": r[4], "confidence": r[5],
                "score": 0.7,  # ILIKE match = high confidence
            }
            for r in rows
        ]
    finally:
        pool.putconn(conn)


async def _db_recall_vector(message, max_hits):
    """Wrapper kept for interface compatibility — delegates to sync version."""
    return await asyncio.to_thread(_db_recall_vector_sync, message, max_hits)


def _db_recall_vector_sync(message: str, max_hits: int = 10) -> list[dict]:
    """pgvector semantic search — better ranking, slower (runs in thread)."""
    from spaice_agent.memory.db_store import retrieve

    return retrieve(message, limit=max_hits)


# ---------------------------------------------------------------------------
# File-based BM25 recall
# ---------------------------------------------------------------------------


async def _file_recall(
    message: str,
    script_path: Path,
    max_hits: int,
    timeout: float,
) -> tuple[List[RecallHit], Optional[str]]:
    """File-based BM25 recall via recall_scan.py shell-out."""
    cmd = [
        "python3", str(script_path),
        "--text", message,
        "--max", str(max_hits),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return [], f"file recall timed out after {timeout}s"

        if proc.returncode != 0:
            msg = stderr.decode("utf-8", errors="replace").strip()[:200]
            return [], f"recall_scan exit {proc.returncode}: {msg}"

        hits = _parse_file_output(
            stdout.decode("utf-8", errors="replace")
        )[:max_hits]
        return hits, None

    except (OSError, ValueError) as exc:
        return [], f"file recall exec failed: {exc}"


def _parse_file_output(stdout: str) -> List[RecallHit]:
    """Parse recall_scan.py output: <relative_path> — <preview>."""
    hits: List[RecallHit] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
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
        hits.append(RecallHit(
            path=path, preview=preview, source="file", score=1.0,
        ))
    return hits
