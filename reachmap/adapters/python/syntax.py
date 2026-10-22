"""Python syntax facts from the standard `ast` (and `tokenize` for which lines hold code).

One `Parsed` per source text: the top-level declarations and their spans (decorators included),
the placement of a line (the declaration it sits in, with the dotted class member and the nested
functions around it), imports in every form, the names a declaration references, the uses of a name
(calls, attribute chains, arguments) and the definitions of a name.

A text that does not parse under the Python that runs Reachmap (newer syntax, Python 2, a syntax
error, a lone carriage return that makes line numbers ambiguous) is a parse problem: `Parsed.problems`
says why and nothing is read from it. There is no fallback to regular expressions. Every walk over the
tree is iterative: a source nested a few thousand levels deep must not exhaust the recursion limit.
"""

from __future__ import annotations

import ast
import bisect
import io
import re
import tokenize
import warnings
from dataclasses import dataclass
from typing import Iterator, Sequence

__all__ = ["Declaration", "ImportRecord", "ImportedName", "Parsed", "Placement", "Use", "parse"]

FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
DEFS = FUNCS + (ast.ClassDef,)
ASSIGNS = (ast.Assign, ast.AnnAssign, ast.AugAssign)
COMPOUND = (ast.If, ast.Try, ast.TryStar, ast.With, ast.AsyncWith, ast.For, ast.AsyncFor, ast.While, ast.Match)
TYPE_ALIAS = getattr(ast, "TypeAlias", None)  # 3.12 and later
DYNAMIC_NAMES = frozenset({"eval", "exec", "globals", "locals", "vars"})
MODULE = "<module>"
_LONE_CR = re.compile(r"\r(?!\n)")


@dataclass(frozen=True, slots=True, eq=False)
class Declaration:
    """A top-level definition: a def, an async def, a class, or an assignment to plain names
    (`names` holds every name it binds). Declarations inside module-level `if`, `try`, `with`, `for`,
    `while` and `match` blocks are top-level too. `start` is the first decorator line."""

    name: str
    names: tuple[str, ...]
    kind: str  # function | class | variable
    start: int
    end: int
    node: ast.AST


@dataclass(frozen=True, slots=True)
class Placement:
    """Where a line sits: the top-level declaration, the dotted name (`Class.member` for what is
    inside a class, the declaration's own name otherwise), the functions around the line inside the
    member (outermost first) and the kind of the member."""

    decl: Declaration
    name: str
    nested: tuple[str, ...]
    kind: str


@dataclass(frozen=True, slots=True)
class ImportedName:
    name: str  # `a.b` for `import a.b`; the name for `from m import name`
    asname: str | None
    line: int


@dataclass(frozen=True, slots=True)
class ImportRecord:
    """One import statement (one alias of `import a, b`), or a `from` import, or a literal
    `import_module("x")` / `__import__("x")` call. `kind`: import | from | star | dynamic. `module` is
    the dotted module without leading dots; `level` the number of dots; `line` is the line of the
    statement (of the literal for a dynamic import, of the alias for `import`)."""

    kind: str
    module: str
    level: int
    names: tuple[ImportedName, ...]
    line: int
    type_only: bool = False

    @property
    def specifier(self) -> str:
        return "." * self.level + self.module


@dataclass(frozen=True, slots=True, eq=False)
class Use:
    """A use of a name: `chain` are the attribute names after it (`m.sub.fn(3)` is `m` with chain
    `("sub", "fn")`); `form` is `call` when the whole chain is called, else `reference`."""

    local: str
    line: int
    chain: tuple[str, ...]
    form: str
    call: ast.Call | None
    col: int = 0


def _span(node: ast.AST) -> tuple[int, int]:
    start = node.lineno  # type: ignore[attr-defined]
    for deco in getattr(node, "decorator_list", ()):
        start = min(start, deco.lineno)
    return start, node.end_lineno  # type: ignore[attr-defined]


