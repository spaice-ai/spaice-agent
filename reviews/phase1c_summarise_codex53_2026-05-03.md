VERDICT: needs revision  
FINDINGS:
1. [severity=blocker] `_load_session_transcript` can crash on valid-but-unexpected JSON message shapes.
   In `_load_session_transcript`, the loop does `for m in messages: role = m.get(...)` without checking `m` is a dict.  
   If `messages` contains strings/numbers (still valid JSON), this raises `AttributeError` and bubbles out of `summarise_session`, causing runtime failure instead of graceful fallback.

2. [severity=major] SQLite connection is not safely closed on all exception paths.
   `_load_session_transcript` opens `conn = sqlite3.connect(...)` and closes it only in some branches.  
   If an exception occurs after connect but before explicit `conn.close()` (e.g., bad query/schema), the `except` returns without closing, causing connection/resource leakage over repeated calls.

3. [severity=major] Temp-file naming is still collision-prone for concurrent writes in the same process.
   `_write_summary_file` uses `.{filename}.tmp-{pid}`. Two async tasks in one process writing the same summary target will use the same tmp path.  
   That can lead to race conditions (overwrites or intermittent `os.replace` failures), so concurrency safety is incomplete.

4. [severity=blocker] Required summary format from spec is not enforced post-LLM.
   The prompt requires exactly four H2 sections (`Goal`, `Key decisions`, `Outstanding threads`, `Artefacts`) or `TRIVIAL`.  
   `summarise_from_text` accepts and persists arbitrary `result.text` (only truncating word count), so non-conforming model output can violate the contract and corrupt downstream continuity expectations.
