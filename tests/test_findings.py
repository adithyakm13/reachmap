"""Findings (M9): one builder per kind, each from evidence objects, with risk and confidence set by a documented rule per
kind and never combined. Real values come from the cached fixture runs; hand-built ones cover what the fixtures do not."""

import dataclasses
import re
import types
import unittest

from reachmap import findings, wording
from reachmap.adapters import Exclusion
from reachmap.adapters.js import JsAdapter
from reachmap.adapters.python import PyAdapter
from reachmap.edges import FindingInput
from reachmap.model import Area, Evidence, Finding, finding_kinds
from tests import scenario_support as ss

SHA = "a" * 40


def make_js(cfg, git):
    return JsAdapter.from_config(cfg, git, limits=ss.env().workspace.limits)


def make_py(cfg, git):
    return PyAdapter.from_config(cfg, git, limits=ss.env().workspace.limits)


_RUNS: dict = {}


def run_of(key):
    if key not in _RUNS:
        for repo, make in (("acme/web", make_js), ("acme/api", make_py), ("acme/contracts", make_py)):
            for sc in ss.scenarios(repo):
                if sc["id"].startswith(key + "-"):
                    _RUNS[key] = ss.run_scenario(sc, make)
    return _RUNS[key]


_BUILT: dict = {}


def built(key):
    if key not in _BUILT:
        run = run_of(key)
        _BUILT[key] = findings.build(run.propagation, run.branches)
    return _BUILT[key]


def by_kind(items, kind):
    return [f for f in items if f.kind == kind]


def ev(path="a.py", line=1, detail="x = 1", repo="t/r"):
    return Evidence(repo, path, line, SHA, detail)


def area(label="/orders", kind="screen", repo="t/web", module="orders", chains=None, by_name=False, priority="must_test"):
    chains = chains if chains is not None else ((ev("src/a.ts", 3, "import x", repo), ev("src/routes.tsx", 9, "<Route />", repo)),)
    return Area(label, kind, repo, module, chains[0][-1], chains, (), "candidate" if by_name else priority, by_name)


