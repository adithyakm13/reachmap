"""The reader's view of a report (M10): the sections in the order a QA reader needs them, what each says and what it never says.
The view is built from the report JSON alone (`reachmap.report.layout`, from `report.model` and `report.author`); the text of the
Markdown rendering is what most of these read, because that is what a person sees."""

import re
import shlex
import unittest

from reachmap import findings, report, wording
from reachmap.report import author, blocks, layout, model
from tests import support_report as sr

DASHES = re.compile("[\u2014\u2013\u2192\u201c\u201d\u2018\u2019]|->")
HEADINGS = ["Areas to test", "Findings", "Branch matrix", "Cross-repository reach", "Backend calls", "Suggested, not verified coverage", "For the author"]


def md(data, **kw):
    return report.render(data, "md", **kw)


def ids(data, **kw):
    return [s.id for s in layout.build(data, **kw).sections()]


def unblock(data):
    for rec in data["incompleteness"]:
        rec["blocks_selection"] = False
    return data


class Order(unittest.TestCase):
    def test_a_run_with_a_blocking_record_puts_what_it_could_not_establish_before_the_areas(self):
        self.assertEqual(ids(sr.fixture("complete")), ["header", "records", "areas", "findings", "matrix", "cross", "backend", "suggested", "author"])

    def test_without_one_it_comes_after_the_findings(self):
        self.assertEqual(ids(unblock(sr.fixture("complete"))), ["header", "areas", "findings", "records", "matrix", "cross", "backend", "suggested", "author"])

    def test_the_headings_are_the_ones_the_brief_names_and_in_order(self):
        text = md(sr.fixture("complete"))
        headings = re.findall(r"^## (.+)$", text, re.M)
        self.assertEqual(headings, ["What the run could not establish", *HEADINGS])
        self.assertTrue(text.startswith("# "))

    def test_a_failed_or_timed_out_run_shows_the_header_and_the_records_only(self):
        for name in ("failed", "timed_out"):
            with self.subTest(name):
                data = sr.fixture(name)
                self.assertEqual(ids(data), ["header", "records"])
                text = md(data)
                self.assertIn("No findings are shown because the run did not complete.", text)
                for heading in HEADINGS:
                    self.assertNotIn(heading, text)
                self.assertNotIn("must test", text.lower())
                self.assertNotRegex(text, r"\b0 (areas|findings)\b")
                self.assertIn(data["incompleteness"][0]["not_established"], text)

    def test_the_failed_sentence_passes_the_wording_check(self):
        self.assertEqual(wording.check("No findings are shown because the run did not complete."), [])

    def test_a_failed_run_with_counts_or_lists_in_the_file_still_shows_none_of_them(self):
        data = sr.fixture("failed")
        data["counts"] = {"areas": 0, "must_test": 0, "sample": 0, "candidates": 0, "findings": 0, "exclusions": 0, "records": 1, "blocking_records": 1}
        self.assertEqual(ids(data), ["header", "records"])
        text = md(data)
        self.assertNotIn("| What | Number |", text)
        self.assertNotIn("Counts", text)

    def test_the_author_view_is_the_header_and_the_author_section(self):
        self.assertEqual(ids(sr.fixture("complete"), view="author"), ["header", "author"])
        self.assertEqual(ids(sr.fixture("failed"), view="author"), ["header", "records"])
        with self.assertRaises(ValueError):
            layout.build(sr.fixture("complete"), view="nonsense")

    def test_mapped_tests_have_a_section_of_their_own_and_only_when_there_are_some(self):
        self.assertNotIn("mapped", ids(sr.fixture("complete")))
        data = sr.fixture("complete")
        data["mapped_tests"] = [{"area_id": "a-0123456789abcdef", "case_id": "7", "title": "Invoice list loads", "source": "qase", "mapping": "areas.Billing.suites=12"}]
        self.assertEqual(ids(data), ["header", "records", "areas", "findings", "matrix", "cross", "backend", "mapped", "suggested", "author"])


