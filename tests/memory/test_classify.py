"""Tests for spaice_agent.memory.classify."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError, URLError

import pytest
import yaml

from spaice_agent.memory.classify import (
    Classifier,
    ClassifierConfig,
    Classification,
    ClassifierAPIError,
    ClassifierConfigError,
    ClassifierResponseError,
    DEFAULT_PRIMARY_MODEL,
    DEFAULT_FALLBACK_MODEL,
    MAX_SNIPPET_CHARS,
)
from spaice_agent.memory.paths import VaultPaths


# -- fixtures ---------------------------------------------------------------


def _setup_agent(tmp_path, monkeypatch, agent_id="testbot", config: dict = None,
                 categorisation: str = "# Categorisation rules\n\nDefault rule.\n"):
    """Create vault + config + CATEGORISATION.md + mock credential.

    Returns the tmp_path-rooted home.
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    vault = tmp_path / agent_id
    vault.mkdir()
    (vault / "_inbox").mkdir()
    (vault / "CATEGORISATION.md").write_text(categorisation)
    agent_dir = tmp_path / ".spaice-agents" / agent_id
    agent_dir.mkdir(parents=True)
    if config is not None:
        (agent_dir / "config.yaml").write_text(yaml.safe_dump(config))
    # Credential file
    cred_dir = tmp_path / ".Hermes" / "credentials"
    cred_dir.mkdir(parents=True)
    key_file = cred_dir / "openrouter.key"
    key_file.write_text("sk-test-1234567890\n")
    key_file.chmod(0o600)
    cred_dir.chmod(0o700)
    return tmp_path


def _mock_ok_response(content: dict) -> MagicMock:
    """urllib.request.urlopen(...) context manager returning an OK response."""
    resp_body = {
        "choices": [{"message": {"content": json.dumps(content)}}],
    }
    r = MagicMock()
    r.read.return_value = json.dumps(resp_body).encode()
    cm = MagicMock()
    cm.__enter__.return_value = r
    cm.__exit__.return_value = False
    return cm


# -- config loading ---------------------------------------------------------


def test_config_defaults():
    cfg = ClassifierConfig.from_config_dict({})
    assert cfg.primary_model == DEFAULT_PRIMARY_MODEL
    assert cfg.fallback_model == DEFAULT_FALLBACK_MODEL
    assert cfg.fallback_threshold == 0.5
    assert cfg.categorisation_md == "CATEGORISATION.md"


def test_config_overrides():
    cfg = ClassifierConfig.from_config_dict({
        "memory": {
            "categorisation_md": "custom.md",
            "classifier": {
                "primary_model": "openai/gpt-5",
                "fallback_threshold": 0.7,
                "temperature": 0.3,
            },
        },
    })
    assert cfg.categorisation_md == "custom.md"
    assert cfg.primary_model == "openai/gpt-5"
    assert cfg.fallback_threshold == 0.7
    assert cfg.temperature == 0.3


# -- classifier construction -----------------------------------------------


def test_for_agent_loads_config(tmp_path, monkeypatch):
    _setup_agent(tmp_path, monkeypatch, config={
        "memory": {"classifier": {"primary_model": "foo/bar"}},
    })
    c = Classifier.for_agent("testbot")
    assert c.config.primary_model == "foo/bar"
    assert c.api_key == "sk-test-1234567890"


def test_for_agent_missing_credential_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / "testbot").mkdir()
    (tmp_path / "testbot" / "_inbox").mkdir()
    (tmp_path / "testbot" / "CATEGORISATION.md").write_text("rules")
    # No credential file
    with pytest.raises(ClassifierConfigError, match="credential"):
        Classifier.for_agent("testbot")


# -- classify() happy path --------------------------------------------------


def test_classify_happy_path(tmp_path, monkeypatch):
    _setup_agent(tmp_path, monkeypatch)
    c = Classifier.for_agent("testbot")

    mock_resp = _mock_ok_response({
        "target_file": "bridges/knx.md",
        "section": "Known IAs",
        "dewey_layer": "500-domain",
        "priority": 2,
        "rule_matched": "Brand + ID",
        "cross_references": ["sites/hanna.md"],
        "confidence": 0.92,
        "reasoning": "KNX IA is a protocol fact.",
    })
    with patch("spaice_agent.memory.classify.urllib.request.urlopen", return_value=mock_resp):
        result = c.classify("KNX IA 1.1.241 at Hanna")

    assert result.target_file == "bridges/knx.md"
    assert result.section == "Known IAs"
    assert result.confidence == 0.92
    assert result.used_fallback is False
    assert result.model_used == DEFAULT_PRIMARY_MODEL
    assert result.cross_references == ("sites/hanna.md",)


