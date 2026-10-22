"""M15 run_all: the four things a configured source can be (not configured, read, read in part, unavailable), the records that say
so, tokens that are nowhere but the Authorization header, and every request a GET."""

from __future__ import annotations

import json
import unittest

from reachmap import incomplete, redact, testsources
from reachmap.config import Workspace
from reachmap.model import Analysis, MappedTest, Suggestion, coverage_gaps
from reachmap.testsources import run_all
from tests import support_testsources as st
from tests.support import area, finding
from tests.support_testsources import QASE, QASE_ENV, QASE_ROUTES, TESTRAIL_ENV, TESTRAIL_ROUTES, TR, Fake


def workspace(*sources) -> Workspace:
    from reachmap import config

    return config.Workspace("acme", None, config.Limits(), config.FrozenMap({}), (), tuple(sources), (), "test")


def billing_area():
    return area(label="/billing/invoices", module="billing")


def analysis(*areas):
    from tests.support import analysis as make

    return make(areas=list(areas) or [billing_area()])


def go(*sources, env=None, routes=None, areas=None, **kw):
    fake = Fake(routes if routes is not None else {**QASE_ROUTES, **TESTRAIL_ROUTES})
    out = run_all(workspace(*sources), analysis(*(areas or [])), env={**QASE_ENV, **TESTRAIL_ENV} if env is None else env, transport=fake, **kw)
    return out, fake


