"""Java and Kotlin syntax facts from the tokens of `lexer`: the package and the imports, the declarations (types and, in Kotlin,
top-level functions and properties) with their members and spans, the annotations and supertypes of a type, the names a
declaration spells, the uses of a name, and the definitions of a name.

One `Parsed` per source text. It is a reader of declarations and of what is spelled inside them, not a compiler: a member it does
not understand is kept as an `other` unit with the span it found (still placed by line), a text that does not lex cleanly (an
unterminated string, comment or text block, a bracket that does not match) is a parse problem and nothing is read from it (`problems`
says why; the adapter then treats every changed line as module level and the importer lists as incomplete). There is no fallback
to regular expressions.

Placement is by line number. A top-level declaration's span starts at its first annotation or modifier. A member of a type is a
unit of its own (`Class.member`, nested types dotted: `Outer.Inner.member`); the lines of a type outside its members (its
annotations, its header up to the opening brace, a closing brace) are the type itself ("class level"). A comment or a blank line
between members changes nothing; inside a member it belongs to it.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from typing import Iterator, Sequence

from reachmap.adapters.jvm.lexer import Token, tokenize

__all__ = ["Annotation", "ImportRecord", "Parsed", "Placement", "SuperRef", "Unit", "Use", "parse_java", "parse_kotlin", "MODULE"]

MODULE = "<module>"
JAVA_MODIFIERS = frozenset({"public", "protected", "private", "abstract", "static", "final", "native", "synchronized", "transient", "volatile", "strictfp", "default", "sealed"})
KOTLIN_MODIFIERS = frozenset({
    "public", "private", "protected", "internal", "open", "final", "abstract", "sealed", "override", "lateinit", "const", "inline", "noinline", "crossinline",
    "infix", "operator", "tailrec", "suspend", "external", "data", "enum", "annotation", "inner", "value", "vararg", "reified", "expect", "actual",
})
TYPE_KINDS = frozenset({"class", "interface", "enum", "record", "annotation", "object", "companion"})
WIDE_ANNOTATIONS = frozenset({"PostConstruct", "PreDestroy", "Bean", "EventListener", "ExceptionHandler", "ModelAttribute", "InitBinder"})
CLASS_WIDE = frozenset({"constructor", "init"})  # units that run when the class is built or loaded: reached, they reach the whole class
DYNAMIC_NAMES = frozenset({
    "forName", "loadClass", "getDeclaredMethod", "getDeclaredMethods", "getMethod", "getMethods", "getDeclaredField", "getDeclaredFields", "getField",
    "getDeclaredConstructor", "newProxyInstance", "getBean", "getBeansOfType", "ServiceLoader", "MethodHandles", "invokeExact",
})
_SPEL = re.compile(r"@([A-Za-z_]\w*)")
_PLAIN_NAME = re.compile(r"^[A-Za-z_]\w*$")


@dataclass(frozen=True, slots=True)
class Annotation:
    """`@Name(args)` (Java) or `@Name(args)` / `@use-site:Name(args)` (Kotlin). `t0`..`t1` are the code-token range of the whole
    annotation; `args` are the top-level arguments as (name or None, first token, last token + 1); `line` is the line of the `@`."""

    name: str  # the last segment: `GetMapping` for `@org.x.GetMapping`
    qualified: str
    line: int
    t0: int
    t1: int
    args: tuple[tuple[str | None, int, int], ...] = ()
    target: str | None = None  # a Kotlin use-site target (`field`, `get`, `file`)


@dataclass(frozen=True, slots=True)
class SuperRef:
    name: str  # the last segment of the type as written
    qualified: str  # as written (`a.b.Base`)
    kind: str  # extends | implements | colon (a Kotlin `: A(), B`)
    line: int


@dataclass(eq=False, slots=True)
class Unit:
    """A declaration: a type, a member of a type, or (Kotlin) a top-level function, property or type alias. `start`..`end` are the
    inclusive lines of the whole of it (annotations and modifiers included); for a type `header_end` is the line of the `{` that opens
    its body. `t0`..`t1` is its range of code tokens. `names` holds every name it declares (`int a, b;` declares two)."""

    kind: str  # class interface enum record annotation object companion | method constructor init field property enum_constant function typealias other
    name: str
    names: tuple[str, ...]
    start: int
    end: int
    name_line: int
    t0: int
    t1: int
    name_tok: int
    header_end: int
    modifiers: frozenset[str]
    annotations: tuple[Annotation, ...]
    parent: "Unit | None" = None
    children: list["Unit"] = field(default_factory=list)
    supertypes: tuple[SuperRef, ...] = ()
    body: tuple[int, int] | None = None  # the code tokens inside the braces of a type
    in_header: bool = False  # a Kotlin constructor property, a record component: declared in the header of its type
    top_level: bool = False
    returns: str = ""  # the written return type of a method or function, the type of a field or property (for a reader that wants it)

    @property
    def is_type(self) -> bool:
        return self.kind in TYPE_KINDS

    @property
    def path(self) -> tuple[str, ...]:
        parts: list[str] = []
        u: Unit | None = self
        while u is not None:
            if not (u.kind == "companion" and u.name == "Companion"):  # an unnamed companion is not part of a name
                parts.append(u.name)
            u = u.parent
        return tuple(reversed(parts))

    @property
    def dotted(self) -> str:
        return ".".join(self.path)

    @property
    def top(self) -> "Unit":
        u = self
        while u.parent is not None:
            u = u.parent
        return u

    @property
    def wide(self) -> bool:
        """Reached, it reaches the whole of its type: a constructor, an initializer block, a static initializer, a method that runs
        when the bean is built or torn down, a method that handles the exceptions or the binding of every handler of its controller."""
        if self.kind in CLASS_WIDE:
            return True
        return any(a.name in WIDE_ANNOTATIONS for a in self.annotations) and self.kind in ("method", "function")

    @property
    def static(self) -> bool:
        """Initialised once, when the type (or the Kotlin file) is loaded: a static field, a Kotlin top-level property, a property of an
        object or a companion, a field of an interface."""
        if self.parent is None:
            return self.kind == "property"
        return "static" in self.modifiers or self.parent.kind in ("object", "companion", "interface")

    def walk(self) -> Iterator["Unit"]:
        yield self
        for c in self.children:
            yield from c.walk()


@dataclass(frozen=True, slots=True)
class Placement:
    """Where a changed line sits: the deepest unit that holds it, its top-level declaration, the dotted name and the unit's kind.
    `whole` is True for a line of a type outside its members (its annotations and header): the type itself changed."""

    unit: Unit
    decl: Unit
    name: str
    nested: tuple[str, ...]
    kind: str
    whole: bool


@dataclass(frozen=True, slots=True)
class ImportRecord:
    """One import statement. `module` is the dotted name without a trailing `.*`; `wildcard` says it ends with `.*`; `static` is a Java
    `import static`; `alias` a Kotlin `as`. `names` is always empty (kept so that a reader of a Python import record reads this one)."""

    kind: str  # import | static | wildcard | static_wildcard
    module: str
    line: int
    alias: str | None = None
    names: tuple = ()
    level: int = 0
    type_only: bool = False

    @property
    def wildcard(self) -> bool:
        return self.kind in ("wildcard", "static_wildcard")

    @property
    def static(self) -> bool:
        return self.kind in ("static", "static_wildcard")

    @property
    def specifier(self) -> str:
        return self.module + (".*" if self.wildcard else "")

    @property
    def local(self) -> str:
        """The name the import binds (None for a wildcard): the alias, else the last segment."""
        return self.alias or self.module.rsplit(".", 1)[-1]


@dataclass(frozen=True, slots=True)
class Use:
    """A use of a name: `chain` are the names after it (`Money.format(3)` is `Money` with chain `("format",)`); `form` is `call` when the
    whole chain is called or constructed, else `reference`. `tok` is the index of the name among the code tokens (-1 for a name spelled
    inside a Kotlin string template); `lparen` the index of the `(` of the call, -1 when there is none."""

    local: str
    line: int
    chain: tuple[str, ...]
    form: str
    tok: int
    lparen: int = -1


_JOINERS_BEFORE = frozenset({",", ".", "?.", "::", "=", "->", "+", "-", "*", "/", "%", "&&", "||", "?:", ":", "<", ">", "&", "|", "!", "in", "is", "as", "by", "to", "and", "or", "else"})
_JOINERS_AFTER = frozenset({".", "?.", "?:", "&&", "||", "->", "as", "is", "in", "by", "else", "catch", "finally", ":", "=", "+", "*", "/", "%", ",", "?", "to", "and", "or"})
_KOTLIN_DECL_STARTS = frozenset({"fun", "val", "var", "class", "interface", "object", "typealias", "init", "constructor", "companion", "enum", "data", "sealed", "private", "public", "protected", "internal", "override", "open", "abstract", "const", "lateinit"})


class Parsed:
    """A parsed source text (see the module docstring). Create it with `parse_java` or `parse_kotlin`."""

    def __init__(self, text: str, kotlin: bool) -> None:
        self.text = text
        self.kotlin = kotlin
        self.lines = text.split("\n")
        res = tokenize(text, kotlin)
        problems = list(res.problems)
        self.all: tuple[Token, ...] = res.tokens
        self.code: list[Token] = [t for t in res.tokens if not t.inner]
        self._inner: list[Token] = [t for t in res.tokens if t.inner]
        self._inner_starts = [t.start for t in self._inner]
        self.match: dict[int, int] = {}
        self.package = ""
        self.package_line = 0
        self.file_annotations: tuple[Annotation, ...] = ()
        self.imports: tuple[ImportRecord, ...] = ()
        self.declarations: tuple[Unit, ...] = ()
        self.unresolved_dynamic: tuple[int, ...] = ()
        self._code_lines: frozenset[int] | None = None
        self._units: list[Unit] | None = None
        self._free: dict[int, frozenset[str]] = {}
        self._uses: dict[str, list[Use]] | None = None
        self._refs: dict[int, frozenset[str]] = {}
        self._chains: list[tuple[str, int, int]] | None = None
        self.problems: tuple[str, ...] = ()
        if not problems:
            problems += self._match_brackets()
        if not problems:
            try:
                _Parser(self).run()
            except _Stop as stop:
                problems.append(str(stop))
            except RecursionError:
                problems.append("the file nests too deeply to be read")
        self.problems = tuple(problems)
        if problems:
            self.declarations = ()
            self.imports = ()

    # ---- helpers over the tokens

    def _match_brackets(self) -> list[str]:
        stack: list[int] = []
        for i, t in enumerate(self.code):
            if t.kind != "punct":
                continue
            if t.value in "([{":
                stack.append(i)
            elif t.value in ")]}":
                if not stack:
                    return [f"line {t.line}: unmatched {t.value!r}"]
                j = stack.pop()
                self.match[j], self.match[i] = i, j
        return []

    def line_text(self, number: int) -> str:
        if 1 <= number <= len(self.lines):
            return self.lines[number - 1].rstrip("\r")
        return ""

    def source(self, a: int, b: int) -> str:
        """The source text from the start of code token `a` to the end of code token `b` (inclusive)."""
        return self.text[self.code[a].start:self.code[b].end]

    # ---- units and placement

    def units(self) -> list[Unit]:
        """Every unit of the file (types and their members, nested ones too), in source order."""
        if self._units is None:
            self._units = [u for d in self.declarations for u in d.walk()]
        return self._units

    @property
    def top_names(self) -> tuple[str, ...]:
        out: list[str] = []
        for d in self.declarations:
            for n in d.names:
                if n not in out:
                    out.append(n)
        return tuple(out)

    @property
    def code_lines(self) -> frozenset[int]:
        """The lines outside every declaration that hold code (a package or import statement, a file annotation, a stray token); the
        lines inside a declaration are not listed: its span holds them."""
        if self._code_lines is None:
            lines: set[int] = set()
            for t in self.code:
                lines.update(range(t.line, t.end_line + 1))
            for d in self.declarations:
                lines.difference_update(range(d.start, d.end + 1))
            self._code_lines = frozenset(lines)
        return self._code_lines

    def _token_lines(self) -> frozenset[int]:
        if not hasattr(self, "_tl"):
            lines: set[int] = set()
            for t in self.code:
                lines.update(range(t.line, t.end_line + 1))
            self._tl = frozenset(lines)
        return self._tl  # type: ignore[attr-defined]

    def placements(self, line: int) -> list[Placement]:
        """Every placement of the line (more than one when declarations share it)."""
        out: list[Placement] = []
        for d in self.declarations:
            if d.start <= line <= d.end:
                out += self._place(d, d, line)
        return out

    def _place(self, unit: Unit, decl: Unit, line: int) -> list[Placement]:
        if not unit.is_type or not unit.children and line <= unit.end and unit.body is None:
            return [self._placement(unit, decl, False)]
        inside = [c for c in unit.children if c.start <= line <= c.end]
        header_prop = [c for c in inside if c.in_header]
        if header_prop:
            out = [self._placement(c, decl, False) for c in header_prop]
            if line == unit.name_line:  # `class A(val a: X, val b: Y) {` on one line: which part changed is not told
                out.append(self._placement(unit, decl, True))
            return out
        if line <= unit.header_end:
            return [self._placement(unit, decl, True)]
        members = [c for c in inside if not c.in_header]
        if members:
            out: list[Placement] = []
            for c in members:
                out += self._place(c, decl, line)
            return out
        if line in self._token_lines():  # a closing brace, a stray token between members: the type itself
            return [self._placement(unit, decl, True)]
        return []  # a comment or a blank line between members changes nothing

    def _placement(self, unit: Unit, decl: Unit, whole: bool) -> Placement:
        kind = unit.kind
        return Placement(unit, decl, unit.dotted, (), kind, whole)

    def enclosing(self, line: int) -> str:
        """The dotted name of the deepest member (or the type, for a line of its header) the line sits in; `<module>` outside every
        declaration."""
        best: Unit | None = None
        for d in self.declarations:
            if d.start <= line <= d.end:
                best = d
                while best.is_type:
                    inner = [c for c in best.children if c.start <= line <= c.end and not c.in_header]
                    if line <= best.header_end or not inner:
                        break
                    best = inner[0]
        return best.dotted if best is not None else MODULE

    def unit_of(self, dotted: str) -> list[Unit]:
        """The units with that dotted name (more than one for overloads)."""
        return [u for u in self.units() if u.dotted == dotted]

    # ---- what a unit spells

    def spelled(self, unit: Unit) -> frozenset[str]:
        """Every name spelled inside the unit (annotations, types, bodies, names a Kotlin template holds), without the name the unit
        declares (a handler called `total` does not spell `total`), plus the bean a SpEL `@name` in an annotation refers to. Shadowing is
        not understood: more names, never fewer."""
        key = id(unit)
        if key not in self._refs:
            self._refs[key] = self._spelled_range(unit.t0, unit.t1, unit.name_tok)
        return self._refs[key]

    def _spelled_range(self, t0: int, t1: int, skip: int = -1) -> frozenset[str]:
        out: set[str] = set()
        for i in range(t0, t1):
            t = self.code[i]
            if t.kind == "ident" and i != skip:
                out.add(t.value)
            elif t.kind == "string" and t.text and "@" in t.text:
                out.update(_SPEL.findall(t.text))
        if t1 > t0:
            lo = self.code[t0].start
            hi = self.code[t1 - 1].end
            a = bisect.bisect_left(self._inner_starts, lo)
            b = bisect.bisect_left(self._inner_starts, hi)
            out.update(t.value for t in self._inner[a:b] if t.kind == "ident")
        return frozenset(out)

    def spelled_all(self) -> frozenset[str]:
        """Every identifier of the file."""
        return frozenset(t.value for t in self.all if t.kind == "ident")

    def _reach_ranges(self, unit: Unit) -> list[tuple[int, int]]:
        """The token ranges of a unit that its reach is read from: a type's header without its constructor properties (each is a unit of its
        own) and without its members, a constructor's body (its parameters are where something is injected, not what it does), else the
        whole unit."""
        if unit.is_type and unit.body is not None:
            ranges = [(unit.t0, unit.body[0] - 1)]
            for c in sorted((c for c in unit.children if c.in_header), key=lambda c: c.t0):
                a, b = ranges.pop()
                ranges += [(a, min(c.t0, b)), (max(c.t1, a), b)]
            return [(a, b) for a, b in ranges if b > a]
        if unit.kind == "constructor" and unit.body is not None:
            return [unit.body]
        return [(unit.t0, unit.t1)]

    def reach_spelled(self, unit: Unit) -> frozenset[str]:
        """The names a unit's reach is read from: `spelled` of the ranges of `_reach_ranges`, without the unit's own name."""
        key = -id(unit)
        if key not in self._refs:
            out: set[str] = set()
            for a, b in self._reach_ranges(unit):
                out |= self._spelled_range(a, b, unit.name_tok)
            self._refs[key] = frozenset(out)
        return self._refs[key]

    def reach_free(self, unit: Unit) -> frozenset[str]:
        """The same, only the names used on their own (`total()`, `service.find()`'s `service`, `this.service`): not `x.total`. A name that
        many classes define is followed only through these."""
        key = id(unit)
        if key not in self._free:
            out: set[str] = set()
            code = self.code
            for a, b in self._reach_ranges(unit):
                for i in range(a, b):
                    t = code[i]
                    if t.kind != "ident" or i == unit.name_tok:
                        continue
                    prev = code[i - 1].value if i else ""
                    if prev in (".", "?.", "::"):
                        before = code[i - 2].value if i > 1 else ""
                        if not (prev == "." and before in ("this", "super")):
                            continue
                    out.add(t.value)
                if b > a:
                    lo, hi = code[a].start, code[b - 1].end
                    x = bisect.bisect_left(self._inner_starts, lo)
                    y = bisect.bisect_left(self._inner_starts, hi)
                    out.update(t.value for t in self._inner[x:y] if t.kind == "ident")
            self._free[key] = frozenset(out)
        return self._free[key]

    def header_spelled(self, unit: Unit) -> frozenset[str]:
        return self.reach_spelled(unit)

    def is_trivial_constructor(self, unit: Unit) -> bool:
        """A constructor that only stores what it is given (`this.service = service;`, `super(...)`): dependency injection, no logic of its
        own. Reached by a name it spells, it does not reach the whole of its type."""
        if unit.kind != "constructor" or unit.body is None:
            return False
        a, b = unit.body
        i = a
        code = self.code
        while i < b:
            j = i
            while j < b and not (code[j].kind == "punct" and code[j].value == ";"):
                if code[j].kind == "punct" and code[j].value in "([{":
                    j = self.match.get(j, j) + 1
                    continue
                j += 1
            stmt = [t.value for t in code[i:j] if True]
            kinds = [t.kind for t in code[i:j]]
            ok = False
            if stmt and stmt[0] in ("super", "this") and len(stmt) > 1 and stmt[1] == "(":
                ok = True
            elif len(stmt) == 5 and stmt[0] == "this" and stmt[1] == "." and stmt[3] == "=" and kinds[2] == "ident" and kinds[4] == "ident":
                ok = True
            elif len(stmt) == 3 and stmt[1] == "=" and kinds[0] == "ident" and kinds[2] == "ident":
                ok = True
            if stmt and not ok:
                return False
            i = j + 1
        return True

    def has_call(self, unit: Unit) -> bool:
        """Whether the unit's initializer calls something (a `(` that follows a name, in its code and not in an annotation)."""
        skip = [(a.t0, a.t1) for a in unit.annotations]
        for i in range(unit.t0 + 1, unit.t1):
            if any(a <= i < b for a, b in skip):
                continue
            if self.code[i].kind == "punct" and self.code[i].value == "(" and self.code[i - 1].kind == "ident" and i - 1 != unit.name_tok:
                return True
        return False

    def uses_dynamic_access(self) -> bool:
        """Whether names may be reached in ways the walk cannot follow: reflection, a bean looked up by type or name, a class loaded by name."""
        return any(t.kind == "ident" and t.value in DYNAMIC_NAMES for t in self.all)

    # ---- uses

    def uses(self, name: str, *, member: bool = False) -> tuple[Use, ...]:
        """The uses of a name, in source order: an identifier that is not the member of something else (`a.name` is a use of `a`). With
        `member=True` a name after a dot counts too (a Kotlin extension function, `x.toMoney()`, is used as a member)."""
        if self._uses is None:
            self._index_uses()
        found = self._uses.get(name, ())  # type: ignore[union-attr]
        if member:
            return tuple(found) + tuple(self._member_uses.get(name, ()))  # type: ignore[attr-defined]
        return tuple(found)

    def _index_uses(self) -> None:
        index: dict[str, list[Use]] = {}
        members: dict[str, list[Use]] = {}
        self._uses, self._member_uses = index, members
        code = self.code
        n = len(code)
        declared = {u.name_tok for u in self.units() if u.name_tok >= 0}  # the name a declaration gives is not a use of it
        for i, t in enumerate(code):
            if t.kind != "ident" or i in declared or i < self._first_decl_token:  # the package and the imports are no use of a name
                continue
            prev = code[i - 1].value if i else ""
            prev2 = code[i - 2] if i > 1 else None
            qualified = prev in (".", "?.") or (prev == "::" and prev2 is not None and prev2.kind == "ident")
            chain: list[str] = []
            j = i + 1
            while j + 1 < n and code[j].value in (".", "?.") and code[j + 1].kind == "ident":
                chain.append(code[j + 1].value)
                j += 2
            k = j
            if k < n and code[k].value == "<":  # explicit type arguments of a call: `Foo<Bar>(x)`
                end = skip_angles(code, k)
                if end > 0:
                    k = end
            form, lparen = "reference", -1
            if k < n and code[k].value == "(":
                form, lparen = "call", k
            elif prev == "new":
                form = "reference"
            use = Use(t.value, t.line, tuple(chain), form, i, lparen)
            (members if qualified else index).setdefault(t.value, []).append(use)
        for t in self._inner:
            if t.kind == "ident":
                index.setdefault(t.value, []).append(Use(t.value, t.line, (), "reference", -1))
        for items in (*index.values(), *members.values()):
            items.sort(key=lambda u: (u.line, u.tok))

    def args_of(self, use: Use) -> tuple[str, ...]:
        """The source text of each argument of the call a use is the callee of (empty for a reference)."""
        if use.lparen < 0 or use.lparen not in self.match:
            return ()
        close = self.match[use.lparen]
        out: list[str] = []
        start = use.lparen + 1
        i = start
        while i < close:
            v = self.code[i].value
            if self.code[i].kind == "punct" and v in "([{":
                i = self.match.get(i, i) + 1
                continue
            if v == "," and self.code[i].kind == "punct":
                if i > start:
                    out.append(self.source(start, i - 1))
                start = i + 1
            i += 1
        if close > start:
            out.append(self.source(start, close - 1))
        return tuple(out)

    def chains(self) -> list[tuple[str, int, int]]:
        """Every maximal dotted chain of names with at least three segments after the imports (`com.acme.util.Money.format`), as
        (text, line, token index): a fully qualified name used in code or in an annotation. Segments after a call are not part of it."""
        if self._chains is None:
            out: list[tuple[str, int, int]] = []
            code = self.code
            n = len(code)
            first = self._first_decl_token
            i = first
            while i < n:
                t = code[i]
                if t.kind == "ident" and not (i and code[i - 1].value in (".", "?.", "::")):
                    j = i
                    segs = [t.value]
                    while j + 2 < n and code[j + 1].value == "." and code[j + 2].kind == "ident":
                        segs.append(code[j + 2].value)
                        j += 2
                    if len(segs) >= 3:
                        out.append((".".join(segs), t.line, i))
                    i = j + 1
                else:
                    i += 1
            self._chains = out
        return self._chains

    _first_decl_token = 0

    # ---- definitions

    def definition_lines(self, name: str) -> list[int]:
        """The lines that define `name`: a type, a method, a field or property, an enum constant, a Kotlin function or type alias, a record
        component or constructor property. A use, an import, a parameter and a local variable are not definitions."""
        out: list[int] = []
        for u in self.units():
            if u.kind in ("constructor", "init", "other", "companion"):
                continue
            if u.name == name or name in u.names:
                out.append(u.name_line)
        return sorted(out)


class _Stop(Exception):
    """The parser met something it cannot read: the file is a parse problem."""


class _Parser:
    def __init__(self, p: Parsed) -> None:
        self.p = p
        self.t = p.code
        self.n = len(p.code)
        self.kotlin = p.kotlin
        self.mods = KOTLIN_MODIFIERS if p.kotlin else JAVA_MODIFIERS

    # ---- the file

    def run(self) -> None:
        p, t = self.p, self.t
        i = 0
        file_annotations: list[Annotation] = []
        seq: list[tuple[Annotation, int]] = []
        k = 0
        while k < self.n and t[k].value == "@" and t[k].kind == "punct":
            ann, j = self.annotation(k)
            if ann is None:
                break
            seq.append((ann, j))
            k = j
        before_package = k < self.n and t[k].value == "package"  # `@Marker package a.b;` annotates the package
        for ann, j in seq:
            if before_package or ann.target == "file":
                file_annotations.append(ann)
                i = j
            else:
                break
        if i < self.n and t[i].value == "package":
            line = t[i].line
            name, i = self.qualified_name(i + 1)
            p.package, p.package_line = name, line
            if i < self.n and t[i].value == ";":
                i += 1
        p.file_annotations = tuple(file_annotations)
        imports: list[ImportRecord] = []
        while i < self.n and t[i].value == "import" and t[i].kind == "ident":
            rec, i = self.import_statement(i)
            imports.append(rec)
        p.imports = tuple(imports)
        p._first_decl_token = i
        decls: list[Unit] = []
        while i < self.n:
            if t[i].value == ";":
                i += 1
                continue
            unit, j = self.member(i, None, True)
            if unit is None:
                j = self.skip_unknown(i)
            else:
                decls.append(unit)
            i = max(j, i + 1)
        p.declarations = tuple(decls)

    def import_statement(self, i: int) -> tuple[ImportRecord, int]:
        t = self.t
        line = t[i].line
        i += 1
        static = False
        if i < self.n and t[i].value == "static" and not self.kotlin:
            static = True
            i += 1
        segs: list[str] = []
        wildcard = False
        while i < self.n:
            if t[i].kind == "ident":
                segs.append(t[i].value)
                i += 1
            else:
                break
            if i < self.n and t[i].value == ".":
                if i + 1 < self.n and t[i + 1].value == "*":
                    wildcard = True
                    i += 2
                    break
                i += 1
            else:
                break
        alias = None
        if i + 1 < self.n and t[i].value == "as" and t[i + 1].kind == "ident" and self.kotlin:
            alias = t[i + 1].value
            i += 2
        if i < self.n and t[i].value == ";":
            i += 1
        module = ".".join(segs)
        kind = ("static_wildcard" if wildcard else "static") if static else ("wildcard" if wildcard else "import")
        return ImportRecord(kind, module, line, alias), i

    def qualified_name(self, i: int) -> tuple[str, int]:
        t = self.t
        segs: list[str] = []
        while i < self.n and t[i].kind == "ident":
            segs.append(t[i].value)
            i += 1
            if i + 1 < self.n and t[i].value == "." and t[i + 1].kind == "ident":
                i += 1
            else:
                break
        return ".".join(segs), i

    # ---- annotations and modifiers

    def annotation(self, i: int) -> tuple[Annotation | None, int]:
        """The annotation that starts at `@` (index i): the annotation and the index after it; (None, i) when it is not one."""
        t, n = self.t, self.n
        line = t[i].line
        j = i + 1
        if j < n and t[j].value == "interface" and not self.kotlin:
            return None, i
        target = None
        if self.kotlin and j + 1 < n and t[j].kind == "ident" and t[j + 1].value == ":" and t[j].value in ("file", "field", "get", "set", "param", "property", "receiver", "setparam", "delegate"):
            target = t[j].value
            j += 2
        if j >= n or t[j].kind != "ident":
            return None, i
        name, j = self.qualified_name(j)
        args: tuple[tuple[str | None, int, int], ...] = ()
        if j < n and t[j].value == "(" and j in self.p.match and not t[j].newline_before:
            close = self.p.match[j]
            args = self.annotation_args(j + 1, close)
            j = close + 1
        return Annotation(name.rsplit(".", 1)[-1], name, line, i, j, args, target), j

    def annotation_args(self, a: int, b: int) -> tuple[tuple[str | None, int, int], ...]:
        t = self.t
        out: list[tuple[str | None, int, int]] = []
        start = a
        i = a
        while i <= b:
            if i == b or (t[i].value == "," and t[i].kind == "punct"):
                if i > start:
                    name = None
                    s = start
                    if i - start >= 2 and t[start].kind == "ident" and t[start + 1].value == "=" and t[start + 1].kind == "punct":
                        name, s = t[start].value, start + 2
                    out.append((name, s, i))
                start = i + 1
                i += 1
                continue
            if t[i].kind == "punct" and t[i].value in "([{":
                i = self.p.match.get(i, i) + 1
                continue
            i += 1
        return tuple(out)

    def leading(self, i: int) -> tuple[int, frozenset[str], tuple[Annotation, ...]]:
        """Skip the annotations and modifiers that start a declaration at index i: (index of the first token after them, the modifiers,
        the annotations)."""
        t, n = self.t, self.n
        mods: set[str] = set()
        anns: list[Annotation] = []
        while i < n:
            v = t[i].value
            if v == "@" and t[i].kind == "punct":
                ann, j = self.annotation(i)
                if ann is None:
                    break
                anns.append(ann)
                i = j
            elif t[i].kind == "ident" and v == "non" and i + 2 < n and t[i + 1].value == "-" and t[i + 2].value == "sealed":
                mods.add("non-sealed")
                i += 3
            elif t[i].kind == "ident" and v in self.mods and self.is_modifier_here(i):
                mods.add(v)
                i += 1
            else:
                break
        return i, frozenset(mods), tuple(anns)

    def is_modifier_here(self, i: int) -> bool:
        """A modifier word is a modifier when something that continues a declaration follows (a soft keyword such as `value` or `data`
        can also be a name: `val data = 1`)."""
        nxt = self.t[i + 1] if i + 1 < self.n else None
        if nxt is None:
            return False
        if nxt.kind == "ident" or (nxt.kind == "punct" and nxt.value in ("@", "<", "{")):
            return True
        if self.t[i].value in ("default", "static") and nxt.value == "{":
            return True
        return False

    # ---- one declaration

    def member(self, i: int, parent: Unit | None, top: bool) -> tuple[Unit | None, int]:
        """The declaration that starts at index i (annotations and modifiers first): (unit, index after it); (None, i) when nothing
        that reads as a declaration starts here."""
        t, n = self.t, self.n
        start_i = i
        j, mods, anns = self.leading(i)
        if j >= n:
            return None, i
        tok = t[j]
        v = tok.value
        owner = parent
        if self.kotlin:
            unit = self.kotlin_member(start_i, j, mods, anns, owner, top)
        else:
            unit = self.java_member(start_i, j, mods, anns, owner, top)
        if unit is None:
            return None, i
        if top:
            unit.top_level = True
        return unit, unit.t1

    def make(self, kind: str, name: str, names: Sequence[str], start_i: int, end_i: int, name_tok: int, header_end: int, mods: frozenset[str],
             anns: tuple[Annotation, ...], parent: Unit | None, **extra: object) -> Unit:
        t = self.t
        start_line = t[start_i].line
        end_line = t[end_i - 1].end_line if end_i > start_i else t[start_i].end_line
        name_line = t[name_tok].line if 0 <= name_tok < self.n else start_line
        unit = Unit(kind, name, tuple(names) or (name,), start_line, end_line, name_line, start_i, end_i, name_tok, header_end if header_end else start_line,
                    mods, anns, parent, [], **extra)  # type: ignore[arg-type]
        return unit

    # ---- Java

    def java_member(self, start_i: int, j: int, mods: frozenset[str], anns: tuple[Annotation, ...], parent: Unit | None, top: bool) -> Unit | None:
        t, n = self.t, self.n
        v = t[j].value
        if v == "{" and not top:  # an initializer block (`static { }`)
            end = self.p.match[j] + 1
            return self.make("init", "<init>", ("<init>",), start_i, end, -1, t[j].line, mods, anns, parent, body=(j + 1, end - 1))
        if t[j].kind == "punct" and v == "@" and j + 1 < n and t[j + 1].value == "interface":
            return self.java_type(start_i, j + 1, "annotation", mods, anns, parent)
        if t[j].kind == "ident" and v in ("class", "interface", "enum") and j + 1 < n and t[j + 1].kind == "ident":
            return self.java_type(start_i, j, v, mods, anns, parent)
        if t[j].kind == "ident" and v == "record" and j + 2 < n and t[j + 1].kind == "ident" and t[j + 2].value in ("(", "<"):
            return self.java_type(start_i, j, "record", mods, anns, parent)
        if top:
            return None
        if parent is None:
            return None
        k = j
        if t[k].value == "<":  # type parameters of a generic method or constructor
            k = skip_angles(t, k)
            if k < 0:
                return None
        if k + 1 < n and t[k].kind == "ident" and t[k + 1].value == "(":  # a constructor
            close = self.p.match.get(k + 1, -1)
            if close < 0:
                return None
            end = self.method_end(close + 1)
            return self.make("constructor", t[k].value, (t[k].value,), start_i, end, k, t[start_i].line, mods, anns, parent, body=self.body_of(close + 1, end))
        if k + 1 < n and t[k].kind == "ident" and t[k + 1].value == "{" and parent.kind == "record":  # a compact record constructor
            end = self.p.match[k + 1] + 1
            return self.make("constructor", t[k].value, (t[k].value,), start_i, end, k, t[start_i].line, mods, anns, parent, body=(k + 2, end - 1))
        e = self.type_end(k)
        if e < 0 or e >= n or t[e].kind != "ident":
            return None
        name_tok = e
        after = e + 1
        if after < n and t[after].value == "(":  # a method
            close = self.p.match.get(after, -1)
            if close < 0:
                return None
            end = self.method_end(close + 1)
            return self.make("method", t[e].value, (t[e].value,), start_i, end, name_tok, t[start_i].line, mods, anns, parent, returns=self.p.source(k, e - 1))
        # a field (or several declarators)
        end = self.statement_end_semicolon(after)
        names = [t[e].value]
        depth_i = after
        while depth_i < end:
            tk = t[depth_i]
            if tk.kind == "punct" and tk.value in "([{":
                depth_i = self.p.match.get(depth_i, depth_i) + 1
                continue
            if tk.value == "," and depth_i + 1 < end and t[depth_i + 1].kind == "ident" and depth_i + 2 <= end:
                nxt = t[depth_i + 2].value if depth_i + 2 < n else ""
                if nxt in ("=", ",", ";", "["):
                    names.append(t[depth_i + 1].value)
            depth_i += 1
        return self.make("field", names[0], names, start_i, end, name_tok, t[start_i].line, mods, anns, parent, returns=self.p.source(k, e - 1))

    def body_of(self, i: int, end: int) -> tuple[int, int] | None:
        """The code tokens inside the braces of the body that starts at or after index i (before `end`); None for a unit with no body."""
        t = self.t
        while i < end:
            if t[i].kind == "punct" and t[i].value == "{":
                return (i + 1, self.p.match[i])
            if t[i].kind == "punct" and t[i].value in "([":
                i = self.p.match.get(i, i) + 1
                continue
            i += 1
        return None

    def method_end(self, i: int) -> int:
        """The index after a method that has its parameters closed before index i: through `throws`, dimensions and a default value to
        the body's closing brace, or to the `;` of an abstract method."""
        t, n = self.t, self.n
        while i < n:
            v = t[i].value
            if t[i].kind == "punct" and v == "{":
                return self.p.match[i] + 1
            if t[i].kind == "punct" and v == ";":
                return i + 1
            if t[i].kind == "punct" and v in "([":
                i = self.p.match.get(i, i) + 1
                continue
            i += 1
        raise _Stop("a method has no body or terminator")

    def statement_end_semicolon(self, i: int) -> int:
        t, n = self.t, self.n
        while i < n:
            if t[i].kind == "punct" and t[i].value == ";":
                return i + 1
            if t[i].kind == "punct" and t[i].value in "([{":
                i = self.p.match.get(i, i) + 1
                continue
            if t[i].kind == "punct" and t[i].value == "}":
                return i
            i += 1
        raise _Stop("a declaration is not terminated")

    def type_end(self, i: int) -> int:
        """The index after the type that starts at i (a qualified name, type arguments, dimensions, annotations inside); -1 when there is none."""
        t, n = self.t, self.n
        while i < n and t[i].value == "@" and t[i].kind == "punct":
            ann, j = self.annotation(i)
            if ann is None:
                break
            i = j
        if i >= n or t[i].kind != "ident":
            return -1
        i += 1
        while True:
            if i < n and t[i].value == "<":
                i = skip_angles(t, i)
                if i < 0:
                    return -1
            if i + 1 < n and t[i].value == "." and t[i + 1].kind == "ident":
                i += 2
                continue
            break
        while i + 1 < n and t[i].value == "[" and t[i + 1].value == "]":
            i += 2
        if i < n and t[i].value == "..." and not self.kotlin:
            i += 1
        return i

    def java_type(self, start_i: int, kw: int, kind: str, mods: frozenset[str], anns: tuple[Annotation, ...], parent: Unit | None) -> Unit | None:
        t, n = self.t, self.n
        name_tok = kw + 1
        if name_tok >= n or t[name_tok].kind != "ident":
            return None
        name = t[name_tok].value
        i = name_tok + 1
        if i < n and t[i].value == "<":
            i = skip_angles(t, i)
            if i < 0:
                raise _Stop(f"line {t[name_tok].line}: type parameters of {name} are not closed")
        components: list[tuple[str, int]] = []
        supers: list[SuperRef] = []
        mode = ""
        header_tok = i
        while i < n and not (t[i].kind == "punct" and t[i].value == "{"):
            tk = t[i]
            if tk.kind == "punct" and tk.value == "(":
                close = self.p.match.get(i, -1)
                if close < 0:
                    raise _Stop(f"line {tk.line}: a bracket is not closed")
                if kind == "record" and not components:
                    components = self.record_components(i + 1, close)
                i = close + 1
                continue
            if tk.kind == "ident" and tk.value in ("extends", "implements", "permits"):
                mode = tk.value
                i += 1
                continue
            if mode in ("extends", "implements") and tk.kind == "ident":
                e = self.type_end(i)
                if e < 0:
                    break
                segs = [x.value for x in t[i:e] if x.kind == "ident"]
                qual = ".".join(self.dotted_prefix(i, e))
                supers.append(SuperRef(self.last_segment(i, e), qual, "extends" if mode == "extends" else "implements", tk.line))
                i = e
                continue
            if tk.kind == "punct" and tk.value == ";":
                break
            i += 1
        if i >= n or t[i].value != "{":
            raise _Stop(f"line {t[name_tok].line}: the body of {name} was not found")
        body_open = i
        body_close = self.p.match[body_open]
        unit = self.make(kind, name, (name,), start_i, body_close + 1, name_tok, t[body_open].line, mods, anns, parent,
                         supertypes=tuple(supers), body=(body_open + 1, body_close))
        for cname, ctok in components:
            unit.children.append(self.make("field", cname, (cname,), ctok, ctok + 1, ctok, t[ctok].line, frozenset(), (), unit, in_header=True))
        self.java_body(unit, body_open + 1, body_close)
        return unit

    def record_components(self, a: int, b: int) -> list[tuple[str, int]]:
        t = self.t
        out: list[tuple[str, int]] = []
        i = a
        last_ident = -1
        while i < b:
            if t[i].kind == "punct" and t[i].value in "([{<":
                if t[i].value == "<":
                    e = skip_angles(t, i)
                    i = e if e > 0 else i + 1
                else:
                    i = self.p.match.get(i, i) + 1
                continue
            if t[i].value == "," and t[i].kind == "punct":
                if last_ident >= 0:
                    out.append((t[last_ident].value, last_ident))
                last_ident = -1
            elif t[i].kind == "ident":
                last_ident = i
            i += 1
        if last_ident >= 0:
            out.append((t[last_ident].value, last_ident))
        return out

    def dotted_prefix(self, a: int, b: int) -> list[str]:
        """The identifiers of a type written t[a:b] before any type arguments: `a.b.Base` of `a.b.Base<X>`."""
        t = self.t
        out: list[str] = []
        i = a
        while i < b:
            if t[i].kind == "ident":
                out.append(t[i].value)
                i += 1
                if i < b and t[i].value == ".":
                    i += 1
                    continue
            break
        return out

    def last_segment(self, a: int, b: int) -> str:
        parts = self.dotted_prefix(a, b)
        return parts[-1] if parts else ""

    def java_body(self, unit: Unit, a: int, b: int) -> None:
        t = self.t
        i = a
        if unit.kind == "enum":
            i = self.enum_constants(unit, i, b)
        while i < b:
            if t[i].kind == "punct" and t[i].value == ";":
                i += 1
                continue
            child, j = self.member(i, unit, False)
            if child is None:
                j = self.skip_unknown(i, b)
                if j > i:
                    unit.children.append(self.make("other", "<other>", ("<other>",), i, j, -1, t[i].line, frozenset(), (), unit))
            else:
                unit.children.append(child)
            i = max(j, i + 1)

    def enum_constants(self, unit: Unit, i: int, b: int) -> int:
        """The constants of an enum body (up to the `;` that ends them); the index after them."""
        t = self.t
        while i < b:
            if t[i].kind == "punct" and t[i].value == ";":
                return i + 1
            start = i
            j, mods, anns = self.leading(i)
            if j >= b or t[j].kind != "ident":
                return i
            name_tok = j
            j += 1
            if j < b and t[j].value == "(":
                j = self.p.match.get(j, j) + 1
            if j < b and t[j].value == "{":
                j = self.p.match.get(j, j) + 1
            unit.children.append(self.make("enum_constant", t[name_tok].value, (t[name_tok].value,), start, j, name_tok, t[start].line, mods, anns, unit))
            i = j
            if i < b and t[i].value == ",":
                i += 1
                continue
            if i < b and t[i].value == ";":
                return i + 1
            return i
        return i

    def skip_unknown(self, i: int, limit: int | None = None) -> int:
        """Skip what is not understood: to the next `;` or past the next balanced group."""
        t = self.t
        n = limit if limit is not None else self.n
        j = i
        while j < n:
            tk = t[j]
            if tk.kind == "punct" and tk.value == ";":
                return j + 1
            if tk.kind == "punct" and tk.value in "([{":
                close = self.p.match.get(j, j)
                if tk.value == "{":
                    return close + 1
                j = close + 1
                continue
            j += 1
        return n

    # ---- Kotlin

    def kotlin_member(self, start_i: int, j: int, mods: frozenset[str], anns: tuple[Annotation, ...], parent: Unit | None, top: bool) -> Unit | None:
        t, n = self.t, self.n
        v = t[j].value
        if t[j].kind != "ident":
            return None
        if v == "class" and not (j + 1 < n and t[j + 1].value in ("(", ":", "{")):
            kind = "enum" if "enum" in mods else "annotation" if "annotation" in mods else "class"
            return self.kotlin_type(start_i, j, kind, mods, anns, parent)
        if v == "interface":
            return self.kotlin_type(start_i, j, "interface", mods, anns, parent)
        if v == "fun" and j + 1 < n and t[j + 1].value == "interface":
            return self.kotlin_type(start_i, j + 1, "interface", mods, anns, parent)
        if v == "object" and j + 1 < n and t[j + 1].kind == "ident":
            return self.kotlin_type(start_i, j, "object", mods, anns, parent)
        if v == "companion" and j + 1 < n and t[j + 1].value == "object":
            return self.kotlin_type(start_i, j + 1, "companion", mods | {"companion"}, anns, parent)
        if v == "typealias" and j + 1 < n and t[j + 1].kind == "ident":
            end = self.expression_end(j + 2)
            return self.make("typealias", t[j + 1].value, (t[j + 1].value,), start_i, end, j + 1, t[start_i].line, mods, anns, parent)
        if v == "init" and j + 1 < n and t[j + 1].value == "{" and parent is not None:
            end = self.p.match[j + 1] + 1
            return self.make("init", "<init>", ("<init>",), start_i, end, -1, t[j].line, mods, anns, parent, body=(j + 2, end - 1))
        if v == "constructor" and j + 1 < n and t[j + 1].value == "(" and parent is not None:
            close = self.p.match.get(j + 1, -1)
            if close < 0:
                return None
            k = close + 1
            while k < n and t[k].value != "{" and not (t[k].newline_before):
                if t[k].kind == "punct" and t[k].value in "([":
                    k = self.p.match.get(k, k) + 1
                    continue
                k += 1
            end = self.p.match[k] + 1 if k < n and t[k].value == "{" and not t[k].newline_before or (k < n and t[k].value == "{") else k
            return self.make("constructor", parent.name, (parent.name,), start_i, end, j, t[start_i].line, mods, anns, parent, body=self.body_of(close + 1, end))
        if v == "fun" and j + 1 < n:
            return self.kotlin_fun(start_i, j, mods, anns, parent, top)
        if v in ("val", "var") and j + 1 < n:
            return self.kotlin_property(start_i, j, mods, anns, parent, top)
        return None

    def kotlin_fun(self, start_i: int, j: int, mods: frozenset[str], anns: tuple[Annotation, ...], parent: Unit | None, top: bool) -> Unit | None:
        t, n = self.t, self.n
        k = j + 1
        if t[k].value == "<":
            k = skip_angles(t, k)
            if k < 0:
                return None
        name_tok = -1
        while k < n:
            tk = t[k]
            if tk.kind == "punct" and tk.value == "(":
                break
            if tk.kind == "punct" and tk.value in ("{", "=", ";", "}"):
                return None
            if tk.kind == "punct" and tk.value == "<":
                e = skip_angles(t, k)
                k = e if e > 0 else k + 1
                continue
            if tk.kind == "ident":
                name_tok = k
            k += 1
        if k >= n or name_tok < 0:
            return None
        close = self.p.match.get(k, -1)
        if close < 0:
            return None
        k = close + 1
        end = None
        while k < n:
            tk = t[k]
            if tk.kind == "punct" and tk.value == "{":
                end = self.p.match[k] + 1
                break
            if tk.kind == "punct" and tk.value == "=":
                end = self.expression_end(k + 1)
                break
            if tk.kind == "punct" and tk.value == ";":
                end = k + 1
                break
            if tk.kind == "punct" and tk.value in "([":
                k = self.p.match.get(k, k) + 1
                continue
            if tk.kind == "punct" and tk.value == "<":
                e = skip_angles(t, k)
                k = e if e > 0 else k + 1
                continue
            if tk.newline_before and k > close + 1 and not (_joins_header(t[k - 1], tk)):
                end = k  # a function without a body (abstract, interface, expect)
                break
            k += 1
        if end is None:
            end = n
        kind = "function" if top or parent is None else "method"
        return self.make(kind, t[name_tok].value, (t[name_tok].value,), start_i, end, name_tok, t[start_i].line, mods, anns, parent)

    def kotlin_property(self, start_i: int, j: int, mods: frozenset[str], anns: tuple[Annotation, ...], parent: Unit | None, top: bool) -> Unit | None:
        t, n = self.t, self.n
        k = j + 1
        if t[k].value == "<":
            k = skip_angles(t, k)
            if k < 0:
                return None
        names: list[str] = []
        name_tok = -1
        if k < n and t[k].value == "(":  # val (a, b) = pair: a destructuring declaration (locals; at member level it cannot occur)
            close = self.p.match.get(k, -1)
            if close < 0:
                return None
            for x in range(k + 1, close):
                if t[x].kind == "ident":
                    names.append(t[x].value)
            name_tok = k + 1
            k = close + 1
        else:
            while k < n and t[k].kind == "ident":
                last = k
                k += 1
                if k + 1 < n and t[k].value == "." and t[k + 1].kind == "ident":
                    k += 1
                    continue
                if k < n and t[k].value == "<":  # a receiver with type arguments: `val <T> List<T>.size`
                    e = skip_angles(t, k)
                    if e > 0 and e + 1 < n and t[e].value == ".":
                        k = e + 1
                        continue
                name_tok = last
                break
            if name_tok < 0:
                return None
            names = [t[name_tok].value]
        end = self.expression_end(k)
        while end < n and self.is_accessor(end):  # `get() = ...` / `private set(value) { ... }` after the property
            end = self.accessor_end(end)
        return self.make("property", names[0], names, start_i, end, name_tok, t[start_i].line, mods, anns, parent)

    def is_accessor(self, i: int) -> bool:
        t = self.t
        k = i
        if k < self.n and t[k].kind == "ident" and t[k].value in ("private", "internal", "protected", "public") and k + 1 < self.n:
            k += 1
        return k < self.n and t[k].kind == "ident" and t[k].value in ("get", "set") and (k + 1 >= self.n or t[k + 1].value in ("(", "=", "{") or t[k + 1].newline_before)

    def accessor_end(self, i: int) -> int:
        t, n = self.t, self.n
        k = i
        while k < n and t[k].kind == "ident" and t[k].value in ("private", "internal", "protected", "public", "get", "set"):
            k += 1
        if k < n and t[k].value == "(":
            k = self.p.match.get(k, k) + 1
        if k < n and t[k].value == "{":
            return self.p.match[k] + 1
        if k < n and t[k].value == "=":
            return self.expression_end(k + 1)
        return k

    def expression_end(self, i: int) -> int:
        """The index after the Kotlin expression that starts at i: to a `;`, or the end of the line when the next line does not continue it
        (an operator at the end of the line or a `.` `?.` `?:` `&&` `||` `else` at the start of the next continue it), or to the `}` that
        closes the enclosing body."""
        t, n = self.t, self.n
        k = i
        first = True
        while k < n:
            tk = t[k]
            if tk.kind == "punct" and tk.value in ")]}":
                return k
            if tk.kind == "punct" and tk.value == ";":
                return k + 1 if k + 1 <= n else k
            if not first and tk.newline_before and not _continues(t[k - 1], tk):
                return k
            first = False
            if tk.kind == "punct" and tk.value in "([{":
                k = self.p.match.get(k, k) + 1
                continue
            k += 1
        return n

    def kotlin_type(self, start_i: int, kw: int, kind: str, mods: frozenset[str], anns: tuple[Annotation, ...], parent: Unit | None) -> Unit | None:
        t, n = self.t, self.n
        i = kw + 1
        name = ""
        name_tok = -1
        if i < n and t[i].kind == "ident" and not (kind in ("companion", "object") and t[i].value in ("{",)):
            if not (t[i].value in ("by",) or (t[i].newline_before and kind == "companion")):
                name, name_tok = t[i].value, i
                i += 1
        if kind == "companion" and not name:
            name = "Companion"
        if not name:
            return None
        if i < n and t[i].value == "<":
            e = skip_angles(t, i)
            if e < 0:
                raise _Stop(f"line {t[kw].line}: type parameters of {name} are not closed")
            i = e
        ctor_params: list[tuple[int, int]] = []
        supers: list[SuperRef] = []
        header_end_tok = i
        # the primary constructor, with modifiers and annotations before `constructor`
        while i < n and t[i].kind == "ident" and (t[i].value in KOTLIN_MODIFIERS or t[i].value == "constructor") and not t[i].newline_before:
            i += 1
        while i < n and t[i].value == "@" and not t[i].newline_before:
            ann, j = self.annotation(i)
            if ann is None:
                break
            i = j
            if i < n and t[i].kind == "ident" and t[i].value == "constructor":
                i += 1
        if i < n and t[i].value == "(":
            close = self.p.match.get(i, -1)
            if close < 0:
                raise _Stop(f"line {t[i].line}: a bracket is not closed")
            ctor_params = self.split_params(i + 1, close)
            i = close + 1
        # supertypes
        if i < n and t[i].value == ":" and kind != "annotation":
            i += 1
            while i < n:
                tk = t[i]
                if tk.kind == "punct" and tk.value == "{":
                    break
                if tk.kind == "ident" and tk.value == "where":
                    break
                if tk.kind == "punct" and tk.value == ";":
                    break
                if tk.newline_before and not _joins_header(t[i - 1], tk) and tk.value != ",":
                    break
                if tk.kind == "ident":
                    e = self.type_end(i)
                    if e < 0:
                        break
                    qual = ".".join(self.dotted_prefix(i, e))
                    supers.append(SuperRef(self.last_segment(i, e), qual, "colon", tk.line))
                    i = e
                    if i < n and t[i].value == "(":
                        i = self.p.match.get(i, i) + 1
                    if i < n and t[i].kind == "ident" and t[i].value == "by":  # a delegate: a name (a chain of names, a call), up to the comma or the body
                        i += 1
                        while i < n and (t[i].kind == "ident" or t[i].value in (".", "?.")):
                            i += 1
                        if i < n and t[i].value == "(":
                            i = self.p.match.get(i, i) + 1
                    continue
                i += 1
        if i < n and t[i].kind == "ident" and t[i].value == "where":
            while i < n and t[i].value != "{" and not (t[i].newline_before and not _joins_header(t[i - 1], t[i]) and t[i].value != "where"):
                i += 1
        body_open = -1
        if i < n and t[i].value == "{" and not (t[i].newline_before and kind not in ("class", "object", "interface", "companion", "enum", "annotation")):
            body_open = i
        if body_open >= 0:
            body_close = self.p.match[body_open]
            end_i = body_close + 1
            body = (body_open + 1, body_close)
            header_line = t[body_open].line
        else:
            end_i = i
            body = None
            header_line = t[end_i - 1].end_line if end_i > start_i else t[start_i].line
        unit = self.make(kind, name, (name,), start_i, end_i, name_tok if name_tok >= 0 else kw, header_line, mods, anns, parent, supertypes=tuple(supers), body=body)
        for a, b in ctor_params:
            prop = self.ctor_property(a, b, unit)
            if prop is not None:
                unit.children.append(prop)
        if body is not None:
            self.kotlin_body(unit, body[0], body[1])
        return unit

    def split_params(self, a: int, b: int) -> list[tuple[int, int]]:
        t = self.t
        out: list[tuple[int, int]] = []
        start = a
        i = a
        while i <= b:
            if i == b or (t[i].kind == "punct" and t[i].value == ","):
                if i > start:
                    out.append((start, i))
                start = i + 1
                i += 1
                continue
            if t[i].kind == "punct" and t[i].value in "([{":
                i = self.p.match.get(i, i) + 1
                continue
            if t[i].kind == "punct" and t[i].value == "<":
                e = skip_angles(t, i)
                i = e if e > 0 else i + 1
                continue
            i += 1
        return out

    def ctor_property(self, a: int, b: int, owner: Unit) -> Unit | None:
        """A primary constructor parameter that is a property (`val` or `var`): a unit of the type, declared in its header."""
        t = self.t
        j, mods, anns = self.leading(a)
        if j < b and t[j].kind == "ident" and t[j].value in ("val", "var") and j + 1 < b and t[j + 1].kind == "ident":
            return self.make("property", t[j + 1].value, (t[j + 1].value,), a, b, j + 1, t[a].line, mods, anns, owner, in_header=True)
        return None

    def kotlin_body(self, unit: Unit, a: int, b: int) -> None:
        t = self.t
        i = a
        if unit.kind == "enum":
            i = self.kotlin_enum_entries(unit, i, b)
        while i < b:
            if t[i].kind == "punct" and t[i].value == ";":
                i += 1
                continue
            child, j = self.member(i, unit, False)
            if child is None:
                j = self.kotlin_skip(i, b)
                if j > i:
                    unit.children.append(self.make("other", "<other>", ("<other>",), i, j, -1, t[i].line, frozenset(), (), unit))
            else:
                unit.children.append(child)
            i = max(j, i + 1)

    def kotlin_skip(self, i: int, limit: int) -> int:
        j = self.expression_end(i)
        return min(max(j, i + 1), limit)

    def kotlin_enum_entries(self, unit: Unit, i: int, b: int) -> int:
        t = self.t
        while i < b:
            if t[i].kind == "punct" and t[i].value == ";":
                return i + 1
            start = i
            j, mods, anns = self.leading(i)
            if j >= b or t[j].kind != "ident" or t[j].value in ("fun", "val", "var", "class", "object", "interface", "init", "constructor", "companion"):
                return i
            name_tok = j
            j += 1
            if j < b and t[j].value == "(":
                j = self.p.match.get(j, j) + 1
            if j < b and t[j].value == "{":
                j = self.p.match.get(j, j) + 1
            unit.children.append(self.make("enum_constant", t[name_tok].value, (t[name_tok].value,), start, j, name_tok, t[start].line, mods, anns, unit))
            i = j
            if i < b and t[i].value == ",":
                i += 1
                continue
            if i < b and t[i].value == ";":
                return i + 1
            return i
        return i


def skip_angles(t: Sequence[Token], i: int) -> int:
    """The index after the `>` that closes the `<` at index i; -1 when what follows does not read as type arguments or parameters."""
    depth = 0
    n = len(t)
    j = i
    while j < n:
        tk = t[j]
        if tk.kind == "punct":
            v = tk.value
            if v == "<":
                depth += 1
            elif v == ">":
                depth -= 1
                if depth == 0:
                    return j + 1
            elif v in (";", "{", "}", "=", "+", "|", "!"):
                return -1
            elif v in "([":
                # annotations or arrays inside: skip the group
                k = j + 1
                d = 1
                while k < n and d:
                    if t[k].kind == "punct" and t[k].value in "([":
                        d += 1
                    elif t[k].kind == "punct" and t[k].value in ")]":
                        d -= 1
                    k += 1
                j = k
                continue
        j += 1
    return -1


def _continues(prev: Token, nxt: Token) -> bool:
    """Whether the Kotlin expression goes on from `prev` (the last token of a line) to `nxt` (the first of the next)."""
    if prev.kind == "punct" and prev.value in _JOINERS_BEFORE:
        return True
    if nxt.kind == "punct" and nxt.value in (".", "?.", "?:", "&&", "||", "->", ":", "="):
        return True
    if nxt.kind == "ident" and nxt.value in ("else", "catch", "finally", "as", "is", "in", "by", "to", "and", "or", "where"):
        return True
    return False


def _joins_header(prev: Token, nxt: Token) -> bool:
    """Whether a type or function header goes on over a line break."""
    if prev.kind == "punct" and prev.value in (",", ":", "=", "->", "&", "<"):
        return True
    if nxt.kind == "punct" and nxt.value in (",", ":", "=", "->", "&", "{", "(", ".", "?."):
        return True
    if nxt.kind == "ident" and nxt.value in ("where", "by"):
        return True
    return False


def parse_java(text: str) -> Parsed:
    return Parsed(text, False)


def parse_kotlin(text: str) -> Parsed:
    return Parsed(text, True)
