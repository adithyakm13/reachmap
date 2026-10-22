"""What a read of a model's field is a read of: the receiver, in the consumer's Python code.

A name match proves little (`resp.status` on a response is not the `status` of a model), so a read is told apart by its
receiver. It is *proven* when the receiver is provably the model in the function that reads it:

- a parameter or a variable annotated with the model (`Invoice`, `Invoice | None`, `Optional[Invoice]`, `Union[Invoice, None]`,
  `Annotated[Invoice, ...]`, a quoted annotation, `module.Invoice`), following the import aliases of the package;
- a variable bound from `Invoice(...)`, `Invoice.model_validate(...)` and the other constructors, or from another proven
  variable, or narrowed with `isinstance(x, Invoice)`;
- the loop or comprehension variable of a value annotated `list[Invoice]` (or `Sequence`, `Iterable`, `set`, `tuple` of it);
- for a keyword, the callee being the model.

Scopes do not leak: a variable is proven in its function and in the functions nested in it. A subscript is never proven (a
model is not subscripted). Everything else is *unproven*: the edge then needs the name itself to be distinctive and the
consumer to define it nowhere (`defines_name`). Limit: an unproven read of a name that only the producer defines (`resp.status`)
passes both and is taken as a read. Everything here is `ast`: a comment or a string is never a read.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any

__all__ = ["ModelRefs", "construct_sites", "defines_name", "read_sites"]

CONSTRUCTORS = frozenset({"model_validate", "model_validate_json", "model_validate_strings", "model_construct", "parse_obj", "parse_raw", "construct", "from_orm"})
COLLECTIONS = frozenset({"list", "List", "Sequence", "Iterable", "Iterator", "Collection", "set", "Set", "frozenset", "FrozenSet", "tuple", "Tuple", "deque", "Deque"})
GETATTRS = frozenset({"getattr", "hasattr", "setattr", "delattr"})


@dataclass(frozen=True, slots=True)
class ModelRefs:
    """The model a change is about, as a consumer file can name it: the class (`owner`) imported from the package `marker`
    (None when the package is not known: any import of the class counts)."""

    owner: str
    marker: str | None = None


def _model_names(parsed: Any, refs: ModelRefs) -> set[str]:
    """The local names the owner class is bound to by the file's imports (aliases followed)."""
    names: set[str] = set()
    for rec in parsed.imports:
        if rec.level or rec.kind != "from":
            continue
        if refs.marker and not (rec.module == refs.marker or rec.module.startswith(refs.marker + ".")):
            continue
        names.update(n.asname or n.name for n in rec.names if n.name == refs.owner)
    return names


class _Types:
    """The expressions of one file that name the model or hold it."""

    def __init__(self, parsed: Any, refs: ModelRefs) -> None:
        self.refs = refs
        self.names = _model_names(parsed, refs)

    def model(self, node: ast.AST | None) -> bool:
        """The class itself: a name bound to it, or `anything.Invoice`."""
        return (isinstance(node, ast.Name) and node.id in self.names) or (isinstance(node, ast.Attribute) and node.attr == self.refs.owner)

    def call(self, node: ast.AST | None) -> bool:
        """A call that makes the model: `Invoice(...)` and `Invoice.model_validate(...)`."""
        if not isinstance(node, ast.Call):
            return False
        f = node.func
        return self.model(f) or (isinstance(f, ast.Attribute) and f.attr in CONSTRUCTORS and self.model(f.value))

    def annotation(self, ann: ast.AST | None) -> bool:
        if ann is None:
            return False
        if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
            try:
                return self.annotation(ast.parse(ann.value, mode="eval").body)
            except (SyntaxError, ValueError, RecursionError):
                return False
        if self.model(ann):
            return True
        if isinstance(ann, ast.BinOp) and isinstance(ann.op, ast.BitOr):
            return self.annotation(ann.left) or self.annotation(ann.right)
        if isinstance(ann, ast.Subscript):
            base = ann.value.attr if isinstance(ann.value, ast.Attribute) else ann.value.id if isinstance(ann.value, ast.Name) else ""
            items = ann.slice.elts if isinstance(ann.slice, ast.Tuple) else [ann.slice]
            if base in ("Optional", "Union"):
                return any(self.annotation(i) for i in items)
            if base == "Annotated" and items:
                return self.annotation(items[0])
        return False

    def collection(self, ann: ast.AST | None) -> bool:
        """`list[Invoice]` and the like (also `Optional[list[Invoice]]` and a quoted one)."""
        if ann is None:
            return False
        if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
            try:
                return self.collection(ast.parse(ann.value, mode="eval").body)
            except (SyntaxError, ValueError, RecursionError):
                return False
        if isinstance(ann, ast.BinOp) and isinstance(ann.op, ast.BitOr):
            return self.collection(ann.left) or self.collection(ann.right)
        if isinstance(ann, ast.Subscript):
            base = ann.value.attr if isinstance(ann.value, ast.Attribute) else ann.value.id if isinstance(ann.value, ast.Name) else ""
            items = ann.slice.elts if isinstance(ann.slice, ast.Tuple) else [ann.slice]
            if base in COLLECTIONS:
                return any(self.annotation(i) for i in items)
            if base in ("Optional", "Union", "Annotated"):
                return any(self.collection(i) for i in (items[:1] if base == "Annotated" else items))
        return False


