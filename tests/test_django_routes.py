"""The Django entry-point adapter's route table on small projects built for one rule each: `path()` and `re_path()`,
converters, `include()` in every form (a module path, a module object, a tuple, an inline list, a list variable), the ways
`urlpatterns` is written, namespaces and url names, the root URLconf (from `ROOT_URLCONF` or from the config), unmounted URLconfs
and what cannot be read.

Views and DRF routers are in test_django_views.py, what a change reaches in test_django_reach.py, the fixture scenarios in
test_scenarios_dj.py.
"""

import unittest

from tests.support_django import ROOT, SETTINGS, URLS, VIEWS, Base, f, ln, tree


class Composition(Base):
    def test_a_route_is_composed_over_every_include_up_to_the_root_urlconf_with_its_evidence_in_order(self):
        self.repo({"files": tree()})
        self.entry()
        r = self.route("ANY /shop/items/{item_id}")
        self.assertEqual(
            (r.kind, r.method, r.path, r.handler, r.file, r.target, r.module, r.partial),
            ("api_route", "ANY", "/shop/items/{item_id}", "item_detail", "shop/urls.py", "shop/views.py", "shop", False),
        )
        self.assertEqual([(e.path, e.line, e.detail.strip()) for e in r.evidence], [
            ("shop/urls.py", ln(URLS, "item_detail"), 'path("items/<int:item_id>/", views.item_detail, name="item-detail"),'),
            ("config/urls.py", 4, 'path("shop/", include("shop.urls")),'),
        ])
        self.assertEqual(r.evidence[0].claim, "route:ANY /shop/items/{item_id}:shop/views.py")
        self.assertIsNone(r.evidence[1].claim)
        self.assertEqual(r.line, r.evidence[0])
        self.assertEqual(self.route_labels(), ["ANY /shop/items", "ANY /shop/items/{item_id}"])

    def test_the_route_of_an_empty_path_is_the_root_and_slashes_are_joined_without_doubling(self):
        urls = URLS.replace('path("items/", views.item_list, name="item-list"),', 'path("", views.item_list),\n    path("//x//", views.item_list),')
        self.repo({"files": tree(**{"shop/urls.py": urls})})
        self.entry()
        self.assertEqual(sorted(self.route_labels()), ["ANY /shop", "ANY /shop/items/{item_id}", "ANY /shop/x"])
        root = f(["from django.urls import path", "from shop import views", "", "urlpatterns = [", '    path("", views.item_list),', "]"])
        self.repo({"files": tree(**{"config/urls.py": root})})
        self.entry()
        self.assertEqual(self.route_labels(), ["ANY /"])

    def test_the_converters_become_parameters_and_the_path_converter_takes_the_rest(self):
        urls = f([
            "from django.urls import path", "from . import views", "",
            "urlpatterns = [",
            '    path("c/<int:a>/<slug:s>/<str:n>/<uuid:u>/<bare>/<path:rest>", views.item_list),',
            "]",
        ])
        self.repo({"files": tree(**{"shop/urls.py": urls})})
        self.assertEqual(self.labels(), [("ANY /shop/c/{a}/{s}/{n}/{u}/{bare}/{rest:path}", "item_list")])

    def test_a_custom_converter_is_a_parameter_too(self):
        urls = f(["from django.urls import path", "from . import views", "", "urlpatterns = [", '    path("y/<year:y>/", views.item_list),', "]"])
        self.repo({"files": tree(**{"shop/urls.py": urls})})
        self.assertEqual(self.labels(), [("ANY /shop/y/{y}", "item_list")])

    def test_re_path_is_kept_as_a_partial_pattern_with_its_groups_marked(self):
        urls = f([
            "from django.urls import re_path", "from . import views", "",
            "urlpatterns = [",
            '    re_path(r"^archive/(?P<year>[0-9]{4})/$", views.item_list),',
            '    re_path(r"^old/(\\d+)/$", views.item_list),',
            '    re_path(r"^plain/$", views.item_list),',
            '    re_path(r"^files/(?P<name>.+)$", views.item_list),',
            '    re_path(r"^opt(ional)?/$", views.item_list),',
            "]",
        ])
        self.repo({"files": tree(**{"shop/urls.py": urls})})
        self.entry()
        got = {r.path: r.partial for r in self.django.routes(None, self.sha("main"))}
        self.assertEqual(got, {
            "/shop/archive/{year}": True,
            "/shop/old/<unknown:(\\d+)>": True,
            "/shop/plain": True,
            "/shop/files/{name:path}": True,
            "/shop/<unknown:opt(ional)?>": True,
        })

    def test_a_regex_prefix_of_an_include_composes_with_what_is_below_it(self):
        root = f(["from django.urls import include, re_path", "", "urlpatterns = [", '    re_path(r"^shop/", include("shop.urls")),', "]"])
        self.repo({"files": tree(**{"config/urls.py": root})})
        self.entry()
        r = self.route("ANY /shop/items")
        self.assertTrue(r.partial)

    def test_a_prefix_that_cannot_be_evaluated_is_marked_in_the_path_and_recorded_when_a_change_reaches_it(self):
        root = f(["from django.conf import settings", "from django.urls import include, path", "", "urlpatterns = [",
                  '    path(settings.SHOP_PREFIX, include("shop.urls")),', "]"])
        views = VIEWS.replace("JsonResponse([], safe=False)", "JsonResponse([1], safe=False)")
        self.repo({"files": tree(**{"config/urls.py": root})}, {"files": {"shop/views.py": views}})
        self.entry()
        self.assertEqual([(r.path, r.partial) for r in self.django.routes(None, self.sha("main"))],
                         [("/<unknown:settings.SHOP_PREFIX>items", True), ("/<unknown:settings.SHOP_PREFIX>items/{item_id}", True)])
        res = self.reach()
        self.assertEqual([r.area.label for r in res.reached], ["ANY /<unknown:settings.SHOP_PREFIX>items"])
        self.assertEqual(self.kinds_of_records(), [("route_path_partial", "config/urls.py:5", False)])

    def test_a_constant_and_an_f_string_of_constants_are_evaluated(self):
        root = f(["from django.urls import include, path", "", 'BASE = "api/"', 'V = "v1"', "urlpatterns = [",
                  '    path(f"{BASE}{V}/", include("shop.urls")),', "]"])
        self.repo({"files": tree(**{"config/urls.py": root})})
        self.assertEqual([r[0] for r in self.labels()], ["ANY /api/v1/items", "ANY /api/v1/items/{item_id}"])
        self.assertEqual(self.records, [])

    def test_an_imported_constant_is_evaluated(self):
        root = f(["from django.urls import include, path", "", "from config.consts import PREFIX", "", "urlpatterns = [", '    path(PREFIX, include("shop.urls")),', "]"])
        self.repo({"files": tree(**{"config/urls.py": root, "config/consts.py": 'PREFIX = "things/"\n'})})
        self.assertEqual([r[0] for r in self.labels()], ["ANY /things/items", "ANY /things/items/{item_id}"])


