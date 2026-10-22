"""What a repository provides and what it depends on: the facts the edge guesses are made of.

`provides` maps `pip:` or `npm:` and the normalised name of every package a manifest of the repository declares (pyproject.toml `project.name` or
`tool.poetry.name`, package.json `name`) to the name code imports it by and where that is written. `deps` lists every dependency
the manifests name (requirements*.txt, pyproject.toml, package.json), each with its line, so that a pin pattern can be written
for the exact line and checked against it.
"""

from __future__ import annotations

import re
from typing import Any

from reachmap.init.common import JS_SUFFIXES, dig, ev, package_jsons, pyprojects
from reachmap.init.contract import import_name
from reachmap.init.guess import Dep
from reachmap.init.registry import Ctx
from reachmap.init.tree import line_of

__all__ = ["normalise", "read_manifests"]

_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(===|==|~=|>=|<=|!=|>|<)?\s*([^\s;#,]+)?")
_PEP508 = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(===|==|~=|>=|<=|!=|>|<)?\s*([^\s;#,]+)?")
_NPM_VERSION = re.compile(r"^[\^~=v]*\d")


def normalise(name: str, ecosystem: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower() if ecosystem == "pip" else name


def _requirement_files(ctx: Ctx) -> list[str]:
    out = []
    for path in ctx.tree.files:
        name = path.rsplit("/", 1)[-1]
        if re.match(r"^requirements[\w.-]*\.txt$", name) or (name.endswith(".txt") and "/requirements/" in "/" + path):
            out.append(path)
    return out


def read_manifests(ctx: Ctx) -> None:
    deps, provides = ctx.facts.deps, ctx.facts.provides
    for path in _requirement_files(ctx):
        text = ctx.tree.text(path)
        for n, line in enumerate((text or "").splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", "-", "git+", "http")):
                continue
            m = _REQ_LINE.match(line)
            if m:
                pinned = m.group(3) in ("==", "===") and bool(m.group(4)) and "*" not in m.group(4)
                deps.append(Dep("pip", normalise(m.group(1), "pip"), path, n, line.rstrip("\r"), pinned, m.group(1)))
    for path, data, text in pyprojects(ctx):
        lines = text.splitlines()
        project = dig(data, "project")
        poetry = dig(data, "tool", "poetry")
        name = project.get("name") or poetry.get("name")
        if isinstance(name, str) and name:
            folder = path.rsplit("/", 1)[0] if "/" in path else ""
            guess = import_name(ctx, folder, only=path)
            norm_import = re.sub(r"[-.]+", "_", name).lower()
            if guess is not None and "package-dir" in guess[2]:
                provides["pip:" + normalise(name, "pip")] = (guess[0], guess[1], guess[2])
            else:
                provides["pip:" + normalise(name, "pip")] = (norm_import, ev(path, line_of(text, name)), "the project name, as an import name")
        strings: list[str] = []
        if isinstance(project.get("dependencies"), list):
            strings += [s for s in project["dependencies"] if isinstance(s, str)]
        for group in dig(project, "optional-dependencies").values():
            if isinstance(group, list):
                strings += [s for s in group if isinstance(s, str)]
        for group in dig(data, "dependency-groups").values():
            if isinstance(group, list):
                strings += [s for s in group if isinstance(s, str)]
        for s in strings:
            m = _PEP508.match(s)
            at = line_of(text, s)
            if m and at:
                pinned = m.group(3) in ("==", "===") and bool(m.group(4)) and "*" not in m.group(4)
                deps.append(Dep("pip", normalise(m.group(1), "pip"), path, at, lines[at - 1], pinned, m.group(1)))
        for section in ("dependencies", "dev-dependencies"):
            table = poetry.get(section)
            if isinstance(table, dict):
                for dep_name, spec in table.items():
                    if dep_name.lower() == "python":
                        continue
                    at = line_of(text, re.compile(r"^[ \t]*" + re.escape(dep_name) + r"[ \t]*=", re.MULTILINE))
                    if at:
                        simple = isinstance(spec, str)
                        pinned = simple and bool(re.match(r"^[\^~=]*\d", spec)) and "*" not in spec
                        deps.append(Dep("pip", normalise(dep_name, "pip"), path, at, lines[at - 1], pinned, dep_name))
    for path, data, text in package_jsons(ctx):
        name = data.get("name")
        lines = text.splitlines()
        if isinstance(name, str) and name:
            provides["npm:" + normalise(name, "npm")] = (name, ev(path, line_of(text, '"name"')), "the package name in package.json")
        for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            table = data.get(section)
            if isinstance(table, dict):
                for dep_name, version in table.items():
                    at = line_of(text, re.compile(r'"' + re.escape(dep_name) + r'"\s*:'))
                    if at:
                        pinned = isinstance(version, str) and bool(_NPM_VERSION.match(version))
                        deps.append(Dep("npm", dep_name, path, at, lines[at - 1], pinned, dep_name))


_PY_IMPORT = re.compile(r"^[ \t]*(?:from[ \t]+([A-Za-z_]\w*)|import[ \t]+([A-Za-z_]\w*))", re.MULTILINE)
_JS_IMPORT = re.compile(r"""(?:\bfrom|\bimport|\brequire\()\s*["'](@[\w.-]+/[\w.-]+|[A-Za-z][\w.-]*)""")


def read_imports(ctx: Ctx) -> None:
    """The top level names the repository's code imports (a Python module, an npm package), each with the first line that does."""
    imports = ctx.facts.imports
    py = ctx.language == "python"
    for path, text in ctx.tree.scan((".py",) if py else JS_SUFFIXES):
        pattern = _PY_IMPORT if py else _JS_IMPORT
        for m in pattern.finditer(text):
            name = (m.group(1) or m.group(2)) if py else m.group(1)
            if name and name not in imports:
                imports[name] = ev(path, line_of(text, m.group(0).strip(), m.start()))
