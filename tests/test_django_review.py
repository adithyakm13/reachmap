"""Review fixes of the Django adapter: layouts and edits the first fixture does not exercise (apps under a folder the module map does not mount, a vendored
package named like an installed one, settings that do not assign the root, a view called by another routed view, an edit of one name of an import statement,
a route narrowed from every method to some), what a table that was not read completely may and may not claim, and the smaller rules (a constant
`detail`, a regex `url_path`, include prefixes joined as Django joins them, implied HEAD and OPTIONS, `http_method_names`).
"""

import unittest

from reachmap import notes
from reachmap.adapters.python import PyAdapter
from tests.support_django import ROOT, SETTINGS, URLS, VIEWS, Base, f, ln, tree
from tests.test_django_views import API, API_URLS, ROOT_API, VIEWS_ALL, drf_tree, urls_of


class InRepoIncludes(Base):
    """An include that does not resolve is external only when nothing in the repository could be that package."""

    def apps_layout(self, **more):
        return tree(**{"shop/__init__.py": None, "shop/views.py": None, "shop/urls.py": None, "apps/shop/__init__.py": "", "apps/shop/views.py": VIEWS, "apps/shop/urls.py": URLS, **more})

    def test_an_include_of_a_module_under_a_folder_the_module_map_does_not_mount_blocks_it_is_not_external(self):
        self.repo({"files": self.apps_layout()})
        self.entry()
        self.assertEqual(self.route_labels(), [])
        self.assertEqual(self.kinds_of_records(), [("mount_unresolved", "config/urls.py:4", True)])
        self.assertTrue(self.django.problems)

    def test_a_urlconf_in_such_a_layout_is_never_called_unmounted(self):
        res = self.changed(self.apps_layout(), {"apps/shop/views.py": VIEWS.replace("JsonResponse([], safe=False)", "JsonResponse([5], safe=False)")})
        self.assertEqual(res.reached, ())
        kinds = {k for k, _s, _b in self.kinds_of_records()}
        self.assertEqual(kinds, {"mount_unresolved"})
        self.assertFalse(res.complete)

    def test_a_module_of_a_package_that_is_nowhere_in_the_repository_is_still_external(self):
        root = f(["from django.urls import include, path", "", "urlpatterns = [", '    path("a/", include("django.contrib.auth.urls")),', '    path("shop/", include("shop.urls")),', "]"])
        self.repo({"files": tree(**{"config/urls.py": root})})
        self.entry()
        self.route_labels()
        self.assertEqual(self.kinds_of_records(), [("include_external", "config/urls.py:4", False)])

    def test_a_vendored_copy_of_a_package_named_like_an_installed_one_is_not_external(self):
        root = f(["from django.urls import include, path", "", "urlpatterns = [", '    path("a/", include("rest_framework.urls")),', "]"])
        self.repo({"files": tree(**{"config/urls.py": root, "vendor/rest_framework/__init__.py": "", "vendor/rest_framework/urls.py": "urlpatterns = []\n"})})
        self.entry()
        self.route_labels()
        self.assertEqual(self.kinds_of_records(), [("mount_unresolved", "config/urls.py:4", True)])

    def test_a_view_imported_from_a_package_the_module_map_does_not_mount_is_unresolved_not_external(self):
        urls = f(["from django.urls import path", "from shop.pages import home", "", "urlpatterns = [path('h/', home)]"])
        self.repo({"files": tree(**{"shop/urls.py": urls, "apps/shop/pages.py": "def home(request):\n    return None\n", "shop/views.py": None, "apps/shop/__init__.py": ""})})
        self.entry()
        self.route_labels()
        self.assertIn("view_unresolved", {k for k, _s, _b in self.kinds_of_records()})


