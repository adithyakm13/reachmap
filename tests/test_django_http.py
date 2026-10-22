"""The http edge into a Django repository: a client call fits a route whose view answers every method (`ANY`), a change in a view reaches the
screens whose client calls its route, a removed route that a client still names and an added route nobody calls are findings, and a route made
from a regex fits only as a candidate. Small repositories per test (`tests/support_cross.py`); the fixture scenarios are in test_scenarios_dj.py.
"""

import unittest

from reachmap.adapters import Route
from reachmap.adapters.http_client import url_from_parts
from reachmap.edges.http import best_matches
from reachmap.model import Evidence
from tests.support_cross import CrossCase

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
http_client = {{ kind = "endpoints-file", path = "src/api/endpoints.ts" }}

[repos."t/shop"]
host = "git"
remote = "{shop}"
language = "python"
roles = ["backend"]
branches = ["main"]
entry_points = {{ kind = "django" }}

[[edges]]
kind = "http"
from = "t/web"
to = "t/shop"
"""

ENDPOINTS = """export const ITEMS_URL = "/api/items/";
export const itemUrl = (id: string) => `/api/items/${id}/`;
export const ARCHIVE_URL = "/api/archive/2024";
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

ITEMS_API = """import { apiGet, apiPost } from "../../api/http";
import { ARCHIVE_URL, ITEMS_URL, itemUrl } from "../../api/endpoints";

export function fetchItems() {
  return apiGet<Item[]>(ITEMS_URL);
}

export function fetchItem(id: string) {
  return apiGet<Item>(itemUrl(id));
}

export function saveItem(id: string) {
  return apiPost<Item>(itemUrl(id), {});
}

export function fetchArchive() {
  return apiGet<Item[]>(ARCHIVE_URL);
}
"""

LIST_PAGE = """import { fetchItems } from "./api";

export default function ItemsPage() {
  fetchItems();
  return <div />;
}
"""

DETAIL_PAGE = """import { fetchItem, saveItem } from "./api";

export default function ItemDetailPage() {
  fetchItem("1");
  saveItem("1");
  return <div />;
}
"""

ARCHIVE_PAGE = """import { fetchArchive } from "./api";

export default function ArchivePage() {
  fetchArchive();
  return <div />;
}
"""

ROUTES = """import ArchivePage from "./modules/items/ArchivePage";
import ItemDetailPage from "./modules/items/ItemDetailPage";
import ItemsPage from "./modules/items/ItemsPage";

export const routes = (
  <Routes>
    <Route path="/items" element={<ItemsPage />} />
    <Route path="/items/:itemId" element={<ItemDetailPage />} />
    <Route path="/archive" element={<ArchivePage />} />
  </Routes>
);
"""

WEB = {
    "src/api/endpoints.ts": ENDPOINTS, "src/api/http.ts": HTTP, "src/modules/items/api.ts": ITEMS_API, "src/modules/items/ItemsPage.tsx": LIST_PAGE,
    "src/modules/items/ItemDetailPage.tsx": DETAIL_PAGE, "src/modules/items/ArchivePage.tsx": ARCHIVE_PAGE, "src/routes.tsx": ROUTES,
}

SETTINGS = 'ROOT_URLCONF = "config.urls"\n'
ROOT = 'from django.urls import include, path\n\nurlpatterns = [\n    path("api/", include("shop.urls")),\n]\n'
URLS = """from django.urls import path, re_path

from . import views

urlpatterns = [
    path("items/", views.item_list),
    path("items/<int:item_id>/", views.item_detail),
    re_path(r"^archive/(?P<year>[0-9]{4})/$", views.archive),
]
"""
VIEWS = """def item_list(request):
    return []


def item_detail(request, item_id):
    return {}


def archive(request, year):
    return []
"""

SHOP = {
    "config/__init__.py": "", "config/settings.py": SETTINGS, "config/urls.py": ROOT, "shop/__init__.py": "", "shop/urls.py": URLS, "shop/views.py": VIEWS,
}


def route(method, path, partial=False, approximate=False):
    ev = Evidence(repo="t/shop", path="shop/urls.py", line=1, ref="a" * 40, detail="path(")
    return Route("t/shop", "api_route", method, path, "shop/urls.py", "h", "shop/views.py", "", (ev,), partial, approximate)


class Matching(unittest.TestCase):
    def test_a_route_that_answers_any_method_fits_a_call_of_every_method_exactly(self):
        for method in ("GET", "POST", "DELETE"):
            ms = best_matches(url_from_parts([("lit", "/api/items/"), ("param", "id")]), method, [route("ANY", "/api/items/{item_id}")])
            self.assertEqual([(m.route.path, m.kind, m.fallback) for m in ms], [("/api/items/{item_id}", "exact", False)], method)

    def test_a_call_whose_method_is_not_known_fits_it_too(self):
        ms = best_matches(url_from_parts([("lit", "/api/items")]), None, [route("ANY", "/api/items")])
        self.assertEqual([m.route.method for m in ms], ["ANY"])

    def test_only_a_route_that_reads_a_pattern_fits_by_name_a_partial_route_with_a_literal_path_does_not(self):
        """FastAPI marks a route partial for a `methods=` that is not a literal: its path is exact, so a match is not a candidate."""
        url = url_from_parts([("lit", "/api/items")])
        exact = best_matches(url, "GET", [route("GET", "/api/items", partial=True)])
        self.assertEqual([m.partial for m in exact], [False])
        approx = best_matches(url, "GET", [route("GET", "/api/items", partial=True, approximate=True)])
        self.assertEqual([m.partial for m in approx], [True])

    def test_a_route_of_one_method_still_fits_only_that_method(self):
        ms = best_matches(url_from_parts([("lit", "/api/items")]), "POST", [route("GET", "/api/items"), route("ANY", "/api/other")])
        self.assertEqual([(m.route.path, m.fallback) for m in ms], [("/api/items", True)])  # the other method: kept, said


