"""The FastAPI entry-point adapter on small repositories built for one rule each: handlers (every decorator form),
mounts across files (aliased imports, module attributes, several prefixes, a prefix on the constructor, sub
applications), prefixes that cannot be evaluated, unmounted routers, removed and added routes, and what a change reaches
(a handler body directly, a service function through the walk).

The M1 scenarios (routes against expected.json) are in test_scenarios_py.py and test_scenarios_entry.py.
"""

import unittest

from reachmap import entrypoints
from reachmap import walk as walkmod
from reachmap.adapters.fastapi import FastApiAdapter
from reachmap.adapters.python import PyAdapter
from tests import scenario_support as ss
from tests.support_universe import Base as UniverseBase
from tests.support_universe import f

MAIN = f(["from fastapi import FastAPI", "", "from app.api import api_router", "", "app = FastAPI()", 'app.include_router(api_router, prefix="/api")'])
API = f(["from fastapi import APIRouter", "", "from app.routers.items import router as items_router", "", "api_router = APIRouter()", 'api_router.include_router(items_router, prefix="/items")'])
ITEMS = f([
    "from fastapi import APIRouter, Request", "", "router = APIRouter()", "", "", '@router.get("")', "def list_items(request: Request):", "    return []", "",
    "", '@router.get("/{item_id}")', "def get_item(request: Request, item_id: str):", "    return {}",
])


def tree(**overrides):
    files = {"app/__init__.py": "", "app/routers/__init__.py": "", "app/main.py": MAIN, "app/api.py": API, "app/routers/items.py": ITEMS}
    files.update(overrides)
    return files


class Base(UniverseBase):
    UNIVERSE_PREFIX = "reachmap-fastapi-"
    REPO = "acme/api"

    def make_adapter(self, **kw):
        kw.setdefault("test_markers", ["tests/", "test_"])
        return PyAdapter(self.REPO, self.url, self.git, **kw)

    def entry(self, **kw):
        self.language = self.make_adapter(**kw)
        self.fastapi = FastApiAdapter(self.REPO, self.language)
        return self.fastapi

    def labels(self, sha="main", **kw):
        entry = self.entry(**kw)
        return sorted((r.label, r.handler) for r in entry.routes(None, self.sha(sha)))

    def route(self, label, sha="main"):
        (r,) = [r for r in self.fastapi.routes(None, self.sha(sha)) if r.label == label]
        return r

    def reach(self, **kw):
        analysis = self.analyze(self.language)
        res = entrypoints.reach(analysis, self.language, self.fastapi, **kw)
        evidence = [e for r in res.reached for c in r.area.chains for e in c]
        self.assertEqual(ss.evidence_problems({self.REPO: self.path}, evidence), [])
        return res


