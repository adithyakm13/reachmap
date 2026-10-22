"""The pipeline (M9): one change in any configured repository to one `Analysis`. Runs against the M1 fixture repositories
(through the cache the scenario modules already fill), with injected failures for what the fixtures cannot do."""

import dataclasses
import json
import unittest
from pathlib import Path
from unittest import mock

from reachmap import branches, config, entrypoints, findings, payload, pipeline, propagate, wording
from reachmap.gitlayer import Git
from reachmap.hosts import ChangeRequest
from reachmap.incomplete import Incompleteness
from reachmap.model import Analysis, ModelError, ReadResult
from tests import scenario_support as ss
from tests import support_cli
from tests import support_git as sg
from tests.fixtures import build


def options(**kw):
    e = ss.env()
    kw.setdefault("cache", e.cache)
    kw.setdefault("git_env", sg.isolated_env(e.home))
    return pipeline.Options(**kw)


_CACHE: dict = {}


def run(repo, spec, **kw):
    """The analysis of a compare spec, built once per process (the pipeline is deterministic and the tests only read it)."""
    key = (repo, spec, tuple(sorted(kw.items())))
    if key not in _CACHE:
        _CACHE[key] = pipeline.build(ss.env().workspace, repo, pipeline.ChangeSpec(compare=spec), options(**kw))
    return _CACHE[key]


def scenario(key):
    for repo in ("acme/web", "acme/api", "acme/contracts"):
        for sc in ss.scenarios(repo):
            if sc["id"].startswith(key + "-"):
                c = sc["change"]
                return sc["repo"], (pipeline.ChangeSpec(compare=f"{c['base']}...{c['head']}") if c["kind"] == "compare" else pipeline.ChangeSpec(range=f"{c['base']}..{c['head']}"))
    raise KeyError(key)


def analyse(key):
    """The analysis of a scenario, from the run the end to end scenarios already made through the CLI (the report is the
    Analysis as JSON, read back with the model's strict `from_dict`): one pipeline run per scenario for the whole suite."""
    if key not in _CACHE:
        sc = [x for repo in ("acme/web", "acme/api", "acme/contracts") for x in ss.scenarios(repo) if x["id"].startswith(key + "-")][0]
        report = support_cli.scenario_run(sc).report
        _CACHE[key] = Analysis.from_dict({k: report[k] for k in support_cli.ANALYSIS_KEYS})
    return _CACHE[key]


def propagation(analysis):
    (p,) = analysis.propagations
    return p


class Spec(unittest.TestCase):
    def test_exactly_one_of_pr_compare_range(self):
        for kw in ({}, {"pr": 1, "compare": "a...b"}, {"compare": "a...b", "range": "a..b"}):
            with self.assertRaises(ValueError):
                pipeline.ChangeSpec(**kw)

    def test_the_dots_are_checked(self):
        for kw in ({"compare": "a..b"}, {"compare": "main"}, {"range": "a...b"}, {"range": "a"}, {"compare": "...b"}, {"range": "a.."}):
            with self.assertRaises(ValueError, msg=str(kw)):
                pipeline.ChangeSpec(**kw)

    def test_the_labels_and_the_head_ref(self):
        c = pipeline.ChangeSpec(compare="main...feature/x")
        self.assertEqual((c.kind, c.label, c.head_ref), ("compare", "compare main...feature/x", "feature/x"))
        r = pipeline.ChangeSpec(range="v1..v2")
        self.assertEqual((r.kind, r.label, r.head_ref), ("range", "range v1..v2", "v2"))
        p = pipeline.ChangeSpec(pr="#12")
        self.assertEqual((p.kind, p.label, p.head_ref), ("pr", "pr #12", None))

    def test_as_of_is_checked_up_front(self):
        self.assertEqual(pipeline.parse_as_of("2024-05-01T12:00:00Z"), "2024-05-01T12:00:00Z")
        with self.assertRaises(ValueError):
            pipeline.parse_as_of("yesterday")

    def test_a_time_before_1970_is_refused_as_a_usage_error(self):
        """git cannot read a negative epoch as a time; the message says so rather than pinning to some other commit."""
        for text in ("1960-01-01T00:00:00Z", "1969-12-31T23:59:59Z", "0001-01-01T00:00:00"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError) as caught:
                    pipeline.parse_as_of(text)
                self.assertIn("1970", str(caught.exception))
        self.assertEqual(pipeline.parse_as_of("1970-01-01T00:00:00Z"), "1970-01-01T00:00:00Z")

    def test_an_unknown_repository_is_a_usage_error_naming_it(self):
        with self.assertRaises(ValueError) as caught:
            pipeline.build(ss.env().workspace, "acme/nope", pipeline.ChangeSpec(compare="a...b"), options())
        self.assertIn("acme/nope", str(caught.exception))


