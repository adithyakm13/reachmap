"""The in-repo walk (`reachmap.walk`) on small repositories built for one rule each: chains of real Evidence from the
changed file up to an entry-point file, narrowing carried hop by hop, the hub rule (one rule, recorded once), the
per-hop module cap, cycles, incomplete importer lists, barrels and Python modules.

The entry-point adapter is a stub here (a set of paths that are entry files): the React Router and FastAPI adapters
have their own tests and the M1 scenarios are in test_scenarios_*.py. The language adapters are the real ones.
"""

import unittest
from unittest import mock

from reachmap import walk as walkmod
from reachmap.adapters import Reach
from reachmap.adapters.js import JsAdapter
from reachmap.adapters.python import PyAdapter
from tests import scenario_support as ss
from tests.support_universe import Base as UniverseBase
from tests.support_universe import f

MARKERS = ["__tests__/", ".test.", ".spec."]


class Entries:
    """A stub entry-point adapter: these paths are the entry-point files."""

    def __init__(self, *paths):
        self.paths = set(paths)

    def is_entry_file(self, path, sha):
        return path in self.paths


class WiredEntries(Entries):
    """A stub that also says which exported names of an entry file only wire it up (as the real adapters do): the walk
    goes past an entry file through the other names."""

    def __init__(self, *paths, structural=None):
        super().__init__(*paths)
        self.structural = structural or {}

    def structural_names(self, path, sha):
        return self.structural.get(path, set())


class JsBase(UniverseBase):
    UNIVERSE_PREFIX = "reachmap-walk-js-"
    REPO = "acme/web"

    def make_adapter(self, **kw):
        kw.setdefault("test_markers", MARKERS)
        return JsAdapter(self.REPO, self.url, self.git, **kw)

    def run_walk(self, entries, *, adapter_kw=None, **kw):
        adapter = self.make_adapter(**(adapter_kw or {}))
        analysis = self.analyze(adapter)
        res = walkmod.walk(analysis, adapter, Entries(*entries), **kw)
        self.assert_evidence_resolves_chains(res)
        return res

    def assert_evidence_resolves_chains(self, res):
        run = mock.Mock()
        run.analysis = None
        evidence = [e for c in res.chains for e in c.hops]
        self.assertEqual(ss.evidence_problems({self.REPO: self.path}, evidence), [])

    @staticmethod
    def entries_of(res):
        return [c.entry for c in res.chains]


def util(body="return x + 1;"):
    return f(["export function fmt(x: number) {", f"  {body}", "}", "", "export function other() {", "  return 2;", "}"])


class ChainsOfEvidence(JsBase):
    def setUp(self):
        super().setUp()
        base = {
            "src/util.ts": util(),
            "src/comp.tsx": f(['import { fmt } from "./util";', "", "export function Badge() {", "  return fmt(1);", "}", "", "export function Extra() {", "  return 1;", "}"]),
            "src/ot.tsx": f(['import { other } from "./util";', "", "export function Other() {", "  return other();", "}"]),
            "src/page.tsx": f(['import { Badge } from "./comp";', "", "export default function Page() {", "  return Badge();", "}"]),
            "src/page2.tsx": f(['import { Other } from "./ot";', "", "export default function Page2() {", "  return Other();", "}"]),
            "src/page3.tsx": f(['import { Extra } from "./comp";', "", "export default function Page3() {", "  return Extra();", "}"]),
            "src/routes.tsx": f(['import Page from "./page";', 'import Page2 from "./page2";', 'import Page3 from "./page3";', "", "export const routes = [Page, Page2, Page3];"]),
        }
        self.use_shared("chains", {"files": base}, {"files": {"src/util.ts": util("return x + 2;")}})

    def test_a_chain_is_the_import_of_each_file_up_to_the_entry_file(self):
        res = self.run_walk(["src/routes.tsx"])
        (chain,) = res.chains
        self.assertEqual(chain.files, ("src/util.ts", "src/comp.tsx", "src/page.tsx", "src/routes.tsx"))
        self.assertEqual([(e.path, e.line, e.detail) for e in chain.hops], [
            ("src/comp.tsx", 1, 'import { fmt } from "./util";'),
            ("src/page.tsx", 1, 'import { Badge } from "./comp";'),
            ("src/routes.tsx", 1, 'import Page from "./page";'),
        ])
        self.assertEqual(chain.edge.importer_path, "src/routes.tsx")
        self.assertEqual((chain.entry, chain.sha, chain.complete), ("src/routes.tsx", self.sha("main"), True))
        self.assertTrue(res.complete)

    def test_the_first_hop_is_narrowed_by_the_names_the_change_reaches(self):
        """Only fmt changed: `ot.tsx` imports `other`, so Page2 is not walked."""
        res = self.run_walk(["src/routes.tsx"])
        self.assertNotIn("src/ot.tsx", [p for c in res.chains for p in c.files])
        self.assertNotIn("src/page2.tsx", [p for c in res.chains for p in c.files])

    def test_every_further_hop_is_narrowed_by_the_names_that_reached_it(self):
        """Badge reaches page.tsx, Extra does not use fmt: page3 imports only Extra from comp.tsx."""
        res = self.run_walk(["src/routes.tsx"])
        self.assertNotIn("src/page3.tsx", [p for c in res.chains for p in c.files])
        self.assertEqual(res.chains[0].names, frozenset({"default"}))  # the names that reached routes.tsx: what page.tsx exports

    def test_a_hop_walks_nothing_past_an_entry_file(self):
        res = self.run_walk(["src/page.tsx", "src/routes.tsx"])
        self.assertEqual(self.entries_of(res), ["src/page.tsx"])  # routes.tsx imports page.tsx, but the walk stopped at the page

    def test_the_depth_of_each_hop_is_counted(self):
        res = self.run_walk(["src/routes.tsx"])
        self.assertEqual(res.depths, (1, 1, 1))  # comp, page, routes
        self.assertEqual(res.expanded, 2)  # the importers of comp and of page were searched; the entry file was not expanded

    def test_records_go_where_the_caller_says(self):
        adapter = self.make_adapter()
        analysis = self.analyze(adapter)
        mine = []
        walkmod.walk(analysis, adapter, Entries("src/routes.tsx"), record_to=mine, hub_importers=0, module_cap=100)
        self.assertEqual([r.kind for r in mine], ["hub_cutoff"])  # comp.tsx has two importers, the limit is zero
        self.assertEqual(self.records, [])


