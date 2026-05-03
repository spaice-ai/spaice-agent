"""Generic recall engine — proper-noun aware vault scanner.

Stdlib-only BM25-like scoring over the vault. Triggers come from
`~/.spaice-agents/<agent_id>/triggers.yaml` (per-agent, starts empty)
and from inline regex patterns in the user message.

v0.2.0 ships BM25 only. Extension points for vector retrieval + RRF
merge + reranker are stubbed so v0.2.1 can drop them in without API
change. See `~/jarvis/spaice/agent/decisions/2026-05-03-qmd-absorbed-not-bundled.md`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

from spaice_agent.memory.paths import (
    VaultPaths,
    CANONICAL_SHELVES,
)


# Default skip dirs — nothing under these paths is scanned.
# Conservative defaults; users can override via triggers.yaml.
DEFAULT_SKIP_DIRS: frozenset[str] = frozenset({
    ".git", ".obsidian", ".vscode", ".idea",
    "_archive", "_inbox", "_continuity", "_dashboard", "_templates",
    "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache",
    ".pytest_cache", "scripts",  # helper scripts don't contain memory
})

# Default regex patterns for technical IDs. Users can add more in triggers.yaml.
DEFAULT_ID_PATTERNS: tuple[str, ...] = (
    r"\b[A-Z]{2,}[-/]?\d{2,}[A-Z0-9-]*\b",   # SKU-ish
    r"\b[A-Z]{2,}\d{2,}[A-Z]*\b",             # Alt SKU
    r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",  # IPv4
    r"\b\d{1,2}\.\d{1,2}\.\d{1,3}\b",         # version/address tuple (e.g. 1.2.34)
    r"https?://[^\s\]\)]+",                    # URL
)

# Extensions we consider "memory" content.
SCAN_EXTENSIONS: frozenset[str] = frozenset({".md", ".yaml", ".yml", ".txt"})

# Stopwords — never trigger recall even if capitalised.
STOPWORDS: frozenset[str] = frozenset({
    "the", "a", "an", "and", "or", "but", "if", "for", "with", "on",
    "in", "at", "to", "from", "by", "is", "are", "was", "were",
    "do", "does", "did", "we", "us", "our", "i", "you", "he", "she",
    "it", "they", "them", "this", "that", "those", "these", "what",
    "when", "where", "why", "how", "which", "who", "whom",
})

# Max preview length in chars for a RecallHit.
MAX_PREVIEW_CHARS = 200


class InvalidTriggersConfigError(ValueError):
    """Raised when triggers.yaml is present but malformed."""


@dataclass(frozen=True)
class RecallHit:
    """A single file match from a recall scan."""

    shelf_priority: int  # lower = more authoritative shelf
    score: int           # higher = better match
    rel_path: str        # relative to vault_root
    preview: str         # first matching line, ≤MAX_PREVIEW_CHARS


@dataclass(frozen=True)
class TriggerConfig:
    """Loaded triggers.yaml — curated proper nouns + regex patterns."""

    client_names: tuple[str, ...] = ()       # priority 0 (highest)
    project_names: tuple[str, ...] = ()      # priority 1
    brand_names: tuple[str, ...] = ()        # priority 2
    external_services: tuple[str, ...] = ()  # priority 3
    id_patterns: tuple[re.Pattern[str], ...] = field(default_factory=tuple)
    skip_dirs: frozenset[str] = field(default_factory=frozenset)


def _load_triggers(path: Path) -> TriggerConfig:
    """Read triggers.yaml, returning an EMPTY config if the file is absent."""
    if not path.exists():
        return TriggerConfig(
            id_patterns=tuple(re.compile(p) for p in DEFAULT_ID_PATTERNS),
            skip_dirs=DEFAULT_SKIP_DIRS,
        )
    if yaml is None:  # pragma: no cover
        raise InvalidTriggersConfigError("PyYAML not installed")
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise InvalidTriggersConfigError(f"malformed triggers.yaml: {exc}") from exc
    if not isinstance(raw, dict):
        raise InvalidTriggersConfigError(
            f"triggers.yaml must be a mapping at top level, got {type(raw).__name__}"
        )

    def _strlist(key: str) -> tuple[str, ...]:
        val = raw.get(key, []) or []
        if not isinstance(val, list):
            raise InvalidTriggersConfigError(f"{key} must be a list")
        return tuple(str(v).lower() for v in val if v)

    # id_patterns: list of {pattern, description} OR list of plain strings
    id_patterns_raw = raw.get("id_patterns") or []
    compiled_patterns: list[re.Pattern[str]] = [
        re.compile(p) for p in DEFAULT_ID_PATTERNS
    ]
    if isinstance(id_patterns_raw, list):
        for item in id_patterns_raw:
            pat: Optional[str] = None
            if isinstance(item, dict):
                pat = item.get("pattern")
            elif isinstance(item, str):
                pat = item
            if pat:
                try:
                    compiled_patterns.append(re.compile(pat))
                except re.error as exc:
                    raise InvalidTriggersConfigError(
                        f"invalid regex in id_patterns: {pat!r} ({exc})"
                    ) from exc

    skip_dirs_raw = raw.get("skip_dirs") or []
    if not isinstance(skip_dirs_raw, list):
        raise InvalidTriggersConfigError("skip_dirs must be a list")
    skip_dirs = DEFAULT_SKIP_DIRS | frozenset(str(d) for d in skip_dirs_raw if d)

    return TriggerConfig(
        client_names=_strlist("client_names"),
        project_names=_strlist("project_names"),
        brand_names=_strlist("brand_names"),
        external_services=_strlist("external_services"),
        id_patterns=tuple(compiled_patterns),
        skip_dirs=skip_dirs,
    )


@dataclass
class Recaller:
    """Vault-scanning recall engine.

    Typical usage:
        r = Recaller.for_agent("jarvis")
        triggers = r.extract_triggers(user_message)
        hits = r.scan(triggers, max_hits=10)
        md = r.format_output(triggers, hits)
    """

    paths: VaultPaths
    triggers_config: TriggerConfig

    # -- constructors ------------------------------------------------------

    @classmethod
    def for_agent(cls, agent_id: str) -> "Recaller":
        paths = VaultPaths.for_agent(agent_id)
        return cls(paths=paths, triggers_config=_load_triggers(paths.triggers_yaml))

    @classmethod
    def for_vault(cls, vault_root: Path, triggers_yaml: Optional[Path] = None) -> "Recaller":
        """Test/tooling constructor — bypass agent config."""
        paths = VaultPaths.for_vault(vault_root)
        triggers_path = triggers_yaml or paths.triggers_yaml
        return cls(paths=paths, triggers_config=_load_triggers(triggers_path))

    # -- trigger extraction ------------------------------------------------

    def extract_triggers(self, text: str) -> list[str]:
        """Pull candidate triggers from the user message.

        Returns a deduped list ordered by priority (strongest signal first).
        """
        low = text.lower()
        scored: list[tuple[int, str]] = []

        tc = self.triggers_config
        for name in tc.client_names:
            if re.search(rf"\b{re.escape(name)}\b", low):
                scored.append((0, name))
        for name in tc.project_names:
            if re.search(rf"\b{re.escape(name)}\b", low):
                scored.append((1, name))
        for name in tc.brand_names:
            if re.search(rf"\b{re.escape(name)}\b", low):
                scored.append((2, name))
        for name in tc.external_services:
            if re.search(rf"\b{re.escape(name)}\b", low):
                scored.append((3, name))

        # Technical IDs via regex
        for pat in tc.id_patterns:
            for m in pat.finditer(text):
                tok = m.group(0).strip()
                if len(tok) >= 4 and not tok.isdigit():
                    scored.append((4, tok.lower()))

        # Capitalised fallback (noisy, lowest priority)
        cap_tokens = re.findall(r"\b[A-Z][a-zA-Z]{2,}\b", text)
        for tok in cap_tokens:
            tl = tok.lower()
            if tl in STOPWORDS:
                continue
            if any(tl == t[1] for t in scored):
                continue
            scored.append((5, tl))

        # Dedupe preserving priority order
        seen: set[str] = set()
        out: list[str] = []
        for _, term in sorted(scored, key=lambda x: x[0]):
            if term in seen:
                continue
            seen.add(term)
            out.append(term)
        return out

    # -- file iteration ----------------------------------------------------

    def _iter_files(self):
        """Yield (shelf_priority, rel_path, full_path) across scannable vault files.

        Top-level .md files get shelf_priority=99 (least authoritative).
        """
        root = self.paths.vault_root
        skip = self.triggers_config.skip_dirs

        # Top-level files
        for p in root.glob("*.md"):
            yield 99, p.name, p

        # Shelves in canonical order
        for idx, shelf in enumerate(CANONICAL_SHELVES):
            base = root / shelf
            if not base.exists():
                continue
            for p in base.rglob("*"):
                if p.is_dir():
                    continue
                if any(part in skip for part in p.parts):
                    continue
                if p.suffix not in SCAN_EXTENSIONS:
                    continue
                rel = p.relative_to(root)
                yield idx, str(rel), p

    # -- scoring -----------------------------------------------------------

    def _score_file(
        self, path: Path, triggers: list[str],
    ) -> tuple[int, str]:
        """BM25-like score for one file. Returns (score, preview)."""
        try:
            text = path.read_text(errors="replace")
        except OSError:
            return 0, ""

        low = text.lower()
        total = 0
        first_hit_line = ""
        for i, term in enumerate(triggers):
            weight = max(1, 10 - i)
            # Word-boundary match. For hyphenated terms like "fsh-123", `\b`
            # doesn't fire on the hyphen, so we use lookaround assertions
            # against non-alphanumeric-or-hyphen chars to get the right
            # semantics ("fsh-123" matches as a unit, not as "fsh" + "123").
            if re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$", term):
                pat = rf"(?<![a-z0-9-]){re.escape(term)}(?![a-z0-9-])"
            else:
                pat = re.escape(term)
            matches = list(re.finditer(pat, low))
            if matches:
                total += len(matches) * weight
                if not first_hit_line:
                    start = matches[0].start()
                    line_start = low.rfind("\n", 0, start) + 1
                    line_end = low.find("\n", start)
                    if line_end == -1:
                        line_end = len(low)
                    first_hit_line = text[line_start:line_end].strip()

        return total, first_hit_line[:MAX_PREVIEW_CHARS]

    def _preview_fallback(self, path: Path) -> str:
        """First non-empty non-frontmatter line — used when score found no line.

        Tracks YAML frontmatter state properly: only the FIRST `---` opens the
        frontmatter block, and the SECOND `---` closes it. Lines after the
        close are scanned normally (so `---` used as a horizontal rule
        mid-document is treated as content, not re-entering frontmatter).
        """
        try:
            in_frontmatter = False
            frontmatter_closed = False
            for i, line in enumerate(path.read_text(errors="replace").splitlines()):
                stripped = line.strip()
                if i == 0 and stripped == "---":
                    in_frontmatter = True
                    continue
                if in_frontmatter and not frontmatter_closed:
                    if stripped == "---":
                        in_frontmatter = False
                        frontmatter_closed = True
                    continue
                if not stripped or stripped.startswith("#!"):
                    continue
                return stripped[:MAX_PREVIEW_CHARS]
        except OSError:
            pass
        return ""

    # -- extension hooks (v0.2.1 will implement) --------------------------

    def _merge_results(
        self,
        bm25_hits: list[RecallHit],
        vector_hits: list[RecallHit],  # noqa: ARG002 — reserved for v0.2.1
    ) -> list[RecallHit]:
        """RRF merge BM25 + vector hits. v0.2.0 returns BM25 unchanged."""
        return bm25_hits

    def _rerank(
        self,
        hits: list[RecallHit],
        query: str,  # noqa: ARG002 — reserved for v0.2.1
    ) -> list[RecallHit]:
        """Cross-encoder rerank. v0.2.0 returns hits unchanged."""
        return hits

    # -- public scan -------------------------------------------------------

    def scan(self, triggers: list[str], max_hits: int = 10) -> list[RecallHit]:
        """Scan the vault. Returns ranked list of RecallHit (score DESC, shelf ASC)."""
        if not triggers:
            return []

        results: list[RecallHit] = []
        for shelf_prio, rel, full in self._iter_files():
            score, preview = self._score_file(full, triggers)
            if score > 0:
                if not preview:
                    preview = self._preview_fallback(full)
                results.append(RecallHit(
                    shelf_priority=shelf_prio,
                    score=score,
                    rel_path=rel,
                    preview=preview,
                ))

        # Extension points — v0.2.0 no-ops
        results = self._merge_results(results, [])
        results = self._rerank(results, " ".join(triggers))

        # Score DESC, shelf priority ASC (earlier shelf = more authoritative)
        results.sort(key=lambda h: (-h.score, h.shelf_priority))
        return results[:max_hits]

    # -- formatting --------------------------------------------------------

    @staticmethod
    def format_output(triggers: list[str], hits: list[RecallHit]) -> str:
        """Render a markdown summary for injection into LLM context."""
        if not triggers:
            return (
                "# recall-scan: no triggers detected\n\n"
                "_No proper nouns, IDs, or known terms matched. Answer from "
                "general knowledge or ask for clarification._\n"
            )
        header = f"# recall-scan: triggers=[{', '.join(triggers[:8])}]"
        if not hits:
            return (
                f"{header}\n\n"
                f"**No hits in vault.** These triggers aren't documented yet.\n"
                f"- If the user is stating a new fact, file it via capture_fact() "
                f"same turn.\n"
                f"- If the user is asking, admit you don't have it rather than "
                f"confabulating.\n"
            )
        lines = [
            header, "",
            f"**{len(hits)} relevant file(s) in vault:**", "",
        ]
        for h in hits:
            lines.append(f"- `{h.rel_path}` (score {h.score}) — {h.preview}")
        lines.append("")
        lines.append(
            "_Read the top 2-3 files before replying. Cite concrete facts; "
            "admit gaps._"
        )
        return "\n".join(lines) + "\n"
