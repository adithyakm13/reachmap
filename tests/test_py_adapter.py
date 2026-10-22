"""The Python adapter on small repositories built for one rule each.

The M1 scenarios are in test_scenarios_py.py. Here: changed symbols placed by line (nested scopes, class
members, decorators), every import form with resolution, the importer search (Shape A: no short list taken
for a whole one, parse problems and unresolved imports make a list incomplete), narrowing, callers, test
paths, definitions (Shape B), and that every Evidence resolves to its line. Trap numbers refer to
docs/CONTEXT.md section 5.
"""

import dataclasses
import difflib
import unittest
from unittest import mock

from reachmap import config
from reachmap.adapters import LanguageAdapter
from reachmap.adapters.python import PyAdapter, symbols_for_change
from reachmap.gitlayer import SearchResult
from reachmap.model import Analysis, Read
from tests import support_git as sg
from tests.support_universe import Base as UniverseBase
from tests.support_universe import f

MARKERS = ["tests/", "test_"]


class Base(UniverseBase):
    UNIVERSE_PREFIX = "reachmap-py-adapter-"
    REPO = "acme/api"

    def make_adapter(self, **kw):
        kw.setdefault("test_markers", MARKERS)
        return PyAdapter(self.REPO, self.url, self.git, **kw)

    adapter = make_adapter

    def importers_of(self, path, *, sha="main", **kw):
        return self.make_adapter(**kw).importers(path, self.sha(sha))

    def edge_summary(self, res):
        return [(e.importer_path, e.kind, e.names, e.evidence.line) for e in res.edges]


# ------------------------------------------------------------------------------ changed symbols


class PlacementOfChanges(unittest.TestCase):
    """Changed lines placed in symbols, with no repository: `symbols_for_change` is given the two texts and the
    line numbers of their diff (what git's hunks hold). The same rules through git, with the records and the
    evidence checked against the commits, are in ChangedSymbols below."""

    SHAS = {"main~1": "b" * 40, "main": "a" * 40}

    def sha(self, ref):
        return self.SHAS[ref]

    def symbols(self, before, after, **kw):
        a, b = before.split("\n"), after.split("\n")
        removed, added = [], []
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
            if tag in ("replace", "delete"):
                removed += range(i1 + 1, i2 + 1)
            if tag in ("replace", "insert"):
                added += range(j1 + 1, j2 + 1)
        return symbols_for_change("acme/api", "m.py", before, after, added, removed, before_sha=self.SHAS["main~1"], after_sha=self.SHAS["main"], **kw)

    def test_a_line_is_placed_by_its_number_not_by_its_text(self):
        """Two functions share an identical line; only the one that changed is reported (Shape B)."""
        before = f(["def a():", "    request = load()", "    return 1", "", "", "def b():", "    request = load()", "    return 2"])
        after = before.replace("return 2", "return 3")
        syms = self.symbols(before, after)
        self.assertEqual([(s.name, s.start, s.end, s.change, s.side) for s in syms], [("b", 6, 8, "modified", "after")])

    def test_the_identical_line_itself_changes_in_one_function_only(self):
        before = f(["def a(", "    request: Request,", "):", "    return 1", "", "", "def b(", "    request: Request,", "):", "    return 2"])
        after = before.replace("    request: Request,\n):\n    return 2", "    request: Request,\n    other: int,\n):\n    return 2")
        self.assertEqual([s.name for s in self.symbols(before, after)], ["b"])

    def test_added_and_removed_declarations(self):
        before = f(["def keep():", "    return 1", "", "", "def gone():", "    return 2"])
        after = f(["def keep():", "    return 1", "", "", "def fresh():", "    return 3"])
        got = {(s.name, s.change, s.side, s.exported, s.export_names, s.kind) for s in self.symbols(before, after)}
        self.assertEqual(got, {("gone", "removed", "before", True, ("gone",), "function"), ("fresh", "added", "after", True, ("fresh",), "function")})
        gone = next(s for s in self.symbols(before, after) if s.name == "gone")
        self.assertEqual((gone.start, gone.end), (5, 6))  # its span in the before text
        self.assertEqual(gone.evidence[0].ref, self.sha("main~1"))
        self.assertEqual([e.detail for e in gone.evidence], ["def gone():", "    return 2"])

    def test_a_removal_only_change_places_the_line_in_the_before_text(self):
        before = f(["def a():", "    step1()", "    step2()", "    return 1", "", "", "def b():", "    return 2"])
        after = f(["def a():", "    step1()", "    return 1", "", "", "def b():", "    return 2"])
        (s,) = self.symbols(before, after)
        self.assertEqual((s.name, s.change, s.side, s.removed_lines, s.added_lines), ("a", "modified", "after", (3,), ()))
        self.assertEqual((s.start, s.end), (1, 3))  # the span in the after text
        self.assertEqual([(e.ref, e.line, e.detail) for e in s.evidence], [(self.sha("main~1"), 3, "    step2()")])

    def test_an_import_line_change_is_a_module_level_change(self):
        before = f(["import os", "import sys", "", "", "def f():", "    return os"])
        after = before.replace("import sys\n", "")
        (s,) = self.symbols(before, after)
        self.assertEqual((s.name, s.kind, s.change, s.side, s.removed_lines), ("<module>", "module", "modified", "before", (2,)))

    def test_a_top_level_statement_change_is_module_level(self):
        before = f(["REGISTRY = {}", "setup()", "", "", "def f():", "    return 1"])
        after = before.replace("setup()", "setup(debug=True)")
        (s,) = self.symbols(before, after)
        self.assertEqual((s.name, s.added_lines, s.removed_lines), ("<module>", (2,), (2,)))

    def test_a_comment_inside_a_function_belongs_to_it(self):
        before = f(["def a():", "    x = 1", "    return x"])
        after = f(["def a():", "    # now explained", "    x = 1", "    return x"])
        self.assertEqual([(s.name, s.added_lines) for s in self.symbols(before, after)], [("a", (2,))])

    def test_a_multi_line_decorator_argument_is_the_function_below_it(self):
        """s09 shape: the argument is attributed to the function the decorator decorates."""
        before = f(["def _log(m):", "    print(m)", "", "", "@scheduled(", "    name='x',", "    retries=3,", ")", "def run():", "    _log('go')"])
        after = before.replace("retries=3", "retries=5")
        (s,) = self.symbols(before, after)
        self.assertEqual((s.name, s.start, s.end), ("run", 5, 10))
        self.assertEqual([e.detail for e in s.evidence], ["    retries=5,", "    retries=3,"])

    def test_nested_functions_are_named_in_nested_and_a_sibling_with_the_same_name_is_unrelated(self):
        before = f(["def one():", "    def _load():", "        return 1", "    return _load()", "", "", "def two():", "    def _load():", "        return 2", "    return _load()"])
        after = before.replace("        return 2", "        return 22")
        (s,) = self.symbols(before, after)
        self.assertEqual((s.name, s.nested), ("two", ("_load",)))

    def test_class_members_are_dotted_and_what_a_new_class_holds_is_one_symbol(self):
        before = f(["class Invoice(Base):", "    id: str", "    reference: str | None = None", "", "    def total(self):", "        return 1"])
        after = f(["class Invoice(Base):", "    id: str", "    reference: str", "", "    def total(self):", "        return 2", "", "", "class Fresh(Base):", "    name: str", "    count: int = 0"])
        got = [(s.name, s.kind, s.change, s.nested, (s.start, s.end)) for s in self.symbols(before, after)]
        self.assertEqual(got, [
            ("Invoice.reference", "variable", "modified", (), (1, 6)),
            ("Invoice.total", "function", "modified", (), (1, 6)),
            ("Fresh", "class", "added", (), (9, 11)),
        ])
        invoice = self.symbols(before, after)[0]
        self.assertEqual(invoice.export_names, ("Invoice",))  # an importer imports the class

    def test_a_new_field_in_an_existing_class_modifies_the_class_and_a_removed_class_is_one_symbol(self):
        before = f(["class A(Base):", "    x: int", "", "", "class B(Base):", "    y: int", "    z: int"])
        after = f(["class A(Base):", "    x: int", "    w: int"])
        got = [(s.name, s.change, s.side) for s in self.symbols(before, after)]
        self.assertEqual(got, [("A.w", "modified", "after"), ("B", "removed", "before")])

    def test_async_defs_lambdas_and_module_assignments(self):
        before = f(["async def fetch():", "    return 1", "", "", "double = lambda v: v * 2", "RATES = {", "    'EUR': 1.0,", "}"])
        after = before.replace("return 1", "return 2").replace("v * 2", "v * 3").replace("1.0", "1.1")
        got = [(s.name, s.kind) for s in self.symbols(before, after)]
        self.assertEqual(got, [("fetch", "function"), ("double", "function"), ("RATES", "variable")])

    def test_a_multi_line_signature_and_a_changed_default_belong_to_the_function(self):
        before = f(["def f(", "    a,", "    b=1,", "):", "    return a"])
        after = before.replace("b=1", "b=2")
        self.assertEqual([(s.name, s.added_lines) for s in self.symbols(before, after)], [("f", (3,))])

    def test_crlf_files_place_lines_and_cite_them_without_the_carriage_return(self):
        before = "def a():\r\n    return 1\r\n\r\n\r\ndef b():\r\n    return 2\r\n"
        after = before.replace("return 2", "return 3")
        (s,) = self.symbols(before, after)
        self.assertEqual((s.name, [e.detail for e in s.evidence]), ("b", ["    return 3", "    return 2"]))

    def test_export_names_cover_every_name_an_assignment_binds(self):
        before = f(["a, b = 1, 2", "", "", "def f():", "    return a"])
        after = before.replace("1, 2", "3, 4")
        (s,) = self.symbols(before, after)
        self.assertEqual((s.name, s.export_names), ("a", ("a", "b")))

    def test_comments_blank_lines_and_docstrings_between_declarations_change_nothing(self):
        before = f(['"""Module docs."""', "", "def a():", "    return 1", "", "# note", "", "def b():", "    return 2"])
        after = f(['"""Module docs, longer."""', "", "def a():", "    return 1", "", "", "# a different note", "", "# and another", "def b():", "    return 2"])
        self.assertEqual(self.symbols(before, after), [])

    def test_without_a_pre_change_version_removals_are_not_computed(self):
        before = f(["def gone():", "    return 1", "", "", "def keep():", "    return 2"])
        after = f(["def keep():", "    return 3"])
        syms = symbols_for_change("acme/api", "m.py", None, after, [2], [], before_sha=None, after_sha=self.SHAS["main"])
        self.assertEqual([(s.name, s.change, s.removed_lines) for s in syms], [("keep", "modified", ())])

    def test_an_added_file_reports_every_declaration_as_added_and_its_imports_as_module_level(self):
        after = f(["import os", "", "", "def f():", "    return os", "", "", "class K:", "    x: int = 1"])
        syms = symbols_for_change("acme/api", "m.py", None, after, range(1, 10), [], before_sha=None, after_sha=self.SHAS["main"], status="added")
        self.assertEqual({(s.name, s.change) for s in syms}, {("f", "added"), ("K", "added"), ("<module>", "modified")})


