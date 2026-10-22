"""A proposed file (M14, context rule 4.7): the one model claim the framework checks. It is verified only when the file exists at a revision the
report read, read through the git layer, and names the change by a distinctive name that is in code, not in a comment or a string. Either way it
stays a note."""

import json
import unittest

from reachmap import notes
from reachmap.model import ReadResult
from tests import support_cli, support_mcp as mc
from tests import scenario_support as ss

S15 = ("--repo", "acme/api", "--compare", "main...scenario/s15-python-callers")
S18 = ("--repo", "acme/contracts", "--compare", "main...scenario/s18-limit-comment")
S19 = ("--repo", "acme/web", "--compare", "main...scenario/s19-generic-name")
SHA_A = "a" * 40
SHA_B = "b" * 40


def synthetic(*, passes=(), contract_changes=(), hops=(), consumers=(), reads=(), head=SHA_A, repo="acme/api", branches=()):
    return {
        "status": "completed", "ref": head, "change": {"repo": repo, "head_sha": head, "pre_change_sha": SHA_B}, "branches": list(branches),
        "propagations": [{"passes": list(passes), "hops": list(hops), "consumers": list(consumers), "contract_changes": list(contract_changes), "contract_reads": list(reads)}],
    }


def a_pass(repo, path, names, *, depth=0, sha=SHA_A, not_distinctive=(), unreadable=(), candidate=False):
    symbols = [{"name": n, "change": "modified", "path": path, "repo": repo} for n in names]
    impact = {"path": path, "symbols": symbols, "not_distinctive": list(not_distinctive), "names_unreadable": list(unreadable)}
    return {"repo": repo, "depth": depth, "sha": sha, "candidate": candidate, "analysis": {"impacts": [impact], "files": []}}


class Names(unittest.TestCase):
    def test_the_changed_symbols_of_the_first_pass_are_the_names(self):
        n = notes.changed_names(synthetic(passes=[a_pass("acme/api", "m.py", ["format_cents", "Money.round"])]))
        self.assertEqual(n.distinct, {"format_cents", "round"})
        self.assertEqual(n.rejected, set())

    def test_a_python_symbol_is_known_by_its_leaf_and_a_javascript_one_by_its_whole_name(self):
        n = notes.changed_names(synthetic(passes=[a_pass("acme/web", "src/a.ts", ["Box.open"])]))
        self.assertEqual(n.distinct, {"Box.open"})

    def test_the_module_symbol_and_a_dunder_are_never_names(self):
        n = notes.changed_names(synthetic(passes=[a_pass("acme/api", "m.py", ["<module>", "Money.__init__", "__all__"])]))
        self.assertEqual(n.distinct, set())

    def test_a_name_the_analysis_found_too_common_or_could_not_count_is_rejected_not_distinct(self):
        n = notes.changed_names(synthetic(passes=[a_pass("acme/api", "m.py", ["mapStateToProps", "format", "unique"], not_distinctive=["mapStateToProps"], unreadable=["format"])]))
        self.assertEqual(n.distinct, {"unique"})
        self.assertEqual(n.rejected, {"mapStateToProps", "format"})

    def test_a_symbol_of_a_later_pass_is_not_a_name_to_link_code_by(self):
        """Passes after the first follow seed lines into another repository: their names were not counted for distinctiveness."""
        n = notes.changed_names(synthetic(passes=[a_pass("acme/api", "m.py", ["first"]), a_pass("acme/web", "a.ts", ["fetchInvoice"], depth=1), a_pass("acme/web", "b.ts", ["byName"], candidate=True)]))
        self.assertEqual(n.distinct, {"first"})

    def test_a_contract_field_the_analysis_counted_as_distinctive_is_a_name(self):
        change = {"name": "limit", "symbol": "Schedule.limit", "distinct": True, "counted": True, "definitions": 1, "repo": "acme/contracts"}
        bad = {"name": "id", "symbol": "Schedule.id", "distinct": False, "counted": True, "definitions": 9, "repo": "acme/contracts"}
        uncounted = {"name": "x", "symbol": "Schedule.x", "distinct": True, "counted": False, "definitions": None, "repo": "acme/contracts"}
        n = notes.changed_names(synthetic(contract_changes=[change, bad, uncounted]))
        self.assertEqual(n.distinct, {"limit"})
        self.assertEqual(n.rejected, {"id", "x"})

    def test_a_report_that_is_odd_gives_no_name_and_no_exception(self):
        for report in ({}, {"propagations": None}, {"propagations": [None]}, {"propagations": [{"passes": [None, {"depth": 0, "analysis": 3}]}]},
                       {"propagations": [{"passes": [{"depth": 0, "analysis": {"impacts": [{"symbols": [{"name": 3}, None]}]}}]}]}):
            with self.subTest(report=report):
                self.assertEqual(notes.changed_names(report).distinct, set())


