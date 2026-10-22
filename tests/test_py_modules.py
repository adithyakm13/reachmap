"""The module map: path to dotted name and back, source roots, service markers, library roots, `src/`
layouts, `package-dir`, namespace packages, relative resolution and ambiguity.

Every case is an in-memory file list: no git. Trap numbers refer to docs/CONTEXT.md section 5.
"""

import unittest

from reachmap.adapters.python.modules import ModuleMap
from reachmap.model import ReadResult


def build(files, contents=None, **kw):
    contents = contents or {}
    records = []

    def read(path):
        return ReadResult.found(contents[path]) if path in contents else ReadResult.absent()

    kw.setdefault("target", records)
    mm = ModuleMap.build(set(files) | set(contents), read=read, **kw)
    return mm, records


def kinds(records):
    return sorted((r.kind, r.subject, r.blocks_selection) for r in records)


API = [
    "libs/common/pyproject.toml", "libs/common/acme_common/__init__.py", "libs/common/acme_common/money.py",
    "services/billing/pyproject.toml", "services/billing/app/__init__.py", "services/billing/app/main.py",
    "services/billing/app/routers/__init__.py", "services/billing/app/routers/invoices.py", "services/billing/tests/test_money.py",
    "services/cases/pyproject.toml", "services/cases/app/__init__.py", "services/cases/app/main.py",
    "services/cases/app/routers/cases.py", "tools/release.py",
]