class Completed(unittest.TestCase):
    def test_an_analysis_has_everything_the_brief_lists(self):
        a = analyse("s13")
        self.assertIsInstance(a, Analysis)
        self.assertEqual(a.status, "completed")
        change = a.change
        repo = ss.env().repos["acme/api"]
        self.assertEqual((change["kind"], change["repo"], change["resolved"]), ("compare", "acme/api", True))
        self.assertEqual(a.ref, sg.rev(repo, "scenario/s13-branch-matrix"))
        self.assertEqual((change["head_sha"], change["base_sha"], change["pre_change_sha"]), (a.ref, sg.rev(repo, "main"), sg.rev(repo, "main")))
        self.assertIn("merge base", change["how"]["pre_change"])
        self.assertEqual([(f["path"], f["role"], f["status"]) for f in change["files"]], [("services/billing/app/services/totals.py", "source", "modified")])
        self.assertEqual({r.branch: r.state for r in a.branches}, {"main": "pre_change", "release/1.x": "present", "release/1.2": "present"})
        self.assertEqual([f.kind for f in a.findings if f.kind == "branch-missing"], ["branch-missing"])
        self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in a.incompleteness], [("edge_unfollowed", "edge:event:acme/api:acme/worker", False)])
        self.assertEqual(a.revision, 1)
        self.assertEqual((a.suggestions, a.mapped_tests), ((), ()))

    def test_areas_are_the_propagations_with_one_priority_over_all(self):
        a = analyse("s11")
        self.assertEqual(sorted((x.repo, x.label, x.priority) for x in a.areas), [
            ("acme/api", "GET /api/billing/settlements/summary", "must_test"), ("acme/web", "/billing/settlements", "must_test"),
        ])
        self.assertEqual(sorted(f.kind for f in a.findings), ["contract-tightened", "contract-tightened", "cross-repo", "cross-repo", "exclusion", "exclusion", "release", "release"])

    def test_findings_and_areas_agree_one_finding_per_area(self):
        for key in ("s01", "s06", "s11", "s15"):
            a = analyse(key)
            with self.subTest(scenario=key):
                got = sorted((f.identity[1], f.identity[-1]) for f in a.findings if f.kind in ("direct", "cross-repo"))
                self.assertEqual(got, sorted((x.repo, x.label) for x in a.areas))

    def test_a_pass_per_repository_with_the_analysis_of_each(self):
        p = propagation(analyse("s11"))
        self.assertEqual([(x["repo"], x["depth"]) for x in p["passes"]], [("acme/contracts", 0), ("acme/api", 1), ("acme/worker", 1), ("acme/web", 2)])
        self.assertEqual([i["path"] for i in p["passes"][0]["analysis"]["impacts"]], ["contracts/models/invoice.py"])
        self.assertEqual(p["passes"][0]["reach"], None)  # the contracts repository has no entry points
        self.assertEqual(p["repo"], "acme/contracts")
        self.assertFalse(p["complete"])
        self.assertTrue(p["reasons"])

    def test_the_whole_report_content_is_plain_json(self):
        for key in ("s05", "s10", "s11", "s13"):
            data = analyse(key).to_dict()
            self.assertEqual(json.loads(json.dumps(data)), data)

    def test_the_backend_calls_of_a_client_change_are_in_the_analysis(self):
        a = analyse("s05")
        self.assertEqual([c["route"]["path"] for c in a.backend_calls], ["/api/billing/settlements/summary"])
        self.assertTrue(a.backend_calls[0]["modules"])

    def test_the_changes_files_carry_the_role_the_adapter_gave(self):
        a = analyse("s14")
        self.assertEqual({(f["path"], f["role"]) for f in a.change["files"]}, {(f.path, f.role) for f in propagation_files(a)})
        self.assertIn(("src/contest/engine.ts", "source"), {(f["path"], f["role"]) for f in a.change["files"]})

    def test_pre_change_unavailable_is_one_blocking_record_and_the_run_still_completes(self):
        a = analyse("s16")
        self.assertEqual(a.status, "completed")
        self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in a.incompleteness],
                         [("pre_change_unavailable", "scenario/s16-no-merge-base", True), ("routes_unmatched", "acme/web:src/modules/*/routes.tsx", True)])  # the orphan tree has no routes file
        self.assertIsNone(a.change["pre_change_sha"])
        self.assertIn("unavailable", a.change["how"]["pre_change"])
        self.assertFalse(propagation(a)["complete"])  # a blocking record never sits beside a complete run
        self.assertEqual([f for f in a.findings if f.kind == "removed_route_still_called"], [])  # no claim that needs the pre-change version

    def test_every_blocking_record_means_the_run_says_it_is_not_complete(self):
        for key in ("s01", "s02", "s03", "s10", "s11", "s12", "s13", "s16", "s21"):
            a = analyse(key)
            blocking = [r for r in a.incompleteness if r.blocks_selection]
            p = propagation(a)
            with self.subTest(scenario=key):
                if blocking or any(r.kind in ("hub_cutoff", "hop_cap", "edge_unfollowed", "entry_points_unread", "module_cap") for r in a.incompleteness):
                    self.assertFalse(p["complete"])
                else:
                    self.assertTrue(p["complete"])
                for r in blocking:
                    self.assertTrue(any(r.kind in why for why in p["reasons"]))

    def test_the_hub_stays_a_non_blocking_record_and_no_widget_is_listed(self):
        a = analyse("s02")
        self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in a.incompleteness], [("hub_cutoff", "src/lib/format.ts", False)])
        self.assertEqual(a.areas, ())
        self.assertEqual(a.findings, ())

    def test_no_second_record_for_the_steps_that_need_the_pre_change_version(self):
        a = analyse("s16")
        self.assertEqual([r.kind for r in a.incompleteness].count("pre_change_unavailable"), 1)
        self.assertFalse(any("pre-change" in r.detail and r.kind != "pre_change_unavailable" for r in a.incompleteness))

    def test_the_output_is_deterministic(self):
        """A second build of a scenario is the same report (the end to end scenarios repeat this for seven scenarios through the CLI)."""
        repo, spec = scenario("s13")
        again = pipeline.build(ss.env().workspace, repo, spec, options())
        self.assertEqual(json.dumps(analyse("s13").to_dict()), json.dumps(again.to_dict()))

    def test_every_citation_resolves_at_its_pinned_sha(self):
        from reachmap import payload
        total = 0
        for key in ("s01", "s03", "s05", "s06", "s10", "s11", "s13", "s15", "s17", "s22"):
            data = analyse(key).to_dict()
            found = list(payload.evidence_in(data))
            total += len(found)
            with self.subTest(scenario=key, evidence=len(found)):
                self.assertTrue(found)
                self.assertEqual(ss.evidence_problems(ss.env().repos, found), [])
        self.assertGreater(total, 300)

    def test_every_human_readable_string_passes_the_wording_check(self):
        for key in ("s01", "s02", "s03", "s05", "s10", "s11", "s12", "s13", "s16", "s21"):
            a = analyse(key)
            texts = [*(f.title for f in a.findings), *(f.confidence_basis for f in a.findings), *(f.workflow for f in a.findings),
                     *(r.detail for r in a.incompleteness), *(r.not_established for r in a.incompleteness), *a.notes, *(b.detail for b in a.branches)]
            for text in texts:
                self.assertEqual(wording.check(text), [], f"{key}: {text}")

    def test_the_records_are_listed_blocking_first_then_by_kind_and_subject(self):
        a = analyse("s16")
        keys = [(not r.blocks_selection, r.kind, r.subject) for r in a.incompleteness]
        self.assertEqual(keys, sorted(keys))


