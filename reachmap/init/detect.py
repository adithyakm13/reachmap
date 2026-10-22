"""Detection: one repository at its default branch in, a `RepoGuess` out (context 10 step 12, the M17 brief).

`detect(spec, git)` reads a repository through the git layer and calls `guess_repo` on its tree; `guess_repo` is the part tests feed
with an in-memory tree. The generic detectors live here (language, branches, aliases, tests, service marker, library roots, roles);
the entry-point, client and contract detectors are registered with `reachmap.init.registry` (see `entries.py`, `clients.py`,
`contract.py`), so another adapter's detector is one `register` call.

Every guessed value carries notes: why, `path:line` evidence and a confidence word. A repository that is read is never "empty": when
a detector finds nothing the table is left out and the output says what was looked for.
"""

from __future__ import annotations

import json
import posixpath
import re
from collections import Counter
from typing import Any, Callable

from reachmap import config, entrypoints, testpaths  # noqa: F401  (entrypoints registers the entry_points kinds the config accepts: django, nextjs)
from reachmap.adapters.js.aliases import load_aliases
from reachmap.gitlayer import Git
from reachmap.init import clients, contract, entries, registry  # noqa: F401  (importing them registers their detectors)
from reachmap.init.common import JS_SUFFIXES, PY_SUFFIXES, dep_names, ev, is_source, is_test, line_of_key, package_jsons, pyprojects
from reachmap.init.facts import read_imports, read_manifests
from reachmap.init.guess import CONFIDENCE, Facts, Guess, Note, RepoGuess, SpecError
from reachmap.init.registry import Ctx, Found
from reachmap.init.source import Opened, Spec, open_repo, parse_spec
from reachmap.init.tree import Tree, line_of
from reachmap.model import Read

__all__ = ["detect", "guess_repo"]

LANGUAGE_SUFFIXES = {
    "typescript": (".ts", ".tsx", ".mts", ".cts"),
    "javascript": (".js", ".jsx", ".mjs", ".cjs"),
    "python": (".py",),
    "java": (".java",),
    "kotlin": (".kt", ".kts"),
}
OTHER_SUFFIXES = {".go": "go", ".rs": "rust", ".rb": "ruby", ".php": "php", ".cs": "c#", ".cpp": "c++", ".cc": "c++", ".c": "c", ".swift": "swift", ".scala": "scala",
                  ".ex": "elixir", ".dart": "dart", ".lua": "lua", ".pl": "perl", ".sh": "shell"}
RELEASE_PATTERNS = (("release/", "release/*"), ("release-", "release-*"), ("hotfix/", "hotfix/*"))
SCREEN_KINDS = ("react-router", "nextjs")
SERVER_ONLY = ("fastapi", "django", "express", "nestjs", "spring")  # entry_points kinds that serve routes and have no screens
FRONTEND_PACKAGES = ("react", "react-dom", "vue", "@angular/core", "svelte", "preact", "solid-js", "next")


# ------------------------------------------------------------------------------------------------ entry


def detect(spec: str | Spec, git: Git) -> RepoGuess:
    """Read one repository and guess its config. Raises `SpecError` (the reason is scrubbed) when it cannot be read."""
    parsed = parse_spec(spec) if isinstance(spec, str) else spec
    opened = open_repo(parsed, git)

    def load_tags() -> tuple[str, ...]:
        res = git.tags(opened.remote)
        return tuple(res.tags) if res.state == Read.FOUND else ()

    try:
        return guess_repo(
            parsed.name, opened.tree, spec=parsed.shown, host=parsed.kind if parsed.kind in config.HOSTS else "git", remote=opened.config_remote,
            remote_local=opened.local, branch=opened.branch, sha=opened.sha, heads=opened.heads, tags_loader=load_tags,
            remote_notes=opened.notes, branch_note=opened.branch_note,
        )
    except SpecError as exc:
        raise SpecError(parsed.shown, exc.reason) from None