class Roots(unittest.TestCase):
    def test_a_plain_package_at_the_repo_root(self):
        mm, records = build(["pkg/__init__.py", "pkg/mod.py", "pkg/sub/__init__.py", "pkg/sub/leaf.py", "script.py"])
        self.assertEqual(mm.names_of("pkg/__init__.py"), ("pkg",))
        self.assertEqual(mm.names_of("pkg/mod.py"), ("pkg.mod",))
        self.assertEqual(mm.names_of("pkg/sub/__init__.py"), ("pkg.sub",))
        self.assertEqual(mm.names_of("pkg/sub/leaf.py"), ("pkg.sub.leaf",))
        self.assertEqual(mm.names_of("script.py"), ("script",))
        self.assertEqual(mm.resolve("pkg.sub", "script.py").files, ("pkg/sub/__init__.py",))
        self.assertEqual(mm.resolve("pkg.mod", "script.py").files, ("pkg/mod.py",))
        self.assertEqual(records, [])
        self.assertTrue(mm.complete)

    def test_a_file_has_a_name_from_each_root_that_holds_it(self):
        """The service root gives `app.main`; the repo root gives the longer name too (a script run from the
        root of the checkout); both resolve to the one file."""
        mm, _ = build(API, service_marker="pyproject.toml", library_roots=("libs/common",))
        self.assertEqual(mm.names_of("services/billing/app/main.py"), ("app.main", "services.billing.app.main"))
        self.assertEqual(mm.names_of("libs/common/acme_common/money.py"), ("acme_common.money", "libs.common.acme_common.money"))
        self.assertEqual(mm.resolve("services.billing.app.main", "tools/release.py").files, ("services/billing/app/main.py",))

    def test_two_services_with_a_package_of_the_same_name_resolve_per_importer(self):
        """s15: billing and cases both have a package called `app`: module names are resolved per service marker."""
        mm, records = build(API, service_marker="pyproject.toml", library_roots=("libs/common",))
        self.assertEqual(mm.resolve("app.main", "services/billing/app/routers/invoices.py").files, ("services/billing/app/main.py",))
        self.assertEqual(mm.resolve("app.main", "services/cases/app/routers/cases.py").files, ("services/cases/app/main.py",))
        self.assertEqual(mm.resolve("app.main", "services/billing/tests/test_money.py").files, ("services/billing/app/main.py",))
        self.assertEqual(records, [])

    def test_a_library_root_is_visible_to_every_service(self):
        mm, _ = build(API, service_marker="pyproject.toml", library_roots=("libs/common",))
        for importer in ("services/billing/app/main.py", "services/cases/app/routers/cases.py", "tools/release.py", "libs/common/acme_common/money.py"):
            self.assertEqual(mm.resolve("acme_common.money", importer).files, ("libs/common/acme_common/money.py",), importer)

    def test_another_services_package_is_not_visible_from_a_service_or_a_library(self):
        mm, _ = build(API + ["services/cases/app/only_cases.py"], service_marker="pyproject.toml", library_roots=("libs/common",))
        self.assertEqual(mm.resolve("app.only_cases", "services/billing/app/main.py").files, ())
        self.assertFalse(mm.resolve("app.only_cases", "services/billing/app/main.py").found)
        self.assertFalse(mm.resolve("app.main", "libs/common/acme_common/money.py").found)

    def test_a_file_outside_every_service_sees_every_root_and_a_clash_is_ambiguous_with_both_kept(self):
        mm, _ = build(API, service_marker="pyproject.toml", library_roots=("libs/common",))
        res = mm.resolve("app.main", "tools/release.py")
        self.assertEqual(res.files, ("services/billing/app/main.py", "services/cases/app/main.py"))
        self.assertTrue(res.ambiguous)

    def test_the_service_marker_is_a_file_name_not_a_path_and_none_means_the_repo_root_only(self):
        mm, _ = build(API)
        self.assertEqual(mm.names_of("services/billing/app/main.py"), ("services.billing.app.main",))
        mm, _ = build(API, service_marker="BUILD")
        self.assertEqual(mm.names_of("services/billing/app/main.py"), ("services.billing.app.main",))

    def test_a_src_layout_at_the_root_and_in_a_service(self):
        mm, _ = build(["src/pkg/__init__.py", "src/pkg/a.py", "svc/pyproject.toml", "svc/src/other/__init__.py", "svc/src/other/b.py"], service_marker="pyproject.toml")
        self.assertEqual(mm.names_of("src/pkg/a.py"), ("pkg.a", "src.pkg.a"))
        self.assertEqual(mm.names_of("svc/src/other/b.py"), ("other.b", "src.other.b", "svc.src.other.b"))
        self.assertEqual(mm.resolve("pkg.a", "src/pkg/__init__.py").files, ("src/pkg/a.py",))
        self.assertEqual(mm.resolve("other.b", "svc/src/other/__init__.py").files, ("svc/src/other/b.py",))

    def test_a_src_folder_that_is_a_package_is_not_a_source_root(self):
        mm, _ = build(["src/__init__.py", "src/a.py"])
        self.assertEqual(mm.names_of("src/a.py"), ("src.a",))

    def test_a_library_root_that_does_not_exist_is_recorded_and_ignored(self):
        mm, records = build(["a.py"], library_roots=("libs/missing",))
        self.assertEqual(kinds(records), [("module_map_gap", "libs/missing", False)])
        self.assertTrue(mm.complete)

    def test_folders_that_cannot_be_imported_by_name_have_no_dotted_name(self):
        mm, _ = build(["my-dir/x.py", "ok/y.py", "ok/class-2.py", "a b/z.py"])
        self.assertEqual(mm.names_of("my-dir/x.py"), ())
        self.assertEqual(mm.names_of("ok/y.py"), ("ok.y",))
        self.assertEqual(mm.names_of("ok/class-2.py"), ())
        self.assertEqual(mm.names_of("a b/z.py"), ())

    def test_environment_and_vendored_folders_are_not_modules_of_the_repository(self):
        mm, _ = build([".venv/lib/python3.12/site-packages/six.py", "node_modules/x/y.py", "pkg/a.py", "pkg/__pycache__/a.py"])
        self.assertEqual(mm.names_of(".venv/lib/python3.12/site-packages/six.py"), ())
        self.assertEqual(mm.names_of("node_modules/x/y.py"), ())
        self.assertEqual(mm.names_of("pkg/__pycache__/a.py"), ())
        self.assertFalse(mm.resolve("six", "pkg/a.py").found)

    def test_non_python_files_are_not_modules(self):
        mm, _ = build(["pkg/a.py", "pkg/b.pyi", "pkg/c.txt"])
        self.assertEqual(mm.names_of("pkg/b.pyi"), ())
        self.assertFalse(mm.resolve("pkg.b", "pkg/a.py").found)