def propagation_files(a):
    from reachmap import payload
    from reachmap.adapters import ChangeAnalysis
    return payload.decode(ChangeAnalysis, propagation(a)["passes"][0]["analysis"]).files


class Retain(unittest.TestCase):
    def test_what_the_steps_made_can_be_kept_without_repeating_the_run(self):
        repo, spec = scenario("s13")
        kept = {}
        a = pipeline.build(ss.env().workspace, repo, spec, options(retain=kept))
        self.assertEqual(sorted(kept), ["analysis", "change", "entry", "git", "language", "matrix", "prop", "reach", "records"])
        self.assertEqual(kept["change"].head_sha, a.ref)
        self.assertIs(kept["prop"].passes[0].analysis, kept["analysis"])  # the pass the propagation started from is the analysis made
        self.assertEqual([r.kind for r in kept["records"]], ["edge_unfollowed"])  # the raw records of the steps, before the report dedupes them
        self.assertEqual([b.branch for b in kept["matrix"].rows], [b.branch for b in a.branches])
        self.assertNotIn("retain", json.dumps(a.to_dict()))

    def test_a_run_that_fails_keeps_what_it_made_so_far(self):
        kept = {}
        with mock.patch.object(entrypoints, "reach", side_effect=RuntimeError("boom")):
            a = pipeline.build(ss.env().workspace, "acme/api", pipeline.ChangeSpec(compare="main...scenario/s13-branch-matrix"), options(retain=kept))
        self.assertEqual(a.status, "failed")
        self.assertIsNotNone(kept["change"])
        self.assertIsNone(kept["prop"])


class Sources(unittest.TestCase):
    def test_a_configured_context_source_is_not_read_and_the_report_says_so_and_a_test_source_is_read_or_recorded(self):
        from reachmap import config
        ws = ss.env().workspace
        text = (ss.FIXTURES / "reachmap.toml").read_text() + '''
[[test_sources]]
kind = "qase"
project = "ACME"
token_env = "QASE_API_TOKEN"

[[context]]
kind = "jira-snapshot"
path = "/nonexistent/snapshot.json"
'''
        with_sources = config.loads(text, fmt="toml", env={"REACHMAP_FIXTURES": "file://" + str(ss.env().dest)})
        self.assertEqual((len(with_sources.test_sources), len(with_sources.context)), (1, 1))
        repo, spec = scenario("s13")
        a = pipeline.build(with_sources, repo, spec, options())
        self.assertEqual(a.status, "completed")
        self.assertTrue(any("1 context source(s)" in n and "were not read" in n and "test source" not in n for n in a.notes), a.notes)  # context is still not read (M15 reads test sources)
        (rec,) = [r for r in a.incompleteness if r.kind == "source_unconfigured"]  # the qase source has no token in this environment: said, not "none suggested"
        self.assertEqual((rec.subject, rec.blocks_selection), ("qase:ACME", False))
        self.assertEqual((a.suggestions, a.mapped_tests), ((), ()))
        self.assertEqual(wording.check(" ".join(a.notes)), [])
        self.assertFalse(any("test source" in n for n in analyse("s13").notes))  # no source configured: nothing to say


