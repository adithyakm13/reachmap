"""The Java / Kotlin lexer: comments, strings, text blocks, raw strings, character literals, annotations, generics and Kotlin string
templates. Every later step works on tokens, so text in a comment or a string never counts as code (Shape B: match code, not comments).
"""

import time
import unittest

from reachmap.adapters.jvm.lexer import Token, tokenize, unescape


def toks(text, kotlin=False):
    return tokenize(text, kotlin).tokens


def values(text, kotlin=False, inner=False):
    return [t.value for t in toks(text, kotlin) if inner or not t.inner]


def kinds(text, kotlin=False):
    return [(t.kind, t.value) for t in toks(text, kotlin) if not t.inner]


class Comments(unittest.TestCase):
    def test_a_line_comment_is_not_code(self):
        self.assertEqual(values("int a; // import x.Y;\nint b;"), ["int", "a", ";", "int", "b", ";"])

    def test_a_block_comment_is_not_code_and_counts_lines(self):
        t = toks("a /* b\nc */ d\ne")
        self.assertEqual([(x.value, x.line) for x in t], [("a", 1), ("d", 2), ("e", 3)])

    def test_comment_markers_inside_strings_are_text(self):
        t = kinds('String u = "http://host/a" + "/* no */";')
        self.assertEqual(t[3:6], [("string", '"http://host/a"'), ("punct", "+"), ("string", '"/* no */"')])

    def test_strings_and_quotes_inside_comments_are_not_strings(self):
        res = tokenize("// it's a \"quote\n/* `tick \" ${ */ a")
        self.assertEqual([t.value for t in res.tokens], ["a"])
        self.assertEqual(res.problems, ())

    def test_a_java_block_comment_does_not_nest(self):
        self.assertEqual(values("/* /* */ a */"), ["a", "*", "/"])

    def test_a_kotlin_block_comment_nests(self):
        res = tokenize("/* /* inner */ still comment */ a", True)
        self.assertEqual([t.value for t in res.tokens], ["a"])
        self.assertEqual(res.problems, ())

    def test_an_unterminated_block_comment_is_a_problem_not_a_crash(self):
        res = tokenize("a /* never closed\nb")
        self.assertEqual([t.value for t in res.tokens], ["a"])
        self.assertTrue(any("unterminated block comment" in p for p in res.problems))

    def test_an_import_in_a_comment_is_not_a_token(self):
        self.assertNotIn("import", values("// import a.B;\n/* import c.D; */\nclass X {}"))

    def test_javadoc_is_a_comment(self):
        self.assertEqual(values("/** See {@link Foo} and \"quotes\" */ class A {}"), ["class", "A", "{", "}"])


class Strings(unittest.TestCase):
    def test_escapes_are_skipped_and_decoded(self):
        (t,) = toks(r'"a\"b\\cA\n"')
        self.assertEqual((t.kind, t.text), ("string", 'a"b\\cA\n'))

    def test_a_string_holds_code_like_text_and_is_one_token(self):
        t = toks('String s = "class X { int y; } // no";')
        self.assertEqual([x.kind for x in t], ["ident", "ident", "punct", "string", "punct"])

    def test_a_text_block_spans_lines_and_holds_anything(self):
        src = 'String t = """\n    select "x" // not a comment\n    class A { \n    """;\nint after;'
        t = toks(src)
        string = [x for x in t if x.kind == "string"][0]
        self.assertEqual((string.line, string.end_line), (1, 4))
        self.assertEqual([x.value for x in t if x.line == 5], ["int", "after", ";"])
        self.assertEqual(tokenize(src).problems, ())

    def test_a_text_block_with_an_escaped_triple_quote(self):
        src = 'String t = """\n  a \\""" b\n  """;\nint x;'
        res = tokenize(src)
        self.assertEqual(res.problems, ())
        self.assertEqual([x.value for x in res.tokens if x.line == 4], ["int", "x", ";"])

    def test_an_unterminated_string_is_a_problem_and_ends_at_the_line(self):
        res = tokenize('String s = "oops;\nint a;')
        self.assertTrue(any("line 1: unterminated string" in p for p in res.problems))
        self.assertEqual([t.value for t in res.tokens if t.line == 2], ["int", "a", ";"])

    def test_an_unterminated_text_block_is_a_problem(self):
        res = tokenize('String t = """\n never closed')
        self.assertTrue(any("unterminated text block" in p for p in res.problems))

    def test_a_character_literal_with_a_quote_inside(self):
        t = kinds("char a = '\\''; char b = '\"'; char c = 'x';")
        self.assertEqual([v for k, v in t if k == "char"], ["'\\''", "'\"'", "'x'"])
        self.assertEqual(tokenize("char a = '\\'';").problems, ())

    def test_an_apostrophe_in_a_string_is_text(self):
        self.assertEqual(kinds('x = "don\'t";')[1:3], [("punct", "="), ("string", '"don\'t"')])

    def test_an_unterminated_character_literal_is_a_problem(self):
        self.assertTrue(tokenize("char a = 'x;").problems)

    def test_unescape(self):
        self.assertEqual(unescape(r"a\tbB\q"), "a\tbB\\q")


