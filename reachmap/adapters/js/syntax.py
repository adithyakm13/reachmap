"""Imports, exports, top-level declarations and their spans, uses, guards and definitions.

All of it works on the tokens of `lexer.tokenize`, so a word in a comment or a string is never
code. This is not a parser of the language. It follows brackets (a `{` after a token that
expects a type is a type literal, not a body), splits statements at `;` and at line breaks
where JavaScript's automatic semicolon rules would end one, and recognises the declaration
shapes TypeScript projects use. What it cannot follow it leaves out rather than guesses; the
limits are listed in docs/briefs/M5-js-adapter.md.

Line numbers are 1-based. A declaration's span starts at the first token of its statement
(decorators and `export` included) and ends at the last token of its body or initializer.
"""

from __future__ import annotations

from dataclasses import dataclass

from reachmap.adapters.js.lexer import Token, tokenize, unquote

__all__ = [
    "Declaration", "ExportEntry", "FunctionInfo", "GuardBlock", "ImportBinding", "ImportRecord", "Param", "Parsed",
    "Use", "parse",
]

# Words that cannot end an expression, so a line break after them continues the statement.
NOT_END = frozenset({
    "typeof", "instanceof", "in", "of", "new", "delete", "void", "await", "yield", "as", "is", "keyof", "extends",
    "satisfies", "const", "let", "var", "function", "class", "export", "default", "import", "async", "else", "throw",
    "case", "do", "implements", "readonly", "declare", "abstract", "static", "type", "interface", "enum", "namespace",
    "module", "if", "for", "while", "switch", "try", "catch", "finally", "with",
})
# Words that continue the previous statement when they start a line.
CONTINUES = frozenset({"else", "catch", "finally", "in", "of", "instanceof", "as", "satisfies", "extends", "implements"})
HARD_STARTERS = frozenset({
    "export", "const", "let", "var", "function", "class", "interface", "type", "enum", "import", "declare", "async",
    "if", "for", "while", "return", "namespace", "abstract", "do", "switch", "try", "throw",
})
TYPE_BRACE_PUNCT = frozenset({":", "|", "&", "<", ",", "(", "[", "=>", "=", "?"})
TYPE_BRACE_WORDS = frozenset({
    "extends", "implements", "keyof", "typeof", "is", "asserts", "infer", "readonly", "new", "as", "satisfies", "in", "of",
})
MODIFIERS = frozenset({"static", "async", "get", "set", "public", "private", "protected", "readonly", "override", "abstract", "declare"})
CONTROL = frozenset({
    "if", "for", "while", "switch", "catch", "function", "return", "with", "typeof", "delete", "new", "await", "yield",
    "super", "import", "require", "void", "throw", "else", "do", "case",
})
ASSIGN_OPS = frozenset({"=", "+=", "-=", "*=", "/=", "%=", "**=", "&&=", "||=", "??=", "&=", "|=", "^=", "<<=", ">>=", ">>>="})
EQUALITY = {"===": True, "==": True, "!==": False, "!=": False}
CALL_BREAK = frozenset({";", "{", "}", ")", "]"})


@dataclass(frozen=True, slots=True)
class ImportBinding:
    imported: str  # a name, "default" or "*"
    local: str
    type_only: bool = False


@dataclass(frozen=True, slots=True)
class ImportRecord:
    """One module reference. `line` is the line holding the specifier (the line cited as
    evidence), `start_line` the first line of the statement. `names` is None for a
    whole-module import (default, namespace, side effect, dynamic, require, `export *`)."""

    kind: str  # import | reexport | export_star | dynamic | require
    specifier: str
    names: tuple[str, ...] | None
    bindings: tuple[ImportBinding, ...]
    type_only: bool
    line: int
    start_line: int


@dataclass(frozen=True, slots=True)
class ExportEntry:
    name: str  # the exported name
    local: str | None  # the local binding it exports, the imported name for a re-export, "*" for `export * as`
    source: str | None  # the specifier of a re-export
    type_only: bool
    line: int


@dataclass(slots=True)
class Declaration:
    """A top-level declaration: `start` and `end` are inclusive lines. `export_names` are the
    names the module exports it under (empty: not exported)."""

    name: str
    kind: str  # function | class | interface | type | enum | namespace | module | variable | default
    start: int
    end: int
    exported: bool
    default: bool
    export_names: tuple[str, ...]
    start_tok: int
    end_tok: int
    name_tok: int
    init_tok: int  # first token of the initializer of a variable, the `function` keyword of a function, else -1


@dataclass(frozen=True, slots=True)
class Use:
    """A use of an imported name. `member` is set for `ns.member`. `args` are the source texts of
    the arguments and `literals` the string value of each argument that is a single string
    literal (else None)."""

    local: str
    member: str | None
    line: int
    form: str  # call | reference
    args: tuple[str, ...]
    literals: tuple[str | None, ...]
    enclosing: str
    tok: int


@dataclass(frozen=True, slots=True)
class Param:
    name: str | None  # None for a destructured parameter
    index: int
    has_default: bool
    rest: bool
    pattern: bool


@dataclass(frozen=True, slots=True)
class FunctionInfo:
    params: tuple[Param, ...]
    body_open: int  # token indexes of the braces of a block body, -1 for an expression body
    body_close: int
    rebound: frozenset[str]  # parameter names assigned, redeclared or shadowed anywhere in the body


@dataclass(frozen=True, slots=True)
class GuardBlock:
    """Lines `first_line` to `last_line` (inclusive; empty when first > last) are reached only
    when the parameter `param` equals one of `literals` (`enter_if_equal`) or none of them. The
    condition is on `guard_line`."""

    param: str
    literals: tuple[str, ...]
    enter_if_equal: bool
    guard_line: int
    first_line: int
    last_line: int

    def permits(self, literal: str) -> bool:
        """Whether a call passing the string `literal` can enter the block."""
        return (literal in self.literals) == self.enter_if_equal


@dataclass(frozen=True, slots=True)
class _Fn:
    name: str
    kind: str  # function | const | method | property
    start_tok: int
    end_tok: int
    start_line: int
    end_line: int


class _Safe(list):
    """A list whose out-of-range integer reads give `default` instead of raising: the scans look a
    few tokens ahead and behind, and the end of a file (or a file cut in the middle of a
    statement) must read as "nothing there", never as a crash."""

    def __init__(self, items, default):
        super().__init__(items)
        self.default = default

    def __getitem__(self, i):
        if isinstance(i, int):
            if i < 0 or i >= len(self):
                return self.default
        return super().__getitem__(i)


def parse(text: str) -> "Parsed":
    return Parsed(text)