def test_classify_rejects_empty(tmp_path, monkeypatch):
    _setup_agent(tmp_path, monkeypatch)
    c = Classifier.for_agent("testbot")
    with pytest.raises(ValueError, match="non-empty"):
        c.classify("")
    with pytest.raises(ValueError, match="non-empty"):
        c.classify("   ")


def test_classify_truncates_oversized_snippet(tmp_path, monkeypatch):
    _setup_agent(tmp_path, monkeypatch)
    c = Classifier.for_agent("testbot")
    mock_resp = _mock_ok_response({
        "target_file": "LOG.md", "section": "top", "dewey_layer": "000",
        "priority": 5, "rule_matched": "fallback", "cross_references": [],
        "confidence": 0.1, "reasoning": "x",
    })

    huge = "x" * (MAX_SNIPPET_CHARS + 1000)
    with patch("spaice_agent.memory.classify.urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        c.classify(huge)
    # The request body would contain at most MAX_SNIPPET_CHARS chars
    sent_request = mock_urlopen.call_args[0][0]
    body_str = sent_request.data.decode()
    body = json.loads(body_str)
    user_msg = body["messages"][1]["content"]
    # Strip the "Categorise this fragment:\n\n" prefix
    sent_snippet = user_msg.split("\n\n", 1)[1]
    assert len(sent_snippet) <= MAX_SNIPPET_CHARS


# -- fallback behaviour -----------------------------------------------------


def test_fallback_triggered_on_low_confidence(tmp_path, monkeypatch):
    _setup_agent(tmp_path, monkeypatch)
    c = Classifier.for_agent("testbot")

    primary_resp = _mock_ok_response({
        "target_file": "LOG.md", "section": "top", "dewey_layer": "000",
        "priority": 5, "rule_matched": "none", "cross_references": [],
        "confidence": 0.3, "reasoning": "primary is unsure",
    })
    fallback_resp = _mock_ok_response({
        "target_file": "bridges/knx.md", "section": "IAs", "dewey_layer": "500",
        "priority": 2, "rule_matched": "KNX rule", "cross_references": [],
        "confidence": 0.88, "reasoning": "fallback is sure",
    })

    call_count = {"n": 0}

    def _urlopen(req, timeout):
        call_count["n"] += 1
        return primary_resp if call_count["n"] == 1 else fallback_resp

    with patch("spaice_agent.memory.classify.urllib.request.urlopen", side_effect=_urlopen):
        result = c.classify("some snippet")

    assert call_count["n"] == 2
    assert result.used_fallback is True
    assert result.confidence == 0.88
    assert result.model_used == DEFAULT_FALLBACK_MODEL


def test_fallback_not_triggered_on_high_confidence(tmp_path, monkeypatch):
    _setup_agent(tmp_path, monkeypatch)
    c = Classifier.for_agent("testbot")
    mock_resp = _mock_ok_response({
        "target_file": "x.md", "section": "top", "dewey_layer": "000",
        "priority": 3, "rule_matched": "r", "cross_references": [],
        "confidence": 0.95, "reasoning": "sure",
    })
    call_count = {"n": 0}

    def _urlopen(req, timeout):
        call_count["n"] += 1
        return mock_resp

    with patch("spaice_agent.memory.classify.urllib.request.urlopen", side_effect=_urlopen):
        result = c.classify("snippet")

    assert call_count["n"] == 1
    assert result.used_fallback is False


def test_fallback_keeps_primary_if_it_fails(tmp_path, monkeypatch):
    """If fallback errors out, keep the primary (even if low-confidence)."""
    _setup_agent(tmp_path, monkeypatch)
    c = Classifier.for_agent("testbot")
    primary_resp = _mock_ok_response({
        "target_file": "x.md", "section": "top", "dewey_layer": "000",
        "priority": 5, "rule_matched": "r", "cross_references": [],
        "confidence": 0.3, "reasoning": "primary unsure",
    })

    # Second call raises
    def _urlopen(req, timeout):
        if _urlopen.n == 0:
            _urlopen.n += 1
            return primary_resp
        raise HTTPError("x", 500, "err", {}, None)
    _urlopen.n = 0

    with patch("spaice_agent.memory.classify.urllib.request.urlopen", side_effect=_urlopen):
        result = c.classify("snippet")

    # Keep primary
    assert result.confidence == 0.3
    assert result.used_fallback is False


# -- HTTP error handling ----------------------------------------------------


def test_4xx_non_retryable_raises(tmp_path, monkeypatch):
    _setup_agent(tmp_path, monkeypatch)
    c = Classifier.for_agent("testbot")

    err_body = b'{"error": "unauthorised"}'
    http_err = HTTPError("x", 401, "unauth", {}, MagicMock(read=lambda: err_body))
    http_err.read = lambda: err_body

    with patch("spaice_agent.memory.classify.urllib.request.urlopen", side_effect=http_err):
        with pytest.raises(ClassifierAPIError, match="401"):
            c.classify("snippet")


def test_429_retries_and_eventually_succeeds(tmp_path, monkeypatch):
    _setup_agent(tmp_path, monkeypatch)
    c = Classifier.for_agent("testbot")

    ok_resp = _mock_ok_response({
        "target_file": "x.md", "section": "top", "dewey_layer": "000",
        "priority": 3, "rule_matched": "r", "cross_references": [],
        "confidence": 0.9, "reasoning": "ok",
    })
    rate_err = HTTPError("x", 429, "rate", {}, None)
    rate_err.read = lambda: b"rate limited"

    call_count = {"n": 0}

    def _urlopen(req, timeout):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise rate_err
        return ok_resp

    # Patch sleep to avoid real delay
    with patch("spaice_agent.memory.classify.time.sleep"), \
         patch("spaice_agent.memory.classify.urllib.request.urlopen", side_effect=_urlopen):
        result = c.classify("snippet")

    assert call_count["n"] == 2
    assert result.confidence == 0.9


def test_5xx_retries_then_raises(tmp_path, monkeypatch):
    _setup_agent(tmp_path, monkeypatch)
    c = Classifier.for_agent("testbot")
    err = HTTPError("x", 503, "unavail", {}, None)
    err.read = lambda: b"unavail"

    call_count = {"n": 0}

    def _urlopen(req, timeout):
        call_count["n"] += 1
        raise err

    with patch("spaice_agent.memory.classify.time.sleep"), \
         patch("spaice_agent.memory.classify.urllib.request.urlopen", side_effect=_urlopen):
        with pytest.raises(ClassifierAPIError, match="unavailable after 3 attempts"):
            c.classify("snippet")
    assert call_count["n"] == 3


def test_network_error_retries(tmp_path, monkeypatch):
    _setup_agent(tmp_path, monkeypatch)
    c = Classifier.for_agent("testbot")

    ok_resp = _mock_ok_response({
        "target_file": "x.md", "section": "top", "dewey_layer": "000",
        "priority": 3, "rule_matched": "r", "cross_references": [],
        "confidence": 0.9, "reasoning": "ok",
    })
    url_err = URLError("connection refused")

    call_count = {"n": 0}

    def _urlopen(req, timeout):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise url_err
        return ok_resp

    with patch("spaice_agent.memory.classify.time.sleep"), \
         patch("spaice_agent.memory.classify.urllib.request.urlopen", side_effect=_urlopen):
        result = c.classify("snippet")
    assert result.confidence == 0.9


# -- response parsing -------------------------------------------------------


def test_response_with_json_fence_parsed(tmp_path, monkeypatch):
    _setup_agent(tmp_path, monkeypatch)
    c = Classifier.for_agent("testbot")
    # Model accidentally wraps in fences
    fenced = "```json\n" + json.dumps({
        "target_file": "x.md", "section": "top", "dewey_layer": "000",
        "priority": 3, "rule_matched": "r", "cross_references": [],
        "confidence": 0.9, "reasoning": "ok",
    }) + "\n```"
    r = MagicMock()
    r.read.return_value = json.dumps({
        "choices": [{"message": {"content": fenced}}],
    }).encode()
    cm = MagicMock()
    cm.__enter__.return_value = r
    cm.__exit__.return_value = False

    with patch("spaice_agent.memory.classify.urllib.request.urlopen", return_value=cm):
        result = c.classify("snippet")
    assert result.target_file == "x.md"


def test_malformed_response_raises(tmp_path, monkeypatch):
    _setup_agent(tmp_path, monkeypatch)
    c = Classifier.for_agent("testbot")
    r = MagicMock()
    r.read.return_value = json.dumps({
        "choices": [{"message": {"content": "totally not json"}}],
    }).encode()
    cm = MagicMock()
    cm.__enter__.return_value = r
    cm.__exit__.return_value = False

    with patch("spaice_agent.memory.classify.urllib.request.urlopen", return_value=cm):
        with pytest.raises(ClassifierResponseError, match="Could not parse"):
            c.classify("snippet")


def test_response_missing_target_raises(tmp_path, monkeypatch):
    _setup_agent(tmp_path, monkeypatch)
    c = Classifier.for_agent("testbot")
    # Missing target_file field
    r = MagicMock()
    r.read.return_value = json.dumps({
        "choices": [{"message": {"content": json.dumps({"confidence": 0.5})}}],
    }).encode()
    cm = MagicMock()
    cm.__enter__.return_value = r
    cm.__exit__.return_value = False

    with patch("spaice_agent.memory.classify.urllib.request.urlopen", return_value=cm):
        with pytest.raises(ClassifierResponseError):
            c.classify("snippet")


def test_response_shape_error(tmp_path, monkeypatch):
    _setup_agent(tmp_path, monkeypatch)
    c = Classifier.for_agent("testbot")
    r = MagicMock()
    r.read.return_value = json.dumps({"unexpected": "shape"}).encode()
    cm = MagicMock()
    cm.__enter__.return_value = r
    cm.__exit__.return_value = False

    with patch("spaice_agent.memory.classify.urllib.request.urlopen", return_value=cm):
        with pytest.raises(ClassifierResponseError, match="Unexpected response shape"):
            c.classify("snippet")


# -- CATEGORISATION.md handling --------------------------------------------


def test_missing_categorisation_md_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / "testbot").mkdir()
    (tmp_path / "testbot" / "_inbox").mkdir()
    # No CATEGORISATION.md
    cred_dir = tmp_path / ".Hermes" / "credentials"
    cred_dir.mkdir(parents=True)
    (cred_dir / "openrouter.key").write_text("key")
    (cred_dir / "openrouter.key").chmod(0o600)
    cred_dir.chmod(0o700)

    c = Classifier.for_agent("testbot")

    with pytest.raises(ClassifierConfigError, match="CATEGORISATION.md"):
        c.classify("snippet")