class Routes(Base):
    def test_a_route_is_composed_over_every_mount_up_to_the_app_with_its_evidence_in_order(self):
        self.repo({"files": tree()})
        self.entry()
        r = self.route("GET /api/items/{item_id}")
        self.assertEqual((r.kind, r.method, r.path, r.handler, r.file, r.target, r.partial), ("api_route", "GET", "/api/items/{item_id}", "get_item", "app/routers/items.py", "app/routers/items.py", False))
        self.assertEqual([(e.path, e.line, e.detail) for e in r.evidence], [
            ("app/routers/items.py", 11, '@router.get("/{item_id}")'),
            ("app/api.py", 6, 'api_router.include_router(items_router, prefix="/items")'),
            ("app/main.py", 6, 'app.include_router(api_router, prefix="/api")'),
        ])
        self.assertEqual(r.line, r.evidence[0])
        self.assertEqual([x.label for x in self.fastapi.routes(None, self.sha("main"))], ["GET /api/items", "GET /api/items/{item_id}"])

    def test_every_decorator_form(self):
        src = f([
            "from fastapi import APIRouter", "router = APIRouter()", "",
            '@router.post("/a")', "async def a():", "    pass", "",
            '@router.put("/b")', "def b():", "    pass", "",
            '@router.patch("/c")', "def c():", "    pass", "",
            '@router.delete("/d")', "def d():", "    pass", "",
            '@router.options("/e")', "def e():", "    pass", "",
            '@router.head("/f")', "def f_():", "    pass", "",
            '@router.api_route("/g", methods=["GET", "POST"])', "def g():", "    pass", "",
            '@router.api_route("/h")', "def h():", "    pass", "",
            '@router.websocket("/ws")', "async def ws():", "    pass", "",
            "@router.get(", '    "/multi",', "    response_model=dict,", ")", "def multi():", "    pass", "",
            '@router.get(path="/kw")', "def kw():", "    pass", "",
            '@router.get("/two")', '@router.get("/two-b")', "def two():", "    pass",
        ])
        self.repo({"files": tree(**{"app/routers/items.py": src})})
        expected = sorted([
            ("GET /api/items/h", "h"), ("GET /api/items/g", "g"), ("GET /api/items/kw", "kw"), ("GET /api/items/multi", "multi"),
            ("GET /api/items/two", "two"), ("GET /api/items/two-b", "two"), ("DELETE /api/items/d", "d"), ("HEAD /api/items/f", "f_"),
            ("OPTIONS /api/items/e", "e"), ("PATCH /api/items/c", "c"), ("POST /api/items/a", "a"), ("POST /api/items/g", "g"), ("PUT /api/items/b", "b"),
            ("WEBSOCKET /api/items/ws", "ws"),
        ])
        self.assertEqual(self.labels(), expected)
        multi = self.route("GET /api/items/multi")
        self.assertEqual(multi.evidence[0].line, 40)  # the decorator's first line

    def test_a_router_mounted_twice_under_two_prefixes_gives_two_routes_with_their_own_mount_line(self):
        api = API + 'api_router.include_router(items_router, prefix="/v2/items")\n'
        self.repo({"files": tree(**{"app/api.py": api})})
        got = self.labels()
        self.assertEqual([g for g in got if g[1] == "get_item"], [("GET /api/items/{item_id}", "get_item"), ("GET /api/v2/items/{item_id}", "get_item")])
        second = self.route("GET /api/v2/items/{item_id}")
        self.assertEqual(second.evidence[1].line, 7)
        self.assertEqual(self.route("GET /api/items/{item_id}").evidence[1].line, 6)

    def test_a_prefix_on_the_constructor_and_on_the_parent_compose_in_the_framework_order(self):
        api = f(["from fastapi import APIRouter", "", "from app.routers.items import router as items_router", "",
                 'api_router = APIRouter(prefix="/p")', 'api_router.include_router(items_router, prefix="/i")'])
        items = ITEMS.replace("router = APIRouter()", 'router = APIRouter(prefix="/c")')
        self.repo({"files": tree(**{"app/api.py": api, "app/routers/items.py": items})})
        self.entry()
        r = self.route("GET /api/p/i/c")
        self.assertEqual(r.handler, "list_items")
        # the decorator, the constructor that gave the router its prefix, the mount, the constructor of the parent, the mount of that
        self.assertEqual([(e.path, e.line) for e in r.evidence], [("app/routers/items.py", 6), ("app/routers/items.py", 3), ("app/api.py", 6), ("app/api.py", 5), ("app/main.py", 6)])

    def test_mounts_resolve_aliased_imports_and_module_attributes(self):
        api = f([
            "import app.routers.items as items_mod", "from fastapi import APIRouter", "from app import routers", "",
            "api_router = APIRouter()", 'api_router.include_router(items_mod.router, prefix="/a")', 'api_router.include_router(routers.items.router, prefix="/b")',
        ])
        self.repo({"files": tree(**{"app/api.py": api})})
        self.assertEqual({r[0] for r in self.labels() if r[1] == "get_item"}, {"GET /api/a/{item_id}", "GET /api/b/{item_id}"})

    def test_a_router_imported_from_another_module_is_found_by_its_handlers_decorator(self):
        core = f(["from fastapi import APIRouter", "", "router = APIRouter()"])
        use = f(["from app.core import router", "", "", '@router.get("/hi")', "def hi():", "    return 1"])
        main = f(["from fastapi import FastAPI", "", "from app.core import router", "import app.use", "", "app = FastAPI()", 'app.include_router(router, prefix="/x")'])
        self.repo({"files": {"app/__init__.py": "", "app/core.py": core, "app/use.py": use, "app/main.py": main}})
        self.assertEqual(self.labels(), [("GET /x/hi", "hi")])

    def test_a_sub_application_mount_composes_like_a_router(self):
        sub = f(["from fastapi import FastAPI", "", "sub = FastAPI()", "", "", '@sub.get("/ping")', "def ping():", "    return 1"])
        main = f(["from fastapi import FastAPI", "from fastapi.staticfiles import StaticFiles", "", "from app.sub import sub", "", "app = FastAPI()",
                  'app.mount("/sub", sub)', 'app.mount("/static", StaticFiles(directory="static"))'])
        self.repo({"files": {"app/__init__.py": "", "app/sub.py": sub, "app/main.py": main}})
        self.assertEqual(self.labels(), [("GET /sub/ping", "ping")])
        r = self.route("GET /sub/ping")
        self.assertEqual([e.detail for e in r.evidence], ['@sub.get("/ping")', 'app.mount("/sub", sub)'])
        self.assertEqual(self.records, [])  # a StaticFiles mount is not a router: nothing is said about it

    def test_a_comment_is_not_a_mount_and_a_router_nothing_mounts_has_no_route(self):
        api = API.replace('api_router.include_router(items_router, prefix="/items")', '# api_router.include_router(items_router, prefix="/items")')
        self.repo({"files": tree(**{"app/api.py": api})})
        self.assertEqual(self.labels(), [])


