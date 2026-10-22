"""The views behind the routes: functions (and the decorators that restrict their methods), `as_view()` classes, wrappers in the
URLconf, classes whose methods come from a base class, and the Django REST Framework: `DefaultRouter().register(prefix, ViewSet)`
expanded to the list and detail routes with the methods the viewset has, `@action` extras, lookup fields, routers in other
files, and what cannot be read (a viewset that does not resolve, a router that is not a known one).

The route table composition is in test_django_routes.py, what a change reaches in test_django_reach.py.
"""

import unittest

from tests.support_django import ROOT, SETTINGS, URLS, VIEWS, Base, f, ln, tree

VIEWS_ALL = f([
    "from django.views import View",
    "from django.views.decorators.http import require_GET, require_POST, require_http_methods, require_safe",
    "from rest_framework.decorators import api_view",
    "",
    "",
    "def plain(request):",
    "    return None",
    "",
    "",
    "@require_GET",
    "def only_get(request):",
    "    return None",
    "",
    "",
    '@require_http_methods(["GET", "PUT"])',
    "def get_put(request):",
    "    return None",
    "",
    "",
    "@require_POST",
    "def only_post(request):",
    "    return None",
    "",
    "",
    "@require_safe",
    "def safe_only(request):",
    "    return None",
    "",
    "",
    '@api_view(["GET", "POST"])',
    "def drf_both(request):",
    "    return None",
    "",
    "",
    "@api_view()",
    "def drf_default(request):",
    "    return None",
    "",
    "",
    "class Hello(View):",
    "    def get(self, request):",
    "        return None",
    "",
    "    def post(self, request):",
    "        return None",
    "",
    "",
    "class Parent(View):",
    "    def get(self, request):",
    "        return None",
    "",
    "",
    "class Child(Parent):",
    "    def delete(self, request):",
    "        return None",
])


def urls_of(*entries, header=()):
    return f(["from django.urls import path", "from . import views", *header, "", "urlpatterns = [", *[f"    {e}," for e in entries], "]"])