class ChangedSymbols(Base):

    def test_comments_blank_lines_and_docstrings_between_declarations_change_nothing(self):
        before = f(['"""Module docs."""', "", "def a():", "    return 1", "", "# note", "", "def b():", "    return 2"])
        after = f(['"""Module docs, longer."""', "", "def a():", "    return 1", "", "", "# a different note", "", "# and another", "def b():", "    return 2"])
        self.repo({"files": {"m.py": before}}, {"files": {"m.py": after}})
        self.analyze()
        impact = self.impact("m.py")
        self.assertEqual(impact.symbols, ())
        self.assertEqual((impact.importers, impact.callers, impact.importers_complete), ((), (), True))

    def test_without_a_pre_change_version_removals_are_not_computed_and_nothing_is_recorded(self):
        before = f(["def gone():", "    return 1", "", "", "def keep():", "    return 2"])
        after = f(["def keep():", "    return 3"])
        self.repo({"files": {"m.py": before}}, {"files": {"m.py": after}})
        self.analyze(pre=None)
        syms = self.impact("m.py").symbols
        self.assertTrue(all(s.change != "removed" for s in syms))
        self.assertTrue(all(not s.removed_lines for s in syms))
        self.assertEqual([r for r in self.records if r.kind == "pre_change_unavailable"], [])  # the change set records it, once
        self.assertFalse(self.impact("m.py").narrowed)

    def test_a_deleted_file_reports_its_declarations_as_removed_and_follows_its_importers(self):
        self.repo({"files": {"pkg/m.py": f(["def a():", "    pass", "", "", "def b():", "    pass"]), "pkg/u.py": "from pkg.m import a\n\na()\n"}},
                  {"delete": ["pkg/m.py"]})
        self.analyze()
        impact = self.impact("pkg/m.py")
        self.assertEqual({(s.name, s.change, s.side) for s in impact.symbols}, {("a", "removed", "before"), ("b", "removed", "before")})
        self.assertEqual([c.path for c in impact.callers], ["pkg/u.py"])  # who imported it before the change is followed too

    def test_an_added_file_reports_every_declaration_as_added_and_its_imports_as_module_level(self):
        self.repo({"files": {"a.py": "x = 1\n"}}, {"files": {"new.py": f(["import os", "", "", "def f():", "    return os", "", "", "class K:", "    x: int = 1"])}})
        self.analyze()
        got = {(s.name, s.change) for s in self.impact("new.py").symbols}
        self.assertEqual(got, {("f", "added"), ("K", "added"), ("<module>", "modified")})

    def test_a_renamed_and_edited_file_is_read_at_its_old_path(self):
        body = f(["def a():", "    return 1", "", "", "def b():", "    return 2", "", "# padding to keep the rename similar", "# more", "# more"])
        self.repo({"files": {"pkg/old.py": body}}, {"delete": ["pkg/old.py"], "files": {"pkg/new.py": body.replace("return 2", "return 3")}})
        cs = self.change()
        (fc,) = [x for x in cs.files if x.status == "renamed"]
        syms = self.adapter().changed_symbols(cs, fc)
        self.assertEqual([(s.name, s.path) for s in syms], [("b", "pkg/new.py")])

    def test_a_pure_rename_removes_the_module_at_its_old_path(self):
        body = f(["def a():", "    return 1", "# padding to keep the rename exact"] * 3)
        self.repo({"files": {"pkg/old.py": body, "pkg/u.py": "from pkg.old import a\n\na()\n"}}, {"delete": ["pkg/old.py"], "files": {"pkg/new.py": body}})
        self.analyze()
        impact = self.impact("pkg/new.py")
        self.assertEqual([(s.name, s.change, s.side) for s in impact.symbols], [("<module>", "removed", "before")])
        self.assertEqual([c.path for c in impact.callers], ["pkg/u.py"])  # importers of the old path

    def test_a_non_python_file_has_no_symbols_and_no_importer_search(self):
        self.repo({"files": {"README.md": "a\n", "pkg/a.py": "x = 1\n", "requirements.txt": "x==1\n"}}, {"files": {"README.md": "b\n", "requirements.txt": "x==2\n"}})
        self.analyze()
        for path in ("README.md", "requirements.txt"):
            impact = self.impact(path)
            self.assertEqual((impact.symbols, impact.importers, impact.importers_complete), ((), (), True))

    def test_an_unreadable_file_makes_a_module_level_change_and_a_record(self):
        self.repo({"files": {"m.py": "x = 1\n"}}, {"files": {"m.py": b"x = '\xe9'\n"}})
        self.analyze()
        (s,) = self.impact("m.py").symbols
        self.assertEqual((s.name, s.change), ("<module>", "modified"))
        self.assertEqual(self.kinds(), [("read_unreadable", True)])

    def test_a_parse_problem_in_the_changed_file_makes_every_changed_line_module_level_with_a_record(self):
        """Python 2 source: nothing can be trusted to sit in a function, so the change reaches every importer."""
        before = f(["def a():", "    print 'one'", "", "", "def b():", "    print 'two'"])
        after = before.replace("'two'", "'three'")
        self.repo({"files": {"m.py": before, "u.py": "from m import b\n\nb()\n"}}, {"files": {"m.py": after}})
        self.analyze()
        (s,) = self.impact("m.py").symbols
        self.assertEqual((s.name, s.added_lines, s.removed_lines), ("<module>", (6,), (6,)))
        self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in self.records], [("parse_degraded", "m.py", False)])
        impact = self.impact("m.py")
        self.assertFalse(impact.narrowed)
        self.assertEqual([c.path for c in impact.callers], ["u.py"])

    def test_a_parse_problem_in_the_before_text_alone_is_enough(self):
        before = f(["def a():", "    print 'one'"])
        after = f(["def a():", "    print('one')"])
        self.repo({"files": {"m.py": before}}, {"files": {"m.py": after}})
        self.analyze()
        (s,) = self.impact("m.py").symbols
        self.assertEqual((s.name, s.change), ("<module>", "modified"))
        self.assertEqual([r.kind for r in self.records], ["parse_degraded"])

    def test_python_that_this_version_cannot_parse_is_a_parse_problem_not_a_guess(self):
        import sys
        if sys.version_info >= (3, 14):
            self.skipTest("t-strings parse on this Python")
        self.repo({"files": {"m.py": "def a():\n    return 1\n"}}, {"files": {"m.py": 'def a():\n    return t"{x}"\n'}})
        self.analyze()
        (s,) = self.impact("m.py").symbols
        self.assertEqual(s.name, "<module>")
        self.assertEqual([r.kind for r in self.records], ["parse_degraded"])

    def test_a_lone_carriage_return_file_is_a_parse_problem(self):
        self.repo({"files": {"m.py": b"x = 1\ry = 2\r"}}, {"files": {"m.py": b"x = 1\ry = 3\r"}})
        self.analyze()
        self.assertEqual([s.name for s in self.impact("m.py").symbols], ["<module>"])
        self.assertEqual([r.kind for r in self.records], ["parse_degraded"])


# ------------------------------------------------------------------------------ imports and resolution


TARGET = f(["def f():", "    return 1", "", "", "def g():", "    return 2"])


class ImportForms(Base):
    """Rule 3: every import form, resolved and cited at its line."""

    FILES = {
        "pkg/__init__.py": "",
        "pkg/target.py": TARGET,
        "pkg/sub/__init__.py": "",
        "pkg/sub/rel.py": f(["from .. import target", "from ..target import f as ff", "from . import sibling", "target.f()"]),
        "pkg/sub/sibling.py": "x = 1\n",
        "pkg/sub/dots.py": "from ...pkg import target\n",
        "use_plain.py": f(["import pkg.target", "", "pkg.target.f()"]),
        "use_alias.py": f(["import pkg.target as t", "", "t.f()"]),
        "use_from.py": f(["from pkg.target import f", "", "f()"]),
        "use_multi.py": f(["from pkg.target import (", "    f,", "    g as gg,", ")", "", "f()"]),
        "use_from_alias.py": f(["from pkg.target import g as other", "", "other()"]),
        "use_star.py": f(["from pkg.target import *", "", "f()"]),
        "use_submodule.py": f(["from pkg import target", "", "target.g()"]),
        "use_submodule_alias.py": f(["from pkg import target as tg, sub", "", "tg.g()"]),
        "use_in_function.py": f(["def run():", "    from pkg.target import f", "    return f()"]),
        "use_type_checking.py": f(["from typing import TYPE_CHECKING", "", "if TYPE_CHECKING:", "    from pkg.target import f", "", "", "def run(x: 'f'):", "    pass"]),
        "use_dynamic.py": f(["import importlib", "", "mod = importlib.import_module('pkg.target')"]),
        "use_dynamic_builtin.py": "mod = __import__('pkg.target')\n",
        "use_two.py": f(["import pkg.target", "from pkg.target import g"]),
        "use_other.py": f(["from pkg.target_other import f", "import pkg"]),
        "pkg/target_other.py": "def f():\n    pass\n",
    }

    def setUp(self):
        super().setUp()
        self.use_shared("forms", {"files": dict(self.FILES)})
        self.res = self.make_adapter().importers("pkg/target.py", self.sha("main"))

    def edges(self, importer):
        return [e for e in self.res.edges if e.importer_path == importer]

    def test_the_importers_are_exactly_the_files_that_resolve_to_the_target(self):
        self.assertEqual(self.res.paths, (
            "pkg/sub/dots.py", "pkg/sub/rel.py", "use_alias.py", "use_dynamic.py", "use_dynamic_builtin.py", "use_from.py", "use_from_alias.py",
            "use_in_function.py", "use_multi.py", "use_plain.py", "use_star.py", "use_submodule.py", "use_submodule_alias.py", "use_two.py",
            "use_type_checking.py",
        ))
        self.assertTrue(self.res.complete)

    def test_import_a_b_is_a_whole_module_import_bound_through_its_first_segment(self):
        (e,) = self.edges("use_plain.py")
        self.assertEqual((e.kind, e.names, e.specifier, e.evidence.line, e.target), ("import", None, "pkg.target", 1, "pkg/target.py"))
        self.assertEqual([(b.imported, b.local) for b in e.bindings], [("pkg.target", "pkg")])

    def test_import_a_b_as_x(self):
        (e,) = self.edges("use_alias.py")
        self.assertEqual((e.names, e.evidence.line, [(b.imported, b.local) for b in e.bindings]), (None, 1, [("*", "t")]))

    def test_from_a_b_import_c_and_with_an_alias(self):
        (e,) = self.edges("use_from.py")
        self.assertEqual((e.names, e.evidence.line, [(b.imported, b.local) for b in e.bindings]), (("f",), 1, [("f", "f")]))
        (e,) = self.edges("use_from_alias.py")
        self.assertEqual([(b.imported, b.local) for b in e.bindings], [("g", "other")])

    def test_a_multi_line_import_cites_the_module_line_and_each_name_line(self):
        (e,) = self.edges("use_multi.py")
        self.assertEqual((e.names, e.evidence.line, e.evidence.detail), (("f", "g"), 1, "from pkg.target import ("))
        self.assertEqual([(b.imported, b.local, b.line) for b in e.bindings], [("f", "f", 2), ("g", "gg", 3)])

    def test_star_imports_are_whole_module_imports_of_kind_export_star(self):
        (e,) = self.edges("use_star.py")
        self.assertEqual((e.kind, e.names, e.bindings), ("export_star", None, ()))

    def test_from_a_package_import_a_submodule_imports_that_submodule(self):
        (e,) = self.edges("use_submodule.py")
        self.assertEqual((e.names, e.target, [(b.imported, b.local) for b in e.bindings]), (None, "pkg/target.py", [("*", "target")]))
        (e,) = self.edges("use_submodule_alias.py")
        self.assertEqual([(b.imported, b.local, b.line) for b in e.bindings], [("*", "tg", 1)])

    def test_relative_imports_resolve_against_the_importers_package(self):
        got = sorted((e.evidence.line, e.specifier, e.names) for e in self.edges("pkg/sub/rel.py"))
        self.assertEqual(got, [(1, "..", None), (2, "..target", ("f",))])
        got = [(e.evidence.line, e.specifier) for e in self.edges("pkg/sub/dots.py")]
        self.assertEqual(got, [(1, "...pkg")])  # three dots from pkg/sub/dots.py is the repo root: pkg is found there

    def test_imports_inside_functions_and_type_checking_blocks_are_importers_and_type_only_is_marked(self):
        (e,) = self.edges("use_in_function.py")
        self.assertEqual((e.evidence.line, e.type_only), (2, False))
        (e,) = self.edges("use_type_checking.py")
        self.assertEqual((e.evidence.line, e.type_only, [b.type_only for b in e.bindings]), (4, True, [True]))

    def test_a_literal_import_module_and_dunder_import_are_dynamic_edges(self):
        (e,) = self.edges("use_dynamic.py")
        self.assertEqual((e.kind, e.names, e.evidence.line, e.bindings), ("dynamic", None, 3, ()))
        (e,) = self.edges("use_dynamic_builtin.py")
        self.assertEqual((e.kind, e.evidence.line), ("dynamic", 1))

    def test_two_imports_of_one_file_are_two_edges(self):
        self.assertEqual([(e.evidence.line, e.names) for e in self.edges("use_two.py")], [(1, None), (2, ("g",))])

    def test_a_similar_name_is_not_an_importer(self):
        self.assertEqual(self.edges("use_other.py"), [])
        self.assertEqual(self.edges("pkg/target_other.py"), [])

    def test_text_in_a_comment_or_string_is_not_an_import(self):
        self.repo({"files": {"pkg/target.py": TARGET, "c.py": f(["# from pkg.target import f", "TEXT = '''", "import pkg.target", "'''", "x = 'from pkg.target import g'"])}})
        self.assertEqual(self.importers_of("pkg/target.py").edges, ())

    def test_a_submodule_import_is_also_an_import_of_the_package_init_by_name(self):
        res = self.make_adapter().importers("pkg/__init__.py", self.sha("main"))
        got = {(e.importer_path, e.names, e.evidence.line) for e in res.edges}
        self.assertIn(("use_submodule.py", ("target",), 1), got)
        self.assertIn(("use_submodule_alias.py", ("target", "sub"), 1), got)
        self.assertIn(("use_other.py", None, 2), got)  # import pkg
        self.assertIn(("pkg/sub/rel.py", ("target",), 1), got)  # from .. import target


