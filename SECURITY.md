# Security Policy

## Reporting a Vulnerability

Please **do not** report security vulnerabilities through public GitHub issues.

Instead, report them privately using GitHub's
[private vulnerability reporting](https://github.com/lhgomes/azure-resilience-iq/security/advisories/new)
("Report a vulnerability" under the repository's **Security** tab).

Please include as much of the following as possible:

- A description of the vulnerability and its impact
- Steps to reproduce (proof of concept, affected endpoints, configuration)
- Affected version, commit, or branch
- Any suggested remediation

You can expect an initial response within a reasonable timeframe. Please allow
time for a fix to be prepared and released before any public disclosure.

## Scope & Operational Notes

This project queries Azure resources using your local credentials
(`DefaultAzureCredential`) and exposes an HTTP API with **no built-in
authentication**. It is intended to run locally.

- Do **not** expose the backend to untrusted networks or the public internet.
- The API binds to `127.0.0.1` for local use; only change the host binding in a
  trusted, network-isolated environment.
- Restrict browser origins via the `CORS_ALLOWED_ORIGINS` environment variable
  (see `backend/.env.sample`).
- Never commit real credentials, secrets, or collected `data/` artifacts.
  `.env`, keys, and `data/` are already gitignored.