class States(st.HostedCase):
    def test_a_read_source_gives_suggestions_and_no_record(self):
        out, fake = go(st.qase_table())
        self.assertEqual([s.case_id for s in out.suggestions], ["ACME-1", "ACME-2", "ACME-3"])
        self.assertEqual(out.records, ())
        self.assertEqual([(r.source, r.state) for r in out.reads], [("qase:ACME", "read")])
        self.assertTrue(testsources.all_read(out.reads))

    def test_unpacks_as_suggestions_and_records(self):
        suggestions, records = go(st.qase_table())[0]
        self.assertEqual(len(suggestions), 3)
        self.assertEqual(records, ())

    def test_a_source_read_completely_with_nothing_to_suggest_is_none_and_the_run_may_say_so(self):
        out, _ = go(st.qase_table(), areas=[area(label="/cases", module="cases")])
        self.assertEqual((out.suggestions, out.records), ((), ()))
        self.assertTrue(testsources.all_read(out.reads))

    def test_an_unset_variable_is_source_unconfigured_and_non_blocking_and_makes_no_request(self):
        out, fake = go(st.qase_table(), env={})
        (rec,) = out.records
        self.assertEqual((rec.kind, rec.subject, rec.blocks_selection), ("source_unconfigured", "qase:ACME", False))
        self.assertIn("QASE_API_TOKEN", rec.detail)
        self.assertEqual(fake.requests, [])
        self.assertFalse(testsources.all_read(out.reads))
        self.assertEqual(out.reads[0].state, "unconfigured")

    def test_configured_false_is_source_unconfigured(self):
        table = st.config.TestSource("qase", st.config.FrozenMap({"project": None, "token_env": "QASE_API_TOKEN"}), (), ("QASE_PROJECT",))
        out, fake = go(table)
        self.assertEqual([r.kind for r in out.records], ["source_unconfigured"])
        self.assertIn("QASE_PROJECT", out.records[0].detail)
        self.assertEqual(fake.requests, [])

    def test_every_way_of_not_answering_is_source_unavailable_and_blocking_never_no_suggestions(self):
        failures = {
            "401": "qase/error-401.json", "429": "qase/error-429.json", "500": "qase/error-500.json", "malformed": "qase/malformed.json",
            "timeout": TimeoutError("slow"),
        }
        for name, route in failures.items():
            with self.subTest(name):
                out, _ = go(st.qase_table(), routes={f"{QASE}/project/ACME": route})
                (rec,) = out.records
                self.assertEqual((rec.kind, rec.subject, rec.blocks_selection, rec.blocks_suggestions), ("source_unavailable", "qase:ACME", False, True))
                self.assertEqual(out.suggestions, ())
                self.assertFalse(testsources.all_read(out.reads))
                self.assertEqual(out.reads[0].state, "unavailable")

    def test_a_page_that_fails_midway_is_unavailable_and_none_of_the_first_pages_survive(self):
        routes = {**QASE_ROUTES, f"{QASE}/case/ACME?limit=15&offset=15": "qase/error-500.json"}
        out, _ = go(st.qase_table(), routes=routes)
        self.assertEqual([r.kind for r in out.records], ["source_unavailable"])
        self.assertEqual(out.suggestions, ())

    def test_a_cut_list_gives_its_suggestions_and_a_blocking_suggestions_incomplete(self):
        out, _ = go(st.qase_table(max_pages=1))
        (rec,) = out.records
        self.assertEqual((rec.kind, rec.blocks_selection, rec.blocks_suggestions), ("suggestions_incomplete", False, True))
        self.assertIn("15 of 30", rec.detail)
        self.assertTrue(out.suggestions)  # what was read is listed: the record says it is not the whole list
        self.assertFalse(testsources.all_read(out.reads))
        self.assertEqual(out.reads[0].state, "incomplete")

    def test_a_short_corpus_is_said_in_a_note_and_a_full_one_is_not(self):
        out, _ = go(st.qase_table())
        self.assertEqual(out.notes, ())  # 30 cases: the cap is the share
        out, _ = go(st.qase_table(max_pages=1))
        self.assertTrue([n for n in out.notes if "listed only 15 cases" in n and "qase:ACME" in n], out.notes)

    def test_a_late_suggestion_past_the_cut_is_not_claimed_absent(self):
        # the 500-of-899 trap: a case past the page cap is not in the list, and the record is what says so
        out, _ = go(st.qase_table(max_pages=1, areas=[dict(st.BILLING, suites=[10])]))
        self.assertNotEqual(out.records, ())
        self.assertFalse(testsources.all_read(out.reads))

    def test_a_record_of_a_source_blocks_suggestions_and_never_selection(self):
        out, _ = go(st.qase_table(), routes={f"{QASE}/project/ACME": "qase/error-500.json"})
        self.assertFalse(any(r.blocks_selection for r in out.records))
        self.assertTrue(all(r.blocks_suggestions for r in out.records))

    def test_one_source_failing_does_not_stop_the_others(self):
        out, _ = go(st.qase_table(), st.testrail_table(), routes={**TESTRAIL_ROUTES, f"{QASE}/project/ACME": "qase/error-401.json"})
        self.assertEqual([r.kind for r in out.records], ["source_unavailable"])
        self.assertEqual({s.source for s in out.suggestions}, {"testrail:3"})
        self.assertEqual([(r.source, r.state) for r in out.reads], [("qase:ACME", "unavailable"), ("testrail:3", "read")])
        self.assertFalse(testsources.all_read(out.reads))

    def test_two_sources_read_completely_are_both_listed(self):
        out, _ = go(st.qase_table(), st.testrail_table())
        self.assertEqual({s.source for s in out.suggestions}, {"qase:ACME", "testrail:3"})
        self.assertEqual(out.records, ())
        self.assertTrue(testsources.all_read(out.reads))

    def test_no_test_source_reads_nothing_and_is_not_read(self):
        out, fake = go()
        self.assertEqual((out.suggestions, out.records, out.reads, fake.requests), ((), (), (), []))
        self.assertFalse(testsources.all_read(out.reads))

    def test_an_area_that_names_neither_prefix_nor_module_is_noted(self):
        out, _ = go(st.qase_table(areas=[st.BILLING, {"name": "Orphan", "suites": [20]}]))
        self.assertTrue(any("Orphan" in n for n in out.notes))

    def test_testrail_unconfigured_and_available(self):
        out, _ = go(st.testrail_table(), env={"TESTRAIL_API_KEY": "no-colon-here-0123456"})
        self.assertEqual([r.kind for r in out.records], ["source_unconfigured"])
        out, _ = go(st.testrail_table(), routes={f"{TR}/get_project/3": "testrail/error-401.json"})
        self.assertEqual([r.kind for r in out.records], ["source_unavailable"])


