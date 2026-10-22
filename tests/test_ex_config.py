"""The `express` and `nestjs` kinds of `entry_points`: known to the config, no option besides `kind`, a message that names the key and never quotes
a value, and the fixture workspace (tests/fixtures/workspaces/express.toml) loads."""

import unittest

from reachmap import config, entrypoints
from tests.fixtures import ex_build

HEAD = 'workspace = "w"\n[repos."t/api"]\nhost = "git"\nremote = "file:///x"\nlanguage = "typescript"\nroles = ["backend"]\n'


def load(entry_points):
    return config.loads(HEAD + f"entry_points = {entry_points}\n", source="inline", fmt="toml", env={})


class Kinds(unittest.TestCase):
    def test_both_kinds_are_known(self):
        for kind in ("express", "nestjs"):
            with self.subTest(kind=kind):
                ws = load(f'{{ kind = "{kind}" }}')
                self.assertEqual(ws.repos["t/api"].entry_points["kind"], kind)

    def test_the_kinds_are_registered_in_the_entry_point_registry(self):
        self.assertIn("express", entrypoints.KINDS)
        self.assertIn("nestjs", entrypoints.KINDS)

    def test_an_option_is_refused_with_its_key_path_and_without_its_value(self):
        for kind in ("express", "nestjs"):
            with self.subTest(kind=kind), self.assertRaises(config.ConfigError) as raised:
                load(f'{{ kind = "{kind}", root = "secret-value" }}')
            text = str(raised.exception)
            self.assertIn("repos.t/api.entry_points: root: not an option", text.replace('"', ""))
            self.assertIn(f"not an option of the {kind} entry points", text)
            self.assertNotIn("secret-value", text)

    def test_every_unknown_key_is_named(self):
        with self.assertRaises(config.ConfigError) as raised:
            load('{ kind = "express", a = 1, b = 2 }')
        text = str(raised.exception)
        self.assertIn("2 problems", text)

    def test_an_unknown_kind_is_refused_and_the_message_lists_the_known_ones(self):
        with self.assertRaises(config.ConfigError) as raised:
            load('{ kind = "expres" }')
        text = str(raised.exception)
        self.assertIn("express", text)

    def test_registering_twice_is_harmless(self):
        from reachmap.adapters import express

        express.register(lambda kind, factory: None)  # the config kinds exist already: the second registration is left alone


class Workspace(unittest.TestCase):
    def test_the_fixture_workspace_loads_with_both_servers_and_both_clients(self):
        ws = config.load(ex_build.WORKSPACE_FILE, env={"REACHMAP_FIXTURES": "file:///fx"})
        self.assertEqual(sorted(ws.repos), ["ex/console", "ex/ledger", "ex/orders", "ex/web"])
        self.assertEqual({n: r.entry_points["kind"] for n, r in ws.repos.items()}, {"ex/console": "react-router", "ex/ledger": "nestjs", "ex/orders": "express", "ex/web": "react-router"})
        self.assertEqual([(e.kind, e.from_repo, e.to_repo) for e in ws.edges], [("http", "ex/web", "ex/orders"), ("http", "ex/console", "ex/ledger")])


if __name__ == "__main__":
    unittest.main()
