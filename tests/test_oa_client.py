"""M16 OpenAPI, the client side: a repository that calls an API through a generated client. The calls are found by the operationId
method names of the committed spec, in the files that import the generated code, and each call is a URL and a method read from
the spec: the http edge then matches it to a route like any other client call.

Rules tested: the name has to be distinctive (generic words and names defined too often are dropped and recorded), a call is import
backed, the generated code and the tests are not calls, and every way the list could be short (a spec that cannot be read, a
generated client that is missing, a file that does not lex, a re-export that hides users, an operation with no operationId) is a
reason that makes the list incomplete, never a shorter list that reads as the whole one.

The same rules against hand-traced expected.json are in test_oa_scenarios.py.
"""

import unittest

from reachmap.adapters.js import JsAdapter
from reachmap.adapters.openapi.client import OpenApiClient
from tests import scenario_support as ss
from tests import support_openapi as oa
from tests.support_openapi import SPEC_PATH, f, line_of, text_of
from tests.support_universe import Base as UniverseBase

GEN = "src/api/generated"

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

PAGE = f([
    'import { getInvoice, voidInvoice } from "../api/generated/client";',
    "",
    "export function InvoicePage() {",
    '  const load = () => getInvoice("1");',
    '  const cancel = () => voidInvoice("1");',
    "  return load;",
    "}",
])


def web_files(**over):
    files = {
        SPEC_PATH: text_of(oa.spec()),
        f"{GEN}/client.ts": CLIENT,
        "src/pages/InvoicePage.tsx": PAGE,
        "src/pages/CustomerPage.tsx": f(['import { getCustomer } from "../api/generated/client";', "", "export const CustomerPage = () => getCustomer(\"2\");"]),
    }
    for key, value in over.items():
        if value is None:
            files.pop(key, None)
        else:
            files[key] = value
    return files


def keyed(**over):
    """`web_files` with paths as keywords: `keyed(**{"src/x.ts": "..."})`."""
    return web_files(**over)


class Case(UniverseBase):
    UNIVERSE_PREFIX = "reachmap-oa-client-"
    REPO = "t/web"

    def make_adapter(self, **kw):
        kw.setdefault("test_markers", ["__tests__/", ".test.", ".spec."])
        kw.setdefault("distinct_max", 3)
        return JsAdapter(self.REPO, self.url, self.git, **kw)

    def client(self, spec=SPEC_PATH, client=(GEN,), **kw):
        self.language = self.make_adapter(**kw)
        self.oa = OpenApiClient(self.REPO, self.language, spec, list(client))
        return self.oa

    def calls(self, files, spec=SPEC_PATH, client=(GEN,), **kw):
        self.repo({"files": files})
        self.client(spec=spec, client=client, **kw)
        got = self.oa.calls(self.sha("main"))
        problems = ss.evidence_problems({self.REPO: self.path}, [c.evidence for c in got.calls] + [e for c in got.calls for e in c.via])
        self.assertEqual(problems, [])
        return got

    def summary(self, got):
        return sorted((c.path, c.line, c.name, c.local, c.method, c.url.text, c.enclosing, c.form) for c in got.calls)

    def subjects(self, kind):
        return sorted(r.subject for r in self.records if r.kind == kind)