def _bodies(stmt: ast.stmt) -> Iterator[list[ast.stmt]]:
    """The statement lists inside a compound statement (if, try, with, for, while, match)."""
    if isinstance(stmt, ast.Match):
        for case in stmt.cases:
            yield case.body
    elif isinstance(stmt, COMPOUND):
        for attr in ("body", "orelse", "finalbody"):
            part = getattr(stmt, attr, None)
            if part:
                yield part
        for handler in getattr(stmt, "handlers", ()):
            yield handler.body


def _target_names(target: ast.AST) -> list[str]:
    out: list[str] = []
    stack = [target]
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Name):
            out.append(node.id)
        elif isinstance(node, (ast.Tuple, ast.List)):
            stack.extend(reversed(node.elts))
        elif isinstance(node, ast.Starred):
            stack.append(node.value)
    return out


def _assigned_names(stmt: ast.stmt) -> list[str]:
    """The plain names an assignment statement binds (attribute and subscript stores bind none)."""
    names: list[str] = []
    if isinstance(stmt, ast.Assign):
        for target in stmt.targets:
            names += _target_names(target)
    elif isinstance(stmt, (ast.AnnAssign, ast.AugAssign)):
        names = _target_names(stmt.target)
    return list(dict.fromkeys(names))


def _members(body: Sequence[ast.stmt]) -> Iterator[ast.stmt]:
    """The statements of a body with compound statements replaced by what is inside them."""
    stack = [iter(body)]
    while stack:
        for stmt in stack[-1]:
            if isinstance(stmt, COMPOUND):
                stack.append(iter([s for part in _bodies(stmt) for s in part]))
                break
            yield stmt
        else:
            stack.pop()


_NAMEISH = re.compile(r"^[A-Za-z_][\w.]*(?:\[[\w.\[\], |]*\])?$")


def _string_names(text: str) -> set[str]:
    """The names a string that reads as a type or a class name spells (`"Customer"`, `"list[Customer]"`,
    `"models.Customer"`): a forward reference, an ORM relationship target or an `__all__` entry."""
    if len(text) > 200 or not _NAMEISH.match(text):
        return set()
    try:
        tree = ast.parse(text, mode="eval")
    except (SyntaxError, ValueError, RecursionError):
        return set()
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


def _names_in(root: ast.AST, skip: frozenset[int] = frozenset(), strings: bool = True) -> set[str]:
    """Every name spelled in the tree, in code and in strings that read as a name."""
    out: set[str] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        if node is not root and id(node) in skip:
            continue
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            out.update(node.names)
        elif strings and isinstance(node, ast.Constant) and isinstance(node.value, str):
            out |= _string_names(node.value)
        stack.extend(ast.iter_child_nodes(node))
    return out


def _attribute_names(root: ast.AST, skip: frozenset[int] = frozenset()) -> set[str]:
    out: set[str] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        if node is not root and id(node) in skip:
            continue
        if isinstance(node, ast.Attribute):
            out.add(node.attr)
        elif isinstance(node, ast.keyword) and node.arg:
            out.add(node.arg)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.isidentifier():
            out.add(node.value)
        stack.extend(ast.iter_child_nodes(node))
    return out


def _root_name(node: ast.AST) -> str | None:
    """The name a callee starts from: `a` for `a()`, `a.b.c()`, `a[0]()` and `a()()`."""
    while True:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            node = node.value
        elif isinstance(node, (ast.Call, ast.Subscript)):
            node = node.func if isinstance(node, ast.Call) else node.value
        else:
            return None


def _is_type_checking(test: ast.expr) -> int:
    """1 when the test is `TYPE_CHECKING` (or `typing.TYPE_CHECKING`), -1 for `not TYPE_CHECKING`, else 0."""
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return 1
    if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
        return 1
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not) and _is_type_checking(test.operand) == 1:
        return -1
    return 0