def guess_repo(
    name: str, tree: Tree, *, spec: str = "", host: str = "git", remote: str | None = None, remote_local: bool = False, branch: str = "main",
    sha: str = "0" * 40, heads: tuple[str, ...] = ("main",), tags_loader: Callable[[], tuple[str, ...]] | None = None,
    remote_notes: list[str] | None = None, branch_note: str | None = None,
) -> RepoGuess:
    language, language_guess, language_notes = detect_language(tree)
    facts = Facts()
    ctx = Ctx(tree, language, facts, tags_loader=tags_loader)
    sections: dict[str, Guess] = {}
    notes: list[str] = list(language_notes)

    read_manifests(ctx)
    read_imports(ctx)
    found_entry = choose(ctx, "entry_points", notes, prefer=lambda f: (f.kind != "celery", f.score))
    server = choose(ctx, "http_server", notes)
    if found_entry is not None and server is not None and found_entry.kind != "celery":
        notes.append(f"a committed OpenAPI spec is at {server.options.get('spec')} but this repository's routes are read from its {found_entry.kind} code "
                     "(a repository has one source of routes); confirm")
        server = None
    client = choose(ctx, "http_client", notes, prefer=lambda f: (f.kind == "openapi", f.score))
    contract_found = choose(ctx, "contract", notes)
    if client is not None and (server is not None or (found_entry is not None and found_entry.kind in SERVER_ONLY)):
        # a module of URL constants in a repository that serves routes is its own configuration (a prefix, a base path), not a client
        notes.append(f"{client.notes[0].evidence[0] if client.notes[0].evidence else ''} holds URL constants, but this repository serves routes, so no http_client is written; "
                     "add one by hand if it calls another service; confirm")
        client = None

    service = detect_service_marker(ctx)
    library = detect_library_roots(ctx) if language == "python" else None
    aliases = detect_aliases(ctx, found_entry)
    markers, command = detect_tests(ctx)
    facts.e2e = detect_e2e(ctx)

    if service is not None:
        sections["service_marker"] = service
    if library is not None:
        sections["library_roots"] = library
    if aliases is not None:
        sections["aliases"] = aliases
    if found_entry is not None:
        sections["entry_points"] = _table(found_entry)
        notes.extend(found_entry.extra_notes)
    if server is not None:
        sections["http_server"] = _table(server)
        notes.extend(server.extra_notes)
    if client is not None:
        sections["http_client"] = _table(client)
        notes.extend(client.extra_notes)
    serves = (found_entry is not None and found_entry.kind != "celery") or server is not None
    if contract_found is not None:
        if serves:
            notes.append(f"Pydantic models under {contract_found.options.get('root')} look like a contract, but this repository also serves routes, so no contract table "
                         "is written; confirm")
        else:
            sections["contract"] = _table(contract_found)
            notes.extend(contract_found.extra_notes)
    if markers is not None:
        value: dict[str, Any] = {"markers": markers.value}
        tests_notes = list(markers.notes)
        if command is not None:
            value["command"] = command.value
            tests_notes += list(command.notes)
        sections["tests"] = Guess(value, tuple(tests_notes))

    if aliases is None and language in ("typescript", "javascript"):
        extra_alias = build_tool_alias_note(tree)
        if extra_alias:
            notes.append(extra_alias)
    notes.extend(ctx.notes)
    roles = detect_roles(ctx, found_entry, server, client, contract_found if (contract_found and not serves) else None,
                         unread=len(tree.unread) + len(tree.skipped))
    branch_guess = detect_branches(branch, heads, branch_note)
    guess = RepoGuess(
        spec=spec, name=name, host=host, remote=remote, remote_local=remote_local, default_branch=branch, sha=sha, branches=branch_guess,
        language=language_guess, roles=roles, sections=sections, notes=notes, facts=facts, remote_notes=list(remote_notes or ()),
    )
    # every read that failed and every entry the checkout did not write (a symlink, a submodule, a name that is not UTF-8) is listed
    guess.unread = sorted({**{p: f"not written to the checkout: {why}" for p, why in tree.skipped.items()}, **tree.unread}.items())
    guess.notes.extend(tree.limits)
    return guess


