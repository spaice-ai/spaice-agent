from __future__ import annotations

import re
from typing import List, Tuple

from .config import AgentConfig


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
    agent_id = config.agent_id
    if agent_id not in _COMPILED:
        # -- search --
        anchors = [
            (a, re.compile(a, re.IGNORECASE))
            for a in config.search.triggers.phrase_anchors
        ]
        url_pat = re.compile(r'https?://[^\s<>"\']+[^\w]*$', re.IGNORECASE)

        # -- consensus --
        words = [
            (w, re.compile(r"\b" + re.escape(w) + r"\b", re.IGNORECASE))
            for w in config.consensus.triggers.words
        ]
        phrases = [
            (p, re.compile(r"\b" + re.escape(p) + r"\b", re.IGNORECASE))
            for p in config.consensus.triggers.phrases
        ]

        _COMPILED[agent_id] = _CompiledPatterns(anchors, url_pat, words, phrases)

    return _COMPILED[agent_id]


def _strip_excluded_regions(text: str) -> str:
    """Remove inline code spans and quoted lines before consensus scanning."""
    # Backtick-quoted spans
    text = re.sub(r"`[^`]*`", " ", text)
    # Lines starting with '>' (optionally preceded by whitespace)
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
    if len(message) < 10:
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
    if len(message) < 20:
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