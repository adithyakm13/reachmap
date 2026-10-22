"""HTTP client and server detectors: an endpoints file of URL constants, and OpenAPI specs (server handlers, generated client).

The endpoints file is the module that exports the most URL-looking constants (`export const ORDERS_URL = "/api/orders"`, builders
such as `(id) => \\`/api/orders/${id}\\``); a file that looks like a navigation table (`routes`, `paths`, `links`) and one that
declares React Router routes are not one. A spec is a committed `openapi*.json` or `swagger*.json` with `paths`; YAML specs are
listed and not configured (the adapter reads JSON only), a JSON file called `openapi.json` with no `paths` is a decoy.
"""

from __future__ import annotations

import json
import re
from typing import Any

from reachmap.adapters.openapi.names import snake
from reachmap.init import registry
from reachmap.init.common import JS_SUFFIXES, PY_SUFFIXES, ev, is_source
from reachmap.init.guess import Note
from reachmap.init.registry import Ctx, Found
from reachmap.init.tree import line_of

__all__ = ["find_specs"]

# ------------------------------------------------------------------------------------------------ endpoints file

_URL_CONST = re.compile(
    r"""^[ \t]*export[ \t]+(?:const|let|var)[ \t]+(\w+)[ \t]*(?::[^=\n]+)?=\s*(?:(?:async\s*)?\([^)\n]*\)\s*(?::\s*[^=\n]+)?=>\s*)?(["'`])(/[A-Za-z_][^"'`\n]*|https?://[^"'`\n]+)\2""",
    re.MULTILINE,
)
_URL_FUNCTION = re.compile(r"""^[ \t]*export[ \t]+function[ \t]+(\w+)\s*\([^)\n]*\)[^{\n]*\{\s*return\s*(["'`])(/[A-Za-z_][^"'`\n]*|https?://[^"'`\n]+)\2""", re.MULTILINE)
_ASSET = re.compile(r"\.(?:png|jpe?g|gif|svg|ico|css|woff2?|ttf|mp4|webp|pdf|html)$", re.IGNORECASE)
_NAVIGATION_NAME = re.compile(r"(?:^|[^a-z])(?:routes?|paths?|links?|nav|navigation|menu|sitemap)(?:[^a-z]|$)", re.IGNORECASE)
_API_NAME = re.compile(r"(?:endpoint|api|url)s?", re.IGNORECASE)


def _url_path(url: str) -> str:
    path = re.sub(r"^https?://[^/]+", "", url)
    return path.split("?")[0].split("#")[0] or "/"


_API_SEGMENT = re.compile(r"(?:^|/)(?:api|rest|graphql|v\d+)(?:/|$)")
_HTTP_CALL = re.compile(r"\b(?:fetch|axios\w*|ky|got|superagent|request|\w*[Hh]ttp\w*|\w*[Cc]lient\w*|api\w*|\$http)\s*(?:\.\s*\w+\s*)?[(<]")


def _call_sites(ctx: Ctx, endpoints: str, names: list[str]) -> tuple[str, int] | None:
    """A file other than the endpoints file that imports it, uses one of its constants, and makes an HTTP call (fetch, axios, a client
    or `api...` wrapper). (path, line of the call) or None."""
    stem = endpoints.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    for path, text in ctx.tree.scan(JS_SUFFIXES, skip=lambda p: not is_source(p) or p == endpoints):
        if stem not in text or not any(re.search(rf"\b{re.escape(n)}\b", text) for n in names):
            continue
        m = _HTTP_CALL.search(text)
        if m:
            return path, line_of(text, m.group(0))
    return None


def detect_endpoints_file(ctx: Ctx) -> list[Found]:
    best: tuple[int, str, list[tuple[int, str, str]]] | None = None
    for path, text in ctx.tree.scan(JS_SUFFIXES, skip=lambda p: not is_source(p)):
        found: list[tuple[int, str, str]] = []
        for pattern in (_URL_CONST, _URL_FUNCTION):
            for m in pattern.finditer(text):
                url = m.group(3)
                if _ASSET.search(_url_path(url)):
                    continue
                found.append((line_of(text, m.group(1), m.start()), url, m.group(1)))
        if len(found) < 2:
            continue
        stem = path.rsplit("/", 1)[-1]
        score = len(found) * 10
        if _API_NAME.search(stem):
            score += 15
        if _NAVIGATION_NAME.search(stem):  # a navigation table of screens, unless its URLs are visibly an API's
            score -= 25 if any(_API_SEGMENT.search(_url_path(u)) or u.startswith("http") for _, u, _ in found) else 100
        if "<Route" in text:
            score -= 100
        if best is None or score > best[0]:
            best = (score, path, sorted(found))
    if best is None or best[0] <= 0:
        return []
    _, path, found = best
    api_like = any(_API_SEGMENT.search(_url_path(u)) or u.startswith("http") for _, u, _ in found)
    call = _call_sites(ctx, path, [n for _, _, n in found])
    if not (call or api_like) or (len(found) < 3 and not call):
        ctx.notes.append(f"{ev(path, found[0][0])} exports {len(found)} URL constants, but no file makes an HTTP call with them and none looks like an API path, "
                         "so it is not taken as an http_client; confirm")
        return []
    ctx.facts.client_paths.extend(_url_path(u) for _, u, _ in found)
    ctx.facts.client_evidence = ev(path, found[0][0])
    why = f"{path} exports {len(found)} URL constants, the most of any module" + (f", and {call[0]} makes an HTTP call with them" if call else ", with API-looking paths")
    evidence = (ev(path, found[0][0]), ev(path, found[-1][0])) + ((ev(*call),) if call else ())
    confidence = "high" if call and _API_NAME.search(path.rsplit("/", 1)[-1]) else "medium"
    return [Found("endpoints-file", {"path": path}, (), (Note(why, evidence, confidence),), score=len(found))]