def choose(ctx: Ctx, section: str, notes: list[str], prefer: Callable[[Found], Any] = lambda f: f.score) -> Found | None:
    """The best candidate of a section; the others are named in `notes` (a repository has one table of each section)."""
    found: list[Found] = []
    for _kind, fn in registry.detectors(section, ctx.language):
        found.extend(fn(ctx))
    if not found:
        return None
    found.sort(key=prefer, reverse=True)
    best, rest = found[0], found[1:]
    for other in rest:
        where = other.notes[0].evidence[0] if other.notes and other.notes[0].evidence else "?"
        notes.append(f"also found: {other.kind} ({other.notes[0].why if other.notes else ''}, {where}); only one {section} table can be written, so {best.kind} was used; confirm")
    return best


def _table(found: Found) -> Guess:
    return Guess({"kind": found.kind, **dict(found.options)}, found.notes)


# ------------------------------------------------------------------------------------------------ language and branches


def detect_language(tree: Tree) -> tuple[str, Guess, list[str]]:
    counts: Counter[str] = Counter()
    first: dict[str, str] = {}
    other: Counter[str] = Counter()  # languages Reachmap does not read, only to say what was found
    for f in tree.files:
        if f.endswith(".d.ts"):
            continue
        if "." in f.rsplit("/", 1)[-1] and OTHER_SUFFIXES.get("." + f.rsplit(".", 1)[-1]):
            other[OTHER_SUFFIXES["." + f.rsplit(".", 1)[-1]]] += 1
        for lang, suffixes in LANGUAGE_SUFFIXES.items():
            if f.endswith(suffixes):
                counts[lang] += 1
                first.setdefault(lang, f)
    allowed = set(config.languages())
    usable = [(n, lang) for lang, n in counts.items() if lang in allowed]
    if not usable:
        seen = ", ".join(f"{n} {lang}" for lang, n in (counts + other).most_common()) or "no source files"
        raise SpecError("", f"no source file in a language Reachmap reads ({', '.join(config.languages())}); found {seen}")
    # typescript wins a tie with javascript (a TypeScript repository keeps a few JavaScript config files)
    usable.sort(key=lambda t: (-t[0], 0 if t[1] == "typescript" else 1, t[1]))
    top_n, top = usable[0]
    others = [f"{n} {lang}" for n, lang in usable[1:]]
    why = f"{top_n} {top} file{'s' if top_n != 1 else ''}" + (f" against {', '.join(others)}" if others else "")
    notes = []
    if usable[1:] and usable[1][0] * 4 >= top_n:
        notes.append(f"the repository also holds {usable[1][0]} {usable[1][1]} file{'s' if usable[1][0] != 1 else ''}; a repository has one language, so {top} was chosen; confirm")
    guess = Guess(top, (Note(why, (ev(first[top]),), "high" if not usable[1:] or usable[1][0] * 4 < top_n else "medium"),))
    return top, guess, notes


def detect_branches(default: str, heads: tuple[str, ...], branch_note: str | None) -> Guess:
    value = [default]
    notes: list[Note] = []
    if branch_note:
        notes.append(Note(branch_note, (f"refs/heads/{default}",), "low"))
    for prefix, pattern in RELEASE_PATTERNS:
        matched = sorted(h for h in heads if h.startswith(prefix) and len(h) > len(prefix))
        if matched:
            value.append(pattern)
            shown = ", ".join(matched[:3]) + (f" and {len(matched) - 3} more" if len(matched) > 3 else "")
            notes.append(Note(f"release branches: {shown} exist and match {pattern}", tuple(f"refs/heads/{h}" for h in matched[:2]), "medium"))
    return Guess(value, tuple(notes))


# ------------------------------------------------------------------------------------------------ aliases

