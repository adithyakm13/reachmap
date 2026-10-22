"""Entry-point detectors: React Router, Next.js, FastAPI, Django, Celery, Express, NestJS and Spring (registered at the bottom).

Each reads one repository at one commit and answers with the config table it would write, the evidence (`path:line`) and a
confidence word. A detector never says "no routes": returning nothing means nothing was found, and the orchestrator
(`reachmap.init.detect`) says that nothing was found. Decoys are part of the rules: a file called `routes.ts` is a router only when
it declares routes the framework's way, `<Routes>` is not `<Route>`, a `urls.py` needs `urlpatterns`, a comment is not code.
"""

from __future__ import annotations

import posixpath
import re
from collections import Counter

from reachmap import testpaths
from reachmap.init import registry
from reachmap.init.common import JS_SUFFIXES, PY_SUFFIXES, dep_names, ev, is_source, is_test, line_of_key, package_jsons
from reachmap.init.guess import Note
from reachmap.init.registry import Ctx, Found
from reachmap.init.tree import code_line, line_of

__all__ = ["routes_glob"]

# ------------------------------------------------------------------------------------------------ React Router

_ROUTE_TAG = re.compile(r"<Route(?=[\s/>])")  # `<Routes>` and `</Route>` do not match
_ROUTE_ATTR = re.compile(r"\b(?:path|index|element|Component|lazy)\b\s*[=:]?")
_ROUTE_ELEMENT = re.compile(r"\b(?:element|Component)\s*=")  # the attributes of React Router's `<Route>` (Solid's router says `component=`)
_ROUTER_CALL = re.compile(r"\b(?:createBrowserRouter|createHashRouter|createMemoryRouter|createStaticRouter|createRoutesFromElements)\s*\(|\buseRoutes\s*\(")
_OBJECT_ROUTE = re.compile(r"\bpath\s*:\s*[\"'`]")
_RR_IMPORT = re.compile(r"""(?:\bfrom\s*|\brequire\(\s*|\bimport\s*)["']react-router(?:-dom|-native)?(?:/[\w./-]*)?["']""")
_OTHER_ROUTER = re.compile(r"""["'](?:@solidjs/router|vue-router|wouter|@reach/router|@tanstack/react-router|@tanstack/router|svelte-routing|preact-router|@angular/router)["']""")
_RR_PACKAGES = ("react-router-dom", "react-router", "@remix-run/router", "react-router-native")


def _in_template(text: str, at: int) -> bool:
    """Whether `at` is inside a template literal (an odd number of backticks before it): `<Route` in a code sample string is no route."""
    return text.count("`", 0, at) % 2 == 1


def _route_hit(text: str) -> tuple[int, str, bool] | None:
    """The first line that declares a route the React Router way: a `<Route` element with a routing attribute within its next lines,
    or a router creation call followed by route objects. (line, what, imports react-router)."""
    imports = _RR_IMPORT.search(text) is not None
    lines = text.splitlines()
    for m in _ROUTE_TAG.finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        if not code_line(lines[line - 1]) or _in_template(text, m.start()):
            continue
        window = text[m.end():m.end() + 300]
        if _ROUTE_ATTR.search(window) and (imports or _ROUTE_ELEMENT.search(window)):
            return line, "<Route element", imports
    if imports:  # a router made by a call counts only where react-router is imported: `useRoutes` and the like are other libraries' names too
        for m in _ROUTER_CALL.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            if code_line(lines[line - 1]) and not _in_template(text, m.start()) and _OBJECT_ROUTE.search(text[m.end():m.end() + 800]):
                return line, m.group(0).split("(")[0].strip(), imports
    return None


