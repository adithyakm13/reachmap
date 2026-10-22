"""M15 TestRail: cases paginated through `_links.next`, the sections tree, an auth failure and the other ways the service can fail
to answer. Recorded (synthetic) responses; a fake transport that only answers a GET."""

from __future__ import annotations

import base64
import unittest

from reachmap.testsources import SourceError, testrail
from tests import support_testsources as st
from tests.support_testsources import TESTRAIL, TESTRAIL_ENV, TESTRAIL_ROUTES, TESTRAIL_TOKEN, TR, Fake, testrail_table


def source(fake=None, table=None, env=None):
    fake = fake if fake is not None else Fake(TESTRAIL_ROUTES)
    return testrail.TestRail(table or testrail_table(), env=TESTRAIL_ENV if env is None else env, transport=fake), fake


class Reading(st.HostedCase):
    def test_the_next_link_is_followed_until_there_is_none(self):
        src, fake = source()
        corpus = src.fetch()
        self.assertEqual(fake.urls, [f"{TR}/get_project/3", f"{TR}/get_sections/3&limit=15&offset=0", f"{TR}/get_cases/3&limit=15&offset=0", f"{TR}/get_cases/3&limit=15&offset=15"])
        self.assertEqual((len(corpus.cases), corpus.complete), (30, True))
        self.assertEqual(corpus.cases[0].id, "C1")
        self.assertEqual(corpus.cases[0].title, "Invoice list shows billing totals")
        self.assertEqual(corpus.cases[0].group, 32)

    def test_the_sections_tree_gives_descendants(self):
        corpus = source()[0].fetch()
        self.assertEqual(corpus.parents, {31: None, 32: 31, 33: 32, 34: 31, 40: None, 41: 40})
        self.assertEqual(corpus.within([31]), {31, 32, 33, 34})
        self.assertEqual(corpus.within([32]), {32, 33})

    def test_the_login_and_the_key_go_in_a_basic_authorization_header_only(self):
        src, fake = source()
        src.fetch()
        expected = "Basic " + base64.b64encode(TESTRAIL_TOKEN.encode()).decode()
        for _, _, headers in fake.requests:
            self.assertEqual(headers["Authorization"], expected)
            self.assertEqual([k for k, v in headers.items() if st.TESTRAIL_KEY in v or st.TESTRAIL_LOGIN in v], [])
        self.assertTrue(all(st.TESTRAIL_LOGIN not in u for u in fake.urls))

    def test_a_suite_in_multi_suite_mode_is_asked_for_in_every_list(self):
        t = testrail_table(suite_id=2)
        fake = Fake({
            f"{TR}/get_project/3": "testrail/project.json", f"{TR}/get_sections/3&suite_id=2&limit=15&offset=0": "testrail/sections.json",
            f"{TR}/get_cases/3&suite_id=2&limit=15&offset=0": "testrail/cases-page1.json", f"{TR}/get_cases/3&suite_id=2&limit=15&offset=15": "testrail/cases-page2.json",
        })
        # the recorded first page links to the second without the suite; the next link is followed as given, so serve it as well
        fake.routes[f"{TR}/get_cases/3&limit=15&offset=15"] = "testrail/cases-page2.json"
        self.assertEqual(len(source(fake, t)[0].fetch().cases), 30)

    def test_an_older_server_answers_a_plain_list_with_no_pagination(self):
        routes = {**TESTRAIL_ROUTES, f"{TR}/get_cases/3&limit=15&offset=0": "testrail/plain-list.json"}
        corpus = source(Fake(routes))[0].fetch()
        self.assertEqual((len(corpus.cases), corpus.complete), (5, True))

    def test_the_trailing_slash_of_the_instance_address_does_not_matter(self):
        self.assertEqual(len(source(table=testrail_table(base_url=TESTRAIL + "/"))[0].fetch().cases), 30)