class Prefixes(Base):
    def api(self, prefix_expr, extra=""):
        return f(["from fastapi import APIRouter", "", extra, "from app.routers.items import router as items_router", "",
                  "api_router = APIRouter()", f"api_router.include_router(items_router, prefix={prefix_expr})"])

    def test_a_module_constant_and_an_f_string_of_constants_are_evaluated(self):
        api = f(["from fastapi import APIRouter", "", "from app.routers.items import router as items_router", "", 'VERSION = "v1"', 'BASE = "/" + VERSION',
                 "api_router = APIRouter()", 'api_router.include_router(items_router, prefix=f"{BASE}/items")'])
        self.repo({"files": tree(**{"app/api.py": api})})
        self.assertEqual([r[0] for r in self.labels()], ["GET /api/v1/items", "GET /api/v1/items/{item_id}"])
        self.assertEqual(self.records, [])

    def test_an_imported_constant_is_evaluated(self):
        consts = 'PREFIX = "/things"\n'
        api = self.api("PREFIX", "from app.consts import PREFIX")
        self.repo({"files": tree(**{"app/api.py": api, "app/consts.py": consts})})
        self.assertEqual([r[0] for r in self.labels()], ["GET /api/things", "GET /api/things/{item_id}"])

    def test_a_prefix_that_cannot_be_evaluated_is_marked_in_the_path_and_recorded_never_guessed(self):
        self.repo({"files": tree(**{"app/api.py": self.api("settings.items_prefix", "from app import settings")})})
        self.entry()
        routes = self.fastapi.routes(None, self.sha("main"))
        self.assertEqual([(r.path, r.partial) for r in routes], [("/api/<unknown:settings.items_prefix>", True), ("/api/<unknown:settings.items_prefix>/{item_id}", True)])

    def test_the_partial_route_is_recorded_once_when_a_change_reaches_it(self):
        files = tree(**{"app/api.py": self.api("settings.items_prefix", "from app import settings")})
        self.repo({"files": files}, {"files": {"app/routers/items.py": ITEMS.replace("    return {}", "    return {1: 2}")}})
        self.entry()
        res = self.reach()
        self.assertEqual([r.area.label for r in res.reached], ["GET /api/<unknown:settings.items_prefix>/{item_id}"])
        self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in self.records], [("route_path_partial", "app/api.py:7", False)])

    def test_an_f_string_with_an_unknown_part_marks_only_that_part(self):
        api = self.api('f"/{settings.v}/items"', "from app import settings")
        self.repo({"files": tree(**{"app/api.py": api})})
        self.entry()
        self.assertTrue(self.route("GET /api/<unknown:settings.v>/items").partial)


class Unmounted(Base):
    LEGACY = f(["from fastapi import APIRouter", "", "legacy_router = APIRouter()", "", "", '@legacy_router.get("/old.csv")', "def old():", "    return 1"])

    def test_a_handler_on_a_router_nothing_mounts_claims_no_route_and_says_which_router(self):
        files = tree(**{"app/routers/legacy.py": self.LEGACY})
        self.repo({"files": files}, {"files": {"app/routers/legacy.py": self.LEGACY.replace("return 1", "return 2")}})
        self.entry()
        res = self.reach()
        self.assertEqual(res.reached, ())
        self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in self.records], [("router_unmounted", "app/routers/legacy.py:legacy_router", False)])
        self.assertEqual(self.labels(), [("GET /api/items", "list_items"), ("GET /api/items/{item_id}", "get_item")])

    def test_the_unmounted_router_is_not_recorded_for_a_change_that_does_not_reach_it(self):
        files = tree(**{"app/routers/legacy.py": self.LEGACY})
        self.repo({"files": files}, {"files": {"app/routers/items.py": ITEMS.replace("    return []", "    return [1]")}})
        self.entry()
        res = self.reach()
        self.assertEqual([r.area.label for r in res.reached], ["GET /api/items"])
        self.assertEqual(self.records, [])

    def test_a_router_mounted_into_an_unmounted_router_names_the_one_nothing_mounts(self):
        mid = f(["from fastapi import APIRouter", "", "from app.routers.items import router as items_router", "", "mid = APIRouter()", 'mid.include_router(items_router, prefix="/m")'])
        main = f(["from fastapi import FastAPI", "", "app = FastAPI()"])
        files = {"app/__init__.py": "", "app/routers/__init__.py": "", "app/main.py": main, "app/mid.py": mid, "app/routers/items.py": ITEMS}
        self.repo({"files": files}, {"files": {"app/routers/items.py": ITEMS.replace("    return []", "    return [1]")}})
        self.entry()
        res = self.reach()
        self.assertEqual(res.reached, ())
        self.assertEqual([r.subject for r in self.records], ["app/mid.py:mid"])

    def test_routers_that_mount_each_other_or_themselves_end_without_a_route_and_without_a_loop(self):
        a = f(["from fastapi import APIRouter", "", "from app.b import b", "", "a = APIRouter()", 'a.include_router(b, prefix="/b")', 'a.include_router(a, prefix="/self")', "", "", '@a.get("/x")', "def x():", "    return 1"])
        b = f(["from fastapi import APIRouter", "", "from app.a import a", "", "b = APIRouter()", 'b.include_router(a, prefix="/a")'])
        main = f(["from fastapi import FastAPI", "", "app = FastAPI()"])
        files = {"app/__init__.py": "", "app/main.py": main, "app/a.py": a, "app/b.py": b}
        self.repo({"files": files}, {"files": {"app/a.py": a.replace("return 1", "return 2")}})
        self.entry()
        self.assertEqual(self.fastapi.routes(None, self.sha("main")), [])
        res = self.reach()
        self.assertEqual(res.reached, ())
        self.assertEqual([r.kind for r in self.records], ["router_unmounted"])

    def test_a_mount_made_inside_a_function_is_recorded_as_unresolved_and_blocks(self):
        main = f(["from fastapi import FastAPI", "", "from app.api import api_router", "", "app = FastAPI()", "", "", "def setup():", '    app.include_router(api_router, prefix="/api")'])
        self.repo({"files": tree(**{"app/main.py": main})}, {"files": {"app/routers/items.py": ITEMS.replace("    return []", "    return [1]")}})
        self.entry()
        self.reach()
        self.assertIn(("mount_unresolved", "app/main.py:9", True), [(r.kind, r.subject, r.blocks_selection) for r in self.records])

    def test_a_mount_whose_router_cannot_be_resolved_is_recorded_and_blocks(self):
        main = f(["from fastapi import FastAPI", "", "from app.factory import make", "", "app = FastAPI()", 'app.include_router(make(), prefix="/x")'])
        self.repo({"files": tree(**{"app/main.py": main})}, {"files": {"app/routers/items.py": ITEMS.replace("    return []", "    return [1]")}})
        self.entry()
        self.reach()
        self.assertIn(("mount_unresolved", "app/main.py:6", True), [(r.kind, r.subject, r.blocks_selection) for r in self.records])


