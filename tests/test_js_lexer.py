"""The JS/TS lexer: comments, strings, template literals, regex literals, JSX braces.

Every later step works on tokens, so text in a comment or a string never counts as code
(Shape B: match code, not comments)."""

import time
import unittest

from reachmap.adapters.js.lexer import Token, tokenize, unquote


def kinds(text):
    return [(t.kind, t.value) for t in tokenize(text).tokens]


def values(text):
    return [t.value for t in tokenize(text).tokens]


def idents(text):
    return [t.value for t in tokenize(text).tokens if t.kind == "ident"]


class Comments(unittest.TestCase):
    def test_line_comment_is_not_code(self):
        res = tokenize('const a = 1; // import x from "y"\nconst b = 2;')
        self.assertEqual([t.value for t in res.tokens if t.kind == "ident"], ["const", "a", "const", "b"])
        self.assertEqual(len(res.comments), 1)
        self.assertEqual(res.comments[0].line, 1)

    def test_block_comment_is_not_code_and_counts_lines(self):
        res = tokenize("a /* b\nc */ d\ne")
        self.assertEqual([(t.value, t.line) for t in res.tokens], [("a", 1), ("d", 2), ("e", 3)])
        self.assertEqual((res.comments[0].line, res.comments[0].end_line), (1, 2))

    def test_comment_markers_inside_strings_are_text(self):
        self.assertEqual(kinds('x = "http://host/a" + \'/* no */\';')[2:5],
                         [("string", '"http://host/a"'), ("punct", "+"), ("string", "'/* no */'")])
        self.assertEqual(tokenize('x = "a // b"; y = 1').comments, ())

    def test_strings_inside_comments_are_not_strings(self):
        res = tokenize("// it's a \"quote\n/* `backtick ${ */ a")
        self.assertEqual(values("// it's a \"quote\n/* `backtick ${ */ a"), ["a"])
        self.assertEqual(len(res.comments), 2)
        self.assertEqual(res.problems, ())

    def test_block_comment_does_not_nest(self):
        self.assertEqual(values("/* /* */ a */"), ["a", "*", "/"])

    def test_unterminated_block_comment_is_a_problem_not_a_crash(self):
        res = tokenize("a /* never closed\nb")
        self.assertEqual(values("a /* never closed\nb"), ["a"])
        self.assertTrue(any("unterminated block comment" in p for p in res.problems))

    def test_hashbang_line_is_a_comment(self):
        res = tokenize("#!/usr/bin/env node\nconst a = 1")
        self.assertEqual(res.tokens[0].value, "const")
        self.assertEqual(len(res.comments), 1)

    def test_import_in_a_comment_is_not_a_token(self):
        self.assertNotIn("import", idents('// import { a } from "./a"\n/* require("x") */\nconst b = 1'))


class Strings(unittest.TestCase):
    def test_escaped_quotes_and_backslashes(self):
        self.assertEqual(kinds(r"a = 'it\'s' + " + '"say \\"hi\\""'), [
            ("ident", "a"), ("punct", "="), ("string", r"'it\'s'"), ("punct", "+"), ("string", '"say \\"hi\\""')])
        self.assertEqual(kinds(r'"a\\" b')[:2], [("string", r'"a\\"'), ("ident", "b")])

    def test_line_continuation_in_a_string(self):
        res = tokenize('a = "x\\\ny"; b')
        self.assertEqual([(t.value, t.line, t.end_line) for t in res.tokens if t.kind == "string"], [('"x\\\ny"', 1, 2)])
        self.assertEqual(res.tokens[-1].line, 2)

    def test_unterminated_string_ends_at_the_line_end(self):
        res = tokenize("a = 'oops\nb = 2")
        self.assertEqual([t.value for t in res.tokens if t.kind == "ident"], ["a", "b"])
        self.assertTrue(any("unterminated string" in p for p in res.problems))

    def test_unquote(self):
        self.assertEqual(unquote('"a\\"b"'), 'a"b')
        self.assertEqual(unquote("'./x'"), "./x")
        self.assertEqual(unquote(r'"A\x42\n"'), "AB\n")
        self.assertEqual(unquote("`./plain`"), "./plain")


