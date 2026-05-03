"""Daily inbox triage — file miner verdicts to canonical shelves.

Reads `_inbox/*.md` (miner/classifier output), auto-files high-confidence
verdicts into canonical vault files, escalates the rest to LOG.md.

Atomic writes. Protected shelves are never touched. Dry-run via flag file.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

from spaice_agent.memory.paths import VaultPaths


log = logging.getLogger(__name__)


# Defaults (per Phase 1B framework; all overridable via config.yaml).
DEFAULT_HIGH_CONFIDENCE = 0.85
DEFAULT_MID_CONFIDENCE = 0.60
DEFAULT_MIN_AGE_HOURS = 4
DEFAULT_LOG_FILENAME = "LOG.md"
DEFAULT_PROTECTED_SHELVES = frozenset({"doctrines", "_continuity", "_archive"})
DRY_RUN_FLAG_NAME = ".inbox-triage-dry-run"
LOW_CONF_SUBDIR = "_low-confidence"


# Valid action labels
ACTION_AUTO_FILE = "auto_file"
ACTION_ESCALATE = "escalate"
ACTION_LOW_CONF = "low_conf"
ACTION_PROTECTED = "protected"
ACTION_SKIP = "skip"
ACTION_MALFORMED = "malformed"


@dataclass(frozen=True)
class TriageResult:
    """A single inbox file's triage verdict."""

    inbox_file: str
    action: str
    reason: str
    target_file: Optional[str] = None
    section: Optional[str] = None


@dataclass(frozen=True)
class TriageReport:
    """Full report from a triage run."""

    filed: tuple[TriageResult, ...]
    escalated: tuple[TriageResult, ...]
    demoted: tuple[TriageResult, ...]
    skipped_count: int
    dry_run: bool


@dataclass(frozen=True)
class TriageConfig:
    """Triage thresholds + safety settings."""

    high_confidence: float = DEFAULT_HIGH_CONFIDENCE
    mid_confidence: float = DEFAULT_MID_CONFIDENCE
    min_age_hours: float = DEFAULT_MIN_AGE_HOURS
    log_md: str = DEFAULT_LOG_FILENAME
    protected_shelves: frozenset[str] = field(default_factory=lambda: DEFAULT_PROTECTED_SHELVES)

    @classmethod
    def from_config_dict(cls, config: dict) -> "TriageConfig":
        mem = (config or {}).get("memory") or {}
        tr = mem.get("triage") or {}
        protected = tr.get("protected_shelves")
        if protected is None:
            protected_set = DEFAULT_PROTECTED_SHELVES
        else:
            if not isinstance(protected, list):
                raise ValueError("memory.triage.protected_shelves must be a list")
            protected_set = frozenset(str(s) for s in protected if s)
        return cls(
            high_confidence=float(tr.get("high_confidence", DEFAULT_HIGH_CONFIDENCE)),
            mid_confidence=float(tr.get("mid_confidence", DEFAULT_MID_CONFIDENCE)),
            min_age_hours=float(tr.get("min_age_hours", DEFAULT_MIN_AGE_HOURS)),
            log_md=str(tr.get("log_md", DEFAULT_LOG_FILENAME)),
            protected_shelves=protected_set,
        )