class OnlyAreasToTest(st.HostedCase):
    def test_a_candidate_is_not_suggested_for_and_other_areas_are(self):
        cand = area(label="/billing/invoices", module="billing", priority="candidate", by_name=True, chains=[])
        out, _ = go(st.qase_table(), areas=[cand, area(label="/billing/invoices/:invoiceId", module="billing")])
        self.assertEqual({s.area_id for s in out.suggestions}, {area(label="/billing/invoices/:invoiceId", module="billing").id})


class NeverCoverage(st.HostedCase):
    def test_a_run_where_every_area_is_suggested_still_has_every_gap(self):
        areas = [area(label="/billing/invoices", module="billing"), area(label="/billing/invoices/:invoiceId", module="billing"),
                 area(label="/billing/settlements", module="billing")]
        out, _ = go(st.qase_table(), st.testrail_table(), areas=areas)
        self.assertEqual({s.area_id for s in out.suggestions if s.area_id}, {a.id for a in areas[:2]})  # (settlements share no two words)
        a = Analysis(change={"kind": "compare"}, ref="a" * 40)
        for x in areas:
            a.add_area(x)
        for s in out.suggestions:
            a.add_suggestion(s)
        a.finish("completed")
        self.assertEqual([x.id for x in coverage_gaps(a)], [x.id for x in areas])

    def test_nothing_in_the_package_makes_or_imports_a_mapped_test(self):
        from pathlib import Path

        for path in Path(testsources.__file__).parent.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("MappedTest(", text, path.name)
            self.assertNotIn("import MappedTest", text, path.name)
            self.assertNotIn("mapped_tests", text, path.name)

    def test_every_result_is_a_suggestion_object(self):
        out, _ = go(st.qase_table(), st.testrail_table())
        self.assertTrue(out.suggestions)
        for s in out.suggestions:
            self.assertIsInstance(s, Suggestion)
            self.assertNotIsInstance(s, MappedTest)
            self.assertEqual(s.label, "suggested, not verified coverage")


class Credentials(st.HostedCase):
    def test_no_token_in_any_record_note_suggestion_or_the_json_of_them(self):
        cases = [
            go(st.qase_table(), st.testrail_table()),
            go(st.qase_table(), routes={f"{QASE}/project/ACME": "qase/error-401.json"}),
            go(st.testrail_table(), routes={f"{TR}/get_project/3": "testrail/error-403.json"}),
            go(st.qase_table(max_pages=1)),
            go(st.qase_table(), env={}),
        ]
        for out, fake in cases:
            blob = json.dumps({"s": [s.to_dict() for s in out.suggestions], "r": [r.to_dict() for r in out.records], "n": list(out.notes),
                               "reads": [r.to_dict() for r in out.reads]})
            for secret in st.SECRETS:
                self.assertNotIn(secret, blob)

    def test_a_token_that_turns_up_in_a_failure_message_is_scrubbed_before_it_is_recorded(self):
        class Leaky(Fake):
            def __call__(self, method, url, headers=None):
                self.requests.append((method, url, dict(headers or {})))
                raise OSError(f"connection to host failed while sending {st.TOKEN}")

        out = run_all(workspace(st.qase_table()), analysis(), env=QASE_ENV, transport=Leaky({}))
        (rec,) = out.records
        self.assertEqual(rec.kind, "source_unavailable")
        self.assertNotIn(st.TOKEN, rec.detail)
        self.assertIn(redact.REDACTED, rec.detail)

    def test_a_token_in_a_case_title_is_scrubbed_from_the_suggestion(self):
        page = st.recorded("qase/cases-page1.json")
        page["result"]["entities"][0]["title"] = f"Invoice billing totals {st.TOKEN}"
        routes = {**QASE_ROUTES, f"{QASE}/case/ACME?limit=15&offset=0": st.respond(page)}
        out, _ = go(st.qase_table(), routes=routes)
        self.assertNotIn(st.TOKEN, json.dumps([s.to_dict() for s in out.suggestions]))
        self.assertTrue(any(redact.REDACTED in s.title for s in out.suggestions))

    def test_the_token_is_only_sent_in_the_sources_own_header(self):
        out, fake = go(st.qase_table(), st.testrail_table())
        for _, url, headers in fake.requests:
            own = st.own_header(url)
            self.assertIn(own, headers)
            self.assertEqual([k for k, v in headers.items() if k != own and any(s in v for s in st.SECRETS)], [])
            self.assertFalse(any(s in url for s in st.SECRETS))


