"""The contract detector: a repository whose Pydantic models sit in one folder, the package they are imported by, and the tags
that number their releases.

Classes inheriting `BaseModel` (directly, as `pydantic.BaseModel`, or through another class of the repository that does) are
counted per folder. The models folder is the deepest folder that holds at least 80 percent of them; models spread over the whole
repository are no contract, and a `models/` folder with no `BaseModel` in it is not one either.
"""

from __future__ import annotations

import re
from typing import Any

from reachmap.init import registry
from reachmap.init.common import PY_SUFFIXES, dig, ev, is_source, pyprojects
from reachmap.init.guess import Note
from reachmap.init.registry import Ctx, Found
from reachmap.init.tree import code_line, line_of

__all__ = ["import_name", "model_classes", "tag_prefix"]

_CLASS = re.compile(r"^class[ \t]+(\w+)[ \t]*\(([^)]*)\)[ \t]*:", re.MULTILINE)
_PYDANTIC = re.compile(r"^[ \t]*(?:from[ \t]+pydantic\b|import[ \t]+pydantic\b)", re.MULTILINE)
_TAG = re.compile(r"^(?P<prefix>[^0-9]*?)(?P<version>\d+(?:\.\d+)*(?:[-+.]?[A-Za-z0-9.]+)?)$")


def model_classes(ctx: Ctx) -> dict[str, list[tuple[str, int, str]]]:
    """Folder to the (path, line, class name) of the BaseModel classes in it (the files directly in the folder)."""
    local_models: set[str] = set()
    parsed: list[tuple[str, str, list[tuple[str, int, str, list[str]]]]] = []
    for path, text in ctx.tree.scan(PY_SUFFIXES, skip=lambda p: not is_source(p)):
        if not _PYDANTIC.search(text):
            continue
        classes = []
        lines = text.splitlines()
        for m in _CLASS.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            if code_line(lines[line - 1]):
                bases = [b.strip().split("[")[0].split(".")[-1] for b in m.group(2).split(",") if b.strip()]
                classes.append((m.group(1), line, bases))
        parsed.append((path, text, [(path, ln, name, bases) for name, ln, bases in classes]))
    changed = True
    while changed:  # a class derives from BaseModel directly or through a model of the repository
        changed = False
        for _path, _text, classes in parsed:
            for _p, _ln, name, bases in classes:
                if name not in local_models and any(b == "BaseModel" or b in local_models for b in bases):
                    local_models.add(name)
                    changed = True
    out: dict[str, list[tuple[str, int, str]]] = {}
    for path, _text, classes in parsed:
        for p, ln, name, _bases in classes:
            if name in local_models:
                out.setdefault(p.rsplit("/", 1)[0] if "/" in p else "", []).append((p, ln, name))
    return out


