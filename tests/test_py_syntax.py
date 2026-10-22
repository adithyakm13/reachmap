"""Python syntax facts from `ast`: declarations and spans, placement of a line (nested scopes, class
members, decorators), imports in every form, references, uses, definitions.

Nothing here reads git; every case is a small piece of source text. Trap numbers refer to
docs/CONTEXT.md section 5.
"""

import sys
import textwrap
import unittest

from reachmap.adapters.python.syntax import parse


def src(text):
    return textwrap.dedent(text).lstrip("\n")


def names(parsed):
    return [(d.name, d.kind, d.start, d.end) for d in parsed.declarations]


def place(parsed, line):
    p = parsed.placement(line)
    return None if p is None else (p.name, p.nested, p.kind)


class Declarations(unittest.TestCase):
    def test_def_async_def_and_class_have_spans(self):
        p = parse(src('''
            def a():
                return 1


            async def b(x):
                return x


            class C:
                x = 1
        '''))
        self.assertEqual(p.problems, ())
        self.assertEqual(names(p), [("a", "function", 1, 2), ("b", "function", 5, 6), ("C", "class", 9, 10)])

    def test_a_decorator_belongs_to_the_function_below_it(self):
        p = parse(src('''
            def above():
                pass


            @register
            @other(1)
            def below():
                pass
        '''))
        self.assertEqual(names(p), [("above", "function", 1, 2), ("below", "function", 5, 8)])
        self.assertEqual(place(p, 5), ("below", (), "function"))
        self.assertEqual(place(p, 6), ("below", (), "function"))

    def test_a_multi_line_decorator_at_column_zero_is_the_function_below(self):
        """s09: the argument line of a decorator spanning five lines is not the function above it (trap: a
        decorator at column 0 was outside the function the walker attributed lines to)."""
        p = parse(src('''
            def _log(message):
                print(message)


            @scheduled(
                name="nightly",
                cron="0 2 * * *",
                retries=3,
            )
            def run_nightly():
                _log("go")
        '''))
        self.assertEqual(names(p), [("_log", "function", 1, 2), ("run_nightly", "function", 5, 11)])
        for line in (5, 6, 7, 8, 9, 10):
            self.assertEqual(place(p, line)[0], "run_nightly", line)
        self.assertEqual(place(p, 2)[0], "_log")

    def test_a_class_decorator_and_a_multi_line_signature_belong_to_the_declaration(self):
        p = parse(src('''
            @dataclass(
                frozen=True,
            )
            class Point:
                x: int


            def long(
                a,
                b=2,
            ):
                return a
        '''))
        self.assertEqual(names(p), [("Point", "class", 1, 5), ("long", "function", 8, 12)])
        self.assertEqual(place(p, 2)[0], "Point")
        self.assertEqual(place(p, 10)[0], "long")

    def test_module_level_assignments_are_declarations(self):
        p = parse(src('''
            RATES = {
                "EUR": 1.0,
                "USD": 1.08,
            }
            interval: int = 5
            count: int
            a = b = 3
            x, (y, *z) = 1, (2, 3)
            handler = lambda v: v + 1
            log = logging.getLogger(__name__)
            app.state.x = 4
            registry["k"] = 5
            total += 1
        '''))
        self.assertEqual(
            [(d.names, d.kind, d.start, d.end) for d in p.declarations],
            [
                (("RATES",), "variable", 1, 4), (("interval",), "variable", 5, 5), (("count",), "variable", 6, 6),
                (("a", "b"), "variable", 7, 7), (("x", "y", "z"), "variable", 8, 8), (("handler",), "function", 9, 9),
                (("log",), "variable", 10, 10), (("total",), "variable", 13, 13),
            ],
        )
        self.assertEqual(place(p, 3), ("RATES", (), "variable"))
        self.assertIsNone(place(p, 11))  # an attribute store is module code, not a declaration
        self.assertIsNone(place(p, 12))
        self.assertIn(11, p.code_lines)
        self.assertIn(12, p.code_lines)

    def test_declarations_inside_module_level_if_and_try_are_top_level(self):
        p = parse(src('''
            import sys

            if sys.platform == "win32":
                def helper():
                    return 1
            else:
                def helper():
                    return 2

            try:
                import json
            except ImportError:
                json = None
        '''))
        self.assertEqual(names(p), [("helper", "function", 4, 5), ("helper", "function", 7, 8), ("json", "variable", 13, 13)])
        self.assertIsNone(place(p, 3))   # the `if` line is module code
        self.assertIn(3, p.code_lines)
        self.assertIn(6, p.code_lines)   # `else:`
        self.assertEqual(place(p, 8), ("helper", (), "function"))

    def test_lines_outside_every_declaration(self):
        p = parse(src('''
            """Module docstring,
            two lines."""
            import os
            # a comment

            def f():
                return os


            # a comment between declarations
            print("hello")
        '''))
        self.assertNotIn(1, p.code_lines)    # a docstring is not code
        self.assertNotIn(2, p.code_lines)
        self.assertIn(3, p.code_lines)       # an import is
        self.assertNotIn(4, p.code_lines)    # a comment is not
        self.assertNotIn(5, p.code_lines)    # blank
        self.assertNotIn(10, p.code_lines)
        self.assertIn(11, p.code_lines)
        self.assertIsNone(p.placement(3))

    def test_a_multi_line_string_and_call_are_code_on_every_line(self):
        p = parse(src('''
            TEXT = """one
            two
            three"""
            setup(
                name="x",
            )
        '''))
        self.assertEqual(names(p), [("TEXT", "variable", 1, 3)])
        self.assertTrue({4, 5, 6} <= p.code_lines)

    def test_an_empty_file_has_nothing(self):
        p = parse("")
        self.assertEqual((p.problems, p.declarations, p.imports, p.code_lines), ((), (), (), frozenset()))


