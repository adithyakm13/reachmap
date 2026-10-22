"""An independent derivation of the facts the Django scenarios claim (`tests/fixtures/dj.py`), for `tests/test_fixtures_dj.py`.

It shares nothing with the adapter: it reads the fixture repositories with `git show`, parses the URLconfs with `ast` in the one shape the fixtures use
(`path()` and `re_path()` calls in `urlpatterns`, `include("dotted.module")`, `include(alias_of_a_module)`, `include(router.urls)`,
`views.function`, `views.Class.as_view()`, `api.ViewSet` registered with `router.register`), composes the routes top down from `ROOT_URLCONF`, and
says which URLconfs nothing includes. It is as simple as it can be: it does not understand anything the fixtures do not use.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tests.fixtures.claims import text_at

ACTIONS = {"ModelViewSet": ["list", "create", "retrieve", "update", "partial_update", "destroy"], "ReadOnlyModelViewSet": ["list", "retrieve"],
           "ListModelMixin": ["list"], "RetrieveModelMixin": ["retrieve"], "CreateModelMixin": ["create"]}
LIST_ROWS = [("GET", "list"), ("POST", "create")]
DETAIL_ROWS = [("GET", "retrieve"), ("PUT", "update"), ("PATCH", "partial_update"), ("DELETE", "destroy")]


def join(*parts: str) -> str:
    out = "/".join(p.strip("/") for p in parts if p and p.strip("/"))
    return "/" + out


def route_text(route: str) -> str:
    return re.sub(r"<(?:\w+:)?(\w+)>", r"{\1}", route)


def regex_text(regex: str) -> str:
    text = regex.lstrip("^").rstrip("$").strip("/")
    return re.sub(r"\(\?P<(\w+)>[^)]*\)", r"{\1}", text)


class Project:
    def __init__(self, repo: Path, rev: str) -> None:
        self.repo, self.rev = repo, rev
        self.trees: dict[str, ast.Module | None] = {}

    def text(self, path: str) -> str | None:
        return text_at(self.repo, self.rev, path)

    def tree(self, path: str) -> ast.Module | None:
        if path not in self.trees:
            text = self.text(path)
            self.trees[path] = ast.parse(text) if text is not None else None
        return self.trees[path]

    def lines(self, path: str) -> list[str]:
        return (self.text(path) or "").split("\n")

    def module_path(self, dotted: str) -> str | None:
        for cand in (dotted.replace(".", "/") + ".py", dotted.replace(".", "/") + "/__init__.py"):
            if self.text(cand) is not None:
                return cand
        return None

    def root(self) -> str:
        for path in ("config/settings/base.py", "config/settings.py"):
            text = self.text(path)
            if text:
                m = re.search(r'^ROOT_URLCONF = "([\w.]+)"$', text, re.M)
                if m:
                    return self.module_path(m.group(1))  # type: ignore[return-value]
        raise LookupError("no literal ROOT_URLCONF")

    def aliases(self, path: str) -> dict[str, str]:
        """A name in the file to the module file it imports (`from billing import views as v`, `from . import views`)."""
        out: dict[str, str] = {}
        tree = self.tree(path)
        base = path.rsplit("/", 1)[0] if "/" in path else ""
        for node in ast.walk(tree) if tree else ():
            if isinstance(node, ast.ImportFrom):
                for a in node.names:
                    dotted = ".".join(x for x in ((base.replace("/", ".") if node.level else ""), node.module or "", a.name) if x)
                    mod = self.module_path(dotted)
                    if mod:
                        out[a.asname or a.name] = mod
        return out

    def patterns(self, path: str) -> list[ast.Call]:
        tree = self.tree(path)
        found = []
        for node in tree.body if tree else ():
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "urlpatterns" for t in node.targets):
                found += [e for e in node.value.elts if isinstance(e, ast.Call)]  # type: ignore[attr-defined]
        return found

    def routers(self, path: str) -> dict[str, list[tuple[str, str, int]]]:
        """Router variable to its registrations (prefix, viewset reference, line) in a file."""
        tree, out = self.tree(path), {}
        for node in tree.body if tree else ():
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and "Router" in ast.unparse(node.value.func):
                out[node.targets[0].id] = []  # type: ignore[union-attr]
        for node in tree.body if tree else ():
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == "register":
                out[node.value.func.value.id].append((node.value.args[0].value, ast.unparse(node.value.args[1]), node.lineno))  # type: ignore[attr-defined]
        return out

    def viewset(self, path: str, ref: str) -> tuple[str, list[str], list[dict]]:
        """(the module file, the actions of the class, its @action extras) of `alias.ClassName`."""
        alias, cls = ref.split(".")
        mod = self.aliases(path)[alias]
        for node in self.tree(mod).body:  # type: ignore[union-attr]
            if isinstance(node, ast.ClassDef) and node.name == cls:
                actions: list[str] = []
                for base in node.bases:
                    actions += ACTIONS.get(ast.unparse(base).split(".")[-1], [])
                extras = []
                for item in node.body:
                    for deco in getattr(item, "decorator_list", ()):
                        if isinstance(deco, ast.Call) and ast.unparse(deco.func) == "action":
                            kws = {k.arg: ast.literal_eval(k.value) for k in deco.keywords}
                            extras.append({"name": item.name, "detail": kws["detail"], "methods": [m.upper() for m in kws.get("methods", ["get"])], "line": deco.lineno})
                return mod, actions, extras
        raise LookupError(ref)

    def view(self, path: str, expr: ast.expr) -> tuple[list[str], str]:
        """(the methods, the handler name) a view expression gives, in the fixture's shapes."""
        mod_alias, _, rest = ast.unparse(expr).partition(".")
        if rest.endswith(".as_view()"):
            cls = rest[: -len(".as_view()")]
            methods = [n.name.upper() for n in self.class_node(path, mod_alias, cls).body if isinstance(n, ast.FunctionDef) and n.name in ("get", "post", "put", "patch", "delete")]  # type: ignore[union-attr]
            return methods, cls
        node = self.function_node(path, mod_alias, rest)
        for deco in node.decorator_list:
            if isinstance(deco, ast.Call) and ast.unparse(deco.func) == "require_http_methods":
                return [m.upper() for m in ast.literal_eval(deco.args[0])], rest
        return ["ANY"], rest

    def class_node(self, path: str, alias: str, name: str) -> ast.ClassDef:
        return next(n for n in self.tree(self.aliases(path)[alias]).body if isinstance(n, ast.ClassDef) and n.name == name)  # type: ignore[union-attr]

    def function_node(self, path: str, alias: str, name: str) -> ast.FunctionDef:
        return next(n for n in self.tree(self.aliases(path)[alias]).body if isinstance(n, ast.FunctionDef) and n.name == name)  # type: ignore[union-attr]


