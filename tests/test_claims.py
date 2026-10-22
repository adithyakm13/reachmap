"""Claims on evidence (M11): the adapters say what an import hop, a route line and a definition line claim, apart from how the line is
spelled, so the fingerprint of an area or a finding follows the claim. Real runs on the M1 fixtures, read from the report."""

import unittest

from tests import support_report as sr

S01 = ("--repo", "acme/web", "--compare", "main...scenario/s01-shared-component")
S15 = ("--repo", "acme/api", "--compare", "main...scenario/s15-python-callers")
BADGE = "import:src/components/StatusBadge.tsx:StatusBadge"


def claims(evidence):
    return [e.get("claim") for e in evidence]


class JavaScriptRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = sr.real(*S01)

    def area(self, label):
        return next(a for a in self.report["areas"] if a["label"] == label)

    def test_a_chain_is_the_import_of_the_changed_file_the_import_of_the_page_and_the_route_line_each_with_its_claim(self):
        a = self.area("/billing/invoices")
        page = "src/modules/billing/pages/InvoiceListPage.tsx"
        self.assertEqual(claims(a["chains"][0]), [BADGE, f"import:{page}:*", f"route:/billing/invoices:{page}"])

    def test_the_route_line_of_the_area_has_the_same_claim_as_the_last_line_of_its_chain(self):
        for a in self.report["areas"]:
            self.assertEqual(a["route_line"].get("claim"), a["chains"][0][-1].get("claim"))
            self.assertTrue(a["route_line"]["claim"].startswith("route:" + a["label"] + ":"))

    def test_a_screen_mounted_twice_has_a_route_claim_per_mount(self):
        a = self.area("/billing/invoices/:invoiceId")
        self.assertEqual([c[-1]["claim"] for c in a["chains"]], [
            "route:/billing/invoices/:invoiceId:src/modules/billing/pages/InvoiceDetailPage.tsx",
            "route:/cases/:caseId/invoices/:invoiceId:src/modules/billing/pages/InvoiceDetailPage.tsx"])

    def test_a_finding_cites_the_definition_line_first_with_the_symbol_as_its_claim_and_then_the_chain(self):
        for f in self.report["findings"]:
            self.assertEqual(f["evidence"][0]["claim"], "def:StatusBadge")
            self.assertEqual(f["evidence"][0]["path"], "src/components/StatusBadge.tsx")
            self.assertEqual(claims(f["evidence"][1:2]), [BADGE])

    def test_the_text_of_the_line_is_still_in_the_detail_for_display(self):
        a = self.area("/cases/:caseId")
        self.assertEqual(a["chains"][0][0]["detail"], 'import { StatusBadge } from "@components/StatusBadge";')

    def test_the_alias_and_the_relative_import_of_the_same_file_have_the_same_claim(self):
        details = {a["chains"][0][0]["detail"]: a["chains"][0][0]["claim"] for a in self.report["areas"]}
        self.assertGreaterEqual(len(details), 3)  # the alias @/, the alias @components/ and the relative path of the fixtures
        self.assertEqual(set(details.values()), {BADGE})


class PythonRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = sr.real(*S15)

    def test_an_api_route_is_claimed_with_its_method_path_and_handler_file_and_the_import_with_the_file_and_the_name(self):
        a = next(x for x in self.report["areas"] if x["label"] == "GET /api/billing/invoices/{invoice_id}")
        self.assertEqual(a["route_line"]["claim"], "route:GET /api/billing/invoices/{invoice_id}:services/billing/app/routers/invoices.py")
        chain = a["chains"][0]
        self.assertEqual(chain[0]["claim"], "import:libs/common/acme_common/money.py:format_cents")
        self.assertEqual(chain[1]["claim"], a["route_line"]["claim"])
        self.assertTrue(all("claim" not in e for e in chain[2:]))  # the mounts are not claimed

    def test_the_definition_line_of_the_changed_function_is_first_in_a_finding(self):
        for f in self.report["findings"]:
            if f["kind"] in ("direct", "cross-repo"):
                self.assertEqual(f["evidence"][0]["claim"], "def:format_cents")


class EveryReport(unittest.TestCase):
    def test_a_claim_is_a_one_line_text_with_a_known_role_in_every_scenario_report(self):
        from tests import scenario_support as ss
        from tests import support_cli
        from tests.fixtures import build

        seen = set()
        for sc in build.scenarios(ss.env().dest):
            data = support_cli.scenario_run(sc).report
            items = [e for a in data["areas"] for e in [a["route_line"], *(x for c in a["chains"] for x in c)]] + [e for f in data["findings"] for e in f["evidence"]]
            for e in items:
                if "claim" in e:
                    self.assertRegex(e["claim"], r"^(import|route|def|wraps):[^\n]+$")
                    seen.add(e["claim"].split(":")[0])
        self.assertEqual(seen, {"import", "route", "def"})  # the M1 fixtures have no wrapper: `wraps` is the Next.js adapter's, below

    def test_the_line_a_wrapper_wraps_the_routes_through_has_the_wraps_role_in_the_nextjs_reports(self):
        import re

        from tests import support_nx as sn

        roles = {}
        for sc in sn.scenarios():
            if sc["id"].split("-")[0] in ("nx02", "nx07", "nx15", "nx17"):
                data = sn.scenario_run(sc).report
                for a in data["areas"]:
                    for e in [a["route_line"], *(x for c in a["chains"] for x in c)]:
                        if "claim" in e:
                            self.assertRegex(e["claim"], r"^(import|route|def|wraps):[^\n]+$")
                            roles.setdefault(e["claim"].split(":")[0], []).append(e["claim"])
        self.assertEqual(set(roles), {"import", "route", "wraps"})
        self.assertTrue(all(re.match(r"^wraps:src/", c) for c in roles["wraps"]))


if __name__ == "__main__":
    unittest.main()