class OtherCompoundStatements(unittest.TestCase):
    def test_match_with_and_except_star_blocks_hold_declarations_and_imports(self):
        p = parse(src('''
            match command:
                case "go":
                    def go():
                        import fast
                        return 1
                case _:
                    DEFAULT = 2

            with open("f") as fh:
                RESULT = fh.read()

            try:
                pass
            except* ValueError:
                def recover():
                    return 3

            while False:
                LOOPED = 4
        '''))
        self.assertEqual(p.problems, ())
        self.assertEqual([(d.name, d.kind) for d in p.declarations], [("go", "function"), ("DEFAULT", "variable"), ("RESULT", "variable"), ("recover", "function"), ("LOOPED", "variable")])
        self.assertEqual(place(p, 5), ("go", (), "function"))
        self.assertEqual([r.module for r in p.imports], ["fast"])
        self.assertIsNone(place(p, 1))
        self.assertIn(1, p.code_lines)


class TokenSoup(unittest.TestCase):
    def test_random_token_soup_never_raises_or_hangs(self):
        import random
        rng = random.Random(6)
        pieces = ["def", "class", "if", "else:", "try:", "except", "import", "from", "as", "x", "y", "(", ")", "[", "]", "{", "}", ":", ",", "=", "\n", "\n    ", "\t",
                  "@", "lambda", "*", "**", "'s'", '"""', "'", "#", "1", "0x", ".", "..", "...", "->", ":=", "async", "await", "match", "case", "\\\n", "\r\n", "\x0c"]
        for _ in range(300):
            text = " ".join(rng.choice(pieces) for _ in range(rng.randint(1, 40)))
            p = parse(text)
            for line in range(0, 6):
                p.placement(line)
            p.imports, p.code_lines, p.module_references(), p.uses("x"), p.definition_lines("x"), p.uses_dynamic_access()


