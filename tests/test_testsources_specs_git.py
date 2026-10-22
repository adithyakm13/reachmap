"""M15 specs, read from a repository: the route table of the screens (M7) at the pinned commit, the spec files that the globs select,
one suggestion per spec line per screen with that line as pinned evidence, non-literal addresses as partial records."""

from __future__ import annotations

import unittest

from reachmap.config import FrozenMap
from reachmap.model import MappedTest, Suggestion
from reachmap.testsources import Context, SourceError, match, specs
from tests import support_testsources as st
from tests.support import area


def screen(label, **kw):
    return area(label=label, kind="screen", repo="acme/e2e", module=kw.pop("module", "billing"), **kw)


AREAS = {p: screen(p) for p in ("/billing/invoices", "/billing/invoices/:invoiceId", "/billing/settlements", "/cases", "/cases/new", "/cases/:caseId",
                                "/cases/:caseId/notes/:noteId", "/reports")}


def run_source(kind="playwright", sources=None, areas=None, origin=None, **options):
    e = st.e2e()
    table = st.table(kind, repo="acme/e2e", **options)
    ws = e.config()
    ctx = Context(ws, git=e.git(), origin=origin)
    infos = match.infos(areas if areas is not None else list(AREAS.values()), [])
    return specs.Specs(table, kind=kind).suggest(infos, ctx), ctx


def triples(found):
    return [(next(p for p, a in AREAS.items() if a.id == s.area_id), s.case_id) for s in found.suggestions]


