"""What a change reaches in a Django project: the routes of a changed view (and only of the changed method of a class view), of
a changed route line or include line, of a helper that a view calls through the walk, of a changed router registration, the
unmounted URLconf that claims nothing, `ROOT_URLCONF`, and the completeness of all of it.

Chains are checked as lists of (path, line); every Evidence of every chain resolves at its pinned sha (`Base.reach`).
"""

import unittest

from tests.support_django import ROOT, SETTINGS, URLS, VIEWS, Base, f, ln, tree
from tests.test_django_views import API, API_URLS, MODELS, ROOT_API, VIEWS_ALL, drf_tree, urls_of


def pairs(chain):
    return [(e.path, e.line) for e in chain]


class Views(Base):
    def test_a_changed_view_body_reaches_its_own_route_only_with_the_chain_from_the_changed_line(self):
        views = VIEWS.replace("    return JsonResponse({})", "    return JsonResponse({1: 1})")
        res = self.changed(tree(), {"shop/views.py": views})
        (r,) = res.reached
        a = r.area
        self.assertEqual((a.label, a.kind, a.module, a.also_mounted, a.priority, a.by_name), ("ANY /shop/items/{item_id}", "api_route", "shop", (), "must_test", False))
        self.assertEqual(pairs(a.chains[0]), [("shop/views.py", ln(views, "{1: 1}")), ("shop/urls.py", ln(URLS, "item_detail")), ("config/urls.py", 4)])
        self.assertEqual(a.route_line.detail.strip(), 'path("items/<int:item_id>/", views.item_detail, name="item-detail"),')
        self.assertEqual(r.routes[0].label, "ANY /shop/items/{item_id}")
        self.assertTrue(r.complete)
        self.assertTrue(res.complete)
        self.assertEqual(self.records, [])

    def test_a_line_identical_to_one_in_a_sibling_view_reaches_only_the_changed_view(self):
        views = VIEWS.replace("    return JsonResponse([], safe=False)", "    return JsonResponse({})")
        files = tree(**{"shop/views.py": views})
        res = self.changed(files, {"shop/views.py": views.replace("def item_detail(request, item_id):\n    return JsonResponse({})", "def item_detail(request, item_id):\n    return JsonResponse({2: 2})")})
        self.assertEqual([r.area.label for r in res.reached], ["ANY /shop/items/{item_id}"])

    def test_a_changed_decorator_line_reaches_the_route_and_the_chain_starts_at_it(self):
        urls = urls_of('path("g/", views.only_get)')
        files = tree(**{"shop/views.py": VIEWS_ALL, "shop/urls.py": urls})
        res = self.changed(files, {"shop/views.py": VIEWS_ALL.replace("@require_GET\n", "@require_GET\n@never_cache\n")})
        self.assertEqual([r.area.label for r in res.reached], ["GET /shop/g"])

    def test_a_new_view_that_no_route_names_reaches_nothing(self):
        res = self.changed(tree(), {"shop/views.py": VIEWS + "\n\ndef unused(request):\n    return None\n"})
        self.assertEqual(res.reached, ())
        self.assertTrue(res.complete)

    def test_a_helper_in_a_view_module_reaches_the_views_that_call_it(self):
        views = VIEWS.replace("from django.http import JsonResponse", "from django.http import JsonResponse\n\n\ndef clean(x):\n    return x").replace("JsonResponse([], safe=False)", "JsonResponse(clean([]), safe=False)")
        res = self.changed(tree(**{"shop/views.py": views}), {"shop/views.py": views.replace("    return x", "    return list(x)")})
        self.assertEqual([r.area.label for r in res.reached], ["ANY /shop/items"])

    def test_a_view_that_calls_a_function_that_did_not_change_is_not_reached(self):
        views = VIEWS.replace("from django.http import JsonResponse", "from django.http import JsonResponse\n\n\ndef clean(x):\n    return x").replace("JsonResponse([], safe=False)", "JsonResponse(clean([]), safe=False)")
        res = self.changed(tree(**{"shop/views.py": views}), {"shop/views.py": views.replace("    return JsonResponse({})", "    return JsonResponse({3: 3})")})
        self.assertEqual([r.area.label for r in res.reached], ["ANY /shop/items/{item_id}"])