class Frameworks(Base):
    """What the Django kind tells the Python adapter is per repository and shadowed by anything of that name in the tree."""

    def analyze_urls_change(self, other, framework="django"):
        files = tree(**{"shop/other.py": other})
        self.repo({"files": files}, {"files": {"shop/urls.py": URLS + "\nextra = 1\n"}})
        return self.analyze(self.make_adapter(framework=framework))

    def test_a_vendored_package_named_like_an_installed_one_keeps_its_record(self):
        files = tree(**{"vendor/rest_framework/__init__.py": "", "vendor/rest_framework/serializers.py": "class Ser:\n    x = 1\n",
                        "shop/views.py": VIEWS + "\nfrom rest_framework.serializers import Ser\n\n\ndef uses(request):\n    return Ser.x\n"})
        self.repo({"files": files}, {"files": {"vendor/rest_framework/serializers.py": "class Ser:\n    x = 2\n"}})
        self.analyze(self.make_adapter(framework="django"))
        self.assertIn(("specifier_unresolved", "rest_framework", True), self.kinds_of_records())

    def test_a_repository_that_is_not_a_django_one_gets_no_such_names(self):
        self.analyze_urls_change("from django.urls import path\n", framework=None)
        self.assertEqual([(r.kind, r.subject) for r in self.records], [("specifier_unresolved", "django")])

    def test_a_django_repository_does_not_record_them(self):
        self.analyze_urls_change("from django.urls import path\n")
        self.assertEqual(self.records, [])

    def test_the_config_gives_the_python_adapter_the_names_of_its_entry_point_kind(self):
        from reachmap import config, entrypoints  # noqa: F401  (importing entrypoints registers the kind)

        ws = config.loads('workspace = "a"\n[repos."a/p"]\nhost = "github"\nlanguage = "python"\nroles = ["backend"]\nentry_points = { kind = "django" }\n[repos."a/q"]\nhost = "github"\nlanguage = "python"\nroles = ["backend"]\nentry_points = { kind = "fastapi" }\n', fmt="toml", env={})
        self.repo({"files": tree()})
        p = PyAdapter.from_config(ws.repos["a/p"], self.git, limits=ws.limits, remote=self.url)
        q = PyAdapter.from_config(ws.repos["a/q"], self.git, limits=ws.limits, remote=self.url)
        self.assertIn("django", p.installed)
        self.assertEqual(q.installed, frozenset())

    def test_urlpatterns_is_not_distinctive_for_a_model_note_either(self):
        files = tree(**{"shop/b_urls.py": "urlpatterns = []\n", "shop/c_urls.py": "urlpatterns = []\n", "shop/d_urls.py": "urlpatterns = []\n"})
        self.repo({"files": files}, {"files": {"shop/urls.py": URLS.replace("urlpatterns = [", "urlpatterns = [  # changed")}})
        self.entry()
        analysis = self.analyze(self.make_adapter(framework="django", distinct_max=3))
        self.assertEqual(self.records, [])  # nothing is recorded: a convention is not counted
        impact = self.impact("shop/urls.py")
        self.assertIn("urlpatterns", impact.not_distinctive)  # but it is among the names the report rejects
        report = {"propagations": [{"passes": [{"depth": 0, "candidate": False, "analysis": {"impacts": [{
            "path": "shop/urls.py", "symbols": [{"name": "urlpatterns"}], "not_distinctive": list(impact.not_distinctive), "names_unreadable": []}]}}]}]}
        names = notes.changed_names(report)
        self.assertEqual((sorted(names.distinct), sorted(names.rejected)), ([], ["urlpatterns"]))
        verdict = notes.verify_file({**report, "revisions_read": {}}, "acme/portal", "shop/b_urls.py", "urlpatterns", None)
        self.assertEqual(verdict["reason"], "symbol_not_distinctive")


class Settings(Base):
    PROD = f(["from .settings import *", "", 'MIDDLEWARE = ["a"]'])

    def test_a_settings_module_that_star_imports_the_one_with_the_root_is_a_settings_module(self):
        res = self.changed(tree(**{"config/settings_prod.py": self.PROD}), {"config/settings_prod.py": self.PROD.replace('"a"', '"b"')})
        self.assertEqual(res.reached, ())
        self.assertEqual(self.kinds_of_records(), [("settings_not_followed", "config/settings_prod.py", True)])
        self.assertFalse(res.complete)

    def test_a_settings_module_named_by_the_manage_script_is_one_even_with_no_root_in_it(self):
        manage = f(["import os", "import sys", "", "", "def main():", '    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.local")'])
        local = 'DEBUG = True\n'
        res = self.changed(tree(**{"manage.py": manage, "config/local.py": local}), {"config/local.py": 'DEBUG = False\n'})
        self.assertEqual(res.reached, ())
        self.assertEqual(self.kinds_of_records(), [("settings_not_followed", "config/local.py", True)])

    def test_a_module_that_is_not_a_settings_module_is_not_one(self):
        res = self.changed(tree(**{"config/other.py": "X = 1\n"}), {"config/other.py": "X = 2\n"})
        self.assertEqual((res.reached, self.records), ((), []))
        self.assertTrue(res.complete)


