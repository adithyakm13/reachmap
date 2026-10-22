"""M16 OpenAPI through the http edge and the registries: a server described by a spec and a client generated from one, followed in both
directions by the walker; which adapter a repository's config selects; the `operation-changed` finding. Small repositories are built per
test (`tests/support_cross.py`); the fixture scenarios are in test_oa_scenarios.py."""

import dataclasses
import unittest

from reachmap import config, entrypoints, findings, wording
from reachmap.adapters.http_client import client_adapter
from reachmap.adapters.js import JsAdapter
from reachmap.adapters.openapi.client import OpenApiClient
from reachmap.adapters.openapi.server import OpenApiServer
from reachmap.adapters.python import PyAdapter
from reachmap.edges import FindingInput
from reachmap.model import Evidence
from tests import support_openapi as oa
from tests.support_cross import CrossCase
from tests.support_openapi import SPEC_PATH, api_files, f, text_of

WORKSPACE = """
workspace = "t"
[limits]
hops = 3
hub_importers = 50
distinct_max = 3

[repos."t/web"]
host = "git"
remote = "{web}"
language = "typescript"
roles = ["frontend"]
branches = ["main"]
entry_points = {{ kind = "react-router", routes_glob = "src/routes.tsx" }}
http_client = {{ kind = "openapi", spec = "api/openapi.json", client = "src/api/generated" }}

[repos."t/api"]
host = "git"
remote = "{api}"
language = "python"
roles = ["backend"]
branches = ["main"]
http_server = {{ kind = "openapi", spec = "api/openapi.json", handlers = "app/handlers/*.py" }}
tests = {{ markers = ["tests/", "test_"] }}

[[edges]]
kind = "http"
from = "t/web"
to = "t/api"
"""

CLIENT = f([
    "function call(method: string, path: string) {",
    "  return fetch(path, { method });",
    "}",
    "",
    "export function listInvoices() {",
    '  return call("GET", "/invoices");',
    "}",
    "",
    "export function getInvoice(invoiceId: string) {",
    '  return call("GET", `/invoices/${invoiceId}`);',
    "}",
    "",
    "export function voidInvoice(invoiceId: string) {",
    '  return call("DELETE", `/invoices/${invoiceId}`);',
    "}",
    "",
    "export function getCustomer(customerId: string) {",
    '  return call("GET", `/customers/${customerId}`);',
    "}",
])
DETAIL = f(['import { getInvoice } from "../api/generated/client";', "", "export default function InvoicePage() {", '  const load = () => getInvoice("1");', "  return load;", "}"])
CUSTOMER = f(['import { getCustomer } from "../api/generated/client";', "", "export default function CustomerPage() {", '  return getCustomer("2");', "}"])
ROUTES = f([
    'import InvoicePage from "./pages/InvoicePage";',
    'import CustomerPage from "./pages/CustomerPage";',
    "",
    "export const routes = (",
    "  <Routes>",
    '    <Route path="/invoices/:invoiceId" element={<InvoicePage />} />',
    '    <Route path="/customers/:customerId" element={<CustomerPage />} />',
    "  </Routes>",
    ");",
])


def web_files(**over):
    files = {
        SPEC_PATH: text_of(oa.spec()), "src/api/generated/client.ts": CLIENT, "src/pages/InvoicePage.tsx": DETAIL, "src/pages/CustomerPage.tsx": CUSTOMER, "src/routes.tsx": ROUTES,
    }
    files.update(over)
    return files


CHANGED_INVOICES = oa.INVOICES.replace("    return None", "    return 1")  # void_invoice
CHANGED_GET = oa.INVOICES.replace("return total_for(invoice_id)", "return total_for(invoice_id) + 1")