def routes_glob(found: list[str], all_files: tuple[str, ...]) -> tuple[str | None, str]:
    """A glob for the routes files that matches exactly them among the repository's files when it can: one file is itself; files that
    differ in one folder name are `*` there (checked against the file list); else a brace list. (glob or None, how it was made)."""
    from reachmap.adapters.react_router import glob_regex

    files = sorted(found)
    if any(c in f for f in files for c in "*?{}"):
        return None, "a routes file has a glob character in its path"
    if len(files) == 1:
        return files[0], "the one routes file"
    parts = [f.split("/") for f in files]
    if len({len(p) for p in parts}) == 1:
        n = len(parts[0])
        varying = [i for i in range(n) if len({p[i] for p in parts}) > 1]
        if len(varying) == 1 and varying[0] < n - 1:
            candidate = "/".join("*" if i == varying[0] else parts[0][i] for i in range(n))
            pattern = glob_regex(candidate)
            if {f for f in all_files if pattern.match(f)} == set(files):
                return candidate, f"the {len(files)} routes files differ only in the folder name at position {varying[0] + 1}"
    if len(files) <= 12:
        return "{" + ",".join(files) + "}", f"the {len(files)} routes files, listed"
    names = {f.rsplit("/", 1)[-1] for f in files}
    if len(names) == 1:
        prefix = posixpath.commonpath([posixpath.dirname(f) or "." for f in files])
        glob = (prefix + "/" if prefix not in ("", ".") else "") + "**/" + names.pop()
        pattern = glob_regex(glob)
        if {f for f in all_files if pattern.match(f)} == set(files):
            return glob, f"the {len(files)} routes files share a name under {prefix or 'the root'}"
    return None, f"{len(files)} routes files do not fit one glob"


def detect_react_router(ctx: Ctx) -> list[Found]:
    dep = None
    for path, data, text in package_jsons(ctx):
        names = dep_names(data)
        found = next((n for n in _RR_PACKAGES if n in names), None)
        if found:
            dep = ev(path, line_of_key(text, found))
            break
    hits: list[tuple[str, int, str, bool]] = []
    rejected: list[tuple[str, int]] = []
    for path, text in ctx.tree.scan(JS_SUFFIXES, skip=lambda p: not is_source(p)):
        hit = _route_hit(text)
        if hit is None:
            continue
        if hit[2] or dep or not _OTHER_ROUTER.search(text):
            hits.append((path, hit[0], hit[1], hit[2]))
        else:
            rejected.append((path, hit[0]))
    for path, line in rejected[:3]:
        ctx.notes.append(f"{ev(path, line)} has a <Route> element but imports another router and the repository has no react-router dependency, so it is not read as React Router; confirm")
    if not hits:
        return []
    glob, how = routes_glob([h[0] for h in hits], ctx.tree.files)
    imported = [h for h in hits if h[3]]
    shown = ", ".join(ev(p, n) for p, n, _, _ in hits[:3]) + (f" and {len(hits) - 3} more" if len(hits) > 3 else "")
    witness = "high" if dep or len(imported) == len(hits) else "low"
    notes = [Note(f"React Router routes declared in {len(hits)} file(s) ({hits[0][2]})", tuple(ev(p, n) for p, n, _, _ in hits[:3]), witness)]
    if witness == "low":
        notes.append(Note("no react-router dependency and no import from react-router in these files: the route elements are the only witness", tuple(ev(p, n) for p, n, _, _ in hits[:2]), "low"))
    options: dict[str, object] = {}
    extra: list[str] = []
    if glob is not None:
        options["routes_glob"] = glob
        notes.append(Note(f"routes_glob from where the routes are: {how}", tuple(ev(p, n) for p, n, _, _ in hits[:3]), "medium"))
    else:
        extra.append(f"routes_glob left out: {how}; list the routes files by hand ({shown}); confirm")
    if dep:
        notes.append(Note("react-router is a dependency", (dep,), "high"))
    return [Found("react-router", options, ("frontend",), tuple(notes), score=len(hits), extra_notes=tuple(extra))]


# ------------------------------------------------------------------------------------------------ Next.js

_NEXT_CONFIG = ("next.config.js", "next.config.mjs", "next.config.ts", "next.config.cjs")
_BASE_PATH = re.compile(r"""\bbasePath\s*:\s*["'](/[^"']*)["']""")


def _next_test(path: str) -> bool:
    """The adapter's own rule: a test file by name only (a route called /test is a route)."""
    return testpaths.is_test_path(path, (".test.", ".spec.", "__tests__/"))