class Header(unittest.TestCase):
    def test_the_header_names_the_change_the_pins_and_the_tool(self):
        text = md(sr.fixture("complete"))
        self.assertIn("acme/api", text)
        self.assertIn("compare main...topic", text)
        self.assertIn("b2" * 6, text)  # the head, short
        self.assertIn("b2" * 20, text)  # and whole, in a block that opens
        self.assertIn("d4" * 20, text)  # the pre-change commit
        self.assertIn("0.0.0", text)

    def test_an_incomplete_run_says_so_at_the_top_with_the_number_of_reasons(self):
        text = md(sr.fixture("complete"))
        top = text.split("\n## ")[0]
        self.assertIn("did not establish everything", top)
        self.assertRegex(top, r"2 reason")

    def test_a_whole_run_never_says_it_is_complete(self):
        text = md(sr.real("--repo", "acme/web", "--compare", "main...scenario/s01-shared-component"))
        self.assertNotIn("complete", text.lower())
        self.assertNotIn("did not establish everything", text)

    def test_the_counts_come_from_the_report_and_candidates_are_apart(self):
        text = md(sr.fixture("complete"))
        self.assertRegex(text, r"\| Areas to test \| 3 \|")
        self.assertRegex(text, r"\| Candidates, matched by name \| 1 \|")
        self.assertRegex(text, r"\| Findings \| 10 \|")

    def test_no_output_says_safe_or_that_nothing_needs_testing(self):
        for data in (sr.fixture("complete"), sr.real(*sr.S13), sr.real(*sr.S11)):
            text = re.sub(r"(?s)```.*?```", "", md(data))
            text = re.sub(r"`[^`\n]*`", "", text)
            self.assertEqual(wording.check(text), [])


class Areas(unittest.TestCase):
    def test_areas_come_must_test_then_sample_then_candidates_with_their_own_label(self):
        text = md(sr.fixture("complete"))
        part = text.split("## Areas to test")[1].split("\n## ")[0]
        order = [part.index("### Must test"), part.index("### Sample"), part.index("### Candidates")]
        self.assertEqual(order, sorted(order))
        self.assertIn("candidate, matched by name, not import-backed", part.split("### Candidates")[1])
        self.assertNotIn("candidate, matched by name", part.split("### Candidates")[0])
        self.assertLess(part.index("/billing/invoices"), part.index("GET /api/cases"))
        self.assertLess(part.index("GET /api/cases"), part.index("/cases/archive"))

    def test_an_area_has_its_priority_label_repository_other_mounts_and_chains(self):
        part = md(sr.fixture("complete")).split("## Areas to test")[1].split("\n## ")[0]
        self.assertIn("`/billing/invoices`", part)
        self.assertIn("`acme/web`", part)
        self.assertIn("/cases/:caseId/invoices", part)  # also mounted
        self.assertIn("2 chains", part)
        self.assertIn("src/modules/billing/pages/InvoiceListPage.tsx:1", part)

    def test_the_completeness_of_an_areas_chain_is_said_when_it_is_not_whole(self):
        part = md(sr.fixture("complete")).split("## Areas to test")[1].split("\n## ")[0]
        self.assertIn("src/modules/legacy/routes.tsx could not be parsed", part)
        self.assertEqual(part.count("could not be parsed"), 1)  # only the area whose read it was

    def test_every_area_cites_its_findings_by_reference(self):
        part = md(sr.fixture("complete")).split("## Areas to test")[1].split("\n## ")[0]
        self.assertIn("F1", part)
        self.assertIn("F2", part)

    def test_zero_areas_say_so_without_claiming_anything(self):
        data = sr.real("--repo", "acme/web", "--compare", "main...scenario/s02-hub-cutoff")
        part = md(data).split("## Areas to test")[1].split("\n## ")[0]
        self.assertIn("No areas reached by this change were found; see the report for what was examined.", part)