class Calls(Case):
    def test_a_named_import_of_the_generated_function_is_a_call_with_the_method_and_url_of_the_spec(self):
        got = self.calls(web_files())
        self.assertTrue(got.complete, got.reasons)
        self.assertEqual(self.summary(got), [
            ("src/pages/CustomerPage.tsx", 3, "getCustomer", "getCustomer", "GET", "/v1/customers/{}", "CustomerPage", "call"),
            ("src/pages/InvoicePage.tsx", 4, "getInvoice", "getInvoice", "GET", "/v1/invoices/{}", "InvoicePage", "call"),
            ("src/pages/InvoicePage.tsx", 5, "voidInvoice", "voidInvoice", "DELETE", "/v1/invoices/{}", "InvoicePage", "call"),
        ])
        self.assertEqual(got.endpoints, 4)

    def test_the_evidence_is_the_call_line_and_the_via_is_the_spec_operation(self):
        got = self.calls(web_files())
        call = [c for c in got.calls if c.name == "getInvoice"][0]
        self.assertEqual((call.evidence.path, call.evidence.line, call.evidence.detail), ("src/pages/InvoicePage.tsx", 4, '  const load = () => getInvoice("1");'))
        text = text_of(oa.spec())
        self.assertEqual([(e.path, e.line) for e in call.via], [(SPEC_PATH, line_of(text, '"get"', 2)), (SPEC_PATH, line_of(text, '"/invoices/{invoiceId}"')), (SPEC_PATH, line_of(text, '"url"'))])
        self.assertEqual({e.ref for e in call.via}, {self.sha("main")})
        self.assertEqual((call.repo, call.url.partial, call.method_unknown, call.partial), (self.REPO, False, False, False))

    def test_an_aliased_import_is_followed(self):
        page = f(['import { getInvoice as fetchInvoice } from "../api/generated/client";', "", "export function P() {", '  return fetchInvoice("1");', "}"])
        got = self.calls(web_files(**{"src/pages/InvoicePage.tsx": page}))
        (c,) = [c for c in got.calls if c.path == "src/pages/InvoicePage.tsx"]
        self.assertEqual((c.name, c.local, c.line), ("getInvoice", "fetchInvoice", 4))

    def test_a_namespace_import_and_a_class_instance_are_calls_by_the_method_name(self):
        ns = f(['import * as api from "../api/generated/client";', "", "export function A() {", '  return api.getInvoice("1");', "}"])
        klass = f(['import { InvoicesApi } from "../api/generated/client";', "", "export function B() {", "  const api = new InvoicesApi();", '  return api?.voidInvoice("1");', "}"])
        got = self.calls(web_files(**{"src/pages/A.tsx": ns, "src/pages/B.tsx": klass, "src/pages/InvoicePage.tsx": None}))
        self.assertEqual([(c.path, c.line, c.name, c.local, c.enclosing) for c in got.calls if c.path in ("src/pages/A.tsx", "src/pages/B.tsx")],
                         [("src/pages/A.tsx", 4, "getInvoice", "getInvoice", "A"), ("src/pages/B.tsx", 5, "voidInvoice", "voidInvoice", "B")])

    def test_a_reference_that_is_not_a_call_is_a_reference(self):
        page = f(['import { getInvoice } from "../api/generated/client";', "", "export function P() {", "  return useQuery(getInvoice);", "}"])
        got = self.calls(web_files(**{"src/pages/InvoicePage.tsx": page}))
        (c,) = [c for c in got.calls if c.path == "src/pages/InvoicePage.tsx"]
        self.assertEqual(c.form, "reference")

    def test_an_import_through_a_barrel_inside_the_generated_folder_is_followed(self):
        barrel = 'export * from "./client";\n'
        page = f(['import { getInvoice } from "../api/generated";', "", "export function P() {", '  return getInvoice("1");', "}"])
        got = self.calls(web_files(**{f"{GEN}/index.ts": barrel, "src/pages/InvoicePage.tsx": page}))
        self.assertTrue(got.complete, got.reasons)
        self.assertEqual([(c.path, c.name) for c in got.calls if c.path == "src/pages/InvoicePage.tsx"], [("src/pages/InvoicePage.tsx", "getInvoice")])

    def test_an_import_through_a_barrel_outside_the_generated_folder_is_followed_too(self):
        barrel = 'export { getInvoice, voidInvoice } from "./generated/client";\n'
        page = f(['import { getInvoice } from "../api";', "", "export function P() {", '  return getInvoice("1");', "}"])
        got = self.calls(web_files(**{"src/api/index.ts": barrel, "src/pages/InvoicePage.tsx": page}))
        self.assertTrue(got.complete, got.reasons)
        self.assertEqual([(c.path, c.name) for c in got.calls if c.path == "src/pages/InvoicePage.tsx"], [("src/pages/InvoicePage.tsx", "getInvoice")])

    def test_a_file_that_does_not_import_the_generated_code_is_not_a_caller_even_with_the_name(self):
        other = f(["function getInvoice(x: string) {", "  return x;", "}", "", "export const a = getInvoice(\"1\");"])
        got = self.calls(web_files(**{"src/lib/local.ts": other}))
        self.assertNotIn("src/lib/local.ts", {c.path for c in got.calls})
        self.assertTrue(got.complete)

    def test_the_generated_code_and_the_tests_are_not_calls(self):
        test = f(['import { getInvoice } from "../api/generated/client";', "", "it('x', () => getInvoice('1'));"])
        got = self.calls(web_files(**{"src/pages/InvoicePage.test.tsx": test}))
        self.assertTrue(all(not c.path.startswith(GEN) and ".test." not in c.path for c in got.calls))

    def test_generated_code_that_uses_generated_code_is_not_a_caller(self):
        helper = f(['import { getInvoice } from "./client";', "", "export const preload = () => getInvoice(\"1\");"])
        got = self.calls(web_files(**{f"{GEN}/helpers.ts": helper}))
        self.assertTrue(all(not c.path.startswith(GEN) for c in got.calls))
        self.assertTrue(got.complete, got.reasons)

    def test_a_name_in_a_comment_or_a_string_is_not_a_call(self):
        page = f(['import { getInvoice } from "../api/generated/client";', "", "// getInvoice is called below", "export function P() {", '  const s = "getInvoice(1)";', "  return getInvoice;", "}"])
        got = self.calls(web_files(**{"src/pages/InvoicePage.tsx": page}))
        self.assertEqual([c.line for c in got.calls if c.path == "src/pages/InvoicePage.tsx"], [6])

    def test_a_type_only_import_is_not_a_call(self):
        page = f(['import type { getInvoice } from "../api/generated/client";', "", "export type T = typeof getInvoice;"])
        got = self.calls(web_files(**{"src/pages/InvoicePage.tsx": page}))
        self.assertNotIn("src/pages/InvoicePage.tsx", {c.path for c in got.calls})

    def test_the_method_is_the_specs_whatever_the_wrapper_is_called(self):
        page = f(['import { voidInvoice } from "../api/generated/client";', "", "export const go = () => someWrapper(voidInvoice);"])
        got = self.calls(web_files(**{"src/pages/InvoicePage.tsx": page}))
        (c,) = [c for c in got.calls if c.path == "src/pages/InvoicePage.tsx"]
        self.assertEqual(c.method, "DELETE")

    def test_a_server_base_path_is_part_of_the_url_and_a_partial_base_makes_the_url_partial(self):
        doc = oa.spec(servers=[{"url": "https://example.invalid/{base}"}])
        got = self.calls(web_files(**{SPEC_PATH: text_of(doc)}))
        (c,) = [c for c in got.calls if c.name == "getCustomer"]
        self.assertTrue(c.url.partial)
        self.assertEqual(c.url.text, "/<unknown:base>/customers/{}")

    def test_two_servers_give_a_call_for_each_url(self):
        doc = oa.spec(servers=[{"url": "https://a.example.invalid/v1"}, {"url": "https://b.example.invalid/v2"}])
        got = self.calls(web_files(**{SPEC_PATH: text_of(doc)}))
        self.assertEqual(sorted(c.url.text for c in got.calls if c.name == "getCustomer"), ["/v1/customers/{}", "/v2/customers/{}"])


