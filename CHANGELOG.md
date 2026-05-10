# Changelog

All notable changes to this project will be documented in this file. Format adheres to [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project follows [Semantic Versioning](https://semver.org/).

## [0.3.3] — 2026-05-11

### Changed
- **Doctrine: GitHub canonical, local Gitea mirror.** Earlier internal docs framed local Gitea (`http://127.0.0.1:8300/`) as the primary remote with GitHub as a mirror. That story is retired. Reality: every active repo's `origin` is `github.com/spaice-ai/*`. Public `spaice-ai/spaice-agent` is the **Jarvis release** — the surface a fresh user installs from. Other framework repos (engine, hub, library, assistant, scope) live on private `spaice-ai/*`. Gitea remains running as offline-survival mirror only — never primary, never the source the installer fetches from.
- Bootstrap and install.sh headers updated to make this canonical-source story explicit so downstream agents bootstrapped via the one-liner inherit the correct mental model from day one.
- Version pin in bootstrap.sh + install.sh advanced to `v0.3.3`.

### Notes
- `pyproject.toml` was stuck at 0.3.0 across the v0.3.1 + v0.3.2 commits; this release re-syncs it to 0.3.3 in the same release. No functional regression — the pin is consumed by `pip install spaice-agent @ git+...@<tag>`, and the tag was correct, but `pip show spaice-agent` reported `0.3.0` for installs between 0.3.1 and 0.3.2. Now correct.

## [0.3.2] — 2026-05-08

### Added
- `consensus` dual-mode pipeline (`use_consensus.py`):
  - thinking: DeepSeek V4 Pro → GPT-5.5 → DeepSeek V4 Pro → Opus 4.7 synthesis
  - coding: Opus 4.7 → GPT-5.3 Codex → DeepSeek V4 Pro → Opus 4.7 synthesis
- `bootstrap.sh` header documents both pipelines so the installer makes the consensus model picks visible.

### Changed
- `README.md` — deprioritized OS cron backends; Hermes internal scheduler is sufficient for memory cron jobs.

## [0.3.1] — 2026-05-06

### Fixed
- `install.sh` step numbering corrected (was 1-2/6 → now 1-2/7) — non-functional cosmetic.
- Bootstrap pin advanced from v0.3.0 → v0.3.1.
- Stale placeholder URL in `install.sh` replaced with real raw URL; `SPAICE_HERMES_VENV` override path documented.

## [0.3.0] — 2026-05-04

### Added
- Bundled skill `memory-conventions` — generic vault discipline shipped with every install (Desk/Shelves/Inbox model, 8-layer Dewey stack with 400 reserved, frontmatter contracts, wikilink cross-reference rules, CATEGORISATION.md priority-rule pattern, search-first retrieval, supersede-don't-delete, anti-patterns).
- Installer step 6 — deploys the `spaice-agent` CLI dispatcher shim to `~/.local/bin/` (backs up any pre-existing non-routing shim before overwrite).
- `spaice_agent/packaging/spaice-agent-shim.sh` — shim source of truth, ships inside the package via `pyproject.toml` package-data.
- Tests: `test_shim_cli_sync.py` (shim subcommand list must match `cli.py` subparsers), `test_bundled_skills_have_no_business_strings` (banned-token coverage extended to shipped skills).

### Changed
- `CONVENTIONS.md` scaffold template rewritten to document all four frontmatter contracts: required, optional, classifier-written (11 fields matching `mine.py`'s emission), and continuity.
- `CATEGORISATION.md` scaffold template rewritten as a priority-rule table (10 rules, first-match-wins) plus the Dewey stack and the classifier output schema.
- `dewey_layer` value format normalised to `"NNN Label"` across all templates and skills.
- `pyproject.toml` license switched from `Proprietary` to `MIT`; homepage/repository/issues URLs added.
- Sanitised `spaice-build-stack` bundled skill of historical user-specific references.

### Fixed
- CLI dispatcher shim — `spaice-agent mine`, `triage`, `vault`, `summarise`, `dashboards`, `recall`, `audit`, and other package subcommands no longer fall through to Hermes' top-level argparser and return "invalid choice" errors. Shim now routes 14 known subcommands to the Python CLI and passes every other command through to Hermes unchanged.

## [0.2.0] — 2026-05-04

### Added
- Memory subsystem `spaice_agent.memory.*` — `paths`, `capture`, `recall` (Phase 1A), `classify`, `triage`, `mine` (Phase 1B), `dashboards`, `audit`, `summarise`, `library_index`, `continuity` (Phase 1C), `vault` scaffold (Phase 2C).
- CLI subcommands: `vault scaffold`, `mine`, `triage`, `dashboards`, `recall`, `summarise`, `audit`.
- Install modes: `install.sh --with-vault`, `install.sh --full`.
- Doctor v2 — `spaice-agent doctor` now checks memory vault + inbox + continuity + dashboard presence, BuildGuard log writability, exemption-file absence.
- Pre-push Codex hook source at `scripts/pre-push.sh` — blocks push on `[severity=blocker]` findings. Not auto-installed on clone; see [CONTRIBUTING.md](CONTRIBUTING.md) for the one-line setup.

### Changed
- All memory modules ≥200 LOC written by DeepSeek V4 Pro under the BuildGuard v1 pipeline; Codex 5.3 adversarial review mandatory.

## [0.1.0] — 2026-05-02

### Added
- Initial FW-1 framework — credentials, budget, triggers, openrouter_client, memory store+recall, search, consensus, orchestrator, advisory, handler modules.
- Install CLI, hook shim generator, bundled skills loader, antigravity vendored library.

Note: v0.1.0 was never publicly linked.

[0.3.0]: https://github.com/spaice-ai/spaice-agent/releases/tag/v0.3.0
[0.2.0]: https://github.com/spaice-ai/spaice-agent/releases/tag/v0.2.0
[0.1.0]: https://github.com/spaice-ai/spaice-agent/releases/tag/v0.1.0
