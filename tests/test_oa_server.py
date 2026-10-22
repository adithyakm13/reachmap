"""M16 OpenAPI, the server side: the entry-point adapter of a repository whose routes are described by a committed spec.

Small repositories built for one rule each: the route table (full paths from `servers`, evidence at the spec line, handlers
found by operationId), what a change reaches (a handler body, a service function through the walk, a spec change by
signature), and every way a read can fail (a spec that is absent, not JSON, YAML, not OpenAPI; a handler file that does not
parse; handlers not configured): each is a record and an answer that says it is not complete, never an empty list that
looks like a finding of no routes.

The M16 scenarios (the same rules against hand-traced expected.json) are in test_oa_scenarios.py.
"""

import copy
import dataclasses
import json
import unittest

from reachmap import entrypoints
from reachmap.adapters.openapi.server import OpenApiServer
from reachmap.adapters.python import PyAdapter
from reachmap.edges import FindingInput
from tests import scenario_support as ss
from tests import support_openapi as oa
from tests.support_openapi import HANDLERS, SPEC_PATH, api_files, edited, line_of, text_of
from tests.support_universe import Base as UniverseBase


CHANGED_INVOICES = oa.INVOICES.replace("    return None", "    return 1")  # void_invoice
CHANGED_GET = oa.INVOICES.replace("return total_for(invoice_id)", "return total_for(invoice_id) + 1")
CHANGED_CUSTOMERS = oa.CUSTOMERS.replace("return {}", "return {'a': 1}")


class Case(UniverseBase):
    UNIVERSE_PREFIX = "reachmap-oa-server-"
    REPO = "t/api"

    def make_adapter(self, **kw):
        kw.setdefault("test_markers", ["tests/", "test_"])
        return PyAdapter(self.REPO, self.url, self.git, **kw)

    def server(self, spec=SPEC_PATH, handlers=(HANDLERS,), **kw):
        self.language = self.make_adapter(**kw)
        self.oa = OpenApiServer(self.REPO, self.language, spec, list(handlers))
        return self.oa

    def head(self, changed, delete=(), base=None):
        """The case: the files of a small API, then a commit with `changed` on top (and `delete` removed)."""
        self.repo({"files": base if base is not None else api_files()}, {"files": changed, "delete": list(delete)})
        return self.server()

    def reach(self, **kw):
        analysis = self.analyze(self.language, **kw)
        result = self.last = entrypoints.reach(analysis, self.language, self.oa)
        evidence = [e for r in result.reached for c in r.area.chains for e in c]
        self.assertEqual(ss.evidence_problems({self.REPO: self.path}, evidence), [])
        return result

    def inputs(self):
        """The `operation-changed` inputs of the last `reach` (the entry result carries them)."""
        return list(self.last.findings)

    def labels(self, result):
        return sorted(r.area.label for r in result.reached)

    def kind_subjects(self):
        return sorted((r.kind, r.subject, r.blocks_selection) for r in self.records)

    def route(self, label, sha="main"):
        (r,) = [r for r in self.oa.routes(None, self.sha(sha)) if r.label == label]
        return r