class ThroughTheWalk(Base):
    SERVICES = f(["def total(items):", "    return len(items)", "", "", "def other():", "    return 1"])

    def files(self):
        views = VIEWS.replace("from django.http import JsonResponse", "from django.http import JsonResponse\n\nfrom .services import total").replace("JsonResponse([], safe=False)", "JsonResponse(total([]), safe=False)")
        return tree(**{"shop/views.py": views, "shop/services.py": self.SERVICES}), views

    def test_a_changed_service_function_reaches_the_routes_of_the_views_that_call_it(self):
        files, views = self.files()
        res = self.changed(files, {"shop/services.py": self.SERVICES.replace("len(items)", "len(items) + 1")})
        (r,) = res.reached
        self.assertEqual(r.area.label, "ANY /shop/items")
        self.assertEqual(pairs(r.area.chains[0]), [("shop/views.py", ln(views, "from .services")), ("shop/urls.py", ln(URLS, "item_list")), ("config/urls.py", 4)])

    def test_a_service_function_no_view_uses_reaches_no_route(self):
        files, _ = self.files()
        res = self.changed(files, {"shop/services.py": self.SERVICES.replace("return 1", "return 2")})
        self.assertEqual(res.reached, ())

    def test_a_helper_of_a_view_module_that_another_view_module_imports_reaches_the_routes_there(self):
        views = f(["def money(x):", "    return x", "", "", "def a(request):", "    return money(1)", "", "", "def c(request):", "    return 3"])
        other_views = f(["from shop.views import money", "", "", "def b(request):", "    return money(2)"])
        urls = f(["from django.urls import path", "", "from . import views", "from other import views as other_views", "", "urlpatterns = [", '    path("a/", views.a),',
                  '    path("b/", other_views.b),', '    path("c/", views.c),', "]"])
        files = tree(**{"shop/views.py": views, "shop/urls.py": urls, "other/__init__.py": "", "other/views.py": other_views})
        res = self.changed(files, {"shop/views.py": views.replace("return x", "return x + 1")})
        self.assertEqual([r.area.label for r in res.reached], ["ANY /shop/a", "ANY /shop/b"])  # not /shop/c: it does not use money

    def test_a_changed_import_reaches_the_views_that_use_what_it_binds(self):
        views = VIEWS.replace("from django.http import JsonResponse", "from django.http import JsonResponse\nfrom .helpers import clean").replace("JsonResponse([], safe=False)", "JsonResponse(clean([]), safe=False)")
        files = tree(**{"shop/views.py": views, "shop/helpers.py": "", "shop/helpers2.py": ""})
        res = self.changed(files, {"shop/views.py": views.replace("from .helpers import clean", "from .helpers2 import clean")})
        self.assertEqual([r.area.label for r in res.reached], ["ANY /shop/items"])  # only item_list uses `clean`

    def test_a_test_that_imports_the_view_is_a_unit_test_and_not_a_route(self):
        files = tree(**{"shop/tests/__init__.py": "", "shop/tests/test_views.py": "from shop import views\n\n\ndef test_it():\n    views.item_list(None)\n"})
        views = VIEWS.replace("JsonResponse([], safe=False)", "JsonResponse([1], safe=False)")
        res = self.changed(files, {"shop/views.py": views})
        self.assertEqual([r.area.label for r in res.reached], ["ANY /shop/items"])