class Includes(Base):
    def root_with(self, include_line, header=()):
        return f(["from django.urls import include, path", *header, "", "urlpatterns = [", f"    {include_line},", "]"])

    def labels_of(self, **files):
        self.repo({"files": tree(**files)})
        self.entry()
        return self.route_labels()

    def test_include_of_a_module_object(self):
        root = self.root_with('path("shop/", include(shop_urls))', ["from shop import urls as shop_urls"])
        self.assertEqual(self.labels_of(**{"config/urls.py": root}), ["ANY /shop/items", "ANY /shop/items/{item_id}"])

    def test_include_of_a_module_attribute_reached_by_a_plain_import(self):
        root = self.root_with('path("shop/", include(shop.urls))', ["import shop.urls"])
        self.assertEqual(self.labels_of(**{"config/urls.py": root}), ["ANY /shop/items", "ANY /shop/items/{item_id}"])

    def test_include_of_a_tuple_with_a_module_or_a_module_path_and_an_application_name(self):
        root = f(["from django.urls import include, path", "from shop import urls as shop_urls", "", "urlpatterns = [",
                  '    path("a/", include((shop_urls, "shop"))),', '    path("b/", include(("shop.urls", "shop"), namespace="b")),', "]"])
        self.assertEqual(self.labels_of(**{"config/urls.py": root}), [
            "ANY /a/items", "ANY /a/items/{item_id}", "ANY /b/items", "ANY /b/items/{item_id}"])

    def test_include_of_an_inline_list_and_of_a_list_variable_in_the_same_file(self):
        root = f(["from django.urls import include, path", "", "from shop import views", "", "extra = [", '    path("e/", views.item_list),', "]", "",
                  "urlpatterns = [", '    path("inline/", include([path("i/", views.item_detail)])),', '    path("var/", include(extra)),', "]"])
        self.repo({"files": tree(**{"config/urls.py": root})})
        self.entry()
        self.assertEqual(self.route_labels(), ["ANY /inline/i", "ANY /var/e"])
        inline = self.route("ANY /inline/i")
        self.assertEqual([(e.path, e.line) for e in inline.evidence], [("config/urls.py", ln(root, 'path("i/"')), ("config/urls.py", ln(root, 'path("inline/"'))])

    def test_include_of_a_list_variable_of_another_module(self):
        root = f(["from django.urls import include, path", "from shop import urls as shop_urls", "", "urlpatterns = [", '    path("s/", include(shop_urls.urlpatterns)),', "]"])
        self.assertEqual(self.labels_of(**{"config/urls.py": root}), ["ANY /s/items", "ANY /s/items/{item_id}"])

    def test_one_module_included_twice_gives_a_route_per_mount_each_with_its_own_include_line(self):
        root = f(["from django.urls import include, path", "", "urlpatterns = [", '    path("a/", include("shop.urls")),', '    path("b/", include("shop.urls")),', "]"])
        self.repo({"files": tree(**{"config/urls.py": root})})
        self.entry()
        self.assertEqual(self.route_labels(), ["ANY /a/items", "ANY /a/items/{item_id}", "ANY /b/items", "ANY /b/items/{item_id}"])
        self.assertEqual(self.route("ANY /b/items").evidence[1].line, 5)
        self.assertEqual(self.route("ANY /a/items").evidence[1].line, 4)

    def test_a_chain_of_three_urlconfs(self):
        mid = f(["from django.urls import include, path", "", "urlpatterns = [", '    path("v1/", include("shop.urls")),', "]"])
        root = self.root_with('path("api/", include("config.mid"))')
        self.repo({"files": tree(**{"config/urls.py": root, "config/mid.py": mid})})
        self.entry()
        r = self.route("ANY /api/v1/items")
        self.assertEqual([(e.path, e.line) for e in r.evidence], [("shop/urls.py", 6), ("config/mid.py", 4), ("config/urls.py", 4)])

    def test_a_comment_is_not_an_include_and_a_module_nothing_includes_has_no_route(self):
        root = f(["from django.urls import include, path", "", "urlpatterns = [", '    # path("shop/", include("shop.urls")),', "]"])
        self.repo({"files": tree(**{"config/urls.py": root})})
        self.assertEqual(self.labels(), [])

    def test_a_cycle_of_includes_ends(self):
        a = f(["from django.urls import include, path", "from shop import views", "", "urlpatterns = [", '    path("b/", include("config.b")),', '    path("x/", views.item_list),', "]"])
        b = f(["from django.urls import include, path", "", "urlpatterns = [", '    path("a/", include("config.a")),', "]"])
        root = self.root_with('path("r/", include("config.a"))')
        self.repo({"files": tree(**{"config/urls.py": root, "config/a.py": a, "config/b.py": b})})
        self.entry()
        self.assertEqual(self.route_labels(), ["ANY /r/x"])


