"""The done-when of M11: a rerun on a new commit marks exactly the right decisions current, stale or orphaned (fixture pair).

Scenario s01 (a change to the shared StatusBadge reaches four screens) is revision A. Revision B is a second commit on the same scenario
branch (`tests/fixtures/review/s01-shared-component/`, applied by `build.apply_revision` to a copy of the built repositories) that:

1. changes an unrelated line (an import of something else) in a file of a chain: /billing/invoices/:invoiceId keeps its fingerprint: current;
2. respells the import of the badge (the alias `@components/StatusBadge` becomes a relative path to the same file) and inserts blank lines at the top
   of that page, so every line of it moves down: /cases/:caseId says the same thing (the claim of an import is its resolved target and the names it
   takes, not its spelling): current;
3. makes the list page reach the badge through a new re-export file (`src/components/index.ts`): the import now resolves to another file, so
   the claim changed and /billing/invoices is stale;
4. removes the route of one screen: /cases is no longer reached: orphaned;
5. makes a settlements page use the badge: /billing/settlements is new: no decision, undecided.

Every area and every finding of A gets a decision, then the same change is analysed again on B in the same reports directory."""

import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from reachmap import cli
from reachmap.review import revisions, store
from tests import scenario_support as ss
from tests import support_git as sg
from tests.fixtures import build

SCENARIO = "s01-shared-component"
ARGS = ("--repo", "acme/web", "--compare", "main...scenario/s01-shared-component")
KEY = store.make_key("acme", "acme/web", "compare:main...scenario/s01-shared-component")

CURRENT = {"/billing/invoices/:invoiceId", "/cases/:caseId"}
STALE = {"/billing/invoices"}
ORPHANED = {"/cases"}
UNDECIDED = {"/billing/settlements"}


class Pair:
    """Revision A analysed, decided on, then B analysed: all in a folder of its own, with its own repositories, cache and reports."""

    _SHARED: "Pair | None" = None

    @classmethod
    def shared(cls) -> "Pair":
        if cls._SHARED is None:
            cls._SHARED = cls()
        return cls._SHARED

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="reachmap-review-"))
        import atexit

        atexit.register(sg.remove_tree, self.root)
        self.dest = self.root / "fx"
        shutil.copytree(ss.env().dest, self.dest, symlinks=True)
        self.config = self.root / "reachmap.toml"
        self.config.write_text((ss.FIXTURES / "reachmap.toml").read_text(encoding="utf-8"), encoding="utf-8")
        self.env = {"REACHMAP_FIXTURES": "file://" + str(self.dest), "REACHMAP_CACHE": str(self.root / "cache")}
        self.home = self.root / "home"
        self.out = self.root / "reports"
        self.clock = [1_767_323_045.0]
        self.a = self.analyse(self.out)
        self.a_file = self.last_report(self.out)[1]
        self.decide_everything()
        self.b_sha = build.apply_revision(self.dest, SCENARIO, "r2")
        self.b = self.analyse(self.out)
        self.b_file = self.last_report(self.out)[1]
        self.b_stdout = self.last_stdout

    def analyse(self, out: Path, *extra: str) -> dict:
        stdout, stderr = io.StringIO(), io.StringIO()
        self.clock[0] += 60
        code = cli.main(["analyse", "--config", str(self.config), *ARGS, "--out", str(out), *extra], env=self.env, stdout=stdout, stderr=stderr,
                        git_env=sg.isolated_env(self.home), now=lambda: self.clock[0])
        assert code == 0, stderr.getvalue()
        self.last_stdout = stdout.getvalue()
        return self.last_report(out)[0]

    @staticmethod
    def last_report(out: Path):
        paths = sorted(out.glob("*.json"), key=lambda p: p.stat().st_mtime_ns)
        return json.loads(paths[-1].read_text(encoding="utf-8")), paths[-1]

    def decide(self, *args: str) -> None:
        stdout, stderr = io.StringIO(), io.StringIO()
        self.clock[0] += 1
        code = cli.main(["decide", str(self.a_file), *args], stdout=stdout, stderr=stderr, now=lambda: self.clock[0])
        assert code == 0, stderr.getvalue()

    def decide_everything(self) -> None:
        verdicts = {"/billing/invoices": ("skip", "retired next release"), "/billing/invoices/:invoiceId": ("test", ""), "/cases": ("sample", ""), "/cases/:caseId": ("skip", "same page as the list")}
        for target in self.a["areas"]:
            verdict, reason = verdicts[target["label"]]
            self.decide("--target", target["id"], "--verdict", verdict, *(("--reason", reason) if reason else ()), "--author", "Quinn")
        for n, target in enumerate(self.a["findings"]):
            verdict, reason = ("reject", "the page is behind a flag") if n % 2 else ("accept", "")
            self.decide("--target", target["id"], "--verdict", verdict, *(("--reason", reason) if reason else ()), "--author", "Quinn")


