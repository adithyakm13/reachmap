"""Specifier to file (resolution) and file to every specifier (reverse lookup).

Resolution asks "which file does this specifier mean": relative paths, aliases (the longest
matching pattern first, then the next ones when a pattern's targets hold no such file),
`baseUrl`, the extensions `.ts .tsx .d.ts .js .jsx .mjs .cjs` (and `.mts .cts`), `x.js` meaning
`x.ts` as in TypeScript's ESM style, and `/index.*`. Existence is checked against the files
of the tree at the pinned commit, never against a guess.

Reverse lookup asks "which specifiers mean this file": every alias pattern that maps to it,
not only the longest, with and without extension and `/index`, and relative spellings
computed per importer. Each spelling it returns resolves back to the file.
"""

from __future__ import annotations

import posixpath
from typing import Iterable

from reachmap.adapters.js.aliases import AliasConfig, norm_path

__all__ = ["EXTENSIONS", "JS_EXTENSIONS", "Resolver", "is_js_path"]

EXTENSIONS = (".ts", ".tsx", ".d.ts", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".json")
JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
_JS_SPELLING = {".js": (".ts", ".tsx", ".d.ts", ".js"), ".jsx": (".tsx", ".jsx"), ".mjs": (".mts", ".d.mts", ".mjs"), ".cjs": (".cts", ".d.cts", ".cjs")}
_TS_TO_JS = {".ts": ".js", ".tsx": ".js", ".mts": ".mjs", ".cts": ".cjs"}


def is_js_path(path: str) -> bool:
    return path.endswith(JS_EXTENSIONS)


def _strip_ext(path: str) -> str:
    if path.endswith(".d.ts"):
        return path[:-5]
    name = posixpath.basename(path)
    if "." in name:
        return path[: -(len(name) - name.rindex("."))]
    return path


def _expand(template: str, star: str | None) -> str:
    return template.replace("*", star) if star is not None and "*" in template else template


class Resolver:
    """Resolves specifiers against the files of one tree and one alias configuration."""

    def __init__(self, files: Iterable[str], aliases: AliasConfig) -> None:
        self.files = frozenset(files)
        self.dirs = frozenset(d for f in self.files for d in self._parents(f)) | {""}
        self.aliases = aliases

    @staticmethod
    def _parents(path: str) -> list[str]:
        parts = path.split("/")[:-1]
        return ["/".join(parts[: i + 1]) for i in range(len(parts))]

    # ---- resolution

    def _probe(self, base: str | None) -> str | None:
        """The file a module path (no relative parts left) means, or None."""
        if base is None:
            return None
        name = posixpath.basename(base)
        ext = base[base.rindex("."):] if "." in name else ""
        if ext in _JS_SPELLING:
            stem = base[: -len(ext)]
            for alt in _JS_SPELLING[ext]:
                if stem + alt in self.files:
                    return stem + alt
        if base in self.files:
            return base
        for e in EXTENSIONS:
            if base + e in self.files:
                return base + e
        if base in self.dirs:
            for e in EXTENSIONS:
                cand = f"{base}/index{e}" if base else f"index{e}"
                if cand in self.files:
                    return cand
        return None

    def resolve(self, importer: str, spec: str) -> str | None:
        """The file `spec` means from `importer`, or None (a package, or nothing at that path). The tree and the
        aliases of a resolver do not change, so an answer is kept (every importer search resolves the same
        specifiers of the same candidates again)."""
        key = (importer, spec)
        memo = self.__dict__.setdefault("_memo", {})
        if key not in memo:
            memo[key] = self._resolve(importer, spec)
        return memo[key]

    def _resolve(self, importer: str, spec: str) -> str | None:
        for mark in ("?", "#"):
            if mark in spec[1:]:  # (a leading `#` is a package.json `imports` name, not a fragment)
                spec = spec[:1] + spec[1:][: spec[1:].index(mark)]
        if not spec:
            return None
        if spec in (".", "..") or spec.startswith(("./", "../")):
            return self._probe(norm_path(posixpath.dirname(importer) + "/" + spec if posixpath.dirname(importer) else spec))
        if spec.startswith("/"):
            return self._probe(norm_path(spec))
        for pattern, targets in self._matching(spec):
            for template, star in targets:
                found = self._probe(norm_path(_expand(template, star)))
                if found:
                    return found
        if self.aliases.base_url is not None:
            base = self.aliases.base_url
            return self._probe(norm_path(f"{base}/{spec}" if base else spec))
        return None

    def _matching(self, spec: str) -> list[tuple[str, list[tuple[str, str | None]]]]:
        """The alias patterns that match `spec`, longest prefix first, with each target template
        and what the pattern's `*` matched."""
        matches: list[tuple[int, int, str, list[tuple[str, str | None]]]] = []
        for order, (pattern, targets) in enumerate(self.aliases.paths):
            if "*" in pattern:
                pre, _, suf = pattern.partition("*")
                if len(spec) >= len(pre) + len(suf) and spec.startswith(pre) and spec.endswith(suf):
                    star = spec[len(pre): len(spec) - len(suf)]
                    matches.append((len(pre), order, pattern, [(t, star) for t in targets]))
            elif spec == pattern:
                matches.append((len(pattern), order, pattern, [(t, None) for t in targets]))
        matches.sort(key=lambda m: (-m[0], m[1]))
        return [(m[2], m[3]) for m in matches]

    # ---- reverse lookup

    def _names_of(self, target: str) -> list[str]:
        """The module paths that can stand for the file: without extension, with it, with the
        `.js` spelling of a TypeScript file, and for an index file the folder and its `/index`."""
        base = _strip_ext(target)
        names = [base, target]
        ext = target[len(base):]
        if ext in _TS_TO_JS:
            names.append(base + _TS_TO_JS[ext])
        if posixpath.basename(base) == "index":
            folder = posixpath.dirname(base)
            names.append(folder)
        out: list[str] = []
        for n in names:
            if n not in out:
                out.append(n)
        return out

    def specifiers_for(self, target: str, importer: str | None = None) -> list[str]:
        """Every specifier that means `target`. Relative spellings need the importer; each
        spelling returned resolves back to `target` (checked)."""
        names = self._names_of(target)
        found: set[str] = set()
        if importer is not None:
            start = posixpath.dirname(importer) or "."
            for n in names:
                rel = posixpath.relpath(n, start) if n else posixpath.relpath(".", start)
                found.add(rel if rel.startswith(".") else "./" + rel)
        for pattern, templates in self.aliases.paths:
            for template in templates:
                if "*" not in template:
                    found.add(pattern)  # an exact alias: kept only if it resolves back to the file
                    continue
                pre, _, suf = template.partition("*")
                for n in names:
                    if len(n) >= len(pre) + len(suf) and n.startswith(pre) and n.endswith(suf):
                        found.add(pattern.replace("*", n[len(pre): len(n) - len(suf)]))
        if self.aliases.base_url is not None:
            base = self.aliases.base_url
            for n in names:
                if base == "":
                    found.add(n)
                elif n.startswith(base + "/"):
                    found.add(n[len(base) + 1:])
        probe_from = importer if importer is not None else "\0root.ts"
        return sorted(s for s in found if s and self.resolve(probe_from, s) == target)