class RouteLines(Base):
    def test_a_changed_route_line_reaches_that_route_and_the_chain_is_the_route_evidence(self):
        urls = URLS.replace('name="item-list"', 'name="items"')
        res = self.changed(tree(), {"shop/urls.py": urls})
        (r,) = res.reached
        self.assertEqual(r.area.label, "ANY /shop/items")
        self.assertEqual(pairs(r.area.chains[0]), [("shop/urls.py", ln(urls, "item_list")), ("config/urls.py", 4)])
        self.assertEqual(tuple(r.area.chains[0]), r.routes[0].evidence)

    def test_a_route_line_that_spans_several_lines_is_reached_by_a_change_on_any_of_them(self):
        urls = f(["from django.urls import path", "from . import views", "", "urlpatterns = [", "    path(", '        "items/",', "        views.item_list,", '        name="item-list",', "    ),", "]"])
        res = self.changed(tree(**{"shop/urls.py": urls}), {"shop/urls.py": urls.replace('name="item-list"', 'name="items"')})
        (r,) = res.reached
        self.assertEqual(r.area.label, "ANY /shop/items")
        self.assertEqual(r.area.chains[0][0].line, 5)  # cited at the first line of the call

    def test_a_changed_include_line_reaches_every_route_below_it(self):
        res = self.changed(tree(), {"config/urls.py": ROOT.replace('"shop/"', '"store/"')})
        self.assertEqual([r.area.label for r in res.reached], ["ANY /store/items", "ANY /store/items/{item_id}"])
        first = res.reached[0].area.chains[0]
        self.assertEqual(pairs(first), [("shop/urls.py", ln(URLS, "item_list")), ("config/urls.py", 4)])

    def test_an_added_route_is_a_route_and_only_the_added_one(self):
        urls = URLS.replace("]", '    path("new/", views.item_list),\n]')
        res = self.changed(tree(), {"shop/urls.py": urls})
        self.assertEqual([r.area.label for r in res.reached], ["ANY /shop/new"])

    def test_a_removed_route_line_reaches_no_route_and_routes_changed_says_it_is_gone(self):
        urls = URLS.replace('    path("items/", views.item_list, name="item-list"),\n', "")
        res = self.changed(tree(), {"shop/urls.py": urls})
        self.assertEqual(res.reached, ())
        removed, added = self.django.routes_changed(self.sha("main~1"), self.sha("main"))
        self.assertEqual(([r.label for r in removed], added), (["ANY /shop/items"], []))

    def test_a_removed_comment_and_a_removed_unused_import_reach_no_route(self):
        res = self.changed(tree(**{"shop/urls.py": "# the item routes\n" + URLS}), {"shop/urls.py": URLS})
        self.assertEqual(res.reached, ())  # a comment is no code
        res = self.changed(tree(**{"shop/urls.py": "import logging\n\n" + URLS}), {"shop/urls.py": URLS})
        self.assertEqual(res.reached, ())  # `logging` is used by no route

    def test_a_removed_statement_outside_every_route_reaches_every_route_of_the_file(self):
        res = self.changed(tree(**{"shop/urls.py": "register_signals()\n" + URLS}), {"shop/urls.py": URLS})
        self.assertEqual([r.area.label for r in res.reached], ["ANY /shop/items", "ANY /shop/items/{item_id}"])

    def test_a_statement_that_runs_when_the_urlconf_loads_reaches_every_route_of_it(self):
        res = self.changed(tree(), {"shop/urls.py": URLS.replace("urlpatterns = [", "register_signals()\nurlpatterns = [")})
        self.assertEqual([r.area.label for r in res.reached], ["ANY /shop/items", "ANY /shop/items/{item_id}"])

    def test_a_changed_app_name_reaches_every_route_of_the_urlconf(self):
        urls = URLS.replace("from . import views\n", 'from . import views\n\napp_name = "shop"\n')
        res = self.changed(tree(**{"shop/urls.py": urls}), {"shop/urls.py": urls.replace('app_name = "shop"', 'app_name = "store"')})
        self.assertEqual([r.area.label for r in res.reached], ["ANY /shop/items", "ANY /shop/items/{item_id}"])

    def test_a_changed_import_in_a_urlconf_reaches_only_the_routes_that_use_what_it_binds(self):
        urls = "import logging\n" + URLS
        res = self.changed(tree(**{"shop/urls.py": urls}), {"shop/urls.py": urls.replace("import logging", "import json")})
        self.assertEqual(res.reached, ())

    def test_a_changed_pattern_list_variable_reaches_the_routes_built_from_it(self):
        urls = f(["from django.urls import path", "from . import views", "", "extra = [", '    path("e/", views.item_list),', "]", "", "urlpatterns = [", '    path("d/", views.item_detail),', "] + extra"])
        res = self.changed(tree(**{"shop/urls.py": urls}), {"shop/urls.py": urls.replace('path("e/"', 'path("ee/"')})
        self.assertEqual([r.area.label for r in res.reached], ["ANY /shop/ee"])

    def test_a_route_through_two_mounts_of_one_urlconf_is_reached_by_a_change_in_it_twice(self):
        root = f(["from django.urls import include, path", "", "urlpatterns = [", '    path("a/", include("shop.urls")),', '    path("b/", include("shop.urls")),', "]"])
        views = VIEWS.replace("JsonResponse([], safe=False)", "JsonResponse([5], safe=False)")
        res = self.changed(tree(**{"config/urls.py": root}), {"shop/views.py": views})
        self.assertEqual([r.area.label for r in res.reached], ["ANY /a/items", "ANY /b/items"])


