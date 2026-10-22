"""The distinctive words rule (docs/briefs/M15-test-sources.md, "Matching suggestions to areas"): a case is tied to an area only when
it shares at least two distinctive words with it, one of them from the area's own label or route and not from the word that only
restates which area it is. Words are lowercased tokens of four letters or more, minus a stop list, minus any word that is in more than
10 percent of the fetched cases. Words are compared by their stem and shown as they were written."""

import unittest

from reachmap.testsources import words


class Tokens(unittest.TestCase):
    def test_lowercased_and_four_letters_or_more_as_written(self):
        self.assertEqual(words.tokens("Open the Invoice LIST"), ["invoice"])  # open and list are generic, the and the short ones are too short
        self.assertEqual(words.tokens("Edit tax id"), [])  # three letters or fewer never count
        self.assertEqual(words.tokens("Settlement summary totals"), ["settlement", "summary", "totals"])  # shown as written, not folded

    def test_the_length_is_the_written_length(self):
        self.assertEqual(words.tokens("rules"), ["rules"])
        self.assertEqual(words.tokens("uses"), [])  # generic as well
        self.assertEqual(words.tokens("tax"), [])

    def test_camel_case_snake_case_and_kebab_case_are_split(self):
        self.assertEqual(words.tokens("settlementSummary"), ["settlement", "summary"])
        self.assertEqual(words.tokens("InvoiceDetailPage"), ["invoice"])  # detail and page are generic
        self.assertEqual(words.tokens("audit-log_export"), ["audit", "export"])

    def test_route_parameters_and_punctuation_are_not_words(self):
        self.assertEqual(words.tokens("/billing/invoices/:invoiceId"), ["billing", "invoices", "invoice"])
        self.assertEqual(words.tokens("/api/billing/invoices/{invoice_id}"), ["billing", "invoices", "invoice"])

    def test_non_ascii_letters_are_words(self):
        self.assertEqual(words.tokens("Rechnungsübersicht"), ["rechnungsübersicht"])

    def test_keyed_gives_each_stem_once_with_the_first_form_written(self):
        self.assertEqual(words.keyed("Invoices and the invoice"), {words.stem("invoice"): "invoices"})


class Stems(unittest.TestCase):
    def same(self, *forms):
        stems = {words.stem(f) for f in forms}
        self.assertEqual(len(stems), 1, f"{forms} stem to {stems}")

    def test_plurals_and_inflections_are_one_word(self):
        self.same("invoice", "invoices")
        self.same("category", "categories")
        self.same("create", "created", "creates", "creating")
        self.same("display", "displayed", "displays", "displaying")
        self.same("correct", "correctly")
        self.same("stop", "stopped", "stopping")
        self.same("apply", "applied", "applies")
        self.same("settlement", "settlements")

    def test_words_that_only_look_inflected_are_left_alone(self):
        for w in ("series", "species", "news", "canvas", "alias", "atlas", "bias", "status", "address", "analysis", "basis", "process", "class",
                  "family", "apply", "supply", "reply", "early", "only", "assembly", "daily", "bulletin", "need", "speed", "feed", "indeed",
                  "string", "thing", "during", "nothing", "king", "ring"):
            with self.subTest(w):
                self.assertEqual(words.stem(w), w)

    def test_a_stem_is_the_word_cut_at_its_end_and_nothing_else(self):
        # all but the last letter of a stem is the start of the word (series is not sery, canvas is not canva, alias is not alia)
        for w in ("series", "canvas", "alias", "bonus", "focus", "menus", "access", "chaos", "items", "invoices", "settlements", "displayed",
                  "creating", "correctly", "stopped", "reports", "categories", "applies", "nothing", "something", "building", "listing"):
            with self.subTest(w):
                self.assertTrue(w.startswith(words.stem(w)[:-1]), (w, words.stem(w)))
                self.assertGreaterEqual(len(words.stem(w)), 4, w)

    def test_a_short_word_is_not_cut_to_nothing(self):
        for w in ("used", "uses", "bed", "red", "ring", "bring", "sing"):
            self.assertGreaterEqual(len(words.stem(w)), 3, w)

    def test_a_stem_is_the_same_for_a_word_and_for_what_it_is_compared_with(self):
        self.assertEqual(words.stem(words.stem("invoices")), words.stem("invoices"))