class FunctionViews(Base):
    def labels_of(self, *entries, header=(), views=VIEWS_ALL, **files):
        self.repo({"files": tree(**{"shop/urls.py": urls_of(*entries, header=header), "shop/views.py": views, **files})})
        return self.labels()

    def test_a_function_view_with_no_restriction_answers_any_method(self):
        self.assertEqual(self.labels_of('path("a/", views.plain)'), [("ANY /shop/a", "plain")])

    def test_the_decorators_that_restrict_the_methods_give_one_route_per_method(self):
        got = self.labels_of(
            'path("g/", views.only_get)', 'path("gp/", views.get_put)', 'path("p/", views.only_post)', 'path("s/", views.safe_only)',
            'path("d/", views.drf_both)', 'path("e/", views.drf_default)',
        )
        self.assertEqual(got, [
            ("GET /shop/d", "drf_both"), ("GET /shop/e", "drf_default"), ("GET /shop/g", "only_get"), ("GET /shop/gp", "get_put"),
            ("GET /shop/s", "safe_only"), ("HEAD /shop/s", "safe_only"), ("POST /shop/d", "drf_both"), ("POST /shop/p", "only_post"),
            ("PUT /shop/gp", "get_put"),
        ])

    def test_wrappers_in_the_urlconf_are_looked_through_and_the_decorators_of_the_view_stay(self):
        got = self.labels_of(
            'path("a/", login_required(views.plain))', 'path("b/", csrf_exempt(login_required(views.plain)))', 'path("c/", require_POST(views.plain))',
            'path("d/", require_http_methods(["DELETE", "PATCH"])(views.plain))', 'path("e/", permission_required("x.y")(views.only_get))',
            header=["from django.contrib.auth.decorators import login_required, permission_required",
                    "from django.views.decorators.csrf import csrf_exempt",
                    "from django.views.decorators.http import require_POST, require_http_methods"],
        )
        self.assertEqual(got, [
            ("ANY /shop/a", "plain"), ("ANY /shop/b", "plain"), ("DELETE /shop/d", "plain"), ("GET /shop/e", "only_get"),
            ("PATCH /shop/d", "plain"), ("POST /shop/c", "plain"),
        ])

    def test_a_class_view_gives_a_route_per_method_it_defines_and_inherits_from_a_base_of_the_repository(self):
        got = self.labels_of('path("h/", views.Hello.as_view())', 'path("c/", views.Child.as_view())')
        self.assertEqual(got, [
            ("DELETE /shop/c", "Child.delete"), ("GET /shop/c", "Parent.get"), ("GET /shop/h", "Hello.get"), ("HEAD /shop/c", "Parent.get"), ("HEAD /shop/h", "Hello.get"),
            ("OPTIONS /shop/c", "Child"), ("OPTIONS /shop/h", "Hello"), ("POST /shop/h", "Hello.post"),
        ])

    def test_the_target_of_a_class_view_route_is_the_file_that_defines_the_method_class(self):
        self.repo({"files": tree(**{"shop/urls.py": urls_of('path("c/", views.Child.as_view())'), "shop/views.py": VIEWS_ALL})})
        self.entry()
        self.assertEqual({r.target for r in self.django.routes(None, self.sha("main"))}, {"shop/views.py"})

    def test_as_view_with_arguments_and_a_view_imported_under_another_name_or_by_star(self):
        got = self.labels_of(
            'path("h/", Hi.as_view(template_name="x.html"))', 'path("f/", pl)', 'path("star/", views2.plain)',
            header=["from .views import Hello as Hi, plain as pl", "from . import views as views2"],
        )
        self.assertEqual(got, [("ANY /shop/f", "plain"), ("ANY /shop/star", "plain"), ("GET /shop/h", "Hello.get"), ("HEAD /shop/h", "Hello.get"), ("OPTIONS /shop/h", "Hello"),
                               ("POST /shop/h", "Hello.post")])

    def test_a_view_imported_by_star_is_found_in_the_module_it_comes_from(self):
        got = self.labels_of('path("a/", plain)', header=["from .views import *"])
        self.assertEqual(got, [("ANY /shop/a", "plain")])

    def test_a_view_re_exported_by_a_package_is_followed_to_the_file_that_defines_it(self):
        views_init = "from .impl import plain\n"
        urls = urls_of('path("a/", views.plain)')
        self.repo({"files": tree(**{"shop/urls.py": urls, "shop/views.py": None, "shop/views/__init__.py": views_init, "shop/views/impl.py": VIEWS_ALL})})
        self.entry()
        (r,) = [r for r in self.django.routes(None, self.sha("main"))]
        self.assertEqual((r.label, r.target), ("ANY /shop/a", "shop/views/impl.py"))

    def test_a_lambda_is_a_route_whose_handler_is_in_the_urlconf(self):
        self.repo({"files": tree(**{"shop/urls.py": urls_of('path("a/", lambda request: None)')})})
        self.entry()
        (r,) = self.django.routes(None, self.sha("main"))
        self.assertEqual((r.label, r.handler, r.target), ("ANY /shop/a", "<lambda>", "shop/urls.py"))


