"""The TypeScript / JavaScript adapter on small repositories built for one rule each.

The M1 scenarios are in test_scenarios_js.py. Here: changed symbols placed by line, the importer
search (Shape A: every specifier, no short list taken for a whole one), narrowing, callers,
guards, test paths, definitions (Shape B), and that every Evidence resolves to its line.
Trap numbers refer to docs/CONTEXT.md section 5.
"""

import atexit
import dataclasses
import tempfile
from pathlib import Path
from unittest import mock

from reachmap.adapters.js import JsAdapter
from reachmap.gitlayer import Git, SearchResult
from reachmap.model import Read
from tests import scenario_support as ss
from tests import support_git as sg
from tests.fixtures import build

TSCONFIG = '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}'
TSCONFIG_FULL = '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"], "@lib/*": ["src/lib/*"], "@fmt": ["src/lib/format.ts"]}}}'
MARKERS = ["__tests__/", ".test.", ".spec."]


class Universe:
    """One git repository for the whole module, one branch per test case, one git cache.

    Every case is cut from the same empty root commit, so its files are only its own. Building a case
    costs a branch and its commits instead of a repository, and the Git layer fetches into one bare
    cache that every test shares (it keeps the commits and the checkouts it has already made)."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="reachmap-js-adapter-"))
        atexit.register(sg.remove_tree, self.root)
        self.path = self.root / "universe"
        self.repo = build.Repo(self.path, build.git_env(self.root))
        self.repo.init()
        self.repo.commits += 1
        self.repo.git("commit", "--allow-empty", "-q", "--no-verify", "-m", "root", date=f"{build.EPOCH + 3600} +0000")
        sg.enable_server_filters(self.path)
        self.url = sg.remote_url(self.path)
        self.cases = 0
        self.keyed = {}

    def add(self, commits):
        """Build the commits on top of the root, on no branch (the repository has one ref, `main`, so
        fetching a commit does not negotiate over every case); returns their shas, first to last."""
        shas = []
        self.cases += 1
        self.repo.git("checkout", "-q", "--detach", "main")
        for spec in commits:
            for rel in spec.get("delete", []):
                (self.path / rel).unlink()
            for rel, content in spec.get("files", {}).items():
                target = self.path / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(content, bytes):
                    target.write_bytes(content)
                else:
                    target.write_text(content, encoding="utf-8", newline="")
            self.repo.commit(spec.get("message", "commit"))
            shas.append(self.repo.git("rev-parse", "HEAD").strip())
        self.repo.git("checkout", "-q", "main")
        return shas


_UNIVERSE = None


def universe():
    global _UNIVERSE
    if _UNIVERSE is None:
        _UNIVERSE = Universe()
    return _UNIVERSE


class Base(sg.TempDirCase):
    """Builds a case from commits ({"files": {...}, "delete": [...], "message": ...}) on a branch of the
    shared repository. In a test, `main` and `main~1` name the case's last and first commit."""

    def setUp(self):
        super().setUp()
        self.git = None
        self.shas = []

    def _use(self, shas, cache=None):
        u = universe()
        self.shas, self.path, self.url = shas, u.path, u.url
        self.records = []
        self.git = Git(cache or self.tmp / "cache", env=sg.isolated_env(self.tmp / "home"), record_to=self.records)

    def use_shared(self, key, *commits):
        """The same case for the tests that only read it: built on first use."""
        u = universe()
        if key not in u.keyed:
            u.keyed[key] = u.add(list(commits))
        self._use(u.keyed[key], u.root / f"cache-{key}")

    def repo(self, *commits, tsconfig=True):
        first = dict(commits[0])
        files = dict(first.get("files", {}))
        if tsconfig and "tsconfig.json" not in files:
            files["tsconfig.json"] = TSCONFIG
        first["files"] = files
        self._use(universe().add([first, *commits[1:]]))
        return self.path

    def repo_raw(self, *commits):
        """A case whose file names are bytes (a name that is not UTF-8)."""
        from tests.support_universe import build_raw
        self._use(build_raw(universe(), list(commits)))
        return self.path

    def adapter(self, **kw):
        kw.setdefault("aliases_from", "tsconfig.json")
        kw.setdefault("test_markers", MARKERS)
        return JsAdapter("acme/web", self.url, self.git, **kw)

    def sha(self, ref):
        if ref == "main":
            return self.shas[-1]
        if ref.startswith("main~"):
            return self.shas[-1 - int(ref[5:])]
        return sg.rev(self.path, ref)

    def change(self, base="main~1", head="main", pre="merge_base"):
        """A ChangeSet between two refs; pre-change version is the base unless pre is None."""
        b, h = self.sha(base), self.sha(head)
        cs = self.git.diff(self.url, b, h)
        return dataclasses.replace(cs, repo="acme/web", remote=self.url, kind="compare",
                                   pre_change_sha=None if pre is None else b)

    def analyze(self, adapter=None, **kw):
        adapter = adapter or self.adapter()
        cs = self.change(**kw)
        self.analysis = adapter.analyze(cs)
        self.assert_evidence_resolves(self.analysis, adapter)
        return self.analysis

    def impact(self, path):
        return next(i for i in self.analysis.impacts if i.path == path)

    def assert_evidence_resolves(self, analysis, adapter=None):
        run = mock.Mock()
        run.analysis = analysis
        problems = ss.evidence_problems({"acme/web": self.path}, ss.iter_evidence(run))
        self.assertEqual(problems, [])

    def kinds(self):
        return sorted((r.kind, r.blocks_selection) for r in self.records)


def f(text):
    """Source text from lines."""
    return "\n".join(text) + "\n"


# ------------------------------------------------------------------------------ changed symbols


class ChangedSymbols(Base):
    def test_a_line_is_placed_by_its_number_not_by_its_text(self):
        """Two functions share an identical line; only the one that changed is reported (Shape B)."""
        before = f(["function a() {", "  const request = load();", "  return 1;", "}", "function b() {", "  const request = load();", "  return 2;", "}"])
        after = before.replace("return 2;", "return 3;")
        self.repo({"files": {"src/m.ts": before}}, {"files": {"src/m.ts": after}})
        self.analyze()
        syms = self.impact("src/m.ts").symbols
        self.assertEqual([(s.name, s.start, s.end, s.change, s.side) for s in syms], [("b", 5, 8, "modified", "after")])

    def test_the_identical_line_itself_changes_in_one_function_only(self):
        before = f(["function a() {", "  return x;", "}", "function b() {", "  return x;", "}"])
        after = f(["function a() {", "  return x;", "}", "function b() {", "  return y;", "}"])
        self.repo({"files": {"src/m.ts": before}}, {"files": {"src/m.ts": after}})
        self.analyze()
        self.assertEqual([s.name for s in self.impact("src/m.ts").symbols], ["b"])

    def test_added_and_removed_declarations(self):
        before = f(["export function keep() {", "  return 1;", "}", "export function gone() {", "  return 2;", "}"])
        after = f(["export function keep() {", "  return 1;", "}", "export function fresh() {", "  return 3;", "}"])
        self.repo({"files": {"src/m.ts": before}}, {"files": {"src/m.ts": after}})
        self.analyze()
        got = {(s.name, s.change, s.side, s.exported, s.export_names) for s in self.impact("src/m.ts").symbols}
        self.assertEqual(got, {("gone", "removed", "before", True, ("gone",)), ("fresh", "added", "after", True, ("fresh",))})
        gone = next(s for s in self.impact("src/m.ts").symbols if s.name == "gone")
        self.assertEqual((gone.start, gone.end), (4, 6))  # its span in the before text
        self.assertEqual(gone.evidence[0].ref, self.sha("main~1"))

    def test_a_removal_only_change_places_the_line_in_the_before_text(self):
        before = f(["function a() {", "  step1();", "  step2();", "  return 1;", "}", "function b() {", "  return 2;", "}"])
        after = f(["function a() {", "  step1();", "  return 1;", "}", "function b() {", "  return 2;", "}"])
        self.repo({"files": {"src/m.ts": before}}, {"files": {"src/m.ts": after}})
        self.analyze()
        (s,) = self.impact("src/m.ts").symbols
        self.assertEqual((s.name, s.change, s.side, s.removed_lines, s.added_lines), ("a", "modified", "after", (3,), ()))
        self.assertEqual((s.start, s.end), (1, 4))  # the span in the after text
        self.assertEqual([(e.ref, e.line, e.detail) for e in s.evidence], [(self.sha("main~1"), 3, "  step2();")])

    def test_an_import_line_change_is_a_module_level_change(self):
        before = f(['import { a } from "./a";', 'import { b } from "./b";', "export function f() {", "  return a();", "}"])
        after = before.replace('import { b } from "./b";\n', "")
        self.repo({"files": {"src/m.ts": before}}, {"files": {"src/m.ts": after}})
        self.analyze()
        (s,) = self.impact("src/m.ts").symbols
        self.assertEqual((s.name, s.kind, s.change, s.side, s.exported), ("<module>", "module", "modified", "before", False))
        self.assertEqual(s.removed_lines, (2,))

    def test_a_top_level_statement_change_is_module_level_and_not_narrowed(self):
        self.repo({"files": {"src/m.ts": f(["export function f() {}", "export const k = 1;", "registerPlugin(1);"]),
                             "src/u.ts": 'import { k } from "./m";\nconsole.log(k);\n'}},
                  {"files": {"src/m.ts": f(["export function f() {}", "export const k = 1;", "registerPlugin(2);"])}})
        self.analyze()
        impact = self.impact("src/m.ts")
        self.assertEqual([s.name for s in impact.symbols], ["<module>"])
        self.assertEqual([c.path for c in impact.callers], ["src/u.ts"])
        self.assertFalse(impact.narrowed)

    def test_comments_and_blank_lines_between_declarations_change_nothing(self):
        before = f(["// one", "function a() {}", "", "function b() {}"])
        after = f(["// one, reworded", "function a() {}", "", "", "// new", "function b() {}"])
        self.repo({"files": {"src/m.ts": before}}, {"files": {"src/m.ts": after}})
        self.analyze()
        self.assertEqual(self.impact("src/m.ts").symbols, ())

    def test_a_comment_inside_a_function_belongs_to_it(self):
        before = f(["function a() {", "  // old", "  return 1;", "}"])
        after = before.replace("// old", "// new")
        self.repo({"files": {"src/m.ts": before}}, {"files": {"src/m.ts": after}})
        self.analyze()
        self.assertEqual([s.name for s in self.impact("src/m.ts").symbols], ["a"])

    def test_nested_functions_are_named_in_nested(self):
        before = f(["export function outer() {", "  function inner() {", "    return 1;", "  }", "  return inner();", "}"])
        after = before.replace("return 1;", "return 2;")
        self.repo({"files": {"src/m.ts": before}}, {"files": {"src/m.ts": after}})
        self.analyze()
        (s,) = self.impact("src/m.ts").symbols
        self.assertEqual((s.name, s.nested), ("outer", ("inner",)))

    def test_a_two_line_constant_is_placed_by_its_lines(self):
        before = f(["export const OTHER = 1;", "export const URL =", '  "/api/a";', "export const Z = 2;"])
        after = before.replace('"/api/a"', '"/api/b"')
        self.repo({"files": {"src/m.ts": before}}, {"files": {"src/m.ts": after}})
        self.analyze()
        (s,) = self.impact("src/m.ts").symbols
        self.assertEqual((s.name, s.start, s.end, s.kind), ("URL", 2, 3, "variable"))

    def test_without_a_pre_change_version_removals_are_not_computed_and_nothing_is_recorded(self):
        before = f(["export function gone() {", "  return 1;", "}", "export function keep() {", "  return 2;", "}"])
        after = f(["export function keep() {", "  return 3;", "}"])
        self.repo({"files": {"src/m.ts": before}}, {"files": {"src/m.ts": after}})
        self.analyze(pre=None)
        syms = self.impact("src/m.ts").symbols
        self.assertTrue(all(s.change != "removed" for s in syms))
        self.assertTrue(all(not s.removed_lines for s in syms))
        self.assertEqual([r for r in self.records if r.kind == "pre_change_unavailable"], [])  # the change set records it, once
        self.assertFalse(self.impact("src/m.ts").narrowed)

    def test_a_deleted_file_reports_its_declarations_as_removed(self):
        self.repo({"files": {"src/m.ts": f(["export function a() {}", "export function b() {}"]), "src/u.ts": 'import { a } from "./m";\na();\n'}},
                  {"delete": ["src/m.ts"]})
        self.analyze()
        impact = self.impact("src/m.ts")
        self.assertEqual({(s.name, s.change, s.side) for s in impact.symbols}, {("a", "removed", "before"), ("b", "removed", "before")})
        self.assertEqual([c.path for c in impact.callers], ["src/u.ts"])  # who imported it before the change is followed too

    def test_a_renamed_and_edited_file_is_read_at_its_old_path(self):
        body = f(["export function a() {", "  return 1;", "}", "export function b() {", "  return 2;", "}", "// padding to keep the rename similar", "// more", "// more"])
        self.repo({"files": {"src/old.ts": body}}, {"delete": ["src/old.ts"], "files": {"src/new.ts": body.replace("return 2;", "return 3;")}})
        cs = self.change()
        (fc,) = [x for x in cs.files if x.status == "renamed"]
        syms = self.adapter().changed_symbols(cs, fc)
        self.assertEqual([(s.name, s.path) for s in syms], [("b", "src/new.ts")])

    def test_a_non_js_file_has_no_symbols_and_no_importer_search(self):
        self.repo({"files": {"README.md": "a\n", "src/a.ts": "export const a = 1;\n"}}, {"files": {"README.md": "b\n"}})
        self.analyze()
        impact = self.impact("README.md")
        self.assertEqual((impact.symbols, impact.importers, impact.role, impact.importers_complete), ((), (), "source", True))

    def test_an_unreadable_file_makes_a_module_level_change_and_a_record(self):
        self.repo({"files": {"src/m.ts": "export const a = 1;\n"}}, {"files": {"src/m.ts": b"export const a = '\xe9';\n"}})
        self.analyze()
        (s,) = self.impact("src/m.ts").symbols
        self.assertEqual((s.name, s.change), ("<module>", "modified"))
        self.assertEqual(self.kinds(), [("read_unreadable", True)])

    def test_export_names_come_from_the_before_and_the_after_lists(self):
        before = f(["export function f() {", "  return 1;", "}"])
        after = f(["function f() {", "  return 2;", "}"])
        self.repo({"files": {"src/m.ts": before}}, {"files": {"src/m.ts": after}})
        self.analyze()
        syms = self.impact("src/m.ts").symbols
        f_sym = next(s for s in syms if s.name == "f")
        self.assertEqual(f_sym.export_names, ("f",))  # exported before, not after: it still counts