class RoutesChanged(Base):
    def test_a_removed_and_an_added_route_each_carry_the_evidence_of_their_own_version(self):
        old = ITEMS
        new = ITEMS.replace('@router.get("/{item_id}")', '@router.get("/by-id/{item_id}")')
        self.repo({"files": tree(**{"app/routers/items.py": old})}, {"files": {"app/routers/items.py": new}})
        entry = self.entry()
        removed, added = entry.routes_changed(self.sha("main~1"), self.sha("main"))
        self.assertEqual([r.label for r in removed], ["GET /api/items/{item_id}"])
        self.assertEqual([r.label for r in added], ["GET /api/items/by-id/{item_id}"])
        self.assertEqual({e.ref for e in removed[0].evidence}, {self.sha("main~1")})
        self.assertEqual({e.ref for e in added[0].evidence}, {self.sha("main")})
        problems = ss.evidence_problems({self.REPO: self.path}, [e for r in removed + added for e in r.evidence])
        self.assertEqual(problems, [])

    def test_a_changed_mount_prefix_removes_every_route_below_it_and_adds_them_under_the_new_one(self):
        self.repo({"files": tree()}, {"files": {"app/api.py": API.replace('prefix="/items"', 'prefix="/things"')}})
        entry = self.entry()
        removed, added = entry.routes_changed(self.sha("main~1"), self.sha("main"))
        self.assertEqual([r.label for r in removed], ["GET /api/items", "GET /api/items/{item_id}"])
        self.assertEqual([r.label for r in added], ["GET /api/things", "GET /api/things/{item_id}"])

    def test_nothing_is_computed_without_a_pre_change_version(self):
        self.repo({"files": tree()})
        self.assertEqual(self.entry().routes_changed(None, self.sha("main")), ([], []))

    def test_a_removed_handler_is_a_removed_route_and_reaches_no_area(self):
        new = f(["from fastapi import APIRouter", "", "router = APIRouter()", "", "", '@router.get("")', "def list_items():", "    return []"])
        self.repo({"files": tree()}, {"files": {"app/routers/items.py": new}})
        entry = self.entry()
        removed, added = entry.routes_changed(self.sha("main~1"), self.sha("main"))
        self.assertEqual(([r.label for r in removed], added), (["GET /api/items/{item_id}"], []))
        res = self.reach()
        self.assertEqual([r.area.label for r in res.reached], ["GET /api/items"])  # the handler that is left changed (its signature); the removed route is not an area


