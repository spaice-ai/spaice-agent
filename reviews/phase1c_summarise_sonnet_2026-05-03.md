# Phase 1C review — summarise

Model: claude-sonnet-4.5
Cost: $0.0545  Latency: 48.1s  7682in/2097out

---

# Code Review: `spaice_agent/memory/summarise.py`

## Factual errors

1. **Blocking I/O in async context** (blocker): `_load_session_transcript` performs synchronous `sqlite3.connect()` and blocking I/O without `asyncio.to_thread()`. This will block the event loop during database reads, defeating the purpose of async functions.

2. **Incorrect cost attribution** (major): `OpenRouterClient.chat()` returns `ChatResult` with `cost_usd`, but the framework spec says "Returns cost_usd from OpenRouter response" — you're trusting the client's cost calculation without validation. If `ChatResult.cost_usd` is `None` or missing, this will crash.

3. **Word count after truncation is wrong** (minor): Line 174 counts words *after* appending `" ..."`, so the count includes the ellipsis as a word. Should count before appending.

## Architectural weaknesses

1. **No session DB path validation** (major): The code checks `hasattr(cfg.memory, "session_db_path")` but never validates the path exists or is readable before attempting `sqlite3.connect()`. Silent failure returns empty transcript, producing `TRIVIAL` summaries for real sessions.

2. **Hardcoded fallback path** (major): `Path.home() / ".hermes" / "sessions.db"` is a magic constant that bypasses configuration. If Hermes moves or uses a different DB name, this silently fails. Should be in `AgentConfig` with no fallback.

3. **No retry logic for transient LLM failures** (major): Network hiccups, rate limits, or transient 5xx errors from OpenRouter will produce fallback summaries. The framework says "cheap LLM" but doesn't specify idempotency — retrying once would catch 90% of transient failures.

4. **Transcript truncation loses context** (major): Keeping the *tail* of a 40k+ char transcript discards the session goal (usually stated early). Better strategy: keep first 5k chars (goal) + last 30k chars (recent work).

## Incomplete specifications

1. **Missing atomic write for DB reads** (blocker): Framework says "Atomic file writes (`.tmp` + `os.replace`)" but `_load_session_transcript` reads from SQLite without any transaction isolation. If Hermes is writing to the DB concurrently, you may read a partial/corrupt `messages` JSON blob.

2. **No logging of cost** (minor): Framework says "Returns cost_usd from OpenRouter response" but never logs it. Operators have no visibility into cumulative summarisation costs.

3. **No handling of TRIVIAL detection** (minor): System prompt says "If the session is trivial (no real work), output exactly: TRIVIAL" but the code doesn't distinguish between LLM-returned `TRIVIAL` and empty-transcript `TRIVIAL`. Downstream consumers (miner) can't tell the difference.

4. **Missing word count enforcement in prompt** (minor): System prompt says "under 500 words" but doesn't tell the LLM this is a hard limit. Post-hoc truncation mid-sentence breaks markdown structure (e.g., unclosed lists).

## Risk omissions

1. **Race condition on session file write** (blocker): Two concurrent calls to `summarise_session("abc123", cfg)` will both write to the same `.tmp` file, causing data corruption or lost writes. Need file locking or unique tmp names (e.g., `filename + f".tmp.{os.getpid()}.{id(summary)}")`).

2. **No handling of malformed session JSON** (major): `json.loads(messages_json)` can raise `JSONDecodeError` (caught), but if `messages` is valid JSON but wrong schema (e.g., `messages` is a dict not a list), iteration on line 95 will crash.

3. **Regex denial-of-service risk** (minor): Line 117–120 use `re.search()` on user-controlled content without `re.MULTILINE` anchor validation. Pathological input like `"x" * 100000 + "[IMPORTANT:"` could cause catastrophic backtracking (unlikely with these simple patterns, but worth noting).

4. **No disk space checks** (minor): Writing to `_archive/sessions/` can fail silently if disk is full. `os.replace()` will raise `OSError`, but it's not caught — the exception will propagate and leave a `.tmp` file behind.

## Implementation pitfalls

1. **Unsafe session_id in filename** (major): Line 152 uses `re.sub(r"[^a-zA-Z0-9_-]", "_", summary.session_id)` but doesn't handle empty `session_id` (from `summarise_from_text`). This creates filenames like `2026-05-03-.md`, which are ambiguous and collide.

2. **Timezone-naive datetime comparison** (minor): `datetime.now(timezone.utc)` is correct, but if Hermes stores timestamps in local time, date extraction on line 175 may be off by a day near midnight.

3. **Content block handling is fragile** (minor): Lines 102–109 handle Anthropic-style content blocks, but the logic assumes `block.get("type") == "text"` is the only valid type. If Hermes adds image blocks or tool-use blocks, they'll be silently skipped (correct) but logged nowhere (incorrect).

4. **No validation of summary structure** (minor): Framework says "Prompt produces headings: ## Goal, ## Key decisions, ## Outstanding threads, ## Artefacts" but the code never validates the LLM output contains these sections. Malformed output (e.g., LLM ignores prompt) will be written as-is.

## Test coverage gaps

1. **No test for concurrent writes** (blocker): The race condition on `.tmp` files is untested. Two threads calling `summarise_session` with the same `session_id` will corrupt each other's output.

2. **No test for DB read during Hermes write** (major): If Hermes is mid-transaction when `_load_session_transcript` runs, the test suite won't catch the resulting `TRIVIAL` summary.

3. **No test for malformed JSON in DB** (major): Tests don't cover `messages` being valid JSON but wrong schema (e.g., `{"messages": "not a list"}`).

4. **No test for empty session_id** (major): `summarise_from_text` returns `session_id=""`, but there's no test verifying the filename generation handles this gracefully (it doesn't — see pitfall #1).

5. **No test for cost_usd=None** (major): If `OpenRouterClient` returns `ChatResult(cost_usd=None)`, the code will crash on line 177. Tests assume `cost_usd` is always a float.

6. **No test for >500 word LLM output** (minor): Tests don't verify truncation logic or that the ellipsis is appended correctly.

7. **No test for missing API key** (minor): `_get_api_key` logs a warning but returns `""`, which causes `summarise_from_text` to return a fallback. Tests don't verify this path.

---

## Verdict

**Needs revision** — multiple **blockers** must be fixed before shipping:

1. **Blocker**: Wrap `_load_session_transcript` in `asyncio.to_thread()` to avoid blocking the event loop.
2. **Blocker**: Fix race condition on `.tmp` file writes (use unique tmp names or file locking).
3. **Blocker**: Handle `session_id=""` in filename generation (either reject or use a placeholder like `"adhoc"`).
4. **Blocker**: Validate `ChatResult.cost_usd` is not `None` before accessing.

**Major** issues to address:
- Validate session DB path exists before attempting connection.
- Remove hardcoded `~/.hermes/sessions.db` fallback (require explicit config).
- Add retry logic for transient LLM failures (1 retry with exponential backoff).
- Improve transcript truncation strategy (keep head + tail, not just tail).
- Validate `messages` is a list after `json.loads()`.

**Minor** improvements:
- Fix word count to exclude ellipsis.
- Log `cost_usd` after each summarisation.
- Add disk space error handling around `os.replace()`.
- Validate LLM output contains required H2 sections (or log warning).

**Estimated fix cost**: ~$0.10 (one DeepSeek revision pass + regression tests). Do not ship until blockers are resolved — the async I/O bug and race condition will cause silent data loss in production.
