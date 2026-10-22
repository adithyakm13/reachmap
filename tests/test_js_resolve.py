"""Resolution (specifier to file) and reverse lookup (file to every specifier)."""

import unittest

from reachmap.adapters.js.aliases import AliasConfig
from reachmap.adapters.js.resolve import Resolver

FILES = [
    "src/lib/format.ts", "src/lib/money.ts", "src/lib/index.ts", "src/lib/legacy.js", "src/lib/legacy.ts",
    "src/components/StatusBadge.tsx", "src/components/Button/index.tsx", "src/components/types.d.ts",
    "src/pages/Home.tsx", "src/pages/Home/index.ts", "src/widgets/Widget01.tsx", "src/data.json",
    "src/esm/util.mts", "src/api/http.ts", "lib/shared.ts", "src/utils.helpers.ts", "index.ts",
]

CFG = AliasConfig(
    source="tsconfig.json", chain=("tsconfig.json",), base_url="",
    paths=(("@/*", ("src/*",)), ("@components/*", ("src/components/*",)), ("@api/*", ("src/api/*",)),
           ("@lib", ("src/lib/index.ts",)), ("@multi/*", ("nope/*", "src/lib/*"))),
)


def resolver(base_url="", paths=CFG.paths, files=FILES):
    return Resolver(files, AliasConfig("tsconfig.json", ("tsconfig.json",), base_url, tuple(paths)))


class Resolve(unittest.TestCase):
    def setUp(self):
        self.r = resolver()

    def test_relative(self):
        for spec, importer, want in [
            ("./format", "src/lib/money.ts", "src/lib/format.ts"),
            ("../lib/format", "src/widgets/Widget01.tsx", "src/lib/format.ts"),
            ("./StatusBadge", "src/components/Button/index.tsx", None),
            ("../StatusBadge", "src/components/Button/index.tsx", "src/components/StatusBadge.tsx"),
            ("../../../components/StatusBadge", "src/modules/cases/pages/CaseListPage.tsx", "src/components/StatusBadge.tsx"),
            ("./format.ts", "src/lib/money.ts", "src/lib/format.ts"),
            ("./data.json", "src/a.ts", "src/data.json"),
            ("./utils.helpers", "src/a.ts", "src/utils.helpers.ts"),
        ]:
            self.assertEqual(self.r.resolve(importer, spec), want, (spec, importer))

    def test_extension_preference_and_directory_index(self):
        self.assertEqual(self.r.resolve("src/a.ts", "./lib/legacy"), "src/lib/legacy.ts")   # .ts before .js
        self.assertEqual(self.r.resolve("src/a.ts", "./lib"), "src/lib/index.ts")           # a folder reads its index
        self.assertEqual(self.r.resolve("src/a.ts", "./components/Button"), "src/components/Button/index.tsx")
        self.assertEqual(self.r.resolve("src/a.ts", "./pages/Home"), "src/pages/Home.tsx")  # a file before a folder
        self.assertEqual(self.r.resolve("src/a.ts", "./components/types"), "src/components/types.d.ts")
        self.assertEqual(self.r.resolve("a.ts", "."), "index.ts")
        self.assertEqual(self.r.resolve("src/a.ts", ".."), "index.ts")

    def test_js_extension_means_the_ts_file(self):
        self.assertEqual(self.r.resolve("src/a.ts", "./lib/format.js"), "src/lib/format.ts")
        self.assertEqual(self.r.resolve("src/a.ts", "./components/StatusBadge.js"), "src/components/StatusBadge.tsx")
        self.assertEqual(self.r.resolve("src/a.ts", "./esm/util.mjs"), "src/esm/util.mts")
        self.assertEqual(self.r.resolve("src/a.ts", "./lib/legacy.js"), "src/lib/legacy.ts")

    def test_alias_and_longest_pattern(self):
        self.assertEqual(self.r.resolve("x.ts", "@/lib/format"), "src/lib/format.ts")
        self.assertEqual(self.r.resolve("x.ts", "@components/StatusBadge"), "src/components/StatusBadge.tsx")
        self.assertEqual(self.r.resolve("x.ts", "@/components/StatusBadge"), "src/components/StatusBadge.tsx")
        self.assertEqual(self.r.resolve("x.ts", "@api/http"), "src/api/http.ts")
        self.assertEqual(self.r.resolve("x.ts", "@lib"), "src/lib/index.ts")  # an exact alias
        self.assertIsNone(self.r.resolve("x.ts", "@lib/format"))              # the exact alias is not a prefix

    def test_longest_pattern_is_tried_first(self):
        r = resolver(paths=[("@/*", ("src/*",)), ("@/lib/*", ("lib/*",))])
        self.assertEqual(r.resolve("x.ts", "@/lib/shared"), "lib/shared.ts")
        self.assertEqual(r.resolve("x.ts", "@/lib/format"), "src/lib/format.ts")  # the longer pattern has no such file: fall back

    def test_multiple_targets_in_order(self):
        self.assertEqual(self.r.resolve("x.ts", "@multi/format"), "src/lib/format.ts")

    def test_base_url_for_bare_specifiers(self):
        r = resolver(base_url="src", paths=[])
        self.assertEqual(r.resolve("x.ts", "lib/format"), "src/lib/format.ts")
        self.assertIsNone(r.resolve("x.ts", "react"))
        self.assertIsNone(resolver(base_url=None, paths=[]).resolve("x.ts", "lib/format"))  # no baseUrl: a bare name is a package

    def test_packages_and_missing_files_do_not_resolve(self):
        self.assertIsNone(self.r.resolve("x.ts", "react"))
        self.assertIsNone(self.r.resolve("x.ts", "@scope/pkg"))
        self.assertIsNone(self.r.resolve("src/a.ts", "./missing"))
        self.assertIsNone(self.r.resolve("a.ts", "../../outside"))
        self.assertIsNone(self.r.resolve("src/a.ts", ""))

    def test_query_and_hash_are_dropped(self):
        self.assertEqual(self.r.resolve("src/a.ts", "./data.json?raw"), "src/data.json")
        self.assertEqual(self.r.resolve("src/a.ts", "./lib/format#x"), "src/lib/format.ts")

    def test_a_root_relative_specifier(self):
        self.assertEqual(self.r.resolve("src/a.ts", "/src/lib/format"), "src/lib/format.ts")

    def test_the_alias_pattern_with_a_suffix(self):
        r = resolver(paths=[("@t/*.svc", ("src/lib/*",))])
        self.assertEqual(r.resolve("x.ts", "@t/format.svc"), "src/lib/format.ts")
        self.assertIsNone(r.resolve("x.ts", "@t/format"))

    def test_no_alias_config_leaves_relative_working(self):
        r = Resolver(FILES, AliasConfig.empty())
        self.assertEqual(r.resolve("src/lib/money.ts", "./format"), "src/lib/format.ts")
        self.assertIsNone(r.resolve("x.ts", "@/lib/format"))


