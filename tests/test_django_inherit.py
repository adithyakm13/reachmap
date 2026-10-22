"""What a change reaches through classes and wrappers that live in other files: a base class of a view in another module (a changed method reaches the
route of that method, a changed attribute every route of the subclass), a base viewset, a decorator a route wraps its view in, the legacy `url()`.
"""

import unittest

from tests.support_django import URLS, VIEWS, Base, f, tree
from tests.test_django_views import urls_of

BASE = f([
    "from django.views import View",
    "",
    "",
    "class Base(View):",
    "    flag = 1",
    "",
    "    def get(self, request):",
    "        return 1",
    "",
    "    def _helper(self):",
    "        return 2",
])
CHILD = f([
    "from shop.base import Base",
    "",
    "",
    "class Child(Base):",
    "    def post(self, request):",
    "        return self._helper()",
    "",
    "    def delete(self, request):",
    "        return 3",
])


class AcrossFiles(Base):
    def case(self, base, **more):
        files = tree(**{"shop/base.py": BASE, "shop/children.py": CHILD, "shop/urls.py": f(["from django.urls import path", "", "from . import children", "", "urlpatterns = [",
                                                                                           '    path("c/", children.Child.as_view()),', "]"])})
        return self.changed(files, {"shop/base.py": base, **more})

    def labels(self, res):
        return [r.area.label for r in res.reached]

    def test_a_changed_method_of_a_base_class_in_another_file_reaches_the_route_of_that_method_only(self):
        res = self.case(BASE.replace("return 1", "return 11"))
        self.assertEqual(self.labels(res), ["GET /shop/c", "HEAD /shop/c"])
        self.assertTrue(res.complete)

    def test_a_changed_private_helper_of_the_base_reaches_the_methods_of_the_subclass_that_call_it(self):
        res = self.case(BASE.replace("return 2", "return 22"))
        self.assertEqual(self.labels(res), ["POST /shop/c"])

    def test_a_changed_class_attribute_of_the_base_reaches_every_route_of_the_subclass(self):
        res = self.case(BASE.replace("flag = 1", "flag = 2"))
        self.assertEqual(self.labels(res), ["DELETE /shop/c", "GET /shop/c", "HEAD /shop/c", "OPTIONS /shop/c", "POST /shop/c"])

    def test_the_base_file_is_an_entry_file_because_a_view_inherits_from_it(self):
        self.repo({"files": tree(**{"shop/base.py": BASE, "shop/children.py": CHILD, "shop/urls.py": f(["from django.urls import path", "", "from . import children", "", "urlpatterns = [",
                                                                                                       '    path("c/", children.Child.as_view()),', "]"])})})
        entry = self.entry()
        sha = self.sha("main")
        self.assertTrue(entry.is_entry_file("shop/base.py", sha))
        self.assertTrue(entry.is_entry_file("shop/children.py", sha))


class Viewsets(Base):
    API = f([
        "from rest_framework import viewsets",
        "",
        "",
        "class Base(viewsets.ReadOnlyModelViewSet):",
        "    def get_queryset(self):",
        "        return []",
    ])
    CHILD = f([
        "from shop.apibase import Base",
        "",
        "",
        "class Things(Base):",
        "    pass",
    ])
    URLS = f([
        "from django.urls import include, path",
        "from rest_framework import routers",
        "",
        "from . import things",
        "",
        "router = routers.SimpleRouter()",
        'router.register(r"things", things.Things)',
        "",
        "urlpatterns = [path('', include(router.urls))]",
    ])

    def test_a_public_hook_of_a_base_viewset_in_another_file_reaches_every_route_of_the_subclass(self):
        files = tree(**{"shop/apibase.py": self.API, "shop/things.py": self.CHILD, "shop/urls.py": self.URLS})
        res = self.changed(files, {"shop/apibase.py": self.API.replace("return []", "return [1]")})
        self.assertEqual([r.area.label for r in res.reached], ["GET /shop/things", "GET /shop/things/{pk}"])


class Wrappers(Base):
    DECOS = f(["def staff_only(view):", "    return view"])

    def test_a_decorator_function_called_where_the_urlconf_loads_reaches_every_route_of_that_urlconf(self):
        """The wrapper runs when the URLconf is imported, so the walk treats a change to it as a change to a statement that runs at load: every route of the file
        (a stated limit: the route it wraps is not told from the others)."""
        urls = f(["from django.urls import path", "", "from . import views", "from .decos import staff_only", "", "urlpatterns = [", '    path("a/", staff_only(views.item_list)),',
                  '    path("b/", views.item_detail),', "]"])
        res = self.changed(tree(**{"shop/urls.py": urls, "shop/decos.py": self.DECOS}), {"shop/decos.py": self.DECOS.replace("return view", "return view or None")})
        self.assertEqual([r.area.label for r in res.reached], ["ANY /shop/a", "ANY /shop/b"])

    def test_a_route_wrapped_in_a_decorator_still_resolves_to_its_view(self):
        urls = f(["from django.urls import path", "", "from . import views", "from .decos import staff_only", "", "urlpatterns = [", '    path("a/", staff_only(views.item_list)),', "]"])
        views = VIEWS.replace("JsonResponse([], safe=False)", "JsonResponse([5], safe=False)")
        res = self.changed(tree(**{"shop/urls.py": urls, "shop/decos.py": self.DECOS}), {"shop/views.py": views})
        self.assertEqual([r.area.label for r in res.reached], ["ANY /shop/a"])

    def test_the_legacy_url_function_is_a_regex_route(self):
        urls = f(["from django.conf.urls import url", "", "from . import views", "", "urlpatterns = [", '    url(r"^old/(?P<n>[0-9]+)/$", views.item_list),', "]"])
        self.repo({"files": tree(**{"shop/urls.py": urls})})
        self.entry()
        (r,) = self.django.routes(None, self.sha("main"))
        self.assertEqual((r.label, r.partial), ("ANY /shop/old/{n}", True))


if __name__ == "__main__":
    unittest.main()