def detect_nextjs(ctx: Ctx) -> list[Found]:
    from reachmap.adapters.nextjs.routes import classify

    best: Found | None = None
    projects = 0
    for pkg, data, text in package_jsons(ctx):
        if "next" not in dep_names(data):
            continue
        root = posixpath.dirname(pkg)
        table = classify(ctx.tree.files, root=root, is_test=_next_test)  # the adapter's routing table: the same files, the same URLs
        routable = [e for e in table.entries if e.role in ("page", "route", "api")]
        if not routable:
            continue
        projects += 1
        pages = [e for e in routable if e.role == "page"]
        api = [e for e in routable if e.role in ("route", "api")]
        options: dict[str, object] = {}
        if root:
            options["root"] = root
        at = ev(pkg, line_of_key(text, "next"))
        notes = [Note(f"Next.js: next is a dependency and the routing folders hold {len(pages)} page(s) and {len(api)} API route(s)", (at, ev(routable[0].path)), "high")]
        if root:
            notes.append(Note("root is the folder of the package.json that depends on next", (at,), "high"))
        for name in _NEXT_CONFIG:
            conf = posixpath.join(root, name)
            conf_text = ctx.tree.text(conf) if ctx.tree.has(conf) else None
            m = _BASE_PATH.search(conf_text or "")
            if m:
                options["base_path"] = m.group(1)
                notes.append(Note("base_path from the Next config", (ev(conf, line_of(conf_text, m.group(0))),), "high"))
                break
        for e in api:  # route hints for the http edge: the URLs the adapter's table gives the API files
            ctx.facts.route_hints.extend(e.paths)
            ctx.facts.route_evidence = ctx.facts.route_evidence or ev(e.path)
        found = Found("nextjs", options, ("frontend", "backend") if api else ("frontend",), tuple(notes), score=1000 + len(routable))
        if best is None or found.score > best.score:
            best = found
    if best is None:
        return []
    if projects > 1:
        best = Found(best.kind, best.options, best.roles, best.notes, best.score,
                     best.extra_notes + (f"{projects} Next.js projects found in this repository; one entry_points table takes one root, so the others need a repository entry of their own; confirm",))
    return [best]


# ------------------------------------------------------------------------------------------------ FastAPI

_FASTAPI_APP = re.compile(r"\bFastAPI\s*\(")
_API_ROUTER = re.compile(r"\bAPIRouter\s*\(")
_FASTAPI_IMPORT = re.compile(r"^[ \t]*(?:from\s+fastapi(?:\.\w+)*\s+import|import\s+fastapi\b)", re.MULTILINE)
_PREFIX = re.compile(r"""\bprefix\s*=\s*["']([^"']*)["']""")
_DECORATED = re.compile(r"""^[ \t]*@\w+\.(?:get|post|put|patch|delete|options|head|api_route)\(\s*["']([^"']*)["']""", re.MULTILINE)


def _hint_segments(path: str) -> list[str]:
    return [s for s in re.split(r"/+", path.split("?")[0]) if s and not s.startswith(("{", "<", ":"))]


def _first_code_match(text: str, pattern: re.Pattern[str]) -> int:
    """The line of the first match of `pattern` that sits on a line of code (not a comment or a docstring start); 0 when none."""
    lines = text.splitlines()
    for m in pattern.finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        if line <= len(lines) and code_line(lines[line - 1]):
            return line
    return 0


def detect_fastapi(ctx: Ctx) -> list[Found]:
    files: list[tuple[str, int, str]] = []
    for path, text in ctx.tree.scan(PY_SUFFIXES, skip=lambda p: not is_source(p)):
        if not _FASTAPI_IMPORT.search(text):
            continue
        line = _first_code_match(text, _FASTAPI_APP)
        what = "FastAPI("
        if not line:
            line, what = _first_code_match(text, _API_ROUTER), "APIRouter("
        if not line:
            continue
        files.append((path, line, what))
        for p in _PREFIX.findall(text) + _DECORATED.findall(text):
            ctx.facts.route_hints.extend(_hint_segments(p))
        ctx.facts.route_evidence = ctx.facts.route_evidence or ev(path, line)
    if not files:
        return []
    apps = [f for f in files if f[2] == "FastAPI("]
    first = (apps or files)[0]
    why = f"FastAPI: {len(apps)} app and {len(files) - len(apps)} router declaration(s) in files that import fastapi"
    return [Found("fastapi", {}, ("backend",), (Note(why, (ev(first[0], first[1]),), "high"),), score=len(files))]


