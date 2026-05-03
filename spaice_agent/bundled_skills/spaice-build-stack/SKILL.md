---
name: spaice-build-stack
description: |
  MANDATORY coding workflow for ALL production code changes — no
  threshold, no exceptions. Opus (Jarvis) writes the framework/spec;
  DeepSeek V4 Pro implements code ≥200 LOC via OpenRouter; direct
  edits for mechanical wiring skip DeepSeek but still require Codex
  5.3 review. Jarvis writes tests and integrates. Nothing ships
  without adversarial review under BuildGuard v1. Load on ANY turn
  that involves writing, implementing, adding, building, coding, or
  shipping a module, function, class, handler, service, adapter,
  client, script, patch, or test. Corrections 008, 009 (2026-05-03).
version: 1.0.0
author: Jarvis
metadata:
  hermes:
    tags: [build, coding, review, openrouter, workflow, mandatory]
    related_skills:
      - spaice-orchestration-stack
      - requesting-code-review
      - test-driven-development
      - writing-plans
---

# Build Stack — Default Coding Workflow

**MANDATORY for any production code.   No exceptions.** The pipeline is immutable:
Opus framework → DeepSeek V4 Pro implementation → Jarvis tests → Codex 5.3
review. Every step, every time. No skips, no shortcuts, no "direct-typed"
escape hatches. If the pipeline cannot run, coding does not happen.

Run silently — don't announce the stack to the user, just use it.

Per correction 009 (2026-05-03): bypass of this pipeline cost ~$300 in a single session
in one session. The escape clauses that made bypass possible have been
REMOVED from this skill. DeepSeek V4 Pro is not optional.

See correction 008 (`~/jarvis/corrections/008-default-coding-stack.md`)
for the origin and full rationale. See correction 009 for why bypass is
no longer permitted. This skill is the operating guide.

## Step 0 — Preflight: verify the build harness exists

Before firing any LLM call, confirm the local infrastructure the stack
depends on actually exists. A prior-session context-summary CAN lie
about files that were in flight — treat it as hearsay until proven.

Run at the start of any multi-module session:

```bash
# Required paths
ls ~/.Hermes/credentials/openrouter.key   # Build-stack credential (0600)
ls ~/jarvis/scripts/call_codex.py         # Review wrapper
ls ~/jarvis/scripts/consensus_panel.py    # Consensus wrapper (if used)
mkdir -p ~/jarvis/_scratch                # Scratch dir for critiques
```

If ANY is missing, fix before firing a single LLM call. Common cases:
- Credential missing → check `~/.Hermes/credentials/` (canonical store) or `~/.hermes/.env` for API keys; if genuinely absent, flag to the user via the `credentials` module's migration pattern (write to
  `~/.Hermes/credentials/<slug>.key`, chmod 0600)
- `call_codex.py` missing → write it with the content-fallback block
  from the "Wrapper must handle content=None" section below
- `_scratch/` missing → `mkdir -p`, nothing else needed

Silent failure mode this prevents: firing 5 parallel Codex reviews only
to have all 5 fail with `TypeError: 'NoneType' + str` because the wrapper
from last session doesn't actually exist, and you just spent 2 minutes
wall-clock learning that.

### Step 0b — Verify framework-assumed dependencies exist in code

Before firing DeepSeek, trace every dependency the framework spec NAMES
to actual code on disk. Frameworks frequently assume infrastructure that
was planned but never built.

**Trigger conditions — do this check when the spec claims:**
- "Module X logs Y to path Z" → grep X for writes to Z
- "The Q module emits event R" → grep Q for R emission
- "Credentials are read from path P" → confirm P exists OR confirm the
  reader module handles missing-P gracefully
- "The hook fires at stage S" → grep for stage-S invocation of the hook
- ANY "existing X already does Y" claim → verify by reading X

**How to check:**

```bash
# For each dependency the spec names:
# 1. Does the module exist?
ls ~/Developer/<project>/spaice_agent/<module>.py

# 2. Does the claimed behaviour exist inside?
search_files(pattern="<behaviour-keyword>",
             path="~/Developer/<project>/spaice_agent/<module>.py")

# 3. If the claimed behaviour is writing a file, does the target
#    directory exist or get created?
search_files(pattern="<target-path-fragment>|mkdir|makedirs",
             path="~/Developer/<project>/spaice_agent/<module>.py")
```

**If ANY assumed dependency is missing, do ONE of these before DeepSeek fires:**

1. **Expand scope** — add the missing dependency as a prerequisite module
   in the pipeline (new cost estimate, new fire). This is what tonight's
   BuildGuard session should have done when the spec assumed a
   non-existent OpenRouter call log.

