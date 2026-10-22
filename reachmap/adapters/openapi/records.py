"""The incompleteness kinds the OpenAPI adapter makes. Registered when `reachmap.edges` is imported (which a report reader does
too), so a report that holds one loads without the adapter.

- `spec_unreadable` (blocking, subject `<repo>:<spec path>`): the configured spec is not in the tree, could not be read, is not
  valid JSON or is not an OpenAPI document. Which operations the repository serves was not established, so no route is claimed
  and no claim of absence is made.
- `spec_gap` (not blocking, subject `<repo>:<spec path>`): something in a spec that was read is written in a way this adapter
  does not follow (a `$ref` outside the document or at nothing, a path that does not start with `/`, a server with no URL). The
  operations that depend on it are treated as changed when the spec changes, and the run says its answer is not complete.
- `operation_handler_unresolved` (not blocking, subject `<repo>:<METHOD> <path>`): no function in the handler files carries the
  operationId of the operation, so a change to code cannot be tied to it.
- `handlers_unmatched` (not blocking, subject `<repo>:<pattern>`): a `handlers` pattern (a file, a folder or a glob) matches no code file of
  the tree, so no operation is tied to code through it.
- `handlers_unconfigured` (not blocking, subject `<repo>`): `http_server` names no `handlers`, so no code is tied to any
  operation and a change to code reaches no route through the spec.
"""

from __future__ import annotations

from reachmap.errors import ModelError
from reachmap.incomplete import kind_spec, register_kind

__all__ = ["KINDS"]

KINDS = (
    ("spec_unreadable", True, True,
     "Which operations {subject} declares was not established, because the spec could not be read"),
    ("spec_gap", False, False,
     "What parts of {subject} say was not established, because they are written in a way this adapter does not follow"),
    ("operation_handler_unresolved", False, False,
     "Which code serves {subject} was not established, because no function in the handler files carries its operationId"),
    ("handlers_unmatched", False, False,
     "Which code serves the operations that {subject} should name was not established, because it matches no code file"),
    ("handlers_unconfigured", False, False,
     "Which code serves the operations of {subject} was not established, because no handler files are configured"),
)

for _kind, _blocking, _always, _template in KINDS:
    try:
        kind_spec(_kind)
    except ModelError:
        register_kind(_kind, _template, _blocking, always_blocking=_always)
