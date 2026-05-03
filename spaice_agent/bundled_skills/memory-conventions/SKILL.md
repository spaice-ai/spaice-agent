---
name: memory-conventions
description: |
  Persistent memory conventions for spaice-agent vaults. Load on any turn
  that reads, writes, retrieves, or restructures markdown in the vault
  (~/<agent>/), or interprets inbox drafts / continuity files / dashboards.
  Defines frontmatter schemas, wikilink discipline, the Dewey layer stack,
  the priority-rule filing pattern, and the search-first retrieval reflex.
  This skill is shipped bundled with spaice-agent — its content is
  user-agnostic, generic conventions. User-specific vocabulary (brands,
  client names, projects) lives in the vault's own CATEGORISATION.md.
version: 1.0.0
author: spaice-agent
metadata:
  hermes:
    tags: [memory, vault, conventions, frontmatter, wikilinks, dewey, bundled]
    related_skills:
      - spaice-build-stack
---

# Memory Conventions — How the vault works

The vault at `~/<agent>/` is more than a directory of markdown. It's a
structured memory system that automation (mine / triage / classify /
summarise / audit) reads and writes continuously. These conventions make
the system work. Drift from them and the automation degrades silently.

Load this skill whenever the task touches durable memory — reading a
shelf, writing a fact, interpreting classifier output, chasing a
cross-reference, or auditing for rot.

## The library model

```
┌─────────────────────────────────────────────────────────────┐
│ THE DESK      = this conversation (live, transient)         │
│ THE SHELVES   = ~/<agent>/ curated topic files (persistent) │
│ THE INBOX     = ~/<agent>/_inbox/ pending classifier drafts │
│ THE LEDGER    = ~/<agent>/_continuity/LATEST.md (resume)    │
│ THE ARCHIVE   = ~/.hermes/sessions/ raw session JSONL       │
└─────────────────────────────────────────────────────────────┘
```

The Desk is what you're reading now — it disappears at session end.
Everything durable goes to the Shelves. The Inbox buffers between the
two: the miner drops drafts there, you or triage review and file.

## Shelves — 9 canonical directories

A fresh vault ships with these shelves, each with a `README.md`:

| Shelf | What goes there | Dewey layer |
|---|---|---|
| `identity/` | Who the user is (name, roles, preferences, values) | 100 / 600 |
| `personal/` | Non-work context (family, health, hobbies, travel) | 100 |
| `corrections/` | Numbered rules the user taught you (`001-slug.md`) | 600 |
| `patterns/` | Reusable solution shapes, recipes, templates | 500 / 700 |
| `learnings/` | Field knowledge, insights, gotchas with resolutions | 500 / 700 |
| `integrations/` | Third-party services, APIs, SaaS tools | 300 |
| `infrastructure/` | Hosts, IPs, networks, physical devices | 300 |
| `projects/` | Named workstreams with deliverables | 200 |
| `sites/` | Physical locations, client sites, venues | 300 |

Plus 5 special directories the user doesn't file into directly:

