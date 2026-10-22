"""The citation audit (M18), with a reader that needs no repository: the four answers for a line, what is a structure problem, what the
output says. The reports of the fixture worlds, through real repositories, are in
`test_eval_audit_acme` and `test_eval_audit_m16a` to `_m16c`."""

import copy
import re
import unittest
from pathlib import Path

from reachmap import wording
from reachmap.evaluate import audit
from reachmap.evaluate.audit import ABSENT, MISMATCH, OK, UNREADABLE
from tests import support_eval_mini as mini
from tests.support_eval_mini import OTHER, SHA, FakeReader, ev


def run(report=None, reader=None):
    return audit.audit_report(mini.mini_report() if report is None else report, reader or FakeReader(), "r.json")


def edited(change):
    report = mini.mini_report()
    change(report)
    return report


class Collecting(unittest.TestCase):
    def test_every_piece_of_evidence_is_found_by_its_shape_wherever_it_is(self):
        problems = []
        sites = [c.site for c in audit.collect(mini.mini_report(), problems)]
        self.assertEqual(problems, [])
        self.assertIn("findings[0].evidence[0]", sites)
        self.assertIn("areas[0].route_line", sites)
        self.assertIn("areas[0].chains[0][1]", sites)
        self.assertIn("branches[0].evidence[0]", sites)
        self.assertIn("propagations[0].passes[0].analysis.impacts[0].importers[0].evidence", sites)
        self.assertEqual(len(sites), 3 + 1 + 2 + 1 + 1 + 3)

    def test_an_object_with_the_keys_of_evidence_that_is_not_valid_evidence_is_a_structure_problem_and_no_locator(self):
        for bad in ({"ref": "main"}, {"path": "../x"}, {"path": "/etc/passwd"}, {"line": 0}, {"line": "3"}, {"ref": "0" * 40}, {"extra": 1}):
            with self.subTest(bad):
                report = edited(lambda r: r["branches"][0]["evidence"][0].update(bad))
                problems = []
                cited = audit.collect(report, problems)
                self.assertEqual(len(problems), 1, problems)
                self.assertEqual(len(cited), 3 + 0 + 2 + 1 + 1 + 3)
                result = run(report)
                self.assertTrue(result.structure)
                self.assertFalse(result.passed())


