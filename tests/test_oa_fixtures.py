"""M16 OpenAPI: the `oa` fixtures build, and every expected.json is true of the files.

The M1 tests (test_fixtures.py) recompute the claims of the `s` scenarios; this module does the same for the `oa` scenarios, with the
independent derivations of `tests/fixtures/claims_openapi.py`: the routes of a spec, the operations a change alters (every `$ref` expanded
in place), the handlers by name, the client URL of every generated function, the best route of a URL and the number of definitions of a
name. Nothing here imports the analyser (the end to end checks of the same scenarios are in test_oa_scenarios.py).
"""

import json
import re
import tempfile
import time
import unittest
from pathlib import Path

from tests import support_git as sg
from tests.fixtures import build, build_openapi as bo, claims, claims_openapi as co

API, WEB = "acme/ledger-api", "acme/ledger-web"
SPEC = "api/openapi.json"
GEN = "src/api/generated/client.ts"
MUST_FIND_TYPES = {"changed_symbol", "changed_file", "caller", "route", "screen", "http_edge", "unit_test", "finding", "backend_call"}
KNOWN_KINDS = {"not_distinctive", "spec_unreadable", "edge_incomplete"}
# What each scenario claims, by section (pinned: a scenario that gains or loses a type is a decision, and nothing is skipped).
TYPES = {
    "oa01": ({"changed_symbol", "route", "http_edge", "screen"}, {"route", "http_edge", "screen"}, {"not_distinctive"}),
    "oa02": ({"changed_symbol", "caller", "unit_test", "route", "http_edge", "screen"}, {"route", "screen"}, {"not_distinctive"}),
    "oa03": ({"changed_symbol", "http_edge", "finding", "screen"}, {"screen"}, {"not_distinctive"}),
    "oa04": ({"changed_symbol", "route", "finding"}, {"route"}, {"not_distinctive"}),
    "oa05": ({"route", "http_edge", "screen", "finding"}, {"route"}, {"not_distinctive"}),
    "oa06": ({"route", "http_edge", "screen", "finding"}, {"route"}, {"not_distinctive"}),
    "oa07": ({"changed_symbol"}, {"route"}, {"spec_unreadable"}),
    "oa08": ({"changed_symbol", "changed_file", "http_edge", "backend_call", "screen"}, {"screen", "unit_test", "http_edge"}, {"not_distinctive"}),
    "oa09": ({"changed_symbol", "screen", "http_edge", "backend_call"}, {"http_edge"}, {"not_distinctive"}),
    "oa10": ({"changed_symbol", "screen"}, {"http_edge"}, {"edge_incomplete", "spec_unreadable"}),
}
BANNED = {"\u2013": "en dash", "\u2014": "em dash", "\u2192": "arrow", "\u2018": "left single quote", "\u2019": "right single quote", "\u201c": "left double quote", "\u201d": "right double quote"}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        start = time.monotonic()
        cls.repos = bo.build_all(Path(cls.tmp.name))
        cls.seconds = time.monotonic() - start
        cls.scenarios = {sc["id"].split("-")[0]: sc for sc in bo.scenarios(Path(cls.tmp.name))}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def at(self, repo, rev, path):
        return claims.text_at(self.repos[repo], rev, path)

    def head(self, sc):
        return sc["change"]["head"]

    def api_rev(self, sc):
        """The revision of the api repository whose spec a scenario's routes are read from: its head, `main` for a scenario of the web
        repository, and `main` for oa07, whose head has a spec that is not JSON (no route is claimed from it)."""
        return self.head(sc) if sc["repo"] == API and not sc["id"].startswith("oa07") else "main"

    def base(self, sc):
        return sc["change"]["base"]


