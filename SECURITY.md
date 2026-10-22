# Security

## Reporting a vulnerability

Please report vulnerabilities privately, not in a public issue. Use GitHub's private
vulnerability reporting on this repository ("Security" tab, "Report a vulnerability").
Include what you found, how to reproduce it and which version you used. You will get an
answer within seven days.

The full security posture (credentials, the local runner, the MCP server, what a report is
and is not) is in [docs/security.md](docs/security.md).

## What Reachmap does with credentials

Reachmap reads code with your own credentials: the host CLI's login (for example
`gh auth login` or `glab auth login`) or a token read from an environment variable with a
fixed name (`GITLAB_TOKEN`, `BITBUCKET_TOKEN`). Only a test source takes the name of a variable
from `reachmap.toml`, in `token_env`. It stores no credential. A token never appears in a cache
key, a cache file, a log or a report.

## What to keep in mind

- Reports quote source lines from every repository read. Treat them as internal data.
- Report content is untrusted input: the HTML report escapes it and makes no network
  calls, and the MCP server hands it to a model fenced and labelled untrusted.
- `reachmap serve` binds 127.0.0.1 only, rejects non-loopback `Host` headers and refuses
  cross-origin callers. It acts as whoever is logged in; do not expose it as a shared
  service.