class Edge(CrossCase):
    SHARED = True

    def build(self, web=None, api=None, web_change=None, api_change=None):
        web_commits = [{"files": web or web_files()}] + ([{"files": web_change[0], "delete": list(web_change[1:])}] if web_change else [])
        api_commits = [{"files": api or api_files()}] + ([{"files": api_change[0], "delete": list(api_change[1:])}] if api_change else [])
        self.repos(web=web_commits, api=api_commits)
        return self.workspace(WORKSPACE)

    def edges(self, result):
        return sorted((m.client.path, m.client.line, m.route.method, m.route.path, m.kind, m.direction) for m in result.http_edges)


class BothDirections(Edge):
    def test_a_change_to_a_handler_reaches_the_web_calls_of_its_operation_and_nothing_else(self):
        ws = self.build(api_change=({"app/handlers/invoices.py": CHANGED_GET},))
        result = self.propagate(ws, "t/api", "main~1...main")
        self.assertEqual(self.edges(result), [("src/pages/InvoicePage.tsx", 4, "GET", "/v1/invoices/{invoiceId}", "exact", "to_client")])
        self.assertEqual(sorted((r.area.repo, r.area.label) for r in result.reached), [("t/api", "GET /v1/invoices/{invoiceId}"), ("t/web", "/invoices/:invoiceId")])
        self.assertTrue(result.complete, result.reasons)
        self.assert_evidence_resolves(result)
        (screen,) = [r for r in result.reached if r.area.kind == "screen"]
        self.assertEqual([e.path for e in screen.area.chains[0]], ["src/pages/InvoicePage.tsx", "src/routes.tsx", "src/routes.tsx"])

    def test_the_method_of_the_spec_separates_two_operations_of_one_path(self):
        ws = self.build(api_change=({"app/handlers/invoices.py": CHANGED_INVOICES},))
        result = self.propagate(ws, "t/api", "main~1...main")
        self.assertEqual([r.area.label for r in result.reached if r.area.repo == "t/api"], ["DELETE /v1/invoices/{invoiceId}"])
        self.assertEqual(self.edges(result), [])  # nothing in the web app calls voidInvoice: no edge, and nothing says it is uncalled (it is not new)
        self.assertEqual([i.kind for i in result.findings], [])

    def test_a_change_to_the_web_app_reaches_the_route_its_generated_function_calls_and_the_handler_modules(self):
        page = DETAIL.replace('getInvoice("1")', 'getInvoice("2")')
        ws = self.build(web_change=({"src/pages/InvoicePage.tsx": page},))
        result = self.propagate(ws, "t/web", "main~1...main")
        self.assertEqual(self.edges(result), [("src/pages/InvoicePage.tsx", 4, "GET", "/v1/invoices/{invoiceId}", "exact", "to_server")])
        (b,) = result.backend_calls
        self.assertEqual((b.handler, [(m.path, m.depth) for m in b.modules]), ("app/handlers/invoices.py", [("app/services/ledger.py", 1)]))
        self.assertTrue(result.complete, result.reasons)

    def test_a_route_whose_handler_is_not_known_says_so_in_its_backend_call(self):
        page = CUSTOMER.replace('getCustomer("2")', 'getCustomer("3")')
        ws = self.build(web_change=({"src/pages/CustomerPage.tsx": page},), api=api_files(customers=None))
        result = self.propagate(ws, "t/web", "main~1...main")
        (b,) = result.backend_calls
        self.assertEqual((b.modules, b.complete), ((), False))
        self.assertTrue(any("handler" in why and "not known" in why for why in b.reasons), b.reasons)

    def test_a_removed_operation_that_a_generated_client_still_calls_is_a_finding_with_both_ends(self):
        doc = oa.edited(lambda d: d["paths"].pop("/customers/{customerId}"))
        ws = self.build(api_change=({SPEC_PATH: text_of(doc)}, "app/handlers/customers.py"))
        result = self.propagate(ws, "t/api", "main~1...main")
        (inp,) = result.findings
        self.assertEqual((inp.kind, inp.risk, inp.confidence), ("removed_route_still_called", "high", "low"))
        self.assertEqual([(e.repo, e.path) for e in inp.evidence], [("t/web", "src/pages/CustomerPage.tsx"), ("t/api", SPEC_PATH)])
        self.assertEqual(inp.evidence[1].ref, self.sha("api", "main~1"))  # the removed method key, at the base
        self.assertEqual([r.area.label for r in result.reached], ["/customers/:customerId"])
        self.assert_evidence_resolves(result)

    def test_a_path_item_split_into_another_file_is_not_a_removed_route_and_no_finding_is_made_of_it(self):
        doc = oa.edited(lambda d: d["paths"].__setitem__("/customers/{customerId}", {"$ref": "paths/customers.json"}))
        ws = self.build(api_change=({SPEC_PATH: text_of(doc)},))
        result = self.propagate(ws, "t/api", "main~1...main")
        self.assertEqual([i.kind for i in result.findings], [])
        self.assertEqual(result.http_edges, ())
        self.assertIn(("spec_unreadable", True), {(r.kind, r.blocks_selection) for r in result.records})
        self.assertFalse(result.complete)

    def test_a_generic_named_operation_that_the_web_app_imports_and_calls_is_a_resolved_edge_and_its_removal_is_a_finding(self):
        ping = {"/ping": {"get": {"operationId": "get", "responses": {"200": {"description": "ok"}}}}}
        base_doc = oa.edited(lambda d: d["paths"].update(ping))
        head_doc = oa.spec()
        client = CLIENT + f(["", "export function get() {", '  return call("GET", "/ping");', "}"])
        page = f(['import { get } from "../api/generated/client";', "", "export default function PingPage() {", "  return get();", "}"])
        routes = ROUTES.replace('import CustomerPage', 'import PingPage from "./pages/PingPage";\nimport CustomerPage').replace("  </Routes>", '    <Route path="/ping" element={<PingPage />} />\n  </Routes>')
        web = web_files(**{SPEC_PATH: text_of(base_doc), "src/api/generated/client.ts": client, "src/pages/PingPage.tsx": page, "src/routes.tsx": routes})
        api = api_files(**{SPEC_PATH: text_of(base_doc), "app/handlers/system.py": "def get():\n    return 'pong'\n"})
        ws = self.build(web=web, api=api, api_change=({SPEC_PATH: text_of(head_doc)},))
        result = self.propagate(ws, "t/api", "main~1...main")
        (inp,) = result.findings
        self.assertEqual((inp.kind, inp.risk, inp.confidence), ("removed_route_still_called", "high", "low"))
        self.assertEqual(inp.evidence[0].path, "src/pages/PingPage.tsx")
        self.assertEqual([r.area.label for r in result.reached], ["/ping"])
        self.assert_evidence_resolves(result)

    def test_a_changed_operation_is_an_operation_changed_input_and_its_client_is_reached(self):
        doc = oa.edited(lambda d: d["paths"]["/invoices/{invoiceId}"]["get"].__setitem__("parameters", [{"name": "expand", "in": "query", "required": True, "schema": {"type": "string"}}]))
        ws = self.build(api_change=({SPEC_PATH: text_of(doc)},))
        result = self.propagate(ws, "t/api", "main~1...main")
        self.assertEqual([(i.kind, i.identity[-2:]) for i in result.findings], [("operation-changed", ("GET", "/v1/invoices/{invoiceId}"))])
        self.assertEqual(self.edges(result), [("src/pages/InvoicePage.tsx", 4, "GET", "/v1/invoices/{invoiceId}", "exact", "to_client")])
        self.assert_evidence_resolves(result)

    def test_an_added_operation_nobody_calls_is_said_once_every_client_was_read_completely(self):
        doc = oa.edited(lambda d: d["paths"].update({"/invoices/{invoiceId}/pdf": {"get": {"operationId": "getInvoicePdf", "responses": {"200": {"description": "ok"}}}}}))
        text = oa.INVOICES + "\n\ndef get_invoice_pdf(invoice_id):\n    return None\n"
        ws = self.build(api_change=({SPEC_PATH: text_of(doc), "app/handlers/invoices.py": text},))
        result = self.propagate(ws, "t/api", "main~1...main")
        self.assertEqual([(i.kind, i.risk, i.confidence) for i in result.findings], [("added_route_uncalled", "low", "medium")])
        self.assertTrue(result.complete, result.reasons)

    def test_a_client_url_that_fits_no_route_is_recorded_and_no_added_route_is_called_uncalled(self):
        """The web app's copy of the spec has another base path (`/api/v1`): its calls fit no route of the server. Nothing is matched
        silently, and a route the change adds is not said to be uncalled while a call could not be placed."""
        web_doc = oa.spec(servers=[{"url": "https://api.example.invalid/api/v1"}])
        doc = oa.edited(lambda d: d["paths"].update({"/invoices/{invoiceId}/pdf": {"get": {"operationId": "getInvoicePdf", "responses": {"200": {"description": "ok"}}}}}))
        text = oa.INVOICES + "\n\ndef get_invoice_pdf(invoice_id):\n    return None\n"
        ws = self.build(web=web_files(**{SPEC_PATH: text_of(web_doc)}), api_change=({SPEC_PATH: text_of(doc), "app/handlers/invoices.py": text},))
        result = self.propagate(ws, "t/api", "main~1...main")
        self.assertEqual(result.findings, ())
        self.assertEqual(result.http_edges, ())
        self.assertIn("client_url_unmatched", {r.kind for r in result.records})
        self.assertTrue(any(r.kind == "edge_incomplete" and r.blocks_selection for r in result.records))
        self.assertFalse(result.complete)

    def test_a_client_whose_base_path_is_not_known_is_a_candidate_by_name(self):
        web_doc = oa.spec(servers=[{"url": "https://example.invalid/{base}"}])
        ws = self.build(web=web_files(**{SPEC_PATH: text_of(web_doc)}), api_change=({"app/handlers/invoices.py": CHANGED_GET},))
        result = self.propagate(ws, "t/api", "main~1...main")
        self.assertTrue(result.http_edges and all(m.candidate for m in result.http_edges))
        self.assertIn("client_url_partial", {r.kind for r in result.records})
        screens = [r for r in result.reached if r.area.kind == "screen"]
        self.assertTrue(screens and all(r.area.by_name and r.area.priority == "candidate" for r in screens))

    def test_a_server_with_no_spec_at_the_head_is_a_blocking_edge_and_a_blocking_record(self):
        ws = self.build(api_change=({"app/handlers/invoices.py": CHANGED_GET}, SPEC_PATH))
        result = self.propagate(ws, "t/api", "main~1...main")
        self.assertFalse(result.complete)
        self.assertIn(("spec_unreadable", True), {(r.kind, r.blocks_selection) for r in result.records})
        self.assertEqual(result.http_edges, ())