class Build(Base):
    def test_the_build_is_fast_and_has_the_two_repositories_and_the_ten_scenarios(self):
        self.assertLess(self.seconds, 10, f"build took {self.seconds:.1f}s")
        self.assertEqual(sorted(self.repos), [API, WEB])
        self.assertEqual(sorted(self.scenarios), [f"oa{n:02d}" for n in range(1, 11)])
        for path in self.repos.values():
            self.assertTrue((path / ".git").is_dir())
            self.assertEqual(claims.git_out(path, "branch", "--show-current").strip(), "main")

    def test_two_builds_have_the_same_shas(self):
        with tempfile.TemporaryDirectory() as other:
            again = bo.build_all(Path(other))
            for name in self.repos:
                self.assertEqual(claims.ref_shas(self.repos[name]), claims.ref_shas(again[name]), name)

    def test_every_scenario_branch_exists_and_changes_something(self):
        for key, sc in self.scenarios.items():
            repo = self.repos[sc["repo"]]
            with self.subTest(scenario=key):
                self.assertTrue(claims.ref_exists(repo, self.head(sc)))
                self.assertNotEqual(claims.git_out(repo, "rev-parse", self.head(sc)), claims.git_out(repo, "rev-parse", self.base(sc)))
                self.assertTrue(claims.git_out(repo, "diff", "--name-only", f"{self.base(sc)}...{self.head(sc)}").strip())

    def test_the_m1_builder_neither_builds_nor_lists_these_scenarios_and_builds_the_same_repositories(self):
        with tempfile.TemporaryDirectory() as m1:
            built = build.build_all(Path(m1))
            self.assertEqual(sorted(built), ["acme/api", "acme/contracts", "acme/web", "acme/worker"])
            self.assertFalse(any(sc["id"].startswith("oa") for sc in build.scenarios(Path(m1))))
            self.assertEqual(len(build.scenarios(Path(m1))), 23)
            for path in built.values():
                self.assertNotIn("scenario/oa", claims.git_out(path, "for-each-ref", "--format=%(refname)"))

    def test_the_workspace_files_agree(self):
        import tomllib
        ws_json = bo.load_workspace()
        ws_toml = tomllib.loads(bo.CONFIG_FILE.read_text(encoding="utf-8"))
        self.assertEqual(sorted(ws_json["repos"]), sorted(ws_toml["repos"]))
        for name, info in ws_json["repos"].items():
            toml = ws_toml["repos"][name]
            for key in ("language", "roles", "branches", "http_server", "http_client", "entry_points", "tests", "aliases"):
                self.assertEqual(info.get(key), toml.get(key), f"{name}.{key}")
            self.assertEqual(toml["remote"], "${REACHMAP_FIXTURES}/" + info["slug"])
        self.assertEqual(ws_json["limits"], ws_toml["limits"])
        self.assertEqual([(e["kind"], e["from"], e["to"]) for e in ws_json["edges"]], [(e["kind"], e["from"], e["to"]) for e in ws_toml["edges"]])

    def test_the_spec_of_the_web_app_is_a_copy_of_the_api_spec_at_main(self):
        self.assertEqual(self.at(API, "main", SPEC), self.at(WEB, "main", SPEC))

    def test_no_file_holds_a_character_a_person_should_not_read(self):
        root = build.HERE
        for path in [*root.glob("scenarios/oa*/**/*"), *root.glob("repos/oa-*/**/*"), *root.glob("workspaces/*"), root / "build_openapi.py", root / "claims_openapi.py"]:
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                for ch, name in BANNED.items():
                    self.assertNotIn(ch, text, f"{name} in {path}")
                self.assertNotIn("\r", text.replace("\r\n", ""), f"a bare carriage return in {path}")

    def test_every_text_of_every_oa_scenario_is_free_of_ascii_arrows_and_of_coverage_and_safety_wording(self):
        def strings(value):
            if isinstance(value, str):
                yield value
            elif isinstance(value, dict):
                for k, v in value.items():
                    yield k
                    yield from strings(v)
            elif isinstance(value, list):
                for v in value:
                    yield from strings(v)

        seen = 0
        for path in sorted(build.SCENARIOS_DIR.glob("oa*/*.json")):
            for text in strings(json.loads(path.read_text(encoding="utf-8"))):
                low = text.lower()
                seen += 1
                self.assertNotIn("->", text, f"{path.name}: ASCII arrow in {text[:60]!r}")
                for phrase in ("covered by", "already covers", "no need to test", "nothing to test"):
                    self.assertNotIn(phrase, low, f"{path.name}: {phrase!r}")
                self.assertIsNone(re.search(r"\bsafe\b", low), f"{path.name}: safe in {text[:60]!r}")
        self.assertGreater(seen, 500)

    def test_no_scenario_folder_has_a_prefix_nobody_owns(self):
        known = re.compile(r"^(s\d\d|oa\d\d|nx\d\d|ex\d\d|dj\d\d|sb\d\d)-[a-z0-9-]+$")
        folders = [p.name for p in build.SCENARIOS_DIR.iterdir() if p.is_dir()]
        self.assertEqual([f for f in folders if not known.match(f)], [])
        mine = [f for f in folders if f.startswith("oa")]
        self.assertEqual(sorted(mine), sorted(sc["dir"].name for sc in self.scenarios.values()))
        for f in mine:  # every oa scenario is of a repository of this workspace and is built by this builder, not the M1 one
            self.assertIn(json.loads((build.SCENARIOS_DIR / f / "scenario.json").read_text(encoding="utf-8"))["repo"], bo.load_workspace()["repos"])

    def test_the_handlers_are_what_the_spec_names(self):
        sources = {p: self.at(API, "main", p) for p in ("app/handlers/invoices.py", "app/handlers/customers.py", "app/handlers/statements.py", "app/handlers/system.py")}
        for r in co.spec_routes(self.at(API, "main", SPEC)):
            self.assertIsNotNone(co.handler_for(r["operation_id"], sources), r["operation_id"])
        self.assertEqual(sorted(n for _p, n in (co.handler_for(r["operation_id"], sources) for r in co.spec_routes(self.at(API, "main", SPEC)))),
                         ["create_invoice", "download_statement", "get", "get_customer", "get_invoice", "list", "list_invoices", "void_invoice"])

    def test_the_generated_client_defines_every_operation_and_nothing_else(self):
        names = co.generated_names(self.at(WEB, "main", GEN))
        self.assertEqual(names, {r["operation_id"] for r in co.spec_routes(self.at(WEB, "main", SPEC))})