# ------------------------------------------------------------------------------------------------ Django

_URLPATTERNS = re.compile(r"^[ \t]*urlpatterns\s*(?:\+)?=", re.MULTILINE)
_ROOT_URLCONF = re.compile(r"""^[ \t]*ROOT_URLCONF\s*=\s*["']([\w.]+)["']""", re.MULTILINE)
_DJANGO_URL = re.compile(r"""^[ \t]*(?:re_)?path\(\s*r?["']([^"']*)["']""", re.MULTILINE)
_DJANGO_IMPORT = re.compile(r"^[ \t]*(?:from\s+django\b|import\s+django\b)", re.MULTILINE)


def detect_django(ctx: Ctx) -> list[Found]:
    root_conf: tuple[str, int, str] | None = None
    urls: list[tuple[str, int]] = []
    manage: str | None = None
    for path, text in ctx.tree.scan(PY_SUFFIXES, skip=lambda p: not is_source(p)):
        name = path.rsplit("/", 1)[-1]
        m = _ROOT_URLCONF.search(text)
        if m and root_conf is None:
            root_conf = (path, line_of(text, m.group(0)), m.group(1))
        if name == "urls.py" and _URLPATTERNS.search(text) and _DJANGO_IMPORT.search(text):
            urls.append((path, line_of(text, _URLPATTERNS)))
            for p in _DJANGO_URL.findall(text):
                ctx.facts.route_hints.extend(_hint_segments(p))
            ctx.facts.route_evidence = ctx.facts.route_evidence or ev(path, urls[-1][1])
        if name == "manage.py" and "DJANGO_SETTINGS_MODULE" in text and manage is None:
            manage = ev(path, line_of(text, "DJANGO_SETTINGS_MODULE"))
    if not urls or (root_conf is None and manage is None):
        return []
    evidence = tuple(x for x in ((ev(root_conf[0], root_conf[1]) if root_conf else None), manage, ev(*urls[0])) if x)
    why = f"Django: {len(urls)} urls.py with urlpatterns" + (f" and ROOT_URLCONF = {root_conf[2]}" if root_conf else " and a manage.py that sets DJANGO_SETTINGS_MODULE")
    return [Found("django", {}, ("backend",), (Note(why, evidence, "high" if root_conf else "medium"),), score=len(urls))]


# ------------------------------------------------------------------------------------------------ Celery

_SHARED_TASK = re.compile(r"^[ \t]*@shared_task\b", re.MULTILINE)
_CELERY_APP = re.compile(r"^[ \t]*(\w+)\s*=\s*Celery\s*\(", re.MULTILINE)
_CELERY_IMPORT = re.compile(r"^[ \t]*(?:from\s+celery\b|import\s+celery\b)", re.MULTILINE)
_TOPIC_CONSTANT = re.compile(r"""^([A-Z][A-Z0-9_]*)\s*=\s*["']([a-z][\w.-]*\.[\w.-]+)["']""", re.MULTILINE)
_TOPIC_FILES = ("topics.py", "topics.ts", "topics.js")


