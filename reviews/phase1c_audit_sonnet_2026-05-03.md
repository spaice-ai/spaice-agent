# Phase 1C review — audit

Model: claude-sonnet-4.5
Cost: $0.0636  Latency: 62.9s  8657in/2507out

---

# Codex 5.3 review: `spaice_agent/memory/audit.py`

---

## Factual errors

**[BLOCKER]** `AuditReport.__post_init__` mutates frozen dataclass state via validation check that will **always fail** because `counts` is passed as constructor arg but validation runs before field assignment completes. The `if self.counts != expected` branch is unreachable in normal flow but will raise `AttributeError` if counts dict is mutated externally before validation. **Frozen dataclasses cannot have `__post_init__` that validates constructor args against derived state** — this pattern requires `__new__` or a factory function.

**[MAJOR]** `check_broken_wikilinks` uses `errors="ignore"` when reading files, silently skipping decode errors. This masks UTF-8 issues that should surface as audit findings (e.g., "file contains invalid UTF-8, cannot audit wikilinks"). Framework spec says "never raise on expected failure paths; return empty result + log warn" — decode errors are expected, should produce findings, not silent skip.

**[MINOR]** `_extract_wikilinks` regex `r"\[\[([^\]]+)\]\]"` fails on nested brackets like `[[file [draft]]]` (valid in some Obsidian vaults). Pattern should be non-greedy or handle escapes.

---

## Architectural weaknesses

**[BLOCKER]** No logging infrastructure. Framework spec: "return empty result + log warn" on failures. Module has zero `logging` calls. When `check_broken_wikilinks` hits `OSError`, it silently continues; when `CHECKS` registry catches exceptions, it fabricates an error finding but **never logs the actual exception traceback**. Production debugging will be impossible.

**[MAJOR]** `check_duplicate_files` flags **every occurrence** of a duplicate filename, not just the duplicates themselves. If `note.md` exists in `projects/` and `identity/`, you get **two findings** (one per file) with identical messages. User sees:
```
WARN projects/note.md: Duplicate filename 'note.md' found in multiple shelves: identity, projects
WARN identity/note.md: Duplicate filename 'note.md' found in multiple shelves: identity, projects
```
Should emit **one finding per duplicate set**, not per file.

**[MAJOR]** `check_stale_dashboard` hardcodes dashboard-to-source mapping. When `continuity.py` or `dashboards.py` adds a new dashboard, this check silently ignores it (no coverage). Should either:
- Import `DASHBOARDS` registry from `dashboards.py` and introspect source paths, OR
- Document that this mapping must be manually synced (tech debt).

**[MINOR]** `_build_path_index` returns `stem_map` where "first match wins" on collisions. If `projects/foo.md` and `identity/foo.md` both exist, wikilink `[[foo]]` resolution is **nondeterministic** (depends on `rglob` traversal order, which is filesystem-dependent). Should either:
- Detect ambiguous stems and flag as audit finding, OR
- Use a priority order (e.g., prefer `library/` over `projects/`).

---

## Incomplete specifications

**[MAJOR]** Framework spec: "Wall-clock bounded: audit must complete in <5s on 10k file vault." No timeout enforcement. `check_broken_wikilinks` does `O(files × wikilinks × stems)` work — on a vault with 10k files averaging 10 links each, that's 100k lookups into a dict (fast), but the `_extract_wikilinks` regex scan is `O(content_size)`. A single 50MB markdown file (e.g., exported chat log) will block for seconds. Should:
- Skip files >1MB, OR
- Add per-check timeout with `signal.alarm` (Unix) or thread-based timeout.

**[MINOR]** Framework spec: "Findings sorted by severity (error → warn → info), then path." Implemented via `AuditFinding.__lt__`, but `AuditReport` constructor doesn't enforce this — it assumes caller sorted. If a future check returns pre-sorted findings and another returns unsorted, final report order is undefined. Should call `all_findings.sort()` in `audit_vault` (already done, but not documented as contract).

**[MINOR]** Spec: "Check functions take `vault_root: Path`, return `list[AuditFinding]`." Type hint on `CHECKS` registry is `callable`, should be `Callable[[Path], List[AuditFinding]]` for IDE/mypy support.

---

## Risk omissions

**[MAJOR]** No protection against symlink loops. `_scan_md_files` uses `rglob("*.md")` which follows symlinks by default. A vault with `library/ -> ../library/` will hang. Should use `Path.resolve(strict=False)` and track visited inodes, or pass `follow_symlinks=False` (Python 3.13+, not available in 3.11 target).