class Revisions(unittest.TestCase):
    def test_the_head_and_the_revisions_the_walk_read_in_each_repository(self):
        r = synthetic(
            passes=[a_pass("acme/api", "m.py", ["f"], sha=SHA_A), a_pass("acme/web", "a.ts", ["g"], depth=1, sha="c" * 40)],
            hops=[{"to_repo": "acme/worker", "pins": [{"repo": "acme/worker", "sha": "d" * 40, "ref": "main", "role": "primary"}]}],
            consumers=[{"repo": "acme/worker", "sha": "e" * 40, "branch": "release/1"}], reads=[{"repo": "acme/web", "sha": "f" * 40}],
            branches=[{"branch": "main", "commit": "1" * 40}])
        self.assertEqual(notes.revisions_read(r, "acme/api"), [SHA_A, "1" * 40])
        self.assertEqual(notes.revisions_read(r, "acme/worker"), ["d" * 40, "e" * 40])
        self.assertEqual(notes.revisions_read(r, "acme/web"), ["c" * 40, "f" * 40])
        self.assertEqual(notes.revisions_read(r, "acme/other"), [])

    def test_only_full_commit_ids_are_revisions_and_there_are_few(self):
        pins = [{"repo": "acme/worker", "sha": f"{i:040x}"} for i in range(1, 30)] + [{"repo": "acme/worker", "sha": "short"}, {"repo": "acme/worker", "sha": 5}]
        got = notes.revisions_read(synthetic(hops=[{"to_repo": "acme/worker", "pins": pins}]), "acme/worker")
        self.assertEqual(len(got), notes.MAX_REVISIONS)
        self.assertTrue(all(len(s) == 40 for s in got))


PY = '''"""format_cents is described here"""
# format_cents in a comment
import os
from acme_common.money import (
    cents_to_decimal,
    format_cents as fmt,
)

NOTE = "format_cents in a string"


def build(rows):
    label = f"{row_total} format_cents"
    return [fmt(r) for r in rows]


class Holder:
    def render(self, item):
        return item.format_cents_total(1)

    def other(self, x):
        return money.format_cents(x)
'''


