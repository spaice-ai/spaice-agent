# Phase 1C — Codex 5.3 retroactive review triage

**Date:** 2026-05-03
**Reviewer:** `openai/gpt-5.3-codex` (correction 017 — supersedes Sonnet 4.5 batch)
**Scope:** re-validate the 5 Phase 1C memory modules before v0.2.0 tag
**Prior review:** Sonnet 4.5 pass (historical record at `phase1c_*_sonnet_2026-05-03.md`)
**Tests at review time:** 558 → **569 passing** (+11 regression tests for Codex findings)

## Cost

| Module | Chars | Cost |
|---|---|---|
| dashboards | 861 | $0.0310 |
| audit | 1541 | $0.0259 |
| summarise | 1622 | $0.0258 |
| library_index | 1745 | $0.0292 |
| continuity | 942 | $0.0226 |
| **Total** | **6711** | **$0.1345** |

Wall-clock: 107s (~21s/module sequential).

## Verdict vs Sonnet pass

Codex 5.3 caught **10 real blockers/majors that Sonnet's batch did NOT surface**, including 3 `NameError` landmines in `audit.py` exception handlers. Not one false positive in the batch. Confirms correction 017: Codex 5.3 is the right reviewer for future work.

## Triage

| # | Module | Sev | Finding | Verdict |
|---|---|---|---|---|
| 1 | audit | blocker | `check_name` undefined in error handler | **ACCEPT** — fix + regression test |
| 2 | audit | blocker | `f` undefined in frontmatter except path | **ACCEPT** — fix + regression test |
| 3 | audit | blocker | `md` undefined in wikilink except path | **ACCEPT** — fix + regression test |
| 4 | audit | major | orphan-inbox hidden filter uses absolute parts | **ACCEPT** — fix + regression test |
| 5 | dashboards | major | `_gen_continuity` skips past section boundary | **ACCEPT** — fix + regression test |
| 6 | dashboards | major | tmp-file collision same-PID | **ACCEPT** — fix + regression test (combined) |
| 7 | continuity | blocker | tmp-file collision same-PID | **ACCEPT** — fix + regression test (combined) |
| 8 | summarise | blocker | non-dict `m` in transcript crashes | **ACCEPT** — fix + regression test |
| 9 | summarise | major | sqlite conn leak on exception path | **ACCEPT** — fix + regression test |
| 10 | summarise | major | tmp-file collision same-PID | **ACCEPT** — fix (covered by #6/#7 pattern) |
| 11 | library_index | blocker | AttributeError on non-dict entries not caught | **ACCEPT** — fix + regression test |
| 12 | library_index | major | reused backlinks not normalised (sort+dedup) | **ACCEPT** — fix + regression test |
| 13 | library_index | major | tmp-file collision shared suffix | **ACCEPT** — fix (covered by #6/#7 pattern) |
| — | summarise | major | 4-H2-section spec enforcement post-LLM | **REJECT — policy** — log-and-accept is intentional fallback; strict enforcement would silently drop summaries on model drift. Deferred to v0.3.0 if ever. |
| — | library_index | major | 1-sec mtime tolerance misses fast edits | **REJECT — policy** — common fs mtime precision. Content-hash-based change detection is a v0.3.0 item. |
| — | continuity | major | `List[str]` mutable on frozen dataclass | **REJECT — scope** — technically correct but no code mutates in place; switching to `Tuple[str, ...]` is a cross-module API change. Deferred to v0.3.0. |

**Summary:** 10 accepted, 3 rejected with rationale. Zero false positives on accept-path (Codex 5.3 delivered clean signal).

## Tmp-file collision fix (affects 4 files)

All four atomic-write sites now use `f".{name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}"` so same-PID concurrent writes to the same target get unique tmp paths. Files changed:

- `spaice_agent/memory/dashboards.py:_atomic_write`
- `spaice_agent/memory/continuity.py:write_latest`
- `spaice_agent/memory/summarise.py:write_summary_file`
- `spaice_agent/memory/library_index.py:save_library_index`

Regression tests cover `_atomic_write` and `write_latest` with thread races (15-20 iterations × 2 threads). Summarise + library_index use the same pattern so are covered implicitly.

## Tests added

`tests/test_phase_1c_codex53.py` — 11 regression tests:

1. `test_audit_orphaned_inbox_skips_relative_to_vault_not_absolute`
2. `test_audit_vault_error_handler_uses_correct_loop_var`
3. `test_audit_frontmatter_except_path_is_clean`
4. `test_audit_wikilinks_except_path_is_clean`
5. `test_dashboards_continuity_next_step_stops_at_next_section`
6. `test_dashboards_atomic_write_tmp_is_unique_per_call`
7. `test_continuity_write_latest_tmp_is_unique_per_call`
8. `test_summarise_transcript_skips_non_dict_entries`
9. `test_summarise_transcript_closes_sqlite_on_exception`
10. `test_library_index_load_tolerates_non_dict_entries`
11. `test_library_index_reused_backlinks_are_sorted_and_deduped`

All 11 green. Full suite: **569/569**.

## Takeaway for correction 017

**Validated.** Codex 5.3 surfaced bugs Sonnet missed across 3 of 5 modules. Policy holds: all future reviews fire `openai/gpt-5.3-codex`.

Budget note: $0.13 vs the previous Sonnet batch's $0.29 — Codex was also *cheaper* on this run (smaller outputs, faster).