class Records(unittest.TestCase):
    def rec(self, kind="hub_cutoff", subject="a.ts", detail="", blocks=None):
        from reachmap.incomplete import kind_spec
        return Incompleteness(kind, subject, detail, kind_spec(kind).blocks_selection if blocks is None else blocks)

    def test_records_with_the_same_kind_and_subject_are_one_and_the_first_wording_stays(self):
        got = pipeline.dedupe_records([self.rec(detail="first"), self.rec(detail="second"), self.rec(subject="b.ts")])
        self.assertEqual([(r.subject, r.detail) for r in got], [("a.ts", "first"), ("b.ts", "")])

    def test_a_blocking_record_is_never_lost_to_a_non_blocking_one_of_the_same_kind_and_subject(self):
        got = pipeline.dedupe_records([self.rec("edge_unfollowed", "e", "x", blocks=False), self.rec("edge_unfollowed", "e", "y", blocks=True)])
        self.assertEqual([(r.detail, r.blocks_selection) for r in got], [("y", True)])
        got = pipeline.dedupe_records([self.rec("edge_unfollowed", "e", "y", blocks=True), self.rec("edge_unfollowed", "e", "x", blocks=False)])
        self.assertEqual([(r.detail, r.blocks_selection) for r in got], [("y", True)])

    def test_the_order_is_blocking_first_then_kind_then_subject(self):
        got = pipeline.dedupe_records([self.rec("hub_cutoff", "z"), self.rec("read_unreadable", "a", "x"), self.rec("hub_cutoff", "b"), self.rec("pre_change_unavailable", "h", "x")])
        self.assertEqual([(r.kind, r.subject) for r in got], [("pre_change_unavailable", "h"), ("read_unreadable", "a"), ("hub_cutoff", "b"), ("hub_cutoff", "z")])


class RecordsThroughThePipeline(unittest.TestCase):
    def test_the_pipeline_dedupes_and_orders_the_records_it_is_given(self):
        """Records that repeat a (kind, subject) with another wording, and a blocking one made last, as the steps leave them."""
        from reachmap.incomplete import record
        real = propagate.walk_edges

        def noisy(run, *a, **kw):
            prop = real(run, *a, **kw)
            record(run.git.target, "hub_cutoff", "z.ts", detail="first")
            record(run.git.target, "hub_cutoff", "z.ts", detail="second")
            record(run.git.target, "hub_cutoff", "a.ts", detail="x")
            record(run.git.target, "read_unreadable", "q", detail="blocking, made last")
            return prop

        repo, spec = scenario("s13")
        with mock.patch.object(propagate, "walk_edges", noisy):
            a = pipeline.build(ss.env().workspace, repo, spec, options())
        got = [(r.kind, r.subject) for r in a.incompleteness]
        self.assertEqual(got, [("read_unreadable", "q"), ("edge_unfollowed", "edge:event:acme/api:acme/worker"), ("hub_cutoff", "a.ts"), ("hub_cutoff", "z.ts")])
        self.assertEqual([r.detail for r in a.incompleteness if r.kind == "hub_cutoff"], ["x", "first"])  # the first wording of a repeated record stays
        self.assertEqual([r.blocks_selection for r in a.incompleteness], [True, False, False, False])  # blocking first, then kind and subject
        self.assertEqual(len({(r.kind, r.subject) for r in a.incompleteness}), len(a.incompleteness))

    def test_the_stated_limits_of_the_hop_cap_scenario_come_out_in_that_order(self):
        self.assertEqual([r.kind for r in analyse("s21").incompleteness], ["edge_unfollowed", "entry_points_unread", "hop_cap"])


class Areas(unittest.TestCase):
    def test_a_candidate_with_the_id_of_an_area_that_is_reached_for_sure_is_shown_once_and_said(self):
        a = analyse("s11")
        reached = list(propagation_reached(a))
        sure = reached[0]
        twin = dataclasses.replace(sure, area=dataclasses.replace(sure.area, by_name=True, priority="candidate"))
        got, notes = pipeline.unique_areas([sure, twin] + reached[1:])
        self.assertEqual([r.area.id for r in got], [r.area.id for r in reached])
        self.assertTrue(all(not r.area.by_name for r in got))
        self.assertEqual(len(notes), 1)
        self.assertIn(sure.area.label, notes[0])

    def test_a_candidate_alone_is_kept_as_a_candidate(self):
        a = analyse("s11")
        sure = list(propagation_reached(a))[0]
        cand = dataclasses.replace(sure, area=dataclasses.replace(sure.area, by_name=True, priority="candidate"))
        got, notes = pipeline.unique_areas([cand])
        self.assertEqual(([r.area.by_name for r in got], notes), ([True], []))


def propagation_reached(a):
    from reachmap import payload
    from reachmap.adapters import Reached
    return [payload.decode(Reached, r) for r in propagation(a)["reached"]]


class UnitTests(unittest.TestCase):
    def prop(self, key="s11"):
        from reachmap.adapters.python import PyAdapter
        sc = [sc for r in ("acme/web", "acme/api", "acme/contracts") for sc in ss.scenarios(r) if sc["id"].startswith(key + "-")][0]
        return ss.run_scenario(sc, lambda cfg, git: PyAdapter.from_config(cfg, git, limits=ss.env().workspace.limits)).propagation

    def workspace(self, command):
        ws = ss.env().workspace
        repos = dict(ws.repos)
        api = repos["acme/api"]
        repos["acme/api"] = dataclasses.replace(api, tests={**dict(api.tests), "command": command} if command is not None else dict(api.tests))
        return dataclasses.replace(ws, repos=repos)

    def test_the_command_of_a_repository_gets_its_test_paths(self):
        commands = pipeline.unit_test_commands(self.prop(), self.workspace("pytest -q {paths}"))
        self.assertEqual(commands, [{"repo": "acme/api", "command": "pytest -q services/billing/tests/test_settlement.py", "paths": ["services/billing/tests/test_settlement.py"]}])

    def test_a_repository_with_no_command_still_lists_its_tests(self):
        (entry,) = pipeline.unit_test_commands(self.prop(), self.workspace(None))
        self.assertEqual((entry["repo"], entry["command"]), ("acme/api", None))

    def test_paths_are_quoted_so_a_name_cannot_inject_a_command(self):
        prop = self.prop()
        t = prop.unit_tests[0]
        nasty = dataclasses.replace(t, path="tests/a b; rm -rf x.py")
        (entry,) = pipeline.unit_test_commands(dataclasses.replace(prop, unit_tests=(nasty,)), self.workspace("pytest {paths}"))
        self.assertEqual(entry["command"], "pytest 'tests/a b; rm -rf x.py'")
        self.assertEqual(entry["paths"], ["tests/a b; rm -rf x.py"])

    def test_a_command_without_the_placeholder_is_kept_as_it_is(self):
        (entry,) = pipeline.unit_test_commands(self.prop(), self.workspace("make test"))
        self.assertEqual(entry["command"], "make test")

    def test_a_command_that_is_not_text_is_ignored(self):
        (entry,) = pipeline.unit_test_commands(self.prop(), self.workspace(["pytest"]))
        self.assertIsNone(entry["command"])

    def test_the_analysis_carries_them(self):
        a = analyse("s11")
        self.assertEqual([c["repo"] for c in propagation(a)["unit_test_commands"]], ["acme/api"])


