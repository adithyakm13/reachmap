"""The http edge (M8): the endpoints-file client adapter, URL matching, and both directions of the edge with the
findings (a removed route still called, an added route nobody calls). Small repositories are built per test
(`tests/support_cross.py`); the fixture scenarios are checked in test_scenarios_cross.py, which also cross-checks the URL fit
against the independent oracle of tests/fixtures/claims.py."""

import dataclasses
import unittest

from reachmap import propagate
from reachmap.adapters import Route
from reachmap.adapters.http_client import LIT, MIXED, PARAM, WILD, Seg, url_from_parts
from reachmap.adapters.http_client.endpoints_file import method_of
from reachmap.adapters.js.syntax import parse
from reachmap.edges.http import best_matches, match_url
from reachmap.model import Evidence
from tests.support_cross import CrossCase

WORKSPACE = """
workspace = "t"
[limits]
hops = {hops}
hub_importers = {hub}
distinct_max = 3

[repos."t/web"]
host = "git"
remote = "{web}"
language = "typescript"
roles = ["frontend"]
branches = ["main"]
entry_points = {{ kind = "react-router", routes_glob = "src/routes.tsx" }}
http_client = {{ kind = "endpoints-file", path = "src/api/endpoints.ts" }}

[repos."t/api"]
host = "git"
remote = "{api}"
language = "python"
roles = ["backend"]
branches = ["main"]
entry_points = {{ kind = "fastapi" }}

[[edges]]
kind = "http"
from = "t/web"
to = "t/api"
"""

ENDPOINTS = """export const ORDERS_URL = "/api/orders";
export const orderUrl = (id: string) => `/api/orders/${id}`;
export const SUMMARY_URL =
  "/api/orders/summary";
export const EXPORT_URL = "/api/orders/export";
"""

HTTP = """export async function apiGet<T>(url: string): Promise<T> {
  const response = await fetch(url);
  return response.json();
}
export async function apiPost<T>(url: string, body: unknown): Promise<T> {
  const response = await fetch(url, { method: "POST", body: JSON.stringify(body) });
  return response.json();
}
"""

ORDERS_API = """import { apiGet, apiPost } from "../../api/http";
import { ORDERS_URL, orderUrl, SUMMARY_URL } from "../../api/endpoints";

export function fetchOrders() {
  return apiGet<Order[]>(ORDERS_URL);
}

export function fetchOrder(id: string) {
  return apiGet<Order>(orderUrl(id));
}

export function fetchSummary() {
  return apiGet<Summary>(SUMMARY_URL);
}

export function cancelOrder(id: string) {
  return apiPost<Order>(orderUrl(id), {});
}
"""

PAGE = """import { fetchOrders, fetchSummary } from "./api";

export default function OrdersPage() {
  fetchOrders();
  fetchSummary();
  return <div />;
}
"""

DETAIL = """import { fetchOrder } from "./api";

export default function OrderDetailPage() {
  fetchOrder("1");
  return <div />;
}
"""

ROUTES = """import OrdersPage from "./modules/orders/OrdersPage";
import OrderDetailPage from "./modules/orders/OrderDetailPage";

export const routes = (
  <Routes>
    <Route path="/orders" element={<OrdersPage />} />
    <Route path="/orders/:orderId" element={<OrderDetailPage />} />
  </Routes>
);
"""

WEB = {
    "src/api/endpoints.ts": ENDPOINTS, "src/api/http.ts": HTTP, "src/modules/orders/api.ts": ORDERS_API,
    "src/modules/orders/OrdersPage.tsx": PAGE, "src/modules/orders/OrderDetailPage.tsx": DETAIL, "src/routes.tsx": ROUTES,
}

MAIN = """from fastapi import FastAPI

from app.routers.orders import router as orders_router

app = FastAPI()
app.include_router(orders_router, prefix="/api/orders")
"""

ORDERS = """from fastapi import APIRouter

router = APIRouter()


@router.get("")
def list_orders():
    return []


@router.get("/summary")
def summary():
    return {}


@router.get("/{order_id}")
def get_order(order_id: str):
    return {"id": order_id}


@router.post("/{order_id}")
def post_order(order_id: str):
    return {"id": order_id}
"""

API = {"app/__init__.py": "", "app/main.py": MAIN, "app/routers/__init__.py": "", "app/routers/orders.py": ORDERS}


def workspace_text(hops: int = 3, hub: int = 50) -> str:
    return WORKSPACE.replace("{hops}", str(hops)).replace("{hub}", str(hub))


class Http(CrossCase):
    """Both repositories at one commit, then a second commit in one of them (the change)."""

    SHARED = True  # one repository per name for the class, a few commits per test, one git cache

    def build(self, web=None, api=None, web_change=None, api_change=None, hops=3, hub=50):
        web_commits = [{"files": {**WEB, **(web or {})}}] + ([{"files": web_change[0], "delete": web_change[1] if len(web_change) > 1 else []}] if web_change else [])
        api_commits = [{"files": {**API, **(api or {})}}] + ([{"files": api_change[0], "delete": api_change[1] if len(api_change) > 1 else []}] if api_change else [])
        self.repos(web=web_commits, api=api_commits)
        return self.workspace(workspace_text(hops, hub))

    def edges(self, result):
        return sorted((m.client.path, m.client.line, m.route.method, m.route.path, m.kind) for m in result.http_edges)


# ------------------------------------------------------------------------------ URLs as segments


