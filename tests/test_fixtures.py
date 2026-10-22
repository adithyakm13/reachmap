"""M1: the synthetic fixtures build, and every expected.json is true of the files.

Run from the repository root: python3.12 -m unittest discover -s tests
(the root is on sys.path, so the builder is imported as tests.fixtures.build).
"""

from __future__ import annotations

import ast
import fnmatch
import json
import os
import re
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests import support_git as sg
from tests.fixtures import build
from tests.fixtures.build import build_all, evidence_revision, run_git, scenarios
from tests.fixtures.claims import (
    MODEL_FILES,
    api_routes,
    best_routes,
    changed_lines,
    client_urls,
    direct_importers,
    functions_containing_changes,
    match_kind,
    route_table_of_pages,
    symbol_changed,
    url_used_on_line,
    TS_SPEC,
    Tree,
    collect,
    git_out,
    import_target,
    is_test_path,
    occurrences,
    occurrences_of_word,
    ref_exists,
    ref_shas,
    resolve_ts,
    text_at,
    ts_importers,
)

FIXTURES = Path(build.__file__).resolve().parent

MUST_FIND_TYPES = {
    "changed_symbol",
    "changed_file",
    "caller",
    "contract_read",
    "route",
    "screen",
    "http_edge",
    "consumer",
    "unit_test",
    "finding",
    "branch_state",
}
NOT_ESTABLISHED_KINDS = {
    "hub_cutoff",
    "router_unmounted",
    "not_distinctive",
    "pre_change_unavailable",
    "hop_cap",
    "module_cap",
    "edge_unfollowed",
    "entry_points_unread",
    "routes_unmatched",
}
BANNED = {
    "\u2013": "en dash",
    "\u2014": "em dash",
    "\u2192": "arrow",
    "\u2018": "left single quote",
    "\u2019": "right single quote",
    "\u201c": "left double quote",
    "\u201d": "right double quote",
}
# ------------------------------------------------------------------------- the tests


class BuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        start = time.monotonic()
        cls.repos = build_all(Path(cls.tmp.name))
        cls.seconds = time.monotonic() - start
        cls.tree = Tree(cls.repos)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_build_is_fast(self):
        self.assertLess(self.seconds, 10, f"build took {self.seconds:.1f}s")

    def test_four_repos(self):
        self.assertEqual(sorted(self.repos), ["acme/api", "acme/contracts", "acme/web", "acme/worker"])
        for path in self.repos.values():
            self.assertTrue((path / ".git").is_dir())

    def test_branches_and_tags(self):
        expected = {
            "acme/web": ({"main", "release/2.0", "release/2.1"}, {"v2.0.0", "v2.1.0"}),
            "acme/api": ({"main", "release/1.x", "release/1.2"}, {"v1.2.0", "v1.9.0"}),
            "acme/contracts": ({"main"}, {"v1.2.0", "v1.9.0", "v1.10.0"}),
            "acme/worker": ({"main"}, set()),
        }
        for name, (branches, tags) in expected.items():
            repo = self.repos[name]
            have_b = set(git_out(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads").split())
            have_t = set(git_out(repo, "tag").split())
            self.assertTrue(branches <= have_b, f"{name}: branches {have_b}")
            self.assertTrue(tags <= have_t, f"{name}: tags {have_t}")
            self.assertEqual(git_out(repo, "branch", "--show-current").strip(), "main")

    def test_contract_tags_are_successive_commits(self):
        repo = self.repos["acme/contracts"]
        self.assertTrue(ref_exists(repo, "v1.10.0"))
        for older, newer in [("v1.2.0", "v1.9.0"), ("v1.9.0", "v1.10.0")]:
            self.assertEqual(run_git(repo, "merge-base", "--is-ancestor", older, newer).returncode, 0)
            self.assertNotEqual(git_out(repo, "rev-parse", older), git_out(repo, "rev-parse", newer))
        # The trap: as strings 1.10.0 sorts before 1.9.0.
        self.assertLess("1.10.0", "1.9.0")
        # The manifest version is a placeholder at every tag.
        for tag in ("v1.2.0", "v1.9.0", "v1.10.0"):
            self.assertIn('version = "0.0.0"', text_at(repo, tag, "pyproject.toml"))

    def test_release_branches_diverge(self):
        for name, branches in {"acme/web": ["release/2.0", "release/2.1"], "acme/api": ["release/1.x", "release/1.2"]}.items():
            repo = self.repos[name]
            for branch in branches:
                base = git_out(repo, "merge-base", "main", branch).strip()
                self.assertNotEqual(base, git_out(repo, "rev-parse", "main").strip(), f"{name} {branch}")
                self.assertNotEqual(base, git_out(repo, "rev-parse", branch).strip(), f"{name} {branch}")

    def test_manifest_pins_per_branch(self):
        api, worker = self.repos["acme/api"], self.repos["acme/worker"]
        pins = {"main": "1.10.0", "release/1.x": "1.9.0", "release/1.2": "1.2.0"}
        for branch, version in pins.items():
            self.assertIn(f"acme-contracts=={version}\n", text_at(api, branch, "requirements.txt"), branch)
        self.assertIn("acme-contracts==1.9.0\n", text_at(worker, "main", "requirements.txt"))

    def test_stale_editor_config_points_nowhere(self):
        web = self.repos["acme/web"]
        stale = json.loads(text_at(web, "main", "jsconfig.json"))
        real = json.loads(text_at(web, "main", "tsconfig.json"))
        self.assertEqual(real["compilerOptions"]["baseUrl"], ".")
        self.assertEqual(sorted(real["compilerOptions"]["paths"]), ["@/*", "@api/*", "@components/*"])
        files = self.tree.files("acme/web", "main")
        for targets in stale["compilerOptions"]["paths"].values():
            folder = targets[0].split("/")[0] + "/"
            self.assertFalse(any(f.startswith(folder) for f in files), folder)

    def test_service_markers_and_library(self):
        files = self.tree.files("acme/api", "main")
        for marker in ("services/billing/pyproject.toml", "services/cases/pyproject.toml", "libs/common/pyproject.toml"):
            self.assertIn(marker, files)

    def test_hub_has_more_than_50_importers(self):
        importers = ts_importers(self.tree, self.repos, "acme/web", "src/lib/format.ts")
        self.assertGreater(len(importers), 50)
        self.assertEqual(len(importers), 57)
        widgets = {p for p in importers if p.startswith("src/widgets/")}
        self.assertEqual(len(widgets), 55)
        self.assertTrue(importers - widgets, "some real pages import the hub too")
        # Both kinds of specifier reach the hub.
        web = self.repos["acme/web"]
        specs = {spec for p in widgets for spec in TS_SPEC.findall(text_at(web, "main", p))}
        self.assertEqual(specs, {"@/lib/format", "../lib/format"})
        # The specifier text of the line s20 removes: 28 odd widgets plus two pages; the
        # other 27 widgets reach the module relatively.
        by_alias = {p for p in importers if '"@/lib/format"' in text_at(web, "main", p)}
        self.assertEqual(len(by_alias), 30)
        self.assertEqual(len(by_alias & widgets), 28)
        self.assertEqual(len(widgets - by_alias), 27)

    def test_generic_and_distinctive_field_definitions(self):
        contracts = self.repos["acme/contracts"]
        counts: dict[str, int] = {}
        classes = 0
        for name in MODEL_FILES:
            text = text_at(contracts, "main", f"contracts/models/{name}.py")
            classes += len(re.findall(r"^class \w+\(BaseModel\):", text, re.M))
            for field in re.findall(r"^    (\w+): ", text, re.M):
                counts[field] = counts.get(field, 0) + 1
        self.assertGreater(counts["interval"], 3)
        self.assertLessEqual(counts["settlement_reference"], 2)
        self.assertEqual(counts["id"], classes, "id is defined in every model")
        self.assertEqual(counts["limit"], 1)

    def test_generic_function_name_defined_in_many_files(self):
        web = self.repos["acme/web"]
        defining = git_out(web, "grep", "-l", "const mapStateToProps", "main", "--", "src").split()
        self.assertGreater(len(defining), 3, defining)

    def test_limit_appears_in_consumers_only_in_a_comment(self):
        for name in ("acme/api", "acme/worker"):
            repo = self.repos[name]
            for path in sorted(self.tree.files(name, "main")):
                if path.endswith(".py"):
                    for in_comment in occurrences(text_at(repo, "main", path), "limit"):
                        self.assertTrue(in_comment, f"{name}:{path} uses limit in code")
        self.assertIn(True, occurrences(text_at(self.repos["acme/worker"], "main", "worker/tasks/settlement_sync.py"), "limit"))

    def test_nested_same_name_and_shared_parameter_line(self):
        api = self.repos["acme/api"]
        invoices = text_at(api, "main", "services/billing/app/routers/invoices.py")
        settlements = text_at(api, "main", "services/billing/app/routers/settlements.py")
        self.assertIn("    def _load():", invoices)
        self.assertIn("    def _load():", settlements)
        total = sum(
            text_at(api, "main", p).count("    request: Request,\n")
            for p in self.tree.files("acme/api", "main")
            if p.endswith(".py")
        )
        self.assertGreaterEqual(total, 5)

    def test_decorator_at_column_zero_spans_lines(self):
        jobs = text_at(self.repos["acme/api"], "main", "services/billing/app/jobs.py")
        self.assertRegex(jobs, r"(?m)^@scheduled\(\n    name=.*\n    cron=.*\n    retries=3,\n\)\ndef run_nightly_settlement")

    def test_endpoint_constant_written_over_two_lines(self):
        endpoints = text_at(self.repos["acme/web"], "main", "src/api/endpoints.ts")
        self.assertIn('export const SETTLEMENT_SUMMARY_URL =\n  "/api/billing/settlements/summary";', endpoints)

    def test_one_page_mounted_twice_and_one_lazy(self):
        web = self.repos["acme/web"]
        billing = text_at(web, "main", "src/modules/billing/routes.tsx")
        cases = text_at(web, "main", "src/modules/cases/routes.tsx")
        reports = text_at(web, "main", "src/modules/reports/routes.tsx")
        self.assertIn('path="/billing/invoices/:invoiceId" element={<InvoiceDetailPage />}', billing)
        self.assertIn('path="/cases/:caseId/invoices/:invoiceId" element={<InvoiceDetailPage />}', cases)
        self.assertIn('React.lazy(() => import("./pages/ScheduleReportPage"))', reports)

    def test_no_network(self):
        for path in self.repos.values():
            self.assertEqual(git_out(path, "remote").strip(), "")
        source = Path(build.__file__).read_text(encoding="utf-8")
        for word in ("import socket", "import urllib", "import http", "import requests", "ssl"):
            self.assertNotIn(word, source)

    def test_scenario_branches_exist(self):
        for sc in scenarios(Path(self.tmp.name)):
            self.assertTrue(ref_exists(sc["repo_path"], sc["change"]["head"]), sc["id"])
            self.assertTrue(ref_exists(sc["repo_path"], sc["change"]["base"]), sc["id"])


class BuilderGuardsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repos = sg.fixture_repos()  # read only: the build every module shares
        cls.dest = cls.repos["acme/web"].parent

    def test_file_at_is_three_way(self):
        web = self.repos["acme/web"]
        status, text = build.file_at(web, "main", "tsconfig.json")
        self.assertEqual(status, "found")
        self.assertIn("baseUrl", text)
        self.assertEqual(build.file_at(web, "main", "src/no/such/file.ts"), ("absent", None))
        self.assertEqual(build.file_at(web, "v2.0.0", "src/modules/billing/api.ts")[0], "found")
        # Anything but "does not exist at that revision" is unreadable, never absent.
        for rev in ("no-such-revision", "0" * 40):
            status, detail = build.file_at(web, rev, "tsconfig.json")
            self.assertEqual(status, "unreadable", rev)
            self.assertTrue(detail)
        status, detail = build.file_at(self.dest / "missing-repo", "main", "tsconfig.json")
        self.assertEqual(status, "unreadable")
        self.assertIsNone(text_at(web, "main", "src/no/such/file.ts"))
        with self.assertRaises(AssertionError):
            text_at(web, "no-such-revision", "tsconfig.json")

    def test_overlay_skips_dotfiles_and_backups(self):
        for rel in (".DS_Store", "src/.DS_Store", ".hidden/file.ts", "src/.a.ts.swp", "src/a.ts~"):
            self.assertFalse(build.wanted(Path(rel)), rel)
        for rel in (".delete", "src/a.ts", "src/dir/b.py", "README.md"):
            self.assertTrue(build.wanted(Path(rel)), rel)
        with tempfile.TemporaryDirectory() as tmp:
            src, dest = Path(tmp) / "src", Path(tmp) / "dest"
            (src / "sub").mkdir(parents=True)
            dest.mkdir()
            (dest / "old.txt").write_text("x")
            for rel in ("a.txt", "sub/b.txt", ".DS_Store", "sub/.DS_Store", ".a.txt.swp", "a.txt~"):
                (src / rel).write_text(rel)
            (src / ".delete").write_text("old.txt\n")
            build.apply_overlay(src, dest)
            got = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file())
            self.assertEqual(got, ["a.txt", "sub/b.txt"])

    def test_build_ignores_a_hostile_git_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            hooks, marker = tmp / "hooks", tmp / "hook-ran"
            hooks.mkdir()
            for name in ("pre-commit", "commit-msg", "post-commit", "post-checkout", "pre-push"):
                hook = hooks / name
                hook.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
                hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
            config = (
                "[commit]\n\tgpgsign = true\n[tag]\n\tgpgsign = true\n[gpg]\n\tprogram = /nonexistent/gpg\n"
                f"[core]\n\thooksPath = {hooks}\n\tautocrlf = true\n"
                "[user]\n\tname = Hostile\n\temail = hostile@example.invalid\n[init]\n\tdefaultBranch = trunk\n"
            )
            gitconfig = tmp / "gitconfig"
            gitconfig.write_text(config)
            home = tmp / "home"
            home.mkdir()
            (home / ".gitconfig").write_text(config)
            hostile = {
                "GIT_CONFIG_GLOBAL": str(gitconfig),
                "HOME": str(home),
                "XDG_CONFIG_HOME": str(tmp / "xdg"),
                "GIT_AUTHOR_NAME": "Hostile",
                "GIT_AUTHOR_EMAIL": "hostile@example.invalid",
                "GIT_COMMITTER_EMAIL": "hostile@example.invalid",
                "GIT_AUTHOR_DATE": "2001-01-01T00:00:00",
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "core.hooksPath",
                "GIT_CONFIG_VALUE_0": str(hooks),
            }
            with mock.patch.dict(os.environ, hostile):
                built = build_all(tmp / "hostile")
            self.assertFalse(marker.exists(), "a hook from the hostile configuration ran")
            for name, path in built.items():
                self.assertEqual(ref_shas(path), ref_shas(self.repos[name]), name)
                self.assertNotIn("hostile", git_out(path, "log", "--all", "--format=%ae %an"))


class DeterminismTest(unittest.TestCase):
    def test_two_builds_have_identical_shas(self):
        """The shared build (made earlier, elsewhere) and a new one in another directory."""
        first = sg.fixture_repos()
        with tempfile.TemporaryDirectory() as two:
            second = build_all(Path(two))
            for name in first:
                a, b = ref_shas(first[name]), ref_shas(second[name])
                self.assertEqual(a, b, name)
                self.assertTrue(a.strip(), name)

    def test_build_ignores_the_users_git_config(self):
        # The environment handed to git cuts off every configuration file.
        env = build.git_env(Path("/nonexistent"))
        self.assertEqual(env["GIT_CONFIG_GLOBAL"], "/dev/null")
        self.assertEqual(env["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(env["GIT_AUTHOR_NAME"], build.AUTHOR_NAME)


class ScenarioTest(unittest.TestCase):
    """Every expected.json is checked against the built repositories."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.repos = sg.fixture_repos()  # read only: the build every module shares
        cls.dest = cls.repos["acme/web"].parent
        cls.tree = Tree(cls.repos)
        cls.scenarios = scenarios(cls.dest)

    def test_there_are_at_least_17_scenarios(self):
        self.assertGreaterEqual(len(self.scenarios), 17)

    def test_ids_and_schema(self):
        folders = [p.name for p in build.m1_scenario_dirs()]
        self.assertEqual([sc["id"] for sc in self.scenarios], folders)
        wanted = {f"s{n:02d}" for n in range(1, 18)}
        self.assertTrue(wanted <= {f.split("-")[0] for f in folders})
        for sc in self.scenarios:
            with self.subTest(scenario=sc["id"]):
                exp = sc["expected"]
                self.assertEqual(sc["dir"].name, sc["id"])
                self.assertEqual(exp["schema"], 1)
                self.assertEqual(exp["id"], sc["id"])
                self.assertEqual(exp["repo"], sc["repo"])
                self.assertEqual(exp["change"], sc["change"])
                self.assertIn(sc["repo"], self.repos)
                for key, value in sc["scenario"].get("limits", {}).items():
                    self.assertIn(key, build.load_workspace()["limits"])
                    self.assertIsInstance(value, int)
                self.assertIsInstance(exp["explains"], str)
                self.assertGreater(len(exp["explains"]), 80, "the hand trace is written out")
                for key in ("must_find", "must_exclude", "not_established"):
                    self.assertIsInstance(exp[key], list)
                for item in exp["must_find"] + exp["must_exclude"]:
                    self.assertIn(item["type"], MUST_FIND_TYPES)
                for kind in exp.get("complete_for", []):
                    self.assertIn(kind, MUST_FIND_TYPES)
                for item in exp["not_established"]:
                    self.assertIn(item["kind"], NOT_ESTABLISHED_KINDS)
                    self.assertIsInstance(item["blocking"], bool)
                    self.assertTrue(item["subject"])
                self.assertTrue(exp["must_find"] or exp["not_established"], "an empty answer proves nothing")

    def test_refs_and_diffs(self):
        for sc in self.scenarios:
            with self.subTest(scenario=sc["id"]):
                repo, change = sc["repo_path"], sc["change"]
                self.assertTrue(ref_exists(repo, change["head"]))
                self.assertTrue(ref_exists(repo, change["base"]))
                if sc["scenario"].get("orphan"):
                    self.assertNotEqual(run_git(repo, "merge-base", change["base"], change["head"]).returncode, 0)
                    self.assertEqual(sc["id"], "s16-no-merge-base")
                    continue
                names = git_out(repo, "diff", "--name-only", change["base"], change["head"]).split()
                self.assertTrue(names, "the diff against the base is empty")
                if change["kind"] == "compare":
                    self.assertEqual(git_out(repo, "merge-base", change["base"], change["head"]).strip(),
                                     git_out(repo, "rev-parse", change["base"]).strip())
                    self.assertEqual(sc["scenario"]["change"]["head"], f"scenario/{sc['id']}")
                else:
                    self.assertEqual(change["kind"], "range")
                    self.assertFalse((sc["dir"] / "changes").exists())

    def test_locators_exist_and_name_code(self):
        total = 0
        for sc in self.scenarios:
            with self.subTest(scenario=sc["id"]):
                locators, comments, _ = [], [], []
                collect(sc["expected"], sc["repo"], locators, comments, [])
                for repo, loc, parent in locators:
                    total += 1
                    rev = evidence_revision(loc, repo, sc["repo"], sc["change"], parent)
                    text = text_at(self.repos[repo], rev, loc["path"])
                    where = f"{repo}@{rev}:{loc['path']}"
                    self.assertIsNotNone(text, f"missing {where}")
                    needle = loc["contains"]
                    self.assertTrue(needle and "\n" not in needle, f"{where}: contains must be one line")
                    flags = occurrences(text, needle)
                    self.assertTrue(flags, f"{where} does not contain {needle!r}")
                    self.assertIn(False, flags, f"{where}: {needle!r} only occurs in comments")
                for repo, loc, parent in comments:
                    rev = evidence_revision(loc, repo, sc["repo"], sc["change"], parent)
                    text = text_at(self.repos[repo], rev, loc["path"])
                    self.assertIsNotNone(text, f"missing {repo}:{loc['path']}")
                    flags = occurrences(text, loc["contains"])
                    self.assertTrue(flags and all(flags), f"{loc} is not a comment")
        self.assertGreater(total, 100)

    def test_entries_name_files_and_symbols_that_exist(self):
        for sc in self.scenarios:
            with self.subTest(scenario=sc["id"]):
                locators, comments, entries = [], [], []
                collect(sc["expected"], sc["repo"], locators, comments, entries)
                for repo, entry in entries:
                    if entry["type"] in ("route", "http_edge", "finding", "screen", "consumer", "branch_state"):
                        continue
                    removed = entry.get("change") == "removed"
                    rev = sc["change"]["base"] if removed else evidence_revision(entry, repo, sc["repo"], sc["change"])
                    text = text_at(self.repos[repo], rev, entry["path"])
                    self.assertIsNotNone(text, f"{repo}@{rev}:{entry['path']}")
                    if "symbol" in entry:
                        last = entry["symbol"].split(".")[-1]
                        self.assertRegex(text, rf"\b{re.escape(last)}\b", f"{entry['path']} lacks {last}")
                    if "nested" in entry:
                        self.assertIn(f"def {entry['nested']}(", text)
                for item in sc["expected"]["must_exclude"]:
                    if "path_prefix" in item:
                        files = self.tree.files(item["repo"], "main")
                        self.assertTrue(any(f.startswith(item["path_prefix"]) for f in files), item)

    def test_chains_are_real_import_chains(self):
        checked = 0
        for sc in self.scenarios:
            with self.subTest(scenario=sc["id"]):
                locators, comments, entries = [], [], []
                collect(sc["expected"], sc["repo"], locators, comments, entries)
                for item in sc["expected"]["must_find"] + sc["expected"]["must_exclude"]:
                    chain = item.get("chain", [])
                    for i, entry in enumerate(chain):
                        repo = entry["repo"]
                        rev = evidence_revision(entry, repo, sc["repo"], sc["change"])
                        importer = entry["path"]
                        target = import_target(self.tree, repo, rev, importer, entry["contains"])
                        if target is None:
                            continue  # not an import line
                        checked += 1
                        self.assertIsNotNone(target[1], f"{sc['id']}: cannot resolve {entry['contains']!r} in {importer}")
                        if i >= 1:
                            prev = chain[i - 1]
                            self.assertEqual(target, (prev["repo"], prev["path"]),
                                             f"{sc['id']}: {importer} imports {target}, not the previous hop")
        self.assertGreater(checked, 10)

    def test_branch_state_claims_match_the_files(self):
        seen = 0
        for sc in self.scenarios:
            with self.subTest(scenario=sc["id"]):
                for item in sc["expected"]["must_find"]:
                    if item["type"] != "branch_state":
                        continue
                    seen += 1
                    repo = self.repos[item["repo"]]
                    change = sc["change"]
                    diff = git_out(repo, "diff", "--unified=0", change["base"], change["head"])
                    path, added, removed = None, [], []
                    for line in diff.splitlines():
                        if line.startswith("+++ b/"):
                            path = line[6:]
                        elif line.startswith("+") and not line.startswith("+++"):
                            added.append((path, line[1:]))
                        elif line.startswith("-") and not line.startswith("---"):
                            removed.append((path, line[1:]))
                    self.assertTrue(added)
                    have = lambda p, l: l in (text_at(repo, item["branch"], p) or "").splitlines()
                    all_added = all(have(p, l) for p, l in added)
                    any_removed = any(have(p, l) for p, l in removed)
                    if item["state"] == "present":
                        self.assertTrue(all_added, item)
                    elif item["state"] == "pre_change":
                        self.assertFalse(any(have(p, l) for p, l in added), item)
                        self.assertTrue(any_removed, item)
                    else:
                        self.fail(f"unknown state {item['state']}")
        self.assertGreaterEqual(seen, 3)

    def test_screen_routes_are_declared(self):
        web = self.repos["acme/web"]
        route_files = [p for p in self.tree.files("acme/web", "main") if p.endswith("/routes.tsx")]
        declared = "\n".join(text_at(web, "main", p) for p in route_files)
        self.assertEqual(len(route_files), 3)
        seen = 0
        for sc in self.scenarios:
            with self.subTest(scenario=sc["id"]):
                for item in sc["expected"]["must_find"] + sc["expected"]["must_exclude"]:
                    if item["type"] == "screen":
                        for route in [item["route"], *item.get("also_mounted", [])]:
                            seen += 1
                            self.assertIn(f'path="{route}"', declared, f"{sc['id']}: no Route for {route}")
        self.assertGreater(seen, 10)

    def test_every_finding_and_claim_has_evidence(self):
        for sc in self.scenarios:
            with self.subTest(scenario=sc["id"]):
                for item in sc["expected"]["must_find"]:
                    kind = item["type"]
                    if kind in ("route", "caller", "contract_read", "consumer", "finding"):
                        self.assertTrue(item.get("evidence"), f"{sc['id']}: {item}")
                    if kind == "screen":
                        self.assertTrue(item.get("chain"), f"{sc['id']}: {item}")
                    if kind == "http_edge":
                        self.assertIn("contains", item["from"])
                        self.assertTrue(item["to"].get("evidence"))
                for item in sc["expected"]["must_exclude"]:
                    self.assertTrue(item.get("why"), f"{sc['id']}: exclusion without a reason")


    def test_also_mounted_evidence_resolves_to_the_screen_page(self):
        seen = 0
        for sc in self.scenarios:
            with self.subTest(scenario=sc["id"]):
                for item in sc["expected"]["must_find"] + sc["expected"]["must_exclude"]:
                    for ev in item.get("also_mounted_evidence", []):
                        target = import_target(self.tree, ev["repo"], "main", ev["path"], ev["contains"])
                        if target is None:
                            continue
                        seen += 1
                        page = "src/modules/billing/pages/InvoiceDetailPage.tsx"
                        self.assertEqual(target, (ev["repo"], page), sc["id"])
        self.assertGreaterEqual(seen, 3)

    def test_consumer_claims_match_the_manifests_and_tags(self):
        contracts = self.repos["acme/contracts"]
        seen = 0
        for sc in self.scenarios:
            with self.subTest(scenario=sc["id"]):
                for item in sc["expected"]["must_find"]:
                    if item["type"] != "consumer":
                        continue
                    seen += 1
                    text = text_at(self.repos[item["repo"]], item["branch"], "requirements.txt")
                    self.assertIn(f"acme-contracts=={item['pin']}\n", text, f"{sc['id']}: {item}")
                    self.assertIn(item["adoption"], ("adopted", "on_adoption"))
                    if "contains_change" in item:
                        # Containment is by tag ancestry, never by comparing version numbers.
                        head = sc["change"]["head"]
                        contains = run_git(contracts, "merge-base", "--is-ancestor", head, f"v{item['pin']}").returncode == 0
                        self.assertEqual(contains, item["contains_change"], f"{sc['id']}: {item}")
                        self.assertEqual(item["adoption"], "adopted" if contains else "on_adoption")
                    else:
                        self.assertEqual(item["adoption"], "on_adoption", "an unreleased change is adopted by nobody")
        self.assertGreaterEqual(seen, 6)

    def test_hub_cutoff_claims_the_real_importer_count(self):
        seen = 0
        for sc in self.scenarios:
            with self.subTest(scenario=sc["id"]):
                for item in sc["expected"]["not_established"]:
                    if item["kind"] == "hub_cutoff":
                        seen += 1
                        found = ts_importers(self.tree, self.repos, sc["repo"], item["subject"])
                        self.assertEqual(len(found), item["importers"])
                        self.assertGreater(len(found), build.load_workspace()["limits"]["hub_importers"])
        self.assertEqual(seen, 1)

    def test_not_distinctive_counts_are_definition_counts(self):
        web, contracts = self.repos["acme/web"], self.repos["acme/contracts"]
        seen = 0
        for sc in self.scenarios:
            with self.subTest(scenario=sc["id"]):
                for item in sc["expected"]["not_established"]:
                    if item["kind"] != "not_distinctive":
                        continue
                    seen += 1
                    if item["subject"] == "mapStateToProps":
                        count = len(git_out(web, "grep", "-l", "const mapStateToProps", "main", "--", "src").split())
                    else:
                        count = sum(
                            len(re.findall(rf"^    {item['subject']}: ", text_at(contracts, "main", f"contracts/models/{n}.py"), re.M))
                            for n in MODEL_FILES
                        )
                    self.assertEqual(count, item["definitions"], item)
                    self.assertGreater(count, build.load_workspace()["limits"]["distinct_max"])
        self.assertEqual(seen, 2)

    def test_consumer_listing_policy(self):
        """Unreleased change: each consumer once, from main, on_adoption. Released change:
        one entry per configured branch of each consumer."""
        ws = build.load_workspace()
        consumers = sorted({e["consumer"] for e in ws["edges"] if e["kind"] == "package"})
        contracts = self.repos["acme/contracts"]
        seen = {"released": 0, "unreleased": 0}
        for sc in self.scenarios:
            entries = [i for i in sc["expected"]["must_find"] if i["type"] == "consumer"]
            if not entries:
                continue
            with self.subTest(scenario=sc["id"]):
                released = bool(git_out(contracts, "tag", "--contains", sc["change"]["head"]).split())
                by_repo = {}
                for item in entries:
                    by_repo.setdefault(item["repo"], []).append(item)
                self.assertEqual(sorted(by_repo), consumers)
                seen["released" if released else "unreleased"] += 1
                for repo, items in by_repo.items():
                    branches = sorted(i["branch"] for i in items)
                    if not released:
                        self.assertEqual(branches, ["main"], repo)
                        self.assertEqual(items[0]["adoption"], "on_adoption")
                    else:
                        have = git_out(self.repos[repo], "for-each-ref", "--format=%(refname:short)", "refs/heads").split()
                        patterns = ws["repos"][repo]["branches"]
                        configured = sorted(b for b in have if not b.startswith("scenario/")
                                            and any(fnmatch.fnmatch(b, pat) for pat in patterns))
                        self.assertEqual(branches, configured, repo)
        self.assertEqual(seen, {"released": 1, "unreleased": 2})

    def test_changed_file_roles_follow_the_repo_markers(self):
        ws = build.load_workspace()
        seen = 0
        for sc in self.scenarios:
            with self.subTest(scenario=sc["id"]):
                items = sc["expected"]["must_find"] + sc["expected"]["must_exclude"]
                for item in items:
                    markers = ws["repos"].get(item.get("repo"), {}).get("tests", {}).get("markers")
                    if not markers or "path" not in item:
                        continue
                    if item["type"] == "changed_file":
                        seen += 1
                        self.assertEqual(is_test_path(item["path"], markers), item["role"] == "test", item)
                    elif item["type"] == "unit_test":
                        seen += 1
                        in_must_find = item in sc["expected"]["must_find"]
                        self.assertEqual(is_test_path(item["path"], markers), in_must_find, item)
        self.assertGreaterEqual(seen, 10)

    def test_markers_match_by_segment_not_by_substring(self):
        web = build.load_workspace()["repos"]["acme/web"]["tests"]["markers"]
        api = build.load_workspace()["repos"]["acme/api"]["tests"]["markers"]
        self.assertFalse(is_test_path("src/contest/engine.ts", web))
        self.assertTrue(is_test_path("src/components/__tests__/StatusBadge.test.tsx", web))
        self.assertTrue(is_test_path("src/lib/dates.spec.ts", web))
        self.assertFalse(is_test_path("services/billing/app/contests/replay.py", api))
        self.assertFalse(is_test_path("services/billing/app/latest_rates.py", api))
        self.assertTrue(is_test_path("services/billing/tests/test_money.py", api))
        self.assertTrue(is_test_path("tests/anything.py", api))
        # The traps are real substring collisions.
        self.assertIn("tests/", "services/billing/app/contests/replay.py")
        self.assertIn("test_", "services/billing/app/latest_rates.py")

    def test_not_established_subjects_have_the_documented_format(self):
        ws = build.load_workspace()
        edges = set()
        for e in ws["edges"]:
            a, b = (e["producer"], e["consumer"]) if "producer" in e else (e["from"], e["to"])
            edges.add(f"edge:{e['kind']}:{a}:{b}")
        patterns = {
            "hub_cutoff": r"[\w./-]+",
            "router_unmounted": r"[\w./-]+\.py:\w+",
            "not_distinctive": r"\w+",
            "pre_change_unavailable": r"[\w./-]+",
            "hop_cap": r"edge:\w+:acme/\w+:acme/\w+",
            "module_cap": r"hop:\d+",
            "edge_unfollowed": r"edge:\w+:acme/\w+:acme/\w+",
            "entry_points_unread": r"acme/\w+",
            "routes_unmatched": r"acme/\w+:[\w./*{},-]+",
        }
        seen = set()
        for sc in self.scenarios:
            with self.subTest(scenario=sc["id"]):
                for item in sc["expected"]["not_established"]:
                    kind, subject = item["kind"], item["subject"]
                    self.assertRegex(subject, f"^{patterns[kind]}$")
                    seen.add(kind)
                    repo = self.repos[sc["repo"]]
                    if kind == "hub_cutoff":
                        self.assertIsNotNone(text_at(repo, "main", subject))
                    elif kind == "router_unmounted":
                        path, var = subject.split(":")
                        self.assertRegex(text_at(repo, sc["change"]["head"], path), rf"(?m)^{var} = APIRouter\(")
                    elif kind == "pre_change_unavailable":
                        self.assertEqual(subject, sc["change"]["head"])
                    elif kind == "hop_cap":
                        self.assertIn(subject, edges)
                    elif kind == "edge_unfollowed":
                        self.assertIn(subject, edges)
                        self.assertEqual(subject.split(":")[1], "event")  # the one edge kind of the fixture workspace that has no adapter
                    elif kind == "entry_points_unread":
                        self.assertEqual(ws["repos"][subject]["entry_points"]["kind"], "celery")  # the one entry-point kind that has no adapter
                    elif kind == "routes_unmatched":
                        name, glob = subject.split(":", 1)
                        self.assertEqual(ws["repos"][name]["entry_points"]["routes_glob"], glob)  # the configured glob
                        rx = re.compile("^" + re.escape(glob).replace(r"\*", "[^/]*") + "$")
                        files = git_out(self.repos[name], "ls-tree", "-r", "--name-only", sc["change"]["head"]).split("\n")
                        self.assertEqual([p for p in files if rx.match(p)], [])  # the head tree has no file it matches
        self.assertEqual(seen, {"hub_cutoff", "router_unmounted", "not_distinctive", "pre_change_unavailable", "hop_cap", "edge_unfollowed", "entry_points_unread", "routes_unmatched"})

    def test_s17_consumer_evidence_resolves_to_the_consumer_branch(self):
        s17 = next(sc for sc in self.scenarios if sc["id"] == "s17-version-order")
        seen = 0
        for item in s17["expected"]["must_find"]:
            if item["type"] != "consumer":
                continue
            for ev in item["evidence"]:
                seen += 1
                self.assertEqual(ev["branch"], item["branch"], "the branch is also written on the evidence")
                bare = {k: v for k, v in ev.items() if k != "branch"}
                got = evidence_revision(bare, item["repo"], s17["repo"], s17["change"], parent=item)
                self.assertEqual(got, item["branch"], item)
                self.assertEqual(evidence_revision(ev, item["repo"], s17["repo"], s17["change"]), item["branch"])
        self.assertEqual(seen, 4)
        # collect() hands evidence_revision the same parent.
        locators = []
        collect(s17["expected"], s17["repo"], locators, [], [])
        for repo, loc, parent in locators:
            if "path" in loc and loc["path"] == "requirements.txt":
                self.assertEqual(parent["type"], "consumer")
                bare = {k: v for k, v in loc.items() if k != "branch"}
                self.assertEqual(evidence_revision(bare, repo, s17["repo"], s17["change"], parent), parent["branch"])

    # ---------------------------------------------------------------- independent claims
    # These tests recompute facts from the repositories (diffs, spans, imports, mounts)
    # and compare them with what expected.json says, so a wrong claim fails.

    def entries_of(self, sc, kind, *, exclude=False):
        return [i for i in sc["expected"]["must_exclude" if exclude else "must_find"] if i["type"] == kind]

    def markers_of(self, repo):
        return build.load_workspace()["repos"][repo].get("tests", {}).get("markers", [])

    def changed_set(self, sc):
        repo = self.repos[sc["repo"]]
        return {p for p in changed_lines(repo, sc["change"]["base"], sc["change"]["head"])}

    def routes_at(self, rev):
        cache = self.__dict__.setdefault("_routes", {})
        if rev not in cache:
            cache[rev] = api_routes(self.tree, self.repos, "acme/api", rev)
        return cache[rev]

    def api_rev(self, sc):
        return sc["change"]["head"] if sc["repo"] == "acme/api" else "main"

    def test_changed_symbols_match_the_diff(self):
        checked = 0
        for sc in self.scenarios:
            if sc["scenario"].get("orphan"):
                continue
            with self.subTest(scenario=sc["id"]):
                repo, change = self.repos[sc["repo"]], sc["change"]
                diff = changed_lines(repo, change["base"], change["head"])
                listed = set()
                items = [(i, True) for i in self.entries_of(sc, "changed_symbol")]
                items += [(i, False) for i in self.entries_of(sc, "changed_symbol", exclude=True)]
                for item, wanted in items:
                    if item["repo"] != sc["repo"]:
                        continue
                    head, base = text_at(repo, change["head"], item["path"]), text_at(repo, change["base"], item["path"])
                    got = symbol_changed(item["path"], item["symbol"], item.get("nested"), head, base, diff,
                                         removed=item.get("change") == "removed")
                    self.assertEqual(got, wanted, f"{item['path']} {item['symbol']}: the diff "
                                     f"{'does not touch' if wanted else 'touches'} it")
                    checked += 1
                    if wanted and "." not in item["symbol"]:
                        listed.add((item["path"], item["symbol"]))
                if "changed_symbol" in sc["expected"]["complete_for"]:
                    for path, entry in diff.items():
                        head, base = text_at(repo, change["head"], path), text_at(repo, change["base"], path)
                        for name in functions_containing_changes(path, head, base, entry):
                            self.assertIn((path, name), listed, f"{path}: changed function {name} is not listed")
        self.assertGreaterEqual(checked, 29)

    def test_chains_start_at_the_change_and_routes_name_their_page(self):
        checked = routes = 0
        for sc in self.scenarios:
            with self.subTest(scenario=sc["id"]):
                changed = {(sc["repo"], p) for p in self.changed_set(sc)} if not sc["scenario"].get("orphan") else set()
                edge_starts = {(e["from"]["repo"], e["from"]["path"], e["from"]["contains"]) for e in self.entries_of(sc, "http_edge")}
                for item in sc["expected"]["must_find"] + sc["expected"]["must_exclude"]:
                    chain = item.get("chain")
                    if not chain:
                        continue
                    first = chain[0]
                    rev = evidence_revision(first, first["repo"], sc["repo"], sc["change"])
                    target = import_target(self.tree, first["repo"], rev, first["path"], first["contains"])
                    start = (first["repo"], first["path"], first["contains"])
                    self.assertTrue(
                        (first["repo"], first["path"]) in changed or (target in changed) or start in edge_starts,
                        f"{sc['id']}: hop 0 is neither the changed file, an import of it, nor the client line of an http edge")
                    checked += 1
                    for i, hop in enumerate(chain):
                        m = re.search(r'<Route path="([^"]+)" element=\{<(\w+) />\} />', hop["contains"])
                        if m:
                            routes += 1
                            self.route_hop_names_its_page(sc, chain[i - 1], hop, m)
                            if i == len(chain) - 1 and item["type"] == "screen":
                                self.assertEqual(m.group(1), item["route"], sc["id"])
                    for ev in item.get("also_mounted_evidence", []):
                        m = re.search(r'<Route path="([^"]+)" element=\{<(\w+) />\} />', ev["contains"])
                        if m:
                            self.assertIn(m.group(1), item["also_mounted"])
                            imports = [e for e in item["also_mounted_evidence"] if e["contains"].startswith("import ")]
                            self.route_hop_names_its_page(sc, imports[0], ev, m)
        self.assertGreaterEqual(checked, 13)
        self.assertGreaterEqual(routes, 13)

    def route_hop_names_its_page(self, sc, previous, hop, match):
        """A Route hop must follow the import of the page component it renders, and that
        file must define the component."""
        imp = re.match(r"import (\w+) from", previous["contains"])
        self.assertIsNotNone(imp, f"{sc['id']}: the hop before a Route is not a default import")
        self.assertEqual(imp.group(1), match.group(2), sc["id"])
        rev = evidence_revision(previous, previous["repo"], sc["repo"], sc["change"])
        target = import_target(self.tree, previous["repo"], rev, previous["path"], previous["contains"])
        page = text_at(self.repos[target[0]], rev, target[1])
        self.assertRegex(page, rf"export (default )?(function|const) {match.group(2)}\b", target)
        self.assertEqual(previous["path"], hop["path"], "the import and the Route are in the same routes file")

    def test_callers_import_the_changed_file(self):
        checked = 0
        for sc in self.scenarios:
            if sc["scenario"].get("orphan"):
                continue
            with self.subTest(scenario=sc["id"]):
                changed = self.changed_set(sc)
                for item, exclude in [(i, False) for i in self.entries_of(sc, "caller")] + [
                    (i, True) for i in self.entries_of(sc, "caller", exclude=True)
                ]:
                    if "path" not in item or "evidence" not in item:
                        continue
                    targets = []
                    for ev in item["evidence"]:
                        rev = evidence_revision(ev, ev["repo"], sc["repo"], sc["change"])
                        target = import_target(self.tree, ev["repo"], rev, ev["path"], ev["contains"])
                        if target:
                            self.assertEqual(ev["path"], item["path"], "an import line cited for a caller is in the caller file")
                            targets.append(target)
                    if not targets and exclude:
                        continue  # an exclusion cited by a guard and a call site, not by an import
                    checked += 1
                    self.assertTrue(any(t[0] == sc["repo"] and t[1] in changed for t in targets),
                                    f"{sc['id']}: {item['path']} cites no import of a changed file ({targets})")
        self.assertGreater(checked, 12)

    def test_complete_caller_and_screen_sets_are_the_real_ones(self):
        callers = screens = 0
        for sc in self.scenarios:
            if sc["scenario"].get("orphan") or not sc["expected"]["complete_for"]:
                continue
            with self.subTest(scenario=sc["id"]):
                repo, head = sc["repo"], sc["change"]["head"]
                markers = self.markers_of(repo) or self.markers_of("acme/api")
                sources = sorted(p for p in self.changed_set(sc)
                                 if p.endswith((".ts", ".tsx", ".py")) and not is_test_path(p, markers))
                found = self.entries_of(sc, "caller")
                excluded = self.entries_of(sc, "caller", exclude=True)
                named = {i["path"] for i in found + excluded if "path" in i}
                prefixes = [i["path_prefix"] for i in excluded if "path_prefix" in i]
                if "caller" in sc["expected"]["complete_for"]:
                    callers += 1
                    importers = set()
                    for path in sources:
                        importers |= direct_importers(self.tree, self.repos, repo, head, path)
                    importers -= set(sources)
                    for path in sorted(importers):
                        if is_test_path(path, markers):
                            continue
                        self.assertTrue(path in named or any(path.startswith(p) for p in prefixes),
                                        f"{sc['id']}: importer {path} is neither a listed caller nor a stated exclusion")
                    for item in found:
                        self.assertIn(item["path"], importers, f"{sc['id']}: caller {item['path']} does not import a changed file")
                if "screen" in sc["expected"]["complete_for"] and repo == "acme/web":
                    screens += 1
                    table = route_table_of_pages(self.tree, self.repos, repo, head)
                    pages = set(sources) | {i["path"] for i in found}
                    computed = set().union(*(table.get(p, set()) for p in pages))
                    expected = set()
                    for s in self.entries_of(sc, "screen"):
                        expected |= {s["route"], *s.get("also_mounted", [])}
                    self.assertEqual(computed, expected, sc["id"])
        self.assertGreaterEqual(callers, 7)
        self.assertGreaterEqual(screens, 7)

    def test_guarded_calls_follow_the_guard(self):
        checked = 0
        for sc in self.scenarios:
            for sym in self.entries_of(sc, "changed_symbol"):
                guards = [ev for ev in sym.get("evidence", []) if re.match(r'if \((\w+) === "(\w+)"\) \{', ev["contains"])]
                if not guards:
                    continue
                with self.subTest(scenario=sc["id"]):
                    param, literal = re.match(r'if \((\w+) === "(\w+)"\) \{', guards[0]["contains"]).groups()
                    repo, head = self.repos[sc["repo"]], sc["change"]["head"]
                    source = text_at(repo, head, sym["path"])
                    signature = re.search(rf"function {sym['symbol']}\((.*)\)\s*:", source).group(1)
                    names = [p.split(":")[0].strip() for p in signature.split(",")]
                    index = names.index(param)
                    callers = self.entries_of(sc, "caller") + self.entries_of(sc, "caller", exclude=True)
                    for item in callers:
                        text = text_at(repo, head, item["path"])
                        call = re.search(rf"{sym['symbol']}\(([^()]*)\)", text.split("export default")[1] if "export default" in text else text)
                        args = [a.strip() for a in call.group(1).split(",")]
                        arg = args[index]
                        if arg == f'"{literal}"':
                            expected = ("find", f"literal_{literal}")
                        elif re.fullmatch(r'"[^"]*"', arg):
                            expected = ("exclude", None)
                        else:
                            expected = ("find", "undecidable")
                        actual = ("exclude", None) if item in self.entries_of(sc, "caller", exclude=True) else ("find", item.get("reason"))
                        self.assertEqual(actual, expected, f"{item['path']}: argument {arg}")
                        checked += 1
        self.assertEqual(checked, 3)

    def test_contract_reads_are_the_real_ones(self):
        ws = build.load_workspace()
        contracts = self.repos["acme/contracts"]
        checked = 0
        for sc in self.scenarios:
            if "contract_read" not in sc["expected"]["complete_for"]:
                continue
            checked += 1
            with self.subTest(scenario=sc["id"]):
                field = self.entries_of(sc, "changed_symbol")[0]["symbol"].split(".")[-1]
                definitions = sum(
                    len(re.findall(rf"(?m)^    {field}\s*:", text_at(contracts, sc["change"]["head"], f"contracts/models/{n}.py")))
                    for n in MODEL_FILES)
                expected = set()
                if definitions <= ws["limits"]["distinct_max"]:
                    for repo in ("acme/api", "acme/worker"):
                        for path in sorted(self.tree.files(repo, "main")):
                            if not path.endswith(".py") or is_test_path(path, ["tests/", "test_"]):
                                continue
                            text = text_at(self.repos[repo], "main", path)
                            if any(not is_comment for is_comment in occurrences_of_word(text, field)):
                                expected.add((repo, path))
                actual = {(i["repo"], i["path"]) for i in self.entries_of(sc, "contract_read")}
                self.assertEqual(actual, expected, sc["id"])
        self.assertEqual(checked, 4)

    def test_http_edges_match_routes_and_findings_match_the_files(self):
        web = self.repos["acme/web"]
        edges = findings = 0
        for sc in self.scenarios:
            with self.subTest(scenario=sc["id"]):
                for item, exclude in [(i, False) for i in self.entries_of(sc, "http_edge")] + [
                    (i, True) for i in self.entries_of(sc, "http_edge", exclude=True)
                ]:
                    fr, to = item["from"], item["to"]
                    rev = evidence_revision(fr, fr["repo"], sc["repo"], sc["change"])
                    urls = client_urls(text_at(web, rev, "src/api/endpoints.ts"))
                    url = url_used_on_line(fr["contains"], urls)
                    self.assertIsNotNone(url, f"{sc['id']}: {fr['contains']!r} passes no known endpoint to apiGet")
                    routes, _ = self.routes_at(sc["change"]["base"] if to.get("removed") else self.api_rev(sc))
                    best = [r["path"] for r in best_routes(url, routes, to["method"])]
                    edges += 1
                    if exclude:
                        if not sc["scenario"].get("limits"):  # a hop cap excludes a real edge
                            self.assertNotIn(to["path"], best, f"{sc['id']}: {url} does fit {to['path']} best")
                    else:
                        self.assertEqual(best, [to["path"]], f"{sc['id']}: {url} against {to['path']}")
                for f in self.entries_of(sc, "finding"):
                    findings += 1
                    base_routes, _ = self.routes_at(sc["change"]["base"])
                    head_routes, _ = self.routes_at(sc["change"]["head"])
                    key = lambda r: (r["method"], r["path"])
                    if f["kind"] == "removed_route_still_called":
                        client, removed = f["evidence"]
                        url = url_used_on_line(client["contains"], client_urls(text_at(web, "main", "src/api/endpoints.ts")))
                        gone = [r for r in base_routes if r["chain"][0].endswith(removed["contains"]) and r["file"] == removed["path"]]
                        self.assertEqual(len(gone), 1)
                        self.assertNotIn(key(gone[0]), {key(r) for r in head_routes})
                        self.assertEqual([r["path"] for r in best_routes(url, base_routes)], [gone[0]["path"]])
                        self.assertEqual(best_routes(url, head_routes), [], "the client URL fits no route after the change")
                    elif f["kind"] == "added_route_uncalled":
                        (ev,) = f["evidence"]
                        added = [r for r in head_routes if r["chain"][0] == ev["contains"] and r["file"] == ev["path"]]
                        self.assertEqual(len(added), 1)
                        self.assertNotIn(key(added[0]), {key(r) for r in base_routes})
                        for name, url in client_urls(text_at(web, "main", "src/api/endpoints.ts")).items():
                            self.assertIsNone(match_kind(url, added[0]["path"]), f"{name} fits the added route")
        self.assertGreaterEqual(edges, 8)
        self.assertEqual(findings, 2)

    def test_routes_follow_the_mounts(self):
        seen = 0
        for sc in self.scenarios:
            with self.subTest(scenario=sc["id"]):
                rev_default = self.api_rev(sc)
                targets = [(i, i.get("removed", False), i["evidence"]) for i in self.entries_of(sc, "route")]
                targets += [(i["to"], i["to"].get("removed", False), i["to"].get("evidence", [])) for i in self.entries_of(sc, "http_edge")]
                changed_funcs = {(i["path"], i["symbol"]) for i in self.entries_of(sc, "changed_symbol") if i["repo"] == "acme/api"}
                for to, removed, evidence in targets:
                    routes, _ = self.routes_at(sc["change"]["base"] if removed else rev_default)
                    found = [r for r in routes if r["method"] == to["method"] and r["path"] == to["path"]]
                    self.assertEqual(len(found), 1, f"{sc['id']}: no single route {to['method']} {to['path']}")
                    route = found[0]
                    seen += 1
                    self.assertEqual(len(evidence), len(route["chain"]), f"{sc['id']}: evidence is the decorator and each mount")
                    for ev, line in zip(evidence, route["chain"]):
                        self.assertIn(ev["contains"], line)
                    if sc["repo"] == "acme/api" and "path" not in to:
                        self.assertIn((route["file"], route["func"]), changed_funcs, sc["id"])
                for item in self.entries_of(sc, "route", exclude=True):
                    routes, unmounted = self.routes_at(rev_default)
                    if "path_suffix" in item:
                        self.assertFalse([r for r in routes if r["path"].endswith(item["path_suffix"])], sc["id"])
                    else:
                        (route,) = [r for r in routes if r["method"] == item["method"] and r["path"] == item["path"]]
                        self.assertNotIn((route["file"], route["func"]), changed_funcs, sc["id"])
                for item in sc["expected"]["not_established"]:
                    if item["kind"] == "router_unmounted":
                        self.assertIn(tuple(item["subject"].split(":")), self.routes_at(rev_default)[1])
                if sc["repo"] == "acme/api" and "route" in sc["expected"]["complete_for"]:
                    routes, _ = self.routes_at(rev_default)
                    computed = {(r["method"], r["path"]) for r in routes if (r["file"], r["func"]) in changed_funcs}
                    expected = {(i["method"], i["path"]) for i in self.entries_of(sc, "route")}
                    self.assertEqual(computed, expected, sc["id"])
        self.assertGreaterEqual(seen, 9)

    def test_workspace_json_matches_the_limits_the_scenarios_assume(self):
        ws = build.load_workspace()
        self.assertEqual(ws["limits"], {"hops": 3, "hub_importers": 50, "distinct_max": 3, "timeout_s": 900})
        self.assertEqual(sorted(ws["repos"]), ["acme/api", "acme/contracts", "acme/web", "acme/worker"])
        for name, info in ws["repos"].items():
            self.assertEqual(info["slug"], name.replace("/", "-"))
        kinds = sorted(e["kind"] for e in ws["edges"])
        self.assertEqual(kinds, ["contract", "contract", "event", "http", "package", "package"])


class StyleTest(unittest.TestCase):
    def test_no_banned_characters_in_fixture_files(self):
        bad = []
        roots = [FIXTURES, Path(__file__).resolve()]
        files = []
        for root in roots:
            files += [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
        for path in files:
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            text = path.read_text(encoding="utf-8")
            for ch, name in BANNED.items():
                if ch in text:
                    bad.append(f"{path}: {name}")
        self.assertEqual(bad, [])

    def test_no_ascii_arrows_or_banned_wording_in_text_a_person_reads(self):
        texts = {"README.md": (FIXTURES / "README.md").read_text(encoding="utf-8")}
        for folder in build.m1_scenario_dirs():
            for name in ("expected.json", "scenario.json"):
                data = json.loads((folder / name).read_text(encoding="utf-8"))
                strings = []

                def walk(node):
                    if isinstance(node, dict):
                        for key, value in node.items():
                            if key not in ("contains", "evidence", "chain", "from", "via", "comment", "naive_match", "removed_line"):
                                walk(value)
                    elif isinstance(node, list):
                        for value in node:
                            walk(value)
                    elif isinstance(node, str):
                        strings.append(node)

                walk(data)
                texts[f"{folder.name}/{name}"] = "\n".join(strings)
        module = ast.parse((FIXTURES / "build.py").read_text(encoding="utf-8"))
        docstrings = [ast.get_docstring(n) or "" for n in ast.walk(module)
                      if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))]
        texts["build.py docstrings"] = "\n".join(docstrings)
        phrase = "covered" + " by"
        for where, text in texts.items():
            self.assertNotIn("->", text, f"{where}: ASCII arrow")
            self.assertNotIn(phrase, text.lower(), where)

    def test_readme_mentions_every_scenario(self):
        readme = (FIXTURES / "README.md").read_text(encoding="utf-8")
        for folder in build.m1_scenario_dirs():
            self.assertIn(folder.name, readme)


if __name__ == "__main__":
    unittest.main()
