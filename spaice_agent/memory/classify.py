"""OpenRouter-backed memory classifier.

Routes a text snippet to its canonical vault file/section using the
agent's CATEGORISATION.md as system prompt. Primary model +
fallback-on-low-confidence pattern (per agent-memory-doctrine).

The CATEGORISATION.md is a VAULT artefact (user-curated), not a package
artefact — fresh installs ship an empty one in the skeleton, users
populate it as their classification rules solidify.
"""
from __future__ import annotations

import json
import re
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

from spaice_agent.credentials import read_credential, MissingCredentialError
from spaice_agent.memory.paths import VaultPaths


# Model pins (per ~/jarvis/spaice/doctrines/agent-memory-doctrine.md).
DEFAULT_PRIMARY_MODEL = "google/gemini-2.5-flash"
DEFAULT_FALLBACK_MODEL = "deepseek/deepseek-v3.1-terminus"
DEFAULT_FALLBACK_THRESHOLD = 0.5
DEFAULT_TEMPERATURE = 0.1
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_CATEGORISATION_FILENAME = "CATEGORISATION.md"
MAX_SNIPPET_CHARS = 4000

OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

SCHEMA_INSTRUCTIONS = """
Return ONLY a JSON object matching this schema (no prose, no markdown fence):

{
  "target_file": "<path relative to vault root>",
  "section": "<## Section heading within the file, or 'top'>",
  "dewey_layer": "<100|200|300|500|600|700|000 with label>",
  "priority": <1-5 integer matching Priority Table>,
  "rule_matched": "<quote the exact trigger from the Priority Table>",
  "cross_references": ["<other files if multi-category>"],
  "confidence": <0.0-1.0>,
  "reasoning": "<one sentence explaining the routing choice>"
}

If no rule matches strongly (confidence < 0.5), set priority=5 and
target_file="LOG.md" with section="Uncategorised".
""".strip()


class ClassifierError(Exception):
    """Base class for all classifier errors."""


class ClassifierConfigError(ClassifierError):
    """Raised when configuration is missing/invalid (no key, no CATEGORISATION.md)."""


class ClassifierAPIError(ClassifierError):
    """Raised when OpenRouter returns a non-recoverable error."""


class ClassifierResponseError(ClassifierError):
    """Raised when the model's response doesn't match the expected schema."""


@dataclass(frozen=True)
class Classification:
    """Structured result from a classify() call."""

    target_file: str
    section: Optional[str]
    dewey_layer: str
    priority: int
    rule_matched: str
    cross_references: tuple[str, ...]
    confidence: float
    reasoning: str
    model_used: str
    used_fallback: bool = False


@dataclass(frozen=True)
class ClassifierConfig:
    """Classifier settings loaded from agent config.yaml."""

    categorisation_md: str = DEFAULT_CATEGORISATION_FILENAME
    primary_model: str = DEFAULT_PRIMARY_MODEL
    fallback_model: str = DEFAULT_FALLBACK_MODEL
    fallback_threshold: float = DEFAULT_FALLBACK_THRESHOLD
    temperature: float = DEFAULT_TEMPERATURE
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    credential_slug: str = "openrouter"

    @classmethod
    def from_config_dict(cls, config: dict) -> "ClassifierConfig":
        """Build from `config.yaml`'s `memory.classifier` section (all keys optional)."""
        mem = (config or {}).get("memory") or {}
        cls_cfg = mem.get("classifier") or {}
        return cls(
            categorisation_md=mem.get("categorisation_md", DEFAULT_CATEGORISATION_FILENAME),
            primary_model=cls_cfg.get("primary_model", DEFAULT_PRIMARY_MODEL),
            fallback_model=cls_cfg.get("fallback_model", DEFAULT_FALLBACK_MODEL),
            fallback_threshold=float(cls_cfg.get("fallback_threshold", DEFAULT_FALLBACK_THRESHOLD)),
            temperature=float(cls_cfg.get("temperature", DEFAULT_TEMPERATURE)),
            timeout_seconds=int(cls_cfg.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)),
            credential_slug=cls_cfg.get("credential_slug", "openrouter"),
        )