_BUILD_TOOL_CONFIGS = re.compile(r"^(?:vite|webpack|rollup|rspack|snowpack|parcel)[\w.-]*\.config\.(?:js|cjs|mjs|ts)$|^craco\.config\.js$|^babel\.config\.js$|^\.babelrc\.js$")


def _alias_config(tree: Tree, path: str) -> dict[str, Any] | None:
    """What one tsconfig/jsconfig says about aliases, read with the JavaScript adapter's own loader (`load_aliases`: JSON with comments
    to a depth limit, relative `extends` followed, targets that are not folders of the tree dropped and recorded). `targets` is how many
    path targets the config declares and `ok` how many of them are folders of the tree; `error` says why it could not be read; None when
    the file is not in the tree at all. A symlink or another entry the checkout did not write is a candidate with an error."""
    if not tree.has(path) and path not in tree.skipped:
        return None
    records: list[Any] = []
    cfg = load_aliases(tree.read, path, tree.is_dir, records)
    missing = [r for r in records if "does not exist at the pinned commit" in (r.detail or "")]
    unreadable = [r for r in records if r.kind == "read_unreadable"]
    error = tree.unread.get(path) or (unreadable[0].detail if unreadable else None)
    if error:
        tree.unread.setdefault(path, error)
    kept = sum(len(targets) for _, targets in cfg.paths)
    texts = {f: tree.text(f) or "" for f in cfg.chain}
    where = ev(path)
    for f in reversed(cfg.chain):  # the file that sets paths (or the base URL) is the evidence, the nearest to the config first
        for key in ('"paths"', '"baseUrl"'):
            if key in texts[f]:
                where = ev(f, line_of(texts[f], key))
                break
        else:
            continue
        break
    return {"path": path, "targets": kept + len(missing), "ok": kept, "has_paths": bool(cfg.paths or missing), "has_base": cfg.base_url is not None,
            "extends_package": any("extends the package" in (r.detail or "") for r in records), "error": error, "where": where}


def _aliasing(info: dict[str, Any]) -> bool:
    return bool(info["error"] or info["has_paths"] or info["has_base"] or info["extends_package"])


def build_tool_alias_note(tree: Tree) -> str | None:
    """A vite, webpack or similar config that sets aliases: said, never pointed at (only JSON configs are read)."""
    for name in tree.files:
        if _BUILD_TOOL_CONFIGS.match(name.rsplit("/", 1)[-1]):
            text = tree.text(name) or ""
            m = re.search(r"\balias\b", text)
            if m:
                return f"{name}:{line_of(text, m.group(0))} sets aliases, but Reachmap reads only JSON configs (tsconfig.json, jsconfig.json): add them there if imports use them; confirm"
    return None