class Failures(unittest.TestCase):
    def failed(self, **kw):
        return pipeline.build(ss.env().workspace, "acme/web", pipeline.ChangeSpec(compare="main...no-such-branch"), options(**kw))

    def test_a_refused_change_set_is_a_failed_run_with_the_blocking_record_and_no_findings(self):
        a = self.failed()
        self.assertEqual(a.status, "failed")
        self.assertIsNone(a.ref)  # no change was resolved: there is no commit to name, and none is made up
        self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in a.incompleteness], [("run_failed", "compare main...no-such-branch", True)])
        self.assertIn("no-such-branch", a.incompleteness[0].detail)
        self.assertNotIn("0" * 40, json.dumps(a.to_dict()))
        data = a.to_dict()
        self.assertIsNone(data["findings"])
        self.assertIsNone(data["areas"])
        with self.assertRaises(ModelError):
            a.finding_count
        self.assertEqual(a.change["resolved"], False)
        self.assertEqual(a.change["spec"], {"compare": "main...no-such-branch"})

    def test_a_pull_request_on_a_host_less_remote_is_refused_and_the_records_stay(self):
        a = pipeline.build(ss.env().workspace, "acme/web", pipeline.ChangeSpec(pr=7), options())
        self.assertEqual(a.status, "failed")
        self.assertEqual([r.kind for r in a.incompleteness], ["run_failed"])

    def test_a_language_with_no_adapter_fails_the_run_naming_it(self):
        with mock.patch.object(propagate, "language_adapter", side_effect=propagate.UnsupportedLanguage("no language adapter for 'go' (repository acme/web)")):
            a = pipeline.build(ss.env().workspace, "acme/api", pipeline.ChangeSpec(compare="main...scenario/s13-branch-matrix"), options())
        self.assertEqual(a.status, "failed")
        self.assertIn("'go'", a.incompleteness[-1].detail)
        self.assertEqual(a.ref, sg.rev(ss.env().repos["acme/api"], "scenario/s13-branch-matrix"))  # the change was resolved: its head is the ref

    def test_an_unexpected_error_is_a_failed_run_with_its_type_and_not_a_traceback(self):
        with mock.patch.object(entrypoints, "reach", side_effect=RuntimeError("boom at /home/someone/secret/path")):
            a = pipeline.build(ss.env().workspace, "acme/api", pipeline.ChangeSpec(compare="main...scenario/s13-branch-matrix"), options())
        self.assertEqual(a.status, "failed")
        (rec,) = [r for r in a.incompleteness if r.kind == "run_failed"]
        self.assertIn("RuntimeError", rec.detail)
        self.assertIsNone(a.to_dict()["findings"])

    def test_the_failure_names_the_step_it_was_in_and_not_the_one_before(self):
        for module, name, step in ((branches, "matrix", "the branch matrix"), (entrypoints, "reach", "the entry points"),
                                   (propagate, "walk_edges", "following the edges"), (findings, "build", "the findings")):
            with self.subTest(step=step), mock.patch.object(module, name, side_effect=RuntimeError("boom")):
                a = pipeline.build(ss.env().workspace, "acme/api", pipeline.ChangeSpec(compare="main...scenario/s13-branch-matrix"), options())
                (rec,) = [r for r in a.incompleteness if r.kind == "run_failed"]
                self.assertEqual(rec.detail, f"RuntimeError during {step}: boom")

    def test_the_exception_text_never_carries_the_machine_it_ran_on(self):
        import os
        import tempfile
        text = f"cannot read /Users/someone/secret/path and {os.path.expanduser('~')}/x and /home/someone/y and {tempfile.gettempdir()}/z/w and /var/folders/ab/cd/T/q"
        with mock.patch.object(entrypoints, "reach", side_effect=RuntimeError(text)):
            a = pipeline.build(ss.env().workspace, "acme/api", pipeline.ChangeSpec(compare="main...scenario/s13-branch-matrix"), options())
        (rec,) = [r for r in a.incompleteness if r.kind == "run_failed"]
        for needle in ("/Users/", "/home/", "/var/folders", os.path.expanduser("~"), tempfile.gettempdir()):
            self.assertNotIn(needle, rec.detail)
        self.assertIn("~/secret/path", rec.detail)
        self.assertIn("<tmp>/z/w", rec.detail)

    def test_a_failure_after_the_steps_while_the_report_is_made_is_a_failed_run_too(self):
        with mock.patch.object(payload, "propagation_to_dict", side_effect=RuntimeError("boom")):
            a = pipeline.build(ss.env().workspace, "acme/api", pipeline.ChangeSpec(compare="main...scenario/s13-branch-matrix"), options())
        self.assertEqual(a.status, "failed")
        self.assertEqual(a.ref, sg.rev(ss.env().repos["acme/api"], "scenario/s13-branch-matrix"))
        (rec,) = [r for r in a.incompleteness if r.kind == "run_failed"]
        self.assertIn("RuntimeError", rec.detail)
        data = a.to_dict()
        self.assertEqual((data["findings"], data["areas"], data["branches"], data["propagations"]), (None, None, None, None))
        self.assertEqual(data["change"]["resolved"], True)

    def test_an_assembly_that_fails_is_rebuilt_as_a_failed_run_with_a_minimal_change(self):
        with mock.patch.object(pipeline.Analysis, "add_branch", side_effect=RuntimeError("cannot add")):
            a = pipeline.build(ss.env().workspace, "acme/api", pipeline.ChangeSpec(compare="main...scenario/s13-branch-matrix"), options())
        self.assertEqual(a.status, "failed")
        (rec,) = [r for r in a.incompleteness if r.kind == "run_failed"]
        self.assertIn("RuntimeError during assembling the report", rec.detail)
        self.assertEqual(sorted(a.change), ["as_of", "head_sha", "kind", "repo", "resolved", "spec"])  # nothing from the failed assembly is trusted
        self.assertEqual(a.change["head_sha"], a.ref)
        self.assertTrue(a.to_dict())

    def test_a_file_name_that_is_not_utf8_does_not_stop_the_run_and_is_shown_escaped(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="reachmap-badname-"))
        self.addCleanup(sg.remove_tree, tmp)
        repo, shas = sg.make_raw_repo(tmp, "raw", [[("100644", b"ok.py", b"def f():\n    return 1\n")],
                                                  [("100644", b"ok.py", b"def f():\n    return 2\n"), ("100644", b"caf\xe9.py", b"x = 1\n")]])
        url = sg.remote_url(repo)
        ws = config.loads(f'workspace = "t"\n[repos."t/app"]\nhost = "git"\nremote = "{url}"\nlanguage = "python"\nroles = ["backend"]\nbranches = ["main"]\n', fmt="toml", env={})
        a = pipeline.build(ws, "t/app", pipeline.ChangeSpec(range=f"{shas[0]}..{shas[1]}"), pipeline.Options(cache=tmp / "cache", git_env=sg.isolated_env(tmp / "home")))
        self.assertEqual(a.status, "completed")
        text = json.dumps(a.to_dict())  # no lone surrogate reaches the JSON
        paths = [f["path"] for f in a.change["files"]]
        self.assertEqual(paths, ["caf\\xe9.py", "ok.py"])  # displayed with the byte escaped
        self.assertTrue(any("caf" in n for n in a.notes))
        text.encode("utf-8")  # and it can be written

    def test_a_run_that_fails_keeps_the_records_made_before_it_failed(self):
        with mock.patch.object(branches, "matrix", side_effect=RuntimeError("boom")):  # right after the change set, which made the record
            a = pipeline.build(ss.env().workspace, "acme/web", pipeline.ChangeSpec(compare="main...scenario/s16-no-merge-base"), options())
        self.assertEqual(sorted(r.kind for r in a.incompleteness), ["pre_change_unavailable", "run_failed"])