class Registries(unittest.TestCase):
    def repo(self, **sections):
        text = """
workspace = "t"
[repos."t/api"]
host = "git"
remote = "/nowhere/api"
language = "python"
roles = ["backend"]
""" + "\n".join(f"{k} = {v}" for k, v in sections.items())
        return config.loads(text, fmt="toml", env={}).repos["t/api"]

    def lang(self, repo):
        return PyAdapter.__new__(PyAdapter)

    def test_http_server_selects_the_openapi_adapter(self):
        repo = self.repo(http_server='{ kind = "openapi", spec = "api/openapi.json", handlers = ["app/handlers/*.py", "app/more/*.py"] }')
        entry = entrypoints.entry_adapter(repo, self.lang(repo))
        self.assertIsInstance(entry, OpenApiServer)
        self.assertEqual((entry.spec_path, entry.handler_globs, entry.repo), ("api/openapi.json", ("app/handlers/*.py", "app/more/*.py"), "t/api"))

    def test_a_celery_entry_points_has_no_adapter_and_the_spec_is_the_source_of_routes(self):
        celery = self.repo(entry_points='{ kind = "celery" }', http_server='{ kind = "openapi", spec = "x.json" }')
        self.assertEqual(entrypoints.entry_adapter(celery, self.lang(celery)).kind, "openapi")
        neither = self.repo()
        self.assertIsNone(entrypoints.entry_adapter(neither, self.lang(neither)))

    def test_http_client_selects_the_openapi_client(self):
        text = """
workspace = "t"
[repos."t/web"]
host = "git"
remote = "/nowhere/web"
language = "typescript"
roles = ["frontend"]
http_client = { kind = "openapi", spec = "api/openapi.json", client = ["src/gen/a.ts", "src/gen/b"] }
"""
        repo = config.loads(text, fmt="toml", env={}).repos["t/web"]
        client = client_adapter(repo, JsAdapter.__new__(JsAdapter))
        self.assertIsInstance(client, OpenApiClient)
        self.assertEqual((client.spec_path, client.client), ("api/openapi.json", ("src/gen/a.ts", "src/gen/b")))

    def test_the_kind_is_registered_where_a_report_reader_finds_it(self):
        self.assertIn("openapi", entrypoints.KINDS)
        from reachmap.model import finding_kinds
        self.assertIn("operation-changed", finding_kinds())
        self.assertIn("operation-changed", findings.TABLE_KINDS)