def detect_aliases(ctx: Ctx, entry: Found | None) -> Guess | None:
    """`aliases = { from = ... }`: the tsconfig.json or jsconfig.json (root, then the Next root) whose `paths` targets exist in the tree.
    A config whose targets do not exist (a stale jsconfig.json next to the real tsconfig.json) is not used, and says why."""
    if ctx.language not in ("typescript", "javascript"):
        return None
    tree = ctx.tree
    root = (entry.options.get("root") if entry is not None else None) or ""
    candidates: list[dict[str, Any]] = []
    for folder in ("", root) if root else ("",):
        for name in ("tsconfig.json", "jsconfig.json"):
            info = _alias_config(tree, posixpath.join(folder, name))
            if info is not None and _aliasing(info):
                candidates.append(info)
    if not candidates:
        return None

    def score(info: dict[str, Any]) -> tuple[int, int, int]:
        return (1 if not info["error"] and info["ok"] == info["targets"] else 0, info["ok"], -candidates.index(info))

    ranked = sorted(candidates, key=score, reverse=True)
    best = ranked[0]
    path = best["path"]
    notes: list[Note] = []
    if best["error"]:
        notes.append(Note(f"{path} exists but could not be read ({best['error']}); it is named anyway because analysis reads it and says so", (ev(path),), "low"))
    else:
        if best["has_paths"]:
            what = f"{path} declares {best['targets']} path target(s)" + (", every one of which exists in the tree" if best["ok"] == best["targets"] else f", but only {best['ok']} exist in the tree")
        else:
            what = f"{path} sets a baseUrl" if best["has_base"] else f"{path} extends a configuration that is not in the repository"
        notes.append(Note(what, (best["where"],), "high" if best["ok"] == best["targets"] and (best["has_paths"] or best["has_base"]) else "low"))
        if best["extends_package"] and best["has_paths"]:
            notes.append(Note("it also extends a configuration that is not in the repository, so aliases set there are not seen", (ev(path),), "low"))
    for other in ranked[1:]:
        bad = other["targets"] - other["ok"]
        reason = (f"{bad} of its {other['targets']} path targets do not exist in the tree" if bad else f"{other['error']}" if other["error"] else "another config with the same job ranks above it")
        notes.append(Note(f"{other['path']} is not used: {reason}", (other["where"],), "medium"))
    extra = build_tool_alias_note(tree)
    if extra:
        name, _, rest = extra.partition(" sets aliases")
        notes.append(Note(f"{name.rsplit(':', 1)[0]} also sets aliases, but Reachmap reads only JSON configs (tsconfig.json, jsconfig.json): compare the two", (name,), "low"))
    return Guess({"from": path}, tuple(notes))


# ------------------------------------------------------------------------------------------------ tests

_JS_TEST_DIRS = ("__tests__", "tests", "test")
_PY_TEST_DIRS = ("tests", "test")


def detect_tests(ctx: Ctx) -> tuple[Guess | None, Guess | None]:
    tree = ctx.tree
    suffixes = JS_SUFFIXES if ctx.language in ("typescript", "javascript") else PY_SUFFIXES if ctx.language == "python" else (".java", ".kt", ".kts")
    code = [f for f in tree.files if f.endswith(suffixes)]
    markers: list[str] = []
    evidence: list[str] = []

    def add(marker: str, files: list[str]) -> None:
        if files and marker not in markers:
            markers.append(marker)
            evidence.append(ev(files[0]))

    if ctx.language in ("typescript", "javascript"):
        for d in _JS_TEST_DIRS:
            add(d + "/", [f for f in code if d in f.split("/")[:-1]])
        add(".test.", [f for f in code if re.search(r"\.test\.[A-Za-z]+$", f)])
        add(".spec.", [f for f in code if re.search(r"\.spec\.[A-Za-z]+$", f)])
    elif ctx.language in ("java", "kotlin"):
        add("src/test/", [f for f in code if "src/test/" in "/" + f])  # the Maven and Gradle layout: a folder pair, matched as consecutive folders
    else:
        for d in _PY_TEST_DIRS:
            add(d + "/", [f for f in code if d in f.split("/")[:-1]])
        add("test_", [f for f in code if f.rsplit("/", 1)[-1].startswith("test_")])
        add("_test", [f for f in code if re.search(r"[^/]_test\.[A-Za-z]+$", f)])
    markers_guess = None
    if markers:
        testpaths.validate_markers(markers)
        markers_guess = Guess(markers, (Note(f"test files by name and folder: {', '.join(markers)}", tuple(evidence[:3]), "high"),))
    return markers_guess, detect_test_command(ctx, code)