# ------------------------------------------------------------------------------ the importer search


class ImporterSearch(Base):
    def test_no_importers_is_a_complete_empty_answer(self):
        self.repo({"files": {"lonely.py": "x = 1\n", "other.py": "y = 2\n"}})
        res = self.importers_of("lonely.py")
        self.assertEqual((res.edges, res.complete, res.reasons), ((), True, ()))

    def test_relative_importers_are_found_without_the_package_name_in_the_text(self):
        """Trap: importer search covered alias imports only and so missed every relative import."""
        self.repo({"files": {"pkg/__init__.py": "", "pkg/money.py": "def f():\n    pass\n", "pkg/user.py": "from .money import f\n\nf()\n", "pkg/user2.py": "from . import money\n"}})
        res = self.importers_of("pkg/money.py")
        self.assertEqual(res.paths, ("pkg/user.py", "pkg/user2.py"))

    def test_an_init_file_is_reached_by_dot_and_dot_dot_which_do_not_name_the_package(self):
        self.repo({"files": {"pkg/__init__.py": "VALUE = 1\n", "pkg/a.py": "from . import VALUE\n", "pkg/sub/__init__.py": "", "pkg/sub/b.py": "from .. import VALUE\n"}})
        res = self.importers_of("pkg/__init__.py")
        self.assertEqual(res.paths, ("pkg/a.py", "pkg/sub/b.py"))

    def test_importers_are_read_at_the_pinned_commit_not_the_branch_tip(self):
        self.repo({"files": {"m.py": "x = 1\n", "a.py": "import m\n"}}, {"files": {"b.py": "import m\n"}})
        a = self.make_adapter()
        self.assertEqual(a.importers("m.py", self.sha("main~1")).paths, ("a.py",))
        self.assertEqual(a.importers("m.py", self.sha("main")).paths, ("a.py", "b.py"))

    def test_an_examine_cap_passes_through_and_the_list_is_marked_incomplete(self):
        """Trap: a search examined a small fraction of the matches and listed that fraction as the consumers."""
        files = {f"u{i:02d}.py": "import m\n" for i in range(12)}
        files["m.py"] = "x = 1\n"
        self.repo({"files": files})
        res = self.importers_of("m.py", examine_cap=6)
        self.assertFalse(res.complete)
        self.assertIn("search_truncated", [r.kind for r in self.records])
        self.assertTrue(all(r.blocks_selection for r in self.records if r.kind == "search_truncated"))
        self.assertLess(len(res.paths), 12)
        self.assertTrue(any("search_truncated" in r for r in res.reasons))
        self.assertTrue(self.make_adapter().importers("m.py", self.sha("main")).complete)

    def test_a_cap_that_finds_nothing_is_still_incomplete_not_empty(self):
        files = {f"a{i:02d}.py": "x = 1\n" for i in range(10)}
        files.update({"z_imp.py": "import m\n", "m.py": "y = 1\n"})
        self.repo({"files": files})
        res = self.importers_of("m.py", examine_cap=3)
        self.assertFalse(res.complete)
        self.assertEqual(res.edges, ())

    def test_an_unreadable_grep_is_an_incomplete_list_not_an_empty_one(self):
        self.repo({"files": {"m.py": "x = 1\n", "a.py": "import m\n"}})
        a = self.make_adapter()
        unreadable = SearchResult(Read.UNREADABLE, "m", self.sha("main"), (), None, 0, None, "git failed")
        with mock.patch.object(self.git, "grep", return_value=unreadable):
            res = a.importers("m.py", self.sha("main"))
        self.assertFalse(res.complete)
        self.assertEqual(res.edges, ())
        self.assertTrue(res.reasons)

    def test_an_unreadable_candidate_file_makes_the_list_incomplete_and_is_recorded(self):
        self.repo({"files": {"m.py": "x = 1\n", "a.py": "import m\n", "z_latin1.py": b"import m  # caf\xe9\n"}})
        res = self.importers_of("m.py")
        self.assertFalse(res.complete)
        self.assertEqual(res.paths, ("a.py",))
        self.assertEqual([(r.kind, r.blocks_selection) for r in self.records], [("read_unreadable", True)])
        self.assertIn("z_latin1.py", self.records[0].subject)

    def test_a_candidate_that_does_not_parse_makes_the_list_incomplete_and_blocks(self):
        """Python 2 print in a candidate: an import may be hidden in it."""
        self.repo({"files": {"m.py": "x = 1\n", "a.py": "import m\n", "old.py": "import m\nprint 'hello'\n"}})
        res = self.importers_of("m.py")
        self.assertFalse(res.complete)
        self.assertEqual(res.paths, ("a.py",))  # no regex fallback: nothing is read from the file that did not parse
        self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in self.records], [("parse_degraded", "old.py", True)])

    def test_an_import_with_an_expression_blocks_the_list(self):
        self.repo({"files": {"m.py": "x = 1\n", "a.py": "import importlib\n\n\ndef load(name):\n    return importlib.import_module(name)\n\n\nm = importlib.import_module('m')\n"}})
        res = self.importers_of("m.py")
        self.assertFalse(res.complete)
        self.assertEqual([e.evidence.line for e in res.edges], [8])  # the literal one is still an importer
        self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in self.records], [("specifier_unresolved", "a.py", True)])

    def test_an_import_that_names_the_file_but_resolves_nowhere_blocks_once_per_prefix(self):
        """Trap: reverse alias lookup missed importers. Here a package this adapter cannot map."""
        self.repo({"files": {
            "svc/totals.py": "x = 1\n",
            "svc/a.py": "from vendor.pkg.totals import x\n",
            "svc/b.py": "from vendor.pkg.totals import y\n",
            "svc/c.py": "from other import totals\n",
            "svc/d.py": "import totals\n",
        }})
        res = self.importers_of("svc/totals.py")
        self.assertFalse(res.complete)
        self.assertEqual(sorted((r.kind, r.subject, r.blocks_selection) for r in self.records),
                         [("specifier_unresolved", "other", True), ("specifier_unresolved", "totals", True), ("specifier_unresolved", "vendor.pkg", True)])

    def test_a_module_called_safe_does_not_trip_the_banned_wording_check_of_a_record(self):
        """The detail of a record is checked for banned wording as written; quoted code must not crash the run."""
        self.repo({"files": {"svc/totals.py": "x = 1\n", "svc/a.py": "from vendor.safe.totals import x\n"}})
        res = self.importers_of("svc/totals.py")
        self.assertFalse(res.complete)
        (rec,) = self.records
        self.assertEqual((rec.kind, rec.subject, rec.blocks_selection), ("specifier_unresolved", "vendor.safe", True))
        self.assertNotIn("safe", rec.detail)

    def test_external_and_stdlib_imports_that_do_not_name_the_file_are_not_reported(self):
        self.repo({"files": {"m.py": "x = 1\n", "a.py": "import os\nimport json\nfrom fastapi import FastAPI\nimport m\nfrom . import nothing\nfrom .gone import y\n"}})
        res = self.importers_of("m.py")
        self.assertTrue(res.complete)
        self.assertEqual(self.records, [])

    def test_a_standard_library_module_with_the_files_name_is_not_a_miss(self):
        self.repo({"files": {"lib/json.py": "x = 1\n", "a.py": "import json\n"}})
        res = self.importers_of("lib/json.py")
        self.assertTrue(res.complete)  # nothing in the repository resolves it, and `json` is the standard library's
        self.assertEqual((res.edges, self.records), ((), []))

    def test_a_repository_module_that_shadows_the_standard_library_is_resolved(self):
        self.repo({"files": {"json.py": "x = 1\n", "a.py": "import json\n"}})
        # `import json` from a.py finds json.py at the repository root: it is an importer
        self.assertEqual(self.importers_of("json.py").paths, ("a.py",))

    def test_a_specifier_that_resolves_to_another_file_is_not_reported(self):
        self.repo({"files": {"pkg/__init__.py": "", "pkg/totals.py": "x = 1\n", "other/__init__.py": "", "other/totals.py": "x = 2\n", "a.py": "from other.totals import x\n"}})
        res = self.importers_of("pkg/totals.py")
        self.assertTrue(res.complete)
        self.assertEqual((res.edges, self.records), ((), []))

    def test_a_relative_import_beyond_the_top_level_is_recorded_and_does_not_crash(self):
        self.repo({"files": {"m.py": "x = 1\n", "pkg/__init__.py": "", "pkg/a.py": "from ... import y\nfrom ...zzz import w\nimport m\n"}})
        res = self.importers_of("m.py")
        self.assertEqual(res.paths, ("pkg/a.py",))
        self.assertTrue(res.complete)
        self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in self.records], [("specifier_unresolved", "pkg/a.py", False)])

    def test_a_relative_import_beyond_the_top_level_that_names_the_file_blocks(self):
        self.repo({"files": {"m.py": "x = 1\n", "a.py": "from .. import m\n"}})
        res = self.importers_of("m.py")
        self.assertFalse(res.complete)
        self.assertIn(("specifier_unresolved", True), self.kinds())

    def test_two_files_with_the_same_module_name_are_both_kept_and_recorded(self):
        """Ambiguity (an importer outside every service sees both) keeps both and says so."""
        files = {"services/a/pyproject.toml": "[project]\nname='a'\n", "services/a/app/__init__.py": "", "services/a/app/main.py": "x = 1\n",
                 "services/b/pyproject.toml": "[project]\nname='b'\n", "services/b/app/__init__.py": "", "services/b/app/main.py": "y = 2\n",
                 "tools/run.py": "import app.main\n"}
        self.repo({"files": files})
        a = self.make_adapter(service_marker="pyproject.toml")
        res_a = a.importers("services/a/app/main.py", self.sha("main"))
        res_b = a.importers("services/b/app/main.py", self.sha("main"))
        self.assertEqual((res_a.paths, res_b.paths), (("tools/run.py",), ("tools/run.py",)))
        self.assertEqual(sorted((r.kind, r.subject, r.blocks_selection) for r in self.records), [("module_ambiguous", "app.main", False)])

    def test_each_service_resolves_its_own_app_package(self):
        files = {"services/a/pyproject.toml": "[project]\nname='a'\n", "services/a/app/__init__.py": "", "services/a/app/main.py": "x = 1\n", "services/a/app/use.py": "from app.main import x\n",
                 "services/b/pyproject.toml": "[project]\nname='b'\n", "services/b/app/__init__.py": "", "services/b/app/main.py": "y = 2\n", "services/b/app/use.py": "from app.main import y\n"}
        self.repo({"files": files})
        a = self.make_adapter(service_marker="pyproject.toml")
        self.assertEqual(a.importers("services/a/app/main.py", self.sha("main")).paths, ("services/a/app/use.py",))
        self.assertEqual(a.importers("services/b/app/main.py", self.sha("main")).paths, ("services/b/app/use.py",))
        self.assertEqual(self.records, [])

    def test_a_library_root_is_importable_from_every_service(self):
        files = {"libs/common/pyproject.toml": "[project]\nname='c'\n", "libs/common/acme_common/__init__.py": "", "libs/common/acme_common/money.py": "def f():\n    pass\n",
                 "services/a/pyproject.toml": "[project]\nname='a'\n", "services/a/app/__init__.py": "", "services/a/app/use.py": "from acme_common.money import f\n",
                 "services/b/pyproject.toml": "[project]\nname='b'\n", "services/b/app/use.py": "import acme_common.money as m\n"}
        self.repo({"files": files})
        a = self.make_adapter(service_marker="pyproject.toml", library_roots=("libs/common",))
        self.assertEqual(a.importers("libs/common/acme_common/money.py", self.sha("main")).paths, ("services/a/app/use.py", "services/b/app/use.py"))

    def test_a_namespace_package_without_init_resolves(self):
        self.repo({"files": {"ns/sub/mod.py": "x = 1\n", "use.py": "from ns.sub import mod\nimport ns.sub.mod as m2\n"}})
        res = self.importers_of("ns/sub/mod.py")
        self.assertEqual([(e.evidence.line, e.names) for e in res.edges], [(1, None), (2, None)])

    def test_a_package_dir_maps_the_source_folder_to_the_import_name(self):
        """The contracts package: folder contracts/, import name acme_contracts, from pyproject.toml."""
        files = {"pyproject.toml": '[tool.setuptools]\npackage-dir = { acme_contracts = "contracts" }\n', "contracts/__init__.py": "", "contracts/models/__init__.py": "",
                 "contracts/models/invoice.py": "class Invoice:\n    pass\n", "tests/test_it.py": "from acme_contracts.models.invoice import Invoice\n"}
        self.repo({"files": files})
        res = self.importers_of("contracts/models/invoice.py")
        self.assertEqual((res.paths, res.complete), (("tests/test_it.py",), True))
        self.assertEqual(self.records, [])

    def test_a_pyproject_that_does_not_parse_makes_every_list_incomplete(self):
        self.repo({"files": {"pyproject.toml": "[tool.setuptools\n", "m.py": "x = 1\n", "a.py": "import m\n"}})
        res = self.importers_of("m.py")
        self.assertFalse(res.complete)
        self.assertEqual(res.paths, ("a.py",))
        self.assertIn(("read_unreadable", True), self.kinds())

    def test_a_symlinked_python_file_is_not_searched_and_the_list_says_so(self):
        repo, shas = sg.make_raw_repo(self.tmp, "links", [[
            ("100644", b"a.py", b"import m\n"), ("120000", b"b.py", b"a.py"), ("100644", b"m.py", b"x = 1\n"), ("120000", b"c.md", b"README.md"),
        ]])
        self.path, self.url = repo, sg.remote_url(repo)
        self.records = []
        from reachmap.gitlayer import Git
        self.git = Git(self.tmp / "cache", env=sg.isolated_env(self.tmp / "home"), record_to=self.records)
        res = self.make_adapter().importers("m.py", shas[-1])
        self.assertEqual(res.paths, ("a.py",))
        self.assertFalse(res.complete)
        self.assertTrue(any("b.py" in r and "symlink" in r for r in res.reasons), res.reasons)
        self.assertEqual([(r.kind, r.blocks_selection) for r in self.records], [("read_unreadable", True)])

    def test_a_candidate_whose_name_cannot_be_cited_makes_the_list_incomplete_and_is_recorded(self):
        self.repo({"files": {"m.py": "x = 1\n", "a.py": "import m\n", "we\\ird.py": "import m\n"}})
        res = self.importers_of("m.py")
        self.assertEqual(res.paths, ("a.py",))
        self.assertFalse(res.complete)
        self.assertEqual([(r.kind, r.blocks_selection) for r in self.records], [("read_unreadable", True)])

    def test_a_changed_file_whose_name_cannot_be_cited_is_recorded_not_dropped(self):
        self.repo({"files": {"we\\ird.py": "x = 1\n"}}, {"files": {"we\\ird.py": "x = 2\n"}})
        self.analyze()
        impact = self.impact("we\\ird.py")
        self.assertEqual((impact.symbols, impact.importers_complete), ((), False))
        self.assertEqual([r.kind for r in self.records], ["read_unreadable"])

    def test_records_go_to_an_analysis_when_one_is_given(self):
        files = {f"u{i:02d}.py": "import m\n" for i in range(12)}
        files["m.py"] = "x = 1\n"
        self.repo({"files": files})
        analysis = Analysis(change={"kind": "compare"}, ref=self.sha("main"))
        res = self.make_adapter(examine_cap=6).importers("m.py", self.sha("main"), record_to=analysis)
        self.assertFalse(res.complete)
        self.assertEqual({r.kind for r in analysis.incompleteness}, {"search_truncated"})
        self.assertEqual(self.records, [])

    def test_a_module_level_change_to_an_init_file_says_what_it_cannot_list(self):
        """Importing a submodule runs the parent packages' __init__ without naming them: a stated limit."""
        self.repo({"files": {"pkg/__init__.py": "VALUE = 1\n", "pkg/a.py": "x = 1\n", "use.py": "import pkg.a\n"}}, {"files": {"pkg/__init__.py": "VALUE = 2\n"}})
        self.analyze()
        self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in self.records], [("package_init_implicit", "pkg/__init__.py", False)])
        self.assertEqual(self.impact("pkg/__init__.py").importers, ())  # `import pkg.a` does not name the package

    def test_the_hub_rule_stops_the_walk_and_records_a_stated_limit(self):
        files = {"lib/format.py": "def truncate(t):\n    return t\n"}
        files.update({f"w{i:02d}.py": "from lib.format import truncate\n\ntruncate('x')\n" for i in range(7)})
        files["tests/test_format.py"] = "from lib.format import truncate\n"
        self.repo({"files": files}, {"files": {"lib/format.py": "def truncate(t):\n    return t + ''\n"}})
        self.analyze(adapter=self.make_adapter(hub_importers=5))
        impact = self.impact("lib/format.py")
        self.assertTrue(impact.hub)
        self.assertEqual(impact.importer_count, 7)
        self.assertEqual((impact.callers, impact.exclusions), ((), ()))
        self.assertEqual([t.path for t in impact.unit_tests], ["tests/test_format.py"])  # tests that import the hub are still listed
        self.assertEqual(self.kinds(), [("hub_cutoff", False)])
        self.assertEqual(self.records[0].subject, "lib/format.py")

    def test_at_the_hub_limit_the_walk_goes_on(self):
        files = {"lib/format.py": "def truncate(t):\n    return t\n"}
        files.update({f"w{i}.py": "from lib.format import truncate\n\ntruncate('x')\n" for i in range(5)})
        self.repo({"files": files}, {"files": {"lib/format.py": "def truncate(t):\n    return t + ''\n"}})
        self.analyze(adapter=self.make_adapter(hub_importers=5))
        impact = self.impact("lib/format.py")
        self.assertFalse(impact.hub)
        self.assertEqual(len(impact.callers), 5)
        self.assertEqual(self.kinds(), [])

    def test_the_dotted_names_of_a_file_are_public(self):
        self.repo({"files": {"pkg/__init__.py": "", "pkg/a.py": "x = 1\n"}})
        self.assertEqual(self.make_adapter().names_for("pkg/a.py", self.sha("main")), ("pkg.a",))


