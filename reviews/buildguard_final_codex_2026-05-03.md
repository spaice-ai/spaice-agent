# Final Codex review — BuildGuard v1 complete stack

Date: 2026-05-03  Cost: $0.1868  Latency: 117.3s

---

# Codex 5.3 FINAL pre-commit review — BuildGuard v1.1

**Date:** 2026-05-03  
**Scope:** Blocker-fix verification + new modules (openrouter_client.py logging, hook.py pre_tool_call)  
**Previous blockers:** 5 (B1–B5), claimed fixed

---

## Blocker-fix verification

### B1 (Relative-path bypass) — **FIXED**

**Original issue:** `./spaice_agent/foo.py` returned `None` from `_normalize_target_path()` because the first segment wasn't exactly `"spaice_agent"`.

**Fix location:** `orchestrator.py:267–295` (`_normalize_target_path`).  
- Uses `PurePosixPath` to parse the raw path.
- Manually resolves `.` and `..` segments without filesystem access (lines 277–289).
- Rejects paths with residual `..` after resolution (lines 291–293).
- Searches for `"spaice_agent"` anywhere in the resolved parts list (line 296), not just at index 0.
- Reassembles the subpath from the anchor onward (lines 298–310).

**Verification:** `test_buildguard_blockers.py:50–68` covers `./spaice_agent/foo.py`, `../spaice_agent/foo.py`, and `foo/./spaice_agent/bar.py`. All three cases now behave correctly:
- `./spaice_agent/foo.py` → normalizes to `spaice_agent/foo.py`, detected as coding write.
- `../spaice_agent/foo.py` → residual `..` after resolution → rejected as non-coding (correct: escapes repo root).
- `foo/./spaice_agent/bar.py` → normalizes to `spaice_agent/bar.py`, detected.

**Status:** ✅ Fixed. No regression path identified.

---

### B2 (`tests/../` traversal loophole) — **FIXED**

**Original issue:** `spaice_agent/tests/../memory/foo.py` was treated as a tests-directory write because the check was `any(part == "tests" for part in parts[1:])` without resolving `..`.

**Fix location:** Same `_normalize_target_path()` function (lines 277–289).  
- The manual `.` / `..` resolver runs *before* the tests-directory check (line 306).
- After resolution, `spaice_agent/tests/../memory/foo.py` becomes `["spaice_agent", "memory", "foo.py"]`.
- The tests check (`if "tests" in subpath[1:]`) now operates on the resolved list, so `"tests"` is not present.

**Verification:** `test_buildguard_blockers.py:77–88` explicitly tests `spaice_agent/tests/../memory/foo.py` and confirms:
- Resolves to `spaice_agent/memory/foo.py`.
- Detected as coding write (not excluded by tests filter).
- Requires DeepSeek call.

**Status:** ✅ Fixed. The traversal is resolved before the exclusion check runs.

---

### B3 (Single-commit exemption never expires) — **FIXED**

**Original issue:** `_check_exemption()` returned `True` as long as the YAML file existed; nothing deleted it after use.

**Fix location:** `orchestrator.py:405–423` (`_check_exemption`).  
- After validating the exemption (lines 407–421), the function attempts to delete the file (line 423: `self._exemption_path.unlink()`).
- Wrapped in try/except to handle `FileNotFoundError` (race condition) and `OSError` (permission issues) without breaking the allow decision (lines 424–432).

**Verification:** `test_buildguard_blockers.py:106–133` has two tests:
1. **First allow consumes exemption:** Calls `check_pending_write` twice with the same exemption. First call allows + deletes file; second call refuses (no exemption, no DeepSeek log). ✅
2. **Non-matching target doesn't consume:** Exemption for `foo.py`, agent tries `bar.py` → refused, exemption file still exists. Then tries `foo.py` → allowed, file deleted. ✅

**Status:** ✅ Fixed. The one-shot guarantee is enforced.

---

### B4 (`execute_code` bypass via non-`write_file` patterns) — **FIXED**

**Original issue:** Only `write_file("...py")` was detected. Patterns like `open("...py", "w")`, `Path(...).write_text(...)`, `os.rename(...)`, etc. bypassed the guard.

