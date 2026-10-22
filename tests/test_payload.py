"""The JSON form of what the adapters and edges found (`reachmap.payload`): every value that goes into a report comes
back as an equal object, so a reader of the report (the end to end harness, later the renderers) works with the same
types the analyser used. Real values are taken from the cached fixture runs."""

import dataclasses
import json
import unittest

from reachmap import payload
from reachmap.adapters import ChangeAnalysis, ChangedFile, FileImpact, Reached, Route
from reachmap.adapters.js import JsAdapter
from reachmap.adapters.python import PyAdapter
from reachmap.edges import Hop
from reachmap.edges.contract import ContractChange, ContractRead
from reachmap.edges.http import BackendCall, HttpMatch
from reachmap.edges.package import Consumer
from reachmap.model import Area, Evidence
from tests import scenario_support as ss


def make_js(cfg, git):
    return JsAdapter.from_config(cfg, git, limits=ss.env().workspace.limits)


def make_py(cfg, git):
    return PyAdapter.from_config(cfg, git, limits=ss.env().workspace.limits)


def run_of(key):
    for repo, make in (("acme/web", make_js), ("acme/api", make_py), ("acme/contracts", make_py)):
        for sc in ss.scenarios(repo):
            if sc["id"].startswith(key + "-"):
                return ss.run_scenario(sc, make)
    raise KeyError(key)


def roundtrip(cls, value):
    text = json.dumps(payload.encode(value), sort_keys=True)  # plain JSON, no tuples left
    return payload.decode(cls, json.loads(text))


class Codec(unittest.TestCase):
    def test_evidence_is_its_own_four_fields_and_a_ref(self):
        ev = Evidence("a/b", "x/y.py", 3, "a" * 40, "    return 1")
        self.assertEqual(payload.encode(ev), ev.to_dict())
        self.assertEqual(roundtrip(Evidence, ev), ev)

    def test_an_evidence_claim_is_in_the_json_only_when_it_is_set_and_round_trips(self):
        plain = Evidence("a/b", "x.py", 1, "a" * 40, "x")
        claimed = Evidence("a/b", "x.py", 1, "a" * 40, "import x", claim="import:x.py:y")
        self.assertNotIn("claim", payload.encode(plain))
        self.assertEqual(payload.encode(claimed)["claim"], "import:x.py:y")
        self.assertEqual(roundtrip(Evidence, claimed).claim, "import:x.py:y")
        self.assertEqual(roundtrip(Evidence, plain).claim, None)
        self.assertEqual([e.claim for e in payload.evidence_in({"a": [payload.encode(plain), payload.encode(claimed)]})], [None, "import:x.py:y"])

    def test_tuples_become_lists_and_back_and_none_stays_none(self):
        ev = Evidence("a/b", "x.py", 1, "a" * 40, "x")
        area = Area("/x", "screen", "a/b", "m", ev, ((ev,),), ("/y",), "must_test", False)
        data = payload.encode(area)
        self.assertEqual(data["chains"], [[ev.to_dict()]])
        self.assertEqual(roundtrip(Area, area), area)

    def test_a_field_the_type_does_not_have_is_refused_and_a_missing_one_is_named(self):
        data = payload.encode(ChangedFile("r", "p.py", "source", "modified"))
        with self.assertRaises(ValueError) as caught:
            payload.decode(ChangedFile, {**data, "extra": 1})
        self.assertIn("extra", str(caught.exception))
        del data["role"]
        with self.assertRaises(ValueError) as caught:
            payload.decode(ChangedFile, data)
        self.assertIn("role", str(caught.exception))

    def test_a_missing_key_is_refused_even_when_the_field_has_a_default(self):
        """The docstring says so: only the fields left out of the JSON form are filled in."""
        from reachmap.adapters import Symbol
        run = run_of("s03")
        symbol = [s for i in run.analysis.impacts for s in i.symbols][0]
        data = payload.encode(symbol)
        self.assertEqual(roundtrip(Symbol, symbol), symbol)
        for key in ("change", "evidence", "guards", "export_names"):  # each has a default in the dataclass
            with self.subTest(key=key):
                with self.assertRaises(ValueError) as caught:
                    payload.decode(Symbol, {k: v for k, v in data.items() if k != key})
                self.assertIn(key, str(caught.exception))

    def test_a_file_name_that_is_not_text_is_encoded_escaped_so_the_json_can_be_written(self):
        data = payload.encode(ChangedFile("r", "caf\udce9.py", "source", "modified"))
        self.assertEqual(data["path"], "caf\\xe9.py")
        json.dumps(data, ensure_ascii=False).encode("utf-8")

    def test_a_value_of_the_wrong_shape_is_refused(self):
        with self.assertRaises(ValueError):
            payload.decode(ChangedFile, {"repo": 1, "path": "p", "role": "source", "status": "modified"})
        with self.assertRaises(ValueError):
            payload.decode(Evidence, {"repo": "r", "path": "p", "line": "3", "ref": "a" * 40, "detail": "x"})

    def test_the_fields_that_hold_no_json_are_left_out_of_a_reached_area(self):
        run = run_of("s01")
        reached = run.reach.reached[0]
        data = payload.encode(reached)
        self.assertNotIn("sources", data)
        back = payload.decode(Reached, data)
        self.assertEqual((back.area, back.routes, back.complete, back.reasons), (reached.area, reached.routes, reached.complete, reached.reasons))
        self.assertEqual(back.sources, ())


