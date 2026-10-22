"""M17: the generated config of each fixture workspace analyses its scenarios correctly (the checks of the scenario harness, nothing
skipped), after the documented confirmations only (`support_init.CONFIRMATIONS`: `distinct_max = 3`), and it means what the hand written
fixture config means: the same repositories, roles, kinds, options and edges. Only `tests` differs, by design: the markers of what is
in the repository at its default branch (a fixture config lists `.spec.` for repositories that have no spec file), and a test command
read from package.json or the pytest style.

The repositories are the M1 fixtures (acme), and the M16 ones (Next.js, OpenAPI, Django, Express, NestJS and Spring), given as plain paths
(host `git`), named as their workspace files name them with `NAME=SPEC`.
"""

import dataclasses
import re
import unittest
from pathlib import Path

from reachmap import config
from tests import scenario_support as ss
from tests import support_cli as sc_cli
from tests import support_init as si
from tests import support_git as sg

ROOT = Path(__file__).resolve().parents[1]


class World:
    """One fixture workspace: its repositories, its hand written config, its scenarios, how to run and check one."""

    name = ""
    config_file: Path
    scenario_count = 0

    @classmethod
    def setup(cls):
        raise NotImplementedError

    def check(self, sc, report):
        raise NotImplementedError


class Acme(World):
    name = "acme (M1)"
    scenario_count = 23

    def __init__(self):
        from tests.fixtures import build

        e = ss.env()
        self.env, self.config_file = e, ss.FIXTURES / "reachmap.toml"
        self.scenarios = build.scenarios(e.dest)
        self.args, self.hops_of = sc_cli.scenario_args, sc_cli.scenario_hops

    def check(self, sc, report):
        return ss.check(sc_cli.to_run(sc, report))


class NextJs(World):
    name = "nextjs"
    scenario_count = 18

    def __init__(self):
        from tests import support_nx as w
        from tests.fixtures import nx_build

        self.w, self.env, self.config_file = w, w.env(), nx_build.WORKSPACE_FILE
        self.scenarios, self.args, self.hops_of = w.scenarios(), w.scenario_args, lambda sc: None

    def check(self, sc, report):
        return self.w.check(sc, report)


class Django(World):
    name = "django"
    scenario_count = 14

    def __init__(self):
        from tests import support_dj as w

        self.w, self.env, self.config_file = w, w.env(), ss.FIXTURES / "workspaces" / "django.toml"
        self.scenarios, self.args, self.hops_of = w.scenarios(), w.scenario_args, lambda sc: None

    def check(self, sc, report):
        return self.w.check(sc, report)


class OpenApi(World):
    name = "openapi"
    scenario_count = 10

    def __init__(self):
        from tests import support_openapi_world as w
        from tests.fixtures import build_openapi as bo

        self.w, self.env, self.config_file = w, w.world(), bo.CONFIG_FILE
        self.scenarios, self.args, self.hops_of = list(w.scenarios().values()), sc_cli.scenario_args, lambda sc: None

    def check(self, sc, report):
        return self.w.check(sc, report, reach_incomplete=sc["id"].startswith("oa10"))


class Express(World):
    name = "express"
    scenario_count = 25

    def __init__(self):
        from tests import support_ex as w
        from tests.fixtures import ex_build

        self.w, self.env, self.config_file = w, w.env(), ex_build.WORKSPACE_FILE
        self.scenarios, self.args, self.hops_of = w.scenarios(), w.scenario_args, lambda sc: None

    def check(self, sc, report):
        return self.w.check(sc, report)


class Spring(World):
    name = "spring"
    scenario_count = 0

    def __init__(self):
        from tests import support_sb as w

        self.w, self.env, self.config_file = w, w.env(), ss.FIXTURES / "workspaces" / "spring.toml"
        self.scenarios, self.args, self.hops_of = w.scenarios(), w.scenario_args, lambda sc: None
        type(self).scenario_count = len(self.scenarios)

    def check(self, sc, report):
        return self.w.check(sc, report)