def labels_of(report: dict, ids) -> set:
    """The area labels (or the screen of a finding) of target ids in a report."""
    by_area = {a["id"]: a["label"] for a in report["areas"]}
    by_finding = {f["id"]: f["identity"][3] for f in report["findings"]}
    return {by_area.get(i) or by_finding.get(i) for i in ids}


def labels_in_a(pair: Pair, ids) -> set:
    return labels_of(pair.a, ids)


class TheFixturePair(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pair = Pair.shared()

    def test_a_has_the_four_screens_and_findings_and_b_has_five_the_one_that_left_gone_and_one_new(self):
        a, b = self.pair.a, self.pair.b
        self.assertEqual({x["label"] for x in a["areas"]}, CURRENT | STALE | ORPHANED)
        self.assertEqual({x["label"] for x in b["areas"]}, CURRENT | STALE | UNDECIDED)
        self.assertEqual(len(a["findings"]), 4)
        self.assertEqual(len(b["findings"]), 4)
        self.assertNotEqual(a["ref"], b["ref"])
        self.assertEqual(b["ref"], self.pair.b_sha)

    def test_b_is_revision_2_of_the_same_change_and_names_a(self):
        b = self.pair.b
        self.assertEqual((self.pair.a["review"]["revision"], b["review"]["revision"]), (1, 2))
        self.assertEqual(b["review"]["previous"], {"number": 1, "file": self.pair.a_file.name, "head_sha": self.pair.a["ref"]})
        self.assertEqual(self.pair.a_file.name.rsplit("-", 2)[0], self.pair.b_file.name.rsplit("-", 2)[0])  # the same change, another commit
        self.assertEqual((self.pair.a_file.name[-8:], self.pair.b_file.name[-8:]), ("-r1.json", "-r2.json"))
        self.assertNotEqual(self.pair.a_file, self.pair.b_file)
        self.assertEqual([r.number for r in revisions.read(self.pair.out, KEY)], [1, 2])

    def test_exactly_the_right_areas_are_current_stale_and_orphaned_and_the_new_one_is_undecided(self):
        d = self.pair.b["review"]["decisions"]
        areas = lambda items: {x["target_id"] for x in items if x["kind"] == "area"}  # noqa: E731
        self.assertEqual(labels_of(self.pair.b, areas(d["current"])), CURRENT)
        self.assertEqual(labels_of(self.pair.b, areas(d["stale"])), STALE)
        self.assertEqual({x["label"] for x in d["orphaned"] if x["kind"] == "area"}, ORPHANED)
        self.assertEqual(labels_of(self.pair.b, d["undecided"]["areas"]), UNDECIDED)

    def test_exactly_the_right_findings_are_current_stale_and_orphaned_and_the_new_one_is_undecided(self):
        d = self.pair.b["review"]["decisions"]
        findings = lambda items: {x["target_id"] for x in items if x["kind"] == "finding"}  # noqa: E731
        self.assertEqual(labels_of(self.pair.b, findings(d["current"])), CURRENT)
        self.assertEqual(labels_of(self.pair.b, findings(d["stale"])), STALE)
        self.assertEqual(len([x for x in d["orphaned"] if x["kind"] == "finding"]), 1)
        orphan = next(x for x in d["orphaned"] if x["kind"] == "finding")
        self.assertEqual(labels_of(self.pair.a, [orphan["target_id"]]), ORPHANED)  # the finding about /cases, found in A
        self.assertEqual(labels_of(self.pair.b, d["undecided"]["findings"]), UNDECIDED)

    def test_the_sets_are_exact_nothing_is_in_two_lists_and_every_decision_is_in_one(self):
        d = self.pair.b["review"]["decisions"]
        ids = [x["target_id"] for k in ("current", "stale", "orphaned") for x in d[k]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), len(self.pair.a["areas"]) + len(self.pair.a["findings"]))  # all 8 decisions of A are somewhere
        self.assertEqual((len(d["current"]), len(d["stale"]), len(d["orphaned"])), (4, 2, 2))
        self.assertEqual((len(d["undecided"]["areas"]), len(d["undecided"]["findings"])), (1, 1))
        self.assertEqual(d["records"], [])

    def test_a_decision_in_each_list_says_what_it_decided_and_which_revision_it_was_made_on(self):
        d = self.pair.b["review"]["decisions"]
        by_label = {x["label"]: x for x in d["current"] if x["kind"] == "area"}
        self.assertEqual((by_label["/cases/:caseId"]["verdict"], by_label["/cases/:caseId"]["reason"]), ("skip", "same page as the list"))
        self.assertEqual(by_label["/billing/invoices/:invoiceId"]["verdict"], "test")
        for entry in d["current"] + d["stale"] + d["orphaned"]:
            self.assertEqual((entry["revision"], entry["author"], entry["channel"]), (1, "Quinn", "cli"))

    def test_the_unrelated_edit_the_respelled_import_and_the_lines_that_moved_down_leave_the_fingerprint_alone_the_new_hop_does_not(self):
        a, b = self.pair.a, self.pair.b
        fp = lambda r: {x["label"]: x["fingerprint"] for x in r["areas"]}  # noqa: E731
        self.assertEqual(fp(a)["/billing/invoices/:invoiceId"], fp(b)["/billing/invoices/:invoiceId"])
        self.assertEqual(fp(a)["/cases/:caseId"], fp(b)["/cases/:caseId"])
        self.assertNotEqual(fp(a)["/billing/invoices"], fp(b)["/billing/invoices"])
        first = lambda r, label: next(x for x in r["areas"] if x["label"] == label)["chains"][0][0]  # noqa: E731
        self.assertEqual((first(a, "/cases/:caseId")["line"], first(b, "/cases/:caseId")["line"]), (4, 7))  # three blank lines above it
        self.assertEqual(first(a, "/cases/:caseId")["detail"], 'import { StatusBadge } from "@components/StatusBadge";')
        self.assertEqual(first(b, "/cases/:caseId")["detail"], 'import { StatusBadge } from "../../../components/StatusBadge";')  # the text is not the same line...
        self.assertEqual(first(a, "/cases/:caseId")["claim"], first(b, "/cases/:caseId")["claim"])  # ...and the claim is
        files = {f["path"] for f in b["change"]["files"]}
        self.assertTrue({"src/modules/billing/pages/InvoiceDetailPage.tsx", "src/modules/cases/pages/CaseDetailPage.tsx", "src/components/index.ts"} <= files)

    def test_the_findings_follow_their_areas(self):
        fp = lambda r: {f["identity"][3]: f["fingerprint"] for f in r["findings"] if f["identity"][2] == "screen"}  # noqa: E731
        a, b = fp(self.pair.a), fp(self.pair.b)
        for label in CURRENT:
            self.assertEqual(a[label], b[label], label)
        for label in STALE:
            self.assertNotEqual(a[label], b[label], label)

    def test_a_stale_decision_shows_what_changed_from_the_earlier_report_that_is_still_beside_the_new_one(self):
        (area,) = [x for x in self.pair.b["review"]["decisions"]["stale"] if x["kind"] == "area"]
        self.assertEqual(area["label"], "/billing/invoices")
        page = "src/modules/billing/pages/InvoiceListPage.tsx"
        self.assertEqual(area["changes"]["removed"], [{"repo": "acme/web", "path": page, "detail": 'import { StatusBadge } from "@/components/StatusBadge";'}])
        self.assertEqual(area["changes"]["added"], [{"repo": "acme/web", "path": page, "detail": 'import { StatusBadge } from "@/components";'}])
        self.assertNotEqual(area["fingerprint"], area["now_fingerprint"])

    def test_the_orphans_are_named_from_the_earlier_report(self):
        self.assertEqual({x["label"] for x in self.pair.b["review"]["decisions"]["orphaned"] if x["kind"] == "area"}, {"/cases"})

    def test_decisions_changed_nothing_in_the_analysis_of_b(self):
        plain_out = self.pair.root / "plain"
        # the same repositories and cache, a reports directory with no decision in it
        plain = self.pair.analyse(plain_out)
        b = self.pair.b
        for key in ("areas", "findings", "counts", "propagations", "branches", "incompleteness", "notes", "ref", "change"):
            self.assertEqual(plain[key], b[key], key)
        self.assertEqual(plain["review"]["decisions"]["current"] + plain["review"]["decisions"]["stale"] + plain["review"]["decisions"]["orphaned"], [])
        self.assertTrue(all(a["priority"] == "must_test" for a in b["areas"]))

    def test_the_rendered_report_says_it_in_words(self):
        from reachmap import report

        text = report.render(self.pair.b, "md")
        self.assertIn("## Decisions", text)
        self.assertIn("decision needs review", text)
        self.assertIn("No decision recorded.", text)
        self.assertIn("Decisions about something no longer in this report", text)
        self.assertIn('risk accepted, not tested (reason from Quinn: "retired next release")', text)
        self.assertEqual(text.count("**Decision:**"), 4 + 4 + 8)  # four areas and four findings, and eight decisions in the section

    def test_the_summary_counts_them(self):
        self.assertIn("Decisions: 4 current, 2 need review, 2 orphaned", self.pair.b_stdout)
        self.assertIn("Revision 2 of this change; it revises revision 1", self.pair.b_stdout)


