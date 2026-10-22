"""Alias configuration: tsconfig and jsconfig `paths` and `baseUrl`, through `extends`."""

import unittest
from pathlib import Path

from reachmap.adapters.js.aliases import AliasConfig, detect_config, load_aliases, parse_jsonc
from reachmap.incomplete import kind_spec
from reachmap.model import ReadResult

FIXTURE_STEPS = Path(__file__).parent / "fixtures" / "repos" / "acme-web" / "steps"


class Tree:
    """An in-memory repository: path to text."""

    def __init__(self, files):
        self.files = dict(files)
        self.reads = []

    def read(self, path):
        self.reads.append(path)
        if path in self.files:
            return ReadResult.found(self.files[path])
        return ReadResult.absent()

    def has_dir(self, path):
        if path in ("", "."):
            return True
        return any(f.startswith(path.rstrip("/") + "/") for f in self.files)

    def has_file(self, path):
        return path in self.files


def load(files, start="tsconfig.json"):
    tree = Tree(files)
    records = []
    cfg = load_aliases(tree.read, start, tree.has_dir, records)
    return cfg, records, tree


class JsonWithComments(unittest.TestCase):
    def test_comments_and_trailing_commas(self):
        text = '''// top comment
{
  /* block */
  "a": 1, // trailing
  "b": [1, 2, ],
  "c": {"d": true, "e": null,},
}'''
        self.assertEqual(parse_jsonc(text), {"a": 1, "b": [1, 2], "c": {"d": True, "e": None}})

    def test_comment_markers_inside_strings_are_kept(self):
        data = parse_jsonc('{"url": "http://x//y", "glob": "src/**/*", "c": "/* not */", "t": "a,]"}')
        self.assertEqual(data, {"url": "http://x//y", "glob": "src/**/*", "c": "/* not */", "t": "a,]"})

    def test_numbers_and_escapes(self):
        self.assertEqual(parse_jsonc('{"n": -1.5e2, "i": 7, "s": "a\\"b\\u0041"}'), {"n": -150.0, "i": 7, "s": 'a"bA'})

    def test_bom_is_ignored(self):
        self.assertEqual(parse_jsonc('\ufeff{"a": 1}'), {"a": 1})

    def test_garbage_raises_value_error(self):
        for bad in ("", "{", '{"a": }', '{"a" 1}', "[1, 2", '{"a": 1} extra', "nope", '{"a": 1,, }', '{1: 2}'):
            with self.assertRaises(ValueError, msg=bad):
                parse_jsonc(bad)

    def test_deep_nesting_is_refused_not_a_crash(self):
        with self.assertRaises(ValueError):
            parse_jsonc("[" * 500 + "]" * 500)