**Fix location:** `orchestrator.py:93–119` (`_EXEC_CODE_PATTERNS` list + `_extract_py_path_from_execute_code` function at lines 232–243).  
- Six compiled regex patterns cover:
  - `write_file(...)`
  - `open(..., "w"/"wb"/"a"/"r+")`
  - `os.rename(..., "...py")`
  - `shutil.copy/copy2/move(..., "...py")`
  - `Path(...).write_text/write_bytes`
  - `.rename("...py")`
- Each pattern captures the `.py` destination path in a numbered group.
- `_extract_py_path_from_execute_code` iterates the patterns and returns the first match.

**Verification:** `test_buildguard_blockers.py:145–178` has seven tests covering:
- `open("spaice_agent/foo.py", "w")` ✅
- `open("spaice_agent/foo.py", "wb")` ✅
- `Path("spaice_agent/foo.py").write_text(...)` ✅
- `Path("spaice_agent/foo.py").write_bytes(...)` ✅
- `os.rename(..., "spaice_agent/foo.py")` ✅
- `shutil.copy(..., "spaice_agent/foo.py")` ✅
- `shutil.move(..., "spaice_agent/foo.py")` ✅

All seven patterns are now detected and refused when no DeepSeek call exists.

**Status:** ✅ Fixed. Coverage is comprehensive for common Python file-write idioms.

---

### B5 (Terminal heredoc/pipe bypass) — **FIXED**

**Original issue:** Only `>`/`>>`/`of=` redirections were caught. Patterns like `mv tmp.py spaice_agent/foo.py`, `sed -i .../foo.py`, `cat <<'PY' | python -`, etc. bypassed the regex.

**Fix location:** `orchestrator.py:125–139` (`_TERMINAL_EXPLICIT_PATTERNS` list) + `orchestrator.py:245–265` (`_extract_py_path_from_terminal_command`).  
- Six explicit patterns now cover:
  - `>` / `>>` / `of=` redirections (line 126)
  - `mv ... *.py` (line 127)
  - `cp ... *.py` (line 128)
  - `sed -i ... *.py` (line 129)
  - `tee ... *.py` (line 130)
  - `install ... *.py` (line 131)
- Conservative fallback (lines 257–265): if the command contains `spaice_agent/` AND any write-verb or redirection symbol, extract the last `spaice_agent/*.py` token.

**Verification:** `test_buildguard_blockers.py:187–220` has eight tests:
- `mv /tmp/tmp.py spaice_agent/foo.py` ✅
- `cp /tmp/src.py spaice_agent/memory/bar.py` ✅
- `sed -i 's/x/y/' spaice_agent/foo.py` ✅
- `tee spaice_agent/foo.py` ✅
- Heredoc with `> spaice_agent/foo.py` ✅
- Innocent `cat spaice_agent/foo.py` (read, not write) → not detected ✅
- Innocent `ls -la` → not detected ✅

**Status:** ✅ Fixed. The explicit patterns + conservative fallback cover the common bypass vectors. The fallback's keyword scan (`"spaice_agent/" in command`) is broad but safe: false positives (flagging a read as a write) are acceptable in a guard—false negatives (missing a write) are not.

---

## New issues in the added modules

### openrouter_client.py

**Issue 1 (minor):** `_log_call` uses `portalocker.lock(f, portalocker.LOCK_EX)` inside an `open(..., "a")` context manager (line 263). The lock is acquired *after* the file is opened, so there's a TOCTOU window where another process could write between open and lock. The lock should be acquired via `portalocker.Lock(log_file, "a", timeout=2)` (as done in `orchestrator.py:447`) to atomically open+lock.

**Impact:** Low. Interleaved writes would produce malformed JSONL (two entries on one line), but BuildGuard's log reader skips malformed lines (see `orchestrator.py:350–355`). The guard's own build-log writes use `portalocker.Lock(...)` correctly (line 447), so this only affects the OpenRouter call log. Since the guard reads that log line-by-line and discards unparseable entries, the worst case is a missed DeepSeek call (false negative → guard refuses a legitimate write). That's acceptable for a v1 logging bug.