class Namespace(unittest.TestCase):
    def test_a_folder_without_init_is_a_namespace_package(self):
        mm, _ = build(["ns/sub/mod.py", "ns/other.py", "main.py"])
        self.assertEqual(mm.names_of("ns/sub/mod.py"), ("ns.sub.mod",))
        res = mm.resolve("ns.sub", "main.py")
        self.assertEqual((res.files, res.namespace, res.found), ((), True, True))
        self.assertEqual(mm.resolve("ns.sub.mod", "main.py").files, ("ns/sub/mod.py",))
        self.assertFalse(mm.resolve("ns.nothing", "main.py").found)

    def test_a_package_wins_over_nothing_and_both_a_module_and_a_package_are_kept(self):
        mm, _ = build(["a.py", "a/__init__.py", "main.py"])
        res = mm.resolve("a", "main.py")
        self.assertEqual(res.files, ("a.py", "a/__init__.py"))
        self.assertTrue(res.ambiguous)


class PackageDir(unittest.TestCase):
    def test_package_dir_maps_a_source_folder_to_the_import_name(self):
        """acme/contracts: the import name is acme_contracts, the folder is contracts/ (read from pyproject.toml)."""
        pyproject = '[tool.setuptools]\npackage-dir = { acme_contracts = "contracts" }\npackages = ["acme_contracts", "acme_contracts.models"]\n'
        files = ["pyproject.toml", "contracts/__init__.py", "contracts/models/__init__.py", "contracts/models/invoice.py", "tests/test_x.py"]
        mm, records = build(files, {"pyproject.toml": pyproject})
        self.assertEqual(mm.names_of("contracts/models/invoice.py"), ("acme_contracts.models.invoice", "contracts.models.invoice"))
        self.assertEqual(mm.names_of("contracts/__init__.py"), ("acme_contracts", "contracts"))
        self.assertEqual(mm.resolve("acme_contracts.models.invoice", "tests/test_x.py").files, ("contracts/models/invoice.py",))
        self.assertEqual(mm.resolve("acme_contracts", "tests/test_x.py").files, ("contracts/__init__.py",))
        self.assertEqual(records, [])

    def test_package_dir_with_an_empty_key_is_a_root_folder(self):
        mm, _ = build(["pyproject.toml", "lib/pkg/a.py"], {"pyproject.toml": '[tool.setuptools]\npackage-dir = {"" = "lib"}\n'})
        self.assertEqual(mm.names_of("lib/pkg/a.py"), ("pkg.a", "lib.pkg.a"))

    def test_package_dir_is_read_relative_to_the_service_that_declares_it(self):
        files = ["svc/pyproject.toml", "svc/sources/pkg/a.py"]
        mm, _ = build(files, {"svc/pyproject.toml": '[tool.setuptools.package-dir]\n"" = "sources"\n'}, service_marker="pyproject.toml")
        self.assertEqual(mm.names_of("svc/sources/pkg/a.py"), ("pkg.a", "sources.pkg.a", "svc.sources.pkg.a"))

    def test_packages_find_where(self):
        mm, _ = build(["pyproject.toml", "python/pkg/a.py"], {"pyproject.toml": '[tool.setuptools.packages.find]\nwhere = ["python"]\n'})
        self.assertIn("pkg.a", mm.names_of("python/pkg/a.py"))

    def test_poetry_packages_from(self):
        text = '[tool.poetry]\npackages = [{ include = "pkg", from = "lib" }]\n'
        mm, _ = build(["pyproject.toml", "lib/pkg/a.py"], {"pyproject.toml": text})
        self.assertIn("pkg.a", mm.names_of("lib/pkg/a.py"))

    def test_hatch_wheel_packages(self):
        text = '[tool.hatch.build.targets.wheel]\npackages = ["python/pkg"]\n'
        mm, _ = build(["pyproject.toml", "python/pkg/a.py"], {"pyproject.toml": text})
        self.assertIn("pkg.a", mm.names_of("python/pkg/a.py"))

    def test_a_package_dir_that_is_not_understood_is_recorded_not_guessed(self):
        cases = {
            "not a table": '[tool.setuptools]\npackage-dir = "src"\n',
            "a value that is not text": '[tool.setuptools]\npackage-dir = { pkg = 3 }\n',
            "an absolute folder": '[tool.setuptools]\npackage-dir = { pkg = "/abs" }\n',
            "a folder above the repository": '[tool.setuptools]\npackage-dir = { pkg = "../x" }\n',
            "a folder with no files": '[tool.setuptools]\npackage-dir = { pkg = "nowhere" }\n',
            "hatch sources": '[tool.hatch.build.targets.wheel.sources]\n"src" = ""\n',
        }
        for name, text in cases.items():
            with self.subTest(name):
                mm, records = build(["pyproject.toml", "src/pkg/a.py"], {"pyproject.toml": text})
                self.assertEqual(kinds(records), [("module_map_gap", "pyproject.toml", False)])
                self.assertEqual(mm.names_of("src/pkg/a.py")[-1], "src.pkg.a")

    def test_shapes_nothing_expected_are_a_gap_or_an_incomplete_map_never_a_crash(self):
        for text in ("[tool.hatch]\nbuild = 'x'\n", "[tool.hatch.build]\ntargets = ['x']\n", "[tool.poetry]\n", "tool.poetry = 'x'\n", "[tool]\npdm = 'x'\n", "[tool.setuptools]\npackages = 3\n"):
            with self.subTest(text):
                mm, records = build(["pyproject.toml", "pkg/a.py"], {"pyproject.toml": text})
                self.assertEqual(mm.names_of("pkg/a.py"), ("pkg.a",))
                self.assertTrue(all(r.kind == "module_map_gap" and not r.blocks_selection for r in records), records)
        mm, records = build(["pyproject.toml", "pkg/a.py"], {"pyproject.toml": "a = " + "[" * 5000 + "]" * 5000 + "\n"})
        self.assertFalse(mm.complete)
        self.assertEqual([(r.kind, r.blocks_selection) for r in records], [("read_unreadable", True)])

    def test_a_non_table_poetry_or_pdm_is_recorded(self):
        for text in ("tool.poetry = 'x'\n", "[tool]\npdm = 'x'\n", "[tool.hatch]\nbuild = 'x'\n"):
            with self.subTest(text):
                _mm, records = build(["pyproject.toml", "pkg/a.py"], {"pyproject.toml": text})
                self.assertEqual(kinds(records), [("module_map_gap", "pyproject.toml", False)])

    def test_setup_py_and_setup_cfg_package_dir_are_not_read_and_say_so(self):
        for name, text in (("setup.py", "setup(package_dir={'': 'src'})\n"), ("setup.cfg", "[options]\npackage_dir =\n    =src\n")):
            with self.subTest(name):
                mm, records = build([name, "src/pkg/a.py"], {name: text})
                self.assertEqual(kinds(records), [("module_map_gap", name, False)])

    def test_a_folder_called_safe_in_a_gap_does_not_trip_the_banned_wording_check(self):
        mm, records = build(["pyproject.toml", "src/pkg/a.py"], {"pyproject.toml": '[tool.setuptools]\npackage-dir = { pkg = "safe" }\n'})
        self.assertEqual(kinds(records), [("module_map_gap", "pyproject.toml", False)])
        self.assertNotIn("safe", records[0].detail)

    def test_a_setup_py_without_package_dir_says_nothing(self):
        mm, records = build(["setup.py", "pkg/a.py"], {"setup.py": "setup(name='x')\n"})
        self.assertEqual(records, [])

    def test_a_pyproject_that_does_not_parse_is_blocking_and_the_map_is_incomplete(self):
        mm, records = build(["pyproject.toml", "pkg/a.py"], {"pyproject.toml": "[tool.setuptools\nnot toml"}, at="abc123def456")
        self.assertEqual(kinds(records), [("read_unreadable", "pyproject.toml at abc123def456", True)])
        self.assertFalse(mm.complete)
        self.assertTrue(mm.reasons)
        self.assertEqual(mm.names_of("pkg/a.py"), ("pkg.a",))  # the plain roots are still known

    def test_an_unreadable_pyproject_makes_the_map_incomplete(self):
        def read(path):
            return ReadResult.unreadable("boom")

        mm = ModuleMap.build({"pyproject.toml", "pkg/a.py"}, read=read, target=[])
        self.assertFalse(mm.complete)

    def test_an_unreadable_service_pyproject_is_incomplete_too(self):
        def read(path):
            return ReadResult.found("[project]\nname = 'x'\n") if path == "svc/pyproject.toml" else ReadResult.unreadable("boom")

        mm = ModuleMap.build({"pyproject.toml", "svc/pyproject.toml", "svc/a.py"}, service_marker="pyproject.toml", read=read, target=[])
        self.assertFalse(mm.complete)

    def test_duplicate_mounts_are_one(self):
        text = '[tool.setuptools]\npackage-dir = { "" = "src" }\n'
        mm, _ = build(["pyproject.toml", "src/pkg/a.py"], {"pyproject.toml": text})
        self.assertEqual(mm.names_of("src/pkg/a.py"), ("pkg.a", "src.pkg.a"))
        self.assertEqual(len([m for m in mm.mounts if (m.dir, m.prefix) == ("src", "")]), 1)


