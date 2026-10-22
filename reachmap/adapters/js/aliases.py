"""Import aliases from `tsconfig.json` or `jsconfig.json`: `compilerOptions.paths`, `baseUrl`
and `extends`.

The files are JSON with comments and trailing commas (tsconfig allows both). They are parsed
from the lexer's tokens, so comment markers inside strings are kept and nothing is stripped
with a regular expression.

Rules, as TypeScript has them:

- `paths` of the most derived config that has one replaces the parents' (no merging).
- `baseUrl` of the most derived config that has one wins; it is relative to the file that
  sets it. `paths` targets are relative to `baseUrl` when one is set anywhere in the chain,
  else to the folder of the config that holds `paths`.
- `extends` is a relative file (`.json` added when missing, a folder reads its
  `tsconfig.json`), a list of them (later ones win), or a package name, which cannot be read
  from the repository: that is recorded (not blocking, a stated limit).

A target whose folder does not exist at the pinned commit is recorded (not blocking) and
dropped. A config that does not parse, or a config it extends that is absent, is recorded as
blocking: the alias importers of the repository were not established. The named config itself
being absent at the commit is an established answer (the commit defines no aliases), not a gap.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import Any, Callable

from reachmap.adapters.js.lexer import Token, tokenize, unquote
from reachmap.errors import ModelError
from reachmap.incomplete import kind_spec, record, register_kind
from reachmap.model import ReadResult

__all__ = ["AliasConfig", "CONFIG_NAMES", "detect_config", "load_aliases", "norm_path", "parse_jsonc"]

CONFIG_NAMES = ("tsconfig.json", "jsconfig.json")
MAX_EXTENDS_DEPTH = 10
MAX_JSON_DEPTH = 64

try:
    kind_spec("alias_config_gap")
except ModelError:
    register_kind(
        "alias_config_gap",
        "Imports that go through the path aliases of {subject} were not fully established, because the alias configuration could not be read completely",
        False,
    )


@dataclass(frozen=True, slots=True)
class AliasConfig:
    """`paths` is (pattern, targets) with every target a repository relative template (`*` stands
    for what the pattern's `*` matched). `base_url` is a repository relative folder (`""` is the
    root) or None. `complete` is False when a config that mattered could not be read."""

    source: str | None
    chain: tuple[str, ...]
    base_url: str | None
    paths: tuple[tuple[str, tuple[str, ...]], ...]
    complete: bool = True
    missing: bool = False  # the config that was named is absent at the commit

    @classmethod
    def empty(cls) -> "AliasConfig":
        return cls(None, (), None, ())


def detect_config(has_file: Callable[[str], bool]) -> str | None:
    """The config to read when the repository's config names none: tsconfig before jsconfig."""
    for name in CONFIG_NAMES:
        if has_file(name):
            return name
    return None


# ----------------------------------------------------------------------------- JSON with comments