class ViewsCalledByViews(Base):
    OTHER = f(["from shop.views import item_detail", "", "", "def wrapped(request, item_id):", "    return item_detail(request, item_id)"])

    def urls(self):
        return URLS.replace("]", '    path("wrapped/<int:item_id>/", other.wrapped),\n]').replace("from . import views", "from . import views, other")

    def test_a_view_that_another_routed_view_calls_reaches_the_route_of_the_caller(self):
        files = tree(**{"shop/other.py": self.OTHER, "shop/urls.py": self.urls()})
        res = self.changed(files, {"shop/views.py": VIEWS.replace("return JsonResponse({})", "return JsonResponse({'a': 1})")})
        self.assertEqual([r.area.label for r in res.reached], ["ANY /shop/items/{item_id}", "ANY /shop/wrapped/{item_id}"])
        self.assertTrue(res.complete)

    def test_the_same_through_a_module_import(self):
        other = f(["from shop import views", "", "", "def wrapped(request, item_id):", "    return views.item_detail(request, item_id)"])
        files = tree(**{"shop/other.py": other, "shop/urls.py": self.urls()})
        res = self.changed(files, {"shop/views.py": VIEWS.replace("return JsonResponse({})", "return JsonResponse({'a': 1})")})
        self.assertEqual([r.area.label for r in res.reached], ["ANY /shop/items/{item_id}", "ANY /shop/wrapped/{item_id}"])

    def test_a_view_the_urlconf_alone_imports_still_ends_the_walk_there(self):
        res = self.changed(tree(), {"shop/views.py": VIEWS.replace("return JsonResponse({})", "return JsonResponse({'a': 1})")})
        self.assertEqual([r.area.label for r in res.reached], ["ANY /shop/items/{item_id}"])
        self.assertEqual([len(r.area.chains) for r in res.reached], [1])  # no second chain through the URLconf that mounts it


class ImportStatements(Base):
    HELPERS = "def clean(x):\n    return x\n\n\ndef other(x):\n    return x\n\n\ndef third(x):\n    return x\n"
    SRC = VIEWS.replace("from django.http import JsonResponse", "from django.http import JsonResponse\nfrom .helpers import clean, other").replace(
        "JsonResponse([], safe=False)", "JsonResponse(clean([]), safe=False)").replace("JsonResponse({})", "JsonResponse(other({}))")

    def case(self, new_import):
        files = tree(**{"shop/views.py": self.SRC, "shop/helpers.py": self.HELPERS})
        return self.changed(files, {"shop/views.py": self.SRC.replace("from .helpers import clean, other", new_import)})

    def test_adding_a_name_to_an_import_statement_reaches_only_the_views_that_use_that_name(self):
        res = self.case("from .helpers import clean, other, third")
        self.assertEqual(res.reached, ())

    def test_re_targeting_one_name_reaches_the_views_that_use_it_and_the_chain_cites_the_import_line(self):
        res = self.case("from .helpers import clean, other as other\nfrom .helpers import third as other_third")  # `other` keeps its target
        self.assertEqual(res.reached, ())
        res = self.case("from .helpers import clean\nfrom .helpers import third as other")
        self.assertEqual([r.area.label for r in res.reached], ["ANY /shop/items/{item_id}"])  # only item_detail uses `other`
        first = res.reached[0].area.chains[0][0]
        self.assertEqual((first.path, first.detail), ("shop/views.py", "from .helpers import third as other"))

    def test_removing_a_name_from_the_statement_reaches_the_views_that_used_it(self):
        res = self.case("from .helpers import clean")
        self.assertEqual([r.area.label for r in res.reached], ["ANY /shop/items/{item_id}"])