class FrameworkViews(Base):
    def labels_of(self, *entries, header=()):
        self.repo({"files": tree(**{"shop/urls.py": urls_of(*entries, header=header)})})
        return self.labels()

    def test_the_generic_views_of_django_have_the_methods_their_class_has(self):
        got = self.labels_of(
            'path("t/", TemplateView.as_view(template_name="t.html"))', 'path("l/", ListView.as_view(model=Thing))', 'path("r/", RedirectView.as_view(url="/x"))',
            'path("c/", CreateView.as_view(model=Thing))', 'path("d/", DeleteView.as_view(model=Thing))',
            header=["from django.views.generic import CreateView, DeleteView, ListView, RedirectView, TemplateView", "from .models import Thing"],
        )
        self.assertEqual(got, [
            ("ANY /shop/r", "RedirectView"), ("DELETE /shop/d", "DeleteView"), ("GET /shop/c", "CreateView"), ("GET /shop/d", "DeleteView"),
            ("GET /shop/l", "ListView"), ("GET /shop/t", "TemplateView"), ("POST /shop/c", "CreateView"), ("POST /shop/d", "DeleteView"),
            ("PUT /shop/c", "CreateView"),
        ])

    def test_a_framework_view_has_its_route_in_the_urlconf_and_no_handler_file_of_the_repository(self):
        self.repo({"files": tree(**{"shop/urls.py": urls_of("path('t/', TemplateView.as_view(template_name='t.html'))", header=["from django.views.generic import TemplateView"])})})
        self.entry()
        (r,) = self.django.routes(None, self.sha("main"))
        self.assertEqual((r.target, r.file), ("shop/urls.py", "shop/urls.py"))

    def test_the_generic_api_views_of_drf_have_the_methods_their_class_has(self):
        views = f([
            "from rest_framework import generics, views", "",
            "class Items(generics.ListCreateAPIView):", "    pass", "",
            "class One(generics.RetrieveUpdateDestroyAPIView):", "    pass", "",
            "class Custom(views.APIView):", "    def get(self, request):", "        return None", "",
            "class OnlyList(generics.ListAPIView):", "    pass",
        ])
        self.repo({"files": tree(**{"shop/views.py": views, "shop/urls.py": urls_of(
            'path("i/", views.Items.as_view())', 'path("o/", views.One.as_view())', 'path("c/", views.Custom.as_view())', 'path("l/", views.OnlyList.as_view())')})})
        self.assertEqual(self.labels(), [
            ("DELETE /shop/o", "One"), ("GET /shop/c", "Custom.get"), ("GET /shop/i", "Items"), ("GET /shop/l", "OnlyList"), ("GET /shop/o", "One"),
            ("HEAD /shop/c", "Custom.get"), ("HEAD /shop/i", "Items"), ("HEAD /shop/l", "OnlyList"), ("HEAD /shop/o", "One"),
            ("OPTIONS /shop/c", "Custom"), ("OPTIONS /shop/i", "Items"), ("OPTIONS /shop/l", "OnlyList"), ("OPTIONS /shop/o", "One"),
            ("PATCH /shop/o", "One"), ("POST /shop/i", "Items"), ("PUT /shop/o", "One"),
        ])

    def test_a_class_with_a_base_outside_the_repository_that_is_not_known_answers_any_method(self):
        views = f(["from thirdparty.views import SpecialView", "", "class Mine(SpecialView):", "    pass"])
        self.repo({"files": tree(**{"shop/views.py": views, "shop/urls.py": urls_of('path("m/", views.Mine.as_view())')})})
        self.assertEqual(self.labels(), [("ANY /shop/m", "Mine")])

    def test_a_mixin_that_is_not_a_known_view_adds_no_method(self):
        views = f(["from django.contrib.auth.mixins import LoginRequiredMixin", "from django.views import View", "",
                   "class Mine(LoginRequiredMixin, View):", "    def get(self, request):", "        return None"])
        self.repo({"files": tree(**{"shop/views.py": views, "shop/urls.py": urls_of('path("m/", views.Mine.as_view())')})})
        self.assertEqual(self.labels(), [("GET /shop/m", "Mine.get"), ("HEAD /shop/m", "Mine.get"), ("OPTIONS /shop/m", "Mine")])

    def test_the_methods_of_a_viewset_as_view_come_from_its_mapping(self):
        views = f(["from rest_framework import viewsets", "", "class Thing(viewsets.ViewSet):", "    def list(self, request):", "        return None", "",
                   "    def create(self, request):", "        return None"])
        self.repo({"files": tree(**{"shop/views.py": views, "shop/urls.py": urls_of(
            'path("t/", views.Thing.as_view({"get": "list", "post": "create"}))', 'path("u/", views.Thing.as_view({"get": "list"}))')})})
        self.assertEqual(self.labels(), [("GET /shop/t", "Thing.list"), ("GET /shop/u", "Thing.list"), ("POST /shop/t", "Thing.create")])

    def test_the_admin_and_other_framework_includes_that_are_not_views_are_not_routes(self):
        got = self.labels_of("path('admin/', admin.site.urls)", header=["from django.contrib import admin"])
        self.assertEqual(got, [])
        self.assertEqual(self.records, [])


