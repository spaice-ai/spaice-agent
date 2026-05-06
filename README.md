# spaice-agent

**Persistent memory + continuity loop for Hermes-based AI agents.** Capture facts as you work, classify them automatically, resume where you left off next session. A vault your agent actually reads.

[![Version](https://img.shields.io/badge/version-0.3.2-blue.svg)](https://github.com/spaice-ai/spaice-agent/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](pyproject.toml)
[![Tests](https://img.shields.io/badge/tests-596%2F596-green.svg)](tests/)

## What it does

`spaice-agent` runs as a pre-turn hook on top of Hermes (the agent runtime) and turns every conversation into durable, searchable knowledge:

- **Mines** your session transcripts for durable facts (decisions, corrections, preferences, references)
- **Classifies** them via an LLM using a routing table you control (`CATEGORISATION.md`)
- **Files** them to a structured markdown vault you can read, edit, and commit
- **Summarises** each session into a continuity record so "continue where we left off" actually works
- **Indexes** the vault for search-first recall across past conversations
- **Audits** the vault for broken cross-references, frontmatter gaps, orphan files

All markdown. Obsidian-compatible. Markdown vault + pgvector recall database. Under git.

## Why

LLM conversations are ephemeral. Context windows compact and lose detail. Copy-pasting chat logs into notes is noise. What you actually need is structured extraction — facts go to the right shelf, decisions are timestamped and attributed, cross-references knit the knowledge graph together, and next session you wake up with a concrete "here's where we were" instead of a half-forgotten chat history.

That's what `spaice-agent` does, running quietly alongside your agent.

## Quick start

**Fresh machine — installs Hermes + spaice-agent end-to-end:**

```bash
curl -fsSL https://raw.githubusercontent.com/spaice-ai/spaice-agent/v0.3.2/bootstrap.sh | bash
```

You'll be asked for 5 values:

1. Agent name (defaults to `jarvis`)
2. OpenRouter API key (covers Opus, DeepSeek V4 Pro, Codex 5.3, auxiliary — one key, whole stack)
3. Exa API key (web search)
4. Telegram bot token (from @BotFather)
5. Telegram user ID allowed to talk to the bot (find via @userinfobot)

Everything else is pre-configured: Opus 4.7 main, DeepSeek V4 Pro for code ≥200 LOC, Codex 5.3 for review, Telegram home channel, local terminal, vault at `~/<agent>/`.

**Already have Hermes installed?** Skip the bootstrap and install spaice-agent directly:

```bash
# Pinned release
curl -fsSL https://raw.githubusercontent.com/spaice-ai/spaice-agent/v0.3.2/install.sh | sh -s myagent v0.3.2 --full

# Bleeding edge (main branch — may be unstable)
curl -fsSL https://raw.githubusercontent.com/spaice-ai/spaice-agent/main/install.sh | sh -s myagent main --full
```

The installer:
- Installs spaice-agent package into your Hermes venv
- Initialises the memory database schema (pgvector + spatial index)
- Sets up the Hermes hook + per-agent config
- Installs bundled skills (memory-conventions, build-stack, and utility skills)
- Scaffolds `~/myagent/` as your memory vault
- Installs the `spaice-agent` CLI shim at `~/.local/bin/`

Then check:

```bash
# If ~/.local/bin is on your PATH (installer warns if not):
spaice-agent list              # shows your agent
spaice-agent doctor myagent    # verifies install end-to-end

# If spaice-agent isn't found, ~/.local/bin probably isn't on your PATH.
# Either add it to your shell rc:
export PATH="$HOME/.local/bin:$PATH"

# ...or find the venv CLI directly. The installer reported its location
# as "HERMES_VENV" — check the install log for the exact path. Common
# locations:
ls -l ~/.hermes/hermes-agent/venv/bin/spaice-agent 2>/dev/null \
  || ls -l ~/.Hermes/hermes-agent/venv/bin/spaice-agent 2>/dev/null \
  || ls -l ~/.Hermes/venv/bin/spaice-agent 2>/dev/null
# Then run the found path directly:
<that-full-path> list
```

## How memory works

The vault at `~/<agent>/` is structured markdown organised into three tiers:

```
┌────────────────────────────────────────────────────────┐
│ THE DESK      — this conversation (ephemeral)          │
│ THE INBOX     — miner drafts pending triage            │
│ THE SHELVES   — filed facts, one topic per file        │
└────────────────────────────────────────────────────────┘
```

The miner pulls durable facts from your Hermes session JSONLs, drops classifier drafts into `_inbox/`, and triage promotes them to the right shelf based on confidence + `CATEGORISATION.md` rules. At session end, the summariser writes `_continuity/LATEST.md` — that's what your next "continue" reads first.

A pgvector-backed recall database (`memory_entries` + `memory_links`) provides sub-100ms ILIKE text search with optional semantic vector enhancement and multi-hop traversal across linked entries (CORRECTS, RELATED_TO, CASCADES_FROM).

Full detail: the bundled `memory-conventions` skill (loaded automatically by agents on install).

## Vault layout

```
~/<agent>/
├── identity/          # who the user is — name, roles, preferences
├── personal/          # non-work context — family, health, hobbies
├── corrections/       # numbered rules ("don't do X again")
├── patterns/          # reusable solution shapes
├── learnings/         # field knowledge, gotchas with resolutions
├── integrations/      # third-party services, APIs, SaaS tools
├── infrastructure/    # hosts, networks, physical devices
├── projects/          # named workstreams with deliverables
├── sites/             # physical locations, client sites, venues
│
├── _inbox/            # miner drafts pending triage
├── _continuity/       # LATEST.md resume point
├── _dashboard/        # auto-regenerated views (never hand-edit)
├── _templates/        # starter markdown for capture workflows
├── _archive/          # retired content
│
├── CONVENTIONS.md     # frontmatter + wikilink + style rules
├── CATEGORISATION.md  # priority-rule routing table (editable)
├── LOG.md             # triage-escalated items, nightly review
└── README.md          # vault home, shelf counts, pinned links
```

## CLI

```
spaice-agent install <agent_id>          Install agent hook + config
spaice-agent uninstall <agent_id>        Remove hook (optionally purge config)
spaice-agent list                        Show all installed agents
spaice-agent upgrade                     Refresh package + skills + shim
spaice-agent doctor <agent_id>           Health check (vault, hook, creds)
spaice-agent version                     Print installed version
spaice-agent memory init                 Initialise pgvector schema
spaice-agent memory index                Rebuild spatial index (cross-layer)

spaice-agent vault scaffold <agent_id>   Write vault skeleton + conventions
spaice-agent mine <agent_id>             Scan sessions, draft to _inbox/
spaice-agent triage <agent_id>           File _inbox/ drafts to shelves
spaice-agent summarise <agent_id>        Write _continuity/LATEST.md
spaice-agent dashboards <agent_id>       Regenerate _dashboard/ views
spaice-agent recall <agent_id> "<query>" Ranked search across the vault
spaice-agent audit <agent_id>            Check for broken links, orphans
spaice-agent skills <subcommand>         Manage bundled + antigravity skills
```

## Configuration

Per-agent config at `~/.spaice-agents/<agent_id>/config.yaml`:

```yaml
memory_root: ~/myagent           # vault location
triggers:                        # optional — extend recall triggers
  - pattern: "what did we do about"
    action: recall
consensus:                       # optional — 3-chair pipeline
  primary: anthropic/claude-opus-4.7
  challenger: openai/gpt-5.3-codex
  adjudicator: deepseek/deepseek-v4-pro
```

Credentials go in `~/.Hermes/credentials/` (600 perms — never in the vault):

- `openrouter.key` — for consensus + classifier
- `exa.json` — for search (optional)
- `brave.json` — for search (optional)

## Integration with Hermes

`spaice-agent` runs as a Hermes pre-turn hook. The hook at `~/.Hermes/hooks/spaice-<id>/handler.py` is a 13-line auto-generated shim that imports the Python package — bugs fix once, ship to every installed agent via `spaice-agent upgrade`.

The package itself contains:

- `spaice_agent.memory.*` — the vault subsystem (mine, triage, classify, summarise, recall, dashboards, audit, vault scaffold)
- `spaice_agent.hook` — Hermes hook factory
- `spaice_agent.orchestrator` — pre-turn decision flow
- `spaice_agent.bundled_skills/` — ships with the `memory-conventions` skill, the `spaice-build-stack` coding workflow, and utility skills for PDF, DOCX, XLSX, PPTX, and Gmail operations.

See `spaice_agent/` source for the full module tree.

## Development

```bash
git clone https://github.com/spaice-ai/spaice-agent.git
cd spaice-agent
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                         # 596 tests, ~8 seconds
```

Every production code change goes through the build-stack pipeline (see `spaice_agent/bundled_skills/spaice-build-stack/SKILL.md`):

1. Framework spec written by the integrator
2. Implementation by DeepSeek V4 Pro (≥200 LOC) or direct-typed (mechanical wiring)
3. Test suite written locally
4. Adversarial review by Codex 5.3
5. Fix cycle + commit

Pre-push hook at `scripts/pre-push.sh` fires a Codex review on the diff (see [CONTRIBUTING.md](CONTRIBUTING.md) for one-line install). Blocks the push on any `[severity=blocker]` finding. Bypass via `SPAICE_SKIP_CODEX_PREPUSH=1` (logged).

## Roadmap

**v0.3.2 (current):** pgvector memory backend — db_store.py with spatial index, multi-hop retrieval, memory init + index CLI subcommands.

**v0.3.1 (next):**
- Cron scheduler — `spaice-agent cron install <agent_id>` registers 4 jobs: hourly mine, hourly dashboards, daily triage 03:00, nightly memory lint 05:00.
- Post-turn continuity hook — auto-regenerate `_continuity/LATEST.md` after each session (debounced, atomic, trailing-edge catch-up).
- Dashboard shelf split — generic dashboards in the package, custom dashboards in the user's vault.

**v0.4+:** Hermes-tool native wrappers, multi-agent shared continuity, OS-level cron backends (lower priority — Hermes' internal scheduler already covers the core use case).

See [RELEASE_NOTES.md](RELEASE_NOTES.md) for detailed release history.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). TL;DR: fork, branch, write tests, run the build-stack pipeline on any code change, submit PR.

## Security

Never report security issues via GitHub issues. See [SECURITY.md](SECURITY.md) for disclosure.

## License

MIT — see [LICENSE](LICENSE).

First-party bundled skills (e.g. `memory-conventions`, `spaice-build-stack`) are covered by the root MIT license. Third-party skills vendored under `spaice_agent/bundled_skills/` (e.g. `antigravity`, office-suite utilities) ship with their own upstream MIT-compatible LICENSE files preserved alongside the skill. The antigravity skill library is vendored at a pinned upstream commit and retains its upstream MIT terms.
