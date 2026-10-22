"""What an entry-point kind tells the language adapter of a repository that uses it: the packages that are installed rather than part of the repository,
and the names that are conventions of the framework and never a name to link code by. Per entry-point kind, so a repository that is not of that kind is
not told anything: `register(kind, installed=..., conventions=...)` is called by the adapter's package when the kind is registered, and the Python adapter
asks `names_for(kind)` for the kind of its repository (`entry_points.kind`).

Installed packages. A language adapter that cannot resolve an import records it when it spells the name of the file under analysis (the importer may
be missed: a package that is not in the repository, or a source root the adapter does not read). For a framework whose own module names are the names
projects give their files (`from django.urls import path` in every `urls.py`, `from django.db import models` in every `models.py`) that record would
be made for every change. For a repository of that kind an unresolved import of one of the registered names is an import of the installed package, not a
miss, unless the tree holds anything of that name (a folder or a module called `django` or `rest_framework` anywhere, a vendored copy): that may be what the
import means, and the record is made as before.

Conventions. A name that every module of a kind defines once by the framework's rule (`urlpatterns` in every URLconf) is defined "too often" by the
distinctiveness count of the language adapters. A changed declaration of one is not counted and not recorded, and the name is listed as one the change
cannot be linked by (a report that rejects it, so a note that cites it is not verified).
"""

from __future__ import annotations

__all__ = ["names_for", "register"]

_KINDS: dict[str, tuple[frozenset[str], frozenset[str]]] = {}


def register(kind: str, *, installed: tuple[str, ...] = (), conventions: tuple[str, ...] = ()) -> None:
    """Declare, for the repositories whose `entry_points.kind` is `kind`, the installed package names and the conventional names."""
    old_i, old_c = _KINDS.get(kind, (frozenset(), frozenset()))
    _KINDS[kind] = (old_i | frozenset(n for n in installed if n), old_c | frozenset(n for n in conventions if n))


def names_for(kind: str | None) -> tuple[frozenset[str], frozenset[str]]:
    """(installed package names, conventional names) of an entry-point kind: both empty for a kind that registered none and for no kind."""
    return _KINDS.get(kind or "", (frozenset(), frozenset()))
