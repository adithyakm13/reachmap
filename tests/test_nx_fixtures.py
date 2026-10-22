"""The M16 Next.js fixtures build, and every nx expected.json is true of the files.

The hand-written expected answers are checked against an independent reading of the repositories (`tests/fixtures/nx_claims.py`,
which shares no code with the adapter): the screens and routes the file tree makes, the files a layout wraps, the importers of a
changed file, that every import hop of a chain really imports the file before it, that every locator names a line of code at the
revision it is read at. The adapter's own output is compared with the same files in test_nextjs_scenarios.py.
"""

import json
import re
import tempfile
import time
import unittest
from pathlib import Path

from tests import support_nx as sn
from tests.fixtures import build, nx_build, nx_claims
from tests.fixtures.claims import (
    Tree, changed_lines, collect, git_out, import_target, occurrences, ref_exists, ref_shas, symbol_changed, text_at, ts_importers,
)

BANNED = chr(0x2013) + chr(0x2014) + chr(0x2192) + chr(0x2018) + chr(0x2019) + chr(0x201c) + chr(0x201d) + "".join(chr(c) for c in range(0x202a, 0x202f)) + "".join(chr(c) for c in range(0x2066, 0x206a))  # en and em dash, arrow, curly quotes, bidi controls (built from code points: no raw one in this file)
NX_KINDS = {"not_distinctive", "parse_degraded", "route_intercepted", "route_path_partial", "hub_cutoff", "module_cap"}
TYPES = {"changed_symbol", "changed_file", "caller", "route", "screen", "http_edge", "unit_test", "finding"}
TEST_MARKERS = ["__tests__/", ".test.", ".spec."]


