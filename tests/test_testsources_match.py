"""M15: which area a case is suggested for. The area has to match a configured area (route prefix or module), the case has to be
in the configured suites (descendants included) and share at least two distinctive words with the area or the finding."""

from __future__ import annotations

import unittest

from reachmap.model import Area, MappedTest, Suggestion
from reachmap.testsources import match, qase, words
from reachmap.testsources.base import Case, Corpus
from tests import support_testsources as st
from tests.support import area, ev, finding

CFG = st.BILLING  # prefixes /billing, module billing, suites [10]


def billing(label="/billing/invoices", **kw) -> Area:
    return area(label=label, module=kw.pop("module", "billing"), **kw)


def corpus(*cases, parents=None):
    return Corpus(tuple(Case(f"C{i}", title, group) for i, (title, group) in enumerate(cases, 1)), parents or {10: None, 11: 10, 12: 11, 13: 10, 20: None})


def recorded_corpus() -> Corpus:
    src = qase.Qase(st.qase_table(), env=st.QASE_ENV, transport=st.Fake(st.QASE_ROUTES))
    return src.fetch()


def suggest(corp, areas, cfgs=(CFG,), findings=()):
    return match.suggest("qase:ACME", corp, [dict(c) for c in cfgs], match.infos(areas, findings))


class AreaMatchesConfiguredArea(unittest.TestCase):
    def matches(self, cfg, a):
        return match.area_matches(cfg, a)

    def test_a_route_prefix_matches_on_a_segment_boundary(self):
        cfg = {"name": "B", "route_prefixes": ["/billing"]}
        for label in ("/billing", "/billing/invoices", "/billing/invoices/:invoiceId"):
            self.assertTrue(self.matches(cfg, billing(label, module="x")), label)
        for label in ("/billingx", "/cases/billing", "/", "/bill"):
            self.assertFalse(self.matches(cfg, billing(label, module="x")), label)

    def test_a_prefix_with_a_trailing_slash_and_a_root_prefix(self):
        self.assertTrue(self.matches({"name": "B", "route_prefixes": ["/billing/"]}, billing("/billing/invoices", module="x")))
        self.assertTrue(self.matches({"name": "B", "route_prefixes": ["/"]}, billing("/anything", module="x")))

    def test_an_api_label_is_matched_on_its_path_without_the_method(self):
        a = area(label="GET /api/billing/invoices", kind="api_route", module="billing")
        self.assertTrue(self.matches({"name": "B", "route_prefixes": ["/api/billing"]}, a))
        self.assertFalse(self.matches({"name": "B", "route_prefixes": ["/billing"]}, area(label="GET /api/billing", kind="api_route", module="")))

    def test_the_other_paths_a_screen_is_mounted_at_count(self):
        a = billing("/cases/:caseId/invoices", module="cases", also_mounted=["/billing/invoices/:invoiceId"])
        self.assertTrue(self.matches({"name": "B", "route_prefixes": ["/billing"]}, a))

    def test_a_module_matches_ignoring_case(self):
        self.assertTrue(self.matches({"name": "B", "modules": ["Billing"]}, billing("/x", module="billing")))
        self.assertFalse(self.matches({"name": "B", "modules": ["billing"]}, billing("/x", module="cases")))
        self.assertFalse(self.matches({"name": "B", "modules": ["billing"]}, billing("/x", module="")))

    def test_an_area_with_neither_prefixes_nor_modules_matches_nothing(self):
        self.assertFalse(self.matches({"name": "B", "suites": [10]}, billing()))

    def test_either_is_enough(self):
        cfg = {"name": "B", "route_prefixes": ["/nope"], "modules": ["billing"]}
        self.assertTrue(self.matches(cfg, billing("/other")))


