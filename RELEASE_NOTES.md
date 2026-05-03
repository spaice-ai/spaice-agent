# Release Notes

## v0.3.0 — 2026-05-04

**Memory conventions ship with the package.** A fresh install now produces a vault that the classify / triage / summarise / audit pipeline can operate on from turn one — no user-authored schema required.

### New — Bundled skill: `memory-conventions`

Generic, zero-branding skill that teaches agents how the vault works: the Desk / Shelves / Inbox model, 9 canonical shelves, required + optional + classifier-written + continuity frontmatter contracts, the 8-layer Dewey stack (`000 General / 100 People / 200 Projects / 300 Systems / 400 reserved / 500 Technical / 600 Preferences / 700 Problems`), wikilink cross-reference discipline, `CATEGORISATION.md` routing via first-match-wins priority rules, search-first-then-read retrieval reflex, continuity handshake, supersede-don't-delete, and common anti-patterns. Ships alongside `spaice-build-stack` and the utility skills.

### Upgraded — Vault scaffold templates

- `CONVENTIONS.md` now documents all four frontmatter contracts the automation reads: required (`title`, `date`, `tags`), optional (`status`, `dewey_layer`, `source`, `refs`, `updated`, `valid_to`), classifier-written (11 `classifier_*` fields that the miner emits into `_inbox/` drafts), and continuity (`source_session`, `summary_generated_at`, `summary_method`, `schema_version`).
- `CATEGORISATION.md` now ships as a priority-rule table (10 rules, first match wins) plus the Dewey stack and the classifier output schema. This is the system prompt the LLM classifier reads, so correct defaults mean correct routing from turn one.
- `dewey_layer` normalised to the `"NNN Label"` format everywhere (matches the LLM contract in `classify.py`).

### Fixed — CLI dispatcher shim

Prior releases installed a `spaice-agent` shim at `~/.local/bin/` that blindly forwarded every arg to the underlying Hermes binary. Consequence: `spaice-agent mine`, `triage`, `vault`, `summarise`, etc. returned `argparse: invalid choice` errors because they never reached the Python CLI. Fixed by rewriting the shim to route the 14 package-owned subcommands (`install`, `uninstall`, `list`, `upgrade`, `version`, `doctor`, `skills`, `vault`, `mine`, `triage`, `summarise`, `dashboards`, `recall`, `audit`) to `python -c "from spaice_agent.cli import main; ..."` while passing every other command through to Hermes unchanged. Installer now drops the shim during step 6, backing up any pre-existing non-routing version.

### Hardening

- New `tests/test_shim_cli_sync.py` — regression guard comparing the shim's `SPAICE_SUBCOMMANDS` list against `cli.py`'s subparsers. Drift fails the build.
- Extended `test_content_has_no_business_strings` with a companion `test_bundled_skills_have_no_business_strings` that asserts no user-specific tokens (`SPAICE`, `Tron`, `Jozef`) leak into the shipped skill bundle. Upstream MIT-imported skills (antigravity, office-suite, gmail, etc.) are exempt.
- Sanitised `spaice-build-stack` skill of historical Jozef/Tron references while preserving the build-stack doctrine.
- License clarified to MIT (`pyproject.toml` + `LICENSE` file). Supersedes the `Proprietary` placeholder in prior releases — code was MIT in practice, now formally.

### Migration from v0.2.0

- `spaice-agent upgrade` refreshes everything — package, bundled skills, and the `~/.local/bin/spaice-agent` shim.
- Existing vaults: `spaice-agent vault scaffold <agent_id>` regenerates the upgraded `CONVENTIONS.md` + `CATEGORISATION.md` templates only if they're missing. To pick up the new content on an existing vault, either (a) `spaice-agent vault scaffold <agent_id> --overwrite` (force-rewrites — backs up previous copies), or (b) diff manually and merge.
- No code or API breakage — new content, same entry points.

### Tests

`595/595 → 596/596` passing. `install.sh` shell syntax verified.

---

## v0.2.0 — 2026-05-04

**The memory loop ships.** End-to-end: store → mine → classify → file → recall + continuity + dashboards + entity cache.

### New — Memory subsystem (`spaice_agent.memory`)

- **Phase 1A** `paths.py`, `capture.py`, `recall.py` — vault path conventions, inbox capture with dedup, recall over fact files.
- **Phase 1B** `classify.py`, `triage.py`, `mine.py` — session miner that drafts classifier entries to `_inbox/` on an hourly cadence, triage pipeline that promotes inbox drafts to canonical categories.
- **Phase 1C** `dashboards.py`, `audit.py`, `summarise.py`, `library_index.py`, `continuity.py` — auto-regenerated dashboards, vault audit, session summarisation, library indexing, and continuity handoff (`_continuity/LATEST.md`).
- **Phase 2C** `vault.py` — `scaffold_vault()` writes the full skeleton (`_inbox/`, `_continuity/`, `_dashboard/`, `_templates/`, `_archive/`, identity/projects/sites/patterns/learnings/corrections subtrees, README/CONVENTIONS/CATEGORISATION/LOG/INDEX).

### New — CLI subcommands (Phase 2B)

```
spaice-agent vault scaffold <agent_id>
spaice-agent mine <agent_id>
spaice-agent triage <agent_id>
spaice-agent dashboards <agent_id>
spaice-agent recall <agent_id> "<query>"
spaice-agent summarise <agent_id> [--session <id>]
spaice-agent audit <agent_id>
```

Each subcommand calls into the corresponding `spaice_agent.memory.*` module and exits 0/1.

### New — Install modes (Phase 2A)

- `install.sh --with-vault` — scaffold the `~/<agent_id>/` memory vault skeleton.
- `install.sh --full` — shorthand for `--with-vault` (+ reserves future `--with-*` flags).
- Backward compatible: bare `install.sh <agent_id>` still works.

### Hardening — Ship readiness (Phase 3)

- **Doctor v2 (3A)**: `spaice-agent doctor <agent_id>` verifies memory vault root + `_inbox/` / `_continuity/` / `_dashboard/` presence, BuildGuard log writability, and absence of stale exemption files.
- **Pre-push Codex hook (3B)**: `scripts/pre-push.sh` fires `codex exec` on the diff being pushed, saves the review to `reviews/pre-push-<sha>.md`, blocks the push on any `[severity=blocker]` finding. Bypass via `SPAICE_SKIP_CODEX_PREPUSH=1`.
- **Release tag (3C)**.

### Engineering hygiene

- All memory modules ≥ 200 LOC written by DeepSeek V4 Pro under the BuildGuard v1 pipeline, reviewed by Codex 5.3.
- `489/489 → 593/593` tests pass.
- Generic-by-default: business data stripped from the shippable package.

---

## v0.1.0 — 2026-05-02 (tag only, not publicly linked)

Initial FW-1 framework: consensus separated from pre-turn hook, modules 1-10 (credentials, budget, triggers, openrouter_client, memory store+recall, search, consensus, orchestrator, advisory, handler), install CLI, hook shim generator, bundled skills loader, antigravity vendored library.