class PatternLists(Base):
    def urls(self, *lines):
        return f(["from django.conf import settings", "from django.conf.urls.static import static", "from django.urls import path", "from . import views", "", *lines])

    def labels_of(self, text):
        self.repo({"files": tree(**{"shop/urls.py": text})})
        self.entry()
        return self.route_labels()

    def test_urlpatterns_extended_with_plus_equals_append_extend_and_a_sum(self):
        text = self.urls(
            "base = [", '    path("a/", views.item_list),', "]", "more = (", '    path("b/", views.item_list),', ")", "",
            "urlpatterns = base + [path('c/', views.item_list)]",
            "urlpatterns += [path('d/', views.item_list)]",
            "urlpatterns.append(path('e/', views.item_list))",
            "urlpatterns.extend([path('f/', views.item_list)])",
            "urlpatterns += more",
        )
        self.assertEqual(self.labels_of(text), ["ANY /shop/a", "ANY /shop/b", "ANY /shop/c", "ANY /shop/d", "ANY /shop/e", "ANY /shop/f"])

    def test_a_route_inside_a_module_level_if_is_a_route(self):
        text = self.urls("urlpatterns = [path('a/', views.item_list)]", "if settings.DEBUG:", "    urlpatterns += [path('dbg/', views.item_list)]")
        self.assertEqual(self.labels_of(text), ["ANY /shop/a", "ANY /shop/dbg"])

    def test_static_helpers_are_not_routes_and_not_unresolved(self):
        text = self.urls("urlpatterns = [path('a/', views.item_list)] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)")
        self.assertEqual(self.labels_of(text), ["ANY /shop/a"])
        self.assertEqual(self.records, [])
        self.assertEqual(self.django.problems, [])

    def test_a_pattern_list_imported_from_another_module(self):
        other = f(["from django.urls import path", "from shop import views", "", "common = [path('c/', views.item_list)]"])
        text = f(["from django.urls import path", "from shop import views", "from shop.common import common", "", "urlpatterns = [path('own/', views.item_detail)] + common"])
        self.repo({"files": tree(**{"shop/urls.py": text, "shop/common.py": other})})
        self.entry()
        self.assertEqual(self.route_labels(), ["ANY /shop/c", "ANY /shop/own"])

    def test_a_list_comprehension_is_not_understood_and_blocks(self):
        text = self.urls("urlpatterns = [path(f'{n}/', views.item_list) for n in ('a', 'b')]")
        self.assertEqual(self.labels_of(text), [])
        self.assertEqual(self.kinds_of_records(), [("mount_unresolved", "shop/urls.py:6", True)])
        self.assertTrue(self.django.problems)

    def test_a_function_that_builds_the_patterns_is_not_understood_and_blocks(self):
        text = self.urls("def build():", "    return [path('a/', views.item_list)]", "", "urlpatterns = build()")
        self.assertEqual(self.labels_of(text), [])
        self.assertIn(("mount_unresolved", "shop/urls.py:9", True), self.kinds_of_records())

    def test_a_pattern_with_no_literal_view_name_still_gives_the_route_with_its_handler_marked(self):
        text = self.urls("urlpatterns = [path('a/', views.item_list, name='x')]", "urlpatterns += [path('b/', lambda request: None)]")
        self.assertEqual(self.labels_of(text), ["ANY /shop/a", "ANY /shop/b"])
        self.assertEqual(self.route("ANY /shop/b").handler, "<lambda>")


