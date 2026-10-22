"""The M16 Express and NestJS fixtures build, and every ex expected.json is true of the files.

The hand-written expected answers are checked against an independent reading of the repositories (`tests/fixtures/ex_claims.py`, which
shares no code with the adapters): the route tables of the Express service and of the NestJS service at main and at every scenario head,
the routers and controllers nothing serves, what a change to a service, a middleware or a module reaches, that every locator names a line
of code at the revision it is read at, that the client URLs fit the routes the http edges name, and what the removed and added routes are.
The adapters' own output is compared with the same files in test_ex_scenarios.py.
"""

import json
import re
import tempfile
import time
import unittest
from pathlib import Path

from tests import support_ex as se
from tests.fixtures import build, ex_build, ex_claims
from tests.fixtures.claims import Tree, changed_lines, collect, git_out, occurrences, ref_exists, ref_shas, text_at, ts_importers

BANNED = chr(0x2013) + chr(0x2014) + chr(0x2192) + chr(0x2018) + chr(0x2019) + chr(0x201c) + chr(0x201d) + "".join(chr(c) for c in range(0x202a, 0x202f)) + "".join(chr(c) for c in range(0x2066, 0x206a))  # en and em dash, arrow, curly quotes, bidi controls (built from code points: no raw one in this file)
EX_KINDS = {"not_distinctive", "parse_degraded", "router_unmounted", "route_path_partial", "routes_unread", "mount_unresolved", "hub_cutoff", "module_cap", "route_change_withheld"}
TYPES = {"changed_symbol", "changed_file", "caller", "route", "screen", "http_edge", "unit_test", "finding"}
# routes that scenarios exclude because the table does not hold them at all (they are not routes, or nothing serves them): the reason is the scenario's
ABSENT = {("GET", "/draft"), ("GET", "/"), ("GET", "/ping"), ("GET", "/orders/{id}"), ("GET", "/api/reports/trial-balance"), ("GET", "/api/audit"),
          ("GET", "/api/custom/ping"), ("GET", "/custom/ping"), ("GET", "/api/status/health")}
SERVERS = {"ex/orders": ex_claims.express_routes, "ex/ledger": ex_claims.nest_routes}