def parse_jsonc(text: str) -> Any:
    """Parse JSON with comments and trailing commas. Raises ValueError (naming the line)."""
    lex = tokenize(text)
    toks = lex.tokens
    pos = 0

    def fail(message: str, tok: Token | None = None) -> ValueError:
        where = f" at line {tok.line}" if tok is not None else ""
        return ValueError(message + where)

    def value(depth: int) -> Any:
        nonlocal pos
        if depth > MAX_JSON_DEPTH:
            raise fail("nested too deeply")
        if pos >= len(toks):
            raise fail("unexpected end of input")
        t = toks[pos]
        if t.kind == "string":
            pos += 1
            return unquote(t.value)
        if t.kind == "punct" and t.value == "-" and pos + 1 < len(toks) and toks[pos + 1].kind == "number":
            pos += 1
            return -number(toks[pos], t)
        if t.kind == "number":
            return number(t, t)
        if t.kind == "ident" and t.value in ("true", "false", "null"):
            pos += 1
            return {"true": True, "false": False, "null": None}[t.value]
        if t.kind == "punct" and t.value == "{":
            pos += 1
            out: dict[str, Any] = {}
            while True:
                if pos >= len(toks):
                    raise fail("unterminated object")
                t2 = toks[pos]
                if t2.kind == "punct" and t2.value == "}":
                    pos += 1
                    return out
                if t2.kind != "string":
                    raise fail("expected a quoted key", t2)
                key = unquote(t2.value)
                pos += 1
                if not (pos < len(toks) and toks[pos].kind == "punct" and toks[pos].value == ":"):
                    raise fail("expected ':'", toks[pos] if pos < len(toks) else t2)
                pos += 1
                out[key] = value(depth + 1)
                if pos < len(toks) and toks[pos].kind == "punct" and toks[pos].value == ",":
                    pos += 1
                    continue
                if pos < len(toks) and toks[pos].kind == "punct" and toks[pos].value == "}":
                    continue
                raise fail("expected ',' or '}'", toks[pos] if pos < len(toks) else t2)
        if t.kind == "punct" and t.value == "[":
            pos += 1
            items: list[Any] = []
            while True:
                if pos >= len(toks):
                    raise fail("unterminated array")
                t2 = toks[pos]
                if t2.kind == "punct" and t2.value == "]":
                    pos += 1
                    return items
                items.append(value(depth + 1))
                if pos < len(toks) and toks[pos].kind == "punct" and toks[pos].value == ",":
                    pos += 1
                    continue
                if pos < len(toks) and toks[pos].kind == "punct" and toks[pos].value == "]":
                    continue
                raise fail("expected ',' or ']'", toks[pos] if pos < len(toks) else t2)
        raise fail("unexpected token", t)

    def number(t: Token, at: Token) -> int | float:
        nonlocal pos
        pos += 1
        try:
            return int(t.value) if t.value.isdigit() else float(t.value)
        except ValueError:
            raise fail("bad number", at) from None

    if lex.problems:
        raise ValueError(lex.problems[0])
    result = value(0)
    if pos != len(toks):
        raise fail("unexpected text after the value", toks[pos])
    return result


# ----------------------------------------------------------------------------- loading


def norm_path(path: str) -> str | None:
    """A normalised repository relative path, or None when it leaves the repository."""
    parts: list[str] = []
    for seg in path.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(seg)
    return "/".join(parts)


def _join(folder: str, rel: str) -> str | None:
    return norm_path(rel[1:] if rel.startswith("/") else (folder + "/" + rel if folder else rel))


def _target_folder(target: str) -> str:
    """The folder a paths target points into: the part before the `*` up to its last `/`, or the
    parent folder of an exact target."""
    if "*" in target:
        head = target[: target.index("*")]
        return head.rsplit("/", 1)[0] if "/" in head else ""
    return posixpath.dirname(target)