class Paths(unittest.TestCase):
    def test_wildcards_and_base_url(self):
        cfg, records, _ = load({
            "tsconfig.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"], "@components/*": ["src/components/*"], "@api/*": ["src/api/*"]}}}',
            "src/a.ts": "", "src/components/b.ts": "", "src/api/c.ts": "",
        })
        self.assertEqual(dict(cfg.paths), {"@/*": ("src/*",), "@components/*": ("src/components/*",), "@api/*": ("src/api/*",)})
        self.assertEqual(cfg.base_url, "")
        self.assertEqual(records, [])
        self.assertTrue(cfg.complete)

    def test_without_base_url_targets_are_relative_to_the_config_file(self):
        cfg, _, _ = load({"apps/web/tsconfig.json": '{"compilerOptions": {"paths": {"@/*": ["./src/*"]}}}', "apps/web/src/a.ts": ""},
                         start="apps/web/tsconfig.json")
        self.assertEqual(dict(cfg.paths), {"@/*": ("apps/web/src/*",)})
        self.assertIsNone(cfg.base_url)

    def test_base_url_is_relative_to_its_config_and_may_move_the_targets(self):
        cfg, _, _ = load({"tsconfig.json": '{"compilerOptions": {"baseUrl": "src", "paths": {"@/*": ["*"], "lib": ["lib/index.ts"]}}}',
                          "src/a.ts": "", "src/lib/index.ts": ""})
        self.assertEqual(cfg.base_url, "src")
        self.assertEqual(dict(cfg.paths), {"@/*": ("src/*",), "lib": ("src/lib/index.ts",)})

    def test_multiple_targets_keep_their_order(self):
        cfg, _, _ = load({"tsconfig.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"@x/*": ["src/a/*", "src/b/*", "vendor/*"]}}}',
                          "src/a/f.ts": "", "src/b/f.ts": "", "vendor/f.ts": ""})
        self.assertEqual(cfg.paths, (("@x/*", ("src/a/*", "src/b/*", "vendor/*")),))

    def test_exact_alias_without_wildcard(self):
        cfg, _, _ = load({"tsconfig.json": '{"compilerOptions": {"paths": {"@lib": ["src/lib/index.ts"]}}}', "src/lib/index.ts": ""})
        self.assertEqual(cfg.paths, (("@lib", ("src/lib/index.ts",)),))

    def test_a_pattern_with_two_wildcards_is_refused_and_recorded(self):
        cfg, records, _ = load({"tsconfig.json": '{"compilerOptions": {"paths": {"@a/*/*": ["src/*"], "@ok/*": ["src/*"]}}}', "src/f.ts": ""})
        self.assertEqual([p for p, _ in cfg.paths], ["@ok/*"])
        self.assertEqual([(r.kind, r.blocks_selection) for r in records], [("alias_config_gap", False)])

    def test_no_paths_is_an_empty_config(self):
        cfg, records, _ = load({"tsconfig.json": '{"compilerOptions": {"strict": true}}'})
        self.assertEqual((cfg.paths, cfg.base_url, records), ((), None, []))
        self.assertTrue(cfg.complete)


class MissingTargets(unittest.TestCase):
    def test_a_missing_target_directory_is_recorded_not_blocking_and_ignored(self):
        cfg, records, _ = load({
            "tsconfig.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["app/*", "src/*"], "@gone/*": ["nowhere/*"]}}}',
            "src/a.ts": "",
        })
        self.assertEqual(cfg.paths, (("@/*", ("src/*",)),))  # the missing target and the pattern left with none are dropped
        self.assertEqual([r.kind for r in records], ["alias_config_gap", "alias_config_gap"])
        self.assertTrue(all(not r.blocks_selection for r in records))
        self.assertIn("app/*", records[0].subject)
        self.assertIn("does not exist", records[0].detail)
        self.assertTrue(cfg.complete)  # a stated limit: the rest of the config was read in full

    def test_the_stale_editor_config_is_not_used_when_the_config_names_tsconfig(self):
        files = {
            "tsconfig.json": (FIXTURE_STEPS / "01-scaffold/tsconfig.json").read_text(),
            "jsconfig.json": (FIXTURE_STEPS / "01-scaffold/jsconfig.json").read_text(),
            "src/components/StatusBadge.tsx": "",
            "src/api/http.ts": "",
        }
        cfg, records, tree = load(files, start="tsconfig.json")
        self.assertEqual(cfg.paths[0], ("@/*", ("src/*",)))
        self.assertNotIn("jsconfig.json", tree.reads)
        self.assertEqual(records, [])

    def test_the_stale_jsconfig_alone_is_recorded_for_its_missing_directories(self):
        files = {"jsconfig.json": (FIXTURE_STEPS / "01-scaffold/jsconfig.json").read_text(), "src/a.ts": ""}
        cfg, records, _ = load(files, start="jsconfig.json")
        self.assertEqual(cfg.paths, ())
        self.assertEqual(len(records), 3)
        self.assertTrue(all(r.kind == "alias_config_gap" and not r.blocks_selection for r in records))


class Extends(unittest.TestCase):
    def test_chain_child_paths_replace_the_parents_and_base_url_is_inherited(self):
        cfg, records, _ = load({
            "tsconfig.json": '{"extends": "./tsconfig.base.json", "compilerOptions": {"paths": {"@c/*": ["src/c/*"]}}}',
            "tsconfig.base.json": '{"extends": "./configs/root", "compilerOptions": {"baseUrl": "."}}',
            "configs/root.json": '{"compilerOptions": {"baseUrl": "configs", "paths": {"@old/*": ["old/*"]}}}',
            "src/c/x.ts": "",
        })
        self.assertEqual(cfg.base_url, "")
        self.assertEqual(cfg.paths, (("@c/*", ("src/c/*",)),))
        self.assertEqual(cfg.chain, ("tsconfig.json", "tsconfig.base.json", "configs/root.json"))
        self.assertEqual(records, [])

    def test_paths_from_the_parent_are_used_when_the_child_has_none(self):
        cfg, _, _ = load({
            "tsconfig.json": '{"extends": "./base.json"}',
            "base.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}',
            "src/a.ts": "",
        })
        self.assertEqual(cfg.paths, (("@/*", ("src/*",)),))

    def test_paths_in_a_parent_without_base_url_are_relative_to_the_parent(self):
        cfg, _, _ = load({
            "apps/web/tsconfig.json": '{"extends": "../../tsconfig.base.json"}',
            "tsconfig.base.json": '{"compilerOptions": {"paths": {"@/*": ["./src/*"]}}}',
            "src/a.ts": "",
        }, start="apps/web/tsconfig.json")
        self.assertEqual(cfg.paths, (("@/*", ("src/*",)),))

    def test_extends_in_a_subfolder_resolves_relative_to_that_file(self):
        cfg, _, _ = load({
            "pkg/tsconfig.json": '{"extends": "../shared/tsconfig.json"}',
            "shared/tsconfig.json": '{"compilerOptions": {"baseUrl": "..", "paths": {"@s/*": ["shared/src/*"]}}}',
            "shared/src/a.ts": "",
        }, start="pkg/tsconfig.json")
        self.assertEqual((cfg.base_url, cfg.paths), ("", (("@s/*", ("shared/src/*",)),)))

    def test_extends_array_later_entries_win(self):
        cfg, _, _ = load({
            "tsconfig.json": '{"extends": ["./a.json", "./b.json"]}',
            "a.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"@a/*": ["src/*"]}}}',
            "b.json": '{"compilerOptions": {"paths": {"@b/*": ["src/*"]}}}',
            "src/x.ts": "",
        })
        self.assertEqual(cfg.paths, (("@b/*", ("src/*",)),))

    def test_extends_a_directory_reads_its_tsconfig(self):
        cfg, _, _ = load({
            "tsconfig.json": '{"extends": "./config"}',
            "config/tsconfig.json": '{"compilerOptions": {"baseUrl": "..", "paths": {"@/*": ["src/*"]}}}',
            "src/a.ts": "",
        })
        self.assertEqual(cfg.paths, (("@/*", ("src/*",)),))

    def test_extends_a_package_is_recorded_not_blocking(self):
        cfg, records, _ = load({"tsconfig.json": '{"extends": "@tsconfig/node18/tsconfig.json", "compilerOptions": {"paths": {"@/*": ["src/*"]}}}', "src/a.ts": ""})
        self.assertEqual([(r.kind, r.blocks_selection) for r in records], [("alias_config_gap", False)])
        self.assertIn("package", records[0].detail)
        self.assertEqual(cfg.paths, (("@/*", ("src/*",)),))

    def test_extends_a_missing_file_is_recorded_blocking(self):
        cfg, records, _ = load({"tsconfig.json": '{"extends": "./missing.json"}'})
        self.assertEqual([(r.kind, r.blocks_selection) for r in records], [("alias_config_gap", True)])
        self.assertFalse(cfg.complete)

    def test_extends_cycle_is_recorded_and_stops(self):
        cfg, records, _ = load({"tsconfig.json": '{"extends": "./b.json"}', "b.json": '{"extends": "./tsconfig.json"}'})
        self.assertEqual([r.kind for r in records], ["alias_config_gap"])
        self.assertIn("cycle", records[0].detail)

    def test_very_long_chain_is_cut_and_recorded(self):
        files = {f"c{i}.json": '{"extends": "./c%d.json"}' % (i + 1) for i in range(30)}
        files["tsconfig.json"] = '{"extends": "./c0.json"}'
        cfg, records, _ = load(files)
        self.assertEqual([r.kind for r in records][:1], ["alias_config_gap"])
        self.assertFalse(cfg.complete)


class Failures(unittest.TestCase):
    def test_a_named_config_that_is_absent_at_the_commit_defines_no_aliases(self):
        # absent is an answer (the read found no such file), not a failure: no records, nothing was missed
        cfg, records, _ = load({}, start="tsconfig.json")
        self.assertEqual(records, [])
        self.assertTrue(cfg.complete)
        self.assertEqual((cfg.paths, cfg.base_url, cfg.chain), ((), None, ()))

    def test_an_unreadable_config_is_not_an_absence(self):
        records = []

        def read(path):
            return ReadResult.unreadable("the object is missing")

        cfg = load_aliases(read, "tsconfig.json", lambda p: True, records)
        self.assertFalse(cfg.complete)
        self.assertEqual(cfg.paths, ())
        # the git layer already made a read_unreadable record for the read itself; the loader adds none of its own
        self.assertEqual(records, [])

    def test_a_config_that_does_not_parse_is_a_blocking_read_failure(self):
        cfg, records, _ = load({"tsconfig.json": '{"compilerOptions": '})
        self.assertEqual([(r.kind, r.blocks_selection) for r in records], [("read_unreadable", True)])
        self.assertIn("tsconfig.json", records[0].subject)
        self.assertFalse(cfg.complete)

    def test_a_config_that_is_not_an_object_is_a_blocking_read_failure(self):
        cfg, records, _ = load({"tsconfig.json": "[1, 2]"})
        self.assertEqual([r.kind for r in records], ["read_unreadable"])

    def test_odd_shapes_are_ignored_with_a_record(self):
        cfg, records, _ = load({"tsconfig.json": '{"compilerOptions": {"baseUrl": 3, "paths": {"@/*": "src/*", "@b/*": [1], "@c/*": ["src/*"]}}}', "src/a.ts": ""})
        self.assertEqual([p for p, _ in cfg.paths], ["@c/*"])
        self.assertTrue(records)
        self.assertTrue(all(r.kind == "alias_config_gap" for r in records))

    def test_the_kind_is_registered_non_blocking_by_default(self):
        spec = kind_spec("alias_config_gap")
        self.assertFalse(spec.blocks_selection)
        self.assertFalse(spec.always_blocking)


class Detect(unittest.TestCase):
    def test_tsconfig_before_jsconfig(self):
        self.assertEqual(detect_config(lambda p: p in ("tsconfig.json", "jsconfig.json")), "tsconfig.json")
        self.assertEqual(detect_config(lambda p: p == "jsconfig.json"), "jsconfig.json")
        self.assertIsNone(detect_config(lambda p: False))

    def test_aliasconfig_is_a_value(self):
        self.assertEqual(AliasConfig.empty(), AliasConfig.empty())
        self.assertEqual(AliasConfig.empty().paths, ())


if __name__ == "__main__":
    unittest.main()


class UnreadPackages(unittest.TestCase):
    def test_a_package_extends_with_no_paths_or_base_url_of_its_own_is_incomplete(self):
        cfg, records, _ = load({"tsconfig.json": '{"extends": "@acme/tsconfig/base.json"}'})
        self.assertFalse(cfg.complete)
        self.assertEqual([(r.kind, r.blocks_selection) for r in records], [("alias_config_gap", False)])

    def test_with_paths_or_a_base_url_it_stays_a_stated_limit(self):
        for body in ('"compilerOptions": {"baseUrl": "."}', '"compilerOptions": {"paths": {"@/*": ["src/*"]}}'):
            cfg, _, _ = load({"tsconfig.json": '{"extends": "@acme/tsconfig/base.json", %s}' % body, "src/a.ts": ""})
            self.assertTrue(cfg.complete, body)

    def test_the_named_config_being_absent_is_marked_missing(self):
        cfg, records, _ = load({}, start="config/tsconfig.app.json")
        self.assertEqual((cfg.missing, cfg.source, records), (True, "config/tsconfig.app.json", []))
        self.assertFalse(load({"tsconfig.json": "{}"})[0].missing)
