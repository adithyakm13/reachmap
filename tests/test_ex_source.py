"""The token level reading of the Express and NestJS adapters (`reachmap.adapters.express.source`): the arguments of a call, the identifiers
a range spells, module level, what a `require` binds, what a file exports, what a string evaluates to, and the Express path form.
No repository: a file is parsed and read.
"""

import unittest

from reachmap.adapters.express import source as sx
from reachmap.adapters.js.syntax import parse


def src_of(text, path="a.ts"):
    return sx.Src(parse(text), path)


def first(src, word, nth=0):
    """The index of the nth token that is the identifier `word`."""
    found = [i for i, t in enumerate(src.t) if t.kind == "ident" and t.value == word]
    return found[nth]


class Ctx:
    """The constants an evaluation may ask for."""

    def __init__(self, consts=None):
        self.consts = consts or {}

    def const(self, path, src, name, depth):
        return sx.Str(self.consts[name]) if name in self.consts else None


def evaluate(expr, consts=None):
    src = src_of(f"x({expr});")
    open_idx = first(src, "x") + 1
    (a, b), = src.args(open_idx)
    return src.eval(a, b, Ctx(consts))


class Arguments(unittest.TestCase):
    def test_the_arguments_of_a_call_are_split_at_the_top_level_commas(self):
        src = src_of('f("/a", g(1, 2), [3, 4], { k: 5 }, (x) => { return [1, 2]; });')
        args = src.args(first(src, "f") + 1)
        self.assertEqual([src.text(a, b) for a, b in args], ['"/a"', "g(1, 2)", "[3, 4]", "{ k: 5 }", "(x) => { return [1, 2]; }"])

    def test_an_empty_call_has_no_arguments_and_a_trailing_comma_adds_none(self):
        self.assertEqual(src_of("f();").args(1), [])
        src = src_of("f(1, 2,);")
        self.assertEqual(len(src.args(1)), 2)

    def test_a_template_with_substitutions_is_one_argument(self):
        src = src_of("f(`/a/${x}/b`, y);")
        self.assertEqual(len(src.args(1)), 2)

    def test_refs_are_the_identifiers_not_after_a_dot(self):
        src = src_of("f(ctrl.list, a?.b, c);")
        a, b = src.args(1)[0][0], src.args(1)[-1][1]
        self.assertEqual(sorted(src.refs(a, b)), ["a", "c", "ctrl"])

    def test_strip_removes_a_type_assertion_parentheses_and_a_semicolon(self):
        src = src_of('const x = ("/api" as const);')
        d = src.parsed.declarations[0]
        a, b = src.strip(d.init_tok, d.end_tok)
        self.assertEqual(src.text(a, b), '"/api"')


class Scopes(unittest.TestCase):
    def test_module_level_is_outside_calls_functions_classes_and_objects(self):
        src = src_of("a.get(1);\nfunction f() { b.get(2); }\nclass C { m() { c.get(3); } }\nconst o = { k: () => { d.get(4); } };\nx(() => { e.get(5); });\n")
        flags = {w: src.module_scope(first(src, w)) for w in "abcde"}
        self.assertEqual(flags, {"a": True, "b": False, "c": False, "d": False, "e": False})

    def test_blocks_that_run_when_the_module_loads_are_module_level(self):
        text = "if (x) { a.get(1); } else { b.get(2); }\ntry { c.get(3); } catch (err) { d.get(4); } finally { e.get(5); }\nfor (const k of ks) { f.get(6); }\nwhile (y) { g.get(7); }\n{ h.get(8); }\n"
        src = src_of(text)
        self.assertEqual({w: src.module_scope(first(src, w)) for w in "abcdefgh"}, {w: True for w in "abcdefgh"})

    def test_a_function_inside_a_block_is_not_module_level(self):
        src = src_of("if (x) { const f = () => { a.get(1); }; b.get(2); }\n")
        self.assertEqual((src.module_scope(first(src, "a")), src.module_scope(first(src, "b"))), (False, True))

    def test_a_method_body_with_a_return_type_that_holds_braces_is_not_module_level(self):
        src = src_of("class C { m(): { a: string } { z.get(1); } }\n")
        self.assertFalse(src.module_scope(first(src, "z")))

    def test_in_declaration_is_inside_a_top_level_declaration(self):
        src = src_of("const r = f();\nr.get(1);\nfunction g() { r.get(2); }\n")
        self.assertEqual([src.in_declaration(first(src, "r", n)) for n in range(3)], [True, False, True])