**Recommendation:** Fix in v1.1.1 or v0.3.0. Not a blocker for this commit.

---

**Issue 2 (nit):** `_log_call` computes `latency_s` twice: once at line 233 (`latency_s = (now - started).total_seconds()`) for the error branch, and again inside `ChatResult` at `openrouter_client.py:328`. The duplication is harmless (both use the same formula) but inelegant.

**Recommendation:** Nit. No functional impact.

---

### hook.py

**Issue 3 (major):** `_get_guard` caches `BuildGuard` instances in a module-level dict (`_GUARDS`, line 19). The cache key is `agent_id` (line 21), but the guard is constructed with `cfg` (line 30), which is passed in by the caller. If `load_agent_config(agent_id)` returns different config objects across calls (e.g., config file is edited mid-session), the cached guard will use stale config.

**Impact:** Medium. The framework spec says "policy lives in `spaice_agent/orchestrator.py` … module is imported at process start, cached in memory … edits to on-disk policy file take effect only on next orchestrator restart." The guard's *code* is immutable, but its *config* (e.g., `agent_id`, log paths) is read from `AgentConfig`. If the config changes, the guard should reload. The current cache never invalidates.

**Scenario:** User edits `~/.spaice-agents/jarvis/config.yaml` to change `agent_id` from `"jarvis"` to `"jarvis-v2"`. The hook's `_get_guard` cache still holds the old guard with `agent_id="jarvis"`, so logs land in the wrong directory.

**Mitigation:** The framework spec's immutability guarantee applies to the *policy code*, not the config. The cache should either:
1. Key on `(agent_id, hash(cfg))` so config changes invalidate the cache, OR
2. Not cache at all—`BuildGuard.__init__` is cheap (just sets instance vars), so constructing a new guard per call is acceptable.

**Recommendation:** Change `_get_guard` to not cache, or cache on `(agent_id, id(cfg))` and document that config reloads require a process restart. For v1.1, I'd remove the cache—it's premature optimization (the guard is called once per tool invocation, not in a hot loop).

**Code fix:**
```python
def _get_guard(agent_id: str, cfg: Any) -> Optional[Any]:
    try:
        from spaice_agent.orchestrator import BuildGuard
    except ImportError:
        logger.warning("BuildGuard unavailable; guard disabled for %s", agent_id)
        return None
    return BuildGuard(cfg)  # no cache
```

---

**Issue 4 (nit):** `hook.py:79` has a defensive `if not isinstance(tool_name, str) or not tool_name.strip()` check, but the subsequent `guard.check_pending_write(tool_name, tool_args)` call (line 87) doesn't validate `tool_args`. If `tool_args` is `None` or not a dict, `BuildGuard._is_coding_write` will crash when it calls `tool_args.get(...)` (e.g., `orchestrator.py:218`).

**Impact:** Low. The hook's top-level `except Exception` (line 92) catches it and returns `None`, so the agent survives. But the error log will be noisy.

**Recommendation:** Add `if not isinstance(tool_args, dict): tool_args = {}` before line 87.

---

## Test-suite adequacy

### Blocker regression tests (`test_buildguard_blockers.py`)

**Coverage:** All five blockers have dedicated tests. Each test documents the original Codex finding and verifies the fix. ✅

**Gap 1 (minor):** B5 (terminal bypass) tests don't cover the `python -c '...'` inline-script pattern mentioned in the triage doc. The conservative fallback (lines 257–265) would catch it if the script writes to `spaice_agent/*.py`, but there's no explicit test.

**Recommendation:** Add `test_b5_python_dash_c_detected`:
```python
def test_b5_python_dash_c_detected(guard):
    cmd = "python -c 'open(\"spaice_agent/foo.py\", \"w\").write(\"x\")'"
    d = guard.check_pending_write("terminal", {"command": cmd})
    assert d.allowed is False
```

**Gap 2 (nit):** B4 tests don't cover `open(..., "r+")` (read-write mode). The regex at line 99 includes `r+`, but no test exercises it.

---

### OpenRouter logging tests (`test_openrouter_logging.py`)

