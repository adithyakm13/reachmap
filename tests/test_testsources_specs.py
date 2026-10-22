"""M15 specs: a Playwright (`page.goto`) or Cypress (`cy.visit`) call with a literal or template address, in a configured repository,
is matched to a screen through the M7 route table; the spec line is the evidence. Comments and strings are not code, and an
address that is not a literal is recorded as partial and never matched."""

from __future__ import annotations

import unittest

from reachmap.adapters import Route
from reachmap.incomplete import kind_spec
from reachmap.testsources import SourceError, specs
from tests import support_testsources as st
from tests.support import area, ev

SPECS = st.E2E / "steps" / "02-specs"
PARAM = None  # a segment that is a parameter (a substitution of a template)


def read(rel: str) -> str:
    return (SPECS / rel).read_text(encoding="utf-8")


def summary(parsed):
    return [(v.line, v.segments, v.title) for v in parsed.visits]


class PlaywrightText(unittest.TestCase):
    def setUp(self):
        self.parsed = specs.visits(read("e2e/billing.spec.ts"), "playwright")

    def test_literals_templates_and_full_addresses(self):
        self.assertEqual(summary(self.parsed), [
            (6, ("billing", "invoices"), "invoice list shows totals"),
            (12, ("billing", "invoices", PARAM), "invoice detail by id"),
            (16, ("billing", "invoices", "INV-7"), "a literal invoice id with a query and a fragment"),  # the query and the fragment are not a path
            (20, ("billing", "settlements"), "settlements by full address"),  # the host is not a path; the options object is not the address
        ])

    def test_a_goto_in_a_line_comment_is_ignored(self):
        self.assertNotIn(3, [v.line for v in self.parsed.visits])

    def test_the_spec_line_is_kept_as_written(self):
        self.assertEqual(self.parsed.visits[0].detail, '    await page.goto("/billing/invoices");')
        self.assertEqual(self.parsed.visits[1].detail, "    await page.goto(`/billing/invoices/${invoiceId}`);")

    def test_a_clean_file_has_no_problems_and_no_unmatched_visits(self):
        self.assertEqual((self.parsed.problems, self.parsed.partial), ((), ()))


class PlaywrightCases(unittest.TestCase):
    def setUp(self):
        self.parsed = specs.visits(read("e2e/cases.spec.ts"), "playwright")

    def test_member_expressions_in_a_substitution_are_parameters(self):
        got = {v.line: v.segments for v in self.parsed.visits}
        self.assertEqual(got[19], ("cases", PARAM, "notes", PARAM))

    def test_literals_and_a_static_screen(self):
        got = {v.line: v.segments for v in self.parsed.visits}
        self.assertEqual((got[7], got[11], got[15]), (("cases",), ("cases", "new"), ("cases", PARAM)))

    def test_a_variable_a_concatenation_and_a_leading_substitution_are_partial_and_not_matched(self):
        self.assertEqual([(p.line, p.reason) for p in self.parsed.partial], [
            (24, "the address is a variable"), (28, "the address is built by concatenation"), (32, "the address starts with a substitution"),
        ])
        self.assertEqual(sorted(v.line for v in self.parsed.visits), [7, 11, 15, 19])

    def test_a_goto_in_a_block_comment_and_in_a_string_is_ignored(self):
        lines = [v.line for v in self.parsed.visits] + [p.line for p in self.parsed.partial]
        self.assertNotIn(36, lines)
        self.assertNotIn(38, lines)

    def test_the_title_is_the_nearest_test_before_the_line(self):
        self.assertEqual({v.line: v.title for v in self.parsed.visits}[11], "a new case is its own screen, not a case called new")


class CypressText(unittest.TestCase):
    def test_cy_visit_with_a_literal_and_a_template_with_a_call_in_the_substitution(self):
        parsed = specs.visits(read("cypress/e2e/billing.cy.ts"), "cypress")
        self.assertEqual(summary(parsed), [
            (3, ("billing", "invoices"), "opens the invoice list"),
            (8, ("billing", "invoices", PARAM), "opens an invoice by id from the environment"),
            (13, ("reports",), "goes through a custom command first"),  # cy.login() is not a visit; the comment is not one
        ])
        self.assertEqual(parsed.partial, ())

    def test_a_concatenation_and_an_options_object_are_partial(self):
        parsed = specs.visits(read("cypress/e2e/misc.cy.js"), "cypress")
        self.assertEqual([(p.line, p.reason) for p in parsed.partial], [(3, "the address is built by concatenation"), (7, "the address is an object")])
        self.assertEqual([(v.line, v.segments) for v in parsed.visits], [(11, ("billing", "settlements")), (12, ("cases", "CASE-2"))])

    def test_each_framework_reads_only_its_own_call(self):
        self.assertEqual(specs.visits(read("cypress/e2e/billing.cy.ts"), "playwright").visits, ())
        self.assertEqual(specs.visits(read("e2e/billing.spec.ts"), "cypress").visits, ())

    def test_a_file_that_does_not_lex_cleanly_says_so(self):
        parsed = specs.visits('test("x", async ({ page }) => {\n  await page.goto("/billing/invoices");\n  const s = "unterminated;\n', "playwright")
        self.assertTrue(parsed.problems)
        self.assertEqual([v.line for v in parsed.visits], [2])  # what was read is kept; the problem says the rest may be missing

    def test_the_other_ways_to_write_the_address(self):
        text = '''
test("a", async ({ page }) => {
  await page.goto('/a/b/');
  await page.goto("billing/invoices");
  await page.goto(`/plain/template`);
  await page.goto(
    "/billing/settlements",
  );
  await this.page.goto("/cases");
  await page.goto("/");
  await page.goto("http://localhost:3000");
  await page.goto("/with%20space/x");
});
'''
        got = [(v.line, v.segments) for v in specs.visits(text, "playwright").visits]
        self.assertEqual(got, [(3, ("a", "b")), (4, ("billing", "invoices")), (5, ("plain", "template")), (7, ("billing", "settlements")), (9, ("cases",)), (10, ()), (11, ()), (12, ("with%20space", "x"))])