class Suggesting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corp = recorded_corpus()

    def test_the_three_cases_that_share_billing_and_invoice_are_suggested_with_the_words(self):
        a = billing()
        got = suggest(self.corp, [a])
        self.assertEqual([(s.case_id, s.matched_words) for s in got], [("ACME-1", ("billing", "invoice")), ("ACME-2", ("billing", "invoice")), ("ACME-3", ("billing", "invoice"))])
        s = got[0]
        self.assertEqual((s.source, s.title, s.area_id, s.evidence), ("qase:ACME", "Invoice list shows billing totals", a.id, ()))
        self.assertIsInstance(s, Suggestion)
        self.assertNotIsInstance(s, MappedTest)

    def test_one_shared_word_is_not_enough(self):
        got = suggest(self.corp, [billing()])
        self.assertNotIn("ACME-4", [s.case_id for s in got])  # "Void a sent credit" shares nothing; "settlement" cases share billing's module only
        self.assertNotIn("ACME-5", [s.case_id for s in got])

    def test_a_case_in_a_suite_below_the_configured_one_counts_and_one_elsewhere_does_not(self):
        for suites, expected in (([10], {"ACME-1", "ACME-2", "ACME-3"}), ([11], {"ACME-1", "ACME-2", "ACME-3"}), ([12], {"ACME-3"}), ([13], set()), ([20], set())):
            with self.subTest(suites=suites):
                got = suggest(self.corp, [billing()], [dict(CFG, suites=suites)])
                self.assertEqual({s.case_id for s in got}, expected)

    def test_with_no_suites_every_case_is_in_scope(self):
        cfg = {"name": "B", "route_prefixes": ["/billing"]}
        self.assertEqual({s.case_id for s in suggest(self.corp, [billing()], [cfg])}, {"ACME-1", "ACME-2", "ACME-3"})

    def test_a_case_with_no_suite_is_in_scope_only_when_no_suites_are_configured(self):
        c = corpus(("Invoice billing statement", None), ("Invoice billing letters", 11), *[(f"Filler {i}", 20) for i in range(10)])
        self.assertEqual([s.case_id for s in suggest(c, [billing()])], ["C2"])
        self.assertEqual([s.case_id for s in suggest(c, [billing()], [{"name": "B", "route_prefixes": ["/billing"]}])], ["C1", "C2"])

    def test_an_area_that_matches_no_configured_area_gets_nothing(self):
        self.assertEqual(suggest(self.corp, [area(label="/cases", module="cases")]), [])

    def test_a_candidate_found_by_name_gets_nothing(self):
        cand = area(label="/billing/invoices", module="billing", priority="candidate", by_name=True, chains=[])
        self.assertEqual(suggest(self.corp, [cand]), [])

    def test_a_word_in_more_than_ten_percent_of_the_cases_does_not_count(self):
        # amount is in five of the thirty titles (and the floor of three cases): common, so "Amount ..." cases need two other words
        a = billing("/billing/amounts", module="billing")
        f = finding(identity=["direct", a.repo, a.kind, a.label], evidence=[ev(claim="def:formatAmount")])
        c = corpus(("Invoice amount rounds", 11), ("Billing amount shows", 11), *[("Amount of line %d" % i, 11) for i in range(5)])
        self.assertEqual(suggest(c, [a], findings=[f]), [])  # amount is common (7 of 7 cases): billing + amount is one distinctive word
        c2 = corpus(("Invoice amount rounds", 11), ("Billing amount shows", 11), ("Billing invoice amount", 11), *[("Other %d" % i, 11) for i in range(30)])
        got = suggest(c2, [a], findings=[f])  # amount is in three of 33 cases: distinctive, so billing + amount are two
        self.assertEqual([s.case_id for s in got], ["C2", "C3"])

    def test_a_symbol_the_change_touches_adds_its_words_to_the_area(self):
        a = billing("/billing/payments", module="billing")
        f = finding(identity=["direct", a.repo, a.kind, a.label], evidence=[ev(claim="def:applyRefundRounding")])
        c = corpus(("Refund rounding on payment", 11), *[("Filler %d" % i, 20) for i in range(12)])
        self.assertEqual([s.case_id for s in suggest(c, [a], findings=[f])], ["C1"])
        self.assertEqual(suggest(c, [a]), [])  # without the symbol, refund and rounding are not words of the area; only payment is

    def test_a_finding_of_another_area_adds_nothing(self):
        a = billing("/billing/payments", module="billing")
        other = finding(identity=["direct", a.repo, a.kind, "/cases"], evidence=[ev(claim="def:applyRefundRounding")])
        c = corpus(("Refund rounding on payment", 11), *[("Filler %d" % i, 20) for i in range(12)])
        self.assertEqual(suggest(c, [a], findings=[other]), [])

    def test_a_case_suggested_for_two_areas_is_two_suggestions_and_two_configured_areas_do_not_double_it(self):
        a1, a2 = billing("/billing/invoices"), billing("/billing/invoices/:invoiceId")
        cfgs = (CFG, {"name": "Everything", "route_prefixes": ["/"], "suites": [10]})
        got = suggest(self.corp, [a1, a2], cfgs)
        self.assertEqual([(s.case_id, s.area_id) for s in got], [(c, a.id) for a in (a1, a2) for c in ("ACME-1", "ACME-2", "ACME-3")])

    def test_the_order_is_by_area_then_case(self):
        a1, a2 = billing("/billing/invoices"), billing("/billing/invoices/:invoiceId")
        ids = [(s.area_id, s.case_id) for s in suggest(self.corp, [a2, a1])]
        self.assertEqual(ids, sorted(ids, key=lambda x: ([a2.id, a1.id].index(x[0]), int(x[1].split("-")[1]))))

    def test_a_title_is_cleaned_of_control_and_bidirectional_characters(self):
        from reachmap.testsources.base import clean_title

        self.assertEqual(clean_title("Invoice\u202e billing\x00 totals\nsecond line"), "Invoice billing totals second line")
        self.assertEqual(clean_title("x" * 400), "x" * 297 + "...")

    def test_a_title_made_only_of_stop_words_matches_nothing(self):
        self.assertEqual(suggest(corpus(("Verify that the page should open", 11)), [billing()]), [])

    def test_no_cases_no_suggestions(self):
        self.assertEqual(suggest(corpus(), [billing()]), [])