class Namespaces(Base):
    def names(self, files):
        self.repo({"files": files})
        self.entry()
        return {k: [r.label for r in v] for k, v in self.django.url_names(self.sha("main")).items()}

    def test_names_have_no_namespace_without_an_application_name(self):
        self.assertEqual(self.names(tree()), {"item-list": ["ANY /shop/items"], "item-detail": ["ANY /shop/items/{item_id}"]})

    def test_an_app_name_of_the_included_module_is_the_namespace(self):
        urls = URLS.replace("from . import views\n", 'from . import views\n\napp_name = "shop"\n')
        self.assertEqual(self.names(tree(**{"shop/urls.py": urls})), {"shop:item-list": ["ANY /shop/items"], "shop:item-detail": ["ANY /shop/items/{item_id}"]})

    def test_the_namespace_argument_names_the_instance_and_namespaces_nest(self):
        urls = URLS.replace("from . import views\n", 'from . import views\n\napp_name = "shop"\n')
        mid = f(["from django.urls import include, path", "", 'app_name = "api"', "urlpatterns = [", '    path("v1/", include("shop.urls", namespace="v1")),', "]"])
        root = f(["from django.urls import include, path", "", "urlpatterns = [", '    path("api/", include("config.mid")),', "]"])
        got = self.names(tree(**{"shop/urls.py": urls, "config/mid.py": mid, "config/urls.py": root}))
        self.assertEqual(got, {"api:v1:item-list": ["ANY /api/v1/items"], "api:v1:item-detail": ["ANY /api/v1/items/{item_id}"]})

    def test_a_tuple_gives_the_application_name(self):
        root = f(["from django.urls import include, path", "", "urlpatterns = [", '    path("a/", include(("shop.urls", "app"))),', "]"])
        got = self.names(tree(**{"config/urls.py": root}))
        self.assertEqual(sorted(got), ["app:item-detail", "app:item-list"])

    def test_the_same_name_in_two_mounts_lists_both_routes(self):
        root = f(["from django.urls import include, path", "", "urlpatterns = [", '    path("a/", include("shop.urls")),', '    path("b/", include("shop.urls")),', "]"])
        got = self.names(tree(**{"config/urls.py": root}))
        self.assertEqual(got["item-list"], ["ANY /a/items", "ANY /b/items"])


