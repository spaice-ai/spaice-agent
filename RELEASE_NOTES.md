# Release Notes

## v0.2.0 — 2026-05-04

**The memory loop ships.** End-to-end: store → mine → classify → file → recall + continuity + dashboards + entity cache. This is the moat — per doctrine "memory is business continuity", v0.2.0 is the first release where the full Jarvis feature set is replicable from the installer.

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

- **Doctor v2 (3A)**: `spaice-agent doctor <agent_id>` now verifies memory vault root (exists + writable), `_inbox/` / `_continuity/` / `_dashboard/` presence, BuildGuard log dir writability, and absence of lingering BuildGuard exemption files. Cron and openrouter log checks folded in.
- **Pre-push Codex hook (3B)**: `scripts/pre-push.sh` (also installed at `.git/hooks/pre-push`) fires `codex exec` on the diff being pushed, saves the review to `reviews/pre-push-<sha>.md`, and blocks the push on any `[severity=blocker]` finding. Bypass via `SPAICE_SKIP_CODEX_PREPUSH=1`.
- **Release tag (3C)**: this file.

### Engineering hygiene

- All memory modules ≥ 200 LOC were written by DeepSeek V4 Pro under the BuildGuard v1 pipeline, reviewed by Codex 5.3, and fix-committed before landing (per correction 009).
- `489/489 → 593/593` tests pass.
- Generic-by-default: SPAICE business data stripped from the shippable package (commit `0f481b1`).
- Hook shim auto-upgrade on `spaice-agent upgrade` keeps installed hooks synced to the package version.

### Credits

Framework authoring: Jozef Doboš (architecture, doctrine, review gates) + Jarvis (build stack orchestrator) + DeepSeek V4 Pro (production code) + Codex 5.3 (adversarial review).

### Migration from v0.1.0

- v0.1.0 was never publicly linked — installers pointing at `spaice.ai/install.sh` get v0.2.0 as first usable release.
- Existing local installs: `spaice-agent upgrade` refreshes shim + bundled skills. For the memory vault, run `spaice-agent vault scaffold <agent_id>` (or reinstall with `install.sh <agent_id> v0.2.0 --full`).

---

## v0.1.0 — 2026-05-02 (tag only, not publicly linked)

Initial FW-1 framework: consensus separated from pre-turn hook, modules 1-10 (credentials, budget, triggers, openrouter_client, memory store+recall, search, consensus, orchestrator, advisory, handler), install CLI, hook shim generator, bundled skills loader, antigravity vendored library. Not publicly announced — shipping broken URL considered worse than delay. v0.2.0 is the first release pinned for external install.