class KotlinTemplates(unittest.TestCase):
    def test_a_simple_template_name_is_an_inner_token(self):
        t = toks('val s = "hello $name!"', True)
        string = [x for x in t if x.kind == "string"][0]
        self.assertEqual(string.parts, (("lit", "hello "), ("expr", "name"), ("lit", "!")))
        self.assertEqual([x.value for x in t if x.inner], ["name"])

    def test_a_braced_expression_is_lexed_with_strings_and_braces_inside(self):
        src = 'val s = "a ${ f("}") + g(mapOf(1 to "x")) } z"\nval after = 1'
        res = tokenize(src, True)
        self.assertEqual(res.problems, ())
        inner = [x.value for x in res.tokens if x.inner]
        self.assertIn("f", inner)
        self.assertIn("g", inner)
        self.assertIn("mapOf", inner)
        self.assertEqual([x.value for x in res.tokens if x.line == 2 and not x.inner], ["val", "after", "=", "1"])

    def test_a_template_inside_a_template(self):
        res = tokenize('val s = "a ${ "b ${c} d" } e"', True)
        self.assertEqual(res.problems, ())
        self.assertIn("c", [x.value for x in res.tokens if x.inner])

    def test_a_dollar_that_starts_no_name_is_text(self):
        (t,) = toks('"price: $ 5 and \\$x"', True)
        self.assertIsNone(t.parts)
        self.assertEqual(t.text, "price: $ 5 and $x")

    def test_java_has_no_templates(self):
        (t,) = toks('"$name ${x}"')
        self.assertEqual(t.text, "$name ${x}")
        self.assertEqual([x for x in toks('"$name ${x}"') if x.inner], [])

    def test_a_raw_string_holds_templates_and_quotes(self):
        src = 'val r = """a "quoted" $x ${y.z} // no"""\nval n = 1'
        res = tokenize(src, True)
        self.assertEqual(res.problems, ())
        self.assertEqual([x.value for x in res.tokens if x.inner], ["x", "y", ".", "z"])
        self.assertEqual([x.value for x in res.tokens if x.line == 2], ["val", "n", "=", "1"])

    def test_extra_quotes_at_the_end_of_a_raw_string_are_content(self):
        res = tokenize('val r = """say "hi""""\nval n = 1', True)
        self.assertEqual(res.problems, ())
        self.assertEqual([x.value for x in res.tokens if x.line == 2], ["val", "n", "=", "1"])

    def test_an_unterminated_template_is_a_problem(self):
        self.assertTrue(any("unterminated" in p for p in tokenize('val s = "a ${ b', True).problems))

    def test_a_backtick_name_is_an_identifier(self):
        t = toks("fun `should work`() = 1", True)
        self.assertEqual([(x.kind, x.value) for x in t[:3]], [("ident", "fun"), ("ident", "should work"), ("punct", "(")])