class DirectChange(JsBase):
    def test_a_change_inside_the_entry_file_is_a_chain_of_the_changed_line(self):
        routes = f(['import Page from "./page";', "", "export const routes = [", "  Page,", "];"])
        self.repo({"files": {"src/routes.tsx": routes, "src/page.tsx": "export default function Page() {}\n"}},
                  {"files": {"src/routes.tsx": routes.replace("  Page,\n", "  Page,\n  Page,\n")}})
        res = self.run_walk(["src/routes.tsx"])
        (chain,) = res.chains
        self.assertTrue(chain.direct)
        self.assertEqual((chain.files, chain.edge), (("src/routes.tsx",), None))
        self.assertEqual([(e.path, e.line, e.detail) for e in chain.hops], [("src/routes.tsx", 5, "  Page,")])
        self.assertEqual([s.name for s in chain.changed], ["routes"])
        self.assertEqual(chain.reach.locals, frozenset({"routes"}))

    def test_an_entry_file_that_is_deleted_is_not_a_chain(self):
        self.repo({"files": {"src/routes.tsx": "export const routes = [];\n"}}, {"delete": ["src/routes.tsx"]})
        res = self.run_walk(["src/routes.tsx"])
        self.assertEqual(res.chains, ())


class Hubs(JsBase):
    def test_a_hub_is_not_passed_through_and_is_recorded_once_whatever_reaches_it(self):
        files = {
            "src/u1.ts": "export function one() {\n  return 1;\n}\n",
            "src/u2.ts": "export function two() {\n  return 2;\n}\n",
            "src/hub.ts": f(['import { one } from "./u1";', 'import { two } from "./u2";', "", "export function both() {", "  return one() + two();", "}"]),
            "src/page.tsx": 'import { both } from "./hub";\nexport function P() {\n  return both();\n}\n',
        }
        for i in range(4):
            files[f"src/w{i}.tsx"] = f(['import { both } from "./hub";', f"export function W{i}() {{", "  return both();", "}"])
        self.repo({"files": files}, {"files": {"src/u1.ts": "export function one() {\n  return 11;\n}\n", "src/u2.ts": "export function two() {\n  return 22;\n}\n"}})
        res = self.run_walk(["src/page.tsx", "src/w0.tsx"], adapter_kw={"hub_importers": 3})
        self.assertEqual(res.chains, ())
        self.assertFalse(res.complete)
        hubs = [(r.kind, r.subject, r.blocks_selection) for r in self.records]
        self.assertEqual(hubs, [("hub_cutoff", "src/hub.ts", False)])  # reached from two changed files, said once
        self.assertEqual(res.expanded, 2)

    def test_the_language_adapters_and_the_walk_share_one_hub_rule(self):
        self.assertFalse(walkmod.exceeds_hub(50, 50))
        self.assertTrue(walkmod.exceeds_hub(51, 50))
        self.assertFalse(walkmod.exceeds_hub(10 ** 6, None))
        target = []
        walkmod.record_hub_cutoff(target, "a.ts", 51, 50)
        walkmod.record_hub_cutoff(target, "a.ts", 51, 50)
        self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in target], [("hub_cutoff", "a.ts", False)])
        # the changed file of the first hop gets the same record from the language adapter
        files = {"src/h.ts": "export function h() {\n  return 1;\n}\n"}
        for i in range(3):
            files[f"src/c{i}.ts"] = f(['import { h } from "./h";', f"export function c{i}() {{", "  return h();", "}"])
        self.repo({"files": files}, {"files": {"src/h.ts": "export function h() {\n  return 2;\n}\n"}})
        adapter = self.make_adapter(hub_importers=2)
        analysis = self.analyze(adapter)
        self.assertTrue(analysis.impact_for("src/h.ts").hub)
        self.assertEqual([(r.kind, r.subject) for r in self.records], [("hub_cutoff", "src/h.ts")])
        res = walkmod.walk(analysis, adapter, Entries("src/c0.ts"))
        self.assertEqual((res.chains, [(r.kind, r.subject) for r in self.records]), ((), [("hub_cutoff", "src/h.ts")]))  # not walked, not recorded twice