class GenericNames(Base):
    """Views are tied to routes by what the import says, never by a name that happens to match."""

    def test_a_view_that_does_not_resolve_is_not_matched_to_a_view_of_the_same_name_elsewhere(self):
        urls = urls_of('path("a/", registry.lookup("index"))', 'path("b/", views.index)', header=["from . import registry"])
        views = f(["def index(request):", "    return None"])
        self.repo({"files": tree(**{"shop/urls.py": urls, "shop/views.py": views, "shop/registry.py": "def lookup(name):\n    return None\n", "shop/other.py": views})})
        self.entry()
        self.assertEqual([(r.label, r.target) for r in self.django.routes(None, self.sha("main"))], [("ANY /shop/b", "shop/views.py")])
        self.assertEqual(self.kinds_of_records(), [("view_unresolved", "shop/urls.py:" + str(ln(urls, 'registry.lookup')), True)])
        self.assertTrue(self.django.problems)

    def test_two_apps_with_a_view_of_the_same_name_keep_their_own_routes(self):
        shop = f(["def index(request):", "    return None"])
        other = f(["def index(request):", "    return None"])
        shop_urls = f(["from django.urls import path", "from . import views", "", "urlpatterns = [path('', views.index)]"])
        other_urls = f(["from django.urls import path", "from . import views", "", "urlpatterns = [path('', views.index)]"])
        root = f(["from django.urls import include, path", "", "urlpatterns = [", '    path("shop/", include("shop.urls")),', '    path("other/", include("other.urls")),', "]"])
        self.repo({"files": tree(**{"shop/views.py": shop, "shop/urls.py": shop_urls, "other/__init__.py": "", "other/views.py": other, "other/urls.py": other_urls, "config/urls.py": root})})
        self.entry()
        got = sorted((r.label, r.target) for r in self.django.routes(None, self.sha("main")))
        self.assertEqual(got, [("ANY /other", "other/views.py"), ("ANY /shop", "shop/views.py")])


# ------------------------------------------------------------------------------ Django REST Framework

API = f([
    "from rest_framework import mixins, viewsets",
    "from rest_framework.decorators import action",
    "",
    "from .models import Item",
    "",
    "",
    "class ItemViewSet(viewsets.ModelViewSet):",
    "    queryset = Item.objects.all()",
    "",
    '    @action(detail=True, methods=["post"])',
    "    def archive(self, request, pk=None):",
    "        return None",
    "",
    "    @action(detail=False)",
    "    def recent(self, request):",
    "        return None",
    "",
    "    @action(",
    "        detail=False,",
    '        methods=["get", "post"],',
    '        url_path="bulk-load",',
    '        url_name="bulk",',
    "    )",
    "    def bulk(self, request):",
    "        return None",
    "",
    "",
    "class ReadOnlyThing(viewsets.ReadOnlyModelViewSet):",
    "    pass",
    "",
    "",
    "class ListOnly(mixins.ListModelMixin, viewsets.GenericViewSet):",
    "    pass",
    "",
    "",
    "class Plain(viewsets.ViewSet):",
    "    def list(self, request):",
    "        return None",
    "",
    "    def retrieve(self, request, pk=None):",
    "        return None",
    "",
    "",
    "class SlugThing(viewsets.ModelViewSet):",
    '    lookup_field = "slug"',
    "",
    "",
    "class KwargThing(viewsets.ReadOnlyModelViewSet):",
    '    lookup_url_kwarg = "ident"',
])
MODELS = "class Item:\n    pass\n"
API_URLS = f([
    "from django.urls import include, path",
    "from rest_framework import routers",
    "",
    "from . import api",
    "",
    "router = routers.DefaultRouter()",
    'router.register(r"items", api.ItemViewSet, basename="item")',
    "",
    "urlpatterns = [",
    '    path("", include(router.urls)),',
    "]",
])
ROOT_API = f(["from django.urls import include, path", "", "urlpatterns = [", '    path("api/", include("shop.api_urls")),', "]"])