class Pagination(st.HostedCase):
    def test_a_page_cap_is_a_cut_and_the_corpus_says_it_is_incomplete(self):
        corpus = source(table=testrail_table(max_pages=1))[0].fetch()
        self.assertEqual(len(corpus.cases), 15)
        self.assertFalse(corpus.complete)
        self.assertIn("cases", corpus.cut)
        self.assertIn("15", corpus.cut)

    def test_a_next_link_to_another_host_is_not_followed(self):
        routes = {**TESTRAIL_ROUTES, f"{TR}/get_cases/3&limit=15&offset=0": "testrail/next-elsewhere.json"}
        with self.assertRaises(SourceError) as caught:
            source(Fake(routes))[0].fetch()
        self.assertIn("another host", str(caught.exception))
        self.assertFalse([u for u in ALL_URLS() if "evil" in u])  # the credential never travelled there

    def test_a_next_link_that_loops_is_unavailable(self):
        page = st.recorded("testrail/cases-page1.json")
        page["_links"]["next"] = "/api/v2/get_cases/3&limit=15&offset=0"
        routes = {**TESTRAIL_ROUTES, f"{TR}/get_cases/3&limit=15&offset=0": st.respond(page)}
        with self.assertRaises(SourceError) as caught:
            source(Fake(routes))[0].fetch()
        self.assertIn("loop", str(caught.exception))

    def test_a_page_that_fails_midway_is_unavailable_never_the_first_half(self):
        routes = {**TESTRAIL_ROUTES, f"{TR}/get_cases/3&limit=15&offset=15": "testrail/error-500.json"}
        with self.assertRaises(SourceError) as caught:
            source(Fake(routes))[0].fetch()
        self.assertIn("HTTP 500", str(caught.exception))


class NextLinks(st.HostedCase):
    BASE = {f"{TR}/get_project/3": "testrail/project.json", f"{TR}/get_sections/3&limit=15&offset=0": "testrail/sections.json"}

    def read(self, link, extra=None):
        page = st.recorded("testrail/cases-page1.json")
        page["_links"]["next"] = link
        routes = {**self.BASE, f"{TR}/get_cases/3&limit=15&offset=0": st.respond(page), **(extra or {})}
        fake = Fake(routes)
        try:
            return testrail.TestRail(st.testrail_table(), env=TESTRAIL_ENV, transport=fake).fetch(), fake
        except SourceError as exc:
            return exc, fake

    def test_a_next_link_keeps_the_endpoint_and_the_project_of_the_list_being_read(self):
        for link in ("/api/v2/delete_case/1", "/api/v2/add_case/31&title=x", "/index.php?/api/v2/delete_project/3", "/api/v2/get_cases/4&limit=15&offset=15",
                     "/api/v2/get_sections/3&limit=15&offset=15", "/api/v2/get_cases/30&limit=15&offset=15", "/api/v2/get_cases/3x", "//evil.example/index.php?/api/v2/get_cases/3",
                     "https://acme.testrail.example/index.php?/api/v2/delete_case/1", "get_cases/3&offset=15", "/api/v1/get_cases/3&offset=15"):
            with self.subTest(link):
                got, fake = self.read(link)
                self.assertIsInstance(got, SourceError)
                self.assertEqual([u for u in fake.urls if "delete_" in u or "add_" in u or "get_cases/4" in u], [])  # nothing was requested there

    def test_a_next_link_on_another_port_or_scheme_is_another_host(self):
        for link in ("https://acme.testrail.example:8443/index.php?/api/v2/get_cases/3&limit=15&offset=15", "http://acme.testrail.example/index.php?/api/v2/get_cases/3&limit=15&offset=15"):
            with self.subTest(link):
                got, fake = self.read(link)
                self.assertIsInstance(got, SourceError)
                self.assertIn("another host", str(got))

    def test_an_absolute_link_on_the_same_host_is_followed_and_rebuilt_from_the_instance_address(self):
        got, fake = self.read("HTTPS://ACME.testrail.example/index.php?/api/v2/get_cases/3&limit=15&offset=15", {f"{TR}/get_cases/3&limit=15&offset=15": "testrail/cases-page2.json"})
        self.assertEqual(len(got.cases), 30)
        self.assertEqual(fake.urls[-1], f"{TR}/get_cases/3&limit=15&offset=15")  # the request is to the instance as configured

    def test_a_relative_link_with_the_same_endpoint_is_followed(self):
        got, _ = self.read("/api/v2/get_cases/3&limit=15&offset=15", {f"{TR}/get_cases/3&limit=15&offset=15": "testrail/cases-page2.json"})
        self.assertEqual(len(got.cases), 30)