class Placement(unittest.TestCase):
    def test_nested_functions_are_reported_in_nested_and_a_sibling_is_unrelated(self):
        """s08: the nested `_load` of get_settlement; get_invoice has its own `_load`."""
        p = parse(src('''
            def get_invoice():
                def _load():
                    return 1

                return _load()


            def get_settlement():
                def _load():
                    return 2

                return _load()
        '''))
        self.assertEqual(place(p, 3), ("get_invoice", ("_load",), "function"))
        self.assertEqual(place(p, 10), ("get_settlement", ("_load",), "function"))
        self.assertEqual(place(p, 12), ("get_settlement", (), "function"))
        self.assertEqual(place(p, 9), ("get_settlement", ("_load",), "function"))  # the nested def line itself

    def test_deeper_nesting_is_listed_outermost_first_and_a_nested_class_counts(self):
        p = parse(src('''
            def outer():
                def middle():
                    def inner():
                        return 1
                    return inner
                class Local:
                    def run(self):
                        return 2
                return middle
        '''))
        self.assertEqual(place(p, 4), ("outer", ("middle", "inner"), "function"))
        self.assertEqual(place(p, 8), ("outer", ("Local", "run"), "function"))

    def test_a_nested_def_inside_an_if_inside_a_function_is_found(self):
        p = parse(src('''
            def outer(flag):
                if flag:
                    def pick():
                        return 1
                else:
                    def pick():
                        return 2
                return pick
        '''))
        self.assertEqual(place(p, 4), ("outer", ("pick",), "function"))
        self.assertEqual(place(p, 7), ("outer", ("pick",), "function"))
        self.assertEqual(place(p, 5), ("outer", (), "function"))

    def test_a_decorated_nested_function_includes_its_decorator_lines(self):
        p = parse(src('''
            def outer():
                @cache(
                    size=3,
                )
                def inner():
                    return 1
                return inner
        '''))
        self.assertEqual(place(p, 3), ("outer", ("inner",), "function"))

    def test_class_members_are_dotted_with_the_class_first(self):
        """Fields and methods are `Class.member` (s11: `Invoice.settlement_reference`); the declaration is
        the class, the nested names are the functions inside the member."""
        p = parse(src('''
            class Invoice(BaseModel):
                """A bill."""

                id: str
                settlement_reference: str | None = None
                count = 0

                def total(self):
                    def part():
                        return 1
                    return part()

                @property
                def label(self):
                    return "x"

                async def fetch(self):
                    return 2

                class Config:
                    frozen = True

                    def check(self):
                        return 3
        '''))
        self.assertEqual(names(p), [("Invoice", "class", 1, 24)])
        self.assertEqual(place(p, 1), ("Invoice", (), "class"))
        self.assertEqual(place(p, 2), ("Invoice", (), "class"))        # the docstring
        self.assertEqual(place(p, 4), ("Invoice.id", (), "variable"))
        self.assertEqual(place(p, 5), ("Invoice.settlement_reference", (), "variable"))
        self.assertEqual(place(p, 6), ("Invoice.count", (), "variable"))
        self.assertEqual(place(p, 8), ("Invoice.total", (), "function"))
        self.assertEqual(place(p, 10), ("Invoice.total", ("part",), "function"))
        self.assertEqual(place(p, 13), ("Invoice.label", (), "function"))   # the decorator line
        self.assertEqual(place(p, 17), ("Invoice.fetch", (), "function"))
        self.assertEqual(place(p, 20), ("Invoice.Config", (), "class"))
        self.assertEqual(place(p, 21), ("Invoice.Config.frozen", (), "variable"))
        self.assertEqual(place(p, 24), ("Invoice.Config.check", (), "function"))

    def test_class_body_if_blocks_are_followed_and_their_header_is_the_class(self):
        p = parse(src('''
            class A:
                if COND:
                    x = 1
                else:
                    x = 2
        '''))
        self.assertEqual(place(p, 3), ("A.x", (), "variable"))
        self.assertEqual(place(p, 5), ("A.x", (), "variable"))
        self.assertEqual(place(p, 2), ("A", (), "class"))
        self.assertEqual(place(p, 4), ("A", (), "class"))

    def test_a_lambda_assigned_at_module_level_is_a_declaration_by_its_name(self):
        p = parse(src('''
            double = lambda v: v * 2
            adders = [lambda v: v + 1,
                      lambda v: v + 2]
        '''))
        self.assertEqual(place(p, 1), ("double", (), "function"))
        self.assertEqual(place(p, 3), ("adders", (), "variable"))

    def test_identical_lines_are_placed_by_number_only(self):
        """s22 / Shape B: the same `request: Request,` line in several handlers."""
        p = parse(src('''
            def one(
                request: Request,
            ):
                return 1


            def two(
                request: Request,
            ):
                return 2
        '''))
        self.assertEqual(place(p, 2)[0], "one")
        self.assertEqual(place(p, 8)[0], "two")

    def test_two_declarations_on_one_line_are_both_placements(self):
        p = parse("x = 1; y = 2\n")
        self.assertEqual([(q.name, q.kind) for q in p.placements(1)], [("x", "variable"), ("y", "variable")])
        self.assertEqual(p.placements(2), [])

    def test_lines_past_the_end_and_before_the_start_have_no_placement(self):
        p = parse("def f():\n    return 1\n")
        self.assertIsNone(p.placement(0))
        self.assertIsNone(p.placement(3))
        self.assertIsNone(p.placement(99))

    def test_a_file_that_did_not_parse_places_nothing(self):
        p = parse("def f(:\n    pass\n")
        self.assertTrue(p.problems)
        self.assertEqual(p.declarations, ())
        self.assertIsNone(p.placement(1))