class Deadline(unittest.TestCase):
    def ticking(self, step):
        state = {"t": 0.0}

        def clock():
            state["t"] += step
            return state["t"]
        return clock

    def test_a_cooperative_deadline_between_steps_returns_timed_out_with_no_findings(self):
        repo, spec = scenario("s13")
        a = pipeline.build(ss.env().workspace, repo, spec, options(timeout_s=100, clock=self.ticking(40)))
        self.assertEqual(a.status, "timed_out")
        data = a.to_dict()
        self.assertEqual((data["findings"], data["areas"]), (None, None))
        self.assertIn("timed_out", [r.kind for r in a.incompleteness])
        self.assertTrue(all(r.blocks_selection for r in a.incompleteness if r.kind == "timed_out"))
        with self.assertRaises(ModelError):
            a.finding_count

    def test_the_step_it_stopped_after_is_said(self):
        repo, spec = scenario("s13")
        a = pipeline.build(ss.env().workspace, repo, spec, options(timeout_s=100, clock=self.ticking(40)))
        (rec,) = [r for r in a.incompleteness if r.kind == "timed_out"]
        self.assertRegex(rec.detail, r"after [a-z ]+")

    def test_a_generous_limit_changes_nothing(self):
        repo, spec = scenario("s13")
        a = pipeline.build(ss.env().workspace, repo, spec, options(timeout_s=10 ** 6, clock=self.ticking(1)))
        self.assertEqual(a.status, "completed")

    def test_the_limit_defaults_to_the_config(self):
        ws = ss.env().workspace
        repo, spec = scenario("s13")
        tight = dataclasses.replace(ws, limits=dataclasses.replace(ws.limits, timeout_s=5))
        a = pipeline.build(tight, repo, spec, options(clock=self.ticking(3)))
        self.assertEqual(a.status, "timed_out")