def detect_test_command(ctx: Ctx, code: list[str]) -> Guess | None:
    tree = ctx.tree
    if ctx.language in ("typescript", "javascript"):
        for path, data, text in package_jsons(ctx):
            script = (data.get("scripts") or {}).get("test") if isinstance(data.get("scripts"), dict) else None
            if not isinstance(script, str):
                continue
            at = ev(path, line_of(text, re.compile(r'"test"\s*:')))
            for tool, command in (("vitest", "npx vitest run {paths}"), ("jest", "npx jest {paths}"), ("mocha", "npx mocha {paths}")):
                if re.search(r"\b" + tool + r"\b", script):
                    return Guess(command, (Note(f"the test script of {path} runs {tool}", (at,), "high"),))
        return None
    if ctx.language == "python":
        for name, label in (("pytest.ini", None), ("tox.ini", "[pytest]"), ("setup.cfg", "[tool:pytest]")):
            for path in tree.named(name):
                text = tree.text(path) or ""
                if label is None or label in text:
                    return Guess("pytest {paths}", (Note("pytest is configured", (ev(path, line_of(text, label) if label else 1),), "high"),))
        for path, _data, text in pyprojects(ctx):
            if "[tool.pytest" in text:
                return Guess("pytest {paths}", (Note("pytest is configured in pyproject.toml", (ev(path, line_of(text, "[tool.pytest")),), "high"),))
        for path in tree.named("conftest.py"):
            return Guess("pytest {paths}", (Note("a conftest.py is pytest's", (ev(path),), "high"),))
        tests = [f for f in code if is_test(f)]
        unit = None
        for path in tests:
            text = tree.text(path) or ""
            if re.search(r"^[ \t]*(?:import pytest|from pytest\b)", text, re.MULTILINE):
                return Guess("pytest {paths}", (Note("a test file imports pytest", (ev(path, line_of(text, "pytest")),), "high"),))
            if re.search(r"unittest\.TestCase|^[ \t]*import unittest|^[ \t]*from unittest\b", text, re.MULTILINE):
                unit = unit or ev(path, line_of(text, "unittest"))
        if unit:
            return Guess("python -m unittest {paths}", (Note("test files use unittest", (unit,), "medium"),))
        for path in tests:
            text = tree.text(path) or ""
            if re.search(r"^def test_\w+\(", text, re.MULTILINE):
                return Guess("pytest {paths}", (Note("module level test_ functions with bare asserts are pytest's style; no runner is configured", (ev(path, line_of(text, re.compile(r"^def test_", re.MULTILINE))),), "medium"),))
        return None
    if ctx.language in ("java", "kotlin"):
        for path in tree.named("pom.xml"):
            return Guess("mvn test", (Note("Maven project", (ev(path),), "medium"),))
        for name in ("build.gradle.kts", "build.gradle"):
            for path in tree.named(name):
                return Guess("./gradlew test", (Note("Gradle project", (ev(path),), "medium"),))
    return None


# ------------------------------------------------------------------------------------------------ monorepos


def _marker_dirs(ctx: Ctx, marker: str) -> list[str]:
    return sorted(posixpath.dirname(p) for p in ctx.tree.named(marker) if posixpath.dirname(p))


def detect_service_marker(ctx: Ctx) -> Guess | None:
    marker = "pyproject.toml" if ctx.language == "python" else "package.json" if ctx.language in ("typescript", "javascript") else None
    if marker is None:
        return None
    below = _marker_dirs(ctx, marker)
    if len(below) < 2:
        return None
    shown = ", ".join(ev(posixpath.join(d, marker)) for d in below[:3])
    why = f"{len(below)} folders below the root hold their own {marker}: a monorepo of services"
    if ctx.language != "python":
        why += " (only the Python adapter reads service_marker today)"
    return Guess(marker, (Note(why, tuple(ev(posixpath.join(d, marker)) for d in below[:3]), "medium"),))


_SERVER_CODE = re.compile(r"\bFastAPI\s*\(|\bAPIRouter\s*\(|^urlpatterns\s*=|\.task\b|@shared_task", re.MULTILINE)