class RouteTable(Case):
    def test_routes_have_the_full_path_the_handler_and_evidence_at_the_spec_lines(self):
        self.repo({"files": api_files()})
        self.server()
        routes = self.oa.routes(None, self.sha("main"))
        self.assertEqual(sorted(r.label for r in routes), ["DELETE /v1/invoices/{invoiceId}", "GET /v1/customers/{customerId}", "GET /v1/invoices", "GET /v1/invoices/{invoiceId}"])
        r = self.route("GET /v1/invoices/{invoiceId}")
        text = text_of(oa.spec())
        self.assertEqual((r.repo, r.kind, r.method, r.path, r.file, r.handler, r.target, r.module, r.partial),
                         (self.REPO, "api_route", "GET", "/v1/invoices/{invoiceId}", SPEC_PATH, "get_invoice", "app/handlers/invoices.py", "invoices", False))
        self.assertEqual([(e.path, e.line) for e in r.evidence], [(SPEC_PATH, line_of(text, '"get"', 2)), (SPEC_PATH, line_of(text, '"/invoices/{invoiceId}"')), (SPEC_PATH, line_of(text, '"url"'))])
        self.assertEqual([e.detail.strip() for e in r.evidence], ['"get": {', '"/invoices/{invoiceId}": {', '"url": "https://api.example.invalid/v1"'])
        self.assertTrue(all(e.ref == self.sha("main") and e.repo == self.REPO for e in r.evidence))
        self.assertEqual(r.evidence[0].claim, f"route:GET /v1/invoices/{{invoiceId}}:{SPEC_PATH}")
        self.assertEqual(r.line, r.evidence[0])
        self.assertEqual(ss.evidence_problems({self.REPO: self.path}, r.evidence), [])

    def test_a_route_with_no_servers_has_no_servers_line_in_its_evidence(self):
        doc = oa.spec()
        del doc["servers"]
        self.repo({"files": api_files(spec=text_of(doc))})
        self.server()
        r = self.route("GET /invoices")
        self.assertEqual(len(r.evidence), 2)

    def test_every_server_gives_its_own_route_each_cited_at_its_own_url_line(self):
        doc = oa.spec(servers=[{"url": "https://a.example.invalid/v1"}, {"url": "https://b.example.invalid/v2"}])
        text = text_of(doc)
        self.repo({"files": api_files(spec=text)})
        self.server()
        a, b = self.route("GET /v1/customers/{customerId}"), self.route("GET /v2/customers/{customerId}")
        self.assertEqual((a.evidence[-1].line, b.evidence[-1].line), (line_of(text, '"url": "https://a'), line_of(text, '"url": "https://b')))
        self.assertEqual(a.evidence[0], b.evidence[0])

    def test_handlers_are_found_by_operation_id_in_camel_or_snake_case(self):
        invoices = oa.f(["def getInvoice(invoice_id):", "    return 1", "", "def list_invoices():", "    return []", "", "def voidInvoice(i):", "    return 2"])
        self.repo({"files": api_files(invoices=invoices)})
        self.server()
        self.assertEqual({r.label: r.handler for r in self.oa.routes(None, self.sha("main"))}, {
            "GET /v1/invoices": "list_invoices", "GET /v1/invoices/{invoiceId}": "getInvoice", "DELETE /v1/invoices/{invoiceId}": "voidInvoice",
            "GET /v1/customers/{customerId}": "get_customer"})

    def test_without_handlers_configured_the_routes_are_listed_and_no_code_is_tied_to_them(self):
        self.repo({"files": api_files()})
        self.server(handlers=())
        r = self.route("GET /v1/invoices/{invoiceId}")
        self.assertEqual((r.handler, r.target), ("getInvoice", ""))
        self.assertEqual(self.oa.problems, [])  # reading the table says nothing about code: a change to code does (below)

    def test_a_route_with_a_partial_base_is_partial(self):
        doc = oa.spec(servers=[{"url": "https://example.invalid/{base}"}])
        self.repo({"files": api_files(spec=text_of(doc))})
        self.server()
        r = self.route("GET /<unknown:base>/invoices")
        self.assertTrue(r.partial)

    def test_the_first_tag_is_the_module_else_the_handler_file(self):
        doc = oa.edited(lambda d: d["paths"]["/customers/{customerId}"]["get"].pop("tags"))
        self.repo({"files": api_files(spec=text_of(doc), customers=None, **{"app/handlers/people.py": oa.CUSTOMERS})})
        self.server()
        self.assertEqual(self.route("GET /v1/customers/{customerId}").module, "people")  # no tag: the stem of the file of the handler
        self.assertEqual(self.route("GET /v1/invoices").module, "invoices")  # the first tag

    def test_two_functions_with_the_name_make_the_handler_ambiguous_and_say_so(self):
        other = oa.f(["def get_invoice(i):", "    return 3"])
        self.repo({"files": api_files(**{"app/handlers/legacy.py": other})}, {"files": {"app/handlers/invoices.py": CHANGED_INVOICES}})
        self.server()
        r = self.route("GET /v1/invoices/{invoiceId}")
        self.assertEqual(r.target, "")  # not claimed: which of the two is the handler is not known
        self.reach()
        self.assertIn(("operation_handler_unresolved", f"{self.REPO}:GET /v1/invoices/{{invoiceId}}", False), self.kind_subjects())
        note = [x for x in self.records if x.kind == "operation_handler_unresolved"][0].detail
        self.assertIn("2", note)

    def test_the_tests_and_the_init_file_are_not_entry_files_and_the_handlers_are(self):
        self.repo({"files": api_files()})
        self.server()
        sha = self.sha("main")
        self.assertEqual({p: self.oa.is_entry_file(p, sha) for p in ("app/handlers/invoices.py", "app/handlers/customers.py", "app/handlers/__init__.py", "app/services/ledger.py", SPEC_PATH, "tests/test_ledger.py")},
                         {"app/handlers/invoices.py": True, "app/handlers/customers.py": True, "app/handlers/__init__.py": False, "app/services/ledger.py": False, SPEC_PATH: False, "tests/test_ledger.py": False})
        self.assertEqual(self.oa.structural_names("app/handlers/invoices.py", sha), frozenset())

    def test_a_handler_file_that_defines_no_operation_is_not_an_entry_file(self):
        self.repo({"files": api_files(**{"app/handlers/helpers.py": "def util():\n    return 1\n"})})
        self.server()
        self.assertFalse(self.oa.is_entry_file("app/handlers/helpers.py", self.sha("main")))


class HandlerPatterns(Case):
    """`handlers` is a file, a folder or a glob (or a list of them), and a pattern that matches no file is said, never an empty tree."""

    def served(self, handlers, files=None, change=None):
        self.repo({"files": files or api_files()}, {"files": change or {"app/handlers/invoices.py": CHANGED_GET}})
        self.server(handlers=handlers)
        return self.reach()

    def test_a_folder_a_folder_with_a_slash_a_file_a_glob_and_a_list_serve_the_same_route(self):
        for handlers in (("app/handlers",), ("app/handlers/",), ("app/handlers/invoices.py", "app/handlers/customers.py"),
                         (HANDLERS,), ("app/**/*.py",), ("app/handlers", "app/handlers/*.py")):
            with self.subTest(handlers=handlers):
                result = self.served(handlers)
                self.assertEqual(self.labels(result), ["GET /v1/invoices/{invoiceId}"])
                self.assertTrue(result.complete, result.reasons)
                self.assertEqual(self.kind_subjects(), [])

    def test_a_folder_is_a_prefix_at_a_folder_boundary_and_non_code_files_are_not_handlers(self):
        files = api_files(**{"app/handlers/README.md": "# handlers\n", "app/handlers2/other.py": "def get_invoice(i):\n    return 1\n"})
        result = self.served(("app/handlers",), files)
        self.assertEqual(self.labels(result), ["GET /v1/invoices/{invoiceId}"])  # handlers2/ is another folder, its get_invoice is not a handler; the README is not parsed
        self.assertTrue(result.complete, result.reasons)

    def test_a_file_names_only_that_file_and_the_operations_of_the_others_are_unresolved_and_said(self):
        result = self.served(("app/handlers/invoices.py",))
        self.assertEqual(self.labels(result), ["GET /v1/invoices/{invoiceId}"])
        self.assertFalse(result.complete)
        self.assertIn(("operation_handler_unresolved", f"{self.REPO}:GET /v1/customers/{{customerId}}", False), self.kind_subjects())

    def test_a_pattern_that_matches_no_file_is_a_record_and_an_incomplete_answer_not_an_empty_complete_one(self):
        for handlers in (("app/nothing/*.py",), ("app/nothing",), ("app/handlers/*.go",)):
            with self.subTest(handlers=handlers):
                result = self.served(handlers)
                self.assertEqual(result.reached, ())
                self.assertFalse(result.complete)
                self.assertIn(("handlers_unmatched", f"{self.REPO}:{handlers[0]}", False), self.kind_subjects())
                self.assertTrue(any("matches no code file" in why for why in result.reasons))

    def test_one_pattern_that_matches_nothing_among_others_is_still_said(self):
        result = self.served((HANDLERS, "app/missing/*.py"))
        self.assertEqual(self.labels(result), ["GET /v1/invoices/{invoiceId}"])
        self.assertFalse(result.complete)
        self.assertEqual([k for k in self.kind_subjects() if k[0] == "handlers_unmatched"], [("handlers_unmatched", f"{self.REPO}:app/missing/*.py", False)])

    def test_unresolved_operations_are_said_whenever_code_that_reaches_nothing_changes(self):
        """The changed file is outside the handler tree and no handler uses it: with an operation that no function carries, a change to
        code anywhere could be its handler under another name, so it is said."""
        files = api_files(customers=None)
        result = self.served((HANDLERS,), files, {"app/unused.py": "def x():\n    return 1\n"})
        self.assertEqual(result.reached, ())
        self.assertFalse(result.complete)
        self.assertIn(("operation_handler_unresolved", f"{self.REPO}:GET /v1/customers/{{customerId}}", False), self.kind_subjects())


