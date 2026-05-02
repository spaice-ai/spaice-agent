from __future__ import annotations

import logging
import re
from typing import List, Tuple

from .config import AgentConfig

logger = logging.getLogger(__name__)


class _CompiledPatterns:
    __slots__ = (
        "search_phrase_anchors",
        "search_url_end_pattern",
        "consensus_words",
        "consensus_phrases",
    )

    def __init__(
        self,
        anchors: List[Tuple[str, re.Pattern]],
        url_pat: re.Pattern,
        words: List[Tuple[str, re.Pattern]],
        phrases: List[Tuple[str, re.Pattern]],
    ) -> None:
        self.search_phrase_anchors = anchors
        self.search_url_end_pattern = url_pat
        self.consensus_words = words
        self.consensus_phrases = phrases


_COMPILED: dict[str, _CompiledPatterns] = {}


def _get_compiled(config: AgentConfig) -> _CompiledPatterns:
    """Return the compiled-pattern bundle for a config instance.

    Keyed by (agent_id, id(config)) so that hot-reloading a new config
    instance (same agent_id, different values) invalidates the cache.
    YAML edits that pass through the config loader produce a new
    AgentConfig object whose id() differs from any cached one.

    If a regex in the YAML fails to compile, the bad pattern is logged
    and skipped; valid patterns still take effect. This keeps a single
    typo in ops config from crashing the whole hook.
    """
    cache_key = f"{config.agent_id}:{id(config)}"
    if cache_key in _COMPILED:
        return _COMPILED[cache_key]

    # -- search --
    anchors: list[tuple[str, re.Pattern]] = []
    for a in config.search.triggers.phrase_anchors:
        try:
            anchors.append((a, re.compile(a, re.IGNORECASE)))
        except re.error as exc:  # pragma: no cover - log path, hit via manual misconfig
            logger.warning("Skipping invalid search anchor regex %r: %s", a, exc)
    url_pat = re.compile(r'https?://[^\s<>"\']+[^\w]*$', re.IGNORECASE)

    # -- consensus --
    words: list[tuple[str, re.Pattern]] = []
    for w in config.consensus.triggers.words:
        try:
            words.append(
                (w, re.compile(r"\b" + re.escape(w) + r"\b", re.IGNORECASE))
            )
        except re.error as exc:  # pragma: no cover
            logger.warning("Skipping invalid consensus word regex %r: %s", w, exc)
    phrases: list[tuple[str, re.Pattern]] = []
    for p in config.consensus.triggers.phrases:
        try:
            phrases.append(
                (p, re.compile(r"\b" + re.escape(p) + r"\b", re.IGNORECASE))
            )
        except re.error as exc:  # pragma: no cover
            logger.warning("Skipping invalid consensus phrase regex %r: %s", p, exc)

    compiled = _CompiledPatterns(anchors, url_pat, words, phrases)
    _COMPILED[cache_key] = compiled
    return compiled


def _strip_excluded_regions(text: str) -> str:
    """Remove inline-code spans, fenced code blocks, and quoted lines.

    Handles three Markdown-ish patterns so they don't produce false-positive
    consensus triggers:

    1. Fenced code blocks (```...```). A fenced block's contents can contain
       LITERAL backticks inside code examples; we therefore look for a
       triple-backtick fence pair explicitly, not ``[^`]*``.
    2. Inline code spans (single-backtick pairs). Collapsed with a greedy
       non-newline match so a stray lone backtick doesn't eat half the line.
    3. Quoted lines (leading ``>`` marker, possibly preceded by whitespace).
    """
    # 1. Fenced code blocks first — so their inner content doesn't trip the
    #    inline regex below. Match an opening ``` through to the next ``` on
    #    its own semantics (DOTALL so newlines count as regular chars).
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    # 2. Inline single-backtick spans. Restrict to a single line so a lone
    #    unpaired backtick in prose doesn't swallow the next 500 chars.
    text = re.sub(r"`[^`\n]*`", " ", text)
    # 3. Quoted lines
    text = re.sub(r"^\s*>.*$", "", text, flags=re.MULTILINE)
    return text


def _is_past_tense(text: str, end: int, matched_word: str) -> bool:
    """
    Return True if the match at ``end`` is immediately followed by a
    past-tense suffix ('ed' or 'd') and a word boundary, *and* the
    matched word does not already end in 'd' or 'e'.
    """
    if matched_word[-1].lower() in ("d", "e"):
        return False

    # "ed" suffix
    if text[end : end + 2].lower() == "ed" and (
        end + 2 >= len(text) or not text[end + 2].isalnum()
    ):
        return True
    # "d" suffix
    if text[end : end + 1].lower() == "d" and (
        end + 1 >= len(text) or not text[end + 1].isalnum()
    ):
        return True

    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def search_triggered(message: str, config: AgentConfig) -> bool:
    """Return ``True`` if the message triggers the *search* stage."""
    return matched_search_reason(message, config) is not None


def consensus_triggered(message: str, config: AgentConfig) -> bool:
    """Return ``True`` if the message triggers the *consensus* stage."""
    return matched_consensus_reason(message, config) is not None


def matched_search_reason(message: str, config: AgentConfig) -> str | None:
    """Return the trigger identity for the search stage, or ``None``."""
    if not config.search.enabled:
        return None
    # Codex 2026-05-03: don't kill legitimate short imperatives
    # ("google it", "find sku"). 5-char floor drops only pleasantries.
    if len(message.strip()) < 5:
        return None

    compiled = _get_compiled(config)

    if config.search.triggers.url_at_end and compiled.search_url_end_pattern.search(message):
        return "URL_AT_END"

    for anchor_str, pattern in compiled.search_phrase_anchors:
        if pattern.search(message):
            return anchor_str

    return None


def matched_consensus_reason(message: str, config: AgentConfig) -> str | None:
    """Return the trigger identity for the consensus stage, or ``None``."""
    if not config.consensus.enabled:
        return None
    # Codex 2026-05-03: keep the floor low — "plan rollout",
    # "audit logs", "review this" are all legitimate triggers.
    if len(message.strip()) < 5:
        return None

    stripped = _strip_excluded_regions(message)
    compiled = _get_compiled(config)

    # Words take priority
    for word, pattern in compiled.consensus_words:
        for m in pattern.finditer(stripped):
            if not _is_past_tense(stripped, m.end(), m.group()):
                return word

    # Then phrases
    for phrase, pattern in compiled.consensus_phrases:
        if pattern.search(stripped):
            return phrase

    return None