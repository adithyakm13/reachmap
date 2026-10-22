"""M15 review: a spec source never says "none suggested" when nothing was established. A spec glob that selects no file, a route table
with no screen, no screen of the run in the screens repository, a source whose entry points are not read: not configured, with the
reason. Spec files whose tests visit nothing the reader can see: the list is incomplete. A visit that matches no screen route is
counted. Addresses: a substitution in place of the host is not a path, `.` and `..` are resolved."""

from __future__ import annotations

import contextlib
import unittest
from types import SimpleNamespace as NS

from reachmap import testsources
from reachmap.adapters import Route
from reachmap.model import Evidence
from reachmap.testsources import Context, match, specs
from tests import support_testsources as st
from tests.support import area

EV = (Evidence("acme/web", "src/routes.tsx", 1, "a" * 40, "x"),)


def route(path, kind="screen"):
    return Route("acme/web", kind, "" if kind == "screen" else "GET", path, "src/routes.tsx", "H", "src/H.tsx", "m", EV)


class FakeGit:
    def __init__(self, files):
        self.files = files

    @contextlib.contextmanager
    def quiet(self):
        yield []

    def checkout(self, remote, sha):
        return NS(files=list(self.files))

    def files_at(self, co, paths):
        return {p: NS(is_found=True, text=self.files[p], detail="") for p in paths}


class Entry:
    problems: list = []

    def __init__(self, routes):
        self._routes = routes

    def routes(self, repo, sha):
        return self._routes


def run_with(files, routes, areas, kind="playwright", entry="given", **options):
    git = FakeGit(files)
    adapter = Entry(routes) if entry == "given" else None
    run = NS(git=git, primary=lambda n: NS(sha="b" * 40), entry=lambda n: adapter, remote=lambda n: "r")
    ctx = Context(NS(), git=git, run=run)
    src = specs.Specs(st.table(kind, repo="acme/web", **options), kind=kind)
    return src.suggest(match.infos(areas, []), ctx)


SCREEN = area(label="/cases", kind="screen", repo="acme/web", module="cases")
ROUTES = [route("/cases"), route("/cases/:caseId")]
SPEC = 'test("opens the cases", async ({ page }) => {\n  await page.goto("/cases");\n});\n'


class Established(unittest.TestCase):
    def test_a_visit_is_found(self):
        found = run_with({"e2e/a.spec.ts": SPEC}, ROUTES, [SCREEN])
        self.assertEqual([s.case_id for s in found.suggestions], ["e2e/a.spec.ts:2"])
        self.assertEqual((found.unconfigured, found.cut), ("", ""))


class NothingToLookAt(unittest.TestCase):
    def test_no_file_matches_the_globs(self):
        found = run_with({"src/a.ts": SPEC}, ROUTES, [SCREEN])
        self.assertEqual(found.suggestions, ())
        self.assertIn("no file of acme/web matches", found.unconfigured)

    def test_an_empty_route_table_and_one_with_no_screen(self):
        for routes in ([], [route("/cases", kind="api_route")]):
            with self.subTest(routes=len(routes)):
                found = run_with({"e2e/a.spec.ts": SPEC}, routes, [SCREEN])
                self.assertIn("no screen route", found.unconfigured)

    def test_no_screen_of_the_run_is_in_the_screens_repository(self):
        other = area(label="/cases", kind="screen", repo="acme/other", module="cases")
        api = area(label="GET /cases", kind="api_route", repo="acme/web", module="cases")
        found = run_with({"e2e/a.spec.ts": SPEC}, ROUTES, [other, api])
        self.assertIn("none of the areas of this run is a screen of acme/web", found.unconfigured)

    def test_no_area_at_all(self):
        self.assertIn("none of the areas of this run", run_with({"e2e/a.spec.ts": SPEC}, ROUTES, []).unconfigured)

    def test_an_entry_point_kind_that_is_not_read_is_not_configured_not_unavailable(self):
        found = run_with({"e2e/a.spec.ts": SPEC}, ROUTES, [SCREEN], entry=None)
        self.assertIn("route table", found.unconfigured)