class Parsed:
    """A parsed source file."""

    def __init__(self, text: str) -> None:
        lex = tokenize(text)
        self.text = text
        self.lines = text.split("\n")
        self.tokens: list[Token] = _Safe(lex.tokens, Token("eof", "", 1, 1, 0, 0, True))
        self.comments = lex.comments
        self.problems: list[str] = list(lex.problems)
        n = len(self.tokens)
        self._partner = _Safe([-1] * n, -1)
        self._depth = _Safe([0] * n, 0)
        self._enclosing = _Safe([-1] * n, -1)
        self._brackets()
        self.code_lines: frozenset[int] = frozenset(
            ln for t in self.tokens for ln in range(t.line, t.end_line + 1)
        )
        self._raw: list[Declaration] = []
        self._imports: list[ImportRecord] = []
        self._skip = [False] * n  # tokens of import and re-export statements
        self._exportlist = [False] * n  # tokens of `export { ... }` lists and `export default name;`
        self._in_decl = [False] * n  # tokens inside a top-level declaration
        self.exports: dict[str, ExportEntry] = {}
        self._stars: list[str] = []
        self._dynamic_unresolved: list[int] = []
        self._fn_cache: dict[tuple[int, int], list[_Fn]] = {}
        self._top_level()
        self._scan_references()
        self._finish_exports()
        self._starts = [d.start_tok for d in self.declarations]

    # ------------------------------------------------------------------ public views

    @property
    def imports(self) -> tuple[ImportRecord, ...]:
        return tuple(self._imports)

    @property
    def declarations(self) -> tuple[Declaration, ...]:
        return tuple(self._raw)

    @property
    def export_stars(self) -> tuple[str, ...]:
        return tuple(self._stars)

    @property
    def unresolved_dynamic(self) -> tuple[int, ...]:
        """Lines of `import(...)` and `require(...)` whose argument is not a plain string."""
        return tuple(self._dynamic_unresolved)

    def line_text(self, number: int) -> str:
        if 1 <= number <= len(self.lines):
            return self.lines[number - 1].rstrip("\r")
        return ""

    def placed(self, line: int) -> list[Declaration]:
        """The top-level declarations whose span holds the line (by number, never by text)."""
        return [d for d in self._raw if d.start <= line <= d.end]

    def declaration_of_token(self, idx: int) -> Declaration | None:
        for d in self._raw:
            if d.start_tok <= idx <= d.end_tok:
                return d
        return None

    # ------------------------------------------------------------------ token helpers

    def w(self, i: int) -> str | None:
        if 0 <= i < len(self.tokens):
            t = self.tokens[i]
            if t.kind == "ident":
                return t.value
        return None

    def p(self, i: int) -> str | None:
        if 0 <= i < len(self.tokens):
            t = self.tokens[i]
            if t.kind == "punct":
                return t.value
        return None

    @staticmethod
    def _is_opener(t: Token) -> bool:
        return (t.kind == "punct" and t.value in ("(", "[", "{")) or (t.kind == "template" and t.value.endswith("${"))

    @staticmethod
    def _is_closer(t: Token) -> bool:
        return (t.kind == "punct" and t.value in (")", "]", "}")) or (t.kind == "template" and t.value.startswith("}"))

    def _brackets(self) -> None:
        stack: list[int] = []
        for i, t in enumerate(self.tokens):
            if self._is_closer(t):
                if stack:
                    j = stack.pop()
                    self._partner[j] = i
                    self._partner[i] = j
                    if self._bracket_kind(self.tokens[j]) != self._bracket_kind(t):
                        self.problems.append(f"line {t.line}: mismatched bracket")
                else:
                    self.problems.append(f"line {t.line}: unmatched closing bracket")
                self._depth[i] = len(stack)
                self._enclosing[i] = stack[-1] if stack else -1
                if self._is_opener(t):  # a middle chunk of a template: `} text ${`
                    self._partner[i] = -1  # its own closer is not known yet
                    stack.append(i)
            elif self._is_opener(t):
                self._depth[i] = len(stack)
                self._enclosing[i] = stack[-1] if stack else -1
                stack.append(i)
            else:
                self._depth[i] = len(stack)
                self._enclosing[i] = stack[-1] if stack else -1
        for j in stack:
            self.problems.append(f"line {self.tokens[j].line}: unclosed bracket")

    @staticmethod
    def _bracket_kind(t: Token) -> str:
        if t.kind == "template":
            return "template"
        return {"(": "p", ")": "p", "[": "b", "]": "b", "{": "c", "}": "c"}[t.value]

    def matching(self, i: int) -> int:
        """From an opener, the index of its closing token (through the chunks of a template); the last token
        when it is never closed."""
        return self._jump(i)

    def _jump(self, i: int) -> int:
        """From an opener, the index of its closing token (through the chunks of a template)."""
        n = len(self.tokens)
        while True:
            j = self._partner[i]
            if j <= i:  # unclosed (or a broken pairing)
                return n - 1
            i = j
            if not self._is_opener(self.tokens[i]):
                return i

    def _ends_expr(self, t: Token, nxt: Token) -> bool:
        if t.kind in ("number", "string", "regex"):
            return True
        if t.kind == "template":
            return not t.value.endswith("${")
        if t.kind == "ident":
            return t.value not in NOT_END
        if t.value in (")", "]", "}", "++", "--", "!"):
            return True
        if t.value == ">":  # a generic's closing bracket, unless the line goes on as a comparison
            return nxt.kind == "ident" and nxt.value in HARD_STARTERS
        return False

    @staticmethod
    def _starts_stmt(t: Token) -> bool:
        if t.kind == "ident":
            return t.value not in CONTINUES
        if t.kind in ("string", "number", "regex"):
            return True
        if t.kind == "template":
            return not t.value.startswith("`")
        return t.value in ("{", "@", "!", "~", "++", "--", ";")

    def _statement_end(self, s: int, limit: int | None = None) -> int:
        """The last token of the statement starting at token s: a `;` at its own depth, or a
        line break where the next token cannot continue the expression."""
        toks, n = self.tokens, len(self.tokens) if limit is None else limit
        base = self._depth[s]
        i = s
        while i < n:
            if self._depth[i] < base:
                return i - 1
            if self._is_opener(toks[i]):
                i = self._jump(i)
            t = toks[i]
            if t.kind == "punct" and t.value == ";" and self._depth[i] == base:
                return i
            k = i + 1
            if k >= n or self._depth[k] < base:
                return i
            nt = toks[k]
            if nt.newline_before and self._ends_expr(t, nt) and self._starts_stmt(nt):
                return i
            i = k
        return n - 1

    def _header_end(self, i: int) -> tuple[int | None, int]:
        """From the keyword of a function, class, interface, enum or namespace: the opening brace
        of its body (None when it has none, as an overload or an ambient declaration) and the
        index of the last token of the declaration."""
        toks, n = self.tokens, len(self.tokens)
        base = self._depth[i]
        j = i
        while j < n:
            if self._depth[j] < base:
                return None, j - 1
            t = toks[j]
            if t.kind == "punct" and t.value in ("(", "["):
                j = self._jump(j) + 1
                continue
            if t.kind == "punct" and t.value == "{":
                prev = toks[j - 1] if j > 0 else None
                if prev is not None and self._type_brace(prev) and j > i:
                    j = self._jump(j) + 1
                    continue
                close = self._jump(j)
                return j, close
            if t.kind == "punct" and t.value == ";":
                return None, j
            if t.kind == "template" and t.value.endswith("${"):
                j = self._jump(j) + 1
                continue
            if j + 1 < n:
                nt = toks[j + 1]
                if nt.newline_before and self._ends_expr(t, nt) and self._starts_stmt(nt) and not (nt.kind == "punct" and nt.value == "{") and j > i:
                    return None, j
            j += 1
        return None, n - 1

    @staticmethod
    def _type_brace(prev: Token) -> bool:
        if prev.kind == "punct":
            return prev.value in TYPE_BRACE_PUNCT
        if prev.kind == "ident":
            return prev.value in TYPE_BRACE_WORDS
        return False

    def _skip_decorator(self, i: int) -> int:
        i += 1  # the @
        if self.w(i) is not None:
            i += 1
            while self.p(i) == "." and self.w(i + 1) is not None:
                i += 2
        if self.p(i) == "(":
            i = self._jump(i) + 1
        return i

    # ------------------------------------------------------------------ statements

    def _top_level(self) -> None:
        n = len(self.tokens)
        s = 0
        while s < n:
            e = self._statement(s)
            if e < s:
                e = s
            s = e + 1

    def _mark_skip(self, s: int, e: int) -> None:
        for k in range(s, min(e, len(self.tokens) - 1) + 1):
            self._skip[k] = True

    def _statement(self, s: int) -> int:
        toks = self.tokens
        n = len(toks)
        i = s
        while self.p(i) == "@":
            i = self._skip_decorator(i)
        if i >= n:
            return n - 1
        exported = default = False
        w, p = self.w, self.p
        if w(i) == "export":
            if w(i + 1) == "default":
                exported = default = True
                i += 2
            elif p(i + 1) == "{":
                return self._export_list(s, i + 1, False)
            elif p(i + 1) == "*":
                return self._export_star(s, i + 1, False)
            elif w(i + 1) == "type" and p(i + 2) == "{":
                return self._export_list(s, i + 2, True)
            elif w(i + 1) == "type" and p(i + 2) == "*":
                return self._export_star(s, i + 2, True)
            elif p(i + 1) == "=":
                return self._statement_end(s)
            elif w(i + 1) == "import":
                return self._statement_end(s)
            else:
                exported = True
                i += 1
        elif w(i) == "import" and p(i + 1) not in ("(", "."):
            return self._import(s, i)
        while w(i) in ("declare", "abstract") and (w(i + 1) is not None or p(i + 1) == "{"):
            i += 1
        kw = w(i)
        if kw == "async" and w(i + 1) == "function":
            i += 1
            kw = "function"
        if kw == "function":
            j = i + 1
            if p(j) == "*":
                j += 1
            name = w(j) if (w(j) is not None and p(j + 1) in ("(", "<")) else None
            _, e = self._header_end(i)
            self._declare(name, "function", s, e, exported, default, name_tok=j if name else i, init_tok=i)
            return e
        if kw == "class":
            j = i + 1
            name = w(j) if w(j) not in (None, "extends", "implements") else None
            _, e = self._header_end(i)
            self._declare(name, "class", s, e, exported, default, name_tok=j if name else i, init_tok=-1)
            return e
        if kw in ("interface", "enum", "namespace") and w(i + 1) is not None:
            _, e = self._header_end(i)
            self._declare(self.w(i + 1), kw, s, e, exported, default, name_tok=i + 1, init_tok=-1)
            return e
        if kw == "module" and (w(i + 1) is not None or (i + 1 < n and toks[i + 1].kind == "string")):
            _, e = self._header_end(i)
            nm = unquote(toks[i + 1].value) if toks[i + 1].kind == "string" else w(i + 1)
            self._declare(nm, "module", s, e, exported, default, name_tok=i + 1, init_tok=-1)
            return e
        if kw == "global" and p(i + 1) == "{":
            _, e = self._header_end(i)
            self._declare("global", "module", s, e, exported, default, name_tok=i, init_tok=-1)
            return e
        if kw == "type" and w(i + 1) is not None and p(i + 2) in ("=", "<"):
            e = self._statement_end(s)
            self._declare(w(i + 1), "type", s, e, exported, default, name_tok=i + 1, init_tok=-1)
            return e
        if kw == "const" and w(i + 1) == "enum" and w(i + 2) is not None:
            _, e = self._header_end(i + 1)
            self._declare(w(i + 2), "enum", s, e, exported, default, name_tok=i + 2, init_tok=-1)
            return e
        if kw in ("const", "let", "var", "using") and (w(i + 1) is not None or p(i + 1) in ("{", "[")):
            e = self._statement_end(s)
            self._variables(s, i, e, exported)
            return e
        e = self._statement_end(s)
        if default:
            if i == e - 1 and w(i) is not None and p(e) == ";" or (i == e and w(i) is not None):
                self.exports["default"] = ExportEntry("default", w(i), None, False, toks[i].line)
                for k in range(s, min(e, len(toks) - 1) + 1):
                    self._exportlist[k] = True
            else:
                self._declare(None, "default", s, e, True, True, name_tok=i, init_tok=i)
        return e

    def _declare(self, name: str | None, kind: str, s: int, e: int, exported: bool, default: bool, *,
                 name_tok: int, init_tok: int, start_tok: int | None = None) -> None:
        e = max(e, s)
        start = s if start_tok is None else start_tok
        name_tok = min(name_tok, len(self.tokens) - 1)
        for k in range(start, min(e, len(self.tokens) - 1) + 1):
            self._in_decl[k] = True
        self._raw.append(Declaration(
            name=name or "default", kind=kind, start=self.tokens[start].line, end=self.tokens[e].end_line,
            exported=exported, default=default, export_names=(), start_tok=start, end_tok=e,
            name_tok=name_tok, init_tok=init_tok,
        ))

    # ---- variables

    def _variables(self, s: int, kw: int, e: int, exported: bool) -> None:
        toks = self.tokens
        base = self._depth[kw]
        i = kw + 1
        first = True
        while i <= e:
            start = s if first else i
            first = False
            names: list[tuple[str, int]] = []
            t = toks[i]
            if t.kind == "ident":
                names = [(t.value, i)]
                i += 1
            elif t.kind == "punct" and t.value in ("{", "["):
                names = self._pattern_names(i)
                i = self._jump(i) + 1
            else:
                break
            if self.p(i) == "!":
                i += 1
            if self.p(i) == ":":
                i = self._skip_annotation(i, e)
            init = -1
            if self.p(i) == "=":
                init = i + 1
                i += 1
            j = self._declarator_end(i, e, base)
            end = min(j - 1, e)
            if end < start:
                end = start
            is_fn = init >= 0 and self._is_function_init(init)
            for nm, ntok in names:
                self._declare(nm, "function" if is_fn and len(names) == 1 else "variable", s, end, exported, False,
                              name_tok=ntok, init_tok=init, start_tok=start)
            i = j + 1

    def _declarator_end(self, j: int, e: int, base: int) -> int:
        """From the start of a declarator's initializer: the index of the `,` that ends it at the
        statement's own depth (the type after `as` or `satisfies` is skipped, its commas are not
        separators), or e + 1."""
        toks = self.tokens
        while j <= e:
            t = toks[j]
            if self._is_opener(t):
                j = self._jump(j) + 1
                continue
            if t.kind == "ident" and t.value in ("as", "satisfies") and self.p(j - 1) not in (".", "?."):
                j = self._skip_type(j + 1, e)
                continue
            if t.kind == "punct" and t.value == "," and self._depth[j] == base:
                return j
            j += 1
        return e + 1

    def _skip_type(self, i: int, e: int) -> int:
        """The index after the type that starts at token i (names, `a.b`, type arguments, `[]`,
        object, tuple and function types, unions and intersections)."""
        toks = self.tokens
        while i <= e:
            t = toks[i]
            if t.kind in ("ident", "string", "number"):
                i += 1
                while self.p(i) == "." and toks[i + 1].kind == "ident":
                    i += 2
                if self.p(i) == "<":
                    depth = 0
                    while i <= e:
                        if self.p(i) == "<":
                            depth += 1
                        elif self.p(i) == ">":
                            depth -= 1
                            if depth == 0:
                                i += 1
                                break
                        elif self._is_opener(toks[i]):
                            i = self._jump(i)
                        i += 1
                while self.p(i) == "[":
                    i = self._jump(i) + 1
            elif self._is_opener(t):
                i = self._jump(i) + 1
            else:
                break
            if self.p(i) in ("|", "&", "=>"):
                i += 1
                continue
            break
        return i

    def _skip_annotation(self, i: int, e: int) -> int:
        """From the `:` of a type annotation: the index of the `=` or `,` that ends it."""
        toks = self.tokens
        angle = 0
        j = i + 1
        while j <= e:
            t = toks[j]
            if self._is_opener(t):
                j = self._jump(j) + 1
                continue
            if t.kind == "punct":
                if t.value == "<":
                    angle += 1
                elif t.value == ">":
                    angle = max(0, angle - 1)
                elif t.value == "=" and angle == 0:
                    return j
                elif t.value == "," and angle == 0:
                    return j
            j += 1
        return e + 1

    def _pattern_names(self, i: int, depth: int = 0) -> list[tuple[str, int]]:
        """The names a destructuring pattern binds (token index of each)."""
        if depth > 20:
            return []
        toks = self.tokens
        close = self._jump(i)
        out: list[tuple[str, int]] = []
        is_obj = toks[i].value == "{"
        j = i + 1
        while j < close:
            t = toks[j]
            if t.kind == "punct" and t.value == "...":
                j += 1
                continue
            if t.kind == "punct" and t.value in ("{", "["):
                out += self._pattern_names(j, depth + 1)
                j = self._jump(j) + 1
            elif t.kind == "ident":
                nxt = self.p(j + 1)
                if is_obj and nxt == ":":
                    j += 2  # the key; the bound name (or nested pattern) follows
                    continue
                out.append((t.value, j))
                j += 1
                if self.p(j) == "=":  # a default value: skip it
                    j += 1
                    while j < close and not (self.p(j) == "," and self._depth[j] == self._depth[i] + 1):
                        j = self._jump(j) + 1 if self._is_opener(toks[j]) else j + 1
            else:
                j += 1
            while j < close and self.p(j) != "," and not (toks[j].kind == "ident") and not (toks[j].kind == "punct" and toks[j].value in ("{", "[", "...")):
                if self._is_opener(toks[j]):
                    j = self._jump(j)
                j += 1
        return out

    def _is_function_init(self, i: int) -> bool:
        """Whether the initializer starting at token i is a function or an arrow function."""
        toks, n = self.tokens, len(self.tokens)
        j = i
        if self.w(j) == "async" and not (self.p(j + 1) == "=>"):
            j += 1
        if self.w(j) == "function":
            return True
        if self.p(j) == "<":  # generic arrow
            depth = 0
            while j < n:
                if self.p(j) == "<":
                    depth += 1
                elif self.p(j) == ">":
                    depth -= 1
                    if depth == 0:
                        j += 1
                        break
                j += 1
        if self.p(j) == "(":
            close = self._jump(j)
            k = close + 1
            if self.p(k) == "=>":
                return True
            if self.p(k) == ":":  # a return type, then the arrow
                limit = min(n, close + 400)
                while k < limit:
                    if self.p(k) == "=>":
                        return True
                    if self.p(k) == ";":
                        return False
                    k = self._jump(k) + 1 if self._is_opener(toks[k]) else k + 1
            return False
        if toks[j].kind == "ident" and self.p(j + 1) == "=>":
            return True
        return False

    # ---- imports and exports

    def _import(self, s: int, i: int) -> int:
        toks = self.tokens
        w, p = self.w, self.p
        end = self._statement_end(s)
        j = i + 1
        type_only = False
        if w(j) == "type" and not (w(j + 1) == "from" and j + 2 < len(toks) and toks[j + 2].kind == "string") \
                and p(j + 1) not in ("=", ",") and (w(j + 1) is not None or p(j + 1) in ("{", "*")):
            type_only = True
            j += 1
        bindings: list[ImportBinding] = []
        if j < len(toks) and toks[j].kind == "string":
            self._record_import("import", toks[j], None, (), type_only, toks[s].line)
            self._mark_skip(s, end)
            return end
        if toks[j].kind == "ident" if j < len(toks) else False:
            if p(j + 1) == "=":  # import a = require("x") or import a = b.c
                return end
            bindings.append(ImportBinding("default", toks[j].value, type_only))
            j += 1
            if p(j) == ",":
                j += 1
        if p(j) == "*" and w(j + 1) == "as" and w(j + 2) is not None:
            bindings.append(ImportBinding("*", toks[j + 2].value, type_only))
            j += 3
        elif p(j) == "{":
            close = self._jump(j)
            for entry in self._entries(j + 1, close):
                name_tok, local_tok, entry_type = entry
                imported = unquote(name_tok.value) if name_tok.kind == "string" else name_tok.value
                bindings.append(ImportBinding(imported, local_tok.value, type_only or entry_type))
            j = close + 1
        if w(j) == "from" and j + 1 < len(toks) and toks[j + 1].kind == "string":
            named_only = bool(bindings) and all(b.imported not in ("default", "*") for b in bindings)
            names = tuple(b.imported for b in bindings) if named_only else None
            self._record_import("import", toks[j + 1], names, tuple(bindings), type_only, toks[s].line)
        self._mark_skip(s, end)
        return end

    def _entries(self, a: int, b: int) -> list[tuple[Token, Token, bool]]:
        """The entries of an import or export list between tokens a and b (exclusive): each as
        (name token, local token, has a `type` modifier)."""
        toks = self.tokens
        out = []
        j = a
        while j < b:
            k = j
            while k < b and not (toks[k].kind == "punct" and toks[k].value == ","):
                k += 1
            group = toks[j:k]
            j = k + 1
            if not group:
                continue
            type_mod = False
            if group[0].kind == "ident" and group[0].value == "type" and len(group) >= 2 and not (group[1].kind == "ident" and group[1].value == "as" and len(group) == 3):
                type_mod = True
                group = group[1:]
            if not group:
                continue
            name = group[0]
            local = group[2] if len(group) >= 3 and group[1].kind == "ident" and group[1].value == "as" else name
            out.append((name, local, type_mod))
        return out

    def _record_import(self, kind: str, spec_tok: Token, names, bindings, type_only: bool, start_line: int) -> None:
        self._imports.append(ImportRecord(
            kind=kind, specifier=unquote(spec_tok.value), names=names, bindings=tuple(bindings),
            type_only=type_only, line=spec_tok.line, start_line=start_line,
        ))

    def _export_list(self, s: int, brace: int, type_only: bool) -> int:
        toks = self.tokens
        end = self._statement_end(s)
        close = self._jump(brace)
        entries = self._entries(brace + 1, close)
        j = close + 1
        if self.w(j) == "from" and j + 1 < len(toks) and toks[j + 1].kind == "string":
            spec = toks[j + 1]
            names = []
            for name_tok, local_tok, entry_type in entries:
                imported = unquote(name_tok.value) if name_tok.kind == "string" else name_tok.value
                exported = unquote(local_tok.value) if local_tok.kind == "string" else local_tok.value
                names.append(imported)
                self.exports[exported] = ExportEntry(exported, imported, unquote(spec.value), type_only or entry_type, name_tok.line)
            self._record_import("reexport", spec, tuple(names), (), type_only, toks[s].line)
            self._mark_skip(s, end)
        else:
            for name_tok, local_tok, entry_type in entries:
                exported = unquote(local_tok.value) if local_tok.kind == "string" else local_tok.value
                self.exports[exported] = ExportEntry(exported, name_tok.value, None, type_only or entry_type, name_tok.line)
            for k in range(s, min(end, len(self.tokens) - 1) + 1):
                self._exportlist[k] = True
        return end

    def _export_star(self, s: int, star: int, type_only: bool) -> int:
        toks = self.tokens
        end = self._statement_end(s)
        j = star + 1
        alias = None
        if self.w(j) == "as" and self.w(j + 1) is not None:
            alias = toks[j + 1].value
            j += 2
        if self.w(j) == "from" and j + 1 < len(toks) and toks[j + 1].kind == "string":
            spec = toks[j + 1]
            self._record_import("export_star", spec, None, (), type_only, toks[s].line)
            if alias:
                self.exports[alias] = ExportEntry(alias, "*", unquote(spec.value), type_only, toks[s].line)
            else:
                self._stars.append(unquote(spec.value))
            self._mark_skip(s, end)
        return end

    def _scan_references(self) -> None:
        """`import("x")` and `require("x")` anywhere in the file."""
        toks = self.tokens
        for i, t in enumerate(toks):
            if t.kind != "ident" or t.value not in ("import", "require"):
                continue
            if self.p(i + 1) != "(" or self.p(i - 1) in (".", "?."):
                continue
            if t.value == "require" and self.w(i - 1) in ("function", "class", "const", "let", "var"):
                continue
            close = self._jump(i + 1)
            inner = toks[i + 2:close]
            args = self._split_args(i + 1)
            first = args[0] if args else []
            if len(first) == 1 and (first[0].kind == "string" or (first[0].kind == "template" and first[0].value.startswith("`") and first[0].value.endswith("`") and len(first[0].value) >= 2)):
                self._record_import("dynamic" if t.value == "import" else "require", first[0], None, (), False, t.line)
            elif inner:
                self._dynamic_unresolved.append(t.line)

    def _finish_exports(self) -> None:
        for d in self._raw:
            if d.exported:
                name = "default" if d.default else d.name
                line = self.tokens[min(d.name_tok, len(self.tokens) - 1)].line
                self.exports.setdefault(name, ExportEntry(name, d.name, None, d.kind in ("interface", "type"), line))
        by_local: dict[str, list[str]] = {}
        for e in self.exports.values():
            if e.source is None and e.local not in (None, "*"):
                by_local.setdefault(e.local, []).append(e.name)  # type: ignore[arg-type]
        for d in self._raw:
            names = set(by_local.get(d.name, ()))
            if d.exported:
                names.add("default" if d.default else d.name)
            d.export_names = tuple(sorted(names))

    # ------------------------------------------------------------------ arguments and uses

    def _split_args(self, open_idx: int) -> list[list[Token]]:
        """The tokens of each top-level argument of the call whose `(` is at open_idx."""
        toks = self.tokens
        close = self._jump(open_idx)
        args: list[list[Token]] = []
        cur: list[Token] = []
        j = open_idx + 1
        while j < close:
            t = toks[j]
            if t.kind == "punct" and t.value == "," and self._depth[j] == self._depth[open_idx] + 1:
                args.append(cur)
                cur = []
                j += 1
                continue
            if self._is_opener(t):
                end = self._jump(j)
                cur.extend(toks[j:end + 1])
                j = end + 1
                continue
            cur.append(t)
            j += 1
        if cur:
            args.append(cur)
        return [a for a in args if a]

    def _arg_text(self, arg: list[Token]) -> str:
        return self.text[arg[0].start:arg[-1].end].replace("\r", "")

    @staticmethod
    def _literal(arg: list[Token]) -> str | None:
        if len(arg) == 1:
            t = arg[0]
            if t.kind == "string" and len(t.value) >= 2 and t.value[-1] == t.value[0]:
                return unquote(t.value)
            if t.kind == "template" and t.value.startswith("`") and t.value.endswith("`") and len(t.value) >= 2:
                return unquote(t.value)
        return None

    def _generic_call_open(self, i: int) -> int | None:
        """For an identifier at i followed by type arguments `<...>(`: the index of the `(`."""
        if self.p(i + 1) != "<":
            return None
        depth = 0
        j = i + 1
        limit = min(len(self.tokens), i + 200)
        while j < limit:
            t = self.tokens[j]
            if t.kind == "punct":
                if t.value == "<":
                    depth += 1
                elif t.value == ">":
                    depth -= 1
                    if depth == 0:
                        return j + 1 if self.p(j + 1) == "(" else None
                elif t.value in CALL_BREAK or t.value in ("&&", "||", "=", "==", "===", "!=", "!=="):
                    return None
            j += 1
        return None

    def _make_use(self, name_tok: int, local: str, member: str | None, form: str, open_idx: int | None) -> Use:
        args: tuple[str, ...] = ()
        lits: tuple[str | None, ...] = ()
        if open_idx is not None:
            parts = self._split_args(open_idx)
            args = tuple(self._arg_text(a) for a in parts)
            lits = tuple(self._literal(a) for a in parts)
        decl = self.declaration_of_token(name_tok)
        return Use(local=local, member=member, line=self.tokens[name_tok].line, form=form, args=args,
                   literals=lits, enclosing=decl.name if decl else "<module>", tok=name_tok)

    def _is_property_key(self, i: int) -> bool:
        return self.p(i - 1) in ("{", ",") and self.p(i + 1) == ":" and self._enclosing[i] >= 0 and self.p(self._enclosing[i]) == "{"

    def _is_declaration_name(self, i: int) -> bool:
        prev = self.w(i - 1)
        if prev in ("function", "class", "const", "let", "var", "interface", "enum", "namespace"):
            return True
        return prev is None and self.p(i - 1) == "*" and self.w(i - 2) == "function"

    def find_uses(self, local: str, member: str | None = None, whole_references: bool = False) -> list[Use]:
        """The uses of the imported name `local` (or of `local.member` for a namespace) outside
        the import statements. `a.local` is not a use. Shadowing is not understood: every
        identifier spelled `local` counts. With `whole_references`, a namespace used as a value
        (`Object.keys(ns)`) is a reference."""
        toks = self.tokens
        out: list[Use] = []
        for i, t in enumerate(toks):
            if t.kind != "ident" or t.value != local or self._skip[i]:
                continue
            if self.p(i - 1) in (".", "?.") or self._is_property_key(i) or self._is_declaration_name(i):
                continue
            if member is not None:
                if self.p(i + 1) in (".", "?.") and self.w(i + 2) == member:
                    k = i + 2
                    call = None
                    if self.p(k + 1) == "(":
                        call = k + 1
                    else:
                        call = self._generic_call_open(k)
                    out.append(self._make_use(i, local, member, "call" if call else "reference", call))
                elif whole_references and self.p(i + 1) not in (".", "?."):
                    out.append(self._make_use(i, local, member, "reference", None))
                continue
            call = None
            if self.p(i + 1) == "(":
                call = i + 1
            else:
                call = self._generic_call_open(i)
            out.append(self._make_use(i, local, None, "call" if call else "reference", call))
        return out

    def member_uses(self, names: Any) -> list[Use]:
        """The uses of any of `names` as a member of something (`x.name`, `x?.name`), outside import statements: a call when `(` follows
        (also past generics), else a reference. The object is not followed: `local` is the member's own name."""
        wanted = set(names)
        out: list[Use] = []
        for i, t in enumerate(self.tokens):
            if t.kind != "ident" or t.value not in wanted or self._skip[i] or self.p(i - 1) not in (".", "?."):
                continue
            call = i + 1 if self.p(i + 1) == "(" else self._generic_call_open(i)
            out.append(self._make_use(i, t.value, None, "call" if call else "reference", call))
        return out

    # ------------------------------------------------------------------ reach inside the file

    def decl_references(self, decl: Declaration) -> frozenset[str]:
        """Every identifier the declaration's tokens spell (code only, never a comment or a
        string), except those after a `.`: what it may use of the rest of the file. Shadowing is
        not understood, so this over-states what is used."""
        cache = self.__dict__.setdefault("_ref_cache", {})
        key = (decl.start_tok, decl.end_tok)
        if key not in cache:
            cache[key] = frozenset(
                t.value for k, t in enumerate(self.tokens[decl.start_tok:decl.end_tok + 1], decl.start_tok)
                if t.kind == "ident" and self.p(k - 1) not in (".", "?.")
            )
        return cache[key]

    def module_references(self) -> frozenset[str]:
        """The identifiers used by top-level code that is neither a declaration, an import, nor an
        export list: statements that run when the module loads."""
        return frozenset(
            t.value for k, t in enumerate(self.tokens)
            if t.kind == "ident" and not (self._in_decl[k] or self._skip[k] or self._exportlist[k]) and self.p(k - 1) not in (".", "?.")
        )

    def _property_names(self, indices: Any) -> frozenset[str]:
        out: set[str] = set()
        for k in indices:
            t = self.tokens[k]
            if t.kind == "ident" and self.p(k - 1) in (".", "?."):
                out.add(t.value)
            elif t.kind == "string" and len(t.value) > 2 and t.value[1:-1].isidentifier():
                out.add(t.value[1:-1])  # obj["name"]
        return frozenset(out)

    def decl_properties(self, decl: Declaration) -> frozenset[str]:
        """The property names the declaration reads or sets on other objects (`doc.total`, `doc?.total`,
        `doc["total"]`): what a change to a class member can reach by name, whatever the object."""
        return self._property_names(range(decl.start_tok, decl.end_tok + 1))

    def module_properties(self) -> frozenset[str]:
        """The same for top-level code that is neither a declaration, an import nor an export list."""
        return self._property_names(k for k in range(len(self.tokens)) if not (self._in_decl[k] or self._skip[k] or self._exportlist[k]))

    def line_identifiers(self, lines: Any) -> frozenset[str]:
        """Every identifier spelled on the given lines (the names a change to those lines touches)."""
        wanted = set(lines)
        return frozenset(t.value for t in self.tokens if t.kind == "ident" and t.line in wanted)

    def initializer_calls(self, decl: Declaration) -> frozenset[str]:
        """The names called in the initializer of a top-level constant, which runs when the module loads
        (`export const CONFIG = load()`). Not computed for a function."""
        if decl.kind != "variable" or decl.init_tok < 0:
            return frozenset()
        out: set[str] = set()
        for k in range(decl.init_tok, decl.end_tok + 1):
            t = self.tokens[k]
            if t.kind == "ident" and self.p(k + 1) == "(" and self.p(k - 1) not in (".", "?."):
                out.add(t.value)
        return frozenset(out)

    def uses_dynamic_access(self) -> bool:
        """Whether the file reaches names in a way the identifiers do not show: `eval`, `new
        Function`, `with`, `this[...]`, `window[...]`, `globalThis[...]`, `global[...]`."""
        for i, t in enumerate(self.tokens):
            if t.kind != "ident" or self.p(i - 1) in (".", "?."):
                continue
            if t.value == "eval" or (t.value == "Function" and self.p(i + 1) == "(") or (t.value == "with" and self.p(i + 1) == "("):
                return True
            if t.value in ("this", "window", "globalThis", "global", "self") and self.p(i + 1) == "[":
                return True
        return False

    # ------------------------------------------------------------------ nested functions

    def _expr_end(self, i: int, limit: int) -> int:
        """The last token of the expression starting at i: before a `,` or `;` at its own depth,
        before a closing bracket of the enclosing group, or at a line break that ends it."""
        toks = self.tokens
        base = self._depth[i]
        j = i
        while j <= limit:
            if self._depth[j] < base:
                return j - 1
            if self._is_opener(toks[j]):
                j = self._jump(j)
            t = toks[j]
            if t.kind == "punct" and t.value in (",", ";") and self._depth[j] == base:
                return j - 1
            k = j + 1
            if k > limit or self._depth[k] < base:
                return j
            nt = toks[k]
            if nt.newline_before and self._ends_expr(t, nt) and self._starts_stmt(nt):
                return j
            if nt.kind == "punct" and nt.value in (",", ";") and self._depth[k] == base:
                return j
            j = k
        return limit

    def _functions(self, a: int, b: int) -> list[_Fn]:
        """Named functions between tokens a and b: function declarations, constants holding a
        function, methods and function-valued properties of classes and object literals."""
        key = (a, b)
        if key in self._fn_cache:
            return self._fn_cache[key]
        toks = self.tokens
        out: list[_Fn] = []
        for i in range(a, b + 1):
            t = toks[i]
            if t.kind != "ident":
                continue
            v = t.value
            prev_p = self.p(i - 1)
            if prev_p in (".", "?."):
                continue
            if v == "function":
                j = i + 1
                if self.p(j) == "*":
                    j += 1
                if self.w(j) is not None and self.p(j + 1) in ("(", "<"):
                    _, e = self._header_end(i)
                    st = i - 1 if self.w(i - 1) == "async" else i
                    out.append(_Fn(self.w(j), "function", st, e, toks[st].line, toks[e].end_line))
            elif v in ("const", "let", "var") and self.w(i + 1) is not None and self.p(i + 2) in ("=", ":", "!"):
                k = i + 2
                if self.p(k) == "!":
                    k += 1
                if self.p(k) == ":":
                    k = self._skip_annotation(k, b)
                if self.p(k) == "=" and self._is_function_init(k + 1):
                    e = self._statement_end(i)
                    out.append(_Fn(self.w(i + 1), "const", i, e, t.line, toks[e].end_line))
            elif self._enclosing[i] >= 0 and self.p(self._enclosing[i]) == "{" and v not in CONTROL:
                prev_ok = prev_p in ("{", ",", ";", "}", "*") or self.w(i - 1) in MODIFIERS
                if not prev_ok:
                    continue
                nxt = self.p(i + 1)
                if nxt in ("(", "<"):
                    open_idx = i + 1
                    if nxt == "<":
                        depth, j = 0, i + 1
                        while j <= b:
                            if self.p(j) == "<":
                                depth += 1
                            elif self.p(j) == ">":
                                depth -= 1
                                if depth == 0:
                                    break
                            j += 1
                        open_idx = j + 1
                        if self.p(open_idx) != "(":
                            continue
                    close = self._jump(open_idx)
                    if self.p(close + 1) not in ("{", ":"):  # a body, or a return type before it
                        continue
                    body, e = self._header_end(close + 1) if close + 1 <= b else (None, close)
                    if body is not None:
                        st = i
                        while st - 1 >= a and (self.w(st - 1) in MODIFIERS or self.p(st - 1) == "*") and self._depth[st - 1] == self._depth[i]:
                            st -= 1
                        out.append(_Fn(v, "method", st, e, toks[st].line, toks[e].end_line))
                elif nxt in ("=", ":", "?") and self._property_function(i, b):
                    e = self._expr_end(self._init_start(i, b), b)
                    out.append(_Fn(v, "property", i, e, t.line, toks[e].end_line))
        self._fn_cache[key] = out
        return out

    def _init_start(self, i: int, b: int) -> int:
        """For a property `name = init` or `name: init` (optionally with a type annotation) at
        token i: the first token of the initializer."""
        k = i + 1
        if self.p(k) == "?":
            k += 1
        if self.p(k) == ":":
            if self._is_function_init(k + 1):
                return k + 1
            k = self._skip_annotation(k, b)
        return k + 1

    def _property_function(self, i: int, b: int) -> bool:
        prev_ok = self.p(i - 1) in ("{", ",", ";", "}") or self.w(i - 1) in MODIFIERS
        if not prev_ok:
            return False
        k = i + 1
        if self.p(k) == "?":
            k += 1
        if self.p(k) == ":":
            if self._is_function_init(k + 1):
                return True
            k = self._skip_annotation(k, b)
            return self.p(k) == "=" and self._is_function_init(k + 1)
        return self.p(k) == "=" and self._is_function_init(k + 1)

    def nested(self, decl: Declaration, line: int) -> tuple[str, ...]:
        """The named functions inside `decl` that contain `line`, outermost first. The
        declaration itself is not listed."""
        found = []
        for fn in self._functions(decl.start_tok, decl.end_tok):
            if fn.name == decl.name and fn.end_tok == decl.end_tok:
                continue  # the declaration itself
            if fn.start_line <= line <= fn.end_line:
                found.append(fn)
        found.sort(key=lambda f: (f.start_tok, -f.end_tok))
        return tuple(fn.name for fn in found)

    # ------------------------------------------------------------------ functions and guards

    def function_info(self, decl: Declaration) -> FunctionInfo | None:
        """The parameters and body of a function declaration or a constant holding a function."""
        if decl.kind != "function":
            return None
        toks, n = self.tokens, len(self.tokens)
        i = decl.init_tok
        arrow = False
        single: Token | None = None
        if self.w(i) == "async" and self.p(i + 1) != "=>":
            i += 1
        if self.w(i) == "function":
            i += 1
            if self.p(i) == "*":
                i += 1
            if self.w(i) is not None and self.p(i + 1) in ("(", "<"):
                i += 1
        else:
            arrow = True
        if self.p(i) == "<":
            depth = 0
            while i < n:
                if self.p(i) == "<":
                    depth += 1
                elif self.p(i) == ">":
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                i += 1
        if arrow and toks[i].kind == "ident" and self.p(i + 1) == "=>":
            single = toks[i]
        if single is not None:
            params = (Param(single.value, 0, False, False, False),)
            j = i + 1
        elif self.p(i) == "(":
            params = self._params(i)
            j = self._jump(i) + 1
        else:
            return None
        body_open = body_close = -1
        if arrow:
            limit = decl.end_tok
            while j <= limit and self.p(j) != "=>":
                j = self._jump(j) + 1 if self._is_opener(toks[j]) else j + 1
            if self.p(j + 1) == "{":
                body_open, body_close = j + 1, self._jump(j + 1)
        else:
            ob, _ = self._header_end(j)
            if ob is not None:
                body_open, body_close = ob, self._jump(ob)
        names = {p.name for p in params if p.name}
        lo, hi = (body_open, body_close) if body_open >= 0 else (j, decl.end_tok)
        return FunctionInfo(params=params, body_open=body_open, body_close=body_close, rebound=self._rebound_names(lo, hi, names))

    def _is_param_group(self, o: int) -> bool:
        """Whether the parenthesised group opening at token o is a parameter list (of an arrow,
        a function, a method) or a `catch` binding, not a call or a condition."""
        toks = self.tokens
        c = self._partner[o]
        if c < 0:
            return False
        nxt, prev = toks[c + 1], toks[o - 1] if o > 0 else None
        if nxt.kind == "punct" and nxt.value == "=>":
            return True
        if prev is not None and prev.kind == "ident":
            if prev.value == "catch":
                return True
            if prev.value in ("if", "while", "switch", "with", "for"):
                return False
        if nxt.kind == "punct" and nxt.value == "{":
            return prev is not None and (prev.kind == "ident" or prev.value in ("*", ">"))
        if nxt.kind == "punct" and nxt.value == ":" and prev is not None and prev.kind == "ident":
            before = toks[o - 2] if o > 1 else None  # a method or function name: look at what precedes it
            named = prev.value == "function" or (before is not None and (before.value in ("function", "*", "{", "}", ";", ",") or (before.kind == "ident" and before.value in MODIFIERS)))
            if named:
                k = c + 1
                while k < min(len(toks), c + 200):
                    if self.p(k) in ("=>", "{"):
                        return True
                    if self.p(k) in (";", ","):
                        return False
                    k = self._jump(k) + 1 if self._is_opener(toks[k]) else k + 1
        return False

    def _rebound_names(self, lo: int, hi: int, names: set[str]) -> frozenset[str]:
        """Which of `names` the code between tokens lo and hi assigns, redeclares or shadows:
        assignment (`=`, `+=`, `++`, destructuring assignment), `const`/`let`/`var`/`function`/`class`
        names and destructuring patterns, parameters of nested functions and arrows, `catch`
        bindings, `for (x of ...)` heads. Any use of `arguments` counts for every name."""
        toks = self.tokens
        out: set[str] = set()
        for k in range(lo, hi + 1):
            t = toks[k]
            if t.kind != "ident" or self.p(k - 1) in (".", "?."):
                continue
            if t.value == "arguments":
                return frozenset(names)
            if t.value not in names:
                continue
            nxt = self.p(k + 1)
            if nxt in ASSIGN_OPS or nxt in ("++", "--", "=>") or self.p(k - 1) in ("++", "--"):
                out.add(t.value)
                continue
            if self.w(k - 1) in ("const", "let", "var", "function", "class") or (self.w(k + 1) in ("of", "in") and self.p(k - 1) == "("):
                out.add(t.value)
                continue
            o = self._enclosing[k]
            while o >= 0 and o >= lo:
                closer = self._partner[o]
                tok = toks[o]
                if closer >= 0 and tok.kind == "punct":
                    if tok.value in ("[", "{") and (self.p(closer + 1) == "=" or self.w(closer + 1) in ("of", "in")):
                        out.add(t.value)
                        break
                    if tok.value == "(" and self._is_param_group(o):
                        out.add(t.value)
                        break
                o = self._enclosing[o]
        return frozenset(out)

    def _params(self, open_idx: int) -> tuple[Param, ...]:
        toks = self.tokens
        close = self._jump(open_idx)
        groups: list[list[int]] = []
        cur: list[int] = []
        angle = 0
        j = open_idx + 1
        while j < close:
            t = toks[j]
            if self._is_opener(t):
                end = self._jump(j)
                cur.extend(range(j, end + 1))
                j = end + 1
                continue
            if t.kind == "punct":
                if t.value == "<":
                    angle += 1
                elif t.value == ">":
                    angle = max(0, angle - 1)
                elif t.value == "," and angle == 0:
                    groups.append(cur)
                    cur = []
                    j += 1
                    continue
            cur.append(j)
            j += 1
        if cur:
            groups.append(cur)
        params: list[Param] = []
        for g in groups:
            ix = list(g)
            while ix and self.w(ix[0]) in ("public", "private", "protected", "readonly", "override"):
                ix.pop(0)
            if not ix:
                continue
            rest = self.p(ix[0]) == "..."
            if rest:
                ix.pop(0)
            if not ix:
                continue
            first = toks[ix[0]]
            if first.kind == "ident" and first.value == "this":
                continue
            pattern = first.kind == "punct" and first.value in ("{", "[")
            name = None if pattern else (first.value if first.kind == "ident" else None)
            has_default = False
            depth0 = self._depth[ix[0]]
            for k in ix[1:]:
                if self.p(k) == "=" and self._depth[k] == depth0:
                    has_default = True
                    break
            params.append(Param(name, len(params), has_default, rest, pattern))
        return tuple(params)

    def _guard_cond(self, a: int, b: int) -> tuple[str, str, bool] | None:
        """`param op "literal"` (or the literal first) between tokens a and b inclusive."""
        toks = self.tokens
        if b - a != 2:
            return None
        x, op, y = toks[a], toks[a + 1], toks[a + 2]
        if op.kind != "punct" or op.value not in EQUALITY:
            return None
        if x.kind == "ident" and y.kind == "string":
            lit = self._literal([y])
            return (x.value, lit, EQUALITY[op.value]) if lit is not None else None
        if x.kind == "string" and y.kind == "ident":
            lit = self._literal([x])
            return (y.value, lit, EQUALITY[op.value]) if lit is not None else None
        return None

    def _branch_body(self, k: int, cond_close_line: int, limit: int) -> tuple[int, int, int]:
        """(first inside line, last inside line, last token) of the branch starting at token k."""
        toks = self.tokens
        if self.p(k) == "{":
            close = self._jump(k)
            return toks[k].line + 1, toks[close].line - 1, close
        e = self._statement_end(k, limit + 1)
        if toks[k].line > cond_close_line:
            return toks[k].line, toks[e].end_line, e
        return toks[k].line + 1, toks[k].line, e  # on the line of the condition: nothing is inside

    def guard_blocks(self, decl: Declaration) -> tuple[GuardBlock, ...]:
        """The `if`, `else if`, `else` and `switch` blocks of the function whose condition is a
        comparison of a variable with a string literal, each with the lines strictly inside it
        and the condition it needs."""
        info = self.function_info(decl)
        if info is None or info.body_open < 0:
            return ()
        toks = self.tokens
        a, b = info.body_open, info.body_close
        blocks: list[GuardBlock] = []
        for i in range(a + 1, b):
            if toks[i].kind != "ident" or self.p(i - 1) in (".", "?."):
                continue
            if toks[i].value == "if" and self.p(i + 1) == "(" and self.w(i - 1) != "else":
                blocks += self._if_chain(i, b)
            elif toks[i].value == "switch" and self.p(i + 1) == "(":
                blocks += self._switch(i, b)
        return tuple(blocks)

    def _if_chain(self, i: int, limit: int) -> list[GuardBlock]:
        toks = self.tokens
        links: list[tuple[tuple[str, str, bool] | None, int, int, int]] = []  # cond, line of the if, first, last
        else_body = None
        k = i
        while True:
            lp = k + 1
            rp = self._jump(lp)
            cond = self._guard_cond(lp + 1, rp - 1)
            first, last, end = self._branch_body(rp + 1, toks[rp].line, limit)
            links.append((cond, toks[k].line, first, last))
            nxt = end + 1
            if self.w(nxt) == "else":
                if self.w(nxt + 1) == "if" and self.p(nxt + 2) == "(":
                    k = nxt + 1
                    continue
                first, last, _ = self._branch_body(nxt + 1, toks[nxt].line, limit)
                else_body = (first, last)
            break
        out: list[GuardBlock] = []
        known = [(c, line) for c, line, _f, _l in links]
        for idx, (cond, line, first, last) in enumerate(links):
            if cond is not None:
                out.append(GuardBlock(cond[0], (cond[1],), cond[2], line, first, last))
            for c, cline in known[:idx]:
                if c is not None:
                    out.append(GuardBlock(c[0], (c[1],), not c[2], cline, first, last))
        if else_body is not None:
            for c, cline in known:
                if c is not None:
                    out.append(GuardBlock(c[0], (c[1],), not c[2], cline, else_body[0], else_body[1]))
        return out

    def _switch(self, i: int, limit: int) -> list[GuardBlock]:
        toks = self.tokens
        lp = i + 1
        rp = self._jump(lp)
        if rp - lp != 2 or toks[lp + 1].kind != "ident" or self.p(rp + 1) != "{":
            return []
        param = toks[lp + 1].value
        open_b = rp + 1
        close_b = self._jump(open_b)
        base = self._depth[open_b] + 1
        labels: list[tuple[int, int, str | None]] = []  # label token, colon token, literal
        j = open_b + 1
        while j < close_b:
            if self._depth[j] == base and toks[j].kind == "ident" and toks[j].value in ("case", "default") and self.p(j - 1) not in (".", "?."):
                k = j + 1
                while k < close_b and not (self.p(k) == ":" and self._depth[k] == base):
                    k = self._jump(k) + 1 if self._is_opener(toks[k]) else k + 1
                lit = None
                if toks[j].value == "case" and k == j + 2 and toks[j + 1].kind == "string":
                    lit = self._literal([toks[j + 1]])
                labels.append((j, k, lit if toks[j].value == "case" else None))
                j = k + 1
            else:
                j = self._jump(j) + 1 if self._is_opener(toks[j]) else j + 1
        out: list[GuardBlock] = []
        entry: list[set[str] | None] = []  # None: any value can enter
        prev_falls = False
        prev_entry: set[str] | None = set()
        for idx, (lab, colon, lit) in enumerate(labels):
            nxt_label = labels[idx + 1][0] if idx + 1 < len(labels) else close_b
            body = (colon + 1, nxt_label - 1)
            own: set[str] | None = {lit} if lit is not None else None
            if prev_falls:
                merged = None if (own is None or prev_entry is None) else own | prev_entry
            else:
                merged = own
            entry.append(merged)
            prev_falls = not self._terminates(body[0], body[1])
            prev_entry = merged
            if merged is not None:
                first_line = toks[colon].line + 1
                last_line = toks[nxt_label].line - 1 if nxt_label < len(toks) else toks[colon].line
                out.append(GuardBlock(param, tuple(sorted(merged)), True, toks[lab].line, first_line, last_line))
        return out

    def _terminates(self, a: int, b: int) -> bool:
        """Whether the statements between tokens a and b end by leaving the switch clause."""
        toks = self.tokens
        while a <= b and self.p(a) == "{" and self._jump(a) == b:  # a block holding the whole clause: look inside
            a, b = a + 1, b - 1
        if a > b:
            return False
        last_start = None
        s = a
        while s <= b:
            e = self._statement_end(s, b + 1)
            last_start = s
            s = e + 1
        return last_start is not None and toks[last_start].kind == "ident" and toks[last_start].value in ("break", "return", "throw", "continue")

    # ------------------------------------------------------------------ definitions

    def definition_lines(self, name: str) -> list[int]:
        """The lines where `name` is defined: a function, class, interface, type, enum or
        namespace declaration, a const, let or var binding (destructuring included), a method,
        or an exported name (`export { x as name }`). Imports, references, `name:` properties
        and re-exports do not count."""
        toks = self.tokens
        lines: list[int] = []
        for i, t in enumerate(toks):
            if t.kind != "ident" or self.p(i - 1) in (".", "?."):
                continue
            v = t.value
            if v == "function":
                j = i + 1
                if self.p(j) == "*":
                    j += 1
                if self.w(j) == name:
                    lines.append(toks[j].line)
            elif v in ("class", "interface", "enum", "namespace") and self.w(i + 1) == name:
                lines.append(toks[i + 1].line)
            elif v == "type" and self.w(i + 1) == name and self.p(i + 2) in ("=", "<"):
                lines.append(toks[i + 1].line)
            elif v in ("const", "let", "var"):
                end = self._statement_end(i)
                lines.extend(toks[k].line for nm, k in self._declared_names(i, end) if nm == name)
        for e in self.exports.values():
            if e.source is None and e.name == name and e.local != name and e.local not in (None, "*"):
                lines.append(e.line)
        if toks:
            lines.extend(fn.start_line for fn in self._functions(0, len(toks) - 1) if fn.name == name and fn.kind == "method")
        return sorted(lines)

    def _declared_names(self, kw: int, end: int) -> list[tuple[str, int]]:
        toks = self.tokens
        base = self._depth[kw]
        names: list[tuple[str, int]] = []
        i = kw + 1
        while i <= end:
            t = toks[i]
            if t.kind == "ident":
                names.append((t.value, i))
                i += 1
            elif t.kind == "punct" and t.value in ("{", "["):
                names += self._pattern_names(i)
                i = self._jump(i) + 1
            else:
                break
            if self.p(i) == "!":
                i += 1
            if self.p(i) == ":":
                i = self._skip_annotation(i, end)
            if self.p(i) == "=":
                i += 1
            i = self._declarator_end(i, end, base) + 1
        return names