class DirectChanges(Base):
    def setUp(self):
        super().setUp()
        self.items = ITEMS

    def changed(self, new, **more):
        self.repo({"files": tree(**{"app/routers/items.py": self.items})}, {"files": {"app/routers/items.py": new, **more}})
        self.entry()
        return self.reach()

    def test_a_changed_handler_body_reaches_its_own_route_only(self):
        res = self.changed(self.items.replace("def get_item(request: Request, item_id: str):\n    return {}", "def get_item(request: Request, item_id: str):\n    return {1: 1}"))
        (r,) = res.reached
        a = r.area
        self.assertEqual((a.label, a.kind, a.module, a.also_mounted, a.priority), ("GET /api/items/{item_id}", "api_route", "", (), "must_test"))
        self.assertEqual([(e.path, e.line, e.detail) for e in a.chains[0]], [
            ("app/routers/items.py", 13, "    return {1: 1}"),
            ("app/routers/items.py", 11, '@router.get("/{item_id}")'),
            ("app/api.py", 6, 'api_router.include_router(items_router, prefix="/items")'),
            ("app/main.py", 6, 'app.include_router(api_router, prefix="/api")'),
        ])
        self.assertEqual(a.route_line.detail, '@router.get("/{item_id}")')
        self.assertEqual(r.routes[0].label, "GET /api/items/{item_id}")
        self.assertTrue(r.complete)

    def test_a_handler_line_identical_to_a_sibling_reaches_only_the_changed_handler(self):
        sib = self.items.replace("    return []", "    request.x = 1\n    return []").replace("    return {}", "    request.x = 1\n    return {}")
        self.items = sib
        res = self.changed(sib.replace("    request.x = 1\n    return {}", "    request.x = 2\n    return {}"))
        self.assertEqual([r.area.label for r in res.reached], ["GET /api/items/{item_id}"])

    def test_a_changed_decorator_line_reaches_the_route_and_the_chain_starts_at_it(self):
        res = self.changed(self.items.replace('@router.get("")', '@router.get("", tags=["x"])'))
        (r,) = res.reached
        self.assertEqual(r.area.label, "GET /api/items")
        # the changed line is the decorator itself: it is cited once, then the mounts
        self.assertEqual([(e.path, e.line) for e in r.area.chains[0]], [("app/routers/items.py", 6), ("app/api.py", 6), ("app/main.py", 6)])

    def test_a_changed_mount_line_reaches_every_route_below_it(self):
        self.repo({"files": tree()}, {"files": {"app/api.py": API.replace('prefix="/items"', 'prefix="/things"')}})
        self.entry()
        res = self.reach()
        self.assertEqual([r.area.label for r in res.reached], ["GET /api/things", "GET /api/things/{item_id}"])
        first = res.reached[0].area.chains[0]
        self.assertEqual((first[0].path, first[0].line), ("app/api.py", 6))

    def test_a_changed_import_reaches_the_handlers_that_use_what_it_binds(self):
        items = ITEMS.replace("from fastapi import APIRouter, Request", "from fastapi import APIRouter, Request\nfrom app.helpers import clean").replace("    return []", "    return clean([])")
        self.items = items
        res = self.changed(items.replace("from app.helpers import clean", "from app.helpers2 import clean"), **{"app/helpers.py": "", "app/helpers2.py": ""})
        self.assertEqual([r.area.label for r in res.reached], ["GET /api/items"])  # only list_items uses `clean`

    def test_a_changed_statement_outside_every_declaration_reaches_every_route_of_the_file(self):
        res = self.changed(self.items.replace("router = APIRouter()", "router = APIRouter()\nCACHE = {}\nsetup(CACHE)"))
        self.assertEqual([r.area.label for r in res.reached], ["GET /api/items", "GET /api/items/{item_id}"])

    def test_a_changed_import_in_a_mount_file_reaches_only_the_routes_it_mounts(self):
        main = MAIN.replace("from fastapi import FastAPI", "from fastapi import FastAPI\nimport logging")
        self.repo({"files": tree()}, {"files": {"app/main.py": main}})
        self.entry()
        self.assertEqual(self.reach().reached, ())  # `logging` is not used by a mount
        api = API.replace("from app.routers.items import router as items_router", "from app.routers.items2 import router as items_router")
        self.repo({"files": tree()}, {"files": {"app/api.py": api, "app/routers/items2.py": ITEMS}})
        self.entry()
        self.assertEqual([r.area.label for r in self.reach().reached], ["GET /api/items", "GET /api/items/{item_id}"])  # `items_router` is what api.py mounts

    def test_a_changed_router_constructor_reaches_every_route_on_it(self):
        res = self.changed(self.items.replace("router = APIRouter()", "router = APIRouter(tags=['x'])"))
        self.assertEqual([r.area.label for r in res.reached], ["GET /api/items", "GET /api/items/{item_id}"])

    def test_an_added_handler_is_a_route_and_only_the_added_one(self):
        res = self.changed(self.items + '\n\n@router.get("/{item_id}/pdf")\ndef pdf(request: Request, item_id: str):\n    return b""\n')
        self.assertEqual([r.area.label for r in res.reached], ["GET /api/items/{item_id}/pdf"])