def routes(repo: Path, rev: str) -> tuple[list[dict], list[str]]:
    """(the routes mounted under ROOT_URLCONF as {method, path, handler, partial, lines: [(file, line)]}, the URLconf files nothing includes)."""
    pr = Project(repo, rev)
    out: list[dict] = []
    reached: set[str] = set()

    def walk(path: str, prefix: str, outer: list[tuple[str, int]]) -> None:
        reached.add(path)
        aliases = pr.aliases(path)
        for call in pr.patterns(path):
            fn = call.func.id  # type: ignore[attr-defined]
            arg0 = call.args[0].value  # type: ignore[attr-defined]
            piece = route_text(arg0) if fn == "path" else regex_text(arg0)
            here = [(path, call.lineno)] + outer
            target = call.args[1]
            if isinstance(target, ast.Call) and ast.unparse(target.func) == "include":
                inner = target.args[0]
                if isinstance(inner, ast.Constant):
                    mod = pr.module_path(inner.value)
                    walk(mod, join(prefix, piece), here)  # type: ignore[arg-type]
                elif isinstance(inner, ast.Name):
                    walk(aliases[inner.id], join(prefix, piece), here)
                else:  # include(router.urls)
                    var = inner.value.id  # type: ignore[attr-defined]
                    router(path, var, join(prefix, piece), here)
            elif ast.unparse(target) == "admin.site.urls":
                continue
            else:
                methods, handler = pr.view(path, target)
                for m in methods:
                    out.append({"method": m, "path": join(prefix, piece), "handler": handler, "partial": fn == "re_path", "lines": here})

    def router(path: str, var: str, prefix: str, outer: list[tuple[str, int]]) -> None:
        out.append({"method": "GET", "path": join(prefix), "handler": "APIRootView", "partial": False, "lines": [(path, ctor_line(path, var))] + outer})
        for pre, ref, line in pr.routers(path)[var]:
            mod, actions, extras = pr.viewset(path, ref)
            cls = ref.split(".")[1]
            for rows, suffix in ((LIST_ROWS, ""), (DETAIL_ROWS, "{pk}")):
                for method, action in rows:
                    if action in actions:
                        out.append({"method": method, "path": join(prefix, pre, suffix), "handler": f"{cls}.{action}", "partial": False, "lines": [(path, line)] + outer})
            for extra in extras:
                for method in extra["methods"]:
                    path_ = join(prefix, pre, "{pk}" if extra["detail"] else "", extra["name"])
                    out.append({"method": method, "path": path_, "handler": f"{cls}.{extra['name']}", "partial": False, "lines": [(mod, extra["line"]), (path, line)] + outer})

    def ctor_line(path: str, var: str) -> int:
        return next(n.lineno for n in pr.tree(path).body if isinstance(n, ast.Assign) and n.targets[0].id == var)  # type: ignore[union-attr]

    walk(pr.root(), "/", [])
    every = sorted(p for p in (f.rstrip("\n") for f in _py_files(repo, rev)) if p.endswith(("urls.py", "_urls.py")) and "/tests/" not in p)
    return out, [p for p in every if p not in reached]


def _py_files(repo: Path, rev: str) -> list[str]:
    from tests.fixtures.build import run_git

    proc = run_git(repo, "ls-tree", "-r", "--name-only", rev)
    return [line for line in proc.stdout.split("\n") if line.endswith(".py")]
