"""The code the Django and the FastAPI route tables share (`reachmap.adapters.python_routes`), on small inputs and a fake language adapter.

Nothing here reads git. What each framework does with its routes is in `test_fastapi.py` and the `test_django_*.py` modules; these tests pin the parts
both rely on: the syntax helpers, the search and the per file reads of a table (a read that fails is a hole, never an absence), the evaluation of a
prefix, the evidence of a route, `routes_changed` (what is withheld when a table has a hole, and in which order the reasons are said) and the areas
a change reaches.
"""

from __future__ import annotations

import ast
import textwrap
import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from reachmap.adapters import Route
from reachmap.adapters import python_routes as pr
from reachmap.adapters.python.syntax import parse
from reachmap.incomplete import records_of
from reachmap.model import Evidence, Read

SHA = "a" * 40
OTHER = "b" * 40


def src(text: str) -> str:
    return textwrap.dedent(text).lstrip("\n")


def expr(text: str) -> ast.expr:
    return ast.parse(text, mode="eval").body


# ------------------------------------------------------------------------------ syntax


class Syntax(unittest.TestCase):
    def test_statements_go_through_every_block_that_runs_when_the_module_loads_and_not_into_functions(self):
        tree = ast.parse(src('''
            a = 1
            if x:
                b = 2
            else:
                c = 3
            try:
                d = 4
            except E:
                e = 5
            finally:
                f = 6
            with w:
                g = 7
            for i in j:
                h = 8
            while k:
                m = 9
            def fn():
                inner = 10
            class C:
                member = 11
            match v:
                case 1:
                    n = 12
            '''))
        names = [s.targets[0].id for s in pr.statements(tree.body) if isinstance(s, ast.Assign)]
        self.assertEqual(names, ["a", "b", "c", "d", "f", "e", "g", "h", "m", "n"])

    def test_statements_yield_a_compound_statement_before_what_is_in_it(self):
        tree = ast.parse("if x:\n    y = 1\n")
        self.assertEqual([type(s).__name__ for s in pr.statements(tree.body)], ["If", "Assign"])

    def test_callee_is_the_last_name_of_a_call_and_nothing_for_anything_else(self):
        self.assertEqual(pr.callee(expr("path(1)").func), "path")
        self.assertEqual(pr.callee(expr("urls.path(1)").func), "path")
        self.assertIsNone(pr.callee(expr("make()(1)").func))
        self.assertIsNone(pr.callee(expr("a[0](1)").func))

    def test_kw_is_the_value_of_a_keyword_or_none(self):
        call = expr("f(1, prefix='/x', methods=['GET'])")
        self.assertEqual(ast.unparse(pr.kw(call, "prefix")), "'/x'")
        self.assertIsNone(pr.kw(call, "path"))

    def test_names_in_collects_plain_names_of_every_node_and_skips_none(self):
        self.assertEqual(pr.names_in(expr("a.b(c, d=e)"), None, expr("f + 1")), frozenset({"a", "c", "e", "f"}))
        self.assertEqual(pr.names_in(), frozenset())

    def test_literal_strings_needs_every_element_to_be_a_string_literal(self):
        self.assertEqual(pr.literal_strings(expr('["GET", "post"]')), ["GET", "post"])
        self.assertEqual(pr.literal_strings(expr('("a", "b")')), ["a", "b"])
        self.assertEqual(pr.literal_strings(expr('{"a"}')), ["a"])
        self.assertEqual(pr.literal_strings(expr("[]")), [])  # an empty list is a list of literals: no methods
        self.assertIsNone(pr.literal_strings(expr('["GET", VERB]')))
        self.assertIsNone(pr.literal_strings(expr('["GET", 1]')))
        self.assertIsNone(pr.literal_strings(expr("METHODS")))
        self.assertIsNone(pr.literal_strings(expr('"GET"')))
        self.assertIsNone(pr.literal_strings(None))

    def test_nearest_folder_is_the_closest_folder_above_the_file_that_holds_the_marker(self):
        files = {"svc/a/pyproject.toml", "svc/a/b/pyproject.toml", "apps.py", "x/y/z.py"}
        self.assertEqual(pr.nearest_folder(files, "svc/a/b/c/mod.py", "pyproject.toml"), "b")
        self.assertEqual(pr.nearest_folder(files, "svc/a/mod.py", "pyproject.toml"), "a")
        self.assertIsNone(pr.nearest_folder(files, "x/y/z.py", "pyproject.toml"))

    def test_the_marker_at_the_root_does_not_name_a_folder(self):
        self.assertIsNone(pr.nearest_folder({"apps.py"}, "mod.py", "apps.py"))
        self.assertIsNone(pr.nearest_folder({"apps.py"}, "pkg/mod.py", "apps.py"))


