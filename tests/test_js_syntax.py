"""Imports, exports, declarations and spans, uses, guards and definitions, on tokens."""

import random
import unittest

from reachmap.adapters.js.syntax import parse


def decls(text):
    p = parse(text)
    return [(d.name, d.kind, d.start, d.end, d.exported) for d in p.declarations]


def imps(text):
    return [(r.kind, r.specifier, r.names, r.type_only, r.line) for r in parse(text).imports]


class ImportForms(unittest.TestCase):
    def test_every_static_form(self):
        text = "\n".join([
            'import def from "./a";',                       # 1
            'import { a, b as c } from "./b";',            # 2
            'import * as ns from "./c";',                  # 3
            'import "./side";',                            # 4
            'import type { T } from "./t";',               # 5
            'import d2, { e } from "./d";',                # 6
            'import d3, * as n3 from "./e";',              # 7
            'import { type U, v } from "./u";',            # 8
            'import { default as dd, "str-name" as sn } from "./f";',  # 9
        ])
        got = {r.specifier: r for r in parse(text).imports}
        self.assertEqual(got["./a"].names, None)
        self.assertEqual([(b.imported, b.local) for b in got["./a"].bindings], [("default", "def")])
        self.assertEqual(got["./b"].names, ("a", "b"))
        self.assertEqual([(b.imported, b.local) for b in got["./b"].bindings], [("a", "a"), ("b", "c")])
        self.assertEqual(got["./c"].names, None)
        self.assertEqual([(b.imported, b.local) for b in got["./c"].bindings], [("*", "ns")])
        self.assertEqual((got["./side"].names, got["./side"].bindings), (None, ()))
        self.assertTrue(got["./t"].type_only)
        self.assertEqual(got["./t"].names, ("T",))
        self.assertEqual(got["./d"].names, None)  # a default import is a whole-module import
        self.assertEqual([(b.imported, b.local) for b in got["./d"].bindings], [("default", "d2"), ("e", "e")])
        self.assertEqual([(b.imported, b.local) for b in got["./e"].bindings], [("default", "d3"), ("*", "n3")])
        self.assertEqual(got["./u"].names, ("U", "v"))
        self.assertEqual([(b.imported, b.type_only) for b in got["./u"].bindings], [("U", True), ("v", False)])
        self.assertEqual([(b.imported, b.local) for b in got["./f"].bindings], [("default", "dd"), ("str-name", "sn")])
        self.assertEqual(sorted(r.line for r in parse(text).imports), list(range(1, 10)))

    def test_default_import_named_type_and_from(self):
        self.assertEqual(parse('import type from "./x";').imports[0].bindings[0].local, "type")
        self.assertFalse(parse('import type from "./x";').imports[0].type_only)
        self.assertEqual(parse('import from from "./x";').imports[0].bindings[0].local, "from")

    def test_multi_line_import_cites_the_line_of_the_specifier(self):
        text = 'import {\n  A,\n  B as C,\n} from "@api/endpoints";\nconst x = 1;'
        (rec,) = parse(text).imports
        self.assertEqual((rec.names, rec.line, rec.start_line), (("A", "B"), 4, 1))

    def test_import_attributes_are_skipped(self):
        (rec,) = parse('import data from "./d.json" with { type: "json" };\nconst z = 1;').imports
        self.assertEqual(rec.specifier, "./d.json")
        self.assertEqual(decls('import data from "./d.json" with { type: "json" };\nconst z = 1;'), [("z", "variable", 2, 2, False)])

    def test_export_from_forms(self):
        text = 'export { a, b as c } from "./x";\nexport * from "./y";\nexport * as ns from "./z";\nexport type { T } from "./t";'
        recs = {r.specifier: r for r in parse(text).imports}
        self.assertEqual((recs["./x"].kind, recs["./x"].names), ("reexport", ("a", "b")))
        self.assertEqual((recs["./y"].kind, recs["./y"].names), ("export_star", None))
        self.assertEqual((recs["./z"].kind, recs["./z"].names), ("export_star", None))
        self.assertEqual((recs["./t"].kind, recs["./t"].type_only), ("reexport", True))
        p = parse(text)
        self.assertEqual((p.exports["c"].local, p.exports["c"].source), ("b", "./x"))
        self.assertEqual(p.exports["ns"].source, "./z")
        self.assertEqual(p.export_stars, ("./y",))

    def test_dynamic_import_and_require(self):
        text = 'const L = React.lazy(() => import("./Lazy"));\nconst r = require("./r");\nconst t = import(`./t`);\nfoo.import("./no");\nobj.require("./no");'
        got = [(r.kind, r.specifier, r.names) for r in parse(text).imports]
        self.assertEqual(got, [("dynamic", "./Lazy", None), ("require", "./r", None), ("dynamic", "./t", None)])

    def test_dynamic_import_with_an_expression_is_listed_not_resolved(self):
        p = parse('const m = import(`./pages/${name}`);\nconst n = import(name);')
        self.assertEqual(p.imports, ())
        self.assertEqual(p.unresolved_dynamic, (1, 2))

    def test_import_equals_require(self):
        p = parse('import fs = require("fs");\nimport A = B.C;')
        self.assertEqual([(r.kind, r.specifier) for r in p.imports], [("require", "fs")])

    def test_import_in_a_comment_or_string_is_not_an_import(self):
        text = '// import x from "./no"\n/* require("./no2") */\nconst s = \'import y from "./no3"\';\nconst t = `require("./no4")`;'
        self.assertEqual(parse(text).imports, ())

    def test_import_meta_is_not_an_import(self):
        self.assertEqual(parse("const u = import.meta.url;").imports, ())

    def test_type_import_position(self):
        self.assertEqual([r.specifier for r in parse('type X = typeof import("./m");').imports], ["./m"])


