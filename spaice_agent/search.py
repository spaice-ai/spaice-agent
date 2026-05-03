"""spaice_agent.search — parallel web search with RRF-merged results.

Fans out a query to all configured providers (Exa, Brave) in parallel,
then merges results via Reciprocal Rank Fusion. Per-provider failures are
logged and skipped — the search returns whatever succeeded.

Brave will be absent for agent 'jarvis' until a key is provisioned; the
module already handles single-provider runs (no merge required, just
dedupe).

Per correction 006: search fires on URL-at-end, anchor phrases, and the
`research` word. Daily fire cap enforced by caller (orchestrator), not
here.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .config import AgentConfig

__all__ = [
    "SearchHit",
    "SearchResult",
    "SearchError",
    "run_search",
]

logger = logging.getLogger(__name__)


class SearchError(RuntimeError):
    """Raised when the entire search fans out to zero usable providers."""


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str
    snippet: str
    provider: str         # "exa" | "brave"
    raw_rank: int         # 1-indexed rank within that provider's own output


@dataclass(frozen=True)
class SearchResult:
    query: str
    hits: List[SearchHit]                # Merged + RRF-ranked
    provider_errors: Dict[str, str]      # provider_name -> error string
    providers_used: List[str]
    elapsed_s: float
    cost_usd: float = 0.0                # Currently always 0; reserved for
                                         # future billed providers.

    def to_markdown(self, max_hits: int = 8) -> str:
        """Render a compact markdown block for prompt injection."""
        if not self.hits:
            errs = "; ".join(
                f"{p}: {e}" for p, e in self.provider_errors.items()
            )
            return (
                f"_search returned no hits for '{self.query}'."
                + (f" Errors: {errs}_" if errs else "_")
            )
        lines = [f"**Search hits for `{self.query}`:**"]
        for i, h in enumerate(self.hits[:max_hits], start=1):
            snippet = h.snippet.replace("\n", " ")[:240]
            lines.append(
                f"{i}. [{h.title}]({h.url}) — {snippet} _({h.provider})_"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Provider callers
# ---------------------------------------------------------------------------


async def _search_exa(
    client: httpx.AsyncClient,
    endpoint: str,
    api_key: str,
    query: str,
    max_results: int,
    timeout_s: float,
) -> List[SearchHit]:
    """POST to Exa /search endpoint; returns ranked hits or raises."""
    payload = {
        "query": query,
        "numResults": max_results,
        "contents": {"text": {"maxCharacters": 500}},
    }
    headers = {"x-api-key": api_key, "Content-Type": "application/json"}
    resp = await client.post(
        endpoint, json=payload, headers=headers, timeout=timeout_s,
    )
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results") or []
    hits: List[SearchHit] = []
    for idx, r in enumerate(results, start=1):
        if not isinstance(r, dict):
            continue
        url = r.get("url") or ""
        if not url:
            continue
        title = (r.get("title") or "").strip() or url
        snippet = (
            r.get("text")
            or r.get("highlight")
            or r.get("summary")
            or ""
        )
        hits.append(SearchHit(
            url=url.strip(),
            title=title[:200],
            snippet=str(snippet).strip()[:500],
            provider="exa",
            raw_rank=idx,
        ))
    return hits


async def _search_brave(
    client: httpx.AsyncClient,
    endpoint: str,
    api_key: str,
    query: str,
    max_results: int,
    timeout_s: float,
) -> List[SearchHit]:
    """GET Brave Search API; returns ranked hits or raises."""
    params = {"q": query, "count": max_results}
    headers = {
        "X-Subscription-Token": api_key,
        "Accept": "application/json",
    }
    resp = await client.get(
        endpoint, params=params, headers=headers, timeout=timeout_s,
    )
    resp.raise_for_status()
    data = resp.json()
    web = data.get("web") or {}
    results = web.get("results") or []
    hits: List[SearchHit] = []
    for idx, r in enumerate(results, start=1):
        if not isinstance(r, dict):
            continue
        url = r.get("url") or ""
        if not url:
            continue
        hits.append(SearchHit(
            url=url.strip(),
            title=(r.get("title") or url).strip()[:200],
            snippet=(r.get("description") or "").strip()[:500],
            provider="brave",
            raw_rank=idx,
        ))
    return hits


# ---------------------------------------------------------------------------
# RRF merge
# ---------------------------------------------------------------------------


def _merge_rrf(
    per_provider: List[List[SearchHit]],
    k: int,
) -> List[SearchHit]:
    """Reciprocal-rank fusion across provider result lists.

    score(doc) = sum_over_providers( 1 / (k + rank_in_that_provider) )

    Dedupe by URL (case-insensitive + trailing-slash insensitive). When
    multiple providers return the same URL, the first-seen hit's metadata
    wins — Exa is listed first in typical configs so Exa's richer snippet
    is preferred over Brave's short description.
    """
    if k < 1:
        raise ValueError("RRF k must be >= 1")

    scores: Dict[str, float] = {}
    winners: Dict[str, SearchHit] = {}
    for provider_hits in per_provider:
        for hit in provider_hits:
            key = hit.url.rstrip("/").lower()
            if not key:
                continue
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + hit.raw_rank)
            if key not in winners:
                winners[key] = hit

    ranked = sorted(
        winners.items(),
        key=lambda kv: scores.get(kv[0], 0.0),
        reverse=True,
    )
    return [hit for _, hit in ranked]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run_search(
    config: AgentConfig,
    query: str,
    *,
    credentials: Dict[str, str],
    client: Optional[httpx.AsyncClient] = None,
) -> SearchResult:
    """Run the configured search pipeline for ``query``.

    ``credentials`` maps provider_name -> api_key. A provider without a
    credential entry is skipped cleanly (not an error — e.g. Brave absent
    on Jarvis until Jozef provisions).

    The search obeys the config's per-provider timeout AND a wrapping
    stage_timeout — the whole fan-out runs under ``asyncio.gather`` with
    a wall-clock watchdog. Providers that complete before the watchdog
    fires contribute their results; providers still running at the
    watchdog boundary are cancelled and recorded as a stage-level error.
    """
    if not isinstance(query, str) or not query.strip():
        raise SearchError("query must be non-empty")
    if not config.search.enabled:
        raise SearchError("search disabled in config")
    if not config.search.providers:
        raise SearchError("no providers configured")
    # Credential pre-check: if no configured provider has credentials,
    # fail fast — avoids firing HTTP calls that will all no-op.
    if not any(credentials.get(p.name) for p in config.search.providers):
        raise SearchError(
            "no providers had credentials available; "
            "configure at least one API key"
        )

    start = asyncio.get_event_loop().time()
    owns_client = client is None
    http_client = client or httpx.AsyncClient()
    search_cfg = config.search

    provider_errors: Dict[str, str] = {}
    providers_used: List[str] = []
    per_provider_hits: List[List[SearchHit]] = []

    async def _call_one(provider) -> Tuple[str, List[SearchHit]]:
        api_key = credentials.get(provider.name)
        if not api_key:
            return provider.name, []
        try:
            if provider.name == "exa":
                hits = await _search_exa(
                    http_client,
                    provider.endpoint,
                    api_key,
                    query,
                    provider.max_results,
                    provider.per_request_timeout_s,
                )
            elif provider.name == "brave":
                hits = await _search_brave(
                    http_client,
                    provider.endpoint,
                    api_key,
                    query,
                    provider.max_results,
                    provider.per_request_timeout_s,
                )
            else:  # pragma: no cover - config schema restricts Literal
                return provider.name, []
            return provider.name, hits
        except httpx.HTTPStatusError as exc:
            provider_errors[provider.name] = (
                f"HTTP {exc.response.status_code}"
            )
            return provider.name, []
        except httpx.HTTPError as exc:
            provider_errors[provider.name] = f"transport: {exc}"
            return provider.name, []
        except (ValueError, KeyError, TypeError) as exc:
            provider_errors[provider.name] = f"parse: {exc}"
            return provider.name, []

    stage_timeout_hit = False
    try:
        # Run each provider as a task wrapped in its own watchdog so we
        # never lose partial successes to a stage-level cancellation.
        tasks = {
            provider.name: asyncio.create_task(_call_one(provider))
            for provider in search_cfg.providers
        }
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks.values(), return_exceptions=True),
                timeout=search_cfg.stage_timeout_s,
            )
        except asyncio.TimeoutError:
            stage_timeout_hit = True
        # Cancel any unfinished tasks and harvest the ones that did land.
        for name, task in tasks.items():
            if not task.done():
                task.cancel()
                provider_errors[name] = (
                    f"stage timeout after {search_cfg.stage_timeout_s}s"
                )
                continue
            try:
                ret_name, hits = task.result()
            except asyncio.CancelledError:
                provider_errors[name] = "cancelled"
                continue
            except Exception as exc:  # noqa: BLE001 — rollup for gather
                provider_errors[name] = f"unexpected: {exc}"
                continue
            if hits:
                providers_used.append(ret_name)
                per_provider_hits.append(hits)
        # Await cancelled tasks so we don't leak pending coroutines.
        for task in tasks.values():
            if task.cancelled() or not task.done():
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass

        merged = _merge_rrf(per_provider_hits, k=search_cfg.merge.k)
        if stage_timeout_hit and not merged:
            provider_errors.setdefault(
                "_stage",
                f"timeout after {search_cfg.stage_timeout_s}s",
            )

    finally:
        if owns_client:
            await http_client.aclose()

    elapsed = asyncio.get_event_loop().time() - start
    return SearchResult(
        query=query,
        hits=merged,
        provider_errors=provider_errors,
        providers_used=providers_used,
        elapsed_s=elapsed,
    )