class Urls(unittest.TestCase):
    def test_segments_drop_the_query_the_fragment_and_slashes_at_the_ends(self):
        for parts in ([("lit", "/a/b")], [("lit", "/a/b/")], [("lit", "a//b")], [("lit", "/a/b?x=1")], [("lit", "/a/b#top")], [("lit", "/a/b/?x=/y")]):
            with self.subTest(parts=parts):
                self.assertEqual(url_from_parts(parts).segments, (Seg(LIT, "a"), Seg(LIT, "b")))

    def test_a_parameter_is_a_segment_and_text_after_a_query_is_dropped(self):
        url = url_from_parts([("lit", "/api/orders/"), ("param", "id"), ("lit", "/lines?sort="), ("param", "key")])
        self.assertEqual(url.segments, (Seg(LIT, "api"), Seg(LIT, "orders"), Seg(PARAM, "id"), Seg(LIT, "lines")))
        self.assertEqual((url.partial, url.text), (False, "/api/orders/{}/lines"))

    def test_an_unknown_part_makes_the_url_partial_alone_it_is_a_run_of_segments_inside_a_segment_it_is_one(self):
        lead = url_from_parts([("unknown", "BASE"), ("lit", "/orders")])
        self.assertEqual((lead.segments, lead.partial), ((Seg(WILD, "BASE"), Seg(LIT, "orders")), True))
        mixed = url_from_parts([("lit", "/v"), ("unknown", "VERSION"), ("lit", "/orders")])
        self.assertEqual(mixed.segments, (Seg(MIXED, "VERSION"), Seg(LIT, "orders")))
        self.assertTrue(mixed.partial)
        self.assertEqual(url_from_parts([("lit", "/orders"), ("lit", "?x="), ("unknown", "Q")]).partial, False)  # an unknown part past the query is not part of the URL


# ------------------------------------------------------------------------------ matching


def route(method, path):
    ev = Evidence(repo="t/api", path="app/x.py", line=1, ref="a" * 40, detail="@router")
    return Route("t/api", "api_route", method, path, "app/x.py", "h", "app/x.py", "", (ev,))


def best(url_parts, routes, method="GET"):
    return [(m.route.path, m.kind) for m in best_matches(url_from_parts(url_parts), method, [route(mm, p) for mm, p in routes])]


class Matching(unittest.TestCase):
    ROUTES = [("GET", "/api/orders"), ("GET", "/api/orders/summary"), ("GET", "/api/orders/{order_id}"), ("POST", "/api/orders/{order_id}")]

    def test_a_client_parameter_fits_only_a_route_parameter(self):
        self.assertEqual(best([("lit", "/api/orders/"), ("param", "id")], self.ROUTES), [("/api/orders/{order_id}", "exact")])
        self.assertIsNone(match_url(url_from_parts([("lit", "/api/orders/"), ("param", "id")]), "/api/orders/summary"))

    def test_a_client_literal_may_fit_a_route_parameter_and_says_so(self):
        self.assertEqual(best([("lit", "/api/orders/42")], self.ROUTES), [("/api/orders/{order_id}", "literal_in_parameter")])

    def test_the_route_with_the_most_literal_segments_wins(self):
        self.assertEqual(best([("lit", "/api/orders/summary")], self.ROUTES), [("/api/orders/summary", "exact")])  # not the parameter route

    def test_a_tie_is_ambiguous_and_every_tied_route_is_kept(self):
        routes = [("GET", "/a/{x}/b"), ("GET", "/a/b/{y}"), ("GET", "/a/{x}/{y}")]
        self.assertEqual(sorted(best([("lit", "/a/b/b")], routes)), [("/a/b/{y}", "literal_in_parameter"), ("/a/{x}/b", "literal_in_parameter")])

    def test_the_method_is_part_of_the_match_and_no_method_matches_every_one(self):
        self.assertEqual(best([("lit", "/api/orders/"), ("param", "i")], self.ROUTES, "POST"), [("/api/orders/{order_id}", "exact")])
        ms = best_matches(url_from_parts([("lit", "/api/orders/"), ("param", "i")]), "DELETE", [route(m, p) for m, p in self.ROUTES])
        self.assertEqual([(m.route.method, m.fallback) for m in ms], [("GET", True), ("POST", True)])  # no DELETE route: the others are kept, said
        self.assertEqual(best([("lit", "/nowhere")], self.ROUTES, "DELETE"), [])
        both = best_matches(url_from_parts([("lit", "/api/orders/"), ("param", "i")]), None, [route(m, p) for m, p in self.ROUTES])
        self.assertEqual(sorted((m.route.method, m.route.path) for m in both), [("GET", "/api/orders/{order_id}"), ("POST", "/api/orders/{order_id}")])

    def test_a_route_of_method_any_fits_a_call_of_every_method_exactly_and_nothing_else_is_a_wildcard(self):
        routes = [("ANY", "/api/status")]
        for method in ("GET", "POST", "DELETE", "put"):
            ms = best_matches(url_from_parts([("lit", "/api/status")]), method, [route(m, p) for m, p in routes])
            self.assertEqual([(m.route.method, m.kind, m.fallback) for m in ms], [("ANY", "exact", False)], method)
        for wild in ("*", "ALL", "any_of", "AN", "<unknown:methods>", ""):  # only ANY is a wildcard: another word is a method of its own, and a GET call does not fit it
            ms = best_matches(url_from_parts([("lit", "/api/status")]), "GET", [route(wild, "/api/status")])
            self.assertEqual([(m.route.method, m.fallback) for m in ms], [(wild, True)], wild)  # only the flagged fallback, never an exact fit
        both = best_matches(url_from_parts([("lit", "/api/status")]), "GET", [route("ANY", "/api/status"), route("GET", "/api/status")])
        self.assertEqual(sorted(m.route.method for m in both), ["ANY", "GET"])  # equally specific: a tie, kept and said

    def test_trailing_slash_and_query_do_not_change_the_match(self):
        a = best([("lit", "/api/orders/summary/")], self.ROUTES)
        b = best([("lit", "/api/orders/summary?x=1")], self.ROUTES)
        self.assertEqual(a, b)
        self.assertEqual(a, [("/api/orders/summary", "exact")])
        self.assertEqual(best([("lit", "/api/orders")], [("GET", "/api/orders/")]), [("/api/orders/", "exact")])

    def test_a_path_converter_takes_the_rest_of_the_url(self):
        self.assertEqual(best([("lit", "/files/a/b/c")], [("GET", "/files/{p:path}")]), [("/files/{p:path}", "literal_in_parameter")])
        self.assertEqual(best([("lit", "/files")], [("GET", "/files/{p:path}")]), [])

    def test_a_partial_url_has_no_best_every_route_it_can_fit_is_a_candidate(self):
        url = url_from_parts([("unknown", "BASE"), ("lit", "/orders")])
        found = best_matches(url, "GET", [route("GET", p) for _m, p in self.ROUTES if _m == "GET"] + [route("GET", "/orders"), route("GET", "/v1/orders")])
        # BASE may be nothing, `/api`, `/v1` or even `/api/orders` (then the literal `orders` fits the parameter): none is the best, all are kept
        self.assertEqual(sorted(m.route.path for m in found), ["/api/orders", "/api/orders/{order_id}", "/orders", "/v1/orders"])
        self.assertTrue(all(m.partial for m in found))

    def test_a_route_with_an_unknown_part_fits_and_is_partial(self):
        found = best_matches(url_from_parts([("lit", "/api/orders/x")]), "GET", [route("GET", "/<unknown:PREFIX>/orders/x")])
        self.assertEqual([(m.route.path, m.partial) for m in found], [("/<unknown:PREFIX>/orders/x", True)])

    def test_lengths_must_agree(self):
        self.assertEqual(best([("lit", "/api/orders/1/lines")], self.ROUTES), [])
        self.assertEqual(best([("lit", "/api")], self.ROUTES), [])