class EvidenceIn(unittest.TestCase):
    def test_every_evidence_in_a_json_structure_is_found_wherever_it_sits(self):
        a = Evidence("r", "a.py", 1, "a" * 40, "x = 1")
        b = Evidence("r", "b.py", 2, "b" * 40, "y = 2")
        data = {"findings": [{"evidence": [a.to_dict()], "n": 3}], "areas": [{"chains": [[b.to_dict()]], "route_line": a.to_dict()}], "x": {"deep": [[{"e": b.to_dict()}]]}}
        self.assertEqual(sorted(payload.evidence_in(data), key=lambda e: e.path), [a, a, b, b])

    def test_a_dict_that_only_looks_a_little_like_evidence_is_not_taken_for_it(self):
        self.assertEqual(list(payload.evidence_in({"repo": "r", "path": "p", "line": 1})), [])
        self.assertEqual(list(payload.evidence_in({"repo": "r", "path": "p", "line": 1, "ref": "a" * 40, "detail": "x", "extra": 1})), [])

    def test_evidence_with_a_bad_field_is_an_error_and_not_skipped(self):
        with self.assertRaises(ValueError):
            list(payload.evidence_in({"repo": "r", "path": "p", "line": 0, "ref": "a" * 40, "detail": "x"}))  # it has all five keys: it is evidence, and it is wrong


class RealValues(unittest.TestCase):
    def test_the_whole_language_analysis_of_a_change_roundtrips(self):
        for key in ("s03", "s04", "s15", "s23", "s11", "s10"):
            run = run_of(key)
            with self.subTest(scenario=key):
                analysis = run.analysis
                data = payload.encode(analysis)
                self.assertEqual(sorted(data), ["files", "impacts", "repo"])  # the change is the report's `change`, not part of this
                back = payload.decode(ChangeAnalysis, data)
                self.assertEqual((back.repo, back.files, back.impacts), (analysis.repo, analysis.files, analysis.impacts))
                self.assertIsNone(back.change)
                for impact in analysis.impacts:
                    self.assertEqual(roundtrip(FileImpact, impact), impact)

    def test_routes_and_reached_areas_roundtrip(self):
        for key in ("s01", "s06", "s11"):
            run = run_of(key)
            with self.subTest(scenario=key):
                found = 0
                for r in run.propagation.reached:
                    back = roundtrip(Reached, r)
                    self.assertEqual((back.area, back.routes, back.complete), (r.area, r.routes, r.complete))
                    found += 1
                    for route in r.routes:
                        self.assertEqual(roundtrip(Route, route), route)
                self.assertGreater(found, 0)

    def test_http_edges_consumers_contract_reads_and_hops_roundtrip(self):
        s10, s11 = run_of("s10").propagation, run_of("s11").propagation
        self.assertTrue(s10.http_edges and s11.consumers and s11.contract_reads and s11.contract_changes and s11.hops)
        for cls, items in ((HttpMatch, s10.http_edges), (Consumer, s11.consumers), (ContractRead, s11.contract_reads),
                           (ContractChange, s11.contract_changes), (Hop, s11.hops)):
            for item in items:
                with self.subTest(type=cls.__name__):
                    self.assertEqual(roundtrip(cls, item), item)
        removed = [m for m in s10.http_edges if m.removed]
        self.assertTrue(removed and removed[0].route.evidence)  # a removed route keeps its evidence at the base

    def test_backend_calls_roundtrip(self):
        calls = [c for key in ("s05",) for c in run_of(key).propagation.backend_calls]
        self.assertTrue(calls and calls[0].modules)
        for call in calls:
            self.assertEqual(roundtrip(BackendCall, call), call)

    def test_a_whole_propagation_roundtrips_through_json(self):
        for key in ("s01", "s06", "s10", "s11", "s21"):
            prop = run_of(key).propagation
            data = json.loads(json.dumps(payload.propagation_to_dict(prop, [{"repo": "r", "command": None, "paths": []}])))
            back = payload.propagation_from_dict(data)
            with self.subTest(scenario=key):
                self.assertEqual((back.repo, back.complete, back.reasons), (prop.repo, prop.complete, prop.reasons))
                self.assertEqual([(r.area, r.routes, r.complete) for r in back.reached], [(r.area, r.routes, r.complete) for r in prop.reached])
                self.assertEqual((back.http_edges, back.consumers, back.contract_reads, back.contract_changes, back.unit_tests, back.hops),
                                 (prop.http_edges, prop.consumers, prop.contract_reads, prop.contract_changes, prop.unit_tests, prop.hops))
                self.assertEqual([(p.repo, p.depth, p.sha, p.candidate, p.prefix, p.seed, p.analysis.impacts, p.analysis.files) for p in back.passes],
                                 [(p.repo, p.depth, p.sha, p.candidate, p.prefix, p.seed, p.analysis.impacts, p.analysis.files) for p in prop.passes])
                self.assertEqual([(p.hop is None) for p in back.passes], [(p.hop is None) for p in prop.passes])
                self.assertEqual([None if p.reach is None else (p.reach.complete, p.reach.reasons, len(p.reach.reached)) for p in back.passes],
                                 [None if p.reach is None else (p.reach.complete, p.reach.reasons, len(p.reach.reached)) for p in prop.passes])
                self.assertEqual(data["unit_test_commands"], [{"repo": "r", "command": None, "paths": []}])

    def test_the_encoding_is_deterministic(self):
        run = run_of("s11")
        a = json.dumps(payload.encode(run.propagation.reached), sort_keys=True)
        b = json.dumps(payload.encode(tuple(run.propagation.reached)), sort_keys=True)
        self.assertEqual(a, b)
        self.assertEqual(dataclasses.is_dataclass(run.propagation.consumers[0]), True)


if __name__ == "__main__":
    unittest.main()