class ModuleCap(JsBase):
    def setUp(self):
        super().setUp()
        files = {"src/u.ts": "export function u() {\n  return 1;\n}\n"}
        for i in range(5):
            files[f"src/c{i}.ts"] = f(['import { u } from "./u";', f"export function c{i}() {{", "  return u();", "}"])
            files[f"src/p{i}.tsx"] = f([f'import {{ c{i} }} from "./c{i}";', f"export function p{i}() {{", f"  return c{i}();", "}"])
        self.use_shared("cap", {"files": files}, {"files": {"src/u.ts": "export function u() {\n  return 2;\n}\n"}})
        self.entries = [f"src/p{i}.tsx" for i in range(5)]

    def test_under_the_cap_every_chain_is_found_and_nothing_is_recorded(self):
        res = self.run_walk(self.entries, module_cap=5)
        self.assertEqual(self.entries_of(res), self.entries)
        self.assertEqual((res.depths, res.complete, self.records), ((5, 5), True, []))

    def test_over_the_cap_the_hop_is_the_last_one_expanded_and_the_set_is_not_complete(self):
        res = self.run_walk(self.entries, module_cap=3)
        self.assertEqual(res.chains, ())  # five files at hop 1, three kept, none of them expanded
        self.assertFalse(res.complete)
        self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in self.records], [("module_cap", "hop:1", False)])
        self.assertIn("module cap", res.reasons[0])

    def test_the_hop_number_counts_files_reached_by_distance_from_the_change(self):
        """A fan-out at the second hop is `hop:2`; every entry file of it has its chain, the cap limits the expansion."""
        files = {"src/u.ts": "export function u() {\n  return 1;\n}\n", "src/mid.ts": f(['import { u } from "./u";', "export function m() {", "  return u();", "}"])}
        for i in range(5):
            files[f"src/q{i}.tsx"] = f(['import { m } from "./mid";', f"export function q{i}() {{", "  return m();", "}"])
        self.repo({"files": files}, {"files": {"src/u.ts": "export function u() {\n  return 2;\n}\n"}})
        res = self.run_walk([f"src/q{i}.tsx" for i in range(5)], module_cap=3)
        self.assertEqual([r.subject for r in self.records if r.kind == "module_cap"], ["hop:2"])
        self.assertEqual(self.entries_of(res), [f"src/q{i}.tsx" for i in range(5)])  # entry files beyond the cap are known: they count
        self.assertFalse(res.complete)
        self.assertEqual(res.depths, (1, 5))


class EntryFilesAtTheCap(JsBase):
    def test_entry_files_beyond_the_cap_of_a_hop_are_still_chains(self):
        files = {"src/u.ts": "export function u() {\n  return 1;\n}\n"}
        for i in range(5):
            files[f"src/r{i}.tsx"] = f(['import { u } from "./u";', f"export const routes{i} = [u];"])
        self.repo({"files": files}, {"files": {"src/u.ts": "export function u() {\n  return 2;\n}\n"}})
        res = self.run_walk([f"src/r{i}.tsx" for i in range(5)], module_cap=3)
        self.assertEqual(self.entries_of(res), [f"src/r{i}.tsx" for i in range(5)])  # all five are known entry files: none is dropped
        self.assertEqual([(r.kind, r.subject) for r in self.records], [("module_cap", "hop:1")])
        self.assertFalse(res.complete)


class PastEntryFiles(JsBase):
    """A change reaches more through an entry file than its routes: a helper that it exports and another module imports."""

    def files(self):
        return {
            "src/u.ts": "export function u() {\n  return 1;\n}\n",
            "src/e1.tsx": f(['import { u } from "./u";', "export const routes = [A];", "export function helper() {", "  return u();", "}", "export function A() {", "  return helper();", "}"]),
            "src/e2.tsx": f(['import { helper } from "./e1";', "export function B() {", "  return helper();", "}"]),
            "src/e3.tsx": f(['import { A } from "./e1";', "export function C() {", "  return A();", "}"]),
            "src/main.tsx": 'import { routes } from "./e1";\nexport const main = routes;\n',
        }

    def test_a_helper_exported_by_an_entry_file_carries_the_change_to_the_entry_files_that_import_it(self):
        files = self.files()
        self.repo({"files": files}, {"files": {"src/u.ts": "export function u() {\n  return 2;\n}\n"}})
        adapter = self.make_adapter()
        analysis = self.analyze(adapter)
        entries = WiredEntries("src/e1.tsx", "src/e2.tsx", "src/e3.tsx", structural={"src/e1.tsx": {"routes"}})
        res = walkmod.walk(analysis, adapter, entries)
        by = {c.entry: c for c in res.chains}
        self.assertEqual(sorted(by), ["src/e1.tsx", "src/e2.tsx", "src/e3.tsx"])
        self.assertEqual(by["src/e2.tsx"].files, ("src/u.ts", "src/e1.tsx", "src/e2.tsx"))  # through helper
        self.assertEqual(by["src/e3.tsx"].files, ("src/u.ts", "src/e1.tsx", "src/e3.tsx"))  # through A, which uses helper
        self.assertNotIn("src/main.tsx", [p for c in res.chains for p in c.files])  # `routes` only wires e1 up: whoever imports it mounts it

    def test_a_change_inside_the_entry_file_goes_on_the_same_way_and_the_closure_is_the_reach(self):
        files = self.files()
        new = files["src/e1.tsx"].replace("  return u();", "  return u() + 1;")
        self.repo({"files": files}, {"files": {"src/e1.tsx": new}})
        adapter = self.make_adapter()
        analysis = self.analyze(adapter)
        res = walkmod.walk(analysis, adapter, WiredEntries("src/e1.tsx", "src/e2.tsx", "src/e3.tsx", structural={"src/e1.tsx": {"routes"}}))
        by = {c.entry: c for c in res.chains}
        self.assertEqual(sorted(by), ["src/e1.tsx", "src/e2.tsx", "src/e3.tsx"])
        direct = by["src/e1.tsx"]
        self.assertTrue(direct.direct)
        self.assertEqual(direct.reach.locals, frozenset({"helper", "A", "routes"}))  # the declarations that use the changed one, to a fixed point
        self.assertEqual(direct.reach.names, frozenset({"helper", "A", "routes"}))
        self.assertFalse(direct.reach.whole)

    def test_an_entry_adapter_that_does_not_name_the_wiring_is_not_walked_past(self):
        self.repo({"files": self.files()}, {"files": {"src/u.ts": "export function u() {\n  return 2;\n}\n"}})
        res = self.run_walk(["src/e1.tsx", "src/e2.tsx", "src/e3.tsx"])
        self.assertEqual(self.entries_of(res), ["src/e1.tsx"])

    def test_a_change_that_reaches_the_whole_module_of_an_entry_file_reaches_it_whole(self):
        files = self.files()
        files["src/e1.tsx"] = files["src/e1.tsx"].replace("export const routes = [A];", "setup(u);\nexport const routes = [A];")
        self.repo({"files": files}, {"files": {"src/u.ts": "export function u() {\n  return 2;\n}\n"}})
        adapter = self.make_adapter()
        analysis = self.analyze(adapter)
        res = walkmod.walk(analysis, adapter, WiredEntries("src/e1.tsx", "src/e2.tsx", structural={"src/e1.tsx": {"routes"}}))
        (chain,) = res.chains
        self.assertTrue(chain.reach.whole)  # `setup(u)` runs when e1.tsx loads: every route in it is affected
        self.assertIsNone(chain.reach.names)


