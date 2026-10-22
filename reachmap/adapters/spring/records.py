"""The incompleteness kinds of the Spring adapter, registered when the package is used. A record is blocking when what was not read could
hold a route or change one (an absence that was never established); the wording names no safety and no coverage."""

from __future__ import annotations

from reachmap.errors import ModelError
from reachmap.incomplete import kind_spec, register_kind

KINDS = (
    ("route_style_unread", "Which routes {subject} serves was not established, because they are made in a style this adapter does not read (functional routes, Jakarta REST)"),
    ("config_not_followed", "What a change to {subject} reaches was not established, because the wiring and the configuration of the application are not followed"),
    ("config_unread", "Whether {subject} sets a path for the routes was not established, because the file is not read completely"),
)

for _kind, _template in KINDS:
    try:
        kind_spec(_kind)
    except ModelError:
        register_kind(_kind, _template, True)
