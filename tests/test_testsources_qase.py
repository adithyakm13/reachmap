"""M15 Qase: recorded (synthetic) responses for the project, the suite tree, two pages of cases, and every way the service can
fail to answer. Nothing here reaches the network: the transport is a fake that only answers a GET."""

from __future__ import annotations

import unittest

from reachmap.testsources import SourceError, qase
from tests import support_testsources as st
from tests.support_testsources import QASE, QASE_ENV, QASE_ROUTES, TOKEN, Fake, qase_table


def source(fake=None, table=None, env=None):
    fake = fake if fake is not None else Fake(QASE_ROUTES)
    return qase.Qase(table or qase_table(), env=QASE_ENV if env is None else env, transport=fake), fake


class Reading(st.HostedCase):
    def test_every_page_is_read_and_the_requests_are_these_and_only_these(self):
        src, fake = source()
        corpus = src.fetch()
        self.assertEqual(fake.urls, [
            f"{QASE}/project/ACME", f"{QASE}/suite/ACME?limit=15&offset=0", f"{QASE}/case/ACME?limit=15&offset=0", f"{QASE}/case/ACME?limit=15&offset=15",
        ])
        self.assertEqual(len(corpus.cases), 30)
        self.assertTrue(corpus.complete)
        self.assertEqual(corpus.cases[0].id, "ACME-1")
        self.assertEqual(corpus.cases[0].title, "Invoice list shows billing totals")
        self.assertEqual(corpus.cases[0].group, 11)
        self.assertEqual(corpus.cases[29].id, "ACME-30")
        self.assertIsNone(corpus.cases[29].group)  # a case with no suite

    def test_the_suite_tree_gives_descendants(self):
        corpus = source()[0].fetch()
        self.assertEqual(corpus.parents, {10: None, 11: 10, 12: 11, 13: 10, 20: None, 21: 20})
        self.assertEqual(corpus.within([10]), {10, 11, 12, 13})
        self.assertEqual(corpus.within([11]), {11, 12})
        self.assertEqual(corpus.within([21, 13]), {21, 13})
        self.assertEqual(corpus.within([999]), {999})  # a suite the project does not have contains only itself

    def test_the_credential_is_sent_in_the_token_header_and_only_there(self):
        src, fake = source()
        src.fetch()
        for _, _, headers in fake.requests:
            self.assertEqual(headers["Token"], TOKEN)
            self.assertEqual([k for k, v in headers.items() if TOKEN in v], ["Token"])
        self.assertTrue(all(url.startswith("https://") for url in fake.urls))

    def test_an_empty_project_is_read_and_has_no_cases(self):
        routes = dict(QASE_ROUTES)
        routes[f"{QASE}/case/ACME?limit=15&offset=0"] = st.respond({"status": True, "result": {"total": 0, "filtered": 0, "count": 0, "entities": []}})
        corpus = source(Fake(routes))[0].fetch()
        self.assertEqual((corpus.cases, corpus.complete), ((), True))

    def test_a_list_that_says_it_was_filtered_when_no_filter_was_sent_is_unavailable(self):
        page = st.recorded("qase/cases-page1.json")
        page["result"].update(total=999, filtered=15)  # the project has more cases than the service says are selected: by what?
        routes = {**QASE_ROUTES, f"{QASE}/case/ACME?limit=15&offset=0": st.respond(page)}
        with self.assertRaises(SourceError) as caught:
            source(Fake(routes))[0].fetch()
        self.assertIn("filtered", str(caught.exception))

    def test_the_total_is_what_has_to_be_read_and_filtered_is_only_a_second_opinion(self):
        page = st.recorded("qase/cases-page1.json")
        page["result"].pop("filtered")
        routes = {**QASE_ROUTES, f"{QASE}/case/ACME?limit=15&offset=0": st.respond(page)}
        self.assertEqual(len(source(Fake(routes))[0].fetch().cases), 30)
        page["result"].update(total=None, filtered=30)
        page2 = {**QASE_ROUTES, f"{QASE}/case/ACME?limit=15&offset=0": st.respond(page)}
        self.assertEqual(len(source(Fake(page2))[0].fetch().cases), 30)  # a service that gives only the filtered figure

    def test_the_default_page_size_is_the_largest_the_service_allows(self):
        routes = {
            f"{QASE}/project/ACME": "qase/project.json",
            f"{QASE}/suite/ACME?limit=100&offset=0": "qase/suites.json",
            f"{QASE}/case/ACME?limit=100&offset=0": st.respond({"status": True, "result": {"total": 0, "filtered": 0, "count": 0, "entities": []}}),
        }
        t = qase_table()
        t = st.config.TestSource(t.kind, st.config.FrozenMap({k: v for k, v in t.options.items() if k != "page_size"}), t.areas)
        corpus = source(Fake(routes), t)[0].fetch()
        self.assertEqual(corpus.cases, ())

    def test_a_project_code_is_one_url_segment(self):
        t = qase_table(project="A/B C")
        fake = Fake({f"{QASE}/project/A%2FB%20C": "qase/project.json", f"{QASE}/suite/A%2FB%20C?limit=15&offset=0": "qase/suites.json",
                     f"{QASE}/case/A%2FB%20C?limit=15&offset=0": st.respond({"status": True, "result": {"total": 0, "filtered": 0, "count": 0, "entities": []}})})
        source(fake, t)[0].fetch()
        self.assertEqual(len(fake.urls), 3)