class Findings(unittest.TestCase):
    def sec(self):
        return md(sr.fixture("complete")).split("## Findings")[1].split("\n## ")[0]

    def test_findings_are_grouped_by_kind_in_a_fixed_order_and_an_unknown_kind_is_kept(self):
        part = self.sec()
        groups = re.findall(r"^### (.+)$", part, re.M)
        self.assertEqual(len(groups), len(set(groups)))
        kinds = [g for g in groups if g != "Not reached (with evidence)"]
        self.assertEqual(kinds[:2], ["direct", "cross-repo"])
        self.assertIn("custom-kind", kinds)
        self.assertEqual(groups[-1], "Not reached (with evidence)")

    def test_exclusions_are_in_a_group_of_their_own_with_their_kind(self):
        part = self.sec()
        excl = part.split("### Not reached (with evidence)")[1]
        self.assertIn("SettlementsPage.tsx", excl)
        self.assertIn("guard-excluded", excl)
        self.assertNotIn("SettlementsPage.tsx", part.split("### Not reached (with evidence)")[0])

    def test_risk_and_confidence_are_two_separate_labelled_fields_never_combined(self):
        part = self.sec()
        first = part.split("**F1**")[1].split("\n- **F")[0]
        self.assertRegex(first, r"\*\*Risk:\*\* medium")
        self.assertRegex(first, r"\*\*Confidence:\*\* high")
        self.assertIn("**Confidence basis:**", first)
        for line in part.splitlines():
            self.assertFalse("Risk:" in line and "Confidence:" in line, line)

    def test_evidence_has_repository_path_line_the_verbatim_line_and_a_short_reference(self):
        first = self.sec().split("**F1**")[1].split("\n- **F")[0]
        self.assertIn("`acme/api`", first)
        self.assertIn("`services/billing/app/services/totals.py:5`", first)
        self.assertIn("def round_total(amount):", first)
        self.assertIn("`" + "b2" * 6 + "`", first)

    def test_findings_are_numbered_by_their_place_in_the_report(self):
        data = sr.fixture("complete")
        part = self.sec()
        for i, f in enumerate(data["findings"], 1):
            self.assertIn(f"**F{i}**", part)


class Matrix(unittest.TestCase):
    def sec(self, data=None):
        return md(data or sr.fixture("complete")).split("## Branch matrix")[1].split("\n## ")[0]

    def test_one_row_per_branch_with_state_and_lines_found(self):
        part = self.sec()
        rows = [ln for ln in part.splitlines() if ln.startswith("| `")]
        self.assertEqual(len(rows), 4)
        self.assertRegex(rows[0], r"`main`.*pre_change.*\| 0 \|")
        self.assertRegex(rows[1], r"`release/1\.x`.*present.*\| 2 \|")
        self.assertRegex(rows[3], r"`release/old`.*unreadable")

    def test_the_evidence_lines_and_the_detail_of_a_row_are_shown_and_an_unreadable_row_says_why(self):
        part = self.sec()
        self.assertIn("return amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)", part)
        self.assertIn("the fetch timed out", part)

    def test_a_branch_missing_finding_is_linked_from_the_row_it_is_about(self):
        part = self.sec()
        main = part.split("`release/1.x`")[0]
        self.assertIn("F7", main)  # branch-missing is the seventh finding; it names main as the missing branch

    def test_a_run_with_no_branch_row_says_none_were_read(self):
        data = sr.fixture("complete")
        data["branches"] = []
        self.assertIn("No branch of the repository was compared.", self.sec(data))


