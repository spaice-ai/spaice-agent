# Code Review: spaice_agent/memory/classify.py

VERDICT: needs revision

FINDINGS:

1. [severity=major] Credential directory resolution bypasses test isolation
   The `for_agent()` method hardcodes `Path.home() / ".Hermes" / "credentials"` as the credential base directory, but the comment claims this enables test monkeypatching. However, `read_credential()` has its own `CREDENTIAL_DIR` module-level cache that gets set at import time. If tests monkeypatch `Path.home()` after the credentials module is imported, the cache won't reflect the patched value. The explicit `base_dir` parameter may not work as intended if `read_credential()` ignores it in favor of its cached value.

2. [severity=major] Missing validation for confidence bounds
   `_parse_response()` extracts `confidence = float(data.get("confidence", 0.0))` but never validates that it's in [0.0, 1.0]. The spec states confidence is "0.0-1.0" and the fallback logic compares against `fallback_threshold` (default 0.5). A malformed model response with confidence=5.0 or -0.3 would bypass fallback logic incorrectly and produce nonsensical Classification objects.

3. [severity=major] Priority validation missing
   `priority = int(data.get("priority", 5))` accepts any integer. The spec references a "Priority Table" with values 1-5, but there's no enforcement. A model returning priority=99 or priority=0 would create invalid Classification objects that downstream triage logic might mishandle.

4. [severity=minor] Inconsistent error message for missing CATEGORISATION.md
   `_load_index_card()` raises `ClassifierConfigError` with message "CATEGORISATION.md not found at {md_path}. Create it in the vault root..." but the actual filename is configurable via `config.categorisation_md` (default "CATEGORISATION.md"). If a user configures a different filename, the error message will be misleading by always saying "CATEGORISATION.md".

5. [severity=minor] Retry logic doesn't handle timeout exceptions
   `_call_openrouter()` catches `urllib.error.URLError` for retries, but `urllib.request.urlopen(timeout=...)` can also raise `socket.timeout` (a subclass of `OSError`, not `URLError`). Timeout errors won't be retried, causing immediate failure instead of the intended 3-attempt resilience.

6. [severity=minor] JSON fence stripping is fragile
   The defensive fence-stripping logic in `_parse_response()` removes all lines starting with "```", but doesn't verify they're actually fence markers. If the model legitimately returns JSON with a string value starting with "```", that line gets silently dropped, corrupting the response. Should check for fence pattern more precisely (e.g., line == "```json" or line == "```").

7. [severity=minor] Unused import: `field` from dataclasses
   Line 13 imports `field` from dataclasses but it's never used in the module. This is dead code.