class Exports(unittest.TestCase):
    def test_declarations_are_exported_by_name(self):
        text = "export function f() {}\nexport class C {}\nexport const a = 1, b = 2;\nexport default function g() {}\nfunction h() {}"
        p = parse(text)
        self.assertEqual({d.name: d.export_names for d in p.declarations},
                         {"f": ("f",), "C": ("C",), "a": ("a",), "b": ("b",), "g": ("default",), "h": ()})
        self.assertEqual(sorted(p.exports), ["C", "a", "b", "default", "f"])

    def test_export_list_with_rename(self):
        text = "function a() {}\nfunction b() {}\nexport { a, b as c };"
        p = parse(text)
        self.assertEqual({d.name: d.export_names for d in p.declarations}, {"a": ("a",), "b": ("c",)})
        self.assertEqual((p.exports["c"].local, p.exports["c"].source), ("b", None))

    def test_default_export_of_an_identifier(self):
        p = parse("const X = 1;\nexport default X;")
        self.assertEqual([(d.name, d.export_names) for d in p.declarations], [("X", ("default",))])

    def test_default_export_of_an_expression_is_a_declaration(self):
        self.assertEqual(decls("export default connect(a)(B);"), [("default", "default", 1, 1, True)])
        self.assertEqual(decls("export default {\n  a: 1,\n};"), [("default", "default", 1, 3, True)])
        self.assertEqual(decls("export default () => {\n  return 1;\n};"), [("default", "default", 1, 3, True)])

    def test_export_as_default(self):
        p = parse("function a() {}\nexport { a as default };")
        self.assertEqual(p.declarations[0].export_names, ("default",))

    def test_exported_types_and_enums(self):
        text = "export interface I { a: number }\nexport type T = { a: number };\nexport enum E { A, B }\nexport const enum F { X }\nexport namespace N { export const z = 1; }\nexport declare function d(): void;\nexport abstract class K {}"
        self.assertEqual([(d.name, d.kind, d.exported) for d in parse(text).declarations],
                         [("I", "interface", True), ("T", "type", True), ("E", "enum", True), ("F", "enum", True),
                          ("N", "namespace", True), ("d", "function", True), ("K", "class", True)])


