"""Load and validate `reachmap.toml` into typed, immutable config (context 7.2 and 13.2).

    ws = load("reachmap.toml")                       # reads os.environ for ${VAR}
    ws = loads(text, fmt="toml", env={...})          # tests pass env, never the real one

One pass over the file collects every problem, each with its dotted key path
(`repos."acme/web".entry_points.kind`), and raises one `ConfigError` listing all of them.
Objects are only built when there is no problem, so a half-valid config never escapes.

Environment variables: `${VAR}` expands inside string values (never inside `token_env`,
which holds a variable NAME). An unset or empty variable makes the whole value "not
configured": `None`, never the literal text and never an empty string. `$${` is a literal
`${`. A variable whose name looks like a credential (`..._TOKEN`, `..._SECRET`, ...) is
refused: a credential is read by name through `token_env`, so no credential value is ever
stored in a Workspace, shown in a repr or put in an error message.

A relative `remote` (a path, not a URL) and a relative context `path` resolve against the
config file's directory and are recorded resolved. Paths inside a repository
(`http_client.path`, `routes_glob`) are not machine paths and stay as written.
"""

from __future__ import annotations

import difflib
import fnmatch
import os
import re
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, NamedTuple
from urllib.parse import urlsplit

from reachmap import config_options, config_sources
from reachmap.keypath import join as _join

HOSTS = ("github", "gitlab", "bitbucket", "git")
ROLES = ("frontend", "backend", "contract", "library", "worker")
EDGE_KINDS = ("http", "package", "contract", "event", "library")

_LENIENT_SECTIONS = ("test_sources", "context")  # where an unset variable means "not configured"
_TOP_KEYS = ("workspace", "ticket_pattern", "limits", "repos", "edges", "test_sources", "context")
_REPO_KEYS = (
    "host", "remote", "language", "roles", "branches", "aliases", "entry_points", "http_client",
    "tests", "service_marker", "contract", "primary_branch", "library_roots", "http_server",
)

# ---------------------------------------------------------------------------- registries
# Adapters add to these (M16 adds java and kotlin).
#
# An option validator is called as `validator(options, describe)` with the options
# AFTER ${VAR} expansion, so they can hold values that came from the environment. A
# validator returns problem messages and must never put an option value in one: say what
# was expected, and use `describe(value)` (a type name, never the value) to say what was found.

Describe = Callable[[Any], str]
OptionValidator = Callable[[Mapping[str, Any], Describe], list[str]]

_LANGUAGES: set[str] = {"typescript", "javascript", "python"}


@dataclass(frozen=True)
class _KindSpec:
    validator: OptionValidator | None = None
    machine_paths: tuple[str, ...] = ()  # option keys holding a path on this machine


@dataclass
class _Section:
    kinds: dict[str, _KindSpec]
    required: bool  # whether a table of this section must say which kind it is
    plain: OptionValidator | None = None  # the validator of a table with no kind (`tests`, `aliases`): it refuses keys the adapters do not read


def _core(section: str, kind: str, **kw: Any) -> _KindSpec:
    return _KindSpec(config_options.VALIDATORS[(section, kind)], **kw)


_SECTIONS: dict[str, _Section] = {
    "entry_points": _Section({kind: _core("entry_points", kind) for kind in ("react-router", "fastapi", "celery")}, True),
    "http_client": _Section({"endpoints-file": _core("http_client", "endpoints-file")}, True),
    "http_server": _Section({}, True),  # a committed spec that describes the routes a repository serves; the adapters register their kinds
    "contract": _Section({"pydantic": _core("contract", "pydantic")}, True),
    "aliases": _Section({}, False, config_options.PLAIN["aliases"]),
    "tests": _Section({}, False, config_options.PLAIN["tests"]),
    "test_sources": _Section({kind: _KindSpec(check) for kind, check in config_sources.validators().items()}, True),
    "context": _Section({"jira-snapshot": _core("context", "jira-snapshot", machine_paths=("path",))}, True),
}


# Keys an [[edges]] table of each kind may carry beyond `kind` and its two repos. `package`
# also takes manifest, pin_pattern and import_marker, which the core reads itself. The extra
# keys are plain non-empty strings; an adapter that needs more registers them here.
_EDGE_OPTION_KEYS: dict[str, tuple[str, ...]] = {
    "http": (), "package": (), "contract": (), "event": ("topics_glob",), "library": (),
}
_PACKAGE_KEYS = ("manifest", "pin_pattern", "import_marker")