def padded(*cases):
    """The cases and enough filler to be a corpus of 30, so that the cap is the share of the cases and not the floor of a short list."""
    return corpus(*cases, *[(f"Filler {i}", 20) for i in range(30 - len(cases))])


class WordsAreShownAsWritten(unittest.TestCase):
    def test_a_matched_word_is_shown_as_the_case_wrote_it_in_the_order_of_the_stems(self):
        got = suggest(padded(("Invoices for billing", 11)), [billing()])
        self.assertEqual([s.matched_words for s in got], [("billing", "invoices")])

    def test_an_inflected_form_matches_and_is_shown_as_written(self):
        a = billing("/billing/settlements", module="billing")
        got = suggest(padded(("Settled settlements are shown correctly", 11), ("Billing settlement created", 11)), [a])
        self.assertEqual([(s.case_id, s.matched_words) for s in got], [("C2", ("billing", "settlement"))])  # created is generic; the first title shares settlement only


class OneWordFromTheRoute(unittest.TestCase):
    def test_the_module_word_and_a_symbol_are_not_enough(self):
        a = billing("/billing/payments", module="billing")
        f = finding(identity=["direct", a.repo, a.kind, a.label], evidence=[ev(claim="def:applyRefundRounding")])
        self.assertEqual(suggest(padded(("Billing refund rounding", 11)), [a], findings=[f]), [])  # billing restates the area, refund and rounding are the symbol
        got = suggest(padded(("Payments refund rounding", 11)), [a], findings=[f])
        self.assertEqual([s.matched_words for s in got], [("payments", "refund", "rounding")])

    def test_two_words_of_the_route_are_enough_and_the_word_of_the_prefix_may_be_one_of_the_two(self):
        a = billing("/billing/invoices", module="billing")
        self.assertEqual(len(suggest(padded(("Billing invoices list", 11)), [a])), 1)

    def test_a_route_that_holds_only_the_word_of_the_prefix_goes_by_that_word(self):
        a = billing("/billing", module="billing")
        f = finding(identity=["direct", a.repo, a.kind, a.label], evidence=[ev(claim="def:createInvoiceDraft")])
        self.assertEqual(len(suggest(padded(("Billing invoice draft", 11)), [a], findings=[f])), 1)
        self.assertEqual(suggest(padded(("Invoice draft", 11)), [a], findings=[f]), [])  # no word of the route

    def test_a_configured_area_with_a_module_and_another_prefix_restates_both(self):
        a = billing("/billing/ledger/postings", module="ledger")
        cfg = {"name": "L", "route_prefixes": ["/billing/ledger"], "modules": ["ledger"], "suites": [10]}
        self.assertEqual(suggest(padded(("Ledger billing", 11)), [a], [cfg]), [])  # both words restate the match
        self.assertEqual(len(suggest(padded(("Ledger postings", 11)), [a], [cfg])), 1)


class Infos(unittest.TestCase):
    def test_only_import_backed_areas_are_considered_and_their_words_are_listed(self):
        a = billing("/billing/invoices/:invoiceId", also_mounted=["/cases/:caseId/invoices/:invoiceId"])
        f = finding(identity=["direct", a.repo, a.kind, a.label], evidence=[ev(claim="def:formatAmount"), ev(claim="import:src/a.ts:*")])
        cand = area(label="/x", priority="candidate", by_name=True, chains=[])
        got = match.infos([a, cand], [f])
        self.assertEqual([i.area for i in got], [a])
        self.assertEqual(got[0].words, {words.stem(w) for w in ("billing", "invoice", "amount")})  # format is a generic word
        self.assertEqual(got[0].symbols, ("formatAmount",))


if __name__ == "__main__":
    unittest.main()