class PullRequest(unittest.TestCase):
    class Host:
        kind = "github"

        def __init__(self, cr):
            self.cr = cr
            self.incompleteness = []

        def remote(self, repo):
            raise AssertionError("the remote is configured")

        def change_request(self, repo, number):
            return ReadResult.found(self.cr.to_json())

    def cr(self, **kw):
        api = ss.env().repos["acme/api"]
        base, head = sg.rev(api, "main"), sg.rev(api, "scenario/s13-branch-matrix")
        count = len(build.run_git(api, "diff", "--name-only", f"{base}...{head}").stdout.split())
        values = dict(number=12, title="Round invoice totals half up", body="", state="open", base_ref="main", head_ref="scenario/s13-branch-matrix",
                      base_sha=base, head_sha=head, merge_sha=None, file_count=count, url="https://example.invalid/pr/12")
        values.update(kw)
        return ChangeRequest(**values)

    def test_an_open_pull_request_is_analysed_like_a_compare(self):
        a = pipeline.build(ss.env().workspace, "acme/api", pipeline.ChangeSpec(pr=12), options(host=self.Host(self.cr())))
        self.assertEqual(a.status, "completed")
        self.assertEqual((a.change["kind"], a.change["spec"], a.change["change_request"]["number"]), ("pr", {"pr": "12"}, 12))
        self.assertEqual(a.change["change_request"]["title"], "Round invoice totals half up")
        self.assertEqual(a.ref, self.cr().head_sha)
        same = analyse("s13")
        self.assertEqual(sorted(x.label for x in a.areas), sorted(x.label for x in same.areas))
        self.assertEqual(sorted(f.id for f in a.findings), sorted(f.id for f in same.findings))

    def test_the_requests_own_head_branch_is_left_out_of_the_branch_matrix(self):
        ws = ss.env().workspace
        web = dataclasses.replace(ws.repos["acme/api"], branches=("main", "release/*", "scenario/*"))
        ws2 = dataclasses.replace(ws, repos={**dict(ws.repos), "acme/api": web})
        a = pipeline.build(ws2, "acme/api", pipeline.ChangeSpec(pr=12), options(host=self.Host(self.cr())))
        names = {b.branch for b in a.branches}
        self.assertNotIn("scenario/s13-branch-matrix", names)
        self.assertIn("scenario/s06-nested-mount-handler", names)
        self.assertTrue(any("scenario/s13-branch-matrix is left out" in n for n in a.notes), a.notes)  # a branch that is left out is said

    def test_a_pull_request_from_a_forks_main_keeps_the_primary_branch_row(self):
        """The host names the head `main` and the head commit is not main's tip here: `main` of this repository is a branch like any other."""
        a = pipeline.build(ss.env().workspace, "acme/api", pipeline.ChangeSpec(pr=12), options(host=self.Host(self.cr(head_ref="main"))))
        self.assertEqual(a.status, "completed")
        self.assertEqual([b.branch for b in a.branches][:1], ["main"])
        self.assertFalse(any("is left out" in n for n in a.notes), a.notes)

    def test_a_request_whose_file_count_disagrees_is_refused_with_the_record(self):
        a = pipeline.build(ss.env().workspace, "acme/api", pipeline.ChangeSpec(pr=12), options(host=self.Host(self.cr(file_count=99))))
        self.assertEqual(a.status, "failed")
        self.assertEqual(sorted(r.kind for r in a.incompleteness), ["diff_count_mismatch", "run_failed"])

    def test_as_of_does_not_move_the_shas_a_host_reports_and_says_so(self):
        a = pipeline.build(ss.env().workspace, "acme/api", pipeline.ChangeSpec(pr=12), options(host=self.Host(self.cr()), as_of="2000-01-01T00:00:00Z"))
        self.assertEqual(a.status, "completed")
        self.assertEqual(a.ref, self.cr().head_sha)
        self.assertTrue(any("as_of" in n or "as-of" in n for n in a.notes))


class Branches(unittest.TestCase):
    def test_an_unreadable_branch_file_is_a_blocking_record_and_the_areas_are_still_selected(self):
        e = ss.env()
        real = Git.files_at
        target = sg.rev(e.repos["acme/api"], "release/1.x")

        def flaky(self, source, paths, sha=None, **kw):
            if sha == target:
                return {p: self._unreadable(f"{p} at {sha[:12]}", "connection reset") for p in paths}
            return real(self, source, paths, sha, **kw)

        repo, spec = scenario("s13")
        with mock.patch.object(Git, "files_at", flaky):
            a = pipeline.build(e.workspace, repo, spec, options())
        self.assertEqual(a.status, "completed")
        rows = {r.branch: r.state for r in a.branches}
        self.assertEqual(rows["release/1.x"], "unreadable")
        self.assertEqual(rows["main"], "pre_change")  # the other branches are still compared
        self.assertTrue(any(r.kind == "read_unreadable" and r.blocks_selection for r in a.incompleteness))
        self.assertFalse(propagation(a)["complete"])
        (missing,) = [f for f in a.findings if f.kind == "branch-missing"]  # release/1.2 still shows the change, main does not
        self.assertIn("release/1.2", missing.title)
        self.assertNotIn("release/1.x", missing.title)  # an unreadable branch is not claimed to have it