def detect_celery(ctx: Ctx) -> list[Found]:
    texts = [(path, text) for path, text in ctx.tree.scan(PY_SUFFIXES, skip=lambda p: not is_source(p))]
    apps: set[str] = set()  # names bound to a Celery application anywhere in the repository (tasks import them from the module that makes them)
    for _path, text in texts:
        if _CELERY_IMPORT.search(text):
            apps |= set(_CELERY_APP.findall(text))
    tasks: list[tuple[str, int]] = []
    for path, text in texts:
        local = _CELERY_IMPORT.search(text) is not None
        names = set(apps) | ({"app", "celery"} if local else set())
        pattern = re.compile(r"^[ \t]*@(?:" + "|".join(re.escape(n) for n in sorted(names)) + r")\.task\b", re.MULTILINE) if names else None
        hit = (_SHARED_TASK.search(text) if local else None) or (pattern.search(text) if pattern else None)
        if hit:
            tasks.append((path, line_of(text, hit.group(0).strip(), hit.start())))
    if not tasks:
        return []
    notes = [Note(f"Celery tasks (@shared_task or @app.task) in {len(tasks)} file(s); the celery kind reads no routes and is not followed yet "
                  "(a run records that the entry points were not read)", (ev(*tasks[0]),), "high")]
    for path in ctx.tree.files:
        if path.rsplit("/", 1)[-1] in _TOPIC_FILES:
            text = ctx.tree.text(path) or ""
            if len(_TOPIC_CONSTANT.findall(text)) >= 2:
                ctx.facts.topics = ev(path, line_of(text, _TOPIC_CONSTANT))
                break
    return [Found("celery", {}, ("backend",), tuple(notes), score=len(tasks))]


# ------------------------------------------------------------------------------------------------ Express and NestJS

_EXPRESS_IMPORT = re.compile(r"""(?:\bfrom\s*["']express["']|\brequire\(\s*["']express["']\s*\)|\bfrom\s*["']express-promise-router["'])""")
_EXPRESS_APP = re.compile(r"""(?:\bexpress\s*\(\s*\)|\bexpress\.Router\s*\(|\bnew\s+express\.Router\s*\(|(?<![\w.])Router\s*\(\s*[{)]|\brequire\(\s*["']express["']\s*\)\s*\()""")
_NEST_IMPORT = re.compile(r"""\bfrom\s*["']@nestjs/common["']""")
_NEST_CONTROLLER = re.compile(r"^[ \t]*@Controller\s*\(", re.MULTILINE)
_EXPRESS_PATH = re.compile(r"""\b\w+\s*\.\s*(?:get|post|put|patch|delete|all|use)\(\s*["'`](/[^"'`]*)["'`]""")
_NEST_PATH = re.compile(r"""^[ \t]*@(?:Controller|Get|Post|Put|Patch|Delete|All)\(\s*(?:["'`]([^"'`]*)["'`]|([A-Za-z_]\w*)\s*\))""", re.MULTILINE)
_STRING_CONSTANT = re.compile(r"""^(?:export\s+)?const\s+([A-Za-z_]\w*)\s*=\s*["'`]([^"'`\n]*)["'`]""", re.MULTILINE)


def detect_express(ctx: Ctx) -> list[Found]:
    files: list[tuple[str, int]] = []
    for path, text in ctx.tree.scan(JS_SUFFIXES, skip=lambda p: not is_source(p)):
        if not _EXPRESS_IMPORT.search(text):
            continue
        line = _first_code_match(text, _EXPRESS_APP)
        if line:
            files.append((path, line))
            for p in _EXPRESS_PATH.findall(text):
                ctx.facts.route_hints.extend(_hint_segments(p.replace(":", "{")))
            ctx.facts.route_evidence = ctx.facts.route_evidence or ev(path, line)
    if not files:
        return []
    dep = next((ev(path, line_of_key(text, "express")) for path, data, text in package_jsons(ctx) if "express" in dep_names(data)), None)
    notes = [Note(f"Express: {len(files)} file(s) import express and make an app or a router", (ev(*files[0]),), "high" if dep else "medium")]
    if dep:
        notes.append(Note("express is a dependency", (dep,), "high"))
    return [Found("express", {}, ("backend",), tuple(notes), score=len(files))]