class Case:
    """The generated config of a world: made once, used by the tests of the module that name it."""

    world: World
    out: Path
    rendered: object
    text: str

    @classmethod
    def prepare(cls):
        cls.world = cls.world_class()
        e = cls.world.env
        cls.out = si.temp_dir("reachmap-init-e2e-")
        cls.addClassCleanup(sg.remove_tree, cls.out)
        cls.rendered = si.generate(e.repos, e.home, cls.out / "init-cache", cls.out)
        cls.text = cls.rendered.text


class Meaning:
    """Tests of one world; subclasses set `world_class`."""

    def test_nothing_failed_to_read_and_every_repository_is_written(self):
        self.assertEqual(self.rendered.failures, [])
        self.assertEqual(sorted(self.rendered.names), sorted(self.world.env.repos))

    def test_the_file_loads_before_and_after_the_confirmations(self):
        # the remotes are paths relative to the file, so it is written where the generator was told it would be
        (self.out / "reachmap.toml").write_text(self.text)
        config.load(self.out / "reachmap.toml", env={})
        (self.out / "confirmed.toml").write_text(si.confirm(self.text))
        config.load(self.out / "confirmed.toml", env={})

    def test_the_only_confirmation_is_the_distinct_max_limit(self):
        got = self.loaded()
        want = self.wanted()
        self.assertEqual({k: v for k, v in dataclasses.asdict(got.limits).items() if k != "distinct_max"}, {k: v for k, v in dataclasses.asdict(want.limits).items() if k != "distinct_max"})
        self.assertEqual(got.limits.distinct_max, 10)  # the default: no repository knows what a team calls a generic name
        self.assertEqual(want.limits.distinct_max, 3)
        self.assertEqual(si.confirm(self.text).count("distinct_max = 3"), 1)

    def loaded(self):
        path = self.out / "reachmap.toml"
        path.write_text(self.text)
        return config.load(path, env={})

    def wanted(self):
        return config.load(self.world.config_file, env={"REACHMAP_FIXTURES": "file://" + str(self.world.env.dest)})

    def test_repositories_roles_kinds_and_options_mean_what_the_fixture_config_means(self):
        got, want = self.loaded(), self.wanted()
        self.assertEqual(sorted(got.repos), sorted(want.repos))
        for name, w in want.repos.items():
            g = got.repos[name]
            with self.subTest(repo=name):
                for field in ("host", "language", "roles", "branches", "service_marker", "library_roots", "primary_branch"):
                    self.assertEqual(getattr(g, field), getattr(w, field), field)
                for field in ("aliases", "entry_points", "http_client", "http_server", "contract"):
                    self.assertEqual(si.plain(getattr(g, field)), si.plain(getattr(w, field)), field)
                gm, wm = si.plain(g.tests) or {}, si.plain(w.tests) or {}
                # the markers of what the repository has at its default branch are among those the fixture config lists
                self.assertLessEqual(set(gm.get("markers", ())), set(wm.get("markers", ())))

    def test_the_edges_mean_what_the_fixture_config_means(self):
        got, want = self.loaded(), self.wanted()
        key = lambda e: (e.kind, e.from_repo, e.to_repo, e.manifest, e.import_marker, si.plain(e.options))
        self.assertEqual(sorted(map(key, got.edges)), sorted(map(key, want.edges)))
        self.assertEqual(self.pins(got), self.pins(want))

    def pins(self, ws):
        """What each pin pattern reads out of the manifest it names, at the default branch of the consumer."""
        out = {}
        for e in ws.edges:
            if e.kind == "package":
                text = self.manifest_text(e.to_repo, e.manifest)
                out[(e.from_repo, e.to_repo)] = [m.group(1) for line in text.splitlines() if (m := e.pin_pattern.search(line))]
        return out

    def manifest_text(self, repo, path):
        from tests.fixtures import build
        proc = build.run_git(self.world.env.repos[repo], "show", f"main:{path}")
        assert proc.returncode == 0, proc.stderr
        return proc.stdout

    def test_every_guess_says_why_gives_evidence_and_asks_to_confirm(self):
        guesses = [line for line in self.text.splitlines() if line.startswith("# guess:")]
        self.assertGreater(len(guesses), 5)
        for line in guesses:
            self.assertRegex(line, r"^# guess: .+, confidence (high|medium|low); confirm$")
            if "evidence" in line:
                self.assertRegex(line, r"evidence [^,;]+")

    def test_every_key_but_host_remote_and_branches_has_a_guess_line_directly_above_it(self):
        from tests.test_init_template import GuessLines

        self.assertEqual(GuessLines.missing(self.text), [])

    def test_the_scenarios_pass_with_the_generated_config_after_the_confirmations(self):
        w = self.world
        self.assertEqual(len(w.scenarios), w.scenario_count)
        failures = si.run_scenarios(si.confirm(self.text), self.out, w.scenarios, w.args, w.check, w.env.cache, w.env.home, w.hops_of)
        self.assertEqual(failures, {})


