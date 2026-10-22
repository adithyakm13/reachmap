"""M15 review: where the credential goes. Qase takes its API token in a `Token` header, TestRail a Basic `Authorization`, and a client
chooses its own header; the token is still registered with `redact`, still refused over a connection that is not https, and a bare
`Token` header is scrubbed from anything that is recorded or shown, in plain, JSON and repr forms."""

from __future__ import annotations

import unittest

from reachmap import redact
from reachmap.hosts import HostBase, ResponseUnusable
from reachmap.testsources import qase, testrail
from tests import support_testsources as st
from tests.support_testsources import QASE, QASE_ENV, QASE_ROUTES, TESTRAIL_ENV, TESTRAIL_ROUTES, TOKEN, Fake


class Header(st.HostedCase):
    def test_qase_sends_token_in_the_token_header_and_no_authorization_header(self):
        fake = Fake(QASE_ROUTES)
        qase.Qase(st.qase_table(), env=QASE_ENV, transport=fake).fetch()
        self.assertTrue(fake.requests)
        for _, _, headers in fake.requests:
            self.assertEqual(headers["Token"], TOKEN)
            self.assertNotIn("Authorization", headers)
            self.assertEqual([k for k, v in headers.items() if TOKEN in v], ["Token"])

    def test_testrail_still_sends_a_basic_authorization_header_and_no_token_header(self):
        fake = Fake(TESTRAIL_ROUTES)
        testrail.TestRail(st.testrail_table(), env=TESTRAIL_ENV, transport=fake).fetch()
        for _, _, headers in fake.requests:
            self.assertTrue(headers["Authorization"].startswith("Basic "))
            self.assertNotIn("Token", headers)

    def test_the_default_of_a_host_is_the_authorization_header(self):
        class Plain(HostBase):
            kind = "plain"

        h = Plain(token_env="X_TOKEN_VAR", env={"X_TOKEN_VAR": "abcdef123456"})
        self.assertEqual(h.auth_header("abcdef123456"), ("Authorization", "Bearer abcdef123456"))

    def test_a_token_is_still_never_sent_over_a_connection_that_is_not_https(self):
        fake = Fake({})
        with self.assertRaises(Exception) as caught:
            qase.Qase(st.qase_table(base_url="http://qase.internal.example/v1"), env=QASE_ENV, transport=fake).fetch()
        self.assertIn("not https", str(caught.exception))
        self.assertEqual(fake.requests, [])

    def test_the_token_is_registered_with_redact_when_it_is_read(self):
        redact.forget_secrets()
        qase.Qase(st.qase_table(), env=QASE_ENV, transport=Fake(QASE_ROUTES)).fetch()
        self.assertEqual(redact.scrub(f"leaked {TOKEN} here"), "leaked [REDACTED] here")

    def test_a_401_does_not_tell_a_person_whose_variable_is_set_to_set_it(self):
        fake = Fake({f"{QASE}/project/ACME": "qase/error-401.json"})
        with self.assertRaises(Exception) as caught:
            qase.Qase(st.qase_table(), env=QASE_ENV, transport=fake).fetch()
        message = str(caught.exception)
        self.assertIn("QASE_API_TOKEN", message)
        self.assertIn("refused", message)
        self.assertNotIn("set QASE_API_TOKEN", message)
        self.assertNotIn(TOKEN, message)

    def test_a_401_with_no_token_says_to_set_the_variable(self):
        class Bare(HostBase):
            kind = "bare"

        h = Bare(token_env="SOME_API_VAR", env={})
        with self.assertRaises(ResponseUnusable) as caught:
            h._classify(401, {}, b"")
        self.assertIn("set SOME_API_VAR", str(caught.exception))


class ScrubbingAToken(unittest.TestCase):
    def test_a_bare_token_header_is_scrubbed_in_plain_json_and_repr_forms(self):
        secret = "abc123def456ghi789"
        for text in (f"Token: {secret}", f'{{"Token": "{secret}"}}', f"{{'Token': '{secret}'}}", f"headers={{'Token': '{secret}', 'Accept': 'x'}}", f"token: {secret}", f"TOKEN={secret}"):
            with self.subTest(text):
                self.assertNotIn(secret, redact.scrub(text))

    def test_the_other_headers_are_still_scrubbed(self):
        secret = "abc123def456ghi789"
        for text in (f"x-api-token: {secret}", f"X-Auth-Token: {secret}", f"Authorization: Bearer {secret}", f"PRIVATE-TOKEN: {secret}"):
            with self.subTest(text):
                self.assertNotIn(secret, redact.scrub(text))

    def test_words_that_only_contain_token_are_left_alone(self):
        for text in ("the token_env key", "tokens are listed", "a tokenizer: lexer", "mytoken: kept", "token-bucket: 5"):
            with self.subTest(text):
                self.assertEqual(redact.scrub(text), text)


if __name__ == "__main__":
    unittest.main()


class TestRailKeyAlone(st.HostedCase):
    def setUp(self):
        super().setUp()
        redact.forget_secrets()

    def go(self, routes, transport=None):
        from reachmap.testsources import run_all
        from tests.test_testsources_run import analysis, workspace

        return run_all(workspace(st.testrail_table()), analysis(), env=TESTRAIL_ENV, transport=transport or Fake(routes))

    def test_the_key_alone_in_a_case_title_is_scrubbed(self):
        page = st.recorded("testrail/cases-page1.json")
        page["cases"][0]["title"] = f"Invoice billing totals {st.TESTRAIL_KEY}"
        out = self.go({**TESTRAIL_ROUTES, f"{st.TR}/get_cases/3&limit=15&offset=0": st.respond(page)})
        self.assertTrue(out.suggestions)
        for s in out.suggestions:
            self.assertNotIn(st.TESTRAIL_KEY, s.title)
        self.assertTrue(any(redact.REDACTED in s.title for s in out.suggestions))

    def test_the_key_alone_in_an_error_is_scrubbed_from_the_record(self):
        class Leaky(Fake):
            def __call__(self, method, url, headers=None):
                self.requests.append((method, url, dict(headers or {})))
                raise OSError(f"connection failed, the key {st.TESTRAIL_KEY} was in the request")

        out = self.go({}, Leaky({}))
        (rec,) = out.records
        self.assertNotIn(st.TESTRAIL_KEY, rec.detail)
        self.assertIn(redact.REDACTED, rec.detail)

    def test_the_key_in_its_quoted_forms_is_scrubbed_too(self):
        self.go(TESTRAIL_ROUTES)
        for form in (st.TESTRAIL_KEY, st.TESTRAIL_KEY.replace("-", "%2D"), f"'{st.TESTRAIL_KEY}'"):
            self.assertNotIn(st.TESTRAIL_KEY, redact.scrub(f"x {form} y"))

    def test_the_login_alone_is_not_a_secret_and_is_left_in_the_text(self):
        self.go(TESTRAIL_ROUTES)
        self.assertIn(st.TESTRAIL_LOGIN, redact.scrub(f"asked as {st.TESTRAIL_LOGIN}"))