class Scale(unittest.TestCase):
    def test_a_large_monorepo_builds_in_well_under_a_few_seconds(self):
        """300 services with 100 modules each (30,000 files): the map is built from folders and ancestors, not
        by testing every file against every mount (that took several seconds per commit, twice per run)."""
        import time
        files = ["libs/common/pyproject.toml", "libs/common/acme_common/money.py"]
        for n in range(300):
            files.append(f"services/s{n}/pyproject.toml")
            files += [f"services/s{n}/app/m{k // 10}/mod{k}.py" for k in range(100)]
        # CPU time, not wall time: other processes on a busy machine must not fail this test.
        started = time.process_time()
        mm, records = build(files, service_marker="pyproject.toml", library_roots=("libs/common",))
        took = time.process_time() - started
        self.assertEqual(records, [])
        self.assertEqual(mm.names_of("services/s7/app/m3/mod37.py"), ("app.m3.mod37", "services.s7.app.m3.mod37"))
        self.assertEqual(mm.resolve("app.m3.mod37", "services/s7/app/m1/mod10.py").files, ("services/s7/app/m3/mod37.py",))
        self.assertLess(took, 1.0, f"building the map of 30,000 files took {took:.1f}s")


class Relative(unittest.TestCase):
    FILES = ["pkg/__init__.py", "pkg/mod.py", "pkg/other.py", "pkg/sub/__init__.py", "pkg/sub/leaf.py", "pkg/sub/deep/__init__.py", "top.py", "x.py"]

    def setUp(self):
        self.mm, self.records = build(self.FILES)

    def test_one_dot_is_the_importers_package(self):
        res = self.mm.resolve_relative("pkg/sub/leaf.py", 1, "")
        self.assertEqual(res.files, ("pkg/sub/__init__.py",))
        self.assertEqual(self.mm.resolve_relative("pkg/sub/leaf.py", 1, "deep").files, ("pkg/sub/deep/__init__.py",))
        self.assertEqual(self.mm.resolve_relative("pkg/sub/__init__.py", 1, "leaf").files, ("pkg/sub/leaf.py",))

    def test_two_dots_go_up_one_package(self):
        self.assertEqual(self.mm.resolve_relative("pkg/sub/leaf.py", 2, "other").files, ("pkg/other.py",))
        self.assertEqual(self.mm.resolve_relative("pkg/sub/leaf.py", 2, "").files, ("pkg/__init__.py",))
        self.assertEqual(self.mm.resolve_relative("pkg/sub/leaf.py", 2, "sub.leaf").files, ("pkg/sub/leaf.py",))

    def test_a_module_that_does_not_exist_resolves_to_nothing(self):
        res = self.mm.resolve_relative("pkg/sub/leaf.py", 1, "nothing")
        self.assertEqual((res.files, res.namespace, res.found), ((), False, False))

    def test_a_file_at_the_top_has_the_repo_root_as_its_package(self):
        res = self.mm.resolve_relative("top.py", 1, "x")
        self.assertEqual(res.files, ("x.py",))

    def test_beyond_the_top_level_is_none_not_a_crash(self):
        self.assertIsNone(self.mm.resolve_relative("pkg/mod.py", 3, "x"))
        self.assertIsNone(self.mm.resolve_relative("top.py", 2, "x"))
        self.assertIsNone(self.mm.resolve_relative("pkg/sub/leaf.py", 9, ""))

    def test_a_relative_import_is_path_based_and_ignores_roots(self):
        mm, _ = build(API, service_marker="pyproject.toml", library_roots=("libs/common",))
        self.assertEqual(mm.resolve_relative("services/billing/app/routers/invoices.py", 2, "main").files, ("services/billing/app/main.py",))
        self.assertEqual(mm.resolve_relative("services/cases/app/routers/cases.py", 2, "main").files, ("services/cases/app/main.py",))