class ImportAfterB(unittest.TestCase):
    """What the HTML page exported from A, imported when B is the analysis at hand, is judged against the fingerprints of A."""

    def test_a_decision_exported_from_a_is_judged_against_a_s_fingerprint_not_b_s(self):
        pair = Pair.shared()
        a = pair.a
        doc = {
            "format": "reachmap-decisions", "version": 1,
            "report": {"key": {k: a["review"]["key"][k] for k in ("workspace", "repo", "change")}, "head_sha": a["ref"], "revision": 1},
            "exported_at": "2026-02-03T04:05:06.789Z",
            "decisions": [
                {"target_id": t["id"], "verdict": "test", "reason": "", "author": "Sam", "fingerprint": t["fingerprint"], "created_at": "2026-02-03T04:00:00.000Z"}
                for t in a["areas"]
            ],
        }
        path = pair.root / "exported.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        fresh = pair.root / "fresh-reports"  # the reports and the log of the pair, but no decision: B is analysed again, as revision 3
        shutil.copytree(pair.out, fresh, ignore=shutil.ignore_patterns("decisions"))
        b = pair.analyse(fresh, "--decisions", str(path))
        self.assertEqual(b["review"]["revision"], 3)
        d = b["review"]["decisions"]
        self.assertEqual(labels_of(b, [x["target_id"] for x in d["current"]]), CURRENT)
        self.assertEqual(labels_of(b, [x["target_id"] for x in d["stale"]]), STALE)
        self.assertEqual({x["target_id"] for x in d["orphaned"]}, {x["id"] for x in a["areas"] if x["label"] in ORPHANED})
        self.assertEqual({x["channel"] for x in d["current"] + d["stale"] + d["orphaned"]}, {"html-import"})
        stale = d["stale"][0]
        a_fp = next(x["fingerprint"] for x in a["areas"] if x["label"] == "/billing/invoices")
        self.assertEqual(stale["fingerprint"], a_fp)  # the exported one is kept
        self.assertNotEqual(stale["now_fingerprint"], a_fp)
        self.assertIsNotNone(stale["changes"])  # revision 1's report is beside this one, and it is the one the person saw

    def test_the_same_exported_decisions_are_current_when_the_analysis_at_hand_is_a_itself(self):
        pair = Pair.shared()
        a = pair.a
        t = a["areas"][0]
        doc = {
            "format": "reachmap-decisions", "version": 1,
            "report": {"key": {k: a["review"]["key"][k] for k in ("workspace", "repo", "change")}, "head_sha": a["ref"], "revision": 1},
            "exported_at": "2026-02-03T04:05:06.789Z",
            "decisions": [{"target_id": t["id"], "verdict": "test", "reason": "", "author": "Sam", "fingerprint": t["fingerprint"], "created_at": "2026-02-03T04:00:00.000Z"}],
        }
        path = pair.root / "exported2.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        out = io.StringIO()
        base = pair.root / "decide-reports"
        base.mkdir(exist_ok=True)
        copy = base / pair.a_file.name
        shutil.copy(pair.a_file, copy)
        revisions.record(base, KEY, revisions.Rev(1, copy.name, a["ref"], "2026-01-02T03:04:05Z"))
        self.assertEqual(cli.main(["decide", "--import", str(path), str(copy)], stdout=out, stderr=io.StringIO()), 0)
        rendered = io.StringIO()
        cli.main(["report", str(copy)], stdout=rendered, stderr=io.StringIO())
        self.assertIn("marked to test", rendered.getvalue())
        self.assertNotIn("decision needs review", rendered.getvalue())