def route(path, kind="screen", partial=False):
    return Route("acme/e2e", kind, "" if kind == "screen" else "GET", path, "src/routes.tsx", "Page", "src/Page.tsx", "m",
                 (ev(repo="acme/e2e"),), partial)


ROUTES = [route(p) for p in ("/cases", "/cases/new", "/cases/:caseId", "/cases/:caseId/notes/:noteId", "/billing/invoices", "/billing/invoices/:invoiceId", "/billing/settlements", "/files/*")]


class Matching(unittest.TestCase):
    def paths(self, segs, routes=ROUTES):
        return [r.path for r in specs.match_routes(segs, routes)]

    def test_a_literal_matches_its_static_route(self):
        self.assertEqual(self.paths(("cases",)), ["/cases"])
        self.assertEqual(self.paths(("billing", "settlements")), ["/billing/settlements"])

    def test_a_static_route_beats_a_parameter_route(self):
        self.assertEqual(self.paths(("cases", "new")), ["/cases/new"])

    def test_a_literal_in_a_parameter_position_matches_the_parameter_route(self):
        self.assertEqual(self.paths(("cases", "CASE-2")), ["/cases/:caseId"])
        self.assertEqual(self.paths(("billing", "invoices", "INV-7")), ["/billing/invoices/:invoiceId"])

    def test_a_spec_parameter_matches_a_route_parameter(self):
        self.assertEqual(self.paths(("cases", PARAM)), ["/cases/:caseId"])  # not /cases/new: a parameter is not the word new
        self.assertEqual(self.paths(("cases", PARAM, "notes", PARAM)), ["/cases/:caseId/notes/:noteId"])

    def test_a_spec_parameter_never_matches_a_static_segment(self):
        self.assertEqual(self.paths(("billing", PARAM)), [])

    def test_the_number_of_segments_has_to_agree(self):
        self.assertEqual(self.paths(("cases", "CASE-1", "notes")), [])
        self.assertEqual(self.paths(("billing",)), [])
        self.assertEqual(self.paths(()), [])

    def test_a_splat_route_takes_whatever_is_below_it(self):
        self.assertEqual(self.paths(("files", "a", "b")), ["/files/*"])

    def test_literals_are_compared_without_regard_to_case_and_encoding(self):
        self.assertEqual(self.paths(("Billing", "Invoices")), ["/billing/invoices"])

    def test_an_api_route_and_a_route_with_an_unknown_part_are_never_matched(self):
        rs = [route("/cases", kind="api_route"), route("/cases/<unknown:BASE>/x", partial=True), route("/cases")]
        self.assertEqual([r.kind for r in specs.match_routes(("cases",), rs)], ["screen"])
        self.assertEqual(specs.match_routes(("cases", "a", "x"), [rs[1]]), [])

    def test_two_routes_equally_specific_are_both_kept(self):
        rs = [route("/x/:a"), route("/x/:b")]
        self.assertEqual(self.paths(("x", "1"), rs), ["/x/:a", "/x/:b"])


class Globs(unittest.TestCase):
    def test_the_defaults_per_framework(self):
        pw = specs.spec_matcher("playwright", None)
        self.assertTrue(all(pw(p) for p in ("e2e/billing.spec.ts", "e2e/deep/x.spec.js", "tests/a.spec.tsx", "e2e/a.test.ts")))
        self.assertFalse(any(pw(p) for p in ("src/lib/goto.test.ts", "cypress/e2e/billing.cy.ts", "e2e/helper.ts", "src/a.spec.ts")))
        cy = specs.spec_matcher("cypress", None)
        self.assertTrue(all(cy(p) for p in ("cypress/e2e/billing.cy.ts", "cypress/e2e/misc.cy.js", "cypress/integration/old.spec.js")))
        self.assertFalse(any(cy(p) for p in ("e2e/billing.spec.ts", "cypress/support/commands.ts", "cypress/fixtures/x.json")))

    def test_configured_globs_replace_the_defaults(self):
        m = specs.spec_matcher("playwright", ["acceptance/**/*.ts"])
        self.assertTrue(m("acceptance/a/b.ts"))
        self.assertFalse(m("e2e/billing.spec.ts"))


if __name__ == "__main__":
    unittest.main()
