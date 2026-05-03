"""Background session miner — scan Hermes session JSONLs for durable facts.

Extracts user-stated facts that look fileable, classifies them, writes
classification drafts to `_inbox/`. Intended for hourly cron execution.

Idempotent: tracks processed-session byte sizes in `_inbox/.state.json`.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

from spaice_agent.memory.capture import InboxEntry, capture_fact
from spaice_agent.memory.classify import (
    Classification,
    Classifier,
    ClassifierError,
)
from spaice_agent.memory.paths import VaultPaths


log = logging.getLogger(__name__)


# Defaults
DEFAULT_SESSION_SOURCE = "~/.hermes/sessions"
DEFAULT_SESSION_PREFIX = "session_"
DEFAULT_SKIP_PREFIXES = ("session_cron_",)
DEFAULT_MAX_UTTERANCE_CHARS = 4000
DEFAULT_MAX_UTTERANCES_PER_RUN = 50
DEFAULT_MIN_UTTERANCE_CHARS = 20
STATE_FILENAME = ".state.json"


# Durable-fact heuristics. Generic defaults; user extends via
# memory.mine.fact_patterns in config.yaml.
DEFAULT_FACT_PATTERNS = (
    # Vendor + SKU (alphanumeric with hyphen, or model-number pattern)
    re.compile(
        r"\b[A-Z][a-zA-Z]+(?:\s+[A-Za-z-]+)?\s+"
        r"(?:[A-Z0-9]{2,}[-/][A-Z0-9-]+|[A-Z]+\d{2,}[A-Z0-9]*)\b"
    ),
    # Product URLs
    re.compile(r"https?://[^\s]+/(?:products|product|shop|store|spec)/[^\s]+"),
    # Currency
    re.compile(r"\$\s?\d{1,3}(?:,?\d{3})+(?:\.\d{2})?"),
    # Address-ish
    re.compile(
        r"\b\d{1,4}\s+[A-Z][a-zA-Z]+\s+"
        r"(?:Street|St|Road|Rd|Avenue|Ave|Drive|Dr|Lane|Ln|Place|Pl)\b"
    ),
    # Correction verbs
    re.compile(
        r"\b(?:you forgot|you missed|you're wrong|don't|stop doing|remember|again|"
        r"you've got this wrong)\b",
        re.IGNORECASE,
    ),
    # Decision verbs
    re.compile(
        r"\b(?:confirmed|locked|all yes|approved|final|go ahead|that's right|"
        r"do it|let's go with)\b",
        re.IGNORECASE,
    ),
    # Infrastructure markers
    re.compile(r"\b(?:ssh|tailscale|VPN)\s+\S+"),
    re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),  # IPv4
)


# Content blocks to skip entirely — Hermes system-injected prompts,
# compaction boundaries, tool-iteration notices.
DEFAULT_SKIP_PATTERNS = (
    re.compile(r"\[CONTEXT COMPACTION", re.IGNORECASE),
    re.compile(r"<<<BEGIN_\w+_INTERNAL_CONTEXT"),
    re.compile(r"\[IMPORTANT:\s*You are running as a scheduled cron", re.IGNORECASE),
    re.compile(r"\[SYSTEM NOTE:\s*Your previous turn was int", re.IGNORECASE),
    re.compile(r"^Review the conversation above", re.IGNORECASE | re.MULTILINE),
    re.compile(r"reached the maximum number of tool-calling iterations", re.IGNORECASE),
)


@dataclass(frozen=True)
class MineReport:
    """Summary of a single miner run."""

    sessions_scanned: int
    candidates_found: int
    facts_filed: int
    low_confidence_count: int
    cutoff: datetime
    errors: tuple[str, ...]
    dry_run: bool


@dataclass(frozen=True)
class _Utterance:
    """Internal: one candidate user utterance, with location metadata."""

    session: str
    turn_index: int
    content: str


@dataclass(frozen=True)
class MineConfig:
    session_source: Path
    session_prefix: str = DEFAULT_SESSION_PREFIX
    skip_session_prefixes: tuple[str, ...] = DEFAULT_SKIP_PREFIXES
    max_utterance_chars: int = DEFAULT_MAX_UTTERANCE_CHARS
    max_utterances_per_run: int = DEFAULT_MAX_UTTERANCES_PER_RUN
    min_utterance_chars: int = DEFAULT_MIN_UTTERANCE_CHARS
    low_confidence_threshold: float = 0.5
    # User-extended fact patterns (regex strings from config.yaml). Loaded
    # into Miner.fact_patterns via from_config_dict.
    fact_patterns_raw: tuple[str, ...] = ()

    @classmethod
    def from_config_dict(cls, config: dict) -> "MineConfig":
        mem = (config or {}).get("memory") or {}
        mn = mem.get("mine") or {}
        source = mn.get("session_source", DEFAULT_SESSION_SOURCE)
        source_path = Path(source).expanduser()
        skip_pref_raw = mn.get("skip_session_prefixes")
        if skip_pref_raw is None:
            skip_pref = DEFAULT_SKIP_PREFIXES
        else:
            if not isinstance(skip_pref_raw, list):
                raise ValueError(
                    "memory.mine.skip_session_prefixes must be a list"
                )
            skip_pref = tuple(str(s) for s in skip_pref_raw if s)

        # Custom fact_patterns — validated at parse time so misconfigs
        # surface immediately rather than on first cron fire.
        fact_patterns_raw = mn.get("fact_patterns") or []
        if not isinstance(fact_patterns_raw, list):
            raise ValueError("memory.mine.fact_patterns must be a list")
        validated: list[str] = []
        for pat in fact_patterns_raw:
            pat_str = str(pat)
            try:
                re.compile(pat_str)
            except re.error as exc:
                raise ValueError(
                    f"invalid regex in memory.mine.fact_patterns: {pat_str!r} ({exc})"
                ) from exc
            validated.append(pat_str)

        return cls(
            session_source=source_path,
            session_prefix=str(mn.get("session_prefix", DEFAULT_SESSION_PREFIX)),
            skip_session_prefixes=skip_pref,
            max_utterance_chars=int(mn.get("max_utterance_chars", DEFAULT_MAX_UTTERANCE_CHARS)),
            max_utterances_per_run=int(mn.get("max_utterances_per_run", DEFAULT_MAX_UTTERANCES_PER_RUN)),
            min_utterance_chars=int(mn.get("min_utterance_chars", DEFAULT_MIN_UTTERANCE_CHARS)),
            low_confidence_threshold=float(mn.get("low_confidence_threshold", 0.5)),
            fact_patterns_raw=tuple(validated),
        )


@dataclass
class Miner:
    """Session miner — scans recent Hermes transcripts for fileable facts."""

    paths: VaultPaths
    config: MineConfig
    agent_id: str
    classifier: Optional[Classifier] = None
    fact_patterns: tuple[re.Pattern, ...] = field(default_factory=lambda: DEFAULT_FACT_PATTERNS)
    skip_patterns: tuple[re.Pattern, ...] = field(default_factory=lambda: DEFAULT_SKIP_PATTERNS)

    @classmethod
    def for_agent(cls, agent_id: str) -> "Miner":
        paths = VaultPaths.for_agent(agent_id)
        agent_cfg = _load_agent_config(paths)
        cfg = MineConfig.from_config_dict(agent_cfg)
        # Merge DEFAULT patterns with user-supplied ones (user patterns appended)
        user_patterns = tuple(re.compile(p) for p in cfg.fact_patterns_raw)
        all_patterns = DEFAULT_FACT_PATTERNS + user_patterns
        return cls(
            paths=paths,
            config=cfg,
            agent_id=agent_id,
            fact_patterns=all_patterns,
        )

    # -- public API --------------------------------------------------------

    def run(
        self,
        *,
        since: timedelta = timedelta(minutes=90),
        max_utterances: Optional[int] = None,
        dry_run: bool = False,
    ) -> MineReport:
        """Run one mining pass. Returns MineReport."""
        if max_utterances is None:
            max_utterances = self.config.max_utterances_per_run

        cutoff = datetime.now() - since
        errors: list[str] = []

        # Load state (tracks byte sizes of processed sessions)
        state = self._load_state()

        sessions_to_mine = list(self._sessions_since(cutoff))

        candidates: list[_Utterance] = []
        for session_path in sessions_to_mine:
            prev_size = state["mined_sessions"].get(session_path.name, 0)
            try:
                size_now = session_path.stat().st_size
            except OSError as exc:
                errors.append(f"stat {session_path.name}: {exc}")
                continue
            if size_now <= prev_size:
                continue

            try:
                for utt in self._extract_user_utterances(session_path):
                    if self._has_fileable_signal(utt.content):
                        candidates.append(utt)
            except Exception as exc:  # noqa: BLE001 — one bad session shouldn't kill the run
                errors.append(f"extract {session_path.name}: {exc}")
                continue

        # Cap utterances (cost control)
        candidates = candidates[:max_utterances]

        filed = 0
        low_conf = 0
        if not dry_run:
            classifier = self._ensure_classifier()
            for utt in candidates:
                try:
                    classification = classifier.classify(utt.content)
                except ClassifierError as exc:
                    errors.append(f"classify {utt.session} t{utt.turn_index}: {exc}")
                    continue
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"classify unexpected {utt.session} t{utt.turn_index}: {exc}")
                    continue

                if classification.confidence < self.config.low_confidence_threshold:
                    low_conf += 1

                try:
                    self._write_inbox_draft(utt, classification)
                    filed += 1
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"write {utt.session} t{utt.turn_index}: {exc}")

        # Update state
        if not dry_run:
            for session_path in sessions_to_mine:
                try:
                    state["mined_sessions"][session_path.name] = session_path.stat().st_size
                except OSError:
                    pass
            state["last_run"] = datetime.now().astimezone().isoformat(timespec="seconds")
            state["last_run_filed"] = filed
            state["last_run_low_conf"] = low_conf
            self._save_state(state)

        return MineReport(
            sessions_scanned=len(sessions_to_mine),
            candidates_found=len(candidates),
            facts_filed=filed,
            low_confidence_count=low_conf,
            cutoff=cutoff,
            errors=tuple(errors),
            dry_run=dry_run,
        )

    # -- internals ---------------------------------------------------------

    def _ensure_classifier(self) -> Classifier:
        if self.classifier is None:
            self.classifier = Classifier.for_agent(self.agent_id)
        return self.classifier

    def _sessions_since(self, cutoff: datetime) -> Iterator[Path]:
        """Yield session files modified since cutoff. Skips cron-sourced sessions."""
        src = self.config.session_source
        if not src.exists():
            return
        for p in src.glob(f"{self.config.session_prefix}*.json"):
            if any(p.name.startswith(pref) for pref in self.config.skip_session_prefixes):
                continue
            try:
                mtime = datetime.fromtimestamp(p.stat().st_mtime)
            except OSError:
                continue
            if mtime >= cutoff:
                yield p

    def _extract_user_utterances(self, session_path: Path) -> list[_Utterance]:
        """Extract user messages from a Hermes session JSON."""
        try:
            data = json.loads(session_path.read_text())
        except (json.JSONDecodeError, OSError):
            return []

        messages = data if isinstance(data, list) else data.get("messages", [])
        out: list[_Utterance] = []
        for i, m in enumerate(messages):
            if not isinstance(m, dict) or m.get("role") != "user":
                continue
            content = m.get("content", "")
            # Multimodal messages ship as a list of {"type": "text", "text": ...}
            # / {"type": "image", ...} parts. Extract only text parts for fact
            # mining — JSON-dumping the whole list produced garbage that broke
            # regex matching (B1-mine-7).
            if isinstance(content, list):
                text_parts: list[str] = []
                for part in content:
                    if isinstance(part, dict):
                        if part.get("type") == "text" and part.get("text"):
                            text_parts.append(str(part["text"]))
                        elif "text" in part:
                            text_parts.append(str(part["text"]))
                    elif isinstance(part, str):
                        text_parts.append(part)
                content = "\n".join(text_parts)
            content = str(content)
            stripped = content.strip()
            if not stripped or len(stripped) < self.config.min_utterance_chars:
                continue
            if any(p.search(content) for p in self.skip_patterns):
                continue
            out.append(_Utterance(
                session=session_path.name,
                turn_index=i,
                content=content[:self.config.max_utterance_chars],
            ))
        return out

    def _has_fileable_signal(self, text: str) -> bool:
        return any(p.search(text) for p in self.fact_patterns)

    def _write_inbox_draft(
        self, utterance: _Utterance, classification: Classification,
    ) -> Path:
        """Persist a classified utterance via capture_fact() with miner metadata."""
        extra = {
            "mined_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source_session": utterance.session,
            "source_turn": utterance.turn_index,
            "classifier_target": classification.target_file,
            "classifier_section": classification.section or "",
            "classifier_dewey_layer": classification.dewey_layer,
            "classifier_priority": classification.priority,
            "classifier_confidence": classification.confidence,
            "classifier_rule": classification.rule_matched,
            "classifier_model": classification.model_used,
            "classifier_used_fallback": classification.used_fallback,
        }
        entry = InboxEntry(
            text=utterance.content,
            source="cron:mine",
            category=None,
            tags=(),
        )
        return capture_fact(entry, agent_id=self.agent_id, extra_frontmatter=extra)

    # -- state file --------------------------------------------------------

    def _state_path(self) -> Path:
        return self.paths.inbox / STATE_FILENAME

    def _load_state(self) -> dict:
        path = self._state_path()
        if not path.exists():
            return {"mined_sessions": {}, "last_run": None}
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            # Corrupt state — try to back up the file, but whatever happens,
            # return a fresh state so the run can proceed. If backup fails,
            # log so debugging info is surfaced (B1-mine-4).
            try:
                backup = path.with_suffix(".json.corrupt")
                path.rename(backup)
                log.warning("corrupt state file backed up to %s", backup)
            except OSError as exc:
                log.warning(
                    "corrupt state file at %s could not be backed up: %s "
                    "(starting with fresh state)", path, exc,
                )
            return {"mined_sessions": {}, "last_run": None}
        if not isinstance(data, dict) or "mined_sessions" not in data:
            return {"mined_sessions": {}, "last_run": None}
        return data

    def _save_state(self, state: dict) -> None:
        self.paths.inbox.mkdir(parents=True, exist_ok=True)
        path = self._state_path()
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(path)


def _load_agent_config(paths: VaultPaths) -> dict:
    """Shared config loader — returns empty dict on any read failure."""
    config_path = paths.agent_config_dir / "config.yaml"
    if not config_path.exists() or yaml is None:
        return {}
    try:
        data = yaml.safe_load(config_path.read_text()) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}