class Imports(unittest.TestCase):
    def test_bind_imports_fills_the_bindings_and_returns_the_star_imports(self):
        facts = pr.FileFacts("m.py", parse(src('''
            from . import a
            from m import b as c
            from .rel import d
            import x.y
            import p as q
            from z import *
            ''')))
        stars = facts.bind_imports()
        self.assertEqual(stars, [("z", 0)])
        self.assertEqual(facts.from_binding, {"a": ("", 1, "a"), "c": ("m", 0, "b"), "d": ("rel", 1, "d")})
        self.assertEqual(facts.module_binding, {"x": ("x", 0), "q": ("p", 0)})  # `import x.y` binds `x`, an alias binds the module it names
        self.assertEqual(facts.consts, {})

    def test_a_file_without_imports_binds_nothing(self):
        facts = pr.FileFacts("m.py", parse("x = 1\n"))
        self.assertEqual(facts.bind_imports(), [])
        self.assertEqual((facts.from_binding, facts.module_binding), ({}, {}))


# ------------------------------------------------------------------------------ a table over a fake language


@dataclass(frozen=True, slots=True)
class Str:
    """The string of the fake table: text and the unknown parts in it."""

    value: str = ""
    unknown: tuple[tuple[str, str, int], ...] = ()

    def __add__(self, other: "Str") -> "Str":
        return Str(self.value + other.value, self.unknown + other.unknown)


class Language:
    """The few methods a table asks of the Python adapter. `files` maps a path to its text (None: it cannot be read)."""

    def __init__(self, files: dict[str, str | None], search: Any = None) -> None:
        self.files = files
        self.found = search
        self.target: list[Any] = []
        self.parses = 0

    def search(self, sha: str, patterns: list[str]) -> Any:
        return self.found

    def file_text(self, sha: str, path: str) -> Any:
        return SimpleNamespace(is_found=self.files.get(path) is not None)

    def parsed(self, sha: str, path: str) -> Any:
        self.parses += 1
        return parse(self.files[path])

    def is_test_path(self, path: str) -> bool:
        return path.startswith("tests/")

    def resolve_module(self, path: str, sha: str, module: str, level: int) -> tuple[str, ...]:
        file = module.replace(".", "/") + ".py"
        return (file,) if file in self.files else ()


def found(paths: list[str], **kw: Any) -> Any:
    base: dict[str, Any] = dict(state=Read.FOUND, detail=None, truncated_by=None, examined=0, total=0, not_searched=[], partial_matches=[SimpleNamespace(path=p) for p in paths])
    base.update(kw)
    return SimpleNamespace(**base)


class Adapter:
    def __init__(self, language: Language) -> None:
        self.language = language
        self.target = language.target
        self.problems: list[str] = []

    def problem(self, why: str) -> None:
        if why not in self.problems:
            self.problems.append(why)


class Table(pr.TableBase):
    LABEL = "fake table"
    NOUN = "things"
    UNPARSED = "things in it were not read"
    DEGRADED = "nothing is claimed from it"
    PATTERNS = ["alpha", "beta"]
    Str = Str

    def __init__(self, adapter: Any, sha: str) -> None:
        super().__init__(adapter, sha)
        self.read: list[str] = []

    def read_facts(self, path: str, parsed: Any) -> Any:
        self.read.append(path)
        facts = pr.FileFacts(path, parsed)
        facts.bind_imports()
        counts: dict[str, int] = {}
        for stmt in parsed.tree.body:
            if isinstance(stmt, ast.Assign) and isinstance(stmt.targets[0], ast.Name):
                name = stmt.targets[0].id
                counts[name] = counts.get(name, 0) + 1
                facts.consts[name] = stmt.value if counts[name] == 1 else None
        return facts

    @staticmethod
    def _unknown(path: str, node: ast.AST) -> Str:
        text = ast.unparse(node)
        return Str(f"<unknown:{text}>", ((text, path, getattr(node, "lineno", 1)),))