class ParseProblems(unittest.TestCase):
    def test_python_2_print_is_a_problem(self):
        p = parse('print "hello"\n')
        self.assertEqual(len(p.problems), 1)
        self.assertIn("line 1", p.problems[0])
        self.assertEqual((p.imports, p.declarations), ((), ()))

    def test_a_syntax_error_in_the_middle_is_a_problem_and_no_regex_fallback_finds_imports(self):
        p = parse("import os\ndef broken(:\n    pass\nimport sys\n")
        self.assertTrue(p.problems)
        self.assertEqual(p.imports, ())

    def test_newer_syntax_is_a_problem_for_a_python_that_cannot_parse_it(self):
        if sys.version_info >= (3, 14):
            self.skipTest("t-strings parse on this Python")
        p = parse('x = t"hello {name}"\n')
        self.assertTrue(p.problems)

    def test_a_lone_carriage_return_makes_line_numbers_ambiguous(self):
        p = parse("import os\rimport sys\r")
        self.assertTrue(p.problems)
        self.assertIn("carriage return", p.problems[0])

    def test_crlf_line_endings_are_fine(self):
        p = parse("import os\r\n\r\ndef f():\r\n    return os\r\n")
        self.assertEqual(p.problems, ())
        self.assertEqual(names(p), [("f", "function", 3, 4)])
        self.assertEqual(p.line_text(3), "def f():")

    def test_a_null_byte_is_a_problem_not_a_crash(self):
        self.assertTrue(parse("x = 1\0\n").problems)

    def test_a_bom_is_skipped(self):
        p = parse("\ufeffimport os\n")
        self.assertEqual(p.problems, ())
        self.assertEqual([r.module for r in p.imports], ["os"])

    def test_inconsistent_indentation_is_a_problem(self):
        self.assertTrue(parse("def f():\n        a = 1\n    b = 2\n").problems)

    def test_a_very_deep_expression_is_a_problem_or_parses_but_never_crashes(self):
        parse("x = " + "(" * 500 + "1" + ")" * 500 + "\n")
        parse("x = " + " + ".join(["1"] * 4000) + "\n")
        parse("x = " + "[" * 3000 + "]" * 3000 + "\n")

    def test_cutting_a_file_at_any_offset_never_raises(self):
        text = src('''
            import os
            from a.b import (c, d as e)

            @deco(1,
                  2)
            class K(Base):
                """doc"""
                x: int = 1

                def m(self, y=lambda q: q):
                    def inner():
                        return [i for i in range(3) if i]
                    return {"k": f"{y!r:>10}"}
        ''')
        for cut in range(0, len(text), 3):
            p = parse(text[:cut])
            for line in range(0, 20):
                p.placement(line)
            p.module_references()
            p.code_lines