class RegexRoutes(Base):
    def test_a_regex_route_that_a_change_reaches_is_partial_and_recorded_once(self):
        urls = f(["from django.urls import re_path", "from . import views", "", "urlpatterns = [", '    re_path(r"^archive/(?P<year>[0-9]{4})/$", views.item_list),', "]"])
        views = VIEWS.replace("JsonResponse([], safe=False)", "JsonResponse([5], safe=False)")
        res = self.changed(tree(**{"shop/urls.py": urls}), {"shop/views.py": views})
        self.assertEqual([r.area.label for r in res.reached], ["ANY /shop/archive/{year}"])
        self.assertEqual(self.kinds_of_records(), [("route_path_partial", "shop/urls.py:5", False)])


class Unmounted(Base):
    LEGACY = f(["from django.urls import path", "", "from shop import legacy_views", "", "urlpatterns = [", '    path("old/", legacy_views.export),', "]"])
    LEGACY_VIEWS = f(["def export(request):", "    return None"])

    def files(self):
        return tree(**{"shop/legacy_urls.py": self.LEGACY, "shop/legacy_views.py": self.LEGACY_VIEWS})

    def test_a_view_of_a_urlconf_nothing_includes_claims_no_route_and_says_which_urlconf(self):
        res = self.changed(self.files(), {"shop/legacy_views.py": self.LEGACY_VIEWS.replace("None", "1")})
        self.assertEqual(res.reached, ())
        self.assertEqual(self.kinds_of_records(), [("urlconf_unmounted", "shop/legacy_urls.py:urlpatterns", False)])
        self.assertEqual(self.route_labels(), ["ANY /shop/items", "ANY /shop/items/{item_id}"])
        self.assertTrue(res.complete)

    def test_the_unmounted_urlconf_is_not_recorded_for_a_change_that_does_not_reach_it(self):
        res = self.changed(self.files(), {"shop/views.py": VIEWS.replace("JsonResponse([], safe=False)", "JsonResponse([5], safe=False)")})
        self.assertEqual([r.area.label for r in res.reached], ["ANY /shop/items"])
        self.assertEqual(self.records, [])

    def test_a_commented_out_include_does_not_mount_the_urlconf(self):
        root = f(["from django.urls import include, path", "", "urlpatterns = [", '    path("shop/", include("shop.urls")),', '    # path("legacy/", include("shop.legacy_urls")),', "]"])
        files = {**self.files(), "config/urls.py": root}
        res = self.changed(files, {"shop/legacy_views.py": self.LEGACY_VIEWS.replace("None", "1")})
        self.assertEqual(res.reached, ())
        self.assertEqual([k for k, _s, _b in self.kinds_of_records()], ["urlconf_unmounted"])

    def test_a_urlconf_that_might_be_the_one_an_unresolved_include_carries_is_not_called_unmounted(self):
        root = f(["from django.urls import include, path", "from config import factory", "", "urlpatterns = [", '    path("shop/", include("shop.urls")),', "    path('x/', include(factory.make())),", "]"])
        files = {**self.files(), "config/urls.py": root, "config/factory.py": "def make():\n    return []\n"}
        res = self.changed(files, {"shop/legacy_views.py": self.LEGACY_VIEWS.replace("None", "1")})
        self.assertEqual(res.reached, ())
        self.assertEqual(self.kinds_of_records(), [("mount_unresolved", "config/urls.py:6", True)])
        self.assertFalse(res.complete)


