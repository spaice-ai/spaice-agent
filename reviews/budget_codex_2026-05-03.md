# Codex 5.3 retroactive review — budget

**Model:** openai/gpt-5-codex  **Cost:** $0.1134  **Latency:** 72.4s  **Tokens:** 18939

---

## Factual errors
None.

## Architectural weaknesses
1. **High** – The cap enforcement is fundamentally racy. `can_fire()` and `increment()` are split, no re-check happens under the lock, so two workers that both observe “29 < 30” will happily increment to 31. That breaks the “daily_fire_cap” guarantee the framework spec depends on.
2. **High** – When `portalocker` times out in `DailyCounter.increment`, you log and return a guessed count without persisting anything. The caller thinks the increment stuck, the ledger never sees it, and the cap is no longer enforced. At minimum this should raise so the tool call can be aborted, or retry until the increment is durably recorded.
3. **Medium** – `Ledger.record` silently drops writes on lock timeout or disk error. That undermines the cost ledger that’s supposed to defend the $40 ceiling; missing entries mean you under-report spend with no signal to the caller.

## Incomplete specifications
1. **Medium** – You added a `BudgetExceeded` sentinel but nothing in this module ever raises it. There’s no provided pathway that actually enforces a daily dollar cap; consumers have to reinvent the logic.

## Risk omissions
1. **Medium** – `datetime.fromisoformat` still explodes on the common `'...Z'` timestamps under Python 3.10, causing you to skip legitimate ledger rows. We’re still running plenty of 3.10 in prod.
2. **Low/Medium** – `DailyCounter` writes via `os.replace`, while readers never take a lock. On SMB/NFS or Windows in general, replacing a file that’s open for read often throws `PermissionError`; you catch and drop it, meaning the increment never lands.

## Implementation pitfalls
1. **Medium** – The best-effort fallback path in `DailyCounter.increment` (after lock failure) derives `new_count` from a stale snapshot and returns it without persisting. On the next successful increment you overwrite the genuine value with the stale one + 1, effectively losing updates.

## Verdict
revise – high-severity defects remain (cap enforcement is broken; persistence can silently fail), plus multiple medium risks that will bite under load.
