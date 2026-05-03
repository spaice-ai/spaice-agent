# BuildGuard — Codex findings triage

Review: `reviews/buildguard_codex_2026-05-03.md`
Cost: $0.1057

## Blockers — all ACCEPT

### B1. Relative-path bypass (`./spaice_agent/foo.py`)
**Verdict:** Accept — real bug. `_normalize_target_path` rejects non-exact-prefix paths.
**Fix:** Use `Path.resolve()` + find `spaice_agent` anchor anywhere in parts, not just index 0.
**Regression test:** add case for `./spaice_agent/foo.py`, `../spaice_agent/foo.py`, `foo/./spaice_agent/bar.py`.

### B2. `tests/../` path traversal loophole
**Verdict:** Accept — real bug. `any(part == "tests" ...)` without resolving `..` wrongly excludes `spaice_agent/tests/../memory/foo.py`.
**Fix:** `Path.resolve()` before tests-check; reject any path containing `..` after normalisation.
**Regression test:** `spaice_agent/tests/../memory/foo.py` must resolve to `spaice_agent/memory/foo.py` and REQUIRE DeepSeek.

### B3. Single-commit exemption never expires
**Verdict:** Accept — real bug. Exemption file persists indefinitely; spec says single-commit.
**Fix:** Two options considered:
  (a) Track a `consumed_at` timestamp inside the file, reject if present. Writer sets it after first allow.
  (b) Delete the file atomically on first ALLOW decision for that target.
**Chosen:** (b) — delete on first allow. Simpler, matches spec "auto-delete". Wrap in try/except so log-write failures don't deny the legitimate write.
**Regression test:** fire `check_pending_write` twice with same exemption — first allows, second refuses (file gone).

### B4. `execute_code` bypass via non-`write_file` patterns
**Verdict:** Accept — real bug. Only `write_file("...py")` regex is checked. `open("...py","w").write(x)`, `pathlib.Path("...py").write_text(x)`, `os.rename`, etc. all bypass.
**Fix:** Broaden regex to catch: `write_file`, `open(..., "w")`/`"wb"`/`"a"`/`"r+"`, `Path(...).write_text/write_bytes`, `os.rename`, `shutil.copy`/`move`, `Path(...).rename`.
**Regression test:** each bypass pattern triggers detection.

### B5. Terminal heredoc/pipe bypass
**Verdict:** Accept — real bug. `cat <<'PY' | python -`, `mv tmp.py spaice_agent/foo.py`, `sed -i .../spaice_agent/foo.py`, `python -c '...'` all bypass.
**Fix:** Broaden `_TERMINAL_WRITE_RE` to catch:
  - `mv`/`cp` with `.py` destination
  - `sed -i ... .py`
  - Any occurrence of `.py` path as argument after specific write-capable commands
  - Pipe-to-python-heredoc patterns
**Conservative fallback:** if command contains `spaice_agent/*.py` AND any of `>`, `>>`, `mv`, `cp`, `sed`, `tee`, `python`, `install` → treat as coding write.
**Regression test:** each pattern triggers.

## Majors — ACCEPT

### M1. `..` in paths neither normalised nor rejected
**Verdict:** Accept. Folded into B1/B2 fix (use `Path.resolve()` consistently).

### M2. Log-source path hardcoded wrong
**Verdict:** Accept — partial. Need to check what `credentials.py` actually writes.

## Minors — JUDGE

### N1. Fragile substring match (known-bypass, already documented)
**Verdict:** Reject for v1 (documented). v2 diff-match is already the v0.3.0 roadmap.

### N2. Windows platform-dependence
**Verdict:** Reject — framework spec explicitly says "macOS + Linux". Document `pathlib.PurePosixPath` normalisation if we want belt-and-braces, but not shipping.

### N3. Global `Path.expanduser` monkeypatch in tests
**Verdict:** Accept — test hygiene. Switch to using `HOME` env var or a fixture that monkey-patches only the guard's own internal path constants.

### N4. Concurrency on build-log writes
**Verdict:** Accept — cheap fix. `portalocker` already in deps. Add a file-lock around the JSONL append.

### N5. Regex DoS on `_TERMINAL_WRITE_RE`
**Verdict:** Accept. Simplify after fixing B5 — use `re.compile` with `re.IGNORECASE` on a clearer pattern without nested alternation.

## Summary of fixes

- `_normalize_target_path` → use `Path.resolve()`, find `spaice_agent` anchor in resolved parts, reject `..` residue
- `_is_coding_write` terminal → broader pattern covering mv/cp/sed/pipe-heredoc
- `_is_coding_write` execute_code → broader pattern covering open/Path.write_text/rename
- `_check_exemption` → atomic delete on first allow
- Credential log path → verify against actual credentials.py location and fix if wrong
- Tests → switch off global Path.expanduser monkeypatch, add regression cases for all 5 blockers

Estimated delta: ~100-150 LOC module changes, ~200 LOC test additions.