class RootUrlconfChanges(Base):
    def test_a_changed_root_urlconf_reaches_every_route_of_the_new_root(self):
        res = self.changed(tree(), {"config/settings.py": SETTINGS.replace("config.urls", "config.urls_v2"), "config/urls_v2.py": ROOT})
        self.assertEqual([r.area.label for r in res.reached], ["ANY /shop/items", "ANY /shop/items/{item_id}"])
        first = res.reached[0].area.chains[0]
        self.assertIn(("config/settings.py", 2), pairs(first))

    def test_another_setting_is_not_followed_and_says_so_with_a_blocking_record(self):
        res = self.changed(tree(), {"config/settings.py": SETTINGS.replace("True", "False")})
        self.assertEqual(res.reached, ())
        self.assertEqual(self.kinds_of_records(), [("settings_not_followed", "config/settings.py", True)])
        self.assertFalse(res.complete)

    def test_a_root_urlconf_that_becomes_unreadable_blocks_the_whole_answer(self):
        settings = 'import os\nROOT_URLCONF = os.environ["ROOT"]\n'
        res = self.changed(tree(), {"config/settings.py": settings, "shop/views.py": VIEWS.replace("JsonResponse([], safe=False)", "JsonResponse([5], safe=False)")})
        self.assertEqual(res.reached, ())
        self.assertIn(("urlconf_root_unknown", "acme/portal", True), self.kinds_of_records())
        self.assertFalse(res.complete)


class Completeness(Base):
    def test_a_urlconf_that_does_not_parse_makes_the_routes_found_incomplete(self):
        broken = "from django.urls import path\nurlpatterns = [\n    path('x/', views.f,\n"
        views = VIEWS.replace("JsonResponse([], safe=False)", "JsonResponse([5], safe=False)")
        res = self.changed(tree(**{"shop/broken_urls.py": broken}), {"shop/views.py": views})
        self.assertEqual([r.area.label for r in res.reached], ["ANY /shop/items"])
        self.assertFalse(res.reached[0].complete)
        self.assertFalse(res.complete)
        self.assertIn(("parse_degraded", "shop/broken_urls.py", True), self.kinds_of_records())

    def test_a_changed_urlconf_that_does_not_parse_reaches_nothing_and_is_not_complete(self):
        res = self.changed(tree(), {"shop/urls.py": "urlpatterns = [\n    path('x/', views.f,\n"})
        self.assertEqual(res.reached, ())
        self.assertFalse(res.complete)
        self.assertIn("parse_degraded", {k for k, _s, _b in self.kinds_of_records()})

    def test_a_search_that_was_cut_makes_the_result_incomplete_not_empty(self):
        self.repo({"files": tree()}, {"files": {"shop/views.py": VIEWS.replace("JsonResponse([], safe=False)", "JsonResponse([5], safe=False)")}})
        self.entry(examine_cap=1)
        res = self.reach()
        self.assertFalse(res.complete)
        self.assertTrue(any("search" in why for why in res.reasons), res.reasons)
        self.assertIn("search_truncated", {r.kind for r in self.records})

    def test_a_whole_search_gives_a_complete_result(self):
        res = self.changed(tree(), {"shop/views.py": VIEWS.replace("JsonResponse([], safe=False)", "JsonResponse([5], safe=False)")})
        self.assertEqual(([r.area.label for r in res.reached], res.complete, res.reasons), (["ANY /shop/items"], True, ()))

    def test_the_importers_of_a_file_that_cannot_be_searched_make_the_walk_incomplete(self):
        from unittest import mock

        from reachmap.adapters import ImporterResult

        views = VIEWS.replace("from django.http import JsonResponse", "from django.http import JsonResponse\n\nfrom .services import total").replace("JsonResponse([], safe=False)", "JsonResponse(total([]), safe=False)")
        services = "from .util import one\n\n\ndef total(x):\n    return one()\n"
        util = "def one():\n    return 1\n"
        self.repo({"files": tree(**{"shop/views.py": views, "shop/services.py": services, "shop/util.py": util})}, {"files": {"shop/util.py": util.replace("1", "2")}})
        self.entry()
        real = self.language.importers

        def cut(path, sha, record_to=None):
            res = real(path, sha, record_to)
            return ImporterResult(res.target, res.sha, res.edges, False, ("simulated cut",), res.candidates)

        with mock.patch.object(self.language, "importers", cut):
            res = self.reach()
        self.assertEqual([r.area.label for r in res.reached], ["ANY /shop/items"])
        self.assertFalse(res.complete)
        self.assertTrue(any("simulated cut" in why for why in res.reasons))
        self.assertFalse(res.reached[0].complete)


