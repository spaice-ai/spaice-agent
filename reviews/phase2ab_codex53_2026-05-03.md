VERDICT: needs revision  
FINDINGS:
1. [severity=major] `install.sh` mis-parses flags when `version_spec` is omitted.
   `VERSION_SPEC` is always read from `$2`, then arguments are shifted by 2 unconditionally (`install.sh`, flag parsing block).  
   So `sh -s jarvis --with-vault` sets `VERSION_SPEC=--with-vault` and never enables `WITH_VAULT`, causing an invalid version/ref install attempt.  
   This conflicts with the documented optional `[version_spec]` syntax.

2. [severity=major] `vault` CLI advertises optional `agent_id` with context fallback, but implementation hard-fails without it.
   In `main()`, `p_vault.add_argument("agent_id", nargs="?", help="...default: from current dir context")` implies omission is supported.  
   But `_resolve_vault_paths()` immediately errors when `agent_id` is falsy and no context resolution is attempted.  
   This is a behavior/docs contract mismatch in `cmd_vault` path resolution.

3. [severity=major] `mine --limit` argument is accepted but ignored.
   Parser defines `--limit` (`main()`, `p_mine.add_argument("--limit", ...)`) but `cmd_mine()` calls `miner.run()` without passing any limit.  
   Users get no effect from the flag, which is incorrect behavior for the exposed CLI API.
