"""M3: reachmap.toml loads into typed immutable config, and every bad config says why.

Tests never read the real environment: every load passes `env=`.
"""

from __future__ import annotations

import dataclasses
import copy
import json
import multiprocessing
import os
import pickle
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from reachmap import config
from reachmap.config import ConfigError, Edge, Limits, Workspace, load, loads

FIXTURES = Path(__file__).parent / "fixtures"
CONFIGS = FIXTURES / "configs"
BAD = CONFIGS / "bad"

SECRET = "tok-9f8e7d6c5b4a-SECRET"
FIXTURE_ROOT = "/fx"

# A smallest valid file; tests append what they exercise.
HEAD = 'workspace = "acme"\n'
WEB = '[repos."acme/web"]\nhost = "github"\nlanguage = "typescript"\nroles = ["frontend"]\n'
API = '[repos."acme/api"]\nhost = "github"\nlanguage = "python"\nroles = ["backend"]\n'
CONTRACTS = '[repos."acme/contracts"]\nhost = "github"\nlanguage = "python"\nroles = ["contract"]\n'
MINIMAL = HEAD + "\n" + WEB


def bad(name: str, env=None) -> ConfigError:
    try:
        load(BAD / name, env={} if env is None else env)
    except ConfigError as error:
        return error
    raise AssertionError(f"{name} loaded; it should not")


def paths(error: ConfigError) -> list[str]:
    return [problem.path for problem in error.problems]


def message_at(error: ConfigError, path: str) -> str:
    for problem in error.problems:
        if problem.path == path:
            return problem.message
    raise AssertionError(f"no problem at {path!r}; have {paths(error)}")


def edge_tuple(edge: Edge):
    pin = edge.pin_pattern.pattern if edge.pin_pattern else None
    return (edge.kind, edge.from_repo, edge.to_repo, edge.manifest, pin, edge.import_marker, tuple(sorted(edge.options.items())))


class ExampleConfig(unittest.TestCase):
    """The context 7.2 example, verbatim in fixtures/configs/example.toml."""

    def example(self, env):
        return load(CONFIGS / "example.toml", env=env)

    def test_loads_with_the_jira_variable_unset(self):
        ws = self.example({})
        self.assertIsInstance(ws, Workspace)
        self.assertEqual(ws.name, "acme")
        self.assertEqual(sorted(ws.repos), ["acme/api", "acme/contracts", "acme/web", "acme/worker"])
        (jira,) = ws.context
        self.assertEqual(jira.kind, "jira-snapshot")
        self.assertFalse(jira.configured)
        self.assertEqual(jira.unset_vars, ("ACME_JIRA_SNAPSHOT",))
        self.assertIsNone(jira.options["path"])

    def test_jira_path_expands_when_the_variable_is_set(self):
        ws = self.example({"ACME_JIRA_SNAPSHOT": "/data/jira.json"})
        (jira,) = ws.context
        self.assertTrue(jira.configured)
        self.assertEqual(jira.options["path"], "/data/jira.json")

    def test_a_relative_jira_path_resolves_against_the_config_directory(self):
        ws = self.example({"ACME_JIRA_SNAPSHOT": "snapshots/jira.json"})
        self.assertEqual(ws.context[0].options["path"], os.path.join(str(CONFIGS), "snapshots", "jira.json"))

    def test_ticket_pattern_is_compiled_from_the_single_quoted_literal(self):
        ws = self.example({})
        self.assertIsInstance(ws.ticket_pattern, re.Pattern)
        self.assertEqual(ws.ticket_pattern.pattern, r"\[([A-Z][A-Z0-9]+-\d+)\]")
        self.assertEqual(ws.ticket_pattern.search("[ABC-123] fix rounding").group(1), "ABC-123")

    def test_limits_keep_the_file_values_and_default_the_rest(self):
        self.assertEqual(self.example({}).limits, Limits(hops=3, hub_importers=50, distinct_max=10, timeout_s=900, module_cap=200))

    def test_limits_default_when_the_table_is_absent(self):
        self.assertEqual(loads(MINIMAL, fmt="toml", env={}).limits, Limits(3, 50, 10, 900, 200))

    def test_repo_fields(self):
        ws = self.example({})
        web = ws.repos["acme/web"]
        self.assertEqual(web.name, "acme/web")
        self.assertEqual(web.host, "github")
        self.assertIsNone(web.remote)
        self.assertEqual(web.language, "typescript")
        self.assertEqual(web.roles, ("frontend",))
        self.assertEqual(web.branches, ("main", "release/*"))
        self.assertEqual(dict(web.aliases), {"from": "tsconfig.json"})
        self.assertEqual(dict(web.entry_points), {"kind": "react-router", "routes_glob": "src/modules/*/routes.tsx"})
        self.assertEqual(dict(web.http_client), {"kind": "endpoints-file", "path": "src/api/endpoints.ts"})
        self.assertEqual(dict(web.tests), {"markers": ("__tests__/", ".test.", ".spec."), "command": "npx jest {paths}"})
        self.assertIsNone(web.contract)
        contracts = ws.repos["acme/contracts"]
        self.assertEqual(contracts.contract["kind"], "pydantic")
        self.assertEqual(contracts.contract["tag_prefix"], "v")
        self.assertEqual(ws.repos["acme/api"].service_marker, "pyproject.toml")

    def test_edges(self):
        edges = {edge_tuple(e) for e in self.example({}).edges}
        pin = "^acme-contracts==(.+)$"
        self.assertEqual(
            edges,
            {
                ("http", "acme/web", "acme/api", None, None, None, ()),
                ("package", "acme/contracts", "acme/api", "requirements.txt", pin, "acme_contracts", ()),
                ("package", "acme/contracts", "acme/worker", "requirements.txt", pin, "acme_contracts", ()),
                ("event", "acme/api", "acme/worker", None, None, None, (("topics_glob", "**/topics.py"),)),
            },
        )

    def test_test_source_and_areas(self):
        (qase,) = self.example({}).test_sources
        self.assertEqual(qase.kind, "qase")
        self.assertTrue(qase.configured)
        self.assertEqual(qase.options["project"], "ACME")
        self.assertEqual(qase.options["token_env"], "QASE_API_TOKEN")
        (area,) = qase.areas
        self.assertEqual(area["name"], "Billing")
        self.assertEqual(area["suites"], (12,))
        self.assertEqual(area["route_prefixes"], ("/billing",))

    def test_source_records_where_it_was_read(self):
        self.assertEqual(self.example({}).source, str(CONFIGS / "example.toml"))
        self.assertEqual(loads(MINIMAL, fmt="toml", env={}).source, "<string>")
        self.assertEqual(loads(MINIMAL, fmt="toml", env={}, source="ci.toml").source, "ci.toml")


