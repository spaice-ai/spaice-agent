"""spaice_agent.credentials — file-based credential store.

Reads keys from ``~/.Hermes/credentials/<name>.key``. Never logs key values,
raises :class:`MissingCredentialError` if a key is absent or empty. Files
must be mode 600 or the read is rejected — prevents accidental group/world
readable leaks.

This module replaces the legacy ``~/.openclaw/credentials/*.json`` store
which is being retired. The config loader's ``*_env`` indirection is still
honoured as a fallback for transitional agents that keep keys in shell env.
"""
from __future__ import annotations

import logging
import os
import stat
from pathlib import Path
from typing import Optional

from .config import AgentConfig, MissingCredentialError

__all__ = ["read_credential", "resolve_credential"]

logger = logging.getLogger(__name__)

CREDENTIAL_DIR = Path("~/.Hermes/credentials").expanduser()


class CredentialPermissionError(MissingCredentialError):
    """Credential file exists but has unsafe permissions."""


def _assert_safe_perms(path: Path) -> None:
    """Reject credential files that are group- or world-readable."""
    try:
        st = path.stat()
    except OSError as exc:
        raise MissingCredentialError(
            f"Could not stat credential file {path}: {exc}"
        ) from exc
    _assert_safe_perms_stat(st, path)


def _assert_safe_perms_stat(st: os.stat_result, path: Path) -> None:
    """Perm check that accepts a pre-fetched ``os.stat_result``.

    Used from the fd-based read path where fstat() result is already in
    hand and we want to avoid re-stat'ing (TOCTOU).
    """
    # Per Codex 2026-05-03: also reject execute bits for group/others —
    # a creds file with 0601 or 0611 is still a leak vector. The right
    # check is: no permission bits beyond owner-rwx are set.
    perms = stat.S_IMODE(st.st_mode)
    forbidden = (
        stat.S_IRGRP | stat.S_IWGRP | stat.S_IXGRP
        | stat.S_IROTH | stat.S_IWOTH | stat.S_IXOTH
    )
    if perms & forbidden:
        raise CredentialPermissionError(
            f"Credential file {path} has unsafe perms {oct(perms)} — "
            f"expected 0600 or stricter"
        )


def read_credential(name: str, *, base_dir: Optional[Path] = None) -> str:
    """Return the credential value stored at ``<base_dir>/<name>.key``.

    ``name`` must be a simple lowercase slug (no path separators, no leading
    dots). Whitespace is stripped. Empty files raise MissingCredentialError.

    Raises:
        MissingCredentialError: file missing or empty.
        CredentialPermissionError: file exists with unsafe permissions.
    """
    # Slug guard — prevent path traversal via '../'.
    if (
        not isinstance(name, str)
        or not name
        or "/" in name
        or "\\" in name
        or name.startswith(".")
        or len(name) > 64
    ):
        raise MissingCredentialError(f"Invalid credential name: {name!r}")

    root = Path(base_dir).expanduser() if base_dir else CREDENTIAL_DIR
    path = root / f"{name}.key"
    # Symlink guard (Codex 2026-05-03 pass 2): reject symlinked credential
    # files outright. An attacker with write access to the credentials
    # directory could otherwise swap a 600-mode symlink onto a sensitive
    # file and cause us to read it.
    try:
        if path.is_symlink():
            raise MissingCredentialError(
                f"Credential file {path} is a symlink — refusing to read"
            )
    except OSError as exc:
        raise MissingCredentialError(
            f"Could not lstat credential file {path}: {exc}"
        ) from exc
    if not path.is_file():
        raise MissingCredentialError(f"Credential file not found: {path}")
    # Open by fd to close the TOCTOU window between stat and read. fstat
    # reflects the post-open state, so changes after open can't trick us.
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise MissingCredentialError(
            f"Could not open credential file {path}: {exc}"
        ) from exc
    try:
        st = os.fstat(fd)
        _assert_safe_perms_stat(st, path)
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            value = fh.read().strip()
    except CredentialPermissionError:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    except OSError as exc:
        try:
            os.close(fd)
        except OSError:
            pass
        raise MissingCredentialError(
            f"Could not read credential file {path}: {exc}"
        ) from exc
    if not value:
        raise MissingCredentialError(f"Credential file {path} is empty")
    return value


def resolve_credential(
    config: AgentConfig,
    field_name: str,
    *,
    base_dir: Optional[Path] = None,
) -> str:
    """Return the credential value for ``field_name``.

    Resolution order:
      1. File at ``~/.Hermes/credentials/<env_var_lower>.key`` — where
         ``env_var_lower`` is the config's ``<field_name>_env`` lowercased
         with ``_api_key`` stripped. e.g. ``OPENROUTER_API_KEY`` →
         ``openrouter``, ``EXA_API_KEY`` → ``exa``, ``BRAVE_API_KEY`` →
         ``brave``.
      2. Environment variable named by ``<field_name>_env`` (legacy path).
      3. MissingCredentialError if neither resolves.

    This ordering means the new file store wins whenever both are present,
    so migrations move forward without coordination.
    """
    env_attr = f"{field_name}_env"
    env_var: str | None = getattr(config.credentials, env_attr, None)
    if not env_var:
        raise MissingCredentialError(
            f"No credential env-var configured for '{field_name}'"
        )

    # Derive slug: OPENROUTER_API_KEY -> openrouter, EXA_API_KEY -> exa
    slug = env_var.lower()
    if slug.endswith("_api_key"):
        slug = slug[: -len("_api_key")]
    elif slug.endswith("_key"):
        slug = slug[: -len("_key")]
    # Sanitise: only [a-z0-9-] pass. Underscores aren't needed in slugs
    # and were historically rejected by ``read_credential``'s slug guard.
    # If an admin types ``FOO_BAR_API_KEY`` we turn it into ``foo_bar``
    # here and then refuse — clean error rather than mysterious 404.
    import re
    if not re.fullmatch(r"[a-z0-9-]+", slug) or not slug:
        raise MissingCredentialError(
            f"Derived credential slug {slug!r} from env var {env_var!r} "
            "must be lowercase alphanumeric + hyphens only (no underscores)"
        )

    # Try file store first
    try:
        return read_credential(slug, base_dir=base_dir)
    except CredentialPermissionError:
        raise  # unsafe perms must surface — don't silently fall through
    except MissingCredentialError:
        pass

    # Fall back to env var
    value = os.environ.get(env_var)
    if not value:
        raise MissingCredentialError(
            f"Credential '{field_name}' not in {CREDENTIAL_DIR}/{slug}.key "
            f"nor in env var {env_var}"
        )
    return value