class Smaller(Base):
    def test_an_action_whose_detail_is_a_module_constant_is_evaluated_and_one_that_is_not_blocks(self):
        api = API.replace("@action(detail=True, methods=[\"post\"])", "@action(detail=DETAIL, methods=[\"post\"])").replace("from .models import Item", "from .models import Item\n\nDETAIL = True")
        self.repo({"files": drf_tree(api=api)})
        self.entry()
        self.assertIn("POST /api/items/{pk}/archive", self.route_labels())
        bad = API.replace("@action(detail=True, methods=[\"post\"])", "@action(detail=flag(), methods=[\"post\"])")
        self.repo({"files": drf_tree(api=bad)})
        self.entry()
        labels = self.route_labels()
        self.assertFalse([x for x in labels if "archive" in x])
        self.assertIn("view_unresolved", {k for k, _s, b in self.kinds_of_records() if b})

    def test_a_url_path_that_is_a_regex_is_read_as_one_and_the_route_is_approximate(self):
        api = f(["from rest_framework import viewsets", "from rest_framework.decorators import action", "", "", "class ItemViewSet(viewsets.ModelViewSet):", "",
                 '    @action(detail=True, url_path=r"parts/(?P<part_pk>[^/.]+)")', "    def part(self, request, pk=None, part_pk=None):", "        return None"])
        self.repo({"files": drf_tree(api=api)})
        self.entry()
        (r,) = [r for r in self.django.routes(None, self.sha("main")) if r.handler == "ItemViewSet.part"]
        self.assertEqual((r.path, r.partial, r.path_approximate), ("/api/items/{pk}/parts/{part_pk}", True, True))

    def test_include_prefixes_join_the_way_django_joins_them_without_adding_a_slash(self):
        root = f(["from django.urls import include, path", "", "urlpatterns = [", '    path("shop", include("shop.urls")),', "]"])
        self.repo({"files": tree(**{"config/urls.py": root})})
        self.entry()
        self.assertEqual(self.route_labels(), ["ANY /shopitems", "ANY /shopitems/{item_id}"])

    def test_a_router_mounted_without_a_slash_joins_the_same_way(self):
        root = f(["from django.urls import include, path", "", "urlpatterns = [", '    path("api", include("shop.api_urls")),', "]"])
        self.repo({"files": drf_tree(**{"config/urls.py": root})})
        self.entry()
        self.assertIn("GET /apiitems", self.route_labels())

    def class_view(self, body, urlname="Thing"):
        views = f(["from django.views import View", "", "", f"class {urlname}(View):", *body])
        self.repo({"files": tree(**{"shop/views.py": views, "shop/urls.py": urls_of(f"path('t/', views.{urlname}.as_view())")})})
        return self.labels()

    def test_a_class_view_that_defines_get_also_answers_head_and_every_class_view_answers_options(self):
        got = self.class_view(["    def get(self, request):", "        return 1"])
        self.assertEqual([g[0] for g in got], ["GET /shop/t", "HEAD /shop/t", "OPTIONS /shop/t"])

    def test_a_head_method_of_its_own_is_not_implied_and_only_lowercase_names_dispatch(self):
        got = self.class_view(["    def get(self, request):", "        return 1", "", "    def head(self, request):", "        return 2", "", "    def POST(self, request):", "        return 3"])
        self.assertEqual([g[0] for g in got], ["GET /shop/t", "HEAD /shop/t", "OPTIONS /shop/t"])
        self.assertEqual(dict(got)["HEAD /shop/t"], "Thing.head")

    def test_a_literal_http_method_names_limits_the_methods_and_one_that_is_not_literal_is_recorded(self):
        got = self.class_view(["    http_method_names = ['get', 'post']", "", "    def get(self, request):", "        return 1", "", "    def delete(self, request):", "        return 2"])
        self.assertEqual([g[0] for g in got], ["GET /shop/t"])  # no DELETE; and no HEAD or OPTIONS either: they are not in http_method_names
        views = f(["from django.views import View", "", "NAMES = compute()", "", "", "class Thing(View):", "    http_method_names = NAMES", "", "    def get(self, request):", "        return 1"])
        self.repo({"files": tree(**{"shop/views.py": views, "shop/urls.py": urls_of("path('t/', views.Thing.as_view())")}), }, {"files": {"shop/views.py": views.replace("return 1", "return 2")}})
        self.entry()
        res = self.reach()
        self.assertIn("route_methods_unknown", {r.kind for r in self.records})

    def test_a_urlconf_nothing_includes_has_a_kind_of_its_own_not_the_wording_about_a_router(self):
        from reachmap import incomplete

        legacy = f(["from django.urls import path", "", "from shop import legacy_views", "", "urlpatterns = [", '    path("old/", legacy_views.export),', "]"])
        lv = f(["def export(request):", "    return None"])
        self.repo({"files": tree(**{"shop/legacy_urls.py": legacy, "shop/legacy_views.py": lv})}, {"files": {"shop/legacy_views.py": lv.replace("None", "1")}})
        self.entry()
        self.reach()
        (rec,) = self.records
        self.assertEqual((rec.kind, rec.subject, rec.blocks_selection), ("urlconf_unmounted", "shop/legacy_urls.py:urlpatterns", False))
        self.assertNotIn("nothing mounts that router", rec.not_established)
        self.assertEqual(incomplete.kind_spec("router_unmounted").template.count("router"), 1)  # the kind of the routers is as it was