def ALL_URLS() -> list[str]:
    return [u for f in st.ALL for u in f.urls]


class Failing(st.HostedCase):
    def fails(self, routes, **kw):
        with self.assertRaises(SourceError) as caught:
            source(Fake(routes), **kw)[0].fetch()
        message = str(caught.exception)
        for secret in st.SECRETS:
            self.assertNotIn(secret, message)
        return message

    def test_an_authentication_failure_is_unavailable(self):
        message = self.fails({f"{TR}/get_project/3": "testrail/error-401.json"})
        self.assertIn("HTTP 401", message)
        self.assertIn("TESTRAIL_API_KEY", message)

    def test_a_disabled_api_is_unavailable(self):
        self.assertIn("HTTP 403", self.fails({f"{TR}/get_project/3": "testrail/error-403.json"}))

    def test_an_unknown_project_is_unavailable_never_no_suggestions(self):
        self.assertIn("404", self.fails({f"{TR}/get_project/3": "testrail/error-404.json"}))

    def test_429_500_malformed_and_timeout_are_unavailable(self):
        self.assertIn("rate limited", self.fails({f"{TR}/get_project/3": "testrail/error-429.json"}))
        self.assertIn("HTTP 500", self.fails({f"{TR}/get_project/3": "testrail/error-500.json"}))
        self.assertIn("not valid JSON", self.fails({**TESTRAIL_ROUTES, f"{TR}/get_cases/3&limit=15&offset=0": "testrail/malformed.json"}))
        self.assertIn("in time", self.fails({f"{TR}/get_project/3": TimeoutError("slow")}))

    def test_a_project_that_is_not_an_object_is_unavailable(self):
        self.assertIn("unexpected", self.fails({f"{TR}/get_project/3": st.respond([1, 2])}).lower())

    def test_an_answer_of_the_wrong_shape_is_unavailable(self):
        for body in ({"offset": 0, "cases": "none"}, {"offset": 0, "limit": 15, "size": 1, "_links": {"next": None}, "cases": [{"id": "x"}]}, "text", 7):
            with self.subTest(body=body):
                routes = {**TESTRAIL_ROUTES, f"{TR}/get_cases/3&limit=15&offset=0": st.respond(body)}
                self.assertIn("unexpected", self.fails(routes).lower())


class NotConfigured(st.HostedCase):
    def test_configured(self):
        self.assertIsNone(source()[0].unconfigured())

    def test_an_unset_variable_is_named(self):
        self.assertIn("TESTRAIL_API_KEY", source(env={})[0].unconfigured())

    def test_a_value_that_is_not_a_login_and_a_key_is_refused_without_being_shown(self):
        why = source(env={"TESTRAIL_API_KEY": "just-a-key-0123456789"})[0].unconfigured()
        self.assertIn("login:key", why)
        self.assertNotIn("just-a-key", why)

    def test_the_instance_and_the_project_are_needed(self):
        self.assertIn("base_url", source(table=st.table("testrail", [st.BILLING], project_id=3, token_env="TESTRAIL_API_KEY"))[0].unconfigured())
        self.assertIn("project_id", source(table=st.table("testrail", [st.BILLING], base_url=TESTRAIL, token_env="TESTRAIL_API_KEY"))[0].unconfigured())

    def test_the_label_names_the_project(self):
        self.assertEqual(source()[0].label, "testrail:3")


class ReadOnly(st.HostedCase):
    def test_only_get_endpoints_exist_in_the_module(self):
        from pathlib import Path

        text = Path(testrail.__file__).read_text(encoding="utf-8")
        for word in ("add_case", "update_case", "delete_case", "add_section", "add_run", "add_result", '"POST"', "'POST'", "method="):
            self.assertNotIn(word, text)
        self.assertRaises(ValueError, testrail.endpoint, "add_case", 3)  # the one function that builds an endpoint refuses anything that is not a get
        self.assertEqual(testrail.endpoint("get_cases", 3), "get_cases/3")


if __name__ == "__main__":
    unittest.main()