def table(files: dict[str, str | None], search: Any = None, cls: type = Table) -> Any:
    return cls(Adapter(Language(files, search)), SHA)


class Search(unittest.TestCase):
    def test_the_candidates_are_the_python_files_that_match_outside_tests(self):
        t = table({}, found(["app/a.py", "app/b.txt", "tests/test_a.py", "app/c.py", "bad\\name.py"]))
        t.search()
        self.assertEqual(t.candidates, {"app/a.py", "app/c.py"})
        self.assertTrue(t.complete)
        self.assertEqual(t.adapter.problems, [])

    def test_a_tree_that_could_not_be_read_is_an_incomplete_table_and_not_an_empty_one(self):
        t = table({}, None)
        t.search()
        self.assertFalse(t.complete)
        self.assertEqual(t.candidates, set())
        self.assertEqual(t.reasons, ["the tree could not be read at this commit"])
        self.assertEqual(t.adapter.problems, [f"fake table at {SHA[:12]}: the tree could not be read at this commit"])

    def test_an_unreadable_search_says_which_search_and_why(self):
        t = table({}, found([], state=Read.UNREADABLE, detail="rate limited"))
        t.search()
        self.assertFalse(t.complete)
        self.assertEqual(t.reasons, ["the search for 'alpha | beta' was unreadable: rate limited"])

    def test_a_cut_search_is_incomplete_and_its_matches_are_still_candidates(self):
        t = table({}, found(["app/a.py"], truncated_by="examine_cap", examined=10, total=30))
        t.search()
        self.assertFalse(t.complete)
        self.assertEqual(t.candidates, {"app/a.py"})
        self.assertEqual(t.reasons, ["search_truncated: the search for 'alpha | beta' examined 10 of 30 files"])

    def test_a_python_file_the_search_does_not_read_is_a_hole_in_the_words_of_the_table(self):
        seen = []

        class Hooked(Table):
            def not_searched(self, link: str, why: str) -> None:
                seen.append((link, why))

        t = table({}, found([], not_searched=[("app/link.py", "symlink"), ("app/data.json", "symlink")]), Hooked)
        t.search()
        self.assertEqual(t.reasons, ["app/link.py is a symlink: git grep does not search it, so things in it may be missed"])
        self.assertEqual(seen, [("app/link.py", "symlink")])


class Facts(unittest.TestCase):
    def test_a_file_is_read_once(self):
        t = table({"a.py": "x = 1\n"})
        first = t.facts_of("a.py")
        self.assertIs(t.facts_of("a.py"), first)
        self.assertEqual((t.read, t.language.parses), (["a.py"], 1))

    def test_a_file_that_cannot_be_read_is_a_hole_and_not_an_absence(self):
        t = table({"a.py": None})
        self.assertIsNone(t.facts_of("a.py"))
        self.assertIsNone(t.facts_of("a.py"))
        self.assertEqual(t.reasons, ["a.py could not be read"])
        self.assertFalse(t.complete)
        self.assertEqual(records_of(t.target), ())  # said as a reason; the file that does not parse is the one with a record

    def test_a_file_that_does_not_parse_claims_nothing_and_records_a_blocking_parse_degraded(self):
        t = table({"a.py": "def broken(:\n    pass\n"})
        self.assertIsNone(t.facts_of("a.py"))
        self.assertIsNone(t.facts_of("a.py"))
        (reason,) = t.reasons
        self.assertTrue(reason.startswith("a.py did not parse ("), reason)
        self.assertTrue(reason.endswith("): things in it were not read"), reason)
        (rec,) = records_of(t.target)
        self.assertEqual((rec.kind, rec.subject, rec.blocks_selection), ("parse_degraded", "a.py", True))
        self.assertTrue(rec.detail.startswith("nothing is claimed from it ("), rec.detail)
        self.assertEqual(t.read, [])

    def test_a_subclass_decides_what_an_unread_file_does_to_the_table(self):
        lost: list[tuple[str, str]] = []

        class Holes(Table):
            def unread(self, path: str, why: str) -> None:
                lost.append((path, why))
                super().unread(path, why)

        t = table({"a.py": None}, cls=Holes)
        t.facts_of("a.py")
        self.assertEqual(lost, [("a.py", "a.py could not be read")])
        self.assertFalse(t.complete)

    def test_incomplete_keeps_a_reason_once(self):
        t = table({})
        t.incomplete("why")
        t.incomplete("why")
        self.assertEqual((t.reasons, t.adapter.problems), (["why"], [f"fake table at {SHA[:12]}: why"]))

    def test_a_table_that_does_not_say_where_its_holes_are_has_one_everywhere_when_it_is_not_whole(self):
        t = table({})
        self.assertFalse(t.hole_covers("/anything"))
        t.incomplete("why")
        self.assertTrue(t.hole_covers("/anything"))


