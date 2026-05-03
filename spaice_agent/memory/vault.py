from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple

from spaice_agent.memory.paths import VaultPaths, CANONICAL_SHELVES, SPECIAL_DIRS
from spaice_agent.memory.dashboards import _atomic_write

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shelf / special-dir descriptions (used in generated READMEs)
# ---------------------------------------------------------------------------

_SHELF_PURPOSES: Dict[str, str] = {
    "identity": "who you are — core facts, roles, and self-descriptions",
    "personal": "non-work context, preferences, and personal notes",
    "corrections": "rules you’ve taught the agent — corrections and constraints",
    "patterns": "reusable solution shapes and design patterns",
    "learnings": "field knowledge, insights, and things you’ve learned",
    "integrations": "services, APIs, and tools you work with",
    "infrastructure": "hosts, networks, and infrastructure details",
    "projects": "workstreams, initiatives, and project pages",
    "sites": "physical locations, client sites, or venue records",
}

_SPECIAL_DIR_PURPOSES: Dict[str, str] = {
    "_inbox": "incoming facts and notes — triage from here",
    "_continuity": "session continuity — LATEST.md lives here",
    "_dashboard": "auto-regenerated dashboards and summaries",
    "_templates": "markdown templates for new content",
    "_archive": "retired or superseded content",
}

# ---------------------------------------------------------------------------
# Template generators
# ---------------------------------------------------------------------------

def _shelf_readme(shelf: str) -> str:
    purpose = _SHELF_PURPOSES.get(shelf, "content")
    return f"""---
title: {shelf} shelf
date: 2026-01-01
tags: [shelf, readme]
---

# {shelf.title()}

{purpose}

## What belongs here

- Items that describe or relate to {shelf}
- Notes, facts, and references specific to this category
- Anything that fits the shelf’s purpose

## Example filename

`example-{shelf}-entry.md`

## Frontmatter template

```yaml
title: <title>
date: YYYY-MM-DD
tags: [<tag1>, <tag2>]
```
"""

def _special_readme(dir_name: str) -> str:
    purpose = _SPECIAL_DIR_PURPOSES.get(dir_name, "special directory")
    return f"""---
title: {dir_name} directory
date: 2026-01-01
tags: [special, readme]
---

# {dir_name}

{purpose}

## What belongs here

- Content managed by the agent or tooling
- Do not manually edit auto-generated files
- See the vault conventions for more details

## Example filename

`example-{dir_name.lstrip('_')}-item.md`
"""

# ---------------------------------------------------------------------------
# Master template dictionary (insertion-order preserved)
# ---------------------------------------------------------------------------

_TEMPLATES: Dict[str, str] = {}

# -- vault-root files ------------------------------------------------------

_TEMPLATES["README.md"] = """\
# Memory Vault

This is your agent’s memory vault — a collection of markdown files organised
into shelves and special directories. The agent reads from here to understand
you, your work, and your preferences.

## Shelves

""" + "\n".join(
    f"- **{shelf}/** — {_SHELF_PURPOSES[shelf]}"
    for shelf in CANONICAL_SHELVES
) + """

## Special directories

""" + "\n".join(
    f"- **{d}/** — {_SPECIAL_DIR_PURPOSES[d]}"
    for d in SPECIAL_DIRS
) + """

## Conventions

Read [`CONVENTIONS.md`](CONVENTIONS.md) for frontmatter, naming, and linking rules.
Read [`CATEGORISATION.md`](CATEGORISATION.md) to decide where to put new content.

## How do I add to this vault?

1. Create a new `.md` file in the appropriate shelf.
2. Include the required YAML frontmatter (see `CONVENTIONS.md`).
3. Write your content below the frontmatter.
4. The agent will pick it up on its next read cycle.

When unsure, drop the file into `_inbox/` — the agent will triage it later.
"""