# ------------------------------------------------------------------------------ importers


IMPORTER_FILES = {
    "src/lib/format.ts": "export function fmt(v: number) {\n  return String(v);\n}\n",
    "src/a_alias.ts": 'import { fmt } from "@/lib/format";\nexport const a = fmt(1);\n',
    "src/b_second_alias.ts": 'import { fmt } from "@lib/format";\nexport const b = fmt(2);\n',
    "src/c_relative.ts": 'import { fmt } from "./lib/format";\nexport const c = fmt(3);\n',
    "src/d/deep/d_relative_up.ts": 'import { fmt } from "../../lib/format";\nexport const d = fmt(4);\n',
    "src/e_extension.ts": 'import { fmt } from "./lib/format.ts";\nexport const e = fmt(5);\n',
    "src/f_js_spelling.ts": 'import { fmt } from "./lib/format.js";\nexport const g = fmt(6);\n',
    "src/h_exact_alias.ts": 'import { fmt } from "@fmt";\nexport const h = fmt(7);\n',
    "src/i_reexport.ts": 'export { fmt } from "./lib/format";\n',
    "src/j_star.ts": 'export * from "./lib/format";\n',
    "src/k_dynamic.tsx": 'import React from "react";\nconst L = React.lazy(() => import("@/lib/format"));\nexport default L;\n',
    "src/l_require.js": 'const m = require("./lib/format");\nmodule.exports = m;\n',
    "src/m_type.ts": 'import type { fmt } from "@/lib/format";\nexport type T = typeof fmt;\n',
    "src/n_namespace.ts": 'import * as ns from "@/lib/format";\nexport const n = ns.fmt(8);\n',
    "src/o_side_effect.ts": 'import "@/lib/format";\n',
    "src/p_comment.ts": '// import { fmt } from "@/lib/format";\nconst s = \'import x from "./lib/format"\';\nexport const p = s;\n',
    "src/other/format.ts": "export const unrelated = 1;\n",
    "src/q_other_format.ts": 'import { unrelated } from "./other/format";\nexport const q = unrelated;\n',
}
IMPORTERS = ["src/a_alias.ts", "src/b_second_alias.ts", "src/c_relative.ts", "src/d/deep/d_relative_up.ts", "src/e_extension.ts",
             "src/f_js_spelling.ts", "src/h_exact_alias.ts", "src/i_reexport.ts", "src/j_star.ts", "src/k_dynamic.tsx",
             "src/l_require.js", "src/m_type.ts", "src/n_namespace.ts", "src/o_side_effect.ts"]


