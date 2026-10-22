"""Helpers that read the fixture repositories and parse expected.json.

Used by tests/test_fixtures.py. Nothing here is a test. Parsers are deliberately small:
they understand the shapes the fixtures use (top-level TypeScript functions and arrow
constants, top-level Python defs with column-zero decorators, FastAPI include_router
mounts), not the languages.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from tests.fixtures import build
from tests.fixtures.build import file_at, run_git

# Python package name to source folder, for repos whose import name differs.
PY_PACKAGE_DIRS = {"acme_contracts": "contracts"}
MODEL_FILES = ["common", "customer", "invoice", "schedule", "settlement"]


_TEXTS: dict[tuple[str, str, str], str | None] = {}


def text_at(repo: Path, rev: str, path: str) -> str | None:
    """File text at a revision; None when the file is absent there.

    An unreadable file (any git failure other than absence) raises: a failed read is
    never turned into an absence. The answer is kept per (repository, revision, path): the fixture
    refs do not move, and the oracles read the same files again and again.
    """
    key = (str(repo), rev, path)
    if key not in _TEXTS:
        status, text = file_at(repo, rev, path)
        if status == "unreadable":
            raise AssertionError(f"cannot read {path} at {rev} in {repo}: {text}")
        _TEXTS[key] = text
    return _TEXTS[key]


def git_out(repo: Path, *args: str) -> str:
    proc = run_git(repo, *args)
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed in {repo}: {proc.stderr}")
    return proc.stdout


def ref_exists(repo: Path, ref: str) -> bool:
    return run_git(repo, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}").returncode == 0


def ref_shas(repo: Path) -> str:
    return git_out(repo, "for-each-ref", "--format=%(refname) %(objectname)")


class Tree:
    """File listings of a repository at revisions, cached."""

    def __init__(self, repos: dict[str, Path]) -> None:
        self.repos = repos
        self._files: dict[tuple[str, str], set[str]] = {}

    def files(self, repo: str, rev: str) -> set[str]:
        key = (repo, rev)
        if key not in self._files:
            out = git_out(self.repos[repo], "ls-tree", "-r", "--name-only", rev)
            self._files[key] = set(out.splitlines())
        return self._files[key]


# ------------------------------------------------------------------- import parsing

TS_SPEC = re.compile(r"""(?:\bfrom\s+|\bimport\s+|\bimport\(\s*)["']([^"']+)["']""")
PY_FROM = re.compile(r"^\s*from\s+([\w.]+)\s+import\b")
PY_IMPORT = re.compile(r"^\s*import\s+([\w.]+)")


def ts_candidates(base: str) -> list[str]:
    return [base + ext for ext in (".ts", ".tsx", "/index.ts", "/index.tsx")] + [base]


def resolve_ts(tree: Tree, repo: str, rev: str, importer: str, spec: str) -> str | None:
    """Resolve a TypeScript specifier from tsconfig paths or a relative path."""
    files = tree.files(repo, rev)
    if spec.startswith("."):
        parts: list[str] = []
        for seg in (str(Path(importer).parent) + "/" + spec).split("/"):
            if seg in ("", "."):
                continue
            if seg == "..":
                parts.pop()
            else:
                parts.append(seg)
        bases = ["/".join(parts)]
    else:
        text = text_at(tree.repos[repo], rev, "tsconfig.json")
        paths = json.loads(text)["compilerOptions"]["paths"] if text else {}
        bases = []
        # Every alias that matches, longest prefix first: resolution asks which file a
        # specifier means.
        for pattern, targets in sorted(paths.items(), key=lambda kv: -len(kv[0])):
            prefix = pattern.rstrip("*")
            if spec.startswith(prefix):
                rest = spec[len(prefix):]
                bases += [t.rstrip("*") + rest for t in targets]
    for base in bases:
        for cand in ts_candidates(base):
            if cand in files:
                return cand
    return None


def ts_importers(tree: Tree, repos: dict[str, Path], repo: str, target: str, rev: str = "main") -> set[str]:
    """Every file at rev whose import specifiers resolve to target."""
    stem = target.rsplit(".", 1)[0].split("/", 1)[-1]  # e.g. lib/format
    importers = set()
    grep = run_git(repos[repo], "grep", "-l", stem.split("/")[-1], rev, "--", "src")  # rc 1: no hit
    for hit in grep.stdout.splitlines():
        path = hit.split(":", 1)[1]
        for spec in TS_SPEC.findall(text_at(repos[repo], rev, path)):
            if resolve_ts(tree, repo, rev, path, spec) == target:
                importers.add(path)
    return importers


def resolve_py(tree: Tree, repo: str, rev: str, importer: str, module: str) -> tuple[str, str] | None:
    """Resolve an absolute Python module to (repo, path).

    Search roots: the importer's service folder (nearest pyproject.toml), libs/*, the
    repo root. A package named in PY_PACKAGE_DIRS lives in the repo of that name.
    """
    head, _, rest = module.partition(".")
    if head in PY_PACKAGE_DIRS:
        target_repo = "acme/contracts"
        rel = "/".join([PY_PACKAGE_DIRS[head]] + ([rest.replace(".", "/")] if rest else []))
        files = tree.files(target_repo, "main")
        for cand in (rel + ".py", rel + "/__init__.py"):
            if cand in files:
                return target_repo, cand
        return None
    files = tree.files(repo, rev)
    roots = [""]
    parent = Path(importer).parent
    while str(parent) != ".":
        if f"{parent}/pyproject.toml" in files:
            roots.insert(0, str(parent) + "/")
            break
        parent = parent.parent
    roots += [f"{p.rsplit('/', 1)[0]}/" for p in files if p.startswith("libs/") and p.endswith("/pyproject.toml")]
    rel = module.replace(".", "/")
    for root in roots:
        for cand in (f"{root}{rel}.py", f"{root}{rel}/__init__.py"):
            if cand in files:
                return repo, cand
    return None


def import_target(tree: Tree, repo: str, rev: str, importer: str, line: str):
    """(repo, path) a single import line points at; (repo, None) if it does not resolve;
    None when the line is not an import."""
    if importer.endswith((".ts", ".tsx")):
        m = TS_SPEC.search(line)
        stripped = line.lstrip()
        if not m or not (stripped.startswith(("import ", "} from")) or "import(" in stripped):
            return None
        return (repo, resolve_ts(tree, repo, rev, importer, m.group(1)))
    if importer.endswith(".py"):
        m = PY_FROM.match(line) or PY_IMPORT.match(line)
        if not m:
            return None
        found = resolve_py(tree, repo, rev, importer, m.group(1))
        return found if found else (repo, None)
    return None


def is_comment_line(line: str, col: int) -> bool:
    """True when the text at column col of the line sits in a comment."""
    stripped = line.lstrip()
    if stripped.startswith(("//", "#", "/*", "*")):
        return True
    before = line[:col]
    return "//" in before or re.search(r"(^|\s)#", before) is not None


def occurrences(text: str, needle: str) -> list[bool]:
    """For each occurrence of needle: is it in a comment?"""
    out = []
    start = 0
    while (idx := text.find(needle, start)) != -1:
        line_start = text.rfind("\n", 0, idx) + 1
        line_end = text.find("\n", idx)
        line = text[line_start : line_end if line_end != -1 else len(text)]
        out.append(is_comment_line(line, idx - line_start))
        start = idx + 1
    return out


def occurrences_of_word(text: str, word: str) -> list[bool]:
    """For each whole-word occurrence of word: is it in a comment?"""
    out = []
    for m in re.finditer(rf"\b{re.escape(word)}\b", text):
        line_start = text.rfind("\n", 0, m.start()) + 1
        line_end = text.find("\n", m.start())
        line = text[line_start : line_end if line_end != -1 else len(text)]
        out.append(is_comment_line(line, m.start() - line_start))
    return out


def collect(node, repo, locators, comments, entries, parent=None) -> None:
    """Walk an expected.json fragment: every {path, contains} and every comment.

    locators and comments get (repo, item, parent) triples. repo is the nearest enclosing
    entry that names one; parent is the nearest enclosing dict that names a branch (a
    consumer entry names the branch its evidence is read at), the argument
    build.evidence_revision takes to resolve a revision.
    """
    if isinstance(node, dict):
        repo = node.get("repo", repo)
        if "type" in node and "path" in node and "contains" not in node:
            entries.append((repo, node))
        if "path" in node and "contains" in node:
            locators.append((repo, node, parent))
        child_parent = node if "branch" in node else parent
        for key, value in node.items():
            if key == "comment":
                comments.append((repo, value, child_parent))
            else:
                collect(value, repo, locators, comments, entries, child_parent)
    elif isinstance(node, list):
        for item in node:
            collect(item, repo, locators, comments, entries, parent)


def is_test_path(path: str, markers: list[str]) -> bool:
    """Test path markers, matched by path segment or file-name part, never as a substring.

    "tests/" matches a folder segment named tests; ".test." matches a dot-delimited part
    of the file name; "test_" matches the start of the file name.
    """
    parts = path.split("/")
    name = parts[-1]
    for marker in markers:
        if marker.endswith("/"):
            if marker[:-1] in parts[:-1]:
                return True
        elif marker.startswith(".") and marker.endswith("."):
            if marker in name:
                return True
        elif name.startswith(marker):
            return True
    return False


# ------------------------------------------------------------------ diffs and symbols

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def changed_lines(repo_path: Path, base: str, head: str) -> dict[str, dict]:
    """Per file: added (head line numbers), removed (base line numbers) and points
    (head positions of pure deletions: the deletion sits between line n and n + 1)."""
    out = git_out(repo_path, "diff", "-U0", "--no-renames", f"{base}...{head}")
    files: dict[str, dict] = {}
    entry = None
    for line in out.splitlines():
        if line.startswith("+++ "):
            path = line[4:]
            if path.startswith("b/"):
                entry = files.setdefault(path[2:], {"added": set(), "removed": set(), "points": set()})
            else:  # +++ /dev/null: the file was deleted, find its name from the --- line
                entry = files.setdefault(prev_path, {"added": set(), "removed": set(), "points": set()})
        elif line.startswith("--- "):
            prev_path = line[6:] if line.startswith("--- a/") else ""
        elif (m := HUNK.match(line)) and entry is not None:
            a, b, c, d = int(m[1]), int(m[2] or 1) if m[2] != "0" else 0, int(m[3]), int(m[4] or 1) if m[4] != "0" else 0
            entry["removed"].update(range(a, a + b))
            entry["added"].update(range(c, c + d))
            if d == 0:
                entry["points"].add(c)
    return files


_STRING = re.compile(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`")


def _depth_delta(line: str) -> int:
    code = re.sub(r"//.*$", "", _STRING.sub('""', line))
    return sum(code.count(c) for c in "{([") - sum(code.count(c) for c in "})]")


def ts_symbols(text: str) -> dict[str, tuple[int, int, str]]:
    """Top-level functions and arrow constants of a TypeScript file: name to
    (first line, last line, kind), 1-based. Spans come from bracket counting."""
    lines = text.split("\n")
    symbols: dict[str, tuple[int, int, str]] = {}
    depth, i = 0, 0
    while i < len(lines):
        line = lines[i]
        m = None
        if depth == 0:
            m = re.match(r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)", line) or re.match(
                r"^(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?\(", line
            )
        if m:
            j, d = i, 0
            while True:
                d += _depth_delta(lines[j])
                if d <= 0 or j + 1 >= len(lines):
                    break
                j += 1
            symbols[m.group(1)] = (i + 1, j + 1, "def")
            i = j + 1
            continue
        depth += _depth_delta(line)
        i += 1
    return symbols


def py_symbols(text: str) -> dict[str, tuple[int, int, str]]:
    """Top-level defs and classes of a Python file: name to (first line, last line, kind).

    The first line is the first decorator line when there are decorators (they sit at
    column 0 and may span lines). A span ends before the next column-0 statement; a
    column-0 line starting with a closing bracket continues a multi-line header.
    """
    lines = text.split("\n")
    symbols: dict[str, tuple[int, int, str]] = {}
    decorator_start = None
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("@") and decorator_start is None:
            decorator_start = i
        m = re.match(r"^(?:async\s+)?(def|class)\s+(\w+)", line)
        if m:
            start = decorator_start if decorator_start is not None else i
            decorator_start = None
            last, j = i, i + 1
            while j < len(lines):
                if lines[j].strip():
                    if lines[j][0] not in " \t)]}":
                        break
                    last = j
                j += 1
            symbols[m.group(2)] = (start + 1, last + 1, "class" if m.group(1) == "class" else "def")
            i = j
            continue
        i += 1
    return symbols


def symbols_of(path: str, text: str) -> dict[str, tuple[int, int, str]]:
    if path.endswith(".py"):
        return py_symbols(text)
    if path.endswith((".ts", ".tsx")):
        return ts_symbols(text)
    return {}


def symbol_span(path: str, text: str, symbol: str, nested: str | None = None) -> tuple[int, int] | None:
    """(first, last) line of a symbol; a dotted symbol is a field of a class; nested
    narrows the span to a nested function defined inside it. None when not found."""
    lines = text.split("\n")
    if "." in symbol:
        cls, field = symbol.split(".", 1)
        found = symbols_of(path, text).get(cls)
        if not found:
            return None
        for n in range(found[0], found[1] + 1):
            if re.match(rf"^\s+{re.escape(field)}\s*:", lines[n - 1]):
                return (n, n)
        return None
    found = symbols_of(path, text).get(symbol)
    if not found:
        return None
    span = (found[0], found[1])
    if nested:
        for n in range(span[0], span[1] + 1):
            m = re.match(rf"^(\s+)def {re.escape(nested)}\(", lines[n - 1])
            if m:
                indent, end = len(m.group(1)), n
                for k in range(n + 1, span[1] + 1):
                    if lines[k - 1].strip() and len(lines[k - 1]) - len(lines[k - 1].lstrip()) <= indent:
                        break
                    end = k
                return (n, end)
        return None
    return span


def touches(span: tuple[int, int], added: set[int], points: set[int]) -> bool:
    lo, hi = span
    return any(lo <= n <= hi for n in added) or any(lo <= p and p + 1 <= hi for p in points)


def symbol_changed(path: str, symbol: str, nested: str | None, head: str | None, base: str | None,
                   diff: dict, removed: bool = False) -> bool:
    """Whether the diff changes the symbol: an added or deleted line inside its span at the
    head, or a removed line inside its span at the base (a removed symbol is read at the base)."""
    d = diff.get(path)
    if d is None:
        return False
    if not removed and head is not None:
        span = symbol_span(path, head, symbol, nested)
        if span and touches(span, d["added"], d["points"]):
            return True
    if base is not None:
        span = symbol_span(path, base, symbol, nested)
        if span and any(span[0] <= n <= span[1] for n in d["removed"]):
            return True
    return False


def functions_containing_changes(path: str, head: str | None, base: str | None, diff_entry: dict) -> set[str]:
    """Names of top-level functions that contain a changed line (class bodies and
    module-level lines do not count)."""
    names = set()
    for text, lines, points in ((head, diff_entry["added"], diff_entry["points"]), (base, diff_entry["removed"], set())):
        if text is None:
            continue
        for name, (lo, hi, kind) in symbols_of(path, text).items():
            if kind == "def" and touches((lo, hi), lines, points):
                names.add(name)
    return names


# --------------------------------------------------------------------- the FastAPI routes


def api_routes(tree: Tree, repos: dict[str, Path], repo: str, rev: str) -> tuple[list[dict], list[tuple[str, str]]]:
    """Derive every route of the api repo at a revision, and the unmounted routers.

    A route is a decorator @<router>.<method>("<path>") on a def; its full path is the
    decorator path preceded by the prefixes of the include_router calls from the app
    down to its router. A route has chain: the decorator line, then the include_router
    lines from the router outwards. Routers no app reaches are returned as unmounted
    (file, variable) pairs.
    """
    kinds: dict[tuple[str, str], str] = {}
    decorators, parents = [], {}
    for path in sorted(tree.files(repo, rev)):
        if not path.endswith(".py") or not path.startswith("services/") or "/tests/" in path:
            continue
        lines = text_at(repos[repo], rev, path).split("\n")
        imported: dict[str, tuple[str, str]] = {}
        for line in lines:
            m = re.match(r"^from ([\w.]+) import (\w+)(?: as (\w+))?\s*$", line)
            if m:
                target = resolve_py(tree, repo, rev, path, m.group(1))
                if target:
                    imported[m.group(3) or m.group(2)] = (target[1], m.group(2))
            m = re.match(r"^(\w+) = (APIRouter|FastAPI)\(", line)
            if m:
                kinds[(path, m.group(1))] = m.group(2)
        for idx, line in enumerate(lines):
            m = re.match(r'^@(\w+)\.(get|post|put|delete|patch)\("([^"]*)"\)', line)
            if m:
                func = next(re.match(r"^(?:async )?def (\w+)", l).group(1)
                            for l in lines[idx + 1:] if re.match(r"^(?:async )?def (\w+)", l))
                decorators.append((path, m.group(1), m.group(2).upper(), m.group(3), func, line))
            m = re.match(r'^(\w+)\.include_router\((\w+), prefix="([^"]*)"\)', line)
            if m:
                child = imported.get(m.group(2), (path, m.group(2)))
                parents.setdefault(child, []).append(((path, m.group(1)), m.group(3), line))

    def mounts(ref):
        if kinds.get(ref) == "FastAPI":
            return [("", [])]
        found = []
        for parent, prefix, line in parents.get(ref, []):
            for pre, chain in mounts(parent):
                found.append((pre + prefix, chain + [line]))
        return found

    routes, mounted = [], set()
    for path, var, method, route_path, func, line in decorators:
        for prefix, chain in mounts((path, var)):
            mounted.add((path, var))
            routes.append({"method": method, "path": prefix + route_path, "file": path, "func": func,
                           "chain": [line] + list(reversed(chain))})
    declared = {(p, v) for (p, v), kind in kinds.items() if kind == "APIRouter"}
    unmounted = sorted(declared - {ref for ref in declared if mounts(ref)})
    return routes, unmounted


# ---------------------------------------------------------------- the web client's URLs


def client_urls(endpoints_text: str) -> dict[str, str]:
    """Endpoint constants and builders in the endpoints file: name to URL, with ${...} as
    {} (a parameter segment). A constant written over two lines is read."""
    urls = {}
    for m in re.finditer(r'export const (\w+)\s*=\s*"([^"]*)";', endpoints_text):
        urls[m.group(1)] = m.group(2)
    for m in re.finditer(r"export const (\w+)\s*=\s*\([^)]*\)\s*=>\s*`([^`]*)`;", endpoints_text):
        urls[m.group(1)] = re.sub(r"\$\{[^}]*\}", "{}", m.group(2))
    return urls


def url_used_on_line(line: str, urls: dict[str, str]) -> str | None:
    """The URL a client line passes to apiGet, through a constant or a builder call."""
    m = re.search(r"apiGet(?:<[^()]*>)?\((\w+)", line)
    return urls.get(m.group(1)) if m else None


def match_kind(client_url: str, route_path: str) -> str | None:
    """How a client URL fits a route path: exact, literal_in_param (a literal client
    segment against a route parameter) or None. A client parameter segment matches only
    a route parameter."""
    client, route = client_url.strip("/").split("/"), route_path.strip("/").split("/")
    if len(client) != len(route):
        return None
    kind = "exact"
    for c, r in zip(client, route):
        r_param = r.startswith("{") and r.endswith("}")
        if c == "{}":
            if not r_param:
                return None
        elif c != r:
            if not r_param:
                return None
            kind = "literal_in_param"
    return kind


def best_routes(client_url: str, routes: list[dict], method: str = "GET") -> list[dict]:
    """The most specific routes a client URL matches: exact matches if any, else the
    literal_in_param ones."""
    matches = [(match_kind(client_url, r["path"]), r) for r in routes if r["method"] == method]
    matches = [(k, r) for k, r in matches if k]
    for kind in ("exact", "literal_in_param"):
        found = [r for k, r in matches if k == kind]
        if found:
            return found
    return []


# ---------------------------------------------------------------------- importers


def direct_importers(tree: Tree, repos: dict[str, Path], repo: str, rev: str, target: str) -> set[str]:
    """Every file at rev with an import that resolves to target (all specifiers)."""
    path_repo = repos[repo]
    found = set()
    if target.endswith((".ts", ".tsx")):
        stem = target.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        hits = run_git(path_repo, "grep", "-l", stem, rev, "--", "src").stdout.splitlines()
        for hit in hits:
            path = hit.split(":", 1)[1]
            if any(resolve_ts(tree, repo, rev, path, spec) == target for spec in TS_SPEC.findall(text_at(path_repo, rev, path))):
                found.add(path)
        return found
    for path in sorted(tree.files(repo, rev)):
        if not path.endswith(".py"):
            continue
        for line in text_at(path_repo, rev, path).split("\n"):
            m = PY_FROM.match(line) or PY_IMPORT.match(line)
            if m and resolve_py(tree, repo, rev, path, m.group(1)) == (repo, target):
                found.add(path)
    return found


def route_table_of_pages(tree: Tree, repos: dict[str, Path], repo: str, rev: str) -> dict[str, set[str]]:
    """Page file to the route paths that mount it, from the modules' routes.tsx files:
    <Route path="P" element={<Name />} /> where Name is a default import."""
    table: dict[str, set[str]] = {}
    for path in sorted(tree.files(repo, rev)):
        if not path.endswith("/routes.tsx"):
            continue
        text = text_at(repos[repo], rev, path)
        names = {}
        for m in re.finditer(r'^import (\w+) from "([^"]+)";', text, re.M):
            target = resolve_ts(tree, repo, rev, path, m.group(2))
            if target:
                names[m.group(1)] = target
        for m in re.finditer(r'<Route path="([^"]+)" element=\{<(\w+) />\} />', text):
            if m.group(2) in names:
                table.setdefault(names[m.group(2)], set()).add(m.group(1))
    return table