class Evaluation(unittest.TestCase):
    def ev(self, files: dict[str, str | None], text: str, path: str = "a.py", cls: type = Table) -> Str:
        return table(files, cls=cls).eval(path, expr(text))

    def test_literals_f_strings_and_sums_are_evaluated(self):
        self.assertEqual(self.ev({"a.py": ""}, '"/api"'), Str("/api"))
        self.assertEqual(self.ev({"a.py": ""}, "None"), Str())
        self.assertEqual(self.ev({"a.py": "V = '/v1'\n"}, 'f"{V}/items"'), Str("/v1/items"))
        self.assertEqual(self.ev({"a.py": "V = '/v1'\n"}, 'V + "/items"'), Str("/v1/items"))

    def test_what_is_not_known_is_an_unknown_part_with_its_place(self):
        out = self.ev({"a.py": ""}, '"/a" + settings.PREFIX')
        self.assertEqual(out, Str("/a<unknown:settings.PREFIX>", (("settings.PREFIX", "a.py", 1),)))
        out = self.ev({"a.py": ""}, 'f"/{x!r}/{y:>4}"')
        self.assertEqual(len(out.unknown), 2)  # a conversion or a format makes the part unknown

    def test_a_number_is_not_a_string(self):
        self.assertEqual(self.ev({"a.py": ""}, "3").unknown[0][0], "3")

    def test_a_constant_of_the_file_and_one_imported_from_a_module_of_the_repository(self):
        files = {"a.py": "from consts import BASE\nLOCAL = BASE + '/x'\n", "consts.py": "BASE = '/base'\n"}
        self.assertEqual(self.ev(files, "LOCAL"), Str("/base/x"))

    def test_a_name_assigned_twice_is_unknown(self):
        self.assertEqual(self.ev({"a.py": "V = '/a'\nV = '/b'\n"}, "V").unknown[0][0], "V")

    def test_a_chain_of_constants_is_followed_only_so_far(self):
        lines = ["V0 = '/x'"] + [f"V{i} = V{i - 1}" for i in range(1, pr.DEPTH + 2)]
        files = {"a.py": "\n".join(lines) + "\n"}
        self.assertEqual(self.ev(files, "V3"), Str("/x"))
        self.assertTrue(self.ev(files, f"V{pr.DEPTH + 1}").unknown)

    def test_an_imported_name_the_file_also_assigns_is_not_followed_unless_the_table_says_it_is_not_assigned(self):
        files = {"a.py": "from consts import BASE\nBASE = BASE\nBASE = BASE\n", "consts.py": "BASE = '/base'\n"}
        self.assertTrue(self.ev(files, "BASE").unknown)  # the file assigns it twice: the import is not what the name stands for

        class Lenient(Table):
            def _assigned(self, facts: Any, name: str) -> bool:
                return False

        self.assertEqual(self.ev(files, "BASE", cls=Lenient), Str("/base"))

    def test_a_file_that_cannot_be_read_gives_an_unknown_part(self):
        self.assertEqual(self.ev({"a.py": None}, "V").unknown[0][0], "V")


# ------------------------------------------------------------------------------ evidence


def parsed_files() -> dict[str, Any]:
    return {
        "app/a.py": parse(src('''
            from fastapi import APIRouter
            router = APIRouter(prefix="/a")

            @router.get("/x")
            def x(): ...
            ''')),
        "app/main.py": parse("app.include_router(router)\n\napp2 = 1\n"),
    }


