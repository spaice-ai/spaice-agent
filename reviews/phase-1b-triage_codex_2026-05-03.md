# Code Review: `spaice_agent/memory/triage.py`

VERDICT: needs revision

FINDINGS:

1. [severity=major] Race condition in atomic append operations
   The `_append_to_target` and `_append_log` functions read existing content, modify it, then write via temp file. Between read and write, another process could modify the file, causing lost updates. The atomic write only protects the final write step, not the read-modify-write sequence. Consider file locking or accept the race with clear documentation.

2. [severity=major] Section insertion regex is fragile and can corrupt markdown
   In `_append_to_target`, the regex `rf"^(##+ +{re.escape(section)}.*)$"` matches any heading level (##, ###, ####, etc.) and inserts immediately after the header line. This breaks if the section has content on the same line or if there are nested subsections. Inserting at `m.end()` places content before any existing subsection headers, potentially breaking document structure.

3. [severity=major] LOG.md section detection uses string search instead of proper parsing
   `_append_log` uses `if header in existing:` and `existing.index(header)` which will false-match if the section name appears in body text (e.g., "## Filing pass" would match "The filing pass completed"). Should use regex with line boundaries like the target append does.

4. [severity=minor] Inconsistent error handling between file operations
   `_classify_one` catches OSError from `_age_hours` and returns a TriageResult, but `run()` catches OSError from move/append operations and creates escalation results inline. The `_append_to_target` and `_append_log` functions can raise OSError but aren't wrapped in try/except. This inconsistency means some file errors escalate gracefully while others could crash the entire triage run.

5. [severity=minor] Missing validation of target_file path traversal
   `_classify_one` extracts `classifier_target` from frontmatter and uses it to construct `target_path = self.paths.vault_root / target_rel` without validating that the path stays within vault_root. A malicious inbox file with `classifier_target: "../../../etc/passwd"` could write outside the vault. Should validate with `.resolve()` and check it starts with vault_root.

6. [severity=minor] Dry-run mode doesn't prevent LOG.md writes
   The `run()` method checks `if not dry_run and (filed or escalated or demoted):` before calling `_append_log_summaries`, but this means dry-run still writes to LOG.md if any results exist. The condition should be `if not dry_run:` only, or the LOG writes should also be gated.

7. [severity=minor] Type inconsistency in section handling
   `_classify_one` converts section to string with `section=str(section) if section else None`, but `_append_to_target` accepts `Optional[str]` and checks `if section:`. If section is an empty string, it will be falsy but not None, causing inconsistent behavior. Should normalize to None for empty strings.