class OperationChangedFinding(unittest.TestCase):
    def input(self, **kw):
        ev = lambda ref, n: Evidence("t/api", "api/openapi.json", n, ref * 40, '      "get": {')  # noqa: E731
        base = dict(kind="operation-changed", risk="medium", confidence="high", confidence_basis="both versions of the spec were read",
                    evidence=(ev("a", 72), ev("b", 72)), identity=("operation-changed", "t/api", "GET", "/v1/invoices/{invoiceId}"), detail="GET /v1/invoices/{invoiceId} differs in: parameters")
        base.update(kw)
        return FindingInput(**base)

    def test_the_builder_makes_a_finding_of_the_kind_with_both_versions_as_evidence(self):
        got = findings.from_input(self.input())
        self.assertEqual((got.kind, got.risk.value, got.confidence.value, got.is_exclusion), ("operation-changed", "medium", "high", False))
        self.assertEqual([e.ref for e in got.evidence], ["a" * 40, "b" * 40])
        self.assertEqual(got.title, "The spec changes the contract of GET /v1/invoices/{invoiceId} in t/api")
        self.assertIn("differs in: parameters", got.confidence_basis)
        self.assertEqual(wording.check(got.title) + wording.check(got.confidence_basis), [])

    def test_the_identity_holds_no_line_and_no_count_so_a_decision_carries_forward(self):
        a = findings.from_input(self.input())
        moved = self.input(evidence=tuple(dataclasses.replace(e, line=90, ref="c" * 40) for e in self.input().evidence), detail="differs in: parameters, responses")
        b = findings.from_input(moved)
        self.assertEqual(a.id, b.id)

    def test_a_route_called_safe_is_defused_in_the_title_and_intact_in_the_identity(self):
        inp = self.input(identity=("operation-changed", "t/api", "GET", "/v1/safe"))
        got = findings.from_input(inp)
        self.assertEqual(wording.check(got.title), [])
        self.assertEqual(got.identity[-1], "/v1/safe")

    def test_an_input_needs_evidence_and_an_unknown_kind_has_no_builder(self):
        with self.assertRaises(ValueError):
            self.input(evidence=())
        with self.assertRaises(ValueError):
            findings.from_input(self.input(kind="made-up"))


if __name__ == "__main__":
    unittest.main()
