# Code Review: spaice_agent/memory/paths.py

VERDICT: needs revision

FINDINGS:

1. [severity=major] Missing PyYAML import guard breaks error messages
   The try/except at lines 28-30 sets `yaml = None` on ImportError, but line 68 uses `yaml is not None` as a condition without handling the case where yaml is None but config_path exists. If PyYAML is missing (despite being a hard dep), the code silently falls through to convention fallback instead of raising a clear error about the missing dependency.

2. [severity=major] Race condition in for_agent with create_agent_dir
   Lines 88-89 create agent_config_dir only if `create_agent_dir=True`, but this happens AFTER attempting to read config.yaml at line 65. If the config dir doesn't exist yet, config_path.exists() returns False, causing fallback to convention even when the user intended to use a configured vault. The create flag should be checked/applied before attempting config read.

3. [severity=minor] Inconsistent error message about scaffolding command
   Line 83 references `spaice-agent vault scaffold {agent_id}` (Phase 2), but line 145 also references the same command. Since Phase 2 isn't implemented yet, these error messages will be misleading to users in Phase 1A. Consider either removing the command reference or making it conditional on Phase 2 availability.

4. [severity=minor] validate() only checks inbox, not other critical dirs
   Lines 150-157 only validate vault_root and _inbox existence, but the docstring says "check skeleton is complete." The _continuity dir is also critical for the "continue" feature mentioned in SPECIAL_DIRS comments. Either validate all SPECIAL_DIRS or update the docstring to clarify only inbox is required.

5. [severity=minor] Missing validation in for_vault constructor
   The `for_vault` classmethod (lines 93-105) accepts an arbitrary agent_id with default "_standalone" but doesn't validate that agent_id is non-empty like `for_agent` does at line 59. This inconsistency could lead to Path objects with empty components.