class DeclarationSpans(unittest.TestCase):
    def test_function_forms(self):
        text = "\n".join([
            "function a(x) {",            # 1
            "  return x;",                # 2
            "}",                          # 3
            "export async function b() {",  # 4
            "}",                          # 5
            "export default function () {",  # 6
            "}",                          # 7
            "function* g() {",            # 8
            "  yield 1;",
            "}",                          # 10
        ])
        self.assertEqual(decls(text), [("a", "function", 1, 3, False), ("b", "function", 4, 5, True),
                                       ("default", "function", 6, 7, True), ("g", "function", 8, 10, False)])

    def test_arrow_and_function_expression_constants(self):
        text = "\n".join([
            "const f = (x: number) => {",    # 1
            "  return x;",
            "};",                            # 3
            "export const g = function () {",  # 4
            "};",                            # 5
            "const h = async (a) => a + 1;",  # 6
            "const k = x =>",                # 7
            "  x * 2;",                      # 8
        ])
        self.assertEqual(decls(text), [("f", "function", 1, 3, False), ("g", "function", 4, 5, True),
                                       ("h", "function", 6, 6, False), ("k", "function", 7, 8, False)])

    def test_value_constants_over_several_lines(self):
        text = "\n".join([
            "export const A = 1;",                      # 1
            "export const SETTLEMENT_SUMMARY_URL =",    # 2
            '  "/api/billing/settlements/summary";',    # 3
            "export const OBJ = {",                     # 4
            "  a: 1,",
            "  b: [1, 2],",
            "};",                                       # 7
            "export const ARR = [",                     # 8
            "  1,",
            "];",                                       # 10
            "const S = `a",                             # 11
            "b ${c}`;",                                 # 12
        ])
        self.assertEqual([(d.name, d.start, d.end) for d in parse(text).declarations],
                         [("A", 1, 1), ("SETTLEMENT_SUMMARY_URL", 2, 3), ("OBJ", 4, 7), ("ARR", 8, 10), ("S", 11, 12)])

    def test_class_with_methods(self):
        text = "export class C extends B<T> implements I {\n  m() {\n    return 1;\n  }\n}\nconst after = 1;"
        self.assertEqual([(d.name, d.start, d.end) for d in parse(text).declarations], [("C", 1, 5), ("after", 6, 6)])

    def test_return_type_with_an_object_type_is_not_the_body(self):
        text = "function f(a: string): { x: number } {\n  return { x: 1 };\n}\nconst z = 1;"
        self.assertEqual([(d.name, d.start, d.end) for d in parse(text).declarations], [("f", 1, 3), ("z", 4, 4)])

    def test_allman_brace_and_overloads(self):
        text = "export function f(a: string): string;\nexport function f(a: number): number;\nexport function f(a: any)\n{\n  return a;\n}\nconst z = 1;"
        self.assertEqual([(d.name, d.start, d.end) for d in parse(text).declarations],
                         [("f", 1, 1), ("f", 2, 2), ("f", 3, 6), ("z", 7, 7)])

    def test_semicolon_free_style(self):
        text = "const a = 1\nconst b = {\n  c: 2\n}\nfunction f() {\n  return 1\n}\nconst d = f()\n  .toString()\nexport const e = 5"
        self.assertEqual([(d.name, d.start, d.end) for d in parse(text).declarations],
                         [("a", 1, 1), ("b", 2, 4), ("f", 5, 7), ("d", 8, 9), ("e", 10, 10)])

    def test_prettier_binary_operator_at_line_end_continues(self):
        text = "const ok = alpha >\n  beta &&\n  gamma;\nconst next = 1;"
        self.assertEqual([(d.name, d.start, d.end) for d in parse(text).declarations], [("ok", 1, 3), ("next", 4, 4)])

    def test_type_alias_with_multi_line_generic(self):
        text = "export type X = Foo<\n  A,\n  B\n>\n\nexport const y = 1;"
        self.assertEqual([(d.name, d.start, d.end) for d in parse(text).declarations], [("X", 1, 4), ("y", 6, 6)])

    def test_multiple_declarators_and_destructuring(self):
        text = "export const a = 1,\n  b = 2;\nconst { c, d: e, ...rest } = obj;\nconst [f, [g]] = arr;"
        self.assertEqual([(d.name, d.start, d.end) for d in parse(text).declarations],
                         [("a", 1, 1), ("b", 2, 2), ("c", 3, 3), ("e", 3, 3), ("rest", 3, 3), ("f", 4, 4), ("g", 4, 4)])

    def test_typed_declarators_with_generics_in_the_annotation(self):
        text = "const m: Map<string, number> = new Map(), n = 1;"
        self.assertEqual([d.name for d in parse(text).declarations], ["m", "n"])

    def test_decorators_belong_to_the_statement(self):
        text = "@Component({\n  selector: 'x',\n})\nexport class C {\n}\nconst z = 1;"
        self.assertEqual([(d.name, d.start, d.end) for d in parse(text).declarations], [("C", 1, 5), ("z", 6, 6)])

    def test_jsx_component_span(self):
        text = "export function A() {\n  return (\n    <ul>\n      {rows.map((r) => (\n        <li key={r.id}>{r.label}</li>\n      ))}\n    </ul>\n  );\n}\nexport const B = 1;"
        self.assertEqual([(d.name, d.start, d.end) for d in parse(text).declarations], [("A", 1, 9), ("B", 10, 10)])

    def test_template_literal_with_braces_does_not_break_spans(self):
        text = "function f() {\n  return `}${ { a: 1 }.a }{`;\n}\nconst z = 1;"
        self.assertEqual([(d.name, d.start, d.end) for d in parse(text).declarations], [("f", 1, 3), ("z", 4, 4)])

    def test_comments_with_braces_do_not_break_spans(self):
        text = "function f() {\n  // }\n  /* } */\n  return 1;\n}\nconst z = 1;"
        self.assertEqual([(d.name, d.start, d.end) for d in parse(text).declarations], [("f", 1, 5), ("z", 6, 6)])

    def test_regex_with_braces_does_not_break_spans(self):
        text = "function f(s) {\n  return /}{/.test(s);\n}\nconst z = 1;"
        self.assertEqual([(d.name, d.start, d.end) for d in parse(text).declarations], [("f", 1, 3), ("z", 4, 4)])

    def test_module_level_statements_have_no_declaration(self):
        text = 'import a from "./a";\nif (a) {\n  b();\n}\nfoo();\nconst z = 1;'
        p = parse(text)
        self.assertEqual([(d.name, d.start) for d in p.declarations], [("z", 6)])
        self.assertEqual(p.placed(3), [])
        self.assertEqual([d.name for d in p.placed(6)], ["z"])

    def test_declare_global_and_module(self):
        text = 'declare global {\n  interface Window { a: 1 }\n}\ndeclare module "x" {\n  export const q: number;\n}'
        self.assertEqual([(d.name, d.kind, d.start, d.end) for d in parse(text).declarations],
                         [("global", "module", 1, 3), ("x", "module", 4, 6)])

    def test_garbage_does_not_raise(self):
        for text in ("}}}}", "((((", "export", "export default", "const", "function", "class {", "import", "import {", "@", "a ? b : c ?? ;;", "`${"):
            parse(text)


class Placement(unittest.TestCase):
    def test_placed_by_line_not_by_text(self):
        text = "function a() {\n  return 1;\n}\nfunction b() {\n  return 1;\n}\n"
        p = parse(text)
        self.assertEqual([d.name for d in p.placed(2)], ["a"])
        self.assertEqual([d.name for d in p.placed(5)], ["b"])
        self.assertEqual(p.placed(7), [])

    def test_code_lines_ignore_comments_and_blanks(self):
        p = parse("// c\n\nconst a = 1; // x\n/* b\n c */\n")
        self.assertEqual(sorted(p.code_lines), [3])

    def test_multi_line_template_lines_are_code(self):
        p = parse("const a = `x\ny\nz`;")
        self.assertEqual(sorted(p.code_lines), [1, 2, 3])