class Unreadable(Case):
    """A spec that cannot be read gives no route and says so: never an empty table that reads as 'this repository serves nothing'."""

    def check(self, files, kind="spec_unreadable", blocking=True):
        self.repo({"files": files})
        self.server()
        self.assertEqual(self.oa.routes(None, self.sha("main")), [])
        self.assertTrue(self.oa.problems)
        subjects = [(r.kind, r.subject, r.blocks_selection) for r in self.records]
        self.assertIn((kind, f"{self.REPO}:{SPEC_PATH}", blocking), subjects)

    def test_invalid_json(self):
        self.check(api_files(spec='{"openapi": "3.0.3", "paths": {,}}'))

    def test_truncated(self):
        self.check(api_files(spec=text_of(oa.spec())[:300]))

    def test_not_an_openapi_document(self):
        self.check(api_files(spec='{"hello": "world"}'))

    def test_the_spec_is_not_in_the_tree(self):
        self.check(api_files(spec=None))

    def test_a_spec_with_a_duplicate_key(self):
        self.check(api_files(spec='{"openapi": "3.0.3", "paths": {"/a": {}, "/a": {}}}'))

    def test_yaml_is_a_blocking_unreadable_spec_and_the_answer_is_not_complete(self):
        self.repo({"files": {**api_files(spec=None), "api/openapi.yaml": "openapi: 3.0.3\npaths: {}\n"}})
        self.server(spec="api/openapi.yaml")
        self.assertEqual(self.oa.routes(None, self.sha("main")), [])
        self.assertTrue(self.oa.problems)
        (rec,) = [r for r in self.records if r.kind == "spec_unreadable"]
        self.assertEqual((rec.subject, rec.blocks_selection), (f"{self.REPO}:api/openapi.yaml", True))
        self.assertIn("YAML", rec.detail)
        self.assertIn("JSON only", rec.detail)
        self.assertEqual([r for r in self.records if r.kind == "file_not_analysed"], [])  # one record for one cause, and it blocks

    def test_yml_too(self):
        self.repo({"files": {"api/openapi.yml": "openapi: 3.0.3\n"}})
        self.server(spec="api/openapi.yml")
        self.oa.routes(None, self.sha("main"))
        self.assertEqual([(r.kind, r.blocks_selection) for r in self.records], [("spec_unreadable", True)])

    def test_a_spec_with_a_gap_is_read_and_the_gap_is_a_record(self):
        doc = oa.edited(lambda d: d["paths"].update({"bad": {"get": {"operationId": "x", "responses": {}}}}))
        self.repo({"files": api_files(spec=text_of(doc))}, {"files": {"app/handlers/customers.py": CHANGED_CUSTOMERS}})
        self.server()
        self.assertEqual(len(self.oa.routes(None, self.sha("main"))), 4)
        self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in self.records if r.kind == "spec_gap"], [("spec_gap", f"{self.REPO}:{SPEC_PATH}", False)])
        self.assertTrue(self.oa.problems)

    def test_a_blocking_record_is_made_once_however_often_the_table_is_asked_for(self):
        self.repo({"files": api_files(spec="not json")})
        self.server()
        for _ in range(3):
            self.oa.routes(None, self.sha("main"))
        self.assertEqual(len([r for r in self.records if r.kind == "spec_unreadable"]), 1)