def _own_nodes(body: list[ast.stmt]) -> Any:
    """Every node of a scope's statements without those of the functions and lambdas nested in it."""
    stack: list[ast.AST] = list(body)
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _bind(types: _Types, body: list[ast.stmt], typed: set[str], colls: set[str]) -> None:
    """What the statements of a scope make provably the model: variables annotated or assigned, loop variables, isinstance."""
    for _ in range(2):  # a second pass lets `d = b` follow `b = Invoice(...)` written after it
        for node in _own_nodes(body):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if types.annotation(node.annotation):
                    typed.add(node.target.id)
                elif types.collection(node.annotation):
                    colls.add(node.target.id)
                elif node.value is not None and types.call(node.value):
                    typed.add(node.target.id)
            elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                v, name = node.value, node.targets[0].id
                if types.call(v) or (isinstance(v, ast.Name) and v.id in typed):
                    typed.add(name)
                elif (isinstance(v, ast.Name) and v.id in colls) or (isinstance(v, (ast.List, ast.Tuple)) and v.elts and all(types.call(e) for e in v.elts)):
                    colls.add(name)
            elif isinstance(node, (ast.For, ast.AsyncFor)) and isinstance(node.target, ast.Name):
                if isinstance(node.iter, ast.Name) and node.iter.id in colls:
                    typed.add(node.target.id)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "isinstance" and len(node.args) == 2:
                if isinstance(node.args[0], ast.Name) and types.model(node.args[1]):
                    typed.add(node.args[0].id)