class Nested(unittest.TestCase):
    TEXT = "\n".join([
        "export function outer() {",            # 1
        "  function inner() {",                  # 2
        "    return 1;",                         # 3
        "  }",                                   # 4
        "  const arrow = (x) => {",              # 5
        "    return x;",                         # 6
        "  };",                                  # 7
        "  return inner() + arrow(2);",          # 8
        "}",                                     # 9
        "class K {",                             # 10
        "  method() {",                          # 11
        "    return 1;",                         # 12
        "  }",                                   # 13
        "  static async other() {",              # 14
        "    return 2;",                         # 15
        "  }",                                   # 16
        "  field = () => {",                     # 17
        "    return 3;",                         # 18
        "  };",                                  # 19
        "}",                                     # 20
        "const o = {",                           # 21
        "  m() {",                               # 22
        "    return 1;",                         # 23
        "  },",                                  # 24
        "  p: () => {",                          # 25
        "    return 2;",                         # 26
        "  },",                                  # 27
        "};",                                    # 28
    ])

    def chain(self, name, line):
        p = parse(self.TEXT)
        decl = next(d for d in p.declarations if d.name == name)
        return p.nested(decl, line)

    def test_nested_function_declaration(self):
        self.assertEqual(self.chain("outer", 3), ("inner",))

    def test_nested_arrow_constant(self):
        self.assertEqual(self.chain("outer", 6), ("arrow",))

    def test_line_in_the_outer_body_has_no_nested_function(self):
        self.assertEqual(self.chain("outer", 8), ())

    def test_class_methods_and_properties(self):
        self.assertEqual(self.chain("K", 12), ("method",))
        self.assertEqual(self.chain("K", 15), ("other",))
        self.assertEqual(self.chain("K", 18), ("field",))

    def test_object_literal_members(self):
        self.assertEqual(self.chain("o", 23), ("m",))
        self.assertEqual(self.chain("o", 26), ("p",))

    def test_doubly_nested_outermost_first(self):
        text = "function a() {\n  function b() {\n    function c() {\n      return 1;\n    }\n  }\n}"
        p = parse(text)
        self.assertEqual(p.nested(p.declarations[0], 4), ("b", "c"))

    def test_sibling_functions_with_the_same_inner_name(self):
        text = "function get_a() {\n  function _load() {\n    return 1;\n  }\n}\nfunction get_b() {\n  function _load() {\n    return 2;\n  }\n}"
        p = parse(text)
        self.assertEqual([(d.name, p.nested(d, n)) for d, n in ((p.declarations[0], 3), (p.declarations[1], 8))],
                         [("get_a", ("_load",)), ("get_b", ("_load",))])

    def test_jsx_expressions_are_not_methods(self):
        text = 'export function A() {\n  return (\n    <li>\n      {truncate(a.number, 12)} {formatDate(a.at)}{" "}\n      {fmt(b)} <b />\n    </li>\n  );\n}'
        p = parse(text)
        for line in (4, 5):
            self.assertEqual(p.nested(p.declarations[0], line), ())

    def test_a_method_with_a_return_type(self):
        text = "class K {\n  m(a: number): { x: number } {\n    return { x: a };\n  }\n}"
        p = parse(text)
        self.assertEqual(p.nested(p.declarations[0], 3), ("m",))

    def test_calls_are_not_methods(self):
        text = "function f() {\n  if (a) {\n    call(b);\n  }\n  foo(x).then(() => {\n    y();\n  });\n}"
        p = parse(text)
        self.assertEqual(p.nested(p.declarations[0], 3), ())
        self.assertEqual(p.nested(p.declarations[0], 6), ())


class Uses(unittest.TestCase):
    TEXT = "\n".join([
        'import { fmt as f, other } from "./m";',   # 1
        'import * as ns from "./n";',               # 2
        "export function Page() {",                 # 3
        "  const a = f(1, \"legacy\");",            # 4
        "  const b = ns.fmt(x, ...rest);",          # 5
        "  return <Wrapper render={f} />;",         # 6
        "}",                                        # 7
        "const top = new f();",                     # 8
        "const obj = { fmt: 1 };",                  # 9
        "other.f(1);",                              # 10
        "// f(2)",                                  # 11
        'const s = "f(3)";',                        # 12
        "x.f(4);",                                  # 13
    ])

    def test_calls_and_references_with_the_enclosing_declaration(self):
        p = parse(self.TEXT)
        got = [(u.line, u.form, u.enclosing) for u in p.find_uses("f")]
        self.assertEqual(got, [(4, "call", "Page"), (6, "reference", "Page"), (8, "call", "top")])

    def test_shadowing_is_not_understood_so_a_parameter_counts(self):
        # Conservative: every use of the name counts, a shadowing parameter included.
        got = [(u.line, u.form) for u in parse("function g(f) { return f(5); }").find_uses("f")]
        self.assertEqual(got, [(1, "reference"), (1, "call")])

    def test_a_declaration_name_is_not_a_use(self):
        self.assertEqual(parse("function f() {}\nclass f {}\nconst f = 1;").find_uses("f"), [])

    def test_arguments_are_source_text_and_literals_are_decoded(self):
        u = parse(self.TEXT).find_uses("f")[0]
        self.assertEqual(u.args, ("1", '"legacy"'))
        self.assertEqual(u.literals, (None, "legacy"))

    def test_namespace_member_calls(self):
        uses = parse(self.TEXT).find_uses("ns", member="fmt")
        self.assertEqual([(u.line, u.form, u.args) for u in uses], [(5, "call", ("x", "...rest"))])

    def test_namespace_use_without_the_member_is_a_reference_when_asked(self):
        p = parse("import * as ns from './n';\nconst k = Object.keys(ns);\nns.other();\nns.fmt();")
        self.assertEqual([(u.line, u.form) for u in p.find_uses("ns", member="fmt", whole_references=True)], [(2, "reference"), (4, "call")])

    def test_generic_call(self):
        u = parse("const a = f<string, number>(1);\n").find_uses("f")
        self.assertEqual([(x.form, x.args) for x in u], [("call", ("1",))])

    def test_uses_in_import_statements_do_not_count(self):
        self.assertEqual(parse('import { a } from "./a";\nexport { a } from "./a";').find_uses("a"), [])

    def test_local_export_of_a_binding_is_a_reference(self):
        self.assertEqual([(u.line, u.form) for u in parse('import { a } from "./a";\nexport { a };').find_uses("a")], [(2, "reference")])

    def test_property_key_is_not_a_use(self):
        self.assertEqual(parse("const o = { f: 1 };").find_uses("f"), [])
        self.assertEqual([u.form for u in parse("const o = { f };").find_uses("f")], ["reference"])

    def test_multi_line_call_arguments(self):
        text = "x(\n  a,\n  { k: [1, 2] },\n  fn(b, c),\n);"
        (u,) = parse(text).find_uses("x")
        self.assertEqual(u.args, ("a", "{ k: [1, 2] }", "fn(b, c)"))
        self.assertEqual(u.line, 1)

    def test_default_import_binding(self):
        p = parse('import Page from "./Page";\nconst r = <Page />;\nconst q = Page;')
        self.assertEqual([(u.line, u.form) for u in p.find_uses("Page")], [(2, "reference"), (3, "reference")])