class Playwright(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.found, cls.ctx = run_source()

    def test_every_literal_and_template_visit_is_a_suggestion_for_its_screen(self):
        self.assertEqual(sorted(triples(self.found)), sorted([
            ("/billing/invoices", "e2e/billing.spec.ts:6"),
            ("/billing/invoices/:invoiceId", "e2e/billing.spec.ts:12"),
            ("/billing/invoices/:invoiceId", "e2e/billing.spec.ts:16"),
            ("/billing/settlements", "e2e/billing.spec.ts:20"),
            ("/cases", "e2e/cases.spec.ts:7"),
            ("/cases/new", "e2e/cases.spec.ts:11"),
            ("/cases/:caseId", "e2e/cases.spec.ts:15"),
            ("/cases/:caseId/notes/:noteId", "e2e/cases.spec.ts:19"),
        ]))

    def test_a_static_screen_is_not_taken_for_a_parameter_screen_and_the_other_way_round(self):
        by = {c: a for a, c in triples(self.found)}
        self.assertEqual(by["e2e/cases.spec.ts:11"], "/cases/new")
        self.assertEqual(by["e2e/cases.spec.ts:15"], "/cases/:caseId")

    def test_the_comment_the_string_the_cypress_specs_and_the_unit_test_are_not_suggestions(self):
        cases = [s.case_id for s in self.found.suggestions]
        self.assertFalse([c for c in cases if c.startswith(("cypress/", "src/"))], cases)
        self.assertNotIn("/reports", [a for a, _ in triples(self.found)])
        self.assertNotIn("e2e/billing.spec.ts:3", cases)
        self.assertNotIn("e2e/cases.spec.ts:36", cases)

    def test_the_spec_line_is_the_pinned_evidence_and_the_title_is_the_test_name(self):
        s = next(x for x in self.found.suggestions if x.case_id == "e2e/billing.spec.ts:6")
        self.assertEqual(s.title, "invoice list shows totals")
        self.assertEqual(s.source, "playwright:acme/e2e")
        self.assertEqual(s.matched_words, ())
        (e,) = s.evidence
        self.assertEqual((e.repo, e.path, e.line, e.ref, e.detail), ("acme/e2e", "e2e/billing.spec.ts", 6, st.e2e().main, '    await page.goto("/billing/invoices");'))
        self.assertIsInstance(s, Suggestion)
        self.assertNotIsInstance(s, MappedTest)

    def test_every_citation_resolves_at_its_pinned_commit(self):
        from tests.fixtures import build

        for s in self.found.suggestions:
            for e in s.evidence:
                kind, text = build.file_at(st.e2e().path, e.ref, e.path)
                self.assertEqual(kind, "found")
                self.assertEqual(text.splitlines()[e.line - 1], e.detail)

    def test_the_non_literal_visits_are_non_blocking_partial_records_and_the_list_is_not_cut(self):
        recs = list(self.found.records)
        self.assertEqual([r.kind for r in recs], ["spec_url_partial"] * 3)
        self.assertEqual([r.subject for r in recs], ["acme/e2e e2e/cases.spec.ts:24", "acme/e2e e2e/cases.spec.ts:28", "acme/e2e e2e/cases.spec.ts:32"])
        self.assertFalse(any(r.blocks_selection for r in recs))
        self.assertIn("variable", recs[0].detail)
        self.assertEqual(self.found.cut, "")

    def test_an_area_that_is_not_a_screen_of_the_screens_repository_gets_nothing(self):
        other = area(label="/billing/invoices", kind="screen", repo="acme/other", module="billing")
        api = area(label="GET /billing/invoices", kind="api_route", repo="acme/e2e", module="billing")
        found, _ = run_source(areas=[other, api])
        self.assertEqual(found.suggestions, ())
        self.assertIn("none of the areas of this run is a screen", found.unconfigured)

    def test_nothing_is_suggested_for_a_screen_the_change_does_not_reach(self):
        found, _ = run_source(areas=[AREAS["/reports"]])
        self.assertEqual(found.suggestions, ())  # a screen of the run, no spec visits it: read, and nothing is suggested
        self.assertEqual(found.unconfigured, "")

    def test_the_screen_a_page_is_also_mounted_at_is_found_by_either_path(self):
        a = screen("/cases/:caseId", also_mounted=["/billing/invoices"])
        found, _ = run_source(areas=[a])
        self.assertEqual({s.case_id for s in found.suggestions}, {"e2e/cases.spec.ts:15", "e2e/billing.spec.ts:6"})


class Cypress(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.found, _ = run_source("cypress")

    def test_cy_visit_calls_are_suggestions(self):
        self.assertEqual(sorted(triples(self.found)), sorted([
            ("/billing/invoices", "cypress/e2e/billing.cy.ts:3"),
            ("/billing/invoices/:invoiceId", "cypress/e2e/billing.cy.ts:8"),
            ("/reports", "cypress/e2e/billing.cy.ts:13"),
            ("/billing/settlements", "cypress/e2e/misc.cy.js:11"),
            ("/cases/:caseId", "cypress/e2e/misc.cy.js:12"),
        ]))

    def test_the_non_literal_visits_are_recorded(self):
        self.assertEqual([r.subject for r in self.found.records], ["acme/e2e cypress/e2e/misc.cy.js:3", "acme/e2e cypress/e2e/misc.cy.js:7"])

    def test_the_source_name_says_the_framework(self):
        self.assertEqual({s.source for s in self.found.suggestions}, {"cypress:acme/e2e"})


class Pinning(unittest.TestCase):
    def test_the_changed_repository_is_read_at_the_head_of_the_change(self):
        e = st.e2e()
        found, _ = run_source(origin=("acme/e2e", e.topic))
        self.assertTrue(found.suggestions)
        self.assertEqual({x.ref for s in found.suggestions for x in s.evidence}, {e.topic})

    def test_another_repository_is_read_at_the_head_of_its_primary_branch(self):
        e = st.e2e()
        found, _ = run_source(origin=("acme/other", "f" * 40))
        self.assertEqual({x.ref for s in found.suggestions for x in s.evidence}, {e.main})

    def test_a_screens_repository_other_than_the_spec_repository(self):
        found, _ = run_source(screens_repo="acme/e2e")
        self.assertTrue(found.suggestions)


class Reading(unittest.TestCase):
    def test_configured_globs_select_the_files(self):
        found, _ = run_source(globs=["e2e/cases.spec.ts"])
        self.assertEqual({s.case_id.split(":")[0] for s in found.suggestions}, {"e2e/cases.spec.ts"})

    def test_no_spec_file_selected_is_not_none_suggested_it_is_nothing_looked_at(self):
        found, _ = run_source(globs=["nothing/**/*.ts"])
        self.assertEqual((found.suggestions, found.records), ((), ()))
        self.assertIn("no file of acme/e2e matches", found.unconfigured)

    def test_a_repository_whose_primary_branch_cannot_be_read_is_unavailable(self):
        import dataclasses

        e = st.e2e()
        ws = e.config()
        broken = dataclasses.replace(ws.repos["acme/e2e"], primary_branch="no-such-branch", branches=("no-such-branch",))
        ctx = Context(dataclasses.replace(ws, repos=FrozenMap({"acme/e2e": broken})), git=e.git())
        with self.assertRaises(SourceError) as caught:
            specs.Specs(st.table("playwright", repo="acme/e2e"), kind="playwright").suggest(match.infos(list(AREAS.values()), []), ctx)
        self.assertIn("primary branch", str(caught.exception))

    def test_a_repository_with_no_route_table_is_not_configured_for_specs(self):
        e = st.e2e()
        import dataclasses

        ws = e.config()
        plain = dataclasses.replace(ws.repos["acme/e2e"], entry_points=None)
        ctx = Context(dataclasses.replace(ws, repos=FrozenMap({"acme/e2e": plain})), git=e.git())
        found = specs.Specs(st.table("playwright", repo="acme/e2e"), kind="playwright").suggest(match.infos(list(AREAS.values()), []), ctx)
        self.assertIn("route table", found.unconfigured)

    def test_the_label_names_the_framework_and_the_repository(self):
        self.assertEqual(specs.Specs(st.table("cypress", repo="acme/e2e"), kind="cypress").label, "cypress:acme/e2e")

    def test_a_spec_source_is_configured_when_it_names_a_repository(self):
        self.assertIsNone(specs.Specs(st.table("cypress", repo="acme/e2e"), kind="cypress").unconfigured())
        self.assertIn("repo", specs.Specs(st.table("cypress"), kind="cypress").unconfigured())


if __name__ == "__main__":
    unittest.main()