# ------------------------------------------------------------------------------ narrowing and callers


API = {
    "libs/common/pyproject.toml": "[project]\nname='common'\n",
    "libs/common/acme_common/__init__.py": "",
    "libs/common/acme_common/money.py": f([
        "from decimal import Decimal", "", "", "def cents_to_decimal(cents):", "    return Decimal(cents) / 100", "", "",
        "def format_cents(cents, currency='EUR'):", "    return f'{cents_to_decimal(cents):.2f} {currency}'",
    ]),
    "services/billing/pyproject.toml": "[project]\nname='billing'\n",
    "services/billing/app/__init__.py": "",
    "services/billing/app/services/__init__.py": "",
    "services/billing/app/services/settlement.py": f([
        "from acme_common.money import (", "    cents_to_decimal,", "    format_cents,", ")", "", "", "def summarise(total):", "    return format_cents(total)",
    ]),
    "services/billing/app/routers/__init__.py": "",
    "services/billing/app/routers/invoices.py": f(["from acme_common.money import format_cents as fmt_cents", "", "", "def get_invoice(c):", "    return fmt_cents(c)"]),
    "services/billing/app/jobs.py": f(["from acme_common.money import cents_to_decimal", "", "", "def run():", "    return cents_to_decimal(0)"]),
    "services/billing/tests/__init__.py": "",
    "services/billing/tests/test_money.py": f(["from acme_common.money import cents_to_decimal, format_cents", "", "", "def test_it():", "    assert format_cents(1)"]),
    "services/billing/tests/test_settlement.py": f(["import app.services.settlement as settlement_svc", "", "", "def test_it():", "    settlement_svc.summarise(1)"]),
    "services/cases/pyproject.toml": "[project]\nname='cases'\n",
    "services/cases/app/__init__.py": "",
    "services/cases/app/cases.py": f(["import acme_common.money as money_utils", "", "", "def get_case(row):", "    return money_utils.format_cents(row)"]),
    "services/cases/app/other.py": f(["import acme_common.money as m", "", "", "def other(row):", "    return m.cents_to_decimal(row)"]),
}
MONEY = "libs/common/acme_common/money.py"