def drf_tree(api_urls=API_URLS, api=API, **more):
    return tree(**{"config/urls.py": ROOT_API, "shop/api.py": api, "shop/api_urls.py": api_urls, "shop/models.py": MODELS, **more})


class Routers(Base):
    def labels_of(self, api_urls=API_URLS, api=API, **more):
        self.repo({"files": drf_tree(api_urls, api, **more)})
        return self.labels()

    def test_a_model_viewset_gives_the_list_and_detail_routes_and_the_actions(self):
        got = self.labels_of()
        self.assertEqual(got, [
            ("DELETE /api/items/{pk}", "ItemViewSet.destroy"),
            ("GET /api", "APIRootView"),
            ("GET /api/items", "ItemViewSet.list"),
            ("GET /api/items/bulk-load", "ItemViewSet.bulk"),
            ("GET /api/items/recent", "ItemViewSet.recent"),
            ("GET /api/items/{pk}", "ItemViewSet.retrieve"),
            ("PATCH /api/items/{pk}", "ItemViewSet.partial_update"),
            ("POST /api/items", "ItemViewSet.create"),
            ("POST /api/items/bulk-load", "ItemViewSet.bulk"),
            ("POST /api/items/{pk}/archive", "ItemViewSet.archive"),
            ("PUT /api/items/{pk}", "ItemViewSet.update"),
        ])

    def test_the_evidence_of_a_router_route_is_the_register_line_then_the_includes_from_the_router_outwards(self):
        self.repo({"files": drf_tree()})
        self.entry()
        r = self.route("GET /api/items/{pk}")
        self.assertEqual((r.target, r.file, r.handler, r.partial), ("shop/api.py", "shop/api_urls.py", "ItemViewSet.retrieve", False))
        self.assertEqual([(e.path, e.line) for e in r.evidence], [
            ("shop/api_urls.py", ln(API_URLS, "router.register")), ("shop/api_urls.py", ln(API_URLS, "include(router.urls)")), ("config/urls.py", ln(ROOT_API, "api/")),
        ])
        self.assertEqual(r.evidence[0].claim, "route:GET /api/items/{pk}:shop/api.py")

    def test_the_evidence_of_an_action_route_starts_at_its_decorator_and_a_multi_line_decorator_is_cited_at_its_first_line(self):
        self.repo({"files": drf_tree()})
        self.entry()
        archive = self.route("POST /api/items/{pk}/archive")
        self.assertEqual([(e.path, e.line) for e in archive.evidence][:2], [("shop/api.py", ln(API, "@action(detail=True")), ("shop/api_urls.py", ln(API_URLS, "router.register"))])
        bulk = self.route("POST /api/items/bulk-load")
        self.assertEqual(bulk.evidence[0].line, ln(API, "@action(", 3))
        self.assertEqual(bulk.evidence[0].detail, "    @action(")

    def test_the_action_url_path_defaults_to_the_function_name_and_the_methods_default_to_get(self):
        got = self.labels_of()
        self.assertIn(("GET /api/items/recent", "ItemViewSet.recent"), got)

    def test_a_read_only_viewset_gives_two_routes_and_a_list_mixin_one(self):
        urls = API_URLS.replace('router.register(r"items", api.ItemViewSet, basename="item")', 'router.register(r"things", api.ReadOnlyThing, basename="thing")\nrouter.register(r"lists", api.ListOnly, basename="lst")')
        got = [g for g in self.labels_of(urls) if g[0] != "GET /api"]
        self.assertEqual(got, [("GET /api/lists", "ListOnly.list"), ("GET /api/things", "ReadOnlyThing.list"), ("GET /api/things/{pk}", "ReadOnlyThing.retrieve")])

    def test_a_viewset_of_the_repository_has_the_routes_of_the_actions_it_defines(self):
        urls = API_URLS.replace('router.register(r"items", api.ItemViewSet, basename="item")', 'router.register(r"plain", api.Plain, basename="plain")')
        got = [g for g in self.labels_of(urls) if g[0] != "GET /api"]
        self.assertEqual(got, [("GET /api/plain", "Plain.list"), ("GET /api/plain/{pk}", "Plain.retrieve")])

    def test_the_lookup_field_and_the_lookup_url_kwarg_name_the_parameter(self):
        urls = API_URLS.replace('router.register(r"items", api.ItemViewSet, basename="item")', 'router.register(r"s", api.SlugThing)\nrouter.register(r"k", api.KwargThing)')
        got = sorted(g[0] for g in self.labels_of(urls) if g[0] != "GET /api")
        self.assertIn("GET /api/s/{slug}", got)
        self.assertIn("GET /api/k/{ident}", got)
        self.assertNotIn("GET /api/s/{pk}", got)

    def test_a_simple_router_has_no_root_view_and_the_trailing_slash_option_changes_nothing_in_the_path(self):
        urls = API_URLS.replace("routers.DefaultRouter()", "routers.SimpleRouter(trailing_slash=False)")
        got = self.labels_of(urls)
        self.assertNotIn("GET /api", [g[0] for g in got])
        self.assertIn(("GET /api/items", "ItemViewSet.list"), got)

    def test_register_with_keyword_arguments_and_an_empty_prefix(self):
        urls = API_URLS.replace('router.register(r"items", api.ItemViewSet, basename="item")', 'router.register(prefix="", viewset=api.ReadOnlyThing, basename="t")')
        got = [g for g in self.labels_of(urls)]
        self.assertIn(("GET /api", "ReadOnlyThing.list"), got)
        self.assertIn(("GET /api/{pk}", "ReadOnlyThing.retrieve"), got)

    def test_a_router_with_urlpatterns_set_to_its_urls_or_extended_by_them(self):
        urls = f(["from rest_framework import routers", "", "from . import api", "", "router = routers.SimpleRouter()", 'router.register(r"things", api.ReadOnlyThing)', "",
                  "urlpatterns = router.urls"])
        self.assertEqual(self.labels_of(urls), [("GET /api/things", "ReadOnlyThing.list"), ("GET /api/things/{pk}", "ReadOnlyThing.retrieve")])
        r = self.route("GET /api/things")
        self.assertEqual([(e.path, e.line) for e in r.evidence], [("shop/api_urls.py", 6), ("shop/api_urls.py", 8), ("config/urls.py", 4)])
        urls2 = f(["from django.urls import path", "from rest_framework import routers", "", "from . import api", "", "router = routers.SimpleRouter()",
                   'router.register(r"things", api.ReadOnlyThing)', "", "urlpatterns = [path('x/', api.ReadOnlyThing.as_view({'get': 'list'}))] + router.urls"])
        self.assertEqual([g[0] for g in self.labels_of(urls2)], ["GET /api/things", "GET /api/things/{pk}", "GET /api/x"])

    def test_a_router_declared_in_one_file_and_registered_and_included_in_others(self):
        routers = f(["from rest_framework import routers", "", "router = routers.DefaultRouter()"])
        urls = f(["from django.urls import include, path", "", "from . import api", "from .routers import router", "", 'router.register(r"things", api.ReadOnlyThing)', "",
                  "urlpatterns = [", '    path("", include(router.urls)),', "]"])
        self.repo({"files": drf_tree(urls, **{"shop/routers.py": routers})})
        self.entry()
        got = [r.label for r in self.django.routes(None, self.sha("main"))]
        self.assertEqual(got, ["GET /api", "GET /api/things", "GET /api/things/{pk}"])

    def test_a_viewset_imported_from_another_app_is_resolved(self):
        other = f(["from rest_framework import viewsets", "", "class Remote(viewsets.ReadOnlyModelViewSet):", "    pass"])
        urls = f(["from django.urls import include, path", "from rest_framework import routers", "", "from remote.api import Remote", "", "router = routers.SimpleRouter()",
                  'router.register(r"r", Remote)', "urlpatterns = [path('', include(router.urls))]"])
        self.repo({"files": drf_tree(urls, **{"remote/__init__.py": "", "remote/api.py": other})})
        self.entry()
        r = self.route("GET /api/r")
        self.assertEqual((r.target, r.handler), ("remote/api.py", "Remote.list"))

    def test_a_router_mounted_twice_gives_the_routes_twice(self):
        root = f(["from django.urls import include, path", "", "urlpatterns = [", '    path("v1/", include("shop.api_urls")),', '    path("v2/", include("shop.api_urls")),', "]"])
        self.repo({"files": drf_tree(**{"config/urls.py": root})})
        self.entry()
        labels = self.route_labels()
        self.assertIn("GET /v1/items", labels)
        self.assertIn("GET /v2/items", labels)

    def test_a_viewset_with_a_base_outside_the_repository_that_is_not_known_has_routes_of_any_method(self):
        api = f(["from thirdparty import SpecialViewSet", "", "class Mystery(SpecialViewSet):", "    pass"])
        urls = API_URLS.replace('router.register(r"items", api.ItemViewSet, basename="item")', 'router.register(r"m", api.Mystery)')
        got = [g for g in self.labels_of(urls, api) if g[0] != "GET /api"]
        self.assertEqual(got, [("ANY /api/m", "Mystery"), ("ANY /api/m/{pk}", "Mystery")])

    def test_a_viewset_that_cannot_be_resolved_blocks_and_its_routes_are_not_guessed(self):
        urls = API_URLS.replace('api.ItemViewSet', 'api.registry["item"]')
        self.repo({"files": drf_tree(urls)})
        self.entry()
        self.assertEqual(self.route_labels(), ["GET /api"])
        self.assertEqual(self.kinds_of_records(), [("view_unresolved", "shop/api_urls.py:" + str(ln(urls, "router.register")), True)])
        self.assertTrue(self.django.problems)

    def test_a_router_of_a_class_that_is_not_known_blocks(self):
        urls = API_URLS.replace("routers.DefaultRouter()", "nested.NestedRouter(parent, 'p')").replace("from rest_framework import routers", "from drf_nested import nested")
        self.repo({"files": drf_tree(urls)})
        self.entry()
        self.assertEqual(self.route_labels(), [])
        self.assertTrue(any(k == "mount_unresolved" and b for k, _s, b in self.kinds_of_records()))

    def test_a_router_nothing_includes_is_unmounted(self):
        urls = API_URLS.replace('path("", include(router.urls))', 'path("x/", api.ReadOnlyThing.as_view({"get": "list"}))')
        self.repo({"files": drf_tree(urls)})
        self.entry()
        self.assertEqual(self.route_labels(), ["GET /api/x"])
        self.assertEqual(self.django.unmounted(self.sha("main")), [("shop/api_urls.py", "router"), ("shop/urls.py", "urlpatterns")])  # (and the base urls of the tree, which this root does not include)

    def test_the_url_names_of_a_router_use_the_basename(self):
        self.repo({"files": drf_tree()})
        self.entry()
        names = self.django.url_names(self.sha("main"))
        self.assertEqual([r.label for r in names["item-list"]], ["GET /api/items", "POST /api/items"])
        self.assertEqual([r.label for r in names["item-detail"]], ["DELETE /api/items/{pk}", "GET /api/items/{pk}", "PATCH /api/items/{pk}", "PUT /api/items/{pk}"])
        self.assertEqual([r.label for r in names["item-archive"]], ["POST /api/items/{pk}/archive"])
        self.assertEqual([r.label for r in names["item-bulk"]], ["GET /api/items/bulk-load", "POST /api/items/bulk-load"])
        self.assertEqual([r.label for r in names["api-root"]], ["GET /api"])


if __name__ == "__main__":
    unittest.main()
