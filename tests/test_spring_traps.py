"""Shape A for the Spring adapter: every test makes a read fail (a file that cannot be read or cited, a search that fails or is cut, a symlink git
grep does not search, a base class that does not parse, a configuration that cannot be read) and asserts an incompleteness, never an empty and
complete answer, and that a route that could not be read is never a removed route.
"""

import unittest
from unittest import mock

from reachmap import entrypoints
from reachmap.adapters.jvm import JvmAdapter
from reachmap.adapters.spring import SpringAdapter
from reachmap.gitlayer import Git
from reachmap.model import ReadResult
from tests import support_git as sg
from tests.support_universe import f
from tests.test_spring_routes import GET, IMPORTS, LIST, PATH, Base, controller

CTRL = controller(*LIST, *GET)
FLAT = ("100644", b"C.java", f(["package p;", "", *IMPORTS, "", "@RestController", "public class C {", '    @GetMapping("/c")', "    void c() {}", "}"]).encode())


class Raw(Base):
    """Cases written as raw trees (names and modes `git add` would refuse), with one Git layer."""

    def raw(self, *entries):
        repo, shas = sg.make_raw_repo(self.tmp, "raw", [list(entries)])
        self.path, self.url = repo, sg.remote_url(repo)
        self.records = []
        self.git = Git(self.tmp / "cache", env=sg.isolated_env(self.tmp / "home"), record_to=self.records)
        self.shas = shas
        return shas[-1]


class FailedReads(Raw):
    def test_a_symlinked_java_file_git_grep_does_not_search_is_an_incomplete_table(self):
        sha = self.raw(FLAT, ("120000", b"Link.java", b"C.java"))
        a = self.spring()
        table = a._table(sha)
        self.assertFalse(table.complete)
        self.assertTrue(any("symlink" in why for why in a.problems), a.problems)
        self.assertEqual([r.label for r in table.routes()], ["GET /c"])  # what could be read is listed, the answer is not whole
        self.assertIn(("read_unreadable", True), self.kinds())

    def test_a_file_name_that_cannot_be_cited_is_an_incomplete_table_and_a_record(self):
        sha = self.raw(FLAT, ("100644", b"we\\ird.java", f(["package p;", "", *IMPORTS, "", "@RestController", "class W {", '    @GetMapping("/w")', "    void w() {}", "}"]).encode()))
        a = self.spring()
        table = a._table(sha)
        self.assertFalse(table.complete)
        self.assertEqual([r.label for r in table.routes()], ["GET /c"])
        self.assertIn(("read_unreadable", True), self.kinds())

    def test_a_name_that_is_not_utf_8_is_the_same(self):
        sha = self.raw(FLAT, ("100644", b"\xff.java", f(["package p;", "", *IMPORTS, "", "@RestController", "class W {", '    @GetMapping("/w")', "    void w() {}", "}"]).encode()))
        a = self.spring()
        self.assertFalse(a._table(sha).complete)
        self.assertIn(("read_unreadable", True), self.kinds())


class Searches(Base):
    def test_a_cut_search_is_an_incomplete_table_and_a_blocking_record(self):
        files = {PATH: CTRL, **{f"src/main/java/com/acme/api/Other{i}.java": f(["package com.acme.api;", f"class Other{i} {{}}"]) for i in range(6)}}
        self.repo({"files": files})
        self.language = self.make_adapter(examine_cap=3)
        self.entry = SpringAdapter(self.REPO, self.language)
        table = self.entry._table(self.sha("main"))
        self.assertFalse(table.complete)
        self.assertIn(("search_truncated", True), self.kinds())
        self.assertTrue(self.entry.problems)

    def test_a_search_that_fails_is_an_incomplete_table_never_no_routes(self):
        self.repo({"files": {PATH: CTRL}})
        a = self.spring()

        class Failed:
            state = "unreadable"
            detail = "git grep failed"
            truncated_by = None
            not_searched = ()
            partial_matches = ()
            examined, total = None, 0

        with mock.patch.object(self.language, "search", return_value=Failed()):
            table = a._table(self.sha("main"))
        self.assertFalse(table.complete)
        self.assertEqual(table.routes(), [])
        self.assertTrue(any("unreadable" in why for why in a.problems), a.problems)

    def test_an_unreadable_application_configuration_is_an_incomplete_table(self):
        self.repo({"files": {PATH: CTRL, "src/main/resources/application.properties": "server.servlet.context-path=/a\n"}})
        a = self.spring()
        real = self.language.file_text
        with mock.patch.object(self.language, "file_text", lambda sha, p: ReadResult.unreadable("simulated") if p.endswith("application.properties") else real(sha, p)):
            table = a._table(self.sha("main"))
            table.composed()
        self.assertFalse(table.complete)
        self.assertTrue(any("application.properties" in why for why in a.problems), a.problems)

    def test_a_tree_that_cannot_be_checked_out_is_an_incomplete_table(self):
        self.repo({"files": {PATH: CTRL}})
        a = self.spring()
        with mock.patch.object(self.language, "checkout", return_value=None):
            table = a._table(self.sha("main"))
        self.assertFalse(table.complete)