class Narrowing(Base):
    def mono(self, change_old, change_new, extra=None):
        files = dict(API)
        files.update(extra or {})
        self.repo({"files": files}, {"files": {MONEY: API[MONEY].replace(change_old, change_new)}})
        self.analyze(adapter=self.make_adapter(service_marker="pyproject.toml", library_roots=("libs/common",)))
        return self.impact(MONEY)

    def test_an_importer_of_other_names_only_is_excluded_with_its_import_line_and_the_symbol_line(self):
        impact = self.mono(".2f", ",.2f")
        self.assertTrue(impact.narrowed)
        (ex,) = [x for x in impact.exclusions if x.subject_path == "services/billing/app/jobs.py"]
        self.assertEqual(ex.reason, "named_export_narrowing")
        self.assertEqual([(e.path, e.line, e.detail) for e in ex.evidence if e.path.endswith("jobs.py")], [("services/billing/app/jobs.py", 1, "from acme_common.money import cents_to_decimal")])
        self.assertIn(("libs/common/acme_common/money.py", 8, "def format_cents(cents, currency='EUR'):"), [(e.path, e.line, e.detail) for e in ex.evidence])

    def test_the_callers_are_the_surviving_importers_with_their_call_sites(self):
        impact = self.mono(".2f", ",.2f")
        got = {c.path: (c.symbol, [(s.line, s.symbol, s.form, s.args) for s in c.call_sites]) for c in impact.callers}
        self.assertEqual(got, {
            "services/billing/app/services/settlement.py": ("summarise", [(8, "format_cents", "call", ("total",))]),
            "services/billing/app/routers/invoices.py": ("get_invoice", [(5, "format_cents", "call", ("c",))]),
            "services/cases/app/cases.py": ("get_case", [(5, "format_cents", "call", ("row",))]),
        })

    def test_a_multi_line_import_cites_the_module_line_and_the_line_of_the_name_that_matters(self):
        impact = self.mono(".2f", ",.2f")
        caller = next(c for c in impact.callers if c.path.endswith("settlement.py"))
        lines = [(e.line, e.detail) for e in caller.evidence]
        self.assertEqual(lines, [(1, "from acme_common.money import ("), (3, "    format_cents,"), (8, "    return format_cents(total)")])
        (site,) = caller.call_sites
        self.assertEqual((site.import_evidence.line, site.evidence.line), (3, 8))

    def test_aliased_names_and_aliased_modules_are_followed(self):
        impact = self.mono(".2f", ",.2f")
        by = {c.path: c for c in impact.callers}
        (s,) = by["services/billing/app/routers/invoices.py"].call_sites
        self.assertEqual((s.symbol, s.evidence.detail), ("format_cents", "    return fmt_cents(c)"))
        (s,) = by["services/cases/app/cases.py"].call_sites
        self.assertEqual((s.symbol, s.evidence.detail), ("format_cents", "    return money_utils.format_cents(row)"))

    def test_a_module_importer_that_uses_only_other_attributes_is_excluded(self):
        impact = self.mono(".2f", ",.2f")
        (ex,) = [x for x in impact.exclusions if x.subject_path == "services/cases/app/other.py"]
        self.assertEqual(ex.reason, "named_export_narrowing")
        self.assertIn("import acme_common.money as m", [e.detail for e in ex.evidence])

    def test_any_non_attribute_use_of_the_module_keeps_the_importer(self):
        extra = {"services/cases/app/passed.py": f(["import acme_common.money as m", "", "", "def go(x):", "    return helper(m)"]),
                 "services/cases/app/dyn.py": f(["import acme_common.money as m", "", "", "def go(n):", "    return getattr(m, n)(1)"])}
        impact = self.mono(".2f", ",.2f", extra)
        by = {c.path: c for c in impact.callers}
        for path in ("services/cases/app/passed.py", "services/cases/app/dyn.py"):
            self.assertIn(path, by)
            self.assertEqual([(s.symbol, s.form) for s in by[path].call_sites], [("*", "reference")])
        self.assertNotIn("services/cases/app/other.py", by)

    def test_an_unused_module_import_is_kept_because_importing_runs_the_module(self):
        extra = {"services/cases/app/unused.py": "import acme_common.money\n"}
        impact = self.mono(".2f", ",.2f", extra)
        (c,) = [c for c in impact.callers if c.path.endswith("unused.py")]
        self.assertEqual([(s.form, s.symbol) for s in c.call_sites], [("import", "*")])

    def test_a_dotted_import_without_alias_narrows_by_the_attribute_chain(self):
        extra = {"services/cases/app/dotted.py": f(["import acme_common.money", "", "", "def go(x):", "    return acme_common.money.format_cents(x)"]),
                 "services/cases/app/dotted_other.py": f(["import acme_common.money", "", "", "def go(x):", "    return acme_common.money.cents_to_decimal(x)"]),
                 "services/cases/app/dotted_elsewhere.py": f(["import acme_common.money", "import acme_common.other", "", "", "def go(x):", "    return acme_common.other.fn(x)"])}
        impact = self.mono(".2f", ",.2f", extra)
        by = {c.path: c for c in impact.callers}
        self.assertIn("services/cases/app/dotted.py", by)
        self.assertEqual([(s.symbol, s.line) for s in by["services/cases/app/dotted.py"].call_sites], [("format_cents", 5)])
        self.assertNotIn("services/cases/app/dotted_other.py", by)
        self.assertEqual([s.form for s in by["services/cases/app/dotted_elsewhere.py"].call_sites], ["import"])  # money is imported and never used: kept

    def test_star_imports_and_dynamic_imports_are_not_narrowed(self):
        extra = {"services/cases/app/star.py": f(["from acme_common.money import *", "", "", "def go(x):", "    return format_cents(x)"]),
                 "services/cases/app/star_other.py": f(["from acme_common.money import *", "", "", "def go(x):", "    return cents_to_decimal(x)"]),
                 "services/cases/app/dyn.py": f(["import importlib", "", "m = importlib.import_module('acme_common.money')"])}
        impact = self.mono(".2f", ",.2f", extra)
        by = {c.path: c for c in impact.callers}
        self.assertEqual([(s.symbol, s.form, s.line) for s in by["services/cases/app/star.py"].call_sites], [("format_cents", "call", 5)])
        self.assertIn("services/cases/app/star_other.py", by)  # a star import is whole module: kept, with an import site
        self.assertEqual([s.form for s in by["services/cases/app/star_other.py"].call_sites], ["import"])
        self.assertEqual([s.form for s in by["services/cases/app/dyn.py"].call_sites], ["import"])

    def test_a_module_level_change_is_not_narrowed(self):
        impact = self.mono("from decimal import Decimal", "from decimal import Decimal, ROUND_HALF_UP")
        self.assertFalse(impact.narrowed)
        self.assertEqual(impact.exclusions, ())
        self.assertIn("services/billing/app/jobs.py", [c.path for c in impact.callers])

    def test_a_change_that_a_changed_name_reaches_inside_the_file_keeps_its_importers(self):
        """cents_to_decimal changes: format_cents calls it, so importers of format_cents are affected too."""
        impact = self.mono("Decimal(cents) / 100", "Decimal(cents) / 1000")
        paths = {c.path for c in impact.callers}
        self.assertEqual(paths, {"services/billing/app/services/settlement.py", "services/billing/app/routers/invoices.py", "services/cases/app/cases.py",
                                 "services/billing/app/jobs.py", "services/cases/app/other.py"})
        ex = [x for x in impact.exclusions]
        self.assertEqual(ex, [])

    def test_the_exclusion_cites_the_declarations_the_change_reached_beyond_the_changed_ones(self):
        files = dict(API)
        files[MONEY] = f(["_RATE = 100", "", "", "def helper(c):", "    return c / _RATE", "", "", "def format_cents(c):", "    return helper(c)", "", "", "def unrelated(c):", "    return c"])
        files["services/cases/app/u.py"] = "from acme_common.money import unrelated\n\nunrelated(1)\n"
        files["services/cases/app/v.py"] = "from acme_common.money import format_cents\n\nformat_cents(1)\n"
        self.repo({"files": files}, {"files": {MONEY: files[MONEY].replace("_RATE = 100", "_RATE = 1000")}})
        self.analyze(adapter=self.make_adapter(service_marker="pyproject.toml", library_roots=("libs/common",)))
        impact = self.impact(MONEY)
        self.assertTrue(impact.narrowed)
        (ex,) = [x for x in impact.exclusions if x.subject_path == "services/cases/app/u.py"]
        cited = {(e.path, e.line) for e in ex.evidence}
        self.assertIn((MONEY, 1), cited)   # the changed constant
        self.assertIn((MONEY, 4), cited)   # helper reaches it
        self.assertIn((MONEY, 8), cited)   # format_cents reaches helper
        self.assertNotIn((MONEY, 12), cited)
        self.assertIn("services/cases/app/v.py", [c.path for c in impact.callers])

    def test_module_level_code_that_uses_a_reached_name_stops_narrowing(self):
        files = {"m.py": f(["def a():", "    return 1", "", "", "def b():", "    return 2", "", "", "register(a)"]), "u.py": "from m import b\n\nb()\n"}
        self.repo({"files": files}, {"files": {"m.py": files["m.py"].replace("return 1", "return 11")}})
        self.analyze()
        impact = self.impact("m.py")
        self.assertFalse(impact.narrowed)
        self.assertEqual([c.path for c in impact.callers], ["u.py"])

    def test_a_module_level_assignment_that_holds_a_changed_name_reaches_its_importers(self):
        files = {"m.py": f(["def a():", "    return 1", "", "", "def b():", "    return 2", "", "", "REGISTRY = [a]"]),
                 "ur.py": "from m import REGISTRY\n", "ub.py": "from m import b\n"}
        self.repo({"files": files}, {"files": {"m.py": files["m.py"].replace("return 1", "return 11")}})
        self.analyze()
        impact = self.impact("m.py")
        self.assertTrue(impact.narrowed)
        self.assertEqual([c.path for c in impact.callers], ["ur.py"])
        self.assertEqual([x.subject_path for x in impact.exclusions], ["ub.py"])

    def test_dynamic_access_stops_narrowing(self):
        for dyn in ("globals()['a']()", "eval('a()')", "locals()"):
            with self.subTest(dyn):
                files = {"m.py": f(["def a():", "    return 1", "", "", "def b():", f"    return {dyn}"]), "u.py": "from m import b\n"}
                self.repo({"files": files}, {"files": {"m.py": files["m.py"].replace("return 1", "return 11")}})
                self.analyze()
                self.assertFalse(self.impact("m.py").narrowed)
                self.assertEqual([c.path for c in self.impact("m.py").callers], ["u.py"])

    def test_a_decorator_registers_the_function_on_its_receiver_so_importers_of_the_receiver_are_affected(self):
        """A changed handler registered on `router` reaches whoever imports the router (the module that mounts it)."""
        files = {"routes.py": f(["router = Router()", "", "", "@router.get('/x')", "def handler():", "    return 1", "", "", "def helper():", "    return 2"]),
                 "mount.py": "from routes import router\n\napp.include(router)\n", "other.py": "from routes import helper\n\nhelper()\n"}
        self.repo({"files": files}, {"files": {"routes.py": files["routes.py"].replace("return 1", "return 11")}})
        self.analyze()
        impact = self.impact("routes.py")
        self.assertTrue(impact.narrowed)
        self.assertEqual([c.path for c in impact.callers], ["mount.py"])
        self.assertEqual([x.subject_path for x in impact.exclusions], ["other.py"])
        (ex,) = impact.exclusions
        self.assertIn((("routes.py"), 1), {(e.path, e.line) for e in ex.evidence})  # the receiver's declaration is cited

    def test_a_handler_that_uses_the_changed_function_reaches_the_receiver_it_is_registered_on(self):
        """helper changes; the handler calls it and is registered on `router`: whoever imports the router is affected."""
        files = {"routes.py": f(["router = Router()", "", "", "def helper():", "    return 1", "", "", "@router.get('/x')", "def handler():", "    return helper()", "", "", "def unrelated():", "    return 3"]),
                 "mount.py": "from routes import router\n\napp.include(router)\n", "other.py": "from routes import unrelated\n\nunrelated()\n"}
        self.repo({"files": files}, {"files": {"routes.py": files["routes.py"].replace("return 1", "return 11")}})
        self.analyze()
        impact = self.impact("routes.py")
        self.assertTrue(impact.narrowed)
        self.assertEqual([c.path for c in impact.callers], ["mount.py"])
        self.assertEqual([x.subject_path for x in impact.exclusions], ["other.py"])

    def test_a_class_named_in_a_string_is_a_reference(self):
        """Forward references and ORM relationship targets are strings: the class that holds one is reached."""
        for ref in ("customer: 'Customer'", "customer = relationship('Customer')", "customer: list['Customer']"):
            with self.subTest(ref):
                files = {"m.py": f(["class Customer:", "    name = ''", "", "", "class Order:", f"    {ref}", "", "", "class Other:", "    x = 1"]),
                         "uo.py": "from m import Order\n", "ux.py": "from m import Other\n"}
                self.repo({"files": files}, {"files": {"m.py": files["m.py"].replace("name = ''", "name = 'x'")}})
                self.analyze()
                impact = self.impact("m.py")
                self.assertEqual([c.path for c in impact.callers], ["uo.py"])
                self.assertEqual([x.subject_path for x in impact.exclusions], ["ux.py"])

    def test_a_function_that_writes_a_module_global_reaches_importers_of_it(self):
        files = {"m.py": f(["STATE = 1", "", "", "def init():", "    global STATE", "    STATE = 2", "", "", "def other():", "    return 3"]),
                 "us.py": "from m import STATE\n", "uo.py": "from m import other\n"}
        self.repo({"files": files}, {"files": {"m.py": files["m.py"].replace("STATE = 2", "STATE = 5")}})
        self.analyze()
        impact = self.impact("m.py")
        self.assertEqual([c.path for c in impact.callers], ["us.py"])
        self.assertEqual([x.subject_path for x in impact.exclusions], ["uo.py"])

    def test_the_namespace_dict_of_a_module_is_a_whole_module_use(self):
        files = {"m.py": f(["def f():", "    return 1", "", "", "def g():", "    return 2"]), "u.py": "import m\nprint(m.__dict__['g'])\n"}
        self.repo({"files": files}, {"files": {"m.py": files["m.py"].replace("return 2", "return 3")}})
        self.analyze()
        (c,) = self.impact("m.py").callers
        self.assertEqual([(s.symbol, s.form) for s in c.call_sites], [("*", "reference")])

    def test_two_declarations_on_one_line_are_both_changed_symbols_when_the_line_changes(self):
        files = {"m.py": "x = 1; y = 2\n", "ux.py": "from m import x\n", "uy.py": "from m import y\n"}
        self.repo({"files": files}, {"files": {"m.py": "x = 9; y = 2\n"}})
        self.analyze()
        impact = self.impact("m.py")
        self.assertEqual(sorted(s.name for s in impact.symbols), ["x", "y"])
        self.assertEqual(sorted(c.path for c in impact.callers), ["ux.py", "uy.py"])

    def test_a_symbol_whose_only_change_is_a_blank_line_still_cites_its_declaration(self):
        self.repo({"files": {"m.py": "def f():\n    x = 1\n    return x\n"}}, {"files": {"m.py": "def f():\n    x = 1\n\n    return x\n"}})
        self.analyze()
        (s,) = self.impact("m.py").symbols
        self.assertEqual((s.name, s.added_lines, [(e.line, e.detail) for e in s.evidence]), ("f", (3,), [(1, "def f():")]))

    def test_a_file_git_calls_binary_is_one_module_level_change_with_its_importers_and_a_record(self):
        self.repo({"files": {"m.py": "def f():\n    return 1\n", "u.py": "from m import f\n"}}, {"files": {"m.py": b"def f():\n    return 2\x00\n"}})
        self.analyze()
        impact = self.impact("m.py")
        self.assertEqual([(s.name, s.change) for s in impact.symbols], [("<module>", "modified")])
        self.assertEqual([c.path for c in impact.callers], ["u.py"])  # the importers need no text of the file
        self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in self.records], [("file_not_analysed", "m.py", False)])

    def test_a_lone_surrogate_in_a_literal_import_is_an_incomplete_list_not_a_crash(self):
        self.repo({"files": {"m.py": "x = 1\n", "u.py": 'import importlib\nimportlib.import_module("\\ud800.m")\nimport m\n'}})
        res = self.importers_of("m.py")
        self.assertEqual(res.paths, ("u.py",))
        self.assertFalse(res.complete)
        self.assertEqual([r.kind for r in self.records], ["specifier_unresolved"])

    def test_the_evidence_of_a_continued_import_is_the_line_that_names_the_module(self):
        self.repo({"files": {"m.py": "def f():\n    pass\n", "u.py": "from \\\n    m import f\n"}})
        (e,) = self.importers_of("m.py").edges
        self.assertEqual((e.evidence.line, e.evidence.detail), (2, "    m import f"))

    def test_attribute_access_reaches_a_changed_class_member(self):
        """Invoice.total changes; summarize(doc) in the same module calls doc.total(): importers of summarize are affected."""
        files = {"m.py": f(["class Invoice:", "    def total(self):", "        return 1", "", "", "def summarize(doc):", "    return doc.total()", "", "", "def make():", "    return Invoice()", "", "", "def unrelated():", "    return 3"]),
                 "us.py": "from m import summarize\n\nsummarize(1)\n", "uu.py": "from m import unrelated\n"}
        self.repo({"files": files}, {"files": {"m.py": files["m.py"].replace("return 1", "return 2")}})
        self.analyze()
        impact = self.impact("m.py")
        self.assertTrue(impact.narrowed)
        self.assertEqual(sorted(c.path for c in impact.callers), ["us.py"])
        self.assertEqual([x.subject_path for x in impact.exclusions], ["uu.py"])

    def test_every_way_a_declaration_can_spell_a_changed_member_reaches_it(self):
        for use in ("return doc.settlement_reference", "return getattr(doc, 'settlement_reference')", "return Invoice(settlement_reference=1)"):
            with self.subTest(use):
                files = {"m.py": f(["class Invoice:", "    settlement_reference: str | None = None", "", "", "def helper(doc):", f"    {use}", "", "", "def unrelated():", "    return 3"]),
                         "uh.py": "from m import helper\n", "uu.py": "from m import unrelated\n"}
                self.repo({"files": files}, {"files": {"m.py": files["m.py"].replace("str | None = None", "str")}})
                self.analyze()
                impact = self.impact("m.py")
                self.assertEqual([c.path for c in impact.callers], ["uh.py"])
                self.assertEqual([x.subject_path for x in impact.exclusions], ["uu.py"])

    def test_module_level_code_that_reads_a_changed_member_stops_narrowing(self):
        files = {"m.py": f(["class Invoice:", "    def total(self):", "        return 1", "", "", "def unrelated():", "    return 3", "", "", "print(thing.total)"]), "uu.py": "from m import unrelated\n"}
        self.repo({"files": files}, {"files": {"m.py": files["m.py"].replace("return 1", "return 2")}})
        self.analyze()
        impact = self.impact("m.py")
        self.assertFalse(impact.narrowed)
        self.assertEqual([c.path for c in impact.callers], ["uu.py"])

    def test_calling_a_changed_name_where_a_declaration_runs_at_import_reaches_every_importer(self):
        """An assignment, a decorator, a default value or a class body runs when the module is imported."""
        cases = {
            "assignment": ("def load():\n    return 1\n\n\nCONFIG = load()\n", "return 1", "return 2"),
            "decorator": ("def scheduled(fn):\n    return fn\n\n\n@scheduled\ndef run():\n    pass\n", "return fn", "return fn or 1"),
            "decorator call": ("def scheduled(**kw):\n    return lambda fn: fn\n\n\n@scheduled(name='x')\ndef run():\n    pass\n", "return lambda fn: fn", "return lambda fn: fn or 1"),
            "default": ("def dflt():\n    return 1\n\n\ndef run(a=dflt()):\n    pass\n", "return 1", "return 2"),
            "class body": ("def compute():\n    return 1\n\n\nclass K:\n    x = compute()\n", "return 1", "return 2"),
            "through a helper": ("def inner():\n    return 1\n\n\ndef outer():\n    return inner()\n\n\nCONFIG = outer()\n", "return 1", "return 2"),
        }
        for name, (text, old, new) in cases.items():
            with self.subTest(name):
                files = {"m.py": text + "\n\ndef unrelated():\n    return 3\n", "uu.py": "from m import unrelated\n"}
                self.repo({"files": files}, {"files": {"m.py": files["m.py"].replace(old, new, 1)}})
                self.analyze()
                impact = self.impact("m.py")
                self.assertFalse(impact.narrowed)
                self.assertEqual([c.path for c in impact.callers], ["uu.py"])

    def test_calling_a_receiver_or_an_unchanged_name_at_import_does_not_stop_narrowing(self):
        files = {"m.py": f(["router = Router()", "", "", "def helper():", "    return 1", "", "", "@router.get('/x')", "def handler():", "    return 2", "", "", "CONFIG = other_load()", "", "", "def unrelated():", "    return 3"]),
                 "uu.py": "from m import unrelated\n", "ur.py": "from m import router\n"}
        self.repo({"files": files}, {"files": {"m.py": files["m.py"].replace("return 2", "return 22")}})
        self.analyze()
        impact = self.impact("m.py")
        self.assertTrue(impact.narrowed)
        self.assertEqual([c.path for c in impact.callers], ["ur.py"])

    def test_sys_modules_is_dynamic_access_and_stops_narrowing(self):
        files = {"m.py": f(["import sys", "", "", "def f():", "    return 1", "", "", "def dispatch(name):", "    return getattr(sys.modules[__name__], name)()"]), "u.py": "from m import dispatch\n\ndispatch('f')\n"}
        self.repo({"files": files}, {"files": {"m.py": files["m.py"].replace("return 1", "return 3")}})
        self.analyze()
        impact = self.impact("m.py")
        self.assertFalse(impact.narrowed)
        self.assertEqual([c.path for c in impact.callers], ["u.py"])

    def test_a_quoted_module_attribute_annotation_keeps_the_importer(self):
        files = {"pkg/__init__.py": "", "pkg/m.py": f(["class Invoice:", "    x = 1", "", "", "def other():", "    return 2"]),
                 "u.py": f(["import pkg.m as m", "", "", "def g(x: \"m.Invoice\"):", "    return m.other()"]), "v.py": f(["import pkg.m as m", "", "", "def g():", "    return m.other()"])}
        self.repo({"files": files}, {"files": {"pkg/m.py": files["pkg/m.py"].replace("x = 1", "x = 2")}})
        self.analyze()
        impact = self.impact("pkg/m.py")
        self.assertEqual([c.path for c in impact.callers], ["u.py"])
        self.assertEqual([x.subject_path for x in impact.exclusions], ["v.py"])

    def test_a_file_that_does_not_parse_is_not_narrowed(self):
        files = {"m.py": f(["def a():", "    return 1", "", "", "def b():", "    return 2"]), "u.py": "from m import b\n"}
        self.repo({"files": files}, {"files": {"m.py": files["m.py"].replace("return 1", "print 11")}})
        self.analyze()
        impact = self.impact("m.py")
        self.assertFalse(impact.narrowed)
        self.assertEqual([c.path for c in impact.callers], ["u.py"])

    def test_a_removed_name_still_counts_for_its_importers(self):
        files = {"m.py": f(["def a():", "    return 1", "", "", "def b():", "    return 2"]), "ua.py": "from m import a\n", "ub.py": "from m import b\n"}
        self.repo({"files": files}, {"files": {"m.py": f(["def b():", "    return 2"])}})
        self.analyze()
        impact = self.impact("m.py")
        self.assertEqual([c.path for c in impact.callers], ["ua.py"])
        self.assertEqual([x.subject_path for x in impact.exclusions], ["ub.py"])

    def test_a_renamed_function_counts_under_its_old_and_new_name(self):
        files = {"m.py": f(["def old():", "    return 1", "", "", "def keep():", "    return 2"]), "uo.py": "from m import old\n", "un.py": "from m import new\n", "uk.py": "from m import keep\n"}
        self.repo({"files": files}, {"files": {"m.py": files["m.py"].replace("def old", "def new")}})
        self.analyze()
        impact = self.impact("m.py")
        self.assertEqual(sorted(c.path for c in impact.callers), ["un.py", "uo.py"])
        self.assertEqual([x.subject_path for x in impact.exclusions], ["uk.py"])

    def test_without_a_pre_change_version_every_importer_is_affected(self):
        files = {"m.py": f(["def a():", "    return 1", "", "", "def b():", "    return 2"]), "ub.py": "from m import b\n"}
        self.repo({"files": files}, {"files": {"m.py": files["m.py"].replace("return 1", "return 11")}})
        self.analyze(pre=None)
        impact = self.impact("m.py")
        self.assertEqual(([c.path for c in impact.callers], impact.exclusions, impact.narrowed), (["ub.py"], (), False))

    def test_several_changed_symbols_keep_an_importer_of_either(self):
        files = {"m.py": f(["def a():", "    return 1", "", "", "def b():", "    return 2", "", "", "def c():", "    return 3"]),
                 "ua.py": "from m import a\n", "ub.py": "from m import b\n", "uc.py": "from m import c\n"}
        self.repo({"files": files}, {"files": {"m.py": files["m.py"].replace("return 1", "return 11").replace("return 2", "return 22")}})
        self.analyze()
        impact = self.impact("m.py")
        self.assertEqual(sorted(c.path for c in impact.callers), ["ua.py", "ub.py"])
        self.assertEqual([x.subject_path for x in impact.exclusions], ["uc.py"])

    def test_a_class_member_change_reaches_importers_of_the_class(self):
        files = {"models.py": f(["class Invoice:", "    reference: str | None = None", "", "", "class Customer:", "    name: str"]),
                 "ui.py": "from models import Invoice\n\nInvoice()\n", "uc.py": "from models import Customer\n"}
        self.repo({"files": files}, {"files": {"models.py": files["models.py"].replace("str | None = None", "str")}})
        self.analyze()
        impact = self.impact("models.py")
        self.assertEqual([(s.name, s.export_names) for s in impact.symbols], [("Invoice.reference", ("Invoice",))])
        self.assertEqual([c.path for c in impact.callers], ["ui.py"])
        self.assertEqual([x.subject_path for x in impact.exclusions], ["uc.py"])

    def test_a_type_only_import_of_a_changed_name_is_kept(self):
        files = {"m.py": "def a():\n    return 1\n", "u.py": f(["from typing import TYPE_CHECKING", "", "if TYPE_CHECKING:", "    from m import a", "", "", "def run(x: 'a'):", "    pass"])}
        self.repo({"files": files}, {"files": {"m.py": "def a():\n    return 2\n"}})
        self.analyze()
        (c,) = self.impact("m.py").callers
        self.assertEqual(c.path, "u.py")

    def test_callers_uses_the_same_narrowing_and_none_without_a_change(self):
        files = {"m.py": f(["def a():", "    return 1", "", "", "def b():", "    return 2"]), "ua.py": "from m import a\n\na()\n", "ub.py": "from m import b\n\nb()\n"}
        self.repo({"files": files}, {"files": {"m.py": files["m.py"].replace("return 1", "return 11")}})
        a = self.make_adapter()
        cs = self.change()
        (fc,) = cs.files
        (sym,) = a.changed_symbols(cs, fc)
        narrowed = a.callers(sym, self.sha("main"), change=cs)
        self.assertEqual([c.path for c in narrowed], ["ua.py"])
        plain = a.callers(sym, self.sha("main"))
        self.assertEqual(sorted(c.path for c in plain), ["ua.py", "ub.py"])  # no change: no pre-change version known, nothing narrowed


