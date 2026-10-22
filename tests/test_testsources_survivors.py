"""M15 review: what the first mutation pass found nothing to catch. A route matches a visit on whole segments and a whole path, never on a
piece of one; a spec file that does not lex, a route table that is not whole, a route whose path could not be evaluated and a spec file that
cannot be read each say so; and the deadline is checked before every request."""

from __future__ import annotations

import unittest

from reachmap.testsources import SourceError, specs
from reachmap.testsources.base import StopRun
from reachmap.testsources.qase import Qase
from tests import support_testsources as st
from tests.support_testsources import QASE_ENV, QASE_ROUTES, Fake
from tests.test_testsources_specs import route
from tests.test_testsources_specs_unseen import ROUTES, SCREEN, SPEC, Entry, FakeGit, run_with


class WholeSegments(unittest.TestCase):
    def paths(self, segs, *routes):
        return [r.path for r in specs.match_routes(segs, [route(p) for p in routes])]

    def test_a_piece_of_a_segment_is_not_the_segment(self):
        self.assertEqual(self.paths(("inv",), "/invoices"), [])
        self.assertEqual(self.paths(("invoices2",), "/invoices"), [])
        self.assertEqual(self.paths(("bill", "invoices"), "/billing/invoices"), [])

    def test_a_longer_or_shorter_path_is_not_the_path(self):
        self.assertEqual(self.paths(("invoices", "new"), "/invoices"), [])
        self.assertEqual(self.paths(("invoices",), "/invoices/new"), [])
        self.assertEqual(self.paths(("a", "invoices"), "/invoices"), [])

    def test_a_literal_is_the_segment_whole_and_case_insensitive(self):
        self.assertEqual(self.paths(("Invoices",), "/invoices"), ["/invoices"])


class WhatASpecSourceSaysItCouldNotDo(unittest.TestCase):
    def test_a_spec_file_that_does_not_lex_makes_the_list_partial(self):
        text = SPEC + 'const broken = "unterminated;\n'
        found = run_with({"e2e/a.spec.ts": text}, ROUTES, [SCREEN])
        self.assertIn("e2e/a.spec.ts did not lex cleanly", found.cut)
        self.assertEqual([s.case_id for s in found.suggestions], ["e2e/a.spec.ts:2"])  # what was read is kept

    def test_a_route_table_that_is_not_whole_makes_the_list_partial(self):
        entry = Entry(ROUTES)
        entry.problems = ["src/modules/cases/routes.tsx did not lex cleanly: the screens it mounts were not established"]
        from types import SimpleNamespace as NS
        from reachmap.testsources import Context, match

        git = FakeGit({"e2e/a.spec.ts": SPEC})
        run = NS(git=git, primary=lambda n: NS(sha="b" * 40), entry=lambda n: entry, remote=lambda n: "r")
        found = specs.Specs(st.table("playwright", repo="acme/web"), kind="playwright").suggest(match.infos([SCREEN], []), Context(NS(), git=git, run=run))
        self.assertIn("the route table of acme/web is not whole", found.cut)
        self.assertIn("did not lex cleanly", found.cut)

    def test_a_route_whose_path_could_not_be_evaluated_makes_the_list_partial(self):
        partial = route("/cases/<unknown:PREFIX>/x")
        from dataclasses import replace

        found = run_with({"e2e/a.spec.ts": SPEC}, [*ROUTES, replace(partial, partial=True)], [SCREEN])
        self.assertIn("1 route(s) of acme/web have a path that could not be evaluated", found.cut)

    def test_a_spec_file_that_cannot_be_read_is_unavailable_not_skipped(self):
        class Unreadable(FakeGit):
            def files_at(self, co, paths):
                from types import SimpleNamespace as NS

                return {p: NS(is_found=False, text="", detail="the file is not valid UTF-8") for p in paths}

        from types import SimpleNamespace as NS
        from reachmap.testsources import Context, match

        git = Unreadable({"e2e/a.spec.ts": SPEC})
        run = NS(git=git, primary=lambda n: NS(sha="b" * 40), entry=lambda n: Entry(ROUTES), remote=lambda n: "r")
        with self.assertRaises(SourceError) as caught:
            specs.Specs(st.table("playwright", repo="acme/web"), kind="playwright").suggest(match.infos([SCREEN], []), Context(NS(), git=git, run=run))
        self.assertIn("1 spec file(s) could not be read", str(caught.exception))
        self.assertIn("e2e/a.spec.ts", str(caught.exception))


class TheDeadlineIsCheckedBeforeEveryRequest(st.HostedCase):
    def test_the_check_runs_once_per_request(self):
        calls = []
        fake = Fake(QASE_ROUTES)
        Qase(st.qase_table(), env=QASE_ENV, transport=fake, check=lambda: calls.append(len(fake.requests))).fetch()
        self.assertEqual(calls, list(range(len(fake.requests))))  # before the first request (0 made), before the second (1 made) and so on
        self.assertEqual(len(fake.requests), 4)

    def test_a_check_that_stops_the_run_stops_it_before_the_next_request(self):
        fake = Fake(QASE_ROUTES)

        def check():
            if len(fake.requests) >= 2:
                raise StopRun("time is up")

        with self.assertRaises(StopRun):
            Qase(st.qase_table(), env=QASE_ENV, transport=fake, check=check).fetch()
        self.assertEqual(len(fake.requests), 2)


if __name__ == "__main__":
    unittest.main()