class RoutesChanged(Case):
    def changed(self, before_files, after_files, delete=()):
        self.repo({"files": before_files}, {"files": after_files, "delete": list(delete)})
        self.server()
        return self.oa.routes_changed(self.sha("main~1"), self.sha("main"))

    def test_removed_and_added_by_method_and_full_path_each_with_the_evidence_of_its_own_version(self):
        doc = oa.edited(lambda d: (d["paths"].pop("/customers/{customerId}"), d["paths"].update({"/invoices/{invoiceId}/pdf": {"get": {"operationId": "getInvoicePdf", "responses": {"200": {"description": "ok"}}}}})))
        removed, added = self.changed(api_files(), {SPEC_PATH: text_of(doc)})
        self.assertEqual([r.label for r in removed], ["GET /v1/customers/{customerId}"])
        self.assertEqual([r.label for r in added], ["GET /v1/invoices/{invoiceId}/pdf"])
        self.assertEqual(removed[0].evidence[0].ref, self.sha("main~1"))
        self.assertEqual(added[0].evidence[0].ref, self.sha("main"))
        self.assertEqual(ss.evidence_problems({self.REPO: self.path}, [*removed[0].evidence, *added[0].evidence]), [])

    def test_no_pre_change_version_means_nothing_is_said(self):
        self.repo({"files": api_files()}, {"files": {SPEC_PATH: text_of(oa.spec(servers=[{"url": "/v2"}]))}})
        self.server()
        self.assertEqual(self.oa.routes_changed(None, self.sha("main")), ([], []))

    def test_a_spec_that_first_appears_does_not_make_its_routes_new(self):
        """The routes may have been served before, declared nowhere this adapter reads: newness is not inferred from a spec that was absent."""
        removed, added = self.changed(api_files(spec=None), {SPEC_PATH: text_of(oa.spec())})
        self.assertEqual((removed, added), ([], []))
        self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in self.records], [("spec_gap", f"{self.REPO}:{SPEC_PATH}", False)])
        self.assertTrue(self.oa.problems)

    def test_a_spec_deleted_by_the_change_is_not_a_removed_route_the_handlers_may_still_serve_it(self):
        removed, added = self.changed(api_files(), {}, delete=[SPEC_PATH])
        self.assertEqual((removed, added), ([], []))
        self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in self.records], [("spec_unreadable", f"{self.REPO}:{SPEC_PATH}", True)])
        self.assertTrue(self.oa.problems)

    def test_a_spec_that_stops_being_json_is_not_an_empty_table(self):
        removed, added = self.changed(api_files(), {SPEC_PATH: "{ broken"})
        self.assertEqual((removed, added), ([], []))  # nothing is claimed removed: it could not be read
        self.assertTrue(any(r.kind == "spec_unreadable" and r.blocks_selection for r in self.records))
        self.assertTrue(self.oa.problems)

    def test_a_spec_that_could_not_be_read_at_all_is_not_an_absence(self):
        from unittest import mock
        from reachmap.model import ReadResult
        self.repo({"files": api_files()})
        self.server()
        real = self.language.file_text
        with mock.patch.object(self.language, "file_text", lambda sha, path: ReadResult.unreadable("the object could not be read") if path == SPEC_PATH else real(sha, path)):
            self.assertEqual(self.oa.routes(None, self.sha("main")), [])
        (rec,) = [r for r in self.records if r.kind == "spec_unreadable"]
        self.assertTrue(rec.blocks_selection)
        self.assertIn("could not be read", rec.detail)
        self.assertNotIn("not in the tree", rec.detail)  # unreadable is not absent

    def test_a_spec_that_was_not_json_before_and_is_now(self):
        removed, added = self.changed(api_files(spec="{ broken"), {SPEC_PATH: text_of(oa.spec())})
        self.assertEqual((removed, added), ([], []))
        self.assertTrue(any(r.kind == "spec_unreadable" for r in self.records))

    def test_the_base_path_changing_removes_and_adds_every_route(self):
        removed, added = self.changed(api_files(), {SPEC_PATH: text_of(oa.spec(servers=[{"url": "https://api.example.invalid/v2"}]))})
        self.assertEqual((len(removed), len(added)), (4, 4))


class ReachByCode(Case):
    def test_a_change_in_a_handler_reaches_its_route_and_not_its_siblings(self):
        self.head({"app/handlers/invoices.py": oa.INVOICES.replace("return total_for(invoice_id)", "return total_for(invoice_id) + 1")})
        result = self.reach()
        self.assertEqual(self.labels(result), ["GET /v1/invoices/{invoiceId}"])
        self.assertTrue(result.complete, result.reasons)
        (r,) = result.reached
        route = r.routes[0]
        chain = r.area.chains[0]
        self.assertEqual((chain[0].path, chain[0].line, chain[0].detail), ("app/handlers/invoices.py", 5, "    return total_for(invoice_id) + 1"))
        self.assertEqual(chain[1:], route.evidence)
        self.assertEqual((r.area.kind, r.area.repo, r.area.module), ("api_route", self.REPO, "invoices"))
        self.assertEqual(r.area.route_line, route.evidence[0])

    def test_a_service_function_reaches_the_handlers_that_call_it_through_the_walk(self):
        self.head({"app/services/ledger.py": oa.SERVICE.replace("    return 0\n", "    return 1\n", 1)})
        result = self.reach()
        self.assertEqual(self.labels(result), ["GET /v1/invoices/{invoiceId}"])  # list_invoices uses all_totals only
        (r,) = result.reached
        self.assertEqual(r.area.chains[0][0].detail, "from app.services.ledger import all_totals, total_for")
        self.assertEqual(r.sources[0].files, ("app/services/ledger.py", "app/handlers/invoices.py"))
        self.assertTrue(result.complete, result.reasons)

    def test_a_helper_inside_a_handler_file_reaches_only_the_handlers_that_use_it(self):
        src = oa.INVOICES.replace("def get_invoice", "def _fmt(v):\n    return v\n\n\ndef get_invoice").replace("return total_for(invoice_id)", "return _fmt(total_for(invoice_id))")
        base = api_files(invoices=src)
        self.head({"app/handlers/invoices.py": src.replace("    return v\n", "    return str(v)\n")}, base=base)
        result = self.reach()
        self.assertEqual(self.labels(result), ["GET /v1/invoices/{invoiceId}"])

    def test_a_module_level_change_in_a_handler_file_reaches_every_route_of_the_file(self):
        self.head({"app/handlers/invoices.py": "print('loaded')\n\n" + oa.INVOICES})  # a statement outside every declaration could change anything the file does
        result = self.reach()
        self.assertEqual(self.labels(result), ["DELETE /v1/invoices/{invoiceId}", "GET /v1/invoices", "GET /v1/invoices/{invoiceId}"])

    def test_a_changed_import_reaches_only_the_handlers_that_spell_a_name_it_binds(self):
        util = "def fmt(v):\n    return v\n\n\ndef other(v):\n    return v\n"
        base_src = "from app.util import fmt\n" + oa.INVOICES.replace("return total_for(invoice_id)", "return fmt(total_for(invoice_id))")
        head_src = base_src.replace("from app.util import fmt\n", "from app.util import fmt, other\n")
        self.head({"app/handlers/invoices.py": head_src}, base=api_files(invoices=base_src, **{"app/util.py": util}))
        result = self.reach()
        self.assertEqual(self.labels(result), ["GET /v1/invoices/{invoiceId}"])  # get_invoice spells fmt; the others spell neither fmt nor other
        self.assertTrue(result.complete, result.reasons)

    def test_a_removed_import_line_is_read_at_the_pre_change_version(self):
        util = "def fmt(v):\n    return v\n"
        base_src = "from app.util import fmt\n" + oa.INVOICES.replace("return total_for(invoice_id)", "return fmt(total_for(invoice_id))")
        head_src = base_src.replace("from app.util import fmt\n", "").replace("fmt(total_for(invoice_id))", "total_for(invoice_id)")
        self.head({"app/handlers/invoices.py": head_src}, base=api_files(invoices=base_src, **{"app/util.py": util}))
        result = self.reach()
        self.assertEqual(self.labels(result), ["GET /v1/invoices/{invoiceId}"])

    def test_a_change_to_code_no_handler_uses_reaches_no_route_and_is_complete(self):
        self.head({"app/unused.py": "def x():\n    return 1\n"})
        result = self.reach()
        self.assertEqual(result.reached, ())
        self.assertTrue(result.complete, result.reasons)
        self.assertEqual(self.records, [])

    def test_a_removed_handler_is_not_a_route_that_is_reached_and_the_operation_is_unresolved(self):
        self.head({"app/handlers/customers.py": "def other():\n    return 1\n"})
        result = self.reach()
        self.assertEqual(result.reached, ())
        self.assertIn(("operation_handler_unresolved", f"{self.REPO}:GET /v1/customers/{{customerId}}", False), self.kind_subjects())
        self.assertFalse(result.complete)

    def test_without_handlers_a_change_to_code_reaches_no_route_and_says_it_cannot(self):
        self.repo({"files": api_files()}, {"files": {"app/handlers/invoices.py": CHANGED_INVOICES}})
        self.server(handlers=())
        result = self.reach()
        self.assertEqual(result.reached, ())
        self.assertFalse(result.complete)
        self.assertIn(("handlers_unconfigured", self.REPO, False), self.kind_subjects())
        self.assertTrue(any("handler files" in why for why in result.reasons))

    def test_a_handler_file_that_does_not_parse_makes_the_answer_incomplete_and_says_which_file(self):
        self.head({"app/handlers/customers.py": "def get_customer(:\n    return {}\n"})
        result = self.reach()
        self.assertFalse(result.complete)
        self.assertTrue(any(r.kind == "parse_degraded" and r.subject == "app/handlers/customers.py" for r in self.records))
        self.assertEqual(result.reached, ())

    def test_a_handler_file_that_did_not_parse_never_makes_the_operation_look_unserved_quietly(self):
        self.repo({"files": api_files(customers="def get_customer(:\n    return {}\n")}, {"files": {"app/handlers/invoices.py": CHANGED_INVOICES}})
        self.server()
        result = self.reach()
        self.assertFalse(result.complete)
        self.assertTrue(any("customers.py" in why for why in result.reasons + tuple(self.oa.problems)))

    def test_a_spec_that_cannot_be_read_gives_no_route_for_a_handler_change_and_blocks(self):
        self.repo({"files": api_files(spec="{ broken")}, {"files": {"app/handlers/invoices.py": CHANGED_INVOICES}})
        self.server()
        result = self.reach()
        self.assertEqual(result.reached, ())
        self.assertFalse(result.complete)
        self.assertIn(("spec_unreadable", f"{self.REPO}:{SPEC_PATH}", True), self.kind_subjects())

    def test_a_test_file_change_is_not_a_route(self):
        self.head({"tests/test_ledger.py": "def test_x():\n    assert True\n"})
        result = self.reach()
        self.assertEqual(result.reached, ())


