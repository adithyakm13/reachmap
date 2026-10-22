"""Hardening: every sub-table of a repository takes only the keys its adapter reads, and says so when it is given another.

A misspelled `routes_glb` used to be ignored, the default glob was used, and the run answered "no areas reached". Each kind that had no option
validator (`react-router`, `fastapi`, `celery`, `endpoints-file`, `pydantic`, `jira-snapshot`) has one, and so have the two tables with no kind
(`tests`, `aliases`). The message names the dotted path of the table and the key, lists the allowed keys and suggests the closest one. It never
quotes a configured value.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

import reachmap.entrypoints  # noqa: F401  (registers the kinds of the adapters, as a run does)
from reachmap import config
from reachmap.config import ConfigError, load, loads

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"
REPO_ROOT = ROOT.parent

HEAD = 'workspace = "acme"\n'
WEB = '[repos."acme/web"]\nhost = "github"\nlanguage = "typescript"\nroles = ["frontend"]\n'
API = '[repos."acme/api"]\nhost = "github"\nlanguage = "python"\nroles = ["backend"]\n'
LIB = '[repos."acme/lib"]\nhost = "github"\nlanguage = "python"\nroles = ["contract"]\n'


def problems(text: str, env=None) -> list[config.Problem]:
    try:
        loads(HEAD + text, fmt="toml", env=env or {})
    except ConfigError as error:
        return list(error.problems)
    raise AssertionError("the config loaded; it should not")


def ok(text: str, env=None):
    return loads(HEAD + text, fmt="toml", env=env or {})


class UnknownKeys(unittest.TestCase):
    def says(self, text: str, path: str, *fragments: str) -> None:
        found = [p for p in problems(text) if p.path == path]
        self.assertTrue(found, [(p.path, p.message) for p in problems(text)])
        for fragment in fragments:
            self.assertTrue(any(fragment in p.message for p in found), (fragment, [p.message for p in found]))

    def test_a_misspelled_routes_glob_is_refused_with_the_path_and_a_hint(self):
        self.says(WEB + 'entry_points = { kind = "react-router", routes_glb = "src/routes.tsx" }\n',
                  'repos."acme/web".entry_points', "routes_glb", "did you mean 'routes_glob'", "allowed keys: routes_glob")

    def test_the_options_of_react_router_are_the_ones_the_adapter_reads(self):
        loaded = ok(WEB + 'entry_points = { kind = "react-router", routes_glob = "src/modules/*/routes.tsx" }\n')
        self.assertEqual(loaded.repos["acme/web"].entry_points["routes_glob"], "src/modules/*/routes.tsx")
        ok(WEB + 'entry_points = { kind = "react-router", routes_glob = ["src/a/routes.tsx", "src/b/routes.tsx"] }\n')
        ok(WEB + 'entry_points = { kind = "react-router" }\n')

    def test_a_routes_glob_must_be_a_path_inside_the_repository_or_a_list_of_them(self):
        for value in ("42", '"/abs/routes.tsx"', '"../out/routes.tsx"', '""', "[]", "[1]", '["ok", "../no"]'):
            with self.subTest(value=value):
                self.says(WEB + f'entry_points = {{ kind = "react-router", routes_glob = {value} }}\n', 'repos."acme/web".entry_points', "routes_glob")

    def test_fastapi_and_celery_take_no_option_besides_kind(self):
        for kind in ("fastapi", "celery"):
            with self.subTest(kind=kind):
                self.says(API + f'entry_points = {{ kind = "{kind}", prefix = "/v1" }}\n', 'repos."acme/api".entry_points', "prefix", "there is none")
                ok(API + f'entry_points = {{ kind = "{kind}" }}\n')

    def test_endpoints_file_takes_a_path(self):
        self.says(WEB + 'http_client = { kind = "endpoints-file", pth = "src/api/endpoints.ts" }\n', 'repos."acme/web".http_client', "pth", "did you mean 'path'")
        self.says(WEB + 'http_client = { kind = "endpoints-file", path = "/etc/endpoints.ts" }\n', 'repos."acme/web".http_client', "path")
        self.says(WEB + 'http_client = { kind = "endpoints-file", path = 7 }\n', 'repos."acme/web".http_client', "path", "an integer")
        ok(WEB + 'http_client = { kind = "endpoints-file", path = "src/api/endpoints.ts" }\n')
        ok(WEB + 'http_client = { kind = "endpoints-file" }\n')

    def test_pydantic_takes_what_the_edges_read(self):
        self.says(LIB + 'contract = { kind = "pydantic", root = "models", tag_prefx = "v" }\n', 'repos."acme/lib".contract', "tag_prefx", "did you mean 'tag_prefix'")
        ok(LIB + 'contract = { kind = "pydantic", root = "contracts/models", package = "acme_lib", versions = "git-tags", tag_prefix = "v" }\n')
        self.says(LIB + 'contract = { kind = "pydantic", root = "../models" }\n', 'repos."acme/lib".contract', "root")
        self.says(LIB + 'contract = { kind = "pydantic", versions = "svn" }\n', 'repos."acme/lib".contract', "versions", "git-tags")
        self.says(LIB + 'contract = { kind = "pydantic", package = 3 }\n', 'repos."acme/lib".contract', "package")

    def test_tests_takes_markers_and_a_command(self):
        self.says(WEB + 'tests = { marker = ["__tests__/"] }\n', 'repos."acme/web".tests', "marker", "did you mean 'markers'", "allowed keys: markers, command")
        self.says(WEB + 'tests = { markers = "__tests__/" }\n', 'repos."acme/web".tests', "markers", "list")
        self.says(WEB + 'tests = { markers = ["a", 3] }\n', 'repos."acme/web".tests', "markers")
        self.says(WEB + 'tests = { command = 5 }\n', 'repos."acme/web".tests', "command")
        loaded = ok(WEB + 'tests = { markers = [] }\n')  # an empty list is allowed: no file is a test (leave the key out for the defaults)
        self.assertEqual(list(loaded.repos["acme/web"].tests["markers"]), [])
        loaded = ok(WEB + 'tests = { markers = ["__tests__/", ".spec."], command = "npx jest {paths}" }\n')
        self.assertEqual(loaded.repos["acme/web"].tests["command"], "npx jest {paths}")

    def test_aliases_takes_from(self):
        self.says(WEB + 'aliases = { form = "tsconfig.json" }\n', 'repos."acme/web".aliases', "form", "did you mean 'from'", "allowed keys: from")
        self.says(WEB + 'aliases = { from = "../tsconfig.json" }\n', 'repos."acme/web".aliases', "from")
        self.says(WEB + 'aliases = { from = 4 }\n', 'repos."acme/web".aliases', "from")
        ok(WEB + 'aliases = { from = "tsconfig.json" }\n')

    def test_tests_and_aliases_have_no_kind(self):
        self.says(WEB + 'tests = { kind = "jest" }\n', 'repos."acme/web".tests.kind', "unknown kind")
        self.says(WEB + 'aliases = { kind = "x" }\n', 'repos."acme/web".aliases.kind', "unknown kind")

    def test_jira_snapshot_takes_a_path(self):
        text = '\n[[context]]\nkind = "jira-snapshot"\npathh = "x.json"\n'
        self.says(WEB + text, "context[0]", "pathh", "did you mean 'path'")
        ok(WEB + '\n[[context]]\nkind = "jira-snapshot"\npath = "${ACME_JIRA_SNAPSHOT}"\n')  # unset: not configured, and fine
        ok(WEB + '\n[[context]]\nkind = "jira-snapshot"\npath = "snap.json"\n')

    def test_the_kinds_other_adapters_registered_keep_their_own_messages(self):
        self.says(WEB + 'entry_points = { kind = "nextjs", roots = "x" }\n', 'repos."acme/web".entry_points', "roots")
        self.says(API + 'entry_points = { kind = "django", root_urlconf_ = "x" }\n', 'repos."acme/api".entry_points', "root_urlconf_")

    def test_a_message_never_quotes_a_configured_value(self):
        secret = "SENTINEL-VALUE-9f3a"
        for text in (WEB + f'entry_points = {{ kind = "react-router", routes_glob = ["{secret}", 3, "../x"] }}\n',
                     WEB + f'tests = {{ markers = [3], command = 1 }}\naliases = {{ from = "../{secret}" }}\n',
                     LIB + f'contract = {{ kind = "pydantic", root = "../{secret}", versions = "{secret}", package = 3 }}\n',
                     WEB + f'http_client = {{ kind = "endpoints-file", path = "../{secret}" }}\n'):
            with self.subTest(text=text):
                found = problems(text)
                self.assertTrue(found)
                for problem in found:
                    self.assertNotIn(secret, problem.message)
        # the name of an unknown key is not a value: it is what the message is about
        self.assertTrue(any(secret in p.message for p in problems(WEB + f'tests = {{ "{secret}" = "v" }}\n')))

    def test_every_problem_of_a_file_is_reported_at_once(self):
        found = problems(WEB + 'aliases = { form = "x" }\ntests = { marker = ["a"] }\nentry_points = { kind = "react-router", routes_glb = "a" }\n')
        self.assertEqual(sorted(p.path for p in found), ['repos."acme/web".aliases', 'repos."acme/web".entry_points', 'repos."acme/web".tests'])

    def test_every_registered_kind_has_a_validator(self):
        """A kind added later without one would accept any key silently: this fails until it has one (or is listed with the reason)."""
        for name, section in config._SECTIONS.items():
            for kind, spec in section.kinds.items():
                with self.subTest(section=name, kind=kind):
                    self.assertIsNotNone(spec.validator, f"{name}.{kind} has no option validator")
        for name in ("tests", "aliases"):
            self.assertIsNotNone(config._SECTIONS[name].plain, name)


class Fixtures(unittest.TestCase):
    """Every config that ships with the tests or the documentation still loads."""

    ENV = {"REACHMAP_FIXTURES": "/fx", "ACME_JIRA_SNAPSHOT": "/fx/jira.json", "API_VERSION": "v1"}

    def good_files(self):
        out = []
        for base in (FIXTURES, REPO_ROOT / "examples"):
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*")):
                if path.suffix != ".toml" or "bad" in path.parts or "node_modules" in path.parts:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                if "[repos." in text and "[project]" not in text and "[tool." not in text:
                    out.append(path)
        return out

    def test_there_are_configs_to_load(self):
        names = [p.name for p in self.good_files()]
        for expected in ("reachmap.toml", "example.toml", "express.toml", "nextjs.toml", "openapi.toml", "django.toml", "spring.toml"):
            self.assertIn(expected, names)

    def test_every_config_under_the_fixtures_and_the_examples_loads(self):
        for path in self.good_files():
            with self.subTest(path=str(path.relative_to(REPO_ROOT))):
                load(path, env=dict(self.ENV))

    def test_the_toml_examples_of_the_documentation_load(self):
        blocks = 0
        for name in ("docs/CONTEXT.md", "docs/BOOTSTRAP.md"):
            text = (REPO_ROOT / name).read_text(encoding="utf-8")
            for i, found in enumerate(re.finditer(r"```toml\n(.*?)```", text, re.S)):
                body = found.group(1)
                if "[repos." not in body or "workspace =" not in body:
                    continue
                blocks += 1
                with self.subTest(doc=name, block=i):
                    loads(body, fmt="toml", env=dict(self.ENV), base_dir=tempfile.gettempdir())
        self.assertGreaterEqual(blocks, 2)

    def test_the_bad_configs_still_fail(self):
        for path in sorted((FIXTURES / "configs" / "bad").iterdir()):
            with self.subTest(path=path.name):
                with self.assertRaises(ConfigError):
                    load(path, env={})


if __name__ == "__main__":
    unittest.main()
