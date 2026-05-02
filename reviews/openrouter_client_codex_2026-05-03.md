# Codex 5.3 retroactive review — openrouter_client

**Model:** openai/gpt-5-codex  **Cost:** $0.2555  **Latency:** 167.4s  **Tokens:** 34508

---

## Factual errors
None found.

## Architectural weaknesses
- High: The retry loop is oblivious to the stage budgets (3 s / 3 s / 2 s). A 429 with `Retry-After: 2` followed by the built-in 1 s/2 s linear backoff will routinely overrun the stage window even though you capped Retry-After at 5 s. The outer `asyncio.wait_for` will slam the coroutine with `CancelledError`, so the hook fails without ever surfacing a structured `OpenRouterError`. The client needs awareness of the remaining allowance or an escape hatch to bail instead of sleeping when the caller’s deadline can’t tolerate another retry.

## Incomplete specifications
- Low: Tests only cover numeric `Retry-After` ≥ MAX. The HTTP-date form is untested, so the “fail immediately when > 5 s” behavior isn’t defended by a regression test.

## Risk omissions
- Medium: Default `timeout_s` is 30 s, far above any hook budget. If a caller forgets to override it, a single OpenRouter call can sit on the event loop for half the session window before the outer watchdog cancels. That’s exactly the “hook-induced gateway hang” metric we’re trying to drive to zero.

## Implementation pitfalls
None beyond the architectural deadline gap noted above.

## Verdict
Ship-with-fixes: one high-severity deadline bug (retry loop ignoring outer budget) and one medium risk (overlong default timeout) need fixing before release.
