"""An independent reading of the Next.js fixtures: the screens, the API routes and what a layout wraps, from the file tree and the
text of the files, written apart from the adapter (it does not import `reachmap.adapters.nextjs`) so that `test_nx_fixtures.py`
can check the hand-written expected.json against something that is not the code under test.

It understands the shapes these fixtures use (folders, route groups, `@slots`, `(..)` interception, dynamic and catch-all
segments, `route.ts` method exports, `pages/api` files), not Next.js.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.fixtures.claims import git_out, text_at

METHODS = ("GET", "HEAD", "OPTIONS", "POST", "PUT", "DELETE", "PATCH")
EXPORT_FN = re.compile(r"^export\s+(?:async\s+)?function\s+(\w+)\s*\(", re.M)
EXPORT_CONST = re.compile(r"^export\s+const\s+(\w+)\s*=", re.M)


def tree(repo: Path, rev: str) -> list[str]:
    return git_out(repo, "ls-tree", "-r", "--name-only", rev).splitlines()


def _folders_url(folders: list[str], api: bool, pages: bool = False) -> tuple[list[str], bool] | None:
    """The URL segments of a chain of folders and whether it is an intercepting route; None for a private folder. `pages` is the Pages
    Router, where a name is a segment and nothing else: no private folder, no slot, no group, no interception."""
    out: list[str] = []
    intercepting = False
    for i, name in enumerate(folders):
        if not pages and name.startswith("_"):
            return None
        if not pages and name.startswith("@"):
            continue
        m = None if pages else re.match(r"^\((\.{1,3})\)(.*)$", name)
        if m:
            intercepting = True
            dots, name = m.group(1), m.group(2)
            if dots == "..":
                if out:
                    out.pop()
            elif dots == "...":
                out.clear()
        elif not pages and re.match(r"^\([^()]+\)$", name):
            continue
        last = i == len(folders) - 1
        m = re.match(r"^\[\[\.\.\.(\w+)\]\]$", name)
        if m and last:
            out.append("{" + m.group(1) + ":path?}" if api else "*?")
            continue
        m = re.match(r"^\[\.\.\.(\w+)\]$", name)
        if m and last:
            out.append("{" + m.group(1) + ":path}" if api else "*")
            continue
        m = re.match(r"^\[(\w+)\]$", name)
        out.append(("{" + m.group(1) + "}" if api else ":" + m.group(1)) if m else name)
    return out, intercepting


def _expand(segments: list[str]) -> list[str]:
    """A path from segments; an optional catch-all gives the path without it and with it."""
    if segments and segments[-1].endswith("?}") or (segments and segments[-1] == "*?"):
        last = segments[-1]
        full = last.replace("?", "")
        base = "/" + "/".join(segments[:-1])
        return [base if base != "" else "/", (base.rstrip("/") + "/" + full)]
    return ["/" + "/".join(segments)]


def routing_files(files: list[str]) -> dict[str, tuple[str, str]]:
    """path to (router, name) for the files of src/app and src/pages that Next gives a meaning: the fixtures use src/ only."""
    out: dict[str, tuple[str, str]] = {}
    for f in files:
        if f.startswith("src/app/"):
            stem = f.rsplit("/", 1)[1].rsplit(".", 1)[0]
            if stem in ("page", "route", "layout", "template", "loading", "error", "not-found", "global-error", "forbidden", "unauthorized", "default") and f.endswith((".ts", ".tsx")):
                out[f] = ("app", stem)
        elif f.startswith("src/pages/") and f.endswith((".ts", ".tsx")) and not re.search(r"\.(test|spec)\.|\.d\.ts$", f):
            out[f] = ("pages", f.rsplit("/", 1)[1].rsplit(".", 1)[0])
    return out


def screens(files: list[str]) -> dict[str, set[str]]:
    """screen label (the smallest path of the screen) to the files that make it, with also-mounted paths merged into the screen."""
    pairs: list[tuple[str, str, bool]] = []  # (path, file, intercepting)
    for f, (router, name) in routing_files(files).items():
        if router == "app" and name == "page":
            got = _folders_url(f[len("src/app/"):].split("/")[:-1], False)
            if got is None:
                continue
            for p in _expand(got[0]):
                pairs.append((p, f, got[1]))
        elif router == "pages" and not f.startswith("src/pages/api/") and name not in ("_app", "_document", "_error"):
            chain = f[len("src/pages/"):].rsplit(".", 1)[0].split("/")
            if chain[-1] == "index":
                chain = chain[:-1]
            got = _folders_url(chain, False, pages=True)
            assert got is not None
            for p in _expand(got[0]):
                pairs.append((p, f, False))
    groups: list[tuple[set[str], set[str]]] = []  # (paths, files)
    for p, f, _i in pairs:
        hit = [g for g in groups if p in g[0] or f in g[1]]
        merged = ({p}, {f})
        for g in hit:
            merged = (merged[0] | g[0], merged[1] | g[1])
            groups.remove(g)
        groups.append(merged)
    return {min(paths): files_ for paths, files_ in groups}


def api_routes(repo: Path, rev: str, files: list[str]) -> dict[tuple[str, str], str]:
    """(method, path) to the file, for the route handlers (the methods a route.ts exports) and the pages/api files (ANY)."""
    out: dict[tuple[str, str], str] = {}
    for f, (router, name) in routing_files(files).items():
        if router == "app" and name == "route":
            got = _folders_url(f[len("src/app/"):].split("/")[:-1], True)
            if got is None:
                continue
            text = text_at(repo, rev, f) or ""
            methods = [m for m in (*EXPORT_FN.findall(text), *EXPORT_CONST.findall(text)) if m in METHODS]
            for m in methods:
                for p in _expand(got[0]):
                    out[(m, p)] = f
        elif router == "pages" and f.startswith("src/pages/api/"):
            chain = f[len("src/pages/"):].rsplit(".", 1)[0].split("/")
            if chain[-1] == "index":
                chain = chain[:-1]
            got = _folders_url(chain, True, pages=True)
            assert got is not None
            for p in _expand(got[0]):
                out[("ANY", p)] = f
    return out


def areas_of(repo: Path, rev: str) -> set[tuple[str, str]]:
    """The (kind, label) of every screen and API route the repository answers at `rev`: what middleware reaches."""
    files = tree(repo, rev)
    found = {("screen", label) for label in screens(files)}
    found |= {("api_route", f"{m} {p}") for (m, p) in api_routes(repo, rev, files)}
    return found


def pages_below(files: list[str], layout: str) -> set[str]:
    """The page files a layout (or its kin) wraps: the App Router pages below its folder, route groups and slots included."""
    folder = layout.rsplit("/", 1)[0] + "/"
    return {f for f, (router, name) in routing_files(files).items()
            if router == "app" and name == "page" and f.startswith(folder) and _folders_url(f[len("src/app/"):].split("/")[:-1], False) is not None}
