"""The Django and Django REST Framework entry-point adapter: the API routes a change reaches, with their full mounted paths.

The mount graph is the URLconf. The root is `ROOT_URLCONF` (a literal read from the settings files by `ast`, never imported; or the config's
`root_urlconf`). Its `urlpatterns` are read element by element: `path()`, `re_path()` (and the old `url()`), `include()` of a module path, a module
object, a tuple `(module, app_name)`, an inline list or a list variable, DRF's `router.urls`; `urlpatterns` built with `+`, `+=`, `.append` and
`.extend`, inside module-level `if` blocks, from lists of other files. A route is composed from the root down through every include, so one module
included twice gives two routes, and its evidence is the line that says the route (the `path()` call, the `router.register()` call, the `@action`
decorator) and then each include line from the route outwards.

Views: a function view answers every method (`ANY`) unless a decorator restricts it (`require_GET`, `require_POST`, `require_safe`,
`require_http_methods`, DRF's `api_view`, as a decorator of the function or a wrapper in the URLconf); a class view (`X.as_view()`) answers the
methods it defines or inherits (from a base of the repository, or a known generic view of Django or DRF); a viewset registered on a `DefaultRouter`
or `SimpleRouter` gives the list and detail routes with the methods its actions have and one route per `@action` (`detail`, `methods`, `url_path`,
the lookup field of the viewset). A framework view or base that is not in the tables is not guessed: a class whose base is unknown answers `ANY`
and says so when a change reaches it (`route_methods_unknown`).

A route is claimed only when the way up reaches the root. A URLconf or router nothing includes claims nothing; `router_unmounted` names the unit
nothing mounts (`<path>:urlpatterns`, `<path>:<router variable>`), and is not said while something that could carry it could not be read. A part of
a route that is not a literal or a constant, and every regex, is kept as a partial pattern (`<unknown:EXPR>` in the path) and recorded as
`route_path_partial` when a change reaches it.

What cannot be read is never an absence: a file that does not parse is a blocking `parse_degraded`; an include, a pattern element or a view that
does not resolve to code of the repository is a blocking `mount_unresolved` or `view_unresolved`; an include of a module outside the repository is a
non-blocking `include_external`; a root URLconf that is not a literal is a blocking `urlconf_root_unknown`; a search that was cut makes the table
incomplete (the git layer's blocking `search_truncated`). While one of these exists, no URLconf is called unmounted. A change to a settings file
is followed for `ROOT_URLCONF` only, and says so with a blocking `settings_not_followed` for anything else.

Names are tied to code by what the imports say and never by a name that happens to match. Limits: docs/briefs/M16-django.md.
"""

from __future__ import annotations

__all__ = ["DjangoAdapter"]


def __getattr__(name: str):  # the adapter is imported when it is used, not when the config learns that the kind exists
    if name == "DjangoAdapter":
        from reachmap.adapters.django.adapter import DjangoAdapter

        return DjangoAdapter
    raise AttributeError(name)