class Build(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        start = time.monotonic()
        cls.repos = ex_build.build_all(Path(cls.tmp.name))
        cls.seconds = time.monotonic() - start

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_build_is_fast(self):
        self.assertLess(self.seconds, 10, f"build took {self.seconds:.1f}s")

    def test_four_repos_on_main_with_the_scenario_branches(self):
        self.assertEqual(sorted(self.repos), ["ex/console", "ex/ledger", "ex/orders", "ex/web"])
        for name, path in self.repos.items():
            self.assertTrue((path / ".git").is_dir())
            self.assertEqual(git_out(path, "branch", "--show-current").strip(), "main")
            self.assertEqual(git_out(path, "remote").strip(), "")
        for sc in ex_build.scenarios(Path(self.tmp.name)):
            self.assertTrue(ref_exists(sc["repo_path"], sc["change"]["head"]), sc["id"])
            self.assertNotEqual(git_out(sc["repo_path"], "diff", "--stat", f"{sc['change']['base']}...{sc['change']['head']}").strip(), "", sc["id"])

    def test_two_builds_have_identical_shas(self):
        with tempfile.TemporaryDirectory() as two:
            other = ex_build.build_all(Path(two))
            for name in self.repos:
                self.assertEqual(ref_shas(self.repos[name]), ref_shas(other[name]), name)
                self.assertTrue(ref_shas(self.repos[name]).strip())

    def test_the_m1_fixtures_are_untouched_by_the_ex_ones(self):
        """The M1 builder knows nothing of the ex repositories and scenarios, and they are in a tree of their own."""
        self.assertEqual(sorted(build.load_workspace()["repos"]), ["acme/api", "acme/contracts", "acme/web", "acme/worker"])
        self.assertTrue(all(not p.name.startswith("ex") for p in build.SCENARIOS_DIR.iterdir()))
        self.assertTrue(all(not p.name.startswith("ex") for p in build.REPOS_DIR.iterdir()))

    def test_the_fixture_has_what_the_adapters_need_to_meet(self):
        orders = set(ex_claims.tree(self.repos["ex/orders"], "main"))
        for path in ("src/app.ts", "src/routes/index.ts", "src/routes/draft.ts", "src/legacy/index.js", "src/legacy/invoices.js", "src/lib/events.ts", "src/lib/warm.ts", "src/middleware/requestLog.ts",
                     "src/routes/__tests__/orders.test.ts", "src/services/__tests__/orders.test.ts"):
            self.assertIn(path, orders)
        ledger = set(ex_claims.tree(self.repos["ex/ledger"], "main"))
        for path in ("src/main.ts", "src/app.module.ts", "src/reports/reports.module.ts", "src/audit/audit.controller.ts", "src/custom/custom.controller.ts", "src/balances/balances.controller.ts"):
            self.assertIn(path, ledger)

    def test_the_name_trap_files_are_not_express_by_the_independent_reading(self):
        orders = self.repos["ex/orders"]
        events = text_at(orders, "main", "src/lib/events.ts")
        self.assertIn('router.get("/ping"', events)
        self.assertNotIn("express", events)
        warm = text_at(orders, "main", "src/lib/warm.ts")
        self.assertIn('cache.get("/orders/:id", load)', warm)
        paths = {(r["method"], r["path"]) for r in ex_claims.express_routes(orders, "main")}
        self.assertNotIn(("GET", "/ping"), paths)
        self.assertNotIn(("GET", "/orders/{id}"), paths)
        custom = text_at(self.repos["ex/ledger"], "main", "src/custom/custom.controller.ts")
        self.assertIn('from "./decorators"', custom)
        self.assertIsNone(re.search(r'import \{[^}]*\b(Controller|Get)\b[^}]*\} from "@nestjs', custom))  # the words come from the repository...
        self.assertIn('import { Param } from "@nestjs/common";', custom)  # ...while the file does import from the package, so the import of the words is what decides

    def test_the_two_servers_have_the_routes_the_trace_says(self):
        orders = ex_claims.express_routes(self.repos["ex/orders"], "main")
        self.assertEqual(len(orders), 15)
        self.assertEqual(sorted({r["method"] for r in orders}), ["GET", "POST"])
        ledger = ex_claims.nest_routes(self.repos["ex/ledger"], "main")
        self.assertEqual(len(ledger), 6)
        self.assertEqual(ex_claims.express_unmounted(self.repos["ex/orders"], "main"), [("src/routes/draft.ts", "draftRouter")])
        self.assertEqual(ex_claims.nest_unmounted(self.repos["ex/ledger"], "main"), [("src/audit/audit.controller.ts", "AuditController"), ("src/reports/reports.controller.ts", "ReportsController")])

    def test_get_is_defined_five_times_and_list_once(self):
        orders = self.repos["ex/orders"]
        self.assertEqual(ex_claims.definitions_of(orders, "main", "get"), 5)
        self.assertEqual(ex_claims.definitions_of(orders, "main", "list"), 1)


class ScenarioFiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.e = se.env()
        cls.repos = cls.e.repos
        cls.scenarios = se.scenarios()
        cls.tree = Tree(cls.repos)

    def revision(self, sc, item, repo):
        return build.evidence_revision(item, repo, sc["repo"], sc["change"], None)

    def table(self, sc, repo, at="head"):
        """The independent route table of a server repository at the scenario's head (or base) when the scenario is about it, else at main."""
        rev = (sc["change"]["head"] if at == "head" else sc["change"]["base"]) if sc["repo"] == repo else "main"
        return SERVERS[repo](self.repos[repo], rev)

    def test_ids_schema_and_folders(self):
        folders = sorted(p.name for p in ex_build.SCENARIOS_DIR.iterdir() if p.is_dir())
        self.assertEqual([sc["id"] for sc in self.scenarios], folders)
        self.assertEqual(len(folders), 25)
        for sc in self.scenarios:
            with self.subTest(scenario=sc["id"]):
                exp = sc["expected"]
                self.assertEqual((exp["schema"], exp["id"], exp["repo"], exp["change"]), (1, sc["id"], sc["repo"], sc["change"]))
                self.assertEqual(sc["dir"].name, sc["id"])
                self.assertTrue(sc["id"].startswith("ex"))
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
                    self.assertIn(item["kind"], EX_KINDS)
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
        self.assertGreater(count, 250)

    def test_no_banned_character_in_any_fixture_file_and_no_ascii_arrow_in_prose(self):
        for path in sorted(ex_build.HERE.rglob("*")):
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                self.assertFalse(set(text) & set(BANNED), str(path))
        for sc in self.scenarios:
            for text in (sc["expected"]["explains"], sc["scenario"]["notes"], sc["scenario"]["commit_message"]):
                self.assertNotRegex(text, r"->|=>")
        self.assertFalse(set(ex_build.WORKSPACE_FILE.read_text(encoding="utf-8")) & set(BANNED))

    def test_the_route_table_of_the_oracle_holds_every_found_route_with_its_evidence_in_order(self):
        for sc in self.scenarios:
            for item in sc["expected"]["must_find"]:
                if item["type"] != "route":
                    continue
                with self.subTest(scenario=sc["id"], route=f"{item['method']} {item['path']}"):
                    found = [r for r in self.table(sc, item["repo"]) if (r["method"], r["path"]) == (item["method"], item["path"])]
                    self.assertTrue(found, "the route is not in the table")
                    ok = [r for r in found if len(r["evidence"]) == len(item["evidence"])
                          and all(r["evidence"][i][0] == e["path"] and e["contains"] in r["evidence"][i][1] for i, e in enumerate(item["evidence"]))]
                    self.assertTrue(ok, f"no route has the evidence {[e['contains'] for e in item['evidence']]}")

    def test_an_excluded_route_is_in_the_table_or_is_one_of_the_named_non_routes(self):
        for sc in self.scenarios:
            for item in sc["expected"]["must_exclude"]:
                if item["type"] != "route":
                    continue
                key = (item["method"], item["path"])
                present = any((r["method"], r["path"]) == key for r in self.table(sc, item["repo"]))
                with self.subTest(scenario=sc["id"], route=key):
                    self.assertEqual(present, key not in ABSENT, "an excluded route exists (not reached) unless it is a named non-route")

    def test_the_unmounted_records_are_the_routers_and_controllers_the_oracle_says_nothing_serves(self):
        for sc in self.scenarios:
            want = {i["subject"] for i in sc["expected"]["not_established"] if i["kind"] == "router_unmounted"}
            repo = sc["repo"]
            if repo not in SERVERS:
                continue
            head = sc["change"]["head"]
            unmounted = ex_claims.express_unmounted(self.repos[repo], head) if repo == "ex/orders" else ex_claims.nest_unmounted(self.repos[repo], head)
            have = {f"{p}:{v}" for p, v in unmounted}
            with self.subTest(scenario=sc["id"]):
                self.assertTrue(want <= have, f"{want} not all unmounted: {have}")
                if want:
                    self.assertEqual(len(want), 1)

    def test_a_service_function_reaches_the_routes_of_the_handlers_that_use_it(self):
        """ex01, ex04, ex07 and ex13: the controller functions that call (or are) the changed function, and the routes whose line spells one of them."""
        cases = {
            "ex01-service-narrowed": ("src/services/orders.ts", "findOrder", "src/controllers/orders.ts"),
            "ex04-route-chain": (None, "addLine", None),
            "ex07-partial-path": (None, "daily", None),
            "ex13-generic-names": (None, "get", None),
        }
        for sid, (service, function, controller_file) in cases.items():
            sc = next(s for s in self.scenarios if s["id"] == sid)
            head = sc["change"]["head"]
            repo = self.repos["ex/orders"]
            routes = ex_claims.express_routes(repo, head)
            if service:
                importers = sorted(ts_importers(self.tree, self.repos, "ex/orders", service, head))
                names = ex_claims.callers_of(repo, head, [p for p in importers if "__tests__" not in p], function)
            else:
                names = [function]
            wanted = {(i["method"], i["path"]) for i in sc["expected"]["must_find"] if i["type"] == "route"}
            if sid == "ex13-generic-names":  # the route spells ctrl.get: the member, not the word alone
                got = {(r["method"], r["path"]) for r in routes if re.search(r"\bctrl\.get\b", r["evidence"][0][1])}
            else:
                got = {(r["method"], r["path"]) for r in ex_claims.routes_naming(routes, names)}
            with self.subTest(scenario=sid, names=names):
                self.assertEqual(wanted, got)

    def test_a_middleware_of_the_app_reaches_every_route_through_the_file_that_registers_it(self):
        sc = next(s for s in self.scenarios if s["id"] == "ex08-app-middleware")
        routes = ex_claims.express_routes(self.repos["ex/orders"], sc["change"]["head"])
        registered = [l for l in (text_at(self.repos["ex/orders"], "main", "src/app.ts") or "").split("\n") if "app.use(requestLog)" in l]
        self.assertEqual(len(registered), 1)
        wanted = {(i["method"], i["path"]) for i in sc["expected"]["must_find"] if i["type"] == "route"}
        self.assertEqual(wanted, {(r["method"], r["path"]) for r in ex_claims.routes_through(routes, "src/app.ts")})
        self.assertEqual(len(wanted), 15)

    def test_a_middleware_named_in_a_route_call_reaches_the_routes_that_name_it(self):
        sc = next(s for s in self.scenarios if s["id"] == "ex09-route-middleware")
        routes = ex_claims.express_routes(self.repos["ex/orders"], sc["change"]["head"])
        wanted = {(i["method"], i["path"]) for i in sc["expected"]["must_find"] if i["type"] == "route"}
        self.assertEqual(wanted, {(r["method"], r["path"]) for r in ex_claims.routes_naming(routes, ["requireAuth"])})

    def test_a_commonjs_require_takes_every_name_so_every_route_of_the_file_is_reached(self):
        sc = next(s for s in self.scenarios if s["id"] == "ex06-commonjs-legacy")
        routes = ex_claims.express_routes(self.repos["ex/orders"], sc["change"]["head"])
        wanted = {(i["method"], i["path"]) for i in sc["expected"]["must_find"] if i["type"] == "route"}
        self.assertEqual(wanted, {(r["method"], r["path"]) for r in routes if r["file"] == "src/legacy/invoices.js"})
        text = text_at(self.repos["ex/orders"], sc["change"]["head"], "src/legacy/invoices.js")
        self.assertIn('const { getInvoice, listInvoices } = require("../controllers/invoices");', text)

    def test_the_nest_scenarios_reach_what_the_decorators_and_the_modules_say(self):
        ledger = self.repos["ex/ledger"]
        ctl = text_at(ledger, "main", "src/ledger/ledger.controller.ts")
        sc15 = next(s for s in self.scenarios if s["id"] == "ex15-nest-injected-property")
        wanted = {(i["method"], i["path"]) for i in sc15["expected"]["must_find"] if i["type"] == "route"}
        blocks = re.split(r"\n(?=  @)", ctl)
        using = {re.match(r"  @(\w+)", b).group(1) for b in blocks[1:] if "this.audit" in b}
        self.assertEqual(using, {"Post", "Delete"})
        self.assertEqual({m for m, _p in wanted}, {"POST", "DELETE"})
        routes = ex_claims.nest_routes(ledger, "main")
        sc19 = next(s for s in self.scenarios if s["id"] == "ex19-nest-module-change")
        wanted19 = {(i["method"], i["path"]) for i in sc19["expected"]["must_find"] if i["type"] == "route"}
        module = ex_claims.NestWorld(ledger, sc19["change"]["head"]).modules["LedgerModule"]
        self.assertEqual(wanted19, {(r["method"], r["path"]) for r in routes if r["class"] in module["controllers"]})
        sc20 = next(s for s in self.scenarios if s["id"] == "ex20-nest-bootstrap")
        wanted20 = {(i["method"], i["path"]) for i in sc20["expected"]["must_find"] if i["type"] == "route"}
        self.assertEqual(wanted20, {(r["method"], r["path"]) for r in ex_claims.nest_routes(ledger, sc20["change"]["head"])})
        self.assertEqual(len(wanted20), 6)

    def test_every_import_hop_of_a_chain_imports_the_file_before_it_and_the_first_one_a_changed_file(self):
        checked = 0
        for sc in self.scenarios:
            changed = set(changed_lines(self.repos[sc["repo"]], sc["change"]["base"], sc["change"]["head"]))
            for item in (i for i in sc["expected"]["must_find"] if i["type"] == "screen"):
                chain = item["chain"]
                for k, hop in enumerate(chain):
                    if not hop["contains"].lstrip().startswith(("import ", "const {", "const ")) or "require(" not in hop["contains"] and not hop["contains"].lstrip().startswith("import "):
                        continue
                    repo = hop["repo"]
                    rev = self.revision(sc, hop, repo)
                    spec = re.search(r"""(?:from\s+|require\()["']([^"']+)["']""", hop["contains"]).group(1)
                    target = self.resolve(repo, rev, hop["path"], spec)
                    self.assertIsNotNone(target, f"{sc['id']}: {hop['contains']} resolves to nothing")
                    if k == 0 and sc["repo"] == repo:
                        self.assertIn(target, changed, f"{sc['id']}: the first hop imports a changed file")
                    elif k > 0 and chain[k - 1]["repo"] == repo and chain[k - 1]["contains"].lstrip().startswith(("import ", "const {")):
                        self.assertEqual(target, chain[k - 1]["path"], sc["id"])
                    checked += 1
        self.assertGreater(checked, 25)

    def resolve(self, repo, rev, importer, spec):
        files = self.tree.files(repo, rev)
        resolved = ex_claims.resolve(files, importer, spec) if spec.startswith(".") else None
        if resolved is None and not spec.startswith("."):
            from tests.fixtures.claims import resolve_ts

            resolved = resolve_ts(self.tree, repo, rev, importer, spec)
        return resolved

    def test_a_changed_symbol_is_a_function_or_a_class_the_diff_touches(self):
        for sc in self.scenarios:
            repo = self.repos[sc["repo"]]
            diff = changed_lines(repo, sc["change"]["base"], sc["change"]["head"])
            for item in sc["expected"]["must_find"]:
                if item["type"] != "changed_symbol":
                    continue
                removed = item.get("change") == "removed"
                added = item.get("change") == "added"
                head = text_at(repo, sc["change"]["head"], item["path"])
                base = text_at(repo, sc["change"]["base"], item["path"])
                with self.subTest(scenario=sc["id"], symbol=item["symbol"]):
                    span = ex_claims.span_of(base if removed else head, item["symbol"])
                    self.assertIsNotNone(span)
                    lines = diff[item["path"]]["removed" if removed else "added"]
                    self.assertTrue(any(span[0] <= n <= span[1] for n in lines), "no changed line inside the declaration")
                    if added:
                        self.assertIsNone(ex_claims.span_of(base, item["symbol"]) if base else None)

    def test_an_http_edge_is_a_client_url_that_fits_the_route_by_segments_and_method(self):
        checked = 0
        for sc in self.scenarios:
            for item in (i for i in sc["expected"]["must_find"] if i["type"] == "http_edge"):
                client, via = item["from"], item["via"][0]
                text = text_at(self.repos[client["repo"]], "main", via["path"])
                line = next(l for l in text.splitlines() if via["contains"] in l)
                url = re.search(r'"(/[^"]*)"|`(/[^`]*)`', line)
                pattern = (url.group(1) or url.group(2)).rstrip("/")
                segs = ["{}" if re.fullmatch(r"\$\{\w+\}", s) else s for s in pattern.split("/")[1:]]
                route = item["to"]["path"].strip("/").split("/")
                fits = len(segs) == len(route) and all((r.startswith("{") if s == "{}" else (s == r) or r.startswith("{")) for s, r in zip(segs, route))
                with self.subTest(scenario=sc["id"], url=pattern):
                    self.assertTrue(fits, f"{pattern} does not fit {item['to']['path']}")
                    for s, r in zip(segs, route):
                        if s == "{}":
                            self.assertTrue(r.startswith("{"))
                    verb = re.search(r"api(Get|Delete|Post|Put)\(", client["contains"])
                    self.assertIsNotNone(verb)
                    self.assertEqual(item["to"]["method"], verb.group(1).upper())
                checked += 1
        self.assertGreater(checked, 10)

    def test_a_removed_route_is_in_the_table_at_the_base_and_not_at_the_head_and_a_client_still_names_it(self):
        sc = next(s for s in self.scenarios if s["id"] == "ex10-removed-route")
        repo = self.repos["ex/orders"]
        key = ("GET", "/api/v1/customers/{customerId}/orders")
        self.assertIn(key, {(r["method"], r["path"]) for r in ex_claims.express_routes(repo, sc["change"]["base"])})
        self.assertNotIn(key, {(r["method"], r["path"]) for r in ex_claims.express_routes(repo, sc["change"]["head"])})
        client = text_at(self.repos["ex/web"], "main", "src/api/endpoints.ts")
        self.assertIn("/api/v1/customers/${customerId}/orders", client)

    def test_an_added_route_is_in_the_table_at_the_head_only_and_no_client_url_fits_it(self):
        sc = next(s for s in self.scenarios if s["id"] == "ex11-added-route")
        repo = self.repos["ex/orders"]
        key = ("GET", "/api/v1/orders/export")
        self.assertNotIn(key, {(r["method"], r["path"]) for r in ex_claims.express_routes(repo, sc["change"]["base"])})
        self.assertIn(key, {(r["method"], r["path"]) for r in ex_claims.express_routes(repo, sc["change"]["head"])})
        endpoints = text_at(self.repos["ex/web"], "main", "src/api/endpoints.ts")
        for url in re.findall(r'"(/[^"]*)"|`(/[^`]*)`', endpoints):
            pattern = (url[0] or url[1]).replace("${orderId}", "{}")
            segs = pattern.strip("/").split("/")
            self.assertFalse(len(segs) == 4 and segs[:3] == ["api", "v1", "orders"] and segs[3] == "export")
            if segs[:3] == ["api", "v1", "orders"] and len(segs) == 4:
                self.assertEqual(segs[3], "{}")  # a client parameter fits only a route parameter, and `export` is a literal

    def test_a_router_built_in_a_function_and_the_inherited_routes_are_what_the_independent_reading_does_not_read_either(self):
        sc12 = next(s for s in self.scenarios if s["id"] == "ex12-router-in-function")
        text = text_at(self.repos["ex/orders"], sc12["change"]["head"], "src/routes/factory.ts")
        self.assertEqual([i for i, l in enumerate(text.split("\n"), 1) if "= Router()" in l], [5])
        self.assertEqual(text.split("\n")[3], "export function buildRouter() {")
        self.assertNotIn("src/routes/factory.ts", {r["file"] for r in ex_claims.express_routes(self.repos["ex/orders"], sc12["change"]["head"])})
        sc23 = next(s for s in self.scenarios if s["id"] == "ex23-nest-inherited-routes")
        text = text_at(self.repos["ex/ledger"], sc23["change"]["head"], "src/base/status.controller.ts")
        self.assertEqual(text.split("\n")[4], "export class StatusController extends BaseController {}")
        self.assertIn('@Get("health")', text_at(self.repos["ex/ledger"], sc23["change"]["head"], "src/base/base.controller.ts"))

    def test_the_partial_records_name_the_line_of_the_unknown_part(self):
        for sid, subject, needle in (("ex07-partial-path", "src/routes/reports.ts:7", "router.get(dailyPath(), daily);"), ("ex21-nest-versioned-partial", "src/balances/balances.controller.ts:3", 'version: "1"')):
            sc = next(s for s in self.scenarios if s["id"] == sid)
            path, _, line = subject.rpartition(":")
            text = text_at(self.repos[sc["repo"]], sc["change"]["head"], path).split("\n")
            self.assertIn(needle, text[int(line) - 1], sid)

    def test_the_not_distinctive_counts_are_the_definitions_in_non_test_code_at_the_head(self):
        for sc in self.scenarios:
            for item in sc["expected"]["not_established"]:
                if item["kind"] != "not_distinctive":
                    continue
                count = ex_claims.definitions_of(self.repos[sc["repo"]], sc["change"]["head"], item["subject"])
                self.assertEqual(count, item["definitions"], sc["id"])
                self.assertGreater(count, 3)  # distinct_max of the workspace

    def test_the_unlexable_and_unread_scenarios_name_the_line_that_makes_them_unread(self):
        sc = next(s for s in self.scenarios if s["id"] == "ex12-router-in-function")
        self.assertEqual([(i["kind"], i["subject"], i["blocking"]) for i in sc["expected"]["not_established"]], [("routes_unread", "src/routes/factory.ts:5", True)])

    def test_every_scenario_edits_what_its_notes_say_and_only_that(self):
        names = {
            "ex01": ["src/services/orders.ts"], "ex02": ["src/routes/customers.ts"], "ex03": ["src/controllers/admin.ts"], "ex04": ["src/controllers/orders.ts"],
            "ex05": ["src/controllers/draft.ts"], "ex06": ["src/controllers/invoices.ts"], "ex07": ["src/controllers/reports.ts"], "ex08": ["src/middleware/requestLog.ts"],
            "ex09": ["src/middleware/auth.ts"], "ex10": ["src/routes/customers.ts"], "ex11": ["src/controllers/export.ts", "src/routes/orders.ts"], "ex12": ["src/routes/factory.ts"],
            "ex13": ["src/controllers/customers.ts"], "ex14": ["src/lib/events.ts", "src/lib/warm.ts"], "ex15": ["src/audit/audit.log.ts"], "ex16": ["src/ledger/ledger.controller.ts"],
            "ex17": ["src/reports/reports.controller.ts"], "ex18": ["src/audit/audit.controller.ts"], "ex19": ["src/ledger/ledger.module.ts"], "ex20": ["src/main.ts"],
            "ex21": ["src/balances/balances.controller.ts"], "ex22": ["src/custom/custom.controller.ts"], "ex23": ["src/base/base.controller.ts", "src/base/status.controller.ts"],
            "ex24": ["src/routes/customers.ts"], "ex25": ["src/ledger/ledger.module.ts"],
        }
        for sc in self.scenarios:
            diff = sorted(changed_lines(self.repos[sc["repo"]], sc["change"]["base"], sc["change"]["head"]))
            self.assertEqual(diff, sorted(names[sc["id"].split("-")[0]]), sc["id"])

    def test_the_scenario_repository_is_the_one_whose_files_it_changes(self):
        for sc in self.scenarios:
            self.assertIn(sc["repo"], ("ex/orders", "ex/ledger"))
            number = int(sc["id"][2:4])
            self.assertEqual(sc["repo"], "ex/orders" if number <= 14 or number == 24 else "ex/ledger", sc["id"])

    def test_no_scenario_is_a_copy_of_another(self):
        notes = [sc["scenario"]["notes"] for sc in self.scenarios]
        self.assertEqual(len(notes), len(set(notes)))
        explains = [sc["expected"]["explains"] for sc in self.scenarios]
        self.assertEqual(len(explains), len(set(explains)))
        for e in explains:
            self.assertGreater(len(e), 250)

    def test_the_expected_files_are_valid_json_with_sorted_scenario_folders(self):
        for sc in self.scenarios:
            json.loads((sc["dir"] / "expected.json").read_text(encoding="utf-8"))
            json.loads((sc["dir"] / "scenario.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