_TEMPLATES["CONVENTIONS.md"] = """\
# Vault Conventions

The vault is markdown with structured frontmatter. Tools in this package
(`classify`, `mine`, `triage`, `summarise`, `audit`) rely on these
conventions — keep them consistent or automation drifts.

## Required frontmatter

Every markdown file in the vault must start with a YAML frontmatter block
containing at least:

```yaml
---
title: <descriptive title>
date: YYYY-MM-DD
tags: [<tag1>, <tag2>]
---
```

- `title` — a human-readable title for the entry.
- `date` — ISO 8601 date (`YYYY-MM-DD`) when the content was created or
  last significantly updated.
- `tags` — a YAML list of lowercase tags. Use kebab-case for multi-word
  tags.

## Optional frontmatter (recognised by tooling)

Add these fields when they apply — the miner, triage, and audit tools
read them if present:

```yaml
status: draft | active | superseded | archived
dewey_layer: "300 Systems"      # see CATEGORISATION.md for layer meanings
source: <where this came from — url, session id, "user 2026-05-04">
refs: ["[[related-file]]", "[[other-file]]"]
updated: YYYY-MM-DD             # last edit, separate from creation `date`
valid_to: YYYY-MM-DD             # set when superseded; preserves history
```

The automation uses `dewey_layer` as the canonical field name (matches
`classifier_dewey_layer` written by the miner). Use the `"NNN Label"`
form — e.g. `"300 Systems"`, `"600 Preferences"`, `"000 General"`.

## Classifier-written frontmatter (do not hand-edit)

When the miner drops a draft into `_inbox/`, it writes these fields.
Triage reads them; leave them alone:

```yaml
mined_at: <ISO timestamp>
source_session: <hermes session file>
source_turn: <int>
classifier_target: <target-file-path>
classifier_section: <target-heading>
classifier_dewey_layer: "300 Systems"
classifier_priority: <1-5 integer>
classifier_confidence: 0.87
classifier_rule: <which CATEGORISATION rule matched>
classifier_model: <model slug used>
classifier_used_fallback: false
```

Triage consumes these and either files the draft to the classifier
target (if confidence ≥ 0.85) or escalates to `LOG.md`.

## Continuity file frontmatter

`_continuity/LATEST.md` is special — it's what "continue" reads first.
Its frontmatter is written by `summarise`:

```yaml
---
source_session: <hermes session file>
summary_generated_at: <ISO timestamp>
summary_method: llm | heuristic
schema_version: 1
---
```

Treat `LATEST.md` as automation output. Never hand-edit — rerun
`spaice-agent summarise <agent_id>` instead.

## File naming

- Use **kebab-case** for filenames: `my-project-notes.md`.
- Avoid spaces, underscores, and special characters.
- Keep names short but descriptive.
- Dewey-style prefixes (e.g. `300.10001-home-network.md`) are optional —
  useful for very large vaults, overkill for small ones.

## Dates

All dates use **ISO 8601** format: `YYYY-MM-DD`. Do not use
`MM/DD/YYYY` or natural-language dates. Timestamps use ISO 8601 with
timezone: `2026-05-04T14:32:00+10:00`.

## Wikilinks — the moat's connective tissue

Link to other vault pages using Obsidian-compatible wikilink syntax:

- `[[target-page]]` — links to `target-page.md` in the same vault.
- `[[target-page|alias]]` — displays "alias" but links to `target-page.md`.
- `[[target-page#Section]]` — links to a specific heading.

Wikilinks resolve relative to the vault root. Do not use absolute
filesystem paths.

**Cross-reference discipline:** if file A mentions file B's subject,
file A should `[[B]]` and file B should mention `[[A]]` back where it
makes sense. The `audit` tool reports broken wikilinks; run
`spaice-agent audit <agent_id>` occasionally to catch rot.

## Single topic per file

One subject per markdown file. If a fact spans two topics, write it in
the primary file and add a one-line `[[cross-ref]]` in the other.
Avoid mixing — the classifier and retrieval tools work better on
focused files.

## Attribution and zero-fabrication

- Attribute user decisions: "Alex decided X on 2026-05-04" — not
  "decided X".
- If a fact is unconfirmed, write `[TBC]` or use `status: draft`.
  Never guess.
- Supersede with a dated block rather than deleting:

  ```markdown
  ## [2026-05-04] Decision changed
  Previously: <old position>. Now: <new position>. Reason: <why>.
  ```

## Never write credentials

Credentials live in the agent's credential store, never in the vault.
If you need to reference a credential by name, write the filename:

```markdown
API key lives at `~/.<agent>/credentials/openrouter.key` — not pasted
here.
```

## General style

- Clear, concise English. No filler.
- Headings (`##`, `###`) for structure, not decoration.
- Fenced code blocks for commands, config, regex, and anything a
  future reader will copy-paste.
"""