class CrossRepo(unittest.TestCase):
    def sec(self, data=None):
        return md(data or sr.fixture("complete")).split("## Cross-repository reach")[1].split("\n## ")[0]

    def test_each_kind_of_edge_has_its_part(self):
        part = self.sec()
        for heading in ("### Package edges", "### HTTP edges", "### Contract edges", "### Not followed"):
            self.assertIn(heading, part)

    def test_pins_per_branch_and_adoption_are_listed(self):
        part = self.sec()
        self.assertIn("`release/1.2`", part)
        self.assertIn("1.2.0", part)
        self.assertIn("adopted", part)
        self.assertIn("on adoption", part)
        self.assertIn("undecided", part)

    def test_http_matches_say_what_was_removed_and_what_is_a_candidate(self):
        part = self.sec().split("### HTTP edges")[1].split("\n### ")[0]
        self.assertIn("removed by the change", part)
        self.assertIn("candidate, matched by name, not import-backed", part)

    def test_contract_reads_by_name_and_candidates_are_apart_from_typed_reads(self):
        part = self.sec().split("### Contract edges")[1].split("\n### ")[0]
        self.assertIn("Invoice.settlement_reference", part)
        typed, rest = part.split("#### Candidates", 1)
        self.assertIn("typed", typed)
        self.assertIn("worker/tasks.py", rest)
        self.assertNotIn("worker/tasks.py", typed)

    def test_what_was_not_followed_is_named(self):
        part = self.sec().split("### Not followed")[1]
        self.assertIn("edge:event:acme/api:acme/worker", part)
        self.assertIn("acme/worker", part)

    def test_a_change_with_no_edge_says_what_was_looked_at(self):
        part = self.sec(sr.real("--repo", "acme/web", "--compare", "main...scenario/s01-shared-component"))
        self.assertIn("No other repository was reached by this change", part)


class Backend(unittest.TestCase):
    def test_a_backend_call_shows_the_client_line_the_route_the_handler_and_the_modules(self):
        part = md(sr.fixture("complete")).split("## Backend calls")[1].split("\n## ")[0]
        self.assertIn("apiGet<InvoiceSummary>(INVOICES_URL)", part)
        self.assertIn("GET", part)
        self.assertIn("/api/billing/invoices", part)
        self.assertIn("libs/common/acme_common/money.py", part)
        self.assertIn("did not parse", part)  # the modules were not all listed

    def test_none_says_none(self):
        data = sr.fixture("complete")
        data["backend_calls"] = []
        part = md(data).split("## Backend calls")[1].split("\n## ")[0]
        self.assertIn("No backend call was reached by this change.", part)


class Suggested(unittest.TestCase):
    def sec(self, data):
        return md(data).split("## Suggested, not verified coverage")[1].split("\n## ")[0]

    def test_a_run_that_read_no_test_source_says_so_and_never_none_suggested(self):
        part = self.sec(sr.fixture("complete"))
        self.assertIn("No test source was read by this run.", part)
        self.assertNotIn("None suggested.", part)

    def test_an_unconfigured_source_uses_its_record(self):
        data = sr.fixture("complete")
        data["incompleteness"].append({"kind": "source_unconfigured", "subject": "qase", "blocks_selection": False,
                                       "not_established": "Suggestions from qase were not looked up, because the source is not configured", "detail": ""})
        part = self.sec(data)
        self.assertIn("No test source is configured.", part)
        self.assertIn("Suggestions from qase were not looked up", part)

    def test_none_suggested_only_when_the_source_was_read(self):
        data = sr.fixture("complete")
        data["inputs"]["test_source_read"] = True
        self.assertIn("None suggested.", self.sec(data))

    def test_an_unavailable_source_is_not_none(self):
        data = sr.fixture("complete")
        data["incompleteness"].append({"kind": "source_unavailable", "subject": "qase", "blocks_selection": True,
                                       "not_established": "Suggestions from qase were not looked up, because the source did not answer", "detail": "timeout"})
        part = self.sec(data)
        self.assertNotIn("None suggested.", part)
        self.assertIn("did not answer", part)

    def test_suggestions_are_listed_as_places_to_look_and_never_with_the_mapped_tests(self):
        data = sr.fixture("complete")
        data["suggestions"] = [{"source": "qase", "case_id": "42", "title": "Open the invoice list", "matched_words": ["invoice", "list"]}]
        data["mapped_tests"] = [{"area_id": "a-0123456789abcdef", "case_id": "7", "title": "Invoice list loads", "source": "qase", "mapping": "areas.Billing.suites=12"}]
        text = md(data)
        sug = self.sec(data)
        self.assertIn("Open the invoice list", sug)
        self.assertIn("invoice", sug)
        self.assertNotIn("Invoice list loads", sug)
        mapped = text.split("## Mapped tests")[1].split("\n## ")[0]
        self.assertIn("Invoice list loads", mapped)
        self.assertNotIn("Open the invoice list", mapped)
        self.assertLess(text.index("## Mapped tests"), text.index("## Suggested, not verified coverage"))


