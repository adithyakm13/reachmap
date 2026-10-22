"""OpenAPI as an HTTP edge (M16): a committed spec describes the routes of a server and the calls of a generated client.

Importing the package registers what the core needs to know about the adapter, and nothing else is imported eagerly (`reachmap.config`
imports it at the end of its own module): the configuration kinds `http_server` and `http_client` of kind `openapi` (with the option
validators of `options`) and the incompleteness kinds of `records`. The pieces:

- `jsonpos`: a strict JSON reader that keeps the line of every key (a spec line is evidence).
- `spec`: operations, base paths, `$ref` closures with cycles ended, signatures and the diff of two versions.
- `names`: operationId to function name, and the generic names that never link code.
- `server`: the entry-point adapter of a repository with `http_server = { kind = "openapi", ... }`.
- `client`: the HTTP client adapter of a repository with `http_client = { kind = "openapi", ... }`.

See docs/briefs/M16-openapi.md for the rules, the choices and the limits.
"""

from reachmap import config
from reachmap.adapters.openapi import options, records  # noqa: F401  (records registers its kinds on import)

config.register_kind("http_server", "openapi", options.server)
config.register_kind("http_client", "openapi", options.client)