class Parsed:
    """A parsed source text. See the module docstring."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.lines = text.split("\n")
        self._bom = text.startswith("\ufeff")
        self.problems: tuple[str, ...] = ()
        self.tree: ast.Module | None = None
        self._declarations: tuple[Declaration, ...] | None = None
        self._starts: list[int] = []
        self._code_lines: frozenset[int] | None = None
        self._imports: tuple[ImportRecord, ...] | None = None
        self._unresolved: tuple[int, ...] = ()
        self._refs: dict[int, set[str]] = {}
        self._module_refs: set[str] | None = None
        self._uses: dict[str, list[Use]] | None = None
        self._line_bytes: dict[int, bytes] = {}
        self._parse()

    # ---- parsing

    def _parse(self) -> None:
        if _LONE_CR.search(self.text):
            self.problems = ("a carriage return that is not part of a CRLF line ending: git and Python count the lines differently",)
            return
        source = self.text[1:] if self.text.startswith("\ufeff") else self.text
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self.tree = ast.parse(source)
        except SyntaxError as exc:  # includes IndentationError and TabError
            where = f" (line {exc.lineno})" if exc.lineno else ""
            self.problems = (f"{exc.msg}{where}",)
        except (ValueError, RecursionError, MemoryError, OverflowError) as exc:
            self.problems = (f"the file could not be parsed: {type(exc).__name__}: {str(exc)[:120]}",)

    def line_text(self, number: int) -> str:
        if 1 <= number <= len(self.lines):
            return self.lines[number - 1].rstrip("\r")
        return ""

    # ---- declarations

    @property
    def declarations(self) -> tuple[Declaration, ...]:
        if self._declarations is None:
            self._declarations = self._collect()
            self._starts = [d.start for d in self._declarations]
        return self._declarations

    def _collect(self) -> tuple[Declaration, ...]:
        if self.tree is None:
            return ()
        out: list[Declaration] = []
        for stmt in _members(self.tree.body):
            if isinstance(stmt, DEFS):
                start, end = _span(stmt)
                out.append(Declaration(stmt.name, (stmt.name,), "class" if isinstance(stmt, ast.ClassDef) else "function", start, end, stmt))
            elif isinstance(stmt, ASSIGNS):
                names = _assigned_names(stmt)
                if names:
                    value = getattr(stmt, "value", None)
                    kind = "function" if isinstance(value, ast.Lambda) else "variable"
                    out.append(Declaration(names[0], tuple(names), kind, stmt.lineno, stmt.end_lineno or stmt.lineno, stmt))
            elif TYPE_ALIAS is not None and isinstance(stmt, TYPE_ALIAS) and isinstance(stmt.name, ast.Name):
                out.append(Declaration(stmt.name.id, (stmt.name.id,), "variable", stmt.lineno, stmt.end_lineno or stmt.lineno, stmt))
        out.sort(key=lambda d: (d.start, d.end))
        return tuple(out)

    def placed(self, line: int) -> Declaration | None:
        """The top-level declaration whose span holds the line (by number, never by text)."""
        decls = self.declarations
        i = bisect.bisect_right(self._starts, line) - 1
        if i >= 0 and decls[i].start <= line <= decls[i].end:
            return decls[i]
        return None

    def placements(self, line: int) -> list[Placement]:
        """Every placement of the line: more than one when declarations share the line (`x = 1; y = 2`)."""
        decls = self.declarations
        i = bisect.bisect_right(self._starts, line) - 1
        out: list[Placement] = []
        while i >= 0 and decls[i].end >= line:
            if decls[i].start <= line:
                out.append(self._placement_of(decls[i], line))
            i -= 1
        out.reverse()
        return out

    def enclosing(self, line: int) -> str:
        """The name of the top-level declaration the line sits in; `<module>` outside every one."""
        decl = self.placed(line)
        return decl.name if decl is not None else MODULE

    def placement(self, line: int) -> Placement | None:
        decl = self.placed(line)
        return None if decl is None else self._placement_of(decl, line)

    def _placement_of(self, decl: Declaration, line: int) -> Placement:
        node = decl.node
        if isinstance(node, ast.ClassDef):
            return self._class_placement(decl, node, [node.name], line)
        if isinstance(node, FUNCS):
            return Placement(decl, decl.name, self._nested(node, line), "function")
        return Placement(decl, decl.name, (), decl.kind)

    def _class_placement(self, decl: Declaration, cls: ast.ClassDef, path: list[str], line: int) -> Placement:
        """Descend through the members of a class: `Class.method`, `Class.field`, `Class.Inner.x`."""
        while True:
            member = None
            for stmt in _members(cls.body):
                if isinstance(stmt, DEFS):
                    lo, hi = _span(stmt)
                elif isinstance(stmt, ASSIGNS) and _assigned_names(stmt):
                    lo, hi = stmt.lineno, stmt.end_lineno or stmt.lineno
                else:
                    continue
                if lo <= line <= hi:
                    member = stmt
                    break
            if member is None:
                return Placement(decl, ".".join(path), (), "class")
            if isinstance(member, ast.ClassDef):
                path = path + [member.name]
                cls = member
                continue
            if isinstance(member, FUNCS):
                return Placement(decl, ".".join(path + [member.name]), self._nested(member, line), "function")
            name = _assigned_names(member)[0]
            kind = "function" if isinstance(getattr(member, "value", None), ast.Lambda) else "variable"
            return Placement(decl, ".".join(path + [name]), (), kind)

    @staticmethod
    def _nested(fn: ast.AST, line: int) -> tuple[str, ...]:
        """The named functions and classes inside `fn` that contain the line, outermost first."""
        chain: list[str] = []
        current = fn
        while True:
            found = None
            stack = list(reversed(current.body))  # type: ignore[attr-defined]
            while stack:
                stmt = stack.pop()
                if isinstance(stmt, DEFS):
                    lo, hi = _span(stmt)
                    if lo <= line <= hi:
                        found = stmt
                        break
                elif isinstance(stmt, COMPOUND):
                    for part in _bodies(stmt):
                        stack.extend(reversed(part))
            if found is None:
                return tuple(chain)
            chain.append(found.name)
            current = found

    # ---- code lines

    @property
    def code_lines(self) -> frozenset[int]:
        """The lines outside every declaration that hold code: a token that is not a comment (a
        module docstring is not code either). Lines inside a declaration are not listed: its span
        holds them."""
        if self._code_lines is None:
            self._code_lines = self._compute_code_lines()
        return self._code_lines

    def _compute_code_lines(self) -> frozenset[int]:
        if self.tree is None:
            return frozenset()
        skip = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER}
        encoding = getattr(tokenize, "ENCODING", None)
        if encoding is not None:
            skip.add(encoding)
        lines: set[int] = set()
        try:
            for tok in tokenize.generate_tokens(io.StringIO(self.text[1:] if self._bom else self.text).readline):
                if tok.type not in skip:
                    lines.update(range(tok.start[0], tok.end[0] + 1))
        except (tokenize.TokenError, SyntaxError, IndentationError):
            lines = {n for n, t in enumerate(self.lines, 1) if t.strip()}  # cannot tell comments from code: all text counts
        body = self.tree.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            lines.difference_update(range(body[0].lineno, (body[0].end_lineno or body[0].lineno) + 1))
        for decl in self.declarations:
            lines.difference_update(range(decl.start, decl.end + 1))
        return frozenset(lines)

    # ---- imports

    @property
    def imports(self) -> tuple[ImportRecord, ...]:
        if self._imports is None:
            self._scan_imports()
        return self._imports  # type: ignore[return-value]

    @property
    def unresolved_dynamic(self) -> tuple[int, ...]:
        """Lines of `import_module(x)` and `__import__(x)` calls whose argument is not a literal."""
        if self._imports is None:
            self._scan_imports()
        return self._unresolved

    def _scan_imports(self) -> None:
        records: list[ImportRecord] = []
        unresolved: list[int] = []
        if self.tree is None:
            self._imports, self._unresolved = (), ()
            return
        calls: list[tuple[ast.Call, bool]] = []
        importlib_names: set[str] = set()
        loader_names: set[str] = set()
        stack: list[tuple[ast.AST, bool]] = [(self.tree, False)]
        while stack:
            node, type_only = stack.pop()
            if isinstance(node, ast.Import):
                for alias in node.names:
                    records.append(ImportRecord("import", alias.name, 0, (ImportedName(alias.name, alias.asname, alias.lineno),), alias.lineno, type_only))
                    if alias.name.split(".")[0] == "importlib":
                        importlib_names.add(alias.asname or "importlib")
                continue
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = tuple(ImportedName(a.name, a.asname, a.lineno) for a in node.names if a.name != "*")
                mline = self._module_line(node)
                if any(a.name == "*" for a in node.names):
                    records.append(ImportRecord("star", module, node.level, (), mline, type_only))
                if names:
                    records.append(ImportRecord("from", module, node.level, names, mline, type_only))
                if module == "importlib" and node.level == 0:
                    loader_names.update(a.asname or a.name for a in node.names if a.name == "import_module")
                continue
            if isinstance(node, ast.Call):
                calls.append((node, type_only))
            if isinstance(node, ast.If):
                mark = _is_type_checking(node.test)
                stack.append((node.test, type_only))
                stack.extend((s, type_only or mark == 1) for s in node.body)
                stack.extend((s, type_only or mark == -1) for s in node.orelse)
                continue
            stack.extend((child, type_only) for child in ast.iter_child_nodes(node))
        for call, type_only in calls:
            if not self._is_dynamic_import(call, importlib_names, loader_names):
                continue
            target = self._dynamic_target(call)
            if target is None:
                unresolved.append(call.lineno)
                continue
            module, line = target
            records.append(ImportRecord("dynamic", module, 0, (), line, type_only))
        records.sort(key=lambda r: (r.line, r.kind, r.module))
        self._imports = tuple(records)
        self._unresolved = tuple(sorted(set(unresolved)))

    def _module_line(self, node: ast.ImportFrom) -> int:
        """The line that names the module of a `from` import (a `\\` continuation can put it below the
        `from`); the statement's first line when it cannot be told."""
        if not node.module:
            return node.lineno
        first = re.compile(r"\b" + re.escape(node.module.split(".")[0]) + r"\b")
        for number in range(node.lineno, (node.end_lineno or node.lineno) + 1):
            text = self.line_text(number)
            if number == node.lineno:
                at = text.find("from")
                text = text[at + 4:] if at >= 0 else text
            if first.search(text):
                return number
        return node.lineno

    @staticmethod
    def _is_dynamic_import(call: ast.Call, importlib_names: set[str], loader_names: set[str]) -> bool:
        func = call.func
        if isinstance(func, ast.Name):
            return func.id == "__import__" or func.id in loader_names
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.attr == "import_module" and func.value.id in importlib_names:
                return True
            if func.attr == "__import__" and func.value.id in importlib_names | {"builtins"}:
                return True
        return False

    @staticmethod
    def _literal(node: ast.AST | None) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    def _dynamic_target(self, call: ast.Call) -> tuple[str, int] | None:
        """(absolute module, line) of a literal dynamic import; None when it is not a literal, or a
        relative name without a literal package."""
        if not call.args or isinstance(call.args[0], ast.Starred):
            return None
        name = self._literal(call.args[0])
        if not name:
            return None
        line = call.args[0].lineno
        if not name.startswith("."):
            return name, line
        package = self._literal(call.args[1]) if len(call.args) > 1 else None
        for kw in call.keywords:
            if kw.arg == "package":
                package = self._literal(kw.value)
        if not package:
            return None
        dots = len(name) - len(name.lstrip("."))
        parts = package.split(".")
        if dots - 1 >= len(parts):
            return None
        base = parts[: len(parts) - (dots - 1)]
        rest = name[dots:]
        return ".".join(base + ([rest] if rest else [])), line

    # ---- references

    def decl_references(self, decl: Declaration) -> set[str]:
        """Every name spelled inside the declaration (its decorators, bases, defaults, annotations,
        body and nested scopes). Shadowing is not understood: more names, never fewer."""
        key = id(decl)
        if key not in self._refs:
            self._refs[key] = _names_in(decl.node)
        return self._refs[key]

    def decorator_names(self, decl: Declaration) -> set[str]:
        """Every name spelled in a decorator of the declaration or of a def or class inside it: what the
        declaration registers itself with (`@router.get("/x")` registers on `router`)."""
        out: set[str] = set()
        stack: list[ast.AST] = [decl.node]
        while stack:
            node = stack.pop()
            for deco in getattr(node, "decorator_list", ()):
                out |= _names_in(deco, strings=False)
            stack.extend(ast.iter_child_nodes(node))
        return out

    def decl_attributes(self, decl: Declaration) -> set[str]:
        """Every attribute name a declaration may read or set on another object: `.name` in `doc.name`,
        a keyword argument (`Invoice(amount=1)`) and a string that reads as a name (`getattr(doc, "name")`).
        What a change to a class member can reach by name, whatever the object."""
        return _attribute_names(decl.node)

    def module_attributes(self) -> set[str]:
        """The same for module-level code (outside every declaration)."""
        if self.tree is None:
            return set()
        return _attribute_names(self.tree, frozenset(id(d.node) for d in self.declarations))

    def import_time_calls(self, decl: Declaration) -> set[str]:
        """The names a declaration calls when the module is imported: the value of an assignment, the
        decorators, defaults, bases and keywords, and the statements of a class body, but not the body of a
        function or a lambda. A call of a name whose behaviour changed runs for every importer."""
        out: set[str] = set()
        stack: list[ast.AST] = [decl.node]
        while stack:
            node = stack.pop()
            if isinstance(node, DEFS):
                for deco in node.decorator_list:  # applying a decorator is a call, with or without parentheses
                    root = _root_name(deco)
                    if root is not None:
                        out.add(root)
            if isinstance(node, FUNCS):
                eager: list[ast.AST] = list(node.decorator_list) + list(node.args.defaults) + [d for d in node.args.kw_defaults if d is not None]
                stack.extend(eager)
                continue
            if isinstance(node, ast.Lambda):
                continue
            if isinstance(node, ast.Call):
                root = _root_name(node.func)
                if root is not None:
                    out.add(root)
            stack.extend(ast.iter_child_nodes(node))
        return out

    def global_names(self, decl: Declaration) -> set[str]:
        """The module names a declaration may write: those in a `global` statement inside it."""
        out: set[str] = set()
        stack: list[ast.AST] = [decl.node]
        while stack:
            node = stack.pop()
            if isinstance(node, ast.Global):
                out.update(node.names)
            stack.extend(ast.iter_child_nodes(node))
        return out

    def module_references(self) -> set[str]:
        """Every name spelled in module-level code: what is outside every declaration."""
        if self._module_refs is None:
            if self.tree is None:
                self._module_refs = set()
            else:
                skip = frozenset(id(d.node) for d in self.declarations)
                self._module_refs = _names_in(self.tree, skip)
        return self._module_refs

    def uses_dynamic_access(self) -> bool:
        """Whether names may be reached in ways the walk cannot follow: `eval`, `exec`, `globals()`,
        `locals()`, `vars()`, `sys.modules[...]` or a module `__getattr__`."""
        if self.tree is None:
            return False
        stack: list[ast.AST] = [self.tree]
        modules_names: set[str] = set()  # `from sys import modules [as m]`
        subscripted: set[str] = set()
        while stack:
            node = stack.pop()
            if isinstance(node, ast.Name) and node.id in DYNAMIC_NAMES:
                return True
            if isinstance(node, ast.Attribute) and node.attr == "modules" and isinstance(node.value, ast.Name) and node.value.id == "sys":
                return True  # sys.modules[...] reaches any module by name
            if isinstance(node, ast.ImportFrom) and node.module == "sys" and node.level == 0:
                modules_names.update(a.asname or a.name for a in node.names if a.name == "modules")
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
                subscripted.add(node.value.id)
            stack.extend(ast.iter_child_nodes(node))
        if modules_names & subscripted:
            return True
        return any("__getattr__" in d.names for d in self.declarations)

    # ---- uses

    def uses(self, local: str) -> tuple[Use, ...]:
        """The uses of a name (loads: stores are not uses), in source order."""
        if self._uses is None:
            self._index_uses()
        return tuple(self._uses.get(local, ()))  # type: ignore[union-attr]

    def _index_uses(self) -> None:
        index: dict[str, list[Use]] = {}
        self._uses = index
        if self.tree is None:
            return
        # (node, the call it is the callee of, the line and column to cite when the node comes from a string)
        stack: list[tuple[ast.AST, ast.Call | None, tuple[int, int] | None]] = [(self.tree, None, None)]
        while stack:
            node, call, at = stack.pop()
            if isinstance(node, (ast.Attribute, ast.Name)):
                chain: list[str] = []
                cur = node
                while isinstance(cur, ast.Attribute):
                    chain.append(cur.attr)
                    cur = cur.value
                if isinstance(cur, ast.Name):
                    if isinstance(cur.ctx, ast.Load):
                        form = "call" if call is not None and call.func is node else "reference"
                        line, col = at if at is not None else (cur.lineno, cur.col_offset)
                        index.setdefault(cur.id, []).append(Use(cur.id, line, tuple(reversed(chain)), form, call if form == "call" else None, col))
                else:
                    stack.append((cur, None, at))
                continue
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and "." in node.value:
                # a quoted annotation such as "m.Invoice" uses `m` (a bare quoted name is not a use: see decl_references)
                if len(node.value) <= 200 and _NAMEISH.match(node.value):
                    try:
                        sub = ast.parse(node.value, mode="eval").body
                    except (SyntaxError, ValueError, RecursionError):
                        sub = None
                    if sub is not None:
                        stack.append((sub, None, (node.lineno, node.col_offset)))
                continue
            for child in ast.iter_child_nodes(node):
                stack.append((child, node if isinstance(node, ast.Call) and child is node.func else None, at))
        for items in index.values():
            items.sort(key=lambda u: (u.line, u.col))

    def args_of(self, use: Use) -> tuple[str, ...]:
        """The source text of each argument of the call a use is the callee of (empty for a reference)."""
        call = use.call
        if call is None:
            return ()
        nodes: list[ast.AST] = [*call.args, *call.keywords]
        nodes.sort(key=lambda n: (n.lineno, n.col_offset))  # type: ignore[attr-defined]
        return tuple(self._segment(n) for n in nodes)

    def _line_as_bytes(self, number: int) -> bytes:
        if number not in self._line_bytes:
            text = self.line_text(number)
            if number == 1 and self._bom:
                text = text[1:]  # `ast` counts columns without the byte order mark
            self._line_bytes[number] = text.encode("utf-8", "surrogatepass")
        return self._line_bytes[number]

    def _segment(self, node: ast.AST) -> str:
        """The source text of a node (columns are UTF-8 byte offsets)."""
        l1, c1 = node.lineno, node.col_offset  # type: ignore[attr-defined]
        l2, c2 = node.end_lineno, node.end_col_offset  # type: ignore[attr-defined]
        if l1 == l2:
            return self._line_as_bytes(l1)[c1:c2].decode("utf-8", "replace")
        parts = [self._line_as_bytes(l1)[c1:]]
        parts.extend(self._line_as_bytes(n) for n in range(l1 + 1, l2))
        parts.append(self._line_as_bytes(l2)[:c2])
        return b"\n".join(parts).decode("utf-8", "replace")

    # ---- definitions

    def definition_lines(self, name: str) -> list[int]:
        """The lines that define `name`: a def, async def or class at any depth, and an assignment or
        annotated assignment (`interval: int`) at module or class level, also inside `if`, `try`, `with`,
        `for`, `while` and `match` blocks there. Imports, references, attribute reads and keyword arguments
        are not definitions; nor is a local variable of a function."""
        if self.tree is None:
            return []
        out: list[int] = []
        stack: list[tuple[Sequence[ast.stmt], bool]] = [(self.tree.body, False)]  # (statements, inside a function)
        while stack:
            body, in_function = stack.pop()
            for stmt in body:
                if isinstance(stmt, DEFS):
                    if stmt.name == name:
                        out.append(stmt.lineno)
                    stack.append((stmt.body, in_function or isinstance(stmt, FUNCS)))
                elif isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                    if not in_function and name in _assigned_names(stmt):
                        out.append(stmt.lineno)
                elif TYPE_ALIAS is not None and isinstance(stmt, TYPE_ALIAS):
                    if not in_function and isinstance(stmt.name, ast.Name) and stmt.name.id == name:
                        out.append(stmt.lineno)
                elif isinstance(stmt, COMPOUND):
                    for part in _bodies(stmt):
                        stack.append((part, in_function))
        return sorted(out)


def parse(text: str) -> Parsed:
    return Parsed(text)