class ThroughTheWalk(Base):
    SERVICE = f(["def total(x):", "    return x", "", "", "def other(x):", "    return x"])

    def files(self):
        items = f([
            "from fastapi import APIRouter", "", "from app.services.calc import total", "from app.services.helper import wrap", "", "router = APIRouter()", "", "",
            '@router.get("/direct")', "def direct():", "    return total(1)", "", "", "def local_helper():", "    return total(2)", "", "",
            '@router.get("/via-helper")', "def via_helper():", "    return local_helper()", "", "", '@router.get("/unrelated")', "def unrelated():", "    return wrap(1)",
        ])
        helper = f(["from app.services.calc import other", "", "", "def wrap(x):", "    return other(x)"])
        return tree(**{"app/routers/items.py": items, "app/services/__init__.py": "", "app/services/calc.py": self.SERVICE, "app/services/helper.py": helper})

    def test_a_changed_service_function_reaches_the_handlers_that_call_it_and_the_ones_that_call_a_helper_of_them(self):
        files = self.files()
        self.repo({"files": files}, {"files": {"app/services/calc.py": self.SERVICE.replace("def total(x):\n    return x", "def total(x):\n    return x + 1")}})
        self.entry()
        res = self.reach()
        self.assertEqual([r.area.label for r in res.reached], ["GET /api/items/direct", "GET /api/items/via-helper"])
        a = res.reached[0].area
        self.assertEqual([(e.path, e.line) for e in a.chains[0]], [("app/routers/items.py", 3), ("app/routers/items.py", 9), ("app/api.py", 6), ("app/main.py", 6)])
        self.assertTrue(res.walk.complete)
        self.assertEqual(self.records, [])

    def test_a_handler_that_calls_a_function_that_did_not_change_is_not_reached(self):
        files = self.files()
        self.repo({"files": files}, {"files": {"app/services/calc.py": self.SERVICE.replace("def other(x):\n    return x", "def other(x):\n    return x + 1")}})
        self.entry()
        res = self.reach()
        self.assertEqual([r.area.label for r in res.reached], ["GET /api/items/unrelated"])  # wrap calls other

    def test_the_entry_files_are_the_files_that_declare_handlers_routers_or_mounts(self):
        self.repo({"files": self.files()})
        entry = self.entry()
        sha = self.sha("main")
        self.assertEqual([p for p in ("app/routers/items.py", "app/api.py", "app/main.py", "app/services/calc.py", "app/services/helper.py") if entry.is_entry_file(p, sha)],
                         ["app/routers/items.py", "app/api.py", "app/main.py"])


class Completeness(Base):
    def test_a_candidate_that_does_not_parse_blocks_and_claims_nothing_from_it(self):
        broken = "from fastapi import APIRouter\nrouter = APIRouter()\n@router.get('/x')\ndef x(:\n    pass\n"
        self.repo({"files": tree(**{"app/routers/broken.py": broken})}, {"files": {"app/routers/items.py": ITEMS.replace("    return []", "    return [1]")}})
        self.entry()
        res = self.reach()
        self.assertEqual([r.area.label for r in res.reached], ["GET /api/items"])
        self.assertFalse(res.reached[0].complete)
        self.assertIn(("parse_degraded", "app/routers/broken.py", True), [(r.kind, r.subject, r.blocks_selection) for r in self.records])

    def test_a_search_that_was_cut_makes_the_result_incomplete_not_empty(self):
        self.repo({"files": tree()}, {"files": {"app/routers/items.py": ITEMS.replace("    return []", "    return [1]")}})
        self.entry(examine_cap=1)
        res = self.reach()
        self.assertFalse(res.complete)  # whatever was or was not found: it is not "no route"
        self.assertTrue(any("search" in why for why in res.reasons), res.reasons)
        self.assertIn("search_truncated", {r.kind for r in self.records})
        self.assertTrue(self.fastapi.problems)

    def test_a_whole_search_gives_a_complete_result(self):
        self.repo({"files": tree()}, {"files": {"app/routers/items.py": ITEMS.replace("    return []", "    return [1]")}})
        self.entry()
        res = self.reach()
        self.assertEqual(([r.area.label for r in res.reached], res.complete, res.reasons), (["GET /api/items"], True, ()))


