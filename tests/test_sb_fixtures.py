"""The M16 Spring Boot fixtures build, and every sb expected.json is true of the files.

The hand-written expected answers are checked against an independent reading of the repositories (`tests/fixtures/sb_claims.py`, which shares no
code with the adapters): the routes the controllers make with their paths and files, the files that import or use a class, the definitions of a
name, the span of a changed member, that every locator names a line of code at the revision it is read at, the URL fit of every http edge. The
adapters' own output is compared with the same files in test_scenarios_sb.py.
"""

import json
import re
import tempfile
import time
import unittest
from pathlib import Path

from tests import support_sb as sb
from tests.fixtures import build, sb_build, sb_claims
from tests.fixtures.claims import (
    Tree, changed_lines, client_urls, collect, git_out, match_kind, occurrences, ref_exists, ref_shas, text_at,
)

BANNED = chr(0x2013) + chr(0x2014) + chr(0x2192) + chr(0x2018) + chr(0x2019) + chr(0x201c) + chr(0x201d) + "".join(chr(c) for c in range(0x202a, 0x202f)) + "".join(chr(c) for c in range(0x2066, 0x206a))  # en and em dash, arrow, curly quotes, bidi controls (built from code points: no raw one in this file)
SB_KINDS = {"not_distinctive", "parse_degraded", "config_not_followed", "client_url_unmatched", "edge_incomplete", "route_path_partial", "route_change_withheld"}
TYPES = {"changed_symbol", "changed_file", "caller", "route", "screen", "http_edge", "unit_test", "finding"}


