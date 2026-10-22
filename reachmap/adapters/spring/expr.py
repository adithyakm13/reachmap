"""The values of the arguments of an annotation: a string, a constant (`Paths.INVOICES`, a static import, a Kotlin `const val`, the same
class or package), a sum of those, an array of them (`{"/a", B}`, `["/a"]`, `arrayOf("/a")`), a Kotlin string template made of constants and
a Spring placeholder `${key}` or `${key:default}` read from the application configuration.

A constant is a field or property of a project class (or a Kotlin top-level property) that is initialised by an expression of the same
kind. Anything else (a method call, a variable, a constant of a library, a placeholder with no value) is unknown: the value is written
`<unknown:SOURCE>` and kept apart, never guessed (the caller marks its route partial and records it). Each constant and each property used
is remembered by the line that gives its value, so a route cites where its path comes from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from reachmap.adapters.jvm.lexer import Token, unescape
from reachmap.adapters.jvm.syntax import Parsed, Unit, skip_angles
from reachmap.adapters.spring.props import substitute

__all__ = ["Evaluator", "Str", "split_items"]

DEPTH = 6  # how far a constant is followed through other constants


@dataclass(frozen=True, slots=True)
class Str:
    """A string made of known text and unknown parts (`unknown`: the expression, the file and the line); `lines` are the (file, line) pairs
    that gave the text (constants, properties); `keys` the property keys a placeholder named."""

    value: str = ""
    unknown: tuple[tuple[str, str, int], ...] = ()
    lines: tuple[tuple[str, int], ...] = ()
    keys: tuple[str, ...] = ()

    def __add__(self, other: "Str") -> "Str":
        return Str(self.value + other.value, self.unknown + other.unknown, self.lines + tuple(x for x in other.lines if x not in self.lines), self.keys + other.keys)

    def __bool__(self) -> bool:
        return bool(self.value)


def split_items(parsed: Parsed, a: int, b: int) -> list[tuple[int, int]]:
    """The code token ranges of the comma separated items in `[a, b)` (a comma inside brackets does not split)."""
    code = parsed.code
    out: list[tuple[int, int]] = []
    start = i = a
    while i <= b:
        if i == b or (code[i].kind == "punct" and code[i].value == ","):
            if i > start:
                out.append((start, i))
            start = i + 1
            i += 1
            continue
        if code[i].kind == "punct" and code[i].value in "([{":
            i = parsed.match.get(i, i) + 1
            continue
        i += 1
    return out


class Evaluator:
    """Evaluates annotation arguments at one commit. `table` provides `language` (the JVM adapter), `sha` and `config_lookup(path)` (the
    property lookup that applies to a file: a key to an Entry or None)."""

    def __init__(self, table: Any) -> None:
        self.table = table
        self.language = table.language
        self.sha = table.sha

    # ---- expressions

    def values(self, path: str, parsed: Parsed, unit: Unit | None, a: int, b: int, depth: int = 0) -> list[Str]:
        """The values of the argument written in the code tokens `[a, b)`: more than one for an array."""
        toks = parsed.code
        if b <= a:
            return []
        first = toks[a]
        # an array: { ... } (Java), [ ... ] (Kotlin), arrayOf(...) / arrayOf<T>(...) / listOf(...)
        if first.kind == "punct" and first.value in "{[" and parsed.match.get(a) == b - 1:
            return self.items(path, parsed, unit, a + 1, b - 1, depth)
        if first.kind == "ident" and first.value in ("arrayOf", "listOf", "setOf") and b - a >= 3:
            lp = a + 1
            if toks[lp].value == "<":
                lp = skip_angles(toks, lp)
            if 0 < lp < b and toks[lp].value == "(" and parsed.match.get(lp) == b - 1:
                return self.items(path, parsed, unit, lp + 1, b - 1, depth)
        parts = self._split_plus(parsed, a, b)
        if len(parts) == 1:
            return self.term(path, parsed, unit, a, b, depth)  # one term may be an array constant
        out = [Str()]
        for x, y in parts:
            got = self.term(path, parsed, unit, x, y, depth)
            if len(got) != 1:
                return [self.unknown(path, parsed, a, b)]
            out = [out[0] + got[0]]
        return out

    def items(self, path: str, parsed: Parsed, unit: Unit | None, a: int, b: int, depth: int) -> list[Str]:
        out: list[Str] = []
        for x, y in split_items(parsed, a, b):
            out += self.values(path, parsed, unit, x, y, depth)
        return out

    @staticmethod
    def _split_plus(parsed: Parsed, a: int, b: int) -> list[tuple[int, int]]:
        parts: list[tuple[int, int]] = []
        start = a
        i = a
        while i < b:
            t = parsed.code[i]
            if t.kind == "punct" and t.value in "([{":
                i = parsed.match.get(i, i) + 1
                continue
            if t.kind == "punct" and t.value == "+" and i > start:
                parts.append((start, i))
                start = i + 1
            i += 1
        parts.append((start, b))
        return parts

    def term(self, path: str, parsed: Parsed, unit: Unit | None, a: int, b: int, depth: int) -> list[Str]:
        toks = parsed.code
        if b <= a:
            return [self.unknown(path, parsed, a, a)]
        first = toks[a]
        if first.kind == "punct" and first.value == "(" and parsed.match.get(a) == b - 1:
            return self.values(path, parsed, unit, a + 1, b - 1, depth)
        if b - a == 1 and first.kind == "string":
            return [self.string(path, parsed, unit, first, depth)]
        # a name or a chain of names: NAME, Class.NAME, pkg.Class.NAME
        chain: list[str] = []
        i = a
        while i < b and toks[i].kind == "ident":
            chain.append(toks[i].value)
            i += 1
            if i + 1 < b and toks[i].value == "." and toks[i + 1].kind == "ident":
                i += 1
                continue
            break
        if chain and i == b:
            return self.constant(path, parsed, unit, chain, a, b, depth)
        return [self.unknown(path, parsed, a, b)]

    def string(self, path: str, parsed: Parsed, unit: Unit | None, tok: Token, depth: int) -> Str:
        lookup = self.table.config_lookup(path)
        if tok.text is not None:
            return self.placeholders(path, tok.text, tok.line, lookup)
        out = Str()
        for kind, text in tok.parts or ():
            if kind == "lit":
                out += self.placeholders(path, unescape(text), tok.line, lookup)
                continue
            chain = text.split(".")
            if all(c.isidentifier() for c in chain):
                got = self.constant(path, parsed, unit, chain, -1, -1, depth, tok.line, "$" + text)
                out += got[0] if len(got) == 1 else Str(f"<unknown:{text}>", ((text, path, tok.line),))
            else:
                out += Str(f"<unknown:{text}>", ((text, path, tok.line),))
        return out

    def placeholders(self, path: str, text: str, line: int, lookup: Any) -> Str:
        if "${" not in text:
            return Str(text)
        keys: list[str] = []
        lines: list[tuple[str, int]] = []

        def look(key: str):  # noqa: ANN202
            entry = lookup(key)
            keys.append(key)
            if entry is not None and not entry.unread:
                if (entry.path, entry.line) not in lines:
                    lines.append((entry.path, entry.line))
            return entry

        value, missing = substitute(text, look)
        unknown = tuple((f"${{{k}}}", path, line) for k in missing)
        return Str(value, unknown, tuple(lines), tuple(keys))

    def unknown(self, path: str, parsed: Parsed, a: int, b: int) -> Str:
        if b > a:
            src = " ".join(parsed.source(a, b - 1).split())
            line = parsed.code[a].line
        else:
            src, line = "?", parsed.code[a].line if a < len(parsed.code) else 1
        return Str(f"<unknown:{src}>", ((src, path, line),))

    # ---- constants

    def constant(self, path: str, parsed: Parsed, unit: Unit | None, chain: list[str], a: int, b: int, depth: int, line: int = 0, src: str = "") -> list[Str]:
        """The value of a constant (more than one for an array constant); unknown when it is not one of the project."""
        text = src or ".".join(chain)
        where = parsed.code[a].line if a >= 0 and a < len(parsed.code) else line
        nothing = [Str(f"<unknown:{text}>", ((text, path, where),))]
        if depth >= DEPTH:
            return nothing
        found = self.find_constant(path, parsed, unit, chain)
        if found is None:
            return nothing
        cpath, cparsed, cunit, name = found
        rng = self.initializer(cparsed, cunit, name)
        if rng is None:
            return nothing
        got = self.values(cpath, cparsed, cunit, rng[0], rng[1], depth + 1)
        if not got:
            return nothing
        mark = Str("", (), ((cpath, cunit.name_line),))
        return [mark + g for g in got]  # the line that gives the value first, then what it was made of

    def find_constant(self, path: str, parsed: Parsed, unit: Unit | None, chain: list[str]) -> tuple[str, Parsed, Unit, str] | None:
        """The declaration of a constant written as `chain` in `path`, with its name: (file, parsed, unit, name)."""
        name = chain[-1]
        lang, sha = self.language, self.sha
        if len(chain) == 1:
            u = unit
            while u is not None:  # the members of the types around the annotation
                if u.is_type:
                    hit = self._member(u, name)
                    if hit is not None:
                        return path, parsed, hit, name
                u = u.parent
            top = next((d for d in parsed.declarations if not d.is_type and name in d.names and d.kind in ("property", "field")), None)
            if top is not None:
                return path, parsed, top, name
            for r in parsed.imports:  # `import static a.B.NAME` (Java), `import a.b.NAME` (Kotlin), `import a.B.Companion.NAME`
                if not r.wildcard and r.local == name:
                    got = self._qualified(path, r.module.split("."))
                    if got is not None:
                        return got
            for r in parsed.imports:  # `import static a.B.*`, `import a.B.*`, `import a.b.*`
                if r.wildcard:
                    got = self._qualified(path, [*r.module.split("."), name])
                    if got is not None:
                        return got
            same = lang.prefer(sha, path, lang.declaring_files(sha, parsed.package, name))
            if len(same) > 1:
                lang.ambiguous(sha, path, name, same)
                return None
            for f in same:  # a Kotlin top-level property of the same package
                p = lang.parsed(sha, f)
                if p is not None and not p.problems:
                    d = next((d for d in p.declarations if name in d.names and d.kind == "property"), None)
                    if d is not None:
                        return f, p, d, name
            return None
        head = chain[0]
        hit = lang.resolve_type(sha, path, parsed, head)
        if hit is not None:
            f, top = hit
            p = lang.parsed(sha, f)
            if p is None or p.problems:
                return None
            cur = next((d for d in p.declarations if d.name == top), None)
            for seg in chain[1:-1]:
                cur = self._child(cur, seg) if cur is not None else None
            if cur is not None:
                member = self._member(cur, name)
                if member is not None:
                    return f, p, member, name
        return self._qualified(path, chain)

    def _qualified(self, path: str, chain: list[str]) -> tuple[str, Parsed, Unit, str] | None:
        """A name written with its package (or with an import that names a member): resolved through the files that declare it; when more
        than one does and nothing tells which one `path` means, it is not resolved (said by the language adapter)."""
        lang, sha = self.language, self.sha
        files, _pkg, top, rest = lang.resolve_fqn(sha, ".".join(chain))
        files = tuple(lang.prefer(sha, path, files))
        if len(files) > 1:
            lang.ambiguous(sha, path, ".".join(chain), files)
            return None
        for f in files:
            p = lang.parsed(sha, f)
            if p is None or p.problems:
                continue
            cur = next((d for d in p.declarations if d.name == top or top in d.names), None)
            if cur is None:
                continue
            if not rest:
                if cur.kind in ("property", "field"):  # a Kotlin top-level property
                    return f, p, cur, top
                continue
            for seg in rest[:-1]:
                cur = self._child(cur, seg)
                if cur is None:
                    break
            if cur is None:
                continue
            member = self._member(cur, rest[-1])
            if member is not None:
                return f, p, member, rest[-1]
        return None

    @staticmethod
    def _child(unit: Unit, name: str) -> Unit | None:
        for c in unit.children:
            if c.is_type and c.name == name:
                return c
        return None

    @staticmethod
    def _member(unit: Unit, name: str) -> Unit | None:
        """The field or property called `name` in a type, or in its companion object."""
        for c in unit.children:
            if c.kind in ("field", "property") and name in c.names:
                return c
        for c in unit.children:
            if c.kind == "companion":
                for k in c.children:
                    if k.kind in ("field", "property") and name in k.names:
                        return k
        return None

    @staticmethod
    def initializer(parsed: Parsed, unit: Unit, name: str) -> tuple[int, int] | None:
        """The code tokens of the expression that initialises `name` in a field or property unit (after its `=`)."""
        code = parsed.code
        depth_end = unit.t1
        i = unit.t0
        while i < unit.t1:
            t = code[i]
            if t.kind == "punct" and t.value in "([{":
                i = parsed.match.get(i, i) + 1
                continue
            if t.kind == "ident" and t.value == name and i + 1 < unit.t1 and code[i + 1].kind == "punct" and code[i + 1].value == "=" and i != unit.t0:
                start = i + 2
                j = start
                while j < depth_end:
                    tj = code[j]
                    if tj.kind == "punct" and tj.value in "([{":
                        j = parsed.match.get(j, j) + 1
                        continue
                    if tj.kind == "punct" and tj.value in (",", ";"):
                        break
                    j += 1
                return (start, j) if j > start else None
            i += 1
        return None