def register_edge_keys(kind: str, *keys: str) -> None:
    """Let `[[edges]]` of `kind` carry these extra string keys (kept in `Edge.options`)."""
    if kind not in _EDGE_OPTION_KEYS:
        raise ValueError(f"unknown edge kind {kind!r}; known: {', '.join(_EDGE_OPTION_KEYS)}")
    _EDGE_OPTION_KEYS[kind] = tuple(dict.fromkeys(_EDGE_OPTION_KEYS[kind] + keys))


def register_language(name: str) -> None:
    _LANGUAGES.add(name)


def languages() -> tuple[str, ...]:
    """The languages a repo may declare, sorted: the built-in ones and what adapters registered."""
    return tuple(sorted(_LANGUAGES))


def kinds(section: str) -> tuple[str, ...]:
    """The kinds registered for a repo sub-table or source section (`entry_points`, `http_client`, `http_server`, `contract`,
    `test_sources`, ...), in registration order."""
    if section not in _SECTIONS:
        raise ValueError(f"unknown config section {section!r}; known: {', '.join(_SECTIONS)}")
    return tuple(_SECTIONS[section].kinds)


def register_kind(
    section: str,
    kind: str,
    validator: OptionValidator | None = None,
    *,
    machine_paths: tuple[str, ...] = (),
    replace: bool = False,
) -> None:
    """Make `kind` valid in `section` (a repo sub-table, `test_sources` or `context`).
    `machine_paths` names the option keys that hold a path on this machine: a relative one
    is resolved against the config file's directory. Paths inside a repository are not
    machine paths. See the note above on what a validator may say."""
    if section not in _SECTIONS:
        raise ValueError(f"unknown config section {section!r}; known: {', '.join(_SECTIONS)}")
    if kind in _SECTIONS[section].kinds and not replace:
        raise ValueError(f"kind {kind!r} is already registered for {section}")
    _SECTIONS[section].kinds[kind] = _KindSpec(validator, machine_paths)


# ---------------------------------------------------------------------------- types


class Problem(NamedTuple):
    path: str  # dotted key path; empty for a problem with the whole file
    message: str


class ConfigError(ValueError):
    """The config has problems. `problems` lists every one found, not just the first."""

    def __init__(self, source: str, problems: list[Problem] | tuple[Problem, ...]) -> None:
        self.source = source
        self.problems = tuple(problems)
        count = len(self.problems)
        lines = [f"{source}: {count} problem{'s' if count != 1 else ''} in the config"]
        lines += [f"  {p.path}: {p.message}" if p.path else f"  {p.message}" for p in self.problems]
        super().__init__("\n".join(lines))


class FrozenMap(Mapping[str, Any]):
    """A read-only mapping that pickles, copies and hashes (a `MappingProxyType` does none
    of those, and a Workspace has to travel to a spawned process). Values are expected to
    be immutable already (`_freeze` makes them so)."""

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[str, Any] | None = None) -> None:
        object.__setattr__(self, "_data", dict(data or {}))

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("a FrozenMap cannot be changed")

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __hash__(self) -> int:
        return hash(frozenset(self._data.items()))

    def __reduce__(self) -> tuple[Any, ...]:
        return (FrozenMap, (self._data,))

    def __copy__(self) -> FrozenMap:
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> FrozenMap:
        return self

    def __repr__(self) -> str:
        return f"FrozenMap({self._data!r})"


def _empty_map() -> FrozenMap:
    return FrozenMap()


@dataclass(frozen=True, slots=True)
class Limits:
    hops: int = 3
    hub_importers: int = 50
    distinct_max: int = 10
    timeout_s: int = 900
    module_cap: int = 200


_LIMIT_DEFAULTS = {f.name: f.default for f in fields(Limits)}  # the dataclass is the one source


@dataclass(frozen=True, slots=True)
class Repo:
    """`aliases`, `entry_points`, `http_client`, `tests` and `contract` are frozen mappings
    holding `kind` (when given) and the adapter's options, or None when absent."""

    name: str
    host: str
    remote: str | None
    language: str
    roles: tuple[str, ...]
    branches: tuple[str, ...]
    aliases: Mapping[str, Any] | None
    entry_points: Mapping[str, Any] | None
    http_client: Mapping[str, Any] | None
    tests: Mapping[str, Any] | None
    service_marker: str | None
    contract: Mapping[str, Any] | None
    primary_branch: str
    library_roots: tuple[str, ...] = ()
    http_server: Mapping[str, Any] | None = None  # a committed spec that describes the routes this repository serves (kind openapi)