def make(world_class, name):
    class T(Case, Meaning, unittest.TestCase):
        pass

    T.world_class = world_class
    T.__name__ = T.__qualname__ = name

    @classmethod
    def setUpClass(cls):
        cls.prepare()

    T.setUpClass = setUpClass
    return T


AcmeInit = make(Acme, "AcmeInit")
NextJsInit = make(NextJs, "NextJsInit")
DjangoInit = make(Django, "DjangoInit")
OpenApiInit = make(OpenApi, "OpenApiInit")
ExpressInit = make(Express, "ExpressInit")
SpringInit = make(Spring, "SpringInit")


class AcmeSpecifics(unittest.TestCase):
    """What the M1 workspace shows beyond the shared checks: the guesses that only a monorepo and a contract repository make."""

    @classmethod
    def setUpClass(cls):
        cls.out = si.temp_dir("reachmap-init-acme-")
        cls.addClassCleanup(sg.remove_tree, cls.out)
        e = ss.env()
        cls.text = si.generate(e.repos, e.home, cls.out / "c", cls.out).text

    def test_the_stale_jsconfig_is_not_used_and_says_why(self):
        self.assertIn('aliases = { from = "tsconfig.json" }', self.text)
        self.assertRegex(self.text, r"# guess: jsconfig\.json is not used: 3 of its 3 path targets do not exist in the tree, evidence jsconfig\.json:\d+")

    def test_the_library_root_is_found_by_who_imports_it(self):
        self.assertIn('library_roots = ["libs/common"]', self.text)
        self.assertIn('service_marker = "pyproject.toml"', self.text)

    def test_the_event_edge_is_a_low_confidence_guess_that_says_nothing_follows_it(self):
        m = re.search(r"# guess: ([^\n]*), confidence low; confirm\n\[\[edges\]\]\nkind = \"event\"\nfrom = \"acme/api\"\nto = \"acme/worker\"\ntopics_glob = \"\*\*/topics\.py\"", self.text)
        self.assertIsNotNone(m)
        self.assertIn("no adapter follows event edges", m.group(1))

    def test_a_token_is_only_ever_named_and_only_in_a_comment(self):
        self.assertIn('# token_env = "QASE_API_TOKEN"', self.text)
        for line in self.text.splitlines():
            if "token_env" in line:
                self.assertTrue(line.startswith("#"), line)

    def test_the_package_pin_patterns_have_one_group_and_read_the_pin(self):
        from reachmap import config
        (self.out / "reachmap.toml").write_text(self.text)
        ws = config.load(self.out / "reachmap.toml", env={})
        pins = [e for e in ws.edges if e.kind == "package"]
        self.assertEqual(len(pins), 2)
        for e in pins:
            self.assertEqual(e.pin_pattern.groups, 1)
            self.assertEqual(e.pin_pattern.search("acme-contracts==1.10.0").group(1), "1.10.0")
            self.assertIsNone(e.pin_pattern.search("acme-common==1.10.0"))

    def test_the_harness_is_not_vacuous_the_scenarios_that_need_distinct_max_fail_without_it(self):
        from tests.fixtures import build

        e = ss.env()
        wanted = [sc for sc in build.scenarios(e.dest) if sc["id"].split("-")[0] in ("s12", "s19")]
        failures = si.run_scenarios(self.text, self.out, wanted, sc_cli.scenario_args,
                                    lambda sc, report: ss.check(sc_cli.to_run(sc, report)), e.cache, e.home)
        self.assertEqual(sorted(k.split("-")[0] for k in failures), ["s12", "s19"])


if __name__ == "__main__":
    unittest.main()
