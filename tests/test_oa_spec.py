"""M16 OpenAPI: the spec model. Operations with the line of their method key, base paths from `servers` (OpenAPI 3) and
`basePath` (Swagger 2), `$ref` resolution with cycle detection, and the signature of an operation (parameters, request body,
responses and every schema they reach) that a spec diff compares.

Nothing here touches git: a spec is text.
"""

import copy
import json
import unittest

from reachmap.adapters.openapi import names
from reachmap.adapters.openapi.spec import Spec, SpecError, diff, read


def doc(**over):
    """A small OpenAPI 3 document; keyword arguments replace top-level members."""
    base = {
        "openapi": "3.0.3",
        "info": {"title": "Ledger", "version": "1"},
        "servers": [{"url": "https://api.example.invalid/v1"}],
        "paths": {
            "/invoices": {
                "get": {"operationId": "listInvoices", "responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {"type": "array", "items": {"$ref": "#/components/schemas/Invoice"}}}}}}},
                "post": {"operationId": "createInvoice", "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Invoice"}}}}, "responses": {"201": {"description": "created"}}},
            },
            "/invoices/{invoiceId}": {
                "parameters": [{"$ref": "#/components/parameters/InvoiceId"}],
                "get": {"operationId": "getInvoice", "responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Invoice"}}}}}},
            },
            "/health": {"get": {"operationId": "health", "responses": {"200": {"description": "ok"}}}},
        },
        "components": {
            "parameters": {"InvoiceId": {"name": "invoiceId", "in": "path", "required": True, "schema": {"type": "string"}}},
            "schemas": {
                "Invoice": {"type": "object", "properties": {"id": {"type": "string"}, "total": {"$ref": "#/components/schemas/Money"}, "customer": {"$ref": "#/components/schemas/Customer"}}},
                "Customer": {"type": "object", "properties": {"id": {"type": "string"}, "invoices": {"type": "array", "items": {"$ref": "#/components/schemas/Invoice"}}}},
                "Money": {"type": "object", "required": ["amount"], "properties": {"amount": {"type": "integer"}}},
            },
        },
    }
    base.update(over)
    return base


def text_of(obj):
    return json.dumps(obj, indent=2) + "\n"


def spec_of(obj):
    return read(text_of(obj), "api/openapi.json")


def line_of(text, needle, nth=1):
    hits = [i for i, line in enumerate(text.split("\n"), 1) if needle in line]
    return hits[nth - 1]