class Answers(unittest.TestCase):
    def test_a_report_whose_lines_are_all_there_passes_with_counts_and_no_failure(self):
        result = run()
        self.assertEqual(result.counts, {OK: 4, MISMATCH: 0, ABSENT: 0, UNREADABLE: 0})
        self.assertEqual(result.occurrences, 11)  # the same line is cited by several lists; each distinct locator is read once
        self.assertEqual(result.structure, [])
        self.assertTrue(result.passed())

    def test_one_batch_of_reads_per_commit_with_every_path_of_it(self):
        reader = FakeReader()
        run(reader=reader)
        self.assertEqual([(r, ref) for r, ref, _ in reader.calls], [("acme/web", SHA)])
        self.assertEqual(sorted(reader.calls[0][2]), sorted(mini.LINES))

    def test_a_different_text_is_a_mismatch_and_both_texts_are_shown(self):
        report = edited(lambda r: r["branches"][0]["evidence"][0].update(detail="}  // edited"))
        result = run(report)
        self.assertEqual((result.counts[MISMATCH], result.counts[OK]), (1, 3))
        fail = result.failures[0]
        self.assertEqual((fail.state, fail.citation.site, fail.found), (MISMATCH, "branches[0].evidence[0]", "}"))
        text = "\n".join(audit.render([result]))
        self.assertIn("report: } // edited", text)
        self.assertIn("commit: }", text)
        self.assertIn("src/c/Badge.tsx:3 at aaaaaaaaaaaa", text)
        self.assertFalse(result.passed())

    def test_a_line_that_moved_is_a_mismatch(self):
        for field, value in (("line", 2), ("line", 1)):
            with self.subTest(value):
                result = run(edited(lambda r: r["branches"][0]["evidence"][0].update({field: value})))
                self.assertEqual(result.counts[MISMATCH], 1)
                self.assertEqual(result.counts[ABSENT] + result.counts[UNREADABLE], 0)

    def test_a_line_past_the_end_is_a_mismatch_that_says_how_long_the_file_is(self):
        result = run(edited(lambda r: r["branches"][0]["evidence"][0].update(line=40)))
        self.assertEqual(result.counts[MISMATCH], 1)
        self.assertEqual(result.failures[0].found, "no line 40: the file has 3 lines")

    def test_the_empty_piece_after_the_last_newline_is_no_line(self):
        text = "export function Badge() {\n  return null;\n}\nx\n"  # four lines; the empty piece after the last newline is not a fifth
        for line, expect in ((4, OK), (5, MISMATCH)):
            files = {("acme/web", SHA): {**FakeReader().files[("acme/web", SHA)], "src/c/Badge.tsx": text}}
            report = edited(lambda r: r["branches"][0]["evidence"][0].update(line=line, detail="x"))
            self.assertEqual(run(report, FakeReader(files)).counts[expect], 4 if expect == OK else 1, line)

    def test_a_path_that_is_not_in_the_commit_is_absent(self):
        result = run(edited(lambda r: r["branches"][0]["evidence"][0].update(path="src/c/Gone.tsx")))
        self.assertEqual((result.counts[ABSENT], result.counts[MISMATCH], result.counts[UNREADABLE]), (1, 0, 0))
        self.assertEqual(result.failures[0].found, "the path is not in the commit")

    def test_a_path_that_is_another_file_of_the_commit_is_a_mismatch(self):
        result = run(edited(lambda r: r["branches"][0]["evidence"][0].update(path="src/routes.tsx", line=3)))
        self.assertEqual(result.counts[MISMATCH], 1)

    def test_a_ref_changed_to_a_commit_the_file_is_not_in_is_absent_and_to_one_that_cannot_be_read_is_unreadable(self):
        report = edited(lambda r: r["branches"][0]["evidence"][0].update(ref=OTHER))
        self.assertEqual(run(report).counts[ABSENT], 1)
        broken = FakeReader(broken={("acme/web", OTHER)})
        result = run(report, broken)
        self.assertEqual((result.counts[UNREADABLE], result.counts[ABSENT], result.counts[MISMATCH]), (1, 0, 0))

    def test_unreadable_is_never_counted_as_ok_or_as_a_mismatch_and_fails_unless_allowed(self):
        result = run(reader=FakeReader(broken={("acme/web", SHA, "src/c/Badge.tsx")}))
        self.assertEqual((result.counts[UNREADABLE], result.counts[MISMATCH], result.counts[ABSENT]), (2, 0, 0))
        self.assertEqual(result.counts[OK], 2)
        self.assertEqual(result.distinct, 4)
        self.assertFalse(result.passed())
        self.assertTrue(result.passed(allow_unreadable=True))
        text = "\n".join(audit.render([result], allow_unreadable=True))
        self.assertIn("unreadable (allowed, not ok): 2 citations were not read", text)
        self.assertIn("why: the object is missing", text)

    def test_allowing_unreadable_does_not_allow_a_mismatch_an_absence_or_a_structure_problem(self):
        report = edited(lambda r: r["branches"][0]["evidence"][0].update(detail="x"))
        self.assertFalse(run(report).passed(allow_unreadable=True))
        report = edited(lambda r: r["findings"][0].update(id="f-0000000000000000"))
        self.assertFalse(run(report).passed(allow_unreadable=True))

    def test_a_carriage_return_at_the_end_of_a_crlf_line_is_not_part_of_the_text(self):
        files = {("acme/web", SHA): {p: "\r\n".join(v) + "\r\n" for p, v in mini.LINES.items()}}
        self.assertEqual(run(reader=FakeReader(files)).counts, {OK: 4, MISMATCH: 0, ABSENT: 0, UNREADABLE: 0})
        files[("acme/web", SHA)]["src/c/Badge.tsx"] = "export function Badge() {\r\n  a\rb\r\n}\r\n"
        inside = edited(lambda r: r["branches"][0]["evidence"][0].update(line=2, detail="  a\rb"))
        self.assertEqual(run(inside, FakeReader(files)).counts[OK], 4)  # a carriage return inside a line is part of its text
        self.assertEqual(run(edited(lambda r: r["branches"][0]["evidence"][0].update(line=2, detail="  ab")), FakeReader(files)).counts[MISMATCH], 1)

    def test_only_a_newline_ends_a_line_not_the_other_separators_a_unicode_string_knows(self):
        files = {("acme/web", SHA): {p: "\n".join(v) + "\n" for p, v in mini.LINES.items()}}
        files[("acme/web", SHA)]["src/c/Badge.tsx"] = "export function Badge() {\x0b\x0c\u2028\u0085\n  return null;\n}\n"
        crowded = "export function Badge() {\x0b\x0c\u2028\u0085"
        report = edited(lambda r: [e.update(detail=crowded) for e in (r["findings"][0]["evidence"][0], r["propagations"][0]["contract_reads"][0]["evidence"])])
        result = run(report, FakeReader(files))
        self.assertEqual((result.counts[OK], result.counts[MISMATCH]), (4, 0))  # line 3 is still the closing brace: no separator inside line 1 made a line

    def test_a_text_with_a_lone_surrogate_in_a_claim_is_refused_as_evidence_not_read(self):
        report = edited(lambda r: r["branches"][0]["evidence"][0].update(detail="a\ud800"))
        result = run(report)
        self.assertEqual(result.distinct, 3)
        self.assertTrue(result.structure)

    def test_the_same_locator_cited_in_several_places_is_read_once_and_counted_once(self):
        report = edited(lambda r: r["branches"].append(copy.deepcopy(r["branches"][0])))
        result = run(report)
        self.assertEqual((result.distinct, result.occurrences), (4, 12))

    def test_a_failure_says_how_many_places_cite_it(self):
        report = edited(lambda r: [r["branches"].append(copy.deepcopy(r["branches"][0])) for _ in range(2)])
        report["branches"][0]["evidence"][0]["detail"] = "x"
        report["branches"][1]["evidence"][0]["detail"] = "x"
        result = run(report)
        self.assertEqual(result.failures[0].occurrences, 2)

    def test_a_report_of_a_run_that_did_not_complete_cites_nothing_and_says_so(self):
        failed = {"schema": 1, "status": "failed", "findings": None, "areas": None, "branches": None, "propagations": None, "incompleteness": [], "notes": []}
        result = run(failed)
        self.assertEqual(result.distinct, 0)
        self.assertIn("status is failed", result.notes[0])

    def test_nothing_is_read_for_a_report_with_no_citations(self):
        reader = FakeReader()
        run({"schema": 1, "status": "completed", "findings": [], "areas": []}, reader)
        self.assertEqual(reader.calls, [])