class Supertypes(Base):
    def test_a_base_class_that_does_not_parse_is_an_incomplete_table_and_a_record_never_a_controller_without_its_routes(self):
        base = f(["package com.acme.api;", "", *IMPORTS, "", "public abstract class BaseController {", '    @GetMapping("/ping")', '    public String ping() { return "pong; }', "}"])
        sub = controller(*LIST).replace("public class InvoiceController", "public class InvoiceController extends BaseController")
        self.repo({"files": {"src/main/java/com/acme/api/BaseController.java": base, PATH: sub}})
        a = self.spring()
        table = a._table(self.sha("main"))
        table.composed()
        self.assertFalse(table.complete)
        self.assertIn(("parse_degraded", True), self.kinds())
        self.assertEqual([r.label for r in table.routes()], ["GET /invoices"])

    def test_a_package_file_that_does_not_parse_may_hold_the_base_class_and_is_said(self):
        """The base class is named by `extends Base`; the only file of its package that could declare it does not parse."""
        broken = f(["package com.acme.api;", "", 'class Base { String s = "oops; }'])
        sub = controller(*LIST).replace("public class InvoiceController", "public class InvoiceController extends Base")
        self.repo({"files": {"src/main/java/com/acme/api/Base.java": broken, PATH: sub}})
        a = self.spring()
        table = a._table(self.sha("main"))
        table.composed()
        self.assertFalse(table.complete)

    def test_a_removed_route_is_not_claimed_while_the_table_of_the_head_is_not_whole(self):
        base = f(["package com.acme.api;", "", *IMPORTS, "", "public abstract class BaseController {", '    @GetMapping("/ping")', '    public String ping() { return "pong"; }', "}"])
        sub = controller(*LIST).replace("public class InvoiceController", "public class InvoiceController extends BaseController")
        broken = base.replace('"pong"', '"pong')
        self.repo({"files": {"src/main/java/com/acme/api/BaseController.java": base, PATH: sub}}, {"files": {"src/main/java/com/acme/api/BaseController.java": broken}})
        a = self.spring()
        removed, added = a.routes_changed(self.sha("main~1"), self.sha("main"))
        self.assertEqual(removed, [])  # GET /invoices/ping is not gone: the base class that gives it cannot be read


class Reaching(Base):
    def test_a_path_that_cannot_be_evaluated_is_a_partial_route_and_a_record_when_a_change_reaches_it(self):
        src = controller('    @GetMapping(Config.base() + "/x")', "    String x() { return \"a\"; }", head=("@RestController",))
        self.repo({"files": {PATH: src, "src/main/java/com/acme/api/Config.java": f(["package com.acme.api;", "class Config { static String base() { return \"/b\"; } }"])},
                   }, {"files": {PATH: src.replace('return "a";', 'return "b";')}})
        self.language = self.make_adapter(distinct_max=3)
        self.entry = SpringAdapter(self.REPO, self.language)
        analysis = self.language.analyze(self.change())
        res = entrypoints.reach(analysis, self.language, self.entry)
        (r,) = res.reached
        self.assertTrue(r.routes[0].partial)
        self.assertEqual([(x.kind, x.blocks_selection) for x in self.records if x.kind == "route_path_partial"], [("route_path_partial", False)])


if __name__ == "__main__":
    unittest.main()