class Punctuation(unittest.TestCase):
    def test_generics_close_one_angle_at_a_time(self):
        self.assertEqual(values("List<List<String>> x;"), ["List", "<", "List", "<", "String", ">", ">", "x", ";"])

    def test_joined_operators(self):
        self.assertEqual(values("a?.b ?: c!!, x -> y, A::b, f(1..3)", True)[1:3], ["?.", "b"])
        self.assertEqual(values("x -> y")[1], "->")
        self.assertIn("::", values("A::b"))
        self.assertIn("..", values("1..3", True))
        self.assertIn("...", values("void f(String... a) {}"))

    def test_annotations_are_an_at_sign_and_a_name(self):
        self.assertEqual(values('@GetMapping("/x") public void f() {}')[:5], ["@", "GetMapping", "(", '"/x"', ")"])
        self.assertEqual(values("@interface Marker {}")[:3], ["@", "interface", "Marker"])
        self.assertEqual(values("@field:JvmName(\"x\") val a = 1", True)[:5], ["@", "field", ":", "JvmName", "("])

    def test_numbers(self):
        self.assertEqual([x.kind for x in toks("1_000L 0x1F 1.5f 1e-3 0b101 2.0 .5")], ["number"] * 7)
        self.assertEqual(values("1..5", True), ["1", "..", "5"])

    def test_a_dollar_in_a_java_name(self):
        self.assertEqual(values("int a$b = c$;"), ["int", "a$b", "=", "c$", ";"])


class Brackets(unittest.TestCase):
    def test_a_balanced_file_has_no_problem(self):
        self.assertEqual(tokenize("class A { void f(int[] a) { g(a[0]); } }").problems, ())

    def test_an_unclosed_brace_is_a_problem(self):
        res = tokenize("class A {\n void f() {\n }\n")
        self.assertTrue(any("'{' is never closed" in p and "line 1" in p for p in res.problems))

    def test_an_unmatched_closing_bracket_is_a_problem(self):
        self.assertTrue(any("unmatched" in p for p in tokenize("class A { } }").problems))

    def test_a_mismatched_bracket_is_a_problem(self):
        self.assertTrue(any("closes the" in p for p in tokenize("f(a]").problems))

    def test_a_brace_in_a_string_or_char_does_not_count(self):
        self.assertEqual(tokenize("class A { String s = \"}\"; char c = '{'; }").problems, ())


class Positions(unittest.TestCase):
    def test_tokens_carry_lines_offsets_and_newlines(self):
        src = "a\nb  c"
        t = toks(src)
        self.assertEqual([(x.value, x.line, x.start, x.end, x.newline_before) for x in t], [("a", 1, 0, 1, False), ("b", 2, 2, 3, True), ("c", 2, 5, 6, False)])
        self.assertEqual([src[x.start:x.end] for x in t], ["a", "b", "c"])

    def test_a_multi_line_string_has_end_line(self):
        (t, _semi) = toks('"""\n\n"""\n;')
        self.assertEqual((t.line, t.end_line), (1, 3))

    def test_a_token_is_a_named_tuple(self):
        self.assertIsInstance(toks("a")[0], Token)


class Robustness(unittest.TestCase):
    def test_token_soup_never_raises_or_hangs(self):
        import random

        rng = random.Random(7)
        pieces = ['"', "'", '"""', "/*", "*/", "//", "${", "}", "{", "(", ")", "$", "`", "\\", "@", "<", ">", "\n", "a", "1", " ", "$x", "/"]
        start = time.monotonic()
        for _ in range(400):
            text = "".join(rng.choice(pieces) for _ in range(rng.randint(1, 60)))
            for kotlin in (False, True):
                res = tokenize(text, kotlin)
                for t in res.tokens:
                    self.assertGreaterEqual(t.end, t.start)
        self.assertLess(time.monotonic() - start, 10)

    def test_a_file_cut_at_every_offset_lexes(self):
        src = 'class A { @Get("/x") String f() { return "a" + """\n t\n"""; } }'
        for cut in range(len(src)):
            tokenize(src[:cut])
            tokenize(src[:cut], True)

    def test_speed(self):
        src = ("class A {\n  @GetMapping(\"/x/{id}\")\n  public String get(String id) { return \"v\" + id; /* c */ }\n}\n") * 400
        start = time.monotonic()
        tokenize(src)
        self.assertLess(time.monotonic() - start, 5)


if __name__ == "__main__":
    unittest.main()