class StopList(unittest.TestCase):
    GENERIC = ("test", "case", "verify", "should", "page", "screen", "click", "with", "that", "create", "update", "delete", "user", "data")
    INFLECTED = ("created", "displayed", "shown", "correctly", "successful", "successfully", "clicked", "updated", "deleted", "loading", "properly")
    CODE = ("handle", "handles", "handled", "change", "changes", "changed", "format", "formatted", "fetch", "fetched", "render", "rendered", "detail", "details",
            "setting", "settings", "default", "defaults", "note", "notes", "report", "reports", "search", "searching", "filter", "filters", "login", "logout")
    UI = ("dialog", "dropdown", "tooltip", "toggle", "checkbox", "sidebar", "popup", "header", "footer", "banner", "column", "icon", "input", "label", "layout",
          "message", "option", "preview", "refresh", "request", "response", "result", "retry", "scroll", "selected", "submit", "success", "visible", "window")

    def test_generic_words_are_dropped(self):
        for group in (self.GENERIC, self.INFLECTED, self.CODE, self.UI):
            for w in group:
                with self.subTest(w):
                    self.assertEqual(words.tokens(w), [])

    def test_the_stop_list_is_lowercase_and_has_no_short_words(self):
        for w in words.STOP_WORDS:
            self.assertEqual(w, w.lower())
            self.assertGreaterEqual(len(w), words.MIN_LENGTH, w)  # a shorter one is dropped by the length rule already

    def test_the_stop_list_is_a_reviewed_list_of_a_few_hundred_words(self):
        self.assertGreaterEqual(len(words.STOP_WORDS), 250)

    def test_a_distinctive_word_is_not_on_it(self):
        for w in ("invoice", "settlement", "billing", "audit", "dashboard", "payment", "refund", "customer", "ledger", "export"):
            self.assertNotIn(w, words.STOP_WORDS)
            self.assertEqual(words.tokens(w), [w])


class FrequencyCap(unittest.TestCase):
    @staticmethod
    def corpus(n: int, common_in: int, word: str = "invoic") -> list[set[str]]:
        """n cases, `word` (a key) in the first `common_in` of them; every case has a word of its own."""
        return [({word} if i < common_in else set()) | {f"own{i:03d}x"} for i in range(n)]

    def test_a_word_in_more_than_ten_percent_of_the_cases_is_not_distinctive(self):
        self.assertEqual(words.common_words(self.corpus(100, 11)), {"invoic"})

    def test_a_word_in_exactly_ten_percent_is_still_distinctive(self):
        self.assertEqual(words.common_words(self.corpus(100, 10)), set())

    def test_the_cap_counts_cases_not_occurrences(self):
        cases = [{"invoice"} for _ in range(5)] + [set() for _ in range(95)]  # a set holds a word once per case
        self.assertEqual(words.common_words(cases), set())

    def test_a_short_list_has_a_floor_of_a_third_of_its_cases_and_never_more_than_three(self):
        # limit = max(0.1 * n, min(3, n / 3)): 8 cases: 2.67, so a word in three of them is common and in two is not
        self.assertEqual(words.common_words(self.corpus(8, 2)), set())
        self.assertEqual(words.common_words(self.corpus(8, 3)), {"invoic"})
        self.assertEqual(words.common_words(self.corpus(30, 3)), set())  # 30 cases: 3
        self.assertEqual(words.common_words(self.corpus(30, 4)), {"invoic"})
        self.assertEqual(words.common_words(self.corpus(3, 1)), set())  # 3 cases: 1, so a word in two of them is common
        self.assertEqual(words.common_words(self.corpus(3, 2)), {"invoic"})
        self.assertEqual(words.common_words(self.corpus(60, 6)), set())
        self.assertEqual(words.common_words(self.corpus(60, 7)), {"invoic"})

    def test_the_words_of_a_single_case_are_all_in_every_case_so_they_are_common(self):
        # limit = max(0.1, min(3, 1/3)) = 0.33 and each of its words is in one case: with one case nothing can be told from anything
        self.assertEqual(words.common_words(self.corpus(1, 1)), {"invoic", "own000x"})

    def test_no_cases_no_common_words(self):
        self.assertEqual(words.common_words([]), set())

    def test_the_constants_are_what_the_notes_say(self):
        self.assertEqual((words.DF_SHARE, words.DF_FLOOR, words.MIN_LENGTH, words.MIN_SHARED, words.SMALL_CORPUS), (0.10, 3, 4, 2, 30))

    def test_a_short_corpus_is_said(self):
        self.assertIn("29 cases", words.small_corpus_note("qase:ACME", 29))
        self.assertIn("qase:ACME", words.small_corpus_note("qase:ACME", 29))
        self.assertIsNone(words.small_corpus_note("qase:ACME", 30))
        self.assertIn("no case", words.small_corpus_note("qase:ACME", 0))