class Bindings(unittest.TestCase):
    def binds(self, text):
        return {k: (b.spec, b.imported) for k, b in src_of(text).bindings().items()}

    def test_es_imports(self):
        got = self.binds('import a from "./a";\nimport * as ns from "./n";\nimport { x, y as z } from "./m";\nimport d, { e } from "./d";\n')
        self.assertEqual(got, {"a": ("./a", "default"), "ns": ("./n", "*"), "x": ("./m", "x"), "z": ("./m", "y"), "d": ("./d", "default"), "e": ("./d", "e")})

    def test_require_forms(self):
        got = self.binds('const a = require("./a");\nconst { b, c: d } = require("./b");\nconst e = require("./e").router;\nlet f: Router = require("./f");\n')
        self.assertEqual(got, {"a": ("./a", "*cjs"), "b": ("./b", "b"), "d": ("./b", "c"), "e": ("./e", "router"), "f": ("./f", "*cjs")})

    def test_a_require_that_binds_nothing_is_not_a_binding(self):
        self.assertEqual(self.binds('require("./side");\napp.use(require("./r"));\nfoo(require("./x"));\n'), {})

    def test_a_member_called_require_is_not_a_require(self):
        self.assertEqual(self.binds('const a = loader.require("./a");\n'), {})


class Exports(unittest.TestCase):
    def exports(self, text):
        return {(e.name, e.local, e.source) for e in src_of(text).exports()}

    def test_es_exports(self):
        got = self.exports("const r = 1;\nexport default r;\nexport const s = 2;\nexport { r as t };\nexport { u } from './u';\n")
        self.assertEqual({(n, l) for n, l, _s in got if _s is None}, {("default", "r"), ("s", "s"), ("t", "r")})
        self.assertIn(("u", "u", "./u"), got)

    def test_commonjs_exports(self):
        self.assertEqual(self.exports("const r = 1;\nmodule.exports = r;\n"), {("*cjs", "r", None)})
        got = self.exports("module.exports = { a, b: c, d: f() };\n")
        self.assertEqual({(n, l) for n, l, _s in got}, {("a", "a"), ("b", "c"), ("d", None), ("*cjs", None)})
        self.assertEqual(self.exports("module.exports.x = y;\nexports.z = w;\n"), {("x", "y", None), ("z", "w", None)})

    def test_a_commonjs_member_that_is_a_literal_keeps_its_tokens(self):
        src = src_of('exports.P = "/api";\nmodule.exports.Q = `/q`;\nmodule.exports = { R: "/r", S: s };\n')
        got = {e.name: (e.local, src.text(*e.value) if e.value else None) for e in src.exports() if e.name != "*cjs"}
        self.assertEqual(got, {"P": (None, '"/api"'), "Q": (None, "`/q`"), "R": (None, '"/r"'), "S": ("s", None)})

    def test_module_exports_of_a_require_is_a_re_export(self):
        self.assertEqual(self.exports('module.exports = require("./inner");\n'), {("*cjs", None, "./inner")})

    def test_an_assignment_in_a_function_is_not_an_export(self):
        self.assertEqual(self.exports("function f() { module.exports = r; }\n"), set())


class Consts(unittest.TestCase):
    def test_a_name_declared_twice_is_unknown_and_the_others_are_their_initializers(self):
        src = src_of('const A = "/a";\nvar B = "/b";\nvar B = "/c";\nconst C = f();\n')
        got = sx.build_consts(src)
        self.assertEqual(src.text(*got["A"]), '"/a"')
        self.assertIsNone(got["B"])
        self.assertEqual(src.text(*got["C"]), "f()")