class Callers(Base):
    def test_test_files_are_never_callers_they_are_unit_tests_with_import_evidence(self):
        files = dict(API)
        self.repo({"files": files}, {"files": {MONEY: API[MONEY].replace(".2f", ",.2f")}})
        self.analyze(adapter=self.make_adapter(service_marker="pyproject.toml", library_roots=("libs/common",)))
        impact = self.impact(MONEY)
        self.assertNotIn("services/billing/tests/test_money.py", [c.path for c in impact.callers])
        tests = {t.path: t for t in impact.unit_tests}
        t = tests["services/billing/tests/test_money.py"]
        self.assertEqual((t.via, [(e.line, e.detail) for e in t.evidence]), (MONEY, [(1, "from acme_common.money import cents_to_decimal, format_cents")]))

    def test_a_test_that_imports_a_caller_is_a_unit_test_one_hop_out(self):
        self.repo({"files": API}, {"files": {MONEY: API[MONEY].replace(".2f", ",.2f")}})
        self.analyze(adapter=self.make_adapter(service_marker="pyproject.toml", library_roots=("libs/common",)))
        tests = {t.path: t for t in self.impact(MONEY).unit_tests}
        t = tests["services/billing/tests/test_settlement.py"]
        self.assertEqual(t.via, "services/billing/app/services/settlement.py")
        self.assertEqual([e.detail for e in t.evidence], ["import app.services.settlement as settlement_svc"])

    def test_tests_of_an_excluded_importer_are_not_listed(self):
        files = dict(API)
        files["services/billing/tests/test_jobs.py"] = "import app.jobs\n"
        self.repo({"files": files}, {"files": {MONEY: API[MONEY].replace(".2f", ",.2f")}})
        self.analyze(adapter=self.make_adapter(service_marker="pyproject.toml", library_roots=("libs/common",)))
        self.assertNotIn("services/billing/tests/test_jobs.py", [t.path for t in self.impact(MONEY).unit_tests])

    def test_a_changed_test_file_gets_no_importer_search(self):
        self.repo({"files": {"tests/test_a.py": "def test():\n    pass\n", "x.py": "import tests.test_a\n"}}, {"files": {"tests/test_a.py": "def test():\n    assert 1\n"}})
        self.analyze()
        impact = self.impact("tests/test_a.py")
        self.assertEqual((impact.role, impact.importers, impact.callers), ("test", (), ()))
        self.assertEqual([s.name for s in impact.symbols], ["test"])

    def test_a_mention_in_a_comment_string_or_attribute_of_another_object_is_not_a_use(self):
        files = {"m.py": "def f():\n    return 1\n", "u.py": f(["from m import f", "", "# f()", "TEXT = 'f()'", "", "", "def g(obj):", "    return obj.f()"])}
        self.repo({"files": files}, {"files": {"m.py": "def f():\n    return 2\n"}})
        self.analyze()
        (c,) = self.impact("m.py").callers
        self.assertEqual([s.form for s in c.call_sites], ["import"])  # an importer with no use is a caller at its import line

    def test_a_reference_passed_as_a_value_is_a_reference_site_in_its_declaration(self):
        files = {"m.py": "def f():\n    return 1\n", "u.py": f(["from m import f", "", "", "def g():", "    return run(f)", "", "", "HANDLERS = [f]"])}
        self.repo({"files": files}, {"files": {"m.py": "def f():\n    return 2\n"}})
        self.analyze()
        (c,) = self.impact("m.py").callers
        self.assertEqual([(s.line, s.form, s.caller_symbol) for s in c.call_sites], [(5, "reference", "g"), (8, "reference", "HANDLERS")])
        self.assertEqual(c.symbol, "g")

    def test_a_call_at_module_level_has_the_module_as_its_caller_symbol(self):
        files = {"m.py": "def f(x, y=1):\n    return 1\n", "u.py": "from m import f\n\nf(\n    1,\n    y=2,\n)\n"}
        self.repo({"files": files}, {"files": {"m.py": "def f(x, y=1):\n    return 2\n"}})
        self.analyze()
        (c,) = self.impact("m.py").callers
        (s,) = c.call_sites
        self.assertEqual((c.symbol, s.caller_symbol, s.line, s.args), ("<module>", "<module>", 3, ("1", "y=2")))

    def test_a_decorator_application_is_a_call_site_and_the_class_is_the_caller_of_its_methods(self):
        files = {"m.py": "def deco(fn):\n    return fn\n", "u.py": f(["from m import deco", "", "", "class K:", "    @deco", "    def run(self):", "        pass"])}
        self.repo({"files": files}, {"files": {"m.py": "def deco(fn):\n    return fn or 1\n"}})
        self.analyze()
        (c,) = self.impact("m.py").callers
        self.assertEqual([(s.line, s.form, s.caller_symbol) for s in c.call_sites], [(5, "reference", "K")])
        self.assertEqual(c.symbol, "K")

    def test_the_callers_of_a_symbol_in_a_contract_style_module_by_package_dir(self):
        files = {"pyproject.toml": '[tool.setuptools]\npackage-dir = { acme_contracts = "contracts" }\n', "contracts/__init__.py": "", "contracts/models/__init__.py": "",
                 "contracts/models/invoice.py": "class Invoice:\n    ref: str | None = None\n", "app/use.py": "from acme_contracts.models.invoice import Invoice\n\nInvoice()\n"}
        self.repo({"files": files}, {"files": {"contracts/models/invoice.py": "class Invoice:\n    ref: str\n"}})
        self.analyze()
        impact = self.impact("contracts/models/invoice.py")
        self.assertEqual([c.path for c in impact.callers], ["app/use.py"])
        self.assertEqual([s.name for s in impact.symbols], ["Invoice.ref"])