class Pagination(st.HostedCase):
    def test_a_page_cap_is_a_cut_and_the_corpus_says_it_is_incomplete(self):
        src, fake = source(table=qase_table(max_pages=1))
        corpus = src.fetch()
        self.assertEqual(len(corpus.cases), 15)
        self.assertFalse(corpus.complete)
        self.assertIn("15 of 30", corpus.cut)  # the 500-of-899 trap: what was read and what there is, said
        self.assertEqual(len([u for u in fake.urls if "/case/" in u]), 1)

    def test_the_cap_counts_pages_of_each_list(self):
        src, fake = source(table=qase_table(max_pages=2))
        self.assertTrue(src.fetch().complete)  # two pages of cases is the whole list

    def test_a_list_cut_in_the_suites_is_a_cut_too(self):
        page = st.recorded("qase/suites.json")
        page["result"].update(total=40, filtered=40)  # more suites than the page carries
        routes = {**QASE_ROUTES, f"{QASE}/suite/ACME?limit=15&offset=0": st.respond(page), f"{QASE}/suite/ACME?limit=15&offset=6": st.respond(page)}
        corpus = source(Fake(routes), qase_table(max_pages=1))[0].fetch()
        self.assertFalse(corpus.complete)
        self.assertIn("suites", corpus.cut)

    def test_a_page_that_fails_midway_is_unavailable_never_the_first_half(self):
        routes = {**QASE_ROUTES, f"{QASE}/case/ACME?limit=15&offset=15": "qase/error-500.json"}
        with self.assertRaises(SourceError) as caught:
            source(Fake(routes))[0].fetch()
        self.assertIn("HTTP 500", str(caught.exception))

    def test_a_page_that_is_empty_before_the_total_is_reached_is_unavailable(self):
        routes = {**QASE_ROUTES, f"{QASE}/case/ACME?limit=15&offset=15": st.respond({"status": True, "result": {"total": 30, "filtered": 30, "count": 0, "entities": []}})}
        with self.assertRaises(SourceError) as caught:
            source(Fake(routes))[0].fetch()
        self.assertIn("empty", str(caught.exception))