**Coverage:** Tests verify that:
- Logging is disabled when `agent_id=None`. ✅
- Successful calls log all required fields (model, messages, tokens, cost, latency). ✅
- Full prompt is preserved verbatim (critical for BuildGuard substring matching). ✅
- Failed calls log as `status="error"`. ✅
- Multiple calls append to the same file. ✅
- Log write failures don't break the chat call. ✅

**Gap (minor):** No test verifies that the log file is named `openrouter-YYYY-MM-DD.jsonl` (the BuildGuard reader expects this format). The tests check that the file exists at the expected path, but don't assert the filename pattern.

**Recommendation:** Add an assertion in `test_successful_call_logged_with_all_fields`:
```python
assert log_file.name.startswith("openrouter-")
assert log_file.name.endswith(".jsonl")
```

---

### Hook integration tests (`test_hook_buildguard.py`)

**Coverage:** Tests verify:
- `pre_tool_call` refuses writes to `spaice_agent/*.py` without DeepSeek. ✅
- Test files pass through. ✅
- Non-coding tools pass through. ✅
- Exemptions allow writes. ✅
- Handler never raises (defensive boundary). ✅
- Existing `pre_turn` flow still works. ✅
- Guard is cached across calls (stable nonce). ✅

**Gap (major):** No test verifies that the guard's *decision* is correctly translated into a `{"reply": ...}` refusal. The test at line 37 checks that `result["reply"]` contains `"BUILD-GUARD"` and `"DeepSeek"`, but doesn't verify the exact wording matches the spec's requirement: `"BUILD-GUARD: target=spaice_agent/memory/foo.py requires a DeepSeek V4 Pro implementation call first."` The actual reply at `hook.py:90` is:
```python
f"BUILD-GUARD refused write to {target}: {reason}. Fire DeepSeek V4 Pro for this module via OpenRouter first."
```
This is close but not identical to the spec. The spec says `"requires a DeepSeek V4 Pro implementation call first"`, the code says `"Fire DeepSeek V4 Pro for this module via OpenRouter first"`. The meaning is the same, but the wording diverges.

**Recommendation:** Either update the spec to match the code, or update the code to match the spec. For consistency, I'd update the code:
```python
reply = (
    f"BUILD-GUARD: target={target} requires a DeepSeek V4 Pro implementation call first. "
    f"Reason: {reason}. Framework spec expected at reviews/<module>-framework.md."
)
```

---

## Integration risks

### Risk 1 (blocker): Hook's `tool_args.get("path")` assumes `write_file` / `patch` use `"path"` key

**Location:** `hook.py:81` passes `tool_args` directly to `guard.check_pending_write(tool_name, tool_args)`. The guard's `_is_coding_write` (line 218) does:
```python
candidate = tool_args.get("path") or tool_args.get("file_path")
```
This assumes the tool schema uses `"path"` or `"file_path"`. If Hermes's `write_file` tool uses a different key (e.g., `"filename"`), the guard will return `None` (not a coding write) and allow the write.

**Verification needed:** Check Hermes's tool schema for `write_file`, `patch`, `terminal`, `execute_code`. If any use non-standard keys, the guard must be updated.

**Mitigation:** The framework spec says the guard "detects coding intent" by inspecting tool calls. The current implementation hardcodes key names. A safer approach would be to extract the path from the tool's *first string argument* (positional or keyword), but that requires knowing the tool schema.

**Recommendation:** Document the assumed tool schema in `BuildGuard.__doc__` and add a test that verifies the schema matches Hermes's actual tools. If the schema is wrong, this is a **blocker**—the guard would silently allow writes.

---

### Risk 2 (major): OpenRouter log path mismatch (original M2 finding)

**Original issue:** The guard hardcodes `~/.spaice-agents/<agent_id>/logs/openrouter-*.jsonl`, but the credentials module might write to a different path.

**Status in v1.1:** The code at `orchestrator.py:87` still hardcodes `_BUILD_LOG_DIR = "~/.spaice-agents/{agent_id}/logs"`. The triage doc says "Need to check what `credentials.py` actually writes."