# ------------------------------------------------------------------------------ test paths


class TestPaths(Base):
    def test_markers_match_by_segment_never_by_substring(self):
        a = PyAdapter("acme/api", "x", None, test_markers=["tests/", "test_"])  # type: ignore[arg-type]
        for path in ("services/billing/tests/test_money.py", "tests/conftest.py", "pkg/test_x.py", "services/billing/tests/helpers.py"):
            self.assertTrue(a.is_test_path(path), path)
        for path in ("services/billing/app/backtests/replay.py", "services/billing/app/latest_rates.py", "pkg/contest.py", "test_data/a.py", "pkg/attest_x.py"):
            self.assertFalse(a.is_test_path(path), path)

    def test_the_defaults_for_python_when_the_config_names_none(self):
        a = PyAdapter("acme/api", "x", None)  # type: ignore[arg-type]
        for path in ("tests/a.py", "test/a.py", "pkg/test_a.py", "pkg/a_test.py", "conftest.py", "pkg/conftest.py", "pkg/tests/conftest.py"):
            self.assertTrue(a.is_test_path(path), path)
        for path in ("pkg/backtests/a.py", "pkg/latest.py", "pkg/contest.py", "pkg/a.py"):
            self.assertFalse(a.is_test_path(path), path)

    def test_a_conftest_under_a_test_folder_is_a_test(self):
        a = PyAdapter("acme/api", "x", None, test_markers=["tests/", "test_"])  # type: ignore[arg-type]
        self.assertTrue(a.is_test_path("services/billing/tests/conftest.py"))

    def test_a_bad_marker_is_refused(self):
        with self.assertRaises(ValueError):
            PyAdapter("acme/api", "x", None, test_markers=["a/b"])  # type: ignore[arg-type]

    def test_a_changed_file_gets_its_role_from_the_markers(self):
        self.repo({"files": {"pkg/backtests/a.py": "x = 1\n", "pkg/latest_rates.py": "y = 1\n", "tests/test_a.py": "z = 1\n"}},
                  {"files": {"pkg/backtests/a.py": "x = 2\n", "pkg/latest_rates.py": "y = 2\n", "tests/test_a.py": "z = 2\n"}})
        analysis = self.analyze()
        self.assertEqual({(f.path, f.role) for f in analysis.files}, {("pkg/backtests/a.py", "source"), ("pkg/latest_rates.py", "source"), ("tests/test_a.py", "test")})


# ------------------------------------------------------------------------------ definitions


