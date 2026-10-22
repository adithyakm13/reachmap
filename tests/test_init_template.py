"""M17: the template writes TOML that parses back to what was given. Strings with quotes, backslashes, newlines, `'''`, control and
bidirectional characters, unicode and `${`; regexes as literal strings unless they hold a quote; comments that hold a name with a
newline in it cannot end the comment; the whole file always loads, and a table the loader refuses is written as a comment."""

import random
import re
import tomllib
import unittest

from reachmap import config
from reachmap.init.guess import Guess, Note
from reachmap.init.template import build, comment_text, render, toml_regex, toml_string, toml_value
from tests import support_init as si

NASTY = ['"', "\\", "'", "'''", '"""', "\n", "\r\n", "\t", "\x00", "\x1b", "\x7f", "\u202e", "\u2066", "\u200d", "\u2028", "\ud800", "é", "漢", "\U0001F600", "${", "$${", "${HOME}",
         "a = 1", "]\n[evil]\nx = 1\n", "# x", " "]


def strings(n=300, seed=17):
    rnd = random.Random(seed)
    out = list(NASTY)
    for _ in range(n):
        out.append("".join(rnd.choice(NASTY + list("abc/._-")) for _ in range(rnd.randint(1, 12))))
    return out


class Escaping(unittest.TestCase):
    def test_a_string_round_trips_through_toml_and_the_loaders_variable_escape(self):
        for s in strings():
            with self.subTest(s=s):
                parsed = tomllib.loads(f"v = {toml_string(s)}\n")["v"]
                expected = s.replace("${", "$${")  # the loader reads $${ as a literal ${
                self.assertEqual(parsed, "".join("�" if 0xD800 <= ord(c) <= 0xDFFF else c for c in expected))
                if s.strip() and not any(0xD800 <= ord(c) <= 0xDFFF for c in s):
                    ws = config.loads(f'workspace = {toml_string(s)}\n[repos."x"]\nhost = "git"\nremote = "/r"\nlanguage = "python"\nroles = ["library"]\n', fmt="toml", env={})
                    self.assertEqual(ws.name, s)

    def test_no_raw_control_or_bidirectional_character_is_written(self):
        for s in strings():
            written = toml_string(s) + toml_regex(s) + toml_value([s, {"k": s}])
            self.assertFalse(re.search("[\x00-\x08\x0a-\x1f\x7f\u202a-\u202e\u2066-\u2069\u200b-\u200f\u2028\u2029]", written), repr(s))

    def test_a_regex_is_a_literal_string_unless_it_holds_a_single_quote_or_an_unprintable(self):
        self.assertEqual(toml_regex(r"^acme-contracts==(.+)$"), r"'^acme-contracts==(.+)$'")
        self.assertEqual(toml_regex(r"^\s*(\d+)\.\d$"), r"'^\s*(\d+)\.\d$'")
        for s in strings():
            self.assertEqual(tomllib.loads(f"v = {toml_regex(s)}\n")["v"], "".join("�" if 0xD800 <= ord(c) <= 0xDFFF else c for c in s.replace("${", "$${")), repr(s))
        self.assertTrue(toml_regex("it's").startswith('"'))
        self.assertTrue(toml_regex("a\tb").startswith('"'))

    def test_values_lists_tables_and_numbers(self):
        value = {"kind": "x", "n": 3, "flag": True, "list": ["a", "b\"c"], "odd key": "v"}
        self.assertEqual(tomllib.loads(f"t = {toml_value(value)}\n")["t"], value)
        with self.assertRaises(TypeError):
            toml_value(1.5)

    def test_a_comment_is_one_printable_line(self):
        for s in strings():
            line = comment_text(s)
            self.assertNotRegex(line, "[\n\r\t\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069\u2028\u2029]")
        self.assertEqual(comment_text("a\n[evil]\nx = 1"), "a [evil] x = 1")
        self.assertTrue(comment_text("x" * 1000).endswith("..."))
        self.assertLessEqual(len(comment_text("x" * 1000)), 480)