class AsOf(unittest.TestCase):
    """`--as-of` reaches every step that reads a branch: the change set, the branch matrix and the pins of the other repositories.
    The change is given by commit (a full sha is not moved by the time), so the time can fall between two commits of a branch."""

    @staticmethod
    def iso(ts):
        from datetime import datetime, timezone
        return datetime.fromtimestamp(ts, timezone.utc).isoformat()

    @staticmethod
    def when(repo, ref):
        return int(build.run_git(ss.env().repos[repo], "log", "-1", "--format=%ct", ref).stdout.strip())

    _runs: dict = {}

    def s13(self, **kw):
        """The s13 change by commit, as of a time (built once per time: the tests only read it)."""
        key = tuple(sorted(kw.items()))
        if key not in self._runs:
            head = sg.rev(ss.env().repos["acme/api"], "scenario/s13-branch-matrix")
            self._runs[key] = pipeline.build(ss.env().workspace, "acme/api", pipeline.ChangeSpec(compare=f"main...{head}"), options(**kw))
        return self._runs[key]

    def test_a_release_branch_is_read_at_the_tip_it_had_between_two_of_its_commits(self):
        api = ss.env().repos["acme/api"]
        fix, cherry = self.when("acme/api", "release/1.x"), self.when("acme/api", "release/1.2")
        self.assertLess(fix, cherry)  # the fix reached release/1.x first, then release/1.2
        now = {b.branch: b for b in self.s13().branches}
        self.assertEqual({k: v.state for k, v in now.items()}, {"main": "pre_change", "release/1.2": "present", "release/1.x": "present"})
        between = {b.branch: b for b in self.s13(as_of=self.iso(fix + 1)).branches}
        self.assertEqual({k: v.state for k, v in between.items()}, {"main": "pre_change", "release/1.2": "pre_change", "release/1.x": "present"})
        self.assertEqual(between["release/1.x"].commit, sg.rev(api, "release/1.x"))
        self.assertEqual(between["release/1.2"].commit, sg.rev(api, "release/1.2~1"))  # the tip it had then, not the one it has
        before = {b.branch: b for b in self.s13(as_of=self.iso(fix - 1)).branches}
        self.assertEqual({v.state for v in before.values()}, {"pre_change"})
        self.assertEqual(before["release/1.x"].commit, sg.rev(api, "release/1.x~1"))

    def test_the_branch_missing_finding_follows_the_time(self):
        fix = self.when("acme/api", "release/1.x")
        titles = lambda a: [f.title for f in a.findings if f.kind == "branch-missing"]  # noqa: E731
        self.assertEqual(titles(self.s13(as_of=self.iso(fix + 1))), ["The change is on release/1.x and not on main"])
        self.assertEqual(titles(self.s13(as_of=self.iso(fix - 1))), [])
        self.assertEqual(titles(self.s13()), ["The change is on release/1.2, release/1.x and not on main"])

    def test_a_consumers_pin_moves_with_the_time(self):
        """The api bumped its pin of the contracts package to 1.10.0 in its last commit on main: an hour earlier it pinned 1.9.0."""
        contracts, api = ss.env().repos["acme/contracts"], ss.env().repos["acme/api"]
        head = sg.rev(contracts, "scenario/s11-contract-field")
        spec = pipeline.ChangeSpec(compare=f"main...{head}")
        bumped = self.when("acme/api", "main")

        def pins(**kw):
            a = pipeline.build(ss.env().workspace, "acme/contracts", spec, options(**kw))
            self.assertEqual(a.status, "completed")
            return {c["repo"]: (c["pin"], c["sha"]) for c in a.propagations[0]["consumers"]}

        self.assertEqual(pins()["acme/api"], ("1.10.0", sg.rev(api, "main")))
        then = pins(as_of=self.iso(bumped - 1))
        self.assertEqual(then["acme/api"], ("1.9.0", sg.rev(api, "main~1")))
        self.assertEqual(then["acme/worker"][0], "1.9.0")

    def test_a_time_after_everything_gives_the_same_analysis(self):
        repo, spec = scenario("s17")
        plain = analyse("s17")
        late = pipeline.build(ss.env().workspace, repo, spec, options(as_of="2100-01-01T00:00:00Z"))
        a, b = late.to_dict(), plain.to_dict()
        self.assertEqual(a["change"].pop("as_of"), "2100-01-01T00:00:00Z")  # the input is part of the report
        self.assertIsNone(b["change"].pop("as_of"))
        for d in (a, b):  # the one thing that differs: the note about the tags
            d["notes"] = [n for n in d["notes"] if "tags" not in n]
        self.assertEqual(a, b)

    def test_the_tag_limit_is_said_when_a_package_edge_is_followed_with_a_time(self):
        repo, spec = scenario("s17")
        late = pipeline.build(ss.env().workspace, repo, spec, options(as_of="2100-01-01T00:00:00Z"))
        self.assertTrue(any("tags" in n and "as-of" in n for n in late.notes), late.notes)
        self.assertFalse(any("tags" in n for n in analyse("s17").notes))  # no time: nothing to say
        self.assertFalse(any("tags" in n for n in self.s13(as_of="2100-01-01T00:00:00Z").notes))  # no package edge followed from the api

    def test_a_time_before_the_first_commit_is_a_failed_run(self):
        repo, spec = scenario("s13")
        a = pipeline.build(ss.env().workspace, repo, spec, options(as_of="2000-01-01T00:00:00Z"))
        self.assertEqual(a.status, "failed")


if __name__ == "__main__":
    unittest.main()