class ReadOnly(st.HostedCase):
    def test_every_request_of_every_transport_is_a_get(self):
        out, fake = go(st.qase_table(), st.testrail_table())
        self.assertTrue(fake.requests)
        self.assertEqual({m for m, _, _ in fake.requests}, {"GET"})
        # and the fake refuses anything else outright, so a write could not pass for a read
        for f in st.ALL:
            self.assertEqual({m for m, _, _ in f.requests}, {"GET"})

    def test_the_urls_name_no_write_endpoint(self):
        out, fake = go(st.qase_table(), st.testrail_table())
        for _, url, _ in fake.requests:
            for word in ("add_", "update_", "delete_", "close_"):
                self.assertNotIn(word, url)

    def test_the_package_has_no_write_verb_anywhere(self):
        import re
        from pathlib import Path

        for path in Path(testsources.__file__).parent.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertFalse(re.search(r"""["'](POST|PUT|PATCH|DELETE)["']""", text), path.name)
            self.assertFalse(re.search(r"\b(add|update|delete)_(case|section|run|result|suite|project)\b", text), path.name)


class NoNetwork(unittest.TestCase):
    def test_without_an_injected_transport_the_suites_guard_stops_a_real_request(self):
        import os

        from reachmap.hosts import DefaultTransportForbidden

        self.assertTrue(os.environ.get("REACHMAP_FORBID_DEFAULT_TRANSPORT"))  # tests/__init__.py sets it for the whole suite
        with self.assertRaises(DefaultTransportForbidden):
            run_all(workspace(st.qase_table()), analysis(), env=QASE_ENV)


class Drafts(unittest.TestCase):
    def test_draft_cases_are_a_stub_that_renders_nothing(self):
        self.assertEqual(testsources.drafts(analysis()), ())


class Labels(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(testsources.label_of(st.qase_table()), "qase:ACME")
        self.assertEqual(testsources.label_of(st.testrail_table()), "testrail:3")
        self.assertEqual(testsources.label_of(st.table("playwright", repo="acme/e2e")), "playwright:acme/e2e")
        self.assertEqual(testsources.label_of(st.table("qase")), "qase")


class ReadStates(unittest.TestCase):
    def test_what_became_of_each_source_is_told_from_the_records(self):
        ws = workspace(st.qase_table(), st.testrail_table(), st.table("cypress", repo="acme/e2e"))
        a = Analysis(change={"kind": "compare"}, ref="a" * 40)
        incomplete.record(a, "source_unavailable", "qase:ACME", detail="HTTP 500")
        incomplete.record(a, "suggestions_incomplete", "testrail:3", detail="cut")
        got = [(r.source, r.state) for r in testsources.read_states(ws, a)]
        self.assertEqual(got, [("qase:ACME", "unavailable"), ("testrail:3", "incomplete"), ("cypress:acme/e2e", "read")])


if __name__ == "__main__":
    unittest.main()
