"""Credential scrubbing (context 8): a token never reaches a message, a path or a record."""

import unittest

from reachmap import redact, wording

TOKEN = "tok_LIVE_0123456789abcdefXYZ"


class MachinePaths(unittest.TestCase):
    """What a report shows of the machine it ran on: nothing (home directories and temporary folders)."""

    def test_home_and_temporary_directories_are_replaced(self):
        import os
        import tempfile
        home, tmp = os.path.expanduser("~"), tempfile.gettempdir()
        text = f"{home}/work/a.py /Users/someone/x /home/someone/y {tmp}/z/w /private/var/folders/ab/cd_e/T/q /var/folders/ab/cd/T/r /tmp/s"
        out = redact.hide_machine_paths(text)
        for needle in ("/Users/", "/home/", "/var/folders", home, tmp, "/tmp"):
            self.assertNotIn(needle, out)
        self.assertEqual(redact.hide_machine_paths("/Users/someone/x and /home/someone/y/z"), "~/x and ~/y/z")
        self.assertIn("<tmp>/s", out)
        self.assertIn("~/work/a.py", out)

    def test_text_without_a_machine_path_is_left_alone(self):
        for text in ("src/home/page.ts", "see /api/tmp/list and /usr/lib", "a/Users/b", "temporary_file", "/tmpl/x"):
            self.assertEqual(redact.hide_machine_paths(text), text)

    def test_a_temp_dir_of_tmp_is_only_hidden_where_a_path_starts_with_it(self):
        # Linux CI has gettempdir() == "/tmp"; macOS does not, so pin it here.
        from unittest import mock
        import tempfile

        with mock.patch.object(tempfile, "gettempdir", return_value="/tmp"):
            self.assertEqual(redact.hide_machine_paths("see /api/tmp/list"), "see /api/tmp/list")
            self.assertEqual(redact.hide_machine_paths("x /tmp/a/b"), "x <tmp>/a/b")

    def test_a_file_name_that_is_not_text_is_shown_with_its_bytes_escaped(self):
        self.assertEqual(redact.display("caf\udce9.py"), "caf\\xe9.py")
        self.assertEqual(redact.display("plain/ok.py"), "plain/ok.py")
        self.assertEqual(redact.display("caf\u00e9.py"), "caf\u00e9.py")  # valid unicode stays
        redact.display("x\udce9").encode("utf-8")  # always encodable