@dataclass(frozen=True, slots=True)
class Edge:
    """`from_repo` and `to_repo` are `from` and `to` as written; for `package` (and a
    `contract` edge written that way) they are `producer` and `consumer`."""

    kind: str
    from_repo: str
    to_repo: str
    manifest: str | None = None
    pin_pattern: re.Pattern[str] | None = None
    import_marker: str | None = None
    options: Mapping[str, Any] = field(default_factory=_empty_map)  # keys the core does not read (topics_glob)

    @property
    def producer(self) -> str:
        return self.from_repo

    @property
    def consumer(self) -> str:
        return self.to_repo


@dataclass(frozen=True, slots=True)
class TestSource:
    """`configured` is False when an option or area value came from an unset variable
    (`unset_vars` names them): the run records `source_unconfigured` (non-blocking), naming
    the variables, instead of failing."""

    kind: str
    options: Mapping[str, Any]
    areas: tuple[Mapping[str, Any], ...] = ()
    unset_vars: tuple[str, ...] = ()  # variables that were unset or empty, in options or areas

    @property
    def configured(self) -> bool:
        return not self.unset_vars


@dataclass(frozen=True, slots=True)
class ContextSource:
    kind: str
    options: Mapping[str, Any]
    unset_vars: tuple[str, ...] = ()  # variables that were unset or empty

    @property
    def configured(self) -> bool:
        return not self.unset_vars


@dataclass(frozen=True, slots=True)
class Workspace:
    name: str
    ticket_pattern: re.Pattern[str] | None
    limits: Limits
    repos: Mapping[str, Repo]
    edges: tuple[Edge, ...]
    test_sources: tuple[TestSource, ...]
    context: tuple[ContextSource, ...]
    source: str


# ---------------------------------------------------------------------------- entry points


_JSON_REFUSED = "JSON configs are not supported; use a .toml file"


def load(path: str | Path, env: Mapping[str, str] | None = None) -> Workspace:
    """Read a config file. Only `.toml` is a config format; a `.json` file is refused, unread."""
    path = Path(path)
    source = str(path)
    suffix = path.suffix.lower()
    if suffix == ".json":
        raise ConfigError(source, [Problem("", _JSON_REFUSED)])
    if suffix != ".toml":
        raise ConfigError(source, [Problem("", f"unsupported file type {path.suffix!r}; use a .toml file")])
    problem = None
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        problem = "the file is not valid UTF-8"
    except OSError as exc:
        problem = f"cannot read the file: {exc.strerror or type(exc).__name__}"
    if problem is not None:  # raised outside the except block: no __context__ to hold the file
        raise ConfigError(source, [Problem("", problem)])
    return loads(text, fmt="toml", env=env, source=source, base_dir=path.absolute().parent)


def loads(
    text: str,
    *,
    fmt: str = "toml",
    env: Mapping[str, str] | None = None,
    source: str = "<string>",
    base_dir: str | Path | None = None,
) -> Workspace:
    """Parse and validate config text. `fmt` is `"toml"`, the only format (`"json"` is refused like a
    `.json` file). `base_dir` (default: the directory of `source`, or the working directory for a string)
    is what relative remotes and paths resolve against."""
    if fmt == "json":
        raise ConfigError(source, [Problem("", _JSON_REFUSED)])
    if fmt != "toml":
        raise ValueError(f"unknown config format {fmt!r}; use 'toml'")
    environment = os.environ if env is None else env
    base = Path(base_dir).absolute() if base_dir is not None else Path(source).absolute().parent
    return _build(_parse_toml(text, source), environment, source, str(base))


def _parse_toml(text: str, source: str) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        message = f"not valid TOML: {exc}"
    raise ConfigError(source, [Problem("", message)])  # outside the handler: no __context__


def _build(data: dict[str, Any], env: Mapping[str, str], source: str, base_dir: str) -> Workspace:
    check = _Check(env, base_dir)
    expanded = check.expand(data)
    workspace = check.workspace(expanded, source)
    if check.problems or workspace is None:
        raise ConfigError(source, list(check.problems))
    return workspace


# ---------------------------------------------------------------------------- validation