class Build(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        start = time.monotonic()
        cls.repos = sb_build.build_all(Path(cls.tmp.name))
        cls.seconds = time.monotonic() - start

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_build_is_fast(self):
        self.assertLess(self.seconds, 15, f"build took {self.seconds:.1f}s")

    def test_two_repos_on_main_with_the_scenario_branches(self):
        self.assertEqual(sorted(self.repos), ["acme/ledger", "acme/ledger-web"])
        for name, path in self.repos.items():
            self.assertTrue((path / ".git").is_dir())
            self.assertEqual(git_out(path, "branch", "--show-current").strip(), "main")
            self.assertEqual(git_out(path, "remote").strip(), "")
        for sc in sb_build.scenarios(Path(self.tmp.name)):
            self.assertTrue(ref_exists(sc["repo_path"], sc["change"]["head"]), sc["id"])
            self.assertNotEqual(git_out(sc["repo_path"], "diff", "--stat", f"{sc['change']['base']}...{sc['change']['head']}").strip(), "", sc["id"])

    def test_two_builds_have_identical_shas(self):
        with tempfile.TemporaryDirectory() as two:
            other = sb_build.build_all(Path(two))
            for name in self.repos:
                self.assertEqual(ref_shas(self.repos[name]), ref_shas(other[name]), name)
                self.assertTrue(ref_shas(self.repos[name]).strip())

    def test_the_m1_fixtures_are_untouched_by_the_sb_ones(self):
        """The M1 builder knows nothing of the sb repositories and scenarios, and they are in a tree of their own."""
        self.assertEqual(sorted(build.load_workspace()["repos"]), ["acme/api", "acme/contracts", "acme/web", "acme/worker"])
        self.assertTrue(all(not p.name.startswith("sb") for p in build.SCENARIOS_DIR.iterdir()))
        self.assertTrue(all(not p.name.startswith("sb") for p in build.REPOS_DIR.iterdir()))

    def test_the_fixture_has_what_the_adapters_need_to_meet(self):
        files = set(sb_claims.tree(self.repos["acme/ledger"], "main"))
        for path in (
            "pom.xml", "src/main/resources/application.properties", "src/main/java/com/acme/ledger/api/Paths.java", "src/main/java/com/acme/ledger/api/InvoiceController.java",
            "src/main/java/com/acme/ledger/api/Helper.java", "src/main/java/com/acme/ledger/service/Pricing.java", "src/main/java/com/acme/ledger/service/PricingImpl.java",
            "src/main/kotlin/com/acme/ledger/api/CustomerController.kt", "src/main/kotlin/com/acme/ledger/util/Strings.kt", "src/main/java/com/acme/ledger/config/WebConfig.java",
            "src/test/java/com/acme/ledger/service/InvoiceServiceTest.java", "src/test/java/com/acme/ledger/api/InvoiceControllerTest.java", "src/main/java/com/acme/ledger/support/Docs.java",
        ):
            self.assertIn(path, files)
        self.assertEqual(sum(f.endswith(".kt") for f in files if "/test/" not in f), 2)  # one Kotlin controller and one Kotlin utility

    def test_get_is_defined_six_times_in_non_test_code_by_the_independent_count(self):
        self.assertEqual(sb_claims.definitions(self.repos["acme/ledger"], "main", "get"), 6)
        self.assertLessEqual(sb_claims.definitions(self.repos["acme/ledger"], "main", "total"), 3)  # a name that is distinctive under distinct_max = 3

    def test_the_route_table_of_main_is_the_one_the_independent_reading_makes(self):
        table = sb_claims.routes(self.repos["acme/ledger"], "main")
        self.assertEqual(len(table), 13)
        self.assertEqual(table[("GET", "/ledger/v1/invoices/{id}/total")], "src/main/java/com/acme/ledger/api/InvoiceController.java")
        self.assertEqual(table[("GET", "/ledger/reports/daily")], "src/main/java/com/acme/ledger/api/ReportController.java")
        self.assertEqual(table[("GET", "/ledger/customers/{id}")], "src/main/kotlin/com/acme/ledger/api/CustomerController.kt")
        self.assertEqual(table[("POST", "/ledger/v1/invoices")], "src/main/java/com/acme/ledger/api/InvoiceController.java")

    def test_the_trap_files_name_the_class_and_are_not_code(self):
        repo = self.repos["acme/ledger"]
        path = "src/main/java/com/acme/ledger/support/Docs.java"
        text = text_at(repo, "main", path)
        self.assertIn("InvoiceService", text)
        self.assertNotIn("InvoiceService", sb_claims.strip_code(text))
        self.assertNotIn(path, sb_claims.importers(repo, "main", "src/main/java/com/acme/ledger/service/InvoiceService.java"))


class ScenarioFiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.e = sb.env()
        cls.repos = cls.e.repos
        cls.scenarios = sb.scenarios()
        cls.tree = Tree(cls.repos)

    def test_ids_schema_and_folders(self):
        folders = sorted(p.name for p in sb_build.SCENARIOS_DIR.iterdir() if p.is_dir())
        self.assertEqual([sc["id"] for sc in self.scenarios], folders)
        self.assertEqual(len(folders), 20)
        for sc in self.scenarios:
            with self.subTest(scenario=sc["id"]):
                exp = sc["expected"]
                self.assertEqual((exp["schema"], exp["id"], exp["repo"], exp["change"]), (1, sc["id"], sc["repo"], sc["change"]))
                self.assertEqual(sc["dir"].name, sc["id"])
                self.assertTrue(sc["id"].startswith("sb"))
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
                    self.assertIn(item["kind"], SB_KINDS)
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
        self.assertGreater(count, 300)

    def test_a_locator_in_a_java_or_kotlin_file_is_in_code_not_in_a_string_or_a_comment(self):
        """The text of a locator, blanked of comments and strings, keeps its first word: it is code (a line that is only an annotation with a string argument has its keywords)."""
        for sc in self.scenarios:
            locators, comments, entries = [], [], []
            collect(sc["expected"], sc["repo"], locators, comments, entries)
            for repo, item, parent in locators:
                if not item["path"].endswith((".java", ".kt")):
                    continue
                rev = build.evidence_revision(item, repo, sc["repo"], sc["change"], parent)
                text = text_at(self.repos[repo], rev, item["path"])
                line = next(l for l in text.splitlines() if item["contains"] in l)
                self.assertTrue(sb_claims.strip_code(line).strip(), f"{sc['id']}: {item['contains']!r} is all comment or string")

    def test_no_banned_character_in_any_fixture_file_and_no_ascii_arrow_in_prose(self):
        root = sb_build.HERE
        for path in sorted(root.rglob("*")):
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                self.assertFalse(set(text) & set(BANNED), str(path))
        for sc in self.scenarios:
            for text in (sc["expected"]["explains"], sc["scenario"]["notes"], sc["scenario"]["commit_message"]):
                self.assertNotRegex(text, r"->|=>")
                self.assertGreater(len(sc["expected"]["explains"]), 200)
        self.assertFalse(set(sb_build.WORKSPACE_FILE.read_text(encoding="utf-8")) & set(BANNED))

    def test_routes_are_the_routes_the_independent_reading_makes_in_the_file_the_evidence_names(self):
        checked = 0
        for sc in self.scenarios:
            if sc["repo"] != "acme/ledger":
                continue
            rev = sc["change"]["head"]
            try:
                truth = sb_claims.routes(self.repos["acme/ledger"], rev)
            except (AssertionError, StopIteration):
                self.assertIn(sc["id"], ("sb11-unlexable-controller",), sc["id"])  # the independent reader gives up on a file that does not lex
                continue
            for item in sc["expected"]["must_find"]:
                if item["type"] != "route":
                    continue
                with self.subTest(scenario=sc["id"], route=f"{item['method']} {item['path']}"):
                    self.assertEqual(truth.get((item["method"], item["path"])), item["evidence"][0]["path"])
                    checked += 1
            for item in sc["expected"]["must_exclude"]:
                if item["type"] == "route" and sc["id"] not in ("sb11-unlexable-controller",):
                    with self.subTest(scenario=sc["id"], excluded=f"{item['method']} {item['path']}"):
                        self.assertIn((item["method"], item["path"]), truth)  # an excluded route exists: it is not reached, it is not absent
        self.assertGreater(checked, 25)

    def test_the_removed_and_old_routes_of_the_http_edges_are_routes_of_the_base(self):
        for sc in self.scenarios:
            for item in sc["expected"]["must_find"]:
                if item["type"] == "http_edge" and item["to"].get("removed"):
                    truth = sb_claims.routes(self.repos["acme/ledger"], sc["change"]["base"])
                    with self.subTest(scenario=sc["id"], route=item["to"]["path"]):
                        self.assertIn((item["to"]["method"], item["to"]["path"]), truth)

    def test_the_importers_are_the_files_the_independent_reading_finds_and_the_expected_answer_lists_each_as_caller_exclusion_or_unit_test(self):
        for sc in self.scenarios:
            if sc["repo"] != "acme/ledger" or "caller" not in sc["expected"]["complete_for"] or sc["id"] in ("sb04-interface-impl", "sb11-unlexable-controller", "sb13-wiring", "sb16-test-only"):
                continue
            repo = self.repos["acme/ledger"]
            head = sc["change"]["head"]
            changed = [p for p in changed_lines(repo, sc["change"]["base"], head) if p.endswith((".java", ".kt")) and "/src/test/" not in "/" + p]
            truth: set[str] = set()
            for path in changed:
                if text_at(repo, head, path) is not None:
                    truth |= sb_claims.importers(repo, head, path)
            non_test = {p for p in truth if "/src/test/" not in "/" + p}
            listed = {i["path"] for i in sc["expected"]["must_find"] + sc["expected"]["must_exclude"] if i["type"] == "caller" and (i["path"] in non_test or "evidence" in i)}
            wanted = {i["path"] for i in sc["expected"]["must_find"] if i["type"] == "caller"}
            excluded = {i["path"] for i in sc["expected"]["must_exclude"] if i["type"] == "caller" and "evidence" in i}
            with self.subTest(scenario=sc["id"]):
                self.assertEqual(wanted | excluded, non_test - {p for p in non_test if p.endswith("Broken.java")} if sc["id"] != "sb12-unlexable-importer" else wanted | excluded)
                self.assertTrue(listed >= wanted)
                tests = {p for p in truth if "/src/test/" in "/" + p}
                self.assertTrue({i["path"] for i in sc["expected"]["must_find"] if i["type"] == "unit_test"} <= tests | {p for p in sb_claims.code_files(repo, head, tests=True) if "/src/test/" in "/" + p})

    def test_an_interface_scenario_has_the_implementation_and_a_caller_of_the_interface_by_the_independent_reading(self):
        repo = self.repos["acme/ledger"]
        impl = text_at(repo, "main", "src/main/java/com/acme/ledger/service/PricingImpl.java")
        self.assertRegex(sb_claims.strip_code(impl), r"class\s+PricingImpl\s+implements\s+Pricing\b")
        users = sb_claims.importers(repo, "main", "src/main/java/com/acme/ledger/service/Pricing.java")
        self.assertIn("src/main/java/com/acme/ledger/api/InvoiceController.java", users)
        self.assertIn("src/main/java/com/acme/ledger/service/PricingImpl.java", users)  # it implements the interface: same package, no import

    def test_a_changed_symbol_is_a_member_whose_lines_the_diff_touches(self):
        checked = 0
        for sc in self.scenarios:
            repo = self.repos[sc["repo"]]
            diff = changed_lines(repo, sc["change"]["base"], sc["change"]["head"])
            for item in sc["expected"]["must_find"]:
                if item["type"] != "changed_symbol" or not item["path"].endswith((".java", ".kt")):
                    continue
                name = item["symbol"].rsplit(".", 1)[-1]
                head = text_at(repo, sc["change"]["head"], item["path"])
                base = text_at(repo, sc["change"]["base"], item["path"])
                entry = diff[item["path"]]
                with self.subTest(scenario=sc["id"], symbol=item["symbol"]):
                    if "." not in item["symbol"] and name == Path(item["path"]).stem:  # the type itself: a line of its header changed
                        span = (1, len(head.splitlines()))
                        self.assertTrue(entry["added"] | entry["removed"])
                    else:
                        spans = [(s, sb_claims.member_span(t, name)) for s, t in (("head", head), ("base", base)) if t is not None]
                        hit = False
                        for side, span in spans:
                            if span is None:
                                continue
                            lines = entry["added"] if side == "head" else entry["removed"]
                            hit = hit or any(span[0] <= n <= span[1] for n in lines | entry["points"])
                        self.assertTrue(hit, f"no changed line in {item['symbol']}")
                    checked += 1
        self.assertGreater(checked, 12)

    def test_the_not_distinctive_counts_are_the_definitions_in_non_test_code_at_the_head(self):
        found = 0
        for sc in self.scenarios:
            for item in sc["expected"]["not_established"]:
                if item["kind"] != "not_distinctive":
                    continue
                with self.subTest(scenario=sc["id"]):
                    self.assertEqual(sb_claims.definitions(self.repos[sc["repo"]], sc["change"]["head"], item["subject"]), item["definitions"])
                    self.assertGreater(item["definitions"], 3)  # distinct_max of the workspace
                    found += 1
        self.assertGreaterEqual(found, 3)

    def test_an_http_edge_is_a_client_url_that_fits_the_route_by_segments_and_method(self):
        checked = 0
        urls = client_urls(text_at(self.repos["acme/ledger-web"], "main", "src/api/endpoints.ts"))
        for sc in self.scenarios:
            for item in (i for i in sc["expected"]["must_find"] if i["type"] == "http_edge"):
                client = item["from"]["contains"]
                name = re.search(r"apiGet\((\w+)", client).group(1)
                with self.subTest(scenario=sc["id"], url=urls[name]):
                    self.assertIsNotNone(match_kind(urls[name], item["to"]["path"]), f"{urls[name]} does not fit {item['to']['path']}")
                    self.assertEqual(item["to"]["method"], "GET")
                    # a parameter of the client fits only a parameter of the route
                    for c, r in zip(urls[name].strip("/").split("/"), item["to"]["path"].strip("/").split("/")):
                        if c == "{}":
                            self.assertTrue(r.startswith("{"))
                checked += 1
        self.assertGreater(checked, 12)

    def test_the_unlexable_files_do_not_lex_by_a_plain_count_and_the_others_do(self):
        repo = self.repos["acme/ledger"]
        self.assertTrue(sb_claims.unlexable(text_at(repo, "scenario/sb11-unlexable-controller", "src/main/java/com/acme/ledger/api/InvoiceController.java")))
        self.assertTrue(sb_claims.unlexable(text_at(repo, "scenario/sb12-unlexable-importer", "src/main/java/com/acme/ledger/support/Broken.java")))
        for path in sb_claims.code_files(repo, "main", tests=True):
            self.assertFalse(sb_claims.unlexable(text_at(repo, "main", path)), path)

    def test_the_diff_of_each_scenario_is_the_change_its_notes_say(self):
        def names(sid):
            sc = next(s for s in self.scenarios if s["id"] == sid)
            return git_out(self.repos[sc["repo"]], "diff", "--name-status", f"{sc['change']['base']}...{sc['change']['head']}").split()

        self.assertEqual(names("sb09-removed-route"), ["M", "src/main/java/com/acme/ledger/api/InvoiceController.java"])
        self.assertEqual(sorted(names("sb12-unlexable-importer")), sorted(["A", "src/main/java/com/acme/ledger/support/Broken.java", "M", "src/main/java/com/acme/ledger/service/InvoiceService.java"]))
        self.assertEqual(names("sb10-context-path"), ["M", "src/main/resources/application.properties"])
        self.assertEqual(names("sb16-test-only"), ["M", "src/test/java/com/acme/ledger/service/InvoiceServiceTest.java"])


if __name__ == "__main__":
    unittest.main()