# -- request body inspection -----------------------------------------------


def test_request_body_has_expected_fields(tmp_path, monkeypatch):
    _setup_agent(tmp_path, monkeypatch)
    c = Classifier.for_agent("testbot")

    mock_resp = _mock_ok_response({
        "target_file": "x.md", "section": "top", "dewey_layer": "000",
        "priority": 3, "rule_matched": "r", "cross_references": [],
        "confidence": 0.9, "reasoning": "ok",
    })

    with patch("spaice_agent.memory.classify.urllib.request.urlopen", return_value=mock_resp) as mu:
        c.classify("snippet")

    req = mu.call_args[0][0]
    body = json.loads(req.data.decode())
    assert body["model"] == DEFAULT_PRIMARY_MODEL
    assert body["temperature"] == 0.1
    assert body["response_format"] == {"type": "json_object"}
    assert len(body["messages"]) == 2
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["role"] == "user"
    assert "snippet" in body["messages"][1]["content"]


# -- regression guards from Codex Phase 1B review 2026-05-03 ---------------


def test_confidence_clamped_to_unit_interval(tmp_path, monkeypatch):
    """Regression: Codex classify #2 — model may hallucinate conf>1.0 or <0."""
    _setup_agent(tmp_path, monkeypatch)
    c = Classifier.for_agent("testbot")

    # Model returns insane confidence values
    for bad_conf, expected in [(5.0, 1.0), (-0.5, 0.0), (1.5, 1.0)]:
        mock_resp = _mock_ok_response({
            "target_file": "x.md", "section": "top", "dewey_layer": "000",
            "priority": 3, "rule_matched": "r", "cross_references": [],
            "confidence": bad_conf, "reasoning": "ok",
        })
        with patch("spaice_agent.memory.classify.urllib.request.urlopen", return_value=mock_resp):
            r = c.classify("snippet")
        assert r.confidence == expected, f"conf={bad_conf} → expected clamp to {expected}"


