# Code Review: spaice_agent/memory/capture.py

VERDICT: needs revision

FINDINGS:

1. [severity=major] Deduplication logic is broken — filename bucketing doesn't match ID bucketing
   In `capture_fact()`, `filename_ts` is bucketed to 5-min intervals (line ~125-126), but `_entry_id()` is called with the original `created_at` (line 127), which then does its own bucketing internally (line 89-91). This means the filename timestamp and the ID can be computed from different 5-minute buckets if `created_at` falls near a bucket boundary, breaking the dedup guarantee.

2. [severity=major] Missing YAML library causes silent runtime failure
   Lines 11-13 set `yaml = None` when import fails, but the module never uses the yaml library — it manually constructs YAML strings. This try/except is dead code that misleads readers into thinking yaml is required. Either remove it (since manual construction is intentional per line 103 comment) or document why it's present for future use.

3. [severity=minor] Inconsistent timezone handling documentation
   Spec says "use system local timezone (typically Australia/Sydney for Jarvis, but whatever `datetime.now().astimezone()` returns)" but the code doesn't validate or document what happens if `entry.created_at` is passed with a different timezone. The `_entry_id()` bucketing will use whatever timezone is in the datetime object, potentially causing dedup misses across timezone boundaries.

4. [severity=minor] `_yaml_scalar()` regex is too permissive for YAML safety
   Line 115 regex allows colons and slashes in unquoted scalars, but YAML 1.2 treats `:` as key-value separator in flow context. A tag like `"http://example.com"` would pass the regex but should be quoted. The regex should exclude `:` or the function should quote more conservatively.

5. [severity=minor] Missing validation for `source` content in `__post_init__`
   Line 67 checks `source` is non-empty but doesn't validate it's a reasonable string (e.g., no newlines, reasonable length). A source like `"telegram\n---\nmalicious: yaml"` could break frontmatter parsing. Add basic sanitization or length check similar to `text`.