@dataclass
class Triager:
    """Daily triage engine for a single agent's inbox."""

    paths: VaultPaths
    config: TriageConfig

    @classmethod
    def for_agent(cls, agent_id: str) -> "Triager":
        paths = VaultPaths.for_agent(agent_id)
        agent_cfg = _load_agent_config(paths)
        return cls(paths=paths, config=TriageConfig.from_config_dict(agent_cfg))

    # -- public API --------------------------------------------------------

    def run(self, *, dry_run: Optional[bool] = None) -> TriageReport:
        """Execute a single triage pass. Returns TriageReport (never raises for
        per-file errors; they surface as 'escalate' results).

        Args:
            dry_run: Force dry-run mode. If None, checks for flag file in
                     vault root. Useful for tests.
        """
        if dry_run is None:
            dry_run = (self.paths.vault_root / DRY_RUN_FLAG_NAME).exists()

        filed: list[TriageResult] = []
        escalated: list[TriageResult] = []
        demoted: list[TriageResult] = []
        skipped = 0

        inbox = self.paths.inbox
        if not inbox.exists():
            return TriageReport(
                filed=(), escalated=(), demoted=(), skipped_count=0, dry_run=dry_run,
            )

        for path in sorted(inbox.glob("*.md")):
            if path.is_dir():
                continue
            result = self._classify_one(path)

            if result.action in (ACTION_SKIP, ACTION_MALFORMED):
                skipped += 1
                continue

            if result.action == ACTION_PROTECTED:
                escalated.append(result)
                continue

            if result.action == ACTION_ESCALATE:
                escalated.append(result)
                continue

            if result.action == ACTION_LOW_CONF:
                if not dry_run:
                    low_conf_dir = inbox / LOW_CONF_SUBDIR
                    low_conf_dir.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.move(str(path), str(low_conf_dir / path.name))
                    except OSError as exc:
                        escalated.append(TriageResult(
                            inbox_file=path.name,
                            action=ACTION_ESCALATE,
                            reason=f"demote failed: {exc}",
                        ))
                        continue
                demoted.append(result)
                continue

            if result.action == ACTION_AUTO_FILE:
                if result.target_file is None:
                    # Shouldn't happen; defensive
                    escalated.append(TriageResult(
                        inbox_file=path.name,
                        action=ACTION_ESCALATE,
                        reason="auto_file with no target_file (internal inconsistency)",
                    ))
                    continue

                target_rel = result.target_file

                # Path-traversal guard: resolve target and verify it stays
                # within the vault. Without this, a malicious inbox file
                # with `classifier_target: "../../../etc/passwd"` could
                # write outside the vault. (Codex B1-triage-5.)
                try:
                    target_path = (self.paths.vault_root / target_rel).resolve()
                    vault_resolved = self.paths.vault_root.resolve()
                    if not _is_subpath(target_path, vault_resolved):
                        escalated.append(TriageResult(
                            inbox_file=path.name,
                            action=ACTION_ESCALATE,
                            reason=f"target {target_rel!r} escapes vault (path traversal blocked)",
                            target_file=target_rel,
                        ))
                        continue
                except (OSError, ValueError) as exc:
                    escalated.append(TriageResult(
                        inbox_file=path.name,
                        action=ACTION_ESCALATE,
                        reason=f"target {target_rel!r} could not be resolved: {exc}",
                        target_file=target_rel,
                    ))
                    continue

                if not target_path.exists():
                    escalated.append(TriageResult(
                        inbox_file=path.name,
                        action=ACTION_ESCALATE,
                        reason=f"target {target_rel!r} does not exist",
                        target_file=target_rel,
                    ))
                    continue

                if not dry_run:
                    try:
                        body = _read_body(path)
                        _append_to_target(
                            target_path, result.section, body, path.name,
                        )
                        path.unlink()
                    except OSError as exc:
                        escalated.append(TriageResult(
                            inbox_file=path.name,
                            action=ACTION_ESCALATE,
                            reason=f"file error: {exc}",
                            target_file=target_rel,
                        ))
                        continue
                filed.append(result)

        # Write LOG.md summaries in live mode ONLY (dry-run must be side-effect-free)
        if not dry_run and (filed or escalated or demoted):
            try:
                self._append_log_summaries(filed, escalated, demoted)
            except OSError as exc:
                # Best-effort log write — don't fail the run
                log.warning("LOG.md write failed: %s", exc)

        return TriageReport(
            filed=tuple(filed),
            escalated=tuple(escalated),
            demoted=tuple(demoted),
            skipped_count=skipped,
            dry_run=dry_run,
        )

    # -- per-file classification ------------------------------------------

    def _classify_one(self, path: Path) -> TriageResult:
        """Decide what to do with one inbox file."""
        name = path.name
        if name == "README.md":
            return TriageResult(name, ACTION_SKIP, "readme")

        try:
            age = _age_hours(path)
        except OSError as exc:
            return TriageResult(name, ACTION_MALFORMED, f"stat failed: {exc}")

        if age < self.config.min_age_hours:
            return TriageResult(
                name, ACTION_SKIP,
                f"too fresh ({age:.1f}h < {self.config.min_age_hours}h)",
            )

        meta = _parse_frontmatter(path)
        if meta is None:
            return TriageResult(name, ACTION_MALFORMED, "no parseable frontmatter")

        # Honour test artefacts and explicit test markers
        if meta.get("classifier_status") == "test_artefact":
            return TriageResult(name, ACTION_SKIP, "test_artefact")

        target = meta.get("classifier_target")
        conf = meta.get("classifier_confidence", 0.0)
        section = meta.get("classifier_section")

        if not target:
            return TriageResult(
                name, ACTION_ESCALATE, "no classifier_target",
                target_file=None, section=None,
            )

        target_str = str(target)
        if self._is_protected(target_str):
            return TriageResult(
                name, ACTION_PROTECTED,
                f"target under protected shelf: {target_str}",
                target_file=target_str, section=section,
            )

        try:
            conf_f = float(conf)
        except (TypeError, ValueError):
            return TriageResult(
                name, ACTION_ESCALATE,
                f"unparseable confidence: {conf!r}",
                target_file=target_str, section=section,
            )

        if conf_f >= self.config.high_confidence:
            return TriageResult(
                name, ACTION_AUTO_FILE,
                f"conf={conf_f:.2f} target={target_str}",
                target_file=target_str,
                section=str(section) if section else None,
            )

        if conf_f >= self.config.mid_confidence:
            return TriageResult(
                name, ACTION_ESCALATE,
                f"conf={conf_f:.2f} target={target_str}",
                target_file=target_str,
                section=str(section) if section else None,
            )

        return TriageResult(
            name, ACTION_LOW_CONF,
            f"conf={conf_f:.2f}",
            target_file=target_str, section=section,
        )

    def _is_protected(self, target: str) -> bool:
        for shelf in self.config.protected_shelves:
            if target.startswith(shelf):
                return True
        return False

    def _append_log_summaries(
        self,
        filed: list[TriageResult],
        escalated: list[TriageResult],
        demoted: list[TriageResult],
    ) -> None:
        """Write filed/escalated/demoted summaries to LOG.md."""
        log_path = self.paths.vault_root / self.config.log_md
        now = datetime.now().astimezone()
        ts = now.strftime("%Y-%m-%d %H:%M %Z").strip()

        if filed:
            lines = [
                f"`{r.inbox_file}` → `{r.target_file}`"
                + (f" § {r.section}" if r.section else "")
                for r in filed
            ]
            _append_log(log_path, "Filing pass", lines, ts)
        if escalated:
            lines = [f"`{r.inbox_file}` — {r.reason}" for r in escalated]
            _append_log(log_path, "Inbox pending review", lines, ts)
        if demoted:
            lines = [f"`{r.inbox_file}` — {r.reason}" for r in demoted]
            _append_log(log_path, "Inbox demoted (low-confidence)", lines, ts)