# ------------------------------------------------------------------------------ the method of a call


class Methods(unittest.TestCase):
    def methods(self, source, ident="X"):
        parsed = parse(source)
        return [method_of(parsed, i) for i, t in enumerate(parsed.tokens) if t.kind == "ident" and t.value == ident and parsed.p(i + 1) != ":" and parsed.w(i - 1) not in ("const", "import")]

    def test_the_wrapper_name_gives_the_method(self):
        src = "apiGet<T>(X);\napiPost(X, {});\napi_delete(X);\nhttp.put(X);\naxios.patch(X);\nclient.head(X);\nuseQuery(X);\n"
        self.assertEqual(self.methods(src), ["GET", "POST", "DELETE", "PUT", "PATCH", "HEAD", None])

    def test_a_verb_is_taken_only_from_a_known_client_shape_never_from_the_end_of_a_name(self):
        """`fetchWithOptions` ends in the word Options and `target` in `get`: neither is a verb. A wrapper named like
        `apiGet` (the last word is exactly Get) and a member of a known client (`axios.get`, `http.get`, `api.get`) are."""
        src = (
            "fetchWithOptions(X);\nwidgetHead(X);\nforget(X);\nloadTarget(X);\ngetUser(X);\nget(X);\nreset(X);\n"
            "apiGet(X);\nhttp_post(X);\nclientDelete(X);\nhttpPatch(X);\nsafePut(X);\n"
            "axios.get(X);\naxios.head(X);\nhttp.options(X);\napi.post(X);\nclient.get(X);\nthing.get(X);\nthis.service.get(X);\n"
        )
        self.assertEqual(self.methods(src), [
            None, None, None, None, None, None, None,
            "GET", "POST", "DELETE", "PATCH", "PUT",
            "GET", "HEAD", "OPTIONS", "POST", "GET", None, None,
        ])

    def test_fetch_reads_the_method_option(self):
        src = 'fetch(X);\nfetch(X, { method: "POST" });\nfetch(X, { method: \'delete\', body });\nfetch(X, opts);\nfetch(X, { ...base });\nfetch(X, { headers: {} });\nfetch(X, { method: m });\n'
        self.assertEqual(self.methods(src), ["GET", "POST", "DELETE", None, None, "GET", None])

    def test_a_config_object_names_the_method_or_defaults_to_get_for_axios(self):
        src = 'axios({ url: X, method: "put" });\naxios({ url: X });\nrequest({ url: X });\n'
        self.assertEqual(self.methods(src), ["PUT", "GET", None])

    def test_a_reference_outside_any_call_has_no_method(self):
        self.assertEqual(self.methods("const u = X;\nreturn X;\n"), [None, None])

    def test_a_builder_call_inside_a_wrapper_takes_the_wrappers_method(self):
        self.assertEqual(self.methods("apiPost(X(1), body);\n"), ["POST"])


# ------------------------------------------------------------------------------ the endpoints file