class ExpectedJson(Base):
    def test_schema_ids_and_changes_agree(self):
        for key, sc in self.scenarios.items():
            e = sc["expected"]
            with self.subTest(scenario=key):
                self.assertEqual(e["schema"], 1)
                self.assertEqual(e["id"], sc["id"])
                self.assertEqual(e["repo"], sc["scenario"]["repo"])
                self.assertEqual(e["change"], sc["change"])
                self.assertEqual(sc["scenario"]["id"], sc["dir"].name)
                self.assertTrue(e["explains"].strip())
                self.assertEqual(sorted(e), ["change", "complete_for", "explains", "id", "must_exclude", "must_find", "not_established", "repo", "schema"])
                self.assertLessEqual(set(e["complete_for"]), MUST_FIND_TYPES)

    def test_each_scenario_claims_exactly_the_types_pinned_for_it(self):
        for key, (find, exclude, kinds) in TYPES.items():
            e = self.scenarios[key]["expected"]
            with self.subTest(scenario=key):
                self.assertEqual({m["type"] for m in e["must_find"]}, find)
                self.assertEqual({m["type"] for m in e["must_exclude"]}, exclude)
                self.assertEqual({m["kind"] for m in e["not_established"]}, kinds)
        self.assertLessEqual(set().union(*(f for f, _e, _k in TYPES.values())), MUST_FIND_TYPES)
        self.assertEqual(set().union(*(k for _f, _e, k in TYPES.values())), KNOWN_KINDS)
        self.assertEqual(set().union(*(f for f, _e, _k in TYPES.values())), MUST_FIND_TYPES)  # every type is claimed by some scenario

    def test_every_locator_is_in_a_file_at_the_revision_it_is_read_at_in_code_on_one_line(self):
        seen = 0
        for key, sc in self.scenarios.items():
            locators, comments, entries = [], [], []
            claims.collect(sc["expected"], sc["repo"], locators, comments, entries)
            self.assertEqual(comments, [])
            for repo, item, parent in locators:
                rev = build.evidence_revision(item, repo, sc["repo"], sc["change"], parent)
                text = self.at(repo, rev, item["path"])
                with self.subTest(scenario=key, path=item["path"], contains=item["contains"][:40]):
                    self.assertIsNotNone(text, f"{item['path']} is absent at {rev} in {repo}")
                    self.assertNotIn("\n", item["contains"])
                    flags = claims.occurrences(text, item["contains"])
                    self.assertTrue(flags and not all(flags), "occurs only in a comment, or not at all")
                    seen += 1
        self.assertGreater(seen, 150)

    def test_no_locator_names_a_file_the_scenario_does_not_have_and_every_entry_names_a_repository_of_the_workspace(self):
        for key, sc in self.scenarios.items():
            for section in ("must_find", "must_exclude"):
                for item in sc["expected"][section]:
                    if "repo" in item:
                        self.assertIn(item["repo"], (API, WEB), key)

    def test_not_established_subjects_have_their_formats(self):
        for key, sc in self.scenarios.items():
            for item in sc["expected"]["not_established"]:
                with self.subTest(scenario=key, kind=item["kind"]):
                    if item["kind"] == "spec_unreadable":
                        self.assertRegex(item["subject"], r"^acme/ledger-(api|web):api/openapi\.json$")
                        self.assertTrue(item["blocking"])
                    elif item["kind"] == "edge_incomplete":
                        self.assertEqual(item["subject"], "edge:http:acme/ledger-web:acme/ledger-api")
                        self.assertTrue(item["blocking"])
                    else:
                        self.assertEqual(item["kind"], "not_distinctive")
                        self.assertFalse(item["blocking"])
                        self.assertRegex(item["subject"], r"^(acme/ledger-web:)?(get|list|downloadStatement)$")
                        self.assertEqual(item["subject"].startswith("acme/ledger-web:"), sc["repo"] == API)  # a record of another repository names it