class Importers(Base):
    def make(self, files=None, **kw):
        """The adapter on a repository of `files` (the default set is built once and shared)."""
        if files is None:
            self.use_shared("importers", {"files": {"tsconfig.json": TSCONFIG_FULL, **IMPORTER_FILES}})
        else:
            self.repo({"files": {"tsconfig.json": TSCONFIG_FULL, **files}}, **kw)
        return self.adapter()

    def test_every_specifier_form_is_an_importer(self):
        """Trap: reverse alias lookup returned only the longest alias; importer search covered aliases only."""
        a = self.make()
        res = a.importers("src/lib/format.ts", self.sha("main"))
        self.assertEqual(sorted(res.paths), sorted(IMPORTERS))
        self.assertTrue(res.complete)
        self.assertEqual(self.kinds(), [])
        self.assertNotIn("src/q_other_format.ts", res.paths)  # same stem, a different file
        self.assertNotIn("src/p_comment.ts", res.paths)       # the specifier is in a comment and a string

    def test_each_edge_keeps_its_kind_names_and_evidence_line(self):
        a = self.make()
        res = a.importers("src/lib/format.ts", self.sha("main"))
        by = {e.importer_path: e for e in res.edges}
        self.assertEqual((by["src/a_alias.ts"].kind, by["src/a_alias.ts"].names, by["src/a_alias.ts"].specifier), ("import", ("fmt",), "@/lib/format"))
        self.assertEqual(by["src/i_reexport.ts"].kind, "reexport")
        self.assertEqual((by["src/j_star.ts"].kind, by["src/j_star.ts"].names), ("export_star", None))
        self.assertEqual((by["src/k_dynamic.tsx"].kind, by["src/k_dynamic.tsx"].names, by["src/k_dynamic.tsx"].evidence.line), ("dynamic", None, 2))
        self.assertEqual((by["src/l_require.js"].kind, by["src/l_require.js"].names), ("require", None))
        self.assertTrue(by["src/m_type.ts"].type_only)
        self.assertEqual((by["src/n_namespace.ts"].names, [(b.imported, b.local) for b in by["src/n_namespace.ts"].bindings]), (None, [("*", "ns")]))
        self.assertEqual((by["src/o_side_effect.ts"].names, by["src/o_side_effect.ts"].bindings), (None, ()))
        self.assertEqual(by["src/c_relative.ts"].target, "src/lib/format.ts")
        self.assertEqual(by["src/a_alias.ts"].evidence.detail, 'import { fmt } from "@/lib/format";')
        self.assertEqual(by["src/a_alias.ts"].evidence.ref, self.sha("main"))

    def test_the_candidate_filter_does_not_depend_on_the_alias_spelling(self):
        """The searches are for the file stem (and an exact alias), never for a spelling such as @/lib/format."""
        patterns = []
        a = self.make()
        original = self.git.grep

        def spy(checkout, pattern, **kw):
            patterns.append(pattern)
            return original(checkout, pattern, **kw)

        with mock.patch.object(self.git, "grep", spy):
            a.importers("src/lib/format.ts", self.sha("main"))
        self.assertIn("format", patterns)
        self.assertIn("@fmt", patterns)  # an exact alias has a spelling that does not hold the stem: searched too
        self.assertTrue(all("@/lib" not in p and "@lib" not in p for p in patterns), patterns)

    def test_an_index_file_is_searched_by_its_directory_name(self):
        files = {"src/ui/Button/index.tsx": "export const Button = () => 1;\n",
                 "src/p.ts": 'import { Button } from "@/ui/Button";\nButton();\n',
                 "src/q.ts": 'import { Button } from "./ui/Button/index";\nButton();\n',
                 "src/r.ts": 'import { Button } from "@/ui/Button/index.tsx";\nButton();\n'}
        a = self.make(files)
        patterns = []
        original = self.git.grep
        with mock.patch.object(self.git, "grep", lambda c, p, **kw: (patterns.append(p), original(c, p, **kw))[1]):
            res = a.importers("src/ui/Button/index.tsx", self.sha("main"))
        self.assertEqual(sorted(res.paths), ["src/p.ts", "src/q.ts", "src/r.ts"])
        self.assertEqual(patterns[0], "Button")

    def test_an_index_file_is_reached_by_dot_and_dot_dot_which_name_no_folder(self):
        """`from "."` and `from ".."` hold no name to search for: the files under the folder are candidates."""
        files = {"src/ui/index.ts": "export const Button = 1;\n",
                 "src/ui/Card.tsx": 'import { Button } from ".";\nexport const c = Button;\n',
                 "src/ui/Row.tsx": 'import { Button } from "./";\nexport const r = Button;\n',
                 "src/ui/deep/x.ts": 'import { Button } from "..";\nexport const x = Button;\n',
                 "src/ui/deep/er/y.ts": 'import { Button } from "../..";\nexport const y = Button;\n',
                 "src/other.ts": 'import { Button } from "./ui";\nexport const o = Button;\n'}
        a = self.make(files)
        res = a.importers("src/ui/index.ts", self.sha("main"))
        self.assertEqual(sorted(res.paths), ["src/other.ts", "src/ui/Card.tsx", "src/ui/Row.tsx", "src/ui/deep/er/y.ts", "src/ui/deep/x.ts"])
        self.assertTrue(res.complete)

    def test_a_root_index_file_has_every_js_file_as_a_candidate(self):
        a = self.make({"index.ts": "export const a = 1;\n", "src/x.ts": 'import { a } from "..";\n', "src/y.ts": 'import { a } from "../index";\n', "src/z.ts": "export const z = 1;\n"})
        res = a.importers("index.ts", self.sha("main"))
        self.assertEqual(sorted(res.paths), ["src/x.ts", "src/y.ts"])

    def test_no_importers_is_a_complete_empty_answer(self):
        a = self.make({"src/lonely.ts": "export const x = 1;\n", "src/other.ts": "export const y = 2;\n"})
        res = a.importers("src/lonely.ts", self.sha("main"))
        self.assertEqual((res.edges, res.complete, res.reasons), ((), True, ()))

    def test_an_examine_cap_passes_through_and_the_list_is_marked_incomplete(self):
        """Trap: a search examined a small fraction of the matches and listed that fraction as the consumers."""
        a = self.make()
        capped = self.adapter(examine_cap=6)
        res = capped.importers("src/lib/format.ts", self.sha("main"))
        self.assertFalse(res.complete)
        self.assertIn("search_truncated", [r.kind for r in self.records])
        self.assertTrue(any("search_truncated" in r or "examine" in r or "cut" in r for r in res.reasons))
        self.assertLess(len(res.paths), len(IMPORTERS))
        self.assertTrue(set(res.paths) <= set(IMPORTERS))
        blocking = [r for r in self.records if r.kind == "search_truncated"]
        self.assertTrue(all(r.blocks_selection for r in blocking))
        full = a.importers("src/lib/format.ts", self.sha("main"))
        self.assertTrue(full.complete)

    def test_records_go_to_an_analysis_when_one_is_given(self):
        from reachmap.model import Analysis
        self.make()
        analysis = Analysis(change={"kind": "compare"}, ref=self.sha("main"))
        res = self.adapter(examine_cap=6).importers("src/lib/format.ts", self.sha("main"), record_to=analysis)
        self.assertFalse(res.complete)
        self.assertEqual({r.kind for r in analysis.incompleteness}, {"search_truncated"})  # one per search made
        self.assertEqual(self.records, [])  # nothing leaked to the git layer's own list

    def test_a_cap_that_finds_nothing_is_still_incomplete_not_empty(self):
        a = self.make({**{f"src/a{i:02d}.ts": "export const x = 1;\n" for i in range(10)}, "src/z_imp.ts": 'import { fmt } from "./lib/format";\n',
                       "src/lib/format.ts": "export const fmt = 1;\n"})
        res = self.adapter(examine_cap=3).importers("src/lib/format.ts", self.sha("main"))
        self.assertFalse(res.complete)
        self.assertEqual(res.edges, ())
        self.assertIn("search_truncated", [r.kind for r in self.records])

    def test_an_unreadable_grep_is_an_incomplete_list_not_an_empty_one(self):
        a = self.make()
        unreadable = SearchResult(Read.UNREADABLE, "format", self.sha("main"), (), None, 0, None, "git failed")
        with mock.patch.object(self.git, "grep", return_value=unreadable):
            res = a.importers("src/lib/format.ts", self.sha("main"))
        self.assertFalse(res.complete)
        self.assertEqual(res.edges, ())
        self.assertTrue(res.reasons)

    def test_an_unreadable_candidate_file_makes_the_list_incomplete_and_is_recorded(self):
        files = dict(IMPORTER_FILES)
        files["src/z_latin1.ts"] = b"import { fmt } from './lib/format'; // caf\xe9\n"
        a = self.make(files)
        res = a.importers("src/lib/format.ts", self.sha("main"))
        self.assertFalse(res.complete)
        self.assertEqual(sorted(res.paths), sorted(IMPORTERS))  # the others are still listed
        self.assertEqual([(r.kind, r.blocks_selection) for r in self.records], [("read_unreadable", True)])
        self.assertIn("src/z_latin1.ts", self.records[0].subject)

    def test_a_commit_without_the_alias_config_defines_no_aliases(self):
        self.repo({"files": {"src/lib/format.ts": "export const fmt = 1;\n", "src/a.ts": 'import { fmt } from "./lib/format";\n'}}, tsconfig=False)
        res = self.adapter().importers("src/lib/format.ts", self.sha("main"))
        self.assertEqual((res.paths, res.complete, self.kinds()), (("src/a.ts",), True, []))  # nothing it would have resolved was in the way

    def test_a_commit_without_the_alias_config_reports_an_alias_import_it_cannot_resolve(self):
        self.repo({"files": {"src/lib/format.ts": "export const fmt = 1;\n", "src/a.ts": 'import { fmt } from "./lib/format";\nimport { x } from "@/lib/format";\n'}}, tsconfig=False)
        res = self.adapter().importers("src/lib/format.ts", self.sha("main"))
        self.assertEqual((res.paths, res.complete), (("src/a.ts",), False))
        self.assertEqual(self.kinds(), [("alias_config_gap", False), ("specifier_unresolved", True)])

    def test_an_alias_config_that_extends_a_missing_file_blocks_and_marks_the_list_incomplete(self):
        self.repo({"files": {"tsconfig.json": '{"extends": "./gone.json"}', "src/lib/format.ts": "export const fmt = 1;\n",
                             "src/a.ts": 'import { fmt } from "./lib/format";\n'}}, tsconfig=False)
        res = self.adapter().importers("src/lib/format.ts", self.sha("main"))
        self.assertEqual(res.paths, ("src/a.ts",))  # relative importers are still found
        self.assertFalse(res.complete)
        self.assertEqual(self.kinds(), [("alias_config_gap", True)])

    def test_a_missing_target_directory_is_a_stated_limit_not_an_incomplete_list(self):
        tsconfig = '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"], "@gone/*": ["nowhere/*"]}}}'
        self.repo({"files": {"tsconfig.json": tsconfig, "src/lib/format.ts": "export const fmt = 1;\n", "src/a.ts": 'import { fmt } from "@/lib/format";\n'}})
        res = self.adapter().importers("src/lib/format.ts", self.sha("main"))
        self.assertEqual((res.paths, res.complete), (("src/a.ts",), True))
        self.assertEqual(self.kinds(), [("alias_config_gap", False)])

    def test_the_stale_editor_config_is_not_used_when_tsconfig_is_named(self):
        files = {"tsconfig.json": TSCONFIG, "jsconfig.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["app/*"]}}}',
                 "src/lib/format.ts": "export const fmt = 1;\n", "src/a.ts": 'import { fmt } from "@/lib/format";\n'}
        self.repo({"files": files})
        res = self.adapter().importers("src/lib/format.ts", self.sha("main"))
        self.assertEqual(res.paths, ("src/a.ts",))
        self.assertEqual(self.kinds(), [])

    def test_without_a_configured_alias_file_tsconfig_is_detected_before_jsconfig(self):
        self.repo({"files": {"src/lib/format.ts": "export const fmt = 1;\n", "src/a.ts": 'import { fmt } from "@/lib/format";\n'}})
        res = self.adapter(aliases_from=None).importers("src/lib/format.ts", self.sha("main"))
        self.assertEqual(res.paths, ("src/a.ts",))

    def test_importers_are_read_at_the_pinned_commit_not_the_branch_tip(self):
        self.repo({"files": {"src/lib/format.ts": "export const fmt = 1;\n", "src/a.ts": 'import { fmt } from "./lib/format";\n'}},
                  {"files": {"src/b.ts": 'import { fmt } from "./lib/format";\n'}})
        a = self.adapter()
        self.assertEqual(a.importers("src/lib/format.ts", self.sha("main~1")).paths, ("src/a.ts",))
        self.assertEqual(a.importers("src/lib/format.ts", self.sha("main")).paths, ("src/a.ts", "src/b.ts"))

    def test_a_candidate_whose_name_cannot_be_cited_makes_the_list_incomplete_and_is_recorded(self):
        files = {"src/lib/format.ts": "export const fmt = 1;\n", "src/a.ts": 'import { fmt } from "./lib/format";\n',
                 "src/we\\ird.ts": 'import { fmt } from "./lib/format";\n'}
        a = self.make(files)
        res = a.importers("src/lib/format.ts", self.sha("main"))
        self.assertEqual(res.paths, ("src/a.ts",))
        self.assertFalse(res.complete)
        self.assertEqual([(r.kind, r.blocks_selection) for r in self.records], [("read_unreadable", True)])

    def test_a_changed_file_whose_name_cannot_be_cited_is_recorded_not_dropped(self):
        self.repo({"files": {"src/we\\ird.ts": "export const a = 1;\n"}}, {"files": {"src/we\\ird.ts": "export const a = 2;\n"}})
        self.analyze()
        impact = self.impact("src/we\\ird.ts")
        self.assertEqual((impact.symbols, impact.importers_complete), ((), False))
        self.assertEqual([r.kind for r in self.records], ["read_unreadable"])

    def test_the_hub_rule_stops_the_walk_and_records_a_stated_limit(self):
        files = {"src/lib/format.ts": "export function truncate(t: string) {\n  return t;\n}\n"}
        files.update({f"src/w{i:02d}.ts": f'import {{ truncate }} from "{"@/lib/format" if i % 2 else "../src/lib/format"}";\ntruncate("x");\n' for i in range(7)})
        self.repo({"files": files}, {"files": {"src/lib/format.ts": "export function truncate(t: string) {\n  return t + '';\n}\n"}})
        self.analyze(adapter=self.adapter(hub_importers=5))
        impact = self.impact("src/lib/format.ts")
        self.assertTrue(impact.hub)
        self.assertEqual(impact.importer_count, 7)
        self.assertEqual((impact.callers, impact.unit_tests, impact.exclusions), ((), (), ()))
        self.assertEqual(self.kinds(), [("hub_cutoff", False)])
        self.assertEqual(self.records[0].subject, "src/lib/format.ts")

    def test_at_the_hub_limit_the_walk_goes_on(self):
        files = {"src/lib/format.ts": "export function truncate(t: string) {\n  return t;\n}\n"}
        files.update({f"src/w{i}.ts": 'import { truncate } from "@/lib/format";\ntruncate("x");\n' for i in range(5)})
        self.repo({"files": files}, {"files": {"src/lib/format.ts": "export function truncate(t: string) {\n  return t + '';\n}\n"}})
        self.analyze(adapter=self.adapter(hub_importers=5))
        impact = self.impact("src/lib/format.ts")
        self.assertFalse(impact.hub)
        self.assertEqual(len(impact.callers), 5)
        self.assertEqual(self.kinds(), [])

    def test_the_reverse_lookup_is_public_and_complete(self):
        a = self.make()
        specs = a.specifiers_for("src/lib/format.ts", self.sha("main"), importer="src/a_alias.ts")
        for want in ("@/lib/format", "@lib/format", "@fmt", "./lib/format", "./lib/format.js", "@/lib/format.ts"):
            self.assertIn(want, specs)


# ------------------------------------------------------------------------------ narrowing and callers


def two_commits(before, after, importers):
    return ({"files": {"src/m.ts": before, **importers}}, {"files": {"src/m.ts": after}})


M_BEFORE = f(["export function alpha() {", "  return 1;", "}", "export function beta() {", "  return 2;", "}", "export default function main() {", "  return 3;", "}"])
M_ALPHA_CHANGED = M_BEFORE.replace("return 1;", "return 10;")


class Narrowing(Base):
    def run_with(self, importers, after=M_ALPHA_CHANGED, **kw):
        self.repo(*two_commits(M_BEFORE, after, importers))
        self.analyze(**kw)
        return self.impact("src/m.ts")

    def test_an_importer_of_other_names_only_is_excluded_with_its_import_line_and_the_symbol_line(self):
        impact = self.run_with({"src/uses_alpha.ts": 'import { alpha } from "./m";\nalpha();\n', "src/uses_beta.ts": 'import { beta } from "./m";\nbeta();\n'})
        self.assertEqual([c.path for c in impact.callers], ["src/uses_alpha.ts"])
        (ex,) = impact.exclusions
        self.assertEqual((ex.subject_path, ex.reason), ("src/uses_beta.ts", "named_export_narrowing"))
        self.assertEqual([(e.path, e.line, e.detail) for e in ex.evidence],
                         [("src/uses_beta.ts", 1, 'import { beta } from "./m";'), ("src/m.ts", 1, "export function alpha() {")])
        self.assertTrue(impact.narrowed)

    def test_the_evidence_of_the_symbol_line_is_at_the_head_for_a_modified_symbol(self):
        impact = self.run_with({"src/uses_beta.ts": 'import { beta } from "./m";\nbeta();\n'})
        self.assertEqual(impact.exclusions[0].evidence[1].ref, self.sha("main"))

    def test_a_type_only_import_of_a_changed_name_is_kept(self):
        impact = self.run_with({"src/t.ts": 'import type { alpha } from "./m";\nexport type A = typeof alpha;\n'})
        self.assertEqual([c.path for c in impact.callers], ["src/t.ts"])

    def test_whole_module_imports_are_not_narrowed(self):
        files = {
            "src/ns.ts": 'import * as m from "./m";\nm.beta();\n',
            "src/def.ts": 'import main from "./m";\nmain();\n',
            "src/side.ts": 'import "./m";\n',
            "src/dyn.ts": 'export const L = () => import("./m");\n',
            "src/req.js": 'const m = require("./m");\n',
            "src/star.ts": 'export * from "./m";\n',
            "src/def_and_named.ts": 'import main, { beta } from "./m";\nmain(); beta();\n',
        }
        impact = self.run_with(files)
        self.assertEqual(sorted(c.path for c in impact.callers), sorted(files))
        self.assertEqual(impact.exclusions, ())

    def test_a_named_reexport_of_another_name_is_narrowed(self):
        impact = self.run_with({"src/barrel.ts": 'export { beta } from "./m";\n', "src/barrel2.ts": 'export { alpha as renamed } from "./m";\n'})
        self.assertEqual([c.path for c in impact.callers], ["src/barrel2.ts"])
        self.assertEqual([x.subject_path for x in impact.exclusions], ["src/barrel.ts"])

    def test_a_removed_export_still_counts_for_its_importers(self):
        after = M_BEFORE.replace("export function beta() {\n  return 2;\n}\n", "")
        impact = self.run_with({"src/uses_beta.ts": 'import { beta } from "./m";\nbeta();\n', "src/uses_main.ts": 'import { alpha } from "./m";\nalpha();\n'}, after=after)
        self.assertEqual([c.path for c in impact.callers], ["src/uses_beta.ts"])
        self.assertEqual([x.subject_path for x in impact.exclusions], ["src/uses_main.ts"])

    def test_a_renamed_export_counts_under_its_old_and_new_name(self):
        after = M_BEFORE.replace("function beta", "function beta2")
        impact = self.run_with({"src/old.ts": 'import { beta } from "./m";\n', "src/new.ts": 'import { beta2 } from "./m";\n', "src/alpha.ts": 'import { alpha } from "./m";\n'}, after=after)
        self.assertEqual(sorted(c.path for c in impact.callers), ["src/new.ts", "src/old.ts"])
        self.assertEqual([x.subject_path for x in impact.exclusions], ["src/alpha.ts"])

    def test_an_unexported_changed_symbol_narrows_by_what_uses_it(self):
        before = f(["function helper() {", "  return 1;", "}", "export function alpha() {", "  return helper();", "}", "export function beta() {", "  return helper() + 1;", "}",
                    "export function gamma() {", "  return 3;", "}"])
        self.repo(*two_commits(before, before.replace("return 1;", "return 2;"), {"src/b.ts": 'import { beta } from "./m";\nbeta();\n', "src/c.ts": 'import { gamma } from "./m";\ngamma();\n'}))
        self.analyze()
        impact = self.impact("src/m.ts")
        self.assertEqual([c.path for c in impact.callers], ["src/b.ts"])        # beta uses the helper
        self.assertEqual([x.subject_path for x in impact.exclusions], ["src/c.ts"])  # gamma does not
        self.assertTrue(impact.narrowed)

    def test_several_changed_symbols_keep_an_importer_of_either(self):
        after = M_ALPHA_CHANGED.replace("return 2;", "return 20;")
        impact = self.run_with({"src/a.ts": 'import { alpha } from "./m";\n', "src/b.ts": 'import { beta } from "./m";\n'}, after=after)
        self.assertEqual(sorted(c.path for c in impact.callers), ["src/a.ts", "src/b.ts"])
        self.assertEqual(impact.exclusions, ())

    def test_without_a_pre_change_version_every_importer_is_affected(self):
        impact = self.run_with({"src/uses_beta.ts": 'import { beta } from "./m";\nbeta();\n'}, pre=None)
        self.assertEqual([c.path for c in impact.callers], ["src/uses_beta.ts"])
        self.assertEqual(impact.exclusions, ())
        self.assertFalse(impact.narrowed)

    def test_a_default_export_change_reaches_default_importers(self):
        after = M_BEFORE.replace("return 3;", "return 30;")
        impact = self.run_with({"src/def.ts": 'import main from "./m";\nmain();\n', "src/named.ts": 'import { alpha } from "./m";\n'}, after=after)
        self.assertEqual([c.path for c in impact.callers], ["src/def.ts"])
        self.assertEqual([x.subject_path for x in impact.exclusions], ["src/named.ts"])


class Callers(Base):
    def test_an_alias_renamed_binding_is_followed(self):
        self.repo(*two_commits(M_BEFORE, M_ALPHA_CHANGED, {"src/u.ts": f(['import { alpha as a1, beta } from "./m";', "export function run() {", "  return a1(1, 2) + beta();", "}"])}))
        self.analyze()
        (caller,) = self.impact("src/m.ts").callers
        (cs,) = caller.call_sites
        self.assertEqual((cs.symbol, cs.line, cs.form, cs.args, cs.caller_symbol), ("alpha", 3, "call", ("1", "2"), "run"))
        self.assertEqual((caller.symbol, cs.import_evidence.line), ("run", 1))
        self.assertEqual(cs.evidence.detail, "  return a1(1, 2) + beta();")

    def test_namespace_member_calls_are_followed(self):
        self.repo(*two_commits(M_BEFORE, M_ALPHA_CHANGED, {"src/u.ts": f(['import * as m from "./m";', "export const x = m.alpha(5);", "export const y = m.beta();"])}))
        self.analyze()
        (caller,) = self.impact("src/m.ts").callers
        self.assertEqual([(c.symbol, c.line, c.args) for c in caller.call_sites], [("alpha", 2, ("5",))])

    def test_a_jsx_use_is_a_reference_in_its_component(self):
        before = "export function Badge() {\n  return <b>x</b>;\n}\n"
        self.repo({"files": {"src/Badge.tsx": before, "src/Page.tsx": 'import { Badge } from "./Badge";\nexport default function Page() {\n  return <Badge />;\n}\n'}},
                  {"files": {"src/Badge.tsx": before.replace("<b>", "<i>").replace("</b>", "</i>")}})
        self.analyze()
        (caller,) = self.impact("src/Badge.tsx").callers
        self.assertEqual((caller.symbol, [(c.form, c.line) for c in caller.call_sites]), ("Page", [("reference", 3)]))

    def test_an_importer_with_no_use_is_a_caller_at_its_import_line(self):
        self.repo(*two_commits(M_BEFORE, M_ALPHA_CHANGED, {"src/u.ts": 'import { alpha } from "./m";\n'}))
        self.analyze()
        (caller,) = self.impact("src/m.ts").callers
        (cs,) = caller.call_sites
        self.assertEqual((cs.form, cs.line, caller.symbol), ("import", 1, "<module>"))

    def test_a_mention_in_a_comment_or_string_is_not_a_use(self):
        self.repo(*two_commits(M_BEFORE, M_ALPHA_CHANGED, {"src/u.ts": f(['import { alpha } from "./m";', "// alpha(1)", 'const s = "alpha(2)";', "export const k = s;"])}))
        self.analyze()
        (caller,) = self.impact("src/m.ts").callers
        self.assertEqual([c.form for c in caller.call_sites], ["import"])

    def test_test_files_are_never_callers_they_are_unit_tests_with_import_evidence(self):
        files = {"src/__tests__/m.test.ts": 'import { alpha } from "../m";\nalpha();\n', "src/m.spec.ts": 'import { beta } from "./m";\nbeta();\n',
                 "src/u.ts": 'import { alpha } from "./m";\nexport const v = alpha();\n'}
        self.repo(*two_commits(M_BEFORE, M_ALPHA_CHANGED, files))
        self.analyze()
        impact = self.impact("src/m.ts")
        self.assertEqual([c.path for c in impact.callers], ["src/u.ts"])
        self.assertEqual(sorted(t.path for t in impact.unit_tests), ["src/__tests__/m.test.ts", "src/m.spec.ts"])  # a test is not narrowed
        t = next(t for t in impact.unit_tests if t.path == "src/__tests__/m.test.ts")
        self.assertEqual((t.via, [e.detail for e in t.evidence]), ("src/m.ts", ['import { alpha } from "../m";']))

    def test_a_test_that_imports_a_caller_is_a_unit_test_one_hop_out(self):
        files = {"src/u.ts": 'import { alpha } from "./m";\nexport const v = alpha();\n', "src/u.test.ts": 'import { v } from "./u";\nv;\n',
                 "src/far.ts": 'import { v } from "./u";\nexport const w = v;\n', "src/far.test.ts": 'import { w } from "./far";\nw;\n'}
        self.repo(*two_commits(M_BEFORE, M_ALPHA_CHANGED, files))
        self.analyze()
        impact = self.impact("src/m.ts")
        self.assertEqual([(t.path, t.via) for t in impact.unit_tests], [("src/u.test.ts", "src/u.ts")])  # two hops out is not listed

    def test_tests_of_an_excluded_importer_are_not_listed(self):
        files = {"src/b.ts": 'import { beta } from "./m";\nexport const v = beta();\n', "src/b.test.ts": 'import { v } from "./b";\nv;\n'}
        self.repo(*two_commits(M_BEFORE, M_ALPHA_CHANGED, files))
        self.analyze()
        self.assertEqual(self.impact("src/m.ts").unit_tests, ())

    def test_a_changed_test_file_gets_no_importer_search(self):
        self.repo({"files": {"src/a.test.ts": "it('x', () => 1);\n"}}, {"files": {"src/a.test.ts": "it('x', () => 2);\n"}})
        self.analyze()
        impact = self.impact("src/a.test.ts")
        self.assertEqual((impact.role, impact.importers, impact.callers), ("test", (), ()))

    def test_markers_match_by_segment_in_the_adapter(self):
        self.repo({"files": {"src/a.ts": "export const a = 1;\n"}})
        a = self.adapter()
        self.assertTrue(a.is_test_path("src/components/__tests__/StatusBadge.test.tsx"))
        self.assertTrue(a.is_test_path("src/lib/dates.spec.ts"))
        self.assertFalse(a.is_test_path("src/backtest/engine.ts"))
        only_test = self.adapter(test_markers=["test/"])
        self.assertFalse(only_test.is_test_path("src/backtest/engine.ts"))
        self.assertTrue(only_test.is_test_path("src/test/engine.ts"))


# ------------------------------------------------------------------------------ guards


GUARD_BEFORE = f([
    'export function fmt(value: number, mode: "legacy" | "modern", pad = 0) {',   # 1
    '  if (mode === "legacy") {',                                                   # 2
    "    return value.toFixed(2);",                                                 # 3
    "  }",                                                                          # 4
    "  return String(value);",                                                      # 5
    "}",                                                                            # 6
])
GUARD_AFTER = GUARD_BEFORE.replace("value.toFixed(2)", "value.toFixed(2).padStart(10)")

CALLS = {
    "literal_in": 'fmt(1, "legacy")',
    "literal_out": 'fmt(1, "modern")',
    "variable": "fmt(1, mode)",
    "expression": 'fmt(1, "mod" + "ern")',
    "spread": "fmt(...args)",
    "missing": "fmt(1)",
    "template_literal": "fmt(1, `modern`)",
    "single_quoted": "fmt(1, 'modern')",
    "member": "fmt(1, opts.mode)",
    "call_result": "fmt(1, pick())",
    "reference": "[1].map(fmt)",
}


class Guards(Base):
    def run_calls(self, before=GUARD_BEFORE, after=GUARD_AFTER, calls=CALLS):
        importers = {f"src/u_{name}.ts": f'import {{ fmt }} from "./m";\nexport function use() {{\n  return {call};\n}}\n' for name, call in calls.items()}
        self.repo(*two_commits(before, after, importers))
        self.analyze()
        impact = self.impact("src/m.ts")
        out = {}
        for name in calls:
            path = f"src/u_{name}.ts"
            caller = next((c for c in impact.callers if c.path == path), None)
            excl = next((x for x in impact.exclusions if x.subject_path == path), None)
            out[name] = ("excluded" if excl and excl.reason == "guard" else None, caller.reason if caller else None)
        return out, impact

    def test_the_call_matrix(self):
        got, impact = self.run_calls()
        self.assertEqual(got, {
            "literal_in": (None, "literal_enters_guard"),
            "literal_out": ("excluded", None),
            "variable": (None, "undecidable"),
            "expression": (None, "undecidable"),
            "spread": (None, "undecidable"),
            "missing": (None, "undecidable"),
            "template_literal": ("excluded", None),
            "single_quoted": ("excluded", None),
            "member": (None, "undecidable"),
            "call_result": (None, "undecidable"),
            "reference": (None, "undecidable"),
        })

    def test_an_exclusion_cites_the_guard_line_and_the_call_line(self):
        _, impact = self.run_calls(calls={"literal_out": CALLS["literal_out"]})
        (ex,) = impact.exclusions
        self.assertEqual([(e.path, e.line, e.detail) for e in ex.evidence],
                         [("src/m.ts", 2, '  if (mode === "legacy") {'), ("src/u_literal_out.ts", 3, '  return fmt(1, "modern");')])
        self.assertEqual(ex.reason, "guard")

    def test_the_guard_line_is_cited_as_evidence_of_the_changed_symbol_too(self):
        _, impact = self.run_calls(calls={"literal_out": CALLS["literal_out"]})
        self.assertIn('  if (mode === "legacy") {', [e.detail for s in impact.symbols for e in s.evidence] + [e.detail for x in impact.exclusions for e in x.evidence])

    def test_not_equal_and_loose_equal_and_flipped_operands(self):
        cases = [('mode !== "legacy"', "legacy", True), ('mode !== "legacy"', "modern", False),
                 ('mode == "legacy"', "modern", True), ('mode != "legacy"', "legacy", True),
                 ('"legacy" === mode', "modern", True), ('"legacy" === mode', "legacy", False)]
        before = "".join(f(['export function fmt%d(value: number, mode: string) {' % i, "  if (%s) {" % guard, "    return value.toFixed(2);", "  }", "  return String(value);", "}"])
                         for i, (guard, _l, _e) in enumerate(cases))
        importers = {f"src/u{i}.ts": f'import {{ fmt{i} }} from "./m";\nexport const c = fmt{i}(1, "{literal}");\n' for i, (_g, literal, _e) in enumerate(cases)}
        self.repo(*two_commits(before, before.replace("value.toFixed(2)", "value.toFixed(3)"), importers))
        self.analyze()
        impact = self.impact("src/m.ts")
        excluded = {x.subject_path for x in impact.exclusions}
        for i, (guard, literal, want) in enumerate(cases):
            with self.subTest(guard=guard, literal=literal):
                self.assertEqual(f"src/u{i}.ts" in excluded, want)

    def test_a_change_outside_the_guard_is_not_excluded(self):
        after = GUARD_BEFORE.replace("return String(value);", "return String(value).trim();")
        got, _ = self.run_calls(after=after, calls={"a": CALLS["literal_out"], "b": CALLS["variable"]})
        self.assertEqual(got, {"a": (None, None), "b": (None, None)})  # no guard covers the change: affected, with no reason

    def test_a_change_partly_inside_the_guard_is_not_excluded(self):
        after = GUARD_AFTER.replace("return String(value);", "return String(value).trim();")
        got, _ = self.run_calls(after=after, calls={"a": CALLS["literal_out"]})
        self.assertEqual(got["a"][0], None)

    def test_a_change_to_the_guard_condition_itself_is_not_excluded(self):
        after = GUARD_BEFORE.replace('"legacy"', '"old"')
        got, _ = self.run_calls(after=after, calls={"a": CALLS["literal_out"]})
        self.assertEqual(got["a"], (None, None))

    def test_else_branch_is_the_complement(self):
        before = f(['export function fmt(value: number, mode: string) {', '  if (mode === "legacy") {', "    return 1;", "  } else {", "    return 2;", "  }", "}"])
        after = before.replace("return 2;", "return 3;")
        got, _ = self.run_calls(before=before, after=after, calls={"in": 'fmt(1, "modern")', "out": 'fmt(1, "legacy")'})
        self.assertEqual(got, {"in": (None, "literal_enters_guard"), "out": ("excluded", None)})

    def test_switch_case_block(self):
        before = f(["export function fmt(value: number, mode: string) {", "  switch (mode) {", '    case "legacy":', "      return 1;", '    case "modern":', "      return 2;", "    default:", "      return 3;", "  }", "}"])
        after = before.replace("return 1;", "return 10;")
        got, _ = self.run_calls(before=before, after=after, calls={"in": 'fmt(1, "legacy")', "out": 'fmt(1, "modern")', "var": "fmt(1, m)"})
        self.assertEqual(got, {"in": (None, "literal_enters_guard"), "out": ("excluded", None), "var": (None, "undecidable")})

    def test_a_default_parameter_is_undecidable(self):
        before = f(['export function fmt(value: number, mode = "modern") {', '  if (mode === "legacy") {', "    return 1;", "  }", "  return 2;", "}"])
        got, _ = self.run_calls(before=before, after=before.replace("return 1;", "return 5;"), calls={"a": CALLS["literal_out"]})
        self.assertEqual(got["a"], (None, "undecidable"))

    def test_a_reassigned_parameter_is_undecidable(self):
        before = f(["export function fmt(value: number, mode: string) {", '  mode = mode + "x";', '  if (mode === "legacy") {', "    return 1;", "  }", "  return 2;", "}"])
        got, _ = self.run_calls(before=before, after=before.replace("return 1;", "return 5;"), calls={"a": CALLS["literal_out"]})
        self.assertEqual(got["a"], (None, "undecidable"))

    def test_the_guard_on_the_first_parameter_reads_the_first_argument(self):
        before = f(['export function fmt(mode: string, value: number) {', '  if (mode === "legacy") {', "    return 1;", "  }", "  return 2;", "}"])
        got, _ = self.run_calls(before=before, after=before.replace("return 1;", "return 5;"),
                                calls={"a": 'fmt("modern", 1)', "b": 'fmt(1, "modern")'.replace("1, ", "x, "), "c": 'fmt(...args, "modern")'})
        self.assertEqual(got, {"a": ("excluded", None), "b": (None, "undecidable"), "c": (None, "undecidable")})

    def test_an_arrow_function_with_a_guard(self):
        before = f(['export const fmt = (value: number, mode: string): string => {', '  if (mode === "legacy") {', "    return value.toFixed(2);", "  }", "  return String(value);", "};"])
        got, _ = self.run_calls(before=before, after=before.replace("toFixed(2)", "toFixed(3)"), calls={"a": CALLS["literal_out"], "b": CALLS["literal_in"]})
        self.assertEqual(got, {"a": ("excluded", None), "b": (None, "literal_enters_guard")})

    def test_a_guard_in_a_comment_or_string_is_no_guard(self):
        before = f(['export function fmt(value: number, mode: string) {', '  // if (mode === "legacy") {', '  const s = \'if (mode === "legacy") {\';', "  return value;", "}"])
        got, _ = self.run_calls(before=before, after=before.replace("return value;", "return value + 1;"), calls={"a": CALLS["literal_out"]})
        self.assertEqual(got["a"], (None, None))

    def test_one_excluded_and_one_affected_call_in_a_file_keep_the_file_a_caller(self):
        self.repo(*two_commits(GUARD_BEFORE, GUARD_AFTER, {"src/u.ts": f(['import { fmt } from "./m";', 'export const a = fmt(1, "modern");', 'export const b = fmt(2, "legacy");'])}))
        self.analyze()
        impact = self.impact("src/m.ts")
        (caller,) = impact.callers
        self.assertEqual((caller.path, caller.reason, [c.line for c in caller.call_sites]), ("src/u.ts", "literal_enters_guard", [3]))
        self.assertEqual(impact.exclusions, ())  # the file is not excluded; only a file whose every call is excluded is

    def test_a_guarded_call_through_a_namespace_import(self):
        self.repo(*two_commits(GUARD_BEFORE, GUARD_AFTER, {"src/u.ts": 'import * as m from "./m";\nexport const a = m.fmt(1, "modern");\n'}))
        self.analyze()
        impact = self.impact("src/m.ts")
        self.assertEqual([x.subject_path for x in impact.exclusions], ["src/u.ts"])


# ------------------------------------------------------------------------------ definitions (Shape B)


class Definitions(Base):
    FILES = {
        "src/a/A.tsx": "const mapStateToProps = (s) => s;\nexport default connect(mapStateToProps)(A);\n",
        "src/b/B.tsx": "export const mapStateToProps = (s) => s.b;\n",
        "src/c/C.tsx": "function mapStateToProps(s) {\n  return s;\n}\n",
        "src/d/D.tsx": "const { mapStateToProps } = require('./x');\n",
        "src/e/E.tsx": 'import { mapStateToProps } from "../b/B";\nexport const e = mapStateToProps(1);\n',
        "src/f/F.test.tsx": "const mapStateToProps = (s) => s;\n",
        "src/g/G.tsx": "// const mapStateToProps = 1;\nconst s = 'function mapStateToProps() {}';\nconst o = { mapStateToProps: 1 };\no.mapStateToProps = 2;\n",
    }

    def test_definitions_are_counted_references_imports_comments_and_tests_are_not(self):
        self.use_shared("definitions", {"files": {"tsconfig.json": TSCONFIG, **self.FILES}})
        a = self.adapter(distinct_max=3)
        found = a.definitions("mapStateToProps", self.sha("main"))
        self.assertEqual((found.state, found.count, found.complete), ("found", 4, True))
        self.assertEqual(sorted({e.path for e in found.sites}), ["src/a/A.tsx", "src/b/B.tsx", "src/c/C.tsx", "src/d/D.tsx"])
        self.assertFalse(found.distinctive(3))
        self.assertTrue(found.distinctive(4))
        run = mock.Mock()
        run.analysis = mock.Mock(impacts=())
        self.assertEqual(ss.evidence_problems({"acme/web": self.path}, found.sites), [])  # every cited definition line is there

    def test_a_name_defined_once_is_distinctive(self):
        self.use_shared("definitions", {"files": {"tsconfig.json": TSCONFIG, **self.FILES}})
        found = self.adapter().definitions("A", self.sha("main"))
        self.assertEqual((found.count, found.distinctive(3)), (0, True))

    def test_methods_classes_types_and_exported_names_count(self):
        self.repo({"files": {"src/m.ts": f(["class K {", "  handler() {}", "}", "export interface handler {}", "type handler = 1;", "export { x as handler };", "enum handler { A }"])}})
        found = self.adapter().definitions("handler", self.sha("main"))
        self.assertEqual(found.count, 5)

    def test_a_substring_match_is_not_a_definition(self):
        self.repo({"files": {"src/m.ts": "function mapStateToPropsX() {}\nconst xmapStateToProps = 1;\n"}})
        self.assertEqual(self.adapter().definitions("mapStateToProps", self.sha("main")).count, 0)

    def test_an_unreadable_search_means_not_established_never_distinctive(self):
        """Trap: a grep failure read as 'defined once'."""
        self.use_shared("definitions", {"files": {"tsconfig.json": TSCONFIG, **self.FILES}})
        a = self.adapter()
        unreadable = SearchResult(Read.UNREADABLE, "x", self.sha("main"), (), None, 0, None, "git grep timed out")
        with mock.patch.object(self.git, "grep", return_value=unreadable):
            found = a.definitions("mapStateToProps", self.sha("main"))
        self.assertEqual((found.state, found.count), ("unreadable", None))
        self.assertFalse(found.distinctive(1000))

    def test_a_cut_search_is_not_a_distinct_count(self):
        self.use_shared("definitions", {"files": {"tsconfig.json": TSCONFIG, **self.FILES}})
        found = self.adapter(examine_cap=2).definitions("mapStateToProps", self.sha("main"))
        self.assertFalse(found.complete)
        self.assertFalse(found.distinctive(1000))
        self.assertIn("search_truncated", [r.kind for r in self.records])

    def test_an_unreadable_candidate_means_the_count_is_not_complete(self):
        files = dict(self.FILES)
        files["src/z.ts"] = b"const mapStateToProps = '\xe9';\n"
        self.repo({"files": files})
        found = self.adapter().definitions("mapStateToProps", self.sha("main"))
        self.assertFalse(found.complete)
        self.assertFalse(found.distinctive(1000))
        self.assertIn("read_unreadable", [r.kind for r in self.records])

    def test_a_changed_name_defined_too_often_is_recorded_not_distinctive_and_a_unique_one_is_not(self):
        files = dict(self.FILES)
        files["src/a/A.tsx"] = "const mapStateToProps = (s) => s;\nexport const unique = 1;\n"
        self.repo({"files": files}, {"files": {"src/a/A.tsx": "const mapStateToProps = (s) => s.x;\nexport const unique = 2;\n"}})
        self.analyze(adapter=self.adapter(distinct_max=3))
        self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in self.records], [("not_distinctive", "mapStateToProps", False)])
        self.assertEqual(self.impact("src/a/A.tsx").not_distinctive, ("mapStateToProps",))

    def test_no_distinct_max_records_nothing(self):
        files = dict(self.FILES)
        self.repo({"files": files}, {"files": {"src/a/A.tsx": "const mapStateToProps = (s) => s.x;\n"}})
        self.analyze()
        self.assertEqual(self.records, [])


# ------------------------------------------------------------------------------ shape B: a removed import line


class RemovedImportLine(Base):
    def test_the_text_of_a_removed_import_line_is_never_searched_for(self):
        """Trap: a removed import line's module path read as a defect fingerprint, producing false findings."""
        files = {"src/lib/format.ts": "export const truncate = (t: string) => t;\n",
                 "src/Page.tsx": 'import { truncate } from "@/lib/format";\nexport default function Page() {\n  return <p>{truncate("a")}</p>;\n}\n'}
        files.update({f"src/w{i}.tsx": 'import { truncate } from "@/lib/format";\nexport const w = truncate;\n' for i in range(12)})
        after = files["src/Page.tsx"].replace('import { truncate } from "@/lib/format";\n', "").replace('{truncate("a")}', "a")
        self.repo({"files": files}, {"files": {"src/Page.tsx": after}})
        patterns = []
        original = self.git.grep
        with mock.patch.object(self.git, "grep", lambda c, p, **kw: (patterns.append(p), original(c, p, **kw))[1]):
            self.analyze()
        self.assertTrue(patterns)
        self.assertTrue(all("format" not in p and "truncate" not in p and "@/" not in p for p in patterns), patterns)
        impact = self.impact("src/Page.tsx")
        self.assertEqual(sorted({s.name for s in impact.symbols}), ["<module>", "Page"])
        self.assertEqual([c.path for c in impact.callers], [])  # nothing imports Page; the 12 importers of the same module are not callers


class ProtocolMethods(Base):
    def test_callers_returns_the_call_sites_of_one_symbol(self):
        importers = {"src/u.ts": f(['import { alpha as a1 } from "./m";', "export const x = a1(1);"]), "src/v.ts": 'import { beta } from "./m";\nbeta();\n',
                     "src/w.test.ts": 'import { alpha } from "./m";\nalpha();\n'}
        self.repo(*two_commits(M_BEFORE, M_ALPHA_CHANGED, importers))
        a = self.adapter()
        cs = self.change()
        (fc,) = [x for x in cs.files if x.path == "src/m.ts"]
        (symbol,) = a.changed_symbols(cs, fc)
        sites = a.callers(symbol, self.sha("main"), change=cs)
        self.assertEqual([(c.path, c.symbol, c.line, c.form, c.args) for c in sites], [("src/u.ts", "alpha", 2, "call", ("1",))])
        unnarrowed = a.callers(symbol, self.sha("main"))  # no change given: no pre-change version, nothing is narrowed
        self.assertEqual([c.path for c in unnarrowed], ["src/u.ts", "src/v.ts"])

    def test_the_adapter_satisfies_the_protocol(self):
        from reachmap.adapters import LanguageAdapter
        self.repo({"files": {"src/a.ts": "export const a = 1;\n"}})
        self.assertIsInstance(self.adapter(), LanguageAdapter)

    def test_a_guard_must_hold_every_changed_line_of_the_function_including_nested_ones(self):
        before = f(["export function fmt(value: number, mode: string) {", '  if (mode === "legacy") {', "    const inner = () => {", "      return 1;", "    };", "    inner();", "  }", "  return 2;", "}"])
        after = before.replace("return 1;", "return 10;").replace("return 2;", "return 20;")
        self.repo(*two_commits(before, after, {"src/u.ts": 'import { fmt } from "./m";\nexport const a = fmt(1, "modern");\n'}))
        self.analyze()
        impact = self.impact("src/m.ts")
        self.assertEqual({s.nested for s in impact.symbols}, {("inner",), ()})  # two symbols for one function
        self.assertEqual(impact.exclusions, ())  # the change outside the guard keeps the caller
        self.assertEqual([c.path for c in impact.callers], ["src/u.ts"])

    def test_a_guard_holding_every_changed_line_in_two_nested_groups_excludes(self):
        before = f(["export function fmt(value: number, mode: string) {", '  if (mode === "legacy") {', "    const inner = () => {", "      return 1;", "    };", "    inner();", "  }", "  return 2;", "}"])
        after = before.replace("return 1;", "return 10;").replace("    inner();", "    inner() + 1;")
        self.repo(*two_commits(before, after, {"src/u.ts": 'import { fmt } from "./m";\nexport const a = fmt(1, "modern");\n'}))
        self.analyze()
        impact = self.impact("src/m.ts")
        self.assertEqual({s.nested for s in impact.symbols}, {("inner",), ()})
        self.assertEqual([x.subject_path for x in impact.exclusions], ["src/u.ts"])


class WholeFileAndNoCodeChanges(Base):
    def test_a_deleted_file_is_not_narrowed_what_it_re_exported_is_gone_too(self):
        files = {"src/m.ts": 'export * from "./x";\nexport function alpha() {}\n', "src/x.ts": "export const fromStar = 1;\n",
                 "src/u.ts": 'import { fromStar } from "./m";\nexport const v = fromStar;\n'}
        self.repo({"files": files}, {"delete": ["src/m.ts"]})
        self.analyze()
        impact = self.impact("src/m.ts")
        self.assertEqual([c.path for c in impact.callers], ["src/u.ts"])
        self.assertFalse(impact.narrowed)

    def test_a_comment_only_change_walks_nothing_and_records_nothing(self):
        files = {"src/m.ts": "// old\nexport function alpha() {}\n"}
        files.update({f"src/u{i}.ts": 'import { alpha } from "./m";\nalpha();\n' for i in range(5)})
        self.repo({"files": files}, {"files": {"src/m.ts": "// new\nexport function alpha() {}\n"}})
        greps = []
        original = self.git.grep
        with mock.patch.object(self.git, "grep", lambda c, p, **kw: (greps.append(p), original(c, p, **kw))[1]):
            self.analyze(adapter=self.adapter(hub_importers=2, distinct_max=3))
        impact = self.impact("src/m.ts")
        self.assertEqual((impact.symbols, impact.callers, impact.hub, greps, self.records), ((), (), False, [], []))


class EdgesOfAChange(Base):
    def test_a_deleted_or_renamed_file_without_a_pre_change_version_has_an_incomplete_importer_list(self):
        body = f(["export function a() {", "  return 1;", "}", "// padding", "// padding", "// padding"])
        for name, second in (("deleted", {"delete": ["src/old.ts"]}), ("renamed", {"delete": ["src/old.ts"], "files": {"src/new.ts": body}})):
            with self.subTest(name):
                self.repo({"files": {"src/old.ts": body, "src/u.ts": 'import { a } from "./old";\nexport const v = a();\n'}}, second)
                self.analyze(pre=None)
                impact = self.impact("src/old.ts" if name == "deleted" else "src/new.ts")
                self.assertFalse(impact.importers_complete)
                self.assertEqual(impact.importer_reasons, ("there is no pre-change version to find the importers of the old path in",))
                self.assertEqual((impact.callers, impact.importers), ((), ()))

    def test_a_changed_file_whose_name_is_not_utf8_is_incomplete_with_a_record(self):
        self.repo_raw({b"src/caf\xe9.ts": b"export const a = 1;\n", b"tsconfig.json": TSCONFIG.encode()}, {b"src/caf\xe9.ts": b"export const a = 2;\n"})
        self.analyze()
        (impact,) = [i for i in self.analysis.impacts if i.path.startswith("src/caf")]
        self.assertEqual((impact.symbols, impact.importers_complete), ((), False))
        self.assertEqual([(r.kind, r.blocks_selection) for r in self.records], [("read_unreadable", True)])

    def test_a_file_git_calls_binary_is_one_module_level_change_with_its_importers_and_a_record(self):
        self.repo({"files": {"src/m.ts": "export const a = 1;\n", "src/u.ts": 'import { a } from "./m";\nexport const v = a;\n'}}, {"files": {"src/m.ts": b"export const a = 2;\x00\n"}})
        self.analyze()
        impact = self.impact("src/m.ts")
        self.assertEqual([(s.name, s.change) for s in impact.symbols], [("<module>", "modified")])
        self.assertEqual([c.path for c in impact.callers], ["src/u.ts"])
        self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in self.records], [("file_not_analysed", "src/m.ts", False)])

    def test_a_definition_in_a_file_git_grep_cannot_search_makes_the_count_partial(self):
        repo, shas = sg.make_raw_repo(self.tmp, "links", [[
            ("100644", b"a.ts", b"export const settlementRef = 1;\n"), ("120000", b"link.ts", b"a.ts"), ("100644", b"b.ts", b"export const x = settlementRef;\n"),
        ]])
        self.path, self.url = repo, sg.remote_url(repo)
        self.records = []
        self.git = Git(self.tmp / "cache", env=sg.isolated_env(self.tmp / "home"), record_to=self.records)
        found = self.adapter(aliases_from=None).definitions("settlementRef", shas[-1])
        self.assertEqual((found.state, found.count, found.complete), ("found", 1, False))
        self.assertFalse(found.distinctive(1000))
        self.assertEqual([(r.kind, r.blocks_selection) for r in self.records], [("read_unreadable", True)])

    def test_a_guard_excludes_nothing_without_a_pre_change_version(self):
        self.repo(*two_commits(GUARD_BEFORE, GUARD_AFTER, {"src/u.ts": 'import { fmt } from "./m";\nexport const a = fmt(1, "modern");\n'}))
        self.analyze(pre=None)
        impact = self.impact("src/m.ts")
        self.assertEqual((impact.exclusions, [c.path for c in impact.callers]), ((), ["src/u.ts"]))
        self.assertEqual(impact.callers[0].reason, None)

    def test_a_pure_rename_removes_the_module_at_its_old_path(self):
        body = f(["export function a() {", "  return 1;", "}", "// padding", "// padding", "// padding"])
        self.repo({"files": {"src/old.ts": body, "src/u.ts": 'import { a } from "./old";\nexport const v = a();\n'}},
                  {"delete": ["src/old.ts"], "files": {"src/new.ts": body}})
        self.analyze()
        impact = self.impact("src/new.ts")
        self.assertEqual([(s.name, s.change, s.side) for s in impact.symbols], [("<module>", "removed", "before")])
        self.assertEqual([c.path for c in impact.callers], ["src/u.ts"])  # it imported the old path

    def test_a_file_whose_hunks_are_not_available_is_a_module_level_change(self):
        self.repo({"files": {"src/m.ts": "export function a() {\n  return 1;\n}\n"}}, {"files": {"src/m.ts": "export function a() {\n  return 2;\n}\n"}})
        cs = self.change()
        (fc,) = cs.files
        syms = self.adapter().changed_symbols(cs, dataclasses.replace(fc, hunks=(), content_available=False))
        self.assertEqual([(s.name, s.change) for s in syms], [("<module>", "modified")])


R_BEFORE = f([
    "export function f(x: string) {",             # 1
    "  return x;",
    "}",
    "export function g(x: string) {",             # 4
    "  return f(x) + '!';",
    "}",
    "export const h = (x: string) => f(x);",      # 7
    "export const table = { run: f };",           # 8
    "export class K {",                           # 9
    "  run() {",
    "    return f('a');",
    "  }",
    "}",
    "export function unrelated() {",              # 14
    "  return 'f(x) in a string' + 1; // f(x) in a comment",
    "}",
    "export function viaG() {",                   # 17
    "  return g('b');",
    "}",
])
R_AFTER = R_BEFORE.replace("  return x;\n", "  return x.trim();\n")


class IntraFileReach(Base):
    """Narrowing follows what a changed declaration reaches inside its own file (review F1)."""

    def importers(self, names):
        return {f"src/only_{n}.ts": f'import {{ {n} }} from "./m";\nexport const used = {n};\n' for n in names}

    def run_with(self, before=R_BEFORE, after=R_AFTER, names=("f", "g", "h", "table", "K", "unrelated", "viaG"), pre="merge_base"):
        self.repo(*two_commits(before, after, self.importers(names)))
        self.analyze(pre=pre)
        return self.impact("src/m.ts")

    def test_importers_of_what_uses_the_changed_function_are_kept(self):
        impact = self.run_with()
        self.assertEqual(sorted(c.path for c in impact.callers), ["src/only_K.ts", "src/only_f.ts", "src/only_g.ts", "src/only_h.ts", "src/only_table.ts", "src/only_viaG.ts"])
        self.assertEqual([x.subject_path for x in impact.exclusions], ["src/only_unrelated.ts"])  # no code of it names f

    def test_the_exclusion_cites_the_closure_as_well_as_the_import_line(self):
        impact = self.run_with()
        (ex,) = impact.exclusions
        cited = [(e.path, e.line) for e in ex.evidence]
        self.assertEqual(cited[0], ("src/only_unrelated.ts", 1))
        self.assertIn(("src/m.ts", 1), cited)  # the changed declaration
        for line in (4, 7, 8, 9, 17):          # what reaches it, g, h, table, K and viaG (through g)
            self.assertIn(("src/m.ts", line), cited)

    def test_reach_is_followed_to_a_fixed_point(self):
        impact = self.run_with(names=("viaG", "unrelated"))
        self.assertEqual([c.path for c in impact.callers], ["src/only_viaG.ts"])

    def test_an_unexported_helper_used_by_an_exported_function(self):
        before = f(["function helper() {", "  return 1;", "}", "export function a() {", "  return helper();", "}", "export function b() {", "  return 2;", "}"])
        self.repo(*two_commits(before, before.replace("return 1;", "return 10;"), self.importers(("a", "b"))))
        self.analyze()
        impact = self.impact("src/m.ts")
        self.assertEqual([c.path for c in impact.callers], ["src/only_a.ts"])
        self.assertEqual([x.subject_path for x in impact.exclusions], ["src/only_b.ts"])
        self.assertTrue(impact.narrowed)

    def test_an_unexported_change_that_nothing_exported_reaches_is_not_narrowed(self):
        before = f(["function dead() {", "  return 1;", "}", "export function b() {", "  return 2;", "}"])
        self.repo(*two_commits(before, before.replace("return 1;", "return 10;"), self.importers(("b",))))
        self.analyze()
        impact = self.impact("src/m.ts")
        self.assertEqual(([c.path for c in impact.callers], impact.exclusions, impact.narrowed), (["src/only_b.ts"], (), False))

    def test_a_renamed_export_of_a_reaching_declaration(self):
        before = f(["function impl(x: string) {", "  return x;", "}", "function wrap() {", "  return impl('a');", "}", "export { impl as api, wrap as other };", "export function third() {", "  return 3;", "}"])
        self.repo(*two_commits(before, before.replace("return x;", "return x + 1;"), self.importers(("api", "other", "third"))))
        self.analyze()
        impact = self.impact("src/m.ts")
        self.assertEqual(sorted(c.path for c in impact.callers), ["src/only_api.ts", "src/only_other.ts"])
        self.assertEqual([x.subject_path for x in impact.exclusions], ["src/only_third.ts"])

    def test_module_level_code_that_uses_the_changed_name_stops_narrowing(self):
        before = R_BEFORE + "registry.register(f);\n"
        impact = self.run_with(before=before, after=before.replace("  return x;\n", "  return x.trim();\n"), names=("unrelated",))
        self.assertEqual(([c.path for c in impact.callers], impact.exclusions, impact.narrowed), (["src/only_unrelated.ts"], (), False))

    def test_an_export_list_is_not_module_level_use(self):
        before = f(["function a() {", "  return 1;", "}", "function b() {", "  return 2;", "}", "export { a, b as c };"])
        self.repo(*two_commits(before, before.replace("return 1;", "return 10;"), self.importers(("a", "c"))))
        self.analyze()
        impact = self.impact("src/m.ts")
        self.assertEqual(([c.path for c in impact.callers], [x.subject_path for x in impact.exclusions]), (["src/only_a.ts"], ["src/only_c.ts"]))

    def test_property_access_reaches_a_changed_class_member(self):
        """A method or a field of a class changes; another function of the file reads it as `doc.total`: its importers are affected."""
        for member in ("  total() {", "  total = 1;"):
            with self.subTest(member):
                body = ["    return 1;", "  }"] if member.endswith("{") else []
                before = f(["export class Invoice {", member, *body, "}", "export function summarize(doc) {", "  return doc.total();", "}", "export function unrelated() {", "  return 3;", "}"])
                after = before.replace("return 1;", "return 2;") if body else before.replace("total = 1;", "total = 2;")
                self.repo(*two_commits(before, after, self.importers(("summarize", "unrelated"))))
                self.analyze()
                impact = self.impact("src/m.ts")
                self.assertEqual([c.path for c in impact.callers], ["src/only_summarize.ts"])
                self.assertEqual([x.subject_path for x in impact.exclusions], ["src/only_unrelated.ts"])

    def test_module_level_property_reads_of_a_changed_member_stop_narrowing(self):
        before = f(["export class Invoice {", "  total() {", "    return 1;", "  }", "}", "export function unrelated() {", "  return 3;", "}", "console.log(thing.total);"])
        self.repo(*two_commits(before, before.replace("return 1;", "return 2;"), self.importers(("unrelated",))))
        self.analyze()
        impact = self.impact("src/m.ts")
        self.assertEqual(([c.path for c in impact.callers], impact.exclusions, impact.narrowed), (["src/only_unrelated.ts"], (), False))

    def test_a_constant_that_calls_a_changed_function_when_the_module_loads_reaches_every_importer(self):
        before = f(["export function load() {", "  return 1;", "}", "export const CONFIG = load();", "export function unrelated() {", "  return 3;", "}"])
        self.repo(*two_commits(before, before.replace("return 1;", "return 2;"), self.importers(("unrelated",))))
        self.analyze()
        impact = self.impact("src/m.ts")
        self.assertEqual(([c.path for c in impact.callers], impact.exclusions, impact.narrowed), (["src/only_unrelated.ts"], (), False))

    def test_a_constant_that_only_holds_a_changed_function_does_not_stop_narrowing(self):
        before = f(["export function load() {", "  return 1;", "}", "export const HOLDER = [load];", "export function unrelated() {", "  return 3;", "}"])
        self.repo(*two_commits(before, before.replace("return 1;", "return 2;"), self.importers(("unrelated", "HOLDER"))))
        self.analyze()
        impact = self.impact("src/m.ts")
        self.assertEqual(([c.path for c in impact.callers], [x.subject_path for x in impact.exclusions], impact.narrowed), (["src/only_HOLDER.ts"], ["src/only_unrelated.ts"], True))

    def test_dynamic_access_stops_narrowing(self):
        for text in ("eval('f(1)');", "new Function('return f')();", "export const dyn = (k: string) => this[k];", "export const w = window[name];"):
            with self.subTest(text=text):
                before = R_BEFORE + text + "\n"
                impact = self.run_with(before=before, after=before.replace("  return x;\n", "  return x.trim();\n"), names=("unrelated",))
                self.assertEqual((impact.exclusions, impact.narrowed), ((), False), text)

    def test_a_file_that_does_not_parse_reliably_is_not_narrowed(self):
        before = f(["export function a(s: string, ok: boolean) {", "  if (ok) /[{]/.test(s);", "  return 1;", "}", "export function b() {", "  return 2;", "}"])
        self.repo(*two_commits(before, before.replace("return 2;", "return 3;"), self.importers(("a", "b"))))
        self.analyze()
        impact = self.impact("src/m.ts")
        self.assertEqual(sorted(c.path for c in impact.callers), ["src/only_a.ts", "src/only_b.ts"])
        self.assertEqual((impact.exclusions, impact.narrowed), ((), False))

    def test_without_a_pre_change_version_nothing_is_narrowed(self):
        impact = self.run_with(pre=None, names=("unrelated", "f"))
        self.assertEqual((impact.exclusions, len(impact.callers)), ((), 2))

    def test_callers_uses_the_same_narrowing_and_none_without_a_change(self):
        self.repo(*two_commits(R_BEFORE, R_AFTER, self.importers(("g", "unrelated"))))
        a = self.adapter()
        cs = self.change()
        (fc,) = [x for x in cs.files if x.path == "src/m.ts"]
        (sym,) = [s for s in a.changed_symbols(cs, fc) if s.name == "f"]
        head = self.sha("main")
        with_change = a.callers(sym, head, change=cs)
        self.assertEqual(sorted({c.path for c in with_change}), ["src/only_g.ts"])  # g reaches f; unrelated does not
        no_change = a.callers(sym, head)
        self.assertEqual(sorted({c.path for c in no_change}), ["src/only_g.ts", "src/only_unrelated.ts"])  # no pre-change version known: nothing is narrowed


SHADOW_CASES = {
    "plain": ('export function fmt(x: number, mode: string) {\n  if (mode === "legacy") {\n    return x + 1;\n  }\n  return x;\n}\n', "return x + 1;", "return x + 2;", "excluded"),
    "arrow_param": ('export function fmt(x: number, mode: string) {\n  return [x].map((mode) => {\n    if (mode === "legacy") {\n      return 1;\n    }\n    return 0;\n  });\n}\n', "return 1;", "return 2;", "undecidable"),
    "arrow_single_param": ('export function fmt(x: number, mode: string) {\n  return [x].map(mode => {\n    if (mode === "legacy") {\n      return 1;\n    }\n    return 0;\n  });\n}\n', "return 1;", "return 2;", "undecidable"),
    "for_of": ('export function fmt(x: number, mode: string) {\n  for (const mode of ["legacy"]) {\n    if (mode === "legacy") {\n      x = x + 1;\n    }\n  }\n  return x;\n}\n', "x = x + 1;", "x = x + 2;", "undecidable"),
    "for_of_assign": ('export function fmt(x: number, mode: string) {\n  for (mode of ["legacy"]) {\n    if (mode === "legacy") {\n      x = x + 1;\n    }\n  }\n  return x;\n}\n', "x = x + 1;", "x = x + 2;", "undecidable"),
    "for_in": ('export function fmt(x: number, mode: string) {\n  for (var mode in { legacy: 1 }) {\n    if (mode === "legacy") {\n      x = x + 1;\n    }\n  }\n  return x;\n}\n', "x = x + 1;", "x = x + 2;", "undecidable"),
    "inner_const_destructure": ('export function fmt(o: any, mode: string) {\n  {\n    const { mode } = o;\n    if (mode === "legacy") {\n      return 1;\n    }\n  }\n  return 0;\n}\n', "return 1;", "return 2;", "undecidable"),
    "inner_let": ('export function fmt(o: any, mode: string) {\n  {\n    let mode = o.m;\n    if (mode === "legacy") {\n      return 1;\n    }\n  }\n  return 0;\n}\n', "return 1;", "return 2;", "undecidable"),
    "catch_param": ('export function fmt(x: number, mode: string) {\n  try { x = 1; } catch (mode) {\n    if (mode === "legacy") {\n      return 1;\n    }\n  }\n  return 0;\n}\n', "return 1;", "return 2;", "undecidable"),
    "inner_function_param": ('export function fmt(x: number, mode: string) {\n  function inner(mode: string) {\n    if (mode === "legacy") {\n      return 1;\n    }\n    return 0;\n  }\n  return inner("legacy");\n}\n', "return 1;", "return 2;", "undecidable"),
    "inner_method_param": ('export function fmt(x: number, mode: string) {\n  const o = {\n    run(mode: string) {\n      if (mode === "legacy") {\n        return 1;\n      }\n      return 0;\n    },\n  };\n  return o.run("legacy");\n}\n', "return 1;", "return 2;", "undecidable"),
    "array_destructuring_assignment": ('export function fmt(o: any, mode: string) {\n  [mode] = [o];\n  if (mode === "legacy") {\n    return 1;\n  }\n  return 0;\n}\n', "return 1;", "return 2;", "undecidable"),
    "object_destructuring_assignment": ('export function fmt(o: any, mode: string) {\n  ({ mode } = o);\n  if (mode === "legacy") {\n    return 1;\n  }\n  return 0;\n}\n', "return 1;", "return 2;", "undecidable"),
    "closure_assigns_it": ('export function fmt(x: number, mode: string) {\n  const set = () => { mode = "legacy"; };\n  set();\n  if (mode === "legacy") {\n    return 1;\n  }\n  return 0;\n}\n', "return 1;", "return 2;", "undecidable"),
    "var_redeclared": ('export function fmt(x: number, mode: string) {\n  if (x) { var mode = "legacy"; }\n  if (mode === "legacy") {\n    return 1;\n  }\n  return 0;\n}\n', "return 1;", "return 2;", "undecidable"),
    "arguments_object": ('export function fmt(x: number, mode: string) {\n  arguments[1] = "legacy";\n  if (mode === "legacy") {\n    return 1;\n  }\n  return 0;\n}\n', "return 1;", "return 2;", "undecidable"),
    "nested_function_declared_after": ('export function fmt(x: number, mode: string) {\n  if (mode === "legacy") {\n    return 1;\n  }\n  function mode2(mode: string) { return mode; }\n  return 0;\n}\n', "return 1;", "return 2;", "undecidable"),
    "use_of_another_name_is_fine": ('export function fmt(x: number, mode: string) {\n  let m = mode;\n  var mode2 = 1;\n  if (mode === "legacy") {\n    return 1;\n  }\n  return 0;\n}\n', "return 1;", "return 2;", "excluded"),
    "mode_passed_to_a_call_is_fine": ('export function fmt(x: number, mode: string) {\n  log(mode);\n  if (mode === "legacy") {\n    return 1;\n  }\n  return 0;\n}\n', "return 1;", "return 2;", "excluded"),
}


class ShadowedParameters(Base):
    """A guard on a name that is bound or assigned anywhere else in the function proves nothing (review F2)."""

    def verdict(self, source, a, b):
        importers = {"src/caller.ts": 'import { fmt } from "./g";\nexport const r1 = fmt(1, "modern");\n'}
        self.repo({"files": {"src/g.ts": source, **importers}}, {"files": {"src/g.ts": source.replace(a, b, 1)}})
        self.analyze()
        impact = self.impact("src/g.ts")
        excluded = [x.reason for x in impact.exclusions if x.subject_path == "src/caller.ts"]
        callers = [c for c in impact.callers if c.path == "src/caller.ts"]
        if excluded:
            return "excluded"
        return callers[0].reason if callers else "missing"

    def test_each_shadowing_or_rebinding_form(self):
        for name, (source, a, b, want) in SHADOW_CASES.items():
            with self.subTest(case=name):
                self.assertEqual(self.verdict(source, a, b), want)

    def test_loose_comparison_with_null_or_undefined_is_no_guard(self):
        for cond in ("mode == null", "mode == undefined", "mode != null", "mode === undefined"):
            with self.subTest(cond=cond):
                source = 'export function fmt(x: number, mode: any) {\n  if (%s) {\n    return 1;\n  }\n  return 0;\n}\n' % cond
                self.assertEqual(self.verdict(source, "return 1;", "return 2;"), None)  # affected, no guard covers the change

    def test_a_removed_line_outside_the_guard_and_an_added_line_inside_do_not_exclude(self):
        """Review F8: the verdict looks at the removed side too."""
        before = 'export function fmt(x: number, mode: string) {\n  log(x);\n  if (mode === "legacy") {\n    return x + 1;\n  }\n  return x;\n}\nfunction log(x: number) { return x; }\n'
        after = 'export function fmt(x: number, mode: string) {\n  if (mode === "legacy") {\n    return x + 1;\n    log(x);\n  }\n  return x;\n}\nfunction log(x: number) { return x; }\n'
        self.repo({"files": {"src/g.ts": before, "src/c.ts": 'import { fmt } from "./g";\nexport const r = fmt(1, "modern");\n'}}, {"files": {"src/g.ts": after}})
        self.analyze()
        impact = self.impact("src/g.ts")
        self.assertEqual(([c.path for c in impact.callers], impact.exclusions), (["src/c.ts"], ()))
        (sym,) = impact.symbols
        self.assertTrue(sym.added_lines and sym.removed_lines)

    def test_an_added_line_outside_the_guard_and_a_removed_line_inside_do_not_exclude(self):
        before = 'export function fmt(x: number, mode: string) {\n  if (mode === "legacy") {\n    log(x);\n    return x + 1;\n  }\n  return x;\n}\nfunction log(x: number) { return x; }\n'
        after = 'export function fmt(x: number, mode: string) {\n  log(x);\n  if (mode === "legacy") {\n    return x + 1;\n  }\n  return x;\n}\nfunction log(x: number) { return x; }\n'
        self.repo({"files": {"src/g.ts": before, "src/c.ts": 'import { fmt } from "./g";\nexport const r = fmt(1, "modern");\n'}}, {"files": {"src/g.ts": after}})
        self.analyze()
        impact = self.impact("src/g.ts")
        self.assertEqual(([c.path for c in impact.callers], impact.exclusions), (["src/c.ts"], ()))

    def test_every_removed_line_inside_the_guard_still_excludes(self):
        before = 'export function fmt(x: number, mode: string) {\n  if (mode === "legacy") {\n    log(x);\n    return x + 1;\n  }\n  return x;\n}\nfunction log(x: number) { return x; }\n'
        after = before.replace("    log(x);\n", "")
        self.repo({"files": {"src/g.ts": before, "src/c.ts": 'import { fmt } from "./g";\nexport const r = fmt(1, "modern");\n'}}, {"files": {"src/g.ts": after}})
        self.analyze()
        self.assertEqual([x.subject_path for x in self.impact("src/g.ts").exclusions], ["src/c.ts"])


class DegradedParsing(Base):
    """A file the lexer cannot follow is not trusted: review F3, F4, F11, F12."""

    FORMAT = "export function fmt(x: string) {\n  return x;\n}\n"

    def importers_of_format(self, files, **kw):
        self.repo({"files": {"tsconfig.json": TSCONFIG, "src/lib/format.ts": self.FORMAT, **files}})
        res = self.adapter(**kw).importers("src/lib/format.ts", self.sha("main"))
        return res

    def assert_degraded(self, res, path):
        self.assertFalse(res.complete, res.reasons)
        self.assertTrue(any(path in r for r in res.reasons), res.reasons)
        self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in self.records if r.kind == "parse_degraded"], [("parse_degraded", path, True)])

    def test_jsx_text_with_a_block_comment_opener_hides_nothing_silently(self):
        files = {"src/routes.tsx": 'import React, { lazy } from "react";\nexport const Help = () => <p>Use /* to match all</p>;\nexport const Page = lazy(() => import("@/lib/format"));\n'}
        self.assert_degraded(self.importers_of_format(files), "src/routes.tsx")

    def test_jsx_text_with_a_url_is_not_a_comment_so_the_import_after_it_is_found(self):
        files = {"src/routes.tsx": 'import { lazy } from "react";\nexport const A = () => <a>http://x {String(lazy(() => import("@/lib/format")))}</a>;\n'}
        res = self.importers_of_format(files)
        self.assertEqual((res.complete, res.paths, self.records), (True, ("src/routes.tsx",), []))

    def test_a_stray_backtick_in_jsx_text(self):
        files = {"src/r.tsx": 'export const A = () => <kbd>`</kbd>;\nexport const m = require("./lib/format");\n'}
        self.assert_degraded(self.importers_of_format(files), "src/r.tsx")

    def test_a_regex_after_a_closing_paren(self):
        files = {"src/a.ts": 'export function t(s: string, ok: boolean) {\n  if (ok) /`/.test(s);\n  return 1;\n}\nexport const m = import("./lib/format");\n'}
        self.assert_degraded(self.importers_of_format(files), "src/a.ts")

    def test_a_clean_candidate_is_not_degraded(self):
        res = self.importers_of_format({"src/a.tsx": 'import { fmt } from "@/lib/format";\nexport const A = () => <p>Don\'t {fmt("a")}</p>;\n'})
        self.assertEqual((res.complete, res.paths, self.records), (True, ("src/a.tsx",), []))

    def test_an_import_with_an_expression_is_unresolved_and_blocks(self):
        for name, text in (("template", 'const n = "format";\nexport const m = import(`./lib/${n}`);\n'), ("variable", 'const p = "./lib/format";\nmodule.exports = require(p);\n')):
            with self.subTest(case=name):
                res = self.importers_of_format({"src/a.js": text})
                self.assertFalse(res.complete)
                self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in self.records if r.kind == "specifier_unresolved"], [("specifier_unresolved", "src/a.js", True)])

    def test_a_changed_file_that_does_not_lex_is_one_module_level_change_with_a_record(self):
        m0 = 'export function a(s: string, ok: boolean) {\n  if (ok) /[{]/.test(s);\n  return 1;\n}\n\nexport function b() {\n  return 2;\n}\n'
        files = {"src/m.ts": m0, "src/useA.ts": 'import { a } from "./m";\na("x", true);\n', "src/useB.ts": 'import { b } from "./m";\nb();\n'}
        self.repo({"files": files}, {"files": {"src/m.ts": m0.replace("return 2;", "return 3;")}})
        self.analyze()
        impact = self.impact("src/m.ts")
        self.assertEqual([s.name for s in impact.symbols], ["<module>"])
        self.assertEqual(sorted(c.path for c in impact.callers), ["src/useA.ts", "src/useB.ts"])
        self.assertEqual((impact.exclusions, impact.narrowed), ((), False))
        self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in self.records], [("parse_degraded", "src/m.ts", False)])

    def test_a_degraded_before_text_alone_is_enough(self):
        bad = 'export function a(s: string, ok: boolean) {\n  if (ok) /[{]/.test(s);\n  return 1;\n}\nexport function b() {\n  return 2;\n}\n'
        good = bad.replace("if (ok) /[{]/.test(s);", "if (ok) { /[{]/.test(s); }")
        self.repo({"files": {"src/m.ts": bad, "src/useB.ts": 'import { b } from "./m";\nb();\n'}}, {"files": {"src/m.ts": good.replace("return 2;", "return 3;")}})
        self.analyze()
        impact = self.impact("src/m.ts")
        self.assertEqual(([s.name for s in impact.symbols], [c.path for c in impact.callers]), (["<module>"], ["src/useB.ts"]))

    def test_a_symlinked_js_file_is_not_searched_and_the_list_says_so(self):
        repo, shas = sg.make_raw_repo(self.tmp, "links", [[
            ("100644", b"a.ts", b'import { fmt } from "./format";\nfmt("a");\n'), ("120000", b"b.ts", b"a.ts"),
            ("100644", b"format.ts", self.FORMAT.encode()), ("120000", b"c.md", b"README.md"),
        ]])
        self.path, self.url = repo, sg.remote_url(repo)
        self.records = []
        self.git = Git(self.tmp / "cache", env=sg.isolated_env(self.tmp / "home"), record_to=self.records)
        res = self.adapter(aliases_from=None).importers("format.ts", shas[-1])
        self.assertEqual(res.paths, ("a.ts",))
        self.assertFalse(res.complete)
        self.assertTrue(any("b.ts" in r and "symlink" in r for r in res.reasons), res.reasons)
        self.assertEqual([(r.kind, r.blocks_selection) for r in self.records], [("read_unreadable", True)])
        self.assertIn("b.ts", self.records[0].subject)


class UnresolvedSpecifiers(Base):
    """A specifier that names the file but resolves to nothing is an importer that may be missed (review F5)."""

    FORMAT = "export function fmt(x: string) {\n  return x;\n}\n"

    def search(self, files, tsconfig=TSCONFIG, **kw):
        files = {"src/lib/format.ts": self.FORMAT, **files}
        if tsconfig is not None:
            files["tsconfig.json"] = tsconfig
        self.repo({"files": files}, tsconfig=False)
        return self.adapter(**kw).importers("src/lib/format.ts", self.sha("main"))

    def unresolved(self):
        return sorted((r.subject, r.blocks_selection) for r in self.records if r.kind == "specifier_unresolved")

    def test_aliases_this_adapter_does_not_read_are_reported(self):
        for spec, prefix in (("~/lib/format", "~/lib"), ("#lib/format", "#lib"), ("@acme/lib/format", "@acme/lib"), ("@acme/format", "@acme")):
            with self.subTest(spec=spec):
                res = self.search({"src/a.ts": f'import {{ fmt }} from "{spec}";\nfmt("a");\n'})
                self.assertEqual(res.paths, ())
                self.assertFalse(res.complete)
                self.assertTrue(any(spec in r for r in res.reasons), res.reasons)
                self.assertEqual(self.unresolved(), [(prefix, True)])

    def test_a_package_or_folder_called_safe_does_not_trip_the_banned_wording_check(self):
        """The detail of a record is checked for banned wording as written; quoted code must not crash the run."""
        res = self.search({"src/a.ts": 'import { fmt } from "~/safe/format";\nfmt("a");\n'})
        self.assertFalse(res.complete)
        (rec,) = [r for r in self.records if r.kind == "specifier_unresolved"]
        self.assertEqual((rec.subject, rec.blocks_selection), ("~/safe", True))
        self.assertNotIn("safe", rec.detail)

    def test_one_record_per_prefix(self):
        files = {"src/a.ts": 'import { fmt } from "~/lib/format";\n', "src/b.ts": 'import { fmt } from "~/lib/format";\n', "src/c.ts": 'import { fmt } from "#lib/format";\n'}
        res = self.search(files)
        self.assertFalse(res.complete)
        self.assertEqual(self.unresolved(), [("#lib", True), ("~/lib", True)])
        self.assertEqual(len([r for r in res.reasons if "resolves to nothing" in r]), 3)  # but every importer is named in the reasons

    def test_a_tsconfig_that_is_only_in_a_package_folder_leaves_root_aliases_unresolved(self):
        res = self.search({"src/a.ts": 'import { fmt } from "@/lib/format";\n', "packages/web/tsconfig.json": TSCONFIG}, tsconfig=None)
        self.assertFalse(res.complete)
        self.assertEqual(self.unresolved(), [("@/lib", True)])

    def test_external_packages_and_missing_relative_files_are_not_reported(self):
        files = {"src/a.ts": 'import React from "react";\nimport d from "lodash/debounce";\nimport x from "./nope/format-other";\nimport y from "../nowhere/format";\n'}
        res = self.search(files)
        self.assertEqual((res.complete, self.records), (True, []))

    def test_a_bare_package_named_like_the_file_is_reported(self):
        res = self.search({"src/a.ts": 'import fmt from "format";\n'})
        self.assertFalse(res.complete)
        self.assertEqual(self.unresolved(), [("format", True)])

    def test_a_specifier_that_resolves_to_another_file_is_not_reported(self):
        res = self.search({"src/other/format.ts": "export const x = 1;\n", "src/a.ts": 'import { x } from "@/other/format";\n'})
        self.assertEqual((res.complete, self.records), (True, []))

    def test_an_index_file_is_named_by_its_folder(self):
        self.repo({"files": {"tsconfig.json": TSCONFIG, "src/ui/index.ts": "export const B = 1;\n", "src/a.ts": 'import { B } from "~/ui";\nimport { C } from "~/ui/index";\n'}}, tsconfig=False)
        res = self.adapter().importers("src/ui/index.ts", self.sha("main"))
        self.assertFalse(res.complete)
        self.assertEqual(self.unresolved(), [("~", True), ("~/ui", True)])

    def test_an_absent_named_alias_file_is_recorded_when_it_matters(self):
        res = self.search({"src/a.ts": 'import { fmt } from "@/lib/format";\n'}, tsconfig=None, aliases_from="config/tsconfig.app.json")
        self.assertFalse(res.complete)
        self.assertEqual(sorted((r.kind, r.blocks_selection) for r in self.records), [("alias_config_gap", False), ("specifier_unresolved", True)])
        gap = next(r for r in self.records if r.kind == "alias_config_gap")
        self.assertIn("config/tsconfig.app.json", gap.subject)

    def test_an_absent_named_alias_file_alone_is_not_a_record(self):
        res = self.search({"src/a.ts": 'import { fmt } from "./lib/format";\n'}, tsconfig=None, aliases_from="config/tsconfig.app.json")
        self.assertEqual((res.paths, res.complete, self.records), (("src/a.ts",), True, []))  # nothing it would have resolved was in the way

    def test_an_extends_of_an_unread_package_that_sets_no_paths_makes_the_list_incomplete(self):
        res = self.search({"src/a.ts": 'import { fmt } from "./lib/format";\n'}, tsconfig='{"extends": "@acme/tsconfig/base.json"}')
        self.assertFalse(res.complete)
        self.assertTrue(any("package" in r for r in res.reasons), res.reasons)

    def test_an_extends_of_an_unread_package_next_to_paths_of_its_own_is_a_stated_limit_only(self):
        res = self.search({"src/a.ts": 'import { fmt } from "@/lib/format";\n'}, tsconfig='{"extends": "@acme/tsconfig/base.json", "compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}')
        self.assertEqual((res.paths, res.complete), (("src/a.ts",), True))


class NonJsFiles(Base):
    """A changed file the adapter cannot read as code is never a complete empty answer (review F6)."""

    BASE = {"src/config.json": '{"a": 1}\n', "src/a.ts": 'import cfg from "./config.json";\nexport const x = cfg.a;\n',
            "src/B.vue": "<template><p/></template>\n", "src/b.ts": 'import B from "./B.vue";\nexport default B;\n'}

    def test_a_changed_json_file_is_a_module_level_change_with_its_importers(self):
        self.repo({"files": self.BASE}, {"files": {"src/config.json": '{"a": 2}\n'}})
        self.analyze()
        impact = self.impact("src/config.json")
        self.assertEqual([(s.name, s.change, s.side) for s in impact.symbols], [("<module>", "modified", "after")])
        self.assertEqual([(c.path, c.symbol) for c in impact.callers], [("src/a.ts", "x")])
        self.assertEqual((impact.importers_complete, impact.narrowed, self.records), (True, False, []))

    def test_json_is_imported_without_its_extension_too(self):
        files = {**self.BASE, "src/c.ts": 'import cfg from "./config";\nexport const y = cfg;\n'}
        self.repo({"files": files}, {"files": {"src/config.json": '{"a": 3}\n'}})
        self.analyze()
        self.assertEqual(sorted(c.path for c in self.impact("src/config.json").callers), ["src/a.ts", "src/c.ts"])

    def test_a_deleted_json_file_is_followed_at_the_pre_change_commit(self):
        self.repo({"files": self.BASE}, {"delete": ["src/config.json"]})
        self.analyze()
        impact = self.impact("src/config.json")
        self.assertEqual(([s.change for s in impact.symbols], [c.path for c in impact.callers]), (["removed"], ["src/a.ts"]))

    def test_a_json_file_nothing_imports_is_a_complete_empty_answer(self):
        self.repo({"files": {"package.json": '{"a": 1}\n', "src/a.ts": "export const a = 1;\n"}}, {"files": {"package.json": '{"a": 2}\n'}})
        self.analyze()
        impact = self.impact("package.json")
        self.assertEqual((impact.callers, impact.importers_complete, self.records), ((), True, []))

    def test_a_changed_file_that_js_imports_through_a_loader_is_not_analysed_and_says_so(self):
        for name in ("src/B.vue", "src/B.svelte", "src/B.mdx", "src/B.css", "src/B.scss", "src/B.svg"):
            with self.subTest(file=name):
                self.repo({"files": {name: "x\n", "src/b.ts": f'import B from "./{name.split("/")[1]}";\n'}}, {"files": {name: "y\n"}})
                self.analyze()
                impact = self.impact(name)
                self.assertEqual((impact.symbols, impact.importers, impact.importers_complete), ((), (), False))
                self.assertTrue(any("not analysed" in r for r in impact.importer_reasons), impact.importer_reasons)
                self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in self.records], [("file_not_analysed", name, False)])

    def test_documents_and_images_are_not_flagged(self):
        self.repo({"files": {"README.md": "a\n", "docs/a.txt": "a\n", "yarn.lock": "a\n"}}, {"files": {"README.md": "b\n", "docs/a.txt": "b\n", "yarn.lock": "b\n"}})
        self.analyze()
        self.assertEqual(self.records, [])
        self.assertTrue(all(i.importers_complete for i in self.analysis.impacts))


class CutSearchesAndCounts(Base):
    FORMAT = "export function fmt(x: string) {\n  return x;\n}\n"

    def test_a_cut_search_that_still_found_importers_is_marked_incomplete(self):
        """Review F7: the cut search has hits, the list is partial and says so."""
        files = {"src/lib/format.ts": self.FORMAT, **{f"src/a{i:02d}.ts": 'import { fmt } from "./lib/format";\nfmt("a");\n' for i in range(10)}}
        self.repo({"files": files})
        res = self.adapter(examine_cap=4).importers("src/lib/format.ts", self.sha("main"))
        self.assertGreater(len(res.edges), 0)
        self.assertLess(len(res.edges), 10)
        self.assertFalse(res.complete)
        self.assertTrue(any("search_truncated" in r for r in res.reasons), res.reasons)
        full = self.adapter().importers("src/lib/format.ts", self.sha("main"))
        self.assertEqual((len(full.edges), full.complete), (10, True))

    def test_a_cut_search_makes_the_file_impact_incomplete_too(self):
        files = {"src/lib/format.ts": self.FORMAT, **{f"src/a{i:02d}.ts": 'import { fmt } from "./lib/format";\nfmt("a");\n' for i in range(10)}}
        self.repo({"files": files}, {"files": {"src/lib/format.ts": self.FORMAT.replace("return x;", "return x + 1;")}})
        self.analyze(adapter=self.adapter(examine_cap=4))
        impact = self.impact("src/lib/format.ts")
        self.assertEqual(impact.importers_complete, False)
        self.assertTrue(impact.importer_reasons)

    def test_a_partial_definition_count_over_the_limit_is_not_distinctive(self):
        """Review F9: a count that is only a lower bound but already over the limit is recorded."""
        files = {f"src/d{i}.ts": "export function fmt() { return 1; }\n" for i in range(6)}
        self.repo({"files": files}, {"files": {"src/d0.ts": "export function fmt() { return 2; }\n"}})
        self.analyze(adapter=self.adapter(distinct_max=1, examine_cap=3))
        impact = self.impact("src/d0.ts")
        self.assertEqual((impact.not_distinctive, impact.names_unreadable), (("fmt",), ()))
        (rec,) = [r for r in self.records if r.kind == "not_distinctive"]
        self.assertIn("at least", rec.detail)

    def test_a_partial_definition_count_under_the_limit_is_not_established(self):
        files = {"src/d0.ts": "export function fmt() { return 1; }\n", **{f"src/z{i}.ts": "// fmt\n" for i in range(6)}}
        self.repo({"files": files}, {"files": {"src/d0.ts": "export function fmt() { return 2; }\n"}})
        self.analyze(adapter=self.adapter(distinct_max=3, examine_cap=2))
        impact = self.impact("src/d0.ts")
        self.assertEqual((impact.not_distinctive, impact.names_unreadable), ((), ("fmt",)))  # never read as distinctive

    def test_a_complete_count_under_the_limit_is_in_neither_list(self):
        self.repo({"files": {"src/d0.ts": "export function fmt() { return 1; }\n"}}, {"files": {"src/d0.ts": "export function fmt() { return 2; }\n"}})
        self.analyze(adapter=self.adapter(distinct_max=3))
        impact = self.impact("src/d0.ts")
        self.assertEqual((impact.not_distinctive, impact.names_unreadable), ((), ()))


class HubTests(Base):
    def test_tests_that_import_the_hub_are_still_listed_when_the_walk_stops(self):
        files = {"src/lib/format.ts": "export function truncate(t: string) {\n  return t;\n}\n", "src/lib/format.test.ts": 'import { truncate } from "./format";\ntruncate("a");\n'}
        files.update({f"src/w{i:02d}.ts": 'import { truncate } from "@/lib/format";\ntruncate("x");\n' for i in range(7)})
        self.repo({"files": files}, {"files": {"src/lib/format.ts": "export function truncate(t: string) {\n  return t + '';\n}\n"}})
        self.analyze(adapter=self.adapter(hub_importers=5))
        impact = self.impact("src/lib/format.ts")
        self.assertTrue(impact.hub)
        self.assertEqual((impact.callers, [(t.path, t.via) for t in impact.unit_tests]), ((), [("src/lib/format.test.ts", "src/lib/format.ts")]))
        self.assertEqual(impact.importer_count, 7)  # tests do not count towards the hub


class MoreReach(Base):
    def test_a_barrel_that_re_exports_what_reaches_the_change_is_kept(self):
        files = {"src/lib/index.ts": 'export { h as hh } from "./m";\n', "src/lib/other.ts": 'export { unrelated } from "./m";\n'}
        self.repo({"files": {"src/lib/m.ts": R_BEFORE, **files}}, {"files": {"src/lib/m.ts": R_AFTER}})
        self.analyze()
        impact = self.impact("src/lib/m.ts")
        self.assertEqual([c.path for c in impact.callers], ["src/lib/index.ts"])      # h uses f
        self.assertEqual([x.subject_path for x in impact.exclusions], ["src/lib/other.ts"])

    def test_loose_equality_of_two_string_literals_is_string_equality(self):
        """`"01" == "1"` is false in JavaScript: a string literal argument is compared as a string."""
        source = 'export function fmt(x: number, mode: any) {\n  if (mode == "1") {\n    return 1;\n  }\n  return 0;\n}\n'
        self.repo({"files": {"src/g.ts": source, "src/c.ts": 'import { fmt } from "./g";\nexport const r = fmt(1, "01");\nexport const s = fmt(1, "1");\n'}}, {"files": {"src/g.ts": source.replace("return 1;", "return 2;")}})
        self.analyze()
        impact = self.impact("src/g.ts")
        (caller,) = impact.callers  # the call passing "1" enters the block, so the file stays a caller
        self.assertEqual((caller.reason, [c.line for c in caller.call_sites]), ("literal_enters_guard", [3]))
