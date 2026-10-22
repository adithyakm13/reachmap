"""Shared pieces of the Django adapter tests: a small project tree, the base case class, a line finder.

The project is `config` (settings and the root URLconf) and one app, `shop`, with two function views. Every
test builds it (or a variation) as a case of the module's universe, as the FastAPI tests do
(`tests/support_universe.py`), and reads the adapter through `Base.entry()`.
"""

from __future__ import annotations

from reachmap import entrypoints
from reachmap.adapters.django import DjangoAdapter
from reachmap.adapters.python import PyAdapter
from tests import scenario_support as ss
from tests.support_universe import Base as UniverseBase
from tests.support_universe import f

SETTINGS = f(["DEBUG = True", 'ROOT_URLCONF = "config.urls"'])
ROOT = f([
    "from django.urls import include, path",
    "",
    "urlpatterns = [",
    '    path("shop/", include("shop.urls")),',
    "]",
])
VIEWS = f([
    "from django.http import JsonResponse",
    "",
    "",
    "def item_list(request):",
    "    return JsonResponse([], safe=False)",
    "",
    "",
    "def item_detail(request, item_id):",
    "    return JsonResponse({})",
])
URLS = f([
    "from django.urls import path",
    "",
    "from . import views",
    "",
    "urlpatterns = [",
    '    path("items/", views.item_list, name="item-list"),',
    '    path("items/<int:item_id>/", views.item_detail, name="item-detail"),',
    "]",
])


def tree(**overrides):
    """The base project; a path mapped to None is left out."""
    files = {
        "config/__init__.py": "", "config/settings.py": SETTINGS, "config/urls.py": ROOT,
        "shop/__init__.py": "", "shop/views.py": VIEWS, "shop/urls.py": URLS,
    }
    files.update(overrides)
    return {k: v for k, v in files.items() if v is not None}


def ln(text: str, needle: str, nth: int = 1) -> int:
    """The 1-based line of the `nth` line of `text` that contains `needle`."""
    hits = [i for i, line in enumerate(text.split("\n"), 1) if needle in line]
    return hits[nth - 1]


class Base(UniverseBase):
    UNIVERSE_PREFIX = "reachmap-django-"
    REPO = "acme/portal"

    def make_adapter(self, **kw):
        kw.setdefault("test_markers", ["tests/", "test_"])
        kw.setdefault("framework", "django")
        return PyAdapter(self.REPO, self.url, self.git, **kw)

    def entry(self, options=None, **kw):
        """The Django adapter on a fresh Python adapter; `options` are the adapter's own (root_urlconf, settings)."""
        self.language = self.make_adapter(**kw)
        self.django = DjangoAdapter(self.REPO, self.language, **(options or {}))
        return self.django

    def labels(self, sha="main", **kw):
        entry = self.entry(**kw)
        return sorted((r.label, r.handler) for r in entry.routes(None, self.sha(sha)))

    def route(self, label, sha="main"):
        (r,) = [r for r in self.django.routes(None, self.sha(sha)) if r.label == label]
        return r

    def route_labels(self, sha="main"):
        return [r.label for r in self.django.routes(None, self.sha(sha))]

    def reach(self, **kw):
        analysis = self.analyze(self.language)
        res = entrypoints.reach(analysis, self.language, self.django, **kw)
        evidence = [e for r in res.reached for c in r.area.chains for e in c]
        self.assertEqual(ss.evidence_problems({self.REPO: self.path}, evidence), [])
        return res

    def changed(self, files, changes, options=None, **kw):
        """Build `files` and a second commit with `changes`, and reach from the change."""
        self.repo({"files": files}, {"files": changes})
        self.entry(options)
        return self.reach(**kw)

    def kinds_of_records(self):
        return sorted((r.kind, r.subject, r.blocks_selection) for r in self.records)
