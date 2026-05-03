# Phase 1C Codex review — triage

Total: 10 blockers + 19 majors + minors across 5 modules.
Review cost: $0.2914 (Sonnet 4.5, 5 modules parallel).

## Blocker verdicts (MUST fix before commit)

| # | Module | Blocker | Fix |
|---|---|---|---|
| 1 | dashboards | Naive `dt.datetime.now()` in `_human_ago` — will crash on tz-aware ts | `dt.datetime.now(dt.timezone.utc)` |
| 2 | dashboards | Race on atomic write cross-fs | `tempfile.NamedTemporaryFile(dir=target.parent, delete=False)` |
| 3 | audit | `AuditReport.__post_init__` can't validate — frozen dataclass | Remove __post_init__, validate in factory fn |
| 4 | audit | Silent exception swallowing | Add `logger.warning` on every catch |
| 5 | summarise | Sync SQLite call inside async fn — blocks event loop | `await asyncio.to_thread(_load_session_transcript, ...)` |
| 6 | summarise | `.tmp` race — concurrent writes collide | PID-suffix: `.tmp-{os.getpid()}` |
| 7 | summarise | `session_id=""` produces `.md` with empty stem | Reject empty, or use `"adhoc"` placeholder |
| 8 | summarise | `ChatResult.cost_usd` could be None | `cost = result.cost_usd or 0.0` |
| 9 | library_index | Backlink invalidation on incremental rebuild — stale data | Full backlink scan when any file changes (doc the O(n)) |
| 10 | library_index | Custom frozen impl doesn't actually prevent mutation | Use `@dataclass(frozen=True)` stdlib |
| 11 | continuity | Race on `write_latest` tmp file | PID-suffix tmp name |
| 12 | continuity | ALREADY FIXED by me in test-driven iteration (H1 break bug) | — |

(Continuity has 1 blocker left, not 2 — one was the H1-break I already patched.)

## Fix strategy

All 11 remaining blockers are mechanical — no architectural rework. Estimated 30 min manual patch.
Per build-stack skill: Jarvis is allowed to apply fixes directly after DeepSeek + Codex, with regression tests. That's exactly this step.

## Majors (deferred unless cheap)

Apply ONLY if the fix is one-liner:
- dashboards: symlink traversal (add `follow_symlinks=False`), encoding try/except wrap
- audit: symlink loop protection (`visited: set[Path]`), duplicate-files emit one finding per set
- summarise: retry on transient LLM failures (1 retry + backoff)
- library_index: same-dir atomic write temp
- continuity: 7-day stale-session threshold

## Defer to v0.3.0

Everything else (minors, nits, "would be nice to have" majors). Noted in the reviews.

## Regression tests required (per skill — every blocker gets one)

- test_human_ago_aware_ts (dashboards #1)
- test_audit_report_accepts_valid_fields_without_post_init (audit #3)
- test_audit_logs_all_skipped_files (audit #4)
- test_summarise_uses_asyncio_to_thread (summarise #5 — mock sqlite)
- test_summarise_concurrent_writes (summarise #6)
- test_summarise_empty_session_id (summarise #7)
- test_summarise_null_cost (summarise #8)
- test_library_backlinks_refresh_on_change (library_index #9)
- test_library_entry_is_truly_frozen (library_index #10)
- test_continuity_concurrent_write (continuity #11)