# ------------------------------------------------------------------------------------------------ OpenAPI specs

_SPEC_NAME = re.compile(r"^(?:openapi|swagger)[\w.-]*\.json$", re.IGNORECASE)
_YAML_NAME = re.compile(r"^(?:openapi|swagger)[\w.-]*\.ya?ml$", re.IGNORECASE)
_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


def find_specs(ctx: Ctx) -> dict[str, Any]:
    """The committed specs of the repository: `specs` (path to the parsed document, only those with paths), `yaml` (paths of YAML
    specs, not read), `ignored` (JSON files named like a spec that are none, with why), `unread` (path to why: unreadable)."""
    if "specs" in ctx.cache:
        return ctx.cache["specs"]
    out: dict[str, Any] = {"specs": {}, "yaml": [], "ignored": {}}
    for path in ctx.tree.files:
        name = path.rsplit("/", 1)[-1]
        if _YAML_NAME.match(name):
            out["yaml"].append(path)
            ctx.notes.append(f"{path} is a YAML OpenAPI spec; YAML is not read, so no routes or client calls come from it: convert the spec to JSON and commit it; confirm")
            continue
        if not _SPEC_NAME.match(name):
            continue
        text = ctx.tree.text(path)
        if text is None:
            continue  # unreadable: the tree lists it
        try:
            doc = json.loads(text)
        except (ValueError, RecursionError) as exc:
            out["ignored"][path] = f"not valid JSON ({'nested too deeply' if isinstance(exc, RecursionError) else str(exc)[:80]})"
            ctx.tree.unread[path] = out["ignored"][path]
            ctx.notes.append(f"{path} is named like an OpenAPI spec but is ignored: {out['ignored'][path]}; confirm")
            continue
        version = str(doc.get("openapi", "")) if isinstance(doc, dict) else ""
        swagger = str(doc.get("swagger", "")) if isinstance(doc, dict) else ""
        paths = doc.get("paths") if isinstance(doc, dict) else None
        if not (version.startswith("3.") or swagger == "2.0"):
            out["ignored"][path] = "no openapi 3 or swagger 2.0 version key"
        elif not isinstance(paths, dict) or not paths:
            out["ignored"][path] = "no paths"
        if path in out["ignored"]:
            ctx.notes.append(f"{path} is named like an OpenAPI spec but is ignored: {out['ignored'][path]}; confirm")
        else:
            out["specs"][path] = doc
    ctx.cache["specs"] = out
    return out