class ReachBySpec(Case):
    def spec_change(self, edit, **extra):
        doc = oa.edited(edit)
        self.repo({"files": api_files()}, {"files": {SPEC_PATH: text_of(doc), **extra}})
        self.server()
        return self.reach()

    def add_param(self, d):
        d["paths"]["/invoices/{invoiceId}"]["get"]["parameters"] = [{"name": "expand", "in": "query", "required": True, "schema": {"type": "string"}}]

    def test_a_changed_operation_is_an_area_with_the_first_changed_line_of_the_operation_then_its_route(self):
        result = self.spec_change(self.add_param)
        self.assertEqual(self.labels(result), ["GET /v1/invoices/{invoiceId}"])
        self.assertTrue(result.complete, result.reasons)
        (r,) = result.reached
        spec = self.oa.table(self.sha("main")).spec
        (op,) = [o for o in spec.operations if o.operation_id == "getInvoice"]
        first = r.area.chains[0][0]
        self.assertEqual((first.path, first.ref), (SPEC_PATH, self.sha("main")))
        self.assertTrue(op.start <= first.line <= op.end, (first.line, op.start, op.end))
        self.assertEqual(r.area.chains[0][1:], r.routes[0].evidence)
        self.assertEqual(r.area.also_mounted, ())
        self.assertFalse(r.area.by_name)

    def test_the_finding_input_of_a_changed_operation_cites_both_versions(self):
        self.spec_change(self.add_param)
        (inp,) = self.inputs()
        self.assertIsInstance(inp, FindingInput)
        self.assertEqual((inp.kind, inp.risk, inp.confidence), ("operation-changed", "medium", "high"))
        before, after = inp.evidence
        self.assertEqual((before.ref, after.ref), (self.sha("main~1"), self.sha("main")))
        self.assertEqual((before.path, after.path), (SPEC_PATH, SPEC_PATH))
        self.assertEqual((before.detail.strip(), after.detail.strip()), ('"get": {', '"get": {'))
        self.assertNotEqual(before.claim, after.claim)  # the claim holds the signature, so a later edit of the operation is a new claim
        self.assertEqual(inp.identity, ("operation-changed", self.REPO, "GET", "/v1/invoices/{invoiceId}"))
        self.assertIn("parameters", inp.detail)
        self.assertEqual(ss.evidence_problems({self.REPO: self.path}, inp.evidence), [])

    def test_a_schema_change_reaches_every_operation_that_reaches_the_schema_through_the_cycle(self):
        result = self.spec_change(lambda d: d["components"]["schemas"]["Money"].__setitem__("required", ["amount", "currency"]))
        self.assertEqual(self.labels(result), ["GET /v1/invoices", "GET /v1/invoices/{invoiceId}"])
        spec = self.oa.table(self.sha("main")).spec
        start, end = spec.span_of("#/components/schemas/Money")
        for r in result.reached:
            first = r.area.chains[0][0]
            self.assertTrue(start <= first.line <= end, first)  # the way in is the changed line of the schema
        self.assertEqual(sorted(i.identity[-1] for i in self.inputs()), ["/v1/invoices", "/v1/invoices/{invoiceId}"])
        self.assertTrue(all("schemas" in i.detail for i in self.inputs()))

    def test_a_description_change_reaches_nothing(self):
        result = self.spec_change(lambda d: d["paths"]["/invoices/{invoiceId}"]["get"].update({"summary": "words"}))
        self.assertEqual(result.reached, ())
        self.assertEqual(self.inputs(), [])
        self.assertTrue(result.complete)

    def test_an_added_operation_is_an_area_and_not_a_changed_operation(self):
        def edit(d):
            d["paths"]["/invoices/{invoiceId}/pdf"] = {"get": {"operationId": "getInvoicePdf", "responses": {"200": {"description": "ok"}}}}
        result = self.spec_change(edit)
        self.assertEqual(self.labels(result), ["GET /v1/invoices/{invoiceId}/pdf"])
        self.assertEqual(self.inputs(), [])

    def test_a_removed_operation_is_no_area_the_route_is_gone(self):
        result = self.spec_change(lambda d: d["paths"].pop("/customers/{customerId}"))
        self.assertEqual(result.reached, ())
        self.assertEqual(self.inputs(), [])

    def test_the_base_path_changing_is_removed_and_added_routes_and_every_new_route_is_an_area(self):
        doc = oa.spec(servers=[{"url": "https://api.example.invalid/v2"}])
        self.repo({"files": api_files()}, {"files": {SPEC_PATH: text_of(doc)}})
        self.server()
        result = self.reach()
        self.assertEqual(self.labels(result), ["DELETE /v2/invoices/{invoiceId}", "GET /v2/customers/{customerId}", "GET /v2/invoices", "GET /v2/invoices/{invoiceId}"])

    def test_a_spec_that_first_appears_makes_every_operation_affected_and_the_answer_incomplete(self):
        self.repo({"files": api_files(spec=None)}, {"files": {SPEC_PATH: text_of(oa.spec())}})
        self.server()
        result = self.reach()
        self.assertEqual(len(result.reached), 4)
        self.assertFalse(result.complete)
        self.assertTrue(any("new in this change" in why for why in result.reasons))
        self.assertEqual(self.inputs(), [])
        self.assertIn(("spec_gap", f"{self.REPO}:{SPEC_PATH}", False), self.kind_subjects())

    def test_a_spec_deleted_by_the_change_makes_every_base_operation_affected_with_evidence_at_the_base_and_blocks(self):
        self.repo({"files": api_files()}, {"files": {}, "delete": [SPEC_PATH]})
        self.server()
        result = self.reach()
        self.assertEqual(self.labels(result), ["DELETE /v1/invoices/{invoiceId}", "GET /v1/customers/{customerId}", "GET /v1/invoices", "GET /v1/invoices/{invoiceId}"])
        self.assertFalse(result.complete)
        self.assertTrue(any("deleted" in why for why in result.reasons))
        self.assertTrue(all(e.ref == self.sha("main~1") for r in result.reached for c in r.area.chains for e in c))
        self.assertIn(("spec_unreadable", f"{self.REPO}:{SPEC_PATH}", True), self.kind_subjects())
        self.assertEqual(self.inputs(), [])

    def test_a_path_level_parameter_change_is_the_changed_line_of_every_operation_of_the_path(self):
        def edit(d):
            d["paths"]["/invoices/{invoiceId}"]["parameters"].append({"name": "expand", "in": "query", "schema": {"type": "string"}})
        result = self.spec_change(edit)
        self.assertEqual(self.labels(result), ["DELETE /v1/invoices/{invoiceId}", "GET /v1/invoices/{invoiceId}"])
        spec = self.oa.table(self.sha("main")).spec
        for r in result.reached:
            (op,) = [o for o in spec.operations if (o.method, o.template) == (r.routes[0].method, "/invoices/{invoiceId}")]
            first = r.area.chains[0][0]
            self.assertTrue(any(a <= first.line <= b for a, b in op.shared) or op.start <= first.line <= op.end, (r.area.label, first.line, op.shared))
            self.assertEqual((first.path, first.ref), (SPEC_PATH, self.sha("main")))
            self.assertNotEqual(r.area.chains[0], r.routes[0].evidence)
        self.assertEqual(len(self.inputs()), 2)

    def test_a_function_with_the_name_outside_the_handler_tree_is_not_the_handler(self):
        self.repo({"files": api_files(customers=None, **{"app/services/people.py": oa.CUSTOMERS})}, {"files": {"app/handlers/invoices.py": CHANGED_INVOICES}})
        self.server()
        self.assertEqual(self.route("GET /v1/customers/{customerId}").target, "")  # get_customer is defined, in a module the handler glob does not name
        self.reach()
        self.assertIn(("operation_handler_unresolved", f"{self.REPO}:GET /v1/customers/{{customerId}}", False), self.kind_subjects())

    def test_a_diff_whose_patch_is_not_available_still_finds_the_changed_operation_without_a_changed_line(self):
        self.repo({"files": api_files()}, {"files": {SPEC_PATH: text_of(oa.edited(self.add_param))}})
        self.server()
        analysis = self.analyze(self.language)
        files = tuple(dataclasses.replace(fc, hunks=(), content_available=False) if fc.path == SPEC_PATH else fc for fc in analysis.change.files)
        blind = dataclasses.replace(analysis, change=dataclasses.replace(analysis.change, files=files))
        result = self.last = entrypoints.reach(blind, self.language, self.oa)
        (r,) = result.reached
        self.assertEqual(r.area.label, "GET /v1/invoices/{invoiceId}")
        self.assertEqual(r.area.chains[0], r.routes[0].evidence)  # no changed line to start from: the route's own lines
        self.assertEqual(len(self.inputs()), 1)

    def test_a_spec_written_with_odd_line_breaks_is_cited_at_the_line_of_the_key(self):
        text = '{"openapi": "3.0.3",\n"paths": {"/invoices/{invoiceId}":\n{\n"get"\n:\n{"operationId":\n"getInvoice", "responses": {}}}}}'
        self.repo({"files": api_files(spec=text)})
        self.server()
        (r,) = [r for r in self.oa.routes(None, self.sha("main")) if r.method == "GET"]
        self.assertEqual([(e.line, e.detail) for e in r.evidence], [(4, '"get"'), (2, '"paths": {"/invoices/{invoiceId}":')])
        self.assertEqual(ss.evidence_problems({self.REPO: self.path}, r.evidence), [])

    def test_no_pre_change_version_means_every_operation_of_the_spec_is_affected_and_the_answer_says_it_is_not_complete(self):
        self.repo({"files": api_files()}, {"files": {SPEC_PATH: text_of(oa.edited(self.add_param))}})
        self.server()
        analysis = self.analyze(self.language, pre=None)
        result = self.last = entrypoints.reach(analysis, self.language, self.oa)
        self.assertEqual(len(result.reached), 4)
        self.assertFalse(result.complete)
        self.assertTrue(any("pre-change" in why for why in result.reasons))
        self.assertEqual(self.inputs(), [])  # nothing is said about what changed in an operation whose before is not known

    def test_a_spec_that_cannot_be_read_at_the_head_claims_nothing_and_blocks(self):
        self.repo({"files": api_files()}, {"files": {SPEC_PATH: "{ broken"}})
        self.server()
        result = self.reach()
        self.assertEqual(result.reached, ())
        self.assertFalse(result.complete)
        self.assertIn(("spec_unreadable", f"{self.REPO}:{SPEC_PATH}", True), self.kind_subjects())

    def test_a_changed_yaml_spec_is_a_blocking_record_and_an_incomplete_result_not_a_quiet_one(self):
        """A removed operation in a YAML spec gives no finding (no YAML reader), so the change must not read as complete or as a note."""
        before = "openapi: 3.0.3\npaths:\n  /a:\n    get:\n      operationId: a\n  /b:\n    get:\n      operationId: b\n"
        after = "openapi: 3.0.3\npaths:\n  /a:\n    get:\n      operationId: a\n"
        self.repo({"files": {**api_files(spec=None), "api/openapi.yaml": before}}, {"files": {"api/openapi.yaml": after}})
        self.server(spec="api/openapi.yaml")
        result = self.reach()
        self.assertEqual((result.reached, result.complete), ((), False))
        self.assertTrue(any("api/openapi.yaml" in why for why in result.reasons), result.reasons)
        self.assertIn(("spec_unreadable", f"{self.REPO}:api/openapi.yaml", True), self.kind_subjects())
        self.assertEqual([r.kind for r in self.records if r.blocks_selection and r.kind != "spec_unreadable"], [])

    def test_a_changed_spec_that_cannot_be_read_for_any_other_reason_blocks_too(self):
        for name, files, changed in (("deleted", {}, None), ("truncated", {SPEC_PATH: text_of(oa.spec())[:200]}, None), ("not json", {SPEC_PATH: "openapi: 3\n"}, None)):
            with self.subTest(name):
                self.repo({"files": api_files()}, {"files": files, "delete": [SPEC_PATH] if name == "deleted" else []})
                self.server()
                result = self.reach()
                self.assertFalse(result.complete)
                self.assertIn(("spec_unreadable", f"{self.REPO}:{SPEC_PATH}", True), self.kind_subjects())

    def test_two_unreadable_versions_are_two_records_for_two_causes(self):
        self.repo({"files": api_files(spec=None)}, {"files": {SPEC_PATH: "{ broken"}})  # absent before, not JSON now
        self.server()
        for ref in ("main~1", "main"):
            self.assertEqual(self.oa.routes(None, self.sha(ref)), [])
        found = [r for r in self.records if r.kind == "spec_unreadable"]
        self.assertEqual(len(found), 2)
        self.assertEqual(len({r.detail for r in found}), 2)  # each says its own cause

    def test_a_spec_that_could_not_be_read_before_makes_every_operation_affected(self):
        self.repo({"files": api_files(spec="{ broken")}, {"files": {SPEC_PATH: text_of(oa.spec())}})
        self.server()
        result = self.reach()
        self.assertEqual(len(result.reached), 4)
        self.assertFalse(result.complete)
        self.assertIn(("spec_unreadable", f"{self.REPO}:{SPEC_PATH}", True), self.kind_subjects())

    def test_an_operation_with_a_ref_outside_the_document_is_changed_whenever_the_spec_changes_and_a_gap_is_recorded(self):
        def edit(d):
            d["paths"]["/customers/{customerId}"]["get"]["responses"] = {"200": {"description": "ok", "content": {"application/json": {"schema": {"$ref": "other.json#/Customer"}}}}}
        before = oa.edited(edit)
        after = copy.deepcopy(before)
        after["info"]["version"] = "2"
        self.repo({"files": api_files(spec=text_of(before))}, {"files": {SPEC_PATH: text_of(after)}})
        self.server()
        result = self.reach()
        self.assertEqual(self.labels(result), ["GET /v1/customers/{customerId}"])
        self.assertIn(("spec_gap", f"{self.REPO}:{SPEC_PATH}", False), self.kind_subjects())
        self.assertFalse(result.complete)
        (inp,) = self.inputs()
        self.assertEqual(inp.confidence, "medium")
        self.assertIn("unresolved", inp.detail)

    def test_a_handler_change_and_a_spec_change_to_one_operation_are_one_area_with_two_chains(self):
        doc = oa.edited(self.add_param)
        self.repo({"files": api_files()}, {"files": {SPEC_PATH: text_of(doc), "app/handlers/invoices.py": oa.INVOICES.replace("total_for(invoice_id)", "total_for(invoice_id) or 0")}})
        self.server()
        result = self.reach()
        (r,) = result.reached
        self.assertEqual(r.area.label, "GET /v1/invoices/{invoiceId}")
        self.assertEqual(len(r.area.chains), 2)
        self.assertEqual(sorted(c[0].path for c in r.area.chains), [SPEC_PATH, "app/handlers/invoices.py"])
        self.assertEqual(len(self.inputs()), 1)

    def test_the_hook_is_idempotent_and_ignores_a_seed(self):
        self.repo({"files": api_files()}, {"files": {SPEC_PATH: text_of(oa.edited(self.add_param))}})
        self.server()
        analysis = self.analyze(self.language)
        first = entrypoints.reach(analysis, self.language, self.oa)
        again = self.last = entrypoints.reach(analysis, self.language, self.oa)
        self.assertEqual(len(first.findings), 1)
        self.assertEqual(first.findings, again.findings)
        self.assertEqual(len(self.inputs()), 1)
        self.assertEqual([r.area.label for r in first.reached], [r.area.label for r in again.reached])
        seeded = dataclasses.replace(analysis, change=dataclasses.replace(analysis.change, kind="seed"))
        self.assertEqual(self.oa.reached_by_files(seeded.change, []), ([], []))

    def test_a_renamed_spec_is_not_the_configured_spec(self):
        self.repo({"files": api_files()}, {"files": {"api/other.json": text_of(oa.edited(self.add_param))}})
        self.server()
        result = self.reach()
        self.assertEqual(result.reached, ())

    def test_the_findings_of_a_changed_operation_and_the_areas_pass_the_wording_check(self):
        self.spec_change(self.add_param)
        from reachmap import wording
        for inp in self.inputs():
            self.assertEqual(wording.check(inp.detail) + wording.check(inp.confidence_basis), [])