class CodeNames(unittest.TestCase):
    def lines(self, name, text="", path="x.py"):
        return notes.code_lines(path, text or PY, name)

    def test_an_import_a_call_and_an_attribute_are_code(self):
        self.assertEqual(self.lines("format_cents"), [6, 22])
        self.assertEqual(self.lines("cents_to_decimal"), [5])

    def test_a_comment_a_docstring_and_a_string_are_not(self):
        text = '"""doc: only_here"""\n# only_here\nx = "only_here"\ny = \'only_here\'\n'
        self.assertEqual(notes.code_lines("a.py", text, "only_here"), [])

    def test_the_expression_of_an_f_string_is_code_and_its_words_are_not(self):
        self.assertEqual(notes.code_lines("a.py", 'x = f"words only_here {value}"\n', "only_here"), [])
        self.assertEqual(notes.code_lines("a.py", 'x = f"words {only_here}"\n', "only_here"), [1])

    def test_a_longer_name_is_not_the_name(self):
        self.assertEqual(self.lines("format"), [])
        self.assertEqual(self.lines("item_format_cents"), [])

    def test_a_definition_a_parameter_a_keyword_and_a_target_are_code(self):
        text = "def target(arg):\n    return other(keyword=arg)\n\nclass Klass:\n    attr = 1\n"
        for name, line in (("target", 1), ("arg", 1), ("keyword", 2), ("Klass", 4), ("attr", 5)):
            with self.subTest(name=name):
                self.assertEqual(notes.code_lines("a.py", text, name), [line] if name != "arg" else [1, 2])

    def test_javascript_code_and_javascript_text(self):
        js = ('// formatCents in a comment\n/* formatCents again */\nconst a = "formatCents";\nconst b = `formatCents ${formatCents(1)}`;\n'
              'import { formatCents } from "./money";\nfoo.formatCents();\nconst c = /formatCents/;\n')
        self.assertEqual(notes.code_lines("a.ts", js, "formatCents"), [4, 5, 6])
        self.assertEqual(notes.code_lines("a.jsx", "const x = <Badge label=\"Badge\" />;\n", "Badge"), [1])
        self.assertEqual(notes.code_lines("a.js", "// only_here\nx = 'only_here'\n", "only_here"), [])

    def test_a_python_file_that_does_not_parse_is_unparsed_never_guessed(self):
        with self.assertRaises(notes.Unparsed):
            notes.code_lines("a.py", "def broken(:\n    format_cents\n", "format_cents")
        with self.assertRaises(notes.Unparsed):
            notes.code_lines("a.py", "x = 1\ry = 2\n", "x")

    def test_a_file_of_a_kind_with_no_reader_has_none(self):
        for path in ("README.md", "data.json", "a.txt", "Makefile", "a.PY.bak"):
            with self.subTest(path=path), self.assertRaises(notes.NoReader):
                notes.code_lines(path, "format_cents", "format_cents")

    def test_a_very_deep_source_is_unparsed_and_not_an_exception(self):
        with self.assertRaises(notes.Unparsed):
            notes.code_lines("a.py", "x = " + "(" * 5000 + "1" + ")" * 5000 + "\n", "x")

    def test_at_most_five_lines_are_named(self):
        self.assertEqual(notes.code_lines("a.py", "\n".join(["only_here()"] * 9), "only_here"), [1, 2, 3, 4, 5])


def fake(answers):
    """A reader that answers from a dict of (repo, path, sha) and records what it was asked."""
    asked = []

    def read(repo, path, sha):
        asked.append((repo, path, sha))
        return answers.get((repo, path, sha), ReadResult.absent())

    read.asked = asked
    return read


