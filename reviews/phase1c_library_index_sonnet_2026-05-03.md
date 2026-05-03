# Phase 1C review — library_index

Model: claude-sonnet-4.5
Cost: $0.0577  Latency: 51.5s  8627in/2121out

---

# Codex 5.3 review: `spaice_agent/memory/library_index.py`

---

## Factual errors

**[BLOCKER]** Manual frozen dataclass implementation is **broken**. `LibraryEntry` and `LibraryIndex` use `__slots__` + `object.__setattr__` to simulate frozen behavior, but:
- `__setattr__` is never overridden to block mutation after `__init__`
- Nothing prevents `object.__setattr__(entry, 'title', 'hacked')` post-construction
- Should use `@dataclass(frozen=True, slots=True)` from stdlib (Python 3.10+, we're on 3.11+)

**[MAJOR]** `os.replace()` is **not atomic across filesystems** on all platforms. Framework spec says "atomic write" but doesn't specify same-filesystem constraint. If `tmp_path.with_suffix('.tmp')` creates temp in different mount, replace fails or is non-atomic. Should use `tempfile.NamedTemporaryFile(dir=dash_dir, delete=False)` to guarantee same-directory.

**[MINOR]** `_extract_summary` word-boundary truncation `rsplit(' ', 1)[0]` can return empty string if summary is exactly one word > 200 chars. Edge case but violates "never empty summary" implicit contract.

---

## Architectural weaknesses

**[BLOCKER]** **Incremental rebuild is fundamentally broken**. `_build_entries` reuses cached entries when `abs(existing.mtime - mtime) < 1.0`, but:
- Backlinks are **never recalculated** for cached entries
- If `source.md` adds `[[target]]`, `target.md` won't see the new backlink until its own mtime changes
- Backlink map is built fresh every time but only applied to newly-parsed entries
- **Fix**: Either (a) always rebuild backlinks for all entries, or (b) track backlink dependencies and invalidate transitively

**[MAJOR]** `_scan_backlinks` walks **entire vault** on every `build_library_index` call, even for incremental rebuilds. On a 10k-file vault this is ~2–5s of pure I/O. Framework spec says "incremental rebuild if previous index exists" but only file mtimes are checked, not backlink staleness. Should cache backlink map in index YAML or accept O(vault) cost and document it.

**[MAJOR]** No validation that `vault_root` is actually a vault. If user passes `/tmp`, module happily scans everything and writes `_dashboard/library-index.yaml` into `/tmp/_dashboard/`. Should check for sentinel (e.g., `.vault` marker file) or at minimum log warning when writing outside expected structure.

---

## Incomplete specifications

**[MAJOR]** Framework spec: "Configurable glob if `library/` absent" — not implemented. Code falls back to scanning entire `vault_root` but there's no way to configure the glob pattern. Should accept optional `library_glob: str = "library/**/*.md"` parameter.

**[MINOR]** Framework spec: "Incremental rebuild if previous index exists (mtime comparison)" — implemented for files but not for deletions. If `library/deleted.md` is removed, it stays in the index forever because `_build_entries` only walks existing files. Should diff `existing_entries.keys()` against scanned paths and prune.

**[MINOR]** Spec says "first paragraph = summary (first 200 chars)" but `_extract_summary` stops at first blank line **or** heading, whichever comes first. If file has `# Title\n\nPara 1\n\nPara 2`, only Para 1 is used. Ambiguous spec but current behavior is reasonable; should document.

---

## Risk omissions

**[MAJOR]** **No filesystem error recovery**. If `_scan_backlinks` hits a permission-denied file mid-scan, it logs and skips that file but continues. If `save_library_index` fails mid-write (disk full), `tmp_path` is left behind and never cleaned up. Should wrap in try/finally or use context manager.

**[MAJOR]** **Race condition in incremental rebuild**. If file is modified between `stat()` call (line 147) and `read_text()` call (line 158), mtime check passes but content is stale. Not fixable without file locking, but should document that index is eventually-consistent.

**[MINOR]** `_WIKILINK_PATTERN` doesn't handle escaped brackets `\[\[not a link\]\]`. Obsidian-style vaults use this. Regex should be `(?<!\\)\[\[([^\]|#]+)...` but adds complexity; acceptable to punt if not in reference implementation.

**[MINOR]** No handling of symlinks. If `library/` contains symlink to `../external/`, it's followed and indexed. Could cause duplicate entries or escape vault boundary. Should `follow_symlinks=False` in `rglob()` or document behavior.

---

## Implementation pitfalls

**[MAJOR]** `_parse_frontmatter` assumes frontmatter ends with `\n---` but YAML spec allows `---\n` (no trailing newline before EOF). If file is `---\ntitle: X\n---` (no content after), `text.find('\n---', 4)` returns -1 and frontmatter is ignored. Should check for `\n---\n` **or** `\n---$`.

**[MINOR]** `_extract_summary` uses `errors='replace'` when reading files but `_parse_frontmatter` (via `_build_entries`) also reads with `errors='replace'`. This means invalid UTF-8 becomes `` in YAML, which might break parsing. Should fail-fast on encoding errors or document that vault must be UTF-8 clean.

**[MINOR]** `yaml.safe_load` in `load_library_index` can return `None` if file is empty or contains only `---`. Code checks `isinstance(data, dict)` but logs "unexpected structure" instead of "empty file". Misleading error message.

**[MINOR]** `_entry_path_to_stem` is defined but only used once. Inline it or use `Path(rel_path).stem` directly for clarity.

---

## Test coverage gaps

**[BLOCKER]** No test for **backlink invalidation bug**. Test should:
1. Build index with `a.md` and `b.md` (no links)
2. Modify `b.md` to add `[[a]]` but don't touch `a.md`
3. Rebuild index incrementally
4. Assert `a.md` entry shows `b.md` in backlinks
Currently this **fails** because `a.md` is reused from cache without recalculating backlinks.

**[MAJOR]** No test for **atomic write failure**. Mock `os.replace` to raise `OSError` and verify tmp file is cleaned up (currently it's not).

**[MAJOR]** No test for **deleted files persisting in index**. Create index with 2 files, delete one, rebuild, assert index has 1 entry (currently has 2).

**[MINOR]** No test for frontmatter edge cases: empty frontmatter `---\n---`, no trailing `---`, YAML syntax error. Current code handles these but tests don't verify.

**[MINOR]** No test for `_is_excluded` behavior. Should verify that `library/.obsidian/config.md` is skipped.

---

## Verdict

**NEEDS REVISION** — two blockers prevent safe deployment:

1. **Backlink invalidation bug** (architectural) — incremental rebuild silently produces stale backlinks, violating core contract. This is a data-correctness issue that will cause user-visible errors (dashboards show wrong links, recall misses connections).

2. **Broken frozen dataclass** (factual) — manual immutability implementation doesn't actually prevent mutation. While tests don't catch this, any code doing `object.__setattr__(entry, ...)` will succeed. Use stdlib `@dataclass(frozen=True)`.

**Major issues** (ship-with-fixes acceptable if blockers addressed):
- `os.replace` cross-filesystem atomicity (add same-dir temp)
- Full-vault backlink scan on every build (document O(n) cost or optimize)
- No deletion handling in incremental rebuild (prune stale entries)
- Frontmatter parsing doesn't handle EOF without trailing newline

**Recommendation**: Fix blockers + add regression tests for backlink invalidation and deletion handling. Major issues can ship with TODO comments if timeline is tight, but blockers are data-corruption risks.

**Estimated fix cost**: 2–3 hours (rewrite dataclasses, fix backlink logic, add 3 tests). Re-review after fixes.
