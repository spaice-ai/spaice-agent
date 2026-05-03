VERDICT: needs revision  
FINDINGS:
1. [severity=major] `vault scaffold --dry-run` still performs filesystem writes.
   In `cmd_vault()`, `vp.ensure_skeleton()` is called unconditionally before `scaffold_vault(..., dry_run=args.dry_run)`.  
   `ensure_skeleton()` is explicitly mutating (creates dirs), so `--dry-run` does not honor its contract (“compute actions without writing”).  
   This is an observable behavior bug for users relying on dry-run safety.

2. [severity=major] Installer silently ignores unknown flags and proceeds.
   In `install.sh`, the flag loop logs `WARN: unknown flag` for unrecognized args, then continues installation.  
   A typo like `--with-vault` will be accepted with only a warning, leading to a partially unintended install state.  
   This is a fragile CLI API behavior and should fail fast for invalid flags.

3. [severity=major] `--with-vault` failures are swallowed, so install can succeed despite requested setup failing.
   In `install.sh`, vault scaffolding is wrapped with `|| { echo "⚠ ..."; }`, which suppresses non-zero exit from `"$VENV_CLI" vault scaffold "$AGENT_ID"`.  
   If a user explicitly requests `--with-vault`/`--full`, a scaffold failure should not be downgraded to a warning-only success path.  
   Current behavior can leave users with a “successful” install missing requested vault initialization.