class GenericNames(Case):
    def spec_with(self, edit):
        return text_of(oa.edited(edit))

    def generic_spec(self):
        def edit(d):
            d["paths"]["/ping"] = {"get": {"operationId": "get", "responses": {"200": {"description": "ok"}}}}
            d["paths"]["/reports"] = {"get": {"operationId": "list", "responses": {"200": {"description": "ok"}}}}
        return self.spec_with(edit)

    def files(self, page=None):
        client = CLIENT + f(["", "export function get() {", '  return call("GET", "/ping");', "}", "", "export function list() {", '  return call("GET", "/reports");', "}"])
        cache = f(["export const cache = {", "  get(key: string) {", "    return key;", "  },", "};"])
        page = page or f(['import { list } from "../api/generated/client";', 'import { cache } from "../lib/cache";', "", "export function ReportsPage() {", '  const v = cache.get("k");', "  return list(v);", "}"])
        return web_files(**{SPEC_PATH: self.generic_spec(), f"{GEN}/client.ts": client, "src/lib/cache.ts": cache, "src/pages/ReportsPage.tsx": page})

    def test_a_generic_name_imported_from_the_generated_client_is_a_resolved_call_and_an_unrelated_member_of_the_same_name_is_not(self):
        got = self.calls(self.files())
        reports = [c for c in got.calls if c.path == "src/pages/ReportsPage.tsx"]
        self.assertEqual([(c.name, c.line, c.method, c.url.text) for c in reports], [("list", 6, "GET", "/v1/reports")])  # list(v) is the import; cache.get is not a call of `get`
        self.assertEqual({c.name for c in got.calls}, {"getInvoice", "voidInvoice", "getCustomer", "list"})
        self.assertEqual(self.subjects("not_distinctive"), ["get", "list"])  # still recorded: no member call is matched by these names
        self.assertTrue(all(not r.blocks_selection for r in self.records if r.kind == "not_distinctive"))
        detail = [r for r in self.records if r.subject == "get"][0].detail
        self.assertIn("generic", detail)
        self.assertIn("member", detail)
        self.assertEqual(got.endpoints, 6)  # every operation with an operationId is a name of the generated client

    def test_an_aliased_import_and_a_namespace_import_of_a_generic_name_are_resolved_too(self):
        alias = f(['import { get as ping } from "../api/generated/client";', "", "export const a = () => ping();"])
        ns = f(['import * as api from "../api/generated/client";', "", "export const b = () => api.get();"])
        files = self.files()
        files.update({"src/pages/Alias.tsx": alias, "src/pages/Ns.tsx": ns})
        got = self.calls(files)
        self.assertEqual([(c.path, c.name, c.local) for c in got.calls if c.path in ("src/pages/Alias.tsx", "src/pages/Ns.tsx")],
                         [("src/pages/Alias.tsx", "get", "ping"), ("src/pages/Ns.tsx", "get", "get")])

    def test_a_generic_member_call_with_no_resolved_binding_is_dropped(self):
        klass = f(['import { InvoicesApi } from "../api/generated/client";', "", "export function B() {", "  const api = new InvoicesApi();", "  return api.get();", "}"])
        default = f(['import api from "../api/generated/client";', "", "export const c = () => api.list();"])
        files = self.files()
        files.update({"src/pages/B.tsx": klass, "src/pages/C.tsx": default})
        got = self.calls(files)
        self.assertEqual([c for c in got.calls if c.path in ("src/pages/B.tsx", "src/pages/C.tsx")], [])

    def test_a_name_defined_more_often_than_the_cap_is_a_call_when_imported_and_dropped_as_a_member_with_its_count(self):
        def edit(d):
            d["paths"]["/reports"] = {"get": {"operationId": "renderRow", "responses": {"200": {"description": "ok"}}}}
        client = CLIENT + f(["", "export function renderRow() {", '  return call("GET", "/reports");', "}"])
        files = web_files(**{SPEC_PATH: self.spec_with(edit), f"{GEN}/client.ts": client})
        for i in range(3):
            files[f"src/rows/Row{i}.ts"] = f(["export function renderRow(x: number) {", "  return x;", "}"])
        files["src/pages/ReportsPage.tsx"] = f(['import { renderRow } from "../api/generated/client";', "", "export const r = () => renderRow();"])
        files["src/pages/Member.tsx"] = f(['import { getCustomer } from "../api/generated/client";', "", "export const m = (rows: any) => [getCustomer, rows.renderRow()];"])
        got = self.calls(files)
        self.assertEqual([(c.name, c.line) for c in got.calls if c.path == "src/pages/ReportsPage.tsx"], [("renderRow", 3)])
        self.assertEqual([c.name for c in got.calls if c.path == "src/pages/Member.tsx"], ["getCustomer"])  # rows.renderRow() is by name only
        self.assertEqual(self.subjects("not_distinctive"), ["renderRow"])
        self.assertEqual(self.language.definitions("renderRow", self.sha("main")).count, 4)
        self.assertIn("4", [r for r in self.records if r.subject == "renderRow"][0].detail)

    def test_a_name_at_the_cap_is_still_distinctive_and_matches_by_member_too(self):
        def edit(d):
            d["paths"]["/reports"] = {"get": {"operationId": "renderRow", "responses": {"200": {"description": "ok"}}}}
        client = CLIENT + f(["", "export function renderRow() {", '  return call("GET", "/reports");', "}"])
        files = web_files(**{SPEC_PATH: self.spec_with(edit), f"{GEN}/client.ts": client})
        for i in range(2):
            files[f"src/rows/Row{i}.ts"] = f(["export function renderRow(x: number) {", "  return x;", "}"])  # 3 definitions with the generated one
        files["src/pages/Member.tsx"] = f(['import { getCustomer } from "../api/generated/client";', "", "export const m = (rows: any) => [getCustomer, rows.renderRow()];"])
        got = self.calls(files)
        self.assertEqual({c.name for c in got.calls if c.path == "src/pages/Member.tsx"}, {"getCustomer", "renderRow"})
        self.assertEqual(self.subjects("not_distinctive"), [])

    def test_a_definition_count_that_cannot_be_made_is_an_incomplete_list_and_no_member_call_is_matched_by_the_name(self):
        from unittest import mock
        from reachmap.adapters import DefinitionCount
        member = f(['import { getCustomer } from "../api/generated/client";', "", "export const m = (api: any) => [getCustomer, api.getInvoice()];"])
        self.repo({"files": web_files(**{"src/pages/Member.tsx": member})})
        self.client()
        unreadable = DefinitionCount("getInvoice", self.sha("main"), "unreadable", None, (), False)
        real = self.language.definitions
        with mock.patch.object(self.language, "definitions", lambda name, sha, record_to=None: unreadable if name == "getInvoice" else real(name, sha)):
            got = self.oa.calls(self.sha("main"))
        self.assertIn("src/pages/InvoicePage.tsx", {c.path for c in got.calls if c.name == "getInvoice"})  # the import is a resolved call
        self.assertNotIn("src/pages/Member.tsx", {c.path for c in got.calls if c.name == "getInvoice"})
        self.assertFalse(got.complete)
        self.assertTrue(any("getInvoice" in why for why in got.reasons))

    def test_an_operation_id_that_two_operations_carry_is_not_a_name_at_all(self):
        def edit(d):
            d["paths"]["/other"] = {"get": {"operationId": "getInvoice", "responses": {"200": {"description": "ok"}}}}
        got = self.calls(web_files(**{SPEC_PATH: self.spec_with(edit)}))
        self.assertNotIn("getInvoice", {c.name for c in got.calls})
        self.assertEqual(self.subjects("not_distinctive"), ["getInvoice"])



