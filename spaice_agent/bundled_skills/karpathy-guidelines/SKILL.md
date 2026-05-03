---
name: karpathy-guidelines
description: Code-quality guardrails for reducing common LLM coding mistakes — overcomplication, scope creep from adjacent-code "improvements", speculative abstractions, and weak success criteria. Load for any non-trivial coding or refactor task. Source — Andrej Karpathy's LLM coding pitfall observations via forrestchang/andrej-karpathy-skills (MIT).
license: MIT
---

# Karpathy Guidelines — code-quality guardrails for coding tasks

Four rules. Bias: caution over speed. For trivial edits (one-line typo fixes, renames), use judgement; these rules are for any change ≥ a single commit's worth of work.

Load this skill **in addition to** the Operating Doctrine in SOUL.md. SOUL governs *whether* to fire a skill; this skill governs *how to write the code once you are coding*.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

- State assumptions explicitly. If uncertain, ask — don't guess silently.
- If multiple interpretations exist, present them. Don't pick one and hope.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it before committing.

Smell-test: *"Would a senior engineer call this overcomplicated?"* If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, **mention it** — don't delete it.

When your changes create orphans:

- Remove imports/variables/functions that YOUR change made unused.
- Don't remove pre-existing dead code unless asked.

The test: every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Rewrite weak tasks into verifiable goals:

- "Add validation" → "Write tests for invalid inputs, then make them pass."
- "Fix the bug" → "Write a test that reproduces it, then make it pass."
- "Refactor X" → "Ensure the existing tests pass before and after."

For multi-step tasks, state the plan internally (or in a `todo`) as:

```
1. [step] → verify: [check]
2. [step] → verify: [check]
```

Strong criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

## Signals this skill is working

- Fewer unnecessary changes in diffs.
- Fewer rewrites caused by overcomplication.
- Clarifying questions come **before** implementation, not after the mistake.
- `git diff --stat` looks small and on-target.

## Examples

See `references/examples.md` for concrete before/after patterns across all four rules (load with `skill_view('karpathy-guidelines', 'references/examples.md')` when you need them — ~500 lines, don't load by default).

## Attribution

Derived from [Andrej Karpathy's tweet on LLM coding pitfalls](https://x.com/karpathy/status/2015883857489522876) via [forrestchang/andrej-karpathy-skills](https://github.com/forrestchang/andrej-karpathy-skills). Licensed MIT.