class CyclesAndDiamonds(JsBase):
    def test_a_cycle_in_the_imports_terminates(self):
        files = {
            "src/u.ts": "export function u() {\n  return 1;\n}\n",
            "src/a.ts": f(['import { u } from "./u";', 'import { b } from "./b";', "export function a() {", "  return u() + b();", "}"]),
            "src/b.ts": f(['import { a } from "./a";', "export function b() {", "  return a();", "}"]),
            "src/routes.tsx": 'import { b } from "./b";\nexport const routes = [b];\n',
        }
        self.repo({"files": files}, {"files": {"src/u.ts": "export function u() {\n  return 2;\n}\n"}})
        res = self.run_walk(["src/routes.tsx"])
        self.assertEqual([c.files for c in res.chains], [("src/u.ts", "src/a.ts", "src/b.ts", "src/routes.tsx")])
        self.assertEqual(res.expanded, 2)  # a.ts and b.ts: b.ts is imported by a.ts, which is already on the path, and is not entered again

    def test_a_second_parent_that_brings_the_same_names_through_other_imports_reaches_other_declarations(self):
        """page.tsx takes `Badge` from a.ts and `Badge as B2` from b.ts: PA uses one, PB the other, and each is rendered
        by its own entry file. The names that arrive are the same, the imports are not: both chains exist."""
        badge = f(['import { u } from "./u";', "export function Badge() {", "  return u();", "}"])
        files = {
            "src/u.ts": "export function u() {\n  return 1;\n}\n",
            "src/a.ts": badge,
            "src/b.ts": badge,
            "src/page.tsx": f(['import { Badge } from "./a";', 'import { Badge as B2 } from "./b";',
                               "export function PA() {", "  return Badge();", "}", "export function PB() {", "  return B2();", "}"]),
            "src/ra.tsx": 'import { PA } from "./page";\nexport const routes = [PA];\n',
            "src/rb.tsx": 'import { PB } from "./page";\nexport const routes = [PB];\n',
        }
        self.repo({"files": files}, {"files": {"src/u.ts": "export function u() {\n  return 2;\n}\n"}})
        res = self.run_walk(["src/ra.tsx", "src/rb.tsx"])
        self.assertEqual(sorted(c.entry for c in res.chains), ["src/ra.tsx", "src/rb.tsx"])
        self.assertTrue(res.complete)

    def test_a_file_reached_again_by_the_same_imports_with_the_same_names_is_expanded_once(self):
        """Two changed files reach m.tsx through p.tsx with the same names and the same imports of p.tsx: m.tsx is
        expanded and walked once, not twice."""
        files = {
            "src/u1.ts": "export function fmt() {\n  return 1;\n}\n",
            "src/u2.ts": "export function fmt() {\n  return 1;\n}\n",
            "src/p.tsx": f(['import { fmt as f1 } from "./u1";', 'import { fmt as f2 } from "./u2";', "export function Badge() {", "  return f1() + f2();", "}"]),
            "src/m.tsx": f(['import { Badge } from "./p";', "export function M() {", "  return Badge();", "}"]),
            "src/routes.tsx": 'import { M } from "./m";\nexport const routes = [M];\n',
        }
        self.repo({"files": files}, {"files": {"src/u1.ts": files["src/u1.ts"].replace("return 1", "return 2"), "src/u2.ts": files["src/u2.ts"].replace("return 1", "return 2")}})
        res = self.run_walk(["src/routes.tsx"])
        self.assertEqual(len(res.chains), 1)
        self.assertEqual(res.expanded, 3)  # p.tsx once for each of its two imports, m.tsx once

    def test_a_lattice_of_files_with_two_parents_each_stays_bounded(self):
        """The same names through different imports are walked, but a lattice does not blow up: the work is bounded by
        the distinct (names, imports) of each file."""
        width, depth = 4, 6
        files = {"src/x0_0.ts": "".join(f"export function e{k}() {{\n  return 1;\n}}\n" for k in range(3))}
        for i in range(1, width):
            files[f"src/x0_{i}.ts"] = 'import { e0 as a0, e1 as a1, e2 as a2 } from "./x0_0";\n' + "".join(f"export function e{k}() {{\n  return a{k}();\n}}\n" for k in range(3))
        for k in range(1, depth):
            for i in range(width):
                a, b = f"x{k - 1}_{i}", f"x{k - 1}_{(i + 1) % width}"
                files[f"src/x{k}_{i}.ts"] = (f'import {{ e0 as a0, e1 as a1 }} from "./{a}";\nimport {{ e0 as b0, e2 as b2 }} from "./{b}";\n'
                                           "export function e0() {\n  return a0();\n}\nexport function e1() {\n  return b2();\n}\nexport function e2() {\n  return a1() + b0();\n}\n")
        imports = "\n".join(f'import {{ e0 as r{i}, e1 as s{i}, e2 as t{i} }} from "./x{depth - 1}_{i}";' for i in range(width))
        files["src/routes.tsx"] = imports + "\nexport const routes = [" + ", ".join(f"r{i}, s{i}, t{i}" for i in range(width)) + "];\n"
        self.repo({"files": files}, {"files": {"src/x0_0.ts": files["src/x0_0.ts"].replace("return 1", "return 2", 1)}})
        res = self.run_walk(["src/routes.tsx"], module_cap=10 ** 6, hub_importers=10 ** 6)
        self.assertGreater(len(res.chains), 0)
        self.assertLess(res.expanded, 4 * (width * depth + 1))