class Failing(st.HostedCase):
    def fails(self, routes, *, table=None, env=None):
        with self.assertRaises(SourceError) as caught:
            source(Fake(routes), table, env)[0].fetch()
        message = str(caught.exception)
        for secret in st.SECRETS:
            self.assertNotIn(secret, message)
        return message

    def test_401_is_unavailable_and_names_the_variable_not_the_token(self):
        message = self.fails({f"{QASE}/project/ACME": "qase/error-401.json"})
        self.assertIn("HTTP 401", message)
        self.assertIn("QASE_API_TOKEN", message)

    def test_429_is_unavailable_and_says_when_to_try_again(self):
        message = self.fails({f"{QASE}/project/ACME": "qase/error-429.json"})
        self.assertIn("rate limited", message)
        self.assertIn("30", message)

    def test_500_is_unavailable(self):
        self.assertIn("HTTP 500", self.fails({f"{QASE}/project/ACME": "qase/error-500.json"}))

    def test_a_page_that_is_not_json_is_unavailable(self):
        self.assertIn("not valid JSON", self.fails({f"{QASE}/project/ACME": "qase/malformed.json"}))

    def test_a_timeout_is_unavailable(self):
        self.assertIn("in time", self.fails({f"{QASE}/project/ACME": TimeoutError("slow")}))

    def test_a_transport_that_cannot_connect_is_unavailable(self):
        from reachmap.hosts import TransportError

        self.assertIn("could not be made", self.fails({f"{QASE}/project/ACME": TransportError("no route to host")}))

    def test_an_unknown_project_is_unavailable_never_no_suggestions(self):
        self.assertIn("404", self.fails({f"{QASE}/project/ACME": "qase/error-404.json"}))

    def test_a_status_false_answer_is_unavailable(self):
        self.assertIn("error", self.fails({f"{QASE}/project/ACME": "qase/status-false.json"}))

    def test_an_answer_of_the_wrong_shape_is_unavailable(self):
        bad = {"result": {"total": 1, "entities": [{"id": "x", "title": 3}]}, "status": True}
        for body in (bad, {"status": True, "result": []}, {"status": True, "result": {"total": "many", "entities": []}}, {"status": True}, [1, 2]):
            with self.subTest(body=body):
                routes = {**QASE_ROUTES, f"{QASE}/case/ACME?limit=15&offset=0": st.respond(body)}
                self.assertIn("unexpected", self.fails(routes).lower())

    def test_a_redirect_is_not_followed(self):
        message = self.fails({f"{QASE}/project/ACME": st.respond({}, 302, {"Location": "https://elsewhere.example.test/x"})})
        self.assertIn("redirect refused", message)

    def test_a_token_is_not_sent_over_a_connection_that_is_not_https(self):
        t = qase_table(base_url="http://qase.internal.example/v1")
        message = self.fails({}, table=t)
        self.assertIn("not https", message)
        self.assertEqual(ALL_REQUESTS(), 0)


def ALL_REQUESTS() -> int:
    return sum(len(f.requests) for f in st.ALL)


class NotConfigured(st.HostedCase):
    def test_a_source_with_its_variable_set_and_a_project_is_configured(self):
        self.assertIsNone(source()[0].unconfigured())

    def test_an_unset_variable_is_named(self):
        for env in ({}, {"QASE_API_TOKEN": ""}):
            with self.subTest(env=env):
                why = source(env=env)[0].unconfigured()
                self.assertIn("QASE_API_TOKEN", why)
                self.assertIn("unset or empty", why)

    def test_no_token_env_and_no_project_are_named(self):
        why = source(table=st.table("qase", [st.BILLING], project="ACME"))[0].unconfigured()
        self.assertIn("token_env", why)
        why = source(table=st.table("qase", [st.BILLING], token_env="QASE_API_TOKEN"))[0].unconfigured()
        self.assertIn("project", why)

    def test_an_unset_variable_in_the_file_is_named(self):
        t = st.config.TestSource("qase", st.config.FrozenMap({"project": None, "token_env": "QASE_API_TOKEN"}), (), ("QASE_PROJECT",))
        self.assertIn("QASE_PROJECT", source(table=t)[0].unconfigured())

    def test_no_areas_say_that_no_case_can_be_tied_to_an_area(self):
        why = source(table=qase_table(areas=[]))[0].unconfigured()
        self.assertIn("areas", why)

    def test_the_label_names_the_project(self):
        self.assertEqual(source()[0].label, "qase:ACME")


class ReadOnly(st.HostedCase):
    def test_a_fake_that_sees_a_write_fails_the_test(self):
        fake = Fake({})
        with self.assertRaises(AssertionError):
            fake("POST", f"{QASE}/case/ACME", {})
        st.ALL.remove(fake)  # it was made to be written to: the end-of-test check is for the transports the code under test used

    def test_the_module_builds_no_write_endpoint_and_no_write_method(self):
        from pathlib import Path

        text = (Path(qase.__file__)).read_text(encoding="utf-8")
        for word in ('"POST"', '"PUT"', '"PATCH"', '"DELETE"', "'POST'", "method="):
            self.assertNotIn(word, text)


if __name__ == "__main__":
    unittest.main()