class EndpointsFile(Http):
    def calls(self, **kw):
        self.build(**kw)
        ws = self.workspace(workspace_text())
        run = propagate.Run(ws, self.git)
        return run.client("t/web").calls(self.sha("web"))

    def test_constants_builders_and_a_two_line_constant_are_read(self):
        res = self.calls()
        self.assertTrue(res.complete)
        by = {c.name: c for c in res.calls}
        self.assertEqual({n: c.url.text for n, c in by.items()}, {
            "ORDERS_URL": "/api/orders", "orderUrl": "/api/orders/{}", "SUMMARY_URL": "/api/orders/summary",
        })
        summary = by["SUMMARY_URL"]
        self.assertEqual([(e.line, e.detail) for e in summary.via], [(3, "export const SUMMARY_URL ="), (4, '  "/api/orders/summary";')])  # both lines of the constant
        self.assertEqual(by["orderUrl"].via[0].detail, "export const orderUrl = (id: string) => `/api/orders/${id}`;")
        self.assertEqual(summary.evidence.detail, "  return apiGet<Summary>(SUMMARY_URL);")

    def test_a_constant_made_of_others_and_a_nested_builder_evaluate_with_every_line_cited(self):
        endpoints = (
            'const ROOT = "/api";\n'
            'export const ORDERS_URL = ROOT + "/orders";\n'
            'export const LINES_URL = `${ORDERS_URL}/lines`;\n'
            "export const orderUrl = (id: string) => ORDERS_URL + `/${id}`;\n"
            "export const lineUrl = (id: string, n: number) => `${orderUrl(id)}/lines/${n}`;\n"
            'export const LONG_URL = ROOT\n  + "/long"\n  + "/path";\n'
        )
        api = (
            'import { ORDERS_URL, LINES_URL, orderUrl, lineUrl, LONG_URL } from "../../api/endpoints";\n'
            "export const a = [ORDERS_URL, LINES_URL, orderUrl(1), lineUrl(1, 2), LONG_URL];\n"
        )
        res = self.calls(web={"src/api/endpoints.ts": endpoints, "src/modules/orders/api.ts": api})
        urls = {c.name: c for c in res.calls}
        self.assertEqual({n: c.url.text for n, c in urls.items()}, {
            "ORDERS_URL": "/api/orders", "LINES_URL": "/api/orders/lines", "orderUrl": "/api/orders/{}", "lineUrl": "/api/orders/{}/lines/{}", "LONG_URL": "/api/long/path",
        })
        self.assertEqual([e.line for e in urls["LONG_URL"].via], [1, 6, 7, 8])  # the constant over three lines, and the one it is made of
        self.assertTrue(res.complete)

    def test_an_endpoint_that_cannot_be_evaluated_is_partial_not_dropped(self):
        endpoints = 'import { BASE } from "./config";\nexport const ORDERS_URL = BASE + "/orders";\nexport const HOST_URL = import.meta.env.VITE_HOST;\n'
        api = 'import { ORDERS_URL, HOST_URL } from "../../api/endpoints";\nexport const a = [ORDERS_URL, HOST_URL];\n'
        res = self.calls(web={"src/api/endpoints.ts": endpoints, "src/modules/orders/api.ts": api})
        by = {c.name: c for c in res.calls}
        self.assertEqual((by["ORDERS_URL"].partial, by["ORDERS_URL"].url.segments), (True, (Seg(WILD, "BASE"), Seg(LIT, "orders"))))
        self.assertTrue(by["HOST_URL"].partial)

    def test_an_aliased_import_and_a_namespace_import_are_calls_and_a_type_import_is_not(self):
        api = (
            'import { ORDERS_URL as LIST, type SUMMARY_URL } from "../../api/endpoints";\nimport * as ep from "../../api/endpoints";\n'
            "export const a = apiGet(LIST);\nexport const b = apiGet(ep.orderUrl(1));\n"
        )
        res = self.calls(web={"src/modules/orders/api.ts": api})
        self.assertEqual(sorted((c.name, c.local, c.method) for c in res.calls), [("ORDERS_URL", "LIST", "GET"), ("orderUrl", "ep.orderUrl", "GET")])

    def test_an_export_that_is_not_a_url_is_not_an_endpoint(self):
        """A number, an object, a helper with a body, a label and a function that only builds a query string are not URLs."""
        endpoints = (
            ENDPOINTS + "export const PAGE_SIZE = 25;\nexport const DEFAULTS = { retries: 3 };\nexport const TITLE = \"Orders\";\n"
            "export function withQuery(url: string, q: string) {\n  const s = url + '?' + q;\n  return s;\n}\n"
            "export const FLAGS = [\"a\", \"b\"];\nexport const ABS_URL = \"https://api.example.invalid/v2\";\n"
        )
        api = (
            'import { PAGE_SIZE, DEFAULTS, TITLE, withQuery, FLAGS, ABS_URL, ORDERS_URL } from "../../api/endpoints";\n'
            "export const a = [PAGE_SIZE, DEFAULTS.retries, TITLE, withQuery('/x', 'a'), FLAGS, ABS_URL, ORDERS_URL];\n"
        )
        res = self.calls(web={"src/api/endpoints.ts": endpoints, "src/modules/orders/api.ts": api})
        self.assertEqual(sorted(c.name for c in res.calls), ["ABS_URL", "ORDERS_URL"])

    def test_a_file_that_only_mentions_the_name_in_a_comment_or_a_string_or_does_not_import_it_is_no_call(self):
        other = '// ORDERS_URL is the list\nconst s = "ORDERS_URL";\nexport const ORDERS_URL = "/other";\nexport const t = ORDERS_URL;\n'
        res = self.calls(web={"src/other.ts": other})
        self.assertEqual({c.path for c in res.calls}, {"src/modules/orders/api.ts"})

    def test_tests_are_not_client_calls(self):
        res = self.calls(web={"src/__tests__/api.test.ts": 'import { ORDERS_URL } from "../api/endpoints";\nexpect(ORDERS_URL).toBe("/api/orders");\n'})
        self.assertNotIn("src/__tests__/api.test.ts", {c.path for c in res.calls})

    def test_a_re_export_hides_users_and_the_answer_says_it_is_incomplete(self):
        res = self.calls(web={"src/index.ts": 'export { ORDERS_URL } from "./api/endpoints";\n'})
        self.assertFalse(res.complete)
        self.assertTrue(any("re-exports" in why for why in res.reasons))
        self.assertTrue(res.calls)  # what was found is kept

    def test_a_file_that_does_not_lex_cleanly_makes_the_answer_incomplete(self):
        res = self.calls(web={"src/modules/orders/Broken.tsx": 'import { ORDERS_URL } from "../../api/endpoints";\nconst x = `unterminated ${ORDERS_URL};\n'})
        self.assertFalse(res.complete)

    def test_the_endpoints_file_not_in_the_tree_is_an_incomplete_answer_never_no_calls(self):
        self.repos(web=[{"files": {k: v for k, v in WEB.items() if k != "src/api/endpoints.ts"}}], api=[{"files": API}])
        ws = self.workspace(workspace_text())
        res = propagate.Run(ws, self.git).client("t/web").calls(self.sha("web"))
        self.assertEqual((res.calls, res.complete), ((), False))
        self.assertIn("is not in the tree", res.reasons[0])

    def test_an_endpoints_file_that_is_a_folder_is_unreadable_and_recorded(self):
        files = {k: v for k, v in WEB.items() if k != "src/api/endpoints.ts"}
        files["src/api/endpoints.ts/index.ts"] = ENDPOINTS
        self.repos(web=[{"files": files}], api=[{"files": API}])
        ws = self.workspace(workspace_text())
        res = propagate.Run(ws, self.git).client("t/web").calls(self.sha("web"))
        self.assertEqual((res.calls, res.complete), ((), False))
        self.assertTrue(any(k == "read_unreadable" for k, _s, _b in self.kinds()))