def shifted(value, by=7):
    """The same value with every Evidence line number moved: what an unrelated insertion above does."""
    if isinstance(value, Evidence):
        return dataclasses.replace(value, line=value.line + by)
    if isinstance(value, tuple):
        return tuple(shifted(v, by) for v in value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.replace(value, **{f.name: shifted(getattr(value, f.name), by) for f in dataclasses.fields(value)})
    return value


class Table(unittest.TestCase):
    """The risk and confidence table of `findings.py`, pinned for every kind a builder makes."""

    def test_the_documented_table_names_every_kind_a_builder_makes(self):
        for kind in ("direct", "cross-repo", "exclusion", "guard-excluded", "contract-tightened", "contract-relaxed",
                     "removed_route_still_called", "added_route_uncalled", "release", "branch-missing"):
            self.assertIn(f"`{kind}`", findings.__doc__, kind)
        self.assertNotIn("`duplicate`", findings.TABLE_KINDS)  # nobody builds it: it is not claimed
        self.assertTrue(set(findings.TABLE_KINDS) <= set(finding_kinds()))

    def test_a_direct_finding_is_medium_risk_and_high_confidence_when_import_backed_and_whole(self):
        f = by_kind(built("s01"), "direct")
        self.assertEqual(len(f), 4)  # one per screen
        for item in f:
            self.assertEqual((item.risk.value, item.confidence.value), ("medium", "high"))
            self.assertTrue(item.confidence_basis.startswith("import-backed"))

    def test_an_undecidable_call_site_is_affected_with_medium_confidence_and_a_literal_that_enters_is_high(self):
        got = {f.identity[-1]: f for f in by_kind(built("s03"), "direct")}
        self.assertEqual(sorted(got), ["/billing/invoices", "/billing/settlements"])
        self.assertEqual(got["/billing/invoices"].confidence.value, "medium")  # InvoiceListPage passes a variable
        self.assertIn("undecidable", got["/billing/invoices"].confidence_basis)
        self.assertEqual(got["/billing/settlements"].confidence.value, "high")  # SettlementsPage passes a literal the guard lets in
        self.assertEqual({f.risk.value for f in got.values()}, {"medium"})  # the risk does not move with the evidence

    def test_a_partial_read_lowers_the_confidence_and_says_so(self):
        run = run_of("s01")
        prop = run.propagation
        reached = prop.reached[0]
        partial = dataclasses.replace(reached, complete=False, reasons=("an importer list was cut",))
        f = findings.build(dataclasses.replace(prop, reached=(partial,)), None)
        direct = by_kind(f, "direct")[0]
        self.assertEqual(direct.confidence.value, "medium")
        self.assertIn("partial read", direct.confidence_basis)
        self.assertIn("an importer list was cut", direct.confidence_basis)

    def test_a_by_name_candidate_is_low_confidence(self):
        item = findings.cross_repo(area(by_name=True, repo="t/web"), "t/api", findings.Backing.by_name("its URL is partial"))
        self.assertEqual((item.kind, item.risk.value, item.confidence.value), ("cross-repo", "medium", "low"))
        self.assertIn("by name", item.confidence_basis)

    def test_cross_repo_findings_are_one_per_area_outside_the_changed_repository(self):
        got = built("s11")
        cross = by_kind(got, "cross-repo")
        self.assertEqual(sorted(f.identity[-1] for f in cross), ["/billing/settlements", "GET /api/billing/settlements/summary"])
        self.assertEqual(by_kind(got, "direct"), [])
        for f in cross:
            self.assertEqual((f.risk.value, f.confidence.value), ("medium", "high"))  # typed reads, an exact URL fit

    def test_a_weak_http_match_or_a_read_by_name_makes_a_cross_repo_finding_medium(self):
        run = run_of("s11")
        prop = run.propagation
        edge = prop.http_edges[0]
        weak = dataclasses.replace(edge, kind="literal_in_parameter")
        got = findings.build(dataclasses.replace(prop, http_edges=(weak,)), None)
        screen = [f for f in by_kind(got, "cross-repo") if f.identity[-1] == "/billing/settlements"][0]
        self.assertEqual(screen.confidence.value, "medium")
        self.assertIn("literal in a parameter", screen.confidence_basis)
        reads = tuple(dataclasses.replace(r, basis="name") for r in prop.contract_reads)
        got = findings.build(dataclasses.replace(prop, contract_reads=reads), None)
        route = [f for f in by_kind(got, "cross-repo") if f.identity[-1].startswith("GET ")][0]
        self.assertEqual(route.confidence.value, "medium")
        self.assertIn("by name", route.confidence_basis)

    def test_exclusions_are_low_risk_and_carry_the_evidence_that_shows_them(self):
        got = built("s05")
        ex = by_kind(got, "exclusion")
        self.assertEqual(sorted(f.identity[-2] for f in ex), ["src/modules/billing/pages/InvoiceDetailPage.tsx", "src/modules/billing/pages/InvoiceListPage.tsx"])
        for f in ex:
            self.assertTrue(f.is_exclusion)
            self.assertEqual((f.risk.value, f.confidence.value), ("low", "high"))
            self.assertTrue(f.evidence)
            self.assertIn("import-backed", f.confidence_basis)

    def test_a_guard_exclusion_is_guard_excluded_with_the_guard_and_the_call_line(self):
        got = by_kind(built("s03"), "guard-excluded")
        self.assertEqual([f.identity[-2] for f in got], ["src/modules/billing/pages/InvoiceDetailPage.tsx"])
        (f,) = got
        self.assertTrue(f.is_exclusion)
        self.assertEqual((f.risk.value, f.confidence.value), ("low", "high"))
        self.assertTrue(f.confidence_basis.startswith("guard proven"))
        self.assertGreaterEqual(len(f.evidence), 2)  # the guard line and the call line
        self.assertEqual(by_kind(built("s03"), "exclusion"), [])

    def test_a_tightened_contract_is_high_risk_per_consumer_and_confidence_follows_the_reads(self):
        got = by_kind(built("s11"), "contract-tightened")
        self.assertEqual(sorted(f.identity[-1] for f in got), ["acme/api", "acme/worker"])
        for f in got:
            self.assertEqual((f.risk.value, f.confidence.value), ("high", "high"))  # every read is typed
            self.assertIn("typed", f.confidence_basis)
            self.assertTrue(any(e.repo == f.identity[-1] for e in f.evidence))  # a line of the consumer's own code
            self.assertTrue(any(e.repo == "acme/contracts" for e in f.evidence))  # and the change itself
        self.assertEqual(by_kind(built("s11"), "contract-relaxed"), [])

    def test_a_contract_change_without_a_read_is_low_confidence_and_cites_the_consumers_pin(self):
        run = run_of("s11")
        prop = run.propagation
        got = findings.build(dataclasses.replace(prop, contract_reads=()), None)
        for f in by_kind(got, "contract-tightened"):
            self.assertEqual((f.risk.value, f.confidence.value), ("high", "low"))
            self.assertIn("no read", f.confidence_basis)
            self.assertTrue(any(e.path == "requirements.txt" for e in f.evidence))

    def test_a_relaxed_contract_is_medium_risk(self):
        run = run_of("s11")
        prop = run.propagation
        (change,) = prop.contract_changes
        relaxed = dataclasses.replace(change, kinds=("contract-relaxed",))
        got = findings.build(dataclasses.replace(prop, contract_changes=(relaxed,)), None)
        rel = by_kind(got, "contract-relaxed")
        self.assertEqual(sorted(f.identity[-1] for f in rel), ["acme/api", "acme/worker"])
        self.assertEqual({(f.risk.value, f.confidence.value) for f in rel}, {("medium", "high")})
        self.assertEqual(by_kind(got, "contract-tightened"), [])

    def test_a_change_that_is_both_tightened_and_relaxed_gives_both_kinds(self):
        run = run_of("s11")
        prop = run.propagation
        (change,) = prop.contract_changes
        both = dataclasses.replace(change, kinds=("contract-tightened", "contract-relaxed"))
        got = findings.build(dataclasses.replace(prop, contract_changes=(both,)), None)
        self.assertEqual(len(by_kind(got, "contract-tightened")), 2)
        self.assertEqual(len(by_kind(got, "contract-relaxed")), 2)

    def test_a_field_that_is_not_distinctive_is_not_followed_and_its_finding_says_so(self):
        run = run_of("s12")
        got = findings.build(run.propagation, run.branches)
        self.assertEqual([f.kind for f in got if f.kind.startswith("contract-")], [])  # a default change is neither tighter nor looser

    def test_removed_and_added_routes_carry_the_readme_values(self):
        (removed,) = by_kind(built("s10"), "removed_route_still_called")
        self.assertEqual((removed.risk.value, removed.confidence.value), ("high", "low"))
        self.assertEqual(removed.evidence, run_of("s10").propagation.findings[0].evidence)  # the client line and the removed decorator line
        (added,) = by_kind(built("s22"), "added_route_uncalled")
        self.assertEqual((added.risk.value, added.confidence.value), ("low", "medium"))
        self.assertEqual(len(added.evidence), 1)

    def test_release_findings_follow_adoption(self):
        got = {(f.identity[-2], f.identity[-1]): f for f in by_kind(built("s17"), "release")}
        self.assertEqual(sorted(got), [("acme/api", "main"), ("acme/api", "release/1.2"), ("acme/api", "release/1.x"), ("acme/worker", "main")])
        self.assertEqual((got[("acme/api", "main")].risk.value, got[("acme/api", "main")].confidence.value), ("high", "high"))  # adopted
        self.assertEqual((got[("acme/api", "release/1.x")].risk.value, got[("acme/api", "release/1.x")].confidence.value), ("medium", "high"))  # on adoption
        self.assertIn("adopted", got[("acme/api", "main")].title)
        self.assertIn("on adoption", got[("acme/worker", "main")].title)
        for f in got.values():
            self.assertEqual([e.path for e in f.evidence], ["requirements.txt"])  # the pin line, read at that branch

    def test_a_release_that_could_not_be_decided_is_low_confidence(self):
        run = run_of("s11")
        c = run.propagation.consumers[0]
        undecided = dataclasses.replace(c, adoption="undecided", contains_change=None, detail="no tag names the pin")
        f = findings.release(undecided)
        self.assertEqual((f.risk.value, f.confidence.value), ("medium", "low"))
        self.assertIn("not decided", f.title)
        self.assertIn("no tag names the pin", f.confidence_basis)

    def test_a_consumer_with_no_pin_has_no_release_finding(self):
        run = run_of("s11")
        c = run.propagation.consumers[0]
        gone = dataclasses.replace(c, pin=None, state="no_pin", evidence=(), adoption="undecided")
        got = findings.build(dataclasses.replace(run.propagation, consumers=(gone,) + run.propagation.consumers[1:]), None)
        self.assertEqual([f.identity[-2] for f in by_kind(got, "release")], ["acme/worker"])

    def test_branch_missing_is_high_risk_and_confidence_follows_how_many_files_agree(self):
        got = by_kind(built("s13"), "branch-missing")
        self.assertEqual(len(got), 1)
        (f,) = got
        self.assertEqual((f.risk.value, f.confidence.value), ("high", "high"))  # the one changed file agrees everywhere
        self.assertIn("main", f.title)
        self.assertIn("release/1.x", f.title)
        self.assertEqual({e.ref for e in f.evidence}, {sg_rev(r) for r in ("main", "release/1.x", "release/1.2")})
        self.assertEqual(f.identity[:3], ("branch-missing", "acme/api", "main"))

    def test_branch_missing_in_the_other_direction_for_a_merged_change(self):
        m = run_of("s13").branches
        rows = {r.branch: r for r in m.rows}
        flipped = [dataclasses.replace(rows["main"], state="present", added_lines_found=2, evidence=rows["release/1.x"].evidence),
                   dataclasses.replace(rows["release/1.x"], state="pre_change", added_lines_found=0, evidence=rows["main"].evidence),
                   rows["release/1.2"]]
        files = tuple(dataclasses.replace(fs, state={"main": "present", "release/1.x": "pre_change"}.get(fs.branch, fs.state)) for fs in m.files)
        got = findings.branch_missing(dataclasses.replace(m, rows=tuple(flipped), files=files), "acme/api")
        kinds = {f.identity[2] for f in got}
        self.assertEqual(kinds, {"release/1.x"})  # the merged change is missing from this release branch
        self.assertEqual({f.risk.value for f in got}, {"high"})

    def test_branch_missing_confidence_is_by_how_many_files_agree(self):
        m = run_of("s13").branches
        base = list(m.files)[0]

        def files_with(states):
            out = []
            for branch, state_list in states.items():
                for i, st in enumerate(state_list):
                    out.append(dataclasses.replace(base, branch=branch, path=f"f{i}.py", state=st, added_found=1 if st == "present" else 0))
            return tuple(out)

        rows = {r.branch: r for r in m.rows}
        sets = {"main": ["pre_change"] * 3, "release/1.x": ["present", "present", "pre_change"]}
        matrix = dataclasses.replace(m, rows=(rows["main"], rows["release/1.x"]), files=files_with(sets))
        (f,) = findings.branch_missing(matrix, "acme/api")
        self.assertEqual(f.confidence.value, "medium")  # two of three files agree on the release branch
        self.assertIn("2 of 3", f.confidence_basis)
        sets = {"main": ["pre_change"] * 3, "release/1.x": ["present", "pre_change", "pre_change"]}
        (f,) = findings.branch_missing(dataclasses.replace(matrix, files=files_with(sets)), "acme/api")
        self.assertEqual(f.confidence.value, "low")

    def test_no_branch_missing_finding_when_the_primary_is_unreadable_or_nothing_is_present_elsewhere(self):
        m = run_of("s13").branches
        rows = [dataclasses.replace(r, state="unreadable", added_lines_found=0, evidence=(), detail="calc.py: boom") if r.branch == "main" else r for r in m.rows]
        self.assertEqual(findings.branch_missing(dataclasses.replace(m, rows=tuple(rows)), "acme/api"), [])
        self.assertEqual(findings.branch_missing(None, "acme/api"), [])
        s01 = run_of("s01")
        self.assertEqual(by_kind(findings.build(s01.propagation, s01.branches), "branch-missing"), [])  # an unmerged change: no branch has it


def sg_rev(branch):
    from tests import support_git as sg
    return sg.rev(ss.env().repos["acme/api"], branch)


class AreaConfidence(unittest.TestCase):
    """An area is as sure as its best chain: the maximum over the chains of each chain's weakest link."""

    def prop(self):
        return types.SimpleNamespace(http_edges=(), contract_reads=())

    def two_chains(self):
        weak = (ev("src/a.ts", 3, "import x", "t/web"), ev("src/routes.tsx", 9, "<Route a />", "t/web"))
        clean = (ev("src/b.ts", 4, "import y", "t/web"), ev("src/routes.tsx", 10, "<Route b />", "t/web"))
        a = area(chains=(weak, clean))
        reached = types.SimpleNamespace(complete=True, reasons=())
        return a, weak, clean, reached

    def test_one_clear_chain_makes_the_area_high_whatever_the_other_one_rests_on(self):
        a, weak, clean, reached = self.two_chains()
        backing = findings._area_backing(a, reached, self.prop(), {("t/web", "src/a.ts")})  # the first chain passes an undecidable call site
        self.assertEqual(backing.confidence.value, "high")
        self.assertEqual(backing.chain, clean)  # and it is the chain that is cited
        f = findings.direct(a, backing)
        self.assertEqual((f.confidence.value, f.evidence), ("high", clean))
        self.assertNotIn("undecidable", f.confidence_basis)

    def test_when_every_chain_is_weak_the_area_is_as_weak_as_the_best_of_them(self):
        a, weak, clean, reached = self.two_chains()
        both = {("t/web", "src/a.ts"), ("t/web", "src/b.ts")}
        backing = findings._area_backing(a, reached, self.prop(), both)
        self.assertEqual(backing.confidence.value, "medium")
        self.assertEqual(backing.chain, weak)  # a tie keeps the first chain
        self.assertIn("undecidable", backing.text)

    def test_a_partial_read_applies_to_every_chain(self):
        a, _, _, _ = self.two_chains()
        partial = types.SimpleNamespace(complete=False, reasons=("an importer list was cut",))
        backing = findings._area_backing(a, partial, self.prop(), set())
        self.assertEqual(backing.confidence.value, "medium")
        self.assertIn("partial read", backing.text)

    def test_the_weakest_link_of_a_chain_still_decides_within_the_chain(self):
        a, weak, clean, reached = self.two_chains()
        edge = types.SimpleNamespace(client=clean[0], candidate=False, literal_in_parameter=True, ambiguous=False, method_unknown=False)
        prop = types.SimpleNamespace(http_edges=(edge,), contract_reads=())
        backing = findings._area_backing(a, reached, prop, {("t/web", "src/a.ts")})
        self.assertEqual(backing.confidence.value, "medium")  # chain 1: undecidable (medium); chain 2: a literal in a parameter (medium)
        self.assertEqual(backing.chain, weak)



class DefinitionLines(unittest.TestCase):
    """Area findings cite the definition line of the changed symbol, then the chain; never the changed body lines."""

    def with_definitions(self, key):
        run = run_of(key)
        defs = findings.definition_lines(run.git, run.adapter.remote, run.change, run.propagation)
        return run, findings.build(run.propagation, run.branches, defs), defs

    def test_the_first_evidence_of_an_area_finding_is_the_def_line_of_the_changed_function(self):
        run, got, _ = self.with_definitions("s06")
        area_findings = [f for f in got if f.kind in ("direct", "cross-repo")]
        self.assertEqual(len(area_findings), 2)  # the api route, and the web screen reached through it
        for f in area_findings:
            first = f.evidence[0]
            with self.subTest(kind=f.kind):
                self.assertEqual((first.repo, first.path), ("acme/api", "services/billing/app/routers/invoices.py"))
                self.assertRegex(first.detail, r"^def get_invoice\(")  # the def line, not the decorator above it and not the body
                self.assertEqual(first.ref, run.change.head_sha)
                self.assertGreater(len(f.evidence), 1)  # then the chain

    def test_the_changed_body_lines_are_never_cited_by_an_area_finding(self):
        for key in ("s06", "s11", "s05", "s03"):
            run, got, defs = self.with_definitions(key)
            definition_lines = {(e.repo, e.path, e.line) for e in defs.values()}  # a changed definition line is cited: it is the definition
            changed = {(e.repo, e.path, e.line) for i in run.propagation.passes[0].analysis.impacts for s in i.symbols for e in s.evidence} - definition_lines
            for f in got:
                if f.kind in ("direct", "cross-repo"):
                    with self.subTest(scenario=key, finding=f.title):
                        self.assertFalse(changed & {(e.repo, e.path, e.line) for e in f.evidence})

    def test_a_contract_change_is_cited_by_the_class_line_in_the_other_repositorys_findings_too(self):
        run, got, _ = self.with_definitions("s11")
        cross = [f for f in got if f.kind == "cross-repo"]
        self.assertEqual(len(cross), 2)
        for f in cross:
            first = f.evidence[0]
            self.assertEqual((first.repo, first.path), ("acme/contracts", "contracts/models/invoice.py"))
            self.assertRegex(first.detail, r"^class Invoice\b")

    def test_every_area_finding_of_every_scenario_starts_with_a_definition_when_the_change_has_a_changed_symbol(self):
        regex = findings.DEFINITION_LINE
        checked = 0
        for key in ("s01", "s03", "s05", "s06", "s10", "s11", "s15", "s20", "s22"):
            run, got, _ = self.with_definitions(key)
            symbols = [s for i in run.propagation.passes[0].analysis.impacts for s in i.symbols if not s.is_module]
            for f in got:
                if f.kind in ("direct", "cross-repo"):
                    checked += 1
                    with self.subTest(scenario=key, finding=f.title):
                        self.assertTrue(symbols)
                        self.assertRegex(f.evidence[0].detail, regex)
        self.assertGreater(checked, 15)

    def test_a_body_edit_changes_neither_the_first_evidence_nor_the_fingerprint(self):
        """The point of citing the definition: the claim does not go stale when the changed function's body is edited again."""
        run, got, defs = self.with_definitions("s06")
        f = [x for x in got if x.kind == "direct"][0]
        again = [x for x in findings.build(run.propagation, run.branches, defs) if x.kind == "direct"][0]
        self.assertEqual(f.evidence[0], again.evidence[0])
        from reachmap.identity import fingerprint
        self.assertEqual(fingerprint(f), fingerprint(again))
        moved = {k: dataclasses.replace(v, line=v.line + 5) for k, v in defs.items()}  # an insertion above: the line moves, the text does not
        shifted_f = [x for x in findings.build(run.propagation, run.branches, moved) if x.kind == "direct"][0]
        self.assertEqual(fingerprint(f), fingerprint(shifted_f))
        self.assertEqual(f.id, shifted_f.id)

    def test_without_the_definitions_a_finding_is_the_chain_alone(self):
        run = run_of("s06")
        got = findings.build(run.propagation, run.branches)
        f = [x for x in got if x.kind == "direct"][0]
        self.assertEqual(f.evidence, run.propagation.reached[0].area.chains[0])

    def test_a_change_with_only_module_level_symbols_has_no_definition_to_cite(self):
        run = run_of("s01")
        impacts = run.propagation.passes[0].analysis.impacts
        module_only = tuple(dataclasses.replace(i, symbols=tuple(s for s in i.symbols if s.is_module)) for i in impacts)
        pass0 = dataclasses.replace(run.propagation.passes[0], analysis=dataclasses.replace(run.propagation.passes[0].analysis, impacts=module_only))
        prop = dataclasses.replace(run.propagation, passes=(pass0,) + run.propagation.passes[1:])
        self.assertEqual(findings.definition_lines(run.git, run.adapter.remote, run.change, prop), {})

    def test_the_definition_line_is_the_def_and_not_the_decorator_or_a_comment_above_it(self):
        text = ["import x", "", "@router.get(\"/a\")", "@other", "async def handler(request):", "    return 1"]
        self.assertEqual(findings.definition_line_number(text, 3), 5)
        self.assertEqual(findings.definition_line_number(["export const A = () => 1;"], 1), 1)
        self.assertEqual(findings.definition_line_number(["class Invoice(BaseModel):", "    x: int"], 1), 1)
        self.assertEqual(findings.definition_line_number(["    amount = 1", "    return amount"], 1), 1)  # nothing that looks like one: the first line
        self.assertEqual(findings.definition_line_number(["@a", "", "x = 1"], 1), 1)


class OnePerThing(unittest.TestCase):
    def test_every_area_has_exactly_one_direct_or_cross_repo_finding(self):
        for key in ("s01", "s03", "s06", "s10", "s11", "s15", "s21", "s22"):
            run = run_of(key)
            got = built(key)
            area_findings = [f for f in got if f.kind in ("direct", "cross-repo")]
            with self.subTest(scenario=key):
                self.assertEqual(sorted((f.identity[1], f.identity[-1]) for f in area_findings),
                                 sorted((a.repo, a.label) for a in run.propagation.areas))
                for f in area_findings:
                    self.assertEqual(f.kind, "direct" if f.identity[1] == run.propagation.repo else "cross-repo")

    def test_every_finding_has_evidence_and_the_ids_are_unique(self):
        for key in ("s01", "s03", "s05", "s06", "s10", "s11", "s13", "s15", "s17", "s20", "s22"):
            got = built(key)
            with self.subTest(scenario=key):
                self.assertTrue(all(f.evidence for f in got))
                self.assertEqual(len({f.id for f in got}), len(got))

    def test_the_order_is_deterministic(self):
        for key in ("s11", "s15"):
            self.assertEqual([f.id for f in built(key)], [f.id for f in findings.build(run_of(key).propagation, run_of(key).branches)])


class Identity(unittest.TestCase):
    def test_moving_a_line_does_not_change_any_id_or_fingerprint_basis(self):
        for key in ("s03", "s05", "s10", "s11", "s13", "s17", "s22"):
            run = run_of(key)
            prop = run.propagation
            moved_passes = tuple(
                dataclasses.replace(p, analysis=dataclasses.replace(p.analysis, impacts=shifted(p.analysis.impacts))) for p in prop.passes
            )
            for_shift = dataclasses.replace(
                prop,
                reached=shifted(prop.reached), passes=moved_passes,
                http_edges=shifted(prop.http_edges), consumers=shifted(prop.consumers), contract_changes=shifted(prop.contract_changes),
                contract_reads=shifted(prop.contract_reads), findings=shifted(prop.findings),
            )
            matrix = run.branches and dataclasses.replace(run.branches, rows=shifted(run.branches.rows), files=shifted(run.branches.files))
            before = findings.build(prop, run.branches)
            after = findings.build(for_shift, matrix)
            with self.subTest(scenario=key):
                self.assertTrue(before)
                self.assertEqual([f.id for f in before], [f.id for f in after])
                self.assertTrue(any(b.evidence != a.evidence for b, a in zip(before, after)))  # the evidence moved, the ids did not

    def test_identity_holds_no_line_number_and_no_count(self):
        for key in ("s03", "s05", "s10", "s11", "s13", "s17", "s22"):
            for f in built(key):
                with self.subTest(scenario=key, kind=f.kind):
                    self.assertFalse(any(re.fullmatch(r"\d+", part) or re.search(r":\d+$", part) for part in f.identity), f.identity)
                    self.assertTrue(f.identity[0] == f.kind)

    def test_an_affected_and_an_excluded_claim_about_the_same_place_never_share_an_id(self):
        run = run_of("s03")
        got = built("s03")
        self.assertEqual(len({f.id for f in got}), len(got))
        guard = by_kind(got, "guard-excluded")[0]
        self.assertTrue(guard.is_exclusion)
        self.assertTrue(guard.id.startswith("f-"))

    def test_a_title_with_a_count_is_not_in_the_identity(self):
        a1 = area()
        f1 = findings.direct(a1, findings.Backing.whole(a1))
        more = dataclasses.replace(a1, chains=a1.chains + a1.chains[:1])
        f2 = findings.direct(more, findings.Backing.whole(more))
        self.assertNotEqual(f1.title, f2.title)  # the title carries the number of chains
        self.assertEqual(f1.id, f2.id)


class Wording(unittest.TestCase):
    def all_findings(self):
        for key in ("s01", "s03", "s05", "s06", "s10", "s11", "s13", "s15", "s17", "s20", "s22", "s02", "s12"):
            yield from built(key)

    def test_every_generated_title_workflow_and_basis_passes_the_wording_check(self):
        n = 0
        for f in self.all_findings():
            for text in (f.title, f.workflow, f.confidence_basis):
                self.assertEqual(wording.check(text), [], text)
            n += 1
        self.assertGreater(n, 40)

    def test_a_label_or_path_that_holds_a_banned_word_is_defused_in_the_text_and_kept_in_the_area(self):
        a = area(label="/safe-mode", module="safe", chains=((ev("src/safe/page.ts", 3, "import x", "t/web"), ev("src/routes.tsx", 9, "<Route />", "t/web")),))
        f = findings.direct(a, findings.Backing.whole(a))
        self.assertEqual(wording.check(f.title), [])
        self.assertEqual(wording.check(f.workflow), [])
        self.assertIn("-mode", f.title)  # still readable
        self.assertEqual(f.identity[-1], "/safe-mode")  # the identity is the real label: an id hashes what the thing is called
        self.assertEqual(a.label, "/safe-mode")
        ex = findings.exclusion("t/web", "src/safe.ts", Exclusion("src/safe/page.ts", "named_export_narrowing", (ev("src/safe/page.ts", 1, "import { other } from './safe'", "t/web"),)))
        self.assertEqual(wording.check(ex.title), [])

    def test_a_file_name_that_is_not_utf8_is_shown_escaped_in_an_exclusion(self):
        e = ev("src/page.ts", 1, "import { other } from './x'", "t/web")
        f = findings.exclusion("t/web", "src/caf\udce9.ts", Exclusion("src/z\udce9.ts", "named_export_narrowing", (e,)))
        self.assertIn("caf\\xe9.ts", f.title)
        self.assertEqual(f.identity[2:4], ("src/caf\\xe9.ts", "src/z\\xe9.ts"))
        f.id  # hashable

    def test_no_title_claims_safety_or_coverage(self):
        for f in self.all_findings():
            self.assertNotRegex(f.title.lower(), r"\b(no impact|nothing to test|fully covered)\b")


class Builders(unittest.TestCase):
    def test_a_finding_without_evidence_cannot_be_built(self):
        with self.assertRaises(Exception):
            findings.exclusion("t/web", "a.ts", Exclusion.__new__(Exclusion))  # an Exclusion needs evidence itself
        inp = FindingInput("added_route_uncalled", "low", "medium", "no client names it", (ev(),), ("added_route_uncalled", "t/api", "GET", "/x"))
        self.assertEqual(findings.added_route_uncalled(inp).kind, "added_route_uncalled")
        with self.assertRaises(ValueError):
            FindingInput("added_route_uncalled", "low", "medium", "x", (), ("a",))

    def test_a_finding_input_of_an_unknown_kind_is_refused(self):
        inp = FindingInput("something_else", "low", "medium", "x", (ev(),), ("something_else", "r"))
        with self.assertRaises(ValueError):
            findings.from_input(inp)

    def test_risk_and_confidence_are_never_combined_in_one_field(self):
        removed = by_kind(built("s10"), "removed_route_still_called")[0]
        exclusion = by_kind(built("s05"), "exclusion")[0]
        self.assertEqual((removed.risk.value, removed.confidence.value), ("high", "low"))  # severe and thinly supported
        self.assertEqual((exclusion.risk.value, exclusion.confidence.value), ("low", "high"))  # mild and well supported
        for f in (removed, exclusion):
            self.assertNotIn(f.risk.value + "/" + f.confidence.value, f.title)

    def test_the_finding_constructor_is_the_model_and_the_kinds_are_registered(self):
        f = findings.direct(area(), findings.Backing.whole(area()))
        self.assertIsInstance(f, Finding)
        self.assertEqual((f.kind, f.workflow), ("direct", "orders"))
        self.assertEqual(f.evidence, area().chains[0])
        for kind in findings.TABLE_KINDS:
            self.assertIn(kind, finding_kinds())

    def test_a_by_name_area_with_no_chain_cites_its_route_line(self):
        e = ev("src/routes.tsx", 9, "<Route path='/x' />", "t/web")
        a = Area("/x", "screen", "t/web", "", e, (), (), "candidate", True)
        f = findings.cross_repo(a, "t/api", findings.Backing.by_name("its URL is partial"))
        self.assertEqual((f.evidence, f.confidence.value), ((e,), "low"))
        self.assertEqual(findings.direct(a, findings.Backing.by_name("x")).evidence, (e,))

    def test_a_cross_repo_title_names_where_the_change_came_from(self):
        a = area(repo="t/web")
        f = findings.cross_repo(a, "t/api", findings.Backing.whole(a))
        self.assertIn("t/api", f.title)
        self.assertIn("t/web", f.title)
        self.assertEqual(f.identity, ("cross-repo", "t/web", "screen", "/orders"))


if __name__ == "__main__":
    unittest.main()