class Imports(unittest.TestCase):
    def one(self, text):
        p = parse(src(text))
        self.assertEqual(p.problems, ())
        return p.imports

    def test_import_forms(self):
        recs = self.one('''
            import a.b
            import a.b as x
            import c, d.e as f
        ''')
        got = [(r.kind, r.specifier, [(n.name, n.asname, n.line) for n in r.names], r.line) for r in recs]
        self.assertEqual(got, [
            ("import", "a.b", [("a.b", None, 1)], 1),
            ("import", "a.b", [("a.b", "x", 2)], 2),
            ("import", "c", [("c", None, 3)], 3),
            ("import", "d.e", [("d.e", "f", 3)], 3),
        ])

    def test_from_imports_single_and_multi_line_with_the_line_of_each_name(self):
        recs = self.one('''
            from a.b import c
            from a.b import (
                c,
                d as e,
            )
            from a import *
        ''')
        got = [(r.kind, r.specifier, r.level, [(n.name, n.asname, n.line) for n in r.names], r.line) for r in recs]
        self.assertEqual(got, [
            ("from", "a.b", 0, [("c", None, 1)], 1),
            ("from", "a.b", 0, [("c", None, 3), ("d", "e", 4)], 2),
            ("star", "a", 0, [], 6),
        ])

    def test_the_line_of_a_continued_from_import_is_the_line_that_names_the_module(self):
        recs = self.one("from \\\n    pkg.mod import f\nfrom . \\\n    import g\n")
        self.assertEqual([(r.module, r.line) for r in recs], [("pkg.mod", 2), ("", 3)])

    def test_relative_imports(self):
        recs = self.one('''
            from . import x
            from .. import y as z
            from ..pkg.sub import w
            from ... import v
            from .m import *
        ''')
        got = [(r.kind, r.specifier, r.level, r.module, [n.name for n in r.names]) for r in recs]
        self.assertEqual(got, [
            ("from", ".", 1, "", ["x"]),
            ("from", "..", 2, "", ["y"]),
            ("from", "..pkg.sub", 2, "pkg.sub", ["w"]),
            ("from", "...", 3, "", ["v"]),
            ("star", ".m", 1, "m", []),
        ])

    def test_imports_inside_functions_branches_and_try_are_found(self):
        recs = self.one('''
            def f():
                import lazy
                if True:
                    from inner import thing

            class K:
                import in_class

            try:
                import fast
            except ImportError:
                import slow as fast
        ''')
        self.assertEqual([r.module for r in recs], ["lazy", "inner", "in_class", "fast", "slow"])
        self.assertEqual([r.line for r in recs], [2, 4, 7, 10, 12])

    def test_type_checking_blocks_are_marked_type_only(self):
        recs = self.one('''
            from typing import TYPE_CHECKING
            import typing as t

            if TYPE_CHECKING:
                from a import Hint
                if True:
                    import deep
            else:
                import real

            if t.TYPE_CHECKING:
                import other

            if not TYPE_CHECKING:
                import runtime
            else:
                import checked
        ''')
        flags = {r.module: r.type_only for r in recs}
        self.assertEqual(flags, {
            "typing": False, "a": True, "deep": True, "real": False, "other": True, "runtime": False, "checked": True,
        })

    def test_import_module_with_a_literal_is_a_dynamic_import_that_resolves(self):
        recs = self.one('''
            import importlib
            from importlib import import_module as load

            a = importlib.import_module("pkg.plugins")
            b = load("pkg.other")
            c = __import__("pkg.third")
            d = importlib.import_module(".sub", package="pkg.base")
            e = importlib.import_module(".sub", "pkg.base")
        ''')
        dyn = [(r.kind, r.module, r.line) for r in recs if r.kind == "dynamic"]
        self.assertEqual(dyn, [
            ("dynamic", "pkg.plugins", 4), ("dynamic", "pkg.other", 5), ("dynamic", "pkg.third", 6),
            ("dynamic", "pkg.base.sub", 7), ("dynamic", "pkg.base.sub", 8),
        ])

    def test_dynamic_imports_with_an_expression_are_recorded_by_line(self):
        p = parse(src('''
            import importlib

            def load(name):
                return importlib.import_module(name)

            mod = __import__(f"pkg.{kind}")
            rel = importlib.import_module(".sub")
        '''))
        self.assertEqual(p.unresolved_dynamic, (4, 6, 7))
        self.assertEqual([r for r in p.imports if r.kind == "dynamic"], [])

    def test_an_unrelated_import_module_call_is_not_an_import(self):
        p = parse('def import_module(x):\n    return x\n\nimport_module("a")\nother.import_module("b")\n')
        self.assertEqual(p.imports, ())
        self.assertEqual(p.unresolved_dynamic, ())

    def test_text_that_looks_like_an_import_in_a_string_or_comment_is_not_one(self):
        p = parse(src('''
            # import fake
            TEXT = """
            import also_fake
            from nothing import more
            """
            def f():
                "import docstring_fake"
        '''))
        self.assertEqual(p.imports, ())

    def test_future_imports_are_listed_like_any_other(self):
        recs = self.one("from __future__ import annotations\n")
        self.assertEqual([r.module for r in recs], ["__future__"])