class Build(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        start = time.monotonic()
        cls.repos = nx_build.build_all(Path(cls.tmp.name))
        cls.seconds = time.monotonic() - start

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_build_is_fast(self):
        self.assertLess(self.seconds, 10, f"build took {self.seconds:.1f}s")

    def test_two_repos_on_main_with_the_scenario_branches(self):
        self.assertEqual(sorted(self.repos), ["nx/mobile", "nx/shop"])
        for name, path in self.repos.items():
            self.assertTrue((path / ".git").is_dir())
            self.assertEqual(git_out(path, "branch", "--show-current").strip(), "main")
            self.assertEqual(git_out(path, "remote").strip(), "")
        for sc in nx_build.scenarios(Path(self.tmp.name)):
            self.assertTrue(ref_exists(sc["repo_path"], sc["change"]["head"]), sc["id"])
            self.assertNotEqual(git_out(sc["repo_path"], "diff", "--stat", f"{sc['change']['base']}...{sc['change']['head']}").strip(), "", sc["id"])

    def test_two_builds_have_identical_shas(self):
        with tempfile.TemporaryDirectory() as two:
            other = nx_build.build_all(Path(two))
            for name in self.repos:
                self.assertEqual(ref_shas(self.repos[name]), ref_shas(other[name]), name)
                self.assertTrue(ref_shas(self.repos[name]).strip())

    def test_the_m1_fixtures_are_untouched_by_the_nx_ones(self):
        """The M1 builder knows nothing of the nx repositories and scenarios, and they are in a tree of their own."""
        self.assertEqual(sorted(build.load_workspace()["repos"]), ["acme/api", "acme/contracts", "acme/web", "acme/worker"])
        self.assertTrue(all(not p.name.startswith("nx") for p in build.SCENARIOS_DIR.iterdir()))
        self.assertTrue(all(not p.name.startswith("nx") for p in build.REPOS_DIR.iterdir()))

    def test_the_fixture_has_what_the_adapter_needs_to_meet(self):
        files = set(nx_claims.tree(self.repos["nx/shop"], "main"))
        for path in (
            "src/app/layout.tsx", "src/app/(shop)/layout.tsx", "src/app/(shop)/products/[slug]/page.tsx", "src/app/docs/[...slug]/page.tsx", "src/app/help/[[...topic]]/page.tsx",
            "src/app/dashboard/@analytics/page.tsx", "src/app/feed/@modal/(..)photos/[id]/page.tsx", "src/app/_private/page.tsx", "src/app/api/orders/[id]/route.ts",
            "src/pages/_app.tsx", "src/pages/_document.tsx", "src/pages/legacy/[id].tsx", "src/pages/api/legacy/status.ts", "src/middleware.ts",
            "src/components/layout.tsx", "src/lib/route.ts", "src/utils/middleware.ts", "src/components/__tests__/StatusBadge.test.tsx",
        ):
            self.assertIn(path, files)

    def test_get_is_defined_in_four_route_files_and_handler_in_two(self):
        shop = self.repos["nx/shop"]
        text = {f: text_at(shop, "main", f) or "" for f in nx_claims.tree(shop, "main") if f.endswith((".ts", ".tsx"))}
        self.assertEqual(sum(bool(re.search(r"^export async function GET\(", t, re.M)) for t in text.values()), 4)
        self.assertEqual(sum(bool(re.search(r"^export default function handler\(", t, re.M)) for t in text.values()), 2)

    def test_the_name_trap_files_exist_and_are_not_routing_files_by_the_independent_reading(self):
        shop = self.repos["nx/shop"]
        routing = nx_claims.routing_files(nx_claims.tree(shop, "main"))
        for path in ("src/components/layout.tsx", "src/lib/route.ts", "src/utils/middleware.ts"):
            self.assertNotIn(path, routing)
        self.assertEqual(sorted(l for l in nx_claims.screens(nx_claims.tree(shop, "main")) if "private" in l), [])  # the private page is no screen


class Oracle(unittest.TestCase):
    def test_the_pages_router_reads_a_name_as_a_segment_and_the_app_router_does_not(self):
        chain = ["_x", "(g)", "@s", "(..)up", "[id]"]
        self.assertEqual(nx_claims._folders_url(chain, False, pages=True), (["_x", "(g)", "@s", "(..)up", ":id"], False))
        self.assertIsNone(nx_claims._folders_url(chain, False))
        self.assertEqual(nx_claims._folders_url(["(g)", "@s", "(..)up", "[id]"], False), (["up", ":id"], True))


class ScenarioFiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.e = sn.env()
        cls.repos = cls.e.repos
        cls.scenarios = sn.scenarios()
        cls.tree = Tree(cls.repos)

    def revision(self, sc, item, repo):
        return build.evidence_revision(item, repo, sc["repo"], sc["change"], None)

    def test_ids_schema_and_folders(self):
        folders = sorted(p.name for p in nx_build.SCENARIOS_DIR.iterdir() if p.is_dir())
        self.assertEqual([sc["id"] for sc in self.scenarios], folders)
        for sc in self.scenarios:
            with self.subTest(scenario=sc["id"]):
                exp = sc["expected"]
                self.assertEqual((exp["schema"], exp["id"], exp["repo"], exp["change"]), (1, sc["id"], sc["repo"], sc["change"]))
                self.assertEqual(sc["dir"].name, sc["id"])
                self.assertTrue(sc["id"].startswith("nx"))
                self.assertTrue(exp["must_find"])
                for key in ("must_find", "must_exclude", "not_established"):
                    self.assertIsInstance(exp[key], list)
                for item in exp["must_find"] + exp["must_exclude"]:
                    self.assertIn(item["type"], TYPES)
                for item in exp["must_exclude"]:
                    self.assertTrue(item["why"])
                for kind in exp["complete_for"]:
                    self.assertIn(kind, TYPES)
                for item in exp["not_established"]:
                    self.assertIn(item["kind"], NX_KINDS)
                    self.assertIsInstance(item["blocking"], bool)

    def test_every_locator_names_a_line_of_code_at_the_revision_it_is_read_at(self):
        count = 0
        for sc in self.scenarios:
            locators, comments, entries = [], [], []
            collect(sc["expected"], sc["repo"], locators, comments, entries)
            for repo, item, parent in locators:
                rev = build.evidence_revision(item, repo, sc["repo"], sc["change"], parent)
                text = text_at(self.repos[repo], rev, item["path"])
                self.assertIsNotNone(text, f"{sc['id']}: {item['path']} at {rev}")
                self.assertIn(item["contains"], text, f"{sc['id']}: {item['contains']!r} not in {item['path']}")
                self.assertTrue(any(not in_comment for in_comment in occurrences(text, item["contains"])), f"{sc['id']}: {item['contains']!r} only in a comment")
                self.assertFalse("\n" in item["contains"], sc["id"])
                count += 1
        self.assertGreater(count, 150)

    def test_no_banned_character_in_any_fixture_file_and_no_ascii_arrow_in_prose(self):
        root = nx_build.HERE
        for path in sorted(root.rglob("*")):
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                self.assertFalse(set(text) & set(BANNED), str(path))
        for sc in self.scenarios:
            for text in (sc["expected"]["explains"], sc["scenario"]["notes"], sc["scenario"]["commit_message"]):
                self.assertNotRegex(text, r"->|=>")
        for path in (nx_build.WORKSPACE_FILE,):
            self.assertFalse(set(path.read_text(encoding="utf-8")) & set(BANNED))

    def test_screens_are_the_screens_the_independent_reading_makes_with_their_paths_and_files(self):
        for sc in self.scenarios:
            files = nx_claims.tree(self.repos["nx/shop"], sc["change"]["head"] if sc["repo"] == "nx/shop" else "main")
            truth = nx_claims.screens(files)
            for item in sc["expected"]["must_find"] + sc["expected"]["must_exclude"]:
                if item["type"] != "screen" or item["repo"] != "nx/shop":
                    continue
                label = item["route"]
                if item in sc["expected"]["must_exclude"]:
                    if sc["id"] == "nx12-not-a-route" and label == "/_private":
                        self.assertNotIn(label, truth)
                    continue
                with self.subTest(scenario=sc["id"], screen=label):
                    self.assertIn(label, truth)
                    if item.get("also_mounted"):
                        paths = {p for p, f, _ in self.paths_of(files) if f in truth[label]}
                        self.assertEqual(sorted(paths - {label}), sorted(item["also_mounted"]))
                    last = item["chain"][-1]
                    self.assertIn(last["path"], truth[label])  # the chain ends at a page file of the screen
                    self.assertRegex(last["contains"], r"^export default function \w+")

    @staticmethod
    def paths_of(files):
        out = []
        for f, (router, name) in nx_claims.routing_files(files).items():
            if (router, name) == ("app", "page"):
                got = nx_claims._folders_url(f[len("src/app/"):].split("/")[:-1], False)
                if got is not None:
                    out += [(p, f, got[1]) for p in nx_claims._expand(got[0])]
            elif router == "pages" and not f.startswith("src/pages/api/") and name not in ("_app", "_document", "_error"):
                chain = f[len("src/pages/"):].rsplit(".", 1)[0].split("/")
                chain = chain[:-1] if chain[-1] == "index" else chain
                out += [(p, f, False) for p in nx_claims._expand(nx_claims._folders_url(chain, False, pages=True)[0])]
        return out

    def test_routes_are_the_routes_the_independent_reading_makes_in_the_file_the_evidence_names(self):
        for sc in self.scenarios:
            shop = self.repos["nx/shop"]
            rev = sc["change"]["head"] if sc["repo"] == "nx/shop" else "main"
            truth = nx_claims.api_routes(shop, rev, nx_claims.tree(shop, rev))
            for item in sc["expected"]["must_find"]:
                if item["type"] != "route":
                    continue
                with self.subTest(scenario=sc["id"], route=f"{item['method']} {item['path']}"):
                    self.assertEqual(truth.get((item["method"], item["path"])), item["evidence"][0]["path"])
            for item in sc["expected"]["must_exclude"]:
                if item["type"] == "route" and sc["id"] not in ("nx16-unlexable-handler",):
                    with self.subTest(scenario=sc["id"], excluded=f"{item['method']} {item['path']}"):
                        self.assertIn((item["method"], item["path"]), truth)  # an excluded route exists: it is not reached, it is not absent

    def test_a_layout_scenario_lists_exactly_the_pages_below_the_layout(self):
        for sid, layout in (("nx02-layout-fanout", "src/app/(shop)/layout.tsx"), ("nx03-root-layout", "src/app/layout.tsx")):
            sc = next(s for s in self.scenarios if s["id"] == sid)
            files = nx_claims.tree(self.repos["nx/shop"], sc["change"]["head"])
            below = nx_claims.pages_below(files, layout)
            truth = nx_claims.screens(files)
            expected = {i["route"] for i in sc["expected"]["must_find"] if i["type"] == "screen"}
            self.assertEqual(expected, {label for label, fs in truth.items() if fs & below}, sid)
        sc = next(s for s in self.scenarios if s["id"] == "nx15-removed-layout")
        files = nx_claims.tree(self.repos["nx/shop"], sc["change"]["head"])
        below = nx_claims.pages_below(nx_claims.tree(self.repos["nx/shop"], sc["change"]["base"]), "src/app/(shop)/layout.tsx")
        truth = nx_claims.screens(files)
        self.assertEqual({i["route"] for i in sc["expected"]["must_find"] if i["type"] == "screen"}, {label for label, fs in truth.items() if fs & below})

    def test_the_pages_router_wrapper_wraps_the_pages_of_that_router_only(self):
        sc = next(s for s in self.scenarios if s["id"] == "nx07-pages-router-app")
        files = nx_claims.tree(self.repos["nx/shop"], sc["change"]["head"])
        pages = {label for label, fs in nx_claims.screens(files).items() if all(f.startswith("src/pages/") for f in fs)}
        self.assertEqual({i["route"] for i in sc["expected"]["must_find"] if i["type"] == "screen"}, pages)

    def test_the_middleware_scenario_reaches_every_screen_and_route_of_the_route_table(self):
        sc = next(s for s in self.scenarios if s["id"] == "nx17-middleware")
        truth = nx_claims.areas_of(self.repos["nx/shop"], sc["change"]["head"])
        self.assertEqual(len([t for t in truth if t[0] == "screen"]), 15)
        self.assertEqual(len([t for t in truth if t[0] == "api_route"]), 8)
        listed = {("screen" if i["type"] == "screen" else "api_route", i["route"] if i["type"] == "screen" else f"{i['method']} {i['path']}")
                  for i in sc["expected"]["must_find"] if i["type"] in ("screen", "route") and i["repo"] == "nx/shop"}
        self.assertEqual(listed, truth)  # the whole table is listed, by hand: the hand trace is self-contained
        self.assertTrue({"screen", "route"} <= set(sc["expected"]["complete_for"]))

    def test_every_import_hop_of_a_chain_imports_the_file_before_it_and_the_first_one_a_changed_file(self):
        checked = 0
        for sc in self.scenarios:
            changed = set(changed_lines(self.repos[sc["repo"]], sc["change"]["base"], sc["change"]["head"]))
            items = [i for i in sc["expected"]["must_find"] if i["type"] == "screen"]
            for item in items:
                chain = item["chain"]
                for k, hop in enumerate(chain):
                    if not hop["contains"].lstrip().startswith("import "):
                        continue
                    repo = hop["repo"]
                    rev = self.revision(sc, hop, repo)
                    target = import_target(self.tree, repo, rev, hop["path"], hop["contains"])
                    self.assertIsNotNone(target, f"{sc['id']}: {hop['contains']}")
                    self.assertIsNotNone(target[1], f"{sc['id']}: {hop['contains']} resolves to nothing")
                    if k == 0:
                        self.assertIn(target[1], changed, f"{sc['id']}: the first hop imports a changed file")
                    elif chain[k - 1]["repo"] == repo and chain[k - 1]["contains"].lstrip().startswith("import "):
                        self.assertEqual(target[1], chain[k - 1]["path"], sc["id"])
                    elif chain[k - 1]["repo"] == repo:
                        # the previous hop is a use of a function the import names: it is in the file the import resolves to, or the client line of an edge
                        self.assertTrue(target[1] == chain[k - 1]["path"] or chain[k - 1]["path"].endswith("services/orders.ts") or chain[k - 1]["path"].endswith("services/health.ts"), sc["id"])
                    checked += 1
        self.assertGreater(checked, 20)

    def test_the_callers_are_the_direct_importers_of_the_changed_files_minus_tests_and_exclusions(self):
        for sc in self.scenarios:
            if "caller" not in sc["expected"]["complete_for"]:
                continue
            repo = sc["repo"]
            head = sc["change"]["head"]
            changed = [p for p in changed_lines(self.repos[repo], sc["change"]["base"], head) if p.endswith((".ts", ".tsx"))]
            importers: set[str] = set()
            for path in changed:
                if text_at(self.repos[repo], head, path) is None:
                    continue
                importers |= ts_importers(self.tree, self.repos, repo, path, head)
            importers = {p for p in importers if not any(m.rstrip("/") in p.split("/")[:-1] or (m.startswith(".") and m in p.split("/")[-1]) for m in TEST_MARKERS)}
            excluded = {i["path"] for i in sc["expected"]["must_exclude"] if i["type"] == "caller"}
            wanted = {i["path"] for i in sc["expected"]["must_find"] if i["type"] == "caller"}
            with self.subTest(scenario=sc["id"]):
                self.assertEqual(wanted, importers - excluded)

    def test_a_changed_symbol_is_a_top_level_function_the_diff_touches(self):
        for sc in self.scenarios:
            repo = self.repos[sc["repo"]]
            diff = changed_lines(repo, sc["change"]["base"], sc["change"]["head"])
            for item in sc["expected"]["must_find"]:
                if item["type"] != "changed_symbol":
                    continue
                removed = item.get("change") == "removed"
                head = text_at(repo, sc["change"]["head"], item["path"])
                base = text_at(repo, sc["change"]["base"], item["path"])
                with self.subTest(scenario=sc["id"], symbol=item["symbol"]):
                    self.assertTrue(symbol_changed(item["path"], item["symbol"], None, head, base, diff, removed=removed))

    def test_an_http_edge_is_a_client_url_that_fits_the_route_by_segments_and_method(self):
        checked = 0
        for sc in self.scenarios:
            for item in (i for i in sc["expected"]["must_find"] if i["type"] == "http_edge"):
                client = item["from"]
                via = item["via"][0]
                text = text_at(self.repos["nx/mobile"], "main", via["path"])
                line = next(l for l in text.splitlines() if via["contains"] in l)
                url = re.search(r'"(/[^"]*)"|`(/[^`]*)`', line)
                pattern = (url.group(1) or url.group(2)).rstrip("/")
                segs = ["{}" if re.fullmatch(r"\$\{\w+\}", s) else s for s in pattern.split("/")[1:]]
                route = item["to"]["path"].strip("/").split("/")
                fits = len(segs) == len(route) and all((r.startswith("{") and True) if s == "{}" else (s == r) or r.startswith("{") for s, r in zip(segs, route))
                with self.subTest(scenario=sc["id"], url=pattern):
                    self.assertTrue(fits, f"{pattern} does not fit {item['to']['path']}")
                    # a parameter of the client fits only a parameter of the route
                    for s, r in zip(segs, route):
                        if s == "{}":
                            self.assertTrue(r.startswith("{"))
                    verb = re.search(r"api(Get|Delete|Post|Put)\(", client["contains"])
                    self.assertTrue(verb is not None)
                    self.assertIn(item["to"]["method"], (verb.group(1).upper(), "ANY"))
                checked += 1
        self.assertGreater(checked, 5)

    def test_every_removed_or_added_route_is_in_the_diff_of_its_scenario(self):
        sc13 = next(s for s in self.scenarios if s["id"] == "nx13-removed-route")
        diff = git_out(self.repos["nx/shop"], "diff", "--name-status", "main...scenario/nx13-removed-route").split()
        self.assertEqual(diff, ["D", "src/pages/api/legacy/status.ts"])
        sc14 = next(s for s in self.scenarios if s["id"] == "nx14-added-route")
        diff = git_out(self.repos["nx/shop"], "diff", "--name-status", "main...scenario/nx14-added-route").split()
        self.assertEqual(diff, ["A", "src/app/api/invoices/route.ts"])
        self.assertTrue(sc13 and sc14)

    def test_the_unlexable_handler_does_not_lex_by_a_plain_count_and_the_others_do(self):
        text = text_at(self.repos["nx/shop"], "scenario/nx16-unlexable-handler", "src/app/api/broken/route.ts")
        self.assertEqual(text.count("`") % 2, 1)  # one backtick: the template literal is never closed
        for path in nx_claims.tree(self.repos["nx/shop"], "main"):
            if path.endswith((".ts", ".tsx")):
                self.assertEqual((text_at(self.repos["nx/shop"], "main", path) or "").count("`") % 2, 0, path)

    def test_the_not_distinctive_counts_are_the_definitions_in_non_test_code_at_the_head(self):
        for sc in self.scenarios:
            for item in sc["expected"]["not_established"]:
                if item["kind"] != "not_distinctive":
                    continue
                repo = self.repos[sc["repo"]]
                head = sc["change"]["head"]
                count = 0
                for path in nx_claims.tree(repo, head):
                    if path.endswith((".ts", ".tsx")) and "__tests__" not in path:
                        count += len(re.findall(rf"^export (?:default )?(?:async )?function {re.escape(item['subject'])}\(", text_at(repo, head, path) or "", re.M))
                self.assertEqual(count, item["definitions"], sc["id"])
                self.assertGreater(count, 3)  # distinct_max of the workspace


if __name__ == "__main__":
    unittest.main()
