"""A route table that could not be read is not a route table with fewer routes (context 5, Shape A): `routes_changed` must not turn the routes of a file
that did not parse into removed ones, nor the routes of a base that could not be read into new ones, in any adapter. The adapters guard themselves
(FastAPI, Django, OpenAPI), and the http edge guards any adapter that does not (`edges.http._changed_routes`), with a record that says what was not claimed.
"""

import unittest
from types import SimpleNamespace

from reachmap.adapters import Route, guard_route_changes
from reachmap.adapters.fastapi import FastApiAdapter
from reachmap.adapters.python import PyAdapter
from reachmap.edges.http import HttpEdgeAdapter
from reachmap.model import Evidence
from tests.support_cross import CrossCase
from tests.test_nextjs import TSCONFIG, route as next_route
from tests.test_nextjs import Base as NextBase
from tests.test_edge_http import API, ORDERS, WEB, workspace_text

BROKEN = ORDERS + "\n\ndef broken(:\n    pass\n"  # one unclosed parenthesis in the file that holds the routes


def route(method="GET", path="/a"):
    ev = Evidence(repo="t/api", path="app/x.py", line=1, ref="a" * 40, detail="@router")
    return Route("t/api", "api_route", method, path, "app/x.py", "h", "app/x.py", "", (ev,))


class Guard(unittest.TestCase):
    def test_a_removal_needs_a_whole_head_and_an_addition_a_whole_base_and_the_record_says_what_was_withheld(self):
        records = []
        r, a = [route("GET", "/gone")], [route("GET", "/new")]
        self.assertEqual(guard_route_changes(records, "t/api", r, a, base_ok=True, head_ok=True), (r, a))
        self.assertEqual(records, [])
        self.assertEqual(guard_route_changes(records, "t/api", r, a, base_ok=True, head_ok=False, why="shop/urls.py did not parse"), ([], a))
        self.assertEqual(guard_route_changes(records, "t/api", r, a, base_ok=False, head_ok=True, why="the base could not be read"), (r, []))
        self.assertEqual([(x.kind, x.subject, x.blocks_selection) for x in records], [("route_change_withheld", "t/api", True)] * 2)
        self.assertIn("did not parse", records[0].detail)

    def test_nothing_withheld_means_no_record(self):
        records = []
        guard_route_changes(records, "t/api", [], [], base_ok=False, head_ok=False)
        self.assertEqual(records, [])


class FastApi(CrossCase):
    SHARED = True

    def build(self, api_commits):
        self.repos(web=[{"files": WEB}], api=[{"files": API}, *api_commits])
        return self.workspace(workspace_text())

    def kinds_of(self, result):
        return {k for k, _s, _b in self.kinds()}

    def test_an_unclosed_parenthesis_in_a_router_file_gives_no_removed_route_finding_and_an_incomplete_answer(self):
        ws = self.build([{"files": {"app/routers/orders.py": BROKEN}}])
        result = self.propagate(ws, "t/api", "main~1...main")
        self.assertEqual([f.kind for f in result.findings if f.kind in ("removed_route_still_called", "added_route_uncalled")], [])
        self.assertEqual([m for m in result.http_edges if m.removed], [])
        kinds = self.kinds_of(result)
        self.assertIn("parse_degraded", kinds)
        self.assertIn("route_change_withheld", kinds)
        self.assertTrue(any(b for k, _s, b in self.kinds() if k == "parse_degraded"))
        self.assertFalse(result.complete)

    def test_the_adapter_itself_withholds_the_removals_of_a_table_it_could_not_read(self):
        self.repos(web=[{"files": WEB}], api=[{"files": API}, {"files": {"app/routers/orders.py": BROKEN}}])
        api = self.paths["api"]
        from tests import support_git as sg
        from reachmap.gitlayer import Git

        records = []
        git = Git(self.tmp / "cache2", env=sg.isolated_env(self.tmp / "home2"), record_to=records)
        language = PyAdapter("t/api", self.urls["api"], git)
        entry = FastApiAdapter("t/api", language)
        removed, added = entry.routes_changed(sg.rev(api, "main~1"), sg.rev(api, "main"))
        self.assertEqual((removed, added), ([], []))
        self.assertFalse(entry.routes_complete(sg.rev(api, "main"))[0])
        self.assertTrue(entry.routes_complete(sg.rev(api, "main~1"))[0])
        self.assertIn("route_change_withheld", {r.kind for r in records})

    def test_a_base_that_could_not_be_read_does_not_make_the_routes_of_the_head_new(self):
        self.repos(web=[{"files": WEB}], api=[{"files": {**API, "app/routers/orders.py": BROKEN}}, {"files": {"app/routers/orders.py": ORDERS}}])
        ws = self.workspace(workspace_text())
        result = self.propagate(ws, "t/api", "main~1...main")
        self.assertEqual([f.kind for f in result.findings if f.kind == "added_route_uncalled"], [])

    def test_a_removal_between_two_readable_tables_is_still_a_finding(self):
        ws = self.build([{"files": {"app/routers/orders.py": ORDERS.replace('@router.get("/summary")\ndef summary():\n    return {}\n\n\n', "")}}])
        result = self.propagate(ws, "t/api", "main~1...main")
        self.assertEqual([f.kind for f in result.findings if f.kind == "removed_route_still_called"], ["removed_route_still_called"])
        self.assertNotIn("route_change_withheld", self.kinds_of(result))


