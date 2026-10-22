import hashlib
import unittest

from reachmap import identity
from reachmap.errors import ModelError
from tests.support import SHA, SHA2, area, ev, finding


LONE_SURROGATE = b"\xff".decode("utf-8", "surrogateescape")


class IdsTest(unittest.TestCase):
    def test_canonical_json_refuses_nan_and_infinity(self):
        for bad in (float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                identity.canonical_json({"a": bad})

    def test_a_lone_surrogate_is_refused_with_advice_instead_of_crashing_later(self):
        with self.assertRaises(ModelError) as cm:
            identity.finding_id("direct", ("a" + LONE_SURROGATE,))
        self.assertIn("replace", str(cm.exception))
        for call in (
            lambda: identity.finding_id("direct" + LONE_SURROGATE, ("a",)),
            lambda: identity.area_id("screen", "repo", "/x" + LONE_SURROGATE),
            lambda: identity.area_id("screen", "repo" + LONE_SURROGATE, "/x"),
        ):
            with self.assertRaises(ModelError):
                call()

    def test_canonical_json_is_sorted_compact_and_keeps_unicode(self):
        self.assertEqual(identity.canonical_json({"b": 1, "a": ["x", "é"]}), '{"a":["x","é"],"b":1}')

    def test_finding_id_has_prefix_and_sixteen_hex_chars(self):
        fid = identity.finding_id("direct", ("acme/web", "src/a.ts", "StatusBadge"))
        self.assertRegex(fid, r"^f-[0-9a-f]{16}$")

    def test_finding_id_is_the_truncated_sha256_of_canonical_json(self):
        fid = identity.finding_id("direct", ("a", "b"))
        expected = hashlib.sha256(identity.canonical_json(["finding", "direct", ["a", "b"]]).encode()).hexdigest()
        self.assertEqual(fid, "f-" + expected[:16])

    def test_finding_id_depends_on_kind_and_identity_only(self):
        base = identity.finding_id("direct", ("a", "b"))
        self.assertEqual(base, identity.finding_id("direct", ["a", "b"]))
        self.assertNotEqual(base, identity.finding_id("duplicate", ("a", "b")))
        self.assertNotEqual(base, identity.finding_id("direct", ("a", "c")))
        self.assertNotEqual(base, identity.finding_id("direct", ("b", "a")))

    def test_finding_id_rejects_a_bare_string_identity(self):
        with self.assertRaises(ModelError):
            identity.finding_id("direct", "abc")

    def test_finding_id_rejects_non_string_parts(self):
        with self.assertRaises(ModelError):
            identity.finding_id("direct", ("a", 3))

    def test_area_id_has_prefix_and_differs_from_a_finding_id(self):
        aid = identity.area_id("screen", "acme/web", "/cases")
        self.assertRegex(aid, r"^a-[0-9a-f]{16}$")
        self.assertEqual(aid, identity.area_id("screen", "acme/web", "/cases"))
        self.assertNotEqual(aid, identity.area_id("screen", "acme/web", "/reports"))
        self.assertNotEqual(aid, identity.area_id("api_route", "acme/web", "/cases"))
        self.assertNotEqual(aid[2:], identity.finding_id("screen", ("acme/web", "/cases"))[2:])


class FindingIdTest(unittest.TestCase):
    def test_findings_with_same_kind_and_identity_but_different_titles_share_an_id(self):
        one = finding(title="StatusBadge reaches 2 screens")
        two = finding(title="StatusBadge reaches 3 screens")
        self.assertNotEqual(one.title, two.title)
        self.assertEqual(one.id, two.id)

    def test_id_ignores_risk_confidence_basis_and_evidence_refs(self):
        base = finding()
        other = finding(
            risk="high", confidence="low", confidence_basis="another basis",
            evidence=[ev(ref=SHA2, line=99, detail="different line")],
        )
        self.assertEqual(base.id, other.id)

    def test_id_changes_with_kind_or_identity(self):
        self.assertNotEqual(finding().id, finding(kind="duplicate").id)
        self.assertNotEqual(finding().id, finding(identity=["direct", "acme/web", "src/a.ts", "Other"]).id)


class FingerprintTest(unittest.TestCase):
    def test_fingerprint_is_a_sha256_hex(self):
        self.assertRegex(identity.fingerprint(finding()), r"^[0-9a-f]{64}$")

    def test_fingerprint_unchanged_when_only_the_evidence_ref_changes(self):
        moved = finding(evidence=[ev(ref=SHA2)])
        self.assertEqual(identity.fingerprint(finding()), identity.fingerprint(moved))

    def test_fingerprint_unchanged_when_only_the_line_number_moves(self):
        moved = finding(evidence=[ev(line=40)])
        self.assertEqual(identity.fingerprint(finding()), identity.fingerprint(moved))

    def test_fingerprint_unchanged_by_title_risk_and_confidence(self):
        other = finding(title="StatusBadge reaches 9 screens", risk="high", confidence="low")
        self.assertEqual(identity.fingerprint(finding()), identity.fingerprint(other))

    def test_fingerprint_unchanged_by_surrounding_whitespace_in_the_detail(self):
        padded = finding(evidence=[ev(detail="  import x from './y'  ")])
        self.assertEqual(identity.fingerprint(finding()), identity.fingerprint(padded))

    def test_fingerprint_changes_when_the_detail_line_changes(self):
        edited = finding(evidence=[ev(detail="import x from './z'")])
        self.assertNotEqual(identity.fingerprint(finding()), identity.fingerprint(edited))

    def test_fingerprint_changes_with_path_repo_kind_identity_or_extra_evidence(self):
        base = identity.fingerprint(finding())
        for other in (
            finding(evidence=[ev(path="src/other.ts")]),
            finding(evidence=[ev(repo="acme/api")]),
            finding(kind="duplicate"),
            finding(identity=["direct", "x"]),
            finding(evidence=[ev(), ev(path="src/b.ts", detail="more")]),
        ):
            self.assertNotEqual(base, identity.fingerprint(other))

    def test_fingerprint_keeps_duplicates_two_identical_call_sites_are_two_claims(self):
        one = finding(evidence=[ev(line=3)])
        two = finding(evidence=[ev(line=3), ev(line=9)])
        self.assertNotEqual(identity.fingerprint(one), identity.fingerprint(two))

    def test_fingerprint_ignores_the_order_of_the_evidence(self):
        a, b = ev(), ev(path="src/b.ts", detail="more")
        self.assertEqual(
            identity.fingerprint(finding(evidence=[a, b])), identity.fingerprint(finding(evidence=[b, a]))
        )

    def test_area_fingerprint_follows_the_chain_not_the_priority_or_the_commit(self):
        base = identity.fingerprint(area())
        self.assertEqual(base, identity.fingerprint(area(priority="sample")))
        moved = area(chains=[[ev(ref=SHA2, line=70), ev(path="src/b.ts", line=11, ref=SHA2, detail="import a from './a'")]])
        self.assertEqual(base, identity.fingerprint(moved))
        changed = area(chains=[[ev(detail="import changed"), ev(path="src/b.ts", detail="import a from './a'")]])
        self.assertNotEqual(base, identity.fingerprint(changed))

    def test_a_finding_and_an_area_never_share_a_fingerprint(self):
        self.assertNotEqual(identity.fingerprint(finding(kind="direct")), identity.fingerprint(area()))


IMPORT = "import:src/components/StatusBadge.tsx:StatusBadge"
ROUTE = "route:/cases:src/pages/CaseListPage.tsx"


class ClaimTest(unittest.TestCase):
    """An Evidence may carry a `claim` (the adapters set it where a line says more than its spelling: an import, a route, a definition).
    The fingerprint hashes the claim when there is one and the stripped detail otherwise, per line."""

    def fp(self, *evidence):
        return identity.fingerprint(finding(evidence=list(evidence)))

    def test_without_a_claim_the_fingerprint_is_what_it_always_was(self):
        self.assertEqual(identity.fingerprint(finding()), "69af43a846bc9f4ed8d9f0016737ca8ffc08710efae6028fd0c03205fa049455")
        self.assertEqual(self.fp(ev(claim=None)), self.fp(ev()))

    def test_an_import_hop_is_unchanged_when_only_the_spelling_of_the_specifier_or_the_formatting_changes(self):
        alias = ev(detail='import { StatusBadge } from "@components/StatusBadge";', claim=IMPORT)
        relative = ev(detail="import {StatusBadge}   from '../../../components/StatusBadge'", claim=IMPORT)
        self.assertEqual(self.fp(alias), self.fp(relative))

    def test_an_import_hop_changes_when_the_resolved_target_or_the_names_followed_change(self):
        base = self.fp(ev(detail="import { StatusBadge } from 'x'", claim=IMPORT))
        for claim in ("import:src/components/index.ts:StatusBadge", "import:src/components/StatusBadge.tsx:StatusBadge,Other", "import:src/components/StatusBadge.tsx:*"):
            self.assertNotEqual(base, self.fp(ev(detail="import { StatusBadge } from 'x'", claim=claim)), claim)

    def test_a_route_line_changes_when_the_path_or_the_component_file_change_not_when_it_is_reformatted(self):
        route = ev(path="src/routes.tsx", detail='<Route path="/cases" element={<CaseListPage />} />', claim=ROUTE)
        reformatted = ev(path="src/routes.tsx", detail="<Route   path='/cases'   element={ <CaseListPage/> } />", claim=ROUTE)
        self.assertEqual(self.fp(route), self.fp(reformatted))
        for claim in ("route:/cases/:id:src/pages/CaseListPage.tsx", "route:/cases:src/pages/CaseDetailPage.tsx"):
            self.assertNotEqual(self.fp(route), self.fp(ev(path="src/routes.tsx", detail=route.detail, claim=claim)), claim)

    def test_a_definition_line_changes_with_the_symbol_not_with_its_signature_text(self):
        one = ev(detail="export function StatusBadge({ status }: Props) {", claim="def:StatusBadge")
        two = ev(detail="export function StatusBadge({ status, size }: Props) {", claim="def:StatusBadge")
        self.assertEqual(self.fp(one), self.fp(two))
        self.assertNotEqual(self.fp(one), self.fp(ev(detail=one.detail, claim="def:Badge")))

    def test_a_line_with_a_claim_and_the_same_line_without_one_are_different_claims_about_it(self):
        self.assertNotEqual(self.fp(ev(claim=IMPORT)), self.fp(ev()))

    def test_the_claim_still_hashes_with_the_path_and_the_repo(self):
        self.assertNotEqual(self.fp(ev(claim=IMPORT)), self.fp(ev(path="src/other.ts", claim=IMPORT)))
        self.assertNotEqual(self.fp(ev(claim=IMPORT)), self.fp(ev(repo="acme/api", claim=IMPORT)))

    def test_the_commit_and_the_line_number_still_do_not_matter(self):
        self.assertEqual(self.fp(ev(claim=IMPORT)), self.fp(ev(claim=IMPORT, ref=SHA2, line=77)))

    def test_an_area_follows_the_claims_of_its_chain(self):
        a = area(chains=[[ev(detail="import a from '@/a'", claim=IMPORT), ev(path="src/b.ts", detail="import a from './a'", claim="import:src/a.ts:a")]])
        b = area(chains=[[ev(detail="import a from '../a'", claim=IMPORT), ev(path="src/b.ts", detail="import   a from \"./a\"", claim="import:src/a.ts:a")]])
        c = area(chains=[[ev(detail="import a from '@/a'", claim=IMPORT), ev(path="src/b.ts", detail="import a from './a'", claim="import:src/other.ts:a")]])
        self.assertEqual(identity.fingerprint(a), identity.fingerprint(b))
        self.assertNotEqual(identity.fingerprint(a), identity.fingerprint(c))

    def test_the_evidence_triples_of_a_claim_and_of_a_detail_never_collide(self):
        self.assertNotEqual(self.fp(ev(detail="x", claim=None)), self.fp(ev(detail="y", claim="x")))


class GoldenTest(unittest.TestCase):
    """Pinned values: a change here detaches every stored decision, so it must be deliberate."""

    def test_finding_id(self):
        self.assertEqual(identity.finding_id("direct", ("acme/web", "src/a.ts", "StatusBadge")), "f-e1a50252f58202ae")

    def test_area_id(self):
        self.assertEqual(identity.area_id("screen", "acme/web", "/cases"), "a-fe06a0e8ed4389b0")

    def test_finding_fingerprint(self):
        self.assertEqual(identity.fingerprint(finding()), "69af43a846bc9f4ed8d9f0016737ca8ffc08710efae6028fd0c03205fa049455")

    def test_the_fingerprint_of_a_finding_whose_evidence_carries_claims(self):
        f = finding(evidence=[ev(detail="import { StatusBadge } from '@/c'", claim=IMPORT), ev(path="src/routes.tsx", detail="<Route />", claim=ROUTE)])
        self.assertEqual(identity.fingerprint(f), "7afc314ea09bab2dbfd85f67dfe85dd5b3918b48a30aad73f2702a274977d3aa")

    def test_area_fingerprint(self):
        self.assertEqual(identity.fingerprint(area()), "1a0f6f9199a1d961b20542fe6de0bfdca9a40bb58eefead616b4a74e82cca6b7")


if __name__ == "__main__":
    unittest.main()
