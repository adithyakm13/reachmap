"""The application configuration reader: `.properties` and the block-mapping subset of YAML, the keys of a path, placeholders, profiles, and what
the reader does not understand (said, never guessed).
"""

import unittest

from reachmap.adapters.spring.props import PATH_KEYS, parse_properties, parse_yaml, placeholders, read_config, substitute


def kv(cfg):
    return {(e.key, e.profile): (e.value, e.line) for e in cfg.entries}


class Properties(unittest.TestCase):
    def test_keys_values_lines_and_comments(self):
        cfg = parse_properties("# a comment\n! another\n\nserver.port=8080\nserver.servlet.context-path = /api\nname: value with spaces\nflag true\n", "a.properties")
        self.assertEqual(kv(cfg), {("server.port", None): ("8080", 4), ("server.servlet.context-path", None): ("/api", 5), ("name", None): ("value with spaces", 6), ("flag", None): ("true", 7)})

    def test_a_continuation_line_and_the_last_value_of_a_key_wins(self):
        cfg = parse_properties("a=one \\\n   two\na=three\n", "a.properties")
        self.assertEqual([(e.value, e.line) for e in cfg.get("a")], [("one two", 1), ("three", 3)])
        self.assertEqual(cfg.base("a").value, "three")

    def test_the_profile_of_the_file_name(self):
        cfg = read_config("src/main/resources/application-prod.properties", "server.servlet.context-path=/p\n")
        self.assertEqual(cfg.entries[0].profile, "prod")
        self.assertIsNone(cfg.base("server.servlet.context-path"))
        self.assertEqual(len(cfg.profiled("server.servlet.context-path")), 1)
        self.assertIsNone(read_config("application.properties", "a=b\n").entries[0].profile)

    def test_an_escaped_separator_in_a_key(self):
        cfg = parse_properties("a\\:b=1\n", "x.properties")
        self.assertEqual(cfg.entries[0].key, "a:b")


class Yaml(unittest.TestCase):
    def test_nested_mappings_become_dotted_keys_with_their_lines(self):
        text = "spring:\n  application:\n    name: x # the name\nserver:\n  port: 1\n  servlet:\n    context-path: '/y'\nflat.key: z\n"
        cfg = parse_yaml(text, "application.yml")
        self.assertEqual(kv(cfg), {("spring.application.name", None): ("x", 3), ("server.port", None): ("1", 5), ("server.servlet.context-path", None): ("/y", 7), ("flat.key", None): ("z", 8)})
        self.assertEqual(cfg.unread, [])

    def test_a_list_holds_no_scalar_and_a_flow_list_is_a_value(self):
        cfg = parse_yaml("a:\n  - one\n  - two\nb: [x, y]\nc: after\n", "application.yml")
        self.assertEqual(kv(cfg), {("b", None): ("[x, y]", 4), ("c", None): ("after", 5)})
        self.assertEqual(cfg.unread, [])

    def test_a_flow_mapping_hides_the_keys_under_it_and_is_said(self):
        cfg = parse_yaml("server: {servlet: {context-path: /x}}\n", "application.yml")
        self.assertEqual([(e.key, e.unread) for e in cfg.entries], [("server", True)])
        self.assertEqual(len(cfg.unread), 1)
        self.assertIn("flow mapping", cfg.unread[0][1])

    def test_a_literal_block_an_anchor_and_a_tab_are_said(self):
        cfg = parse_yaml("a: |\n  text\n  more\nb: &x 1\nc:\n\td: 2\ne: 3\n", "application.yml")
        reasons = [why for _p, why in cfg.unread]
        self.assertTrue(any("literal block" in r for r in reasons))
        self.assertTrue(any("anchor" in r for r in reasons))
        self.assertTrue(any("tab" in r for r in reasons))
        self.assertEqual(cfg.base("e").value, "3")

    def test_documents_with_a_profile_apply_under_it(self):
        text = "server.servlet.context-path: /base\n---\nspring.config.activate.on-profile: prod\nserver.servlet.context-path: /prod\n"
        cfg = parse_yaml(text, "application.yml")
        self.assertEqual(cfg.base("server.servlet.context-path").value, "/base")
        self.assertEqual([(e.value, e.profile) for e in cfg.profiled("server.servlet.context-path")], [("/prod", "prod")])

    def test_quoted_keys_and_a_hash_inside_a_quoted_value(self):
        cfg = parse_yaml('"a.b": "x # not a comment"\n', "application.yml")
        self.assertEqual(kv(cfg), {("a.b", None): ("x # not a comment", 1)})


class Placeholders(unittest.TestCase):
    def test_the_keys_a_value_names(self):
        self.assertEqual(placeholders("${a.b}/x/${c:dflt}"), ["a.b", "c"])
        self.assertEqual(placeholders("/plain"), [])

    def test_substitute_uses_the_value_then_the_default_and_names_what_is_missing(self):
        cfg = parse_properties("a=1\n", "x.properties")
        value, missing = substitute("${a}/${b:two}/${c}", lambda k: cfg.base(k))
        self.assertEqual((value, missing), ("1/two/${c}", ["c"]))

    def test_an_unread_entry_is_not_a_value(self):
        cfg = parse_yaml("a: {x: 1}\n", "application.yml")
        value, missing = substitute("${a}", lambda k: cfg.base(k))
        self.assertEqual((value, missing), ("<unknown:${a}>", ["a"]))

    def test_an_unread_entry_is_not_replaced_by_the_default(self):
        cfg = parse_yaml("a: {x: 1}\n", "application.yml")
        value, missing = substitute("${a:/dflt}/x", lambda k: cfg.base(k))
        self.assertEqual((value, missing), ("<unknown:${a}>/x", ["a"]))

    def test_the_path_keys(self):
        self.assertEqual(PATH_KEYS, ("server.servlet.context-path", "spring.mvc.servlet.path", "spring.webflux.base-path"))


if __name__ == "__main__":
    unittest.main()