class GenericNames(Base):
    def test_a_changed_view_called_index_reaches_only_the_route_that_imports_it(self):
        shop = f(["def index(request):", "    return None"])
        other = f(["def index(request):", "    return None"])
        shop_urls = f(["from django.urls import path", "from . import views", "", "urlpatterns = [path('', views.index)]"])
        other_urls = f(["from django.urls import path", "from . import views", "", "urlpatterns = [path('', views.index)]"])
        root = f(["from django.urls import include, path", "", "urlpatterns = [", '    path("shop/", include("shop.urls")),', '    path("other/", include("other.urls")),', "]"])
        files = tree(**{"shop/views.py": shop, "shop/urls.py": shop_urls, "other/__init__.py": "", "other/views.py": other, "other/urls.py": other_urls, "config/urls.py": root})
        res = self.changed(files, {"shop/views.py": shop.replace("None", "1")})
        self.assertEqual([r.area.label for r in res.reached], ["ANY /shop"])

    def test_a_name_defined_more_often_than_the_limit_is_recorded_and_links_nothing(self):
        def view():
            return f(["def index(request):", "    return None"])

        urls = f(["from django.urls import path", "from . import views", "", "urlpatterns = [path('', views.index)]"])
        files = tree(**{"shop/views.py": view(), "shop/urls.py": urls, "a/__init__.py": "", "a/views.py": view(), "b/__init__.py": "", "b/views.py": view(), "c/__init__.py": "", "c/views.py": view()})
        self.repo({"files": files}, {"files": {"shop/views.py": view().replace("None", "1")}})
        self.entry(distinct_max=3)
        res = self.reach()
        self.assertEqual([r.area.label for r in res.reached], ["ANY /shop"])
        self.assertEqual(self.kinds_of_records(), [("not_distinctive", "index", False)])


class ClassViews(Base):
    VIEWS = f([
        "from django.views import View",
        "",
        "",
        "class Hello(View):",
        '    greeting = "hi"',
        "",
        "    def get(self, request):",
        "        return self.greeting",
        "",
        "    def post(self, request):",
        "        return self._save(request)",
        "",
        "    def _save(self, request):",
        "        return 1",
        "",
        "    def get_context_data(self):",
        "        return {}",
    ])

    def run_case(self, new):
        urls = urls_of('path("h/", views.Hello.as_view())')
        return self.changed(tree(**{"shop/views.py": self.VIEWS, "shop/urls.py": urls}), {"shop/views.py": new})

    def labels(self, res):
        return [r.area.label for r in res.reached]

    def test_a_changed_method_reaches_the_route_of_that_method_only(self):
        self.assertEqual(self.labels(self.run_case(self.VIEWS.replace("return self.greeting", "return self.greeting + '!'"))), ["GET /shop/h", "HEAD /shop/h"])  # Django answers HEAD with get
        self.assertEqual(self.labels(self.run_case(self.VIEWS.replace("self._save(request)", "self._save(request) + 1"))), ["POST /shop/h"])

    def test_a_private_helper_reaches_the_methods_that_call_it(self):
        self.assertEqual(self.labels(self.run_case(self.VIEWS.replace("        return 1", "        return 2"))), ["POST /shop/h"])

    def test_a_public_helper_or_a_class_attribute_reaches_every_route_of_the_class(self):
        every = ["GET /shop/h", "HEAD /shop/h", "OPTIONS /shop/h", "POST /shop/h"]
        self.assertEqual(self.labels(self.run_case(self.VIEWS.replace("return {}", "return {1: 1}"))), every)
        self.assertEqual(self.labels(self.run_case(self.VIEWS.replace('"hi"', '"hello"'))), every)

    def test_a_changed_method_of_a_base_class_reaches_the_route_of_that_method_in_the_classes_that_inherit_it(self):
        views = f(["from django.views import View", "", "", "class Base(View):", "    flag = 1", "", "    def get(self, request):", "        return 1", "", "", "class Child(Base):", "    def post(self, request):", "        return 2"])
        urls = urls_of('path("c/", views.Child.as_view())')
        res = self.changed(tree(**{"shop/views.py": views, "shop/urls.py": urls}), {"shop/views.py": views.replace("return 1", "return 3")})
        self.assertEqual(self.labels(res), ["GET /shop/c", "HEAD /shop/c"])
        res = self.changed(tree(**{"shop/views.py": views, "shop/urls.py": urls}), {"shop/views.py": views.replace("flag = 1", "flag = 2")})
        self.assertEqual(self.labels(res), ["GET /shop/c", "HEAD /shop/c", "OPTIONS /shop/c", "POST /shop/c"])  # a class attribute of the base is read by every method

    def test_a_framework_base_with_an_unknown_parent_records_that_the_methods_are_not_known_when_reached(self):
        views = f(["from thirdparty.views import SpecialView", "", "", "class Mine(SpecialView):", "    def helper(self):", "        return 1"])
        urls = urls_of('path("m/", views.Mine.as_view())')
        res = self.changed(tree(**{"shop/views.py": views, "shop/urls.py": urls}), {"shop/views.py": views.replace("return 1", "return 2")})
        self.assertEqual(self.labels(res), ["ANY /shop/m"])
        self.assertEqual(self.kinds_of_records(), [("route_methods_unknown", "shop/views.py:Mine", False)])