class Completeness(JsBase):
    def files(self, **extra):
        files = {
            "src/u.ts": "export function u() {\n  return 1;\n}\n",
            "src/comp.tsx": f(['import { u } from "./u";', "export function Badge() {", "  return u();", "}"]),
            "src/page.tsx": f(['import { Badge } from "./comp";', "export default function Page() {", "  return Badge();", "}"]),
            "src/routes.tsx": 'import Page from "./page";\nexport const routes = [Page];\n',
        }
        files.update(extra)
        return files

    def walk_changed(self, files, **kw):
        self.repo({"files": files}, {"files": {"src/u.ts": "export function u() {\n  return 2;\n}\n"}})
        return self.run_walk(["src/routes.tsx"], **kw)

    def test_a_file_that_nothing_cuts_short_is_complete(self):
        res = self.walk_changed(self.files())
        self.assertEqual([(c.complete, c.reasons) for c in res.chains], [(True, ())])
        self.assertEqual(self.records, [])

    def test_an_importer_list_that_could_not_be_established_makes_the_chains_through_the_file_incomplete(self):
        dyn = f(["// comp", "export const load = (name: string) => import(name);"])
        res = self.walk_changed(self.files(**{"src/dyn.ts": dyn}))
        (chain,) = res.chains  # the chain is real ...
        self.assertFalse(chain.complete)  # ... but the importers of comp.tsx were not established completely
        self.assertIn("src/comp.tsx", " ".join(chain.reasons))
        self.assertFalse(res.complete)
        self.assertIn(("specifier_unresolved", True), [(r.kind, r.blocks_selection) for r in self.records])  # the language adapter's record stays

    def test_an_importer_list_that_is_incomplete_only_further_out_still_makes_the_chain_incomplete(self):
        """Nothing at the first hop is incomplete: a broken file that mentions `page` makes the importers of page.tsx (the
        second hop) incomplete, and the chain that runs through page.tsx says so."""
        files = {
            "src/util.ts": "export function fmt() {\n  return 1;\n}\n",
            "src/comp.tsx": f(['import { fmt } from "./util";', "export function Badge() {", "  return fmt();", "}"]),
            "src/page.tsx": f(['import { Badge } from "./comp";', "export default function Page() {", "  return Badge();", "}"]),
            "src/other.tsx": f(['import Page from "./page";', "export function Other() {", "  return Page();", "}"]),
            "src/routes.tsx": 'import { Other } from "./other";\nexport const routes = [Other];\n',
            "src/broken.ts": f(["// page", 'const s = "unterminated;']),
        }
        self.repo({"files": files}, {"files": {"src/util.ts": "export function fmt() {\n  return 2;\n}\n"}})
        res = self.run_walk(["src/routes.tsx"])
        self.assertTrue(self.analysis.impacts[0].importers_complete)  # the changed file's own list is complete
        self.assertEqual([c.complete for c in res.chains], [False])
        self.assertIn("src/page.tsx", " ".join(res.chains[0].reasons))
        self.assertFalse(res.complete)

    def test_an_incomplete_list_at_the_changed_file_makes_every_chain_incomplete(self):
        dyn = f(["// u", "export const load = (name: string) => import(name);"])
        res = self.walk_changed(self.files(**{"src/dyn.ts": dyn}))
        (chain,) = res.chains
        self.assertFalse(chain.complete)
        self.assertIn("src/u.ts", " ".join(chain.reasons))

    def test_a_file_in_the_chain_that_does_not_lex_cleanly_makes_it_incomplete(self):
        res = self.walk_changed(self.files(**{"src/page.tsx": f(['import { Badge } from "./comp";', "export default function Page() {", "  return Badge();"])}))
        # an unclosed brace: page.tsx did not lex cleanly (and is also a candidate with a recorded problem)
        self.assertTrue(all(not c.complete for c in res.chains))
        self.assertFalse(res.complete)