_TEMPLATES["CATEGORISATION.md"] = """\
# Categorisation Guide

This file is the **routing table** used by `spaice-agent classify`.
When the miner processes a fact, the LLM classifier reads this file as
its system prompt and picks a target shelf. Keep it accurate — drift
here causes drift in every auto-filed fact.

## Dewey layers (8-layer stack)

Every fact gets a `dewey_layer` tag. The layers below match library
science conventions (400 is deliberately kept empty for readability —
don't use it):

| Layer | Theme | Shelves in this vault |
|---|---|---|
| **000** | General, cross-cutting, uncategorised | `_inbox/`, `LOG.md` |
| **100** | People, relationships, identity | `identity/`, `personal/` |
| **200** | Projects, builds, workstreams | `projects/` |
| **300** | Systems, infrastructure, integrations | `infrastructure/`, `integrations/` |
| **400** | *(reserved — do not use)* | — |
| **500** | Technical knowledge, reference material | `learnings/`, `patterns/` |
| **600** | Preferences, opinions, rules | `corrections/` (as rules), `identity/` (as preferences) |
| **700** | Problems, gotchas, resolutions | `patterns/` (as resolved recipes), `learnings/` |

A single fact can be multi-layer; pick the most specific and list
cross-references in frontmatter `refs:`.

## Priority rules (first match wins)

The classifier walks this list top-to-bottom and picks the first rule
that fires. Add your own vocabulary to each rule — the defaults below
are the minimum scaffold.

1. **Personal identity** — core facts about the user (name, roles,
   values, self-descriptions, long-standing preferences).
   → `identity/<topic>.md` · Dewey 100

2. **Personal context** — non-work (family, health, hobbies, travel,
   personal finance).
   → `personal/<topic>.md` · Dewey 100

3. **Correction or rule** — user told the agent "don't do X", "always
   do Y", or "remember this".
   → new file `corrections/NNN-<slug>.md` · Dewey 600

4. **Reusable pattern** — design shape, recipe, code snippet, or
   solution template that applies across projects.
   → `patterns/<topic>.md` · Dewey 500 or 700

5. **Field learning** — domain knowledge, gotcha, lesson, insight.
   → `learnings/<topic>.md` · Dewey 500 or 700

6. **External service, API, or tool** — third-party integration the
   user works with (SaaS, APIs, libraries).
   → `integrations/<service>.md` · Dewey 300

7. **Host, network, or hardware** — servers, IPs, devices, physical
   infrastructure, network topology.
   → `infrastructure/<host>.md` · Dewey 300

8. **Project or workstream** — named initiative with a start/end,
   deliverables, or active work.
   → `projects/<name>.md` · Dewey 200

9. **Location or site** — physical place, client site, venue, home
   address, office.
   → `sites/<site>.md` · Dewey 300 (infrastructure-adjacent)

10. **Uncategorised** — matches none of the above.
    → `LOG.md § Uncategorised` · Dewey 000 (nightly triage reviews)

## Output schema (read by classifier)

The classifier returns JSON shaped like:

```json
{
  "target_file": "integrations/openrouter.md",
  "section": "Authentication",
  "dewey_layer": "300 Systems",
  "priority": 6,
  "rule_matched": "External service, API, or tool",
  "cross_references": ["infrastructure/api-keys.md"],
  "confidence": 0.92,
  "reasoning": "OpenRouter API key rotation procedure — belongs with integrations."
}
```

`dewey_layer` uses the `"NNN Label"` form. Label pairings: `000 General`,
`100 People`, `200 Projects`, `300 Systems`, `500 Technical`, `600
Preferences`, `700 Problems`.

- `confidence >= 0.85` → triage auto-files to `target_file`.
- `0.60 <= confidence < 0.85` → triage escalates to `LOG.md §
  Triage review needed`.
- `confidence < 0.60` → draft moves to `_inbox/_low-confidence/`.

## Extending this file

- Add new priority rules as the vault grows. Keep them concrete (a rule
  only useful if the classifier can match on it — vague rules get
  skipped).
- Add vocabulary hints inline (brands, client names, product families,
  platform terms) so the LLM has grounding.
- Never delete the Dewey stack — automation depends on the layer values
  being one of `000|100|200|300|500|600|700`.
- When a rule is superseded, leave it in place and mark
  `DEPRECATED: <date> — <why>` so old inbox drafts still resolve.
"""