class RouteEvidence(unittest.TestCase):
    def test_the_first_place_carries_the_claim_and_every_place_is_cited_at_its_line(self):
        files = parsed_files()
        out = pr.route_evidence("t/api", SHA, [("app/a.py", 4), ("app/a.py", 2), ("app/main.py", 1)], files.get, "route:GET /a/x:app/a.py")
        assert out is not None
        self.assertEqual([(e.path, e.line, e.claim) for e in out], [("app/a.py", 4, "route:GET /a/x:app/a.py"), ("app/a.py", 2, None), ("app/main.py", 1, None)])
        self.assertEqual(out[0].detail, '@router.get("/x")')
        self.assertTrue(all(e.repo == "t/api" and e.ref == SHA for e in out))

    def test_a_place_that_cannot_be_cited_means_no_route_and_nothing_after_it_is_asked(self):
        files = parsed_files()
        asked: list[str] = []

        def parsed_of(path: str) -> Any:
            asked.append(path)
            return files.get(path)

        self.assertIsNone(pr.route_evidence("t/api", SHA, [("app/a.py", 4), ("app/a.py", 3), ("app/main.py", 1)], parsed_of, "c"))  # line 3 is blank
        self.assertEqual(asked, ["app/a.py", "app/a.py"])
        self.assertIsNone(pr.route_evidence("t/api", SHA, [("app/missing.py", 1)], parsed_of, "c"))
        self.assertIsNone(pr.route_evidence("t/api", SHA, [("app/a.py", 4), ("app/main.py", 9)], parsed_of, "c"))  # past the end of the file

    def test_no_place_is_no_evidence(self):
        self.assertEqual(pr.route_evidence("t/api", SHA, [], {}.get, "c"), ())


# ------------------------------------------------------------------------------ the adapter


def route(method: str = "GET", path: str = "/a", file: str = "app/x.py", handler: str = "h", line: int = 1) -> Route:
    ev = Evidence(repo="t/api", path=file, line=line, ref=SHA, detail="@router")
    return Route("t/api", "api_route", method, path, file, handler, file, "", (ev,))


class FakeTable:
    """A table made of a spec: `routes`, `complete`, `reasons`, `holes` (path prefixes that may hide routes; None: anywhere), `unresolved`, `files`."""

    def __init__(self, adapter: Any, sha: str) -> None:
        spec = adapter.specs[sha]
        self.sha = sha
        self._routes = spec.get("routes", [])
        self.complete = spec.get("complete", True)
        self.reasons = spec.get("reasons", [])
        self.holes = spec.get("holes", [])
        self.unresolved = spec.get("unresolved", False)
        self.entry_files = spec.get("files", set())

    def routes(self) -> list[Route]:
        return self._routes

    def hole_covers(self, path: str) -> bool:
        return any(h is None or path.startswith(h) for h in self.holes)


class Python(pr.Shared):
    kind = "fake"
    TABLE = FakeTable
    UNMOUNTED_KIND = "router_unmounted"
    UNMOUNTED_DETAIL = "nothing mounts it"
    UNMOUNTED_UNSURE = "{subject} may be mounted by something unread"

    def __init__(self, specs: dict[str, dict[str, Any]], record_to: Any = None) -> None:
        super().__init__("t/api", SimpleNamespace(target=["language target"]), record_to=record_to)
        self.specs = specs


class Accessors(unittest.TestCase):
    def test_a_table_is_made_once_per_commit(self):
        a = Python({SHA: {}, OTHER: {}})
        self.assertIs(a._table(SHA), a._table(SHA))
        self.assertIsNot(a._table(SHA), a._table(OTHER))

    def test_the_record_target_is_the_one_given_or_the_languages(self):
        mine: list[Any] = []
        self.assertIs(Python({}, record_to=mine).target, mine)
        self.assertEqual(Python({}).target, ["language target"])

    def test_a_problem_is_listed_once(self):
        a = Python({})
        a.problem("x")
        a.problem("x")
        a.problem("y")
        self.assertEqual(a.problems, ["x", "y"])

    def test_routes_of_another_repository_is_refused_and_none_means_this_one(self):
        a = Python({SHA: {"routes": [route()]}})
        self.assertEqual(len(a.routes(None, SHA)), 1)
        self.assertEqual(len(a.routes("t/api", SHA)), 1)
        with self.assertRaises(ValueError):
            a.routes("t/web", SHA)

    def test_routes_complete_gives_a_copy_of_the_reasons(self):
        a = Python({SHA: {"complete": False, "reasons": ["why"]}})
        ok, reasons = a.routes_complete(SHA)
        reasons.append("more")
        self.assertEqual((ok, a.routes_complete(SHA)[1]), (False, ["why"]))

    def test_an_entry_file_is_one_the_table_names(self):
        a = Python({SHA: {"files": {"app/x.py"}}})
        self.assertTrue(a.is_entry_file("app/x.py", SHA))
        self.assertFalse(a.is_entry_file("app/y.py", SHA))

    def test_the_adapter_guards_its_own_route_changes(self):
        self.assertTrue(Python.withholds_unread)


