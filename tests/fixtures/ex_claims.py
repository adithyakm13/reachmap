"""An independent reading of the Express and NestJS fixtures: the route tables, the routers nothing mounts, and who reaches what, from
the text of the files, written apart from the adapter (it does not import `reachmap.adapters`) so that `test_ex_fixtures.py` can
check the hand-written expected.json, and `test_ex_scenarios.py` the adapter, against something that is not the code under test.

It understands the shapes these fixtures use (one statement per line or the `router\\n  .route(...)\\n  .get(...)` chain, relative
imports and `require`, `module.exports = router`, `@Controller` and `@Get` lines, `@Module({ controllers: [...] })` lists), not
Express or NestJS. Every line it reports is a line of code (a comment line is skipped).
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path

from tests.fixtures.claims import git_out, text_at

EXTENSIONS = (".ts", ".tsx", ".js", "/index.ts", "/index.tsx", "/index.js")
METHODS = ("get", "post", "put", "delete", "patch")


def tree(repo: Path, rev: str) -> list[str]:
    return git_out(repo, "ls-tree", "-r", "--name-only", rev).splitlines()


def source_files(files: list[str]) -> list[str]:
    return sorted(f for f in files if f.startswith("src/") and f.endswith((".ts", ".tsx", ".js")) and "__tests__" not in f and not f.endswith(".d.ts"))


def resolve(files: set[str], importer: str, spec: str) -> str | None:
    """A relative specifier to the file it means (the fixtures use no alias in a server)."""
    if not spec.startswith("."):
        return None
    base = posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec))
    for ext in EXTENSIONS:
        if base + ext in files:
            return base + ext
    return base if base in files else None


def code_lines(text: str) -> list[tuple[int, str]]:
    """(number, text) of every line that is not blank and not a comment line."""
    return [(n, line) for n, line in enumerate(text.split("\n"), 1) if line.strip() and not line.strip().startswith("//")]


# ------------------------------------------------------------------------------------------------------------------------ Express


class _File:
    def __init__(self, repo: Path, rev: str, files: set[str], path: str) -> None:
        self.path = path
        self.files = files
        self.lines = code_lines(text_at(repo, rev, path) or "")
        self.imports: dict[str, tuple[str, str]] = {}  # local name to (file, imported name)
        self.consts: dict[str, str] = {}
        self.recvs: dict[str, tuple[str, int]] = {}  # variable to (app or router, line)
        self.exports: dict[str, str] = {}  # exported name to the local variable
        for n, line in self.lines:
            m = re.match(r'^import (\w+) from "([^"]+)";', line)
            if m and self._file(m.group(2)):
                self.imports[m.group(1)] = (self._file(m.group(2)), "default")
            m = re.match(r'^import \* as (\w+) from "([^"]+)";', line)
            if m and self._file(m.group(2)):
                self.imports[m.group(1)] = (self._file(m.group(2)), "*")
            m = re.match(r'^import \{ ([^}]+) \} from "([^"]+)";', line)
            if m and self._file(m.group(2)):
                for part in m.group(1).split(","):
                    name, _, alias = part.strip().partition(" as ")
                    self.imports[alias or name] = (self._file(m.group(2)), name)
            m = re.match(r'^const (\w+) = require\("([^"]+)"\);', line)
            if m and self._file(m.group(2)):
                self.imports[m.group(1)] = (self._file(m.group(2)), "*cjs")
            m = re.match(r'^const \{ ([^}]+) \} = require\("([^"]+)"\);', line)
            if m and self._file(m.group(2)):
                for part in m.group(1).split(","):
                    self.imports[part.strip()] = (self._file(m.group(2)), part.strip())
            m = re.match(r'^(?:export )?const (\w+) = "([^"]*)";', line)
            if m:
                self.consts[m.group(1)] = m.group(2)
            m = re.match(r"^(export )?const (\w+) = (express\(\)|express\.Router\(|Router\()", line)
            if m:
                self.recvs[m.group(2)] = ("app" if m.group(3) == "express()" else "router", n)
                if m.group(1):
                    self.exports[m.group(2)] = m.group(2)
            m = re.match(r"^export default (\w+);", line)
            if m:
                self.exports["default"] = m.group(1)
            m = re.match(r"^module\.exports = (\w+);", line)
            if m:
                self.exports["*cjs"] = m.group(1)
                self.exports["default"] = m.group(1)

    def _file(self, spec: str) -> str | None:
        return resolve(self.files, self.path, spec)

    def text_of(self, n: int) -> str:
        return next(line for k, line in self.lines if k == n).strip()


def _path_text(world: "ExpressWorld", f: _File, expr: str) -> str:
    """A path expression of the fixtures: string literals, constants (this file's or an imported one), a template over a constant, a call
    (kept as the unknown part), joined by +."""
    expr = expr.strip()
    out = ""
    for part in re.split(r"\s\+\s", expr):
        part = part.strip()
        m = re.fullmatch(r'"([^"]*)"', part)
        if m:
            out += m.group(1)
            continue
        m = re.fullmatch(r"`([^`]*)`", part)
        if m:
            out += re.sub(r"\$\{(\w+)\}", lambda k: world.const(f, k.group(1)), m.group(1))
            continue
        if re.fullmatch(r"\w+\(\)", part):
            out += f"/<unknown:{part}>"  # a path that is a call stands for a segment
            continue
        out += world.const(f, part)
    return out


def _convert(path: str) -> str:
    segs = [s for s in path.split("/") if s]
    return "/" + "/".join("{" + s[1:] + "}" if s.startswith(":") else s for s in segs)


class ExpressWorld:
    """The routes of an Express repository at a revision."""

    def __init__(self, repo: Path, rev: str) -> None:
        files = set(tree(repo, rev))
        self.files = {p: _File(repo, rev, files, p) for p in source_files(sorted(files))}
        self.mounts: dict[tuple[str, str], list[tuple[tuple[str, str], str, tuple[str, int]]]] = {}  # child to (parent, prefix, the mount line)
        self.stmts: list[dict] = []
        for f in self.files.values():
            self._statements(f)

    def const(self, f: _File, name: str) -> str:
        if name in f.consts:
            return f.consts[name]
        if name in f.imports:
            target, imported = f.imports[name]
            return self.files[target].consts.get(imported, f"<unknown:{name}>")
        return f"<unknown:{name}>"

    def ref(self, f: _File, expr: str) -> tuple[str, str] | None:
        """The router or app an expression names: a local one, an imported one (default, named, namespace member, require)."""
        expr = expr.strip()
        m = re.fullmatch(r'require\("([^"]+)"\)', expr)
        if m:
            target = f._file(m.group(1))
            return self._exported(target, "*cjs") if target else None
        if expr in f.recvs:
            return (f.path, expr)
        if expr in f.imports:
            target, imported = f.imports[expr]
            return self._exported(target, imported) if imported != "*" else None
        m = re.fullmatch(r"(\w+)\.(\w+)", expr)
        if m and m.group(1) in f.imports and f.imports[m.group(1)][1] in ("*", "*cjs"):
            return self._exported(f.imports[m.group(1)][0], m.group(2))
        return None

    def _exported(self, target: str, name: str) -> tuple[str, str] | None:
        f = self.files[target]
        local = f.exports.get(name)
        if local is None and name == "default":
            local = f.exports.get("*cjs")
        return (target, local) if local in f.recvs else None

    def _statements(self, f: _File) -> None:
        lines = f.lines
        i = 0
        while i < len(lines):
            n, line = lines[i]
            m = re.match(r'^(\w+)\.(get|post|put|delete|patch)\(("[^"]*"|\w+\(\)|\w+),', line)
            if m:
                self.stmts.append({"file": f.path, "recv": m.group(1), "method": m.group(2).upper(), "path_expr": m.group(3), "lines": [n]})
            m = re.match(r"^(\w+)$", line)
            if m and i + 1 < len(lines) and re.match(r'^\s+\.route\("([^"]*)"\)$', lines[i + 1][1]):
                path_line = lines[i + 1]
                path_expr = re.match(r'^\s+\.route\(("[^"]*")\)$', path_line[1]).group(1)
                j = i + 2
                while j < len(lines):
                    mm = re.match(r"^\s+\.(get|post|put|delete|patch)\((\w+)\);?$", lines[j][1])
                    if not mm:
                        break
                    self.stmts.append({"file": f.path, "recv": m.group(1), "method": mm.group(1).upper(), "path_expr": path_expr, "lines": [lines[j][0], path_line[0]]})
                    j += 1
                i = j
                continue
            m = re.match(r"^(\w+)\.use\((.+), ([\w.]+|require\(\"[^\"]+\"\))\);$", line)
            if m:
                child = self.ref(f, m.group(3))
                parent = (f.path, m.group(1)) if m.group(1) in f.recvs else self.ref(f, m.group(1))
                if child is not None and parent is not None:
                    self.mounts.setdefault(child, []).append((parent, _path_text(self, f, m.group(2)), (f.path, n)))
            i += 1

    def _up(self, ref: tuple[str, str], seen: tuple = ()) -> list[tuple[str, list[tuple[str, int]], bool, tuple[str, str]]]:
        """Every way up from a router: (the prefixes joined, the mount lines from the router outwards, whether it ends at an app, where it ends)."""
        f = self.files[ref[0]]
        out = []
        for parent, prefix, line in self.mounts.get(ref, []):
            if parent in seen or parent == ref:
                continue
            for above, lines, app, top in self._up(parent, seen + (ref,)):
                out.append((above + prefix, [line] + lines, app, top))
        if not out:
            out.append(("", [], f.recvs[ref[1]][0] == "app", ref))
        return out

    def routes(self) -> list[dict]:
        """Every route that ends at an app: method, full path, the file and the evidence lines (the route's own line, the `.route(` line when it has one, the mounts)."""
        found = []
        for s in self.stmts:
            f = self.files[s["file"]]
            ref = self.ref(f, s["recv"]) if s["recv"] not in f.recvs else (f.path, s["recv"])
            if ref is None:
                continue
            for prefix, lines, app, top in self._up(ref):
                if not app:
                    continue
                path = _convert(prefix + _path_text(self, f, s["path_expr"]))
                evidence = [(s["file"], f.text_of(n)) for n in s["lines"]] + [(p, self.files[p].text_of(n)) for p, n in lines]
                found.append({"method": s["method"], "path": path, "file": s["file"], "evidence": evidence})
        return sorted(found, key=lambda r: (r["path"], r["method"]))

    def unmounted(self) -> list[tuple[str, str]]:
        """The routers with statements on them that no app reaches."""
        used = {self.ref(self.files[s["file"]], s["recv"]) if s["recv"] not in self.files[s["file"]].recvs else (s["file"], s["recv"]) for s in self.stmts}
        return sorted(r for r in used if r is not None and self.files[r[0]].recvs[r[1]][0] == "router" and not any(app for _p, _l, app, _t in self._up(r)))


def express_routes(repo: Path, rev: str) -> list[dict]:
    return ExpressWorld(repo, rev).routes()


def express_unmounted(repo: Path, rev: str) -> list[tuple[str, str]]:
    return ExpressWorld(repo, rev).unmounted()


def routes_through(routes: list[dict], path: str) -> list[dict]:
    """The routes a change to `path` reaches when nothing narrows it (a middleware of the app): those declared in the file or mounted by a line of it."""
    return [r for r in routes if r["file"] == path or any(p == path for p, _t in r["evidence"])]


# ------------------------------------------------------------------------------------------------------------------------ NestJS


class NestWorld:
    """The routes of a NestJS repository at a revision."""

    def __init__(self, repo: Path, rev: str) -> None:
        files = set(tree(repo, rev))
        self.texts = {p: text_at(repo, rev, p) or "" for p in source_files(sorted(files))}
        self.classes: dict[str, str] = {}  # class name to file
        for p, text in self.texts.items():
            for m in re.finditer(r"^export (?:abstract )?class (\w+)", text, re.M):
                self.classes[m.group(1)] = p
        self.prefix: list[tuple[str, str, int]] = []  # (prefix, file, line)
        self.roots: list[str] = []
        for p, text in self.texts.items():
            for n, line in code_lines(text):
                m = re.search(r'\.setGlobalPrefix\("([^"]*)"\)', line)
                if m:
                    self.prefix.append((m.group(1), p, n))
                m = re.search(r"NestFactory\.create\((\w+)\)", line)
                if m:
                    self.roots.append(m.group(1))
        self.uri_versioning = any(re.search(r"\.enableVersioning\((\)|[^)]*VersioningType\.URI)", t) for t in self.texts.values())  # a version is a path segment only then
        self.modules: dict[str, dict] = {}
        for p, text in self.texts.items():
            for m in re.finditer(r"@Module\(\{(.*?)\n\}\)\nexport class (\w+)", text, re.S):
                body = m.group(1)
                ctrl = re.search(r"controllers: \[([^\]]*)\]", body)
                imp = re.search(r"imports: \[([^\]]*)\]", body)
                line = text[: m.start(1) + body.index("controllers:")].count("\n") + 1 if ctrl else 0
                self.modules[m.group(2)] = {
                    "file": p, "controllers": [x.strip() for x in ctrl.group(1).split(",") if x.strip()] if ctrl else [],
                    "imports": [x.strip() for x in imp.group(1).split(",") if x.strip()] if imp else [], "controllers_line": line,
                }

    def reachable_modules(self) -> set[str]:
        todo = list(self.roots) or [m for m in self.modules if not any(m in o["imports"] for o in self.modules.values())]
        seen: set[str] = set()
        while todo:
            m = todo.pop()
            if m in seen or m not in self.modules:
                continue
            seen.add(m)
            todo += self.modules[m]["imports"]
        return seen

    def _const(self, file: str, name: str) -> str:
        for text in [self.texts[file]] + list(self.texts.values()):
            m = re.search(rf'export const {name} = "([^"]*)";', text)
            if m:
                return m.group(1)
        return f"<unknown:{name}>"

    def controllers(self) -> list[dict]:
        out = []
        for p, text in self.texts.items():
            if "from \"@nestjs/common\"" not in text:
                continue
            for m in re.finditer(r"^@Controller\(([^)]*)\)\nexport class (\w+)", text, re.M):
                arg = m.group(1).strip()
                version = False
                if arg.startswith("{"):
                    pm = re.search(r'path: "([^"]*)"', arg)
                    path = pm.group(1) if pm else ""
                    version = "version:" in arg
                elif arg.startswith('"'):
                    path = arg.strip('"')
                elif arg:
                    path = self._const(p, arg)
                else:
                    path = ""
                start = text[: m.start()].count("\n") + 1
                imported = re.search(r'import \{([^}]*)\} from "@nestjs/common";', text)
                names = {x.strip() for x in imported.group(1).split(",")} if imported else set()
                end = text.find("\n}\n", m.start())
                body = text[m.end(): end if end >= 0 else len(text)]
                methods = []
                for mm in re.finditer(r'^  @(Get|Post|Put|Patch|Delete)\(("[^"]*")?\)\n  (?:async )?(\w+)\(', body, re.M):
                    if mm.group(1) not in names:
                        continue
                    line = text[: m.end() + mm.start()].count("\n") + 1
                    methods.append({"verb": mm.group(1).upper(), "path": mm.group(2).strip('"') if mm.group(2) else "", "name": mm.group(3), "line": line})
                out.append({"file": p, "class": m.group(2), "path": path, "version": version, "decorator_line": start, "methods": methods})
        return out

    def routes(self) -> list[dict]:
        found = []
        reachable = self.reachable_modules()
        for c in self.controllers():
            regs = [(name, mod) for name, mod in self.modules.items() if c["class"] in mod["controllers"] and name in reachable]
            if not regs:
                continue
            for prefix in self.prefix or [("", "", 0)]:
                for name, mod in regs:
                    for m in c["methods"]:
                        segs = [s for s in [prefix[0], "<unknown:version>" if c["version"] and self.uri_versioning else "", c["path"], m["path"]] for s in s.split("/") if s]
                        path = "/" + "/".join("{" + s[1:] + "}" if s.startswith(":") else s for s in segs)
                        text = self.texts[c["file"]].split("\n")
                        evidence = [(c["file"], text[m["line"] - 1].strip())]
                        if c["path"]:
                            evidence.append((c["file"], text[c["decorator_line"] - 1].strip()))
                        evidence.append((mod["file"], self.texts[mod["file"]].split("\n")[mod["controllers_line"] - 1].strip()))
                        if prefix[0]:
                            evidence.append((prefix[1], self.texts[prefix[1]].split("\n")[prefix[2] - 1].strip()))
                        found.append({"method": m["verb"], "path": path, "file": c["file"], "evidence": evidence, "handler": m["name"], "class": c["class"]})
        return sorted(found, key=lambda r: (r["path"], r["method"]))

    def unmounted(self) -> list[tuple[str, str]]:
        """The controllers that have routes and that nothing serves: no module lists them, or only a module the application does not import."""
        served = {c["class"] for c in self.controllers() for name, mod in self.modules.items() if c["class"] in mod["controllers"] and name in self.reachable_modules()}
        return sorted((c["file"], c["class"]) for c in self.controllers() if c["methods"] and c["class"] not in served)


def nest_routes(repo: Path, rev: str) -> list[dict]:
    return NestWorld(repo, rev).routes()


def nest_unmounted(repo: Path, rev: str) -> list[tuple[str, str]]:
    return NestWorld(repo, rev).unmounted()


def definitions_of(repo: Path, rev: str, name: str) -> int:
    """How many times `name` is defined in non-test code: a function declaration, or a method of an object literal or a class (`name(`
    at the start of an indented line that opens a body)."""
    count = 0
    for p in source_files(tree(repo, rev)):
        for _n, line in code_lines(text_at(repo, rev, p) or ""):
            if re.match(rf"^export (?:async )?function {name}\(", line) or re.match(rf"^function {name}\(", line) or re.match(rf"^\s+(?:async )?{name}\([^)]*\)[^;]*\{{$", line):
                count += 1
    return count


# ------------------------------------------------------------------------------------------------------------------------ reach


def span_of(text: str, name: str) -> tuple[int, int] | None:
    """The lines of the function or class `name` declared at the top level of a file: from its first decorator or `export` line to the line
    of the brace that closes it."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if re.match(rf"^(?:export )?(?:default )?(?:abstract )?(?:async )?(?:function|class) {name}\b", line):
            start = i
            while start > 0:
                prev = lines[start - 1]
                if prev.startswith("@"):
                    start -= 1
                elif prev.rstrip() in ("})", ")"):  # the end of a decorator that takes an object: its first line starts with @
                    j = start - 1
                    while j >= 0 and not lines[j].startswith("@"):
                        j -= 1
                    start = max(j, 0)
                else:
                    break
            depth, seen = 0, False
            for j in range(i, len(lines)):
                depth += lines[j].count("{") - lines[j].count("}")
                seen = seen or "{" in lines[j]
                if seen and depth <= 0:
                    return start + 1, j + 1
    return None


def callers_of(repo: Path, rev: str, importers: list[str], name: str) -> list[str]:
    """The top-level functions of the files that call `name(` (a call, not a declaration): who uses a changed function."""
    found = []
    for path in importers:
        text = text_at(repo, rev, path) or ""
        for m in re.finditer(r"^export (?:async )?function (\w+)", text, re.M):
            span = span_of(text, m.group(1))
            if span and re.search(rf"\b{name}\(", "\n".join(text.split("\n")[span[0]: span[1]])):
                found.append(m.group(1))
    return found


def routes_naming(routes: list[dict], names: list[str]) -> list[dict]:
    """The routes whose own line (the route call, or the call of a chain) spells one of the names as a word."""
    return [r for r in routes if any(re.search(rf"\b{n}\b", r["evidence"][0][1]) for n in names)]
