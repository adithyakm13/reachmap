"""M16, the Django fixtures: the repositories build, and every expected.json of the `dj` scenarios is true of the files.

The checks do not trust expected.json and do not use the adapter: the route table of the project at main and at every scenario head is derived by
`tests/fixtures/claims_dj.py`, a small independent reader of the one shape the fixtures use, and compared with what the scenarios claim. (The adapter is
checked against the same files, through `reachmap analyse`, in test_scenarios_dj.py.)
"""

from __future__ import annotations

import json
import re
import tempfile
import time
import unittest
from pathlib import Path

from tests.fixtures import build, claims, claims_dj, dj

BANNED = {chr(c): name for c, name in ((0x2013, "en dash"), (0x2014, "em dash"), (0x2192, "arrow"), (0x2018, "left single quote"), (0x2019, "right single quote"),
                                       (0x201C, "left double quote"), (0x201D, "right double quote"))}
FIXTURES = Path(build.__file__).resolve().parent

# the route table of the project at main, written by hand from billing/urls.py, cases/urls.py, config/urls.py and the DRF viewsets
MAIN_ROUTES = sorted([
    ("ANY", "/billing"), ("ANY", "/billing/invoices"), ("GET", "/billing/invoices/{invoice_id}"), ("PUT", "/billing/invoices/{invoice_id}"),
    ("POST", "/billing/invoices/{invoice_id}/void"), ("ANY", "/billing/reports/monthly"), ("ANY", "/cases"), ("ANY", "/cases/{case_id}"), ("ANY", "/legacy/{year}"),
    ("GET", "/api/v1"), ("GET", "/api/v1/invoices"), ("GET", "/api/v1/invoices/{pk}"), ("POST", "/api/v1/invoices/{pk}/void"), ("GET", "/api/v1/invoices/overdue"),
    ("GET", "/api/v1/customers"), ("POST", "/api/v1/customers"), ("GET", "/api/v1/customers/{pk}"), ("PUT", "/api/v1/customers/{pk}"),
    ("PATCH", "/api/v1/customers/{pk}"), ("DELETE", "/api/v1/customers/{pk}"),
])


_BUILT: dict = {}


def built() -> dict:
    """The repositories, built once for the module (read only)."""
    if not _BUILT:
        tmp = tempfile.TemporaryDirectory()
        start = time.monotonic()
        repos = dj.build_dj(Path(tmp.name))
        _BUILT.update(tmp=tmp, repos=repos, seconds=time.monotonic() - start, scenarios=dj.scenarios_dj(Path(tmp.name)))
    return _BUILT


def tearDownModule() -> None:
    if _BUILT:
        _BUILT["tmp"].cleanup()
        _BUILT.clear()