# -- shelf READMEs ---------------------------------------------------------

for _shelf in CANONICAL_SHELVES:
    _TEMPLATES[f"{_shelf}/README.md"] = _shelf_readme(_shelf)

# -- special-dir READMEs (skip _archive) -----------------------------------

for _sdir in SPECIAL_DIRS:
    if _sdir != "_archive":
        _TEMPLATES[f"{_sdir}/README.md"] = _special_readme(_sdir)

# -- identity/SOUL.md (shipped template for the agent's persona + doctrine) -
# Loaded from package resource so the full text lives in a .md.template file
# (easier to diff, review, and localise than an inline string).
try:
    from importlib import resources as _res
    _TEMPLATES["identity/SOUL.md"] = (
        _res.files("spaice_agent.memory.templates")
        .joinpath("soul.md.template")
        .read_text(encoding="utf-8")
    )
except (ModuleNotFoundError, FileNotFoundError, AttributeError):
    # Fallback for source checkouts and older Python
    _soul_path = Path(__file__).parent / "templates" / "soul.md.template"
    if _soul_path.exists():
        _TEMPLATES["identity/SOUL.md"] = _soul_path.read_text(encoding="utf-8")

# -- starter templates -----------------------------------------------------

_TEMPLATES["_templates/note.md"] = """\
---
title: <title>
date: YYYY-MM-DD
tags: [<tag>]
---

# <title>

<Your content here>
"""

_TEMPLATES["_templates/correction.md"] = """\
---
title: <correction title>
date: YYYY-MM-DD
tags: [correction]
---

# Correction: <title>

**Context:** <when / where this applies>

**Rule:** <what the agent should do differently>

**Example:** <brief example>
"""

_TEMPLATES["_templates/pattern.md"] = """\
---
title: <pattern name>
date: YYYY-MM-DD
tags: [pattern]
---

# Pattern: <name>

**Problem:** <what problem does this solve?>

**Solution:** <the reusable approach>

**When to use:** <conditions or triggers>
"""

_TEMPLATES["_templates/project.md"] = """\
---
title: <project name>
date: YYYY-MM-DD
tags: [project]
---

# Project: <name>

**Status:** <active | paused | completed>

**Goal:** <one-line goal>

**Key links:** (add wikilinks here, e.g. `[[related-project]]`)

## Notes

<Free-form project notes>
"""

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScaffoldAction:
    """One scaffold step — what happened for one target file/dir."""
    path: Path
    action: str          # "created" | "skipped_existed" | "overwrote" | "would_create" | "would_overwrite"
    category: str        # "shelf_readme" | "convention" | "template" | "special_readme"