class UnitTestsAreNotWalked(JsBase):
    def test_a_test_that_imports_the_file_is_not_a_hop(self):
        files = {
            "src/u.ts": "export function u() {\n  return 1;\n}\n",
            "src/comp.tsx": f(['import { u } from "./u";', "export function Badge() {", "  return u();", "}"]),
            "src/comp.test.tsx": f(['import { Badge } from "./comp";', "test('x', () => Badge());"]),
            "src/routes.tsx": 'import { Badge } from "./comp";\nexport const routes = [Badge];\n',
        }
        self.repo({"files": files}, {"files": {"src/u.ts": "export function u() {\n  return 2;\n}\n"}})
        res = self.run_walk(["src/routes.tsx", "src/comp.test.tsx"])
        self.assertEqual(self.entries_of(res), ["src/routes.tsx"])


class Barrels(JsBase):
    def test_export_star_passes_the_reached_names_on_unchanged(self):
        files = {
            "src/lib/util.ts": "export function fmt() {\n  return 1;\n}\nexport function other() {\n  return 2;\n}\n",
            "src/lib/index.ts": 'export * from "./util";\n',
            "src/pa.tsx": f(['import { fmt } from "./lib";', "export default function PA() {", "  return fmt();", "}"]),
            "src/pb.tsx": f(['import { other } from "./lib";', "export default function PB() {", "  return other();", "}"]),
            "src/routes.tsx": 'import PA from "./pa";\nimport PB from "./pb";\nexport const routes = [PA, PB];\n',
        }
        self.repo({"files": files}, {"files": {"src/lib/util.ts": files["src/lib/util.ts"].replace("return 1;", "return 3;")}})
        res = self.run_walk(["src/routes.tsx"])
        self.assertEqual([c.files for c in res.chains], [("src/lib/util.ts", "src/lib/index.ts", "src/pa.tsx", "src/routes.tsx")])

    def test_a_barrel_is_walked_through_by_the_names_it_re_exports(self):
        files = {
            "src/lib/util.ts": "export function fmt() {\n  return 1;\n}\nexport function other() {\n  return 2;\n}\n",
            "src/lib/index.ts": 'export { fmt, other } from "./util";\n',
            "src/pa.tsx": f(['import { fmt } from "./lib";', "export default function PA() {", "  return fmt();", "}"]),
            "src/pb.tsx": f(['import { other } from "./lib";', "export default function PB() {", "  return other();", "}"]),
            "src/routes.tsx": 'import PA from "./pa";\nimport PB from "./pb";\nexport const routes = [PA, PB];\n',
        }
        self.repo({"files": files}, {"files": {"src/lib/util.ts": files["src/lib/util.ts"].replace("return 1;", "return 3;")}})
        res = self.run_walk(["src/routes.tsx"])
        (chain,) = res.chains
        self.assertEqual(chain.files, ("src/lib/util.ts", "src/lib/index.ts", "src/pa.tsx", "src/routes.tsx"))
        self.assertEqual([e.detail for e in chain.hops][:2], ['export { fmt, other } from "./util";', 'import { fmt } from "./lib";'])


class SeveralChangedFiles(JsBase):
    def test_two_changed_files_that_one_entry_file_imports_are_two_chains(self):
        files = {
            "src/a.tsx": "export default function A() {\n  return 1;\n}\n",
            "src/b.tsx": "export default function B() {\n  return 1;\n}\n",
            "src/routes.tsx": 'import A from "./a";\nimport B from "./b";\nexport const routes = [A, B];\n',
        }
        self.repo({"files": files}, {"files": {"src/a.tsx": files["src/a.tsx"].replace("return 1", "return 2"), "src/b.tsx": files["src/b.tsx"].replace("return 1", "return 2")}})
        res = self.run_walk(["src/routes.tsx"])
        self.assertEqual([(c.files, c.hops[0].line) for c in res.chains], [(("src/a.tsx", "src/routes.tsx"), 1), (("src/b.tsx", "src/routes.tsx"), 2)])


class Renamed(JsBase):
    def test_the_importers_of_a_renamed_file_are_walked_at_the_pre_change_commit_for_its_old_path(self):
        body = f(["export function u() {", "  return 1;", "}", "", "export function v() {", "  return 2;", "}", "", "export const W = 3;"])
        files = {
            "src/old.ts": body,
            "src/page.tsx": f(['import { u } from "./old";', "export default function Page() {", "  return u();", "}"]),
            "src/routes.tsx": 'import Page from "./page";\nexport const routes = [Page];\n',
        }
        self.repo({"files": files}, {"delete": ["src/old.ts"], "files": {"src/new.ts": body}})
        analysis = self.analyze(self.make_adapter())
        self.assertEqual(analysis.impact_for("src/new.ts").status, "renamed")
        res = self.run_walk(["src/routes.tsx"])
        (chain,) = res.chains
        self.assertEqual(chain.files, ("src/old.ts", "src/page.tsx", "src/routes.tsx"))
        self.assertEqual({e.ref for e in chain.hops}, {self.sha("main~1")})


