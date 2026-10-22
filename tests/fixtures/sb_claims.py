"""An independent reading of the Spring fixtures: the routes of the controllers, the files that import or use a class, the definitions of a name
and the span of a member, written apart from the adapter (it does not import `reachmap.adapters`) so that `test_sb_fixtures.py` can check the
hand-written expected.json against something that is not the code under test.

It understands the shapes these fixtures use (annotations on their own lines with their arguments on one line, one top-level type per file,
`static final String` and `const val` constants, `application.properties`), not Java or Spring. It reads text with regular expressions after
blanking comments and string literals; the adapter reads tokens.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path

from tests.fixtures.claims import git_out
from tests.fixtures.claims import text_at as _text_at

CODE_SUFFIXES = (".java", ".kt")
MAPPING = re.compile(r"^\s*@(Get|Post|Put|Delete|Patch|Request)Mapping(?:\((.*)\))?\s*$")
VERBS = {"Get": "GET", "Post": "POST", "Put": "PUT", "Delete": "DELETE", "Patch": "PATCH"}
_BLANK = re.compile(r'//[^\n]*|/\*.*?\*/|"""(?:.|\n)*?"""|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'', re.S)


@functools.lru_cache(maxsize=None)
def _tree(repo: str, rev: str) -> tuple[str, ...]:
    return tuple(git_out(Path(repo), "ls-tree", "-r", "--name-only", rev).splitlines())


@functools.lru_cache(maxsize=None)
def _text(repo: str, rev: str, path: str) -> str | None:
    return _text_at(Path(repo), rev, path)


def text_at(repo: Path, rev: str, path: str) -> str | None:
    """The text of a file at a revision (kept: a revision of a fixture never changes)."""
    return _text(str(repo), rev, path)


def tree(repo: Path, rev: str) -> list[str]:
    return list(_tree(str(repo), rev))


def code_files(repo: Path, rev: str, tests: bool = False) -> list[str]:
    return [f for f in tree(repo, rev) if f.endswith(CODE_SUFFIXES) and (tests or "/src/test/" not in "/" + f)]


def strip_code(text: str) -> str:
    """The text with comments and string literals blanked (the newlines kept), so a name is found only where it is code."""
    return _BLANK.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), text)


def package_of(text: str) -> str:
    m = re.search(r"^package\s+([\w.]+)", text, re.M)
    return m.group(1) if m else ""


def declared_types(code: str) -> list[str]:
    """The top-level types and, in a Kotlin file, the top-level functions and properties (not indented)."""
    out = re.findall(r"^(?:(?:public|final|abstract|open|data|sealed|internal)\s+)*(?:class|interface|enum|record|object)\s+(\w+)", code, re.M)
    out += re.findall(r"^(?:(?:public|private|internal|const|inline)\s+)*(?:fun|val|var)\s+(?:<[^>]*>\s*)?(\w+)", code, re.M)
    return out


# ------------------------------------------------------------------------------ the routes


def properties(repo: Path, rev: str) -> dict[str, tuple[str, int, str]]:
    """key to (value, line, file) of the non-test application.properties files."""
    out: dict[str, tuple[str, int, str]] = {}
    for f in tree(repo, rev):
        if re.search(r"(^|/)application\.properties$", f) and "/src/test/" not in "/" + f:
            for n, line in enumerate((text_at(repo, rev, f) or "").splitlines(), 1):
                if line.strip() and not line.lstrip().startswith(("#", "!")) and "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip()] = (v.strip(), n, f)
    return out


def constants(repo: Path, rev: str) -> dict[str, tuple[str, str, int]]:
    """`Class.NAME` and `NAME` to (expression, file, line) for `static final String` (Java) and `const val` (Kotlin)."""
    out: dict[str, tuple[str, str, int]] = {}
    for f in code_files(repo, rev):
        text = text_at(repo, rev, f) or ""
        cls = Path(f).stem
        for n, line in enumerate(text.splitlines(), 1):
            m = re.match(r"^\s*(?:public\s+)?static\s+final\s+String\s+(\w+)\s*=\s*(.*);\s*$", line) or re.match(r"^\s*(?:public\s+)?const\s+val\s+(\w+)\s*=\s*(.*?)\s*$", line)
            if m:
                out[f"{cls}.{m.group(1)}"] = (m.group(2), f, n)
                out.setdefault(m.group(1), (m.group(2), f, n))
    return out


def evaluate(expr: str, consts: dict, props: dict, used: list[tuple[str, int]], depth: int = 0) -> str | None:
    """The value of a path expression: strings, `+`, constants and `${key}`; None when it cannot be told."""
    if depth > 6:
        return None
    parts, buf, quote = [], "", False
    for c in expr:
        if c == '"':
            quote = not quote
        if c == "+" and not quote:
            parts.append(buf)
            buf = ""
        else:
            buf += c
    parts.append(buf)
    out = ""
    for p in parts:
        p = p.strip()
        m = re.fullmatch(r'"(.*)"', p)
        if m:
            text = m.group(1)
            for key in re.findall(r"\$\{([^}:]+)\}", text):
                if key not in props:
                    return None
                used.append((props[key][2], props[key][1]))
                text = text.replace("${" + key + "}", props[key][0])
            out += text
        elif p in consts:
            sub, f, n = consts[p]
            used.append((f, n))
            got = evaluate(sub, consts, props, used, depth + 1)
            if got is None:
                return None
            out += got
        else:
            return None
    return out


def _args(text: str) -> list[str]:
    parts, buf, depth, quote = [], "", 0, False
    for c in text:
        if c == '"':
            quote = not quote
        if not quote and c in "({[":
            depth += 1
        if not quote and c in ")}]":
            depth -= 1
        if c == "," and depth == 0 and not quote:
            parts.append(buf)
            buf = ""
        else:
            buf += c
    if buf.strip():
        parts.append(buf)
    return [p.strip() for p in parts]


def _path_and_methods(args: str | None, verb: str, consts: dict, props: dict, used: list) -> tuple[str, list[str]] | None:
    path, methods = "", []
    for a in _args(args or ""):
        m = re.match(r"^(?:value|path)\s*=\s*(.*)$", a)
        if m:
            path = evaluate(m.group(1), consts, props, used) or ""
            if path is None:
                return None
        elif a.startswith("method"):
            methods = re.findall(r"RequestMethod\.(\w+)", a)
        elif not re.match(r"^\w+\s*=", a):
            value = evaluate(a, consts, props, used)
            if value is None:
                return None
            path = value
    if verb != "Request":
        methods = [VERBS[verb]]
    return path, methods or ["ANY"]


def routes(repo: Path, rev: str) -> dict[tuple[str, str], str]:
    """(method, full path) to the file, for the controllers of the repository (a class with @RestController): the context path of the
    application properties, the class level @RequestMapping and the method level mappings, joined with slashes."""
    consts, props = constants(repo, rev), properties(repo, rev)
    ctx = props.get("server.servlet.context-path", ("", 0, ""))[0]
    out: dict[tuple[str, str], str] = {}
    for f in code_files(repo, rev):
        text = text_at(repo, rev, f) or ""
        if "@RestController" not in strip_code(text):
            continue
        lines = text.splitlines()
        cls_idx = next(i for i, line in enumerate(lines) if re.match(r"^\s*(?:public\s+)?(?:class)\s+\w+", line))
        base = ""
        for line in lines[:cls_idx]:
            m = MAPPING.match(line)
            if m and m.group(1) == "Request":
                got = _path_and_methods(m.group(2), "Request", consts, props, [])
                assert got is not None, (f, line)
                base = got[0]
        for line in lines[cls_idx:]:
            m = MAPPING.match(line)
            if not m:
                continue
            got = _path_and_methods(m.group(2), m.group(1), consts, props, [])
            assert got is not None, (f, line)
            path, methods = got
            full = "/".join(p.strip("/") for p in (ctx, base, path) if p.strip("/"))
            for method in methods:
                out[(method, "/" + full)] = f
    return out


# ------------------------------------------------------------------------------ importers and definitions


def importers(repo: Path, rev: str, target: str) -> set[str]:
    """The non-test files that use a class (or a Kotlin top-level function) of `target`: an import of its package and name (a static import or
    a nested type too), or a reference to its name from the same package, in code (comments and strings are blanked first)."""
    text = text_at(repo, rev, target) or ""
    code = strip_code(text)
    pkg = package_of(text)
    names = declared_types(code)
    found: set[str] = set()
    for f in code_files(repo, rev, tests=True):
        if f == target:
            continue
        other = strip_code(text_at(repo, rev, f) or "")
        if not any(re.search(rf"\b{re.escape(n)}\b", other) for n in names):
            continue
        for n in names:
            fq = f"{pkg}.{n}" if pkg else n
            if re.search(rf"^import\s+(?:static\s+)?{re.escape(fq)}(?:\.\w+)?(?:\s+as\s+\w+)?\s*;?\s*$", text_at(repo, rev, f) or "", re.M):
                found.add(f)
        if package_of(text_at(repo, rev, f) or "") == pkg and any(re.search(rf"\b{re.escape(n)}\b", re.sub(r"^(?:import|package)\b.*$", "", other, flags=re.M)) for n in names):
            found.add(f)
    return found


def definitions(repo: Path, rev: str, name: str) -> int:
    """How many times non-test code defines `name`: a type, a method or a function, a field or a property (a declaration, not a call)."""
    n = 0
    for f in code_files(repo, rev):
        for line in strip_code(text_at(repo, rev, f) or "").splitlines():
            if re.match(rf"^\s*(?:(?:public|private|protected|static|final|abstract|override|open|internal)\s+)*(?:class|interface|enum|record|object|fun|val|var)\s+(?:<[^>]*>\s*)?{re.escape(name)}\b", line):
                n += 1
            elif re.match(rf"^\s*(?:(?:public|private|protected|static|final|abstract)\s+)+[\w<>\[\],.? ]+\s+{re.escape(name)}\s*[(=;]", line):
                n += 1
    return n


def member_span(text: str, member: str) -> tuple[int, int] | None:
    """The lines of the first method or field called `member` in a Java or Kotlin file (annotations above it included), by counting braces."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.match(rf"^\s*(?:(?:public|private|protected|static|final|abstract|override)\s+)*(?:fun\s+|[\w<>\[\],.?]+\s+){re.escape(member)}\s*[(=]", line) and not line.strip().startswith(("return", "new ")):
            start = i
            while start > 0 and lines[start - 1].strip().startswith("@"):
                start -= 1
            depth, end = 0, i
            for j in range(i, len(lines)):
                code = strip_code(lines[j])
                depth += code.count("{") - code.count("}")
                end = j
                if depth <= 0 and ("{" in code or ";" in code or j > i or "=" in code):
                    break
            return start + 1, end + 1
    return None


def unlexable(text: str) -> bool:
    """A line that opens a string and does not close it (an odd number of unescaped quotes after the comments are blanked)."""
    for line in re.sub(r"/\*.*?\*/", "", text, flags=re.S).splitlines():
        stripped = re.sub(r"//.*$", "", line)
        if len(re.findall(r'(?<!\\)"', stripped)) % 2:
            return True
    return False