class ViewSets(Base):
    def run_case(self, files=None, changes=None):
        files = files or drf_tree()
        return self.changed(files, changes)

    def labels(self, res):
        return [r.area.label for r in res.reached]

    def test_a_changed_action_reaches_the_route_of_that_action_only(self):
        res = self.run_case(changes={"shop/api.py": API.replace("def archive(self, request, pk=None):\n        return None", "def archive(self, request, pk=None):\n        return 1")})
        self.assertEqual(self.labels(res), ["POST /api/items/{pk}/archive"])
        chain = res.reached[0].area.chains[0]
        self.assertEqual(pairs(chain)[0], ("shop/api.py", ln(API, "def archive") + 1))
        self.assertEqual([p[0] for p in pairs(chain)], ["shop/api.py", "shop/api.py", "shop/api_urls.py", "shop/api_urls.py", "config/urls.py"])

    def test_a_changed_action_decorator_reaches_the_routes_of_that_action(self):
        res = self.run_case(changes={"shop/api.py": API.replace('@action(detail=False)', '@action(detail=False, url_path="latest")')})
        self.assertEqual(self.labels(res), ["GET /api/items/latest"])  # the new path of the changed decorator; the old route is removed

    def test_a_class_attribute_of_a_viewset_reaches_every_route_of_it_and_no_other_viewset(self):
        res = self.run_case(changes={"shop/api.py": API.replace("queryset = Item.objects.all()", "queryset = Item.objects.none()")})
        self.assertEqual(sorted(self.labels(res)), sorted([
            "DELETE /api/items/{pk}", "GET /api/items", "GET /api/items/bulk-load", "GET /api/items/recent", "GET /api/items/{pk}", "PATCH /api/items/{pk}",
            "POST /api/items", "POST /api/items/bulk-load", "POST /api/items/{pk}/archive", "PUT /api/items/{pk}"]))

    def test_a_serializer_that_the_viewset_names_reaches_every_route_of_the_viewset(self):
        api = API.replace("queryset = Item.objects.all()", "queryset = Item.objects.all()\n    serializer_class = ItemSerializer").replace("from .models import Item", "from .models import Item\nfrom .serializers import ItemSerializer")
        ser = "class ItemSerializer:\n    fields = ['id']\n"
        res = self.run_case(drf_tree(api=api, **{"shop/serializers.py": ser}), {"shop/serializers.py": ser.replace("'id'", "'id', 'name'")})
        self.assertEqual(len(res.reached), 10)
        self.assertNotIn("GET /api", self.labels(res))

    def test_a_helper_that_only_one_action_uses_reaches_that_action(self):
        api = API.replace("from .models import Item", "from .models import Item\nfrom .helpers import latest").replace("def recent(self, request):\n        return None", "def recent(self, request):\n        return latest()")
        helpers = "def latest():\n    return []\n"
        res = self.run_case(drf_tree(api=api, **{"shop/helpers.py": helpers}), {"shop/helpers.py": helpers.replace("[]", "[1]")})
        self.assertEqual(self.labels(res), ["GET /api/items/recent"])

    def test_a_changed_register_line_reaches_the_routes_of_that_registration_only(self):
        urls = API_URLS.replace('router.register(r"items", api.ItemViewSet, basename="item")', 'router.register(r"items", api.ItemViewSet, basename="item")\nrouter.register(r"things", api.ReadOnlyThing)')
        res = self.run_case(drf_tree(urls), {"shop/api_urls.py": urls.replace('r"things"', 'r"stuff"')})
        self.assertEqual(self.labels(res), ["GET /api/stuff", "GET /api/stuff/{pk}"])

    def test_a_changed_router_constructor_reaches_every_route_on_the_router(self):
        res = self.run_case(changes={"shop/api_urls.py": API_URLS.replace("routers.DefaultRouter()", "routers.DefaultRouter(trailing_slash=False)")})
        self.assertEqual(len(res.reached), 11)
        self.assertIn("GET /api", self.labels(res))

    def test_a_changed_include_of_the_router_reaches_every_route_under_it(self):
        res = self.run_case(changes={"shop/api_urls.py": API_URLS.replace('path("", include(router.urls))', 'path("v2/", include(router.urls))')})
        self.assertEqual(len(res.reached), 11)

    def test_a_viewset_no_router_registers_reaches_nothing(self):
        res = self.run_case(drf_tree(), {"shop/api.py": API.replace("class ReadOnlyThing(viewsets.ReadOnlyModelViewSet):\n    pass", "class ReadOnlyThing(viewsets.ReadOnlyModelViewSet):\n    queryset = None")})
        self.assertEqual(res.reached, ())  # ReadOnlyThing is registered by no router in this tree
        self.assertEqual(self.records, [])

    def test_the_viewset_routes_of_an_unmounted_router_claim_nothing(self):
        urls = API_URLS.replace('path("", include(router.urls))', 'path("x/", api.ReadOnlyThing.as_view({"get": "list"}))')
        res = self.run_case(drf_tree(urls), {"shop/api.py": API.replace("def recent(self, request):\n        return None", "def recent(self, request):\n        return 7")})
        self.assertEqual(res.reached, ())
        self.assertEqual(self.kinds_of_records(), [("urlconf_unmounted", "shop/api_urls.py:router", False)])


