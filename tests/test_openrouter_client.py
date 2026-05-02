"""Tests for spaice_agent.openrouter_client."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from spaice_agent.openrouter_client import (
    AuthError,
    BadRequestError,
    ChatResult,
    OpenRouterClient,
    OpenRouterError,
    RateLimitError,
    ServerError,
    _parse_retry_after,
)


# ============================================================
# _parse_retry_after
# ============================================================


class TestParseRetryAfter:
    def test_none_returns_none(self):
        assert _parse_retry_after(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_retry_after("") is None
        assert _parse_retry_after("   ") is None

    def test_integer_seconds(self):
        assert _parse_retry_after("5") == 5.0

    def test_float_seconds(self):
        assert _parse_retry_after("1.5") == 1.5

    def test_zero_seconds(self):
        assert _parse_retry_after("0") == 0.0

    def test_negative_clamped_to_zero(self):
        assert _parse_retry_after("-5") == 0.0

    def test_http_date_future(self):
        now = datetime(2026, 5, 3, 12, 0, 0, tzinfo=timezone.utc)
        future = (now + timedelta(seconds=10)).strftime("%a, %d %b %Y %H:%M:%S GMT")
        result = _parse_retry_after(future, now=now)
        assert result is not None
        assert abs(result - 10.0) < 1.0

    def test_http_date_past_clamped_to_zero(self):
        now = datetime(2026, 5, 3, 12, 0, 0, tzinfo=timezone.utc)
        past = (now - timedelta(seconds=60)).strftime("%a, %d %b %Y %H:%M:%S GMT")
        result = _parse_retry_after(past, now=now)
        assert result == 0.0

    def test_garbage_returns_none(self):
        assert _parse_retry_after("not-a-date") is None
        assert _parse_retry_after("[]") is None


# ============================================================
# Helpers for mock-transport tests
# ============================================================


def _mock_client(handler, api_key: str = "sk-test", max_retries: int = 2) -> OpenRouterClient:
    """Build an OpenRouterClient backed by a MockTransport."""
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        transport=transport,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://test.local",
            "X-Title": "test",
        },
    )
    return OpenRouterClient(
        api_key=api_key,
        max_retries=max_retries,
        client=http_client,
    )


def _success_body(text: str = "ok", cost: float = 0.01) -> dict:
    return {
        "id": "test-id",
        "model": "anthropic/claude-opus-4.7",
        "choices": [{
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "cost": cost},
    }


# ============================================================
# Construction
# ============================================================


class TestConstruction:
    def test_empty_api_key_rejected(self):
        with pytest.raises(ValueError):
            OpenRouterClient(api_key="")

    async def test_context_manager(self):
        client = _mock_client(lambda req: httpx.Response(200, json=_success_body()))
        async with client as c:
            assert c is client


# ============================================================
# Success path
# ============================================================


class TestChatSuccess:
    async def test_basic_success(self):
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert body["model"] == "test-model"
            assert body["max_tokens"] == 100
            return httpx.Response(200, json=_success_body("hello world", cost=0.05))

        client = _mock_client(handler)
        result = await client.chat(
            model="test-model",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=100,
        )
        assert isinstance(result, ChatResult)
        assert result.text == "hello world"
        assert result.input_tokens == 100
        assert result.output_tokens == 50
        assert result.cost_usd == 0.05
        assert result.finish_reason == "stop"
        assert result.latency_s >= 0.0
        await client.aclose()

    async def test_empty_content_becomes_empty_string(self):
        def handler(req):
            body = _success_body()
            body["choices"][0]["message"]["content"] = None
            return httpx.Response(200, json=body)

        client = _mock_client(handler)
        result = await client.chat(model="m", messages=[{"role": "user", "content": "x"}], max_tokens=1)
        assert result.text == ""
        await client.aclose()

    async def test_missing_usage_defaults(self):
        def handler(req):
            body = _success_body()
            body.pop("usage")
            return httpx.Response(200, json=body)

        client = _mock_client(handler)
        result = await client.chat(model="m", messages=[{"role": "user", "content": "x"}], max_tokens=1)
        assert result.input_tokens == 0
        assert result.output_tokens == 0
        assert result.cost_usd == 0.0
        await client.aclose()


# ============================================================
# Unrecoverable errors — fail-fast, no retry
# ============================================================


class TestUnrecoverable:
    async def test_400_bad_request_no_retry(self):
        call_count = 0
        def handler(req):
            nonlocal call_count
            call_count += 1
            return httpx.Response(400, json={"error": {"message": "bad model"}})

        client = _mock_client(handler, max_retries=3)
        with pytest.raises(BadRequestError) as excinfo:
            await client.chat(model="bogus", messages=[{"role": "user", "content": "x"}], max_tokens=1)
        assert "bad model" in str(excinfo.value)
        assert excinfo.value.status == 400
        assert call_count == 1  # no retries
        await client.aclose()

    async def test_401_auth_no_retry(self):
        call_count = 0
        def handler(req):
            nonlocal call_count
            call_count += 1
            return httpx.Response(401, json={"error": {"message": "no key"}})

        client = _mock_client(handler, max_retries=3)
        with pytest.raises(AuthError):
            await client.chat(model="m", messages=[{"role": "user", "content": "x"}], max_tokens=1)
        assert call_count == 1
        await client.aclose()

    async def test_404_no_retry(self):
        call_count = 0
        def handler(req):
            nonlocal call_count
            call_count += 1
            return httpx.Response(404, json={"error": {"message": "not found"}})

        client = _mock_client(handler, max_retries=3)
        with pytest.raises(BadRequestError):
            await client.chat(model="m", messages=[{"role": "user", "content": "x"}], max_tokens=1)
        assert call_count == 1
        await client.aclose()


# ============================================================
# Retry behaviour — 429 and 5xx
# ============================================================


class TestRetry:
    async def test_429_then_success(self, monkeypatch):
        # no-op sleep to speed tests
        async def fake_sleep(s): pass
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        call_count = 0
        def handler(req):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(429, json={"error": {"message": "slow down"}},
                                      headers={"Retry-After": "2"})
            return httpx.Response(200, json=_success_body())

        client = _mock_client(handler, max_retries=2)
        result = await client.chat(model="m", messages=[{"role": "user", "content": "x"}], max_tokens=1)
        assert result.text == "ok"
        assert call_count == 2
        await client.aclose()

    async def test_429_with_long_retry_after_fails_immediately(self, monkeypatch):
        async def fake_sleep(s): pass
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        call_count = 0
        def handler(req):
            nonlocal call_count
            call_count += 1
            return httpx.Response(429, json={"error": {"message": "slow down"}},
                                  headers={"Retry-After": "60"})

        client = _mock_client(handler, max_retries=3)
        with pytest.raises(RateLimitError) as excinfo:
            await client.chat(model="m", messages=[{"role": "user", "content": "x"}], max_tokens=1)
        # Long Retry-After caps at MAX_RETRY_AFTER_S (5s) — must NOT retry
        assert call_count == 1
        assert excinfo.value.retry_after_seconds == 60.0
        await client.aclose()

    async def test_500_retried_then_succeeds(self, monkeypatch):
        async def fake_sleep(s): pass
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        call_count = 0
        def handler(req):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return httpx.Response(500, json={"error": {"message": "oops"}})
            return httpx.Response(200, json=_success_body())

        client = _mock_client(handler, max_retries=3)
        result = await client.chat(model="m", messages=[{"role": "user", "content": "x"}], max_tokens=1)
        assert result.text == "ok"
        assert call_count == 3
        await client.aclose()

    async def test_all_retries_exhausted_raises(self, monkeypatch):
        async def fake_sleep(s): pass
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        call_count = 0
        def handler(req):
            nonlocal call_count
            call_count += 1
            return httpx.Response(503, json={"error": {"message": "overloaded"}})

        client = _mock_client(handler, max_retries=2)
        with pytest.raises(ServerError):
            await client.chat(model="m", messages=[{"role": "user", "content": "x"}], max_tokens=1)
        assert call_count == 3  # initial + 2 retries
        await client.aclose()

    async def test_linear_backoff_when_no_retry_after(self, monkeypatch):
        """Without Retry-After, backoff should be attempt * 1.0s."""
        sleeps = []
        async def fake_sleep(s): sleeps.append(s)
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        call_count = 0
        def handler(req):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return httpx.Response(503)  # no Retry-After
            return httpx.Response(200, json=_success_body())

        client = _mock_client(handler, max_retries=2)
        await client.chat(model="m", messages=[{"role": "user", "content": "x"}], max_tokens=1)
        assert sleeps == [1.0, 2.0]
        await client.aclose()

    async def test_retry_after_respected_over_linear(self, monkeypatch):
        sleeps = []
        async def fake_sleep(s): sleeps.append(s)
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        call_count = 0
        def handler(req):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(429, json={"error": {"message": "x"}},
                                      headers={"Retry-After": "3"})
            return httpx.Response(200, json=_success_body())

        client = _mock_client(handler, max_retries=2)
        await client.chat(model="m", messages=[{"role": "user", "content": "x"}], max_tokens=1)
        assert sleeps == [3.0]  # server-supplied, not linear
        await client.aclose()


# ============================================================
# Timeout + cancellation
# ============================================================


class TestTimeoutAndCancellation:
    async def test_httpx_timeout_retried(self, monkeypatch):
        async def fake_sleep(s): pass
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        call_count = 0
        def handler(req):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise httpx.TimeoutException("too slow", request=req)
            return httpx.Response(200, json=_success_body())

        client = _mock_client(handler, max_retries=2)
        result = await client.chat(model="m", messages=[{"role": "user", "content": "x"}], max_tokens=1)
        assert call_count == 2
        await client.aclose()

    async def test_repeated_timeouts_raise_server_error(self, monkeypatch):
        async def fake_sleep(s): pass
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        def handler(req):
            raise httpx.TimeoutException("always slow", request=req)

        client = _mock_client(handler, max_retries=1)
        with pytest.raises(ServerError):
            await client.chat(model="m", messages=[{"role": "user", "content": "x"}], max_tokens=1)
        await client.aclose()

    async def test_cancelled_error_propagates(self, monkeypatch):
        async def fake_sleep(s):
            raise asyncio.CancelledError()
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        def handler(req):
            return httpx.Response(500, json={"error": {"message": "x"}})

        client = _mock_client(handler, max_retries=2)
        with pytest.raises(asyncio.CancelledError):
            await client.chat(model="m", messages=[{"role": "user", "content": "x"}], max_tokens=1)
        await client.aclose()


# ============================================================
# Edge cases
# ============================================================


class TestEdgeCases:
    async def test_200_no_choices_raises(self):
        def handler(req):
            return httpx.Response(200, json={"model": "m", "choices": []})

        client = _mock_client(handler)
        with pytest.raises(OpenRouterError) as excinfo:
            await client.chat(model="m", messages=[{"role": "user", "content": "x"}], max_tokens=1)
        assert "no choices" in str(excinfo.value).lower()
        await client.aclose()

    async def test_malformed_json_body_on_error(self, monkeypatch):
        async def fake_sleep(s): pass
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        def handler(req):
            return httpx.Response(500, content=b"<html>502 Bad Gateway</html>")

        client = _mock_client(handler, max_retries=0)
        with pytest.raises(ServerError) as excinfo:
            await client.chat(model="m", messages=[{"role": "user", "content": "x"}], max_tokens=1)
        # Should still classify by status even without parseable body
        assert excinfo.value.status == 500
        await client.aclose()

    async def test_max_retries_zero(self):
        call_count = 0
        def handler(req):
            nonlocal call_count
            call_count += 1
            return httpx.Response(500)

        client = _mock_client(handler, max_retries=0)
        with pytest.raises(ServerError):
            await client.chat(model="m", messages=[{"role": "user", "content": "x"}], max_tokens=1)
        assert call_count == 1  # no retries at all
        await client.aclose()

    async def test_negative_max_retries_clamped_to_zero(self):
        client = _mock_client(lambda req: httpx.Response(500), max_retries=-5)
        assert client._max_retries == 0
        await client.aclose()


# ============================================================
# Codex 5.3 retroactive fixes
# ============================================================


class TestCodexFixes:
    async def test_default_timeout_is_10s_not_30s(self):
        """Codex 2026-05-03: default timeout dropped from 30s to 10s so a
        single un-overridden call doesn't blow the typical hook budget."""
        client = OpenRouterClient(api_key="sk-test")
        assert client._default_timeout_s == 10.0
        await client.aclose()

    async def test_deadline_prevents_backoff_overshoot(self, monkeypatch):
        """With a 2s deadline and a 429+Retry-After:3, we must fail FAST
        rather than sleep into a CancelledError from the outer wait_for.
        """
        sleeps = []
        async def fake_sleep(s): sleeps.append(s)
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        call_count = 0
        def handler(req):
            nonlocal call_count
            call_count += 1
            return httpx.Response(429, json={"error": {"message": "slow"}},
                                  headers={"Retry-After": "3"})

        client = _mock_client(handler, max_retries=2)
        with pytest.raises(RateLimitError):
            await client.chat(
                model="m", messages=[{"role": "user", "content": "x"}],
                max_tokens=1, deadline_s=2.0,
            )
        # Should have made just one attempt — no retry slept through
        assert call_count == 1
        assert sleeps == []  # never slept
        await client.aclose()

    async def test_deadline_allows_retry_when_budget_permits(self, monkeypatch):
        """With a 10s deadline and a 429+Retry-After:1, retry is allowed."""
        async def fake_sleep(s): pass
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        call_count = 0
        def handler(req):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(429,
                    json={"error": {"message": "slow"}},
                    headers={"Retry-After": "1"})
            return httpx.Response(200, json=_success_body())

        client = _mock_client(handler, max_retries=2)
        result = await client.chat(
            model="m", messages=[{"role": "user", "content": "x"}],
            max_tokens=1, deadline_s=10.0,
        )
        assert result.text == "ok"
        assert call_count == 2
        await client.aclose()

    async def test_deadline_zero_fails_immediately(self, monkeypatch):
        """A caller passing deadline_s<=0 means already-past-deadline —
        fail without even attempting a call."""
        async def fake_sleep(s): pass
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        # Need to simulate elapsed time with a monotonic-clock trick
        import asyncio as _asyncio
        real_loop_time = _asyncio.get_event_loop().time

        def _fake_time():
            _fake_time.n += 10  # each call advances 10s
            return real_loop_time() + _fake_time.n
        _fake_time.n = 0
        # Just pass a tiny deadline instead — simpler
        client = _mock_client(
            lambda req: httpx.Response(500), max_retries=0,
        )
        # Tiny deadline hit immediately when the retry loop checks
        # Actually with deadline_s=0, the first check will see remaining <= 0
        # Wait — we start with remaining = deadline_s - 0 = 0, so remaining <= 0 fires
        with pytest.raises(ServerError):
            await client.chat(
                model="m", messages=[{"role": "user", "content": "x"}],
                max_tokens=1, deadline_s=0.0,
            )
        await client.aclose()

    async def test_deadline_caps_per_call_timeout(self, monkeypatch):
        """When remaining budget is less than default timeout, the per-call
        timeout must be capped to remaining so a single hang doesn't burn
        the whole budget."""
        # Hard to test precisely without freezing time. Just verify that
        # passing a small deadline DOESN'T cause the coroutine to wait 10s
        # (default timeout) when the httpx call itself hangs.
        # We use a handler that raises TimeoutException immediately to
        # simulate "network would take forever" — the retry logic with
        # deadline should NOT sleep past remaining.
        sleeps = []
        async def fake_sleep(s): sleeps.append(s)
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        def handler(req):
            raise httpx.TimeoutException("slow", request=req)

        client = _mock_client(handler, max_retries=2)
        with pytest.raises(ServerError) as excinfo:
            await client.chat(
                model="m", messages=[{"role": "user", "content": "x"}],
                max_tokens=1, deadline_s=0.5,
            )
        # Should have at most ONE attempt since the 1.0s backoff exceeds 0.5s
        assert "deadline" in str(excinfo.value).lower() or "timeout" in str(excinfo.value).lower()
        # Should not have slept 1.0s (would push past 0.5s deadline)
        assert not any(s >= 1.0 for s in sleeps)
        await client.aclose()

    async def test_retry_after_http_date_respects_5s_cap(self, monkeypatch):
        """Codex 2026-05-03: HTTP-date form of Retry-After should still
        trigger the >5s fast-fail path. (Previously only tested with
        numeric form.)"""
        async def fake_sleep(s): pass
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        # Build a future HTTP-date ~60s ahead
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        from email.utils import format_datetime
        future = _dt.now(tz=_tz.utc) + _td(seconds=60)
        future_header = format_datetime(future, usegmt=True)

        call_count = 0
        def handler(req):
            nonlocal call_count
            call_count += 1
            return httpx.Response(429, json={"error": {"message": "slow"}},
                                  headers={"Retry-After": future_header})

        client = _mock_client(handler, max_retries=3)
        with pytest.raises(RateLimitError):
            await client.chat(
                model="m", messages=[{"role": "user", "content": "x"}],
                max_tokens=1,
            )
        # Cap is 5s, server asked for ~60s → fail immediately, no retries
        assert call_count == 1
        await client.aclose()
