#!/usr/bin/env bash
# Pre-push Codex 5.3 review — Phase 3B, spaice-agent v0.2.0.
#
# Fires codex exec on the diff being pushed, saves review under
# reviews/pre-push-<sha>.md, blocks the push if Codex reports any
# `severity=blocker` finding.
#
# Bypass (tactical escape-hatch only):
#   SPAICE_SKIP_CODEX_PREPUSH=1 git push ...
#
# Requires: codex CLI on PATH (codex-cli ≥ 0.124).

set -euo pipefail

# --- bypass -----------------------------------------------------------------
if [[ "${SPAICE_SKIP_CODEX_PREPUSH:-0}" == "1" ]]; then
  echo "pre-push: SPAICE_SKIP_CODEX_PREPUSH=1 — skipping Codex review" >&2
  exit 0
fi

# --- sanity: codex available ------------------------------------------------
if ! command -v codex >/dev/null 2>&1; then
  echo "pre-push: codex CLI not found on PATH — install codex-cli or set SPAICE_SKIP_CODEX_PREPUSH=1" >&2
  exit 1
fi

# --- read remote refs from stdin (git passes them like this) ---------------
# Each line: <local ref> <local sha> <remote ref> <remote sha>
# We only care about the newest local sha being pushed that actually has a diff.
z40="0000000000000000000000000000000000000000"
LOCAL_SHA=""
REMOTE_SHA=""

while read -r local_ref local_sha remote_ref remote_sha; do
  if [[ "$local_sha" == "$z40" ]]; then
    # Deleting a branch — nothing to review.
    continue
  fi
  LOCAL_SHA="$local_sha"
  REMOTE_SHA="$remote_sha"
  break
done

if [[ -z "$LOCAL_SHA" ]]; then
  # Only branch deletions or nothing to push — allow.
  exit 0
fi

# --- compute diff range -----------------------------------------------------
if [[ "$REMOTE_SHA" == "$z40" ]]; then
  # New branch — diff against main (or whatever upstream default is).
  DEFAULT_BRANCH="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's@^origin/@@' || echo main)"
  RANGE="origin/${DEFAULT_BRANCH}...${LOCAL_SHA}"
else
  RANGE="${REMOTE_SHA}..${LOCAL_SHA}"
fi

DIFF="$(git diff "$RANGE" -- 'spaice_agent/**/*.py' 'tests/**/*.py' 2>/dev/null || true)"

if [[ -z "$DIFF" ]]; then
  echo "pre-push: no Python changes in range $RANGE — skipping Codex review"
  exit 0
fi

# --- prepare review output --------------------------------------------------
REPO_ROOT="$(git rev-parse --show-toplevel)"
REVIEWS_DIR="$REPO_ROOT/reviews"
mkdir -p "$REVIEWS_DIR"
SHORT_SHA="$(git rev-parse --short "$LOCAL_SHA")"
OUT="$REVIEWS_DIR/pre-push-${SHORT_SHA}.md"

echo "pre-push: firing Codex review on $RANGE → $OUT" >&2

# --- fire codex -------------------------------------------------------------
PROMPT="Adversarially review the following git diff for the spaice-agent package.
Flag correctness bugs, memory leaks, race conditions, missing input validation,
credential leaks, or doctrine violations (Azure autonomy, no macOS paths in
shippable code, no lingering BuildGuard exemptions).

For each finding, use the format:
  N. [severity=blocker|major|minor] <one-line title>
     <detail paragraph>

Use severity=blocker ONLY for issues that must not ship (data loss, auth bypass,
crashes on valid input, or doctrine violations). Ship-blockers use exactly the
string 'severity=blocker' in square brackets so pre-push can grep for it.

If the diff is clean, say 'VERDICT: ship' on the first line and stop.

Diff:
---
$DIFF"

# codex exec writes to stdout; capture it.
# Use --full-auto if available; otherwise fall back to `exec`.
if ! codex exec "$PROMPT" > "$OUT" 2>&1; then
  echo "pre-push: codex invocation failed — see $OUT" >&2
  exit 1
fi

# --- gate on severity=blocker -----------------------------------------------
if grep -qE 'severity\s*=\s*blocker' "$OUT"; then
  echo "" >&2
  echo "╔═══════════════════════════════════════════════════════════════════╗" >&2
  echo "║  pre-push: BLOCKED — Codex found severity=blocker finding(s)      ║" >&2
  echo "╚═══════════════════════════════════════════════════════════════════╝" >&2
  echo "" >&2
  grep -nE 'severity\s*=\s*blocker' "$OUT" >&2 || true
  echo "" >&2
  echo "Full review: $OUT" >&2
  echo "Bypass (not recommended): SPAICE_SKIP_CODEX_PREPUSH=1 git push ..." >&2
  exit 1
fi

echo "pre-push: Codex review clean — push allowed ($OUT)" >&2
exit 0