class Routes(Base):
    """The routes the scenarios claim are the spec's, composed with the base path of its server, cited at the lines the oracle finds."""

    def routes(self, repo, rev):
        return co.spec_routes(self.at(repo, rev, SPEC))

    def test_every_route_entry_is_a_route_of_the_spec_at_the_head_with_its_three_lines(self):
        seen = 0
        for key, sc in self.scenarios.items():
            head = self.routes(API, self.api_rev(sc))
            text = self.at(API, self.api_rev(sc), SPEC).split("\n")
            for item in sc["expected"]["must_find"]:
                if item["type"] != "route" and not (item["type"] == "http_edge" and not item["to"].get("removed")):
                    continue
                route = item if item["type"] == "route" else item["to"]
                with self.subTest(scenario=key, route=f"{route['method']} {route['path']}"):
                    (found,) = [r for r in head if (r["method"], r["path"]) == (route["method"], route["path"])]
                    lines = [text[n - 1] for n in found["lines"]]
                    for got, want in zip(lines, route["evidence"]):
                        self.assertIn(want["contains"], got)
                    self.assertEqual(len(route["evidence"]), 3)
                    seen += 1
        self.assertGreater(seen, 15)

    def test_every_excluded_route_exists_so_the_exclusion_means_something(self):
        for key, sc in self.scenarios.items():
            for item in sc["expected"]["must_exclude"]:
                if item["type"] != "route":
                    continue
                with self.subTest(scenario=key, route=f"{item['method']} {item['path']}"):
                    present = {(r["method"], r["path"]) for rev in ("main", self.api_rev(sc)) for r in self.routes(API, rev)}
                    self.assertIn((item["method"], item["path"]), present)

    def test_the_removed_and_added_routes_are_the_spec_diff(self):
        for key in ("oa03", "oa04"):
            sc = self.scenarios[key]
            d = co.diff(self.at(API, self.base(sc), SPEC), self.at(API, self.head(sc), SPEC))
            e = sc["expected"]
            with self.subTest(scenario=key):
                if key == "oa03":
                    self.assertEqual((d["removed"], d["added"], d["changed"]), ({("GET", "/v1/customers/{customerId}")}, set(), set()))
                    (edge,) = [m for m in e["must_find"] if m["type"] == "http_edge"]
                    self.assertEqual((edge["to"]["method"], edge["to"]["path"], edge["to"].get("removed")), ("GET", "/v1/customers/{customerId}", True))
                else:
                    self.assertEqual((d["removed"], d["added"], d["changed"]), (set(), {("GET", "/v1/invoices/{invoiceId}/pdf")}, set()))
                    (r,) = [m for m in e["must_find"] if m["type"] == "route"]
                    self.assertEqual((r["method"], r["path"]), ("GET", "/v1/invoices/{invoiceId}/pdf"))

    def test_the_changed_operations_of_a_spec_change_are_the_routes_the_scenario_claims(self):
        for key in ("oa05", "oa06"):
            sc = self.scenarios[key]
            d = co.diff(self.at(API, self.base(sc), SPEC), self.at(API, self.head(sc), SPEC))
            claimed = {(m["method"], m["path"]) for m in sc["expected"]["must_find"] if m["type"] == "route"}
            with self.subTest(scenario=key):
                self.assertEqual((d["removed"], d["added"]), (set(), set()))
                self.assertEqual(d["changed"], claimed)
                self.assertEqual(len([m for m in sc["expected"]["must_find"] if m["type"] == "finding" and m["kind"] == "operation-changed"]), len(claimed))
                everything = {(r["method"], r["path"]) for r in self.routes(API, self.api_rev(sc))}
                excluded = {(m["method"], m["path"]) for m in sc["expected"]["must_exclude"] if m["type"] == "route"}
                self.assertTrue(excluded <= everything - claimed)

    def test_the_change_of_oa05_is_one_parameter_and_that_of_oa06_is_one_schema(self):
        d5 = claims.changed_lines(self.repos[API], "main", "scenario/oa05-operation-changed")
        self.assertEqual(sorted(d5), [SPEC])
        text = self.at(API, "scenario/oa05-operation-changed", SPEC).split("\n")
        self.assertTrue(all(any(k in text[n - 1] for k in ("expand", "name", "in", "required", "schema", "type", "enum", "customer", "[", "]", "{", "}")) for n in d5[SPEC]["added"]))
        d6 = claims.changed_lines(self.repos[API], "main", "scenario/oa06-schema-ref-changed")
        text6 = self.at(API, "scenario/oa06-schema-ref-changed", SPEC).split("\n")
        added = " ".join(text6[n - 1].strip() for n in sorted(d6[SPEC]["added"]))
        self.assertIn("currency", added)
        self.assertNotIn("operationId", added)

    def test_the_handler_scenarios_reach_the_routes_by_name(self):
        sources = lambda rev: {p: self.at(API, rev, p) for p in ("app/handlers/invoices.py", "app/handlers/customers.py", "app/handlers/statements.py", "app/handlers/system.py")}
        by_function = {}
        for r in co.spec_routes(self.at(API, "main", SPEC)):
            found = co.handler_for(r["operation_id"], sources("main"))
            by_function.setdefault(found[1], []).append((r["method"], r["path"]))
        # oa01: the function that holds the changed lines is the handler of the route claimed
        sc = self.scenarios["oa01"]
        diff = claims.changed_lines(self.repos[API], self.base(sc), self.head(sc))
        names = claims.functions_containing_changes(INV, self.at(API, self.head(sc), INV), self.at(API, self.base(sc), INV), diff[INV])
        self.assertEqual(names, {"get_invoice"})
        self.assertEqual({(m["method"], m["path"]) for m in sc["expected"]["must_find"] if m["type"] == "route"}, {r for n in names for r in by_function[n]})
        # oa02: the handlers that use the changed service function (by name) are the routes claimed
        sc = self.scenarios["oa02"]
        users = {n for n, text in self._function_bodies(self.at(API, self.head(sc), INV)).items() if re.search(r"\binvoice_total\(", text)}
        self.assertEqual(users, {"get_invoice"})
        self.assertEqual({(m["method"], m["path"]) for m in sc["expected"]["must_find"] if m["type"] == "route"}, {r for n in users for r in by_function[n]})
        # oa04: the added function is the handler of the added operation
        sc = self.scenarios["oa04"]
        added = self.at(API, self.head(sc), INV)
        self.assertIn("def get_invoice_pdf(", added)
        self.assertEqual(co.handler_for("getInvoicePdf", {INV: added}), (INV, "get_invoice_pdf"))

    @staticmethod
    def _function_bodies(text):
        parts = re.split(r"^def (\w+)\(", text, flags=re.M)
        return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}