class References(unittest.TestCase):
    TEXT = src('''
        import os
        from m import imported

        LIMIT = 3


        def helper():
            return LIMIT


        def user():
            return helper() + imported


        class K:
            limit = LIMIT

            def go(self):
                return user()


        VALUE = helper()
        print(VALUE)
        os.environ["x"] = "1"
    ''')

    def test_declaration_references_are_every_name_inside_its_span(self):
        p = parse(self.TEXT)
        by = {d.name: d for d in p.declarations}
        self.assertIn("LIMIT", p.decl_references(by["helper"]))
        self.assertTrue({"helper", "imported"} <= p.decl_references(by["user"]))
        self.assertTrue({"LIMIT", "user"} <= p.decl_references(by["K"]))   # a class body and its methods
        self.assertIn("helper", p.decl_references(by["VALUE"]))

    def test_module_references_exclude_declaration_bodies(self):
        p = parse(self.TEXT)
        refs = p.module_references()
        self.assertTrue({"print", "VALUE", "os"} <= refs)
        self.assertNotIn("user", refs)
        self.assertNotIn("LIMIT", refs)
        self.assertNotIn("helper", refs)

    def test_decorators_defaults_and_annotations_count(self):
        p = parse(src('''
            @router.get("/x")
            def f(a: Hint = DEFAULT) -> Result:
                pass
        '''))
        (d,) = p.declarations
        self.assertTrue({"router", "Hint", "DEFAULT", "Result"} <= p.decl_references(d))

    def test_strings_that_read_as_names_are_references_and_prose_is_not(self):
        p = parse(src('''
            class Order:
                customer: 'Customer'
                items = relationship("list[Item]")
                other = lookup("models.Owner")
                note = "a long sentence about Nothing"
                path = "/api/x"
        '''))
        (d,) = p.declarations
        refs = p.decl_references(d)
        self.assertTrue({"Customer", "list", "Item", "models"} <= refs)
        self.assertNotIn("Nothing", refs)
        self.assertNotIn("api", refs)

    def test_attribute_names_are_what_a_declaration_may_read_of_other_objects(self):
        p = parse(src('''
            def summarize(doc, **kw):
                x = doc.total()
                y = getattr(doc, "settlement_reference")
                return Invoice(amount=1, **kw), x, y

            VALUE = other.name

            print(thing.module_level)
        '''))
        by = {d.name: d for d in p.declarations}
        self.assertEqual(p.decl_attributes(by["summarize"]), {"total", "settlement_reference", "amount"})
        self.assertEqual(p.decl_attributes(by["VALUE"]), {"name"})
        self.assertEqual(p.module_attributes(), {"module_level"})

    def test_import_time_calls_are_the_names_called_where_a_declaration_runs_at_import(self):
        p = parse(src('''
            CONFIG = load(env.get("x"))
            LAZY = lambda: skipped()
            ITEMS = [make(i) for i in range(3)]


            @register(spec())
            def handler(a=default(), *, b=other()):
                return not_at_import()


            class K(base(), metaclass=meta()):
                x = compute()

                def method(self, y=dflt()):
                    return not_either()

                class Inner:
                    z = nested_call()


            def plain():
                return ignored()
        '''))
        by = {d.name: d for d in p.declarations}
        self.assertEqual(p.import_time_calls(by["CONFIG"]), {"load", "env"})
        self.assertEqual(p.import_time_calls(by["LAZY"]), set())
        self.assertEqual(p.import_time_calls(by["ITEMS"]), {"make", "range"})
        self.assertEqual(p.import_time_calls(by["handler"]), {"register", "spec", "default", "other"})
        self.assertEqual(p.import_time_calls(by["K"]), {"base", "meta", "compute", "dflt", "nested_call"})
        self.assertEqual(p.import_time_calls(by["plain"]), set())

    def test_global_names_are_what_a_declaration_may_write(self):
        p = parse("STATE = 1\n\n\ndef init():\n    global STATE, OTHER\n    STATE = 2\n\n\ndef read():\n    return STATE\n")
        by = {d.name: d for d in p.declarations}
        self.assertEqual(p.global_names(by["init"]), {"STATE", "OTHER"})
        self.assertEqual(p.global_names(by["read"]), set())

    def test_decorator_names_are_what_a_declaration_registers_itself_with(self):
        p = parse(src('''
            router = APIRouter()


            @router.get("/x", response_model=Out)
            def f():
                @functools.cache
                def inner():
                    pass


            class K:
                @app.task(name="t")
                def m(self):
                    pass


            def plain():
                pass
        '''))
        by = {d.name: d for d in p.declarations}
        self.assertEqual(p.decorator_names(by["f"]), {"router", "Out", "functools"})
        self.assertEqual(p.decorator_names(by["K"]), {"app"})
        self.assertEqual(p.decorator_names(by["plain"]), set())
        self.assertEqual(p.decorator_names(by["router"]), set())

    def test_dynamic_access_is_detected(self):
        for text in (
            "x = eval('1')\n", "exec('a = 1')\n", "g = globals()\n", "def f():\n    return locals()\n", "v = vars(K)\n",
            "def __getattr__(name):\n    return 1\n",
        ):
            with self.subTest(text=text):
                self.assertTrue(parse(text).uses_dynamic_access())
        for text in (
            "import sys\n\n\ndef f(n):\n    return getattr(sys.modules[__name__], n)()\n", "import sys\nm = sys.modules.get(__name__)\n",
            "from sys import modules\nm = modules[__name__]\n",
        ):
            with self.subTest(text=text):
                self.assertTrue(parse(text).uses_dynamic_access())
        self.assertFalse(parse(self.TEXT).uses_dynamic_access())
        self.assertFalse(parse("import sys\nprint(sys.path, sys.argv)\nmodules = []\nx = modules[0]\n").uses_dynamic_access())
        self.assertFalse(parse("x = obj.eval\ny = {'globals': 1}\n").uses_dynamic_access())