def _models_root(by_dir: dict[str, list[tuple[str, int, str]]]) -> tuple[str, int] | None:
    total = sum(len(v) for v in by_dir.values())
    if total < 2:
        return None
    counts: dict[str, int] = {}
    for d, items in by_dir.items():
        parts = d.split("/") if d else []
        for i in range(len(parts) + 1):
            key = "/".join(parts[:i])
            counts[key] = counts.get(key, 0) + len(items)
    need = -(-total * 8 // 10)
    ok = [(len(d.split("/")) if d else 0, c, d) for d, c in counts.items() if c >= need]
    if not ok:
        return None
    depth, count, folder = max(ok)
    return None if folder == "" else (folder, count)


def import_name(ctx: Ctx, root: str, only: str | None = None) -> tuple[str, str, str] | None:
    """The name code imports the package by, with the evidence and why: setuptools `package-dir`, a poetry or hatch package list,
    the project name when a folder of that name is a package, else the first package folder on the path to the models. (name, evidence, why).
    `only` limits the search to one pyproject.toml."""
    for path, data, text in pyprojects(ctx):
        if only is not None and path != only:
            continue
        folder = path.rsplit("/", 1)[0] if "/" in path else ""
        pd = dig(data, "tool", "setuptools").get("package-dir")
        if isinstance(pd, dict):
            for key, target in pd.items():
                if key and re.match(r"^[A-Za-z_]\w*$", key):
                    inside = (folder + "/" if folder else "") + str(target).strip("/")
                    if root == inside or root.startswith(inside + "/") or not root:
                        return key, ev(path, line_of(text, re.compile(r"^[ \t]*package-dir", re.MULTILINE))), "package-dir maps the import name to the source folder"
        poetry = dig(data, "tool", "poetry").get("packages")
        if isinstance(poetry, list) and poetry and isinstance(poetry[0], dict) and re.match(r"^[A-Za-z_]\w*$", str(poetry[0].get("include", ""))):
            return str(poetry[0]["include"]), ev(path, line_of(text, "packages")), "the first package of the poetry packages list"
        name = dig(data, "project").get("name") or dig(data, "tool", "poetry").get("name")
        if isinstance(name, str):
            norm = re.sub(r"[-.]+", "_", name).lower()
            for candidate in ((folder + "/" if folder else "") + norm, (folder + "/" if folder else "") + "src/" + norm):
                if ctx.tree.has(candidate + "/__init__.py"):
                    return norm, ev(path, line_of(text, name)), "the project name, with a package folder of that name"
    parts = root.split("/")
    for i in range(1, len(parts) + 1):
        folder = "/".join(parts[:i])
        if ctx.tree.has(folder + "/__init__.py") and re.match(r"^[A-Za-z_]\w*$", parts[i - 1]):
            return parts[i - 1], ev(folder + "/__init__.py"), "the first package folder on the path to the models"
    return None


def tag_prefix(tags: tuple[str, ...]) -> tuple[str | None, str]:
    """The prefix every tag shares before its version (`v` for v1.2.0), None when the tags are bare numbers. (prefix, text); the
    prefix is None and the text says why when the tags share none or none looks like a version."""
    versions = [(m.group("prefix"), t) for t in tags if (m := _TAG.match(t))]
    if not versions:
        return None, "no tag looks like a version"
    prefixes = {p for p, _ in versions}
    if len(prefixes) != 1:
        counts: dict[str, int] = {}
        for p, _ in versions:
            counts[p] = counts.get(p, 0) + 1
        top, n = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
        if n * 2 <= len(versions):
            return None, "the tags do not share one prefix"
        return top, f"{n} of {len(versions)} version tags start with it"
    return next(iter(prefixes)), f"all {len(versions)} version tags start with it"


def detect_pydantic(ctx: Ctx) -> list[Found]:
    by_dir = model_classes(ctx)
    ctx.facts.has_pydantic_models = bool(by_dir)
    chosen = _models_root(by_dir)
    if chosen is None:
        return []
    root, count = chosen
    inside = [i for d, items in by_dir.items() if d == root or d.startswith(root + "/") for i in items]
    total = sum(len(v) for v in by_dir.values())
    options: dict[str, Any] = {}
    notes = [Note(f"{count} of {total} Pydantic model classes (BaseModel) are under {root}", tuple(ev(p, n) for p, n, _ in inside[:3]), "high" if count == total else "medium")]
    pkg = import_name(ctx, root)
    if pkg is not None:
        options["package"] = pkg[0]
        notes.append(Note(f"package {pkg[0]}: {pkg[2]}", (pkg[1],), "high" if "package-dir" in pkg[2] else "medium"))
    options["versions"] = "git-tags"
    extra: list[str] = []
    tags = ctx.tag_names()
    if tags:
        prefix, why = tag_prefix(tags)
        if prefix:
            options["tag_prefix"] = prefix
            shown = ", ".join(sorted(tags)[:3])
            notes.append(Note(f"tag_prefix {prefix!r}: {why}", (f"tags {shown}",), "medium"))
        elif prefix == "":
            extra.append("tag_prefix left out: the tags are bare version numbers, which are read as they are; confirm")
        else:
            extra.append(f"tag_prefix left out: {why}; without it a tag with or without a leading v is read as a version; confirm")
    else:
        extra.append("tag_prefix left out: the repository has no tags, so no change can be told released until a version is tagged; confirm")
    ordered = {"root": root, **options}
    return [Found("pydantic", ordered, ("contract",), tuple(notes), score=count, extra_notes=tuple(extra))]


def register_all() -> None:
    registry.register("contract", "pydantic", detect_pydantic, languages=("python",))


register_all()