class WhatIsWithheld(Base):
    """Removals and additions are withheld only where the unread part could be: an unresolved include costs the routes under its own prefix, an unknown root or
    a search that failed costs all of them, and every withheld claim is counted in a record."""

    BASE_ROOT = f(["from django.urls import include, path", "", "urlpatterns = [", '    path("shop/", include("shop.urls")),', '    path("x/", include("x.urls")),', "]"])
    HEAD_ROOT = f(["from django.urls import include, path", "from config import factory", "", "urlpatterns = [", '    path("shop/", include("shop.urls")),', '    path("x/", include(factory.make())),', "]"])
    X_URLS = f(["from django.urls import path", "", "from shop import views", "", "urlpatterns = [", '    path("a/", views.item_list),', "]"])

    def removed(self, base_files, head_files):
        self.repo({"files": base_files}, {"files": head_files})
        entry = self.entry()
        removed, added = entry.routes_changed(self.sha("main~1"), self.sha("main"))
        return [r.label for r in removed], [r.label for r in added]

    def files(self, **more):
        return tree(**{"config/urls.py": self.BASE_ROOT, "x/__init__.py": "", "x/urls.py": self.X_URLS, **more})

    def test_an_unresolved_include_withholds_the_routes_under_its_prefix_and_not_the_others(self):
        head = {"config/urls.py": self.HEAD_ROOT, "config/factory.py": "def make():\n    return []\n", "shop/urls.py": URLS.replace('    path("items/", views.item_list, name="item-list"),\n', "")}
        removed, added = self.removed(self.files(), head)
        self.assertEqual(removed, ["ANY /shop/items"])  # unrelated to /x: still a removal
        self.assertEqual(added, [])
        self.assertIn(("route_change_withheld", "acme/portal", True), self.kinds_of_records())  # /x/a is not claimed as removed, and that is said

    def test_a_removal_under_the_prefix_of_the_unread_part_is_withheld(self):
        head = {"config/urls.py": self.HEAD_ROOT, "config/factory.py": "def make():\n    return []\n"}
        removed, _added = self.removed(self.files(), head)
        self.assertEqual(removed, [])

    def test_a_urlconf_that_does_not_parse_withholds_the_routes_under_the_include_that_carries_it_only(self):
        head = {"x/urls.py": "urlpatterns = [\n    path('a/', views.f,\n"}
        removed, _ = self.removed(self.files(), head)
        self.assertEqual(removed, [])
        head2 = {"x/urls.py": "urlpatterns = [\n    path('a/', views.f,\n", "shop/urls.py": URLS.replace('    path("items/", views.item_list, name="item-list"),\n', "")}
        removed, _ = self.removed(self.files(), head2)
        self.assertEqual(removed, ["ANY /shop/items"])

    def test_an_unknown_root_withholds_everything(self):
        head = {"config/settings.py": 'import os\nROOT_URLCONF = os.environ["R"]\n'}
        removed, added = self.removed(self.files(), head)
        self.assertEqual((removed, added), ([], []))
        self.assertIn("route_change_withheld", {k for k, _s, _b in self.kinds_of_records()})

    def test_a_file_that_is_not_text_and_that_nothing_readable_includes_withholds_everything(self):
        head = {"shop/stray_urls.py": b"\xff\xfeurlpatterns = []\n", "shop/urls.py": URLS.replace('    path("items/", views.item_list, name="item-list"),\n', "")}
        removed, _ = self.removed(self.files(), head)
        self.assertEqual(removed, [])


class Misc(unittest.TestCase):
    def test_no_ascii_arrow_in_a_comment_or_a_docstring_of_the_adapter(self):
        import ast
        import io
        import pathlib
        import tokenize

        root = pathlib.Path(__file__).resolve().parents[1] / "reachmap" / "adapters" / "django"
        for path in root.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            for tok in tokenize.generate_tokens(io.StringIO(text).readline):
                if tok.type == tokenize.COMMENT:
                    self.assertNotIn("->", tok.string, f"{path.name}:{tok.start[0]}")
            for node in ast.walk(ast.parse(text)):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                    self.assertNotIn("->", ast.get_docstring(node) or "", path.name)


if __name__ == "__main__":
    unittest.main()
