"""A tokenizer for JavaScript and TypeScript (and JSX/TSX) source.

It knows exactly what is needed to tell code from text: line and block comments, single and
double quoted strings, template literals with nested `${ ... }` substitutions (braces,
strings, templates and comments inside), and regular expression literals (decided by the
previous token). Everything later works on the tokens, so text in a comment or a string is
never taken for code.

What it is not: a parser, and not a conforming lexer. JSX text is lexed as code: braces
balance, and a few rules keep the usual JSX text from derailing it (see "Limits" in
docs/briefs/M5-js-adapter.md). It never raises on bad input; what it could not make sense
of is listed in `LexResult.problems`.

`Token.kind` is one of `ident` (keywords and `#private` names included), `number`, `string`,
`template` (a chunk of a template literal: the whole literal when it has no substitution,
else pieces whose raw text ends with `${` or starts with `}`), `regex` and `punct`.
`line` is the 1-based line the token starts on, `end_line` where it ends.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NamedTuple

__all__ = ["LexResult", "Token", "tokenize", "unquote", "KEYWORDS"]


class Token(NamedTuple):
    kind: str
    value: str  # the exact source text
    line: int
    end_line: int
    start: int  # offsets into the source
    end: int
    newline_before: bool  # a line break between the previous token (or comment) and this one


@dataclass(frozen=True, slots=True)
class LexResult:
    tokens: tuple[Token, ...]
    comments: tuple[Token, ...]  # kind "comment"
    problems: tuple[str, ...]  # "line 3: unterminated string"


# Keywords after which a `/` starts a regular expression, not a division.
REGEX_AFTER_KEYWORDS = frozenset({
    "return", "typeof", "instanceof", "in", "of", "new", "delete", "void", "throw", "case", "do", "else",
    "yield", "await", "extends",
})
# Words after which a quote directly following (no space) is still a string, as in `from"x"`.
KEYWORDS = frozenset({
    "from", "as", "of", "in", "typeof", "instanceof", "case", "return", "void", "delete", "else", "yield", "await",
    "export", "default", "import", "extends", "new", "throw", "do", "satisfies", "is", "keyof", "require",
})

_IDENT_START = re.compile(r"[^\W\d]|[$#]", re.UNICODE)
_IDENT = re.compile(r"#?(?:[^\W\d]|\$)(?:\w|\$)*", re.UNICODE)
_NUMBER = re.compile(
    r"0[xX][0-9a-fA-F_]+n?|0[oO][0-7_]+n?|0[bB][01_]+n?|(?:\d[\d_]*\.?[\d_]*|\.\d[\d_]*)(?:[eE][+-]?\d[\d_]*)?n?"
)
_PUNCT = (
    "===", "!==", "...", "**=", "<<=", "&&=", "||=", "??=",
    "=>", "==", "!=", "<=", ">=", "&&", "||", "??", "?.", "++", "--", "+=", "-=", "*=", "%=", "&=", "|=", "^=", "**", "<<",
)
_SPACE = " \t\r\f\v\u00a0\ufeff\u2028\u2029"


def tokenize(text: str) -> LexResult:
    """Tokenize source text. Never raises on its content."""
    tokens: list[Token] = []
    comments: list[Token] = []
    problems: list[str] = []
    n = len(text)
    i = 0
    line = 1
    newline = False  # a line break seen since the last token
    brace_stack: list[bool] = []  # one entry per open `{`; True when it opened a template substitution
    prev: Token | None = None

    def add(kind: str, start: int, end: int, start_line: int, end_line: int) -> None:
        nonlocal prev, newline
        tok = Token(kind, text[start:end], start_line, end_line, start, end, newline)
        tokens.append(tok)
        prev = tok
        newline = False

    def template_chunk(pos: int, start_line: int, tok_start: int) -> tuple[int, int]:
        """Scan template text from pos (just after the opening backtick or the `}` that closed a
        substitution) up to the closing backtick or the next `${`. Emits the chunk and returns
        (new position, new line)."""
        j, ln = pos, start_line
        while j < n:
            c = text[j]
            if c == "\\":
                if j + 1 < n and text[j + 1] == "\n":
                    ln += 1
                elif j + 1 < n and text[j + 1] == "\r" and j + 2 < n and text[j + 2] == "\n":
                    j += 1
                    ln += 1
                j += 2
                continue
            if c == "\n":
                ln += 1
            elif c == "`":
                add("template", tok_start, j + 1, start_line, ln)
                return j + 1, ln
            elif c == "$" and j + 1 < n and text[j + 1] == "{":
                add("template", tok_start, j + 2, start_line, ln)
                brace_stack.append(True)
                return j + 2, ln
            j += 1
        problems.append(f"line {start_line}: unterminated template literal")
        add("template", tok_start, n, start_line, ln)
        return n, ln

    def regex_allowed() -> bool:
        if prev is None:
            return True
        if prev.kind == "punct":
            return prev.value not in (")", "]", "}", "++", "--")
        if prev.kind == "ident":
            return prev.value in REGEX_AFTER_KEYWORDS
        if prev.kind == "template":
            return prev.value.endswith("${")  # the start of a substitution; the end of a literal is a value
        return False

    if text.startswith("#!"):
        end = text.find("\n")
        end = n if end == -1 else end
        comments.append(Token("comment", text[:end], 1, 1, 0, end, False))
        i = end

    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            newline = True
            i += 1
            continue
        if c in _SPACE:
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/" and not (i > 0 and text[i - 1] == ":" and i + 2 < n and text[i + 2] not in " \t\r\n"):
            # (a `//` glued to a `:` and to what follows is a URL in JSX text, `http://x`, not a comment)
            end = text.find("\n", i)
            end = n if end == -1 else end
            comments.append(Token("comment", text[i:end], line, line, i, end, newline))
            i = end
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            if end == -1:
                problems.append(f"line {line}: unterminated block comment")
                end = n
                stop = n
            else:
                stop = end + 2
            body = text[i:stop]
            lines_in = body.count("\n")
            comments.append(Token("comment", body, line, line + lines_in, i, stop, newline))
            if lines_in:
                newline = True
            line += lines_in
            i = stop
            continue
        if c in "\"'":
            if prev is not None and prev.kind == "ident" and prev.end == i and prev.value not in KEYWORDS \
                    and not prev.value.startswith("#"):
                # A quote glued to a word cannot start a string in valid code: it is JSX text ("Don't").
                add("punct", i, i + 1, line, line)
                i += 1
                continue
            j, start_line = i + 1, line
            while j < n:
                d = text[j]
                if d == "\\":
                    if j + 1 < n and text[j + 1] == "\n":
                        line += 1
                    elif j + 2 < n and text[j + 1] == "\r" and text[j + 2] == "\n":
                        line += 1
                        j += 1
                    j += 2
                    continue
                if d == c:
                    break
                if d == "\n":
                    break
                j += 1
            if j >= n or text[j] != c:
                problems.append(f"line {start_line}: unterminated string")
                end = min(j, n)
            else:
                end = j + 1
            add("string", i, end, start_line, line)
            i = end
            continue
        if c == "`":
            start_line = line
            i, line = template_chunk(i + 1, line, i)
            continue
        if c == "{":
            brace_stack.append(False)
            add("punct", i, i + 1, line, line)
            i += 1
            continue
        if c == "}":
            if brace_stack and brace_stack.pop():
                start_line = line
                i, line = template_chunk(i + 1, line, i)
                continue
            add("punct", i, i + 1, line, line)
            i += 1
            continue
        if c == "/" and regex_allowed() and not (prev is not None and prev.value == "<" and prev.end == i):
            j, in_class = i + 1, False
            while j < n:
                d = text[j]
                if d == "\n":
                    break
                if d == "\\":
                    j += 2
                    continue
                if d == "[":
                    in_class = True
                elif d == "]":
                    in_class = False
                elif d == "/" and not in_class:
                    break
                j += 1
            if j < n and text[j] == "/":
                j += 1
                while j < n and (text[j].isalnum() or text[j] in "_$"):
                    j += 1
                add("regex", i, j, line, line)
                i = j
                continue
            # no closing slash on the line: it was a division after all
        if c.isdigit() or (c == "." and i + 1 < n and text[i + 1].isdigit()):
            m = _NUMBER.match(text, i)
            end = m.end() if m else i + 1
            add("number", i, end, line, line)
            i = end
            continue
        if _IDENT_START.match(c):
            m = _IDENT.match(text, i)
            if m:
                add("ident", i, m.end(), line, line)
                i = m.end()
                continue
        matched = False
        for op in _PUNCT:
            if text.startswith(op, i):
                if op == "?." and i + 2 < n and text[i + 2].isdigit():
                    continue
                add("punct", i, i + len(op), line, line)
                i += len(op)
                matched = True
                break
        if matched:
            continue
        add("punct", i, i + 1, line, line)
        i += 1
    return LexResult(tuple(tokens), tuple(comments), tuple(problems))


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}


def unquote(raw: str) -> str:
    """The value of a string literal token (or a template without substitutions): quotes
    removed, the usual escapes applied."""
    if len(raw) >= 2 and raw[0] in "\"'`" and raw[-1] == raw[0]:
        raw = raw[1:-1]
    elif raw and raw[0] in "\"'`":
        raw = raw[1:]
    out: list[str] = []
    i, n = 0, len(raw)
    while i < n:
        c = raw[i]
        if c != "\\" or i + 1 >= n:
            out.append(c)
            i += 1
            continue
        d = raw[i + 1]
        if d == "u" and re.fullmatch(r"[0-9a-fA-F]{4}", raw[i + 2:i + 6]):
            out.append(chr(int(raw[i + 2:i + 6], 16)))
            i += 6
        elif d == "x" and re.fullmatch(r"[0-9a-fA-F]{2}", raw[i + 2:i + 4]):
            out.append(chr(int(raw[i + 2:i + 4], 16)))
            i += 4
        elif d == "\n":
            i += 2
        elif d == "\r":
            i += 3 if raw[i + 2:i + 3] == "\n" else 2
        else:
            out.append(_ESCAPES.get(d, d))
            i += 2
    return "".join(out)