class Hostile(unittest.TestCase):
    """A repository whose names and paths are chosen by an attacker: the file still loads and has no table the repository chose."""

    EVIL = 'a\n[repos."evil"]\nhost = "git"\n# \u202e'

    def guess(self, name):
        g = si.guess({self.EVIL + ".py": "x = 1\n", "src/test_x.py": "", "package.json": "{}"}, name=name, remote="/r/x")
        g.sections["tests"] = Guess({"markers": ["test_"]}, (Note("because of " + self.EVIL, (self.EVIL + ":1",), "high"),))
        g.notes.append("found " + self.EVIL)
        g.unread.append((self.EVIL, self.EVIL))
        return g

    def test_newlines_quotes_and_bidi_in_names_and_paths_cannot_start_a_line_of_toml(self):
        text = render([self.guess('acme/we"b')], out_dir="/r")
        parsed = tomllib.loads(text)
        self.assertEqual(list(parsed["repos"]), ['acme/we"b'])
        self.assertNotIn("evil", parsed["repos"])
        self.assertNotIn("\u202e", text)
        config.loads(text, fmt="toml", env={})

    def test_a_hostile_failure_reason_stays_in_its_comment(self):
        built = build([self.guess("acme/web")], [(self.EVIL, self.EVIL)], "/r")
        self.assertEqual(list(tomllib.loads(built.text)["repos"]), ["acme/web"])


class Unread(unittest.TestCase):
    def test_files_that_could_not_be_read_are_listed_under_the_repository_counted_and_capped(self):
        g = si.guess({"a.py": "x = 1\n"}, name="acme/x", remote="/r/x")
        g.unread = [(f"p{i}.py", "the file is not valid UTF-8") for i in range(30)]
        built = build([g], out_dir="/r")
        self.assertEqual(len(built.unread), 30)
        self.assertEqual(built.unread[0], ("acme/x", "p0.py", "the file is not valid UTF-8"))
        self.assertEqual(built.text.count("# could not read p"), 25)
        self.assertIn("# and 5 more files could not be read", built.text)
        self.assertIn("(30 could not be read)", build([si.guess({"a.py": ""}, name="a/y", remote="/r/y"), g], out_dir="/r").text)
        config.loads(built.text, fmt="toml", env={})

    def test_a_credential_in_a_failure_is_scrubbed_where_it_is_written(self):
        built = build([si.guess({"a.py": ""}, name="a/y", remote="/r/y")], [("git:https://u:s3cretvalue1@example.com/x.git", "failed for https://u:s3cretvalue1@example.com/x.git")], "/r")
        self.assertNotIn("s3cretvalue1", built.text)


class GuessLines(unittest.TestCase):
    KEYS = re.compile(r"^(language|roles|aliases|entry_points|http_server|http_client|contract|tests|service_marker|library_roots) = ")

    @staticmethod
    def missing(text):
        """Key lines of a repository table with no `# guess:` line directly above (a block of guess lines may sit above one key)."""
        lines = text.splitlines()
        return [lines[i] for i, line in enumerate(lines) if GuessLines.KEYS.match(line) and not lines[i - 1].startswith("# guess:")]

    def test_every_key_but_host_remote_and_branches_has_a_guess_line_directly_above_it(self):
        files = {"src/routes.tsx": '<Route path="/a" element={<A />} />\n', "src/api/endpoints.ts": 'export const A = "/api/a";\nexport const B = "/api/b";\nexport const C = "/api/c";\n',
                 "src/s.ts": 'import { A } from "./api/endpoints";\nfetch(A);\n', "src/a.test.ts": "", "tsconfig.json": '{"compilerOptions": {"paths": {"@/*": ["src/*"]}}}',
                 "package.json": '{"scripts": {"test": "jest"}}'}
        text = render([si.guess(files, name="a/web", remote="/r/web"), si.guess({"a.py": ""}, name="a/lib", remote="/r/lib")], out_dir="/r")
        self.assertEqual(self.missing(text), [])
        self.assertIn("entry_points = ", text)

    def test_the_check_notices_a_dropped_comment(self):
        text = render([si.guess({"src/routes.tsx": '<Route path="/a" element={<A />} />\n'}, name="a/web", remote="/r/web")], out_dir="/r")
        stripped = "\n".join(l for l in text.splitlines() if not l.startswith("# guess: frontend") and not l.startswith("# guess: no react-router"))
        self.assertTrue(any(l.startswith("roles = ") for l in self.missing(stripped)))