class Structure(unittest.TestCase):
    def problems(self, change):
        return run(edited(change)).structure

    def test_a_finding_with_no_evidence_is_a_structure_problem(self):
        problems = self.problems(lambda r: r["findings"][0].update(evidence=[]))
        self.assertEqual(len(problems), 1)
        self.assertIn("findings[0]: a finding with no evidence", problems[0])

    def test_an_id_or_a_fingerprint_that_is_not_what_the_object_hashes_to_is_a_structure_problem(self):
        for name, report_item in (("id", "findings"), ("fingerprint", "findings"), ("id", "areas"), ("fingerprint", "areas")):
            with self.subTest(f"{report_item} {name}"):
                problems = self.problems(lambda r: r[report_item][0].update({name: "f" * 64 if name == "fingerprint" else r[report_item][0][name][:2] + "0" * 16}))
                self.assertEqual(len(problems), 1, problems)
                self.assertIn(f"{report_item}[0]: does not read back", problems[0])
                self.assertIn(name, problems[0])

    def test_a_cited_line_edited_after_the_fact_changes_the_fingerprint_and_is_found(self):
        def respelled(r):
            hop = r["areas"][0]["chains"][0][0]
            hop.pop("claim")
            hop["detail"] = "import { Other } from '@c/Other';"
        problems = self.problems(respelled)
        self.assertTrue(any("fingerprint" in p for p in problems), problems)

    def test_a_repeated_id_is_a_structure_problem(self):
        problems = self.problems(lambda r: r["findings"].append(copy.deepcopy(r["findings"][0])))
        self.assertEqual(len(problems), 1)
        self.assertIn("repeats the id of findings[0]", problems[0])

    def test_an_unknown_role_a_claim_that_is_not_role_value_and_a_claim_on_the_wrong_line_are_found(self):
        cases = {
            "unknown claim role": lambda r: r["branches"][0]["evidence"][0].update(claim="magic:x"),
            "not role:value": lambda r: r["branches"][0]["evidence"][0].update(claim="nothing"),
            "is not a name on its line": lambda r: r["branches"][0]["evidence"][0].update(claim="def:Badge"),
            "is not an import": lambda r: r["branches"][0]["evidence"][0].update(claim="import:src/x.ts:Badge"),
            "an import claim is import:<file>:<names>": lambda r: r["branches"][0]["evidence"][0].update(claim="import:Badge"),
            "names the file its line is in": lambda r: r["branches"][0]["evidence"][0].update(claim="wraps:src/other.tsx"),
            "a route claim is route:": lambda r: r["branches"][0]["evidence"][0].update(claim="route:/x"),
            "operation claim is operation:": lambda r: r["branches"][0]["evidence"][0].update(claim="operation:get:abc"),
        }
        for words, change in cases.items():
            with self.subTest(words):
                problems = self.problems(change)
                self.assertEqual(len(problems), 1, problems)
                self.assertIn(words, problems[0])
                self.assertIn("branches[0].evidence[0]", problems[0])

    def test_claims_that_agree_with_their_line_are_not_problems(self):
        good = {
            ("src/c/Badge.tsx", 1): "def:Badge",
            ("src/pages/Invoices.tsx", 1): "import:src/c/Badge.tsx:Badge",
            ("src/pages/Invoices.tsx", 3): "wraps:src/pages/Invoices.tsx",
        }
        for (path, line), claim in good.items():
            with self.subTest(claim):
                self.assertEqual(self.problems(lambda r: r["branches"][0]["evidence"][0].update(ev(path, line, claim))), [])

    def test_a_field_is_claimed_as_model_dot_field_and_cited_at_the_class_line(self):
        files = {("acme/web", SHA): {"m.py": "class Invoice(BaseModel):\n"}}
        mini.LINES["m.py"] = ["class Invoice(BaseModel):"]
        try:
            report = edited(lambda r: r["branches"][0]["evidence"][0].update(ev("m.py", 1, "def:Invoice.settlement_reference")))
        finally:
            del mini.LINES["m.py"]
        self.assertEqual(run(report, FakeReader(files)).structure, [])

    def test_an_import_whose_statement_goes_on_below_names_nothing_on_its_first_line_and_is_not_a_problem(self):
        for first in ("from acme_common.money import (", "import {", "import {   "):
            with self.subTest(first):
                report = edited(lambda r: r["branches"][0]["evidence"][0].update(detail=first, claim="import:libs/m.py:cents_to_decimal,format_cents"))
                self.assertEqual(run(report).structure, [], first)

    JAVA = "src/A.java"
    JAVA_TEXT = "@RestController\n@RequestMapping(Paths.X)\npublic class InvoiceController {\n  void get() {}\n}\n"

    def java(self, change, text=JAVA_TEXT):
        files = {("acme/web", SHA): {**FakeReader().files[("acme/web", SHA)], self.JAVA: text}}
        return run(edited(change), FakeReader(files)).structure

    def test_java_cites_a_declaration_at_its_annotation_when_the_declaration_below_names_the_symbol(self):
        ok = lambda r: r["branches"][0]["evidence"][0].update(path=self.JAVA, line=1, detail="@RestController", claim="def:InvoiceController.get")  # noqa: E731
        self.assertEqual(self.java(ok), [])
        wrong = lambda r: r["branches"][0]["evidence"][0].update(path=self.JAVA, line=1, detail="@RestController", claim="def:Other.thing")  # noqa: E731
        problems = self.java(wrong)
        self.assertEqual(len(problems), 1)
        self.assertIn("annotation at line 1 is not followed", problems[0])
        far = "@RestController\n" + "// filler\n" * 20 + "public class InvoiceController {}\n"
        self.assertEqual(len(self.java(ok, far)), 1)  # the declaration is too far below to be the one the annotation belongs to

    def test_the_annotation_and_same_package_relaxations_are_for_java_and_kotlin_files_only(self):
        for path in ("src/A.kt", "src/A.kts"):
            self.assertEqual(self.java(lambda r: r["branches"][0]["evidence"][0].update(path=path, line=1, detail="  return Helper.describe(x);", claim="import:src/Helper.kt:Helper")), [])
        for path in ("src/a.py", "src/a.ts", "src/A.java.txt"):
            problems = self.java(lambda r: r["branches"][0]["evidence"][0].update(path=path, line=1, detail="  return Helper.describe(x);", claim="import:src/Helper.kt:Helper"))
            self.assertTrue(any("is not an import" in p for p in problems), (path, problems))
            problems = self.java(lambda r: r["branches"][0]["evidence"][0].update(path=path, line=1, detail="@RestController", claim="def:InvoiceController"))
            self.assertTrue(any("is not a name on its line" in p for p in problems), (path, problems))

    def test_a_same_package_use_must_still_name_the_claimed_name(self):
        problems = self.java(lambda r: r["branches"][0]["evidence"][0].update(path=self.JAVA, line=1, detail="  return Other.describe(x);", claim="import:src/Helper.java:Helper"))
        self.assertTrue(any("neither imports nor uses 'Helper'" in p for p in problems), problems)

    def test_an_operation_method_is_matched_as_a_word(self):
        good = self.problems(lambda r: r["branches"][0]["evidence"][0].update(detail='"get": {', claim="operation:GET /x:abc"))
        self.assertEqual(good, [])
        bad = self.problems(lambda r: r["branches"][0]["evidence"][0].update(detail='"target": {', claim="operation:GET /x:abc"))
        self.assertEqual(len(bad), 1)
        self.assertIn("as a word", bad[0])

    def test_a_star_import_and_a_default_import_name_nothing_the_line_must_hold(self):
        for names in ("*", "default"):
            report = edited(lambda r: r["branches"][0]["evidence"][0].update(detail="import x from 'y';", claim=f"import:src/y.ts:{names}"))
            self.assertEqual(run(report).structure, [])

    def test_the_route_claim_of_an_area_is_its_label_and_the_last_hop_of_a_chain_one_of_its_mounts(self):
        def other_label(r):
            r["areas"][0]["route_line"]["claim"] = "route:/other:src/pages/Invoices.tsx"
        problems = self.problems(other_label)
        self.assertTrue(any("areas[0].route_line" in p and "own label" in p for p in problems), problems)

        def wrong_mount(r):
            r["areas"][0]["chains"][0][-1]["claim"] = "route:/other:src/pages/Invoices.tsx"
        problems = self.problems(wrong_mount)
        self.assertTrue(any("areas[0].chains[0][1]" in p and "not a route of the area" in p for p in problems), problems)

    def test_a_route_claim_for_a_route_the_area_is_also_mounted_at_is_not_a_problem(self):
        def other_mount(r):
            r["areas"][0]["also_mounted"] = ["/cases/x"]
            r["areas"][0]["chains"].append([copy.deepcopy(r["areas"][0]["chains"][0][0]), dict(copy.deepcopy(r["areas"][0]["route_line"]), claim="route:/cases/x:src/pages/Invoices.tsx")])
        report = edited(other_mount)
        problems = [p for p in run(report).structure if "not a route of the area" in p]
        self.assertEqual(problems, [])

    def test_a_stale_decision_that_says_a_line_was_added_that_the_report_does_not_hold_is_found(self):
        def stale(r, detail):
            r["review"] = {"decisions": {"stale": [{"changes": {"added": [{"repo": "acme/web", "path": "src/c/Badge.tsx", "detail": detail}], "removed": []}}]}}
        self.assertEqual(run(edited(lambda r: stale(r, "export function Badge() {"))).structure, [])
        problems = run(edited(lambda r: stale(r, "export function Nothing() {"))).structure
        self.assertEqual(len(problems), 1)
        self.assertIn("review.decisions.stale[0].changes.added[0]", problems[0])