def _operation_ids(doc: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for item in (doc.get("paths") or {}).values():
        if isinstance(item, dict):
            for method in _METHODS:
                op = item.get(method)
                if isinstance(op, dict) and isinstance(op.get("operationId"), str):
                    ids.append(op["operationId"])
    return ids


def _spec_paths(doc: dict[str, Any]) -> list[str]:
    base = ""
    servers = doc.get("servers")
    if isinstance(servers, list) and servers and isinstance(servers[0], dict) and isinstance(servers[0].get("url"), str):
        base = re.sub(r"^https?://[^/]+", "", servers[0]["url"]).rstrip("/")
    elif isinstance(doc.get("basePath"), str):
        base = doc["basePath"].rstrip("/")
    return [base + p for p in (doc.get("paths") or {})]


def _handler_files(ctx: Ctx, ids: list[str]) -> dict[str, tuple[int, int]]:
    """Files that define a module level function named like an operationId (snake_case for Python, as written for JavaScript):
    path to (functions found, line of the first)."""
    wanted = {i: i for i in ids}
    py = ctx.language == "python"
    names = {snake(i) if py else i: i for i in wanted}
    pattern = (re.compile(r"^(?:async[ \t]+)?def[ \t]+(\w+)[ \t]*\(", re.MULTILINE) if py
               else re.compile(r"^(?:export[ \t]+)?(?:async[ \t]+)?(?:function[ \t]+(\w+)|(?:const|let|var)[ \t]+(\w+)[ \t]*=)", re.MULTILINE))
    out: dict[str, tuple[int, int]] = {}
    for path, text in ctx.tree.scan(PY_SUFFIXES if py else JS_SUFFIXES, skip=lambda p: not is_source(p)):
        count, first = 0, 0
        for m in pattern.finditer(text):
            name = m.group(1) or (m.group(2) if m.lastindex and m.lastindex >= 2 else None)
            if name in names:
                count += 1
                first = first or text.count("\n", 0, m.start()) + 1
        if count:
            out[path] = (count, first)
    return out


def _glob_for(paths: list[str], suffix: str) -> list[str]:
    """One `dir/*.ext` per folder that holds handler files."""
    dirs = sorted({p.rsplit("/", 1)[0] if "/" in p else "" for p in paths})
    return [(d + "/" if d else "") + "*" + suffix for d in dirs]


def _generated(path: str, text: str) -> bool:
    head = "\n".join(text.splitlines()[:8]).lower()
    return bool(re.search(r"\b(generated|auto-generated|do not edit)\b", head)) or bool(re.search(r"(?:^|/)(?:generated|gen|sdk|client|openapi)(?:/|$)", path.rsplit("/", 1)[0] + "/"))


def _client_files(ctx: Ctx, ids: list[str]) -> dict[str, tuple[int, int]]:
    if ctx.language not in ("typescript", "javascript"):
        return {}
    found = _handler_files(ctx, ids)
    return {p: v for p, v in found.items() if _generated(p, ctx.tree.text(p) or "")}


def detect_openapi_server(ctx: Ctx) -> list[Found]:
    info = find_specs(ctx)
    if not info["specs"]:
        return []
    out: list[Found] = []
    for path, doc in sorted(info["specs"].items()):
        ids = _operation_ids(doc)
        handlers = _handler_files(ctx, ids) if ids else {}
        if ctx.language != "python":
            handlers = {p: v for p, v in handlers.items() if not _generated(p, ctx.tree.text(p) or "")}
        if handlers or ctx.language == "python":
            line = line_of(ctx.tree.text(path) or "", '"paths"')
            options: dict[str, object] = {"spec": path}
            notes = [Note(f"committed OpenAPI spec with {len(doc['paths'])} paths", (ev(path, line),), "high")]
            extra: list[str] = []
            if handlers:
                suffix = ".py" if ctx.language == "python" else "." + next(iter(handlers)).rsplit(".", 1)[-1]
                globs = _glob_for(sorted(handlers), suffix)
                options["handlers"] = globs[0] if len(globs) == 1 else globs
                total = sum(c for c, _ in handlers.values())
                first = sorted(handlers)[0]
                notes.append(Note(f"handlers: {total} function(s) named like an operationId in {len(handlers)} file(s)", (ev(first, handlers[first][1]),), "medium"))
            else:
                extra.append("handlers left out: no function is named like an operationId, so a change to code reaches no route until `handlers` names the files; confirm")
            ctx.facts.route_hints.extend(s for p in _spec_paths(doc) for s in re.split(r"/+", p) if s and not s.startswith("{"))
            ctx.facts.route_evidence = ctx.facts.route_evidence or ev(path, line)
            ctx.facts.spec_text[path] = ctx.tree.text(path) or ""
            out.append(Found("openapi", options, ("backend",), tuple(notes), score=len(doc["paths"]) + len(handlers) * 5, extra_notes=tuple(extra)))
    return out[:1] if out else []


def detect_openapi_client(ctx: Ctx) -> list[Found]:
    info = find_specs(ctx)
    if not info["specs"] or ctx.language not in ("typescript", "javascript"):
        return []
    for path, doc in sorted(info["specs"].items()):
        ids = _operation_ids(doc)
        files = _client_files(ctx, ids) if ids else {}
        if not files:
            continue
        dirs = sorted({p.rsplit("/", 1)[0] for p in files})
        client: object
        if len(dirs) == 1 and (len(files) > 1 or re.search(r"(?:^|/)(?:generated|gen|sdk|client|openapi)$", dirs[0])):
            client = dirs[0]
        else:
            client = sorted(files)[0] if len(files) == 1 else sorted(files)
        first = sorted(files)[0]
        line = line_of(ctx.tree.text(path) or "", '"paths"')
        notes = (Note(f"committed OpenAPI spec with {len(doc['paths'])} paths", (ev(path, line),), "high"),
                 Note(f"client: {sum(c for c, _ in files.values())} exported function(s) named like an operationId in generated code", (ev(first, files[first][1]),), "medium"))
        ctx.facts.client_paths.extend(_spec_paths(doc))
        ctx.facts.client_evidence = ev(path, line)
        ctx.facts.spec_text[path] = ctx.tree.text(path) or ""
        return [Found("openapi", {"spec": path, "client": client}, (), notes, score=len(files))]
    return []


def register_all() -> None:
    registry.register("http_client", "endpoints-file", detect_endpoints_file, languages=("typescript", "javascript"))
    registry.register("http_client", "openapi", detect_openapi_client, languages=("typescript", "javascript"))
    registry.register("http_server", "openapi", detect_openapi_server)


register_all()