class Templates(unittest.TestCase):
    def test_plain_template_is_one_token(self):
        self.assertEqual(kinds("x = `a b`"), [("ident", "x"), ("punct", "="), ("template", "`a b`")])

    def test_expression_tokens_are_code(self):
        text = "x = `n ${formatAmount(v)} !`"
        self.assertEqual(idents(text), ["x", "formatAmount", "v"])
        self.assertEqual([t.value for t in tokenize(text).tokens if t.kind == "template"], ["`n ${", "} !`"])

    def test_nested_template_with_braces_and_strings(self):
        text = 'a = `x ${ f(`y ${ {k: 1}.k } z`, "}") } w`; b = 2'
        res = tokenize(text)
        self.assertEqual([t.value for t in res.tokens if t.kind == "ident"], ["a", "f", "k", "k", "b"])
        self.assertEqual(res.tokens[-3].value, "b")
        self.assertEqual(res.problems, ())
        # the "}" in the string did not close the substitution
        self.assertIn(("string", '"}"'), kinds(text))

    def test_backtick_and_dollar_escapes(self):
        self.assertEqual(kinds(r"a = `\` \${not} x`")[2:], [("template", r"`\` \${not} x`")])

    def test_dollar_without_brace_is_text(self):
        self.assertEqual(kinds("a = `$5 and $`")[2:], [("template", "`$5 and $`")])

    def test_multi_line_template_lines(self):
        res = tokenize("a = `l1\nl2 ${\n  b\n} l4`\nc")
        self.assertEqual([(t.value, t.line) for t in res.tokens if t.kind == "ident"], [("a", 1), ("b", 3), ("c", 5)])
        self.assertEqual(res.tokens[2].line, 1)
        self.assertEqual(res.tokens[2].end_line, 2)

    def test_comment_inside_a_substitution(self):
        self.assertEqual(idents("a = `${ b /* } */ + c // }\n}`"), ["a", "b", "c"])

    def test_unterminated_template_is_a_problem(self):
        res = tokenize("a = `never")
        self.assertTrue(any("unterminated template" in p for p in res.problems))

    def test_static_template_is_an_unquotable_string(self):
        tok = tokenize("require(`./x`)").tokens[2]
        self.assertEqual((tok.kind, unquote(tok.value)), ("template", "./x"))


class Regex(unittest.TestCase):
    def test_regex_with_slashes_quotes_and_classes(self):
        text = r"""const re = /ab\/c[/]d'"`/gi; const x = 1"""
        toks = kinds(text)
        self.assertIn(("regex", r"""/ab\/c[/]d'"`/gi"""), toks)
        self.assertEqual([v for k, v in toks if k == "ident"], ["const", "re", "const", "x"])

    def test_double_slash_inside_a_regex_is_not_a_comment(self):
        res = tokenize(r"const r = /\/\//; const y = 2")
        self.assertEqual(res.comments, ())
        self.assertIn(("regex", r"/\/\//"), [(t.kind, t.value) for t in res.tokens])
        self.assertEqual(idents(r"const r = /\/\//; const y = 2"), ["const", "r", "const", "y"])

    def test_division_is_not_a_regex(self):
        text = "a = b / c / d"
        self.assertEqual([t.kind for t in tokenize(text).tokens].count("regex"), 0)
        self.assertEqual(idents(text), ["a", "b", "c", "d"])
        self.assertEqual([t.kind for t in tokenize("x = (a) / 2 / 3").tokens].count("regex"), 0)
        self.assertEqual([t.kind for t in tokenize("x = a[1] / 2 / 3").tokens].count("regex"), 0)
        self.assertEqual([t.kind for t in tokenize("x = 4 / 2 / 3").tokens].count("regex"), 0)

    def test_regex_after_keywords_and_operators(self):
        for text in ("return /a+/.test(s)", "x = y || /a/", "f(/a/, 1)", "if (a) { } else if (/x/.test(b)) {}",
                     "typeof /a/", "case /a/: break", "x = [/a/, /b/]", "!/a/.test(s)"):
            self.assertIn("regex", [t.kind for t in tokenize(text).tokens], text)

    def test_regex_quote_does_not_start_a_string(self):
        res = tokenize("const q = /'/; const z = 'ok'")
        self.assertEqual([t.value for t in res.tokens if t.kind == "string"], ["'ok'"])
        self.assertEqual(res.problems, ())

    def test_slash_without_closing_slash_on_the_line_is_division(self):
        res = tokenize("x = ( a ) / b\ny = /z/")
        self.assertEqual(idents("x = ( a ) / b\ny = /z/"), ["x", "a", "b", "y"])

    def test_closing_tag_slash_is_not_a_regex(self):
        text = "<ul><li>{a}</li>{b && <i>x</i>}</ul>"
        res = tokenize(text)
        self.assertNotIn("regex", [t.kind for t in res.tokens])
        self.assertEqual(brace_balance(res), 0)

    def test_comment_after_a_regex(self):
        res = tokenize("const r = /a/g // note\nconst s = 1")
        self.assertEqual(len(res.comments), 1)
        self.assertEqual(idents("const r = /a/g // note\nconst s = 1"), ["const", "r", "const", "s"])


def brace_balance(res):
    depth = 0
    for t in res.tokens:
        if t.kind == "punct" and t.value in "{(":
            depth += 1
        elif t.kind == "punct" and t.value in "})":
            depth -= 1
        elif t.kind == "template":
            if t.value.endswith("${"):
                depth += 1
            if t.value.startswith("}"):
                depth -= 1
    return depth


