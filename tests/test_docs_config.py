"""docs/config.md documents every key the config loader accepts.

The registries of `reachmap.config` are the source of truth: the top-level, repository and limit keys,
the kinds of every section (`entry_points`, `http_client`, `http_server`, `contract`, `test_sources`,
`context`), the edge kinds and their extra keys, the hosts, roles and languages. The option keys of each
registered kind are found by asking its validator, which names its allowed keys in the message it gives
for an unknown key (`allowed keys: a, b`); a kind whose options are not validated is listed in
`NO_OPTIONS` below. Every kind and the two plain tables (`aliases`, `tests`) have a validator, so the keys
are never taken from a list in this file. A kind that an adapter registers later, or a key a validator
learns, fails this test until docs/config.md says what it does.

A name counts as documented when it appears in backticks under the heading of its section
(the heading names the section or the kind in backticks, and a kind's heading sits under its section's).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import reachmap.entrypoints  # noqa: F401  (registers the entry-point kinds of every adapter with the config)
import reachmap.pipeline  # noqa: F401
from reachmap import config, config_sources
from reachmap.adapters import js, python as py_adapter
from reachmap.adapters.react_router import DEFAULT_ROUTES_GLOBS

DOC = Path(__file__).resolve().parent.parent / "docs" / "config.md"

# Keys several kinds share and docs/config.md describes once, under their own heading (below the section's).
SHARED = {("test_sources", "qase"): "Keys of every hosted source", ("test_sources", "testrail"): "Keys of every hosted source"}

# Kinds that take no option besides `kind`: their validator names no key ("there is none besides kind"), and the doc says so. A kind not listed
# here whose validator names no key fails the test, so a kind cannot be added with options that nobody documented.
NO_OPTIONS = {("entry_points", "fastapi"), ("entry_points", "celery"), ("entry_points", "express"), ("entry_points", "nestjs")}

_ALLOWED = re.compile(r"(?:allowed keys|known): (.+?)(?: \(did you mean|\)|$)")
_PROBE = "zz_probe_key"


class Doc:
    """The headings of a Markdown file as a tree, with the text under each."""

    def __init__(self, text: str) -> None:
        self.nodes: list[dict] = []
        stack: list[dict] = []
        in_fence = False
        for line in text.split("\n"):
            if line.startswith("```"):
                in_fence = not in_fence
            match = None if in_fence else re.match(r"^(#{1,6}) (.+)$", line)
            if match:
                level = len(match.group(1))
                while stack and stack[-1]["level"] >= level:
                    stack.pop()
                node = {"level": level, "title": match.group(2), "ancestors": [n["title"] for n in stack], "lines": []}
                self.nodes.append(node)
                stack.append(node)
            elif stack:
                for node in stack:
                    node["lines"].append(line)

    def section(self, name: str, *, under: str | None = None) -> str | None:
        """The text of the heading that names `name` in backticks (or, for plain words, in the heading),
        below a heading that names `under`; None when there is no such heading."""
        for node in self.nodes:
            if not _names(node["title"], name):
                continue
            if under is None or any(_names(a, under) for a in node["ancestors"]):
                return "\n".join(node["lines"])
        return None


def _names(title: str, name: str) -> bool:
    return f"`{name}`" in title or title.lower().startswith(name.lower() + " ") or title.lower() == name.lower()


def _probe(validator) -> tuple[str, ...]:
    """The keys a validator allows, from its message about an unknown key (none when it says there are none)."""
    for message in validator({"kind": "probe", _PROBE: 1}, config._type_name):
        found = _ALLOWED.search(message)
        if found:
            return tuple(key.strip() for key in found.group(1).split(","))
    return ()


def option_keys(section: str, kind: str) -> tuple[str, ...]:
    validator = config._SECTIONS[section].kinds[kind].validator
    assert validator is not None, f"{section} `{kind}` has no option validator"
    return _probe(validator)


def plain_keys(section: str) -> tuple[str, ...]:
    """The keys of a table with no kind (`aliases`, `tests`), from its validator."""
    return _probe(config._SECTIONS[section].plain)


class ConfigDocs(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = DOC.read_text(encoding="utf-8")
        cls.doc = Doc(cls.text)

    def need(self, what: str, names, section: str | None, *, under: str | None = None) -> None:
        body = self.doc.section(section, under=under) if section is not None else self.text
        self.assertIsNotNone(body, f"docs/config.md has no heading for {section!r}" + (f" under {under!r}" if under else ""))
        missing = [n for n in names if f"`{n}`" not in body]
        self.assertEqual(missing, [], f"docs/config.md does not document {what} {missing} in the section {section or 'whole file'}")

    def test_top_level_keys(self) -> None:
        self.need("the top-level keys", config._TOP_KEYS, "Top-level keys")

    def test_limits(self) -> None:
        self.need("the limits", tuple(config._LIMIT_DEFAULTS), "limits")

    def test_repository_keys(self) -> None:
        self.need("the repository keys", config._REPO_KEYS, "Keys of a repository", under="repos")

    def test_hosts_roles_and_languages(self) -> None:
        self.need("the hosts", config.HOSTS, "Keys of a repository", under="repos")
        self.need("the roles", config.ROLES, "Keys of a repository", under="repos")
        self.need("the languages", sorted(config._LANGUAGES), "Keys of a repository", under="repos")

    def test_every_registered_section_has_a_heading(self) -> None:
        for section in config._SECTIONS:
            self.assertIsNotNone(self.doc.section(section), f"docs/config.md has no heading for the section {section!r}")

    def test_every_registered_kind_has_a_heading_with_its_keys(self) -> None:
        for section, spec in config._SECTIONS.items():
            for kind in spec.kinds:
                body = self.doc.section(kind, under=section)
                self.assertIsNotNone(body, f"docs/config.md has no heading for the {section} kind `{kind}` under the heading of {section}")
                keys = option_keys(section, kind)
                self.assertTrue(keys or (section, kind) in NO_OPTIONS,
                                f"the {section} kind `{kind}` names no option key: say its keys in its validator's message "
                                "(`allowed keys: a, b`), or put the kind in NO_OPTIONS if it takes none")
                shared = SHARED.get((section, kind))
                if shared is not None:  # the shared keys are checked in their own section, the kind's own keys in the kind's
                    common = self.doc.section(shared, under=section) or ""
                    own = [k for k in keys if f"`{k}`" not in common]
                    self.need(f"the shared keys of {section} `{kind}`", [k for k in keys if k not in own], shared, under=section)
                    keys = tuple(own)
                self.need(f"the keys of {section} `{kind}`", keys, kind, under=section)

    def test_plain_sections_document_their_keys(self) -> None:
        for section, spec in config._SECTIONS.items():
            if spec.plain is not None:
                keys = plain_keys(section)
                self.assertTrue(keys, f"the plain table `{section}` names no key")
                self.need(f"the keys of `{section}`", keys, section)

    def test_edge_kinds_and_their_keys(self) -> None:
        for kind in config.EDGE_KINDS:
            body = self.doc.section(kind, under="edges")
            self.assertIsNotNone(body, f"docs/config.md has no heading for the edge kind `{kind}` under the heading of edges")
            slots = ("producer", "consumer") if kind == "package" else ("from", "to")
            keys = list(slots) + list(config._EDGE_OPTION_KEYS[kind])
            if kind == "package":
                keys += list(config._PACKAGE_KEYS)
            self.need(f"the keys of the {kind} edge", keys, kind, under="edges")

    def test_test_source_area_keys(self) -> None:
        self.need("the area keys", config_sources.AREA_KEYS, "Areas", under="test_sources")

    def test_documented_defaults_are_the_defaults_of_the_code(self) -> None:
        limits = self.doc.section("limits")
        for name, default in config._LIMIT_DEFAULTS.items():
            self.assertRegex(limits, rf"\| `{name}` \| {default} \|", f"docs/config.md gives another default for limits.{name} than the {default} the code has")
        react = self.doc.section("react-router", under="entry_points")
        for glob in DEFAULT_ROUTES_GLOBS:
            self.assertIn(f"`{glob}`", react, f"docs/config.md does not give the default routes glob {glob}")
        tests = self.doc.section("tests")
        for marker in (*js.DEFAULT_MARKERS, *py_adapter.DEFAULT_MARKERS):
            self.assertIn(f"`{marker}`", tests, f"docs/config.md does not give the default test marker {marker}")

    def test_the_probe_finds_the_keys_it_should(self) -> None:
        # a guard on the test itself: if the validators stop saying "allowed keys", the check above would pass without checking
        self.assertIn("token_env", option_keys("test_sources", "qase"))
        self.assertIn("suite_id", option_keys("test_sources", "testrail"))
        self.assertIn("screens_repo", option_keys("test_sources", "playwright"))
        self.assertIn("handlers", option_keys("http_server", "openapi"))
        self.assertIn("client", option_keys("http_client", "openapi"))
        self.assertIn("root_urlconf", option_keys("entry_points", "django"))
        self.assertIn("base_path", option_keys("entry_points", "nextjs"))
        self.assertIn("context_path", option_keys("entry_points", "spring"))
        self.assertIn("routes_glob", option_keys("entry_points", "react-router"))
        self.assertNotIn("module_map", option_keys("entry_points", "react-router"))
        self.assertIn("path", option_keys("http_client", "endpoints-file"))
        self.assertIn("tag_prefix", option_keys("contract", "pydantic"))
        self.assertIn("path", option_keys("context", "jira-snapshot"))
        self.assertEqual(plain_keys("tests"), ("markers", "command"))
        self.assertEqual(plain_keys("aliases"), ("from",))

    def test_a_missing_key_is_caught(self) -> None:
        shortened = Doc(self.text.replace("`module_cap`", "`module-cap`"))
        body = shortened.section("limits")
        self.assertNotIn("`module_cap`", body)


class ExampleConfig(unittest.TestCase):
    """examples/reachmap.toml is the file the docs point at: it has to load, and say what it is for."""

    def test_the_example_config_loads(self) -> None:
        workspace = config.load(DOC.parent.parent / "examples" / "reachmap.toml", env={"REACHMAP_FIXTURES": "/tmp/example-repos"})
        self.assertEqual(sorted(workspace.repos), ["acme/api", "acme/contracts", "acme/web", "acme/worker"])
        self.assertEqual({e.kind for e in workspace.edges}, {"http", "package", "contract", "event"})
        self.assertEqual(workspace.repos["acme/web"].remote, "/tmp/example-repos/acme-web")

    def test_the_example_config_needs_its_variable(self) -> None:
        with self.assertRaises(config.ConfigError) as caught:
            config.load(DOC.parent.parent / "examples" / "reachmap.toml", env={})
        self.assertIn("REACHMAP_FIXTURES", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