class Builder(unittest.TestCase):
    def test_the_revision_is_one_more_commit_on_the_scenario_branch_and_nothing_else_moves(self):
        shared = ss.env().dest
        pair = Pair.shared()

        def refs(dest):
            proc = build.run_git(Path(dest) / "acme-web", "for-each-ref", "--format=%(refname) %(objectname)")
            return dict(line.split(" ") for line in proc.stdout.splitlines())

        before, after = refs(shared), refs(pair.dest)
        moved = {r for r in before if before[r] != after[r]}
        self.assertEqual(moved, {"refs/heads/scenario/s01-shared-component"})
        self.assertEqual(set(before), set(after))
        old = before["refs/heads/scenario/s01-shared-component"]
        proc = build.run_git(Path(pair.dest) / "acme-web", "rev-list", "--parents", "-n", "1", after["refs/heads/scenario/s01-shared-component"])
        self.assertEqual(proc.stdout.split()[1:], [old])  # the new head's parent is the old head

    def test_the_other_repositories_are_untouched(self):
        shared, pair = ss.env().dest, Pair.shared()
        for slug in ("acme-api", "acme-contracts", "acme-worker"):
            a = build.run_git(Path(shared) / slug, "for-each-ref", "--format=%(refname) %(objectname)").stdout
            b = build.run_git(Path(pair.dest) / slug, "for-each-ref", "--format=%(refname) %(objectname)").stdout
            self.assertEqual(a, b, slug)

    def test_applying_the_same_revision_twice_to_equal_copies_gives_the_same_sha(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "fx"
            shutil.copytree(ss.env().dest, copy, symlinks=True)
            self.assertEqual(build.apply_revision(copy, SCENARIO, "r2"), Pair.shared().b_sha)

    def test_a_scenario_without_revisions_has_none_and_an_unknown_name_is_an_error(self):
        self.assertEqual(build.revision_steps("s02-hub-cutoff"), [])
        self.assertEqual([s["name"] for s in build.revision_steps(SCENARIO)], ["r2"])
        with self.assertRaises(KeyError):
            build.apply_revision(Pair.shared().dest, SCENARIO, "r9")

    def test_build_all_does_not_apply_a_revision_so_every_existing_sha_is_what_it_was(self):
        e = ss.env()
        head = sg.rev(e.repos["acme/web"], "scenario/s01-shared-component")
        self.assertNotEqual(head, Pair.shared().b_sha)
        self.assertEqual(head, Pair.shared().a["ref"])


if __name__ == "__main__":
    unittest.main()
