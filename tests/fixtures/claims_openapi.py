"""Independent derivations of what the `oa` scenarios claim (the M16 OpenAPI fixtures), for the tests that do not trust expected.json.

Nothing here uses `reachmap`: the spec is read with `json` and its lines found by a scan of the pretty printed text (two spaces: a path
key at four, a method key at six), routes are composed with `urllib`, an operation is compared by expanding every `$ref` in place
(a cycle is cut where it comes back to a reference being expanded; the analyser instead takes the closure of references and a digest
of each), handlers are found with regular expressions, names are counted with regular expressions. Where the analyser and an oracle
disagree on a fixture, one of them is wrong.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

from tests.fixtures.claims import match_kind

METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
DOC_KEYS = {"description", "summary", "title", "example", "examples", "externalDocs"}
NAME_MAPS = {"properties", "patternProperties", "responses", "content", "headers", "links", "callbacks", "variables", "encoding", "definitions", "$defs"}
GENERIC = {"get", "list", "find", "fetch", "create", "update", "delete", "remove", "add", "set", "put", "post", "all", "item", "items", "data", "info", "ping", "health", "status"}


def snake(name: str) -> str:
    out = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name)
    return re.sub(r"[^A-Za-z0-9]+", "_", out).lower()


def is_generic(name: str) -> bool:
    words = snake(name).split("_")
    return all(w in GENERIC for w in words)


def base_path(doc: dict) -> str:
    servers = doc.get("servers") or [{"url": "/"}]
    return urlsplit(servers[0]["url"]).path.rstrip("/")


def spec_routes(text: str) -> list[dict]:
    """Every operation as {method, path (base path + path), operation_id, template, lines (method key, path key, server url)}."""
    doc = json.loads(text)
    lines = text.split("\n")
    base = base_path(doc)
    url_line = next((i for i, line in enumerate(lines, 1) if re.match(r'^\s+"url": ', line)), 0)
    out = []
    for template, item in doc.get("paths", {}).items():
        path_line = next(i for i, line in enumerate(lines, 1) if line == f'    {json.dumps(template)}: {{')
        for method in METHODS:
            if method not in item:
                continue
            # the method key is the first line at six spaces after the path key that opens it
            method_line = next(i for i in range(path_line + 1, len(lines) + 1) if lines[i - 1] == f'      "{method}": {{')
            out.append({"method": method.upper(), "path": base + template, "template": template, "operation_id": item[method].get("operationId"),
                        "lines": (method_line, path_line, url_line), "node": item[method], "item": item})
    return out


def _walk(node: Any, doc: dict, stack: tuple[str, ...], names_map: bool = False) -> Any:
    """The node with every reference replaced by what it points at, documentation left out, a cycle cut where it comes back."""
    if isinstance(node, dict):
        if "$ref" in node and not names_map and isinstance(node["$ref"], str):
            ref = node["$ref"]
            if ref in stack:
                return {"$cycle": ref}
            target: Any = doc
            for part in ref[2:].split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
            return {"$ref": ref, "is": _walk(target, doc, stack + (ref,))}
        return {k: _walk(v, doc, stack, (not names_map) and k in NAME_MAPS) for k, v in node.items() if names_map or k not in DOC_KEYS}
    if isinstance(node, list):
        return [_walk(v, doc, stack) for v in node]
    return node


def expanded(text: str) -> dict[tuple[str, str], str]:
    """(METHOD, full path) to the operation as it reads with every reference followed (parameters of the path item included)."""
    doc = json.loads(text)
    out = {}
    for r in spec_routes(text):
        node = r["node"]
        params = {}
        for owner in (r["item"], node):
            for p in owner.get("parameters", []):
                key = json.dumps(_walk(p, doc, ()), sort_keys=True)
                real = _walk(p, doc, ())
                real = real.get("is", real)
                params[(real.get("in"), real.get("name"))] = key
        form = {"id": node.get("operationId"), "params": sorted(params.items(), key=str), "body": _walk(node.get("requestBody"), doc, ()), "responses": _walk(node.get("responses"), doc, ())}
        out[(r["method"], r["path"])] = json.dumps(form, sort_keys=True)
    return out


def diff(before: str, after: str) -> dict[str, set[tuple[str, str]]]:
    old, new = expanded(before), expanded(after)
    return {"removed": set(old) - set(new), "added": set(new) - set(old), "changed": {k for k in set(old) & set(new) if old[k] != new[k]}}


def client_urls(web_spec: str) -> dict[str, tuple[str, str]]:
    """operationId to (METHOD, url with `{}` for every parameter) as the web copy of the spec gives it."""
    return {r["operation_id"]: (r["method"], re.sub(r"\{[^{}]*\}", "{}", r["path"])) for r in spec_routes(web_spec) if r["operation_id"]}


def generated_names(client_text: str) -> set[str]:
    return set(re.findall(r"^export function (\w+)\(", client_text, re.M))


def definitions(sources: dict[str, str], name: str) -> int:
    """How many times `name` is defined in the non-test TypeScript files: `function name`, `const name =`, and a method `name(...) {`."""
    n = 0
    for path, text in sources.items():
        if re.search(r"\.(test|spec)\.", path):
            continue
        n += len(re.findall(rf"\bfunction\s+{re.escape(name)}\b", text))
        n += len(re.findall(rf"\b(?:const|let|var)\s+{re.escape(name)}\b", text))
        n += len(re.findall(rf"^\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", text, re.M))
    return n


def best_routes(url: str, method: str, routes: list[dict]) -> list[dict]:
    """The routes with the most literal segments equal to the client's among those it fits (a client parameter fits only a parameter)."""
    fits = [(r, match_kind(url, r["path"])) for r in routes if r["method"] == method]
    fits = [(r, k) for r, k in fits if k]
    if not fits:
        return []
    top = max(sum(1 for c, p in zip(url.strip("/").split("/"), r["path"].strip("/").split("/")) if c == p) for r, _k in fits)
    return [r for r, _k in fits if sum(1 for c, p in zip(url.strip("/").split("/"), r["path"].strip("/").split("/")) if c == p) == top]


def handler_for(operation_id: str, handler_sources: dict[str, str]) -> tuple[str, str] | None:
    """(file, function) of the one function in the handler files named like the operationId or its snake_case form."""
    names = {operation_id, snake(operation_id)}
    found = [(path, n) for path, text in sorted(handler_sources.items()) for n in re.findall(r"^def (\w+)\(", text, re.M) if n in names]
    return found[0] if len(found) == 1 else None