class ScrubTests(unittest.TestCase):
    def setUp(self):
        redact.forget_secrets()
        self.addCleanup(redact.forget_secrets)

    def test_url_userinfo_is_removed(self):
        for url in (f"https://user:{TOKEN}@example.com/o/r.git", f"https://{TOKEN}@example.com/o/r.git",
                    f"https://x-access-token:{TOKEN}@example.com/o/r.git"):
            out = redact.scrub(f"fatal: unable to access '{url}': 403")
            self.assertNotIn(TOKEN, out)
            self.assertIn("https://example.com/o/r.git", out)

    def test_strip_userinfo(self):
        self.assertEqual(redact.strip_userinfo(f"https://u:{TOKEN}@h.example/a/b"), "https://h.example/a/b")
        self.assertEqual(redact.strip_userinfo("ssh://git@h.example/a/b"), "ssh://h.example/a/b")
        self.assertEqual(redact.strip_userinfo("git@h.example:a/b.git"), "h.example:a/b.git")
        self.assertEqual(redact.strip_userinfo("file:///tmp/x"), "file:///tmp/x")
        self.assertEqual(redact.strip_userinfo("/tmp/a@b/x"), "/tmp/a@b/x")
        self.assertEqual(redact.strip_userinfo("https://h.example/a?x=1#f"), "https://h.example/a?x=1#f")

    def test_headers_and_query_parameters(self):
        text = f"Authorization: Bearer {TOKEN}\nPRIVATE-TOKEN: {TOKEN}\nGET /x?access_token={TOKEN}&per_page=100"
        out = redact.scrub(text)
        self.assertNotIn(TOKEN, out)
        self.assertIn("per_page=100", out)
        self.assertIn("Authorization: " + redact.REDACTED, out)

    def test_well_known_token_shapes(self):
        for token in ("ghp_" + "a" * 36, "github_pat_" + "B" * 40, "glpat-" + "c" * 20):
            self.assertNotIn(token, redact.scrub(f"remote said {token} is bad"))

    def test_registered_secret_is_removed_anywhere(self):
        redact.register_secret(TOKEN)
        self.assertNotIn(TOKEN, redact.scrub(f"odd place {TOKEN} here"))
        self.assertNotIn(TOKEN, redact.scrub_exception(RuntimeError(f"boom {TOKEN}")))

    def test_short_secrets_are_not_registered(self):
        redact.register_secret("abc")
        self.assertEqual(redact.scrub("abc def"), "abc def")

    def test_argv(self):
        argv = ["curl", "-H", f"Authorization: Bearer {TOKEN}", f"https://u:{TOKEN}@h.example/x", "--token", TOKEN]
        out = redact.scrub_argv(argv)
        self.assertNotIn(TOKEN, " ".join(out))

    def test_exception_names_its_type(self):
        self.assertEqual(redact.scrub_exception(TimeoutError()), "TimeoutError")
        self.assertTrue(redact.scrub_exception(ValueError("x")).startswith("ValueError: "))

    def test_for_record_is_one_line_bounded_and_clean_of_banned_wording(self):
        out = redact.for_record("hint: add safe.directory\nsecond line " + "x" * 2000)
        self.assertNotIn("\n", out)
        self.assertLessEqual(len(out), 600)
        self.assertEqual(wording.check(out), [])
        self.assertIn("directory", out)

    def test_header_in_json_or_repr_form(self):
        for text in ('{"Authorization": "Bearer abcdefghijklmnop"}', "{'Authorization': 'Bearer abcdefghijklmnop'}",
                     '"PRIVATE-TOKEN": "glpat-xxxxxxxxxxxxxxxxxxxx"', "authorization: bearer abc.def.ghi"):
            out = redact.scrub(text)
            self.assertNotIn("abcdefghijklmnop", out)
            self.assertNotIn("xxxxxxxxxxxxxxxxxxxx", out)
            self.assertNotIn("abc.def.ghi", out)
            self.assertIn(redact.REDACTED, out)

    def test_registered_secret_is_removed_in_its_url_quoted_and_base64_forms(self):
        import base64
        import urllib.parse
        tok = "s3cr3t/T0ken+with=chars"
        pair = "bob:app_pw_123456"
        redact.register_secret(tok)
        redact.register_secret(pair)
        for form in (tok, urllib.parse.quote(tok, safe=""), urllib.parse.quote(tok), urllib.parse.quote_plus(tok),
                     base64.b64encode(pair.encode()).decode(), base64.b64encode(pair.encode()).decode().rstrip("=")):
            out = redact.scrub(f"GET https://h/x?next={form}&y=1 failed")
            self.assertNotIn(form, out)
        self.assertIn("y=1", redact.scrub(f"x?next={urllib.parse.quote(tok, safe='')}&y=1"))

    def test_credential_query_parameters_are_dropped_from_urls_given_to_git(self):
        url = "https://github.com/a/b?access_token=abcdef123456&ref=main&private_token=zzzzzz1&x=1"
        for fn in (redact.transport_url, redact.strip_userinfo):
            out = fn(url)
            self.assertNotIn("abcdef123456", out)
            self.assertNotIn("zzzzzz1", out)
            self.assertIn("ref=main", out)
            self.assertIn("x=1", out)
        self.assertEqual(redact.transport_url("https://github.com/a/b?token=abc123def456"), "https://github.com/a/b")
        self.assertEqual(redact.transport_url("https://u:p@github.com/a/b?access_token=abcdef123456"), "https://github.com/a/b")

    def test_a_token_split_over_two_lines_is_a_documented_limit(self):
        # Scrubbing works line by line on shapes and registered values: a token that git or a
        # server wrapped across two lines is not recognised. The layers never log such text raw.
        out = redact.scrub("token glpat-ABCDEFGH\nIJKLMNOPQRST")
        self.assertIn("ABCDEFGH", out)

    def test_strip_userinfo_and_transport_url_agree_except_for_the_ssh_user(self):
        for url in ("https://u:p@h.example/a", "ssh://git:pw@h.example/a", "git@h.example:a/b", "https://h.example/a?token=abcdef123456", "/tmp/x"):
            stripped, kept = redact.strip_userinfo(url), redact.transport_url(url)
            self.assertEqual(stripped.replace("git@", ""), kept.replace("git@", ""))

    def test_plain_text_is_unchanged(self):
        text = "fatal: couldn't find remote ref refs/heads/nope"
        self.assertEqual(redact.scrub(text), text)


if __name__ == "__main__":
    unittest.main()