# ------------------------------------------------------------------------------ the edge, both directions


class Directions(Http):
    def test_a_client_change_is_matched_to_the_route_it_calls_and_the_modules_the_handler_runs(self):
        api = {
            "app/routers/orders.py": ORDERS.replace("from fastapi import APIRouter\n", "from fastapi import APIRouter\n\nfrom app.services import pricing\n").replace(
                'def summary():\n    return {}', 'def summary():\n    return pricing.total()'),
            "app/services/__init__.py": "", "app/services/pricing.py": "from app.services import tax\n\n\ndef total():\n    return tax.rate()\n",
            "app/services/tax.py": "from app.services import audit\n\n\ndef rate():\n    return audit.note()\n",
            "app/services/audit.py": "from app.services import deep\n\n\ndef note():\n    return deep.x()\n",
            "app/services/deep.py": "def x():\n    return 1\n",
        }
        ws = self.build(api=api, web_change=({"src/modules/orders/api.ts": ORDERS_API.replace("fetchSummary() {", "fetchSummary(): Promise<Summary> {")},))
        result = self.propagate(ws, "t/web", "main~1...main")
        self.assertEqual(self.edges(result), [("src/modules/orders/api.ts", 13, "GET", "/api/orders/summary", "exact")])
        (m,) = result.http_edges
        self.assertEqual((m.direction, m.client_repo, m.server_repo), ("to_server", "t/web", "t/api"))
        self.assertEqual([e.detail for e in m.via], ["export const SUMMARY_URL =", '  "/api/orders/summary";'])
        self.assertEqual([e.detail for e in m.route.evidence], ['@router.get("/summary")', 'app.include_router(orders_router, prefix="/api/orders")'])
        # the route is read at the primary branch of the server, the client at the head of the change
        self.assertEqual({e.ref for e in m.route.evidence}, {self.sha("api")})
        self.assertEqual(m.client.ref, self.sha("web"))
        (b,) = result.backend_calls
        self.assertEqual(b.handler, "app/routers/orders.py")
        self.assertEqual([(x.path, x.depth, x.imported_by) for x in b.modules], [
            ("app/services/__init__.py", 1, "app/routers/orders.py"), ("app/services/pricing.py", 1, "app/routers/orders.py"), ("app/services/tax.py", 2, "app/services/pricing.py"), ("app/services/audit.py", 3, "app/services/tax.py"),
        ])  # three hops: deep.py is the fourth and is not listed
        self.assertTrue(b.complete)
        self.assertEqual([r.area.label for r in result.reached], ["/orders"])  # the page of the web repository, reached inside it
        self.assert_evidence_resolves(result)

    def test_a_server_change_reaches_the_screens_of_its_clients_with_a_chain_that_starts_in_the_server_repository(self):
        ws = self.build(api_change=({"app/routers/orders.py": ORDERS.replace("def summary():\n    return {}", "def summary():\n    return {\"n\": 1}")},))
        result = self.propagate(ws, "t/api", "main~1...main")
        self.assertEqual(self.edges(result), [("src/modules/orders/api.ts", 13, "GET", "/api/orders/summary", "exact")])
        self.assertEqual(result.http_edges[0].direction, "to_client")
        self.assertEqual({e.ref for e in result.http_edges[0].client.__class__ and [result.http_edges[0].client]}, {self.sha("web")})  # the client file is read at its primary branch
        screens = [r for r in result.reached if r.area.kind == "screen"]
        self.assertEqual([r.area.label for r in screens], ["/orders"])
        chain = screens[0].area.chains[0]
        self.assertEqual([e.repo for e in chain], ["t/web", "t/web", "t/web", "t/web"])
        self.assertEqual(chain[0].detail, "  return apiGet<Summary>(SUMMARY_URL);")  # the client line is the first hop; the pass inside the handler is not a hop
        self.assertEqual(chain[-1].detail, '    <Route path="/orders" element={<OrdersPage />} />')
        self.assertEqual([p.repo for p in result.passes], ["t/api", "t/web"])
        self.assertEqual({r.area.label for r in result.reached if r.area.kind == "api_route"}, {"GET /api/orders/summary"})
        self.assert_evidence_resolves(result)

    def test_a_literal_in_a_parameter_is_marked_and_the_more_specific_route_is_the_only_edge(self):
        web = {"src/modules/orders/api.ts": ORDERS_API + 'import { EXPORT_URL } from "../../api/endpoints";\nexport function exportOrders() {\n  return apiGet<Blob>(EXPORT_URL);\n}\n'}
        ws = self.build(web=web, api_change=({"app/routers/orders.py": ORDERS.replace("def get_order(order_id: str):\n    return {\"id\": order_id}", "def get_order(order_id: str):\n    return {\"id\": order_id, \"v\": 2}")},))
        result = self.propagate(ws, "t/api", "main~1...main")
        by = {(m.client.line, m.route.path): m for m in result.http_edges}
        lit = [m for m in result.http_edges if m.url == "/api/orders/export"]
        self.assertEqual([(m.route.path, m.kind, m.literal_in_parameter) for m in lit], [("/api/orders/{order_id}", "literal_in_parameter", True)])
        self.assertIn("literal_in_parameter", lit[0].note)
        exact = [m for m in result.http_edges if m.url == "/api/orders/{}"]
        self.assertEqual([(m.route.path, m.kind) for m in exact], [("/api/orders/{order_id}", "exact")])
        self.assertTrue(by)

    def test_a_call_whose_method_cannot_be_told_matches_every_method_and_says_so(self):
        api_ts = ORDERS_API.replace("return apiGet<Order>(orderUrl(id));", "return useQuery<Order>(orderUrl(id));")
        ws = self.build(web={"src/modules/orders/api.ts": api_ts}, api_change=({"app/routers/orders.py": ORDERS.replace('return {"id": order_id}', 'return {"id": order_id, "v": 2}')},))
        result = self.propagate(ws, "t/api", "main~1...main")
        mine = [m for m in result.http_edges if m.client.line == 9]
        self.assertEqual(sorted((m.route.method, m.route.path) for m in mine), [("GET", "/api/orders/{order_id}"), ("POST", "/api/orders/{order_id}")])
        for m in mine:
            self.assertTrue(m.method_unknown)
            self.assertIn("method_unknown", m.note)
            self.assertEqual(len(m.ambiguous_with), 1)
        self.assertFalse(any(k == "method_unknown" for k, _s, _b in self.kinds()))  # a note on the edge, not a record

    def test_a_post_call_matches_the_post_route_only(self):
        ws = self.build(api_change=({"app/routers/orders.py": ORDERS.replace('def post_order(order_id: str):\n    return {"id": order_id}', 'def post_order(order_id: str):\n    return {"id": order_id, "v": 2}')},))
        result = self.propagate(ws, "t/api", "main~1...main")
        self.assertEqual(self.edges(result), [("src/modules/orders/api.ts", 17, "POST", "/api/orders/{order_id}", "exact")])

    def test_the_hop_cap_stops_the_edge_and_says_so_only_when_the_pass_had_a_route_to_carry(self):
        change = ({"app/routers/orders.py": ORDERS.replace("def summary():\n    return {}", "def summary():\n    return {\"n\": 1}")},)
        ws = self.build(api_change=change)
        result = self.propagate(ws, "t/api", "main~1...main", limits=dataclasses.replace(ws.limits, hops=0))
        self.assertEqual(self.kinds(), [("hop_cap", "edge:http:t/web:t/api", False)])  # the declared direction, web to api, though the change travels back
        self.assertEqual((result.http_edges, [p.repo for p in result.passes], result.complete), ((), ["t/api"], False))
        self.assertTrue(any("hop limit" in why for why in result.reasons))
        # a change that reaches no route has nothing to carry over the edge
        self.records.clear()
        self.repos(web=[{"files": WEB}], api=[{"files": API}, {"files": {"app/routers/other.py": "def helper():\n    return 1\n"}}])
        ws2 = self.workspace(workspace_text())
        self.propagate(ws2, "t/api", "main~1...main", limits=dataclasses.replace(ws2.limits, hops=0))
        self.assertEqual(self.kinds(), [])