class Shared(unittest.TestCase):
    AREA = {words.stem("billing"), words.stem("invoice")}

    def case(self, text):
        return words.keyed(text)

    def test_two_distinctive_words_are_a_match_and_come_back_as_the_case_wrote_them_sorted_by_stem(self):
        self.assertEqual(words.shared(self.case("Invoices billing totals"), self.AREA, set()), ("billing", "invoices"))

    def test_one_shared_word_is_not_a_match(self):
        self.assertEqual(words.shared(self.case("invoice payment"), self.AREA, set()), ())

    def test_a_common_word_does_not_count_toward_the_two(self):
        self.assertEqual(words.shared(self.case("invoice billing"), self.AREA, {words.stem("billing")}), ())

    def test_a_stop_word_does_not_count_either(self):
        self.assertEqual(words.shared(self.case("invoice should"), self.AREA | {"should"}, set()), ())

    def test_two_forms_of_one_word_are_one(self):
        self.assertEqual(words.shared(self.case("invoice invoices"), self.AREA, set()), ())

    def test_one_of_the_two_has_to_come_from_the_label_or_the_route(self):
        area = {words.stem(w) for w in ("billing", "invoice", "refund", "rounding")}
        anchor = {words.stem("invoice")}  # the label's words, less the word that only restates which area it is
        self.assertEqual(words.shared(self.case("billing refund rounding"), area, set(), anchor), ())  # the symbol and the module word only
        self.assertEqual(words.shared(self.case("invoice refund rounding"), area, set(), anchor), ("invoice", "refund", "rounding"))

    def test_without_an_anchor_the_rule_is_not_applied(self):
        self.assertEqual(words.shared(self.case("billing refund"), {words.stem("billing"), words.stem("refund")}, set()), ("billing", "refund"))


class AreaWords(unittest.TestCase):
    def test_label_module_route_segments_and_symbols_are_the_sources(self):
        got = words.area_words(label="/billing/invoices/:invoiceId", module="billing", also_mounted=("/cases/:caseId/invoices/:invoiceId",),
                               symbols=("formatAmount", "InvoiceDetailPage"))
        self.assertEqual(got, {words.stem(w) for w in ("billing", "invoice", "amount")})  # format and detail are generic words now

    def test_an_api_label_has_the_method_dropped(self):
        self.assertEqual(words.area_words(label="GET /api/billing/settlements/summary", module="billing", also_mounted=(), symbols=()),
                         {words.stem(w) for w in ("billing", "settlement", "summary")})

    def test_a_generic_module_name_is_not_a_word(self):
        self.assertEqual(words.area_words(label="/reports", module="components", also_mounted=(), symbols=()), set())  # report is generic as well

    def test_the_route_words_are_those_of_the_label_and_where_it_is_also_mounted_only(self):
        got = words.route_words(label="/billing/invoices", also_mounted=("/cases/:caseId/settlements",))
        self.assertEqual(got, {words.stem(w) for w in ("billing", "invoice", "settlement")})


if __name__ == "__main__":
    unittest.main()