# -- frontmatter helpers ----------------------------------------------------


def _is_subpath(child: Path, parent: Path) -> bool:
    """True iff `child` is inside (or equal to) `parent`. Both must be resolved."""
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _parse_frontmatter(path: Path) -> Optional[dict]:
    """Read YAML frontmatter; return None if malformed/absent."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end < 0:
        return None
    if yaml is None:  # pragma: no cover
        return None
    try:
        meta = yaml.safe_load(text[4:end])
    except yaml.YAMLError:
        return None
    return meta if isinstance(meta, dict) else None


def _read_body(path: Path) -> str:
    """Return markdown body after the frontmatter block. Strips trailing/leading whitespace."""
    text = path.read_text(encoding="utf-8")
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end >= 0:
            text = text[end + 5:]
    return text.strip()


def _age_hours(path: Path) -> float:
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return (datetime.now(tz=timezone.utc) - mtime).total_seconds() / 3600


# -- atomic file append -----------------------------------------------------


def _append_to_target(
    target_path: Path,
    section: Optional[str],
    fact: str,
    source: str,
) -> None:
    """Append fact to target_path under an H3 dated today. Atomic write.

    Section matching uses line-anchored regex on any heading level (H1-H6)
    to avoid false matches against body text mentioning the section name.
    """
    # Normalize empty string section to None
    if section == "":
        section = None

    target_path.parent.mkdir(parents=True, exist_ok=True)
    existing = target_path.read_text(encoding="utf-8") if target_path.exists() else ""
    date_str = datetime.now().astimezone().strftime("%Y-%m-%d")
    dated_header = f"\n\n### {date_str} — filed from inbox\n\n"
    fact_block = f"{dated_header}{fact}\n\n_Source: `_inbox/{source}`_\n"

    if section:
        # Match a heading line (H1-H6) whose text begins with the section
        # name. Anchored at start-of-line via MULTILINE. Escaped so section
        # names with regex metachars are safe.
        pattern = rf"^#{{1,6}} +{re.escape(section)}\b.*$"
        m = re.search(pattern, existing, flags=re.MULTILINE)
        if m:
            insert_at = m.end()
            new = existing[:insert_at] + fact_block + existing[insert_at:]
        else:
            new = existing.rstrip() + fact_block
    else:
        new = existing.rstrip() + fact_block

    tmp = target_path.with_suffix(target_path.suffix + ".tmp")
    tmp.write_text(new, encoding="utf-8")
    os.replace(tmp, target_path)


def _append_log(log_path: Path, section: str, lines: list[str], timestamp: str) -> None:
    """Append lines under a section in the log file. Atomic write.

    Section detection uses line-anchored regex, not substring search, so
    body text containing the header phrase (e.g. "The filing pass finished")
    doesn't false-match and split the file mid-paragraph.
    """
    if not lines:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    existing = log_path.read_text(encoding="utf-8") if log_path.exists() else "# LOG\n\n"
    block = (
        f"\n### {timestamp}\n\n"
        + "\n".join(f"- {line}" for line in lines)
        + "\n"
    )
    header_pattern = rf"^## +{re.escape(section)}\b.*$"
    header_match = re.search(header_pattern, existing, flags=re.MULTILINE)
    if header_match:
        # Insert block just before the next H2 (or EOF)
        search_from = header_match.end()
        nxt = re.search(r"^## ", existing[search_from:], flags=re.MULTILINE)
        if nxt:
            insert_at = search_from + nxt.start()
        else:
            insert_at = len(existing)
        new = existing[:insert_at] + block + existing[insert_at:]
    else:
        new = existing.rstrip() + f"\n\n## {section}\n{block}"

    tmp = log_path.with_suffix(".tmp")
    tmp.write_text(new, encoding="utf-8")
    os.replace(tmp, log_path)


def _load_agent_config(paths: VaultPaths) -> dict:
    """Shared helper — load ~/.spaice-agents/<id>/config.yaml, empty on miss."""
    config_path = paths.agent_config_dir / "config.yaml"
    if not config_path.exists() or yaml is None:
        return {}
    try:
        data = yaml.safe_load(config_path.read_text()) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}