class UnreadPaths(Case):
    """What the reader could not turn into operations is never a removed or an added route: a spec split into files, a method that is not an
    object. It is said, blocking, for those routes."""

    CUSTOMERS = "/customers/{customerId}"

    def changed_to(self, edit, before=None):
        self.repo({"files": api_files(spec=text_of(before) if before is not None else text_of(oa.spec()))}, {"files": {SPEC_PATH: text_of(oa.edited(edit))}})
        self.server()

    def split(self, d):
        d["paths"][self.CUSTOMERS] = {"$ref": "paths/customers.json"}

    def test_a_path_item_moved_to_another_file_is_not_a_removed_route(self):
        self.changed_to(self.split)
        removed, added = self.oa.routes_changed(self.sha("main~1"), self.sha("main"))
        self.assertEqual((removed, added), ([], []))
        self.assertIn(("spec_unreadable", f"{self.REPO}:{SPEC_PATH}", True), self.kind_subjects())
        self.assertTrue(any("customers" in why for why in self.oa.problems))

    def test_a_method_that_is_no_longer_an_object_is_not_a_removed_route(self):
        self.changed_to(lambda d: d["paths"][self.CUSTOMERS].__setitem__("get", "moved"))
        self.assertEqual(self.oa.routes_changed(self.sha("main~1"), self.sha("main")), ([], []))
        self.assertIn(("spec_unreadable", f"{self.REPO}:{SPEC_PATH}", True), self.kind_subjects())

    def test_a_route_that_was_unread_before_is_not_an_added_route(self):
        before = oa.edited(self.split)
        self.changed_to(lambda d: None, before=before)
        self.assertEqual(self.oa.routes_changed(self.sha("main~1"), self.sha("main")), ([], []))
        self.assertIn(("spec_unreadable", f"{self.REPO}:{SPEC_PATH}", True), self.kind_subjects())

    def test_routes_the_reader_did_read_are_still_removed_and_added_when_another_path_is_unread(self):
        def edit(d):
            self.split(d)
            d["paths"].pop("/invoices")
        self.changed_to(edit)
        removed, added = self.oa.routes_changed(self.sha("main~1"), self.sha("main"))
        self.assertEqual(sorted(r.label for r in removed), ["GET /v1/invoices"])  # the customers route is the unread one

    def test_the_change_says_it_through_the_entry_result_too_and_claims_no_area_for_the_unread_route(self):
        self.changed_to(self.split)
        result = self.reach()
        self.assertEqual(result.reached, ())
        self.assertFalse(result.complete)
        self.assertIn(("spec_unreadable", f"{self.REPO}:{SPEC_PATH}", True), self.kind_subjects())

    def test_nothing_is_suppressed_when_nothing_is_unread(self):
        self.changed_to(lambda d: d["paths"].pop(self.CUSTOMERS))
        removed, _added = self.oa.routes_changed(self.sha("main~1"), self.sha("main"))
        self.assertEqual([r.label for r in removed], ["GET /v1/customers/{customerId}"])
        self.assertEqual([k for k in self.kind_subjects() if k[0] == "spec_unreadable"], [])


class Records(Case):
    def test_every_kind_is_registered_with_its_blocking_default(self):
        from reachmap.incomplete import kind_spec
        import reachmap.config  # noqa: F401  (the adapter package registers its kinds when the config module, which every run imports, is)
        self.assertEqual({k: kind_spec(k).blocks_selection for k in ("spec_unreadable", "spec_gap", "operation_handler_unresolved", "handlers_unconfigured", "handlers_unmatched")},
                         {"spec_unreadable": True, "spec_gap": False, "operation_handler_unresolved": False, "handlers_unconfigured": False, "handlers_unmatched": False})
        self.assertTrue(kind_spec("spec_unreadable").always_blocking)

    def test_the_records_pass_the_wording_check(self):
        from reachmap import wording
        from reachmap.incomplete import Incompleteness
        for kind, subject in (("spec_unreadable", "t/api:api/openapi.json"), ("spec_gap", "t/api:api/openapi.json"), ("operation_handler_unresolved", "t/api:GET /x"), ("handlers_unconfigured", "t/api"), ("handlers_unmatched", "t/api:app/x/*.py")):
            self.assertEqual(wording.check(Incompleteness(kind, subject, "", kind == "spec_unreadable").not_established), [])


if __name__ == "__main__":
    unittest.main()