class EntryFiles(Base):
    def test_the_entry_files_are_the_urlconfs_the_files_with_views_that_routes_name_and_the_settings(self):
        self.repo({"files": tree(**{"shop/other.py": "x = 1\n"})})
        entry = self.entry()
        sha = self.sha("main")
        self.assertEqual({p: entry.is_entry_file(p, sha) for p in ("config/urls.py", "shop/urls.py", "shop/views.py", "config/settings.py", "shop/other.py", "shop/__init__.py")}, {
            "config/urls.py": True, "shop/urls.py": True, "shop/views.py": True, "config/settings.py": True, "shop/other.py": False, "shop/__init__.py": False,
        })

    def test_an_admin_registration_is_not_a_router_and_its_file_is_not_an_entry_file(self):
        admin = "from django.contrib import admin\n\nfrom .models import Item\n\nadmin.site.register(Item)\n"
        self.repo({"files": tree(**{"shop/admin.py": admin, "shop/models.py": "class Item:\n    pass\n"})})
        entry = self.entry()
        self.assertFalse(entry.is_entry_file("shop/admin.py", self.sha("main")))
        self.assertEqual(self.route_labels(), ["ANY /shop/items", "ANY /shop/items/{item_id}"])
        self.assertEqual(self.records, [])

    def test_the_names_that_only_wire_a_file_up_do_not_carry_a_change_past_it(self):
        self.repo({"files": tree(**{"shop/views.py": VIEWS + "\n\ndef helper():\n    return 1\n"})})
        entry = self.entry()
        sha = self.sha("main")
        self.assertEqual(entry.structural_names("shop/views.py", sha), frozenset({"item_list", "item_detail"}))
        self.assertEqual(entry.structural_names("shop/urls.py", sha), frozenset({"urlpatterns", "app_name"}))


if __name__ == "__main__":
    unittest.main()