class Stems(unittest.TestCase):
    def test_the_stem_of_a_module_is_its_file_name_and_of_a_package_its_dotted_name(self):
        mm, _ = build(API, service_marker="pyproject.toml", library_roots=("libs/common",))
        self.assertEqual(mm.stems_of("libs/common/acme_common/money.py"), ("money",))
        self.assertEqual(mm.stems_of("services/billing/app/routers/__init__.py"), ("routers",))
        self.assertEqual(mm.stems_of("services/billing/app/__init__.py"), ("app",))

    def test_a_mapped_package_has_both_names(self):
        text = '[tool.setuptools]\npackage-dir = { acme_contracts = "contracts" }\n'
        mm, _ = build(["pyproject.toml", "contracts/__init__.py", "contracts/models/invoice.py"], {"pyproject.toml": text})
        self.assertEqual(mm.stems_of("contracts/__init__.py"), ("acme_contracts", "contracts"))
        self.assertEqual(mm.stems_of("contracts/models/invoice.py"), ("invoice",))

    def test_a_file_with_no_dotted_name_still_has_a_stem(self):
        mm, _ = build(["my-dir/x.py", "my-dir/__init__.py"])
        self.assertEqual(mm.stems_of("my-dir/x.py"), ("x",))
        self.assertEqual(mm.stems_of("my-dir/__init__.py"), ("my-dir",))

    def test_a_root_level_init_has_no_stem(self):
        mm, _ = build(["__init__.py", "a.py"])
        self.assertEqual(mm.stems_of("__init__.py"), ())

    def test_owner_of_is_the_nearest_service_folder(self):
        mm, _ = build(API, service_marker="pyproject.toml", library_roots=("libs/common",))
        self.assertEqual(mm.owner_of("services/billing/app/main.py"), "services/billing")
        self.assertEqual(mm.owner_of("libs/common/acme_common/money.py"), "libs/common")
        self.assertEqual(mm.owner_of("tools/release.py"), "")


if __name__ == "__main__":
    unittest.main()