def detect_nestjs(ctx: Ctx) -> list[Found]:
    files: list[tuple[str, int]] = []
    nest_hints: list[str] = []
    constants: dict[str, str] = {}
    for path, text in ctx.tree.scan((".ts", ".js", ".mts"), skip=lambda p: not is_source(p)):
        constants.update(dict(_STRING_CONSTANT.findall(text)))
        if _NEST_IMPORT.search(text):
            line = _first_code_match(text, _NEST_CONTROLLER)
            if line:
                files.append((path, line))
                for literal, name in _NEST_PATH.findall(text):
                    nest_hints.append(literal or name)
                ctx.facts.route_evidence = ctx.facts.route_evidence or ev(path, line)
    for hint in nest_hints:  # a controller path that is a constant of the repository is read through it
        ctx.facts.route_hints.extend(_hint_segments(constants.get(hint, hint) if re.match(r"^[A-Za-z_]\w*$", hint) else hint))
    if not files:
        return []
    dep = next((ev(path, line_of_key(text, "@nestjs/core")) for path, data, text in package_jsons(ctx) if "@nestjs/core" in dep_names(data)), None)
    notes = [Note(f"NestJS: {len(files)} controller file(s) with @Controller from @nestjs/common", (ev(*files[0]),), "high" if dep else "medium")]
    if dep:
        notes.append(Note("@nestjs/core is a dependency", (dep,), "high"))
    return [Found("nestjs", {}, ("backend",), tuple(notes), score=len(files))]


# ------------------------------------------------------------------------------------------------ Spring

JVM_SUFFIXES = (".java", ".kt")
_SPRING_IMPORT = re.compile(r"^[ \t]*import[ \t]+(?:static[ \t]+)?org\.springframework\.", re.MULTILINE)
_SPRING_CONTROLLER = re.compile(r"^[ \t]*@(?:RestController|Controller|RequestMapping|GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping)\b", re.MULTILINE)
_SPRING_PATH = re.compile(r"""@(?:Request|Get|Post|Put|Delete|Patch)Mapping\s*\(\s*(?:(?:value|path)\s*=\s*)?(?:\{\s*|arrayOf\(\s*)?"([^"$]*)\"""")
_SPRING_BUILD = ("pom.xml", "build.gradle", "build.gradle.kts")


def detect_spring(ctx: Ctx) -> list[Found]:
    files: list[tuple[str, int]] = []
    for path, text in ctx.tree.scan(JVM_SUFFIXES, skip=lambda p: not is_source(p) or "/src/test/" in "/" + p):
        if not _SPRING_IMPORT.search(text):
            continue
        line = _first_code_match(text, _SPRING_CONTROLLER)
        if line:
            files.append((path, line))
            for p in _SPRING_PATH.findall(text):
                ctx.facts.route_hints.extend(_hint_segments(p))
            ctx.facts.route_evidence = ctx.facts.route_evidence or ev(path, line)
    if not files:
        return []
    dep = None
    for name in _SPRING_BUILD:
        for path in ctx.tree.named(name):
            text = ctx.tree.text(path) or ""
            m = re.search(r"spring-boot|spring-web|org\.springframework", text)
            if m:
                dep = ev(path, line_of(text, m.group(0)))
                break
        if dep:
            break
    notes = [Note(f"Spring: {len(files)} file(s) import org.springframework and declare a controller or a request mapping", (ev(*files[0]),), "high" if dep else "medium")]
    if dep:
        notes.append(Note("Spring is a dependency of the build", (dep,), "high"))
    return [Found("spring", {}, ("backend",), tuple(notes), score=len(files))]


def register_all() -> None:
    registry.register("entry_points", "react-router", detect_react_router, languages=("typescript", "javascript"))
    registry.register("entry_points", "nextjs", detect_nextjs, languages=("typescript", "javascript"))
    registry.register("entry_points", "fastapi", detect_fastapi, languages=("python",))
    registry.register("entry_points", "django", detect_django, languages=("python",))
    registry.register("entry_points", "celery", detect_celery, languages=("python",))
    registry.register("entry_points", "express", detect_express, languages=("typescript", "javascript"))
    registry.register("entry_points", "nestjs", detect_nestjs, languages=("typescript", "javascript"))
    registry.register("entry_points", "spring", detect_spring, languages=("java", "kotlin"))


register_all()