class Refused(unittest.TestCase):
    def test_a_table_the_loader_refuses_is_written_as_a_comment_with_its_message_and_the_rest_loads(self):
        g = si.guess({"a.py": "x = 1\n"}, name="acme/x", remote="/r/x")
        g.sections["entry_points"] = Guess({"kind": "no-such-kind"}, (Note("made up", ("a.py:1",), "low"),))
        built = build([g], out_dir="/r")
        self.assertIn("# not written: entry_points was guessed, but the loader refused it: unknown kind", built.text)
        self.assertEqual(config.loads(built.text, fmt="toml", env={}).repos["acme/x"].entry_points, None)

    def test_a_repository_whose_required_key_the_loader_refuses_is_left_out_and_listed(self):
        g = si.guess({"a.py": "x = 1\n"}, name="acme/x", remote="/r/x")
        other = si.guess({"a.py": "x = 1\n"}, name="acme/y", remote="/r/y")
        g.roles = Guess(["wizard"], ())
        built = build([g, other], out_dir="/r")
        self.assertEqual([spec for spec, _ in built.failures], ["acme/x"])
        self.assertIn("the config loader refused it", built.failures[0][1])
        self.assertEqual(list(config.loads(built.text, fmt="toml", env={}).repos), ["acme/y"])
        self.assertIn("# Could not read", built.text)

    def test_nothing_readable_gives_a_file_with_only_the_list_and_no_names(self):
        built = build([], [("gitlab:a/b", "the remote could not be read")], "/r")
        self.assertEqual(built.names, [])
        self.assertIn("#   gitlab:a/b: the remote could not be read", built.text)


class Remotes(unittest.TestCase):
    def remote(self, path, out_dir):
        g = si.guess({"a.py": "x = 1\n"}, name="acme/x", remote=path, remote_local=True)
        return config.loads(build([g], out_dir=out_dir).text, fmt="toml", env={}, base_dir=out_dir).repos["acme/x"].remote, build([g], out_dir=out_dir).text

    def test_a_local_repository_is_a_path_relative_to_the_file_that_resolves_back(self):
        got, text = self.remote("/work/repos/web", "/work/conf")
        self.assertEqual(got, "/work/repos/web")
        self.assertIn('remote = "../repos/web"', text)
        got, text = self.remote("/work/conf/web", "/work/conf")
        self.assertEqual(got, "/work/conf/web")
        self.assertIn('remote = "./web"', text)

    def test_a_far_path_stays_absolute_and_says_a_committed_config_should_not_hold_it(self):
        got, text = self.remote("/a/b/c/d/e/f/g/web", "/x/y/z/w/v/u/t")
        self.assertEqual(got, "/a/b/c/d/e/f/g/web")
        self.assertIn("a committed config should not hold one", text)

    def test_a_host_repository_has_no_remote_line_and_a_url_is_written_as_given(self):
        g = si.guess({"a.py": ""}, name="acme/x", host="github", remote=None)
        text = build([g]).text
        self.assertIn('host = "github"', text)
        self.assertNotIn("remote =", text)
        g = si.guess({"a.py": ""}, name="acme/x", host="git", remote="https://example.com/acme/x.git")
        self.assertEqual(config.loads(build([g]).text, fmt="toml", env={}).repos["acme/x"].remote, "https://example.com/acme/x.git")

    def test_a_dollar_brace_in_a_path_reads_back_as_written(self):
        got, _ = self.remote("/work/${x}/web", "/work/${x}")
        self.assertEqual(got, "/work/${x}/web")


class Wording(unittest.TestCase):
    PROSE = "".join(map(chr, (0x2014, 0x2013, 0x2192, 0x201C, 0x201D, 0x2018, 0x2019)))
    BANNED = re.compile("[" + PROSE + "]|->")

    def test_the_generated_text_has_no_dashes_arrows_curly_quotes_or_ascii_arrows(self):
        g = si.guess({"src/a.test.ts": "", "package.json": '{"scripts": {"test": "jest"}}'}, name="acme/x", remote="/r/x")
        self.assertIsNone(self.BANNED.search(render([g], out_dir="/r")))

    def test_the_sources_of_the_package_have_none_either(self):
        from pathlib import Path

        for path in sorted(Path(__file__).resolve().parents[1].glob("reachmap/init/*.py")):
            text = path.read_text(encoding="utf-8")
            code = re.sub(r"\)\s*->\s", ") ", text)  # a return annotation is syntax, not prose
            self.assertIsNone(re.search("[" + self.PROSE + "]", text), path.name)
            self.assertIsNone(re.search(r"(?<!\))\s->\s", re.sub(r"\)\s*->", ")", code)), path.name)

    def test_no_secret_shaped_text_and_a_token_only_by_name(self):
        text = render([si.guess({"a.py": ""}, name="acme/x", remote="/r/x")], out_dir="/r")
        for line in text.splitlines():
            if "token_env" in line:
                self.assertTrue(line.startswith("#"))
            self.assertNotRegex(line, r"ghp_|glpat-|xox[bp]-|Bearer |password\s*=")


if __name__ == "__main__":
    unittest.main()