class Author(unittest.TestCase):
    def setUp(self):
        self.data = sr.fixture("complete")
        self.view = author.build(self.data)

    def test_other_repositories_reached_and_how(self):
        by = {r.repo: r.edges for r in self.view.repos}
        self.assertEqual(sorted(by), ["acme/web", "acme/worker"])
        self.assertIn("http", by["acme/web"])
        self.assertIn("package", by["acme/worker"])
        self.assertNotIn("acme/api", by)

    def test_files_beyond_the_diff_are_the_cited_files_of_the_reach_that_the_change_did_not_touch(self):
        files = {(f.repo, f.path) for f in self.view.files}
        self.assertIn(("acme/web", "src/modules/billing/routes.tsx"), files)
        self.assertIn(("acme/api", "services/billing/app/routers/invoices.py"), files)
        self.assertNotIn(("acme/api", "services/billing/app/services/totals.py"), files)  # that is the diff
        self.assertNotIn(("acme/web", "src/modules/billing/pages/SettlementsPage.tsx"), files)  # excluded: not reached
        self.assertNotIn(("acme/api", "services/billing/app/jobs.py"), files)
        self.assertEqual(sorted(files), [(f.repo, f.path) for f in self.view.files])

    def test_other_branches_come_from_the_matrix_with_the_ones_without_the_change_first(self):
        states = [(b.branch, b.state) for b in self.view.branches]
        self.assertEqual(len(states), 4)
        self.assertEqual({s for _, s in states[:3]}, {"pre_change", "partial", "unreadable"})
        self.assertEqual(states[3], ("release/1.x", "present"))

    def test_a_unit_test_command_is_shown_whole_and_without_one_the_quoted_paths(self):
        by = {t.repo: t for t in self.view.tests}
        self.assertEqual(by["acme/api"].command, "pytest services/billing/tests/test_totals.py")
        self.assertIsNone(by["acme/web"].command)
        self.assertEqual(by["acme/web"].quoted, (shlex.quote("src/modules/billing/__tests__/api it's.test.ts"),))
        self.assertEqual(by["acme/web"].text, shlex.quote("src/modules/billing/__tests__/api it's.test.ts"))
        self.assertEqual(by["acme/api"].text, "pytest services/billing/tests/test_totals.py")

    def test_the_author_section_says_each_part_and_each_command_is_a_block_of_its_own(self):
        text = md(self.data)
        part = text.split("## For the author")[1]
        for heading in ("### Other repositories reached", "### Files beyond the diff", "### Other branches", "### Unit tests to run"):
            self.assertIn(heading, part)
        self.assertIn("```\npytest services/billing/tests/test_totals.py\n```", part)
        self.assertIn("No test command is configured for this repository.", part)

    def test_a_report_with_none_of_it_says_so(self):
        data = sr.real("--repo", "acme/web", "--compare", "main...scenario/s02-hub-cutoff")
        part = md(data).split("## For the author")[1]
        self.assertIn("No other repository was reached by this change.", part)
        self.assertIn("No unit test was found among the files this change reaches.", part)