class Reverse(unittest.TestCase):
    def setUp(self):
        self.r = resolver()

    def test_every_alias_that_maps_to_the_file(self):
        got = self.r.specifiers_for("src/components/StatusBadge.tsx")
        for want in ("@/components/StatusBadge", "@components/StatusBadge", "@/components/StatusBadge.tsx",
                     "@components/StatusBadge.tsx", "@components/StatusBadge.js", "@/components/StatusBadge.js"):
            self.assertIn(want, got)

    def test_without_extension_with_extension_and_index(self):
        got = self.r.specifiers_for("src/components/Button/index.tsx")
        for want in ("@components/Button", "@components/Button/index", "@components/Button/index.tsx", "@/components/Button"):
            self.assertIn(want, got)

    def test_exact_alias_and_baseurl_spellings(self):
        got = self.r.specifiers_for("src/lib/index.ts")
        self.assertIn("@lib", got)
        self.assertIn("@/lib", got)
        self.assertIn("src/lib", resolver().specifiers_for("src/lib/index.ts"))  # baseUrl "." (root) spelling

    def test_relative_spellings_are_computed_per_importer(self):
        r = self.r
        self.assertIn("./format", r.specifiers_for("src/lib/format.ts", importer="src/lib/money.ts"))
        self.assertIn("../lib/format", r.specifiers_for("src/lib/format.ts", importer="src/widgets/Widget01.tsx"))
        self.assertIn("../../src/lib/format", r.specifiers_for("src/lib/format.ts", importer="lib/x/y.ts"))
        self.assertNotIn("./format", r.specifiers_for("src/lib/format.ts", importer="src/widgets/Widget01.tsx"))

    def test_without_an_importer_there_are_no_relative_spellings(self):
        self.assertTrue(all(not s.startswith(".") for s in self.r.specifiers_for("src/lib/format.ts")))

    def test_every_reported_spelling_resolves_back_to_the_file(self):
        for target in FILES:
            for importer in (None, "src/lib/money.ts", "src/widgets/Widget01.tsx"):
                for spec in self.r.specifiers_for(target, importer=importer):
                    self.assertEqual(self.r.resolve(importer or "x.ts", spec), target, (target, importer, spec))

    def test_reverse_lookup_agrees_with_resolution_when_a_longer_pattern_has_no_such_file(self):
        r = resolver(paths=[("@/*", ("src/*",)), ("@/lib/*", ("lib/*",))])
        self.assertEqual(r.resolve("x.ts", "@/lib/format"), "src/lib/format.ts")  # the longer pattern has no file: fall back
        self.assertIn("@/lib/format", r.specifiers_for("src/lib/format.ts"))
        self.assertEqual(r.resolve("x.ts", "@/lib/shared"), "lib/shared.ts")
        self.assertIn("@/lib/shared", r.specifiers_for("lib/shared.ts"))
        self.assertNotIn("@/lib/shared", r.specifiers_for("src/lib/format.ts"))

    def test_the_js_spelling_of_a_ts_file(self):
        self.assertIn("./format.js", self.r.specifiers_for("src/lib/format.ts", importer="src/lib/money.ts"))


if __name__ == "__main__":
    unittest.main()