- `_inbox/` — miner output, pending triage
- `_continuity/` — `LATEST.md` resume point, one summary per session
- `_dashboard/` — auto-regenerated dashboard files (never hand-edit)
- `_templates/` — starter markdown files for capture workflows
- `_archive/` — retired content (keep history, don't mix with live)

## Frontmatter — the contract with automation

### Required (every file)

```yaml
---
title: <descriptive title>
date: YYYY-MM-DD
tags: [<tag1>, <tag2>]
---
```

### Optional (recognised by tooling when present)

```yaml
status: draft | active | superseded | archived
dewey_layer: "300 Systems"          # see Dewey layers below
source: <session id, url, "user 2026-05-04">
refs: ["[[related-file]]", "[[other]]"]
updated: YYYY-MM-DD
valid_to: YYYY-MM-DD                # set when superseded
```

Canonical field name is `dewey_layer` (matches `classifier_dewey_layer`
written by the miner). Value format is `"NNN Label"` — see the Dewey
table below for label pairings.

### Classifier-written (in `_inbox/` drafts — never hand-edit)

```yaml
mined_at: <ISO timestamp>
source_session: <hermes session file>
source_turn: <int>
classifier_target: <path>
classifier_section: <heading>
classifier_dewey_layer: "300 Systems"
classifier_priority: <1-5 int>
classifier_confidence: 0.87
classifier_rule: <rule name>
classifier_model: <model slug used>
classifier_used_fallback: false
```

### Continuity file (`_continuity/LATEST.md` — written by `summarise`)

```yaml
---
source_session: <hermes session file>
summary_generated_at: <ISO timestamp>
summary_method: llm | heuristic
schema_version: 1
---
```

## Dewey 8-layer stack

Every filed fact should carry a Dewey layer in frontmatter or in its
categorisation routing. The 8 layers are library-science convention
(400 reserved for readability symmetry, deliberately never used):

| Layer | Theme | Typical shelves |
|---|---|---|
| 000 | Cross-cutting, uncategorised | `_inbox/`, `LOG.md` |
| 100 | People, relationships, identity | `identity/`, `personal/` |
| 200 | Projects, builds, workstreams | `projects/` |
| 300 | Systems, infrastructure, integrations | `infrastructure/`, `integrations/`, `sites/` |
| 400 | *(reserved — do not use)* | — |
| 500 | Technical knowledge, reference | `learnings/`, `patterns/` |
| 600 | Preferences, opinions, rules | `corrections/`, `identity/` |
| 700 | Problems, gotchas, resolutions | `patterns/`, `learnings/` |

Use the most specific layer. If a fact spans two, pick the primary and
list the other in `refs:`.

## Wikilinks — the moat's connective tissue

```markdown
[[target-page]]             # links to target-page.md in vault
[[target-page|alias]]       # shows "alias", links to target-page.md
[[target-page#Section]]     # links to specific heading
```

**Cross-reference discipline.** When file A writes about file B's
subject, add `[[B]]` to A. Where sensible, add a backlink in B to A.
The web of links turns a folder into a knowledge graph — kill it and
you have an orphan pile. The `audit` tool reports broken links:

```bash
spaice-agent audit <agent_id>
```

Run it when things feel stale. Orphaned wikilinks are usually the
loudest signal of rot.

## Priority rules — the filing routing table

`CATEGORISATION.md` at the vault root is the classifier's system
prompt. It lists priority rules **first match wins**:

1. Personal identity → `identity/`
2. Personal context → `personal/`
3. Correction or rule → `corrections/NNN-slug.md`
4. Reusable pattern → `patterns/`
5. Field learning → `learnings/`
6. External service / API / tool → `integrations/`
7. Host / network / hardware → `infrastructure/`
8. Project or workstream → `projects/`
9. Location or site → `sites/`
10. Uncategorised → `LOG.md § Uncategorised` (000 layer)

The user extends `CATEGORISATION.md` with their own vocabulary (brand
names, client names, product families). The default 10 rules ship as
scaffolding — a bare-vault install classifies correctly from turn one.

## Read pattern — search first, always

1. **Find the hit.** Use search before reading — `search_files` on the
   shelves, `spaice-agent recall <agent_id> "<query>"` for ranked
   vault-wide recall, `session_search` for "what did we do before".
2. **Read the hit.** Load the specific file. Don't auto-load the whole
   shelf; respect the desk's context budget.
3. **Follow the links.** Each file has `refs:` in frontmatter and
   wikilinks inline. Chase them when they're relevant; ignore them when
   they're not.

**Anti-pattern:** reading every file in `projects/` because the user
asked a question about a project. The retrieval tools exist to avoid
this. A 40-file shelf is a fine size for search, a terrible size for
load-all.

## Write pattern — route via CATEGORISATION, commit

1. **Classify.** Either let the miner do it (passive path) or apply
   the priority rules yourself when writing explicitly.
2. **File to the shelf.** Use kebab-case filenames.
   `integrations/openrouter-rotation.md`, not
   `Integrations/OpenRouter rotation.md`.
3. **Write with frontmatter.** Required fields always; optional fields
   when they add signal.
4. **Cross-reference.** Add `[[...]]` wikilinks inline where relevant.
5. **Never write credentials.** Reference by filename: "key lives at
   `~/.<agent>/credentials/openrouter.key`".
6. **Commit.** The vault is under git for a reason — each filing is a
   restorable save-point.

### When a fact spans two topics

Primary bucket gets the full entry. Other bucket gets a one-line
cross-reference:

```markdown
<!-- in sites/acme-office.md -->
## [2026-05-04] Network outage
- Root cause: ISP BGP re-advertisement
- See: [[integrations/acme-isp|Acme ISP notes]]

<!-- in integrations/acme-isp.md -->
## Known incidents
- 2026-05-04 BGP re-advertisement — see [[sites/acme-office|Acme office]]
```

## Superseding facts (never silently rewrite)

When a fact changes, don't edit the old one away — add a dated
supersession:

```markdown
## [2026-05-04] Decision changed

Previously: chose Postgres for the metrics store.
Now: switched to DuckDB — 10× query speed, simpler ops.
Reason: metrics volume below 10 GB, analytic queries dominate.
```

For field-level changes, mark the old block with `valid_to: 2026-05-04`
in its inline metadata and link to the new block. Future readers need
the history, not the tidied present.

## Attribution and zero-fabrication

- **Attribute decisions.** "Alex decided X on 2026-05-04", not
  "decided X".
- **Mark uncertainty.** `[TBC]` inline, or `status: draft` in
  frontmatter. Never guess.
- **Timestamp everything operational.** Decisions, incidents, resolved
  gotchas get a dated `## [YYYY-MM-DD]` heading. Timeless reference
  material doesn't need one.

## Continuity — the resume handshake

`_continuity/LATEST.md` is what the next session reads first. Its
format is written by `summarise`:

```markdown
---
source_session: <hermes session>
summary_generated_at: <ISO>
summary_method: llm | heuristic
schema_version: 1
---

# Continuity — <date>

## Goal
<what we were trying to achieve>

## Progress
<what got done this session>

## Open threads
<what's in flight, with file refs>

## Next step
<what the next session should do first>
```

**Never hand-edit `LATEST.md`.** If it's stale or wrong, rerun
`spaice-agent summarise <agent_id>` to regenerate. If the summary is
always wrong, the session-mining pipeline has a bug — fix the pipeline,
not the output.

## Inbox triage — the filing discipline

`_inbox/` fills up as the miner runs. Drafts have classifier
frontmatter; triage reads it and decides:

- `confidence ≥ 0.85` → auto-file to `classifier_target`, delete draft
- `0.60 ≤ confidence < 0.85` → escalate to `LOG.md § Triage review`
- `confidence < 0.60` → move to `_inbox/_low-confidence/`
- Files younger than `MIN_AGE_HOURS` (default 4) → skip (let the miner
  settle)

Run triage yourself occasionally even if automation does it nightly.
The human call on borderline drafts is often better than the
classifier's — and catches drift in the classification rules.

## Dashboards — read-only views

`_dashboard/*.md` are regenerated by `spaice-agent dashboards
<agent_id>`. Each file has `_Auto-generated <ts>_` in the header.
**Never hand-edit.** If a dashboard is wrong, the script or its inputs
are wrong — fix those.

Typical dashboards:

- `README.md` — vault home, shelf counts
- `continuity.md` — mirrors `_continuity/LATEST.md`
- `recent-sessions.md` — last N session summaries
- `projects.md` — projects by last-touched
- `corrections.md` — rules + reinforcement counts
- `open-questions.md` — `[TBC]` / `TODO` / `status: draft` markers
  vault-wide

## Hard rules

- **Single topic per file.** Split mixed files before they grow.
- **Never write credentials into the vault.** Reference by filename only.
- **Never hand-edit auto-generated files** (`_dashboard/*`,
  `_continuity/LATEST.md`, `_inbox/` classifier frontmatter).
- **Never delete a correction** (`corrections/NNN-slug.md`) — they're
  append-only. Supersede with a new file that references the old.
- **Never break git.** Every filing is a commit; the git history is the
  audit trail.

## Anti-patterns

- **Dump and forget.** Writing a 5000-word session transcript as a
  single shelf file. That's what `_archive/` or session mining is for.
- **Directory-as-working-memory.** Using a shelf file to track in-flight
  task state. Use a `todo` list (session-scoped) instead.
- **Auto-load everything.** Reading `~/<agent>/**/*.md` at session
  start. The system is designed for search-first, not load-all.
- **Hand-editing classifier frontmatter.** The miner rewrites the file
  on next run; your edits get lost. Fix the classifier or the rule
  instead.
- **Silent supersession.** Editing a decision block without a dated
  supersession. Future readers need the history.

## Integration with automation

When these conventions are followed, the pipeline works end-to-end:

```
user utterance
  → mine.py      (session JSONL → _inbox/ drafts with classifier frontmatter)
  → classify.py  (LLM routing using CATEGORISATION.md)
  → triage.py    (_inbox/ → shelves or LOG.md based on confidence)
  → summarise.py (session JSONL → _continuity/LATEST.md on continuity hook)
  → dashboards.py (shelves → _dashboard/ read-only views)
  → audit.py     (vault-wide — broken wikilinks, frontmatter gaps, duplicates)
```

When conventions drift, the pipeline silently degrades. Audit
occasionally; patch this skill (or `CATEGORISATION.md`) when you
discover a new rule that would have prevented the drift.

## Related skills

- `spaice-build-stack` — coding workflow for changing any automation module
- The user's own memory skill (if one exists for this vault) —
  project-specific vocabulary and routing rules layered on top of these
  generic conventions