class Immutability(unittest.TestCase):
    def setUp(self):
        self.ws = load(CONFIGS / "example.toml", env={})

    def test_dataclasses_are_frozen(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            self.ws.name = "x"
        with self.assertRaises(dataclasses.FrozenInstanceError):
            self.ws.limits.hops = 9
        with self.assertRaises(dataclasses.FrozenInstanceError):
            self.ws.repos["acme/web"].host = "git"

    def test_containers_are_immutable(self):
        web = self.ws.repos["acme/web"]
        with self.assertRaises(TypeError):
            self.ws.repos["x"] = web
        with self.assertRaises(TypeError):
            web.entry_points["kind"] = "fastapi"
        with self.assertRaises(AttributeError):
            web.roles.append("backend")
        with self.assertRaises(TypeError):
            self.ws.test_sources[0].options["project"] = "X"
        with self.assertRaises(TypeError):
            self.ws.test_sources[0].areas[0]["name"] = "X"
        self.assertIsInstance(self.ws.edges, tuple)


class FixtureWorkspace(unittest.TestCase):
    """tests/fixtures/reachmap.toml is tests/fixtures/workspace.json."""

    def setUp(self):
        self.ws = load(FIXTURES / "reachmap.toml", env={"REACHMAP_FIXTURES": FIXTURE_ROOT})
        self.expected = json.loads((FIXTURES / "workspace.json").read_text(encoding="utf-8"))

    def test_name_and_limits(self):
        self.assertEqual(self.ws.name, self.expected["workspace"])
        for key, value in self.expected["limits"].items():
            self.assertEqual(getattr(self.ws.limits, key), value, key)

    def test_same_repos(self):
        self.assertEqual(sorted(self.ws.repos), sorted(self.expected["repos"]))

    def test_every_repo_matches(self):
        for name, want in self.expected["repos"].items():
            repo = self.ws.repos[name]
            with self.subTest(repo=name):
                self.assertEqual(repo.host, want["host"])
                self.assertEqual(repo.remote, f"{FIXTURE_ROOT}/{want['slug']}")
                self.assertEqual(repo.language, want["language"])
                self.assertEqual(list(repo.roles), want["roles"])
                self.assertEqual(list(repo.branches), want["branches"])
                self.assertEqual(repo.primary_branch, "main")
                self.assertEqual(repo.service_marker, want.get("service_marker"))
                self.assertEqual(list(repo.library_roots), want.get("library_roots", []))
                for table in ("aliases", "entry_points", "http_client", "tests", "contract"):
                    got = getattr(repo, table)
                    if table not in want:
                        self.assertIsNone(got, table)
                        continue
                    self.assertEqual(
                        {k: list(v) if isinstance(v, tuple) else v for k, v in got.items()}, want[table], table
                    )

    def test_same_edges(self):
        want = []
        for e in self.expected["edges"]:
            options = tuple(sorted((k, v) for k, v in e.items() if k in ("topics_glob",)))
            want.append(
                (
                    e["kind"],
                    e.get("from", e.get("producer")),
                    e.get("to", e.get("consumer")),
                    e.get("manifest"),
                    e.get("pin_pattern"),
                    e.get("import_marker"),
                    options,
                )
            )
        self.assertEqual([edge_tuple(e) for e in self.ws.edges], want)

    def test_does_not_load_without_the_variable(self):
        with self.assertRaises(ConfigError) as caught:
            load(FIXTURES / "reachmap.toml", env={})
        self.assertIn("REACHMAP_FIXTURES", str(caught.exception))
        self.assertEqual(len(caught.exception.problems), 4)


class OneBadConfigPerRule(unittest.TestCase):
    def test_unknown_role(self):
        err = bad("unknown_role.toml")
        message = message_at(err, 'repos."acme/web".roles[1]')
        self.assertNotIn("gateway", message)  # a configured value is never echoed
        for allowed in ("frontend", "backend", "contract", "library", "worker"):
            self.assertIn(allowed, message)
        self.assertEqual(len(err.problems), 1)

    def test_unknown_edge_kind(self):
        err = bad("unknown_edge_kind.toml")
        message = message_at(err, "edges[0].kind")
        self.assertNotIn("grpc", message)
        for allowed in ("http", "package", "contract", "event", "library"):
            self.assertIn(allowed, message)

    def test_edge_naming_a_missing_repo(self):
        err = bad("edge_missing_repo.toml")
        message = message_at(err, "edges[0].to")
        self.assertNotIn("acme/api", message)
        self.assertIn("acme/web", message)  # lists the repos that do exist
        self.assertEqual(len(err.problems), 1)

    def test_pin_pattern_without_a_group(self):
        err = bad("pin_pattern_no_group.toml")
        message = message_at(err, "edges[0].pin_pattern")
        self.assertIn("exactly one group", message)
        self.assertIn("0", message)

    def test_pin_pattern_with_two_groups(self):
        err = bad("several_problems.toml")
        self.assertIn("exactly one group", message_at(err, "edges[1].pin_pattern"))

    def test_bad_regex(self):
        err = bad("bad_regex.toml")
        message = message_at(err, "ticket_pattern")
        self.assertIn("regular expression", message)

    def test_bad_pin_regex_names_the_key(self):
        text = HEAD + WEB + API + '[[edges]]\nkind = "package"\nproducer = "acme/web"\nconsumer = "acme/api"\n'
        text += 'manifest = "m"\nimport_marker = "x"\npin_pattern = \'(unclosed\'\n'
        with self.assertRaises(ConfigError) as caught:
            loads(text, fmt="toml", env={})
        self.assertIn("regular expression", message_at(caught.exception, "edges[0].pin_pattern"))

    def test_git_host_without_remote(self):
        err = bad("git_without_remote.toml")
        message = message_at(err, 'repos."acme/web".remote')
        self.assertIn("git", message)
        self.assertIn("remote", message)

    def test_negative_limit(self):
        err = bad("negative_limit.toml")
        message = message_at(err, "limits.hops")
        self.assertIn("positive", message)
        self.assertNotIn("-1", message)

    def test_limits_reject_zero_text_bool_and_unknown_keys(self):
        for value in ("0", '"3"', "true", "2.5"):
            with self.subTest(value=value):
                with self.assertRaises(ConfigError) as caught:
                    loads(MINIMAL + "\n[limits]\nhops = " + value + "\n", fmt="toml", env={})
                self.assertIn("limits.hops", paths(caught.exception))
        with self.assertRaises(ConfigError) as caught:
            loads(HEAD + "[limits]\ndepth = 4\n\n" + WEB, fmt="toml", env={})
        message = message_at(caught.exception, "limits.depth")
        self.assertIn("unknown key", message)
        self.assertIn("module_cap", message)

    def test_unknown_top_level_key(self):
        err = bad("unknown_top_level_key.toml")
        message = message_at(err, "limts")
        self.assertIn("unknown key", message)
        self.assertIn("limits", message)  # the allowed keys, and a near miss

    def test_unknown_repo_key(self):
        err = bad("unknown_repo_key.toml")
        message = message_at(err, 'repos."acme/web".framework')
        self.assertIn("unknown key", message)
        self.assertIn("entry_points", message)

    def test_unknown_entry_point_kind(self):
        err = bad("unknown_entry_point_kind.toml")
        message = message_at(err, 'repos."acme/web".entry_points.kind')
        self.assertNotIn("gatsby", message)
        for known in ("react-router", "fastapi", "celery"):
            self.assertIn(known, message)

    def test_entry_point_kind_is_required(self):
        text = MINIMAL + "entry_points = { routes_glob = 'x' }\n"
        with self.assertRaises(ConfigError) as caught:
            loads(text, fmt="toml", env={})
        self.assertIn("react-router", message_at(caught.exception, 'repos."acme/web".entry_points.kind'))

    def test_token_looking_token_env(self):
        pasted = "qase_tok_9f8a7b6c5d4e3f2a1b0c"
        err = bad("token_looking_token_env.toml")
        message = message_at(err, "test_sources[0].token_env")
        self.assertIn("variable name", message)
        self.assertNotIn(pasted, message)
        self.assertNotIn(pasted, str(err))
        self.assertNotIn(pasted, repr(err))

    def test_token_env_must_be_a_string(self):
        text = MINIMAL + '\n[[test_sources]]\nkind = "qase"\ntoken_env = 12\n'
        with self.assertRaises(ConfigError) as caught:
            loads(text, fmt="toml", env={})
        self.assertIn("test_sources[0].token_env", paths(caught.exception))

    def test_a_good_token_env_name_is_kept(self):
        text = MINIMAL + '\n[[test_sources]]\nkind = "qase"\ntoken_env = "QASE_API_TOKEN"\n'
        ws = loads(text, fmt="toml", env={})
        self.assertEqual(ws.test_sources[0].options["token_env"], "QASE_API_TOKEN")

    def test_no_repos_is_an_error(self):
        with self.assertRaises(ConfigError) as caught:
            loads(HEAD, fmt="toml", env={})
        self.assertIn("repos", paths(caught.exception))

    def test_missing_workspace_name(self):
        with self.assertRaises(ConfigError) as caught:
            loads(WEB, fmt="toml", env={})
        self.assertIn("workspace", paths(caught.exception))

    def test_repo_requires_host_language_and_roles(self):
        with self.assertRaises(ConfigError) as caught:
            loads(HEAD + '[repos."acme/web"]\n', fmt="toml", env={})
        self.assertEqual(
            paths(caught.exception),
            ['repos."acme/web".host', 'repos."acme/web".language', 'repos."acme/web".roles'],
        )

    def test_unknown_host_and_language_list_the_allowed_values(self):
        err = bad("several_problems.toml")
        host = message_at(err, 'repos."acme/web".host')
        for allowed in ("github", "gitlab", "bitbucket", "git"):
            self.assertIn(allowed, host)
        language = message_at(err, 'repos."acme/web".language')
        for allowed in ("typescript", "javascript", "python"):
            self.assertIn(allowed, language)

    def test_primary_branch(self):
        def primary(branches):
            line = "" if branches is None else f"branches = {json.dumps(branches)}\n"
            return loads(MINIMAL + line, fmt="toml", env={}).repos["acme/web"]

        self.assertEqual(primary(None).primary_branch, "main")
        self.assertEqual(primary(None).branches, ("main",))
        self.assertEqual(primary(["release/*", "develop", "main"]).primary_branch, "develop")
        self.assertEqual(primary(["release/*", "hotfix/*"]).primary_branch, "main")
        self.assertEqual(primary(["trunk"]).primary_branch, "trunk")
        explicit = loads(MINIMAL + 'branches = ["main", "trunk"]\nprimary_branch = "trunk"\n', fmt="toml", env={})
        self.assertEqual(explicit.repos["acme/web"].primary_branch, "trunk")
        with self.assertRaises(ConfigError) as caught:
            loads(MINIMAL + 'primary_branch = "release/*"\n', fmt="toml", env={})
        self.assertIn('repos."acme/web".primary_branch', paths(caught.exception))

    def test_github_may_override_the_remote_and_git_takes_a_url_or_a_path(self):
        ws = loads(
            HEAD
            + WEB
            + 'remote = "https://git.example.invalid/acme/web.git"\n'
            + '[repos."acme/api"]\nhost = "git"\nlanguage = "python"\nroles = ["backend"]\nremote = "git@git.example.invalid:acme/api.git"\n'
            + '[repos."acme/lib"]\nhost = "git"\nlanguage = "python"\nroles = ["library"]\nremote = "/srv/git/lib"\n',
            fmt="toml",
            env={},
        )
        self.assertEqual(ws.repos["acme/web"].remote, "https://git.example.invalid/acme/web.git")
        self.assertEqual(ws.repos["acme/api"].remote, "git@git.example.invalid:acme/api.git")
        self.assertEqual(ws.repos["acme/lib"].remote, "/srv/git/lib")


class Edges(unittest.TestCase):
    def test_package_edge_needs_manifest_pin_pattern_and_import_marker(self):
        text = HEAD + CONTRACTS + API + '[[edges]]\nkind = "package"\nproducer = "acme/contracts"\nconsumer = "acme/api"\n'
        with self.assertRaises(ConfigError) as caught:
            loads(text, fmt="toml", env={})
        self.assertEqual(paths(caught.exception), ["edges[0].manifest", "edges[0].pin_pattern", "edges[0].import_marker"])

    def test_package_edge_rejects_from_and_to(self):
        text = HEAD + CONTRACTS + API + '[[edges]]\nkind = "package"\nfrom = "acme/contracts"\nto = "acme/api"\n'
        text += 'manifest = "m"\npin_pattern = "(x)"\nimport_marker = "x"\n'
        with self.assertRaises(ConfigError) as caught:
            loads(text, fmt="toml", env={})
        self.assertIn("producer", message_at(caught.exception, "edges[0].from"))
        self.assertIn("consumer", message_at(caught.exception, "edges[0].to"))

    def test_http_edge_rejects_producer_and_consumer(self):
        text = HEAD + WEB + API + '[[edges]]\nkind = "http"\nproducer = "acme/web"\nconsumer = "acme/api"\n'
        with self.assertRaises(ConfigError) as caught:
            loads(text, fmt="toml", env={})
        self.assertIn("from", message_at(caught.exception, "edges[0].producer"))
        self.assertIn("to", message_at(caught.exception, "edges[0].consumer"))

    def test_contract_edge_takes_from_to_or_producer_consumer(self):
        base = HEAD + CONTRACTS + API
        a = loads(base + '[[edges]]\nkind = "contract"\nfrom = "acme/contracts"\nto = "acme/api"\n', fmt="toml", env={})
        b = loads(base + '[[edges]]\nkind = "contract"\nproducer = "acme/contracts"\nconsumer = "acme/api"\n', fmt="toml", env={})
        self.assertEqual(a.edges, b.edges)
        self.assertEqual((a.edges[0].producer, a.edges[0].consumer), ("acme/contracts", "acme/api"))
        with self.assertRaises(ConfigError) as caught:
            loads(base + '[[edges]]\nkind = "contract"\nfrom = "acme/contracts"\nconsumer = "acme/api"\n', fmt="toml", env={})
        self.assertIn("edges[0]", paths(caught.exception))

    def test_library_edge_and_missing_endpoints(self):
        ws = loads(HEAD + API + '[[edges]]\nkind = "library"\nfrom = "acme/api"\nto = "acme/api"\n', fmt="toml", env={})
        self.assertEqual(ws.edges[0].kind, "library")
        with self.assertRaises(ConfigError) as caught:
            loads(HEAD + API + '[[edges]]\nkind = "http"\n', fmt="toml", env={})
        self.assertEqual(paths(caught.exception), ["edges[0].from", "edges[0].to"])

    def test_edge_without_kind_and_edges_not_a_list(self):
        with self.assertRaises(ConfigError) as caught:
            loads(HEAD + API + '[[edges]]\nfrom = "acme/api"\n', fmt="toml", env={})
        self.assertIn("edges[0].kind", paths(caught.exception))
        with self.assertRaises(ConfigError) as caught:
            loads(HEAD + 'edges = "http"\n' + API, fmt="toml", env={})
        self.assertIn("edges", paths(caught.exception))


class EdgeKeys(unittest.TestCase):
    def edge(self, kind, body, extra=""):
        return HEAD + WEB + API + extra + f'[[edges]]\nkind = "{kind}"\n{body}'

    def problems_of(self, text):
        with self.assertRaises(ConfigError) as caught:
            loads(text, fmt="toml", env={})
        return caught.exception

    def test_a_misspelt_key_is_rejected_with_a_hint(self):
        err = self.problems_of(self.edge("http", 'frm = "acme/web"\nto = "acme/api"\n'))
        self.assertIn("did you mean 'from'", message_at(err, "edges[0].frm"))
        self.assertIn("edges[0].from", paths(err))  # and the real key is still missing

    def test_a_key_that_belongs_to_another_kind_is_rejected(self):
        for kind, body, key in (
            ("http", 'from = "acme/web"\nto = "acme/api"\npin_pattern = "(x"\n', "pin_pattern"),
            ("http", 'from = "acme/web"\nto = "acme/api"\nmanifest = "m"\n', "manifest"),
            ("contract", 'from = "acme/web"\nto = "acme/api"\ntopics_glob = "*"\n', "topics_glob"),
            ("library", 'from = "acme/web"\nto = "acme/api"\nimport_marker = "x"\n', "import_marker"),
            ("event", 'from = "acme/web"\nto = "acme/api"\ntopic = "t"\n', "topic"),
        ):
            with self.subTest(kind=kind, key=key):
                err = self.problems_of(self.edge(kind, body))
                self.assertIn("unknown key", message_at(err, f"edges[0].{key}"))
                self.assertEqual(len(err.problems), 1)

    def test_the_keys_each_kind_takes_are_accepted(self):
        text = self.edge("event", 'from = "acme/web"\nto = "acme/api"\ntopics_glob = "**/topics.py"\n')
        self.assertEqual(loads(text, fmt="toml", env={}).edges[0].options["topics_glob"], "**/topics.py")
        err = self.problems_of(self.edge("event", 'from = "acme/web"\nto = "acme/api"\ntopics_glob = 3\n'))
        self.assertIn("edges[0].topics_glob", paths(err))

    def test_an_adapter_can_register_edge_keys(self):
        text = self.edge("event", 'from = "acme/web"\nto = "acme/api"\nqueue = "q"\n')
        self.problems_of(text)
        with mock.patch.dict(config._EDGE_OPTION_KEYS):
            config.register_edge_keys("event", "queue")
            self.assertEqual(loads(text, fmt="toml", env={}).edges[0].options["queue"], "q")
        with self.assertRaises(ValueError):
            config.register_edge_keys("grpc", "x")


class StricterRules(unittest.TestCase):
    def problems_of(self, text):
        with self.assertRaises(ConfigError) as caught:
            loads(text, fmt="toml", env={})
        return caught.exception

    def test_duplicate_edges_are_rejected(self):
        one = '[[edges]]\nkind = "http"\nfrom = "acme/web"\nto = "acme/api"\n'
        err = self.problems_of(HEAD + WEB + API + one + one)
        self.assertIn("edges[0]", message_at(err, "edges[1]"))
        self.assertEqual(paths(err), ["edges[1]"])

    def test_two_package_edges_with_different_manifests_are_not_duplicates(self):
        def pkg(manifest):
            return ('[[edges]]\nkind = "package"\nproducer = "acme/contracts"\nconsumer = "acme/api"\n'
                    f'manifest = "{manifest}"\npin_pattern = "(x)"\nimport_marker = "x"\n')
        ws = loads(HEAD + CONTRACTS + API + pkg("requirements.txt") + pkg("package.json"), fmt="toml", env={})
        self.assertEqual(len(ws.edges), 2)

    def test_self_edges_are_rejected_except_for_library(self):
        for kind, extra in (("http", ""), ("event", ""), ("contract", ""),
                            ("package", 'manifest = "m"\npin_pattern = "(x)"\nimport_marker = "x"\n')):
            with self.subTest(kind=kind):
                ends = 'producer = "acme/api"\nconsumer = "acme/api"\n' if kind == "package" else 'from = "acme/api"\nto = "acme/api"\n'
                err = self.problems_of(HEAD + API + f'[[edges]]\nkind = "{kind}"\n{ends}{extra}')
                self.assertEqual(len(err.problems), 1)
                self.assertIn("itself", err.problems[0].message)
        ws = loads(HEAD + API + '[[edges]]\nkind = "library"\nfrom = "acme/api"\nto = "acme/api"\n', fmt="toml", env={})
        self.assertEqual(ws.edges[0].kind, "library")

    def test_empty_branches_are_rejected(self):
        err = self.problems_of(MINIMAL + "branches = []\n")
        self.assertIn("empty", message_at(err, 'repos."acme/web".branches'))

    def test_duplicate_roles_and_branches_are_rejected(self):
        err = self.problems_of(MINIMAL + 'branches = ["main", "dev", "main"]\n')
        self.assertIn("duplicate", message_at(err, 'repos."acme/web".branches[2]'))
        err = self.problems_of(HEAD + '[repos."a"]\nhost = "github"\nlanguage = "python"\nroles = ["backend", "backend"]\n')
        self.assertIn("duplicate", message_at(err, "repos.a.roles[1]"))

    def test_an_empty_repo_name_is_rejected(self):
        err = self.problems_of(HEAD + '[repos.""]\nhost = "github"\nlanguage = "python"\nroles = ["backend"]\n')
        self.assertEqual(paths(err), ['repos.""'])

    def test_primary_branch_must_be_one_of_the_branches(self):
        err = self.problems_of(MINIMAL + 'branches = ["main", "release/*"]\nprimary_branch = "trunk"\n')
        self.assertIn("branches", message_at(err, 'repos."acme/web".primary_branch'))
        for primary in ("main", "release/1.x"):
            ws = loads(MINIMAL + f'branches = ["main", "release/*"]\nprimary_branch = "{primary}"\n', fmt="toml", env={})
            self.assertEqual(ws.repos["acme/web"].primary_branch, primary)
        err = self.problems_of(MINIMAL + 'primary_branch = "trunk"\n')  # branches default to main
        self.assertIn('repos."acme/web".primary_branch', paths(err))

    def test_ticket_pattern_needs_exactly_one_group(self):
        for pattern in (r"[A-Z]+-\d+", r"([A-Z]+)-(\d+)"):
            with self.subTest(pattern=pattern):
                err = self.problems_of(MINIMAL.replace(HEAD, HEAD + f"ticket_pattern = '{pattern}'\n"))
                self.assertIn("exactly one group", message_at(err, "ticket_pattern"))
        ws = loads(MINIMAL.replace(HEAD, HEAD + "ticket_pattern = '([A-Z]+-\\d+)'\n"), fmt="toml", env={})
        self.assertEqual(ws.ticket_pattern.groups, 1)


class SeveralProblems(unittest.TestCase):
    def test_every_problem_is_reported_with_its_key_path(self):
        err = bad("several_problems.toml")
        self.assertEqual(
            paths(err),
            [
                "test_sources[0].token_env",  # token_env is checked wherever it appears, while the file is read
                "colour",
                "ticket_pattern",
                "limits.depth",
                "limits.hops",
                'repos."acme/web".host',
                'repos."acme/web".language',
                'repos."acme/web".roles[1]',
                'repos."acme/api".remote',
                'repos."acme/api".entry_points.kind',
                "edges[0].to",
                "edges[1].pin_pattern",
            ],
        )
        text = str(err)
        for path in paths(err):
            self.assertIn(path, text)
        self.assertIn("12 problems", text)

    def test_the_message_names_the_source(self):
        self.assertIn("several_problems.toml", str(bad("several_problems.toml")))
        with self.assertRaises(ConfigError) as caught:
            loads("workspace = 3\n", fmt="toml", env={}, source="inline.toml")
        self.assertIn("inline.toml", str(caught.exception))
        self.assertEqual(caught.exception.source, "inline.toml")

    def test_one_problem_reads_as_one(self):
        self.assertIn("1 problem", str(bad("unknown_role.toml")))
        self.assertNotIn("1 problems", str(bad("unknown_role.toml")))


class Expansion(unittest.TestCase):
    def marker(self, value, env):
        text = MINIMAL + f"service_marker = '{value}'\n"
        return loads(text, fmt="toml", env=env).repos["acme/web"].service_marker

    def test_set_variable_expands_inside_a_string(self):
        self.assertEqual(self.marker("pre-${A}-${B}", {"A": "1", "B": "2"}), "pre-1-2")

    def assert_unset(self, value, env, variable):
        text = MINIMAL + f"service_marker = '{value}'\n"
        with self.assertRaises(ConfigError) as caught:
            loads(text, fmt="toml", env=env)
        message = message_at(caught.exception, 'repos."acme/web".service_marker')
        self.assertIn(variable, message)
        self.assertEqual(len(caught.exception.problems), 1)

    def test_an_unset_variable_under_repos_is_an_error_naming_it(self):
        self.assert_unset("${NOPE}", {}, "NOPE")
        self.assert_unset("pre-${NOPE}", {}, "NOPE")

    def test_an_empty_variable_is_an_error_too(self):
        self.assert_unset("${EMPTY}", {"EMPTY": ""}, "EMPTY")

    def test_one_unset_among_several_names_the_unset_one(self):
        self.assert_unset("${A}/${NOPE}", {"A": "1"}, "NOPE")

    def test_unset_variables_are_errors_everywhere_outside_the_two_optional_sources(self):
        cases = {
            'repos."acme/web".tests.markers[0]': MINIMAL + "tests = { markers = ['${V}'] }\n",
            'repos."acme/web".http_client.path': MINIMAL + "http_client = { kind = 'endpoints-file', path = '${V}' }\n",
            'repos."acme/web".primary_branch': MINIMAL + "primary_branch = '${V}'\n",
            'repos."acme/web".remote': MINIMAL + "remote = '${V}'\n",
            'repos."acme/web".branches[0]': MINIMAL + "branches = ['${V}']\n",
            'repos."acme/web".library_roots[0]': MINIMAL + "library_roots = ['${V}']\n",
            "edges[0].topics_glob": HEAD + WEB + API + '[[edges]]\nkind = "event"\nfrom = "acme/web"\nto = "acme/api"\ntopics_glob = "${V}"\n',
            "edges[0].from": HEAD + WEB + API + '[[edges]]\nkind = "http"\nfrom = "${V}"\nto = "acme/api"\n',
            "workspace": 'workspace = "${V}"\n' + WEB,
            "ticket_pattern": MINIMAL.replace(HEAD, HEAD + "ticket_pattern = '${V}'\n"),
            "limits.hops": MINIMAL.replace(HEAD, HEAD + "[limits]\nhops = '${V}'\n\n"),
        }
        for path, text in cases.items():
            with self.subTest(path=path):
                with self.assertRaises(ConfigError) as caught:
                    loads(text, fmt="toml", env={})
                self.assertIn(path, paths(caught.exception))
                self.assertIn("V", message_at(caught.exception, path))
                self.assertEqual(len(caught.exception.problems), 1, caught.exception.problems)

    def test_dollar_dollar_brace_is_a_literal_brace(self):
        self.assertEqual(self.marker("$${HOME}", {"HOME": "/root"}), "${HOME}")
        self.assertEqual(self.marker("a$${b}c", {}), "a${b}c")

    def test_a_dollar_that_is_not_a_reference_is_kept(self):
        self.assertEqual(self.marker("cost$", {}), "cost$")
        self.assertEqual(self.marker("$HOME", {"HOME": "/root"}), "$HOME")

    def test_expanded_text_is_not_expanded_again(self):
        self.assertEqual(self.marker("${A}", {"A": "${B}", "B": "x"}), "${B}")

    def test_malformed_reference_is_an_error(self):
        for value in ("${", "${1X}", "${A-B}", "${}"):
            with self.subTest(value=value):
                text = MINIMAL + f"service_marker = '{value}'\n"
                with self.assertRaises(ConfigError) as caught:
                    loads(text, fmt="toml", env={"A": "x"})
                self.assertIn('repos."acme/web".service_marker', paths(caught.exception))

    def test_only_string_values_expand(self):
        text = MINIMAL + "[limits]\nhops = 2\n"
        self.assertEqual(loads(text, fmt="toml", env={}).limits.hops, 2)
        ws = loads(MINIMAL + "branches = ['${A}', 'b']\n", fmt="toml", env={"A": "a"})
        self.assertEqual(ws.repos["acme/web"].branches, ("a", "b"))

    def test_expansion_reaches_sub_table_options(self):
        text = MINIMAL + "http_client = { kind = 'endpoints-file', path = '${P}' }\n"
        self.assertEqual(loads(text, fmt="toml", env={"P": "x.ts"}).repos["acme/web"].http_client["path"], "x.ts")

    def test_token_env_is_a_name_not_a_template_and_is_never_expanded(self):
        text = MINIMAL + '\n[[test_sources]]\nkind = "qase"\ntoken_env = "${QASE_VAR}"\n'
        with self.assertRaises(ConfigError) as caught:
            loads(text, fmt="toml", env={"QASE_VAR": "ANYTHING"})
        self.assertEqual(paths(caught.exception), ["test_sources[0].token_env"])
        self.assertNotIn("ANYTHING", str(caught.exception))
        self.assertIn("template", message_at(caught.exception, "test_sources[0].token_env"))

    def test_token_env_is_checked_wherever_it_appears(self):
        areas = '\n[[test_sources]]\nkind = "qase"\n\n[[test_sources.areas]]\nname = "A"\ntoken_env = "not a name"\n'
        edge = HEAD + WEB + API + '[[edges]]\nkind = "http"\nfrom = "acme/web"\nto = "acme/api"\ntoken_env = "not a name"\n'
        sub = MINIMAL + "http_client = { kind = 'endpoints-file', token_env = 'not a name' }\n"
        for path, text in {
            "test_sources[0].areas[0].token_env": MINIMAL + areas,
            "edges[0].token_env": edge,
            'repos."acme/web".http_client.token_env': sub,
            "context[0].token_env": MINIMAL + '\n[[context]]\nkind = "jira-snapshot"\ntoken_env = "not a name"\n',
        }.items():
            with self.subTest(path=path):
                with self.assertRaises(ConfigError) as caught:
                    loads(text, fmt="toml", env={})
                self.assertIn(path, paths(caught.exception))

    def test_an_unconfigured_source_is_kept_not_configured(self):
        text = MINIMAL + '\n[[test_sources]]\nkind = "qase"\nproject = "${QASE_PROJECT}"\n'
        text += '\n[[context]]\nkind = "jira-snapshot"\npath = "${SNAP}"\n'
        ws = loads(text, fmt="toml", env={})
        self.assertFalse(ws.test_sources[0].configured)
        self.assertEqual(ws.test_sources[0].unset_vars, ("QASE_PROJECT",))
        self.assertIsNone(ws.test_sources[0].options["project"])
        self.assertFalse(ws.context[0].configured)
        self.assertEqual(ws.context[0].unset_vars, ("SNAP",))
        ws = loads(text, fmt="toml", env={"QASE_PROJECT": "ACME", "SNAP": "/s.json"})
        self.assertTrue(ws.test_sources[0].configured)
        self.assertEqual(ws.test_sources[0].unset_vars, ())
        self.assertTrue(ws.context[0].configured)

    def test_an_unset_variable_in_an_area_makes_the_source_not_configured(self):
        text = MINIMAL + '\n[[test_sources]]\nkind = "qase"\nproject = "ACME"\n'
        text += '\n[[test_sources.areas]]\nname = "Billing"\nroute_prefixes = ["${PREFIX}"]\nsuites = [1]\n'
        text += '\n[[test_sources.areas]]\nname = "${AREA_NAME}"\n'
        ws = loads(text, fmt="toml", env={})
        (qase,) = ws.test_sources
        self.assertFalse(qase.configured)
        self.assertEqual(qase.unset_vars, ("AREA_NAME", "PREFIX"))
        self.assertEqual(qase.areas[0]["route_prefixes"], (None,))
        ok = loads(text, fmt="toml", env={"PREFIX": "/b", "AREA_NAME": "N"})
        self.assertTrue(ok.test_sources[0].configured)

    def test_unset_remote_for_a_git_repo_names_the_variable(self):
        text = HEAD + '[repos."acme/web"]\nhost = "git"\nlanguage = "python"\nroles = ["backend"]\nremote = "${WEB_REMOTE}"\n'
        with self.assertRaises(ConfigError) as caught:
            loads(text, fmt="toml", env={})
        message = message_at(caught.exception, 'repos."acme/web".remote')
        self.assertIn("WEB_REMOTE", message)
        self.assertIn("not configured", message)
        self.assertEqual(len(caught.exception.problems), 1)

    def test_env_defaults_to_the_process_environment(self):
        text = MINIMAL + "service_marker = '${RM_TEST_MARKER}'\n"
        with mock.patch.dict(os.environ, {"RM_TEST_MARKER": "from-os"}):
            self.assertEqual(loads(text, fmt="toml").repos["acme/web"].service_marker, "from-os")

    def test_a_credential_variable_is_refused_and_never_expanded(self):
        env = {"GITLAB_TOKEN": SECRET}
        err = bad("credential_variable.toml", env)
        message = message_at(err, 'repos."acme/web".remote')
        self.assertIn("GITLAB_TOKEN", message)
        self.assertIn("token_env", message)
        self.assertNotIn(SECRET, str(err))
        self.assertNotIn(SECRET, repr(err))


class MachinePaths(unittest.TestCase):
    def remote(self, value, base_dir=None):
        text = HEAD + f'[repos."a"]\nhost = "git"\nlanguage = "python"\nroles = ["backend"]\nremote = "{value}"\n'
        return loads(text, fmt="toml", env={}, base_dir=base_dir).repos["a"].remote

    def test_relative_remote_resolves_against_the_base_directory(self):
        self.assertEqual(self.remote("../repos/web", Path("/work/cfg")), "/work/repos/web")
        self.assertEqual(self.remote("web", Path("/work/cfg")), "/work/cfg/web")

    def test_absolute_paths_and_urls_are_untouched(self):
        base = Path("/work/cfg")
        self.assertEqual(self.remote("/srv/git/web", base), "/srv/git/web")
        self.assertEqual(self.remote("https://git.example.invalid/a/web.git", base), "https://git.example.invalid/a/web.git")
        self.assertEqual(self.remote("ssh://git@git.example.invalid/a/web.git", base), "ssh://git@git.example.invalid/a/web.git")
        self.assertEqual(self.remote("git@git.example.invalid:a/web.git", base), "git@git.example.invalid:a/web.git")

    def test_load_resolves_against_the_config_file_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub = Path(tmp) / "cfg"
            sub.mkdir()
            path = sub / "reachmap.toml"
            path.write_text(
                HEAD + '[repos."a"]\nhost = "git"\nlanguage = "python"\nroles = ["backend"]\nremote = "../web"\n'
                '\n[[context]]\nkind = "jira-snapshot"\npath = "snap.json"\n',
                encoding="utf-8",
            )
            ws = load(path, env={})
            self.assertEqual(ws.repos["a"].remote, os.path.join(tmp, "web"))
            self.assertEqual(ws.context[0].options["path"], os.path.join(tmp, "cfg", "snap.json"))

    def test_relative_load_path_is_made_absolute(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "r.toml").write_text(
                HEAD + '[repos."a"]\nhost = "git"\nlanguage = "python"\nroles = ["backend"]\nremote = "web"\n', encoding="utf-8"
            )
            old = os.getcwd()
            os.chdir(tmp)
            try:
                ws = load("r.toml", env={})
                expected = os.path.join(os.getcwd(), "web")
            finally:
                os.chdir(old)
            self.assertEqual(ws.repos["a"].remote, expected)
            self.assertTrue(os.path.isabs(ws.repos["a"].remote))

    def test_paths_inside_a_repository_stay_relative(self):
        text = MINIMAL + "http_client = { kind = 'endpoints-file', path = 'src/api/endpoints.ts' }\n"
        ws = loads(text, fmt="toml", env={}, base_dir=Path("/work/cfg"))
        self.assertEqual(ws.repos["acme/web"].http_client["path"], "src/api/endpoints.ts")


class Remotes(unittest.TestCase):
    def load_remote(self, value, env=None, host="git"):
        text = HEAD + f'[repos."a"]\nhost = "{host}"\nlanguage = "python"\nroles = ["backend"]\nremote = \'{value}\'\n'
        return loads(text, fmt="toml", env=env or {}, base_dir=Path("/work/cfg")).repos["a"].remote

    def refused(self, value, env=None):
        with self.assertRaises(ConfigError) as caught:
            self.load_remote(value, env)
        error = caught.exception
        self.assertEqual(paths(error), ["repos.a.remote"])
        return error

    def test_a_user_name_in_an_http_url_is_refused_literal_and_via_a_variable(self):
        for url in ("https://abcd1234token@github.com/a/b", "http://x@host/a/b", "git://x@host/a/b.git"):
            with self.subTest(url=url):
                error = self.refused(url)
                self.assertIn("user", message_at(error, "repos.a.remote"))
                self.assertNotIn(url.split("//")[1].split("@")[0], str(error))
        # a token in the user name position through a variable whose name gives nothing away
        error = self.refused("https://${BUILD_USER}@github.com/a/b", {"BUILD_USER": SECRET})
        self.assertNotIn(SECRET, str(error))
        self.assertNotIn(SECRET, repr(error))

    def test_a_credential_named_variable_is_refused_before_it_is_expanded(self):
        error = self.refused("https://${GH_PAT}@github.com/a/b", {"GH_PAT": SECRET})
        self.assertIn("GH_PAT", str(error))
        self.assertNotIn(SECRET, str(error))

    def test_a_password_is_refused_in_every_form(self):
        for url in ("https://u:pw1234@host/a", "ssh://git:pw1234@host/a", "user:pw1234@host:a/b.git"):
            with self.subTest(url=url):
                self.assertNotIn("pw1234", str(self.refused(url)))

    def test_a_bare_user_is_fine_for_ssh_and_scp_style(self):
        for url in ("git@github.com:a/b.git", "ssh://git@github.com/a/b.git", "ssh://git@host:2222/a/b.git",
                    "git+ssh://git@host/a/b.git"):
            with self.subTest(url=url):
                self.assertEqual(self.load_remote(url), url)

    def test_a_url_is_never_rewritten(self):
        for url in ("https://git.example.invalid/a/../b.git", "file:///srv/git/a", "git://host/a/b.git"):
            self.assertEqual(self.load_remote(url), url)

    def test_scp_style_without_a_user_is_a_remote_not_a_relative_path(self):
        self.assertEqual(self.load_remote("gitserver:repos/web.git"), "gitserver:repos/web.git")
        self.assertEqual(self.load_remote("git@gitserver:repos/web.git"), "git@gitserver:repos/web.git")

    def test_relative_and_absolute_paths_still_resolve_or_stay(self):
        self.assertEqual(self.load_remote("../web"), "/work/web")
        self.assertEqual(self.load_remote("./web"), "/work/cfg/web")
        self.assertEqual(self.load_remote("/srv/web"), "/srv/web")

    def test_an_invalid_url_is_a_problem_without_an_echo(self):
        error = self.refused("https://[bad-host/a")
        self.assertNotIn("bad-host", str(error))


class MachinePathsByKind(unittest.TestCase):
    CONTEXT = '\n[[context]]\nkind = "jira-snapshot"\npath = "%s"\n'

    def context_path(self, value):
        ws = loads(MINIMAL + self.CONTEXT % value, fmt="toml", env={}, base_dir=Path("/work/cfg"))
        return ws.context[0].options["path"]

    def test_the_kind_declares_which_keys_are_machine_paths(self):
        self.assertEqual(self.context_path("snap/jira.json"), "/work/cfg/snap/jira.json")
        self.assertEqual(self.context_path("/abs/jira.json"), "/abs/jira.json")

    def test_a_url_valued_path_is_not_rewritten(self):
        self.assertEqual(self.context_path("https://files.example.invalid/jira.json"), "https://files.example.invalid/jira.json")

    def test_a_key_called_path_that_the_kind_does_not_declare_stays_as_written(self):
        text = MINIMAL + '\n[[test_sources]]\nkind = "export-source"\npath = "reports/x.json"\n'  # (qase refuses keys it does not know, M15)
        with mock.patch.dict(config._SECTIONS["test_sources"].kinds):
            config.register_kind("test_sources", "export-source")
            ws = loads(text, fmt="toml", env={}, base_dir=Path("/work/cfg"))
        self.assertEqual(ws.test_sources[0].options["path"], "reports/x.json")

    def test_an_adapter_declares_its_own_machine_path_keys(self):
        text = MINIMAL + '\n[[context]]\nkind = "export-dir"\nfolder = "exports"\npath = "kept"\n'
        with mock.patch.dict(config._SECTIONS["context"].kinds):
            config.register_kind("context", "export-dir", machine_paths=("folder",))
            ws = loads(text, fmt="toml", env={}, base_dir=Path("/work/cfg"))
        self.assertEqual(ws.context[0].options["folder"], "/work/cfg/exports")
        self.assertEqual(ws.context[0].options["path"], "kept")


class Registries(unittest.TestCase):
    def test_a_registered_entry_point_kind_becomes_valid(self):
        text = MINIMAL + "entry_points = { kind = 'flask' }\n"
        with self.assertRaises(ConfigError):
            loads(text, fmt="toml", env={})
        with mock.patch.dict(config._SECTIONS["entry_points"].kinds):
            config.register_kind("entry_points", "flask")
            ws = loads(text, fmt="toml", env={})
        self.assertEqual(ws.repos["acme/web"].entry_points["kind"], "flask")
        with self.assertRaises(ConfigError):
            loads(text, fmt="toml", env={})

    def test_a_registered_language_becomes_valid(self):
        text = HEAD + '[repos."a"]\nhost = "github"\nlanguage = "ruby"\nroles = ["backend"]\n'
        with self.assertRaises(ConfigError):
            loads(text, fmt="toml", env={})
        with mock.patch.object(config, "_LANGUAGES", set(config._LANGUAGES)):
            config.register_language("ruby")
            self.assertEqual(loads(text, fmt="toml", env={}).repos["a"].language, "ruby")

    def test_an_adapter_option_validator_runs_and_its_messages_are_pathed(self):
        def validator(options, describe):
            return ["routes_glob is required"] if "routes_glob" not in options else []

        text = MINIMAL + "entry_points = { kind = 'react-router' }\n"
        self.assertEqual(loads(text, fmt="toml", env={}).repos["acme/web"].entry_points["kind"], "react-router")
        with mock.patch.dict(config._SECTIONS["entry_points"].kinds):
            config.register_kind("entry_points", "react-router", validator, replace=True)
            with self.assertRaises(ConfigError) as caught:
                loads(text, fmt="toml", env={})
        self.assertIn("routes_glob is required", message_at(caught.exception, 'repos."acme/web".entry_points'))

    def test_a_validator_sees_expanded_values_and_a_describe_that_never_echoes(self):
        seen = {}

        def validator(options, describe):
            seen["glob"] = options["routes_glob"]
            seen["described"] = describe(options["routes_glob"])
            return []

        text = MINIMAL + "entry_points = { kind = 'react-router', routes_glob = '${GLOB}' }\n"
        with mock.patch.dict(config._SECTIONS["entry_points"].kinds):
            config.register_kind("entry_points", "react-router", validator, replace=True)
            loads(text, fmt="toml", env={"GLOB": "src/*/routes.tsx"})
        self.assertEqual(seen, {"glob": "src/*/routes.tsx", "described": "a string"})

    def test_registering_twice_needs_replace(self):
        with mock.patch.dict(config._SECTIONS["entry_points"].kinds):
            with self.assertRaises(ValueError):
                config.register_kind("entry_points", "fastapi")
            with self.assertRaises(ValueError):
                config.register_kind("nonsense", "x")

    def test_unknown_test_source_and_context_kinds(self):
        text = MINIMAL + '\n[[test_sources]]\nkind = "xray"\n\n[[context]]\nkind = "linear"\n'
        with self.assertRaises(ConfigError) as caught:
            loads(text, fmt="toml", env={})
        self.assertIn("qase", message_at(caught.exception, "test_sources[0].kind"))
        self.assertIn("jira-snapshot", message_at(caught.exception, "context[0].kind"))


class NoSecretInTheWorkspace(unittest.TestCase):
    ENV = {"QASE_API_TOKEN": SECRET, "GITLAB_TOKEN": SECRET + "-gl", "ACME_JIRA_SNAPSHOT": "/data/jira.json"}
    VALUES = (SECRET, SECRET + "-gl")

    def test_a_config_that_references_a_token_variable_holds_only_its_name(self):
        ws = load(CONFIGS / "example.toml", env=self.ENV)
        self.assertEqual(ws.test_sources[0].options["token_env"], "QASE_API_TOKEN")
        blobs = [repr(ws), str(ws), repr(ws.test_sources[0]), pickle.dumps(ws).decode("latin-1")]
        for blob in blobs:
            for value in self.VALUES:
                self.assertNotIn(value, blob)
        self.assertIn("QASE_API_TOKEN", blobs[0])

    def test_no_error_message_holds_a_token_value(self):
        for name in sorted(p.name for p in BAD.glob("*.toml")):
            with self.subTest(name=name):
                err = bad(name, self.ENV)
                for value in self.VALUES:
                    self.assertNotIn(value, str(err))
                    self.assertNotIn(value, repr(err))
                    for problem in err.problems:
                        self.assertNotIn(value, problem.message)
                        self.assertNotIn(value, problem.path)

    def test_an_error_next_to_a_token_reference_does_not_leak_it(self):
        text = MINIMAL + 'bogus = 1\n\n[[test_sources]]\nkind = "qase"\ntoken_env = "QASE_API_TOKEN"\n'
        with self.assertRaises(ConfigError) as caught:
            loads(text, fmt="toml", env=self.ENV)
        self.assertNotIn(SECRET, str(caught.exception))

    def test_a_token_looking_value_is_never_echoed_from_any_field(self):
        token = "ghp_" + SECRET
        text = f"""
workspace = "acme"
ticket_pattern = '({token}'

[limits]
hops = "{token}"

[repos."acme/web"]
host = "{token}"
language = "{token}"
roles = ["{token}"]
branches = [5]
primary_branch = "{token}/*"
entry_points = {{ kind = "{token}" }}
http_client = {{ kind = "{token}" }}

[repos."acme/api"]
host = "github"
language = "python"
roles = ["backend"]

[[edges]]
kind = "{token}"

[[edges]]
kind = "http"
from = "{token}"
to = "acme/api"

[[edges]]
kind = "package"
producer = "acme/api"
consumer = "{token}"
manifest = "m"
import_marker = "x"
pin_pattern = '({token}'

[[test_sources]]
kind = "{token}"

[[context]]
kind = "{token}"
"""
        with self.assertRaises(ConfigError) as caught:
            loads(text, fmt="toml", env={})
        self.assertGreaterEqual(len(caught.exception.problems), 14)
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertNotIn(SECRET, repr(caught.exception))

    def test_an_expanded_value_that_is_otherwise_invalid_is_not_echoed(self):
        text = HEAD + '[repos."acme/web"]\nhost = "${H}"\nlanguage = "python"\nroles = ["backend"]\n'
        with self.assertRaises(ConfigError) as caught:
            loads(text, fmt="toml", env={"H": "private-value-xyz"})
        self.assertIn('repos."acme/web".host', paths(caught.exception))
        self.assertNotIn("private-value-xyz", str(caught.exception))


class Loading(unittest.TestCase):
    def test_invalid_toml_is_a_config_error(self):
        with self.assertRaises(ConfigError) as caught:
            loads("workspace = \n", fmt="toml", env={}, source="broken.toml")
        self.assertIn("broken.toml", str(caught.exception))
        self.assertIn("TOML", str(caught.exception))

    def test_missing_file_and_unknown_extension(self):
        with self.assertRaises(ConfigError) as caught:
            load(CONFIGS / "nope.toml", env={})
        self.assertIn("nope.toml", str(caught.exception))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reachmap.yaml"
            path.write_text("x: 1\n", encoding="utf-8")
            with self.assertRaises(ConfigError) as caught:
                load(path, env={})
            self.assertIn(".toml", str(caught.exception))

    def test_file_that_is_not_utf8(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reachmap.toml"
            path.write_bytes(b'workspace = "\xff\xfe"\n')
            with self.assertRaises(ConfigError):
                load(path, env={})

    def test_unknown_format_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            loads("", fmt="yaml", env={})

    def test_a_json_config_is_refused_and_says_to_use_toml(self):
        text = '{"workspace": "acme", "repos": {}}'
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("reachmap.json", "Reachmap.JSON"):
                path = Path(tmp) / name
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(ConfigError) as caught:
                    load(path, env={})
                self.assertEqual(paths(caught.exception), [""])
                self.assertIn(name, str(caught.exception))
                self.assertIn("JSON configs are not supported; use a .toml file", str(caught.exception))
        with self.assertRaises(ConfigError) as caught:
            loads(text, fmt="json", env={}, source="old.json")
        self.assertIn("JSON configs are not supported; use a .toml file", str(caught.exception))

    def test_toml_is_the_default_format_of_loads(self):
        self.assertEqual(loads(MINIMAL, env={}).name, "acme")

    def test_a_json_config_is_refused_without_being_read(self):
        with self.assertRaises(ConfigError) as caught:
            load(CONFIGS / "does-not-exist.json", env={})
        self.assertIn("JSON configs are not supported", str(caught.exception))
        self.assertNotIn("cannot read", str(caught.exception))


def _describe_in_child(workspace, queue):
    queue.put((workspace.name, sorted(workspace.repos), workspace.limits.hops))


class Transport(unittest.TestCase):
    """M12 runs analyses in a spawned process: a Workspace must pickle, copy and hash."""

    def workspaces(self):
        yield load(CONFIGS / "example.toml", env={"QASE_API_TOKEN": SECRET})
        yield load(FIXTURES / "reachmap.toml", env={"REACHMAP_FIXTURES": FIXTURE_ROOT})

    def test_pickle_round_trip(self):
        for ws in self.workspaces():
            for protocol in range(2, pickle.HIGHEST_PROTOCOL + 1):
                with self.subTest(workspace=ws.name, protocol=protocol):
                    again = pickle.loads(pickle.dumps(ws, protocol))
                    self.assertEqual(again, ws)
                    self.assertEqual(again.repos["acme/web"].entry_points["kind"], "react-router")
                    self.assertEqual(again.ticket_pattern, ws.ticket_pattern)

    def test_deepcopy_and_copy(self):
        for ws in self.workspaces():
            self.assertEqual(copy.deepcopy(ws), ws)
            self.assertEqual(copy.copy(ws), ws)

    def test_workspace_is_hashable_and_equal_loads_hash_alike(self):
        a, b = (load(CONFIGS / "example.toml", env={}) for _ in range(2))
        self.assertEqual(hash(a), hash(b))
        self.assertEqual(len({a, b}), 1)

    def test_frozen_map_is_a_read_only_mapping(self):
        m = config.FrozenMap({"a": 1, "b": (2,)})
        self.assertEqual((m["a"], len(m), sorted(m)), (1, 2, ["a", "b"]))
        self.assertEqual(m, {"a": 1, "b": (2,)})
        self.assertEqual(pickle.loads(pickle.dumps(m)), m)
        with self.assertRaises(TypeError):
            m["c"] = 3
        with self.assertRaises(AttributeError):
            m._data = {}

    def test_a_spawned_process_receives_a_workspace(self):
        ws = load(FIXTURES / "reachmap.toml", env={"REACHMAP_FIXTURES": FIXTURE_ROOT})
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        child = context.Process(target=_describe_in_child, args=(ws, queue))
        child.start()
        try:
            result = queue.get(timeout=60)
        finally:
            child.join(60)
        self.assertEqual(child.exitcode, 0)
        self.assertEqual(result, ("acme", sorted(ws.repos), 3))


class SmallRules(unittest.TestCase):
    def test_the_file_suffix_is_case_insensitive(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Reachmap.TOML"
            path.write_text(MINIMAL, encoding="utf-8")
            self.assertEqual(load(path, env={}).name, "acme")

    def test_limit_defaults_come_from_the_dataclass(self):
        names = [f.name for f in dataclasses.fields(Limits)]
        self.assertEqual(list(config._LIMIT_DEFAULTS), names)
        self.assertEqual(config._LIMIT_DEFAULTS, dataclasses.asdict(Limits()))

    def test_parse_errors_leave_no_original_exception_holding_the_file(self):
        secret_text = f'workspace = "{SECRET}\n'  # unterminated string
        cases = [
            lambda: loads(secret_text, fmt="toml", env={}),
            lambda: load(CONFIGS / "missing.toml", env={}),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            bad_bytes = Path(tmp) / "bytes.toml"
            bad_bytes.write_bytes(b'x = "\xff"\n')
            cases.append(lambda: load(bad_bytes, env={}))
        for case in cases:
            with self.assertRaises(ConfigError) as caught:
                case()
            error = caught.exception
            self.assertIsNone(error.__context__)
            self.assertIsNone(error.__cause__)
            self.assertNotIn(SECRET, str(error))


if __name__ == "__main__":
    unittest.main()