class Built(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        b = built()
        cls.repos, cls.seconds, cls.scenarios = b["repos"], b["seconds"], b["scenarios"]
        cls.portal = cls.repos["acme/portal"]

    _tables: dict[tuple[str, str], list[dict]] = {}

    def table(self, rev: str) -> list[dict]:
        key = (str(self.portal), rev)  # the repositories are read only: one derivation per revision for the whole class
        if key not in self._tables:
            self._tables[key] = claims_dj.routes(self.portal, rev)[0]
        return self._tables[key]


class Build(Built):
    def test_the_build_is_fast_and_makes_both_repositories(self):
        self.assertLess(self.seconds, 10, f"build took {self.seconds:.1f}s")
        self.assertEqual(sorted(self.repos), ["acme/portal", "acme/portal-web"])
        for path in self.repos.values():
            self.assertTrue((path / ".git").is_dir())

    def test_every_scenario_has_its_branch_and_a_non_empty_diff(self):
        self.assertEqual(len(self.scenarios), 14)
        for sc in self.scenarios:
            with self.subTest(scenario=sc["id"]):
                head = sc["change"]["head"]
                self.assertTrue(claims.ref_exists(sc["repo_path"], head))
                diff = claims.git_out(sc["repo_path"], "diff", "--stat", f"{sc['change']['base']}...{head}")
                self.assertTrue(diff.strip())

    def test_the_m1_builder_does_not_build_or_list_these_scenarios(self):
        with tempfile.TemporaryDirectory() as tmp:
            repos = build.build_all(Path(tmp))
            self.assertEqual(sorted(repos), ["acme/api", "acme/contracts", "acme/web", "acme/worker"])
            self.assertEqual([sc["id"] for sc in build.scenarios(Path(tmp)) if sc["id"].startswith("dj")], [])
        self.assertEqual([p.name for p in build.m1_scenario_dirs() if p.name.startswith("dj")], [])

    def test_two_builds_have_the_same_commits(self):
        with tempfile.TemporaryDirectory() as tmp:
            again = dj.build_dj(Path(tmp))
            for name, path in self.repos.items():
                self.assertEqual(claims.ref_shas(path), claims.ref_shas(again[name]), name)

    def test_ids_schema_and_changes(self):
        for sc in self.scenarios:
            with self.subTest(scenario=sc["id"]):
                exp = sc["expected"]
                self.assertEqual((exp["schema"], exp["id"], exp["repo"], exp["change"]), (1, sc["dir"].name, sc["repo"], sc["change"]))
                self.assertIn(sc["repo"], dj.load_workspace()["repos"])
                self.assertTrue(exp["explains"].strip())

    def test_no_banned_character_in_a_dj_fixture_file(self):
        bad = []
        roots = [FIXTURES / "repos" / "dj-portal", FIXTURES / "repos" / "dj-portal-web", FIXTURES / "workspaces", *[p for p in (FIXTURES / "scenarios").iterdir() if p.name.startswith("dj")]]
        for root in roots:
            for path in root.rglob("*"):
                if path.is_file() and path.suffix != ".pyc":
                    text = path.read_text(encoding="utf-8")
                    bad += [f"{path}: {name}" for ch, name in BANNED.items() if ch in text]
                    if "->" in text and path.suffix in (".json", ".toml"):
                        bad.append(f"{path}: ASCII arrow")
        self.assertEqual(bad, [])

    def test_no_dj_scenario_text_uses_banned_wording(self):
        for sc in self.scenarios:
            texts = [sc["expected"]["explains"], *(e.get("why", "") for sec in ("must_find", "must_exclude") for e in sc["expected"][sec])]
            for text in texts:
                self.assertNotIn("covered" + " by", text.lower())
                self.assertNotRegex(text.lower(), r"\bsafe\b")


class Locators(Built):
    """Every {path, contains} names a file that exists at the revision it is read at and a line in code that holds the string (a `comment` locator must be a comment)."""

    def test_every_locator_is_true_of_the_files(self):
        checked = 0
        for sc in self.scenarios:
            locators: list = []
            comments: list = []
            entries: list = []
            claims.collect(sc["expected"], sc["repo"], locators, comments, entries)
            for repo, item, parent in locators:
                rev = build.evidence_revision(item, repo, sc["repo"], sc["change"], parent)
                text = claims.text_at(self.repos[repo], rev, item["path"])
                with self.subTest(scenario=sc["id"], path=item["path"], contains=item["contains"]):
                    self.assertIsNotNone(text, f"{item['path']} is not at {rev}")
                    marks = claims.occurrences(text, item["contains"])
                    self.assertTrue(marks, "the string does not occur")
                    self.assertTrue(any(not m for m in marks), "the string occurs only in comments")
                    self.assertFalse("\n" in item["contains"])
                checked += 1
            for repo, item, parent in comments:
                rev = build.evidence_revision(item, repo, sc["repo"], sc["change"], parent)
                text = claims.text_at(self.repos[repo], rev, item["path"]) or ""
                with self.subTest(scenario=sc["id"], comment=item["contains"]):
                    marks = claims.occurrences(text, item["contains"])
                    self.assertTrue(marks and all(marks), "a comment locator must occur only in comments")
        self.assertGreater(checked, 100)


class RouteTables(Built):
    def test_the_route_table_at_main_is_the_one_written_by_hand(self):
        got = sorted((r["method"], r["path"]) for r in self.table("main"))
        self.assertEqual(got, MAIN_ROUTES)

    def test_a_urlconf_nothing_includes_is_found_and_the_commented_include_is_not_one(self):
        _routes, unmounted = claims_dj.routes(self.portal, "main")
        self.assertEqual(unmounted, ["billing/legacy_urls.py"])

    def test_every_route_a_scenario_claims_is_in_the_table_of_its_head_with_the_evidence_it_lists(self):
        seen = 0
        for sc in self.scenarios:
            if sc["id"] in ("dj11-urlconf-syntax-error", "dj12-root-urlconf-dynamic", "dj14-settings-flag"):
                continue
            table = {(r["method"], r["path"]): r for r in self.table(sc["change"]["head"])}
            for entry in sc["expected"]["must_find"]:
                if entry["type"] != "route":
                    continue
                with self.subTest(scenario=sc["id"], route=f"{entry['method']} {entry['path']}"):
                    found = table[(entry["method"], entry["path"])]
                    lines = [claims.text_at(self.portal, sc["change"]["head"], f)  # the file the oracle took the line from
                             .split("\n")[n - 1] for f, n in found["lines"]]
                    self.assertEqual(len(lines), len(entry["evidence"]))
                    for line, ev, (f, _n) in zip(lines, entry["evidence"], found["lines"]):
                        self.assertEqual(ev["path"], f)
                        self.assertIn(ev["contains"], line)
                seen += 1
        self.assertGreaterEqual(seen, 15)

    def test_a_route_a_scenario_excludes_by_path_is_not_in_the_table_unless_the_exclusion_says_why_it_is_another_route(self):
        for sc in self.scenarios:
            if sc["id"] in ("dj11-urlconf-syntax-error", "dj12-root-urlconf-dynamic", "dj14-settings-flag"):
                continue
            table = {(r["method"], r["path"]) for r in self.table(sc["change"]["head"])}
            for entry in sc["expected"]["must_exclude"]:
                if entry["type"] == "route" and "comment" in entry:
                    with self.subTest(scenario=sc["id"], route=entry["path"]):
                        self.assertNotIn((entry["method"], entry["path"]), table)  # no include mounts it: there is no such route

    def test_a_claimed_route_that_a_scenario_makes_new_or_removes_differs_between_base_and_head_as_stated(self):
        by_id = {sc["id"]: sc for sc in self.scenarios}
        head = {(r["method"], r["path"]) for r in self.table(by_id["dj09-include-moved"]["change"]["head"])}
        base = {(r["method"], r["path"]) for r in self.table("main")}
        self.assertEqual(sorted(base - head), [("ANY", "/cases"), ("ANY", "/cases/{case_id}")])
        self.assertEqual(sorted(head - base), [("ANY", "/support/cases"), ("ANY", "/support/cases/{case_id}")])
        head10 = {(r["method"], r["path"]) for r in self.table(by_id["dj10-route-added"]["change"]["head"])}
        self.assertEqual(sorted(head10 - base), [("ANY", "/billing/invoices/export")])
        self.assertEqual(sorted(base - head10), [])

    def test_the_two_scenarios_that_break_the_table_really_do(self):
        by_id = {sc["id"]: sc for sc in self.scenarios}
        with self.assertRaises(SyntaxError):
            self.table(by_id["dj11-urlconf-syntax-error"]["change"]["head"])
        with self.assertRaises(LookupError):
            self.table(by_id["dj12-root-urlconf-dynamic"]["change"]["head"])


class Clients(Built):
    """The URL a client line passes (a constant, a two-line constant or a builder) fits the route an http_edge names, and no better one."""

    def client_url(self, line: str, rev: str = "main") -> tuple[str, str]:
        urls = claims.client_urls(claims.text_at(self.repos["acme/portal-web"], rev, "src/api/endpoints.ts") or "")
        m = re.search(r"api(Get|Post)(?:<[^()]*>)?\((\w+)", line)
        return urls[m.group(2)], m.group(1).upper()

    def fits(self, url: str, method: str, table: list[dict]) -> list[dict]:
        found = [(claims.match_kind(url, r["path"]), r) for r in table if r["method"] in (method, "ANY")]
        for kind in ("exact", "literal_in_param"):
            hits = [r for k, r in found if k == kind]
            if hits:
                return hits
        return []

    def test_every_http_edge_fits_its_route_best_and_a_removed_one_fits_the_base_and_nothing_at_the_head(self):
        seen = 0
        for sc in self.scenarios:
            if sc["id"] in ("dj11-urlconf-syntax-error", "dj12-root-urlconf-dynamic", "dj14-settings-flag"):
                continue
            for entry in sc["expected"]["must_find"]:
                if entry["type"] != "http_edge":
                    continue
                with self.subTest(scenario=sc["id"], path=entry["to"]["path"]):
                    text = claims.text_at(self.repos["acme/portal-web"], "main", entry["from"]["path"]) or ""
                    line = next(ln for ln in text.split("\n") if entry["from"]["contains"] in ln)
                    url, method = self.client_url(line)
                    removed = entry["to"].get("removed", False)
                    head = self.table(sc["change"]["head"])
                    base = self.table(sc["change"]["base"])
                    if removed:
                        self.assertEqual([(r["method"], r["path"]) for r in self.fits(url, method, base)], [(entry["to"]["method"], entry["to"]["path"])])
                        self.assertEqual(self.fits(url, method, head), [])
                    else:
                        self.assertEqual([(r["method"], r["path"]) for r in self.fits(url, method, head)], [(entry["to"]["method"], entry["to"]["path"])])
                seen += 1
        self.assertGreaterEqual(seen, 6)

    def test_the_client_urls_are_what_the_endpoints_file_says(self):
        urls = claims.client_urls(claims.text_at(self.repos["acme/portal-web"], "main", "src/api/endpoints.ts") or "")
        self.assertEqual(urls, {
            "INVOICES_URL": "/billing/invoices/", "MONTHLY_REPORT_URL": "/billing/reports/monthly/", "OVERDUE_URL": "/api/v1/invoices/overdue/", "CASES_URL": "/cases/",
            "invoiceUrl": "/billing/invoices/{}/", "voidInvoiceUrl": "/billing/invoices/{}/void/",
        })


class Definitions(Built):
    def test_index_is_defined_in_four_non_test_modules_more_than_distinct_max(self):
        sc = next(s for s in self.scenarios if s["id"] == "dj08-generic-name")
        files = [p for p in claims_dj._py_files(self.portal, sc["change"]["head"]) if "/tests/" not in p]
        found = [p for p in files if re.search(r"^def index\(", claims.text_at(self.portal, sc["change"]["head"], p) or "", re.M)]
        self.assertEqual(found, ["billing/legacy_views.py", "billing/reports.py", "billing/views.py", "cases/views.py"])
        (entry,) = [e for e in sc["expected"]["not_established"] if e["kind"] == "not_distinctive"]
        self.assertEqual(entry["definitions"], len(found))
        self.assertGreater(len(found), dj.load_workspace()["limits"]["distinct_max"])


if __name__ == "__main__":
    unittest.main()