class Guards(unittest.TestCase):
    MONEY = "\n".join([
        "// Money formatting.",                                                          # 1
        'export function formatAmount(value: number, mode: "legacy" | "modern"): string {',  # 2
        '  if (mode === "legacy") {',                                                    # 3
        '    return value.toFixed(2).replace(".", ",");',                                # 4
        "  }",                                                                           # 5
        '  return new Intl.NumberFormat("en-GB").format(value);',                        # 6
        "}",                                                                             # 7
    ])

    def info(self, text, name):
        p = parse(text)
        d = next(x for x in p.declarations if x.name == name)
        return p, d, p.function_info(d)

    def test_parameters(self):
        _, _, fi = self.info(self.MONEY, "formatAmount")
        self.assertEqual([(a.name, a.index, a.has_default, a.rest, a.pattern) for a in fi.params],
                         [("value", 0, False, False, False), ("mode", 1, False, False, False)])

    def test_parameter_kinds(self):
        _, _, fi = self.info("function f(this: X, a = 1, ...r: number[], { b, c }: P, [d]: Q, e?: string, m: Map<string, number>) {}", "f")
        self.assertEqual([(a.name, a.has_default, a.rest, a.pattern) for a in fi.params],
                         [("a", True, False, False), ("r", False, True, False), (None, False, False, True),
                          (None, False, False, True), ("e", False, False, False), ("m", False, False, False)])
        self.assertEqual([a.index for a in fi.params], [0, 1, 2, 3, 4, 5])

    def test_arrow_and_expression_functions(self):
        _, _, fi = self.info("const f = async (a: number, b = 2) => {\n  return a;\n};", "f")
        self.assertEqual([a.name for a in fi.params], ["a", "b"])
        _, _, fi = self.info("const g = x => x;", "g")
        self.assertEqual([a.name for a in fi.params], ["x"])
        _, _, fi = self.info("const h = function named(p) {\n};", "h")
        self.assertEqual([a.name for a in fi.params], ["p"])

    def test_if_block_guard(self):
        p, d, _ = self.info(self.MONEY, "formatAmount")
        (g,) = p.guard_blocks(d)
        self.assertEqual((g.param, g.literals, g.enter_if_equal, g.guard_line, g.first_line, g.last_line), ("mode", ("legacy",), True, 3, 4, 4))
        self.assertTrue(g.permits("legacy"))
        self.assertFalse(g.permits("modern"))

    def test_not_equal_and_loose_equal_and_flipped(self):
        text = 'function f(m) {\n  if (m !== "a") {\n    x();\n  }\n  if ("b" == m) {\n    y();\n  }\n  if (m != "c") {\n    z();\n  }\n}'
        p, d, _ = self.info(text, "f")
        got = [(g.literals, g.enter_if_equal, g.guard_line) for g in p.guard_blocks(d)]
        self.assertEqual(got, [(("a",), False, 2), (("b",), True, 5), (("c",), False, 8)])
        a, b, c = p.guard_blocks(d)
        self.assertEqual((a.permits("a"), a.permits("z")), (False, True))

    def test_else_is_the_complement(self):
        text = 'function f(m) {\n  if (m === "a") {\n    x();\n  } else {\n    y();\n  }\n}'
        p, d, _ = self.info(text, "f")
        blocks = p.guard_blocks(d)
        self.assertEqual([(g.enter_if_equal, g.first_line, g.last_line, g.guard_line) for g in blocks], [(True, 3, 3, 2), (False, 5, 5, 2)])

    def test_else_if_chain_collects_every_necessary_condition(self):
        text = 'function f(m) {\n  if (m === "a") {\n    x();\n  } else if (m === "b") {\n    y();\n  } else {\n    z();\n  }\n}'
        p, d, _ = self.info(text, "f")
        by_line = {}
        for g in p.guard_blocks(d):
            by_line.setdefault(g.first_line, []).append((g.literals, g.enter_if_equal))
        self.assertEqual(by_line[3], [(("a",), True)])
        self.assertEqual(sorted(by_line[5]), sorted([(("a",), False), (("b",), True)]))
        self.assertEqual(sorted(by_line[7]), sorted([(("a",), False), (("b",), False)]))

    def test_unrecognised_conditions_make_no_block(self):
        text = 'function f(m, n) {\n  if (m === "a" && n) {\n    x();\n  }\n  if (m === n) {\n    y();\n  }\n  if (m) {\n    z();\n  }\n  if (m === 1) {\n    w();\n  }\n}'
        p, d, _ = self.info(text, "f")
        self.assertEqual(p.guard_blocks(d), ())

    def test_single_statement_body_on_a_later_line(self):
        text = 'function f(m) {\n  if (m === "a")\n    x();\n  y();\n}'
        p, d, _ = self.info(text, "f")
        (g,) = p.guard_blocks(d)
        self.assertEqual((g.first_line, g.last_line), (3, 3))

    def test_body_on_the_guard_line_has_no_inside_lines(self):
        text = 'function f(m) {\n  if (m === "a") { x(); }\n}'
        p, d, _ = self.info(text, "f")
        (g,) = p.guard_blocks(d)
        self.assertGreater(g.first_line, g.last_line)  # an empty range: no line is inside

    def test_switch_cases(self):
        text = "\n".join([
            "function f(k) {",              # 1
            "  switch (k) {",               # 2
            '    case "a":',                # 3
            "      x();",                   # 4
            "      break;",                 # 5
            '    case "b":',                # 6
            '    case "c":',                # 7
            "      y();",                   # 8
            "      return 1;",              # 9
            '    case "d":',                # 10
            "      z();",                   # 11
            '    case "e":',                # 12
            "      w();",                   # 13
            "      break;",                 # 14
            "    default:",                 # 15
            "      v();",                   # 16
            "  }",                          # 17
            "}",
        ])
        p, d, _ = self.info(text, "f")
        got = {g.first_line: (g.literals, g.guard_line) for g in p.guard_blocks(d)}
        self.assertEqual(got[4], (("a",), 3))
        self.assertEqual(got[8], (("b", "c"), 7))   # b falls into c (empty body)
        self.assertEqual(got[11], (("d",), 10))
        self.assertEqual(got[13], (("d", "e"), 12))  # d has no break: it falls into e
        self.assertNotIn(16, got)                    # default is not a literal case

    def test_switch_with_a_non_literal_case_in_the_fall_through_is_not_a_guard(self):
        text = 'function f(k) {\n  switch (k) {\n    case X:\n    case "a":\n      y();\n      break;\n  }\n}'
        p, d, _ = self.info(text, "f")
        self.assertEqual(p.guard_blocks(d), ())

    def test_reassigned_parameters(self):
        text = 'function f(m, n) {\n  m = "x";\n  n += 1;\n  k++;\n}'
        _, _, fi = self.info(text, "f")
        self.assertEqual(fi.rebound, frozenset({"m", "n"}))

    def test_nested_ifs_are_found(self):
        text = 'function f(m) {\n  if (a) {\n    if (m === "z") {\n      x();\n    }\n  }\n}'
        p, d, _ = self.info(text, "f")
        self.assertEqual([(g.first_line, g.last_line) for g in p.guard_blocks(d)], [(4, 4)])

    def test_if_in_a_comment_or_string_is_not_a_guard(self):
        text = 'function f(m) {\n  // if (m === "a") {\n  const s = \'if (m === "b") { x }\';\n  return 1;\n}'
        p, d, _ = self.info(text, "f")
        self.assertEqual(p.guard_blocks(d), ())