class WhatAChangeReachesInAnEntryFile(Base):
    """Undecidable means affected: a change in a handler or mount file reaches the routes that use what changed, or, when the
    whole module is reached (a statement that runs when it loads), every route in or through the file."""

    def run_case(self, files, changed):
        self.repo({"files": files}, {"files": changed})
        self.entry()
        return self.reach()

    def test_a_helper_in_a_router_file_reaches_the_handlers_that_call_it(self):
        items = f(["from fastapi import APIRouter", "", "router = APIRouter()", "", "", "def helper(x):", "    return x", "", "",
                   '@router.get("/a")', "def a():", "    return helper(1)", "", "", '@router.get("/b")', "def b():", "    return 2"])
        res = self.run_case(tree(**{"app/routers/items.py": items}), {"app/routers/items.py": items.replace("def helper(x):\n    return x", "def helper(x):\n    return x + 1")})
        self.assertEqual([r.area.label for r in res.reached], ["GET /api/items/a"])

    def test_a_helper_of_a_handler_file_that_another_router_file_imports_reaches_the_routes_there(self):
        items = f(["from fastapi import APIRouter", "", "router = APIRouter()", "", "", "def shared(x):", "    return x", "", "", '@router.get("/a")', "def a():", "    return 1"])
        other = f(["from fastapi import APIRouter", "from app.routers.items import shared", "", "other_router = APIRouter()", "", "", '@other_router.get("/o")', "def o():", "    return shared(1)"])
        api = API + "from app.routers.other import other_router\n" + 'api_router.include_router(other_router, prefix="/other")\n'
        res = self.run_case(tree(**{"app/routers/items.py": items, "app/routers/other.py": other, "app/api.py": api}),
                            {"app/routers/items.py": items.replace("def shared(x):\n    return x", "def shared(x):\n    return x + 1")})
        self.assertEqual([r.area.label for r in res.reached], ["GET /api/other/o"])  # `a` does not use shared; the router itself only wires the file up
        self.assertEqual(res.walk.chains[-1].files, ("app/routers/items.py", "app/routers/other.py"))

    def test_a_changed_constant_used_as_a_mount_prefix_reaches_every_route_under_the_mount(self):
        api = f(["from fastapi import APIRouter", "", "from app.routers.items import router as items_router", "", 'ITEMS = "/items"', "",
                 "api_router = APIRouter()", "api_router.include_router(items_router, prefix=ITEMS)"])
        res = self.run_case(tree(**{"app/api.py": api}), {"app/api.py": api.replace('ITEMS = "/items"', 'ITEMS = "/things"')})
        self.assertEqual([r.area.label for r in res.reached], ["GET /api/things", "GET /api/things/{item_id}"])
        self.assertTrue(res.walk.chains[0].reach.whole)

    def test_a_dependency_of_a_mount_reaches_every_route_under_it(self):
        deps = f(["def verify():", "    return 1"])
        api = f(["from fastapi import APIRouter, Depends", "", "from app.deps import verify", "from app.routers.items import router as items_router", "",
                 "api_router = APIRouter()", 'api_router.include_router(items_router, prefix="/items", dependencies=[Depends(verify)])'])
        res = self.run_case(tree(**{"app/api.py": api, "app/deps.py": deps}), {"app/deps.py": deps.replace("return 1", "return 2")})
        self.assertEqual([r.area.label for r in res.reached], ["GET /api/items", "GET /api/items/{item_id}"])

    def test_a_statement_that_runs_when_a_handler_file_loads_reaches_every_route_of_it(self):
        deps = f(["def setup():", "    return 1"])
        items = ITEMS.replace("router = APIRouter()", "from app.deps import setup\nrouter = APIRouter()\nsetup()")
        res = self.run_case(tree(**{"app/routers/items.py": items, "app/deps.py": deps}), {"app/deps.py": deps.replace("return 1", "return 2")})
        self.assertEqual([r.area.label for r in res.reached], ["GET /api/items", "GET /api/items/{item_id}"])

    def test_an_app_level_dependency_reaches_every_route_of_the_app(self):
        deps = f(["def verify():", "    return 1"])
        main = f(["from fastapi import FastAPI, Depends", "", "from app.api import api_router", "from app.deps import verify", "", "app = FastAPI(dependencies=[Depends(verify)])", 'app.include_router(api_router, prefix="/api")'])
        res = self.run_case(tree(**{"app/main.py": main, "app/deps.py": deps}), {"app/deps.py": deps.replace("return 1", "return 2")})
        self.assertEqual([r.area.label for r in res.reached], ["GET /api/items", "GET /api/items/{item_id}"])

    def test_middleware_added_to_the_app_reaches_every_route_of_the_app(self):
        mw = f(["class M:", "    def __init__(self, app):", "        self.app = app"])
        main = MAIN + "from app.mw import M\napp.add_middleware(M)\n"
        res = self.run_case(tree(**{"app/main.py": main, "app/mw.py": mw}), {"app/mw.py": mw.replace("self.app = app", "self.app = app\n        self.y = 1")})
        self.assertEqual([r.area.label for r in res.reached], ["GET /api/items", "GET /api/items/{item_id}"])

    def test_a_router_level_dependency_reaches_every_handler_on_the_router(self):
        deps = f(["def auth():", "    return 1"])
        items = f(["from fastapi import APIRouter, Depends", "from app.deps import auth", "", "router = APIRouter(dependencies=[Depends(auth)])", "", "",
                   '@router.get("/a")', "def a():", "    return 1", "", "", '@router.get("/b")', "def b():", "    return 2"])
        res = self.run_case(tree(**{"app/routers/items.py": items, "app/deps.py": deps}), {"app/deps.py": deps.replace("return 1", "return 2")})
        self.assertEqual([r.area.label for r in res.reached], ["GET /api/items/a", "GET /api/items/b"])