class Invisible(unittest.TestCase):
    def test_tests_that_visit_nothing_the_reader_can_see_make_the_list_incomplete(self):
        text = 'test("opens the cases", async ({ page }) => {\n  await openCases(page);\n});\ntest("again", async ({ page }) => { await go(page); });\n'
        found = run_with({"e2e/a.spec.ts": text}, ROUTES, [SCREEN])
        self.assertEqual(found.suggestions, ())
        self.assertIn("2 test or it calls", found.cut)
        self.assertIn("no visit", found.cut)

    def test_some_visits_seen_is_not_invisible(self):
        text = SPEC + 'test("through a helper", async ({ page }) => { await go(page); });\n'
        self.assertEqual(run_with({"e2e/a.spec.ts": text}, ROUTES, [SCREEN]).cut, "")

    def test_a_non_literal_visit_is_a_visit_the_reader_saw(self):
        text = 'test("x", async ({ page }) => { await page.goto(url); });\n'
        found = run_with({"e2e/a.spec.ts": text}, ROUTES, [SCREEN])
        self.assertEqual(found.cut, "")
        self.assertEqual([r.kind for r in found.records], ["spec_url_partial"])

    def test_a_spec_file_with_no_test_calls_at_all_is_not_flagged(self):
        self.assertEqual(run_with({"e2e/helpers.spec.ts": "export const x = 1;\n"}, ROUTES, [SCREEN]).cut, "")


class Unmatched(unittest.TestCase):
    def test_a_literal_visit_that_matches_no_screen_route_is_counted_and_non_blocking(self):
        text = SPEC + 'test("elsewhere", async ({ page }) => {\n  await page.goto("/nowhere");\n  await page.goto("/also/nowhere");\n});\n'
        found = run_with({"e2e/a.spec.ts": text}, ROUTES, [SCREEN])
        (rec,) = [r for r in found.records if r.kind == "spec_visit_unmatched"]
        self.assertEqual((rec.blocks_selection, rec.blocks_suggestions), (False, False))
        self.assertIn("2 visits", rec.detail)
        self.assertIn("e2e/a.spec.ts:5", rec.detail)
        self.assertEqual(rec.subject, "acme/web playwright specs")

    def test_a_visit_to_a_route_that_is_not_an_area_of_the_run_is_not_unmatched(self):
        text = 'test("x", async ({ page }) => {\n  await page.goto("/cases/1");\n});\n'
        self.assertEqual([r.kind for r in run_with({"e2e/a.spec.ts": text}, ROUTES, [SCREEN]).records], [])


class Titles(unittest.TestCase):
    def test_a_title_is_cleaned_like_any_other_content(self):
        text = 'test("opens' + chr(0x202E) + ' the' + chr(0x00) + ' cases", async ({ page }) => {\n  await page.goto("/cases");\n});\n'
        (s,) = run_with({"e2e/a.spec.ts": text}, ROUTES, [SCREEN]).suggestions
        self.assertEqual(s.title, "opens the cases")


class Addresses(unittest.TestCase):
    def parse(self, call):
        return specs.visits(f'test("t", async ({{ page }}) => {{\n  await page.goto({call});\n}});\n', "playwright")

    def test_a_substitution_in_place_of_the_host_is_not_a_path(self):
        p = self.parse("`https://${host}/cases/1`")
        self.assertEqual(p.visits, ())
        self.assertEqual([x.reason for x in p.partial], ["the host is a substitution"])

    def test_dot_segments_are_resolved(self):
        self.assertEqual(self.parse('"./cases/new"').visits[0].segments, ("cases", "new"))
        self.assertEqual(self.parse('"/billing/./invoices"').visits[0].segments, ("billing", "invoices"))
        self.assertEqual(self.parse('"/billing/settlements/../invoices"').visits[0].segments, ("billing", "invoices"))

    def test_the_tests_are_counted(self):
        self.assertEqual(self.parse('"/a"').tests, 1)
        self.assertEqual(specs.visits('it.only("a", () => {});\ntest.skip("b", () => {});\ntest.describe("c", () => {});\n', "playwright").tests, 2)


if __name__ == "__main__":
    unittest.main()