_REFERENCE = re.compile(r"\$\$\{|\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$\{")
_CREDENTIAL_NAME = re.compile(r"TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|API_?KEY|PRIVATE_?KEY|ACCESS_?KEY|(?:^|_)PAT(?:_|$)", re.IGNORECASE)
_TOKEN_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
_URL = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_SCP_LIKE = re.compile(r"^(?:[^/\s@:]+@)?[^/\s@:]+:(?!//)")  # host:path and user@host:path
_SCP_PASSWORD = re.compile(r"^[^/\s@]+:[^/\s@]*@")  # user:password@host:path
_SSH_SCHEMES = ("ssh", "git+ssh", "ssh+git")  # where a bare user name (git@) is normal
_GLOB = re.compile(r"[*?\[]")


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return FrozenMap({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


def _type_name(value: Any) -> str:
    """What a value is, never what it says: configured values do not go into messages."""
    if isinstance(value, bool):
        return "a boolean"
    names = {int: "an integer", float: "a number", str: "a string", dict: "a table", list: "a list"}
    return names.get(type(value), type(value).__name__)


def _kinds(section: str) -> str:
    return ", ".join(_SECTIONS[section].kinds) or "none registered"


class _Check:
    """One validation pass. `problems` collects; builders return None when they added one."""

    def __init__(self, env: Mapping[str, str], base_dir: str) -> None:
        self.env = env
        self.base_dir = base_dir
        self.problems: list[Problem] = []
        self.unset: dict[str, tuple[str, ...]] = {}  # path of a not-configured value: its variable names
        self.failed: set[str] = set()  # paths whose expansion already produced a problem

    # ---- bookkeeping

    def add(self, path: str, message: str) -> None:
        self.problems.append(Problem(path, message))

    def not_configured(self, path: str) -> str:
        names = self.unset.get(path)
        return f"not configured: variable {', '.join(names)} is unset or empty" if names else "not configured"

    def unset_under(self, base: str) -> tuple[str, ...]:
        """Every variable that was unset or empty in a value at or below `base`."""
        inside = (base, base + ".", base + "[")
        names = {n for path, vars_ in self.unset.items() if path.startswith(inside) for n in vars_}
        return tuple(sorted(names))

    # ---- ${VAR} expansion (a pass over the parsed file, before validation)

    def expand(self, data: dict[str, Any]) -> dict[str, Any]:
        return self.expand_value(data, "", True)

    def expand_value(self, value: Any, path: str, strict: bool) -> Any:
        """`strict`: an unset variable is a problem. Only test_sources and context may be
        "not configured": a repo, edge, limit or name quietly missing would be an absence
        the tool manufactured."""
        if isinstance(value, dict):
            out = {}
            for key, item in value.items():
                child = _join(path, key)
                if key == "token_env":  # a variable NAME: checked, never expanded
                    self.check_token_env(item, child)
                    out[key] = item
                else:
                    child_strict = strict if path else key not in _LENIENT_SECTIONS
                    out[key] = self.expand_value(item, child, child_strict)
            return out
        if isinstance(value, list):
            return [self.expand_value(v, _join(path, i), strict) for i, v in enumerate(value)]
        if isinstance(value, str):
            return self.expand_string(value, path, strict)
        return value

    def expand_string(self, text: str, path: str, strict: bool) -> str | None:
        parts: list[str] = []
        missing: list[str] = []
        broken = False
        last = 0
        for match in _REFERENCE.finditer(text):
            parts.append(text[last:match.start()])
            last = match.end()
            token, name = match.group(0), match.group(1)
            if token == "$${":
                parts.append("${")
            elif name is None:
                self.add(path, "malformed variable reference; write ${NAME}, or $${ for a literal ${")
                broken = True
            elif _CREDENTIAL_NAME.search(name):
                self.add(path, f"variable {name} looks like a credential and is not expanded into values; "
                               "name it in token_env instead")
                broken = True
            else:
                value = self.env.get(name)
                if value:
                    parts.append(value)
                else:
                    missing.append(name)
        parts.append(text[last:])
        if broken:
            self.failed.add(path)
            return None
        if missing:
            self.unset[path] = tuple(missing)
            if strict:
                self.add(path, f"{self.not_configured(path)}; only test_sources and context may be left "
                               "unconfigured")
                self.failed.add(path)
            return None
        return "".join(parts)

    # ---- small readers; each reports its own problem and returns None

    def table(self, path: str, value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            return value
        if not (value is None and path in self.failed):
            self.add(path, f"expected a table, got {_type_name(value)}")
        return None

    def unknown_keys(self, table: Mapping[str, Any], base: str, allowed: tuple[str, ...]) -> None:
        for key in table:
            if key in allowed:
                continue
            hint = difflib.get_close_matches(key, allowed, n=1)
            self.add(_join(base, key), f"unknown key; allowed keys: {', '.join(allowed)}"
                     + (f" (did you mean {hint[0]!r}?)" if hint else ""))

    def text(self, table: Mapping[str, Any], base: str, key: str, *, required: bool = False) -> str | None:
        """A non-empty string. Absent is fine unless required; not configured never is."""
        path = _join(base, key)
        if key not in table:
            if required:
                self.add(path, "missing; it is required")
            return None
        value = table[key]
        if value is None:
            if path not in self.failed:
                self.add(path, self.not_configured(path))
            return None
        if not isinstance(value, str) or not value.strip():
            self.add(path, f"expected a non-empty string, got {_type_name(value)}")
            return None
        return value

    def optional_text(self, table: Mapping[str, Any], base: str, key: str) -> str | None:
        """A string that may be not configured (None) without being a problem."""
        if table.get(key) is None:
            return None
        return self.text(table, base, key)

    def choice(self, table: Mapping[str, Any], base: str, key: str, allowed: tuple[str, ...] | list[str],
               what: str, *, required: bool = True) -> str | None:
        value = self.text(table, base, key, required=required)
        if value is not None and value not in allowed:
            path = _join(base, key)
            self.add(path, f"unknown {what}; allowed: {', '.join(allowed)}")
            return None
        return value

    def strings(self, table: Mapping[str, Any], base: str, key: str, *, required: bool = False,
                nonempty: bool = False, allowed: tuple[str, ...] | None = None,
                what: str = "value") -> tuple[str, ...] | None:
        """A list of distinct non-empty strings."""
        path = _join(base, key)
        if key not in table:
            if required:
                self.add(path, "missing; it is required")
            return None
        value = table[key]
        if not isinstance(value, list):
            if not (value is None and path in self.failed):
                self.add(path, f"expected a list of strings, got {_type_name(value)}")
            return None
        before = len(self.problems)
        items: list[str] = []
        for i, item in enumerate(value):
            item_path = _join(path, i)
            if item is None:
                if item_path not in self.failed:
                    self.add(item_path, self.not_configured(item_path))
            elif not isinstance(item, str) or not item.strip():
                self.add(item_path, f"expected a non-empty string, got {_type_name(item)}")
            elif allowed is not None and item not in allowed:
                self.add(item_path, f"unknown {what}; allowed: {', '.join(allowed)}")
            elif item in items:
                self.add(item_path, f"duplicate {what}")
            else:
                items.append(item)
        if nonempty and not value and len(self.problems) == before:
            self.add(path, "must not be empty")
        return tuple(items) if len(self.problems) == before else None

    def pattern(self, path: str, text: str, *, groups: int | None = None, what: str = "") -> re.Pattern[str] | None:
        try:
            compiled = re.compile(text)
        except re.error as exc:
            where = f" (error at position {exc.pos})" if exc.pos is not None else ""
            self.add(path, f"not a valid regular expression{where}")
            return None
        if groups is not None and compiled.groups != groups:
            self.add(path, f"the pattern has {compiled.groups} groups; it needs exactly one group, "
                           f"the part that captures {what}")
            return None
        return compiled

    def check_token_env(self, value: Any, path: str) -> None:
        """token_env is a variable NAME, wherever it appears. Anything else is probably a
        pasted token: say so, and never print it."""
        if not isinstance(value, str):
            self.add(path, "must be a string naming an environment variable")
        elif not _TOKEN_ENV_NAME.match(value):
            self.add(path, "must be an environment variable name (upper case letters, digits and "
                           "underscores, at most 64 characters), not a template. The value is not shown: "
                           "if it is a token, remove it from the file and revoke it")

    def kind_of(self, table: Mapping[str, Any], base: str, section: str) -> str | None:
        """Check `kind` against the registry; run the adapter's option validator if any."""
        path = _join(base, "kind")
        spec = _SECTIONS[section]
        known = spec.kinds
        if "kind" not in table:
            if spec.required:
                self.add(path, f"missing; known kinds: {_kinds(section)}")
            elif spec.plain is not None:
                for message in spec.plain(_freeze(table), _type_name):
                    self.add(base, message)
            return None
        kind = table["kind"]
        if kind is None and path in self.unset:
            self.add(path, self.not_configured(path))
            return None
        if not isinstance(kind, str) or kind not in known:
            self.add(path, f"unknown kind; known kinds: {_kinds(section)}")
            return None
        validator = known[kind].validator
        if validator is not None:
            for message in validator(_freeze(table), _type_name):
                self.add(base, message)
        return kind

    # ---- top level

    def workspace(self, data: dict[str, Any], source: str) -> Workspace | None:
        self.unknown_keys(data, "", _TOP_KEYS)
        name = self.text(data, "", "workspace", required=True)
        ticket_pattern = None
        text = self.text(data, "", "ticket_pattern")
        if text is not None:
            ticket_pattern = self.pattern("ticket_pattern", text, groups=1, what="the ticket key")
        limits = self.limits(data.get("limits"))
        repos = self.repos(data.get("repos"), "repos" in data)
        edges = self.edges(data.get("edges"), set(data["repos"]) if isinstance(data.get("repos"), dict) else set())
        test_sources = self.test_sources(data.get("test_sources"), repos if repos is not None else {})
        context = self.context(data.get("context"))
        if self.problems or name is None or limits is None or repos is None:
            return None
        return Workspace(name, ticket_pattern, limits, FrozenMap(repos), tuple(edges or ()),
                         tuple(test_sources or ()), tuple(context or ()), source)

    def limits(self, raw: Any) -> Limits | None:
        if raw is None:
            return Limits()
        table = self.table("limits", raw)
        if table is None:
            return None
        before = len(self.problems)
        self.unknown_keys(table, "limits", tuple(_LIMIT_DEFAULTS))
        values: dict[str, int] = {}
        for key, default in _LIMIT_DEFAULTS.items():
            value = table.get(key, default)
            path = _join("limits", key)
            if value is None and path in self.failed:
                continue
            if isinstance(value, int) and not isinstance(value, bool):
                if value < 1:
                    self.add(path, "must be a positive integer (1 or more)")
                else:
                    values[key] = value
            else:
                self.add(path, f"must be a positive integer, got {_type_name(value)}")
        return Limits(**values) if len(self.problems) == before else None

    # ---- repos

    def repos(self, raw: Any, present: bool) -> dict[str, Repo] | None:
        if not present:
            self.add("repos", 'missing; add one [repos."owner/name"] table per repository')
            return None
        table = self.table("repos", raw)
        if table is None:
            return None
        if not table:
            self.add("repos", 'empty; add one [repos."owner/name"] table per repository')
            return None
        repos: dict[str, Repo] = {}
        for name, value in table.items():
            if not name.strip():
                self.add(_join("repos", name), "a repo name cannot be empty")
                continue
            repo = self.repo(name, value)
            if repo is not None:
                repos[name] = repo
        return repos if len(repos) == len(table) else None

    def repo(self, name: str, raw: Any) -> Repo | None:
        base = _join("repos", name)
        table = self.table(base, raw)
        if table is None:
            return None
        before = len(self.problems)
        self.unknown_keys(table, base, _REPO_KEYS)
        host = self.choice(table, base, "host", HOSTS, "host")
        remote = self.remote(table, base, host)
        language = self.choice(table, base, "language", sorted(_LANGUAGES), "language")
        roles = self.strings(table, base, "roles", required=True, nonempty=True, allowed=ROLES, what="role")
        branches = self.strings(table, base, "branches", nonempty=True, what="branch")
        sections = {
            key: self.section(table, base, key) for key in ("aliases", "entry_points", "http_client", "http_server", "tests")
        }
        if sections["http_server"] is not None and (sections["entry_points"] or {}).get("kind", "celery") != "celery":
            self.add(_join(base, "http_server"), "a repository has one source of routes: its entry_points (kind "
                     f"{(sections['entry_points'] or {}).get('kind')}) or an http_server, not both (only a celery entry_points, which has no routes, goes with one)")
        service_marker = self.optional_text(table, base, "service_marker")
        contract = self.section(table, base, "contract")
        primary = self.primary_branch(table, base, branches)
        library_roots = self.strings(table, base, "library_roots", what="library root")
        if len(self.problems) != before or host is None or language is None or roles is None or primary is None:
            return None
        return Repo(
            name=name, host=host, remote=remote, language=language, roles=roles,
            branches=branches if branches is not None else ("main",),
            aliases=sections["aliases"], entry_points=sections["entry_points"],
            http_client=sections["http_client"], tests=sections["tests"],
            service_marker=service_marker, contract=contract, primary_branch=primary,
            library_roots=library_roots or (), http_server=sections["http_server"],
        )

    def remote(self, table: Mapping[str, Any], base: str, host: str | None) -> str | None:
        path = _join(base, "remote")
        value = table.get("remote")
        if value is None:
            if host == "git" and "remote" not in table:  # an unset variable was reported already
                self.add(path, "host git needs a remote (a URL or a path)")
            return None
        text = self.text(table, base, "remote")
        if text is None:
            return None
        return self.checked_remote(path, text)

    def checked_remote(self, path: str, text: str) -> str | None:
        """A remote is a URL, an scp-style `host:path`, or a path on this machine. Only a
        path is resolved. A user name is normal for ssh and scp-style (git@host) and a
        credential anywhere else, so it is refused (never echoed): git's own login
        supplies credentials."""
        if _URL.match(text):
            try:
                parts = urlsplit(text)
                username, password = parts.username, parts.password
            except ValueError:
                self.add(path, "the remote is not a valid URL")
                return None
            if password is not None or (username is not None and parts.scheme.lower() not in _SSH_SCHEMES):
                self.add(path, "the remote has a user or password in its URL; only ssh URLs may name a user "
                               "(git@host), and never a password. Let git's own login supply credentials")
                return None
            return text
        if _SCP_PASSWORD.match(text):
            self.add(path, "the remote has a password in it; let git's own login supply credentials")
            return None
        if _SCP_LIKE.match(text) or os.path.isabs(text):
            return text
        return os.path.normpath(os.path.join(self.base_dir, text))

    def primary_branch(self, table: Mapping[str, Any], base: str, branches: tuple[str, ...] | None) -> str | None:
        explicit = self.optional_text(table, base, "primary_branch")
        if explicit is not None:
            path = _join(base, "primary_branch")
            if _GLOB.search(explicit):
                self.add(path, "must be a branch name, not a pattern")
                return None
            if not any(explicit == b or fnmatch.fnmatchcase(explicit, b) for b in branches or ("main",)):
                self.add(path, "must equal a branch in branches, or match one of its patterns")
                return None
            return explicit
        return next((b for b in branches or () if not _GLOB.search(b)), "main")

    def section(self, table: Mapping[str, Any], base: str, key: str) -> Mapping[str, Any] | None:
        """A repo sub-table: `kind` (checked against the registry) plus the adapter's options."""
        if key not in table:
            return None
        path = _join(base, key)
        sub = self.table(path, table[key])
        if sub is None:
            return None
        self.kind_of(sub, path, key)
        return _freeze(sub)

    # ---- edges

    def edges(self, raw: Any, repo_names: set[str]) -> list[Edge] | None:
        if raw is None:
            return []
        if not isinstance(raw, list):
            self.add("edges", f"expected a list of [[edges]] tables, got {_type_name(raw)}")
            return None
        edges: list[Edge | None] = []
        seen: dict[tuple[Any, ...], int] = {}
        for i, item in enumerate(raw):
            edge = self.edge(i, item, repo_names)
            edges.append(edge)
            if edge is not None:
                key = (edge.kind, edge.from_repo, edge.to_repo, edge.manifest)
                if key in seen:
                    self.add(_join("edges", i), f"duplicates edges[{seen[key]}]")
                    edges[-1] = None
                else:
                    seen[key] = i
        return None if any(e is None for e in edges) else edges  # type: ignore[return-value]

    def edge(self, index: int, raw: Any, repo_names: set[str]) -> Edge | None:
        base = _join("edges", index)
        table = self.table(base, raw)
        if table is None:
            return None
        before = len(self.problems)
        kind = self.choice(table, base, "kind", EDGE_KINDS, "edge kind")
        if kind is None:
            return None
        slots = self.endpoint_slots(table, base, kind)
        if slots is None:
            return None
        extra_keys = _EDGE_OPTION_KEYS[kind]
        package_keys = _PACKAGE_KEYS if kind == "package" else ()
        other_pair = [k for k in (self._FROM_TO + self._PRODUCER_CONSUMER) if k in table and k not in slots]
        known = ("kind", *slots, *package_keys, *extra_keys)
        self.unknown_keys({k: v for k, v in table.items() if k not in other_pair}, base, known)
        for key in other_pair:  # report the wrong spelling once, not also the right keys as missing
            self.add(_join(base, key), f"{kind} edges take {slots[0]} and {slots[1]}, not {key}")
        ends: list[str] = []
        for slot in slots:
            repo = self.text(table, base, slot, required=not other_pair)
            if repo is not None:
                path = _join(base, slot)
                if repo not in repo_names:
                    known_repos = ", ".join(sorted(repo_names)) or "none"
                    self.add(path, f"names a repo that is not in [repos]; known repos: {known_repos}")
                else:
                    ends.append(repo)
        if len(ends) == 2 and ends[0] == ends[1] and kind != "library":
            self.add(_join(base, slots[1]), f"a {kind} edge cannot connect a repo to itself; only a library "
                                            "edge can (a library inside a monorepo)")
        manifest = pin_pattern = import_marker = None
        if kind == "package":
            manifest = self.text(table, base, "manifest", required=True)
            text = self.text(table, base, "pin_pattern", required=True)
            if text is not None:
                pin_pattern = self.pattern(_join(base, "pin_pattern"), text, groups=1, what="the version")
            import_marker = self.text(table, base, "import_marker", required=True)
        for key in extra_keys:
            self.text(table, base, key)
        if len(self.problems) != before or len(ends) != 2:
            return None
        options = _freeze({k: table[k] for k in extra_keys if k in table})
        return Edge(kind, ends[0], ends[1], manifest, pin_pattern, import_marker, options)

    _FROM_TO = ("from", "to")
    _PRODUCER_CONSUMER = ("producer", "consumer")

    def endpoint_slots(self, table: Mapping[str, Any], base: str, kind: str) -> tuple[str, str] | None:
        """Which two keys name the repos: producer/consumer for package, from/to for the
        others; a contract edge takes either spelling, not both."""
        if kind == "contract":
            has_from_to = any(k in table for k in self._FROM_TO)
            has_pc = any(k in table for k in self._PRODUCER_CONSUMER)
            if has_from_to and has_pc:
                self.add(base, "a contract edge takes from and to, or producer and consumer, not both")
                return None
            return self._PRODUCER_CONSUMER if has_pc else self._FROM_TO
        return self._PRODUCER_CONSUMER if kind == "package" else self._FROM_TO

    # ---- sources

    def test_sources(self, raw: Any, repos: Mapping[str, Repo] | None = None) -> list[TestSource] | None:
        items = self.source_list("test_sources", raw)
        if items is None:
            return None
        sources = []
        for i, table in enumerate(items):
            base = _join("test_sources", i)
            before = len(self.problems)
            kind = self.kind_of(table, base, "test_sources")
            areas = self.areas(table, base)
            options = _freeze({k: v for k, v in table.items() if k not in ("kind", "areas")})
            if kind in config_sources.SPEC_KINDS:
                self.spec_repos(table, base, repos or {})
            if len(self.problems) == before and kind is not None:
                sources.append(TestSource(kind, options, areas, self.unset_under(base)))
        return sources if len(sources) == len(items) else None

    def spec_repos(self, table: Mapping[str, Any], base: str, repos: Mapping[str, Repo]) -> None:
        """The repositories a spec source names exist, and the one its screens are read from has an entry-point kind that makes screens
        (a source that can only ever match nothing is a mistake, not an absence)."""
        for key in ("repo", "screens_repo"):  # a name the run would look up: one that is not configured is a mistake, not an absence
            if isinstance(table.get(key), str) and table[key] not in repos:
                self.add(_join(base, key), "names a repository that is not configured under [repos]")
        screens_key = "screens_repo" if isinstance(table.get("screens_repo"), str) else "repo"
        screens = repos.get(table.get(screens_key)) if isinstance(table.get(screens_key), str) else None
        if screens is not None and (screens.entry_points or {}).get("kind") not in config_sources.SCREEN_ENTRY_KINDS:
            self.add(_join(base, screens_key), "names a repository whose entry_points do not make screens, so no spec can be matched to one; "
                     f"it needs entry_points of kind {', '.join(config_sources.SCREEN_ENTRY_KINDS)}")

    def areas(self, table: Mapping[str, Any], base: str) -> tuple[Mapping[str, Any], ...]:
        if "areas" not in table:
            return ()
        path = _join(base, "areas")
        if not isinstance(table["areas"], list):
            self.add(path, f"expected a list of [[...areas]] tables, got {_type_name(table['areas'])}")
            return ()
        areas = []
        for i, item in enumerate(table["areas"]):
            area_path = _join(path, i)
            area = self.table(area_path, item)
            if area is None:
                continue
            unset_name = "name" in area and area["name"] is None  # "not configured", kept
            if unset_name or self.text(area, area_path, "name", required=True) is not None:
                areas.append(_freeze(area))
        return tuple(areas)

    def context(self, raw: Any) -> list[ContextSource] | None:
        items = self.source_list("context", raw)
        if items is None:
            return None
        sources = []
        for i, table in enumerate(items):
            base = _join("context", i)
            before = len(self.problems)
            kind = self.kind_of(table, base, "context")
            options = {k: v for k, v in table.items() if k != "kind"}
            self.resolve_machine_paths("context", kind, options)
            frozen = _freeze(options)
            if len(self.problems) == before and kind is not None:
                sources.append(ContextSource(kind, frozen, self.unset_under(base)))
        return sources if len(sources) == len(items) else None

    def resolve_machine_paths(self, section: str, kind: str | None, options: dict[str, Any]) -> None:
        """Resolve the option keys the kind declares as machine paths; never touch a URL."""
        spec = _SECTIONS[section].kinds.get(kind or "")
        for key in spec.machine_paths if spec else ():
            value = options.get(key)
            if isinstance(value, str) and not _URL.match(value) and not os.path.isabs(value):
                options[key] = os.path.normpath(os.path.join(self.base_dir, value))

    def source_list(self, key: str, raw: Any) -> list[dict[str, Any]] | None:
        if raw is None:
            return []
        if not isinstance(raw, list):
            self.add(key, f"expected a list of [[{key}]] tables, got {_type_name(raw)}")
            return None
        items = [self.table(_join(key, i), item) for i, item in enumerate(raw)]
        return None if any(t is None for t in items) else items  # type: ignore[return-value]


import reachmap.adapters.openapi  # noqa: E402,F401  (the adapter registers its kinds with `register_kind`)