class NextJs(NextBase):
    """A route handler of Next.js that cannot be read at the head (not text) has routes that were not read, not routes that were deleted."""

    def test_a_route_file_that_cannot_be_read_removes_nothing_and_a_readable_deletion_does(self):
        good = next_route("GET", "POST")
        files = {"tsconfig.json": TSCONFIG, "src/dep.ts": "export function dep() {\n  return 1;\n}\n", "app/api/items/route.ts": good}
        self.repo({"files": files}, {"files": {"app/api/items/route.ts": b"\xff\xfe" + good.encode()}})
        entry = self.entry()
        before, after = self.sha("main~1"), self.sha("main")
        self.assertEqual(entry.routes_changed(before, after), ([], []))
        self.assertEqual(entry.screens_changed(before, after), ([], []))
        self.assertEqual((entry.routes_complete(before)[0], entry.routes_complete(after)[0]), (True, False))
        self.assertIn("route_change_withheld", {r.kind for r in self.records})
        self.repo({"files": files}, {"files": {"app/api/items/route.ts": next_route("GET")}})
        entry = self.entry()
        removed, added = entry.routes_changed(self.sha("main~1"), self.sha("main"))
        self.assertEqual(([r.label for r in removed], added), (["POST /api/items"], []))


class CoreGuard(unittest.TestCase):
    """An entry-point adapter that does not guard its own `routes_changed` is guarded by the http edge, by what it says it could not read."""

    def run_case(self, entry, removed, added):
        entry.routes_changed = lambda before, after: (list(removed), list(added))
        run = SimpleNamespace(target=[], memo=lambda key, make: make(), entry=lambda repo: entry)
        p = SimpleNamespace(repo="t/api", sha="h" * 40, seed=None, change=SimpleNamespace(pre_change_sha="b" * 40, head_sha="h" * 40))
        out = HttpEdgeAdapter()._changed_routes(run, p)
        return out, run.target

    def test_an_adapter_that_reports_problems_has_its_removals_and_additions_withheld_with_a_record(self):
        entry = SimpleNamespace(routes=lambda repo, sha: [], problems=["a file did not parse"])
        (removed, added), records = self.run_case(entry, [route("GET", "/gone")], [route("GET", "/new")])
        self.assertEqual((removed, added), ([], []))
        self.assertEqual([(r.kind, r.blocks_selection) for r in records], [("route_change_withheld", True)])

    def test_an_adapter_that_says_which_version_is_whole_is_asked(self):
        entry = SimpleNamespace(routes=lambda repo, sha: [], problems=["x"], routes_complete=lambda sha: (sha.startswith("b"), ["the head did not parse"]))
        (removed, added), records = self.run_case(entry, [route("GET", "/gone")], [route("GET", "/new")])
        self.assertEqual((removed, [r.path for r in added]), ([], ["/new"]))  # the base is whole: the addition stands; the head is not: no removal
        self.assertEqual(len(records), 1)

    def test_an_adapter_that_guards_itself_is_left_alone(self):
        entry = SimpleNamespace(routes=lambda repo, sha: [], problems=["x"], withholds_unread=True)
        (removed, added), records = self.run_case(entry, [route("GET", "/gone")], [route("GET", "/new")])
        self.assertEqual(([r.path for r in removed], [r.path for r in added], records), (["/gone"], ["/new"], []))

    def test_an_adapter_with_no_problems_is_not_touched(self):
        entry = SimpleNamespace(routes=lambda repo, sha: [], problems=[])
        (removed, added), records = self.run_case(entry, [route("GET", "/gone")], [])
        self.assertEqual(([r.path for r in removed], records), (["/gone"], []))


if __name__ == "__main__":
    unittest.main()
