"""The file-system routing of Next.js, read from a list of paths alone: which files are pages, route handlers, wrappers
(layouts and their kin) or API routes, and the URL each one answers. No file is read here; the adapter reads what the
table says is a routing file.

App Router (`app/`): a folder is a URL segment. `page.<ext>` makes the folder a screen, `route.<ext>` an HTTP handler
(exported `GET`, `POST`, ...). `layout`, `template`, `loading`, `error`, `not-found`, `global-error` and `default` wrap the
pages below their folder (`wrapped`). A folder in parentheses (a route group) is not part of the URL, `@name` (a parallel
slot) is not either, `(.)x`, `(..)x`, `(..)(..)x` and `(...)x` (intercepting routes) take the URL they intercept and are marked
`intercepting`, `[id]` is a parameter, `[...rest]` a catch-all, `[[...rest]]` an optional catch-all (two URLs), and a folder
that starts with an underscore and everything below it is private: never routed.

Pages Router (`pages/`): every file is a route (`index` is its folder), `pages/api/**` are API routes, `_app` and `_document`
wrap every page (`wrapped`), `_error` is not a route, and a test file is not a route. `src/app` and `src/pages` are read only
when the project root has no `app` / `pages` of its own, as Next does.

A screen path is written the way React Router writes it (`/products/:slug`, a catch-all as a trailing `*`), an API path the
way the FastAPI adapter writes it (`/api/orders/{id}`, a catch-all as `{rest:path}`), so the http edge and the test sources
match both. A segment that cannot be read as a route part (`post-[id]`, a catch-all that is not last) is kept as
`<unknown:TEXT>` and listed in `unknown`: the route is partial, never guessed.

`middleware.<ext>` (`proxy.<ext>` in Next.js 16) and `instrumentation.<ext>` beside the router folders wrap every route (the
matcher of middleware is not evaluated, and instrumentation runs for the whole server: undecidable means affected). The folder
is the parent of `pages/` when there is one, else of `app/`, as Next finds it.

Two files that answer one URL (a `page` and a `route` of one folder, an App Router page and a Pages Router page, two route groups)
are a build error in Next.js: `Table.conflicts` names them, the adapter records it and still reaches both (affected).

The file-based metadata routes of the App Router are GET routes of their own: `sitemap`, `robots`, `manifest`, `icon`, `apple-icon`,
`opengraph-image` and `twitter-image` with a code extension, served at the folder's URL plus `/sitemap.xml`, `/robots.txt`,
`/manifest.webmanifest` or the file's name (`kind` is `metadata`, the role is `route`).
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, replace
from functools import cached_property
from typing import Callable, Iterable

__all__ = ["APP_WRAPPERS", "Entry", "HTTP_METHODS", "PAGE_EXTENSIONS", "Table", "classify", "is_method", "is_reserved_export"]

PAGE_EXTENSIONS = ("tsx", "ts", "jsx", "js")
HTTP_METHODS = ("GET", "HEAD", "OPTIONS", "POST", "PUT", "DELETE", "PATCH")
APP_WRAPPERS = ("layout", "template", "loading", "error", "not-found", "global-error", "forbidden", "unauthorized", "default")
METADATA_ROUTES = {"sitemap": "sitemap.xml", "robots": "robots.txt", "manifest": "manifest.webmanifest", "icon": "icon", "apple-icon": "apple-icon",
                   "opengraph-image": "opengraph-image", "twitter-image": "twitter-image"}
MIDDLEWARE_NAMES = ("middleware", "proxy")
GLOBAL_WRAPPERS = {"middleware": "middleware", "proxy": "middleware", "instrumentation": "instrumentation"}  # file stem to the kind of wrapper
PAGES_WRAPPERS = ("_app", "_document")
# What Next reads from an entry file by name: an export with one of these names is the framework's, not a helper other
# files may import (a helper exported from a page is: the walk goes on past the file through it).
RESERVED_EXPORTS = frozenset({
    "default", "metadata", "generateMetadata", "generateStaticParams", "generateViewport", "viewport", "dynamic", "dynamicParams",
    "revalidate", "fetchCache", "runtime", "preferredRegion", "maxDuration", "config", "getServerSideProps", "getStaticProps",
    "getStaticPaths", *HTTP_METHODS,
})

_INTERCEPT = re.compile(r"^((?:\(\.\.\))+|\(\.\)|\(\.\.\.\))(.+)$")
_GROUP = re.compile(r"^\(([^()]+)\)$")
_DYNAMIC = re.compile(r"^\[([^\[\].]+)\]$")
_CATCH_ALL = re.compile(r"^\[\.\.\.([^\[\].]+)\]$")
_OPTIONAL = re.compile(r"^\[\[\.\.\.([^\[\].]+)\]\]$")


def is_method(name: str) -> bool:
    return name in HTTP_METHODS


def is_reserved_export(name: str) -> bool:
    return name in RESERVED_EXPORTS


@dataclass(frozen=True, slots=True)
class Entry:
    """A file the router gives a meaning to.

    `role` is `page` (a screen), `route` (an App Router handler), `api` (a Pages Router API route) or `wrapper`
    (`kind` says which: a layout and its kin, `_app`, `_document`, `middleware`). `paths` are the URLs a page, route or
    api file answers (two for an optional catch-all). `scope` is, for a wrapper, the folders (relative to the router folder)
    whose pages it wraps. `slot` marks a file inside a parallel slot, `intercepting` one that intercepts another route,
    `unknown` the segments that could not be read as route parts."""

    path: str
    router: str  # app | pages | ""
    role: str
    kind: str
    paths: tuple[str, ...] = ()
    module: str = ""
    scope: tuple[str, ...] = ()
    folders: tuple[str, ...] = ()
    slot: bool = False
    intercepting: bool = False
    unknown: tuple[str, ...] = ()

    @property
    def partial(self) -> bool:
        return bool(self.unknown) or self.intercepting


@dataclass(frozen=True)
class Table:
    app_dir: str | None
    pages_dir: str | None
    base: str  # the folder that holds the router folders (the project, or its src)
    entries: tuple[Entry, ...]

    @cached_property
    def by_path(self) -> dict[str, Entry]:
        return {e.path: e for e in self.entries}

    @cached_property
    def conflicts(self) -> dict[str, tuple[str, ...]]:
        """For each routing file that answers a URL another file answers too (Next.js refuses to build): the other files. A slot page and an
        intercepting page share a URL on purpose, and a metadata route is not compared."""
        by_url: dict[tuple[str, ...], dict[str, Entry]] = {}
        for e in self.entries:
            if e.role in ("page", "route", "api") and e.kind != "metadata" and not e.slot and not e.intercepting:
                for path in e.paths:
                    by_url.setdefault(_shape(path), {})[e.path] = e
        out: dict[str, set[str]] = {}
        for files in by_url.values():
            if len(files) > 1:
                for f in files:
                    out.setdefault(f, set()).update(set(files) - {f})
        return {f: tuple(sorted(others)) for f, others in sorted(out.items())}

    def wrapped(self, wrapper: Entry) -> list[Entry]:
        """The entries a wrapper wraps: for a layout and its kin the pages of the App Router below its folder (route groups and
        slots included: the folders are compared as written), for `_app` and `_document` the pages of the Pages Router, for
        middleware and instrumentation every page, route handler and API route."""
        out: list[Entry] = []
        for e in self.entries:
            if wrapper.kind in ("middleware", "instrumentation"):
                ok = e.role in ("page", "route", "api")
            elif wrapper.router == "pages":
                ok = e.role == "page" and e.router == "pages"
            else:
                ok = e.role == "page" and e.router == "app" and e.folders[:len(wrapper.scope)] == wrapper.scope
            if ok:
                out.append(e)
        return out


def _shape(path: str) -> tuple[str, ...]:
    """A path with its parameter names forgotten (`/a/:id`, `/a/{slug}` are one shape), a catch-all a star."""
    out = []
    for seg in path.strip("/").split("/"):
        if seg == "*" or (seg.startswith("{") and seg.endswith(":path}")):
            out.append("*")
        elif seg.startswith(":") or (seg.startswith("{") and seg.endswith("}")):
            out.append("{}")
        else:
            out.append(seg)
    return tuple(out)


def _ext(name: str, extensions: tuple[str, ...]) -> tuple[str, str] | None:
    """(stem, extension) of a file name when its extension is a page extension and it is not a declaration file."""
    stem, dot, ext = name.rpartition(".")
    if not dot or ext not in extensions or not stem or stem.endswith(".d"):
        return None
    return stem, ext


@dataclass(slots=True)
class _Url:
    levels: list[str]
    unknown: list[str]
    groups: list[str]
    slot: bool = False
    intercepting: bool = False
    optional: str | None = None  # the name of an optional catch-all at the end
    rest: str | None = None  # the name of a catch-all at the end


def _segments(folders: tuple[str, ...], *, brace: bool) -> _Url | None:
    """The URL a chain of folders answers, or None when a private folder (an underscore) is in it."""
    url = _Url([], [], [])
    n = len(folders)
    for i, raw in enumerate(folders):
        last = i == n - 1
        if raw.startswith("_"):
            return None
        m = _INTERCEPT.match(raw)
        if m is None and _GROUP.match(raw):
            url.groups.append(raw[1:-1])
            continue
        if raw.startswith("@"):
            url.slot = True
            continue
        if m:
            url.intercepting = True
            marker, name = m.groups()
            if marker == "(...)":
                url.levels.clear()
            elif marker != "(.)":
                for _ in range(marker.count("(..)")):
                    if url.levels:
                        url.levels.pop()
            raw = name
        m = _OPTIONAL.match(raw)
        if m and last:
            url.optional = m.group(1)
            continue
        m = _CATCH_ALL.match(raw)
        if m and last:
            url.rest = m.group(1)
            continue
        m = _DYNAMIC.match(raw)
        if m:
            url.levels.append("{%s}" % m.group(1) if brace else ":" + m.group(1))
            continue
        if "[" in raw or "]" in raw:
            url.unknown.append(raw)
            url.levels.append(f"<unknown:{raw}>")
            continue
        url.levels.append(raw)
    return url


def _paths(url: _Url, *, brace: bool) -> tuple[str, ...]:
    base = "/" + "/".join(url.levels)
    if url.optional is not None:
        return (base, base.rstrip("/") + ("/{%s:path}" % url.optional if brace else "/*"))
    if url.rest is not None:
        return (base.rstrip("/") + ("/{%s:path}" % url.rest if brace else "/*"),)
    return (base,)


def _module(url: _Url, *, api: bool) -> str:
    if url.groups:
        return url.groups[0]
    levels = [lv for lv in url.levels]
    if api and levels and levels[0] == "api" and len(levels) > 1:
        levels = levels[1:]
    if not levels:
        if url.rest is not None:
            return url.rest
        return url.optional or ""
    first = levels[0]
    if first.startswith(":"):
        return first[1:]
    if first.startswith("{") and first.endswith("}"):
        return first[1:-1]
    return first


def _router_dir(files: set[str], base: str, name: str) -> str | None:
    """`<base>/<name>` when it holds a file, else `<base>/src/<name>` when that does (Next ignores src/<name> when the project root has one)."""
    for folder in (posixpath.join(base, name), posixpath.join(base, "src", name)):
        folder = folder.lstrip("/")
        if any(f.startswith(folder + "/") for f in files):
            return folder
    return None


def classify(paths: Iterable[str], *, root: str = "", extensions: Iterable[str] = PAGE_EXTENSIONS,
             is_test: Callable[[str], bool] = lambda p: False, base_path: str = "") -> Table:
    """The routing files among `paths` (the tree of a commit), with the URL each one answers and what each wrapper wraps.
    `root` is the folder of the Next project in the repository (default the repository root); `extensions` the page
    extensions; `is_test` says which paths are tests (a test file is never a route, even in a routing folder); `base_path`
    is the `basePath` of the Next config, put before every URL."""
    exts = tuple(extensions)
    files = set(paths)
    prefix = "/" + base_path.strip("/") if base_path.strip("/") else ""
    base = root.strip("/")
    app_dir = _router_dir(files, base, "app")
    pages_dir = _router_dir(files, base, "pages")
    holder = posixpath.dirname(pages_dir or app_dir or "") if (app_dir or pages_dir) else base  # Next: join(pagesDir || appDir, "..")
    entries: list[Entry] = []
    for path in sorted(files):
        if is_test(path):
            continue
        entry = None
        if app_dir is not None and path.startswith(app_dir + "/"):
            entry = _app_entry(path, path[len(app_dir) + 1:], exts)
        elif pages_dir is not None and path.startswith(pages_dir + "/"):
            entry = _pages_entry(path, path[len(pages_dir) + 1:], exts)
        elif posixpath.dirname(path) == holder:
            stem_ext = _ext(posixpath.basename(path), exts)
            if stem_ext is not None and stem_ext[0] in GLOBAL_WRAPPERS:
                entry = Entry(path, "", "wrapper", GLOBAL_WRAPPERS[stem_ext[0]])
        if entry is not None:
            if prefix and entry.paths:
                entry = replace(entry, paths=tuple(prefix + p if p != "/" else prefix for p in entry.paths))
            entries.append(entry)
    return Table(app_dir, pages_dir, holder, tuple(entries))


def _app_entry(path: str, rel: str, exts: tuple[str, ...]) -> Entry | None:
    folders = tuple(rel.split("/")[:-1])
    name = rel.split("/")[-1]
    stem_ext = _ext(name, exts)
    if stem_ext is None:
        return None
    stem = stem_ext[0]
    if stem not in ("page", "route", *APP_WRAPPERS, *METADATA_ROUTES):
        return None
    url = _segments(folders, brace=stem == "route" or stem in METADATA_ROUTES)
    if url is None:
        return None  # a private folder
    if stem in METADATA_ROUTES:
        module = _module(url, api=False)  # the folder's, not the file's
        url.levels.append(METADATA_ROUTES[stem])
        return Entry(path, "app", "route", "metadata", paths=_paths(url, brace=True), module=module, folders=folders, slot=url.slot,
                     intercepting=url.intercepting, unknown=tuple(url.unknown))
    if stem in APP_WRAPPERS:
        scope = folders
        if stem == "default" and any(f.startswith("@") for f in folders):
            scope = folders[:next(i for i, f in enumerate(folders) if f.startswith("@"))]
        return Entry(path, "app", "wrapper", stem, scope=scope, folders=folders, slot=url.slot)
    brace = stem == "route"
    return Entry(
        path, "app", "route" if brace else "page", stem, paths=_paths(url, brace=brace), module=_module(url, api=brace), folders=folders,
        slot=url.slot, intercepting=url.intercepting, unknown=tuple(url.unknown),
    )


def _pages_entry(path: str, rel: str, exts: tuple[str, ...]) -> Entry | None:
    parts = rel.split("/")
    stem_ext = _ext(parts[-1], exts)
    if stem_ext is None:
        return None
    stem = stem_ext[0]
    folders = tuple(parts[:-1])
    if not folders and stem in PAGES_WRAPPERS:
        return Entry(path, "pages", "wrapper", stem, folders=())
    if not folders and stem == "_error":
        return None
    api = (bool(folders) and folders[0] == "api") or (not folders and stem == "api")  # Next's isAPIRoute counts `/api` itself
    chain = folders if stem == "index" else (*folders, stem)
    url = _plain(chain, brace=api)  # `_`, `@` and `(...)` mean nothing in the Pages Router: a name is a segment
    return Entry(
        path, "pages", "api" if api else "page", "api" if api else "page", paths=_paths(url, brace=api), module=_module(url, api=api),
        folders=folders, unknown=tuple(url.unknown),
    )


def _plain(chain: tuple[str, ...], *, brace: bool) -> _Url:
    """The Pages Router reads a file or folder name as a segment and nothing else: no groups, no slots, no private folders."""
    url = _Url([], [], [])
    n = len(chain)
    for i, raw in enumerate(chain):
        last = i == n - 1
        m = _OPTIONAL.match(raw)
        if m and last:
            url.optional = m.group(1)
            continue
        m = _CATCH_ALL.match(raw)
        if m and last:
            url.rest = m.group(1)
            continue
        m = _DYNAMIC.match(raw)
        if m:
            url.levels.append("{%s}" % m.group(1) if brace else ":" + m.group(1))
        elif "[" in raw or "]" in raw:
            url.unknown.append(raw)
            url.levels.append(f"<unknown:{raw}>")
        else:
            url.levels.append(raw)
    return url