class Definitions(Base):
    FILES = {
        "a/models.py": "class Schedule:\n    interval: int = 1\n    other = 2\n",
        "b/models.py": "class Cadence:\n    interval: int = 1\n",
        "c/models.py": "def interval():\n    pass\n",
        "d/models.py": "interval = 3\n\n\ndef f():\n    interval = 4\n    return interval\n",
        "e/use.py": "from a.models import interval\nx = interval\ny = obj.interval\nz = dict(interval=1)\n# interval = 5\nS = 'interval = 6'\n",
        "tests/test_x.py": "interval = 7\n",
        "f/test_y.py": "interval = 8\n",
        "g/other.py": "intervals = 1\nxinterval = 2\n",
    }

    def test_definitions_are_counted_references_imports_comments_attribute_reads_and_tests_are_not(self):
        self.use_shared("definitions", {"files": dict(self.FILES)})
        found = self.make_adapter(distinct_max=3).definitions("interval", self.sha("main"))
        self.assertEqual((found.state, found.count, found.complete), ("found", 4, True))
        self.assertEqual([(e.path, e.line) for e in found.sites], [("a/models.py", 2), ("b/models.py", 2), ("c/models.py", 1), ("d/models.py", 1)])
        self.assertFalse(found.distinctive(3))
        self.assertTrue(found.distinctive(4))
        from tests import scenario_support as ss
        self.assertEqual(ss.evidence_problems({self.REPO: self.path}, found.sites), [])

    def test_a_name_defined_once_is_distinctive(self):
        self.use_shared("definitions", {"files": dict(self.FILES)})
        found = self.make_adapter().definitions("Schedule", self.sha("main"))
        self.assertEqual((found.count, found.distinctive(3)), (1, True))

    def test_a_substring_match_is_not_a_definition(self):
        self.use_shared("definitions", {"files": dict(self.FILES)})
        self.assertEqual(self.make_adapter().definitions("interval", self.sha("main")).count, 4)  # intervals, xinterval are not interval
        self.assertEqual(self.make_adapter().definitions("nterva", self.sha("main")).count, 0)

    def test_an_unreadable_search_means_not_established_never_distinctive(self):
        """Trap: a grep failure read as 'defined once'."""
        self.use_shared("definitions", {"files": dict(self.FILES)})
        a = self.make_adapter()
        unreadable = SearchResult(Read.UNREADABLE, "x", self.sha("main"), (), None, 0, None, "git grep timed out")
        with mock.patch.object(self.git, "grep", return_value=unreadable):
            found = a.definitions("interval", self.sha("main"))
        self.assertEqual((found.state, found.count), ("unreadable", None))
        self.assertFalse(found.distinctive(1000))

    def test_a_cut_search_is_not_a_distinct_count(self):
        self.use_shared("definitions", {"files": dict(self.FILES)})
        found = self.make_adapter(examine_cap=2).definitions("interval", self.sha("main"))
        self.assertFalse(found.complete)
        self.assertFalse(found.distinctive(1000))
        self.assertIn("search_truncated", [r.kind for r in self.records])

    def test_an_unreadable_candidate_means_the_count_is_not_complete(self):
        files = dict(self.FILES)
        files["z.py"] = b"interval = '\xe9'\n"
        self.repo({"files": files})
        found = self.make_adapter().definitions("interval", self.sha("main"))
        self.assertFalse(found.complete)
        self.assertFalse(found.distinctive(1000))
        self.assertIn("read_unreadable", [r.kind for r in self.records])

    def test_a_candidate_that_does_not_parse_means_the_count_is_not_complete(self):
        files = dict(self.FILES)
        files["old.py"] = "print 'interval'\n"
        self.repo({"files": files})
        found = self.make_adapter().definitions("interval", self.sha("main"))
        self.assertFalse(found.complete)
        self.assertFalse(found.distinctive(1000))

    def test_a_changed_class_field_is_checked_by_its_leaf_name_and_recorded_when_defined_too_often(self):
        files = dict(self.FILES)
        self.repo({"files": files}, {"files": {"a/models.py": files["a/models.py"].replace("interval: int = 1", "interval: int = 2").replace("other = 2", "other = 3")}})
        self.analyze(adapter=self.make_adapter(distinct_max=3))
        self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in self.records], [("not_distinctive", "interval", False)])
        impact = self.impact("a/models.py")
        self.assertEqual(impact.not_distinctive, ("interval",))
        self.assertEqual(impact.names_unreadable, ())

    def test_a_dunder_method_name_is_never_counted(self):
        files = {"a.py": "class K:\n    def __init__(self):\n        self.x = 1\n"}
        self.repo({"files": files}, {"files": {"a.py": files["a.py"].replace("self.x = 1", "self.x = 2")}})
        self.analyze(adapter=self.make_adapter(distinct_max=0))
        self.assertEqual(self.records, [])

    def test_no_distinct_max_records_nothing(self):
        files = dict(self.FILES)
        self.repo({"files": files}, {"files": {"a/models.py": files["a/models.py"].replace("interval: int = 1", "interval: int = 2")}})
        self.analyze()
        self.assertEqual(self.records, [])


# ------------------------------------------------------------------------------ guards, protocol, parts


class GuardsAndProtocol(Base):
    def test_python_call_sites_are_never_excluded_by_a_guard(self):
        """Guards are out of scope for Python in M6: every call site is affected, whatever the change sits under."""
        before = f(["def fmt(amount, mode):", "    if mode == 'legacy':", "        return amount", "    return amount * 2"])
        after = before.replace("        return amount", "        return amount + 1")
        files = {"m.py": before, "u.py": f(["from m import fmt", "", "", "def go():", "    return fmt(1, 'modern')"])}
        self.repo({"files": files}, {"files": {"m.py": after}})
        self.analyze()
        impact = self.impact("m.py")
        (c,) = impact.callers
        self.assertEqual((c.reason, impact.exclusions, [s.guards for s in impact.symbols]), (None, (), [()]))
        a = self.make_adapter()
        cs = self.change()
        (fc,) = cs.files
        (sym,) = a.changed_symbols(cs, fc)
        self.assertEqual(a.guards(sym, c.call_sites[0], before_sha=self.sha("main~1"), after_sha=self.sha("main")), "affected")

    def test_the_adapter_satisfies_the_protocol(self):
        self.repo({"files": {"m.py": "x = 1\n"}})
        self.assertIsInstance(self.make_adapter(), LanguageAdapter)

    def test_callers_returns_the_call_sites_of_one_symbol(self):
        files = {"m.py": "def a():\n    return 1\n\n\ndef b():\n    return 2\n", "u.py": "from m import a, b\n\na()\nb()\na()\n"}
        self.repo({"files": files}, {"files": {"m.py": files["m.py"].replace("return 1", "return 11")}})
        a = self.make_adapter()
        cs = self.change()
        (fc,) = cs.files
        (sym,) = a.changed_symbols(cs, fc)
        sites = a.callers(sym, self.sha("main"))
        self.assertEqual(sorted((s.path, s.line, s.symbol) for s in sites), [("u.py", 3, "a"), ("u.py", 4, "b"), ("u.py", 5, "a")])
        sites = a.callers(sym, self.sha("main"), change=cs)
        self.assertEqual(sorted((s.path, s.line, s.symbol) for s in sites), [("u.py", 3, "a"), ("u.py", 5, "a")])

    def test_from_config_takes_the_markers_roots_and_limits_of_the_repository(self):
        ws = config.loads(
            'workspace = "w"\n[limits]\nhub_importers = 7\ndistinct_max = 2\n[repos."acme/api"]\nhost = "git"\nremote = "file:///x"\nlanguage = "python"\n'
            'roles = ["backend"]\nservice_marker = "pyproject.toml"\nlibrary_roots = ["libs/common"]\ntests = { markers = ["spec/"] }\n', fmt="toml", env={})
        a = PyAdapter.from_config(ws.repos["acme/api"], None, limits=ws.limits)  # type: ignore[arg-type]
        self.assertEqual((a.markers, a.service_marker, a.library_roots, a.hub_importers, a.distinct_max, a.remote), (("spec/",), "pyproject.toml", ("libs/common",), 7, 2, "file:///x"))
        self.assertTrue(a.is_test_path("spec/x.py"))
        self.assertFalse(a.is_test_path("tests/x.py"))


class NonPythonFiles(Base):
    def test_a_changed_stub_or_extension_module_is_not_analysed_and_says_so(self):
        for name in ("m.pyi", "m.pyx", "m.pxd"):
            with self.subTest(name):
                self.repo({"files": {name: "def f(): ...\n", "u.py": "import m\n"}}, {"files": {name: "def f(x): ...\n"}})
                self.analyze()
                impact = self.impact(name)
                self.assertEqual((impact.symbols, impact.importers, impact.importers_complete), ((), (), False))
                self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in self.records], [("file_not_analysed", name, False)])

    def test_documents_config_and_data_files_are_not_flagged(self):
        files = {"README.md": "a\n", "pyproject.toml": "[project]\nname='x'\n", "data.json": "{}\n", "config.yaml": "a: 1\n", "setup.cfg": "[metadata]\nname = x\n"}
        self.repo({"files": files}, {"files": {k: v + "\n" for k, v in files.items()}})
        self.analyze()
        self.assertEqual(self.records, [])
        for path in files:
            self.assertTrue(self.impact(path).importers_complete)


class EdgesOfAChange(Base):
    def test_a_deleted_or_renamed_file_without_a_pre_change_version_has_an_incomplete_importer_list(self):
        """Without the pre-change commit the importers of the old path cannot be found: not 'no importers'."""
        body = f(["def a():", "    return 1", "# padding to keep the rename exact"] * 3)
        for name, second in (("deleted", {"delete": ["pkg/m.py"]}), ("renamed", {"delete": ["pkg/m.py"], "files": {"pkg/n.py": body}})):
            with self.subTest(name):
                self.repo({"files": {"pkg/m.py": body, "app/u.py": "from pkg.m import a\n\na()\n"}}, second)
                self.analyze(pre=None)
                impact = self.impact("pkg/m.py" if name == "deleted" else "pkg/n.py")
                self.assertFalse(impact.importers_complete)
                self.assertEqual(impact.importer_reasons, ("there is no pre-change version to find the importers of the old path in",))
                self.assertEqual((impact.callers, impact.importers), ((), ()))

    def test_a_changed_file_whose_name_is_not_utf8_is_incomplete_with_a_record(self):
        self.repo_raw({b"pkg/__init__.py": b"", b"pkg/caf\xe9.py": b"def f():\n    return 1\n"}, {b"pkg/caf\xe9.py": b"def f():\n    return 2\n"})
        self.analyze()
        (impact,) = [i for i in self.analysis.impacts if i.path.startswith("pkg/caf")]
        self.assertEqual((impact.symbols, impact.importers_complete), ((), False))
        self.assertEqual([(r.kind, r.blocks_selection) for r in self.records], [("read_unreadable", True)])

    def test_a_changed_non_python_file_with_a_name_that_is_not_utf8_says_nothing(self):
        self.repo_raw({b"docs/caf\xe9.md": b"a\n"}, {b"docs/caf\xe9.md": b"b\n"})
        self.analyze()
        (impact,) = self.analysis.impacts
        self.assertEqual((impact.importers_complete, self.records), (True, []))

    def test_a_definition_in_a_file_git_grep_cannot_search_makes_the_count_partial(self):
        repo, shas = sg.make_raw_repo(self.tmp, "links", [[
            ("100644", b"a.py", b"settlement_ref = 1\n"), ("120000", b"link.py", b"a.py"), ("100644", b"b.py", b"x = settlement_ref\n"),
        ]])
        self.path, self.url = repo, sg.remote_url(repo)
        self.records = []
        from reachmap.gitlayer import Git
        self.git = Git(self.tmp / "cache", env=sg.isolated_env(self.tmp / "home"), record_to=self.records)
        found = self.make_adapter().definitions("settlement_ref", shas[-1])
        self.assertEqual((found.state, found.count, found.complete), ("found", 1, False))
        self.assertFalse(found.distinctive(1000))
        self.assertEqual([(r.kind, r.blocks_selection) for r in self.records], [("read_unreadable", True)])
        self.assertIn("link.py", self.records[0].subject)

    def test_a_script_directory_module_with_the_name_of_a_standard_library_module_is_not_skipped(self):
        """tools/run.py `import logging` finds tools/logging.py first when run from tools/: not a standard library miss."""
        self.repo({"files": {"tools/logging.py": "def f():\n    return 1\n", "tools/run.py": "import logging\n\nlogging.f()\n", "other/x.py": "import logging\n"}})
        res = self.importers_of("tools/logging.py")
        self.assertFalse(res.complete)
        self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in self.records], [("specifier_unresolved", "logging", True)])

    def test_a_standard_library_name_with_nothing_beside_the_importer_is_still_not_a_miss(self):
        self.repo({"files": {"lib/logging.py": "def f():\n    return 1\n", "tools/run.py": "import logging\n"}})
        res = self.importers_of("lib/logging.py")
        self.assertEqual((res.complete, self.records), (True, []))

    def test_a_file_whose_hunks_are_not_available_is_a_module_level_change(self):
        self.repo({"files": {"m.py": "def a():\n    return 1\n"}}, {"files": {"m.py": "def a():\n    return 2\n"}})
        a = self.make_adapter()
        cs = self.change()
        (fc,) = cs.files
        unavailable = dataclasses.replace(fc, hunks=(), content_available=False)
        (s,) = a.changed_symbols(cs, unavailable)
        self.assertEqual((s.name, s.change), ("<module>", "modified"))

    def test_a_comment_only_change_walks_nothing_and_records_nothing(self):
        self.repo({"files": {"m.py": "# one\ndef a():\n    return 1\n", "u.py": "from m import a\n"}}, {"files": {"m.py": "# two\ndef a():\n    return 1\n"}})
        self.analyze()
        impact = self.impact("m.py")
        self.assertEqual((impact.symbols, impact.importers, impact.callers, impact.exclusions), ((), (), (), ()))
        self.assertEqual(self.records, [])