class _Scan:
    def __init__(self, parsed: Any, name: str, refs: ModelRefs | None, as_class: bool) -> None:
        self.tree = parsed.tree
        self.name = name
        self.refs = refs
        self.as_class = as_class
        self.types = _Types(parsed, refs) if refs is not None else None
        self.found: set[tuple[int, str, bool]] = set()

    def run(self) -> list[tuple[int, str, bool]]:
        if self.tree is None:
            return []
        typed: set[str] = set()
        colls: set[str] = set()
        if self.types is not None:
            _bind(self.types, self.tree.body, typed, colls)
        for stmt in self.tree.body:
            self.visit(stmt, typed, colls)
        return sorted(self.found)

    def proven_value(self, node: ast.AST | None, typed: set[str]) -> bool:
        if self.types is None:
            return False
        return (isinstance(node, ast.Name) and node.id in typed) or self.types.call(node)

    def visit(self, node: ast.AST, typed: set[str], colls: set[str]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            t, c = set(typed), set(colls)
            a = node.args
            for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs, *([a.vararg] if a.vararg else []), *([a.kwarg] if a.kwarg else [])]:
                t.discard(arg.arg)  # a parameter shadows what the outer scope knew of its name
                c.discard(arg.arg)
                ann = getattr(arg, "annotation", None)
                if self.types is not None and self.types.annotation(ann):
                    t.add(arg.arg)
                elif self.types is not None and self.types.collection(ann):
                    c.add(arg.arg)
            body = node.body if isinstance(node.body, list) else []
            if self.types is not None:
                _bind(self.types, body, t, c)
            for deco in getattr(node, "decorator_list", ()):
                self.visit(deco, typed, colls)
            for d in [*a.defaults, *a.kw_defaults]:
                if d is not None:
                    self.visit(d, typed, colls)
            for child in body if body else [node.body]:
                self.visit(child, t, c)
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            t = set(typed)
            for gen in node.generators:
                self.visit(gen.iter, t, colls)
                if isinstance(gen.target, ast.Name):
                    if isinstance(gen.iter, ast.Name) and gen.iter.id in colls:
                        t.add(gen.target.id)
                    else:
                        t.discard(gen.target.id)
                for cond in gen.ifs:
                    self.visit(cond, t, colls)
            for part in ([node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]):
                self.visit(part, t, colls)
            return
        self.read(node, typed)
        for child in ast.iter_child_nodes(node):
            self.visit(child, typed, colls)

    def read(self, node: ast.AST, typed: set[str]) -> None:
        name = self.name
        if self.as_class:
            if isinstance(node, ast.Name) and node.id == name:
                self.found.add((node.lineno, "name", True))
            elif isinstance(node, ast.ImportFrom):
                self.found.update((a.lineno, "import", True) for a in node.names if a.name == name)  # a removed or renamed class breaks the import itself
            return
        if isinstance(node, ast.Attribute) and node.attr == name:
            if isinstance(node.ctx, ast.Store) and isinstance(node.value, ast.Name) and node.value.id in ("self", "cls"):
                return  # `self.name = ...` defines the attribute of the class it sits in: `defines_name` counts it
            self.found.add((node.end_lineno or node.lineno, "attribute", self.proven_value(node.value, typed)))
        elif isinstance(node, ast.Subscript):
            sl = node.slice
            if isinstance(sl, ast.Constant) and sl.value == name:
                self.found.add((sl.lineno, "subscript", False))  # a model is not subscripted: whatever this is, it is not proven to be one
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == name:
                    self.found.add((kw.lineno, "keyword", self.types is not None and self.types.model(node.func)))
            if isinstance(node.func, ast.Name) and node.func.id in GETATTRS and len(node.args) >= 2:
                arg = node.args[1]
                if isinstance(arg, ast.Constant) and arg.value == name:
                    self.found.add((arg.lineno, "getattr", self.proven_value(node.args[0], typed)))


def read_sites(parsed: Any, name: str, refs: ModelRefs | None = None, *, as_class: bool = False) -> list[tuple[int, str, bool]]:
    """The (line, form, proven) of every use of `name` as a field in the code of a parsed Python file: `.name`, `name=` as a
    keyword, `["name"]` and `getattr(x, "name")`; for a class, every `name` identifier and the import of it. `proven` is
    whether the receiver is provably the model `refs` names (always False without `refs`). A file that did not parse has
    no tree: the caller treats it as unread, not as empty."""
    return _Scan(parsed, name, refs, as_class).run()


def construct_sites(parsed: Any, refs: ModelRefs) -> list[int]:
    """The lines that make the model: `Invoice(...)` (also with `**data`), `Invoice.model_validate(...)`, `parse_obj`, ...
    through the file's import of the class (aliases followed). Only calls the file spells out: a factory somewhere else
    that builds the model is not seen."""
    if parsed.tree is None:
        return []
    types = _Types(parsed, refs)
    if not types.names:
        return []
    return sorted({n.lineno for n in ast.walk(parsed.tree) if types.call(n)})


def defines_name(parsed: Any, name: str) -> int:
    """How many times the file defines `name` as something a read of `.name` could be a read of: a def or a class (anywhere:
    a method is one), an assignment or annotated field at module or class level, and `self.name = ...`. A local variable, a
    read and a keyword argument define nothing."""
    tree = parsed.tree
    if tree is None:
        return 0
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            count += 1
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                for sub in (t.elts if isinstance(t, (ast.Tuple, ast.List)) else [t]):
                    if isinstance(sub, ast.Attribute) and sub.attr == name and isinstance(sub.value, ast.Name) and sub.value.id in ("self", "cls"):
                        count += 1
    bodies: list[list[ast.stmt]] = [tree.body] + [n.body for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    for body in bodies:
        for stmt in body:
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target] if isinstance(stmt, (ast.AnnAssign, ast.AugAssign)) else []
            for t in targets:
                for sub in (t.elts if isinstance(t, (ast.Tuple, ast.List)) else [t]):
                    if isinstance(sub, ast.Name) and sub.id == name:
                        count += 1
    return count