class Deleted(JsBase):
    def test_the_importers_of_a_deleted_file_are_walked_at_the_pre_change_commit(self):
        files = {
            "src/u.ts": "export function u() {\n  return 1;\n}\n",
            "src/page.tsx": f(['import { u } from "./u";', "export default function Page() {", "  return u();", "}"]),
            "src/routes.tsx": 'import Page from "./page";\nexport const routes = [Page];\n',
        }
        self.repo({"files": files}, {"delete": ["src/u.ts"]})
        res = self.run_walk(["src/routes.tsx"])
        (chain,) = res.chains
        self.assertEqual(chain.sha, self.sha("main~1"))
        self.assertEqual({e.ref for e in chain.hops}, {self.sha("main~1")})


class PythonBase(UniverseBase):
    UNIVERSE_PREFIX = "reachmap-walk-py-"
    REPO = "acme/api"

    def make_adapter(self, **kw):
        kw.setdefault("test_markers", ["tests/", "test_"])
        return PyAdapter(self.REPO, self.url, self.git, **kw)

    def run_walk(self, entries, **kw):
        adapter = self.make_adapter()
        analysis = self.analyze(adapter)
        res = walkmod.walk(analysis, adapter, Entries(*entries), **kw)
        evidence = [e for c in res.chains for e in c.hops]
        self.assertEqual(ss.evidence_problems({self.REPO: self.path}, evidence), [])
        return res


class PythonModules(PythonBase):
    def files(self, billing_extra=""):
        return {
            "app/__init__.py": "",
            "app/services/__init__.py": "",
            "app/routers/__init__.py": "",
            "app/services/totals.py": f(["def round_total(x):", "    return x", "", "", "def unrelated():", "    return 1"]),
            "app/services/billing.py": f(["from app.services.totals import round_total", "", "", "def compute(x):", "    return round_total(x)", "", "", "def other_fn():", "    return 2", billing_extra]),
            "app/routers/inv.py": f(["from app.services.billing import compute", "", "", "def handler():", "    return compute(1)"]),
            "app/routers/oth.py": f(["from app.services.billing import other_fn", "", "", "def handler2():", "    return other_fn()"]),
        }

    def build(self, *, billing_extra=""):
        files = self.files(billing_extra)
        self.repo({"files": files}, {"files": {"app/services/totals.py": files["app/services/totals.py"].replace("    return x", "    return x + 1", 1)}})

    def test_a_python_chain_and_narrowing_at_the_second_hop(self):
        self.build()
        res = self.run_walk(["app/routers/inv.py", "app/routers/oth.py"])
        (chain,) = res.chains  # oth.py imports other_fn, which does not use round_total
        self.assertEqual(chain.files, ("app/services/totals.py", "app/services/billing.py", "app/routers/inv.py"))
        self.assertEqual([(e.path, e.line, e.detail) for e in chain.hops], [
            ("app/services/billing.py", 1, "from app.services.totals import round_total"),
            ("app/routers/inv.py", 1, "from app.services.billing import compute"),
        ])
        self.assertEqual(chain.reach.locals & {"handler", "compute"}, {"handler", "compute"})
        self.assertIn("handler", chain.reach.locals)
        self.assertEqual(chain.names, frozenset({"compute"}))

    def test_a_use_at_module_level_reaches_the_whole_module_and_stops_narrowing(self):
        self.build(billing_extra="RESULT = round_total(5)")
        res = self.run_walk(["app/routers/inv.py", "app/routers/oth.py"])
        self.assertEqual(self.entries_of(res), ["app/routers/inv.py", "app/routers/oth.py"])
        self.assertTrue(all(c.names is None for c in res.chains))

    @staticmethod
    def entries_of(res):
        return [c.entry for c in res.chains]

    def test_the_python_hub_rule_is_the_same_rule(self):
        self.build()
        adapter = self.make_adapter(hub_importers=1)
        analysis = self.analyze(adapter)
        res = walkmod.walk(analysis, adapter, Entries("app/routers/inv.py", "app/routers/oth.py"))
        # billing.py has two importers (the limit is one): not passed through, said once, and the set says it is not whole
        self.assertEqual(res.chains, ())
        self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in self.records], [("hub_cutoff", "app/services/billing.py", False)])
        self.assertFalse(res.complete)