@dataclass(frozen=True)
class ScaffoldReport:
    """Summary of one scaffold_vault invocation."""
    vault_root: Path
    actions: Tuple[ScaffoldAction, ...]
    created_count: int
    skipped_count: int
    overwrote_count: int
    dry_run: bool

    def summary_line(self) -> str:
        parts = []
        if self.created_count:
            parts.append(f"created {self.created_count}")
        if self.skipped_count:
            parts.append(f"skipped {self.skipped_count}")
        if self.overwrote_count:
            parts.append(f"overwrote {self.overwrote_count}")
        if not parts:
            return "no changes"
        return ", ".join(parts)

class VaultScaffoldError(RuntimeError):
    """Raised when scaffolding cannot proceed safely."""

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _category_for(rel_path: str) -> str:
    if rel_path.startswith("_templates/"):
        return "template"
    if rel_path in ("README.md", "CONVENTIONS.md", "CATEGORISATION.md"):
        return "convention"
    if rel_path.startswith("_") and rel_path.endswith("/README.md"):
        return "special_readme"
    return "shelf_readme"

def _validate_target(target: Path, rel_path: str, vault_root: Path) -> None:
    # Ensure target is inside vault_root
    try:
        target.resolve().relative_to(vault_root.resolve())
    except ValueError:
        raise VaultScaffoldError(
            f"Target path {rel_path} resolves outside vault root {vault_root}"
        )
    # Check for type conflict
    if target.exists() and target.is_dir():
        raise VaultScaffoldError(
            f"Expected a file at {rel_path} but found a directory: {target}"
        )

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scaffold_vault(
    vault_paths: VaultPaths,
    *,
    overwrite: bool = False,
    dry_run: bool = False,
) -> ScaffoldReport:
    """Populate an empty vault skeleton with conventions, READMEs, and templates.

    Args:
        vault_paths: VaultPaths instance (skeleton must already exist).
        overwrite: If True, overwrite existing files.
        dry_run: If True, compute actions but perform no writes.

    Returns:
        ScaffoldReport summarising what happened (or would happen).
    """
    # Preconditions
    if not vault_paths.vault_root.exists():
        raise VaultScaffoldError(
            f"Vault root does not exist: {vault_paths.vault_root}. "
            f"Run vault_paths.ensure_skeleton() first."
        )
    if not vault_paths.inbox.exists():
        raise VaultScaffoldError(
            f"Inbox missing: {vault_paths.inbox}. "
            f"Run vault_paths.ensure_skeleton() to create the skeleton."
        )

    actions = []
    created = skipped = overwrote = 0

    for rel_path, content in _TEMPLATES.items():
        target = vault_paths.vault_root / rel_path
        _validate_target(target, rel_path, vault_paths.vault_root)

        category = _category_for(rel_path)

        if target.exists():
            if overwrite:
                if dry_run:
                    action = "would_overwrite"
                else:
                    _atomic_write(target, content)
                    action = "overwrote"
                    overwrote += 1
            else:
                action = "skipped_existed"
                skipped += 1
        else:
            if dry_run:
                action = "would_create"
            else:
                _atomic_write(target, content)
                action = "created"
                created += 1

        actions.append(ScaffoldAction(
            path=target.resolve(),
            action=action,
            category=category,
        ))

    return ScaffoldReport(
        vault_root=vault_paths.vault_root.resolve(),
        actions=tuple(actions),
        created_count=created,
        skipped_count=skipped,
        overwrote_count=overwrote,
        dry_run=dry_run,
    )

def is_scaffolded(vault_paths: VaultPaths) -> bool:
    """Return True if all scaffold target files exist."""
    for rel_path in _TEMPLATES:
        target = vault_paths.vault_root / rel_path
        if not target.is_file():
            return False
    return True