def test_priority_clamped_to_1_to_5(tmp_path, monkeypatch):
    """Regression: Codex classify #3 — priority must stay in [1, 5]."""
    _setup_agent(tmp_path, monkeypatch)
    c = Classifier.for_agent("testbot")

    for bad_prio, expected in [(99, 5), (0, 1), (-3, 1), (7, 5)]:
        mock_resp = _mock_ok_response({
            "target_file": "x.md", "section": "top", "dewey_layer": "000",
            "priority": bad_prio, "rule_matched": "r", "cross_references": [],
            "confidence": 0.9, "reasoning": "ok",
        })
        with patch("spaice_agent.memory.classify.urllib.request.urlopen", return_value=mock_resp):
            r = c.classify("snippet")
        assert r.priority == expected, f"priority={bad_prio} → expected clamp to {expected}"


def test_socket_timeout_retried(tmp_path, monkeypatch):
    """Regression: Codex classify #5 — socket.timeout not a URLError subclass."""
    import socket as _socket
    _setup_agent(tmp_path, monkeypatch)
    c = Classifier.for_agent("testbot")

    ok_resp = _mock_ok_response({
        "target_file": "x.md", "section": "top", "dewey_layer": "000",
        "priority": 3, "rule_matched": "r", "cross_references": [],
        "confidence": 0.9, "reasoning": "ok",
    })
    call_count = {"n": 0}

    def _urlopen(req, timeout):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise _socket.timeout("timed out")
        return ok_resp

    with patch("spaice_agent.memory.classify.time.sleep"), \
         patch("spaice_agent.memory.classify.urllib.request.urlopen", side_effect=_urlopen):
        r = c.classify("snippet")
    assert call_count["n"] == 2
    assert r.confidence == 0.9