class Definitions(unittest.TestCase):
    def lines(self, text, name):
        return parse(text).definition_lines(name)

    def test_declaration_forms_count(self):
        text = "\n".join([
            "function a() {}",                    # 1
            "class a {}",                         # 2
            "const a = 1;",                       # 3
            "let { a } = o;",                     # 4
            "var [a] = o;",                       # 5
            "interface a {}",                     # 6
            "type a = number;",                   # 7
            "enum a { X }",                       # 8
            "class K { a() {} }",                 # 9
            "const o = { a() {} };",              # 10
            "function f() { const a = 2; }",      # 11
            "export { x as a };",                 # 12
        ])
        self.assertEqual(self.lines(text, "a"), list(range(1, 13)))

    def test_references_imports_and_property_assignments_do_not_count(self):
        text = "\n".join([
            'import { a } from "./a";',     # import
            "a();",                         # call
            "const o = { a: 1 };",          # property
            "o.a = 2;",                     # member assignment
            "const b = a;",                 # reference
            "export { a } from './x';",     # re-export
            "// function a() {}",           # comment
            'const s = "function a() {}";',  # string
            "function g(a) {}",             # parameter
        ])
        self.assertEqual(self.lines(text, "a"), [])

    def test_mapStateToProps_in_a_connected_component(self):
        text = "const mapStateToProps = (state) => ({\n  rows: state.rows,\n});\nexport default connect(mapStateToProps)(View);"
        self.assertEqual(self.lines(text, "mapStateToProps"), [1])


if __name__ == "__main__":
    unittest.main()