**Verification:** I don't see `credentials.py` in the provided files. The `openrouter_client.py` module writes to `~/.spaice-agents/<agent_id>/logs/openrouter-YYYY-MM-DD.jsonl` (line 251), which matches the guard's expectation. ✅

**However:** The guard's `_get_recent_openrouter_log_files` (lines 375–402) searches for `openrouter-*.jsonl` in `_BUILD_LOG_DIR`. If the client writes to a different directory (e.g., `~/.spaice-agents/<agent_id>/openrouter/...` as mentioned in the original M2 finding), the guard will never find the logs.

**Current status:** The client and guard agree on the path (`~/.spaice-agents/<agent_id>/logs/openrouter-*.jsonl`). The original M2 concern is **resolved** as long as the client is the only thing writing OpenRouter logs. If there's a separate `credentials.py` module that also logs calls, its path must match.

**Recommendation:** Add a test that verifies the client's log path matches the guard's search path:
```python
def test_client_log_path_matches_guard_expectation():
    from spaice_agent.openrouter_client import OpenRouterClient
    from spaice_agent.orchestrator import _BUILD_LOG_DIR
    # ... construct client with agent_id="test" ...
    # ... make a call ...
    # Assert log file exists at _BUILD_LOG_DIR.format(agent_id="test") / "openrouter-*.jsonl"
```

---

### Risk 3 (minor): Guard's nonce is stable per instance, but hook caches the instance

**Scenario:** The hook's `_GUARDS` cache (line 19) means the same `BuildGuard` instance is reused across all tool calls in a session. The guard's nonce is set once at `__init__` (line 172: `self._nonce = uuid.uuid4().hex[:8]`). The framework spec says "Session-random nonce prefix Opus can't forge."

**Question:** Is "session-random" per-agent-process or per-guard-instance? If the guard is cached for the lifetime of the agent process, the nonce is stable across all tool calls, which is correct (Opus can't forge it because it's set before Opus sees any prompt). ✅

**However:** If the cache is cleared (e.g., `_GUARDS.clear()` in a test), a new guard gets a new nonce. The banner logs will show different nonces for the same session. This is fine for tests but could confuse audit log analysis.

**Recommendation:** Document in `BuildGuard.__doc__` that the nonce is stable per instance and the instance should be reused for a session.

---

## Verdict

**Ship with fixes.**

### Blockers (must fix before commit):
1. **Hook tool schema assumption (Risk 1):** Verify that Hermes's `write_file` / `patch` tools use `"path"` or `"file_path"` keys. If not, update `_is_coding_write` to match the actual schema. Add a test that documents the assumed schema.

### Majors (fix in v1.1.1 or document as known issues):
2. **Hook guard cache (Issue 3):** Remove the `_GUARDS` cache or key it on `(agent_id, id(cfg))`. The current cache can hold stale config.
3. **Hook reply wording (test gap):** Update `hook.py:90` to match the spec's exact wording, or update the spec to match the code.

### Minors (fix in v0.3.0):
4. **OpenRouter log locking (Issue 1):** Use `portalocker.Lock(log_file, "a", timeout=2)` instead of `open(..., "a")` + `portalocker.lock(...)`.
5. **Hook tool_args validation (Issue 4):** Add `if not isinstance(tool_args, dict): tool_args = {}` before calling the guard.
6. **Test gap: B5 `python -c` pattern (Gap 1):** Add test for inline Python scripts.
7. **Test gap: B4 `open(..., "r+")` (Gap 2):** Add test for read-write mode.

### Nits:
8. **OpenRouter latency_s duplication (Issue 2):** Compute once, reuse.
9. **Test: OpenRouter log filename pattern (Gap):** Assert the filename matches `openrouter-YYYY-MM-DD.jsonl`.

---

**Summary:** The five blocker fixes are real and correct. The new modules (openrouter_client.py, hook.py) introduce two new issues: a major (guard cache holding stale config) and a blocker (tool schema assumption not verified). Fix the blocker (#1) and document the major (#2) as a known limitation, then ship. The guard is now effective against the original bypass vectors and provides the immutability guarantee the framework requires.