class RootUrlconf(Base):
    def test_the_root_comes_from_a_literal_root_urlconf_in_any_settings_module(self):
        files = tree(**{"config/settings.py": None, "config/settings/__init__.py": "", "config/settings/base.py": "DEBUG = False\n", "config/settings/prod.py": SETTINGS})
        self.repo({"files": files})
        self.entry()
        self.assertEqual(self.route_labels(), ["ANY /shop/items", "ANY /shop/items/{item_id}"])

    def test_the_option_names_the_root_and_settings_are_not_read(self):
        files = tree(**{"config/settings.py": 'ROOT_URLCONF = os.environ["ROOT"]\n', "config/other.py": ROOT})
        self.repo({"files": files})
        self.entry({"root_urlconf": "config.other"})
        self.assertEqual(self.route_labels(), ["ANY /shop/items", "ANY /shop/items/{item_id}"])
        self.assertEqual(self.records, [])

    def test_two_settings_with_two_roots_give_the_routes_of_both(self):
        admin = f(["from django.urls import path", "from shop import views", "", "urlpatterns = [", '    path("only/", views.item_list),', "]"])
        files = tree(**{"config/settings_admin.py": 'ROOT_URLCONF = "config.admin_urls"\n', "config/admin_urls.py": admin})
        self.repo({"files": files})
        self.entry()
        self.assertEqual(self.route_labels(), ["ANY /only", "ANY /shop/items", "ANY /shop/items/{item_id}"])

    def test_the_settings_option_limits_which_files_are_read(self):
        files = tree(**{"config/settings_admin.py": 'ROOT_URLCONF = "config.admin_urls"\n'})
        self.repo({"files": files})
        self.entry({"settings": "config/settings.py"})
        self.assertEqual(self.route_labels(), ["ANY /shop/items", "ANY /shop/items/{item_id}"])
        self.assertEqual(self.records, [])

    def test_a_root_urlconf_that_is_not_a_literal_blocks_and_no_route_is_claimed(self):
        self.repo({"files": tree(**{"config/settings.py": 'import os\nROOT_URLCONF = os.environ["ROOT"]\n'})})
        self.entry()
        self.assertEqual(self.django.routes(None, self.sha("main")), [])
        self.assertEqual(self.kinds_of_records(), [("urlconf_root_unknown", "acme/portal", True)])
        self.assertTrue(self.django.problems)

    def test_no_root_urlconf_anywhere_blocks_and_no_route_is_claimed(self):
        self.repo({"files": tree(**{"config/settings.py": "DEBUG = True\n"})})
        self.entry()
        self.assertEqual(self.django.routes(None, self.sha("main")), [])
        self.assertEqual(self.kinds_of_records(), [("urlconf_root_unknown", "acme/portal", True)])

    def test_a_root_urlconf_naming_a_module_that_is_not_there_blocks(self):
        self.repo({"files": tree(**{"config/settings.py": 'ROOT_URLCONF = "config.nowhere"\n'})})
        self.entry()
        self.assertEqual(self.django.routes(None, self.sha("main")), [])
        self.assertEqual(self.kinds_of_records(), [("urlconf_root_unknown", "acme/portal", True)])

    def test_a_reference_to_root_urlconf_that_is_not_an_assignment_is_not_a_root(self):
        mw = f(["from django.conf import settings", "", "def root():", "    return settings.ROOT_URLCONF"])
        self.repo({"files": tree(**{"config/mw.py": mw})})
        self.entry()
        self.assertEqual(self.route_labels(), ["ANY /shop/items", "ANY /shop/items/{item_id}"])
        self.assertEqual(self.records, [])

    def test_a_conditional_root_urlconf_gives_both_roots(self):
        settings = f(["import os", "", "if os.environ.get('ADMIN'):", '    ROOT_URLCONF = "config.admin_urls"', "else:", '    ROOT_URLCONF = "config.urls"'])
        admin = f(["from django.urls import path", "from shop import views", "", "urlpatterns = [", '    path("only/", views.item_list),', "]"])
        self.repo({"files": tree(**{"config/settings.py": settings, "config/admin_urls.py": admin})})
        self.entry()
        self.assertEqual(self.route_labels(), ["ANY /only", "ANY /shop/items", "ANY /shop/items/{item_id}"])