class Wording(unittest.TestCase):
    REPORTS = {
        "complete": lambda: sr.fixture("complete"), "hostile": lambda: sr.fixture("hostile"), "failed": lambda: sr.fixture("failed"),
        "timed_out": lambda: sr.fixture("timed_out"), "s11": lambda: sr.real(*sr.S11), "s13": lambda: sr.real(*sr.S13),
        "s15": lambda: sr.real("--repo", "acme/api", "--compare", "main...scenario/s15-python-callers"),
        "s16": lambda: sr.real("--repo", "acme/web", "--compare", "main...scenario/s16-no-merge-base"),
    }

    def test_every_string_the_view_makes_passes_the_wording_check_and_has_no_dash_or_arrow(self):
        for name, make in self.REPORTS.items():
            for view in ("full", "author"):
                doc = layout.build(make(), view=view)
                made = [s for s, content in blocks.strings(doc) if not content]
                self.assertTrue(made)
                for s in made:
                    with self.subTest(report=name, view=view, text=s[:40]):
                        self.assertEqual(wording.check(s), [])
                        self.assertIsNone(DASHES.search(s))

    def test_content_is_never_checked_or_altered(self):
        data = sr.fixture("complete")
        data["findings"][0]["evidence"][0]["detail"] = "    return is_safe(x)  # safe to release, nothing to test"
        data["findings"][0]["title"] = "The route /safe-mode is fully covered"
        data["areas"][0]["label"] = "GET /safe-mode"
        data["incompleteness"][1]["detail"] = "no need to test"
        text = md(data)
        for s in ("return is_safe(x)  # safe to release, nothing to test", "The route /safe-mode is fully covered", "GET /safe-mode", "no need to test"):
            self.assertIn(s, text)
        self.assertEqual(wording.check("safe"), ["safe"])  # and the check is on: the same word in our own text would fail

    def test_a_banned_phrase_in_what_the_view_says_is_refused_while_it_is_made(self):
        with self.assertRaises(Exception):
            blocks.say("this release is safe")

    def test_the_content_marker_is_what_tells_content_from_what_the_view_says(self):
        doc = layout.build(sr.fixture("complete"))
        contents = [s for s, content in blocks.strings(doc) if content]
        self.assertIn("GET /api/cases", contents)
        self.assertTrue(all(isinstance(s, str) for s in contents))


class ViewModel(unittest.TestCase):
    def test_the_kinds_the_view_orders_are_the_kinds_findings_builds(self):
        self.assertTrue(set(findings.TABLE_KINDS) <= set(model.KIND_ORDER) | set(model.EXCLUSION_KINDS))

    def test_a_document_with_a_banned_phrase_in_what_the_tool_says_is_refused_but_not_one_in_content(self):
        from reachmap.errors import WordingError
        bad = blocks.Document(("title",), (blocks.Para(("this release is safe",)),))
        with self.assertRaises(WordingError):
            blocks.require_clean(bad)
        fine = blocks.Document(("title",), (blocks.Para((blocks.content("this release is safe"),)),))
        self.assertIs(blocks.require_clean(fine), fine)

    def test_the_view_model_reads_only_the_report(self):
        view = model.build(sr.fixture("complete"))
        self.assertEqual(view.status, "completed")
        self.assertEqual([a.label for a in view.must_test], ["GET /api/billing/invoices/{invoice_id}", "/billing/invoices"])
        self.assertEqual([a.label for a in view.sample], ["GET /api/cases"])
        self.assertEqual([a.label for a in view.candidates], ["/cases/archive"])
        self.assertEqual(len(view.exclusions), 2)
        self.assertEqual([g.kind for g in view.groups][:2], ["direct", "cross-repo"])

    def test_a_finding_keeps_its_number_whatever_the_grouping(self):
        view = model.build(sr.fixture("complete"))
        by = {f.number: f.kind for g in view.groups for f in g.findings}
        by.update({f.number: f.kind for f in view.exclusions})
        self.assertEqual(sorted(by), list(range(1, 11)))
        self.assertEqual(by[7], "branch-missing")

    def test_an_area_is_matched_to_its_reach_for_the_completeness_of_its_chain(self):
        view = model.build(sr.fixture("complete"))
        web = [a for a in view.must_test if a.label == "/billing/invoices"][0]
        self.assertFalse(web.complete)
        self.assertEqual(web.reasons, ("src/modules/legacy/routes.tsx could not be parsed",))
        api = view.must_test[0]
        self.assertTrue(api.complete)
        self.assertEqual(api.findings, (1,))

    def test_content_is_marked_and_a_lone_surrogate_cannot_get_out_of_it(self):
        view = model.build(sr.fixture("hostile"))
        self.assertIsInstance(view.header.repo, blocks.Content)
        self.assertIsInstance(view.must_test[0].label, blocks.Content)