class FindingRouters(Base):
    def test_a_router_imported_under_another_name_and_decorated_in_a_file_that_names_none_of_the_patterns(self):
        core = f(["from fastapi import APIRouter", "", "router = APIRouter()"])
        use = f(["from app.core import router as r", "", "", '@r.get("/hi")', "def hi():", "    return 1"])
        main = f(["from fastapi import FastAPI", "", "from app.core import router", "import app.use", "", "app = FastAPI()", 'app.include_router(router, prefix="/x")'])
        files = {"app/__init__.py": "", "app/core.py": core, "app/use.py": use, "app/main.py": main}
        self.repo({"files": files}, {"files": {"app/use.py": use.replace("return 1", "return 2")}})
        entry = self.entry()
        self.assertEqual(self.labels(), [("GET /x/hi", "hi")])
        res = self.reach()
        self.assertEqual([r.area.label for r in res.reached], ["GET /x/hi"])

    def test_the_importers_of_a_router_file_that_cannot_be_searched_make_the_table_incomplete(self):
        from unittest import mock

        from reachmap.adapters import ImporterResult

        self.repo({"files": tree()}, {"files": {"app/routers/items.py": ITEMS.replace("    return []", "    return [1]")}})
        self.entry()
        real = self.language.importers

        def cut(path, sha, record_to=None):
            res = real(path, sha, record_to)
            return ImporterResult(res.target, res.sha, res.edges, False, ("simulated cut",), res.candidates)

        with mock.patch.object(self.language, "importers", cut):
            res = self.reach()
        self.assertFalse(res.complete)
        self.assertTrue(any("simulated cut" in why for why in res.reasons))


class UnknownMethods(Base):
    def test_a_methods_list_that_is_not_literal_is_marked_and_recorded(self):
        items = f(["from fastapi import APIRouter", "router = APIRouter()", "", '@router.api_route("/v", methods=METHODS)', "def v():", "    return 1"])
        self.repo({"files": tree(**{"app/routers/items.py": items})}, {"files": {"app/routers/items.py": items.replace("return 1", "return 2")}})
        self.entry()
        (route,) = self.fastapi.routes(None, self.sha("main"))
        self.assertEqual((route.method, route.partial, route.label), ("<unknown:METHODS>", True, "<unknown:METHODS> /api/items/v"))
        res = self.reach()
        self.assertEqual([r.area.label for r in res.reached], ["<unknown:METHODS> /api/items/v"])
        self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in self.records], [("route_path_partial", "app/routers/items.py:4", False)])


class Joining(Base):
    def test_slashes_are_joined_as_the_framework_joins_them(self):
        items = f(["from fastapi import APIRouter", "router = APIRouter()", "", '@router.get("/")', "def root():", "    pass", "", '@router.get("")', "def empty():", "    pass", "", '@router.get("/x/")', "def x():", "    pass"])
        main = f(["from fastapi import FastAPI", "", "from app.routers.items import router", "", "app = FastAPI()", 'app.include_router(router, prefix="/api/")'])
        self.repo({"files": {"app/__init__.py": "", "app/routers/__init__.py": "", "app/main.py": main, "app/routers/items.py": items}})
        self.assertEqual(sorted(r[0] for r in self.labels()), ["GET /api", "GET /api", "GET /api/x"])

    def test_the_constructor_line_is_in_the_evidence_only_when_the_router_has_a_prefix(self):
        self.repo({"files": tree()})
        self.entry()
        self.assertEqual(len(self.route("GET /api/items").evidence), 3)  # decorator and two mounts: no router has a prefix


class UnmountedAndUnresolved(Base):
    def test_a_router_that_might_be_the_one_an_unresolved_mount_carries_is_not_called_unmounted(self):
        api = f(["from fastapi import APIRouter", "", "from app.routers.items import router as items_router", "",
                 "api_router = APIRouter()", "for r in [items_router]:", '    api_router.include_router(r, prefix="/items")'])
        self.repo({"files": tree(**{"app/api.py": api})}, {"files": {"app/routers/items.py": ITEMS.replace("    return []", "    return [1]")}})
        self.entry()
        res = self.reach()
        kinds = [(r.kind, r.blocks_selection) for r in self.records]
        self.assertIn(("mount_unresolved", True), kinds)
        self.assertNotIn("router_unmounted", [k for k, _b in kinds])  # that claim cannot be made while a mount is unresolved
        self.assertFalse(res.complete)
        self.assertEqual(res.reached, ())


if __name__ == "__main__":
    unittest.main()