class RealWorldShapes(unittest.TestCase):
    """Shapes seen in real TypeScript and React code."""

    def spans(self, text):
        return [(d.name, d.kind, d.start, d.end) for d in parse(text).declarations]

    def test_react_class_component_with_generics_and_a_default_export(self):
        text = "export default class Box extends React.Component<Props, State> {\n  state = { a: 1 };\n  render() {\n    return <div />;\n  }\n}\nexport const z = 1;"
        self.assertEqual(self.spans(text), [("Box", "class", 1, 6), ("z", "variable", 7, 7)])

    def test_anonymous_default_class(self):
        self.assertEqual(self.spans("export default class extends Base {\n}\nconst z = 1;"), [("default", "class", 1, 2), ("z", "variable", 3, 3)])

    def test_tsx_generic_arrow(self):
        text = "export const id = <T,>(a: T) => a;\nexport const wrap = <T extends object>(a: T): T => {\n  return a;\n};"
        self.assertEqual(self.spans(text), [("id", "function", 1, 1), ("wrap", "function", 2, 4)])

    def test_async_generator_and_overloads_in_a_class(self):
        text = "export async function* gen() {\n  yield 1;\n}\nabstract class A {\n  abstract m(): void;\n  f(a: string): void;\n  f(a: number): void;\n  f(a: any) {}\n}\nconst z = 1;"
        self.assertEqual(self.spans(text), [("gen", "function", 1, 3), ("A", "class", 4, 9), ("z", "variable", 10, 10)])

    def test_enum_with_initializers(self):
        self.assertEqual(self.spans("export enum E {\n  A = `${1}`.length,\n  B = (() => { return 2; })(),\n}\nconst z = 1;"), [("E", "enum", 1, 4), ("z", "variable", 5, 5)])

    def test_arrow_returning_an_object_literal(self):
        self.assertEqual(self.spans("const mapState = (s) => ({\n  rows: s.rows,\n});\nconst z = 1;"), [("mapState", "function", 1, 3), ("z", "variable", 4, 4)])

    def test_regex_and_division_edge_cases(self):
        text = "const a = x => /a}/.test(x);\nconst b = i++ / 2;\nconst c = (1) / 2 / 3;\nconst d = 1;"
        self.assertEqual([n for n, *_ in self.spans(text)], ["a", "b", "c", "d"])

    def test_contextual_keywords_as_identifiers(self):
        text = "const type = 1;\nconst of = 2;\nconst get = 3;\nlet set;\ntype Foo = Bar;\nconst from = 4;"
        self.assertEqual([n for n, *_ in self.spans(text)], ["type", "of", "get", "set", "Foo", "from"])

    def test_computed_members_getters_and_setters(self):
        text = "class K {\n  get a() {\n    return 1;\n  }\n  set a(v) {\n  }\n  *[Symbol.iterator]() {\n  }\n  static async s() {\n    return 1;\n  }\n}\nconst z = 1;"
        p = parse(text)
        self.assertEqual(self.spans(text), [("K", "class", 1, 12), ("z", "variable", 13, 13)])
        self.assertEqual(p.nested(p.declarations[0], 3), ("a",))
        self.assertEqual(p.nested(p.declarations[0], 10), ("s",))

    def test_namespace_and_export_equals_and_ambient_module(self):
        text = "export namespace A.B {\n  export const z = 1;\n}\nexport = foo;\ndeclare module \"x\";\nconst y = 1;"
        self.assertEqual([n for n, *_ in self.spans(text)], ["A", "x", "y"])

    def test_leading_semicolon_style(self):
        text = "const a = 1\n;[1, 2].forEach((n) => n)\nconst b = 2"
        self.assertEqual(self.spans(text), [("a", "variable", 1, 1), ("b", "variable", 3, 3)])

    def test_object_with_getters_and_async_members(self):
        text = "export const api = {\n  get a() {\n    return 1;\n  },\n  async b() {\n    return 2;\n  },\n  [k]: 1,\n};\nconst z = 1;"
        p = parse(text)
        self.assertEqual(self.spans(text), [("api", "variable", 1, 9), ("z", "variable", 10, 10)])
        self.assertEqual(p.nested(p.declarations[0], 3), ("a",))
        self.assertEqual(p.nested(p.declarations[0], 6), ("b",))

    def test_default_parameter_with_an_arrow_and_a_destructured_default(self):
        text = "export function f(cb = (x) => x, { a = 1, b } = {}) {\n  return cb(a);\n}\nconst z = 1;"
        p = parse(text)
        self.assertEqual(self.spans(text), [("f", "function", 1, 3), ("z", "variable", 4, 4)])
        self.assertEqual([(a.name, a.has_default, a.pattern) for a in p.function_info(p.declarations[0]).params], [("cb", True, False), (None, True, True)])

    def test_jsx_with_conditional_rendering_and_string_attributes(self):
        text = 'export function A({ ok }) {\n  return ok ? <a href="https://x.test/a//b">it\'s {ok && <b>yes</b>}</a> : null;\n}\nexport const z = 1;'
        self.assertEqual(self.spans(text), [("A", "function", 1, 3), ("z", "variable", 4, 4)])

    def test_import_type_and_inline_type_modifiers(self):
        (rec,) = parse('import { type A, type B as C, d } from "./m";').imports
        self.assertEqual([(b.imported, b.local, b.type_only) for b in rec.bindings], [("A", "A", True), ("B", "C", True), ("d", "d", False)])

    def test_a_long_arrow_chain_and_curried_function(self):
        text = "export const curried = (a) => (b) => (c) => {\n  return a + b + c;\n};\nconst z = 1;"
        self.assertEqual(self.spans(text), [("curried", "function", 1, 3), ("z", "variable", 4, 4)])

    def test_if_else_at_the_top_level_does_not_swallow_the_next_declaration(self):
        text = "if (a) {\n  b();\n} else {\n  c();\n}\nexport function f() {}"
        self.assertEqual(self.spans(text), [("f", "function", 6, 6)])

    def test_try_catch_finally_and_labels(self):
        text = "try {\n  a();\n} catch (e) {\n  b();\n} finally {\n  c();\n}\nouter: for (;;) {\n  break outer;\n}\nconst z = 1;"
        self.assertEqual(self.spans(text), [("z", "variable", 11, 11)])


