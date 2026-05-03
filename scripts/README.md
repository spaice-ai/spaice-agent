# scripts/

Operational scripts for spaice-agent development.

## pre-push.sh

Pre-push Codex 5.3 review hook (Phase 3B, v0.2.0).

### Install

```bash
cp scripts/pre-push.sh .git/hooks/pre-push
chmod +x .git/hooks/pre-push
```

### Behaviour

- Fires `codex exec` on the diff being pushed (filtered to `spaice_agent/**/*.py` and `tests/**/*.py`).
- Saves the review to `reviews/pre-push-<short-sha>.md`.
- Blocks the push if Codex reports any `[severity=blocker]` finding.
- No-ops when only non-Python files change or when deleting branches.

### Bypass

Tactical escape-hatch — use sparingly, explain in the commit or PR:

```bash
SPAICE_SKIP_CODEX_PREPUSH=1 git push
```

### Requirements

- `codex` CLI on `PATH` (codex-cli ≥ 0.124).
- `git` (obviously).