class Edge(CrossCase):
    SHARED = True

    def build(self, shop=None, shop_change=None, web=None):
        shop_commits = [{"files": {**SHOP, **(shop or {})}}] + ([{"files": shop_change[0], "delete": shop_change[1] if len(shop_change) > 1 else []}] if shop_change else [])
        self.repos(web=[{"files": {**WEB, **(web or {})}}], shop=shop_commits)
        return self.workspace(WORKSPACE)

    def edges(self, result):
        return sorted((m.client.path, m.client.line, m.route.method, m.route.path, m.kind, m.candidate) for m in result.http_edges)

    def test_a_changed_view_reaches_the_screens_whose_client_calls_its_route_whatever_the_method(self):
        ws = self.build(shop_change=({"shop/views.py": VIEWS.replace("    return {}", "    return {1: 1}")},))
        result = self.propagate(ws, "t/shop", "main~1...main")
        self.assertEqual([(r.area.repo, r.area.label) for r in result.reached if r.area.kind == "screen"], [("t/web", "/items/:itemId")])
        self.assertEqual(self.edges(result), [
            ("src/modules/items/api.ts", 9, "ANY", "/api/items/{item_id}", "exact", False),
            ("src/modules/items/api.ts", 13, "ANY", "/api/items/{item_id}", "exact", False),
        ])  # the GET of fetchItem and the POST of saveItem: one route answers both
        self.assert_evidence_resolves(result)

    def test_the_change_reaches_only_the_route_it_changed(self):
        ws = self.build(shop_change=({"shop/views.py": VIEWS.replace("    return []\n\n\ndef item_detail", "    return [1]\n\n\ndef item_detail")},))
        result = self.propagate(ws, "t/shop", "main~1...main")
        self.assertEqual([(r.area.repo, r.area.label) for r in result.reached if r.area.kind == "screen"], [("t/web", "/items")])

    def test_a_removed_route_that_a_client_still_names_is_the_finding_high_risk_low_confidence(self):
        urls = URLS.replace('    path("items/", views.item_list),\n', "")
        ws = self.build(shop_change=({"shop/urls.py": urls},))
        result = self.propagate(ws, "t/shop", "main~1...main")
        (finding,) = [f for f in result.findings if f.kind == "removed_route_still_called"]
        self.assertEqual((finding.risk, finding.confidence), ("high", "low"))
        self.assertEqual({e.path for e in finding.evidence}, {"src/modules/items/api.ts", "shop/urls.py"})
        removed = [m for m in result.http_edges if m.removed]
        self.assertEqual([(m.route.method, m.route.path) for m in removed], [("ANY", "/api/items")])
        self.assert_evidence_resolves(result)

    def test_a_route_narrowed_from_every_method_to_some_is_not_removed_for_the_calls_that_still_fit(self):
        views = "from django.views.decorators.http import require_GET\n\n\n" + VIEWS.replace("def item_list", "@require_GET\ndef item_list", 1)
        ws = self.build(shop_change=({"shop/views.py": views},))
        result = self.propagate(ws, "t/shop", "main~1...main")
        self.assertEqual([f.kind for f in result.findings if f.kind == "removed_route_still_called"], [])  # fetchItems is a GET: GET /api/items still answers it
        # a call of a method the route no longer answers is still the finding: saveItem posts to the detail route, which is narrowed to GET too
        views2 = "from django.views.decorators.http import require_GET\n\n\n" + VIEWS.replace("def item_detail", "@require_GET\ndef item_detail", 1)
        ws = self.build(shop_change=({"shop/views.py": views2},))
        result = self.propagate(ws, "t/shop", "main~1...main")
        found = [f for f in result.findings if f.kind == "removed_route_still_called"]
        self.assertEqual(len(found), 1)
        self.assertIn("/api/items/{item_id}", found[0].detail)

    def test_an_added_route_that_no_client_names_is_said(self):
        urls = URLS.replace("\n]\n", '\n    path("export/", views.item_list),\n]\n')
        ws = self.build(shop_change=({"shop/urls.py": urls},))
        result = self.propagate(ws, "t/shop", "main~1...main")
        added = [f for f in result.findings if f.kind == "added_route_uncalled"]
        self.assertEqual([(f.risk, f.confidence) for f in added], [("low", "medium")])

    def test_a_regex_route_fits_only_as_a_candidate_never_as_a_must_test_screen(self):
        ws = self.build(shop_change=({"shop/views.py": VIEWS.replace("def archive(request, year):\n    return []", "def archive(request, year):\n    return [2]")},))
        result = self.propagate(ws, "t/shop", "main~1...main")
        self.assertEqual([r.area.label for r in result.reached if r.area.repo == "t/shop"], ["ANY /api/archive/{year}"])
        self.assertEqual(self.edges(result), [("src/modules/items/api.ts", 17, "ANY", "/api/archive/{year}", "literal_in_parameter", True)])
        screens = [(r.area.label, r.area.by_name, r.area.priority) for r in result.reached if r.area.kind == "screen"]
        self.assertEqual(screens, [("/archive", True, "candidate")])
        self.assertTrue(any(r.kind == "route_path_partial" for r in self.records))


if __name__ == "__main__":
    unittest.main()
