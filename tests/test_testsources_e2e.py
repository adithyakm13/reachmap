"""M15 end to end: `reachmap analyse` on the small spec repository with a Qase source (a fake transport serving the recorded
responses), a Playwright source and a Cypress source. The suggestions land in the report, under their areas and under the heading
"Suggested, not verified coverage"; none of them is ever coverage; a source that cannot be read changes the suggestions and nothing
else."""

from __future__ import annotations

import io
import json
import unittest
from pathlib import Path

from reachmap import cli
from reachmap.model import Analysis, coverage_gaps
from tests import support_testsources as st
from tests.fixtures import build
from tests.support_testsources import QASE, QASE_ENV, QASE_ROUTES, Fake

AREAS = "[[test_sources.areas]]\nname = \"Billing\"\nroute_prefixes = [\"/billing\"]\nmodules = [\"billing\"]\nsuites = [10]\n"
QASE_TOML = f'[[test_sources]]\nkind = "qase"\nproject = "ACME"\ntoken_env = "QASE_API_TOKEN"\npage_size = 15\n{AREAS}\n'
SPECS_TOML = '[[test_sources]]\nkind = "playwright"\nrepo = "acme/e2e"\n\n[[test_sources]]\nkind = "cypress"\nrepo = "acme/e2e"\n'
ARGS = ("--repo", "acme/e2e", "--compare", "main...topic/money-rounding")
SCREENS = {"/billing/invoices", "/billing/invoices/:invoiceId", "/billing/settlements", "/cases/:caseId", "/reports"}
_RUNS: dict = {}


class Result:
    def __init__(self, code, out, err, report, text, fake, tmp):
        self.code, self.out, self.err, self.report, self.text, self.fake, self.tmp = code, out, err, report, text, fake, tmp


def run(sources: str, *, env=None, routes=None, key=None, extra=(), clock=None, as_of=None) -> Result:
    """One `reachmap analyse`, once per key for the whole process (a run is deterministic)."""
    key = key or (sources, tuple(sorted((env or QASE_ENV).items())), tuple(sorted(routes or ())), extra, as_of)
    if key in _RUNS:
        return _RUNS[key]
    import atexit
    import tempfile

    from tests import support_git as sg

    e = st.e2e()
    tmp = Path(tempfile.mkdtemp(prefix="reachmap-e2e-run-"))
    atexit.register(sg.remove_tree, tmp)
    cfg = tmp / "reachmap.toml"
    ws_text = f'''workspace = "acme"

[limits]
hops = 3
hub_importers = 50
distinct_max = 10
timeout_s = 900

[repos."acme/e2e"]
host = "git"
remote = "{e.remote}"
language = "typescript"
roles = ["frontend"]
branches = ["main"]
aliases = {{ from = "tsconfig.json" }}
entry_points = {{ kind = "react-router", routes_glob = "src/modules/*/routes.tsx" }}

{sources}
'''
    cfg.write_text(ws_text, encoding="utf-8")
    fake = Fake(routes if routes is not None else QASE_ROUTES)
    out, err = io.StringIO(), io.StringIO()
    args = ["analyse", "--config", str(cfg), *ARGS, "--out", str(tmp / "out"), "--format", "json,md,html", *extra]
    if as_of:
        args += ["--as-of", as_of]
    kw = {"clock": clock} if clock else {}
    code = cli.main(args, env={**(QASE_ENV if env is None else env), "REACHMAP_CACHE": str(e.cache)}, stdout=out, stderr=err, git_env=e.git_env, transport=fake, **kw)
    files = sorted((tmp / "out").glob("*.json")) if (tmp / "out").exists() else []
    text = files[0].read_text(encoding="utf-8") if files else None
    _RUNS[key] = Result(code, out.getvalue(), err.getvalue(), json.loads(text) if text else None, text, fake, tmp)
    return _RUNS[key]


