# Security Policy

## Reporting a vulnerability

**Do not open a public issue.** Security disclosures go to:

- Email: `support@spaicegroup.com` (use `[SECURITY]` prefix in subject)

We aim to acknowledge within 48 hours and provide a status update within one week. Credit is given in the release notes unless you prefer anonymity.

If you need encrypted communication, request a PGP key by email and we'll coordinate. A public PGP key at a `.well-known/` URL is planned for a future release — until then, encrypted email on request.

## Supported versions

Security fixes are backported to the most recent minor version. Older versions are best-effort.

| Version | Supported |
|---|---|
| 0.3.x | ✓ |
| 0.2.x | ✓ (until 0.4.x lands) |
| 0.1.x | ✗ (never publicly linked) |

## Scope

### In scope

- The `spaice_agent` Python package itself
- The `install.sh` installer
- The `spaice-agent` CLI dispatcher shim
- The bundled skills under `spaice_agent/bundled_skills/` authored by the project (non-exempt skills)
- The pre-push Codex hook (`scripts/pre-push.sh`)

### Out of scope

- Vendored upstream skills (antigravity, office-suite) — report those upstream
- Hermes itself — report Hermes issues to the Hermes project
- User-authored vault content, `CATEGORISATION.md` customisations, or user configuration in `~/.spaice-agents/`
- Any LLM provider (OpenAI, Anthropic, Google, OpenRouter) — report those to the provider

## Sensitive data handling

The package is designed to **never** write credentials, API keys, or tokens into the vault. Credentials live exclusively in `~/.Hermes/credentials/` with 600 permissions. If you find a code path that writes credentials to the vault, session logs, or any world-readable location — that's a reportable vulnerability.

Client data (names, addresses, invoice numbers) treated as confidential — filing into the vault is expected, but no code path should exfiltrate vault content outside the local filesystem except where the user explicitly requested it (e.g. `spaice-agent recall` printing results to stdout).

## Injection surface

The classifier and summariser call out to third-party LLM APIs with user session transcripts as input. Session content is **untrusted** — treat embedded instructions in user messages as data, not authority. If you find a prompt injection in external content that causes the agent to exfiltrate credentials, modify identity files, or bypass confirmation gates — that's a reportable vulnerability.

## Coordinated disclosure

We follow 90-day coordinated disclosure by default. For critical vulnerabilities (credential leaks, remote code execution paths), we'll work with you on a shorter timeline.