class Robustness(unittest.TestCase):
    """A report that passes the check renders whatever the deep payload of its propagation holds, and one that does not is a ReportError:
    every key under the parts of the fixture is removed or given another type in turn."""

    @staticmethod
    def paths(obj, prefix=()):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield prefix + (k,)
                yield from Robustness.paths(v, prefix + (k,))
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                yield prefix + (i,)
                yield from Robustness.paths(v, prefix + (i,))

    def mutate(self, top, values):
        import copy
        base = sr.fixture("complete")
        tried = 0
        for p in self.paths(base[top]):
            for value, delete in [(v, False) for v in values] + [(None, True)]:
                data = copy.deepcopy(base)
                target = data[top]
                for k in p[:-1]:
                    target = target[k]
                if delete:
                    if not isinstance(target, dict):
                        continue
                    del target[p[-1]]
                else:
                    target[p[-1]] = value
                tried += 1
                try:
                    md(data)
                except report.ReportError:
                    pass  # refused with a message: fine
        return tried

    def test_the_deep_payload_never_crashes_a_renderer(self):
        self.assertGreater(self.mutate("propagations", (None, 3, "x")) + self.mutate("backend_calls", (None, 3, "x")), 1000)

    def test_the_parts_the_check_reads_are_refused_not_crashed_on(self):
        total = sum(self.mutate(top, (None, 3, "x", [], {})) for top in ("findings", "areas", "branches", "incompleteness", "change", "notes", "inputs", "tool"))
        self.assertGreater(total, 1000)