class Robustness(unittest.TestCase):
    """A file cut in the middle of a statement, or plain noise, must never crash or hang the parser."""

    ATOMS = ["function", "class", "const", "let", "export", "default", "import", "from", "as", "{", "}", "(", ")", "[", "]", ";", ",", "=",
             "=>", "`", "${", "'", '"', "/", "//", "/*", "*/", "<", ">", "<T>", "?", ":", "...", "@", "if", "else", "switch", "case", "async",
             "await", "new", "type", "interface", "enum", "get", "set", "static", "*", "+", "a", "b", "c1", "\n", "\n", "  "]

    def exercise(self, text):
        p = parse(text)
        for d in p.declarations:
            p.nested(d, d.start)
            p.function_info(d)
            p.guard_blocks(d)
        p.find_uses("a")
        p.find_uses("a", member="b", whole_references=True)
        p.definition_lines("a")
        p.placed(1)

    def test_a_file_cut_anywhere_still_parses(self):
        source = "\n".join([
            'import { a, b as c } from "./m";', "export default class K<T> extends B<T> {", "  m = (x: number): { y: number } => ({ y: x });",
            "  async n() {", "    switch (a) {", '      case "k": return `${a}${c}`;', "    }", "  }", "}",
            "export const f = (mode: string) => {", '  if (mode === "x") {', "    return /re}/.test(mode) ? <b>{mode}</b> : null;", "  }", "};",
        ])
        for cut in range(0, len(source), 3):
            self.exercise(source[:cut])
            self.exercise(source[cut:])

    def test_random_token_soup_terminates(self):
        rng = random.Random(20260101)
        for _ in range(400):
            self.exercise(" ".join(rng.choice(self.ATOMS) for _ in range(rng.randrange(1, 70))))

    def test_unclosed_template_substitutions_do_not_loop(self):
        for text in ("`${", "`${ `${ `${", "a `${ b } c ${ d", "`${ } ${ } ${", "x = `${ { `${"):
            self.exercise(text)


class ReachInsideTheFile(unittest.TestCase):
    def test_declaration_references_are_code_identifiers_only(self):
        p = parse("function a() {\n  b(); // c()\n  const s = 'd()';\n  return e.f + `${g}`;\n}\n")
        self.assertEqual(p.decl_references(p.declarations[0]), frozenset({"function", "a", "b", "const", "s", "return", "e", "g"}))

    def test_module_references_leave_out_declarations_imports_and_export_lists(self):
        text = 'import { i } from "./i";\nfunction a() { return used1; }\nexport { a as b };\nconst c = used2;\nregister(used3);\nexport default c;\n'
        self.assertEqual(parse(text).module_references(), frozenset({"register", "used3"}))

    def test_dynamic_access(self):
        for text in ("eval('x')", "new Function('a')", "with (o) { a; }", "const v = this[k];", "window[n]()", "globalThis['a']"):
            self.assertTrue(parse(text).uses_dynamic_access(), text)
        for text in ("a.eval(x)", "const evaluate = 1;", "const t = this.k;", "const v = obj[k];", "Function.prototype.call"):
            self.assertFalse(parse(text).uses_dynamic_access(), text)

    def test_unbalanced_and_mismatched_brackets_are_problems(self):
        for text in ("a ) b", "( ]", "{ ) }", "f(", "if (ok) /[{]/.test(s);"):
            self.assertTrue(parse(text).problems, text)
        for text in ("f(a[0], {b: `${c}`})", "const a = () => { return [1]; };"):
            self.assertEqual(parse(text).problems, [], text)


class TypeArgumentsAndDepth(unittest.TestCase):
    """Review F13 and F14."""

    def test_satisfies_and_as_with_type_arguments_do_not_split_a_declarator(self):
        text = "\n".join([
            "export const cfg = { a: 1 } satisfies Record<string, number>;",
            "const a = x as Map<string, number>, b = 2;",
            "const c = y as unknown as Foo<A, B>[], d = 3;",
            "const e = z satisfies { k: number; m: string } | Other<P, Q>, f = 4;",
        ])
        p = parse(text)
        self.assertEqual([d.name for d in p.declarations], ["cfg", "a", "b", "c", "d", "e", "f"])
        self.assertEqual(p.definition_lines("number"), [])
        self.assertEqual(p.definition_lines("B"), [])
        self.assertEqual([d.name for d in p.declarations if d.exported], ["cfg"])

    def test_a_comparison_comma_is_still_a_declarator_separator(self):
        self.assertEqual([d.name for d in parse("const a = 1, b = 2, c = a < b;").declarations], ["a", "b", "c"])

    def test_deeply_nested_blocks_in_a_switch_case_do_not_recurse(self):
        depth = 3000
        text = 'function f(k) {\n  switch (k) {\n    case "a":\n' + "{" * depth + "x();" + "}" * depth + '\n    case "b":\n      return 1;\n  }\n}\n'
        p = parse(text)
        p.guard_blocks(p.declarations[0])  # no RecursionError

    def test_deeply_nested_everything_does_not_recurse(self):
        depth = 4000
        for text in ("f(" * depth + ")" * depth, "[" * depth + "]" * depth, "const a = " + "{ b: " * depth + "1" + " }" * depth + ";",
                     "const { " + "a: { " * 50 + "z" + " }" * 50 + " } = o;", "const f = " + "() => " * depth + "1;", "`${" * 500 + "}`" * 500):
            p = parse(text)
            for d in p.declarations:
                p.nested(d, d.start)
                p.function_info(d)
                p.guard_blocks(d)