class Verifying(unittest.TestCase):
    REPORT = synthetic(passes=[a_pass("acme/api", "m.py", ["format_cents"])])

    def verify(self, answers, repo="acme/api", path="s.py", symbol="format_cents", report=None, **more):
        return notes.verify_file(report or self.REPORT, repo, path, symbol, fake(answers) if answers is not None else None, **more)

    def test_a_file_that_names_the_symbol_in_code_at_a_revision_the_report_read_is_verified(self):
        v = self.verify({("acme/api", "s.py", SHA_A): ReadResult.found("from m import format_cents\n")})
        self.assertEqual((v["status"], v["reason"], v["read"], v["sha"], v["symbol"], v["lines"]), ("verified", "verified", "found", SHA_A, "format_cents", [1]))
        self.assertIsNone(v["detail"])

    def test_the_symbol_may_be_given_with_its_class(self):
        report = synthetic(passes=[a_pass("acme/api", "m.py", ["Money.format_cents"])])
        v = self.verify({("acme/api", "s.py", SHA_A): ReadResult.found("format_cents(1)\n")}, symbol="Money.format_cents", report=report)
        self.assertEqual(v["status"], "verified")
        v = self.verify({("acme/api", "s.py", SHA_A): ReadResult.found("format_cents(1)\n")}, symbol="format_cents", report=report)
        self.assertEqual(v["status"], "verified")

    def test_a_qualified_claim_needs_the_qualifier_of_the_changed_symbol(self):
        report = synthetic(passes=[a_pass("acme/api", "m.py", ["Money.format_cents"])])
        answers = {("acme/api", "s.py", SHA_A): ReadResult.found("format_cents(1)\n")}
        self.assertEqual(self.verify(answers, symbol="Money.format_cents", report=report)["status"], "verified")
        for other in ("Other.format_cents", "a.b.format_cents", "Money.Inner.format_cents"):
            v = self.verify(answers, symbol=other, report=report)
            with self.subTest(claim=other):
                self.assertEqual((v["status"], v["reason"], v["read"]), ("not_verified", "symbol_qualifier", "not_attempted"))
        self.assertIn("symbol_qualifier", notes.REASON_WORDS)

    def test_a_contract_field_is_qualified_by_its_model(self):
        change = {"name": "limit", "symbol": "Schedule.limit", "distinct": True, "counted": True, "definitions": 1, "repo": "acme/contracts"}
        report = synthetic(contract_changes=[change], repo="acme/contracts")
        answers = {("acme/contracts", "s.py", SHA_A): ReadResult.found("x.limit\n")}
        self.assertEqual(self.verify(answers, repo="acme/contracts", symbol="Schedule.limit", report=report)["status"], "verified")
        self.assertEqual(self.verify(answers, repo="acme/contracts", symbol="Other.limit", report=report)["reason"], "symbol_qualifier")
        self.assertEqual(self.verify(answers, repo="acme/contracts", symbol="limit", report=report)["status"], "verified")

    def test_a_qualified_claim_of_a_name_that_is_not_distinctive_says_that_first(self):
        report = synthetic(passes=[a_pass("acme/api", "m.py", ["Money.common"], not_distinctive=["common"])])
        self.assertEqual(self.verify({}, symbol="Other.common", report=report)["reason"], "symbol_not_distinctive")

    def test_a_name_only_in_a_comment_or_a_string_is_not_verified(self):
        for text in ("# format_cents\n", "x = 'format_cents'\n", '"""format_cents"""\nx = 1\n'):
            with self.subTest(text=text):
                v = self.verify({("acme/api", "s.py", SHA_A): ReadResult.found(text)})
                self.assertEqual((v["status"], v["reason"], v["read"]), ("not_verified", "not_in_code", "found"))

    def test_a_name_that_is_not_distinctive_is_not_verified_and_nothing_is_read(self):
        report = synthetic(passes=[a_pass("acme/api", "m.py", ["common"], not_distinctive=["common"])])
        read = fake({("acme/api", "s.py", SHA_A): ReadResult.found("common()\n")})
        v = notes.verify_file(report, "acme/api", "s.py", "common", read)
        self.assertEqual((v["status"], v["reason"], v["read"]), ("not_verified", "symbol_not_distinctive", "not_attempted"))
        self.assertEqual(read.asked, [])

    def test_a_symbol_the_change_did_not_touch_is_not_verified(self):
        v = self.verify({("acme/api", "s.py", SHA_A): ReadResult.found("other_name()\n")}, symbol="other_name")
        self.assertEqual((v["status"], v["reason"]), ("not_verified", "symbol_not_changed"))

    def test_an_absent_file_is_not_verified_as_absent(self):
        v = self.verify({})
        self.assertEqual((v["status"], v["reason"], v["read"], v["sha"]), ("not_verified", "file_absent", "absent", None))

    def test_an_unreadable_read_is_not_verified_as_unreadable_never_as_absent(self):
        v = self.verify({("acme/api", "s.py", SHA_A): ReadResult.unreadable("the fetch timed out")})
        self.assertEqual((v["status"], v["reason"], v["read"], v["detail"]), ("not_verified", "file_unreadable", "unreadable", "the fetch timed out"))

    def test_one_unreadable_revision_and_no_revision_with_the_file_is_unreadable_not_absent(self):
        report = synthetic(passes=[a_pass("acme/api", "m.py", ["format_cents"], sha=SHA_A)], branches=[{"branch": "main", "commit": SHA_B}])
        v = self.verify({("acme/api", "s.py", SHA_A): ReadResult.absent(), ("acme/api", "s.py", SHA_B): ReadResult.unreadable("no")}, report=report)
        self.assertEqual((v["reason"], v["read"]), ("file_unreadable", "unreadable"))

    def test_the_first_revision_that_has_the_file_decides_and_each_is_tried_in_order(self):
        report = synthetic(passes=[a_pass("acme/api", "m.py", ["format_cents"], sha=SHA_A)], branches=[{"branch": "main", "commit": SHA_B}])
        read = fake({("acme/api", "s.py", SHA_B): ReadResult.found("format_cents()\n")})
        v = notes.verify_file(report, "acme/api", "s.py", "format_cents", read)
        self.assertEqual((v["status"], v["sha"]), ("verified", SHA_B))
        self.assertEqual(read.asked, [("acme/api", "s.py", SHA_A), ("acme/api", "s.py", SHA_B)])

    def test_a_repository_the_report_never_read_is_not_verified(self):
        v = self.verify({}, repo="acme/elsewhere")
        self.assertEqual((v["reason"], v["read"]), ("repo_not_read", "not_attempted"))

    def test_no_reader_and_an_unknown_repository_are_said(self):
        self.assertEqual(self.verify(None)["reason"], "no_config")
        v = self.verify({}, known=lambda repo: False)
        self.assertEqual((v["reason"], v["read"]), ("unknown_repo", "not_attempted"))

    def test_a_file_that_is_no_code_or_does_not_parse_is_not_verified(self):
        v = self.verify({("acme/api", "README.md", SHA_A): ReadResult.found("format_cents\n")}, path="README.md")
        self.assertEqual((v["reason"], v["read"]), ("no_reader", "found"))
        v = self.verify({("acme/api", "s.py", SHA_A): ReadResult.found("def x(:\n format_cents\n")})
        self.assertEqual((v["reason"], v["read"]), ("unparsed", "found"))

    def test_the_result_has_the_stored_shape_and_every_reason_has_words(self):
        for answers in ({}, {("acme/api", "s.py", SHA_A): ReadResult.found("format_cents()\n")}, {("acme/api", "s.py", SHA_A): ReadResult.unreadable("x" * 900)}):
            v = self.verify(answers)
            with self.subTest(v=v):
                self.assertTrue(notes._verification_ok(v))
                self.assertIn(v["reason"], notes.REASON_WORDS)

    def test_the_detail_of_an_unreadable_read_is_cut(self):
        v = self.verify({("acme/api", "s.py", SHA_A): ReadResult.unreadable("x" * 900)})
        self.assertLessEqual(len(v["detail"]), 500)

    def test_the_claim_is_never_repeated_in_a_reason(self):
        for kwargs in ({"symbol": "<script>alert(1)</script>"}, {"repo": "<img src=x>"}, {"path": "<svg onload=1>"}):
            v = self.verify({}, **kwargs)
            self.assertNotIn("<", json.dumps({k: v[k] for k in ("status", "reason", "read")}))
            self.assertNotIn("<", notes.REASON_WORDS[v["reason"]])