class Unmounted(Base):
    LEGACY = f(["from django.urls import path", "", "from shop import views", "", "urlpatterns = [", '    path("old/", views.item_list),', "]"])

    def test_a_urlconf_nothing_includes_has_no_route_and_is_listed_as_unmounted(self):
        self.repo({"files": tree(**{"shop/legacy_urls.py": self.LEGACY})})
        self.entry()
        self.assertEqual(self.route_labels(), ["ANY /shop/items", "ANY /shop/items/{item_id}"])
        self.assertEqual(self.django.unmounted(self.sha("main")), [("shop/legacy_urls.py", "urlpatterns")])

    def test_a_urlconf_included_only_by_an_unmounted_one_is_unmounted_through_it(self):
        mid = f(["from django.urls import include, path", "", "urlpatterns = [", '    path("m/", include("shop.legacy_urls")),', "]"])
        self.repo({"files": tree(**{"shop/legacy_urls.py": self.LEGACY, "config/mid.py": mid})})
        self.entry()
        self.assertEqual(self.django.unmounted(self.sha("main")), [("config/mid.py", "urlpatterns")])

    def test_the_root_and_what_it_includes_are_never_unmounted(self):
        self.repo({"files": tree()})
        self.entry()
        self.assertEqual(self.django.unmounted(self.sha("main")), [])


