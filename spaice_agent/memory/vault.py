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
    return f"""# {shelf.title()}

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
    return f"""# {dir_name}

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
- `date` — ISO 8601 date (`YYYY-MM-DD`) when the content was created or last
  significantly updated.
- `tags` — a YAML list of lowercase tags. Use kebab-case for multi-word tags.

## File naming

- Use **kebab-case** for filenames: `my-project-notes.md`.
- Avoid spaces, underscores, and special characters.
- Keep names short but descriptive.

## Dates

All dates must be in **ISO 8601** format: `YYYY-MM-DD`.  Do not use
`MM/DD/YYYY` or natural-language dates.

## Wikilinks

Link to other vault pages using wikilink syntax:

- `[[target]]` — links to `target.md` in the same vault.
- `[[target|alias]]` — displays “alias” but links to `target.md`.

Wikilinks are resolved relative to the vault root.  Do not use absolute
filesystem paths.

## General style

- Write in clear, concise English.
- Use headings to structure longer pages.
- Keep entries focused — one topic per file.
"""

_TEMPLATES["CATEGORISATION.md"] = """\
# Categorisation Guide

Use this decision tree to choose the right shelf for new content.

- **Is it about you?**
  - Core facts, roles, self-descriptions → `identity/`
  - Personal context, preferences, non-work → `personal/`
- **Is it a rule or correction you’ve given the agent?**
  → `corrections/`
- **Is it a reusable solution shape or design pattern?**
  → `patterns/`
- **Is it something you’ve learned (field knowledge, insight)?**
  → `learnings/`
- **Is it about a service, API, or tool?**
  → `integrations/`
- **Is it about infrastructure, hosts, or networks?**
  → `infrastructure/`
- **Is it a workstream or project?**
  → `projects/`
- **Is it a physical location, client site, or venue?**
  → `sites/`
- **Unsure?**
  → `_inbox/` — the agent will triage it later.
"""

# -- shelf READMEs ---------------------------------------------------------

for _shelf in CANONICAL_SHELVES:
    _TEMPLATES[f"{_shelf}/README.md"] = _shelf_readme(_shelf)

# -- special-dir READMEs (skip _archive) -----------------------------------

for _sdir in SPECIAL_DIRS:
    if _sdir != "_archive":
        _TEMPLATES[f"{_sdir}/README.md"] = _special_readme(_sdir)

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

**Key links:** [[...]]

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