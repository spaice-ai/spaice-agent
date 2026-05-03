VERDICT: needs rework  
FINDINGS:
1. [severity=blocker] `audit_vault()` can crash in its own error handler due to undefined `check_name`.
   In `audit_vault`, the `except` branch logs with `check_name`, but the loop variable is `name`.  
   If any check raises, this `logger.warning(...)` line raises `NameError`, so the function violates the “must not raise” behavior and fails to return an `AuditReport`.

2. [severity=blocker] `check_missing_frontmatter()` has a broken exception path (`f` is undefined).
   In the `except (OSError, UnicodeDecodeError)` block, logging references `f`, which is never defined in this scope (should be `full`).  
   A normal read/decode failure therefore becomes a `NameError`, turning recoverable file issues into a hard check failure.

3. [severity=blocker] `check_broken_wikilinks()` has a broken exception path (`md` is undefined).
   In the file-read `except OSError` block, logging uses `md`, which is undefined (should be `full`).  
   This causes `NameError` on read failures, again escalating recoverable IO errors into exceptions that can take down the audit flow.

4. [severity=major] `check_orphaned_inbox()` hidden-file filtering is based on absolute path parts, causing false skips.
   The condition `any(part.startswith(".") for part in md_file.parts)` inspects full absolute path components, not path relative to `vault_root`.  
   If the vault lives under any dot-prefixed ancestor directory, valid inbox files may be skipped entirely, producing incorrect audit results in that environment.