class Uses(unittest.TestCase):
    def test_calls_references_and_attribute_chains(self):
        p = parse(src('''
            def a():
                x = fmt(1, y=2)
                m.sub.fn(3)
                run(fmt)
                return m.sub


            v = m.sub.fn
        '''))
        got = [(u.line, u.chain, u.form) for u in p.uses("fmt")]
        self.assertEqual(got, [(2, (), "call"), (4, (), "reference")])
        got = [(u.line, u.chain, u.form) for u in p.uses("m")]
        self.assertEqual(got, [(3, ("sub", "fn"), "call"), (5, ("sub",), "reference"), (8, ("sub", "fn"), "reference")])

    def test_a_quoted_annotation_that_reads_as_a_dotted_name_is_a_use_of_its_root(self):
        p = parse(src('''
            import pkg.m as m


            def f(x: "m.Invoice", y: list["m.Other"], z: "plain", note: "a sentence of prose") -> "m.Result":
                pass
        '''))
        got = [(u.line, u.chain, u.form) for u in p.uses("m")]
        self.assertEqual(got, [(4, ("Invoice",), "reference"), (4, ("Other",), "reference"), (4, ("Result",), "reference")])
        self.assertEqual(p.uses("plain"), ())  # a bare quoted name is a reference for narrowing, not a call site

    def test_arguments_are_source_text_including_multi_line_and_starred(self):
        p = parse(src('''
            r = fmt(
                row.exposure_cents,
                *rest,
                currency="EUR",
                **opts,
            )
            s = fmt()
        '''))
        first, second = p.uses("fmt")
        self.assertEqual(p.args_of(first), ("row.exposure_cents", "*rest", 'currency="EUR"', "**opts"))
        self.assertEqual(p.args_of(second), ())
        self.assertEqual((first.line, second.line), (1, 7))

    def test_a_byte_order_mark_does_not_shift_the_arguments_of_the_first_line(self):
        p = parse("\ufeffprint(foo, bar)\n")
        (u,) = p.uses("print")
        self.assertEqual(p.args_of(u), ("foo", "bar"))
        self.assertEqual(p.line_text(1), "\ufeffprint(foo, bar)")  # the line is cited as git has it
        self.assertEqual(parse("\ufeff# only a comment\nx = 1\n").code_lines, frozenset())

    def test_non_ascii_text_before_an_argument_does_not_shift_it(self):
        p = parse('r = fmt("café", n)\n')
        (u,) = p.uses("fmt")
        self.assertEqual(p.args_of(u), ('"café"', "n"))

    def test_enclosing_is_the_top_level_declaration_and_module_otherwise(self):
        p = parse(src('''
            class K:
                def go(self):
                    return fmt(1)


            def top():
                return fmt(2)


            VALUE = fmt(3)
            print(fmt(4))
        '''))
        self.assertEqual([p.enclosing(u.line) for u in p.uses("fmt")], ["K", "top", "VALUE", "<module>"])

    def test_stores_and_attribute_stores_on_other_objects_are_not_uses_of_the_name(self):
        p = parse("fmt = 1\nobj.fmt = 2\nm.x = 3\n")
        self.assertEqual(p.uses("fmt"), ())
        self.assertEqual([(u.line, u.chain) for u in p.uses("m")], [(3, ("x",))])

    def test_decorator_use_is_a_call(self):
        p = parse("@scheduled(name='x')\ndef f():\n    pass\n")
        (u,) = p.uses("scheduled")
        self.assertEqual((u.form, u.line), ("call", 1))


class Definitions(unittest.TestCase):
    def test_defs_classes_and_module_and_class_level_assignments_count(self):
        p = parse(src('''
            interval = 1


            def interval():
                local = 2
                def interval():
                    pass


            class interval:
                interval: int = 3
                other = 4

                def interval(self):
                    interval = 5


            async def f():
                interval = 6
                interval: int = 7


            if True:
                interval = 8

            for interval in range(3):
                pass
        '''))
        self.assertEqual(p.definition_lines("interval"), [1, 4, 6, 10, 11, 14, 24])

    def test_references_imports_and_attribute_reads_do_not_count(self):
        p = parse(src('''
            from m import interval
            import interval as other
            x = interval
            y = obj.interval
            z = {"interval": 1}
            # interval = 3
            "interval = 4"
            call(interval=5)
        '''))
        self.assertEqual(p.definition_lines("interval"), [])

    def test_a_file_that_did_not_parse_has_no_definitions(self):
        self.assertEqual(parse("def interval(:\n").definition_lines("interval"), [])


if __name__ == "__main__":
    unittest.main()