class Incomplete(Case):
    """A list that could be short says so: the reasons make it incomplete and the http edge turns them into a blocking record."""

    def test_the_generated_client_is_not_in_the_tree(self):
        got = self.calls(web_files(), client=("src/nowhere",))
        self.assertFalse(got.complete)
        self.assertTrue(any("src/nowhere" in why for why in got.reasons))

    def test_an_operation_without_an_operation_id_cannot_be_found(self):
        doc = oa.edited(lambda d: d["paths"]["/customers/{customerId}"]["get"].pop("operationId"))
        got = self.calls(web_files(**{SPEC_PATH: text_of(doc)}))
        self.assertFalse(got.complete)
        self.assertTrue(any("operationId" in why and "GET /customers/{customerId}" in why for why in got.reasons))

    def test_a_spec_that_is_not_there_is_not_json_or_is_yaml(self):
        for files, spec, fragment, kind, blocking in (
                (web_files(**{SPEC_PATH: None}), SPEC_PATH, "not in the tree", "spec_unreadable", True),
                (web_files(**{SPEC_PATH: "{ nope"}), SPEC_PATH, "not valid JSON", "spec_unreadable", True),
                (web_files(**{"api/openapi.yaml": "openapi: 3.0.3\n"}), "api/openapi.yaml", "YAML", "spec_unreadable", True)):
            with self.subTest(spec=spec, fragment=fragment):
                got = self.calls(files, spec=spec)
                self.assertEqual(got.calls, ())
                self.assertFalse(got.complete)
                self.assertTrue(any(fragment in why for why in got.reasons), got.reasons)
                self.assertIn((kind, blocking), [(r.kind, r.blocks_selection) for r in self.records])

    def test_a_file_that_does_not_lex_cleanly_could_hide_a_call(self):
        bad = f(['import { getInvoice } from "../api/generated/client";', "", "export const x = `unterminated;", "export const y = getInvoice;"])
        got = self.calls(web_files(**{"src/pages/Bad.tsx": bad}))
        self.assertFalse(got.complete)
        self.assertTrue(any("Bad.tsx" in why for why in got.reasons))

    def test_a_dynamic_import_with_no_binding_says_which_operations_it_uses_were_not_established(self):
        page = f(["export async function load() {", '  await import("../api/generated/client");', "}"])
        got = self.calls(web_files(**{"src/pages/Lazy.tsx": page}))
        self.assertFalse(got.complete)
        self.assertTrue(any("Lazy.tsx" in why and "dynamic" in why for why in got.reasons))

    def test_a_spec_gap_that_hides_an_operation_is_a_reason_but_an_unresolved_schema_ref_is_not(self):
        bad_path = oa.edited(lambda d: d["paths"].update({"missing-slash": {"get": {"operationId": "other", "responses": {}}}}))
        got = self.calls(web_files(**{SPEC_PATH: text_of(bad_path)}))
        self.assertFalse(got.complete)
        remote = oa.edited(lambda d: d["paths"]["/customers/{customerId}"]["get"].__setitem__("responses", {"200": {"description": "ok", "content": {"application/json": {"schema": {"$ref": "other.json#/C"}}}}}))
        got = self.calls(web_files(**{SPEC_PATH: text_of(remote)}))
        self.assertTrue(got.complete, got.reasons)

    def test_an_importer_search_that_was_cut_is_a_reason_and_never_a_shorter_list_that_reads_as_whole(self):
        from unittest import mock
        from reachmap.adapters import ImporterResult
        self.repo({"files": web_files()})
        self.client()
        real = self.language.importers

        def cut(path, sha, record_to=None):
            got = real(path, sha)
            return ImporterResult(got.target, got.sha, got.edges[:1], False, ("the search was cut short",), got.candidates)

        with mock.patch.object(self.language, "importers", cut):
            got = self.oa.calls(self.sha("main"))
        self.assertFalse(got.complete)
        self.assertTrue(any("cut short" in why for why in got.reasons))


class Repeats(Case):
    def test_calls_are_sorted_and_the_same_adapter_answers_the_same_twice(self):
        self.repo({"files": web_files()})
        self.client()
        a = self.oa.calls(self.sha("main"))
        b = self.oa.calls(self.sha("main"))
        self.assertIs(a, b)
        self.assertEqual(self.summary(a), sorted(self.summary(a)))


if __name__ == "__main__":
    unittest.main()
