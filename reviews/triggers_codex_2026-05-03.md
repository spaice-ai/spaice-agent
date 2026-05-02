# Codex 5.3 retroactive review — triggers

**Model:** openai/gpt-5-codex  **Cost:** $0.2262  **Latency:** 150.4s  **Tokens:** 29327

---

## Factual errors
None spotted in this slice.

## Architectural weaknesses
- `_COMPILED` is keyed only by `agent_id`. If we hot-reload or swap a config object for the same agent inside the running process (exactly what the framework’s config cache allows), the trigger set never updates — we keep using the first compiled regex bundle forever. That violates the expectation that editing YAML flips behaviour without a process restart. Severity: medium.

## Incomplete specifications
- Test suite never exercises config reload / mutated trigger sets, so the stale-cache issue above sailed through. No fence-code or short-command coverage either, which hides the bugs below.

## Risk omissions
- We assume every regex in the YAML compiles. A typo in ops land raises `re.error` inside `_get_compiled`, crashes the hook, and blocks the whole turn. There’s no guard/log/fallback path. Severity: medium.

## Implementation pitfalls
- Hard length guards stop the whole feature. `consensus_triggered` bails out for `len(message) < 20`, so “review this”, “audit logs”, “plan rollout” — all explicit triggers — never fire consensus. That’s a show-stopper: real users write short imperatives. Same story for search (`len < 10` kills “google it” / “find sku”). Severity: high.
- `_strip_excluded_regions` collapses fenced code only if the fence contents have zero backticks. The moment a code block includes a literal backtick (e.g. template strings, Markdown examples), the regex closes early, leaves the rest of the fence (including `plan/decide/audit`) in place, and consensus fires on pure code. That’s an expensive false positive that will happen in real conversations. Severity: high.

## Verdict
Revise. High-severity issues remain (short-command triggers silently disabled, fenced-code false positives), plus medium-severity cache/validation problems. Fix before shipping.