INV = "app/handlers/invoices.py"


class Clients(Base):
    """The calls of the web app through its generated client: URL and method from the web copy of the spec, a name only when it is
    distinctive, the best route by segments."""

    def web_sources(self, rev):
        files = claims.git_out(self.repos[WEB], "ls-tree", "-r", "--name-only", rev).split()
        return {p: self.at(WEB, rev, p) for p in files if p.endswith((".ts", ".tsx"))}

    def calls(self, rev):
        """(file, line, text, operationId, bound) of every use of a generated function in a file that imports the generated client: `bound` when
        the file imports the name from it (a resolved edge, whatever the name), else a member use of the name (a match by name alone)."""
        urls = co.client_urls(self.at(WEB, "main", SPEC))  # (the copy of the spec is broken at the head of oa10, which claims no edge)
        out = []
        for path, text in self.web_sources(rev).items():
            if path.startswith("src/api/generated/") or re.search(r"\.(test|spec)\.", path):
                continue
            imports = re.findall(r"""import \{([^}]*)\} from ["'](?:@/api/generated/client|(?:\.\./)+api/generated/client)["']""", text)
            if not imports and not re.search(r"""from ["'](@/api/generated/client|(\.\./)+api/generated/client)["']""", text):
                continue
            imported = {n.strip() for group in imports for n in group.split(",") if n.strip()}
            for n, line in enumerate(text.split("\n"), 1):
                if line.lstrip().startswith(("import ", "//")):
                    continue
                for name in urls:
                    if re.search(rf"(?<![\w.]){re.escape(name)}\s*\(", line):
                        if name in imported:
                            out.append((path, n, line, name, True))
                    elif re.search(rf"\.{re.escape(name)}\s*\(", line):
                        out.append((path, n, line, name, False))
        return out

    def test_the_generic_names_and_the_over_defined_one_are_exactly_the_dropped_ones(self):
        sources = self.web_sources("main")
        spec = self.at(WEB, "main", SPEC)
        counts = {name: co.definitions(sources, name) for name in co.client_urls(spec)}
        dropped = {n for n in counts if co.is_generic(n) or counts[n] > 3}
        self.assertEqual(dropped, {"get", "list", "downloadStatement"})
        self.assertEqual({n: counts[n] for n in dropped}, {"get": 2, "list": 1, "downloadStatement": 4})
        for key, sc in self.scenarios.items():
            for item in sc["expected"]["not_established"]:
                if item["kind"] == "not_distinctive":
                    self.assertEqual(item["definitions"], counts[item["subject"].split(":")[-1]], f"{key} {item['subject']}")

    def test_the_http_edges_the_scenarios_claim_are_calls_that_fit_the_route_best(self):
        urls = co.client_urls(self.at(WEB, "main", SPEC))
        api_routes_at = lambda sc, base=False: co.spec_routes(self.at(API, self.base(sc) if base else self.api_rev(sc), SPEC))
        checked = 0
        for key, sc in self.scenarios.items():
            rev = self.head(sc) if sc["repo"] == WEB else "main"
            calls = self.calls(rev)
            for item in sc["expected"]["must_find"]:
                if item["type"] != "http_edge":
                    continue
                to = item["to"]
                routes = api_routes_at(sc, base=bool(to.get("removed")))
                with self.subTest(scenario=key, to=f"{to['method']} {to['path']}"):
                    here = [c for c in calls if c[0] == item["from"]["path"] and item["from"]["contains"] in c[2]]
                    self.assertTrue(here, f"no call of a generated function on a line containing {item['from']['contains']!r}")
                    names = {c[3] for c in here}
                    fits = [co.best_routes(urls[n][1], urls[n][0], routes) for n in names]
                    self.assertIn((to["method"], to["path"]), {(r["method"], r["path"]) for group in fits for r in group})
                    checked += 1
        self.assertEqual(checked, 12)  # oa01, oa02, oa03, oa05 one each, oa06 four, oa08 and oa09 two each

    def test_for_a_change_to_the_api_the_edges_are_every_call_whose_best_route_is_a_reached_route(self):
        urls = co.client_urls(self.at(WEB, "main", SPEC))
        calls = self.calls("main")
        for key, sc in self.scenarios.items():
            if sc["repo"] != API or "http_edge" not in sc["expected"]["complete_for"]:
                continue
            exp = sc["expected"]
            reached = {(m["method"], m["path"]) for m in exp["must_find"] if m["type"] == "route"}
            if key == "oa03":
                reached = {("GET", "/v1/customers/{customerId}")}
                routes = co.spec_routes(self.at(API, self.base(sc), SPEC))
            else:
                routes = co.spec_routes(self.at(API, self.api_rev(sc), SPEC))
            dropped = {n for n in urls if co.is_generic(n) or n == "downloadStatement"}
            want = set()
            for path, n, text, name, bound in calls:
                if name in dropped and not bound:  # the cap is for a match by name alone: an import is resolved
                    continue
                for r in co.best_routes(urls[name][1], urls[name][0], routes):
                    if (r["method"], r["path"]) in reached:
                        want.add((path, r["method"], r["path"]))
            got = {(m["from"]["path"], m["to"]["method"], m["to"]["path"]) for m in exp["must_find"] if m["type"] == "http_edge"}
            with self.subTest(scenario=key):
                self.assertEqual(got, want)

    def test_the_excluded_calls_are_calls_of_other_operations_or_of_dropped_names(self):
        urls = co.client_urls(self.at(WEB, "main", SPEC))
        sources = self.web_sources("main")
        for key in ("oa01", "oa09"):
            sc = self.scenarios[key]
            rev = self.head(sc) if sc["repo"] == WEB else "main"
            calls = self.calls(rev)
            for item in sc["expected"]["must_exclude"]:
                if item["type"] != "http_edge":
                    continue
                with self.subTest(scenario=key, why=item["why"][:40]):
                    here = [c for c in calls if c[0] == item["from"]["path"] and item["from"]["contains"] in c[2]]
                    text = self.at(WEB, rev, item["from"]["path"])
                    self.assertIn(item["from"]["contains"], text)
                    if key == "oa01":
                        self.assertEqual({urls[c[3]][0] for c in here}, {"DELETE"})  # voidInvoice: a DELETE, the changed handler serves the GET
                    else:  # a member call of a generic name: matched by name alone, which the cap refuses
                        self.assertTrue(here and all(not c[4] and (co.is_generic(c[3]) or co.definitions(sources, c[3]) > 3) for c in here), here)

    def test_the_web_files_that_call_by_a_generic_name_exist_and_are_screens_of_the_scenario(self):
        sc = self.scenarios["oa09"]
        text = self.at(WEB, self.head(sc), "src/modules/reports/pages/ReportsPage.tsx")
        self.assertIn("list()", text)
        self.assertIn('cache.get("reports")', text)
        self.assertIn("import { list } from", text)
        self.assertIn("downloadStatement(", self.at(WEB, self.head(sc), "src/modules/statements/pages/StatementsPage.tsx"))

    def test_the_test_file_that_names_a_generated_function_is_not_a_caller_and_imports_no_changed_file(self):
        text = self.at(WEB, "main", "src/modules/invoices/pages/InvoiceDetailPage.test.tsx")
        self.assertIn("getInvoice", text)
        self.assertNotIn('"../api"', text)

    def test_the_backend_calls_modules_are_what_the_handler_file_imports(self):
        sc = self.scenarios["oa08"]
        handler = self.at(API, "main", INV)
        imports = set(re.findall(r"^from (app\.[\w.]+) import", handler, re.M))
        for item in sc["expected"]["must_find"]:
            if item["type"] == "backend_call":
                self.assertEqual(item["handler"], INV)
                self.assertEqual({m["path"] for m in item["modules"]}, {i.replace(".", "/") + ".py" for i in imports})
                self.assertEqual({m["depth"] for m in item["modules"]}, {1})
                ledger = self.at(API, "main", "app/services/ledger.py")
                self.assertFalse(re.search(r"^(from|import) app", ledger, re.M), "the service imports nothing of the app: depth 1 is all")


