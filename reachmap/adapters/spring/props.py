"""Spring application configuration: the few keys that decide the URL of a route, read from `application.properties` and the
simple block-mapping subset of `application.yml`.

`server.servlet.context-path` (and `spring.mvc.servlet.path`, `spring.webflux.base-path`) is put before every route, and a mapping may
name a property (`@RequestMapping("${app.api.base}/invoices")`). This reads keys; it does not run Spring: environment variables, command
line arguments, `spring.config.import` and a property computed from another file are not seen (stated limits).

Properties: `key=value`, `key: value` or `key value`; `#` and `!` comments; a line that ends with a backslash continues; a line `#---` (or
`!---`) starts a new document (Spring Boot 2.4 and later), and a document that names a profile is a profile document, as in YAML; the last value
of a key wins (as in Spring). YAML: block mappings by indentation (a dotted key such as `server.servlet.context-path: /api` too), scalars
plain or quoted, comments, block lists (the key of a list holds no scalar), several documents separated by `---` (a document that names a
profile with `spring.config.activate.on-profile` or `spring.profiles` is a profile document: its values only apply when that profile is
active). A construct this reader does not understand (a tab in the indentation, an anchor or alias, a flow mapping `{...}`, a literal
block `|` or `>`) makes the file `unread` for the keys under it: whether it sets a key was not established, and the caller says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = ["Config", "Entry", "PATH_KEYS", "parse_properties", "parse_yaml", "read_config"]

PATH_KEYS = ("server.servlet.context-path", "spring.mvc.servlet.path", "spring.webflux.base-path")
_PLACEHOLDER = re.compile(r"\$\{([^${}:]+)(?::([^${}]*))?\}")
_PROFILE_KEYS = ("spring.config.activate.on-profile", "spring.profiles")


@dataclass(frozen=True, slots=True)
class Entry:
    """One value of one key: `profile` is None for a value that always applies, else the profile expression it applies under;
    `unread` marks a value the reader could not take (a flow mapping, a literal block): the key is set, to something unknown."""

    key: str
    value: str
    path: str
    line: int
    profile: str | None = None
    unread: bool = False


@dataclass(slots=True)
class Config:
    """The entries of one or more files and what could not be read of them (`unread`: file and reason)."""

    entries: list[Entry] = field(default_factory=list)
    unread: list[tuple[str, str]] = field(default_factory=list)

    def get(self, key: str) -> list[Entry]:
        return [e for e in self.entries if e.key == key]

    def base(self, key: str) -> Entry | None:
        """The value that always applies (the last one given); None when the key is only set under a profile or not at all."""
        found = [e for e in self.get(key) if e.profile is None]
        return found[-1] if found else None

    def profiled(self, key: str) -> list[Entry]:
        return [e for e in self.get(key) if e.profile is not None]

    def keys(self) -> set[str]:
        return {e.key for e in self.entries}


def _decode_value(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def parse_properties(text: str, path: str, profile: str | None = None) -> Config:
    """A `.properties` file: every key with its value and line. Documents are split at `#---`; a document that sets
    `spring.config.activate.on-profile` (or `spring.profiles`) applies under that profile only."""
    docs: list[Config] = [Config()]
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        raw = lines[i].rstrip("\r")
        start = i + 1
        i += 1
        stripped = raw.lstrip()
        if stripped.rstrip() in ("#---", "!---"):
            docs.append(Config())
            continue
        if not stripped or stripped[0] in "#!":
            continue
        logical = raw
        while logical.rstrip().endswith("\\") and not logical.rstrip().endswith("\\\\") and i < len(lines):
            logical = logical.rstrip()[:-1] + lines[i].lstrip().rstrip("\r")
            i += 1
        m = re.match(r"^\s*((?:\\.|[^:=\s\\])+)\s*[:=]?\s*(.*)$", logical)
        if not m:
            continue
        key = re.sub(r"\\(.)", r"\1", m.group(1))
        docs[-1].entries.append(Entry(key, m.group(2).strip(), path, start, profile))
    return _with_document_profiles(docs, profile)


def _with_document_profiles(docs: list[Config], profile: str | None) -> Config:
    """The entries of the documents of one file, each under the profile its own `spring.config.activate.on-profile` names (else the file's)."""
    cfg = Config()
    for sub in docs:
        doc_profile = None
        for e in sub.entries:
            if e.key in _PROFILE_KEYS:
                doc_profile = e.value
        for e in sub.entries:
            if e.key in _PROFILE_KEYS:
                continue
            cfg.entries.append(Entry(e.key, e.value, e.path, e.line, doc_profile or profile, e.unread))
        cfg.unread.extend(sub.unread)
    return cfg


def parse_yaml(text: str, path: str, profile: str | None = None) -> Config:
    """The block-mapping subset of YAML (see the module docstring). Documents are split at `---`."""
    docs: list[list[tuple[int, str]]] = [[]]
    for n, raw in enumerate(text.split("\n"), 1):
        line = raw.rstrip("\r")
        if line.strip() in ("---", "--- "):
            docs.append([])
            continue
        docs[-1].append((n, line))
    return _with_document_profiles([_yaml_document(doc, path) for doc in docs], profile)


def _yaml_document(rows: list[tuple[int, str]], path: str) -> Config:
    cfg = Config()
    stack: list[tuple[int, str]] = []  # (indent, key path) of the mappings that are open
    skip_indent: int | None = None  # inside a literal block or a list: lines indented deeper than this are not read
    for n, raw in rows:
        body = _strip_comment(raw)
        if not body.strip():
            continue
        indent = len(body) - len(body.lstrip(" "))
        if body[indent:indent + 1] == "\t" or "\t" in body[:indent]:
            cfg.unread.append((path, f"line {n}: a tab in the indentation"))
            continue
        if skip_indent is not None:
            if indent > skip_indent:
                continue
            skip_indent = None
        text = body.strip()
        if text.startswith("-"):  # a list item: the key above holds a list, not a scalar
            continue
        m = re.match(r"^((?:\"[^\"]*\"|'[^']*'|[^:\s][^:]*?))\s*:(?:\s+(.*))?$", text)
        if not m:
            cfg.unread.append((path, f"line {n}: not a key"))
            continue
        key = _decode_value(m.group(1))
        value = (m.group(2) or "").strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        full = f"{stack[-1][1]}.{key}" if stack else key
        if value == "":
            stack.append((indent, full))
            continue
        if value[0] in "|>":
            cfg.entries.append(Entry(full, "", path, n, None, True))
            cfg.unread.append((path, f"line {n}: a literal block is not read"))
            skip_indent = indent
            continue
        if value[0] in "&*!":
            cfg.entries.append(Entry(full, "", path, n, None, True))
            cfg.unread.append((path, f"line {n}: an anchor, an alias or a tag is not read"))
            continue
        if value[0] in "{[":
            cfg.entries.append(Entry(full, value, path, n, None, value[0] == "{"))
            if value[0] == "{":
                cfg.unread.append((path, f"line {n}: a flow mapping is not read"))
            continue
        cfg.entries.append(Entry(full, _decode_value(value), path, n))
    return cfg


def _strip_comment(line: str) -> str:
    out: list[str] = []
    quote = ""
    for i, c in enumerate(line):
        if quote:
            if c == quote:
                quote = ""
        elif c in "\"'" and (i == 0 or line[i - 1] in " :[{,-"):
            quote = c
        elif c == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        out.append(c)
    return "".join(out)


def read_config(path: str, text: str) -> Config:
    """The entries of one file by its name: `application-dev.properties` is under the profile `dev`; `.yml` and `.yaml` are YAML."""
    base = path.rsplit("/", 1)[-1]
    m = re.match(r"^(?:application|bootstrap)-([^.]+)\.(?:properties|ya?ml)$", base)
    profile = m.group(1) if m else None
    return parse_yaml(text, path, profile) if base.endswith((".yml", ".yaml")) else parse_properties(text, path, profile)


def substitute(value: str, lookup) -> tuple[str, list[str]]:  # noqa: ANN001 - lookup: a key to an Entry or None
    """The value with each `${key}` and `${key:default}` replaced by what `lookup` gives (an Entry or None); the placeholders that could not
    be replaced are returned (the value keeps them). A key that is set to something unread is `<unknown:${key}>`, whatever its default is."""
    missing: list[str] = []

    def one(m: re.Match[str]) -> str:
        entry = lookup(m.group(1))
        if entry is not None and entry.unread:  # the key is set, to something this reader could not take: the default is not what applies
            missing.append(m.group(1))
            return f"<unknown:${{{m.group(1)}}}>"
        if entry is not None:
            return entry.value
        if m.group(2) is not None:
            return m.group(2)
        missing.append(m.group(1))
        return m.group(0)

    return _PLACEHOLDER.sub(one, value), missing


def placeholders(value: str) -> list[str]:
    """The keys a value names with `${key}` or `${key:default}`."""
    return [m.group(1) for m in _PLACEHOLDER.finditer(value)]
