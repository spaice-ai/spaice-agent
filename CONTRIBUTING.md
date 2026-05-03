# Contributing

Thanks for looking at `spaice-agent`. Contributions are welcome.

## Before you start

**Open an issue first** for non-trivial changes. A 10-line bug fix doesn't need a pre-discussion, but a new CLI subcommand or memory subsystem module does. This saves everyone time when the maintainers have context you don't.

## Dev setup

```bash
git clone https://github.com/spaice-ai/spaice-agent.git
cd spaice-agent
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                         # baseline: 596/596 should pass
```

## Development workflow

1. **Branch from `main`** — short descriptive name (`fix/shim-path-resolution`, `feat/cron-install`).
2. **Write tests first** for behaviour changes. We use pytest with `asyncio_mode = "auto"`.
3. **Run the build-stack pipeline** for any production code change ≥ 50 LOC (see `spaice_agent/bundled_skills/spaice-build-stack/SKILL.md` for the full pipeline — it's the skill your agent should load). Short version:
   - Framework spec (what, why, public API)
   - Implementation (DeepSeek V4 Pro for ≥200 LOC, direct-typed for mechanical wiring)
   - Adversarial review by Codex 5.3 on the diff
   - Fix cycle, regression tests for each blocker finding
4. **Keep commits focused** — one logical change per commit. No WIP commits in PRs (squash first).
5. **Open a PR** with a description that answers: what changed, why, how it was tested, any API breaks.

## Pre-push hook

This repo ships a pre-push hook at `scripts/pre-push.sh` that runs Codex review on the diff being pushed. It's **not** automatically installed — Git doesn't sync `.git/hooks/` with fresh clones. To enable it after cloning:

```bash
ln -sf ../../scripts/pre-push.sh .git/hooks/pre-push
chmod +x .git/hooks/pre-push
```

Once installed, it blocks the push on any `[severity=blocker]` finding. To bypass in exceptional cases:

```bash
SPAICE_SKIP_CODEX_PREPUSH=1 git push
```

Bypasses are logged. Use sparingly and explain in the PR.

## Tests

- **All behaviour changes need tests.** A PR without tests for new behaviour will be asked to add them before review.
- **Regression tests for each blocker finding.** If Codex review catches a real bug, the fix must include a test that would have caught it.
- **Don't break the banned-token tests** — `test_content_has_no_business_strings` and `test_bundled_skills_have_no_business_strings` guard against user-specific data leaking into the shipped package.

## Style

- `from __future__ import annotations` at the top of every module.
- Type hints on all public functions.
- `pathlib.Path`, not `os.path`.
- `@dataclass(frozen=True)` for result types.
- Kebab-case for shell scripts and CLI args, snake_case for Python.
- ISO 8601 dates in docstrings and file naming.
- No f-strings that could accidentally interpolate user input into shell commands — use `subprocess` with list args.

## Commit messages

Conventional style. First line ≤ 70 chars, imperative mood:

```
Fix shim path resolution on macOS without Homebrew

The shim's HERMES_BIN fallback assumed /usr/local/bin/hermes exists, but
on Apple Silicon with Homebrew in /opt/homebrew/bin, the fallback never
fired. Now probes both paths and the $HERMES_HOME env if set.

Fixes #N
```

If Codex reviewed the diff, add:

```
Reviewed-by: openai/gpt-5.3-codex (<N> findings, all addressed)
```

## Bundled skills

The `spaice_agent/bundled_skills/` directory contains skills that ship with every install. Adding or modifying these has user-facing impact:

- **Adding a skill** — requires (a) MIT-compatible license, (b) no user-specific content (the banned-tokens test enforces this for in-house skills), (c) entry in `BUNDLED_SKILLS` list in `cli.py`.
- **Modifying the `memory-conventions` skill** — this ships to every install and defines the vault contract. Changes need care — breaking the frontmatter schema breaks every existing vault.
- **Modifying the `spaice-build-stack` skill** — this is the coding workflow itself. Discuss in an issue first.

Upstream MIT-imported skills (antigravity, office-suite, gmail, etc.) are in the exempt list — don't modify their content in this repo; patch upstream and re-vendor.

## Questions

Open a discussion or an issue. Don't DM maintainers for project questions — use the public channels so answers help the next person.