class _Loader:
    def __init__(self, read: Callable[[str], ReadResult], has_dir: Callable[[str], bool], target: Any) -> None:
        self.read = read
        self.has_dir = has_dir
        self.target = target
        self.complete = True
        self.missing = False
        self.unread_package = False
        self.chain: list[str] = []

    def gap(self, subject: str, detail: str, blocking: bool = False) -> None:
        record(self.target, "alias_config_gap", subject, detail=detail, blocks_selection=True if blocking else None)

    def collect(self, path: str, via: str | None, depth: int, seen: tuple[str, ...]) -> list[tuple[str, dict[str, Any]]]:
        """The configs of `path` and what it extends, lowest precedence first."""
        if path in seen:
            self.gap(path, f"extends cycle: {' to '.join(seen + (path,))}")
            return []
        if depth > MAX_EXTENDS_DEPTH:
            self.gap(path, f"extends chain longer than {MAX_EXTENDS_DEPTH}: the rest is not read")
            self.complete = False
            return []
        res = self.read(path)
        if res.is_unreadable:
            self.complete = False  # the git layer recorded the failed read
            return []
        if res.is_absent:
            if via is None:
                self.missing = True
                return []  # no config at this commit (found, absent, unreadable: this one is absent): no aliases are defined
            self.gap(path, f"the config extended by {via} does not exist at the pinned commit", blocking=True)
            self.complete = False
            return []
        try:
            data = parse_jsonc(res.text)
            if not isinstance(data, dict):
                raise ValueError("the top level is not an object")
        except ValueError as exc:
            record(self.target, "read_unreadable", path, detail=f"could not be parsed as JSON with comments: {exc}")
            self.complete = False
            return []
        self.chain.append(path)
        configs: list[tuple[str, dict[str, Any]]] = []
        ext = data.get("extends")
        specs = [ext] if isinstance(ext, str) else [e for e in ext if isinstance(e, str)] if isinstance(ext, list) else []
        folder = posixpath.dirname(path)
        for spec in specs:
            if not (spec.startswith(".") or spec.startswith("/")):
                self.gap(path, f"extends the package {spec!r}, which is not in the repository: its options are not read")
                self.unread_package = True
                continue
            base = _join(folder, spec)
            if base is None:
                self.gap(path, f"extends {spec!r}, which is outside the repository")
                continue
            candidates = [base if base.endswith(".json") else base + ".json"]
            if not base.endswith(".json"):
                candidates.append(base + "/tsconfig.json")
            target = candidates[0]
            first = self.read(candidates[0])
            if first.is_absent and len(candidates) > 1:
                second = self.read(candidates[1])
                if not second.is_absent:
                    target = candidates[1]
            configs += self.collect(target, path, depth + 1, seen + (path,))
        configs.append((path, data))
        return configs


def load_aliases(
    read: Callable[[str], ReadResult],
    config_path: str,
    has_dir: Callable[[str], bool],
    record_to: Any,
) -> AliasConfig:
    """Read the alias config at `config_path` through `read` (found, absent or unreadable) and
    its `extends` chain. `has_dir` says whether a folder exists in the tree at the pinned
    commit. Problems are recorded to `record_to`."""
    loader = _Loader(read, has_dir, record_to)
    configs = loader.collect(config_path, None, 0, ())
    base_dir: str | None = None
    paths_raw: Any = None
    paths_folder = ""
    for path, data in configs:
        folder = posixpath.dirname(path)
        options = data.get("compilerOptions")
        if not isinstance(options, dict):
            continue
        if "baseUrl" in options:
            url = options["baseUrl"]
            joined = _join(folder, url) if isinstance(url, str) else None
            if joined is None:
                loader.gap(path, "baseUrl is not a folder inside the repository: it is ignored")
            else:
                base_dir = joined
        if "paths" in options:
            paths_raw, paths_folder = options["paths"], folder
    paths: list[tuple[str, tuple[str, ...]]] = []
    origin = base_dir if base_dir is not None else paths_folder
    if isinstance(paths_raw, dict):
        for pattern, targets in paths_raw.items():
            subject_cfg = config_path
            if pattern.count("*") > 1:
                loader.gap(subject_cfg, f'the path pattern "{pattern}" has more than one "*": it is ignored')
                continue
            if not isinstance(targets, list) or not all(isinstance(t, str) for t in targets):
                loader.gap(subject_cfg, f'the targets of "{pattern}" are not a list of strings: it is ignored')
                continue
            kept: list[str] = []
            for target in targets:
                resolved = _join(origin, target)
                if resolved is None:
                    loader.gap(f'{subject_cfg} paths "{pattern}" target "{target}"', "the target is outside the repository: ignored")
                    continue
                folder = _target_folder(resolved)
                if not has_dir(folder):
                    loader.gap(f'{subject_cfg} paths "{pattern}" target "{target}"',
                               f"the directory {folder or '.'} does not exist at the pinned commit: the target is ignored")
                    continue
                kept.append(resolved)
            if kept:
                paths.append((pattern, tuple(kept)))
    elif paths_raw is not None:
        loader.gap(config_path, "paths is not an object: it is ignored")
    complete = loader.complete and not (loader.unread_package and not paths and base_dir is None)
    return AliasConfig(source=config_path, chain=tuple(loader.chain), base_url=base_dir, paths=tuple(paths), complete=complete, missing=loader.missing)