class RoutesChanged(unittest.TestCase):
    def changed(self, base: dict[str, Any], head: dict[str, Any], cls: type = Python) -> tuple[Any, list[Any]]:
        records: list[Any] = []
        a = cls({SHA: base, OTHER: head}, record_to=records)
        removed, added = a.routes_changed(SHA, OTHER)
        return ([r.path for r in removed], [r.path for r in added]), records_of(records)

    def test_without_a_pre_change_version_nothing_is_computed(self):
        a = Python({OTHER: {"routes": [route()]}})
        self.assertEqual(a.routes_changed(None, OTHER), ([], []))  # no table was asked for
        self.assertEqual(a._tables, {})

    def test_between_two_whole_tables_the_difference_stands_and_nothing_is_recorded(self):
        got, records = self.changed({"routes": [route(path="/gone"), route(path="/kept")]}, {"routes": [route(path="/kept"), route(path="/new")]})
        self.assertEqual(got, (["/gone"], ["/new"]))
        self.assertEqual(records, ())

    def test_a_method_is_part_of_the_identity_of_a_route(self):
        got, _ = self.changed({"routes": [route("GET", "/a")]}, {"routes": [route("POST", "/a")]})
        self.assertEqual(got, (["/a"], ["/a"]))

    def test_a_removal_is_withheld_where_the_head_has_a_hole_and_an_addition_where_the_base_has_one(self):
        base = {"routes": [route(path="/billing/old"), route(path="/cases/old")], "complete": False, "reasons": ["base hole"], "holes": ["/new"]}
        head = {"routes": [route(path="/new/x"), route(path="/other")], "complete": False, "reasons": ["head hole"], "holes": ["/billing"]}
        got, records = self.changed(base, head)
        self.assertEqual(got, (["/cases/old"], ["/other"]))  # /billing/old and /new/x are under a hole of the other side
        (rec,) = records
        self.assertEqual((rec.kind, rec.subject, rec.blocks_selection), ("route_change_withheld", "t/api", True))
        self.assertTrue(rec.detail.startswith("1 removal(s) and 1 addition(s) were not claimed: "), rec.detail)

    def test_a_hole_anywhere_withholds_everything_on_that_side(self):
        got, records = self.changed({"routes": [route(path="/a")]}, {"routes": [route(path="/b")], "complete": False, "reasons": ["x"], "holes": [None]})
        self.assertEqual(got, ([], ["/b"]))
        self.assertEqual(len(records), 1)

    def test_the_reasons_of_a_table_that_is_whole_are_not_said(self):
        base = {"routes": [route(path="/a")], "reasons": ["a reason kept by a whole table"]}
        head = {"routes": [], "complete": False, "reasons": ["head hole"], "holes": [None]}
        _, records = self.changed(base, head)
        self.assertTrue(records[0].detail.endswith(": head hole"), records[0].detail)

    def test_the_reasons_are_said_base_first_unless_the_adapter_says_head_first(self):
        base = {"routes": [route(path="/a")], "complete": False, "reasons": ["base hole"], "holes": [None]}
        head = {"routes": [route(path="/b")], "complete": False, "reasons": ["head one", "head two"], "holes": [None]}
        _, records = self.changed(base, head)
        self.assertTrue(records[0].detail.endswith(": base hole; head one; head two"), records[0].detail)

        class HeadFirst(Python):
            HEAD_REASONS_FIRST = True

        _, records = self.changed(base, head, HeadFirst)
        self.assertTrue(records[0].detail.endswith(": head one; head two; base hole"), records[0].detail)

    def test_the_reasons_are_cut_at_300_characters(self):
        head = {"routes": [], "complete": False, "reasons": ["w" * 400], "holes": [None]}
        _, records = self.changed({"routes": [route()]}, head)
        self.assertEqual(records[0].detail, "1 removal(s) and 0 addition(s) were not claimed: " + "w" * 300)