2. **Revise framework** — rewrite the spec so it doesn't depend on the
   missing piece (e.g. switch from "read call log" to "deny-by-default
   with exemption-only").

3. **Surface to the user** — if the dependency gap changes the fundamental
   value prop of the module, stop and ask which of the above he wants.

**Do NOT silently fire DeepSeek hoping Codex will catch it.** Codex
tonight caught the downstream blockers (5 of them) but the SCOPE-REVEAL
(no log exists anywhere) only surfaced after the review, adding ~$0.14
to the cycle and requiring plan revision. The check-before-fire is
30 seconds; catching it in Codex review is $0.10+ and a retry.

**Case file — BuildGuard cycle:**

Framework spec: "BuildGuard checks the credentials module's OpenRouter
call log for a recent DeepSeek invocation against the target path."

Actual state of the codebase at spec-time:
- `credentials.py` — key reader only, no call logging
- `openrouter_client.py` — has `logger.warning` calls but no JSONL append
- No log file written by any module, anywhere

Result: DeepSeek implemented a log-checker against a file that nobody
writes. Codex caught 5 blockers in the checker's logic; I then had to
discover separately that the log itself doesn't exist, turning a
$0.25-planned module into a $0.45 scope-revealed cycle.

30 seconds of pre-fire grep for `openrouter-.*\.jsonl` would have caught
this before DeepSeek ran. That's the check this step exists for.

## The four roles

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. OPUS (Jarvis) — FRAMEWORK                                    │
│    - Write plan / spec / public API                             │
│    - Pass through Codex critique if scope >3 days BEFORE coding │
│    - Define contracts each module must honour                   │
└──────────────────────────┬──────────────────────────────────────┘
                           │ spec + context
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. DEEPSEEK V4 PRO — CODE IMPLEMENTATION                        │
│    Slug: deepseek/deepseek-v4-pro ($0.43/$0.87 per M)           │
│    - Called via OpenRouter API (key in ~/.hermes/.env)          │
│    - Temperature 0.1, NO max_tokens cap                         │
│    - Prompt = the module spec + any relevant existing modules   │
│    - Watch for reasoning-mode spill (scratchpad not code)       │
└──────────────────────────┬──────────────────────────────────────┘
                           │ Python source
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. JARVIS — READ, TEST, FIX                                     │
│    - Read the module carefully; flag bugs directly              │
│    - Write pytest suite (DeepSeek does NOT write tests)         │
│    - Fix any DeepSeek bugs you catch                            │
│    - Run pytest → all green REQUIRED before next step           │
└──────────────────────────┬──────────────────────────────────────┘
                           │ module + tests (green)
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. CODEX 5.3 — ADVERSARIAL REVIEW                               │
│    Slug: openai/gpt-5.3-codex ($1.75/$14 per M)                 │
│    - Gets ORIGINAL FRAMEWORK CONTEXT (whole plan goes in)       │
│    - Gets the module source                                     │
│    - Gets Jarvis's test suite                                   │
│    - Structured critique: factual errors / arch weaknesses /    │
│      incomplete specs / risk omissions / impl pitfalls / verdict│
│    - Jarvis applies all LEGITIMATE hits (judgement call on      │
│      borderline ones — flag to the user if ambiguous)              │
└──────────────────────────┬──────────────────────────────────────┘
                           │ fixes applied, tests still green
                           ▼
                        COMMIT
```

## Detection — when to load this skill

Load automatically when ANY of these signals appear in the turn:

**Verb signals:**
- "write", "implement", "code", "build", "add", "create", "develop", "ship"
- Applied to: module, function, class, handler, service, adapter, client,
  endpoint, hook, plugin, script, pipeline stage, middleware

**Artifact signals:**
- User mentions a specific `.py` file to be created or substantially modified
- User references a spec / plan / framework that implies code output
- User asks for a new component of an in-flight project

**Scope signals:**
- Any work inside `~/Developer/<repo>/`, any local `spaice-*` checkout,
  or any repo under the project's GitHub organisation
- Bug fix touching >50 LOC
- Any change to concurrency, credentials, security, or external
  protocol handling (HTTP retry, auth, file locking, rate limiting)

**Anti-signals (DON'T load):**
- User asks a quick question (skill tax > benefit)
- Throwaway exploratory script
- Tiny config / fixture edits
- Documentation-only changes

If any signal matches, this skill loads and the stack runs. No announcement.

## Step 1 — Framework (Opus)

Before any code is written, Jarvis produces:

1. **Module interface spec** — public API (functions, classes, exceptions,
   return types). Frozen dataclasses preferred for return types.
2. **Behaviour contracts** — what each function must do, edge cases,
   error semantics.
3. **Dependency boundaries** — what this module imports, what it doesn't
   import (esp. "no LLM judgement inside" constraints).
4. **Integration points** — how it plugs into the existing framework.

For multi-week scope OR any framework that proposes NEW ENFORCEMENT /
POLICY / PROCESS infrastructure (gates, hooks, pipelines, audit systems):
**pass the plan through Codex critique BEFORE coding, and iterate until the
framework holds up.** See `.hermes/plans/*-codex-critique.md` patterns from
2026-05-02/03 (first successful cycle) and 2026-05-03 build-ledger (first
two-iteration cycle).

### Framework-critique loop (proven 2026-05-03)

When the framework proposes a new mechanism, don't assume v1 survives contact
with an adversarial reviewer. Budget 2-3 Codex critique rounds:

```
Jarvis writes framework v1
    ↓
Codex critique v1     ← ~$0.04, 50s
    ↓
Verdict: needs rework?
    ├─ YES → pivot architecture (don't patch; the failure is usually
    │        the architectural premise, not details) → v2
    │             ↓
    │        Codex critique v2 ← ~$0.04
    │             ↓
    │        [loop until verdict = ship-as-spec or revise-spec]
    │
    └─ NO  → proceed to DeepSeek (step 2)
```

**The 2026-05-03 build-ledger case proved why this matters:**

- v1 was a "gate" that depended on Hermes intercepting write_file calls.
  Codex caught: Hermes has no interception layer, so the gate is decorative.
- v2 pivoted to a "ledger" (voluntary log + dashboard). Codex caught:
  voluntary logging is a diary — same failure mode, just prettier.
- v3 pivoted to a **commit hook** (mechanical tripwire inside git).
  Codex verdict: "ship-with-fixes" — the architecture was finally right,
  only implementation details needed adjustment.

Total framework-iteration cost: $0.08 (two critique rounds). Cost of shipping
v1 or v2 then discovering they don't enforce anything: days of drift + loss
of user trust. **Always cheaper to iterate the framework than iterate the
shipped code.**

### When the pivot is architectural, not editorial

If Codex verdict is "needs rework" (not "revise-spec"), **do not patch v1
in place**. Start v2 as a fresh document that absorbs the critique as a
premise. Name v2 explicitly (`build-gate-framework-v2.md`) and include a
"Supersedes + why" block pointing at the critique. This forces the premise
shift to be visible rather than buried in a diff.

### The "mechanical tripwire" diagnostic

Before firing the v1 critique, sanity-check the framework yourself with
this one question: **"What physical mechanism forces this rule to fire,
and does that mechanism exist in our stack today?"**

A rule that depends on Hermes intercepting `write_file`, on the agent
remembering to call a log function, or on the user enforcing a pre-flight
announcement — all fail this test. They are decorative, not enforced.

Mechanical tripwires that DO exist:
- Git hooks (pre-commit, post-commit, post-merge) — fire on every commit
- Systemd timers / launchctl / cron — fire on schedule
- File-system watchers (fswatch, inotify) — fire on disk events
- Shell wrappers intercepting specific commands
- Network-layer proxies intercepting API calls

If the framework can't name which of these (or a comparable primitive)
enforces the rule, the framework is a diary, not a gate — and Codex will
catch it. Save the round-trip by pivoting before v1 ships.

**Case file — build-ledger cycle:**
v1: "pre-flight gate" depending on Hermes write_file interception
    → Codex: mechanism doesn't exist, gate is decorative.
v2: "self-audit ledger" depending on agent logging each write
    → Codex: voluntary log is a diary, same failure mode.
v3: "commit hook" parsing `git diff --cached` and blocking commits
    missing a `Reviewed-by:` trailer
    → Codex: ship-with-fixes. Git commits are a real tripwire.

Two critique rounds ($0.08) to reach the enforceable architecture.
Asking "what's the tripwire?" before v1 would have saved one round.

### Context injection for each round

**v1 critique prompt:** framework v1 + correction-008 + build-stack skill.

**v2 critique prompt:** framework v1 + v1-critique + framework v2 (current).
The critic must see its own prior critique to judge whether v2 actually
addressed the original failure mode rather than just rearranged surface.

**DeepSeek implementation prompt (final round):** final framework spec +
correction-008 + ALL prior critiques (v1, v2, ...). DeepSeek learns from
the failed architectures too — avoids repeating their assumptions in code.

**Final Codex review prompt (step 4):** framework spec + module source +
tests + prior critiques still relevant. Reviewer sees the full design
history, not just the final plan.

## Step 2 — Code (DeepSeek V4 Pro)

### Invocation pattern

```python
import json, urllib.request, pathlib, time

key = [l.split("=",1)[1].strip().strip('"').strip("'")
       for l in (pathlib.Path.home()/".hermes/.env").read_text().splitlines()
       if l.startswith("OPENROUTER_API_KEY=")][0]

# Include ALL relevant framework context — other modules the new one imports
context_modules = (pathlib.Path.home()/"Developer/spaice-agent/spaice_agent/config.py").read_text()

task = """Write `spaice_agent/<module>.py` — <one-line purpose>.

## Functional requirements
... (numbered, concrete)

## Public API
```python
from spaice_agent.<module> import (A, B, C)
```

## Rules / constraints
... (settled decisions — avoid open questions)

## Implementation notes
... (guide on subtleties, DO NOT leave them to figure out)

## Required deliverables
Complete `<module>.py`. Return ONLY Python source. No fences, no prose.

## Context — other modules this imports from
""" + context_modules

body = {
    "model": "deepseek/deepseek-v4-pro",
    "messages": [{"role": "user", "content": task}],
    "temperature": 0.1,  # consistent output
    # NO max_tokens — let it finish naturally
}
req = urllib.request.Request(
    "https://openrouter.ai/api/v1/chat/completions",
    data=json.dumps(body).encode(),
    headers={"Authorization": f"Bearer {key}",
             "Content-Type": "application/json",
             "HTTP-Referer": "https://example.local",
             "X-Title": "build <module>.py"})
t0 = time.time()
with urllib.request.urlopen(req, timeout=600) as r:
    resp = json.loads(r.read())
elapsed = time.time() - t0
print(f"Cost: ${resp['usage']['cost']:.4f}  Latency: {elapsed:.1f}s")

stripped = resp["choices"][0]["message"]["content"].strip()
if stripped.startswith("```"):
    lines = stripped.split("\n")
    stripped = "\n".join(
        lines[1:-1] if lines[-1].strip().startswith("```") else lines[1:])
(pathlib.Path.home()/f"Developer/spaice-agent/spaice_agent/<module>.py"
 ).write_text(stripped)
```

### Reasoning-spill detection

After receiving the output, check the first few lines. If you see:
- "Edge cases:", "Implementation skeleton:", "Let me think...", numbered
  analysis paragraphs at the top of the file
- No `from __future__ import annotations` or `import` statement in the
  first 10 lines
- Lines that aren't valid Python (prose paragraphs)

...DeepSeek spilled into reasoning mode. Options:
1. Re-prompt with a tighter "Return ONLY Python source" instruction
2. Use the spilled output as a design document and write the module
   directly yourself (the spec is already clear at this point)

Option 2 was faster in `openrouter_client.py` — don't waste another
call when the spec is already crystal clear in the prompt.

## Step 3 — Jarvis reads, tests, fixes

### Read with intent

Look specifically for:
- File-doesn't-exist preconditions (e.g. `open(path, "r+")` without
  creating the file first)
- Nested lock acquisition on the same file handle
- Atomic-replace operations locking the file being replaced
- `asyncio.to_thread` paired with `wait_for` — thread keeps running on
  cancellation, that's by design, make sure callers handle it
- Off-by-one in retry loops
- Regex patterns with escape-sequence issues in raw strings
- Missing `\b` word boundaries in regex
- Type mismatches at boundaries (e.g. `re.Pattern` where `str` expected)

### Test coverage requirements

Every module gets tests for:
- Happy path (typical inputs)
- Boundary cases (empty, single item, max size)
- Invalid inputs (each rejection mechanism)
- Concurrency (if any state is shared — use threading to prove)
- Isolation (if multi-tenant — prove tenants don't bleed into each other)
- Error paths (each exception type raised correctly)

### Green = required

Don't proceed to Codex review until `pytest` is green. Codex reviewing
broken code is noise; it can't judge correctness without the ground
truth of passing tests.

## Step 4 — Codex 5.3 review

### Invocation pattern

```python
module_source = (pathlib.Path.home()/"Developer/spaice-agent/spaice_agent/<module>.py").read_text()
test_source   = (pathlib.Path.home()/"Developer/spaice-agent/tests/test_<module>.py").read_text()
framework_plan = (pathlib.Path.home()/"jarvis/.hermes/plans/<latest-framework-plan>.md").read_text()

prompt = f"""You are Codex 5.3, senior staff engineer reviewing a module
that was just shipped (DeepSeek wrote the code, Jarvis wrote the tests).
Your job: find everything that's still wrong. Be ruthless.

The module is part of a framework — here's the original plan for grounding:

<framework_plan>
{framework_plan}
</framework_plan>

<module_source file="spaice_agent/<module>.py">
{module_source}
</module_source>

<test_source file="tests/test_<module>.py">
{test_source}
</test_source>

Structure your critique:

## Factual errors
(Things that don't match the framework spec, or are wrong about Python,
OpenRouter, httpx, portalocker, etc.)

## Architectural weaknesses
(Design issues that pass tests but will fail in production.)

## Incomplete specifications
(Contracts not fully implemented, edge cases the tests don't cover.)

## Risk omissions
(Failure modes not handled: concurrency, network, filesystem, auth.)

## Implementation pitfalls
(Concrete Python traps: async leaks, regex edge cases, lock semantics,
encoding issues, platform differences.)

## Verdict
One paragraph: ship as-is, ship with fixes, or needs substantial revision.
Rank severity of remaining issues.
"""

body = {
    "model": "openai/gpt-5.3-codex",
    "messages": [{"role": "user", "content": prompt}],
    "temperature": 0.2,
}
# ... same OpenRouter call pattern as step 2
```

### What to do with Codex's findings

- **Factual errors** → fix immediately, no exceptions
- **Architectural weaknesses** → judgement call; fix if prod impact
  clear, flag to the user if ambiguous
- **Incomplete specs** → fix if contract violated; ignore if
  aspirational ("could also handle X" that's not in the plan)
- **Risk omissions** → fix if listed as a real failure mode; ignore
  hypotheticals with no trigger condition
- **Implementation pitfalls** → fix all concrete ones (encoding,
  locking, async); push back on overblown ones with reasoning

Save the critique to disk alongside the module:
`~/Developer/<repo>/reviews/<module>_codex_<date>.md`.
It becomes audit evidence and the basis for follow-up.

### Finding triage — the accept/reject matrix

Codex finds real bugs AND also raises false positives from context it
didn't see (e.g. reviewing `recall.py` in isolation, not realising
`paths.py` already defines the symbol it flags as undefined). Before
applying any fix, triage each finding with one of four verdicts:

| Verdict | When | Action |
|---|---|---|
| **Accept — real bug** | Codex found an actual correctness issue in your code | Fix + write regression test (mandatory for blocker/major) |
| **Accept — defensive hardening** | Codex raised a valid concern about input validation / edge case not exercised by tests | Fix + add the missing test |
| **Reject — context gap** | Codex didn't see the file/symbol/pattern that resolves the concern | Record rationale in the review doc; do NOT delete the finding |
| **Reject — disagree on policy** | Codex wants stricter validation / different architecture than the spec | Record rationale with reference to the spec |

**The rationale is mandatory for rejects.** Write it in the review doc
under the finding before moving on. The audit trail prevents you from
re-arguing the same finding in the next review cycle and proves that
the rejection was deliberate, not lazy.

**Typical false-positive rate:** 40-60% on modules reviewed in
isolation (without full repo context). This is normal — don't lower
Codex's strictness, just keep triaging. The 40-60% that survive triage
are usually real wins.

**Python-semantics verification before rejecting a "factual" finding.**
Reviewers (especially Sonnet in reasoning-compressed mode) occasionally
claim something about Python semantics that sounds authoritative but is
wrong. Before accepting a "factual error" finding that involves language
semantics (dataclass behaviour, async semantics, import mechanics,
descriptor protocol, metaclass interactions, generator exhaustion), run
a **10-line verification** against live Python:

```bash
python -c "
# Reviewer claimed: frozen dataclass cannot have __post_init__
from dataclasses import dataclass
@dataclass(frozen=True)
class X:
    a: int
    def __post_init__(self):
        if self.a < 0: raise ValueError()
X(a=1)     # ok
try: X(a=-1)
except ValueError: print('validation works — reviewer claim is WRONG')
"
```

If the verification contradicts the reviewer, **reject with rationale +
the verification snippet in the triage doc**. Cost: 10 seconds. Saves
you from:
- Removing legitimate validation code because the reviewer said "frozen
  can't self-validate" (it can — `__post_init__` runs on frozen dataclasses)
- Reworking `async def` functions because the reviewer said an `await`
  on a non-coroutine would deadlock (only matters if it's actually a
  coroutine, the reviewer conflated mocks with real coroutines)
- Replacing `@dataclass(frozen=True)` with manual `object.__setattr__`
  patterns because the reviewer confused "frozen" with "immutable bytes"

Record the rationale in the triage doc like:

```md
### Blocker #3 — REJECTED (Sonnet false positive)
**Claim:** `AuditReport.__post_init__` can't validate — frozen dataclass
constructor can't self-validate.
**Verification:** Live Python confirms `__post_init__` DOES run on
frozen dataclass instances and can raise. Only assignment inside the
frozen instance is blocked.
**Verdict:** rejected — the validate-and-raise pattern is correct as
shipped.
```

This is the ONE triage move that's purely Jarvis's call — the reviewer
is wrong about the language, not about your code. Don't defer to the
reviewer's confidence; defer to the interpreter.

**Case file — Phase 1A (spaice-agent memory modules):**
16 findings across 3 modules (paths/capture/recall). Triage result:
- 6 accepted (3 real behaviour bugs + 3 defensive hardening)
- 10 rejected (7 context gaps, 3 policy disagreements with spec)
- Total review cost: $0.08. Real bugs caught: hyphenated SKU match
  broken by `\b`, mid-doc `---` mistaken for frontmatter, `:` in
  YAML scalar not properly quoted. All three would have shipped
  unnoticed without the review.

**Case file — Phase 1C Codex 5.3 re-review (post-correction-017):**
Same 5 modules re-reviewed with `openai/gpt-5.3-codex` (versioned slug)
to validate correction 017 against the earlier Sonnet pass. 13 findings
surfaced; triage:
- 10 ACCEPTED (3 NameError landmines in audit.py exception handlers
  referencing undefined `check_name`/`f`/`md`, tmp-file collisions in
  4 modules, section-boundary bleed in dashboards, non-dict defensiveness
  in summarise + library_index, sqlite conn leak, reused-backlink
  normalisation)
- 3 REJECTED with written rationale (post-LLM format enforcement is
  intentional fallback; 1-sec mtime tolerance is fs precision not a
  bug; frozen-dataclass List[] is cross-module API change deferred)
- **Zero false positives on the accept path** — every accepted finding
  was a real bug verified by the test suite's failure behaviour before
  the fix landed
- 11 regression tests written, full suite 569/569
- Cost $0.1345 total (5 modules × Codex 5.3, 107s sequential,
  $0.023-0.031/module). CHEAPER than the Sonnet batch ($0.29) it replaced
  AND higher-quality output. Correction 017 validated in field.

Pattern validated twice: Codex reviews turn into a test-generator
for the edge cases you didn't think of. The accept-path findings
almost always map 1-to-1 to a missing regression test.

**Case file — Phase 1C five-module cycle (Sonnet 4.5, superseded):**
5 modules through DeepSeek → Sonnet 4.5 review → triage → fix cycle.
Review surfaced 10 blockers + 19 majors across the 5 modules; triage
verdict:
- 10 blockers ACCEPTED and fixed (datetime tz, atomic-write race,
  concurrent tmp collision, broader sqlite→thread wrap, path-traversal
  in frozen dataclass impl, stale backlinks on incremental rebuild)
- 1 blocker REJECTED via Python-semantics verification (reviewer
  claimed `__post_init__` incompatible with `frozen=True` — live-python
  check confirmed it works; rationale recorded in triage doc)
- Majors deferred to v0.3.0 (documented per-review in `reviews/`)
- 12 regression tests written, one per accepted blocker
- Total review cost: $0.29 (5 modules parallel via Sonnet 4.5)
- Fix cycle: 30 min mechanical patches, no re-invocation of DeepSeek

Pattern validated: even after fixing 5 blocker SEV issues, the test
suite gained 12 targeted regression guards that couldn't have been
written upfront. The review step surfaces the exact edge cases that
belong in the test suite — use it as a test-generator, not just a
code-reviewer.

### Post-fix regression guard — MANDATORY for blocker-class findings

When Codex flags a **blocker** that existing tests didn't catch, the
fix is NOT complete until you add a test that would have caught it.
Lesson from 2026-05-03 FW-1 cycle:

- `orchestrator.py` referenced undefined `_increment_turns_since_call`
  on line 202 (should have been `advance_suppression_counter`).
- **248/248 tests passed** because no test exercised the
  counter-advance path — it runs on every real hook turn.
- Codex caught it on first pass via static reading.
- Shipping the fix without a regression test leaves the same coverage
  hole: the next rename/refactor could reintroduce the same class of
  bug.

**Rule:** for every Codex blocker finding, write (or extend) a test
that fails BEFORE the fix and passes AFTER. Name it with a
`# Regression guard: <finding-number>` comment referencing the review
doc. Example:

```python
async def test_suppression_counter_advances_every_turn(config, counter):
    """FW-1: advance_suppression_counter runs at end of every turn.

    Regression guard: earlier build referenced undefined
    `_increment_turns_since_call` which caused a NameError at
    runtime. This test exercises the actual counter path.
    """
```

Majors and minors are judgement calls, but blockers ALWAYS get a
regression test. This is how the test suite learns from each review
cycle instead of staying a fixed snapshot of what you thought to cover
at write-time.

## Cost tracking

Per-module target: **~$0.06-0.11 total** (DeepSeek + Codex combined).

Report this in commit messages so cost drift is visible:
```
Module 5/N: memory/entity_cache.py

DeepSeek V4 Pro: $0.023 (228s, 9.8K tokens)
Codex 5.3:      $0.051 (34s, 7.2K tokens)
Total:          $0.074

Codex findings:
- (list legitimate hits, how resolved)

47/47 tests pass. Full suite: 165/165.
```

## Direct-typed work (CLI, installer, shell) — DeepSeek may skip, Codex still required

Some work is mechanically wired, not algorithmically novel: argparse
subcommand dispatchers, shell installers, small config renderers, CLI
wrappers that delegate to existing module APIs. DeepSeek is NOT
mandatory for this class (no new logic to reason about). **Codex 5.3
review IS still mandatory.** Direct-typed work has its own failure
modes that pass manual testing:

- **Argparse contract drift** — flag advertised (`--limit`, optional
  positional) but not wired through to the callee, or default value
  differs from the module's default
- **Shell arg parsing** — `sh -s <agent> --flag` (no version_spec)
  silently assigns `--flag` to VERSION_SPEC. Parse-by-position is
  brittle; Codex catches this in 30 seconds
- **Silently-swallowed errors** — `cmd || { echo warn; }` downgrades
  a failure to a success exit code. If the user explicitly requested
  the thing, they deserve a loud failure
- **"Unknown flag" warnings** instead of errors — typos become silent
  installs missing pieces
- **Contract mismatch between --help and behaviour** — advertised
  "optional, falls back to context" when the code immediately errors

### Process for direct-typed CLI/shell work

1. **Write/edit directly.** No DeepSeek fire. No framework spec needed
   for argparse wiring or shell flag parsing — the design is the
   existing module's API.
2. **Run the CLI against a real scenario end-to-end** BEFORE committing.
   `python -m spaice_agent.cli <subcommand> <fixture-args>`. Verify
   stdout matches the advertised contract (flag has effect, help text
   matches reality, exit codes honour the failure cases).
3. **Fire Codex 5.3 on the diff** (not on the whole file) — the diff is
   smaller and Codex's signal-to-noise is higher. Typical cost:
   $0.03/pass. Re-fire on the fix diff for a second pass — new code
   paths that the first pass' findings created may have their own bugs.
4. **Triage + regression tests** as usual (accept-real-bug, reject-
   context-gap, etc.). Shell-script tests can run as subprocess calls
   in pytest; argparse tests can import `main()` directly.

### Case file — Phase 2A+2B (CLI subcommands + install.sh flag)

- Scope: added 7 memory subcommands to `cli.py`, `--with-vault` +
  `--full` flags to `install.sh`, fixed 2 audit false positives.
- Skipped DeepSeek (pure wiring). Fired Codex twice on the diff.
- **Pass 1 ($0.0313):** 3 blockers found — install.sh flag parsing
  mis-assigns version_spec when flags come right after agent_id;
  `vault agent_id` argparse advertised optional-with-context-fallback
  but code hard-fails; `mine --limit` parsed but never passed to
  `miner.run()`.
- **Pass 2 on fix diff ($0.0315):** 3 more blockers found in the fixes
  themselves — `vault scaffold --dry-run` still calls `ensure_skeleton()`
  (writes dirs) violating dry-run contract; install.sh silently warns
  on unknown flags instead of erroring; `--with-vault` scaffold
  failures wrapped in `|| { echo warn; }` swallowing the exit code.
- **Zero false positives across both passes.** All 6 findings → 10
  regression tests (5 audit + 5 CLI).
- Result: caught 6 real bugs for $0.063 combined. Manual smoke-testing
  caught zero of them; argparse + shell look-right-work-wrong bugs are
  exactly Codex's sweet spot.

Takeaway: **direct-typed work is NOT review-exempt work.** Skip
DeepSeek freely for mechanical wiring; NEVER skip Codex. Two cheap
passes on the diff beats any amount of eyeball review.

## Dogfood new generated content against sibling validators

When a new module generates content that another module in the same
package consumes or validates (scaffold → audit; exporter → importer;
renderer → linter), **run the pipeline end-to-end on a throwaway
fixture before committing the new module.** The audit/validator WILL
surface issues in both ends of the pipeline:

- False positives in the validator's own logic (patterns it didn't
  handle — wikilink-in-code-fence, README.md across directories)
- Real flaws in the new generator's output (missing frontmatter,
  broken link placeholders, format violations)

### Pattern

```bash
# Scaffold a throwaway vault
rm -rf /tmp/_test_agent ~/.spaice-agents/_test_agent ~/_test_agent
mkdir -p /tmp/_test_agent && ln -sfn /tmp/_test_agent ~/_test_agent
python -m spaice_agent.cli vault scaffold _test_agent

# Run the audit/validator against the fresh output
python -m spaice_agent.cli audit _test_agent

# Cleanup
rm -f ~/_test_agent && rm -rf /tmp/_test_agent ~/.spaice-agents/_test_agent
```

**Triage rule:** every finding is either a bug in the generator or a
bug in the validator. Fix both ends. The audit module got two
net-positive fixes from dogfooding Phase 2C's scaffold output:

1. `check_duplicate_files` now exempts `README.md`/`index.md` (every
   user vault benefits, not just the scaffold output)
2. `_extract_wikilinks` strips fenced code + inline code before
   scanning (any user documenting wikilink syntax benefits)

Neither fix would have happened without dogfooding. Worth 20 minutes
before every CLI/scaffold/exporter commit.

## Never break this pattern by

- **Skipping Codex review** because the module "looks fine". That's
  the whole point — the risky modules are the ones that LOOK fine.
- **Using delegate_task for Codex review.** It inherits Opus, which is
  Opus reviewing Opus — blind spot. Always direct OpenRouter API call.
- **Writing tests BEFORE DeepSeek writes the code.** Then you're
  testing your own assumptions. Let DeepSeek interpret the spec first,
  THEN write tests for what the module should do (informed by reading
  what it does do).
- **Applying Codex fixes without re-running tests.** Every fix could
  regress something else. Green test run after every fix batch.
- **Committing with open Codex issues.** If a finding is ignored,
  document why in the commit message.

## Retroactive review — catching up when Codex was skipped

### Reviewer-model selection — ALWAYS GPT-5.3-Codex (correction 017, 2026-05-03)

**`openai/gpt-5.3-codex` (released 2026-02-24, $1.75/M in, $14/M out,
400K context) is the ONLY sanctioned reviewer.** No Sonnet. No earlier
Codex. No unversioned meta-slug. No exceptions — single module, batch,
retroactive, pre-tag final, all GPT-5.3-Codex. user directive (see correction 017).

**Why the versioned slug, not `openai/gpt-5-codex`:** the unversioned
meta-slug can route to older builds. Pin the exact version.

**Minimum version:** 5.3 is the floor. If OpenRouter ships a newer
Codex (5.4, 6.x, etc.) under its own versioned slug, upgrade. If
`openai/gpt-5.3-codex` is ever deprecated without a newer versioned
slug available, refuse to fire and surface to the user.

**Why not Sonnet 4.5 (previously recommended for batch retro review):**
Sonnet raised false positives on the 2026-05-03 Phase 1C review
cycle — most notably claiming `@dataclass(frozen=True)` +
`__post_init__` validate-and-raise is illegal Python (it is legal;
live-interpreter check confirmed). The cost of triaging language-
semantics confabulations, plus the risk of applying a "fix" that
regresses working code, exceeds the cost of re-firing Codex until it
returns clean structured output.

**Operational rules:**

1. **Model pin:** `openai/gpt-5.3-codex` for all reviews. Use the versioned slug; the unversioned `openai/gpt-5-codex` meta-slug can route to older builds.
2. **Wrapper content-fallback mandatory** — Codex reasoning-mode
   output often lands in non-standard fields. See the
   "Wrapper must handle `content=None`" block below.
3. **On empty Codex response** (reasoning ate the output): re-fire
   with a tightened prompt that explicitly requests the
   `VERDICT / FINDINGS` block format. Do NOT fall back to another
   model.
4. **Sequential dispatch with `sleep 2`** between calls for batches
   of 5+. Parallel ThreadPoolExecutor tripped rate limits and
   reasoning-mode stalls on 2026-05-03.
5. **Budget:** Codex costs ~$0.10+/module. Pay it. Cheaper than
   triaging false positives or shipping regressions.

The earlier Sonnet recommendation was based on one session where
parallel Codex calls returned `content=None`. The fix was the
wrapper + sequential dispatch — NOT a model swap. Corrected here.

### Wrapper must handle `content=None`

Any call_codex.py / review wrapper you ship must extract text via a
fallback chain, not a single `.content` read:

```python
choice = data["choices"][0]["message"]
text = (
    choice.get("content")
    or choice.get("reasoning")
    or choice.get("reasoning_content")
    or data["choices"][0].get("reasoning")
    or ""
)
if not text:
    print(f"ERR: empty completion — message={choice}", file=sys.stderr)
    return 4
```

The `.get("content") or ...` chain handles both the "missing key"
and "present but None" cases in one line. Reasoning-model responses
land in the second/third branch; standard chat completions land in
the first. Without this fallback, a single reasoning-only response
from GPT-5-codex crashes the wrapper with
`TypeError: unsupported operand type(s) for +: 'NoneType' and 'str'`.

### Parallel dispatch — beware OpenRouter rate limits

`concurrent.futures.ThreadPoolExecutor(max_workers=6)` firing six
OpenRouter calls simultaneously tripped a flaky rate-limit / auth
response on 2026-05-03 (5/6 failed with parse errors). Sequential
dispatch with `sleep 2` between calls ran clean in ~2 minutes.
Prefer sequential for batches of 5+ unless you know the provider's
concurrent-request budget.

If several modules shipped without step 4 (common during velocity
bursts or when the stack doctrine was still forming), batch the catch-up
but run SEQUENTIALLY with small sleeps between calls:

**Pattern (proven 2026-05-03, 4 modules, $0.71, 170s wall-clock):**

1. Collect all modules that skipped Codex into a list.
2. Spawn N parallel OpenRouter calls to `openai/gpt-5.3-codex`, one per
   module, each with the full framework plan + module source + test
   source (same prompt structure as step 4).
3. Save each critique under `reviews/<module>_codex_<date>.md`.
4. Write a `reviews/SUMMARY_<date>.md` rolling up severity + counts.
5. Apply fixes module-by-module, re-run full `pytest` between each,
   commit per module with the cost + findings in the message.

**Parallel dispatch example:**

```python
import asyncio, aiohttp, json, pathlib

modules = ["config", "triggers", "budget", "openrouter_client"]
plan = (pathlib.Path.home()/"jarvis/.hermes/plans/<plan>.md").read_text()

async def review(session, mod):
    src = (pathlib.Path.home()/f"Developer/spaice-agent/spaice_agent/{mod}.py").read_text()
    tst = (pathlib.Path.home()/f"Developer/spaice-agent/tests/test_{mod}.py").read_text()
    prompt = f"<framework_plan>{plan}</framework_plan>\n<module>{src}</module>\n<tests>{tst}</tests>\n\nCritique: factual / arch / spec / risk / pitfalls / verdict."
    async with session.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "openai/gpt-5.3-codex",
              "messages": [{"role":"user","content":prompt}],
              "temperature": 0.2},
        timeout=aiohttp.ClientTimeout(total=600),
    ) as r:
        return mod, await r.json()

async def main():
    async with aiohttp.ClientSession() as s:
        return await asyncio.gather(*[review(s, m) for m in modules])
```

**Don't skip this when time-pressed.** The whole point of the stack is
that the risky modules look fine. Retroactive batch review at ~$0.18
per module is cheap insurance — and because it's parallel, wall-clock
cost is a single Codex latency (~3 min for 4 modules).

### Parallel side-fix track (proven 2026-05-03)

While running the main build-stack cycle (framework → DeepSeek → Codex),
a previously-shipped stack-bypassed script can be retro-reviewed in
**parallel** without blocking the main cycle. Pattern:

1. Fire both OpenRouter calls as separate background processes.
2. Main track: framework critique → fixes.
3. Side track: retroactive Codex review of the already-shipped script.
4. Apply side-track findings as micro-patches (<50 LOC each) with
   commit trailer `Reviewed-by: override:micro-patch addresses
   retroactive-review findings #N, #M`. Don't re-run the full stack
   for these — they're confined fixes with a clear audit trail back
   to the retro review that motivated them.
5. Commit the main-track module and the side-track fixes in separate
   commits so each has a focused message and trailer.

This keeps the main stack running at its natural pace while paying down
stack-bypass debt opportunistically.

### Pre-fire preflight for the review wrapper (correction 017 era)

Before firing a batch of retro reviews, verify TWO things beyond step 0:

1. **Test-context path exists for every module.** If you loop over
   modules assuming `tests/memory/test_<mod>.py` or `tests/test_<mod>.py`
   exists, a single missing file kills every call in the batch because
   `call_codex.py` does `args.context.read_text()` before any LLM call.
   Sanity-check with `ls` over the expected paths first, and if any are
   missing, fall back to the consolidated-context pattern below.

2. **Reviewer slug is the versioned one.** Correction 017 requires
   `openai/gpt-5.3-codex`, NOT `openai/gpt-5-codex`. Grep the wrapper
   (`grep MODEL ~/jarvis/scripts/call_codex.py`) before a batch run; if
   the meta-slug ever creeps back in, patch it before firing.

### Consolidated-context pattern for batch retro review

When production tests don't map 1-to-1 to modules (tests consolidated
into `test_phase_<N>.py` + `test_phase_<N>_blockers.py`, etc.), don't
pass per-module test files — reviewer misses coverage context and may
re-flag behaviour already guarded. Instead:

```python
# Build once per batch
combined = (
    "# Combined test context — ALL tests for this phase.\n"
    "# Reviewer uses this to understand existing coverage so findings\n"
    "# focus on UNCOVERED risk.\n\n"
    "# === tests/test_phase_Nc.py ===\n"
    + (repo / "tests/test_phase_Nc.py").read_text()
    + "\n\n# === tests/test_phase_Nc_blockers.py ===\n"
    + (repo / "tests/test_phase_Nc_blockers.py").read_text()
)
(repo / "_scratch_review_context.py").write_text(combined)
```

Then every per-module review call uses the same `--context` path:
`--context _scratch_review_context.py`. Delete the scratch file before
commit. Measured 2026-05-03 Phase 1C Codex 5.3 batch: 5 modules, 13
findings (10 accepted, 0 false positives on accept path), $0.1345 —
cheaper AND cleaner than a Sonnet batch would have been.

### Cross-module concern: unique per-call tmp paths for atomic writes

When DeepSeek writes multiple modules that each perform atomic writes,
it consistently uses `f".{name}.tmp-{os.getpid()}"` — which is race-
UNSAFE for concurrent writers in the same process (two async tasks or
threads writing the same target race on the same tmp path, one
`os.replace()` can remove the tmp file before the other runs).

**Fix pattern (applies to EVERY atomic-write site, cross-cutting):**

```python
import uuid
tmp = file_path.parent / f".{file_path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}"
```

Codex 5.3 will catch this per-module as blocker/major in the first
review cycle (2026-05-03 Phase 1C: flagged in 4 of 5 modules). When
fixing, grep the WHOLE repo for `.tmp-{os.getpid()}` and upgrade every
occurrence in a single commit — otherwise the next review will flag
the same pattern in modules you missed. Regression test: two threads
× 15+ iterations writing the same target, assert no exceptions and
final file exists.

## Case file — five-module cycle

End-to-end cycle on `credentials`, `memory_recall`, `memory_store`,
`search`, `consensus`, `orchestrator`. Documents a multi-module session
with both framework-critique iteration AND retroactive batch review.

**Shape:**
1. Preflight revealed three assumed paths absent
   (`~/.Hermes/credentials/`, `~/.spaice-agents/`, `call_codex.py`)
   — summary from prior context claimed all three existed. Fixed in
   10 minutes before any LLM call. This is why Step 0 exists.
2. Credentials migrated from `~/.hermes/.env` and
   `~/.Hermes/credentials/` (canonical store — migration from `~/.openclaw/credentials/` completed 2026-05-03)
   (0600). No Brave key available → `search.py` works as Exa-only
   until provisioned.
3. Framework written by Jarvis (no framework critique needed — scope
   was 5 tight modules, spec was already clear from prior session).
4. Five modules coded. **DeepSeek MUST be invoked. There is no skip.**
   The previous "direct-typed-no-deepseek-invoke" exception has been
   REMOVED per correction 009 (2026-05-03). Opus self-typing bypass cost
   ~$300 in tokens across Phase 1A+1B (historical incident). No exceptions. No shortcuts.
   No approval pathway. DeepSeek runs or coding does not happen.
5. Pass 1 retroactive review attempted with the (then unversioned) `openai/gpt-5-codex` slug via
   ThreadPoolExecutor(max_workers=6) — 5/6 failed with
   `TypeError: NoneType + str`. Wrapper missing content-fallback.
6. Wrapper patched with fallback chain. Pass 1 retry sequential —
   still 4/6 empty because GPT-5-codex returns reasoning-only for
   many review prompts.
7. Pass 2 model-swapped to `anthropic/claude-sonnet-4.5` — 6/6 clean
   structured reviews in ~2 minutes, $0.13 total, 31 findings
   surfaced (11 blocker/major, 20 minor/nit).
8. Blockers/majors fixed in-code. Test suite: 232/232 passing
   (146 old + 86 new). Live smoke test fired full pipeline end-to-end
   against OpenRouter: 25s, $0.042, 4 stages, correct voice output.
9. Remaining blocker surfaced is HERMES wiring (not package code) —
   documented in `~/jarvis/_scratch/hermes-wiring-blockers.md` for
   separate decision.

**Cost breakdown:**
- Pass 1 attempts (wasted): $0.05 (4 × failed GPT-5-codex calls)
- Wrapper debug + fix: $0 (local only)
- Pass 2 (all 6 modules): $0.13 (Sonnet 4.5)
- Live smoke (full pipeline): $0.042
- Total session: **~$0.22** for 6 modules reviewed + 1 live fire.

**Key takeaways baked into this skill:**
- Preflight step 0 exists (would have saved the first 10 minutes)
- Sonnet 4.5 is the recommended reviewer for batch retroactive review
- Wrapper content-fallback is mandatory
- Sequential-with-sleep beats parallel for OpenRouter batches of 5+
- **DeepSeek is ALWAYS invoked for implementation. No skip. No exceptions.**
  Per correction 009, the "direct-typed-no-deepseek-invoke" escape hatch
  has been removed from this skill. The pipeline is immutable.

## Known failure modes

### DeepSeek reasoning spill
First-pass output is a design document, not Python. Either:
- Tighter re-prompt: "Return ONLY valid Python source code. Do not
  include reasoning, analysis, or commentary. Start with `from
  __future__ import annotations` or the first import statement."
- Or write the module directly from the already-complete spec

### Codex findings I disagree with
Some findings are wrong or over-scoped. Push back with evidence
(specific line numbers, specific Python semantics, benchmark if cost
claim). Don't just apply every finding silently.

### DeepSeek writes tests anyway
Rare. If DeepSeek's prompt accidentally asks for tests, it might
produce some. Ignore them — write your own. The tester-separate-from-
coder separation matters more than saving 10 minutes.

## Integration with other skills

- **writing-plans** — use for the step-1 framework work on multi-week
  scope
- **requesting-code-review** — this skill IS the code review flow;
  that skill covers the mechanics of pointing Codex at any diff
- **test-driven-development** — complement, not replacement: TDD runs
  inside step 3 (Jarvis tests)
- **spaice-orchestration-stack** — parent/sibling skill, defines the
  model choices this skill depends on
- **spaice-memory** — if the module being built touches memory
  structure, load this first