class Strings(unittest.TestCase):
    def test_literals_and_sums(self):
        self.assertEqual(evaluate('"/a"').value, "/a")
        self.assertEqual(evaluate("'/a' + \"/b\"").value, "/a/b")
        self.assertEqual(evaluate("`/a`").value, "/a")

    def test_a_template_with_a_substitution(self):
        self.assertEqual(evaluate("`/x/${V}/y`", {"V": "v1"}).value, "/x/v1/y")
        self.assertEqual(evaluate("`${V}/y`", {"V": "/v1"}).value, "/v1/y")
        self.assertEqual(evaluate("`/x/${V}`", {"V": "v1"}).value, "/x/v1")
        self.assertEqual(evaluate("`/${A}/${B}`", {"A": "a", "B": "b"}).value, "/a/b")

    def test_a_constant_and_a_sum_of_constants(self):
        self.assertEqual(evaluate("P", {"P": "/p"}).value, "/p")
        self.assertEqual(evaluate('P + "/x"', {"P": "/p"}).value, "/p/x")

    def test_a_type_assertion_and_parentheses_are_looked_through(self):
        self.assertEqual(evaluate('("/a" as string)').value, "/a")

    def test_what_cannot_be_evaluated_is_an_unknown_part_with_its_place(self):
        got = evaluate("dailyPath()")
        self.assertEqual((got.value, [(e, p, ln) for e, p, ln in got.unknown]), ("<unknown:dailyPath()>", [("dailyPath()", "a.ts", 1)]))
        got = evaluate('"/a/" + name')
        self.assertEqual(got.value, "/a/<unknown:name>")

    def test_a_template_with_an_unknown_substitution(self):
        got = evaluate("`/a/${x()}/b`")
        self.assertEqual(got.value, "/a/<unknown:x()>/b")

    def test_a_long_expression_is_cut_in_the_unknown_text(self):
        got = evaluate("f(" + "a, " * 40 + "b)")
        self.assertLessEqual(len(got.unknown[0][0]), 60)


class Paths(unittest.TestCase):
    def test_parameters_are_written_in_braces(self):
        self.assertEqual(sx.convert_path("/users/:id/posts/:postId"), [("/users/{id}/posts/{postId}", False)])

    def test_an_optional_parameter_is_two_paths(self):
        self.assertEqual(sx.convert_path("/users/:id?"), [("/users", False), ("/users/{id}", False)])

    def test_a_pattern_after_a_parameter_is_dropped_and_the_route_is_partial(self):
        self.assertEqual(sx.convert_path("/users/:id(\\d+)"), [("/users/{id}", True)])  # the route fits more than it serves

    def test_a_trailing_star_is_the_rest_of_the_path(self):
        self.assertEqual(sx.convert_path("/files/*"), [("/files/{rest:path}", False)])

    def test_a_repeating_parameter_is_the_rest_of_the_path_and_partial(self):
        self.assertEqual(sx.convert_path("/files/:name+"), [("/files/{name:path}", True)])
        self.assertEqual(sx.convert_path("/files/:name*"), [("/files/{name:path}", True)])  # what `*` after a parameter means differs between Express 4 and 5

    def test_express_5_optional_groups_are_two_paths(self):
        self.assertEqual(sx.convert_path("/users{/:id}"), [("/users", False), ("/users/{id}", False)])
        self.assertEqual(sx.convert_path("/a{/:b}/c"), [("/a/c", False), ("/a/{b}/c", False)])

    def test_a_trailing_star_is_not_partial(self):
        self.assertEqual(sx.convert_path("/f/*"), [("/f/{rest:path}", False)])

    def test_a_pattern_in_the_middle_is_an_unknown_segment(self):
        self.assertEqual(sx.convert_path("/a/b*c/d"), [("/a/<unknown:b*c>/d", True)])
        self.assertEqual(sx.convert_path("/a/file.:ext"), [("/a/<unknown:file.:ext>", True)])

    def test_slashes_are_normalised(self):
        self.assertEqual(sx.convert_path("//a//b/"), [("/a/b", False)])
        self.assertEqual(sx.convert_path(""), [("/", False)])

    def test_an_unknown_part_with_a_slash_in_its_text_is_one_segment(self):
        self.assertEqual(sx.convert_path('/a/<unknown:x("/y")>/b'), [('/a/<unknown:x("/y")>/b', True)])

    def test_pieces_make_a_path_that_is_unknown_as_a_whole_a_segment(self):
        value = sx.Str("<unknown:p()>", (("p()", "a.ts", 1),))
        ((piece, partial),) = sx.path_pieces(value)
        self.assertEqual((piece.value, partial), ("/<unknown:p()>", True))

    def test_pieces_of_a_plain_path(self):
        ((piece, partial),) = sx.path_pieces(sx.Str("/a/:id"))
        self.assertEqual((piece.value, partial), ("/a/{id}", False))


class BodyOpen(unittest.TestCase):
    def test_the_body_of_a_method_after_a_return_type_with_braces(self):
        src = src_of("class C { m(): { a: string } { return 1; } }")
        close = first(src, "m") + 1
        body = src.body_open(src.match(close) + 1)
        self.assertEqual(src.text(body, body + 1), "{ return")

    def test_no_body_for_an_overload(self):
        src = src_of("class C { m(): void; }")
        self.assertIsNone(src.body_open(src.match(first(src, "m") + 1) + 1))


if __name__ == "__main__":
    unittest.main()