class ReachOfAFile(PythonBase):
    """`PyAdapter.reach` and `JsAdapter.reach` directly: what the use of a name touches inside the importer."""

    def test_python_reach_closes_over_declarations_and_registrations(self):
        files = {
            "m.py": "def f():\n    return 1\n",
            "n.py": f(["from m import f", "", "REGISTRY = {}", "", "", "def helper():", "    return f()", "", "", "def wrapper():", "    return helper()", "", "", "def alone():", "    return 0"]),
        }
        self.repo({"files": files}, {"files": {"m.py": "def f():\n    return 2\n"}})
        adapter = self.make_adapter()
        edges = adapter.importers("m.py", self.sha("main")).edges
        reach = adapter.reach("n.py", edges, self.sha("main"), frozenset({"f"}))
        self.assertEqual(reach.names, frozenset({"helper", "wrapper"}))  # alone() does not use f
        self.assertEqual(reach.locals, frozenset({"f", "helper", "wrapper"}))
        self.assertTrue(reach.complete)

    def test_python_reach_of_a_file_that_does_not_parse_is_incomplete(self):
        self.repo({"files": {"m.py": "def f():\n    return 1\n", "n.py": "from m import f\nprint f()\n"}}, {"files": {"m.py": "def f():\n    return 2\n"}})
        adapter = self.make_adapter()
        edges = adapter.importers("m.py", self.sha("main")).edges
        reach = adapter.reach("n.py", edges, self.sha("main"), frozenset({"f"}), [])
        self.assertEqual((reach.complete, reach.names), (False, None))

    def test_an_import_that_is_never_used_reaches_the_whole_module(self):
        self.repo({"files": {"m.py": "def f():\n    return 1\n", "n.py": "from m import f\n\n\ndef g():\n    return 2\n"}}, {"files": {"m.py": "def f():\n    return 2\n"}})
        adapter = self.make_adapter()
        edges = adapter.importers("m.py", self.sha("main")).edges
        reach = adapter.reach("n.py", edges, self.sha("main"), frozenset({"f"}))
        self.assertIsNone(reach.names)  # importing runs the module: undecidable means affected


class OwnReach(PythonBase):
    def test_python_own_reach_closes_over_the_declarations_that_use_the_changed_one(self):
        src = f(["REGISTRY = {}", "", "", "def helper():", "    return 1", "", "", "@route", "def handler():", "    return helper()", "", "", "def wrapper():", "    return handler()", "", "", "def alone():", "    return 0"])
        self.repo({"files": {"m.py": src}})
        adapter = self.make_adapter()
        reach = adapter.own_reach("m.py", self.sha("main"), {"helper"})
        self.assertEqual(reach.locals, frozenset({"helper", "handler", "wrapper"}))
        self.assertEqual(reach.names, frozenset({"helper", "handler", "wrapper"}))  # `route` is imported, not declared here, so it is not exported by the file
        self.assertFalse(reach.whole)

    def test_python_own_reach_of_a_name_used_at_module_level_is_the_whole_module(self):
        self.repo({"files": {"m.py": f(["def helper():", "    return 1", "", "", "setup(helper)"])}})
        reach = self.make_adapter().own_reach("m.py", self.sha("main"), {"helper"})
        self.assertEqual((reach.whole, reach.names), (True, None))

    def test_a_registration_receiver_is_exported_but_does_not_make_the_module_whole_or_reach_its_other_handlers(self):
        src = f(["router = APIRouter()", "app = FastAPI()", "app.include_router(router)", "", "", "@router.get('/a')", "def a():", "    return 1", "", "", "@router.get('/b')", "def b():", "    return 2"])
        self.repo({"files": {"m.py": src}})
        reach = self.make_adapter().own_reach("m.py", self.sha("main"), {"a"})
        self.assertEqual(reach.locals, frozenset({"a"}))
        self.assertEqual(reach.names, frozenset({"a", "router"}))  # importers of `router` are reached; `b` is not
        self.assertFalse(reach.whole)


class JsReach(JsBase):
    def test_js_reach_names_what_the_module_exports_of_the_reached_declarations(self):
        files = {
            "src/m.ts": "export function f() {\n  return 1;\n}\n",
            "src/n.ts": f(['import { f } from "./m";', "", "function inner() {", "  return f();", "}", "", "export function outer() {", "  return inner();", "}", "", "export function alone() {", "  return 0;", "}"]),
        }
        self.repo({"files": files}, {"files": {"src/m.ts": "export function f() {\n  return 2;\n}\n"}})
        adapter = self.make_adapter()
        edges = adapter.importers("src/m.ts", self.sha("main")).edges
        reach = adapter.reach("src/n.ts", edges, self.sha("main"), frozenset({"f"}))
        self.assertEqual(reach.names, frozenset({"outer"}))
        self.assertEqual(reach.locals, frozenset({"f", "inner", "outer"}))
        self.assertFalse(reach.whole)

    def test_a_default_export_and_module_level_use(self):
        files = {
            "src/m.ts": "export function f() {\n  return 1;\n}\n",
            "src/n.ts": f(['import { f } from "./m";', "", "export const CONFIG = f();", "", "export function g() {", "  return 1;", "}"]),
        }
        self.repo({"files": files}, {"files": {"src/m.ts": "export function f() {\n  return 2;\n}\n"}})
        adapter = self.make_adapter()
        edges = adapter.importers("src/m.ts", self.sha("main")).edges
        reach = adapter.reach("src/n.ts", edges, self.sha("main"), frozenset({"f"}))
        self.assertIsNone(reach.names)  # a constant initialised by a reached call runs for every importer
        self.assertTrue(reach.whole)

    def test_a_reached_declaration_that_nothing_exports_is_not_the_whole_module(self):
        files = {"src/m.ts": "export function f() {\n  return 1;\n}\n", "src/n.ts": f(['import { f } from "./m";', "", "function inner() {", "  return f();", "}"])}
        self.repo({"files": files}, {"files": {"src/m.ts": "export function f() {\n  return 2;\n}\n"}})
        adapter = self.make_adapter()
        edges = adapter.importers("src/m.ts", self.sha("main")).edges
        reach = adapter.reach("src/n.ts", edges, self.sha("main"), frozenset({"f"}))
        self.assertEqual((reach.names, reach.whole, sorted(reach.locals)), (None, False, ["f", "inner"]))  # nothing to narrow with, but only `inner` is touched


if __name__ == "__main__":
    unittest.main()