class Findings(Http):
    def test_a_removed_route_that_a_client_still_names_is_a_finding_with_evidence_at_both_ends(self):
        removed = ORDERS.replace('@router.get("/{order_id}")\ndef get_order(order_id: str):\n    return {"id": order_id}\n\n\n', "")
        ws = self.build(api_change=({"app/routers/orders.py": removed},))
        result = self.propagate(ws, "t/api", "main~1...main")
        (f,) = result.findings
        self.assertEqual((f.kind, f.risk, f.confidence), ("removed_route_still_called", "high", "low"))
        self.assertEqual([(e.repo, e.path, e.detail, e.ref) for e in f.evidence], [
            ("t/web", "src/modules/orders/api.ts", "  return apiGet<Order>(orderUrl(id));", self.sha("web")),
            ("t/api", "app/routers/orders.py", '@router.get("/{order_id}")', self.sha("api", "main~1")),  # the removed decorator, at the base
        ])
        removed_edges = [m for m in result.http_edges if m.removed]
        # no GET route fits the URL any more; the POST route of the same path is kept with a note, and the finding stands
        self.assertEqual([(m.route.method, m.route.path, m.rematched.method) for m in removed_edges], [("GET", "/api/orders/{order_id}", "POST")])
        self.assertEqual({e.ref for e in removed_edges[0].route.evidence}, {self.sha("api", "main~1")})
        self.assertEqual([r.area.label for r in result.reached if r.area.kind == "screen"], ["/orders/:orderId"])  # the removed thing is followed too
        self.assert_evidence_resolves(result)

    def test_a_removed_route_that_was_the_best_match_is_always_the_finding_even_when_the_url_now_fits_another_route(self):
        """GET /summary is removed; the client's literal `summary` now fits GET /{order_id} (literal_in_parameter): a different
        handler answers a call that was written for the removed one. High risk, low confidence, the rematch in the detail."""
        removed = ORDERS.replace('@router.get("/summary")\ndef summary():\n    return {}\n\n\n', "")
        ws = self.build(api_change=({"app/routers/orders.py": removed},))
        result = self.propagate(ws, "t/api", "main~1...main")
        (f,) = result.findings
        self.assertEqual((f.kind, f.risk, f.confidence), ("removed_route_still_called", "high", "low"))
        self.assertIn("GET /api/orders/{order_id}", f.detail)  # the rematch
        self.assertIn("literal_in_parameter", f.detail)
        (m,) = [m for m in result.http_edges if m.removed]
        self.assertEqual((m.route.path, m.rematched.path), ("/api/orders/summary", "/api/orders/{order_id}"))
        self.assertIn("now fits", m.note)
        self.assertEqual([r.area.label for r in result.reached if r.area.kind == "screen"], ["/orders"])  # and the client is followed

    def test_a_removed_route_is_a_finding_when_a_client_that_cannot_tell_its_method_still_fits_another_method(self):
        api_ts = ORDERS_API.replace("return apiGet<Order>(orderUrl(id));", "return useQuery<Order>(orderUrl(id));")
        removed = ORDERS.replace('@router.get("/{order_id}")\ndef get_order(order_id: str):\n    return {"id": order_id}\n\n\n', "")
        ws = self.build(web={"src/modules/orders/api.ts": api_ts}, api_change=({"app/routers/orders.py": removed},))
        result = self.propagate(ws, "t/api", "main~1...main")
        (f,) = result.findings
        self.assertEqual((f.kind, f.risk, f.confidence), ("removed_route_still_called", "high", "low"))
        self.assertIn("POST /api/orders/{order_id}", f.detail)

    def test_the_identity_of_a_finding_does_not_move_with_the_line_of_the_client(self):
        removed = ORDERS.replace('@router.get("/{order_id}")\ndef get_order(order_id: str):\n    return {"id": order_id}\n\n\n', "")
        first = self.propagate(self.build(api_change=({"app/routers/orders.py": removed},)), "t/api", "main~1...main").findings
        self.records.clear()
        shifted = ORDERS_API.replace("export function fetchOrders", "// a comment above\n// and another\nexport function fetchOrders")
        second = self.propagate(self.build(web={"src/modules/orders/api.ts": shifted}, api_change=({"app/routers/orders.py": removed},)), "t/api", "main~1...main").findings
        self.assertNotEqual(first[0].evidence[0].line, second[0].evidence[0].line)
        self.assertEqual(first[0].identity, second[0].identity)
        self.assertFalse(any(part.isdigit() for part in first[0].identity))

    def test_an_added_route_that_no_client_names_is_said_and_one_that_a_client_names_is_not(self):
        added = ORDERS + '\n\n@router.get("/{order_id}/lines")\ndef lines(order_id: str):\n    return []\n\n\n@router.get("/export")\ndef export():\n    return ""\n'
        web = {"src/modules/orders/api.ts": ORDERS_API + 'import { EXPORT_URL } from "../../api/endpoints";\nexport function exportOrders() {\n  return apiGet<Blob>(EXPORT_URL);\n}\n'}
        ws = self.build(web=web, api_change=({"app/routers/orders.py": added},))
        result = self.propagate(ws, "t/api", "main~1...main")
        (f,) = result.findings
        self.assertEqual((f.kind, f.risk, f.confidence), ("added_route_uncalled", "low", "medium"))
        self.assertEqual([e.detail for e in f.evidence], ['@router.get("/{order_id}/lines")'])
        self.assertEqual([e.ref for e in f.evidence], [self.sha("api")])
        exported = [m for m in result.http_edges if m.route.path == "/api/orders/export"]
        self.assertEqual([m.kind for m in exported], ["exact"])

    def test_no_pre_change_version_means_no_removed_or_added_claim(self):
        removed = ORDERS.replace('@router.get("/{order_id}")\ndef get_order(order_id: str):\n    return {"id": order_id}\n\n\n', "")
        ws = self.build(api_change=({"app/routers/orders.py": removed},))
        from dataclasses import replace
        from reachmap.changeset import resolve
        spec = f"{self.sha('api', 'main~1')}...{self.sha('api')}"
        change = replace(resolve(ws, "t/api", git=self.git, compare=spec), pre_change_sha=None)
        result = propagate.propagate(ws, "t/api", change, git=self.git)
        self.assertEqual(result.findings, ())
        self.assertEqual([m for m in result.http_edges if m.removed], [])

    def test_a_client_whose_repository_cannot_be_read_is_an_incompleteness_not_no_client_and_no_uncalled_claim(self):
        added = ORDERS + '\n\n@router.get("/{order_id}/lines")\ndef lines(order_id: str):\n    return []\n'
        self.repos(api=[{"files": API}, {"files": {"app/routers/orders.py": added}}])
        self.urls["web"] = "file://" + str(self.tmp / "nowhere")
        ws = self.workspace(workspace_text())
        result = self.propagate(ws, "t/api", "main~1...main")
        self.assertEqual(result.findings, ())  # nobody was found to name the route, but nobody could be looked for
        self.assertFalse(result.complete)
        kinds = {k for k, _s, _b in self.kinds()}
        self.assertEqual(kinds, {"read_unreadable", "edge_incomplete"})
        blocking = [(k, s) for k, s, b in self.kinds() if b]
        self.assertIn(("edge_incomplete", "edge:http:t/web:t/api"), blocking)
        self.assertTrue(any(h.kind == "http" and not h.complete for h in result.hops))

    def test_a_client_url_that_cannot_be_evaluated_is_a_candidate_recorded_and_never_matched_silently(self):
        endpoints = 'import { BASE } from "./config";\nexport const ORDERS_URL = BASE + "/orders";\n'
        api_ts = 'import { ORDERS_URL } from "../../api/endpoints";\nexport function fetchOrders() {\n  return apiGet(ORDERS_URL);\n}\n'
        page = 'import { fetchOrders } from "./api";\nexport default function OrdersPage() {\n  fetchOrders();\n  return <div />;\n}\n'
        ws = self.build(web={"src/api/endpoints.ts": endpoints, "src/modules/orders/api.ts": api_ts, "src/modules/orders/OrdersPage.tsx": page},
                        api_change=({"app/routers/orders.py": ORDERS.replace("def list_orders():\n    return []", "def list_orders():\n    return [1]")},))
        result = self.propagate(ws, "t/api", "main~1...main")
        (m,) = result.http_edges
        self.assertEqual((m.route.path, m.candidate, m.kind), ("/api/orders", True, "exact"))
        self.assertIn("candidate", m.note)
        self.assertEqual(self.kinds(), [("client_url_partial", "t/web:src/modules/orders/api.ts:3", False)])
        screens = [r for r in result.reached if r.area.kind == "screen"]
        self.assertEqual([(r.area.label, r.area.by_name, r.area.priority) for r in screens], [("/orders", True, "candidate")])  # shown apart, never must test
        self.assertEqual([r.area.priority for r in result.reached if r.area.kind == "api_route"], ["must_test"])