@dataclass
class Classifier:
    """OpenRouter-backed memory classifier."""

    paths: VaultPaths
    config: ClassifierConfig
    api_key: str

    # -- constructors ------------------------------------------------------

    @classmethod
    def for_agent(cls, agent_id: str) -> "Classifier":
        """Build a classifier by reading the agent's config + credential."""
        paths = VaultPaths.for_agent(agent_id)
        agent_cfg = _load_agent_config(paths)
        cfg = ClassifierConfig.from_config_dict(agent_cfg)

        # Resolve credential dir via Path.home() at call time, so tests that
        # monkeypatch Path.home work. read_credential() caches CREDENTIAL_DIR
        # at module-load, so we must pass base_dir explicitly.
        cred_base = Path.home() / ".Hermes" / "credentials"
        try:
            api_key = read_credential(cfg.credential_slug, base_dir=cred_base)
        except MissingCredentialError as exc:
            raise ClassifierConfigError(
                f"Missing credential '{cfg.credential_slug}': {exc}"
            ) from exc

        return cls(paths=paths, config=cfg, api_key=api_key)

    # -- public API --------------------------------------------------------

    def classify(self, snippet: str) -> Classification:
        """Classify a snippet. Returns Classification, may retry on low confidence."""
        if not snippet or not snippet.strip():
            raise ValueError("snippet must be non-empty")

        # Truncate oversized snippets (cost control)
        snippet = snippet[:MAX_SNIPPET_CHARS]

        index_card = self._load_index_card()
        system_prompt = self._build_system_prompt(index_card)

        # Primary attempt
        primary = self._call_openrouter(
            snippet=snippet,
            system_prompt=system_prompt,
            model=self.config.primary_model,
        )
        primary_classification = self._parse_response(primary, model=self.config.primary_model)

        # Fallback if confidence too low AND fallback model is distinct
        if (
            primary_classification.confidence < self.config.fallback_threshold
            and self.config.primary_model != self.config.fallback_model
        ):
            try:
                fallback = self._call_openrouter(
                    snippet=snippet,
                    system_prompt=system_prompt,
                    model=self.config.fallback_model,
                )
                fallback_classification = self._parse_response(
                    fallback, model=self.config.fallback_model,
                )
                if fallback_classification.confidence > primary_classification.confidence:
                    # Keep fallback, mark it
                    return Classification(
                        **{
                            **fallback_classification.__dict__,
                            "used_fallback": True,
                        }
                    )
            except (ClassifierAPIError, ClassifierResponseError):
                # Fallback failed — keep primary
                pass

        return primary_classification

    # -- internals ---------------------------------------------------------

    def _load_index_card(self) -> str:
        md_path = self.paths.vault_root / self.config.categorisation_md
        if not md_path.exists():
            raise ClassifierConfigError(
                f"{self.config.categorisation_md} not found at {md_path}. "
                f"Create it in the vault root to define classification rules."
            )
        return md_path.read_text()

    def _build_system_prompt(self, index_card: str) -> str:
        return (
            "You are the agent's memory categoriser. Apply CATEGORISATION.md "
            "rules in priority order (first match wins) to route a fragment "
            "to its canonical target file.\n\n"
            "CATEGORISATION.md (the index card — ALWAYS consult this first):\n"
            "---\n"
            f"{index_card}"
            "\n---\n\n"
            f"{SCHEMA_INSTRUCTIONS}"
        )

    def _call_openrouter(
        self,
        *,
        snippet: str,
        system_prompt: str,
        model: str,
    ) -> dict:
        """Single API call with retry on transient errors (429/5xx). Returns parsed JSON."""
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Categorise this fragment:\n\n{snippet}"},
            ],
            "response_format": {"type": "json_object"},
            "temperature": self.config.temperature,
        }
        req = urllib.request.Request(
            OPENROUTER_ENDPOINT,
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://spaice.local/agent",
                "X-Title": "spaice-agent memory classifier",
            },
        )

        delays = (2, 4)  # exponential-ish backoff on retriable errors
        last_exc: Optional[Exception] = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as exc:
                # Retry on rate limit (429) and 5xx; bail on 4xx otherwise
                if exc.code in (429,) or 500 <= exc.code < 600:
                    last_exc = exc
                    if attempt < len(delays):
                        time.sleep(delays[attempt])
                    continue
                body_snippet = exc.read()[:500].decode("utf-8", errors="replace")
                raise ClassifierAPIError(
                    f"OpenRouter {exc.code}: {body_snippet}"
                ) from exc
            except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
                # Retry network errors AND socket timeouts. urllib.urlopen
                # raises socket.timeout (subclass of OSError, not URLError)
                # when the `timeout=` param expires — without this branch,
                # the call would fail on first timeout instead of retrying.
                last_exc = exc
                if attempt < len(delays):
                    time.sleep(delays[attempt])
                continue

        raise ClassifierAPIError(
            f"OpenRouter unavailable after 3 attempts: {last_exc}"
        )

    def _parse_response(self, resp: dict, *, model: str) -> Classification:
        """Pull the JSON object out of the response and validate shape."""
        try:
            content = resp["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ClassifierResponseError(
                f"Unexpected response shape: {resp}"
            ) from exc

        # Strip ```json fences defensively — match only actual fence lines
        # (```, ```json, ```yaml, etc.) not content that happens to start
        # with three backticks followed by text.
        stripped = content.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            lines = [
                l for l in lines
                if not re.match(r"^\s*```[a-zA-Z0-9_-]*\s*$", l)
            ]
            stripped = "\n".join(lines).strip()

        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ClassifierResponseError(
                f"Could not parse JSON from model: {content[:300]!r}"
            ) from exc

        if not isinstance(data, dict):
            raise ClassifierResponseError(
                f"Expected JSON object, got {type(data).__name__}"
            )

        # Extract fields with defaults for optional ones
        try:
            target_file = str(data["target_file"])
            dewey_layer = str(data.get("dewey_layer", "000"))
            priority_raw = int(data.get("priority", 5))
            # Clamp priority to [1, 5] per schema — model may hallucinate
            priority = max(1, min(5, priority_raw))
            rule_matched = str(data.get("rule_matched", "unknown"))
            confidence_raw = float(data.get("confidence", 0.0))
            # Clamp confidence to [0.0, 1.0] — model may hallucinate outside range
            confidence = max(0.0, min(1.0, confidence_raw))
            reasoning = str(data.get("reasoning", ""))
            section = data.get("section")
            # Normalise empty string to None for consistent downstream handling
            section = str(section) if section else None
            if section == "":
                section = None
            cross_refs_raw = data.get("cross_references", []) or []
            cross_refs = tuple(str(x) for x in cross_refs_raw if x)
        except (KeyError, ValueError, TypeError) as exc:
            raise ClassifierResponseError(
                f"Missing/invalid field in classifier response: {exc}"
            ) from exc

        return Classification(
            target_file=target_file,
            section=section,
            dewey_layer=dewey_layer,
            priority=priority,
            rule_matched=rule_matched,
            cross_references=cross_refs,
            confidence=confidence,
            reasoning=reasoning,
            model_used=model,
            used_fallback=False,
        )


def _load_agent_config(paths: VaultPaths) -> dict:
    """Load ~/.spaice-agents/<id>/config.yaml, returning an empty dict if absent."""
    config_path = paths.agent_config_dir / "config.yaml"
    if not config_path.exists():
        return {}
    if yaml is None:  # pragma: no cover
        return {}
    try:
        data = yaml.safe_load(config_path.read_text()) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}