class NothingFromTheReportIsTheToolsOwnText(unittest.TestCase):
    """Every string of a report is content. A spy on the wording check marks the strings of a report and fails if a marked one is
    checked as if the tool had said it (what the first version did with the kind of an edge)."""

    REPORTS = {
        "complete": lambda: sr.fixture("complete"), "hostile": lambda: sr.fixture("hostile"), "failed": lambda: sr.fixture("failed"),
        "timed_out": lambda: sr.fixture("timed_out"), "s11": lambda: sr.real(*sr.S11), "s13": lambda: sr.real(*sr.S13),
        "s15": lambda: sr.real("--repo", "acme/api", "--compare", "main...scenario/s15-python-callers"),
        "real hostile run": lambda: sr.hostile_run(),
    }

    def test_no_marked_string_reaches_the_wording_check(self):
        from unittest import mock
        reached = []
        real = wording.require_clean

        def spy(text, field):
            if "ZQX" in text:
                reached.append(text[:100])
            return real(text, field)

        for name, make in self.REPORTS.items():
            data = sr.mark(make())
            for view in ("full", "author"):
                with self.subTest(report=name, view=view), mock.patch.object(wording, "require_clean", spy):
                    layout.build(data, view=view)
        self.assertEqual(reached, [])

    def test_a_report_with_hostile_values_in_every_field_renders_and_never_raises_anything_but_a_report_error(self):
        for name, make in self.REPORTS.items():
            data = sr.hostile_copy(make())
            for view in ("full", "author"):
                with self.subTest(report=name, view=view):
                    text = md(data, view=view)
                    text.encode("utf-8")
                    report.render(data, "html", view=view).encode("utf-8")

    def test_the_kinds_of_an_edge_are_fixed_words_when_known_and_content_when_not(self):
        data = sr.fixture("complete")
        view = author.build(data)
        known = [e for r in view.repos for e in r.edges]
        self.assertTrue(all(not isinstance(e, blocks.Content) for e in known if e in ("http", "package", "contract")))
        for hop_kind in ("safe", "x\ud800", "\x1b[31m", "\u202e"):
            data = sr.fixture("complete")
            data["propagations"][0]["hops"][1]["kind"] = hop_kind
            text = md(data)
            self.assertEqual(wording.check(re.sub(r"`[^`\n]*`", "", re.sub(r"(?s)```.*?```", "", text))), [])
            self.assertNotRegex(text, "[\x00-\x08\x0b-\x1f\u202a-\u202e\ud800-\udfff]")
            report.render(data, "html").encode("utf-8")
            edges = [e for r in author.build(data).repos for e in r.edges]
            self.assertTrue(any(isinstance(e, blocks.Content) and str(e) == blocks.content(hop_kind) for e in edges), hop_kind)

    def test_a_hostile_enumeration_the_layout_needs_is_a_report_error_never_a_traceback(self):
        for key, where in (("priority", ("areas", 0)), ("risk", ("findings", 0)), ("confidence", ("findings", 0))):
            data = sr.fixture("complete")
            data[where[0]][where[1]][key] = "safe </script>"
            with self.subTest(key), self.assertRaises(report.ReportError):
                report.render(data, "md")
        data = sr.fixture("complete")
        data["status"] = "safe"
        with self.assertRaises(report.ReportError):
            report.render(data, "html")


class Facts(unittest.TestCase):
    """What the report holds about the change and its reach is shown (nothing the view reads is left unused)."""

    def test_the_pull_request_the_commits_and_how_the_change_was_found(self):
        text = md(sr.fixture("complete"))
        head = text.split("\n## ")[0]
        self.assertIn("**Pull request:** #12 Round invoice totals half up (merged), from `fix/rounding` into `main`", head)
        self.assertNotIn("Fixes the rounding", text)  # the body of a pull request is not part of the report
        self.assertNotIn("host.invalid", text)  # nor is a link
        self.assertIn("merge base of main and topic", head)  # how the pre-change version was found

    def test_a_contract_read_names_its_consumer_adoption_and_form(self):
        part = md(sr.fixture("complete")).split("### Contract edges")[1].split("\n### ")[0]
        typed = part.split("#### Candidates")[0]
        self.assertRegex(typed, r"`Invoice\.settlement_reference` read in `reference_for` of `acme/api` on `main`, pin `1\.10\.0`.*will be exposed on adoption.*`attribute`")
        self.assertIn("`acme/worker`", part.split("#### Candidates")[1])

    def test_the_matrix_shows_the_short_commit_of_every_row(self):
        part = md(sr.fixture("complete")).split("## Branch matrix")[1].split("\n## ")[0]
        for short in ("b2" * 6, "c3" * 6, "d4" * 6):
            self.assertIn(f"`{short}`", part)

    def test_a_failed_or_timed_out_run_keeps_its_notes_and_the_facts_of_the_change(self):
        for name in ("failed", "timed_out"):
            data = sr.fixture(name)
            data["notes"] = ["main is left out", "release/old was read at its tip"]
            data["change"]["file_count"] = 3
            text = md(data)
            with self.subTest(name):
                self.assertIn("release/old was read at its tip", text)
                self.assertEqual(ids(data), ["header", "records"])
                self.assertIn("**Files in the change:** 3", text)  # a fact about the change, not a count of what the analysis found
                self.assertNotIn("| What | Number |", text)


if __name__ == "__main__":
    unittest.main()