class Notes(unittest.TestCase):
    def note(self, **over):
        verification = {"status": "verified", "reason": "verified", "read": "found", "sha": SHA, "symbol": "Badge", "lines": [1], "detail": None}
        entry = {"state": "current", "target_id": "a-0", "verification": verification, "proposal": {"repo": "acme/web", "path": "src/c/Badge.tsx", "symbol": "Badge"}}
        entry.update(over)
        return entry

    def report(self, *notes, ids=True):
        report = mini.mini_report()
        target = report["areas"][0]["id"]
        report["model_notes"] = {"notes": [{**n, "target_id": target if n.get("target_id") == "a-0" else n.get("target_id")} for n in notes], "records": [], "as_of": "x"}
        return report

    def test_a_verified_proposal_is_read_again_at_its_commit_and_the_symbol_must_be_on_the_line(self):
        result = run(self.report(self.note()))
        self.assertEqual((result.counts[OK], result.note_citations), (5, 1))
        self.assertTrue(result.passed())

    def test_a_line_that_no_longer_holds_the_symbol_is_a_mismatch(self):
        result = run(self.report(self.note(verification={**self.note()["verification"], "lines": [2]})))
        self.assertEqual(result.counts[MISMATCH], 1)
        self.assertEqual(result.failures[0].citation.symbol, "Badge")
        self.assertIn("symbol: Badge", "\n".join(audit.render([result])))

    def test_a_file_that_is_gone_is_absent_and_a_qualified_symbol_is_read_by_its_last_part(self):
        gone = run(self.report(self.note(proposal={"repo": "acme/web", "path": "src/c/Gone.tsx", "symbol": "Badge"})))
        self.assertEqual(gone.counts[ABSENT], 1)
        qualified = run(self.report(self.note(verification={**self.note()["verification"], "symbol": "ui.Badge"})))
        self.assertEqual(qualified.counts[MISMATCH], 0)

    def test_a_note_that_was_not_verified_cites_nothing(self):
        unverified = {**self.note()["verification"], "status": "not_verified", "reason": "unchecked_text", "sha": None, "lines": []}
        result = run(self.report(self.note(verification=unverified)))
        self.assertEqual(result.note_citations, 0)

    def test_a_current_note_about_a_target_the_report_lacks_is_a_problem_an_orphaned_one_is_not(self):
        current = run(self.report(self.note(target_id="a-ffffffffffffffff")))
        self.assertTrue(any("current note about a target the report does not have" in p for p in current.structure))
        orphaned = run(self.report(self.note(target_id="a-ffffffffffffffff", state="orphaned")))
        self.assertEqual(orphaned.structure, [])


