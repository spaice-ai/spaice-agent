# Code Review: spaice_agent/memory/mine.py

VERDICT: needs revision

FINDINGS:

1. [severity=major] State file race condition in concurrent execution
   The state file load-modify-save sequence (lines 252-268, 270-275) is not atomic. If two miner instances run concurrently (e.g., manual invocation overlapping with cron), they can both load the same state, process different sessions, and the second write will clobber the first's updates, losing tracking data.

2. [severity=major] Session file size comparison is insufficient for incremental processing
   Line 166 compares `size_now <= prev_size` to skip already-processed sessions. If a session file is appended to (new turns added), the miner will re-extract ALL utterances from the file (line 171), not just new ones. This violates the "idempotent" claim and will cause duplicate classifications/inbox entries on subsequent runs.

3. [severity=major] Missing validation of classification target before writing
   `_write_inbox_draft()` (lines 237-256) writes inbox drafts regardless of whether `classification.target_file` points to a valid/existing shelf file. The spec says triage will escalate missing targets, but the miner should validate or at least log when classifier returns a non-existent target to avoid silent data loss if triage fails.

4. [severity=minor] Inconsistent error handling for state corruption
   Lines 262-268 catch state file corruption and back up the corrupt file, but the backup operation itself (line 265) can fail silently (bare `except OSError: pass`). If the backup fails, the corrupt state is deleted without preservation, losing debugging information.

5. [severity=minor] `fact_patterns` config extension not implemented
   The spec says "users can extend via `memory.mine.fact_patterns` in config (list of regexes)" but `MineConfig.from_config_dict()` (lines 88-113) never reads or applies this config key. The `fact_patterns` field exists on `Miner` but is always initialized to `DEFAULT_FACT_PATTERNS`.

6. [severity=minor] Misleading variable name `size_now` used before stat check
   Line 164 assigns `prev_size` from state, then line 166 tries to stat the file. If stat fails (line 168), the loop continues without updating state, but the variable name `size_now` (line 166) implies the current size was successfully retrieved when it might not have been.

7. [severity=minor] JSON content handling loses structure
   Line 221 converts list-type message content to JSON string (`json.dumps(content)`), which will produce a serialized array string like `'["text", "image"]'` instead of extracting the actual text content. This likely breaks fact pattern matching for multimodal messages.