def analysis_of(report) -> Analysis:
    keys = ("change", "ref", "branches", "findings", "areas", "propagations", "backend_calls", "suggestions", "mapped_tests", "incompleteness", "notes", "revision", "status")
    return Analysis.from_dict({k: report[k] for k in keys})


class EverythingSuggested(st.HostedCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run(QASE_TOML + SPECS_TOML)

    def test_the_run_completes_with_the_five_screens_the_money_component_reaches(self):
        self.assertEqual(self.r.code, 0, self.r.err)
        self.assertEqual({a["label"] for a in self.r.report["areas"]}, SCREENS)
        self.assertTrue(all(a["priority"] == "must_test" for a in self.r.report["areas"]))

    def test_every_area_has_a_suggestion_and_still_every_gap_shows(self):
        report = self.r.report
        ids = {a["id"] for a in report["areas"]}
        self.assertEqual({s["area_id"] for s in report["suggestions"]}, ids)  # every area is suggested for
        self.assertEqual(report["mapped_tests"], [])
        gaps = coverage_gaps(analysis_of(report))
        self.assertEqual({g.id for g in gaps}, ids)  # ... and every area is still a gap

    def test_the_suggestions_say_what_they_are(self):
        for s in self.r.report["suggestions"]:
            self.assertEqual(s["label"], "suggested, not verified coverage")
        self.assertEqual({s["source"] for s in self.r.report["suggestions"]}, {"qase:ACME", "playwright:acme/e2e", "cypress:acme/e2e"})

    def test_a_qase_suggestion_carries_its_matched_words_and_a_spec_one_its_line(self):
        by = {(s["source"], s["case_id"]): s for s in self.r.report["suggestions"]}
        q = by[("qase:ACME", "ACME-1")]
        self.assertEqual(q["matched_words"], ["billing", "invoice"])
        self.assertEqual(q["evidence"], [])
        spec = next(s for s in self.r.report["suggestions"] if s["source"] == "playwright:acme/e2e" and s["case_id"] == "e2e/billing.spec.ts:6")
        (e,) = spec["evidence"]
        self.assertEqual((e["path"], e["line"], e["ref"]), ("e2e/billing.spec.ts", 6, st.e2e().topic))  # the head of the change

    def test_every_spec_citation_resolves_at_the_commit_it_names(self):
        n = 0
        for s in self.r.report["suggestions"]:
            for e in s["evidence"]:
                kind, text = build.file_at(st.e2e().path, e["ref"], e["path"])
                self.assertEqual(kind, "found")
                self.assertEqual(text.splitlines()[e["line"] - 1], e["detail"])
                n += 1
        self.assertGreater(n, 5)

    def test_the_inputs_say_every_source_was_read(self):
        inputs = self.r.report["inputs"]
        self.assertIs(inputs["test_source_read"], True)
        self.assertEqual([(s["source"], s["state"], s["read"]) for s in inputs["test_sources"]],
                         [("qase:ACME", "read", True), ("playwright:acme/e2e", "read", True), ("cypress:acme/e2e", "read", True)])

    def test_the_only_records_are_the_partial_spec_addresses_and_none_blocks(self):
        recs = self.r.report["incompleteness"]
        self.assertEqual({r["kind"] for r in recs}, {"spec_url_partial"})
        self.assertEqual(len(recs), 5)  # three in the Playwright specs, two in the Cypress specs
        self.assertFalse(any(r["blocks_selection"] for r in recs))

    def test_the_markdown_lists_them_under_their_areas_apart_from_mapped_tests(self):
        md = (next(self.r.tmp.joinpath("out").glob("*.md"))).read_text(encoding="utf-8")
        section = md.split("## Suggested, not verified coverage")[1].split("\n## ")[0]
        self.assertIn("ACME-1", section)
        self.assertIn("Invoice list shows billing totals", section)
        self.assertIn("e2e/billing.spec.ts:6", section)
        self.assertIn("invoice list shows totals", section)
        self.assertNotIn("None suggested.", section)
        for label in SCREENS:
            self.assertIn(f"`{label}`", section)  # a heading per area
        self.assertNotIn("## Mapped tests", md)

    def test_the_html_has_the_section_and_the_titles_as_text(self):
        html = (next(self.r.tmp.joinpath("out").glob("*.html"))).read_text(encoding="utf-8")
        self.assertIn("Suggested, not verified coverage", html)
        self.assertIn("Invoice list shows billing totals", html)

    def test_every_request_was_a_get_and_the_token_is_in_no_output(self):
        self.assertTrue(self.r.fake.requests)
        self.assertEqual({m for m, _, _ in self.r.fake.requests}, {"GET"})
        every = [self.r.text, self.r.out, self.r.err] + [p.read_text(encoding="utf-8") for p in self.r.tmp.joinpath("out").rglob("*") if p.is_file()]
        for blob in every:
            for secret in st.SECRETS:
                self.assertNotIn(secret, blob)
        for _, _, headers in self.r.fake.requests:
            self.assertEqual([k for k, v in headers.items() if st.TOKEN in v], ["Token"])

    def test_the_summary_names_the_suggestions_as_not_coverage(self):
        self.assertRegex(self.r.out, r"Suggested cases \(not coverage and not counted above\): \d+")
        self.assertNotIn("Blocking:", self.r.out)

    def test_the_old_note_that_sources_are_not_read_is_gone(self):
        self.assertFalse([n for n in self.r.report["notes"] if "were not read" in n])


class AFailingSource(st.HostedCase):
    @classmethod
    def setUpClass(cls):
        cls.good = run(SPECS_TOML)
        cls.r = run(QASE_TOML + SPECS_TOML, routes={f"{QASE}/project/ACME": "qase/error-500.json"}, key="qase-500")

    def test_the_run_still_completes_and_the_areas_are_the_same(self):
        self.assertEqual(self.r.code, 0, self.r.err)
        self.assertEqual(self.r.report["areas"], self.good.report["areas"])
        self.assertEqual(self.r.report["findings"], self.good.report["findings"])

    def test_the_failure_is_a_blocking_source_unavailable_never_none_suggested(self):
        recs = [x for x in self.r.report["incompleteness"] if x["kind"] == "source_unavailable"]
        (rec,) = recs
        self.assertEqual((rec["subject"], rec["blocks_selection"], rec["blocks_suggestions"], rec["scope"]), ("qase:ACME", False, True, "suggestions"))
        self.assertIn("HTTP 500", rec["detail"])
        self.assertIs(self.r.report["inputs"]["test_source_read"], False)
        self.assertEqual({s["source"] for s in self.r.report["suggestions"]}, {"playwright:acme/e2e", "cypress:acme/e2e"})  # the other sources are still listed
        self.assertEqual([(s["source"], s["state"]) for s in self.r.report["inputs"]["test_sources"]][0], ("qase:ACME", "unavailable"))

    def test_the_counts_keep_the_records_that_block_selection_apart_from_those_that_block_suggestions(self):
        counts = self.r.report["counts"]
        self.assertEqual((counts["blocking_records"], counts["blocking_suggestion_records"]), (0, 1))

    def test_it_blocks_the_suggestions_and_not_the_selection_of_areas(self):
        prop = self.r.report["propagations"][0]
        self.assertEqual(prop["complete"], self.good.report["propagations"][0]["complete"])  # the areas are as complete as they were
        self.assertNotIn("may be incomplete", self.r.out.split("Blocking:")[0])
        self.assertIn("Blocking: Suggestions from qase:ACME were not looked up", self.r.out)
        self.assertNotIn("none were established", self.r.out)
        self.assertNotIn("The lists above may be incomplete", self.r.out)

    def test_the_report_says_the_suggestions_could_not_be_established(self):
        md = next(self.r.tmp.joinpath("out").glob("*.md")).read_text(encoding="utf-8")
        section = md.split("## Suggested, not verified coverage")[1].split("\n## ")[0]
        self.assertIn("did not answer", section)
        self.assertNotIn("None suggested.", section)
        self.assertIn("are not affected", md.replace("\n", " "))

    def test_a_failing_source_is_exit_zero_and_the_areas_come_first(self):
        md = next(self.r.tmp.joinpath("out").glob("*.md")).read_text(encoding="utf-8")
        self.assertLess(md.index("## Areas to test"), md.index("## Findings"))


class NotConfiguredAndNotRead(st.HostedCase):
    def test_an_unset_token_variable_is_source_unconfigured_and_nothing_blocks(self):
        r = run(QASE_TOML, env={}, routes={}, key="no-token")
        self.assertEqual(r.code, 0, r.err)
        (rec,) = [x for x in r.report["incompleteness"] if x["kind"] == "source_unconfigured"]
        self.assertFalse(rec["blocks_selection"])
        self.assertIn("QASE_API_TOKEN", rec["detail"])
        self.assertEqual(r.report["suggestions"], [])
        self.assertIs(r.report["inputs"]["test_source_read"], False)
        self.assertEqual(r.fake.requests, [])
        md = next(r.tmp.joinpath("out").glob("*.md")).read_text(encoding="utf-8")
        self.assertIn("No test source is configured.", md)
        self.assertNotIn("None suggested.", md)

    def test_a_source_read_completely_with_no_match_says_none_suggested(self):
        r = run(QASE_TOML.replace("billing", "nothing-like-it"), key="no-match")
        self.assertEqual(r.report["suggestions"], [])
        self.assertIs(r.report["inputs"]["test_source_read"], True)
        md = next(r.tmp.joinpath("out").glob("*.md")).read_text(encoding="utf-8")
        self.assertIn("None suggested.", md)

    def test_a_workspace_with_no_test_source_has_no_test_source_keys_and_says_not_read(self):
        r = run("", key="none")
        self.assertNotIn("test_source_read", r.report["inputs"])
        self.assertNotIn("test_sources", r.report["inputs"])
        md = next(r.tmp.joinpath("out").glob("*.md")).read_text(encoding="utf-8")
        self.assertIn("No test source was read by this run.", md)


class CutList(st.HostedCase):
    def test_a_page_cap_lists_what_was_read_and_says_the_list_is_cut(self):
        r = run(QASE_TOML.replace("page_size = 15", "page_size = 15\nmax_pages = 1"), key="cut")
        (rec,) = [x for x in r.report["incompleteness"] if x["kind"] == "suggestions_incomplete"]
        self.assertEqual((rec["blocks_selection"], rec["blocks_suggestions"]), (False, True))
        self.assertIn("15 of 30", rec["detail"])
        self.assertTrue(r.report["suggestions"])
        self.assertIs(r.report["inputs"]["test_source_read"], False)
        self.assertEqual(r.report["inputs"]["test_sources"][0]["state"], "incomplete")
        md = next(r.tmp.joinpath("out").glob("*.md")).read_text(encoding="utf-8")
        section = md.split("## Suggested, not verified coverage")[1].split("\n## ")[0]
        self.assertIn("cut short", section)
        self.assertNotIn("None suggested.", section)


class AsOfAndTimeout(st.HostedCase):
    def test_as_of_does_not_move_a_hosted_source_and_the_run_says_so(self):
        r = run(QASE_TOML, as_of="2100-01-01T00:00:00Z", key="asof")
        self.assertEqual(r.code, 0, r.err)
        self.assertTrue([n for n in r.report["notes"] if "test sources are read as they are now" in n])

    def test_a_timeout_between_two_requests_is_timed_out_and_has_no_suggestions(self):
        ticks = {"now": 0.0}

        class Slow(Fake):
            def __call__(self, method, url, headers=None):
                ticks["now"] += 10_000.0  # the run's time limit has passed by the time the next request is made
                return super().__call__(method, url, headers)

        e = st.e2e()
        from reachmap import config, pipeline

        ws = config.loads(ws_text(e) + QASE_TOML, fmt="toml", env={})
        slow = Slow(QASE_ROUTES)

        def fast_clock():
            return ticks["now"]

        a = pipeline.build(ws, "acme/e2e", pipeline.ChangeSpec(compare="main...topic/money-rounding"),
                           pipeline.Options(cache=e.cache, git_env=e.git_env, test_transport=slow, env=QASE_ENV, timeout_s=5_000.0, clock=fast_clock))
        self.assertEqual(a.status, "timed_out")
        self.assertIsNone(a.to_dict()["suggestions"])
        self.assertIn("the test sources", a.incompleteness[0].detail)


def ws_text(e) -> str:
    return f'''workspace = "acme"
[limits]
hops = 3
hub_importers = 50
distinct_max = 10
timeout_s = 900

[repos."acme/e2e"]
host = "git"
remote = "{e.remote}"
language = "typescript"
roles = ["frontend"]
branches = ["main"]
aliases = {{ from = "tsconfig.json" }}
entry_points = {{ kind = "react-router", routes_glob = "src/modules/*/routes.tsx" }}

'''


if __name__ == "__main__":
    unittest.main()


class ADeepAnswer(st.HostedCase):
    def test_a_source_that_answers_with_json_nested_too_deeply_changes_the_suggestions_and_nothing_else(self):
        good = run(SPECS_TOML)
        r = run(QASE_TOML + SPECS_TOML, routes={f"{QASE}/project/ACME": (200, {}, b"[" * 200000 + b"]" * 200000)}, key="deep")
        self.assertEqual(r.code, 0, r.err)
        self.assertEqual(r.report["status"], "completed")
        self.assertEqual(r.report["areas"], good.report["areas"])
        self.assertEqual([x["kind"] for x in r.report["incompleteness"] if x["blocks_suggestions"]], ["source_unavailable"])


class NothingToLookAt(st.HostedCase):
    def test_a_spec_source_that_selects_no_file_is_not_configured_and_never_says_none_suggested(self):
        r = run('[[test_sources]]\nkind = "playwright"\nrepo = "acme/e2e"\nglobs = ["nowhere/**/*.ts"]\n', key="no-globs")
        self.assertEqual(r.code, 0, r.err)
        (rec,) = r.report["incompleteness"]
        self.assertEqual((rec["kind"], rec["scope"], rec["blocks_selection"], rec["blocks_suggestions"]), ("source_unconfigured", "suggestions", False, False))
        self.assertIn("no file of acme/e2e matches", rec["detail"])
        self.assertIs(r.report["inputs"]["test_source_read"], False)
        self.assertEqual(r.report["inputs"]["test_sources"][0]["state"], "unconfigured")
        md = next(r.tmp.joinpath("out").glob("*.md")).read_text(encoding="utf-8")
        self.assertNotIn("None suggested.", md)
        self.assertIn("No test source is configured.", md)

    def test_a_qase_source_with_no_match_next_to_spec_addresses_that_were_not_literals_says_what_could_not_be_read(self):
        r = run(QASE_TOML.replace("billing", "nothing-like-it") + SPECS_TOML.replace('kind = "cypress"\nrepo = "acme/e2e"\n', 'kind = "cypress"\nrepo = "acme/e2e"\nglobs = ["cypress/e2e/misc.cy.js"]\n'), key="partial-none")
        md = next(r.tmp.joinpath("out").glob("*.md")).read_text(encoding="utf-8")
        section = md.split("## Suggested, not verified coverage")[1].split("\n## ")[0]
        self.assertTrue(r.report["suggestions"])  # the specs still suggest; the Qase source suggests nothing
        self.assertIn("address that is not a literal", section)