class Output(unittest.TestCase):
    def test_the_output_has_counts_and_no_percentage(self):
        text = "\n".join(audit.render([run(), run(edited(lambda r: r["branches"][0]["evidence"][0].update(detail="x")))]))
        self.assertNotIn("%", text)
        self.assertIn("2 reports: 8 distinct citations (ok 7, mismatch 1, absent 0, unreadable 0), 0 structure problems; 1 report(s) failed", text)
        self.assertIn("r.json: passed", text)
        self.assertIn("r.json: FAILED", text)

    def test_only_the_first_n_failures_are_listed_and_the_rest_counted(self):
        report = edited(lambda r: r["branches"].extend({"evidence": [ev("src/c/Badge.tsx", 1, detail=f"x{n}")]} for n in range(7)))
        result = run(report)
        text = "\n".join(audit.render([result], show=3))
        self.assertEqual(len(re.findall(r"^  mismatch: ", text, re.M)), 3)
        self.assertIn("... 4 more failures not shown (--show N)", text)
        self.assertIn("mismatch 7", text)

    def test_the_text_of_a_hostile_line_is_shown_without_acting(self):
        hostile = "x\u202e\x1b[31m\u2028" + "a" * 500
        result = run(edited(lambda r: r["branches"][0]["evidence"][0].update(detail=hostile)))
        text = "\n".join(audit.render([result]))
        self.assertNotRegex(text, "[\u202e\x1b\u2028]")
        self.assertIn("\\u202e", text)
        self.assertIn("... (", text)
        self.assertIn("more characters)", text)

    def test_what_the_tool_says_passes_the_wording_check_and_has_no_dash_or_arrow(self):
        report = edited(lambda r: [r["branches"][0]["evidence"][0].update(detail="x"), r["findings"][0].update(evidence=[])])
        text = "\n".join(audit.render([run(report), run()]))
        self.assertEqual(wording.check(text.replace("A pass says every citation resolves at its commit; it does not say the analysis found everything.", "")), [])
        self.assertNotRegex(text, "[\u2014\u2013\u2192\u201c\u201d\u2018\u2019]|->")

    def test_a_pass_never_says_it_found_everything(self):
        text = "\n".join(audit.render([run()]))
        self.assertIn("does not say the analysis found everything", text)
        self.assertNotRegex(text.lower(), r"\bcomplete\b|\bsafe\b|\bcovered\b")