class Operations(unittest.TestCase):
    def test_every_operation_with_the_line_of_its_method_key_and_of_its_path_key(self):
        text = text_of(doc())
        spec = read(text, "api/openapi.json")
        ops = {(o.method, o.template): o for o in spec.operations}
        self.assertEqual(sorted(ops), [("GET", "/health"), ("GET", "/invoices"), ("GET", "/invoices/{invoiceId}"), ("POST", "/invoices")])
        get = ops[("GET", "/invoices/{invoiceId}")]
        self.assertEqual((get.operation_id, get.line, get.path_line), ("getInvoice", line_of(text, '"get"', 2), line_of(text, '"/invoices/{invoiceId}"')))
        self.assertEqual(spec.line_text(get.line).strip(), '"get": {')
        self.assertLessEqual(get.start, get.line)
        self.assertGreater(get.end, get.line)
        self.assertEqual((ops[("POST", "/invoices")].line, ops[("POST", "/invoices")].path_line), (line_of(text, '"post"'), line_of(text, '"/invoices"')))

    def test_a_version_is_read_from_openapi_or_swagger(self):
        self.assertEqual(spec_of(doc()).version, "3.0.3")
        sw = {"swagger": "2.0", "basePath": "/v2", "paths": {"/a": {"get": {"operationId": "a", "responses": {"200": {"description": "ok"}}}}}}
        self.assertEqual(spec_of(sw).version, "2.0")

    def test_the_other_keys_of_a_path_item_are_not_operations(self):
        obj = doc()
        obj["paths"]["/health"].update({"summary": "s", "description": "d", "x-internal": {"get": 1}, "servers": [{"url": "/h"}]})
        self.assertEqual(sorted(o.method for o in spec_of(obj).operations if o.template == "/health"), ["GET"])

    def test_all_eight_methods(self):
        item = {m: {"operationId": m + "It", "responses": {"200": {"description": "ok"}}} for m in ("get", "put", "post", "delete", "options", "head", "patch", "trace")}
        spec = spec_of(doc(paths={"/x": item}))
        self.assertEqual(sorted(o.method for o in spec.operations), ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT", "TRACE"])

    def test_an_operation_without_an_operation_id_has_none(self):
        spec = spec_of(doc(paths={"/x": {"get": {"responses": {"200": {"description": "ok"}}}}}))
        self.assertIsNone(spec.operations[0].operation_id)

    def test_a_document_that_is_not_openapi_is_refused(self):
        for text in ('{"paths": {}}', '{"openapi": 3}', '{"openapi": "4.0.0", "paths": {}}', "[1]", '{"swagger": "1.2"}'):
            with self.subTest(text=text):
                with self.assertRaises(SpecError):
                    read(text, "api/openapi.json")

    def test_invalid_json_is_refused_with_the_line(self):
        with self.assertRaises(SpecError) as ctx:
            read('{\n  "openapi": "3.0.3",\n  "paths": {,}\n}', "api/openapi.json")
        self.assertIn("line 3", str(ctx.exception))

    def test_paths_that_are_not_an_object_are_refused(self):
        with self.assertRaises(SpecError):
            read(text_of(doc(paths=[1])), "api/openapi.json")

    def test_a_path_that_does_not_start_with_a_slash_is_a_stated_gap_and_not_an_operation(self):
        spec = spec_of(doc(paths={"invoices": {"get": {"operationId": "x", "responses": {}}}, "/ok": {"get": {"operationId": "y", "responses": {}}}}))
        self.assertEqual([o.template for o in spec.operations], ["/ok"])
        self.assertTrue(any("invoices" in p and "does not start with /" in p for p in spec.problems))

    def test_a_method_whose_value_is_not_an_object_is_a_stated_gap(self):
        spec = spec_of(doc(paths={"/x": {"get": "nope", "post": {"operationId": "p", "responses": {}}}}))
        self.assertEqual([o.method for o in spec.operations], ["POST"])
        self.assertTrue(any("get" in p for p in spec.problems))

    def test_a_path_item_that_is_a_local_ref_is_followed_and_a_remote_one_is_a_stated_gap(self):
        obj = doc()
        obj["components"]["pathItems"] = {"Shared": {"get": {"operationId": "shared", "responses": {}}}}
        obj["paths"]["/shared"] = {"$ref": "#/components/pathItems/Shared"}
        obj["paths"]["/remote"] = {"$ref": "other.json#/Item"}
        spec = spec_of(obj)
        self.assertIn(("GET", "/shared"), {(o.method, o.template) for o in spec.operations})
        self.assertNotIn("/remote", {o.template for o in spec.operations})
        self.assertTrue(any("/remote" in p for p in spec.problems))


class BasePaths(unittest.TestCase):
    def full(self, spec, template="/health"):
        (op,) = [o for o in spec.operations if o.template == template and o.method == "GET"]
        return [(p, b.partial) for p, b in op.routes()]

    def test_the_path_of_the_first_server_url_is_the_base(self):
        text = text_of(doc())
        spec = read(text, "api/openapi.json")
        self.assertEqual(self.full(spec), [("/v1/health", False)])
        (op,) = [o for o in spec.operations if o.template == "/health"]
        self.assertEqual(op.bases[0].line, line_of(text, '"url": "https://api.example.invalid/v1"'))

    def test_every_server_gives_its_own_route(self):
        obj = doc(servers=[{"url": "https://a.example.invalid/v1"}, {"url": "https://b.example.invalid/v2"}])
        self.assertEqual(self.full(spec_of(obj)), [("/v1/health", False), ("/v2/health", False)])

    def test_the_same_path_on_two_servers_is_one_route(self):
        obj = doc(servers=[{"url": "https://a.example.invalid/v1"}, {"url": "https://b.example.invalid/v1/"}])
        self.assertEqual(self.full(spec_of(obj)), [("/v1/health", False)])

    def test_no_servers_means_the_root(self):
        obj = doc()
        del obj["servers"]
        self.assertEqual(self.full(spec_of(obj)), [("/health", False)])
        self.assertEqual(self.full(spec_of(doc(servers=[]))), [("/health", False)])
        self.assertEqual(self.full(spec_of(doc(servers=[{"url": "/"}]))), [("/health", False)])
        self.assertEqual(self.full(spec_of(doc(servers=[{"url": "https://h.example.invalid"}]))), [("/health", False)])

    def test_a_server_variable_takes_its_default(self):
        obj = doc(servers=[{"url": "https://{region}.example.invalid/{base}", "variables": {"region": {"default": "eu"}, "base": {"default": "v3"}}}])
        self.assertEqual(self.full(spec_of(obj)), [("/v3/health", False)])

    def test_a_server_variable_with_no_default_makes_the_route_partial_and_is_not_guessed(self):
        obj = doc(servers=[{"url": "https://example.invalid/{base}"}])
        (path, partial), = self.full(spec_of(obj))
        self.assertEqual((path, partial), ("/<unknown:base>/health", True))

    def test_a_relative_server_url_is_partial(self):
        (path, partial), = self.full(spec_of(doc(servers=[{"url": "api/v1"}])))
        self.assertTrue(partial)
        self.assertIn("<unknown:", path)

    def test_a_server_without_a_url_is_a_stated_gap(self):
        spec = spec_of(doc(servers=[{"description": "x"}]))
        self.assertTrue(any("server" in p for p in spec.problems))
        self.assertTrue(all(partial for _p, partial in self.full(spec)))

    def test_a_path_level_servers_replaces_the_root_ones_and_an_operation_level_one_replaces_both(self):
        obj = doc()
        obj["paths"]["/health"]["servers"] = [{"url": "https://h.example.invalid/ops"}]
        self.assertEqual(self.full(spec_of(obj)), [("/ops/health", False)])
        obj["paths"]["/health"]["get"]["servers"] = [{"url": "https://h.example.invalid/op-level"}]
        self.assertEqual(self.full(spec_of(obj)), [("/op-level/health", False)])
        self.assertEqual(self.full(spec_of(obj), "/invoices"), [("/v1/invoices", False)])

    def test_swagger_two_uses_base_path(self):
        sw = {"swagger": "2.0", "basePath": "/v2/", "paths": {"/a": {"get": {"operationId": "a", "responses": {}}}}}
        (op,) = spec_of(sw).operations
        self.assertEqual([p for p, _b in op.routes()], ["/v2/a"])
        sw["basePath"] = "/"
        (op,) = spec_of(sw).operations
        self.assertEqual([p for p, _b in op.routes()], ["/a"])
        del sw["basePath"]
        (op,) = spec_of(sw).operations
        self.assertEqual([p for p, _b in op.routes()], ["/a"])

    def test_slashes_are_joined_without_doubles(self):
        obj = doc(servers=[{"url": "https://h.example.invalid/v1/"}], paths={"/a/": {"get": {"operationId": "a", "responses": {}}}})
        self.assertEqual(self.full(spec_of(obj), "/a/"), [("/v1/a", False)])


class Refs(unittest.TestCase):
    def op(self, spec, operation_id):
        (op,) = [o for o in spec.operations if o.operation_id == operation_id]
        return op

    def test_the_closure_of_an_operation_holds_every_schema_it_reaches_and_a_cycle_ends(self):
        spec = spec_of(doc())
        get = self.op(spec, "getInvoice")
        self.assertEqual(get.refs, ("#/components/parameters/InvoiceId", "#/components/schemas/Customer", "#/components/schemas/Invoice", "#/components/schemas/Money"))
        health = self.op(spec, "health")
        self.assertEqual(health.refs, ())

    def test_a_schema_that_refers_to_itself_ends(self):
        obj = doc()
        obj["components"]["schemas"]["Tree"] = {"type": "object", "properties": {"children": {"type": "array", "items": {"$ref": "#/components/schemas/Tree"}}}}
        obj["paths"]["/tree"] = {"get": {"operationId": "tree", "responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Tree"}}}}}}}
        spec = spec_of(obj)
        self.assertEqual(self.op(spec, "tree").refs, ("#/components/schemas/Tree",))
        self.assertEqual(self.op(spec, "getInvoice").refs, ("#/components/parameters/InvoiceId", "#/components/schemas/Customer", "#/components/schemas/Invoice", "#/components/schemas/Money"))  # Invoice and Customer refer to each other

    def test_a_ref_to_a_ref_is_followed_and_a_ref_loop_is_a_stated_gap(self):
        obj = doc()
        obj["components"]["schemas"]["A"] = {"$ref": "#/components/schemas/B"}
        obj["components"]["schemas"]["B"] = {"$ref": "#/components/schemas/A"}
        obj["paths"]["/loop"] = {"get": {"operationId": "loop", "responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/A"}}}}}}}
        spec = spec_of(obj)
        op = self.op(spec, "loop")
        self.assertEqual(set(op.refs), {"#/components/schemas/A", "#/components/schemas/B"})
        self.assertEqual(op.unresolved, ())

    def test_json_pointer_escapes_and_percent_encoding(self):
        obj = doc()
        obj["components"]["schemas"]["a/b"] = {"type": "string"}
        obj["components"]["schemas"]["c~d"] = {"type": "string"}
        obj["components"]["schemas"]["e f"] = {"type": "string"}
        item = {"get": {"operationId": "esc", "responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {"type": "object", "properties": {
            "a": {"$ref": "#/components/schemas/a~1b"}, "b": {"$ref": "#/components/schemas/c~0d"}, "c": {"$ref": "#/components/schemas/e%20f"}}}}}}}}}
        obj["paths"]["/esc"] = item
        op = self.op(spec_of(obj), "esc")
        self.assertEqual(op.unresolved, ())
        self.assertEqual(len(op.refs), 3)

    def test_an_unresolvable_ref_is_kept_on_the_operation_and_a_stated_gap(self):
        obj = doc()
        obj["paths"]["/broken"] = {"get": {"operationId": "broken", "responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Nope"}}}}}}}
        obj["paths"]["/remote"] = {"get": {"operationId": "remote", "responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {"$ref": "other.json#/Thing"}}}}}}}
        obj["paths"]["/url"] = {"get": {"operationId": "url", "responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {"$ref": "https://example.invalid/x.json"}}}}}}}
        spec = spec_of(obj)
        self.assertEqual(self.op(spec, "broken").unresolved, ("#/components/schemas/Nope",))
        self.assertEqual(self.op(spec, "remote").unresolved, ("other.json#/Thing",))
        self.assertEqual(self.op(spec, "url").unresolved, ("https://example.invalid/x.json",))
        self.assertEqual(sum("cannot be resolved" in p for p in spec.problems), 3)
        self.assertEqual(self.op(spec, "getInvoice").unresolved, ())

    def test_the_span_of_a_ref_target_is_known(self):
        text = text_of(doc())
        spec = read(text, "api/openapi.json")
        start, end = spec.span_of("#/components/schemas/Money")
        self.assertEqual(spec.line_text(start).strip(), '"Money": {')
        self.assertGreater(end, start)
        self.assertIsNone(spec.span_of("#/components/schemas/Nope"))
        self.assertIsNone(spec.span_of("other.json#/x"))

    def test_swagger_two_definitions_and_parameters_resolve(self):
        sw = {"swagger": "2.0", "paths": {"/a/{id}": {"get": {"operationId": "a", "parameters": [{"$ref": "#/parameters/Id"}], "responses": {"200": {"description": "ok", "schema": {"$ref": "#/definitions/Thing"}}}}}},
              "parameters": {"Id": {"name": "id", "in": "path", "required": True, "type": "string"}},
              "definitions": {"Thing": {"type": "object"}}}
        (op,) = spec_of(sw).operations
        self.assertEqual(op.refs, ("#/definitions/Thing", "#/parameters/Id"))
        self.assertEqual(op.unresolved, ())

    def test_a_deep_chain_of_refs_is_not_a_recursion_error(self):
        n = 3000
        schemas = {f"S{i}": {"type": "object", "properties": {"next": {"$ref": f"#/components/schemas/S{i + 1}"}}} for i in range(n)}
        schemas[f"S{n}"] = {"type": "string"}
        obj = doc()
        obj["components"]["schemas"].update(schemas)
        obj["paths"]["/deep"] = {"get": {"operationId": "deep", "responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/S0"}}}}}}}
        self.assertEqual(len(self.op(spec_of(obj), "deep").refs), n + 1)

    def test_a_wide_shared_graph_is_not_exponential(self):
        schemas = {}
        for i in range(60):  # every level refers to the next one twice: a naive expansion has 2**60 paths
            schemas[f"L{i}"] = {"type": "object", "properties": {"a": {"$ref": f"#/components/schemas/L{i + 1}"}, "b": {"$ref": f"#/components/schemas/L{i + 1}"}}}
        schemas["L60"] = {"type": "string"}
        obj = doc()
        obj["components"]["schemas"].update(schemas)
        obj["paths"]["/wide"] = {"get": {"operationId": "wide", "responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/L0"}}}}}}}
        self.assertEqual(len(self.op(spec_of(obj), "wide").refs), 61)