class Reaching(unittest.TestCase):
    def chain(self, sha: str = SHA, complete: bool = True, reasons: tuple[str, ...] = ()) -> Any:
        return SimpleNamespace(sha=sha, complete=complete, reasons=reasons)

    def evidence(self, line: int) -> tuple[Evidence, ...]:
        return (Evidence(repo="t/api", path="app/x.py", line=line, ref=SHA, detail="change"),)

    def test_a_route_reached_twice_keeps_both_chains_once_each_and_the_first_extra(self):
        a = Python({SHA: {}})
        g = pr.Gathered()
        r = route()
        g.add(self.chain(), a._table(SHA), r, self.evidence(1), tag="first")
        g.add(self.chain(), a._table(SHA), r, self.evidence(2), tag="second")
        g.add(self.chain(), a._table(SHA), r, self.evidence(2), tag="third")  # the same chain again adds nothing
        (entry,) = g.found.values()
        self.assertEqual((entry["tag"], len(entry["chains"]), len(entry["sources"]), len(g.order)), ("first", 2, 2, 1))

    def test_a_chain_or_a_table_that_is_not_whole_makes_the_route_incomplete_with_each_reason_once(self):
        a = Python({SHA: {"complete": False, "reasons": ["table hole"]}, OTHER: {}})
        g = pr.Gathered()
        r = route()
        g.add(self.chain(SHA, True, ()), a._table(SHA), r, self.evidence(1))
        g.add(self.chain(OTHER, False, ("chain cut", "table hole")), a._table(OTHER), r, self.evidence(2))
        (entry,) = g.found.values()
        self.assertEqual((entry["complete"], entry["reasons"]), (False, ["table hole", "chain cut"]))

    def test_a_route_reached_through_whole_chains_and_tables_is_complete(self):
        a = Python({SHA: {}})
        g = pr.Gathered()
        g.add(self.chain(), a._table(SHA), route(), self.evidence(1))
        self.assertTrue(list(g.found.values())[0]["complete"])

    def test_settle_makes_one_area_per_route_ordered_by_label_and_file_and_calls_back_in_the_order_found(self):
        a = Python({SHA: {}})
        g = pr.Gathered()
        later, first = route("GET", "/b", "app/b.py", "hb"), route("GET", "/a", "app/a.py", "ha")
        g.add(self.chain(), a._table(SHA), later, self.evidence(1))
        g.add(self.chain(), a._table(SHA), first, self.evidence(2))
        seen: list[str] = []
        out = a.settle(g, [self.chain()], lambda entry: seen.append(entry["route"].path))
        self.assertEqual(seen, ["/b", "/a"])
        self.assertEqual([r.area.label for r in out], ["GET /a", "GET /b"])
        self.assertEqual([r.routes for r in out], [(first,), (later,)])
        self.assertEqual(out[0].area.kind, "api_route")
        self.assertEqual(out[0].area.chains, (self.evidence(2),))
        self.assertTrue(all(r.complete and r.reasons == () for r in out))

    def test_a_unit_nothing_mounts_is_recorded_unless_a_mount_could_not_be_read(self):
        records: list[Any] = []
        a = Python({SHA: {}}, record_to=records)
        g = pr.Gathered()
        g.unmounted_at(("app/x.py", "router"))
        g.unmounted_at(("app/x.py", "router"))
        a.settle(g, [self.chain()], lambda entry: None)
        (rec,) = records_of(records)
        self.assertEqual((rec.kind, rec.subject, rec.detail), ("router_unmounted", "app/x.py:router", "nothing mounts it"))
        self.assertEqual(a.problems, [])

        records2: list[Any] = []
        b = Python({SHA: {"unresolved": True}}, record_to=records2)
        b.settle(g, [self.chain()], lambda entry: None)
        self.assertEqual(records_of(records2), ())
        self.assertEqual(b.problems, ["app/x.py:router may be mounted by something unread"])


if __name__ == "__main__":
    unittest.main()
