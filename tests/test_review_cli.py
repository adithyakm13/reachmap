"""`reachmap analyse` and `reachmap decide` with the review layer (M11): the revision of every run, the decisions carried into the report,
`reachmap decide` (one decision, a list, an import), `--decisions`, and what a decision never does to the analysis (rule 4.4).

The fixtures are the M1 repositories through the cache the scenario modules fill. Each test that writes a store or a log uses a reports
directory of its own."""

import io
import json
import os
import re
import shutil
import unittest

from reachmap import cli, identity, wording
from reachmap.model import Analysis, Area, Finding, coverage_gaps
from reachmap.review import carry, formats, revisions, store
from tests import scenario_support as ss
from tests import support_cli
from tests import support_git as sg
from tests.test_cli import CliCase

S01 = ("--repo", "acme/web", "--compare", "main...scenario/s01-shared-component")
S13 = ("--repo", "acme/api", "--compare", "main...scenario/s13-branch-matrix")
NOW = lambda: 1_767_323_045.0  # noqa: E731  2026-01-02T03:04:05Z
THEN = "2026-01-02T03:04:05Z"
KEY = store.make_key("acme", "acme/web", "compare:main...scenario/s01-shared-component")


def _reason_ok(text):
    try:
        formats.check_reason(text)
        return True
    except formats.TextRefused:
        return False


def decide(*args, stdout=None, stderr=None, now=NOW, env=None):
    out, err = stdout or io.StringIO(), stderr or io.StringIO()
    code = cli.main(["decide", *args], env=env if env is not None else {}, stdout=out, stderr=err, now=now)
    return code, out.getvalue(), err.getvalue()


class ReviewCase(CliCase):
    """A reports directory of its own, `go()` for analyse, and a copy of a real report to decide on."""

    def run_analyse(self, *args, **kw):
        code, out, err = self.go(*args, **kw)
        return code, out, err

    def latest_report(self):
        paths = sorted(self.out.glob("*.json"), key=lambda p: p.stat().st_mtime_ns)
        return json.loads(paths[-1].read_text(encoding="utf-8")), paths[-1]

    def copy_report(self, *args):
        """A real report (made once per process) in a reports directory of this test: where `decide` puts its store."""
        src = support_cli.cli_run(*args).path
        dest = self.tmp / "reports"
        dest.mkdir(exist_ok=True)
        shutil.copy(src, dest / src.name)
        return dest / src.name