class Completeness(Base):
    def test_a_urlconf_that_does_not_parse_blocks_and_claims_nothing_from_it(self):
        broken = "from django.urls import path\nurlpatterns = [\n    path('x/', views.f,\n"
        self.repo({"files": tree(**{"shop/broken_urls.py": broken})})
        self.entry()
        self.assertEqual(self.route_labels(), ["ANY /shop/items", "ANY /shop/items/{item_id}"])
        self.assertIn(("parse_degraded", "shop/broken_urls.py", True), self.kinds_of_records())
        self.assertTrue(self.django.problems)

    def test_an_included_module_that_does_not_parse_blocks_and_its_routes_are_not_guessed(self):
        self.repo({"files": tree(**{"shop/urls.py": "urlpatterns = [\n    path('x/', views.f,\n"})})
        self.entry()
        self.assertEqual(self.route_labels(), [])
        self.assertIn(("parse_degraded", "shop/urls.py", True), self.kinds_of_records())
        self.assertTrue(self.django.problems)

    def test_an_include_that_cannot_be_resolved_to_a_module_of_the_repository_blocks(self):
        root = f(["from django.urls import include, path", "from config import factory", "", "urlpatterns = [", "    path('x/', include(factory.make())),", '    path("shop/", include("shop.urls")),', "]"])
        self.repo({"files": tree(**{"config/urls.py": root, "config/factory.py": "def make():\n    return []\n"})})
        self.entry()
        self.assertEqual(self.route_labels(), ["ANY /shop/items", "ANY /shop/items/{item_id}"])
        self.assertEqual(self.kinds_of_records(), [("mount_unresolved", "config/urls.py:5", True)])
        self.assertTrue(self.django.problems)

    def test_an_include_of_a_module_path_that_is_not_in_the_repository_is_external_and_does_not_block(self):
        root = f(["from django.urls import include, path", "", "urlpatterns = [", '    path("accounts/", include("django.contrib.auth.urls")),', '    path("shop/", include("shop.urls")),', "]"])
        self.repo({"files": tree(**{"config/urls.py": root})})
        self.entry()
        self.assertEqual(self.route_labels(), ["ANY /shop/items", "ANY /shop/items/{item_id}"])
        self.assertEqual(self.kinds_of_records(), [("include_external", "config/urls.py:4", False)])
        self.assertEqual(self.django.problems, [])

    def test_an_include_of_a_missing_module_of_the_repository_blocks(self):
        root = f(["from django.urls import include, path", "", "urlpatterns = [", '    path("x/", include("shop.missing_urls")),', "]"])
        self.repo({"files": tree(**{"config/urls.py": root})})
        self.entry()
        self.django.routes(None, self.sha("main"))
        self.assertIn(("mount_unresolved", "config/urls.py:4", True), self.kinds_of_records())

    def test_the_admin_site_is_not_a_route_and_not_unresolved(self):
        root = f(["from django.contrib import admin", "from django.urls import include, path", "", "urlpatterns = [", '    path("admin/", admin.site.urls),', '    path("shop/", include("shop.urls")),', "]"])
        self.repo({"files": tree(**{"config/urls.py": root})})
        self.entry()
        self.assertEqual(self.route_labels(), ["ANY /shop/items", "ANY /shop/items/{item_id}"])
        self.assertEqual((self.records, self.django.problems), ([], []))

    def test_a_search_that_was_cut_makes_the_table_incomplete_not_empty(self):
        self.repo({"files": tree()})
        self.entry(examine_cap=1)
        self.django.routes(None, self.sha("main"))
        self.assertTrue(self.django.problems)
        self.assertTrue(any("search" in why for why in self.django.problems), self.django.problems)
        self.assertIn("search_truncated", {r.kind for r in self.records})

    def test_a_whole_search_is_complete(self):
        self.repo({"files": tree()})
        self.entry()
        self.django.routes(None, self.sha("main"))
        self.assertEqual((self.django.problems, self.records), ([], []))

    def test_an_include_in_a_function_is_recorded_as_unresolved_and_blocks(self):
        text = URLS + "\n\ndef more():\n    return [path('z/', include('shop.other'))]\n"
        self.repo({"files": tree(**{"shop/urls.py": text.replace("from django.urls import path", "from django.urls import include, path")})})
        self.entry()
        self.django.routes(None, self.sha("main"))
        self.assertIn(("mount_unresolved", "shop/urls.py:" + str(ln(text, "include('shop.other')")), True), self.kinds_of_records())


class RoutesChanged(Base):
    def test_a_removed_and_an_added_route_each_carry_the_evidence_of_their_own_version(self):
        new = URLS.replace('"items/<int:item_id>/"', '"items/by-id/<int:item_id>/"')
        self.repo({"files": tree()}, {"files": {"shop/urls.py": new}})
        entry = self.entry()
        removed, added = entry.routes_changed(self.sha("main~1"), self.sha("main"))
        self.assertEqual([r.label for r in removed], ["ANY /shop/items/{item_id}"])
        self.assertEqual([r.label for r in added], ["ANY /shop/items/by-id/{item_id}"])
        self.assertEqual({e.ref for e in removed[0].evidence}, {self.sha("main~1")})
        self.assertEqual({e.ref for e in added[0].evidence}, {self.sha("main")})

    def test_a_changed_include_prefix_removes_every_route_below_it_and_adds_them_under_the_new_one(self):
        self.repo({"files": tree()}, {"files": {"config/urls.py": ROOT.replace('"shop/"', '"store/"')}})
        entry = self.entry()
        removed, added = entry.routes_changed(self.sha("main~1"), self.sha("main"))
        self.assertEqual([r.label for r in removed], ["ANY /shop/items", "ANY /shop/items/{item_id}"])
        self.assertEqual([r.label for r in added], ["ANY /store/items", "ANY /store/items/{item_id}"])

    def test_nothing_is_computed_without_a_pre_change_version(self):
        self.repo({"files": tree()})
        entry = self.entry()
        self.assertEqual(entry.routes_changed(None, self.sha("main")), ([], []))


if __name__ == "__main__":
    unittest.main()