def test_configurable_categorisation_filename_in_error(tmp_path, monkeypatch):
    """Regression: Codex classify #4 — error must mention configured filename."""
    _setup_agent(tmp_path, monkeypatch, config={
        "memory": {"categorisation_md": "my-rules.md"},
    })
    # Delete default CATEGORISATION.md so the configured one is missing
    (tmp_path / "testbot" / "CATEGORISATION.md").unlink()
    c = Classifier.for_agent("testbot")
    with pytest.raises(ClassifierConfigError, match="my-rules.md"):
        c.classify("snippet")


def test_fence_stripping_preserves_json_with_backticks(tmp_path, monkeypatch):
    """Regression: Codex classify #6 — fence regex was dropping any line starting with ```."""
    _setup_agent(tmp_path, monkeypatch)
    c = Classifier.for_agent("testbot")

    # Response wrapped in fences — should still parse
    fenced = (
        "```json\n"
        + json.dumps({
            "target_file": "x.md", "section": "top", "dewey_layer": "000",
            "priority": 3, "rule_matched": "r", "cross_references": [],
            "confidence": 0.9, "reasoning": "has ```nested``` backticks in reasoning",
        })
        + "\n```"
    )
    r = MagicMock()
    r.read.return_value = json.dumps({
        "choices": [{"message": {"content": fenced}}],
    }).encode()
    cm = MagicMock()
    cm.__enter__.return_value = r
    cm.__exit__.return_value = False

    with patch("spaice_agent.memory.classify.urllib.request.urlopen", return_value=cm):
        result = c.classify("snippet")

    assert result.target_file == "x.md"
    assert "nested" in result.reasoning  # content preserved


def test_empty_string_section_normalised_to_none(tmp_path, monkeypatch):
    """Regression: Codex classify follow-up — empty string section should be None."""
    _setup_agent(tmp_path, monkeypatch)
    c = Classifier.for_agent("testbot")
    mock_resp = _mock_ok_response({
        "target_file": "x.md", "section": "", "dewey_layer": "000",
        "priority": 3, "rule_matched": "r", "cross_references": [],
        "confidence": 0.9, "reasoning": "ok",
    })
    with patch("spaice_agent.memory.classify.urllib.request.urlopen", return_value=mock_resp):
        r = c.classify("snippet")
    assert r.section is None