**[MAJOR]** `check_orphaned_inbox` uses `st_mtime` without handling filesystem timestamp precision issues. On FAT32 (still used on some USB drives), mtime has 2-second granularity. A file written at `12:00:00.9` and checked at `12:00:01.1` will appear 1 second old, not 0.1 seconds. For 7-day threshold this is negligible, but logic is fragile if threshold ever drops to hours.

**[MINOR]** `check_empty_shelves` excludes `_inbox` and `_archive` but not `_dashboard` or `_continuity`. If user deletes all dashboards, `_dashboard/` will be flagged as empty (probably undesired). Should exclude all `_*` directories or make exclusion list configurable.

**[MINOR]** No handling of case-insensitive filesystems (macOS default, Windows). `stem_map` uses `full.stem` as key, so `Foo.md` and `foo.md` will collide on case-preserving but case-insensitive FS. Wikilink `[[Foo]]` might resolve to `foo.md` unexpectedly.

---

## Implementation pitfalls

**[MAJOR]** `check_missing_frontmatter` only checks if first line is `---`, doesn't validate closing `---` exists. A file starting with `---\ntitle: foo\n<no closing>` will pass. Should scan for closing delimiter or use a YAML parser (expensive but correct).

**[MAJOR]** `_extract_wikilinks` splits on `|` and `#` but doesn't handle escaped characters. Obsidian allows `[[file\|with\|pipes]]` (backslash-escaped). Regex should use a proper parser or at least handle `\|` and `\#`.

**[MINOR]** `check_broken_wikilinks` tries multiple resolution strategies (exact path, stem match, `./` prefix strip) but doesn't document precedence. If `projects/foo.md` exists and user writes `[[./foo]]`, does it resolve? Code says yes (via `clean = target.lstrip("./")`), but this is implicit.

**[MINOR]** `check_stale_dashboard` compares float timestamps (`st_mtime`) directly. On NFS or cloud-synced filesystems, clock skew can cause false positives (dashboard appears older than source due to server time drift). Should allow ~1 second tolerance or use checksums instead of mtime.

**[MINOR]** `AuditFinding.path` is `str`, not `Path`. Inconsistent with rest of codebase (all other modules use `Path`). Should be `Path` or document why string is preferred (serialization?).

---

## Test coverage gaps

**[BLOCKER]** No test for `AuditReport.__post_init__` validation logic. If counts mismatch is ever triggered, it will raise `ValueError` in production, violating "never raise" contract. Test should verify this path is unreachable or remove validation.

**[MAJOR]** No test for exception handling in `audit_vault`. If a check raises (e.g., `PermissionError` on a locked file), does it produce an error finding or crash? Code says it catches and fabricates finding, but no test verifies this.

**[MAJOR]** No test for `check_broken_wikilinks` with:
- Wikilinks containing `#` anchors: `[[file#section]]`
- Wikilinks with aliases: `[[file|display text]]`
- Relative paths: `[[../other/file]]`
- Subdirectory targets: `[[projects/subdir/file]]`

**[MAJOR]** No test for `check_duplicate_files` with >2 duplicates (e.g., `note.md` in 3 shelves). Does it emit 3 findings or 1?

**[MINOR]** No test for hidden directory exclusion. If `vault/.git/README.md` exists, is it scanned? Code says no (checks `part.startswith(".")`), but no test confirms.

**[MINOR]** No test for `check_stale_dashboard` when source directory is empty (should not flag as stale if no source files exist).

---

## Verdict

**SHIP-WITH-FIXES** — blockers must be resolved before production use:

1. **[BLOCKER]** Remove `AuditReport.__post_init__` validation or move to factory function. Frozen dataclass cannot self-validate constructor args.
2. **[BLOCKER]** Add `logging` calls for all exception paths and skipped files. Current silent failures violate framework spec and make debugging impossible.
3. **[MAJOR]** Fix `check_duplicate_files` to emit one finding per duplicate set, not per file.
4. **[MAJOR]** Add symlink loop protection to `_scan_md_files` (track visited paths or use `resolve()`).
5. **[MAJOR]** Change `check_broken_wikilinks` to produce findings (not skip) on UTF-8 decode errors.

Remaining issues are **major** (should fix in Phase 1C cleanup) or **minor** (defer to Phase 2). Module is architecturally sound but has sharp edges that will cause production pain. Estimated fix cost: **30 minutes** for blockers, **2 hours** for majors.
