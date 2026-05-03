# Phase 2A + 2B — Codex 5.3 review triage

**Date:** 2026-05-03
**Reviewer:** `openai/gpt-5.3-codex` (correction 017)
**Scope:** install.sh flag parsing + 7 new CLI subcommands + 2 audit fixes

## Pass 1 ($0.0313)

Reviewed the combined diff (spaice_agent/cli.py + spaice_agent/memory/audit.py + install.sh + vault content).

### Findings

| # | Sev | Finding | Verdict |
|---|---|---|---|
| 1 | major | install.sh mis-parses `sh -s jarvis --with-vault` (no version_spec) | **ACCEPT** |
| 2 | major | `vault agent_id` argparse says "optional/default from context" but code hard-fails | **ACCEPT** |
| 3 | major | `mine --limit` flag wired into argparse but ignored by `cmd_mine` | **ACCEPT** |

All three fixed in one commit; test_phase2b_cli_fixes.py verifies all via subprocess.

## Pass 2 ($0.0315)

Re-fired Codex on the fix diff. Three more real findings surfaced.

### Findings

| # | Sev | Finding | Verdict |
|---|---|---|---|
| 1 | major | `vault scaffold --dry-run` calls `ensure_skeleton` (writes dirs), violating dry-run contract | **ACCEPT** |
| 2 | major | install.sh silently accepts unknown flags as `WARN` — typos pass through | **ACCEPT** (changed to hard `ERROR` + exit 1) |
| 3 | major | install.sh `--with-vault` wraps scaffold in `|| {...}` — failures silently downgraded | **ACCEPT** (propagate exit code, fail loudly) |

All three fixed + regression tests added.

## Result

- Two Codex passes caught **6 real bugs** across install.sh + CLI dispatcher
- Zero false positives (both passes)
- 5 regression tests in `test_phase2b_cli_fixes.py` — subprocess-based, black-box
- Total review cost: **$0.0628** ($0.0313 + $0.0315)
- Test count: 588 → **593 passing**

## Takeaway

Dogfooding the scaffold against `audit` surfaced content-quality issues that tests didn't catch (duplicate README flagging, wikilink documentation false-positives). Codex then caught CLI contract mismatches that manual testing missed. **Dogfood + Codex-review is the right combo for CLI work** — neither alone was sufficient.

Side benefits:
- `check_duplicate_files` now exempts README.md/index.md (won't flag intentional per-dir files in ANY user vault, not just our scaffold).
- `_extract_wikilinks` now strips fenced code + inline code (won't flag documentation of wikilink syntax).
- Both are net improvements to the audit module independent of the scaffold.

## Rejected findings

None. Both passes had zero false positives. Codex 5.3 is delivering.