class ReportKeys(ReviewCase):
    def test_a_completed_report_carries_one_review_object_and_the_minor_version(self):
        r = support_cli.cli_run(*S01).report
        self.assertEqual((r["schema"], r["schema_minor"]), (1, 2))
        for old in ("review_key", "previous_revision", "targets", "decisions"):
            self.assertNotIn(old, r)  # one object, not four keys beside the analysis
        review = r["review"]
        self.assertEqual(list(review), ["key", "revision", "previous", "decisions", "as_of"])
        self.assertEqual(review["key"], {"workspace": "acme", "repo": "acme/web", "change": "compare:main...scenario/s01-shared-component", "slug": store.key_slug(KEY)})
        self.assertEqual(review["revision"], 1)
        self.assertIsNone(review["previous"])
        self.assertRegex(review["as_of"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        d = review["decisions"]
        self.assertEqual(set(d), {"current", "stale", "orphaned", "undecided", "records", "as_of"})
        self.assertEqual(d["as_of"], review["as_of"])  # the time the store was read
        self.assertEqual(len(d["undecided"]["areas"]), len(r["areas"]))
        self.assertEqual(len(d["undecided"]["findings"]), len(r["findings"]))

    def test_the_top_level_revision_is_the_models_own_and_the_review_number_lives_in_the_review_object(self):
        self.go(*S01)
        self.go(*S01)
        data, _ = self.latest_report()
        self.assertEqual(data["review"]["revision"], 2)
        self.assertEqual(data["revision"], 1)  # `Analysis.revision`: what the model carries, not the place of the run in the log
        self.assertEqual(Analysis.from_dict({k: data[k] for k in support_cli.ANALYSIS_KEYS}).revision, 1)

    def test_every_area_and_finding_carries_its_id_and_the_models_fingerprint_and_a_changed_one_is_refused_on_reading(self):
        r = support_cli.cli_run(*S01).report
        a = Analysis.from_dict({k: r[k] for k in support_cli.ANALYSIS_KEYS})
        self.assertEqual([t["fingerprint"] for t in r["areas"]], [identity.fingerprint(x) for x in a.areas])
        self.assertEqual([t["id"] for t in r["findings"]], [x.id for x in a.findings])
        r["areas"][0]["fingerprint"] = "0" * 64
        with self.assertRaises(Exception):
            Analysis.from_dict({k: r[k] for k in support_cli.ANALYSIS_KEYS})

    def test_a_pull_request_is_keyed_by_its_host_and_number(self):
        self.assertEqual(store.make_key("w", "r", store.change_identity({"pr": "12"}, "github"))["change"], "pr:github:12")

    def test_a_run_that_did_not_complete_has_the_key_and_a_null_revision_and_no_decisions_and_no_entry_in_the_log(self):
        code, _, _ = self.go("--repo", "acme/api", "--compare", "main...nope")
        self.assertEqual(code, 3)
        (path,) = self.reports()
        data = json.loads(path.read_text(encoding="utf-8"))
        review = data["review"]
        self.assertEqual((review["revision"], review["previous"], review["decisions"], review["as_of"]), (None, None, None, None))
        self.assertEqual(review["key"]["repo"], "acme/api")
        self.assertFalse((self.out / ".reachmap" / "revisions").exists())
        self.assertRegex(path.name, r"-failed-[0-9a-f]{8}\.json$")


class Revisions(ReviewCase):
    def test_three_runs_on_one_key_are_revisions_1_2_3_each_in_a_file_of_its_own_naming_the_one_before(self):
        seen = []
        for _ in range(3):
            self.assertEqual(self.go(*S01)[0], 0)
            seen.append(self.latest_report())
        reports = [r for r, _ in seen]
        self.assertEqual([r["review"]["revision"] for r in reports], [1, 2, 3])
        names = [p.name for _, p in seen]
        self.assertEqual(len(set(names)), 3)  # the same commit three times: three files
        self.assertEqual([n.rsplit("-", 1)[1] for n in names], ["r1.json", "r2.json", "r3.json"])
        self.assertIsNone(reports[0]["review"]["previous"])
        self.assertEqual(reports[1]["review"]["previous"], {"number": 1, "file": names[0], "head_sha": reports[0]["ref"]})
        self.assertEqual(reports[2]["review"]["previous"], {"number": 2, "file": names[1], "head_sha": reports[1]["ref"]})
        log = revisions.read(self.out, KEY)
        self.assertEqual([(r.number, r.file) for r in log], [(1, names[0]), (2, names[1]), (3, names[2])])
        self.assertEqual(sorted(p.name for p in self.reports()), sorted(names))  # nothing replaced a report the log names

    def test_a_different_change_starts_at_1_and_does_not_disturb_the_first(self):
        self.go(*S01)
        self.go(*S01)
        self.go(*S13)
        revs = sorted(((r["inputs"]["repo"], r["review"]["revision"]) for r in (json.loads(p.read_text()) for p in self.reports())))
        self.assertEqual(revs, [("acme/api", 1), ("acme/web", 1), ("acme/web", 2)])
        self.go("--repo", "acme/web", "--compare", "main...scenario/s01-shared-component")
        self.assertEqual(revisions.next_number(revisions.read(self.out, KEY)), 4)

    def test_the_number_is_stored_not_read_from_the_files_that_happen_to_be_there(self):
        self.go(*S01)
        for p in self.reports():
            p.unlink()
        self.go(*S01)
        self.assertEqual(self.latest_report()[0]["review"]["revision"], 2)

    def test_a_corrupt_revision_log_stops_the_run_with_a_message_and_writes_no_report(self):
        path = revisions.revisions_path(self.out, KEY)
        path.parent.mkdir(parents=True)
        path.write_text("garbage\n")
        code, out, err = self.go(*S01)
        self.assertEqual(code, 2)
        self.assertIn("revision", err)
        self.assertEqual(self.reports(), [])

    def test_a_reachmap_that_is_a_file_stops_the_run_with_a_message_before_the_analysis(self):
        self.out.mkdir(parents=True)
        (self.out / ".reachmap").write_text("a file")
        code, out, err = self.go(*S01)
        self.assertEqual((code, out), (2, ""))
        self.assertIn(".reachmap", err)
        self.assertEqual(self.reports(), [])

    def test_the_summary_says_the_revision_only_from_the_second_run_on(self):
        _, first, _ = self.go(*S01)
        _, second, _ = self.go(*S01)
        self.assertNotIn("Revision", first)
        self.assertRegex(second, r"Revision 2 of this change; it revises revision 1 \(")
        self.assertEqual(wording.check(second), [])


class FailedRuns(ReviewCase):
    """A run that does not complete is a report of its own: it never replaces a completed one and it says no revision."""

    def fail(self, at=None):
        from unittest import mock

        from reachmap import pipeline

        with mock.patch.object(pipeline, "build", side_effect=RuntimeError("boom")):
            return self.go(*S01, **({"now": lambda: at} if at else {}))

    def test_a_failed_rerun_on_the_same_commit_leaves_every_completed_report_and_the_log_as_they_were(self):
        self.go(*S01)
        self.go(*S01)
        before = {p.name: p.read_bytes() for p in self.reports()}
        log_before = revisions.revisions_path(self.out, KEY).read_bytes()
        code, _, err = self.fail()
        self.assertEqual(code, 3)
        after = {p.name: p.read_bytes() for p in self.reports()}
        self.assertEqual({n: b for n, b in after.items() if n in before}, before)
        (failed,) = [n for n in after if n not in before]
        self.assertRegex(failed, r"-failed-[0-9a-f]{8}\.json$")
        self.assertEqual(revisions.revisions_path(self.out, KEY).read_bytes(), log_before)
        previous = revisions.read(self.out, KEY)[-1]
        self.assertIn(previous.file, before)  # the file the log points at is still the completed report

    def test_a_failed_run_after_earlier_revisions_says_no_revision_not_revision_1(self):
        self.go(*S01)
        self.go(*S01)
        self.fail()
        failed = json.loads(next(p for p in self.reports() if "-failed-" in p.name).read_text(encoding="utf-8"))
        self.assertEqual(failed["status"], "failed")
        self.assertIsNone(failed["review"]["revision"])
        self.assertIsNone(failed["review"]["previous"])
        self.assertIsNone(failed["review"]["decisions"])
        self.assertEqual(failed["review"]["key"]["repo"], "acme/web")
        self.go(*S01)  # and the next completed run is revision 3: the failed one took no number
        self.assertEqual(max(json.loads(p.read_text())["review"]["revision"] or 0 for p in self.reports()), 3)

    def test_two_failed_runs_at_different_times_are_two_files_and_neither_replaces_a_completed_report(self):
        self.go(*S01)
        self.fail(1_767_323_045.0)
        self.fail(1_767_323_046.0)
        names = sorted(p.name for p in self.reports())
        self.assertEqual(len([n for n in names if "-failed-" in n]), 2)
        self.assertEqual(len([n for n in names if n.endswith("-r1.json")]), 1)

    def test_a_timed_out_run_with_a_resolved_head_on_a_key_with_revisions_is_not_logged(self):
        """The run resolves its change (so its head is a real commit) and misses its deadline: no revision, no log line, no number taken."""
        self.go(*S01)
        log_before = revisions.revisions_path(self.out, KEY).read_bytes()
        state = {"t": 0.0}

        def clock():
            state["t"] += 400
            return state["t"]

        code, _, _ = self.go(*S01, clock=clock)
        self.assertEqual(code, 4)
        data = json.loads(next(p for p in self.reports() if "-failed-" in p.name).read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "timed_out")
        self.assertTrue(data["change"]["resolved"])
        self.assertIsNone(data["review"]["revision"])
        self.assertEqual(revisions.revisions_path(self.out, KEY).read_bytes(), log_before)
        self.go(*S01)
        self.assertEqual(revisions.read(self.out, KEY)[-1].number, 2)


@unittest.skipIf(os.name == "nt", "folder permissions work differently on Windows")
class UnsearchableFolder(ReviewCase):
    """A decisions folder that cannot be searched is an unreadable store, never an absent one: no traceback."""

    def setUp(self):
        super().setUp()
        self.assertEqual(self.go(*S01)[0], 0)
        self.data, self.path = self.latest_report()
        decide(str(self.path), "--target", self.data["areas"][0]["id"], "--verdict", "skip", "--reason", "old", "--author", "Q")
        self.assertEqual(self.go(*S01)[0], 0)
        self.data, self.path = self.latest_report()
        self.folder = self.out / ".reachmap" / "decisions"
        os.chmod(self.folder, 0o600)
        self.addCleanup(os.chmod, self.folder, 0o755)
        if os.access(self.folder / "x", os.F_OK) or os.geteuid() == 0:
            self.skipTest("this account can search the folder anyway (root)")

    def test_report_says_the_decisions_could_not_be_read_instead_of_failing(self):
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(["report", str(self.path)], stdout=out, stderr=err)
        self.assertEqual((code, err.getvalue()), (0, ""))
        self.assertIn("decision_store_unreadable", out.getvalue())
        self.assertNotIn("risk accepted", out.getvalue())  # not the decision the JSON held: the store cannot be read now
        self.assertNotIn("Traceback", out.getvalue())

    def test_decide_list_says_so_and_decide_one_is_refused_with_a_message(self):
        code, out, err = decide(str(self.path), "--list")
        self.assertEqual(code, 0)
        self.assertIn("decisions could not be read", out)
        code, out, err = decide(str(self.path), "--target", self.data["areas"][0]["id"], "--verdict", "test", "--author", "Q")
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", err)
        self.assertNotIn(str(self.out), err)


class DecideCommand(ReviewCase):
    def setUp(self):
        super().setUp()
        self.path = self.copy_report(*S01)
        self.report = json.loads(self.path.read_text(encoding="utf-8"))
        self.area = self.report["areas"][0]
        self.finding = self.report["findings"][0]
        self.base = self.path.parent

    def store(self):
        return store.read(self.base, KEY)

    def test_a_skip_is_recorded_with_the_fingerprint_and_revision_of_the_report_and_prints_what_it_means(self):
        code, out, err = decide(str(self.path), "--target", self.area["id"], "--verdict", "skip", "--reason", "retired in this release", "--author", "Quinn")
        self.assertEqual((code, err), (0, ""))
        self.assertIn('risk accepted, not tested (reason from Quinn: "retired in this release")', out)
        (item,) = self.store().items
        d = item.decision
        self.assertEqual((d.target_id, d.verdict, d.reason, d.author, d.channel), (self.area["id"], "skip", "retired in this release", "Quinn", "cli"))
        self.assertEqual((d.fingerprint, d.revision, item.created_at), (self.area["fingerprint"], self.report["review"]["revision"], THEN))

    def test_a_decision_on_a_report_that_is_revision_2_records_revision_2(self):
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data["review"]["revision"] = 2
        self.path.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(decide(str(self.path), "--target", self.area["id"], "--verdict", "test", "--author", "Q")[0], 0)
        self.assertEqual([i.decision.revision for i in self.store().items], [2])

    def test_a_finding_can_be_accepted_or_rejected(self):
        self.assertEqual(decide(str(self.path), "--target", self.finding["id"], "--verdict", "accept", "--author", "Sam")[0], 0)
        code, out, _ = decide(str(self.path), "--target", self.finding["id"], "--verdict", "reject", "--reason", "another service", "--author", "Sam")
        self.assertEqual(code, 0)
        self.assertIn("finding rejected by the reviewer", out)
        self.assertEqual([i.decision.verdict for i in self.store().items], ["accept", "reject"])

    def test_what_cannot_be_recorded_is_exit_2_with_a_message_and_nothing_is_written(self):
        base = ("--author", "Quinn")
        cases = {
            "unknown target": ("--target", "a-" + "9" * 16, "--verdict", "test"),
            "not an id": ("--target", "/billing/invoices", "--verdict", "test"),
            "verdict for the other kind": ("--target", self.area["id"], "--verdict", "accept"),
            "verdict for the other kind 2": ("--target", self.finding["id"], "--verdict", "skip", "--reason", "x"),
            "no such verdict": ("--target", self.area["id"], "--verdict", "maybe"),
            "skip without a reason": ("--target", self.area["id"], "--verdict", "skip"),
            "reject with a blank reason": ("--target", self.finding["id"], "--verdict", "reject", "--reason", "  "),
            "reason too long": ("--target", self.area["id"], "--verdict", "skip", "--reason", "x" * 2001),
        }
        for name, args in cases.items():
            with self.subTest(name):
                code, out, err = decide(str(self.path), *args, *base)
                self.assertEqual((code, out), (2, ""), name)
                self.assertTrue(err.startswith("reachmap: error: "), err)
        self.assertEqual(self.store().items, ())

    def test_the_author_is_required_given_by_the_person_and_never_the_account_of_the_machine(self):
        env = {"USER": "machine-user", "LOGNAME": "machine-user"}
        code, out, err = decide(str(self.path), "--target", self.area["id"], "--verdict", "test", env=env)
        self.assertEqual(code, 2)
        self.assertIn("--author", err)
        for blank in ("", "   "):
            self.assertEqual(decide(str(self.path), "--target", self.area["id"], "--verdict", "test", "--author", blank, env=env)[0], 2)
        decide(str(self.path), "--target", self.area["id"], "--verdict", "test", "--author", "A. Person", env=env)
        self.assertNotIn("machine-user", store.decisions_path(self.base, KEY).read_text())

    def test_a_target_the_report_has_is_required_so_a_decision_cannot_be_made_for_a_report_without_review_data(self):
        for name in ("complete", "failed"):
            from tests import support_report as sr

            bare = self.tmp / f"{name}.json"
            bare.write_text(json.dumps(sr.fixture(name)))
            code, _, err = decide(str(bare), "--target", "a-" + "1" * 16, "--verdict", "test", "--author", "Q")
            self.assertEqual(code, 2, name)
            self.assertRegex(err, r"(?i)review|did not complete")

    def test_a_report_that_is_not_there_or_not_a_report_is_exit_2(self):
        self.assertEqual(decide(str(self.tmp / "nope.json"), "--target", "a-" + "1" * 16, "--verdict", "test", "--author", "Q")[0], 2)
        (self.tmp / "x.json").write_text("{}")
        self.assertEqual(decide(str(self.tmp / "x.json"), "--target", "a-" + "1" * 16, "--verdict", "test", "--author", "Q")[0], 2)

    def test_a_store_that_cannot_be_read_is_not_added_to(self):
        path = store.decisions_path(self.base, KEY)
        path.parent.mkdir(parents=True)
        path.write_text("garbage\n")
        code, _, err = decide(str(self.path), "--target", self.area["id"], "--verdict", "test", "--author", "Q")
        self.assertEqual(code, 2)
        self.assertIn("line 1", err)
        self.assertEqual(path.read_text(), "garbage\n")

    def test_a_hostile_reason_is_printed_with_its_odd_characters_written_out_and_is_not_checked_as_the_tools_own_wording(self):
        reason = "it is safe <script> \"quoted\" `````` ]]> line\nbreak"
        code, out, err = decide(str(self.path), "--target", self.area["id"], "--verdict", "skip", "--reason", reason, "--author", "</b>Quinn")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("safe", out)  # content: quoted and attributed, never the tool's claim
        self.assertEqual(self.store().items[0].decision.reason, reason)

    def test_control_and_direction_characters_in_the_author_or_the_reason_are_refused_and_nothing_is_written(self):
        for author, reason in (("Quinn\x1b", "ok"), ("Quinn\u202e", "ok"), ("Quinn\n", "ok"), ("Quinn", "it is safe \x1b[31m"), ("Quinn", "a \u202e b"), ("Quinn", "a \u2028 b"), ("Quinn", "a\rb")):
            with self.subTest(author=author, reason=reason):
                code, out, err = decide(str(self.path), "--target", self.area["id"], "--verdict", "skip", "--reason", reason, "--author", author)
                self.assertEqual((code, out), (2, ""))
                self.assertIn("--author" if author != "Quinn" else "--reason", err)
                for raw in ("\x1b", "\u202e", "\u2028"):
                    self.assertNotIn(raw, err)
        self.assertEqual(len(self.store().items), 0)

    def test_list_prints_every_target_with_its_id_kind_label_and_whether_it_has_a_decision(self):
        decide(str(self.path), "--target", self.area["id"], "--verdict", "test", "--author", "Q")
        code, out, _ = decide(str(self.path), "--list")
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(len(lines), len(self.report["areas"]) + len(self.report["findings"]))
        first = next(ln for ln in lines if ln.startswith(self.area["id"]))
        self.assertIn("marked to test", first)
        self.assertTrue(all(re.match(r"^[af]-[0-9a-f]{16}  (area|finding)  ", ln) for ln in lines))
        self.assertIn("no decision", [ln for ln in lines if ln.startswith(self.finding["id"])][0])
        self.assertEqual(wording.check(out.replace(self.report["areas"][0]["label"], "")), [])

    def test_list_with_a_store_that_cannot_be_read_says_so_and_does_not_claim_no_decision(self):
        decide(str(self.path), "--target", self.area["id"], "--verdict", "test", "--author", "Q")
        with open(store.decisions_path(self.base, KEY), "ab") as f:
            f.write(b"garbage")
        code, out, _ = decide(str(self.path), "--list")
        self.assertEqual(code, 0)
        self.assertNotIn("no decision", out)
        self.assertEqual(out.count("decisions could not be read"), len(self.report["areas"]) + len(self.report["findings"]))

    def test_what_the_command_says_passes_the_wording_check(self):
        said = []
        said.append(decide(str(self.path), "--target", self.area["id"], "--verdict", "skip", "--reason", "retired", "--author", "Q")[1].replace("retired", ""))
        said.append(decide(str(self.path), "--target", self.finding["id"], "--verdict", "accept", "--author", "Q")[1])
        said.append(decide(str(self.path), "--list")[1].replace(self.report["areas"][0]["label"], ""))
        for args in (("--target", self.area["id"], "--verdict", "accept", "--author", "Q"), ("--list", "--target", "x"), ("--target", "x", "--verdict", "test", "--author", "Q")):
            said.append(decide(str(self.path), *args)[2])
        said.append(decide(str(self.path), "--import", str(self.tmp / "nope.json"))[2].replace(str(self.tmp), ""))
        for text in said:
            self.assertEqual(wording.check(text), [], text)

    def test_the_store_lies_beside_the_report_not_in_the_working_directory(self):
        decide(str(self.path), "--target", self.area["id"], "--verdict", "test", "--author", "Q")
        self.assertTrue(store.decisions_path(self.path.parent, KEY).is_file())
        self.assertFalse((self.tmp / ".reachmap").exists())


class ImportCommand(ReviewCase):
    def setUp(self):
        super().setUp()
        self.path = self.copy_report(*S01)
        self.report = json.loads(self.path.read_text(encoding="utf-8"))
        self.base = self.path.parent
        self.area, self.finding = self.report["areas"][0], self.report["findings"][0]
        revisions.record(self.base, KEY, revisions.Rev(1, self.path.name, self.report["ref"], THEN))  # the revision the files below were exported from

    def exchange(self, **over):
        doc = {
            "format": "reachmap-decisions", "version": 1,
            "report": {"key": {k: self.report["review"]["key"][k] for k in ("workspace", "repo", "change")}, "head_sha": self.report["ref"], "revision": 1},
            "exported_at": "2026-02-03T04:05:06.789Z",
            "decisions": [
                {"target_id": self.area["id"], "verdict": "skip", "reason": "known", "author": "Quinn", "fingerprint": "e" * 64, "created_at": "2026-02-03T04:00:00.000Z"},
                {"target_id": self.finding["id"], "verdict": "accept", "reason": "", "author": "Quinn", "fingerprint": self.finding["fingerprint"], "created_at": "2026-02-03T04:01:00.000Z"},
            ],
        }
        doc.update(over)
        path = self.tmp / "decisions.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        return str(path)

    def test_decide_import_appends_with_the_channel_html_import_and_the_exported_fingerprint(self):
        code, out, err = decide("--import", self.exchange(), str(self.path))
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Imported 2 decisions", out)
        items = store.read(self.base, KEY).items
        self.assertEqual([i.decision.channel for i in items], ["html-import", "html-import"])
        self.assertEqual(items[0].decision.fingerprint, "e" * 64)  # not the report's: the one the person saw
        self.assertNotEqual(items[0].decision.fingerprint, self.area["fingerprint"])
        self.assertEqual(items[0].created_at, "2026-02-03T04:00:00.000Z")

    def test_a_file_for_another_analysis_key_is_refused_with_a_message_and_nothing_is_written(self):
        other = {"key": {"workspace": "acme", "repo": "acme/api", "change": "compare:main...x"}, "head_sha": "a" * 40, "revision": 1}
        code, out, err = decide("--import", self.exchange(report=other), str(self.path))
        self.assertEqual(code, 2)
        self.assertIn("another analysis", err)
        self.assertFalse(store.decisions_path(self.base, KEY).exists())

    def test_a_file_that_is_not_valid_is_refused_and_nothing_is_written(self):
        bad = self.tmp / "bad.json"
        for body in ("not json", "{}", json.dumps({"format": "x"})):
            bad.write_text(body)
            code, _, err = decide("--import", str(bad), str(self.path))
            self.assertEqual(code, 2)
            self.assertTrue(err.startswith("reachmap: error: "))
        self.assertEqual(decide("--import", str(self.tmp / "missing.json"), str(self.path))[0], 2)
        self.assertFalse(store.decisions_path(self.base, KEY).exists())

    def test_an_import_may_name_a_target_the_report_no_longer_has_it_is_kept_and_shown_as_orphaned_later(self):
        gone = "a-" + "7" * 16
        doc = json.loads(open(self.exchange()).read())
        doc["decisions"].append({"target_id": gone, "verdict": "test", "reason": "", "author": "Q", "fingerprint": "7" * 64, "created_at": "2026-02-03T04:03:00.000Z"})
        (self.tmp / "d2.json").write_text(json.dumps(doc))
        self.assertEqual(decide("--import", str(self.tmp / "d2.json"), str(self.path))[0], 0)
        self.assertIn(gone, [i.decision.target_id for i in store.read(self.base, KEY).items])

    def test_a_file_exported_from_a_revision_or_commit_the_reports_directory_has_no_record_of_is_refused(self):
        key = {k: self.report["review"]["key"][k] for k in ("workspace", "repo", "change")}
        for report in ({"key": key, "head_sha": self.report["ref"], "revision": 9}, {"key": key, "head_sha": "b" * 40, "revision": 1}):
            code, out, err = decide("--import", self.exchange(report=report), str(self.path))
            self.assertEqual((code, out), (2, ""))
            self.assertIn("no record of that revision", err)
            self.assertFalse(store.decisions_path(self.base, KEY).exists())

    def test_the_summary_says_how_many_decisions_it_replaced(self):
        first = decide("--import", self.exchange(), str(self.path))
        self.assertIn("Imported 2 decisions from decisions.json (channel html-import); 0 replaced a decision in force", first[1])
        again = decide("--import", self.exchange(), str(self.path))
        self.assertIn("2 replaced a decision in force", again[1])
        self.assertEqual(wording.check(again[1]), [])

    def test_an_imported_decision_older_than_the_one_in_force_is_a_warning_on_stderr_and_still_imported(self):
        self.assertEqual(decide(str(self.path), "--target", self.area["id"], "--verdict", "test", "--author", "Q")[0], 0)  # recorded 2026-01-02
        doc = json.loads(open(self.exchange()).read())
        doc["decisions"][0]["created_at"] = "2025-12-31T23:59:59.000Z"  # older than the decision in force
        doc["decisions"][1]["created_at"] = "2026-03-01T00:00:00.000Z"
        (self.tmp / "older.json").write_text(json.dumps(doc))
        code, out, err = decide("--import", str(self.tmp / "older.json"), str(self.path))
        self.assertEqual(code, 0)
        self.assertEqual(err.count("warning"), 1)
        self.assertIn(self.area["id"], err)
        self.assertIn("2025-12-31T23:59:59.000Z", err)
        self.assertIn("1 replaced a decision in force", out)
        self.assertEqual(len(store.read(self.base, KEY).items), 3)  # nothing refused: the imported one is the last line and so in force
        self.assertEqual(wording.check(err), [])

    def test_the_times_are_compared_as_times_not_as_text(self):
        decide(str(self.path), "--target", self.area["id"], "--verdict", "test", "--author", "Q", )
        doc = json.loads(open(self.exchange()).read())
        doc["decisions"][0]["created_at"] = "2026-01-02T03:04:05.500Z"  # later than 03:04:05Z by half a second, though "." sorts before "Z"
        (self.tmp / "half.json").write_text(json.dumps(doc))
        _, _, err = decide("--import", str(self.tmp / "half.json"), str(self.path))
        self.assertEqual(err, "")

    def test_a_file_too_big_is_refused_by_its_bytes_before_it_is_decoded(self):
        from reachmap.review import exchange

        big = self.tmp / "big.json"
        big.write_bytes(b"\xff" * (exchange.MAX_BYTES + 1))  # not UTF-8 either: the size is what is said
        code, _, err = decide("--import", str(big), str(self.path))
        self.assertEqual(code, 2)
        self.assertIn("too big", err)
        self.assertNotIn("UTF-8", err)

    def test_a_decision_and_an_import_are_not_asked_for_together(self):
        code, _, err = decide("--import", self.exchange(), str(self.path), "--target", self.area["id"], "--verdict", "test", "--author", "Q")
        self.assertEqual(code, 2)


class AnalyseCarriesDecisions(ReviewCase):
    def first(self):
        self.assertEqual(self.go(*S01)[0], 0)
        data, path = self.latest_report()
        return data, path

    def test_decisions_made_on_the_first_run_are_current_in_the_second_when_nothing_changed(self):
        data, path = self.first()
        a = data["areas"]
        decide(str(path), "--target", a[0]["id"], "--verdict", "skip", "--reason", "known", "--author", "Q")
        decide(str(path), "--target", data["findings"][1]["id"], "--verdict", "reject", "--reason", "no", "--author", "Q")
        self.go(*S01)
        second, _ = self.latest_report()
        self.assertEqual(second["review"]["revision"], 2)
        self.assertEqual({x["target_id"] for x in second["review"]["decisions"]["current"]}, {a[0]["id"], data["findings"][1]["id"]})
        self.assertEqual((second["review"]["decisions"]["stale"], second["review"]["decisions"]["orphaned"]), ([], []))
        self.assertEqual(len(second["review"]["decisions"]["undecided"]["areas"]), len(second["areas"]) - 1)

    def test_a_decision_changes_nothing_in_the_analysis_rule_4_4(self):
        base = self.first()[0]
        _, path = self.latest_report()
        for t in base["areas"]:
            decide(str(path), "--target", t["id"], "--verdict", "skip", "--reason", "risk taken", "--author", "Q")
        for t in base["findings"]:
            decide(str(path), "--target", t["id"], "--verdict", "reject", "--reason", "no", "--author", "Q")
        self.go(*S01)
        after, _ = self.latest_report()
        self.assertEqual(len(after["review"]["decisions"]["current"]), len(base["areas"]) + len(base["findings"]))
        for key in ("areas", "findings", "counts", "propagations", "branches", "incompleteness", "notes", "backend_calls", "suggestions", "mapped_tests", "change", "ref"):
            self.assertEqual(after[key], base[key], key)
        was = Analysis.from_dict({k: base[k] for k in support_cli.ANALYSIS_KEYS})
        now = Analysis.from_dict({k: after[k] for k in support_cli.ANALYSIS_KEYS})
        self.assertEqual([a.id for a in coverage_gaps(now)], [a.id for a in coverage_gaps(was)])
        self.assertEqual(len(coverage_gaps(now)), len(base["areas"]))  # every skipped area is still a gap: a decision clears none
        self.assertTrue(all(a["priority"] == "must_test" for a in after["areas"]))

    def test_the_decisions_object_is_the_only_thing_that_differs_between_a_run_with_decisions_and_one_without(self):
        plain = support_cli.cli_run(*S01).report
        self.first()
        _, path = self.latest_report()
        decide(str(path), "--target", plain["areas"][0]["id"], "--verdict", "sample", "--author", "Q")
        self.go(*S01)
        decided, _ = self.latest_report()
        drop = lambda r: {k: v for k, v in r.items() if k not in ("run", "review", "inputs")}  # noqa: E731
        self.assertEqual(drop(decided), drop(plain))

    def test_a_store_with_a_corrupt_last_line_is_a_blocking_record_for_the_decisions_and_the_run_is_otherwise_the_same(self):
        data, path = self.first()
        decide(str(path), "--target", data["areas"][0]["id"], "--verdict", "test", "--author", "Q")
        with open(store.decisions_path(self.out, KEY), "ab") as f:
            f.write(b'{"v":1,"target_id":"a-')
        code, out, err = self.go(*S01)
        self.assertEqual(code, 0)
        second, _ = self.latest_report()
        d = second["review"]["decisions"]
        self.assertEqual((d["current"], d["stale"], d["orphaned"]), ([], [], []))  # never read as "no decisions" and never as the intact ones
        (rec,) = d["records"]
        self.assertEqual((rec["kind"], rec["blocks_selection"]), ("decision_store_unreadable", True))
        self.assertEqual(second["incompleteness"], data["incompleteness"])  # it blocks the decisions view only
        self.assertEqual(second["propagations"], data["propagations"])
        self.assertIn("decision_store_unreadable", out)

    def test_the_summary_counts_the_decisions_by_state(self):
        data, path = self.first()
        decide(str(path), "--target", data["areas"][0]["id"], "--verdict", "test", "--author", "Q")
        _, out, _ = self.go(*S01)
        self.assertRegex(out, r"Decisions: 1 current, 0 need review, 0 orphaned")
        self.assertEqual(wording.check(out), [])


class AnalyseDecisionsFile(ReviewCase):
    def exchange(self, report, items, revision=1, key=None):
        doc = {
            "format": "reachmap-decisions", "version": 1,
            "report": {"key": key or {k: report["review"]["key"][k] for k in ("workspace", "repo", "change")}, "head_sha": report["ref"], "revision": revision},
            "exported_at": "2026-02-03T04:05:06.789Z", "decisions": items,
        }
        path = self.tmp / "decisions.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        if key is None and not revisions.read(self.out, KEY):  # the reports directory has the revision the file says it was exported from
            revisions.record(self.out, KEY, revisions.Rev(1, "earlier-r1.json", report["ref"], THEN))
        return str(path)

    def item(self, target, verdict="test", reason="", fingerprint=None, author="Quinn"):
        return {"target_id": target["id"], "verdict": verdict, "reason": reason, "author": author, "fingerprint": fingerprint or target["fingerprint"], "created_at": "2026-02-03T04:00:00.000Z"}

    def test_analyse_with_decisions_imports_them_first_so_the_report_shows_them(self):
        base = support_cli.cli_run(*S01).report
        t = base
        path = self.exchange(base, [self.item(t["areas"][0], "skip", "known"), self.item(t["findings"][0], "accept")])
        code, out, err = self.go(*S01, "--decisions", path)
        self.assertEqual((code, err), (0, ""))
        data, _ = self.latest_report()
        self.assertEqual({x["target_id"] for x in data["review"]["decisions"]["current"]}, {t["areas"][0]["id"], t["findings"][0]["id"]})
        self.assertEqual({x["channel"] for x in data["review"]["decisions"]["current"]}, {"html-import"})
        self.assertIn("Imported 2 decisions", out)

    def test_the_exported_fingerprint_is_kept_so_an_import_made_after_another_commit_is_judged_against_what_the_person_saw(self):
        base = support_cli.cli_run(*S01).report
        t = base
        old_claim = "c" * 64  # the fingerprint of the claim as the person saw it on an earlier revision
        path = self.exchange(base, [self.item(t["areas"][0], fingerprint=old_claim), self.item(t["areas"][1])], revision=1)
        self.go(*S01, "--decisions", path)
        data, _ = self.latest_report()
        self.assertEqual([x["target_id"] for x in data["review"]["decisions"]["stale"]], [t["areas"][0]["id"]])
        self.assertEqual(data["review"]["decisions"]["stale"][0]["fingerprint"], old_claim)
        self.assertEqual(data["review"]["decisions"]["stale"][0]["now_fingerprint"], t["areas"][0]["fingerprint"])
        self.assertEqual([x["target_id"] for x in data["review"]["decisions"]["current"]], [t["areas"][1]["id"]])

    def test_a_file_for_another_key_stops_the_command_before_anything_is_run_or_written(self):
        base = support_cli.cli_run(*S01).report
        path = self.exchange(base, [self.item(base["areas"][0])], key={"workspace": "acme", "repo": "acme/api", "change": "compare:main...x"})
        code, out, err = self.go(*S01, "--decisions", path)
        self.assertEqual(code, 2)
        self.assertIn("another analysis", err)
        self.assertEqual(self.reports(), [])
        self.assertFalse((self.out / ".reachmap").exists())

    def test_a_file_that_is_not_valid_or_not_there_is_exit_2(self):
        bad = self.tmp / "bad.json"
        bad.write_text("nope")
        self.assertEqual(self.go(*S01, "--decisions", str(bad))[0], 2)
        self.assertEqual(self.go(*S01, "--decisions", str(self.tmp / "missing.json"))[0], 2)
        self.assertEqual(self.reports(), [])

    def test_a_hostile_reason_and_author_go_through_the_file_the_store_the_report_and_every_renderer_inert(self):
        import re

        from reachmap import report as reports
        from tests import support_report as sr
        from tests.test_report_html import Parsed
        from tests.test_report_markdown import BAD_CONTROL, scan

        base = support_cli.cli_run(*S01).report
        reason = " ".join(h for h in sr.HOSTILE_ALL if _reason_ok(h)) + " it is safe, covered by tests"
        author = "</b>Quinn <img src=x onerror=alert(1)>"[:100]
        path = self.exchange(base, [self.item(base["areas"][0], "skip", reason, author=author)])
        self.assertEqual(self.go(*S01, "--decisions", path, "--format", "json,md,html")[0], 0)
        data, jpath = self.latest_report()
        (entry,) = [x for x in data["review"]["decisions"]["current"] if x["kind"] == "area"]
        self.assertEqual((entry["reason"], entry["author"]), (reason, author))
        md = jpath.with_suffix(".md").read_text(encoding="utf-8")
        page = jpath.with_suffix(".html").read_text(encoding="utf-8")
        self.assertEqual(md, reports.render(reports.load(jpath), "md"))
        pieces = list(scan(md))
        self.assertEqual([v for k, v in pieces if k == "stray"], [])
        for kind, value in pieces:
            if kind == "text":
                value = re.sub(r"\*\*[^*]+\*\*", "", value)
                self.assertNotRegex(value, r"[<>\[\]*_~|]")
        self.assertIsNone(BAD_CONTROL.search(md))
        parsed = Parsed(page)
        self.assertEqual((len(parsed.json_scripts), len(parsed.code_scripts)), (1, 1))
        self.assertEqual(json.loads(parsed.json_scripts[0]["text"]), json.loads(jpath.read_text(encoding="utf-8")))
        self.assertNotIn("<img src=x", re.sub(r"(?s)<script.*?</script>", "", page))
        self.assertNotIn("connect-src", page)
        out = io.StringIO()
        self.assertEqual(cli.main(["report", str(jpath), "--format", "html"], stdout=out, stderr=io.StringIO()), 0)
        self.assertEqual(out.getvalue(), page)  # the command renders the same bytes from the same store

    def test_a_reason_that_cannot_be_kept_a_lone_surrogate_is_refused_with_a_message(self):
        base = support_cli.cli_run(*S01).report
        path = self.exchange(base, [self.item(base["areas"][0], "skip", "\ud800")])
        code, _, err = self.go(*S01, "--decisions", path)
        self.assertEqual(code, 2)
        self.assertIn("surrogate", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(self.reports(), [])

    def test_a_file_whose_revision_the_reports_directory_has_no_record_of_is_exit_2_before_anything_is_run(self):
        base = support_cli.cli_run(*S01).report
        path = self.exchange(base, [self.item(base["areas"][0])])
        os.unlink(revisions.revisions_path(self.out, KEY))
        code, out, err = self.go(*S01, "--decisions", path)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("no record of that revision", err)
        self.assertEqual(self.reports(), [])
        self.assertFalse(store.decisions_path(self.out, KEY).exists())

    def test_an_import_stamps_the_revision_of_the_file_not_the_revision_of_the_run_that_imported_it(self):
        self.go(*S01)
        self.go(*S01)  # revisions 1 and 2 are in the log
        base, _ = self.latest_report()
        path = self.exchange(base, [self.item(base["areas"][0])], revision=1)
        code, out, err = self.go(*S01, "--decisions", path)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.latest_report()[0]["review"]["revision"], 3)
        (item,) = store.read(self.out, KEY).items
        self.assertEqual(item.decision.revision, 1)  # judged as the decision of the revision the person looked at
        self.assertEqual(item.decision.channel, "html-import")

    def test_the_summary_of_an_analyse_with_a_file_says_how_many_it_replaced(self):
        base = support_cli.cli_run(*S01).report
        path = self.exchange(base, [self.item(base["areas"][0])])
        _, first, _ = self.go(*S01, "--decisions", path)
        _, second, _ = self.go(*S01, "--decisions", path)
        self.assertIn("0 replaced a decision in force", first)
        self.assertIn("1 replaced a decision in force", second)

    def test_a_run_that_does_not_complete_does_not_import_and_says_so(self):
        base = support_cli.cli_run(*S01).report
        key = {"workspace": "acme", "repo": "acme/api", "change": "compare:main...nope"}
        path = self.exchange(base, [self.item(base["areas"][0])], key=key)
        revisions.record(self.out, store.make_key(**key), revisions.Rev(1, "earlier-r1.json", base["ref"], THEN))
        code, out, err = self.go("--repo", "acme/api", "--compare", "main...nope", "--decisions", path)
        self.assertEqual(code, 3)
        self.assertIn("not imported", err)
        self.assertFalse(store.decisions_path(self.out, key).exists())

    def test_the_same_file_twice_adds_the_lines_again_and_the_last_one_is_in_force(self):
        base = support_cli.cli_run(*S01).report
        path = self.exchange(base, [self.item(base["areas"][0], "sample")])
        self.go(*S01, "--decisions", path)
        self.go(*S01, "--decisions", path)
        self.assertEqual(len(store.read(self.out, KEY).items), 2)
        data, _ = self.latest_report()
        self.assertEqual(len(data["review"]["decisions"]["current"]), 1)


class ConcurrentAnalyse(ReviewCase):
    def test_three_analyses_started_at_once_on_one_key_get_revisions_1_2_3_and_three_whole_reports(self):
        import subprocess
        import sys

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        home = self.tmp / "home"
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(support_cli.env_vars(), HOME=str(home), XDG_CONFIG_HOME=str(home / ".xdg"), PYTHONDONTWRITEBYTECODE="1")
        argv = [sys.executable, "-m", "reachmap", "analyse", "--config", str(self.config), *S01, "--out", str(self.out), "--quiet"]
        procs = [subprocess.Popen(argv, cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(3)]
        results = [(p.wait(timeout=300), p.stderr.read()) for p in procs]
        self.assertEqual(results, [(0, "")] * 3)
        reports = [json.loads(p.read_text(encoding="utf-8")) for p in self.reports()]
        self.assertEqual(sorted(r["review"]["revision"] for r in reports), [1, 2, 3])
        log = revisions.read(self.out, KEY)
        self.assertEqual([r.number for r in log], [1, 2, 3])
        self.assertEqual(sorted(r.file for r in log), sorted(p.name for p in self.reports()))  # every line of the log names the report that was written for it
        for r in reports:  # a report's `previous` is the run that entered the log just before it
            number = r["review"]["revision"]
            self.assertEqual(r["review"]["previous"], None if number == 1 else {"number": number - 1, "file": log[number - 2].file, "head_sha": r["ref"]})


class RenderedFromTheStore(ReviewCase):
    def test_report_shows_the_decisions_made_since_the_analysis(self):
        self.go(*S01)
        data, path = self.latest_report()
        decide(str(path), "--target", data["areas"][0]["id"], "--verdict", "skip", "--reason", "retired", "--author", "Quinn")
        out = io.StringIO()
        self.assertEqual(cli.main(["report", str(path)], stdout=out, stderr=io.StringIO()), 0)
        self.assertIn("risk accepted, not tested", out.getvalue())
        self.assertIn("retired", out.getvalue())
        self.assertNotIn("risk accepted", path.read_text())  # the JSON file is the analysis: it is not rewritten by a decision

    def test_report_without_a_store_renders_what_the_json_holds(self):
        self.go(*S01, "--format", "md")
        (md,) = self.out.glob("*.md")
        data, path = self.latest_report()
        out = io.StringIO()
        cli.main(["report", str(path), "--format", "md"], stdout=out, stderr=io.StringIO())
        self.assertEqual(out.getvalue(), md.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
