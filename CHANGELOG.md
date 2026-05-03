# Changelog

All notable changes to this project will be documented in this file. Format adheres to [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project follows [Semantic Versioning](https://semver.org/).

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