class Changes(Base):
    def test_every_scenario_changes_what_it_says(self):
        expect = {
            "oa01": {INV},
            "oa02": {"app/services/ledger.py"},
            "oa03": {SPEC, "app/handlers/customers.py"},
            "oa04": {SPEC, INV},
            "oa05": {SPEC},
            "oa06": {SPEC},
            "oa07": {SPEC, INV},
            "oa08": {"src/modules/invoices/api.ts"},
            "oa09": {"src/modules/reports/pages/ReportsPage.tsx", "src/modules/statements/pages/StatementsPage.tsx"},
            "oa10": {SPEC, "src/modules/invoices/pages/InvoiceDetailPage.tsx"},
        }
        for key, files in expect.items():
            sc = self.scenarios[key]
            changed = set(claims.git_out(self.repos[sc["repo"]], "diff", "--name-only", f"{self.base(sc)}...{self.head(sc)}").split())
            with self.subTest(scenario=key):
                self.assertEqual(changed, files)

    def test_the_changed_symbols_are_the_functions_that_contain_the_changed_lines(self):
        for key in ("oa01", "oa02", "oa04", "oa07", "oa08", "oa09", "oa10"):
            sc = self.scenarios[key]
            diff = claims.changed_lines(self.repos[sc["repo"]], self.base(sc), self.head(sc))
            for item in [m for m in sc["expected"]["must_find"] if m["type"] == "changed_symbol"]:
                with self.subTest(scenario=key, symbol=item["symbol"]):
                    head = self.at(sc["repo"], self.head(sc), item["path"])
                    base = self.at(sc["repo"], self.base(sc), item["path"])
                    if item["path"].endswith(".tsx") or item["path"].endswith(".ts"):
                        self.assertIn(item["symbol"], head)
                    else:
                        self.assertTrue(claims.symbol_changed(item["path"], item["symbol"], None, head, base, diff, removed=item.get("change") == "removed"), item)

    def test_oa07_and_oa10_really_leave_a_spec_that_is_not_json(self):
        for key, repo in (("oa07", API), ("oa10", WEB)):
            sc = self.scenarios[key]
            with self.assertRaises(ValueError):
                json.loads(self.at(repo, self.head(sc), SPEC))
            json.loads(self.at(repo, self.base(sc), SPEC))

    def test_oa03_deletes_the_handler_module_and_the_operation_together(self):
        sc = self.scenarios["oa03"]
        self.assertIsNone(self.at(API, self.head(sc), "app/handlers/customers.py"))
        self.assertIn("get_customer", self.at(API, self.base(sc), "app/handlers/customers.py"))
        self.assertNotIn("/customers/{customerId}", self.at(API, self.head(sc), SPEC))
        self.assertIn("getCustomer", self.at(WEB, "main", SPEC))  # the web copy still has it

    def test_oa04_adds_an_import_to_a_handler_module_and_a_function_that_uses_it(self):
        sc = self.scenarios["oa04"]
        added = claims.changed_lines(self.repos[API], self.base(sc), self.head(sc))[INV]["added"]
        head = self.at(API, self.head(sc), INV).split("\n")
        texts = [head[n - 1] for n in sorted(added)]
        self.assertIn("from app.services.render import statement_pdf", texts)
        self.assertIn("def get_invoice_pdf(invoice_id):", texts)


if __name__ == "__main__":
    unittest.main()
