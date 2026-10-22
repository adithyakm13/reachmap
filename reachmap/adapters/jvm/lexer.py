"""A tokenizer for Java and Kotlin source.

It knows exactly what is needed to tell code from text: line and block comments (Kotlin's nest, Java's do not), strings,
Java text blocks, Kotlin raw strings, character literals and Kotlin string templates (`"$name"`, `"${ expr }"`, with strings,
braces and templates inside the expression). Everything later works on tokens, so text in a comment or a string is never
taken for code (Shape B: match code, not comments), and the names a template spells are tokens too, marked `inner`.

What it is not: a parser and not a conforming lexer (no Unicode escapes outside strings, no operator table: `<` and `>` are
always single tokens so that `List<List<String>>` closes its generics, and only the few joined operators the parser needs
are one token: the arrow of a lambda, `::`, `?.`, `?:`, `...`, `..`, `!!`, `&&`, `||`). It never raises on bad input; what it could not make sense
of (an unterminated string, comment or text block, a bracket that does not match) is listed in `LexResult.problems`.

`Token.kind` is `ident` (keywords included; a Kotlin backtick name is an `ident` whose value is the name), `number`, `string`
(`value` is the exact source text including its quotes; `text` is the decoded content when the literal has no template,
`parts` the pieces of one that has), `char` and `punct`. `line` is the 1-based line a token starts on, `end_line` where it ends.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NamedTuple

__all__ = ["LexResult", "Token", "tokenize", "unescape"]


class Token(NamedTuple):
    kind: str
    value: str  # the exact source text
    line: int
    end_line: int
    start: int  # offsets into the source
    end: int
    newline_before: bool  # a line break between the previous token (or comment) and this one
    inner: bool = False  # inside a Kotlin string template: a name the string spells, not part of the code's structure
    text: str | None = None  # a string without a template: its decoded content
    parts: tuple[tuple[str, str], ...] | None = None  # a string with a template: ("lit", text) and ("expr", source) pieces


@dataclass(frozen=True, slots=True)
class LexResult:
    tokens: tuple[Token, ...]
    problems: tuple[str, ...]  # "line 3: unterminated string"


_NUMBER = re.compile(
    r"0[xX][0-9a-fA-F_]+[lLuU]*|0[bB][01_]+[lLuU]*|(?:\d[\d_]*(?:\.(?!\.)[\d_]*)?|\.\d[\d_]*)(?:[eE][+-]?\d[\d_]*)?[fFdDlLuU]*"
)
_JOINED = ("...", "->", "::", "?.", "?:", "..", "!!", "&&", "||")
_SPACE = " \t\r\f\v\u00a0\ufeff\u2028\u2029"
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "s": " ", "0": "\0", '"': '"', "'": "'", "\\": "\\", "$": "$"}
_OPEN = {"(": ")", "[": "]", "{": "}"}
_CLOSE = {")", "]", "}"}


def _is_ident_start(c: str) -> bool:
    return c.isalpha() or c in "_$"


def _is_ident_part(c: str) -> bool:
    return c.isalnum() or c in "_$"


def unescape(raw: str) -> str:
    """The content of a quoted literal body (without its quotes): escapes decoded, an unknown escape kept as written."""
    out: list[str] = []
    i, n = 0, len(raw)
    while i < n:
        c = raw[i]
        if c != "\\" or i + 1 >= n:
            out.append(c)
            i += 1
            continue
        d = raw[i + 1]
        if d == "u":
            j = i + 2
            while j < n and raw[j] == "u":
                j += 1
            digits = raw[j:j + 4]
            if len(digits) == 4 and all(h in "0123456789abcdefABCDEF" for h in digits):
                out.append(chr(int(digits, 16)))
                i = j + 4
                continue
        if d in _ESCAPES:
            out.append(_ESCAPES[d])
            i += 2
            continue
        out.append(c + d)
        i += 2
    return "".join(out)


class _Lexer:
    def __init__(self, text: str, kotlin: bool) -> None:
        self.text = text
        self.kotlin = kotlin
        self.n = len(text)
        self.i = 0
        self.line = 1
        self.newline = False
        self.tokens: list[Token] = []
        self.problems: list[str] = []
        self.brackets: list[tuple[str, int]] = []
        self.pending: list[Token] = []  # the names a template spells, emitted after the string that holds them

    def problem(self, line: int, what: str) -> None:
        self.problems.append(f"line {line}: {what}")

    def emit(self, kind: str, start: int, line: int, inner: bool = False, **extra: object) -> None:
        end_line = self.line
        self.tokens.append(Token(kind, self.text[start:self.i], line, end_line, start, self.i, self.newline, inner, **extra))  # type: ignore[arg-type]
        self.newline = False

    # ---- the main loop (also used for a template expression, which stops at its closing brace)

    def run(self, stop_at_brace: bool = False, inner: bool = False) -> bool:
        """Lex until the end of the text, or (in a template expression) until the `}` that closes it. Returns whether the stop
        was reached."""
        text, n = self.text, self.n
        depth = 0
        while self.i < n:
            c = text[self.i]
            if c == "\n":
                self.line += 1
                self.newline = True
                self.i += 1
            elif c in _SPACE:
                self.i += 1
            elif c == "/" and text.startswith("//", self.i):
                j = text.find("\n", self.i)
                self.i = n if j < 0 else j
            elif c == "/" and text.startswith("/*", self.i):
                self.block_comment()
            elif c == '"':
                self.string(inner)
            elif c == "'":
                self.char(inner)
            elif _is_ident_start(c):
                start = self.i
                self.i += 1
                while self.i < n and _is_ident_part(text[self.i]):
                    self.i += 1
                self.emit("ident", start, self.line, inner)
            elif c == "`" and self.kotlin:
                j = text.find("`", self.i + 1)
                k = text.find("\n", self.i + 1)
                if j < 0 or (0 <= k < j):
                    self.problem(self.line, "unterminated backtick name")
                    self.i = n if k < 0 else k
                else:
                    start = self.i
                    self.i = j + 1
                    name = text[start + 1:j]
                    self.tokens.append(Token("ident", name, self.line, self.line, start, self.i, self.newline, inner))
                    self.newline = False
            elif c.isdigit() or (c == "." and self.i + 1 < n and text[self.i + 1].isdigit()):
                m = _NUMBER.match(text, self.i)
                start = self.i
                self.i = m.end() if m and m.end() > self.i else self.i + 1
                self.emit("number", start, self.line, inner)
            else:
                start = self.i
                if c in _OPEN:
                    self.brackets.append((c, self.line))
                    depth += 1
                    self.i += 1
                elif c in _CLOSE:
                    if stop_at_brace and c == "}" and depth == 0:
                        return True
                    self.close(c)
                    depth = max(0, depth - 1)
                    self.i += 1
                else:
                    for op in _JOINED:
                        if text.startswith(op, self.i):
                            self.i += len(op)
                            break
                    else:
                        self.i += 1
                self.emit("punct", start, self.line, inner)
        return False

    def close(self, c: str) -> None:
        if not self.brackets:
            self.problem(self.line, f"unmatched {c!r}")
            return
        opener, line = self.brackets.pop()
        if _OPEN[opener] != c:
            self.problem(self.line, f"{c!r} closes the {opener!r} opened at line {line}")

    def block_comment(self) -> None:
        text, n = self.text, self.n
        start_line = self.line
        depth = 1
        i = self.i + 2
        while i < n:
            if self.kotlin and text.startswith("/*", i):
                depth += 1
                i += 2
            elif text.startswith("*/", i):
                depth -= 1
                i += 2
                if depth == 0:
                    break
            else:
                if text[i] == "\n":
                    self.line += 1
                    self.newline = True
                i += 1
        else:
            self.problem(start_line, "unterminated block comment")
        self.i = i

    def char(self, inner: bool) -> None:
        text, n = self.text, self.n
        start, line = self.i, self.line
        i = start + 1
        while i < n and text[i] not in "'\n":
            i += 2 if text[i] == "\\" else 1
        if i >= n or text[i] != "'":
            self.problem(line, "unterminated character literal")
            self.i = min(i, n)
            self.emit("char", start, line, inner)
            return
        self.i = i + 1
        self.emit("char", start, line, inner, text=unescape(text[start + 1:i]))

    def string(self, inner: bool) -> None:
        text = self.text
        start, line = self.i, self.line
        if text.startswith('"""', self.i):
            self.raw_string(inner, start, line)
            return
        i = self.i + 1
        parts: list[tuple[str, str]] = []
        lit_start = i
        n = self.n
        while True:
            if i >= n or text[i] == "\n":
                self.problem(line, "unterminated string")
                self.i = i
                self.emit("string", start, line, inner)
                return
            c = text[i]
            if c == "\\":
                i += 2
            elif c == '"':
                break
            elif c == "$" and self.kotlin and i + 1 < n and (text[i + 1] == "{" or _is_ident_start(text[i + 1])):
                if i > lit_start:
                    parts.append(("lit", text[lit_start:i]))
                i = self.template(i, parts)
                lit_start = i
                if i < 0:
                    return
            else:
                i += 1
        if i > lit_start:
            parts.append(("lit", text[lit_start:i]))
        self.i = i + 1
        has_template = any(kind == "expr" for kind, _ in parts)
        if has_template:
            self.tokens.append(Token("string", text[start:self.i], line, self.line, start, self.i, self.newline, inner, None, tuple(parts)))
        else:
            self.tokens.append(Token("string", text[start:self.i], line, self.line, start, self.i, self.newline, inner, unescape(text[start + 1:i]), None))
        self.newline = False
        self.flush_inner()

    def raw_string(self, inner: bool, start: int, line: int) -> None:
        """A Java text block or a Kotlin raw string: from `\"\"\"` to the next `\"\"\"` (any further quotes are content)."""
        text, n = self.text, self.n
        i = start + 3
        parts: list[tuple[str, str]] = []
        lit_start = i
        while True:
            if i >= n:
                self.problem(line, "unterminated text block")
                self.i = n
                self.emit("string", start, line, inner)
                return
            c = text[i]
            if c == "\n":
                self.line += 1
                i += 1
            elif c == "\\" and not self.kotlin:
                i += 2 if i + 1 < n and text[i + 1] != "\n" else 1
            elif text.startswith('"""', i):
                while i + 3 < n and text[i + 3] == '"':
                    i += 1
                break
            elif c == "$" and self.kotlin and i + 1 < n and (text[i + 1] == "{" or _is_ident_start(text[i + 1])):
                if i > lit_start:
                    parts.append(("lit", text[lit_start:i]))
                i = self.template(i, parts)
                lit_start = i
                if i < 0:
                    return
            else:
                i += 1
        if i > lit_start:
            parts.append(("lit", text[lit_start:i]))
        self.i = i + 3
        has_template = any(kind == "expr" for kind, _ in parts)
        body = text[start + 3:i]
        self.tokens.append(Token("string", text[start:self.i], line, self.line, start, self.i, self.newline, inner, None if has_template else body,
                                 tuple(parts) if has_template else None))
        self.newline = False
        self.flush_inner()

    def template(self, i: int, parts: list[tuple[str, str]]) -> int:
        """Lex the template that starts at `$` (offset `i`); adds an ("expr", source) part and queues the names it spells. Returns the
        offset after it (-1 when the text ended inside it: the problem is recorded)."""
        text = self.text
        if text[i + 1] != "{":
            j = i + 1
            while j < self.n and _is_ident_part(text[j]):
                j += 1
            name = text[i + 1:j]
            parts.append(("expr", name))
            self.pending.append(Token("ident", name, self.line, self.line, i + 1, j, False, True))
            return j
        sub = _Lexer(text, self.kotlin)
        sub.i, sub.line = i + 2, self.line
        closed = sub.run(stop_at_brace=True, inner=True)
        self.line = sub.line
        self.problems.extend(sub.problems)
        if not closed:
            self.problem(self.line, "unterminated string template")
            self.i = self.n
            return -1
        parts.append(("expr", text[i + 2:sub.i]))
        self.pending.extend(t._replace(inner=True) for t in sub.tokens)
        return sub.i + 1

    def flush_inner(self) -> None:
        if self.pending:
            self.tokens.extend(self.pending)
            self.pending.clear()


def tokenize(text: str, kotlin: bool = False) -> LexResult:
    """Tokenize Java (or, with `kotlin=True`, Kotlin) source text. Never raises on its content."""
    lx = _Lexer(text, kotlin)
    lx.run()
    for opener, line in lx.brackets:
        lx.problem(line, f"{opener!r} is never closed")
    return LexResult(tuple(lx.tokens), tuple(lx.problems))