class Loading(unittest.TestCase):
    def write(self, text):
        import tempfile
        from pathlib import Path

        d = Path(tempfile.mkdtemp(prefix="reachmap-audit-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        p = d / "r.json"
        p.write_text(text, encoding="utf-8")
        return p

    def test_a_file_that_is_not_a_report_of_schema_1_is_an_audit_error(self):
        for text in ("not json", "[]", '{"schema": 2, "status": "completed"}', '{"schema": 1}'):
            with self.subTest(text):
                with self.assertRaises(audit.AuditError):
                    audit.load(self.write(text))

    def test_a_missing_file_is_an_audit_error_that_names_the_file_only(self):
        with self.assertRaises(audit.AuditError) as ctx:
            audit.load("/nonexistent-dir/r.json")
        self.assertEqual(str(ctx.exception).split(":")[0], "r.json")


class NotAudited(unittest.TestCase):
    FAILED = {"schema": 1, "status": "failed", "findings": None, "areas": None, "branches": None, "propagations": None, "incompleteness": [], "notes": []}

    def test_a_run_that_did_not_complete_is_not_a_pass_whatever_is_allowed(self):
        for status in ("failed", "timed_out"):
            result = run({**self.FAILED, "status": status})
            self.assertTrue(result.not_audited)
            self.assertFalse(result.passed())
            self.assertFalse(result.passed(allow_unreadable=True))

    def test_the_output_says_it_was_not_audited_and_counts_it_among_the_failures(self):
        text = "\n".join(audit.render([run(), run(self.FAILED)], allow_unreadable=True))
        self.assertIn("r.json: NOT AUDITED: the run did not complete", text)
        self.assertNotIn("r.json: passed\n  citations: 0", text)
        self.assertIn("1 report(s) failed (1 not audited)", text)

    def test_a_completed_report_is_never_called_not_audited(self):
        self.assertFalse(run().not_audited)


class Loading2(unittest.TestCase):
    def write(self, text):
        import shutil
        import tempfile

        d = Path(tempfile.mkdtemp(prefix="reachmap-audit-"))
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        p = d / "r.json"
        p.write_text(text, encoding="utf-8")
        return p

    def test_a_completed_report_without_its_result_lists_is_not_a_report_the_audit_reads(self):
        with self.assertRaises(audit.AuditError) as ctx:
            audit.load(self.write('{"schema": 1, "status": "completed"}'))
        self.assertIn("not a report reachmap can read", str(ctx.exception))

    def test_a_report_that_does_not_pass_the_check_the_renderers_make_is_refused(self):
        import json

        report = mini.mini_report()
        report["findings"][0]["evidence"] = []
        with self.assertRaises(audit.AuditError):
            audit.load(self.write(json.dumps(report)))

    def test_a_report_held_in_memory_that_lacks_what_a_completed_report_has_is_a_structure_problem(self):
        result = run({"schema": 1, "status": "completed"})
        self.assertEqual(len(result.structure), 1)
        self.assertIn("a completed report has change, ref, incompleteness", result.structure[0])
        self.assertFalse(result.passed())
        self.assertFalse(run({"schema": 1, "status": "completed", "findings": [], "areas": []}).passed())


class Stores(unittest.TestCase):
    RECORD = {"kind": "note_store_unreadable", "subject": "s.jsonl", "not_established": "which notes", "detail": "line 2: the line is not JSON", "blocks_selection": True}

    def with_records(self, where, record=None):
        report = mini.mini_report()
        record = record or self.RECORD
        if where == "notes":
            report["model_notes"] = {"notes": [], "records": [record], "as_of": "x"}
        else:
            report["review"] = {"decisions": {"current": [], "stale": [], "orphaned": [], "undecided": {}, "records": [dict(record, kind="decision_store_unreadable")], "as_of": "x"}}
        return run(report)

    def test_a_note_store_that_could_not_be_read_is_one_unreadable_with_its_detail_and_not_a_pass(self):
        result = self.with_records("notes")
        self.assertEqual((result.counts[UNREADABLE], result.counts[OK]), (1, 4))
        self.assertFalse(result.passed())
        self.assertTrue(result.passed(allow_unreadable=True))  # allowed like any unreadable read, and still counted
        text = "\n".join(audit.render([result]))
        self.assertIn("unreadable: model_notes.records[0] (note_store_unreadable)", text)
        self.assertIn("why: line 2: the line is not JSON", text)

    def test_a_decision_store_that_could_not_be_read_is_counted_the_same_way(self):
        result = self.with_records("decisions")
        self.assertEqual(result.counts[UNREADABLE], 1)
        self.assertIn("review.decisions.records[0] (decision_store_unreadable)", result.failures[0].what)

    def test_other_records_and_readable_stores_are_not_failures(self):
        report = mini.mini_report()
        report["model_notes"] = {"notes": [], "records": [{"kind": "something_else", "detail": "d"}], "as_of": "x"}
        self.assertEqual(run(report).counts[UNREADABLE], 0)
        self.assertTrue(run().passed())


class OneWalker(unittest.TestCase):
    def test_evidence_under_backend_calls_suggestions_and_contract_reads_is_cited_and_read(self):
        problems = []
        sites = [c.site for c in audit.collect(mini.mini_report(), problems)]
        self.assertEqual(problems, [])
        for site in ("backend_calls[0].evidence[0]", "suggestions[0].evidence[0]", "propagations[0].contract_reads[0].evidence"):
            self.assertIn(site, sites)
        broken = edited(lambda r: r["suggestions"][0]["evidence"][0].update(detail="not what the line says"))
        self.assertEqual(run(broken).counts[MISMATCH], 1)
        broken = edited(lambda r: r["propagations"][0]["contract_reads"][0]["evidence"].update(path="src/gone.ts"))
        self.assertEqual(run(broken).counts[ABSENT], 1)
        broken = edited(lambda r: r["backend_calls"][0]["evidence"][0].update(ref=OTHER))
        self.assertEqual(run(broken).counts[ABSENT], 1)

    def test_an_object_that_looks_like_evidence_and_lacks_a_key_is_a_problem_not_skipped(self):
        for key in ("ref", "detail"):
            report = edited(lambda r: r["backend_calls"][0]["evidence"][0].pop(key))
            problems = []
            audit.collect(report, problems)
            self.assertEqual(len(problems), 1, key)
            self.assertIn("looks like evidence and lacks", problems[0])
            self.assertIn(key, problems[0])
            self.assertFalse(run(report).passed())

    def test_a_call_site_or_a_contract_read_with_a_repo_a_path_and_a_line_of_its_own_is_not_evidence(self):
        problems = []
        cited = audit.collect({"call_sites": [{"repo": "a", "path": "p", "line": 3, "symbol": "s", "evidence": ev("src/c/Badge.tsx", 1)}]}, problems)
        self.assertEqual((problems, len(cited)), ([], 1))

    def test_the_audit_and_the_evidence_readers_share_one_walker(self):
        from reachmap import payload

        report = mini.mini_report()
        found = [(site, obj["path"], obj["line"]) for site, obj, complete in payload.evidence_walk(report) if complete]
        self.assertEqual(found, [(c.site, c.path, c.line) for c in audit.collect(report, [])])
        self.assertEqual(len(list(payload.evidence_in(report))), len(found))


class Records(unittest.TestCase):
    def test_a_blocking_record_of_the_analysis_is_no_concern_of_the_audit(self):
        """`routes_unmatched` and every other incompleteness record say what the analysis could not establish; the audit reads citations."""
        report = edited(lambda r: r["incompleteness"].append({"kind": "routes_unmatched", "subject": "acme/web", "not_established": "x", "detail": "d", "blocks_selection": True}))
        result = run(report)
        self.assertTrue(result.passed())
        self.assertEqual(result.counts[OK], 4)


class NoAnswer(unittest.TestCase):
    def test_a_reader_that_gives_no_answer_for_a_path_never_makes_it_ok(self):
        class Silent:
            def read(self, repo, ref, paths):
                return {}

        result = audit.audit_report(mini.mini_report(), Silent(), "r.json")
        self.assertEqual(result.counts, {OK: 0, MISMATCH: 0, ABSENT: 0, UNREADABLE: 4})
        self.assertFalse(result.passed())
        self.assertIn("gave no answer", result.failures[0].found)


if __name__ == "__main__":
    unittest.main()