class Jsx(unittest.TestCase):
    SRC = """const A = () => (
  <ul className="list">
    {rows.map((row) => (
      <li key={row.id} title={`row ${row.id}`}>
        {row.label}{" "}
        {row.ok ? <b>{"yes"}</b> : null}
      </li>
    ))}
  </ul>
);
"""

    def test_braces_balance_in_jsx(self):
        res = tokenize(self.SRC)
        self.assertEqual(brace_balance(res), 0)
        self.assertEqual(res.problems, ())

    def test_jsx_expression_tokens_are_code(self):
        self.assertIn("rows", idents(self.SRC))
        self.assertIn("label", idents(self.SRC))

    def test_apostrophe_in_jsx_text_does_not_swallow_the_next_lines(self):
        src = "const A = () => <p>Don't panic</p>;\nexport const B = 1;\n"
        res = tokenize(src)
        self.assertIn("B", [t.value for t in res.tokens if t.kind == "ident"])
        self.assertEqual(res.problems, ())

    def test_a_url_in_jsx_text_is_not_a_comment(self):
        src = 'const A = () => <a>http://x.test/a {fmt(1)}</a>;\nexport const B = 1;\n'
        res = tokenize(src)
        self.assertEqual(res.comments, ())
        self.assertIn("fmt", [t.value for t in res.tokens if t.kind == "ident"])
        self.assertEqual(brace_balance(res), 0)

    def test_a_comment_after_a_colon_with_a_space_is_still_a_comment(self):
        res = tokenize("const a = ok ? 1 : // note\n  2;\n")
        self.assertEqual(len(res.comments), 1)

    def test_apostrophe_after_a_closing_brace_ends_at_the_line(self):
        src = "const A = () => <p>{name}'s page</p>;\nexport const B = 1;\n"
        res = tokenize(src)
        self.assertIn("B", [t.value for t in res.tokens if t.kind == "ident"])
        self.assertEqual(brace_balance(res), 0)

    def test_string_directly_after_a_keyword_is_still_a_string(self):
        self.assertEqual([t.kind for t in tokenize('import x from"./a"').tokens][-1], "string")
        self.assertEqual([t.kind for t in tokenize('return"x"').tokens][-1], "string")


class Misc(unittest.TestCase):
    def test_identifiers_numbers_and_punctuation(self):
        text = "$a_1 = café?.b ?? c; d = 1_000 + .5 + 0xFF + 1e-3 + 10n; e(...f) => 0; g !== h; i === j; k == l"
        toks = kinds(text)
        for expected in [("ident", "$a_1"), ("ident", "café"), ("punct", "?."), ("punct", "??"), ("number", "1_000"),
                         ("number", ".5"), ("number", "0xFF"), ("number", "1e-3"), ("number", "10n"), ("punct", "..."),
                         ("punct", "=>"), ("punct", "!=="), ("punct", "==="), ("punct", "==")]:
            self.assertIn(expected, toks)

    def test_ternary_with_a_leading_dot_number_is_not_optional_chaining(self):
        self.assertEqual(kinds("a?.5:1")[:3], [("ident", "a"), ("punct", "?"), ("number", ".5")])

    def test_private_names_and_decorators(self):
        self.assertIn(("ident", "#secret"), kinds("class A { #secret = 1; }"))
        self.assertEqual(kinds("@Component() class A {}")[:2], [("punct", "@"), ("ident", "Component")])

    def test_lines_are_one_based_and_newline_before_is_tracked(self):
        res = tokenize("a\n\nb c")
        self.assertEqual([(t.value, t.line, t.newline_before) for t in res.tokens], [("a", 1, False), ("b", 3, True), ("c", 3, False)])

    def test_crlf_line_counting(self):
        res = tokenize("a\r\nb\r\n/* x\r\ny */ c")
        self.assertEqual([(t.value, t.line) for t in res.tokens], [("a", 1), ("b", 2), ("c", 4)])

    def test_empty_input(self):
        res = tokenize("")
        self.assertEqual((res.tokens, res.comments, res.problems), ((), (), ()))

    def test_tokens_cover_offsets(self):
        text = 'import {a} from "b"; // c\n'
        for t in tokenize(text).tokens:
            self.assertEqual(text[t.start:t.end], t.value)

    def test_a_large_file_is_fast(self):
        text = ("export function f%d(a, b) { return `x ${a} y` + \"s\" + /re/.test(b) // c\n}\n" * 3000)
        start = time.monotonic()
        res = tokenize(text)
        self.assertLess(time.monotonic() - start, 3.0)
        self.assertGreater(len(res.tokens), 40000)

    def test_deeply_nested_brackets_do_not_recurse(self):
        text = "(" * 5000 + ")" * 5000 + "`${" * 500 + "}`" * 500
        tokenize(text)  # must not raise RecursionError

    def test_lone_surrogate_text_does_not_crash(self):
        tokenize("a = '\ud800'")


class TokenType(unittest.TestCase):
    def test_token_is_a_value(self):
        t = Token("ident", "a", 1, 1, 0, 1, False)
        self.assertEqual(t, Token("ident", "a", 1, 1, 0, 1, False))


if __name__ == "__main__":
    unittest.main()