class Signatures(unittest.TestCase):
    def sig(self, obj, operation_id="getInvoice"):
        (op,) = [o for o in spec_of(obj).operations if o.operation_id == operation_id]
        return op

    def changed(self, edit, operation_id="getInvoice"):
        before = doc()
        after = copy.deepcopy(before)
        edit(after)
        a, b = self.sig(before, operation_id), self.sig(after, operation_id)
        return [k for k in a.parts if a.parts[k] != b.parts[k]] if a.signature != b.signature else []

    def test_the_same_document_has_the_same_signature_whatever_the_formatting(self):
        a = read(json.dumps(doc(), indent=2), "x.json")
        b = read(json.dumps(doc(), separators=(",", ":")), "x.json")
        self.assertEqual([o.signature for o in a.operations], [o.signature for o in b.operations])

    def test_a_description_a_summary_or_an_example_is_not_a_contract_change(self):
        def edit(o):
            o["paths"]["/invoices/{invoiceId}"]["get"]["summary"] = "new words"
            o["paths"]["/invoices/{invoiceId}"]["get"]["description"] = "more words"
            o["paths"]["/invoices/{invoiceId}"]["get"]["responses"]["200"]["description"] = "fine"
            o["components"]["schemas"]["Invoice"]["description"] = "an invoice"
            o["components"]["schemas"]["Money"]["example"] = {"amount": 3}
            o["components"]["parameters"]["InvoiceId"]["description"] = "which one"
        self.assertEqual(self.changed(edit), [])

    def test_a_property_called_description_is_a_property_and_not_a_description(self):
        def edit(o):
            o["components"]["schemas"]["Invoice"]["properties"]["description"] = {"type": "string"}
        self.assertEqual(self.changed(edit), ["schemas"])

    def test_a_required_parameter_added(self):
        def edit(o):
            o["paths"]["/invoices/{invoiceId}"]["get"]["parameters"] = [{"name": "expand", "in": "query", "required": True, "schema": {"type": "string"}}]
        self.assertEqual(self.changed(edit), ["parameters"])

    def test_an_optional_parameter_added_is_a_change_too(self):
        def edit(o):
            o["paths"]["/invoices/{invoiceId}"]["get"]["parameters"] = [{"name": "expand", "in": "query", "schema": {"type": "string"}}]
        self.assertEqual(self.changed(edit), ["parameters"])

    def test_a_path_level_parameter_changed_reaches_every_operation_on_the_path(self):
        def edit(o):
            o["components"]["parameters"]["InvoiceId"]["schema"] = {"type": "integer"}
        self.assertEqual(self.changed(edit), ["parameters", "schemas"])

    def test_the_request_body_and_the_responses(self):
        def request(o):
            o["paths"]["/invoices"]["post"]["requestBody"]["required"] = False
        def response(o):
            o["paths"]["/invoices"]["post"]["responses"]["201"]["content"] = {"application/json": {"schema": {"$ref": "#/components/schemas/Invoice"}}}
        def code(o):
            o["paths"]["/invoices"]["post"]["responses"]["400"] = {"description": "bad"}
        self.assertEqual(self.changed(request, "createInvoice"), ["request"])
        self.assertEqual(self.changed(response, "createInvoice"), ["responses"])  # Invoice was already reached through the request body
        self.assertEqual(self.changed(code, "createInvoice"), ["responses"])

    def test_a_schema_two_hops_away_changes_the_operation_and_a_cycle_does_not_hide_it(self):
        def edit(o):
            o["components"]["schemas"]["Money"]["required"] = ["amount", "currency"]
        for operation_id in ("getInvoice", "listInvoices", "createInvoice"):
            with self.subTest(operation=operation_id):
                self.assertEqual(self.changed(edit, operation_id), ["schemas"])
        self.assertEqual(self.changed(edit, "health"), [])

    def test_a_ref_pointing_elsewhere_changes_the_operation(self):
        def edit(o):
            o["paths"]["/invoices/{invoiceId}"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"] = "#/components/schemas/Customer"
        self.assertEqual(self.changed(edit), ["responses"])

    def test_the_operation_id_is_part_of_the_signature(self):
        def edit(o):
            o["paths"]["/invoices/{invoiceId}"]["get"]["operationId"] = "fetchInvoice"
        before, after = doc(), doc()
        edit(after)
        (a,) = [o for o in spec_of(before).operations if o.operation_id == "getInvoice"]
        (b,) = [o for o in spec_of(after).operations if o.operation_id == "fetchInvoice"]
        self.assertEqual([k for k in a.parts if a.parts[k] != b.parts[k]], ["operation_id"])

    def test_an_unresolved_ref_makes_the_signature_unknown_and_so_a_change(self):
        obj = doc()
        obj["paths"]["/broken"] = {"get": {"operationId": "broken", "responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {"$ref": "other.json#/Thing"}}}}}}}
        a, b = spec_of(obj), spec_of(obj)
        (x,) = [o for o in a.operations if o.operation_id == "broken"]
        self.assertFalse(x.known)
        self.assertTrue(self.sig(doc()).known)
        d = diff(a, b)  # the same text twice: nothing readable differs, and still the operation is reported as changed
        self.assertEqual([(c.method, c.path, c.why) for c in d.changed], [("GET", "/v1/broken", ("unresolved",))])


class Diff(unittest.TestCase):
    def pair(self, edit):
        before = doc()
        after = copy.deepcopy(before)
        edit(after)
        return diff(spec_of(before), spec_of(after))

    def test_nothing_changed(self):
        d = self.pair(lambda o: None)
        self.assertEqual((d.removed, d.added, d.changed), ([], [], []))

    def test_removed_and_added_operations_by_method_and_full_path(self):
        def edit(o):
            del o["paths"]["/health"]
            o["paths"]["/invoices/{invoiceId}/pdf"] = {"get": {"operationId": "getInvoicePdf", "responses": {"200": {"description": "ok"}}}}
        d = self.pair(edit)
        self.assertEqual([(o.method, p) for o, p in d.removed], [("GET", "/v1/health")])
        self.assertEqual([(o.method, p) for o, p in d.added], [("GET", "/v1/invoices/{invoiceId}/pdf")])
        self.assertEqual(d.changed, [])

    def test_a_changed_operation_says_what_changed_and_names_both_versions(self):
        def edit(o):
            o["paths"]["/invoices/{invoiceId}"]["get"]["parameters"] = [{"name": "expand", "in": "query", "required": True, "schema": {"type": "string"}}]
        d = self.pair(edit)
        (c,) = d.changed
        self.assertEqual((c.method, c.path, c.why), ("GET", "/v1/invoices/{invoiceId}", ("parameters",)))
        self.assertEqual((c.before.operation_id, c.after.operation_id), ("getInvoice", "getInvoice"))
        self.assertTrue(c.before.line > 0 and c.after.line > 0)
        self.assertEqual(c.before.line, c.after.line)  # the parameters are added after the responses: the method key is where it was

    def test_the_base_path_changing_removes_every_route_and_adds_every_route(self):
        d = self.pair(lambda o: o.__setitem__("servers", [{"url": "https://api.example.invalid/v2"}]))
        self.assertEqual(len(d.removed), 4)
        self.assertEqual(len(d.added), 4)
        self.assertEqual(d.changed, [])

    def test_a_schema_change_reaches_every_operation_that_refers_to_it_through_a_cycle(self):
        d = self.pair(lambda o: o["components"]["schemas"]["Money"].__setitem__("required", ["amount", "currency"]))
        self.assertEqual(sorted(c.after.operation_id for c in d.changed), ["createInvoice", "getInvoice", "listInvoices"])
        self.assertTrue(all(c.why == ("schemas",) for c in d.changed))

    def test_an_operation_with_two_servers_is_compared_per_route(self):
        def both(o):
            o["servers"] = [{"url": "https://a.example.invalid/v1"}, {"url": "https://b.example.invalid/v2"}]
        before = copy.deepcopy(doc())
        both(before)
        after = copy.deepcopy(before)
        after["paths"]["/health"]["get"]["parameters"] = [{"name": "deep", "in": "query", "schema": {"type": "boolean"}}]
        d = diff(spec_of(before), spec_of(after))
        self.assertEqual(sorted((c.method, c.path) for c in d.changed), [("GET", "/v1/health"), ("GET", "/v2/health")])


class Equivalences(unittest.TestCase):
    """What a spec says the same way twice is not a change; what differs by one flag is."""

    def changed(self, edit, operation_id="getInvoice"):
        before = doc()
        after = copy.deepcopy(before)
        edit(after)
        (a,) = [o for o in spec_of(before).operations if o.operation_id == operation_id]
        (b,) = [o for o in spec_of(after).operations if o.operation_id == operation_id]
        return [k for k in a.parts if a.parts[k] != b.parts[k]]

    def test_the_order_of_a_set_like_list_is_not_a_change(self):
        def edit(o):
            o["components"]["schemas"]["Money"]["required"] = ["amount", "currency"]
            o["components"]["schemas"]["Money"]["properties"]["currency"] = {"type": ["string", "null"], "enum": ["EUR", "USD"]}
        before = doc()
        edit(before)
        after = copy.deepcopy(before)
        after["components"]["schemas"]["Money"]["required"] = ["currency", "amount"]
        after["components"]["schemas"]["Money"]["properties"]["currency"] = {"type": ["null", "string"], "enum": ["USD", "EUR"]}
        a, b = spec_of(before), spec_of(after)
        self.assertEqual([o.signature for o in a.operations], [o.signature for o in b.operations])

    def test_an_absent_required_is_required_false_on_a_parameter(self):
        def edit(o):
            o["paths"]["/invoices/{invoiceId}"]["get"]["parameters"] = [{"name": "expand", "in": "query", "schema": {"type": "string"}}]
        before = doc()
        edit(before)
        after = copy.deepcopy(before)
        after["paths"]["/invoices/{invoiceId}"]["get"]["parameters"][0]["required"] = False
        (a,) = [o for o in spec_of(before).operations if o.operation_id == "getInvoice"]
        (b,) = [o for o in spec_of(after).operations if o.operation_id == "getInvoice"]
        self.assertEqual(a.signature, b.signature)

    def test_a_path_parameter_is_required_whatever_the_flag_says(self):
        def edit(o):
            o["components"]["parameters"]["InvoiceId"].pop("required")
        self.assertEqual(self.changed(edit), [])

    def test_the_required_flag_of_a_parameter_flipping_alone_is_a_change(self):
        def edit(o):
            o["paths"]["/invoices/{invoiceId}"]["get"]["parameters"] = [{"name": "expand", "in": "query", "required": True, "schema": {"type": "string"}}]
        before = doc()
        edit(before)
        after = copy.deepcopy(before)
        after["paths"]["/invoices/{invoiceId}"]["get"]["parameters"][0]["required"] = False
        (a,) = [o for o in spec_of(before).operations if o.operation_id == "getInvoice"]
        (b,) = [o for o in spec_of(after).operations if o.operation_id == "getInvoice"]
        self.assertEqual([k for k in a.parts if a.parts[k] != b.parts[k]], ["parameters"])

    def test_a_number_written_one_or_one_point_zero_is_one_number(self):
        before = doc()
        before["components"]["schemas"]["Money"]["properties"]["amount"]["minimum"] = 1
        after = copy.deepcopy(before)
        after["components"]["schemas"]["Money"]["properties"]["amount"]["minimum"] = 1.0
        text_a, text_b = text_of(before), text_of(after).replace("1.0", "1.0")
        self.assertIn("1.0", text_b)
        a, b = read(text_a, "x.json"), read(text_b, "x.json")
        self.assertEqual([o.signature for o in a.operations], [o.signature for o in b.operations])
        after["components"]["schemas"]["Money"]["properties"]["amount"]["minimum"] = 1.5
        c = spec_of(after)
        self.assertNotEqual([o.signature for o in a.operations], [o.signature for o in c.operations])

    def test_a_ref_loop_among_parameters_responses_and_path_items_is_unresolved_and_ends(self):
        obj = doc()
        obj["components"]["parameters"]["A"] = {"$ref": "#/components/parameters/B"}
        obj["components"]["parameters"]["B"] = {"$ref": "#/components/parameters/A"}
        obj["components"]["responses"] = {"R1": {"$ref": "#/components/responses/R2"}, "R2": {"$ref": "#/components/responses/R1"}}
        obj["components"]["pathItems"] = {"P1": {"$ref": "#/components/pathItems/P2"}, "P2": {"$ref": "#/components/pathItems/P1"}}
        obj["paths"]["/loop/{x}"] = {"parameters": [{"$ref": "#/components/parameters/A"}], "get": {"operationId": "loopParam", "responses": {"200": {"$ref": "#/components/responses/R1"}}}}
        obj["paths"]["/pi"] = {"$ref": "#/components/pathItems/P1"}
        spec = spec_of(obj)
        (op,) = [o for o in spec.operations if o.operation_id == "loopParam"]
        self.assertFalse(op.known)
        self.assertIn("#/components/parameters/A", op.unresolved)
        self.assertIn("#/components/responses/R1", op.unresolved)
        self.assertIn(("/pi", None), [(t, m) for t, m, _why in spec.unread])


class Unread(unittest.TestCase):
    """What the reader could not turn into operations is kept, by path and method, so a diff never calls it removed or added."""

    def test_a_path_item_outside_the_document_a_bad_path_and_a_method_that_is_not_an_object_are_kept(self):
        obj = doc()
        obj["paths"]["/remote"] = {"$ref": "other.json#/Item"}
        obj["paths"]["no-slash"] = {"get": {"operationId": "x", "responses": {}}}
        obj["paths"]["/badmethod"] = {"get": "nope", "post": {"operationId": "p", "responses": {}}}
        spec = spec_of(obj)
        self.assertEqual(sorted((t, m) for t, m, _why in spec.unread), [("/badmethod", "GET"), ("/remote", None), ("no-slash", None)])
        self.assertTrue(all(why for _t, _m, why in spec.unread))

    def test_is_unread_covers_every_method_of_an_unread_template_and_only_that_method_otherwise(self):
        obj = doc()
        obj["paths"]["/remote"] = {"$ref": "other.json#/Item"}
        obj["paths"]["/badmethod"] = {"get": "nope", "post": {"operationId": "p", "responses": {}}}
        spec = spec_of(obj)
        self.assertTrue(spec.is_unread("/remote", "DELETE"))
        self.assertTrue(spec.is_unread("/badmethod", "GET"))
        self.assertFalse(spec.is_unread("/badmethod", "POST"))
        self.assertFalse(spec.is_unread("/invoices", "GET"))

    def test_two_operations_with_one_method_and_path_are_a_stated_gap(self):
        obj = doc()
        obj["paths"]["/invoices/{id}"] = {"get": {"operationId": "dup", "responses": {}}}  # the same route as /invoices/{invoiceId} with a parameter written otherwise
        obj["paths"]["/other/{a}"] = {"get": {"operationId": "x", "responses": {}}}
        obj["paths"]["/other/{b}"] = {"get": {"operationId": "y", "responses": {}}}
        spec = spec_of(obj)
        self.assertEqual(sum("two operations" in p for p in spec.problems), 0)  # different templates are different routes
        obj["servers"] = [{"url": "https://a.example.invalid/v1"}, {"url": "https://b.example.invalid/v1"}]
        obj["paths"]["/x"] = {"get": {"operationId": "x1", "responses": {}}}
        obj["paths"]["/x/"] = {"get": {"operationId": "x2", "responses": {}}}  # a trailing slash is the same route
        spec = spec_of(obj)
        self.assertEqual(sum("two operations" in p and "GET /v1/x" in p for p in spec.problems), 1)

    def test_a_change_to_a_path_level_parameter_or_server_is_a_shared_span_of_every_operation_of_the_path(self):
        text = text_of(doc())
        spec = read(text, "x.json")
        get, = [o for o in spec.operations if o.operation_id == "getInvoice"]
        param_line = line_of(text, '"parameters": [')
        self.assertTrue(any(a <= param_line <= b for a, b in get.shared))
        health = [o for o in spec.operations if o.operation_id == "health"][0]
        self.assertEqual(health.shared, ())


class Names(unittest.TestCase):
    def test_snake_case(self):
        for given, want in (("getInvoice", "get_invoice"), ("get_invoice", "get_invoice"), ("GetInvoice", "get_invoice"), ("getInvoiceByID", "get_invoice_by_id"),
                            ("listV2Invoices", "list_v2_invoices"), ("HTMLParser", "html_parser"), ("get-invoice", "get_invoice"), ("get.invoice", "get_invoice"), ("x", "x")):
            with self.subTest(given=given):
                self.assertEqual(names.snake(given), want)

    def test_words(self):
        self.assertEqual(names.words("getInvoiceByID"), ["get", "invoice", "by", "id"])
        self.assertEqual(names.words("list_all-items"), ["list", "all", "items"])

    def test_a_name_made_only_of_generic_words_is_generic(self):
        for generic in ("get", "list", "find", "create", "delete", "getAll", "listItems", "get_data", "post", "update", "fetchAll", "getInfo", "ping", "health", "status", "search", "run"):
            with self.subTest(name=generic):
                self.assertTrue(names.is_generic(generic), generic)
        for specific in ("getInvoice", "listInvoices", "voidInvoice", "getCustomer", "createInvoice", "downloadStatement", "getInvoicePdf"):
            with self.subTest(name=specific):
                self.assertFalse(names.is_generic(specific), specific)


if __name__ == "__main__":
    unittest.main()