def detect_library_roots(ctx: Ctx) -> Guess | None:
    tree = ctx.tree
    dirs = _marker_dirs(ctx, "pyproject.toml")
    if len(dirs) < 2:
        return None
    py = [f for f in tree.files if f.endswith(".py")]
    roots: list[str] = []
    notes: list[Note] = []
    for d in dirs:
        inside = [f for f in py if f.startswith(d + "/")]
        packages: set[str] = set()
        for f in inside:
            rel = f[len(d) + 1:]
            if rel.startswith("src/"):
                rel = rel[4:]
            top = rel.split("/")[0]
            name = top[:-3] if top.endswith(".py") else top
            if re.match(r"^[A-Za-z_]\w*$", name) and (top != "tests" and not is_test(f)):
                packages.add(name)
        if not packages:
            continue
        if any(_SERVER_CODE.search(tree.text(f) or "") for f in inside if is_source(f)):
            continue  # a service, not a library
        pattern = re.compile(r"^[ \t]*(?:from|import)[ \t]+(?:" + "|".join(re.escape(p) for p in sorted(packages)) + r")(?=[.\s,]|$)", re.MULTILINE)
        for f in py:
            if f.startswith(d + "/"):
                continue
            text = tree.text(f)
            m = pattern.search(text or "")
            if m:
                roots.append(d)
                notes.append(Note(f"{d} has its own pyproject.toml, no routes or tasks, and its package is imported from outside it", (ev(f, line_of(text, m.group(0).strip())),), "high"))
                break
    if not roots:
        return None
    return Guess(roots, tuple(notes))


# ------------------------------------------------------------------------------------------------ end to end specs

_E2E = (
    ("playwright", re.compile(r"\bpage\.goto\(\s*[\"'`]"), ("playwright.config.ts", "playwright.config.js", "playwright.config.mjs"), "@playwright/test"),
    ("cypress", re.compile(r"\bcy\.visit\(\s*[\"'`]"), ("cypress.config.ts", "cypress.config.js", "cypress.config.mjs"), "cypress"),
)


def detect_e2e(ctx: Ctx) -> tuple[str, str] | None:
    if ctx.language not in ("typescript", "javascript"):
        return None
    deps: set[str] = set()
    for _path, data, _text in package_jsons(ctx):
        deps |= set(dep_names(data))
    for framework, visit, configs, package in _E2E:
        configured = package in deps or any(ctx.tree.named(c) for c in configs)
        if not configured:
            continue
        for path, text in ctx.tree.scan(JS_SUFFIXES, skip=lambda p: not is_test(p)):
            m = visit.search(text)
            if m:
                return framework, ev(path, line_of(text, m.group(0)))
    return None


# ------------------------------------------------------------------------------------------------ roles


def detect_roles(ctx: Ctx, entry: Found | None, server: Found | None, client: Found | None, contract_found: Found | None, unread: int = 0) -> Guess:
    roles: list[str] = []
    notes: list[Note] = []
    if entry is not None:
        roles += list(entry.roles)
        notes.append(Note(f"{', '.join(entry.roles)}: the {entry.kind} entry points", entry.notes[0].evidence, entry.notes[0].confidence))
    if server is not None:
        roles.append("backend")
        notes.append(Note("backend: serves the routes of a committed OpenAPI spec", server.notes[0].evidence, server.notes[0].confidence))
    if client is not None and "frontend" not in roles:
        roles.append("frontend")
        notes.append(Note("frontend: it calls an HTTP API through a client", client.notes[0].evidence, "medium"))
    if "frontend" not in roles:
        for path, data, text in package_jsons(ctx):
            hit = next((p for p in FRONTEND_PACKAGES if p in dep_names(data)), None)
            if hit:
                roles.append("frontend")
                notes.append(Note(f"frontend: {hit} is a dependency, but no route table was found", (ev(path, line_of_key(text, hit)),), "low"))
                break
    if contract_found is not None:
        roles.append("contract")
        notes.append(Note("contract: its Pydantic models are concentrated in one folder and it serves no routes", contract_found.notes[0].evidence, contract_found.notes[0].confidence))
    if not roles:
        roles.append("library")
        seen = f"in the files that could be read ({unread} could not be read)" if unread else "in the files that were read"
        notes.append(Note(f"no entry points, routes, client or models were found {seen}, so it is taken as a library", (), "low"))
    order = ("frontend", "backend", "contract", "library", "worker")
    return Guess(sorted(dict.fromkeys(roles), key=order.index), tuple(notes))
