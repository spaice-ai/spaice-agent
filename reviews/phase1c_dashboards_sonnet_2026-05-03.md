# Phase 1C review — dashboards

Model: claude-sonnet-4.5
Cost: $0.0563  Latency: 44.1s  9167in/1923out

---

# Codex 5.3 review: `spaice_agent/memory/dashboards.py`

---

## Factual errors

1. **`_human_ago` unused** — Function defined (lines 95–104) but never called. Dead code.
2. **`dt.datetime.now()` without timezone** (line 97) — Compares naive datetime to potentially aware `ts` parameter. Will raise `TypeError` if `ts` has tzinfo.
3. **Frontmatter parsing fragile** — `_parse_frontmatter` assumes first `---` is frontmatter start, but Markdown files can have `---` in prose. Should verify it's at line 0.

---

## Architectural weaknesses

1. **Generator exceptions swallowed silently** — `regenerate_one` catches all exceptions, emits warning, returns `ok=False`. Caller has no structured error info (no exception type, traceback). Debugging production failures will be painful.
   
2. **No incremental rebuild** — Framework spec mentions "Incremental rebuild if previous index exists (mtime comparison)" for `library_index.py`, but dashboards always regenerate from scratch. For large vaults (10k files), this violates the audit.py "wall-clock bounded: <5s" constraint when applied to dashboards.

3. **Hardcoded paths** — `_gen_library` reads `_dashboard/library-index.yaml`, but if `library_index.py` hasn't run yet, returns empty rows silently. No validation that dependency exists.

4. **Wikilink generation inconsistent** — Some generators use `[[path|title]]`, others `[[path|stem]]`. No normalization. Obsidian/Logseq may render differently.

---

## Incomplete specifications

1. **Missing "never raise" contract enforcement** — Spec says "Generator that fails must return empty rows + log warning; never raise". Code catches exceptions in `regenerate_one`, but individual generators (`_gen_*`) can still raise (e.g., `yaml.safe_load` in `_gen_library` line 269). Should wrap each generator call.

2. **Timestamp format not validated** — Spec requires "UTC ISO 8601". Code uses `.isoformat()` which is correct, but doesn't enforce `timezone.utc` in all datetime objects. `_gen_continuity` uses `fromtimestamp()` without `tz=timezone.utc` (line 123) — produces naive datetime.

3. **Dashboard output format underspecified** — Spec says "formats as markdown table" but doesn't mandate column order. `_render_table` uses `dict.keys()` order (insertion order in Py3.7+), but this is implicit. Should sort keys or use OrderedDict.

4. **No validation of row dict uniformity** — `_render_table` assumes all dicts have same keys (line 301). If generator returns `[{"a": 1}, {"b": 2}]`, table will have misaligned columns. Should validate or pad missing keys.

---

## Risk omissions

1. **Race condition in atomic write** — `_atomic_write` uses `os.replace()`, which is atomic on POSIX but **not** on Windows if target is open by another process (e.g., Obsidian indexer). Should use `tempfile.NamedTemporaryFile(delete=False)` in same directory to guarantee same filesystem.

2. **No file size limits** — `_gen_projects` reads all `projects/**/*.md` into memory. A 100MB markdown file will OOM. Should add size check or streaming parse.

3. **Regex DoS in `_gen_corrections`** — Line 243: `re.search(r"^status:\s*(\w+)", text, re.MULTILINE)` on untrusted input. Catastrophic backtracking unlikely here, but no input size limit.

4. **Symlink traversal** — `glob("**/*.md")` follows symlinks by default. Circular symlinks will hang. Should use `Path.rglob()` or check `is_symlink()`.

5. **Encoding assumptions** — All `read_text()` calls use `encoding="utf-8"`. If vault contains latin-1 files, will raise `UnicodeDecodeError`. Should catch and skip with warning.

---

## Implementation pitfalls

1. **Frontmatter YAML bomb** — `yaml.safe_load()` is safe from code execution but not resource exhaustion. A 1GB YAML file will hang. Should add size limit before parsing.

2. **Wikilink escaping incomplete** — `_render_table` escapes `|` in cell values (line 306), but doesn't escape `[`, `]`, which break Markdown links. Should escape all Markdown special chars.

3. **Mtime precision loss** — `fromtimestamp().isoformat()` loses sub-second precision on some filesystems (FAT32). Not critical but inconsistent with "UTC ISO 8601" claim.

4. **Empty table edge case** — If `rows=[]`, `_render_table` returns `""` (line 298). Dashboard file will have header comment but no table. Should add "No entries" placeholder.

5. **Numeric prefix regex too permissive** — Line 237: `re.match(r"^(\d+)-", file.stem)` matches `000-`, `99999-`. Should validate range (e.g., `\d{1,4}`).

---

## Test coverage gaps

1. **No test for atomic write failure** — If disk full or permissions denied, `_atomic_write` raises but tmp file may leak. Test should verify cleanup.

2. **No test for malformed frontmatter** — What if YAML has tabs, null bytes, or `---` inside a code block? `_parse_frontmatter` returns `{}` silently; should test.

3. **No test for concurrent regeneration** — Two processes calling `regenerate_all()` simultaneously could corrupt output files. Should test with `multiprocessing`.

4. **No test for large vault** — Spec mentions 10k files. Smoke tests use <10 files. Should benchmark `regenerate_all()` on synthetic 1k-file vault.

5. **No test for missing `_dashboard/` directory** — If `vault_root/_dashboard` doesn't exist, `regenerate_one` creates it (line 318), but test doesn't verify.

6. **No test for non-UTF8 files** — Should test that `UnicodeDecodeError` is caught and logged, not propagated.

7. **No test for symlink loops** — Create `a -> b`, `b -> a` in `projects/`, verify doesn't hang.

---

## Verdict

**Ship with fixes.** Module is 85% production-ready but has **2 blockers** and **4 majors**:

**Blockers:**
1. **Naive datetime comparison in `_human_ago`** — Will crash if `ts` is timezone-aware. Fix: `dt.datetime.now(dt.timezone.utc)`.
2. **Race condition in atomic write on Windows** — Use `tempfile.NamedTemporaryFile` in same dir as target.

**Majors:**
1. **No file size limits** — Add 10MB cap on `read_text()` calls to prevent OOM.
2. **Symlink traversal** — Check `is_symlink()` or use `follow_symlinks=False` in glob.
3. **Encoding error propagation** — Wrap `read_text()` in try/except, log warning, skip file.
4. **Generator exception swallowing loses context** — Return structured error (exception type + message) in `DashboardResult`.

**Minors:** Dead code (`_human_ago`), wikilink escaping, empty table placeholder, frontmatter validation.

**Nits:** Column order, numeric prefix regex tightening.

**Estimated fix cost:** 30 minutes + 15 minutes regression tests. Low risk; changes are localized. Recommend fixing blockers before merge, majors in follow-up PR same sprint.
