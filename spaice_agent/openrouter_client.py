"""spaice_agent.openrouter_client — async OpenRouter chat completions client.

Responsibilities:
  * Post to /chat/completions with httpx.AsyncClient
  * Retry on 429/500/502/503/504 up to `max_retries` times
  * Respect `Retry-After` header (numeric seconds or HTTP-date)
  * Cap `Retry-After` at 5s — beyond that, fail the call rather than stall
  * Fail-fast on 400/401/403/404 — these are unrecoverable
  * Propagate asyncio.CancelledError cleanly; httpx handles TCP cleanup

Nothing else. Keep this module narrow; the retry math must be easy to audit.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, ClassVar, Optional

import httpx

__all__ = [
    "OpenRouterClient",
    "ChatResult",
    "OpenRouterError",
    "RateLimitError",
    "AuthError",
    "BadRequestError",
    "ServerError",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class OpenRouterError(Exception):
    """Base exception for OpenRouter client errors."""

    def __init__(
        self,
        message: str,
        *,
        status: Optional[int] = None,
        body: Optional[dict[str, Any]] = None,
        retry_after_seconds: Optional[float] = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.body = body
        self.retry_after_seconds = retry_after_seconds


class RateLimitError(OpenRouterError):
    """HTTP 429 — rate limit hit."""


class AuthError(OpenRouterError):
    """HTTP 401 or 403 — auth missing or rejected. Unrecoverable."""


class BadRequestError(OpenRouterError):
    """HTTP 400 or 404 — bad model / bad payload. Unrecoverable."""


class ServerError(OpenRouterError):
    """HTTP 5xx — server problem. Retryable."""


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChatResult:
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    model: str
    finish_reason: Optional[str]
    latency_s: float


# ---------------------------------------------------------------------------
# Retry-After parsing
# ---------------------------------------------------------------------------


def _parse_retry_after(
    header_value: Optional[str],
    now: Optional[datetime] = None,
) -> Optional[float]:
    """Parse a Retry-After header.

    Returns the number of seconds to wait, or None if unparseable / missing.
    Handles both numeric (int/float) and HTTP-date formats per RFC 7231 §7.1.3.
    Negative deltas are clamped to 0.
    """
    if header_value is None:
        return None
    header_value = header_value.strip()
    if not header_value:
        return None

    # Numeric form (seconds as int OR float)
    try:
        seconds = float(header_value)
        return max(0.0, seconds)
    except ValueError:
        pass

    # HTTP-date form
    try:
        retry_dt = parsedate_to_datetime(header_value)
    except (TypeError, ValueError):
        return None
    if retry_dt is None:
        return None
    # parsedate_to_datetime may return naive datetime; treat as UTC per RFC 7231.
    if retry_dt.tzinfo is None:
        retry_dt = retry_dt.replace(tzinfo=timezone.utc)
    current = now or datetime.now(tz=timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    delta = (retry_dt - current).total_seconds()
    return max(0.0, delta)


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------


def _extract_error_message(body: Any) -> str:
    """Best-effort extraction of a user-friendly message from an error body."""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            msg = err.get("message")
            if isinstance(msg, str) and msg:
                return msg
        if isinstance(body.get("message"), str):
            return body["message"]
    if isinstance(body, str):
        return body
    return ""


def _classify_error(
    status: int,
    body: dict[str, Any],
    retry_after_seconds: Optional[float] = None,
) -> OpenRouterError:
    msg = _extract_error_message(body) or f"HTTP {status}"
    kwargs = {
        "status": status,
        "body": body,
        "retry_after_seconds": retry_after_seconds,
    }
    if status == 429:
        return RateLimitError(msg, **kwargs)
    if status in (401, 403):
        return AuthError(msg, **kwargs)
    if status in (400, 404):
        return BadRequestError(msg, **kwargs)
    if status in (500, 502, 503, 504):
        return ServerError(msg, **kwargs)
    return OpenRouterError(f"HTTP {status}: {msg}", **kwargs)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class OpenRouterClient:
    """Async OpenRouter chat-completions client."""

    BASE_URL: ClassVar[str] = "https://openrouter.ai/api/v1"
    # Retry-After values above this cap result in immediate failure.
    MAX_RETRY_AFTER_S: ClassVar[float] = 5.0

    def __init__(
        self,
        api_key: str,
        *,
        referer: str = "https://spaice.local/agent",
        title: str = "SPAICE Agent Framework",
        timeout_s: float = 30.0,
        max_retries: int = 2,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self._api_key = api_key
        self._default_timeout_s = timeout_s
        self._max_retries = max(0, max_retries)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": referer,
            "X-Title": title,
        }
        # Allow injection of a pre-built client for tests that use mock transport
        self._client = client or httpx.AsyncClient(headers=headers, timeout=timeout_s)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "OpenRouterClient":
        return self

    async def __aexit__(self, *a: Any) -> None:
        await self.aclose()

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float = 0.2,
        timeout_s: Optional[float] = None,
    ) -> ChatResult:
        """POST /chat/completions with retry + Retry-After handling."""
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        call_timeout = timeout_s if timeout_s is not None else self._default_timeout_s

        started = datetime.now(tz=timezone.utc)
        attempt = 0  # number of retries performed

        while True:
            try:
                response = await self._client.post(
                    f"{self.BASE_URL}/chat/completions",
                    json=payload,
                    timeout=call_timeout,
                )
            except asyncio.CancelledError:
                raise
            except httpx.TimeoutException as e:
                # Treat httpx timeout like a transient error
                if attempt >= self._max_retries:
                    raise ServerError(f"timeout: {e}") from e
                sleep_for = (attempt + 1) * 1.0
                logger.warning(
                    "OpenRouter timeout, retrying in %.1fs (attempt %d/%d)",
                    sleep_for, attempt + 1, self._max_retries,
                )
                await asyncio.sleep(sleep_for)
                attempt += 1
                continue
            except httpx.HTTPError as e:
                # Transport error (DNS, connection refused, reset)
                if attempt >= self._max_retries:
                    raise ServerError(f"transport error: {e}") from e
                sleep_for = (attempt + 1) * 1.0
                logger.warning(
                    "OpenRouter transport error, retrying in %.1fs: %s",
                    sleep_for, e,
                )
                await asyncio.sleep(sleep_for)
                attempt += 1
                continue

            # HTTP response received
            if response.status_code == 200:
                return _parse_success(response, model, started)

            # Error response
            body = _safe_json(response)
            retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            error = _classify_error(response.status_code, body, retry_after)

            if isinstance(error, (BadRequestError, AuthError)):
                raise error  # unrecoverable

            if retry_after is not None and retry_after > self.MAX_RETRY_AFTER_S:
                # Server asking for too long — fail immediately
                raise error

            if attempt >= self._max_retries:
                raise error

            sleep_for = retry_after if retry_after is not None else (attempt + 1) * 1.0
            logger.warning(
                "OpenRouter %s, retrying in %.1fs (attempt %d/%d)",
                type(error).__name__, sleep_for, attempt + 1, self._max_retries,
            )
            await asyncio.sleep(sleep_for)
            attempt += 1


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    """Parse response body as JSON, return empty dict on failure."""
    if not response.content:
        return {}
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _parse_success(
    response: httpx.Response,
    requested_model: str,
    started: datetime,
) -> ChatResult:
    """Convert a 200 response to a ChatResult."""
    data = _safe_json(response)
    choices = data.get("choices", [])
    if not choices:
        raise OpenRouterError(
            "OpenRouter returned no choices", status=200, body=data,
        )
    message = choices[0].get("message", {})
    text = message.get("content") or ""
    finish_reason = choices[0].get("finish_reason")
    model_echo = data.get("model", requested_model)

    usage = data.get("usage", {})
    # OpenRouter uses "prompt_tokens" and "completion_tokens" but we expose
    # them as input/output in ChatResult for clarity.
    input_tokens = int(usage.get("prompt_tokens", 0) or 0)
    output_tokens = int(usage.get("completion_tokens", 0) or 0)
    cost_usd = float(usage.get("cost", 0.0) or 0.0)

    latency_s = (datetime.now(tz=timezone.utc) - started).total_seconds()
    return ChatResult(
        text=text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        model=model_echo,
        finish_reason=finish_reason,
        latency_s=latency_s,
    )