class Methods2(Http):
    EXPORT_WEB = ORDERS_API + 'import { EXPORT_URL } from "../../api/endpoints";\nexport function exportOrders() {\n  return %s(EXPORT_URL);\n}\n'
    ADDED_GET = ORDERS + '\n\n@router.get("/export")\ndef export():\n    return ""\n'

    def test_a_wrapper_that_ends_in_a_verb_like_word_is_no_verb_and_makes_no_uncalled_route(self):
        """`fetchWithOptions(EXPORT_URL)` was read as an OPTIONS call, fitted no route, and the new GET /export was called uncalled."""
        ws = self.build(web={"src/modules/orders/api.ts": self.EXPORT_WEB % "fetchWithOptions"}, api_change=({"app/routers/orders.py": self.ADDED_GET},))
        result = self.propagate(ws, "t/api", "main~1...main")
        self.assertEqual(result.findings, ())
        (m,) = [m for m in result.http_edges if m.route.path == "/api/orders/export"]
        self.assertEqual((m.method, m.method_unknown), (None, True))

    def test_a_fallback_to_the_routes_of_other_methods_is_kept_when_no_route_of_the_method_fits(self):
        web = {"src/modules/orders/api.ts": self.EXPORT_WEB % "apiGet"}
        api = {"app/routers/orders.py": ORDERS.replace('@router.get("/{order_id}")\ndef get_order(order_id: str):\n    return {"id": order_id}\n\n\n', "")}
        added = api["app/routers/orders.py"] + '\n\n@router.post("/export")\ndef export():\n    return ""\n'
        ws = self.build(web=web, api=api, api_change=({"app/routers/orders.py": added},))
        result = self.propagate(ws, "t/api", "main~1...main")
        (m,) = [m for m in result.http_edges if m.route.path == "/api/orders/export"]
        self.assertEqual((m.route.method, m.method, m.method_unknown), ("POST", "GET", True))
        self.assertIn("method_unknown", m.note)
        self.assertIn("GET", m.note)
        self.assertEqual(result.findings, ())

    def test_a_reached_client_call_that_fits_no_route_is_recorded_not_dropped(self):
        endpoints = ENDPOINTS.replace('"/api/orders"', '"/v2/orders"')
        ws = self.build(web={"src/api/endpoints.ts": endpoints}, web_change=({"src/modules/orders/api.ts": ORDERS_API.replace("fetchOrders() {", "fetchOrders(): Promise<Order[]> {")},))
        result = self.propagate(ws, "t/web", "main~1...main")
        self.assertEqual(result.http_edges, ())
        self.assertEqual(self.kinds(), [("client_url_unmatched", "t/web:src/modules/orders/api.ts:5", False)])

    def test_an_added_route_is_not_called_uncalled_while_a_client_call_fits_no_route(self):
        endpoints = ENDPOINTS.replace('"/api/orders"', '"/v2/orders"')
        added = ORDERS + '\n\n@router.get("/{order_id}/lines")\ndef lines(order_id: str):\n    return []\n'
        ws = self.build(web={"src/api/endpoints.ts": endpoints}, api_change=({"app/routers/orders.py": added},))
        result = self.propagate(ws, "t/api", "main~1...main")
        self.assertEqual(result.findings, ())  # the unmatched call may be the new route under a prefix the match does not know
        self.assertIn(("client_url_unmatched", "t/web:src/modules/orders/api.ts:5", False), self.kinds())
        self.assertFalse(result.complete)

    def test_an_added_route_is_not_called_uncalled_after_a_client_scan_that_was_incomplete(self):
        """Mutation M13: the check that every client edge was read completely was dropped and nothing failed."""
        added = ORDERS + '\n\n@router.get("/{order_id}/lines")\ndef lines(order_id: str):\n    return []\n'
        ws = self.build(web={"src/index.ts": 'export { ORDERS_URL } from "./api/endpoints";\n'}, api_change=({"app/routers/orders.py": added},))
        result = self.propagate(ws, "t/api", "main~1...main")
        self.assertEqual(result.findings, ())
        self.assertFalse(result.complete)
        self.assertTrue(any(h.kind == "http" and not h.complete for h in result.hops) or result.reasons)

    def test_a_client_repository_with_no_http_client_configured_is_an_incomplete_edge_not_a_skip(self):
        text = workspace_text().replace('http_client = {{ kind = "endpoints-file", path = "src/api/endpoints.ts" }}\n', "")
        self.build(web_change=({"src/modules/orders/api.ts": ORDERS_API.replace("fetchSummary() {", "fetchSummary(): Promise<Summary> {")},))
        result = self.propagate(self.workspace(text), "t/web", "main~1...main")
        self.assertIn(("edge_incomplete", "edge:http:t/web:t/api", True), self.kinds())
        self.assertFalse(result.complete)

    def test_a_partial_and_an_exact_endpoint_on_one_line_keep_the_backed_screen(self):
        """The candidate pass for the partial URL must not shadow the pass for the exact one on the same client line."""
        endpoints = ENDPOINTS + 'import { BASE } from "./config";\nexport const AAA_URL = BASE + "/orders";\n'
        api_ts = ORDERS_API.replace("return apiGet<Order[]>(ORDERS_URL);", "return apiGet<Order[]>(flag() ? AAA_URL : ORDERS_URL);").replace(
            'import { ORDERS_URL, orderUrl, SUMMARY_URL } from "../../api/endpoints";', 'import { AAA_URL, ORDERS_URL, orderUrl, SUMMARY_URL } from "../../api/endpoints";')
        ws = self.build(web={"src/api/endpoints.ts": endpoints, "src/modules/orders/api.ts": api_ts},
                        api_change=({"app/routers/orders.py": ORDERS.replace("def list_orders():\n    return []", "def list_orders():\n    return [1]")},))
        result = self.propagate(ws, "t/api", "main~1...main")
        screens = [(r.area.label, r.area.by_name, r.area.priority) for r in result.reached if r.area.kind == "screen"]
        self.assertEqual(screens, [("/orders", False, "must_test")])

    def test_the_cap_on_the_modules_a_handler_runs_has_its_own_subject_counted_per_depth(self):
        api = {
            "app/routers/orders.py": ORDERS.replace("from fastapi import APIRouter\n", "from fastapi import APIRouter\n\nfrom app.services import a, b, c\n"),
            "app/services/__init__.py": "", "app/services/a.py": "x = 1\n", "app/services/b.py": "x = 2\n", "app/services/c.py": "x = 3\n",
        }
        ws = self.build(api=api, web_change=({"src/modules/orders/api.ts": ORDERS_API.replace("fetchSummary() {", "fetchSummary(): Promise<Summary> {")},))
        result = self.propagate(ws, "t/web", "main~1...main", limits=dataclasses.replace(ws.limits, module_cap=2))
        self.assertIn(("module_cap", "backend:t/api:depth:1", False), self.kinds())
        (b,) = result.backend_calls
        self.assertEqual((len(b.modules), b.complete), (2, False))


if __name__ == "__main__":
    unittest.main()