class OnTheFixtures(unittest.TestCase):
    """Against real reports and the real git layer on the M1 fixture repositories."""

    @classmethod
    def setUpClass(cls):
        cls.git = ss.make_git()
        cls.workspace = ss.env().workspace

    def reader(self):
        def read(repo, path, sha):
            return self.git.file_at(self.workspace.repos[repo].remote, path, sha)

        return read

    def verify(self, args, repo, path, symbol):
        report = support_cli.cli_run(*args).report
        return notes.verify_file(report, repo, path, symbol, self.reader(), known=lambda r: r in self.workspace.repos), report

    def test_a_caller_of_the_changed_symbol_is_verified(self):
        v, report = self.verify(S15, "acme/api", "services/billing/app/services/settlement.py", "format_cents")
        self.assertEqual((v["status"], v["reason"], v["read"], v["sha"], v["symbol"]), ("verified", "verified", "found", report["change"]["head_sha"], "format_cents"))
        self.assertEqual(v["lines"][:1], [3])  # the import: `    format_cents,`

    def test_a_caller_in_a_directory_the_change_did_not_touch_is_verified_too(self):
        v, _ = self.verify(S15, "acme/api", "services/cases/app/routers/cases.py", "format_cents")
        self.assertEqual(v["status"], "verified")

    def test_a_file_that_does_not_use_the_symbol_in_code_is_not_verified(self):
        v, _ = self.verify(S15, "acme/api", "services/billing/app/jobs.py", "format_cents")
        self.assertEqual((v["status"], v["reason"], v["read"]), ("not_verified", "not_in_code", "found"))

    def test_a_name_only_in_a_comment_is_not_verified_and_the_definition_is(self):
        v, _ = self.verify(S18, "acme/worker", "worker/tasks/settlement_sync.py", "limit")
        self.assertEqual((v["status"], v["reason"], v["read"]), ("not_verified", "not_in_code", "found"))
        v, report = self.verify(S18, "acme/contracts", "contracts/models/schedule.py", "limit")
        self.assertEqual((v["status"], v["read"], v["sha"]), ("verified", "found", report["change"]["head_sha"]))

    def test_the_revision_of_another_repository_is_the_one_the_walk_pinned(self):
        report = support_cli.cli_run(*S18).report
        pinned = [p["sha"] for h in report["propagations"][0]["hops"] for p in h["pins"] if p["repo"] == "acme/worker"]
        self.assertEqual(notes.revisions_read(report, "acme/worker")[:1], pinned[:1])

    def test_a_generic_name_is_not_verified_and_nothing_is_read(self):
        v, _ = self.verify(S19, "acme/web", "src/modules/cases/legacy/CaseTable.tsx", "mapStateToProps")
        self.assertEqual((v["status"], v["reason"], v["read"]), ("not_verified", "symbol_not_distinctive", "not_attempted"))

    def test_an_absent_file_is_not_verified(self):
        v, _ = self.verify(S15, "acme/api", "services/billing/app/services/nothing_here.py", "format_cents")
        self.assertEqual((v["reason"], v["read"]), ("file_absent", "absent"))

    def test_a_path_that_cannot_be_read_is_unreadable_and_says_so(self):
        for path in ("services/billing/app", "../../etc/passwd", "/etc/passwd"):
            v, _ = self.verify(S15, "acme/api", path, "format_cents")
            with self.subTest(path=path):
                self.assertEqual((v["status"], v["reason"], v["read"]), ("not_verified", "file_unreadable", "unreadable"))
                self.assertTrue(v["detail"])

    def test_a_repository_the_report_did_not_read_is_not_verified(self):
        v, _ = self.verify(S15, "acme/worker", "worker/tasks/settlement_sync.py", "format_cents")
        self.assertEqual((v["reason"], v["read"]), ("repo_not_read", "not_attempted"))

    def test_a_symbol_of_a_following_pass_is_not_a_name_to_propose_by(self):
        v, _ = self.verify(S15, "acme/web", "src/modules/billing/api.ts", "fetchInvoice")
        self.assertEqual(v["reason"], "symbol_not_changed")


if __name__ == "__main__":
    unittest.main()
