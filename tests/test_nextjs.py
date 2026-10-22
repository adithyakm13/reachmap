"""The Next.js entry-point adapter on small repositories: screens from `app/` and `pages/` (a page reached by an import or by an
edit of its own file), wrappers that reach every page below them (layouts and their kin, `_app`, `_document`, middleware), route
handlers per HTTP method, a Pages Router API file, entry files that import entry files, the route table and what it is made
of, and what the adapter says when it cannot read what it needs.

The file-system routing itself (a pure function of the paths) is in test_nextjs_routes.py, the M16 fixture scenarios in
test_nextjs_scenarios.py.
"""

import unittest
from unittest import mock

from reachmap import config, entrypoints
from reachmap.adapters.js import JsAdapter
from reachmap.adapters.nextjs import NextJsAdapter
from reachmap.model import ReadResult
from tests import scenario_support as ss
from tests.support_universe import Base as UniverseBase
from tests.support_universe import f

TSCONFIG = '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}\n'


def page(name, ret="1"):
    return f([f"export default function {name}() {{", f"  return {ret};", "}"])


def dep(name="dep", ret="1"):
    return f([f"export function {name}() {{", f"  return {ret};", "}"])


def uses(name, comp, path, ret=None):
    """A page (default export on line 3) that imports a default-exported component (import on line 1)."""
    return f([f'import {comp} from "{path}";', "", f"export default function {name}() {{", f"  return {ret or f'<{comp} />'};", "}"])


def comp(name, dep_path="@/dep", dep_name="dep"):
    """A component (import on line 1) that calls a function of dep.ts."""
    return f([f'import {{ {dep_name} }} from "{dep_path}";', "", f"export default function {name}() {{", f"  return {dep_name}();", "}"])


def layout(name, ret=None):
    """A layout: the default export on line 1 and the line that renders its children on line 4."""
    return f([f"export default function {name}({{ children }}: {{ children: React.ReactNode }}) {{", "  return (", f"    <main>{ret or ''}", "      {children}", "    </main>", "  );", "}"])


def route(*methods, helper="dep", imp='import { dep } from "@/dep";'):
    """A route file that imports dep.ts (line 1) and exports each method as `export async function M()`."""
    body = []
    for m in methods:
        body += [f"export async function {m}() {{", f"  return {helper}();", "}", ""]
    return f([imp, ""] + body)


class Base(UniverseBase):
    UNIVERSE_PREFIX = "reachmap-nextjs-"
    REPO = "t/shop"

    def make_adapter(self, **kw):
        kw.setdefault("test_markers", ["__tests__/", ".test.", ".spec."])
        kw.setdefault("aliases_from", "tsconfig.json")
        return JsAdapter(self.REPO, self.url, self.git, **kw)

    def entry(self, **kw):
        self.language = self.make_adapter()
        self.next = NextJsAdapter(self.REPO, self.language, **kw)
        return self.next

    def reach(self, **kw):
        analysis = self.analyze(self.language)
        res = entrypoints.reach(analysis, self.language, self.next, **kw)
        evidence = [e for r in res.reached for c in r.area.chains for e in c]
        self.assertEqual(ss.evidence_problems({self.REPO: self.path}, evidence), [])
        return res

    def run_case(self, files, changed, **kw):
        self.repo({"files": files}, {"files": changed} if not isinstance(changed, dict) or "files" not in changed else changed)
        self.entry(**kw)
        return self.reach()

    def labels(self, res):
        return [r.area.label for r in res.reached]

    def chain(self, res, label, n=0):
        (r,) = [r for r in res.reached if r.area.label == label]
        return [(e.path, e.line) for e in r.area.chains[n]]

    def records_of(self, kind):
        return [(r.subject, r.blocks_selection) for r in self.records if r.kind == kind]


class ScreensByImport(Base):
    FILES = {
        "tsconfig.json": TSCONFIG,
        "src/dep.ts": dep(),
        "src/components/Badge.tsx": comp("Badge"),
        "src/app/(shop)/products/page.tsx": uses("ProductsPage", "Badge", "@/components/Badge"),
        "src/app/(shop)/products/[slug]/page.tsx": uses("ProductPage", "Badge", "../../../../components/Badge"),
        "src/app/(shop)/cart/page.tsx": page("CartPage"),
        "src/pages/legacy/[id].tsx": uses("LegacyItem", "Badge", "@/components/Badge"),
        "src/pages/legacy/index.tsx": page("LegacyHome"),
    }

    def go(self):
        return self.run_case(self.FILES, {"src/dep.ts": dep(ret="2")})

    def test_a_shared_component_reaches_the_pages_of_both_routers_by_both_spellings(self):
        res = self.go()
        self.assertEqual(self.labels(res), ["/legacy/:id", "/products", "/products/:slug"])
        self.assertTrue(res.complete)

    def test_the_chain_is_the_imports_then_the_pages_default_export(self):
        res = self.go()
        self.assertEqual(self.chain(res, "/products"), [
            ("src/components/Badge.tsx", 1), ("src/app/(shop)/products/page.tsx", 1), ("src/app/(shop)/products/page.tsx", 3)])
        self.assertEqual(self.chain(res, "/products/:slug"), [
            ("src/components/Badge.tsx", 1), ("src/app/(shop)/products/[slug]/page.tsx", 1), ("src/app/(shop)/products/[slug]/page.tsx", 3)])
        self.assertEqual(self.chain(res, "/legacy/:id"), [
            ("src/components/Badge.tsx", 1), ("src/pages/legacy/[id].tsx", 1), ("src/pages/legacy/[id].tsx", 3)])

    def test_a_screen_has_its_route_the_module_and_the_route_line(self):
        res = self.go()
        by = {r.area.label: r for r in res.reached}
        r = by["/products/:slug"]
        self.assertEqual((r.area.kind, r.area.module, r.area.also_mounted, r.area.by_name), ("screen", "shop", (), False))
        self.assertEqual((r.area.route_line.path, r.area.route_line.line), ("src/app/(shop)/products/[slug]/page.tsx", 3))
        (route,) = r.routes
        self.assertEqual((route.kind, route.method, route.path, route.handler, route.target, route.partial),
                         ("screen", "", "/products/:slug", "ProductPage", "src/app/(shop)/products/[slug]/page.tsx", False))
        self.assertEqual(route.evidence[0].claim, "route:/products/:slug:src/app/(shop)/products/[slug]/page.tsx")
        self.assertEqual(by["/legacy/:id"].area.module, "legacy")

    def test_pages_that_do_not_use_the_component_are_not_reached(self):
        res = self.go()
        self.assertNotIn("/cart", self.labels(res))
        self.assertNotIn("/legacy", self.labels(res))

    def test_the_priority_is_by_the_count_of_screens(self):
        files = {"tsconfig.json": TSCONFIG, "src/dep.ts": dep(), "src/components/Badge.tsx": comp("Badge")}
        for i in range(9):
            files[f"src/app/p{i}/page.tsx"] = uses(f"P{i}", "Badge", "@/components/Badge")
        res = self.run_case(files, {"src/dep.ts": dep(ret="2")})
        self.assertEqual((len(res.reached), {r.area.priority for r in res.reached}), (9, {"sample"}))
        files.pop("src/app/p8/page.tsx")
        res = self.run_case(files, {"src/dep.ts": dep(ret="2")})
        self.assertEqual((len(res.reached), {r.area.priority for r in res.reached}), (8, {"must_test"}))

    def test_a_named_import_is_narrowed_by_the_names_that_arrive(self):
        lib = f(["export function used() {", "  return 1;", "}", "", "export function other() {", "  return 2;", "}"])
        files = {"tsconfig.json": TSCONFIG, "src/lib.ts": lib,
                 "src/app/a/page.tsx": f(['import { used } from "@/lib";', "", "export default function A() {", "  return used();", "}"]),
                 "src/app/b/page.tsx": f(['import { other } from "@/lib";', "", "export default function B() {", "  return other();", "}"])}
        res = self.run_case(files, {"src/lib.ts": lib.replace("return 1;", "return 3;")})
        self.assertEqual(self.labels(res), ["/a"])

    def test_a_page_in_a_private_folder_is_not_a_screen(self):
        files = {"tsconfig.json": TSCONFIG, "src/dep.ts": dep(), "src/components/Badge.tsx": comp("Badge"),
                 "src/app/_internal/page.tsx": uses("Hidden", "Badge", "@/components/Badge"),
                 "src/app/real/page.tsx": uses("Real", "Badge", "@/components/Badge")}
        res = self.run_case(files, {"src/dep.ts": dep(ret="2")})
        self.assertEqual(self.labels(res), ["/real"])

    def test_a_folder_called_test_in_the_app_folder_is_a_url_segment_and_its_page_a_screen(self):
        files = {"src/app/test/page.tsx": page("TestPage"), "src/app/api/tests/route.ts": route("GET", imp=""), "src/pages/a.test.tsx": page("T"), "src/pages/__tests__/b.tsx": page("B")}
        self.repo({"files": files})
        routes = self.entry().routes(None, self.sha("main"))
        self.assertEqual([(r.kind, r.path) for r in routes], [("api_route", "/api/tests"), ("screen", "/test")])

    def test_a_test_file_is_a_unit_test_and_not_a_screen(self):
        files = {"tsconfig.json": TSCONFIG, "src/dep.ts": dep(), "src/app/a/page.tsx": uses("A", "dep", "@/dep", "dep()").replace("import dep from", "import { dep } from"),
                 "src/pages/a.test.tsx": uses("ATest", "dep", "@/dep", "dep()").replace("import dep from", "import { dep } from")}
        res = self.run_case(files, {"src/dep.ts": dep(ret="2")})
        self.assertEqual(self.labels(res), ["/a"])
        self.assertEqual([t.path for i in self.analysis.impacts for t in i.unit_tests], ["src/pages/a.test.tsx"])


class DirectChange(Base):
    def test_a_change_inside_a_page_reaches_its_screen_and_the_chain_starts_at_the_changed_line(self):
        files = {"src/app/a/page.tsx": page("A"), "src/app/b/page.tsx": page("B")}
        res = self.run_case(files, {"src/app/a/page.tsx": page("A", "2")})
        self.assertEqual(self.labels(res), ["/a"])
        self.assertEqual(self.chain(res, "/a"), [("src/app/a/page.tsx", 2), ("src/app/a/page.tsx", 1)])

    def test_a_page_with_an_optional_catch_all_is_one_screen_with_a_second_path(self):
        files = {"src/app/help/[[...topic]]/page.tsx": page("Help")}
        res = self.run_case(files, {"src/app/help/[[...topic]]/page.tsx": page("Help", "2")})
        (r,) = res.reached
        self.assertEqual((r.area.label, r.area.also_mounted, [x.path for x in r.routes], len(r.area.chains)), ("/help", ("/help/*",), ["/help", "/help/*"], 1))

    def test_a_catch_all_is_a_trailing_star(self):
        files = {"src/app/docs/[...slug]/page.tsx": page("Docs")}
        self.assertEqual(self.labels(self.run_case(files, {"src/app/docs/[...slug]/page.tsx": page("Docs", "2")})), ["/docs/*"])

    def test_a_page_in_a_slot_and_the_page_of_its_folder_are_one_screen_with_a_chain_each(self):
        files = {"src/app/dashboard/page.tsx": page("Dashboard"), "src/app/dashboard/@stats/page.tsx": page("Stats"), "src/app/other/page.tsx": page("Other")}
        res = self.run_case(files, {"src/app/dashboard/page.tsx": page("Dashboard", "2"), "src/app/dashboard/@stats/page.tsx": page("Stats", "2")})
        (r,) = res.reached
        self.assertEqual((r.area.label, len(r.area.chains), sorted(x.file for x in r.routes)), ("/dashboard", 2, ["src/app/dashboard/@stats/page.tsx", "src/app/dashboard/page.tsx"]))

    def test_only_the_slot_page_changing_reaches_the_screen_of_its_url(self):
        files = {"src/app/dashboard/page.tsx": page("Dashboard"), "src/app/dashboard/@stats/page.tsx": page("Stats")}
        res = self.run_case(files, {"src/app/dashboard/@stats/page.tsx": page("Stats", "2")})
        (r,) = res.reached
        self.assertEqual((r.area.label, len(r.area.chains), [x.file for x in r.routes]), ("/dashboard", 1, ["src/app/dashboard/@stats/page.tsx"]))

    def test_an_intercepting_page_is_the_screen_of_the_url_it_intercepts_and_is_recorded_as_partial(self):
        files = {"src/app/photos/[id]/page.tsx": page("Photo"), "src/app/feed/page.tsx": page("Feed"),
                 "src/app/feed/@modal/(..)photos/[id]/page.tsx": page("PhotoModal")}
        res = self.run_case(files, {"src/app/feed/@modal/(..)photos/[id]/page.tsx": page("PhotoModal", "2")})
        (r,) = res.reached
        self.assertEqual((r.area.label, [(x.file, x.partial) for x in r.routes]), ("/photos/:id", [("src/app/feed/@modal/(..)photos/[id]/page.tsx", True)]))
        self.assertEqual(self.records_of("route_intercepted"), [("src/app/feed/@modal/(..)photos/[id]/page.tsx", False)])

    def test_an_intercepting_page_and_the_page_it_intercepts_are_one_screen(self):
        files = {"src/app/photos/[id]/page.tsx": page("Photo"), "src/app/feed/@modal/(..)photos/[id]/page.tsx": page("PhotoModal")}
        res = self.run_case(files, {"src/app/photos/[id]/page.tsx": page("Photo", "2"), "src/app/feed/@modal/(..)photos/[id]/page.tsx": page("PhotoModal", "2")})
        (r,) = res.reached
        self.assertEqual((r.area.label, len(r.area.chains), [x.partial for x in r.routes]), ("/photos/:id", 2, [False, True]))

    def test_a_folder_name_that_is_not_a_route_part_is_kept_and_recorded(self):
        files = {"src/app/post-[id]/page.tsx": page("Post")}
        res = self.run_case(files, {"src/app/post-[id]/page.tsx": page("Post", "2")})
        self.assertEqual(self.labels(res), ["/<unknown:post-[id]>"])
        self.assertTrue(res.reached[0].routes[0].partial)
        self.assertEqual(self.records_of("route_path_partial"), [("src/app/post-[id]/page.tsx:1", False)])

    def test_a_page_that_does_not_lex_is_still_a_screen_and_says_so(self):
        broken = 'export default function A() {\n  return "unterminated;\n}\n'
        res = self.run_case({"src/app/a/page.tsx": page("A")}, {"src/app/a/page.tsx": broken})
        self.assertEqual(self.labels(res), ["/a"])
        self.assertFalse(res.reached[0].complete)
        self.assertFalse(res.complete)
        self.assertIn(("src/app/a/page.tsx", False), self.records_of("parse_degraded"))


class Wrappers(Base):
    FILES = {
        "tsconfig.json": TSCONFIG,
        "src/dep.ts": dep(),
        "src/components/Nav.tsx": comp("Nav"),
        "src/app/layout.tsx": layout("RootLayout"),
        "src/app/page.tsx": page("Home"),
        "src/app/(shop)/layout.tsx": f(['import Nav from "@/components/Nav";', "", "export default function ShopLayout({ children }: { children: React.ReactNode }) {", "  return (", "    <div>", "      <Nav />", "      {children}", "    </div>", "  );", "}"]),
        "src/app/(shop)/cart/page.tsx": page("Cart"),
        "src/app/(shop)/products/page.tsx": page("Products"),
        "src/app/(marketing)/about/page.tsx": page("About"),
        "src/app/api/ping/route.ts": route("GET", imp=""),
        "src/pages/legacy.tsx": page("Legacy"),
        "src/pages/_app.tsx": f(["export default function MyApp({ Component, pageProps }: any) {", "  return (", "    <div>", "      <Component {...pageProps} />", "    </div>", "  );", "}"]),
        "src/pages/_document.tsx": f(["export default function Document() {", "  return (", "    <html>", "      <Main />", "    </html>", "  );", "}"]),
    }

    def go(self, changed, files=None):
        return self.run_case(files or self.FILES, changed)

    def test_a_changed_layout_reaches_every_page_below_its_folder_and_says_how(self):
        res = self.go({"src/app/(shop)/layout.tsx": self.FILES["src/app/(shop)/layout.tsx"].replace("<div>", '<div className="x">')})
        self.assertEqual(self.labels(res), ["/cart", "/products"])
        # the changed line, the line of the layout that renders its children, the page's default export
        self.assertEqual(self.chain(res, "/cart"), [("src/app/(shop)/layout.tsx", 5), ("src/app/(shop)/layout.tsx", 7), ("src/app/(shop)/cart/page.tsx", 1)])
        claims = [e.claim for e in res.reached[0].area.chains[0]]
        self.assertEqual(claims[1], "wraps:src/app/(shop)/layout.tsx")
        self.assertEqual(claims[2], "route:/cart:src/app/(shop)/cart/page.tsx")

    def test_the_run_says_that_one_change_reaches_every_page_below_the_layout(self):
        res = self.go({"src/app/(shop)/layout.tsx": self.FILES["src/app/(shop)/layout.tsx"].replace("<div>", '<div className="x">')})
        self.assertEqual(len(res.notes), 1)
        self.assertIn("src/app/(shop)/layout.tsx wraps the 2 screen(s) below its folder", res.notes[0])
        self.assertIn("folder tree", res.notes[0])
        res = self.go({"src/dep.ts": dep(ret="2")})  # reached by an import of the layout: the same sentence
        self.assertEqual(len(res.notes), 1)
        res = self.go({"src/app/(shop)/products/page.tsx": page("Products", "2")})  # a page is no wrapper: nothing to say
        self.assertEqual(res.notes, ())

    def test_the_notes_for_app_document_and_middleware(self):
        res = self.go({"src/pages/_app.tsx": self.FILES["src/pages/_app.tsx"].replace("<div>", '<div className="x">')})
        self.assertEqual(res.notes, ("src/pages/_app.tsx wraps every page of the Pages Router (1 screen(s)): a change to it reaches each of them; each chain runs through the line of the "
                                     "wrapper that renders the page and on to the page's default export",))
        files = dict(self.FILES)
        files["src/middleware.ts"] = f(["export function middleware() {", "  return 1;", "}"])
        res = self.go({"src/middleware.ts": f(["export function middleware() {", "  return 2;", "}"])}, files)
        self.assertIn("matcher is not evaluated", res.notes[0])

    def test_a_removed_layout_says_so(self):
        self.repo({"files": {"src/app/layout.tsx": layout("Root"), "src/app/a/page.tsx": page("A")}}, {"delete": ["src/app/layout.tsx"]})
        self.entry()
        res = self.reach()
        self.assertEqual(len(res.notes), 1)
        self.assertIn("was removed", res.notes[0])

    def test_a_layout_does_not_reach_a_route_handler_or_the_pages_of_the_other_router_or_another_group(self):
        res = self.go({"src/app/(shop)/layout.tsx": self.FILES["src/app/(shop)/layout.tsx"].replace("<div>", '<div className="x">')})
        self.assertEqual({r.area.kind for r in res.reached}, {"screen"})
        for gone in ("/about", "/legacy", "/"):
            self.assertNotIn(gone, self.labels(res))

    def test_the_root_layout_reaches_every_app_router_page_and_the_priority_follows_the_count(self):
        files = dict(self.FILES)
        for i in range(8):
            files[f"src/app/extra{i}/page.tsx"] = page(f"E{i}")
        res = self.go({"src/app/layout.tsx": layout("RootLayout", " ")}, files)
        self.assertEqual(len(res.reached), 12)  # /, /about, /cart, /products and eight more: not the Pages Router page, not the handler
        self.assertEqual({r.area.priority for r in res.reached}, {"sample"})
        self.assertEqual(self.chain(res, "/about"), [("src/app/layout.tsx", 3), ("src/app/layout.tsx", 4), ("src/app/(marketing)/about/page.tsx", 1)])

    def test_the_root_layout_reaches_few_pages_as_must_test(self):
        res = self.go({"src/app/layout.tsx": layout("RootLayout", " ")})
        self.assertEqual((self.labels(res), {r.area.priority for r in res.reached}), (["/", "/about", "/cart", "/products"], {"must_test"}))

    def test_a_layout_reached_by_an_import_reaches_its_pages_through_the_import_and_the_children_line(self):
        res = self.go({"src/dep.ts": dep(ret="2")})
        self.assertEqual(self.labels(res), ["/cart", "/products"])
        self.assertEqual(self.chain(res, "/cart"), [
            ("src/components/Nav.tsx", 1), ("src/app/(shop)/layout.tsx", 1), ("src/app/(shop)/layout.tsx", 7), ("src/app/(shop)/cart/page.tsx", 1)])

    def test_a_layout_with_no_page_below_it_reaches_nothing(self):
        files = {"src/app/empty/layout.tsx": layout("Empty"), "src/app/page.tsx": page("Home")}
        res = self.run_case(files, {"src/app/empty/layout.tsx": layout("Empty", " ")})
        self.assertEqual((res.reached, res.complete), ((), True))

    def test_the_kin_of_a_layout_wrap_the_same_pages(self):
        for name in ("template", "loading", "error", "not-found"):
            files = {f"src/app/s/{name}.tsx": page("W"), "src/app/s/x/page.tsx": page("X"), "src/app/t/page.tsx": page("T")}
            res = self.run_case(files, {f"src/app/s/{name}.tsx": page("W", "2")})
            self.assertEqual(self.labels(res), ["/s/x"], name)
            self.assertEqual(self.chain(res, "/s/x"), [(f"src/app/s/{name}.tsx", 2), (f"src/app/s/{name}.tsx", 1), ("src/app/s/x/page.tsx", 1)], name)

    def test_app_reaches_every_page_of_the_pages_router_and_nothing_else(self):
        res = self.go({"src/pages/_app.tsx": self.FILES["src/pages/_app.tsx"].replace("<div>", '<div className="x">')})
        self.assertEqual(self.labels(res), ["/legacy"])
        self.assertEqual(self.chain(res, "/legacy"), [("src/pages/_app.tsx", 3), ("src/pages/_app.tsx", 4), ("src/pages/legacy.tsx", 1)])

    def test_document_wraps_the_pages_of_the_pages_router_through_its_main_line(self):
        res = self.go({"src/pages/_document.tsx": self.FILES["src/pages/_document.tsx"].replace("<html>", '<html lang="en">')})
        self.assertEqual(self.labels(res), ["/legacy"])
        self.assertEqual(self.chain(res, "/legacy")[1], ("src/pages/_document.tsx", 4))

    def test_a_file_called_layout_outside_the_app_folder_wraps_nothing(self):
        files = {"src/components/layout.tsx": comp("Layout"), "src/lib/route.ts": dep(), "tsconfig.json": TSCONFIG, "src/dep.ts": dep(),
                 "src/app/a/page.tsx": page("A"), "src/app/b/page.tsx": uses("B", "Layout", "@/components/layout")}
        res = self.run_case(files, {"src/components/layout.tsx": comp("Layout").replace("dep()", "dep() + 1")})
        self.assertEqual(self.labels(res), ["/b"])  # reached by its import, not as a layout of the folder it sits in

    def test_a_file_called_layout_in_a_private_folder_wraps_nothing(self):
        files = {"src/app/_ui/layout.tsx": layout("Private"), "src/app/a/page.tsx": page("A")}
        res = self.run_case(files, {"src/app/_ui/layout.tsx": layout("Private", " ")})
        self.assertEqual(res.reached, ())

    def test_middleware_reaches_every_page_and_handler_through_its_function(self):
        files = dict(self.FILES)
        files["src/middleware.ts"] = f(["export function middleware() {", "  return 1;", "}"])
        res = self.go({"src/middleware.ts": f(["export function middleware() {", "  return 2;", "}"])}, files)
        self.assertEqual(self.labels(res), ["/", "/about", "/cart", "/legacy", "/products", "GET /api/ping"])
        self.assertEqual(self.chain(res, "/cart"), [("src/middleware.ts", 2), ("src/middleware.ts", 1), ("src/app/(shop)/cart/page.tsx", 1)])
        self.assertEqual(self.chain(res, "GET /api/ping")[:2], [("src/middleware.ts", 2), ("src/middleware.ts", 1)])

    def test_a_helper_imported_by_middleware_reaches_every_page_and_handler_through_the_import(self):
        files = dict(self.FILES)
        files["src/middleware.ts"] = f(['import { readSession } from "@/lib/session";', "", "export function middleware() {", "  return readSession();", "}"])
        files["src/lib/session.ts"] = f(["export function readSession() {", "  return 1;", "}"])
        res = self.go({"src/lib/session.ts": f(["export function readSession() {", "  return 2;", "}"])}, files)
        self.assertEqual(self.labels(res), ["/", "/about", "/cart", "/legacy", "/products", "GET /api/ping"])
        self.assertEqual(self.chain(res, "/cart"), [("src/middleware.ts", 1), ("src/middleware.ts", 3), ("src/app/(shop)/cart/page.tsx", 1)])

    def test_a_component_imported_by_app_reaches_the_pages_router_pages_through_the_import(self):
        files = dict(self.FILES)
        files["src/components/Shell.tsx"] = comp("Shell")
        files["src/pages/_app.tsx"] = f(['import Shell from "@/components/Shell";', "", "export default function MyApp({ Component, pageProps }: any) {", "  return (", "    <Shell>", "      <Component {...pageProps} />", "    </Shell>", "  );", "}"])
        res = self.go({"src/dep.ts": dep(ret="2")}, files)
        self.assertIn("/legacy", self.labels(res))
        self.assertEqual(self.chain(res, "/legacy"), [("src/components/Shell.tsx", 1), ("src/pages/_app.tsx", 1), ("src/pages/_app.tsx", 6), ("src/pages/legacy.tsx", 1)])
        self.assertNotIn("/about", self.labels(res))

    def test_a_file_called_middleware_that_is_not_beside_the_router_folders_is_no_middleware(self):
        files = {"src/utils/middleware.ts": f(["export function middleware() {", "  return 1;", "}"]), "src/app/a/page.tsx": page("A")}
        res = self.run_case(files, {"src/utils/middleware.ts": f(["export function middleware() {", "  return 2;", "}"])})
        self.assertEqual(res.reached, ())

    def test_a_wrapper_that_cannot_be_read_is_a_problem_not_an_empty_answer(self):
        files = {"src/app/layout.tsx": layout("Root"), "src/app/a/page.tsx": page("A")}
        self.repo({"files": files}, {"files": {"src/app/layout.tsx": layout("Root", " ")}})
        self.entry()
        real = self.language.file_text

        def unreadable(sha, path):
            return ReadResult.unreadable("simulated failure") if path == "src/app/a/page.tsx" else real(sha, path)

        with mock.patch.object(self.language, "file_text", unreadable):
            res = self.reach()
        self.assertEqual(res.reached, ())
        self.assertFalse(res.complete)
        self.assertTrue(any("src/app/a/page.tsx" in why for why in res.reasons))


class Citations(Base):
    """The line that says a wrapper wraps something, and what it is not."""

    def test_a_layout_that_names_no_children_is_cited_at_its_default_export(self):
        files = {"src/app/layout.tsx": f(["export default function Root() {", "  return null;", "}"]), "src/app/a/page.tsx": page("A")}
        res = self.run_case(files, {"src/app/layout.tsx": f(["export default function Root() {", "  return undefined;", "}"])})
        self.assertEqual(self.chain(res, "/a"), [("src/app/layout.tsx", 2), ("src/app/layout.tsx", 1), ("src/app/a/page.tsx", 1)])

    def test_a_comment_that_names_children_is_not_the_line_that_renders_them(self):
        text = f(["export default function Root({ children }: any) {", "  return <main>{children}</main>;", "}", "", "// children are rendered above"])
        files = {"src/app/layout.tsx": text, "src/app/a/page.tsx": page("A")}
        res = self.run_case(files, {"src/app/layout.tsx": text.replace("<main>", '<main id="x">')})
        self.assertNotIn(("src/app/layout.tsx", 5), self.chain(res, "/a"))
        self.assertEqual(self.chain(res, "/a")[-1], ("src/app/a/page.tsx", 1))

    def test_a_string_that_names_children_is_not_the_line_that_renders_them(self):
        text = f(["export default function Root({ children }: any) {", "  return <main>{children}</main>;", "}", "", 'export const note = "children";'])
        files = {"src/app/layout.tsx": text, "src/app/a/page.tsx": page("A")}
        res = self.run_case(files, {"src/app/layout.tsx": text.replace("<main>", '<main id="x">')})
        self.assertNotIn(("src/app/layout.tsx", 5), self.chain(res, "/a"))


class NextAuthShapes(Base):
    def test_a_destructured_export_names_every_method_it_binds(self):
        text = f(['import { handlers } from "@/auth";', "", "export const { GET, POST } = handlers;"])
        files = {"tsconfig.json": TSCONFIG, "src/auth.ts": f(["export const handlers = { GET: 1, POST: 2 };"]), "src/app/api/auth/[...nextauth]/route.ts": text}
        self.repo({"files": files}, {"files": {"src/auth.ts": f(["export const handlers = { GET: 3, POST: 2 };"])}})
        entry = self.entry()
        self.assertEqual(sorted((r.method, r.path) for r in entry.routes(None, self.sha("main"))), [("GET", "/api/auth/{nextauth:path}"), ("POST", "/api/auth/{nextauth:path}")])
        self.assertEqual(self.labels(self.reach()), ["GET /api/auth/{nextauth:path}", "POST /api/auth/{nextauth:path}"])

    def test_one_handler_exported_under_two_methods_is_reached_by_a_change_inside_it(self):
        text = f(['import { dep } from "@/dep";', "", "async function handler() {", "  return dep();", "}", "", "export { handler as GET, handler as POST };"])
        files = {"tsconfig.json": TSCONFIG, "src/dep.ts": dep(), "src/app/api/x/route.ts": text}
        res = self.run_case(files, {"src/app/api/x/route.ts": text.replace("return dep();", "return dep() + 1;")})
        self.assertEqual(self.labels(res), ["GET /api/x", "POST /api/x"])

    def test_an_unreadable_routing_file_met_while_looking_for_re_exports_is_a_problem(self):
        files = {"tsconfig.json": TSCONFIG, "src/dep.ts": dep(), "src/app/a/page.tsx": uses("A", "dep", "@/dep", "dep()").replace("import dep from", "import { dep } from"), "src/app/b/page.tsx": page("B")}
        self.repo({"files": files}, {"files": {"src/dep.ts": dep(ret="2")}})
        self.entry()
        real = self.language.parsed

        def broken(sha, path):
            return None if path == "src/app/b/page.tsx" else real(sha, path)

        with mock.patch.object(self.language, "parsed", broken):
            res = self.reach()
        self.assertFalse(res.complete)
        self.assertTrue(any("src/app/b/page.tsx" in why for why in res.reasons))


class Removals(Base):
    FILES = {"src/app/(shop)/layout.tsx": layout("ShopLayout"), "src/app/(shop)/cart/page.tsx": page("Cart"), "src/app/(shop)/products/page.tsx": page("Products"),
             "src/app/about/page.tsx": page("About")}

    def test_a_deleted_layout_reaches_the_pages_that_lost_it_through_the_line_it_had(self):
        self.repo({"files": self.FILES}, {"delete": ["src/app/(shop)/layout.tsx"]})
        self.entry()
        res = self.reach()
        self.assertEqual(self.labels(res), ["/cart", "/products"])
        self.assertEqual(self.chain(res, "/cart"), [("src/app/(shop)/layout.tsx", 4), ("src/app/(shop)/cart/page.tsx", 1)])
        self.assertEqual([e.ref for e in res.reached[0].area.chains[0]], [self.sha("main~1"), self.sha("main")])  # the layout as it was, the page as it is
        self.assertTrue(res.complete)

    def test_a_renamed_layout_reaches_the_pages_of_the_folder_it_left_and_those_of_the_folder_it_went_to(self):
        files = {**self.FILES, "src/app/(store)/mug/page.tsx": page("Mug")}
        self.repo({"files": files}, {"delete": ["src/app/(shop)/layout.tsx"], "files": {"src/app/(store)/layout.tsx": layout("ShopLayout")}})
        self.entry()
        res = self.reach()
        self.assertEqual(self.labels(res), ["/cart", "/mug", "/products"])

    def test_a_deleted_page_reaches_nothing_and_a_deleted_layout_without_pages_reaches_nothing(self):
        self.repo({"files": {**self.FILES, "src/app/lonely/layout.tsx": layout("Lonely")}}, {"delete": ["src/app/lonely/layout.tsx", "src/app/about/page.tsx"]})
        self.entry()
        self.assertEqual(self.labels(self.reach()), [])

    def test_without_a_pre_change_version_removals_are_not_computed_and_nothing_is_claimed(self):
        self.repo({"files": self.FILES}, {"delete": ["src/app/(shop)/layout.tsx"]})
        self.entry()
        analysis = self.analyze(self.language, pre=None)
        res = entrypoints.reach(analysis, self.language, self.next)
        self.assertEqual(res.reached, ())

    def test_a_deleted_app_wrapper_of_the_pages_router_and_a_deleted_middleware(self):
        files = {"src/pages/_app.tsx": page("App"), "src/pages/a.tsx": page("A"), "src/app/b/page.tsx": page("B"), "src/middleware.ts": f(["export function middleware() {", "  return 1;", "}"])}
        self.repo({"files": files}, {"delete": ["src/pages/_app.tsx"]})
        self.entry()
        self.assertEqual(self.labels(self.reach()), ["/a"])
        self.repo({"files": files}, {"delete": ["src/middleware.ts"]})
        self.entry()
        self.assertEqual(self.labels(self.reach()), ["/a", "/b"])


class BasePath(Base):
    def test_the_base_path_of_the_config_is_put_before_every_url(self):
        files = {"src/app/page.tsx": page("Home"), "src/app/a/[id]/page.tsx": page("A"), "src/app/api/x/route.ts": route("GET", imp=""), "src/pages/legacy.tsx": page("L")}
        self.repo({"files": files})
        entry = self.entry(base_path="/docs/")
        self.assertEqual(sorted(r.path for r in entry.routes(None, self.sha("main"))), ["/docs", "/docs/a/:id", "/docs/api/x", "/docs/legacy"])


class RouteConflicts(Base):
    DEP = {"tsconfig.json": TSCONFIG, "src/dep.ts": dep()}

    def test_a_page_and_a_route_file_of_one_folder_are_recorded_and_both_are_reached(self):
        files = {**self.DEP, "src/app/a/page.tsx": uses("A", "dep", "@/dep", "dep()").replace("import dep from", "import { dep } from"), "src/app/a/route.ts": route("GET")}
        res = self.run_case(files, {"src/dep.ts": dep(ret="2")})
        self.assertEqual(self.labels(res), ["/a", "GET /a"])  # affected: neither is dropped
        self.assertEqual(self.records_of("route_conflict"), [("src/app/a/page.tsx and src/app/a/route.ts", False)])

    def test_an_app_page_and_a_pages_page_of_one_url_are_recorded_once_naming_both(self):
        use = uses("A", "dep", "@/dep", "dep()").replace("import dep from", "import { dep } from")
        files = {**self.DEP, "src/app/about/page.tsx": use, "src/pages/about.tsx": use}
        res = self.run_case(files, {"src/dep.ts": dep(ret="2")})
        self.assertEqual(self.labels(res), ["/about"])
        self.assertEqual(self.records_of("route_conflict"), [("src/app/about/page.tsx and src/pages/about.tsx", False)])
        self.assertEqual(len(res.reached[0].area.chains), 2)

    def test_no_conflict_no_record(self):
        res = self.run_case({**self.DEP, "src/app/a/page.tsx": page("A"), "src/pages/b.tsx": page("B")}, {"src/app/a/page.tsx": page("A", "2")})
        self.assertEqual((self.labels(res), self.records_of("route_conflict")), (["/a"], []))

    def test_the_record_is_made_only_for_a_route_a_change_reaches(self):
        files = {**self.DEP, "src/app/about/page.tsx": page("A"), "src/pages/about.tsx": page("B"), "src/app/c/page.tsx": page("C")}
        res = self.run_case(files, {"src/app/c/page.tsx": page("C", "2")})
        self.assertEqual((self.labels(res), self.records_of("route_conflict")), (["/c"], []))


class InstrumentationAndKin(Base):
    def test_instrumentation_reaches_every_page_and_handler_and_the_note_says_so(self):
        before = f(["export function register() {", "  return 1;", "}"])
        files = {"src/instrumentation.ts": before, "src/app/a/page.tsx": page("A"), "src/app/api/x/route.ts": route("GET", imp=""), "src/pages/b.tsx": page("B")}
        res = self.run_case(files, {"src/instrumentation.ts": before.replace("return 1;", "return 2;")})
        self.assertEqual(self.labels(res), ["/a", "/b", "GET /api/x"])
        self.assertEqual(self.chain(res, "/a"), [("src/instrumentation.ts", 2), ("src/instrumentation.ts", 1), ("src/app/a/page.tsx", 1)])
        self.assertEqual(len(res.notes), 1)
        self.assertIn("instrumentation", res.notes[0])
        self.assertNotIn("matcher", res.notes[0])

    def test_a_file_called_instrumentation_elsewhere_is_nothing(self):
        files = {"src/lib/instrumentation.ts": f(["export function register() {", "  return 1;", "}"]), "src/app/a/page.tsx": page("A")}
        res = self.run_case(files, {"src/lib/instrumentation.ts": f(["export function register() {", "  return 2;", "}"])})
        self.assertEqual(res.reached, ())

    def test_forbidden_and_unauthorized_wrap_the_pages_below_their_folder_like_not_found(self):
        for name in ("forbidden", "unauthorized"):
            files = {f"src/app/s/{name}.tsx": page("W"), "src/app/s/x/page.tsx": page("X"), "src/app/t/page.tsx": page("T")}
            res = self.run_case(files, {f"src/app/s/{name}.tsx": page("W", "2")})
            self.assertEqual(self.labels(res), ["/s/x"], name)
            self.assertEqual(self.chain(res, "/s/x"), [(f"src/app/s/{name}.tsx", 2), (f"src/app/s/{name}.tsx", 1), ("src/app/s/x/page.tsx", 1)], name)


class MetadataRoutes(Base):
    def test_a_changed_sitemap_reaches_its_get_route_cited_at_its_default_export(self):
        before = f(["export default function sitemap() {", "  return [];", "}"])
        files = {"src/app/sitemap.ts": before, "src/app/blog/robots.ts": f(["export default function robots() {", "  return {};", "}"]), "src/app/a/page.tsx": page("A")}
        res = self.run_case(files, {"src/app/sitemap.ts": before.replace("return [];", "return [{ url: 'x' }];")})
        self.assertEqual(self.labels(res), ["GET /sitemap.xml"])
        self.assertEqual(self.chain(res, "GET /sitemap.xml"), [("src/app/sitemap.ts", 2), ("src/app/sitemap.ts", 1)])
        self.assertEqual(sorted(r.path for r in self.next.routes(None, self.sha("main"))), ["/a", "/blog/robots.txt", "/sitemap.xml"])

    def test_proxy_reaches_what_middleware_reaches(self):
        files = {"src/proxy.ts": f(["export function proxy() {", "  return 1;", "}"]), "src/app/a/page.tsx": page("A")}
        res = self.run_case(files, {"src/proxy.ts": f(["export function proxy() {", "  return 2;", "}"])})
        self.assertEqual(self.labels(res), ["/a"])
        self.assertEqual(self.chain(res, "/a"), [("src/proxy.ts", 2), ("src/proxy.ts", 1), ("src/app/a/page.tsx", 1)])


class RouteHandlers(Base):
    BOTH = f([
        'import { listOrders } from "@/orders";', 'import { createOrder } from "@/orders";', "",
        "export async function GET() {", "  return listOrders();", "}", "", "export async function POST() {", "  return createOrder();", "}", "",
    ])
    ORDERS = f(["export function listOrders() {", "  return [];", "}", "", "export function createOrder() {", "  return 1;", "}"])

    def go(self, route_text, changed, **kw):
        files = {"tsconfig.json": TSCONFIG, "src/orders.ts": self.ORDERS, "src/app/api/orders/route.ts": route_text}
        return self.run_case(files, changed, **kw)

    def test_a_changed_method_reaches_that_route_only(self):
        res = self.go(self.BOTH, {"src/app/api/orders/route.ts": self.BOTH.replace("return listOrders();", "return listOrders() ?? [];")})
        self.assertEqual(self.labels(res), ["GET /api/orders"])
        self.assertEqual(self.chain(res, "GET /api/orders"), [("src/app/api/orders/route.ts", 5), ("src/app/api/orders/route.ts", 4)])
        (r,) = res.reached
        self.assertEqual((r.area.kind, r.area.module, r.routes[0].method, r.routes[0].handler), ("api_route", "orders", "GET", "GET"))
        self.assertEqual(r.area.route_line.claim, "route:GET /api/orders:src/app/api/orders/route.ts")

    def test_an_imported_function_reaches_the_methods_that_use_it(self):
        res = self.go(self.BOTH, {"src/orders.ts": self.ORDERS.replace("return [];", "return [1];")})
        self.assertEqual(self.labels(res), ["GET /api/orders"])
        self.assertEqual(self.chain(res, "GET /api/orders"), [("src/app/api/orders/route.ts", 1), ("src/app/api/orders/route.ts", 4)])
        res = self.go(self.BOTH, {"src/orders.ts": self.ORDERS.replace("return 1;", "return 2;")})
        self.assertEqual(self.labels(res), ["POST /api/orders"])

    def test_a_helper_two_methods_use_reaches_both(self):
        text = f(['import { dep } from "@/dep";', "", "function shared() {", "  return dep();", "}", "", "export async function GET() {", "  return shared();", "}", "",
                  "export async function DELETE() {", "  return shared();", "}", "", "export async function POST() {", "  return 1;", "}"])
        files = {"tsconfig.json": TSCONFIG, "src/dep.ts": dep(), "src/app/api/x/route.ts": text}
        res = self.run_case(files, {"src/dep.ts": dep(ret="2")})
        self.assertEqual(self.labels(res), ["DELETE /api/x", "GET /api/x"])

    def test_a_statement_that_runs_when_the_file_loads_reaches_every_method(self):
        text = f(['import { dep } from "@/dep";', "", "const config = dep();", "", "export async function GET() {", "  return config;", "}", "", "export async function POST() {", "  return 1;", "}"])
        files = {"tsconfig.json": TSCONFIG, "src/dep.ts": dep(), "src/app/api/x/route.ts": text}
        res = self.run_case(files, {"src/dep.ts": dep(ret="2")})
        self.assertEqual(self.labels(res), ["GET /api/x", "POST /api/x"])

    def test_a_changed_import_line_reaches_the_methods_that_use_its_binding(self):
        before = self.BOTH
        after = before.replace('import { createOrder } from "@/orders";', 'import { createOrder } from "@/orders2";')
        res = self.go(before, {"src/app/api/orders/route.ts": after})
        self.assertEqual(self.labels(res), ["POST /api/orders"])

    def test_a_changed_statement_that_is_not_an_import_reaches_every_method(self):
        before = self.BOTH.replace("export async function GET", "setup(1);\n\nexport async function GET")
        after = before.replace("setup(1)", "setup(2)")
        res = self.go(before, {"src/app/api/orders/route.ts": after})
        self.assertEqual(self.labels(res), ["GET /api/orders", "POST /api/orders"])

    def test_a_dynamic_segment_is_a_brace_parameter_and_a_catch_all_a_rest_parameter(self):
        files = {"src/app/api/orders/[id]/route.ts": f(["export async function GET() {", "  return 1;", "}"]),
                 "src/app/api/files/[...path]/route.ts": f(["export async function GET() {", "  return 1;", "}"])}
        res = self.run_case(files, {"src/app/api/orders/[id]/route.ts": f(["export async function GET() {", "  return 2;", "}"]),
                                    "src/app/api/files/[...path]/route.ts": f(["export async function GET() {", "  return 2;", "}"])})
        self.assertEqual(self.labels(res), ["GET /api/files/{path:path}", "GET /api/orders/{id}"])

    def test_const_exports_and_export_lists_are_methods(self):
        text = f(['import { dep } from "@/dep";', "", "export const GET = async () => {", "  return dep();", "};", "", "async function remove() {", "  return 1;", "}", "", "export { remove as DELETE };"])
        files = {"tsconfig.json": TSCONFIG, "src/dep.ts": dep(), "src/app/api/x/route.ts": text}
        res = self.run_case(files, {"src/dep.ts": dep(ret="2")})
        self.assertEqual(self.labels(res), ["GET /api/x"])
        routes = self.next.routes(None, self.sha("main"))
        self.assertEqual([(r.method, r.evidence[0].line) for r in routes], [("GET", 3), ("DELETE", 11)])

    def test_a_method_exported_by_reexport_or_export_star_is_a_method(self):
        files = {"src/lib/handlers.ts": f(["export async function GET() {", "  return 1;", "}", "", "export async function POST() {", "  return 2;", "}"]),
                 "src/app/api/a/route.ts": f(['export { GET } from "../../../lib/handlers";']),
                 "src/app/api/b/route.ts": f(['export * from "../../../lib/handlers";'])}
        self.repo({"files": files})
        entry = self.entry()
        got = [(r.path, r.method, r.evidence[0].line) for r in entry.routes(None, self.sha("main"))]
        self.assertEqual(got, [("/api/a", "GET", 1), ("/api/b", "GET", 1), ("/api/b", "POST", 1)])

    def test_a_handler_that_is_reached_through_a_reexport_of_the_methods_it_serves(self):
        handlers = f(['import { dep } from "@/dep";', "", "export async function GET() {", "  return dep();", "}", "", "export async function POST() {", "  return 2;", "}"])
        files = {"tsconfig.json": TSCONFIG, "src/dep.ts": dep(), "src/lib/handlers.ts": handlers, "src/app/api/a/route.ts": f(['export { GET, POST } from "../../../lib/handlers";'])}
        res = self.run_case(files, {"src/dep.ts": dep(ret="2")})
        self.assertEqual(self.labels(res), ["GET /api/a"])  # only the name that arrives with the change

    def test_get_in_lower_case_a_helper_called_handler_and_other_exports_are_not_methods(self):
        text = f(["export async function get() {", "  return 1;", "}", "", "export const handler = () => 2;", "", 'export const dynamic = "force-dynamic";', "", "export async function POST_URL() {", "  return 3;", "}"])
        self.repo({"files": {"src/app/api/x/route.ts": text}})
        self.assertEqual(self.entry().routes(None, self.sha("main")), [])

    def test_a_file_called_route_outside_the_app_folder_is_not_a_handler(self):
        files = {"src/lib/route.ts": f(["export async function GET() {", "  return 1;", "}"]), "src/app/a/page.tsx": page("A")}
        self.repo({"files": files})
        self.assertEqual([r.path for r in self.entry().routes(None, self.sha("main"))], ["/a"])

    def test_a_handler_file_that_does_not_lex_claims_no_route_and_blocks(self):
        broken = 'import { dep } from "@/dep";\nexport async function GET() {\n  return `unterminated;\n}\n'
        files = {"tsconfig.json": TSCONFIG, "src/dep.ts": dep(), "src/app/api/x/route.ts": f(['import { dep } from "@/dep";', "export async function GET() {", "  return dep();", "}"])}
        res = self.run_case(files, {"src/app/api/x/route.ts": broken})
        self.assertEqual(res.reached, ())
        self.assertFalse(res.complete)
        self.assertIn(("src/app/api/x/route.ts", True), self.records_of("parse_degraded"))
        self.assertTrue(self.next.problems)
        self.assertEqual(self.next.routes(None, self.sha("main")), [])

    def test_an_export_star_of_a_module_that_does_not_resolve_is_a_blocking_record(self):
        files = {"src/app/api/x/route.ts": f(['export * from "some-package";'])}
        self.repo({"files": files})
        entry = self.entry()
        self.assertEqual(entry.routes(None, self.sha("main")), [])
        self.assertIn(("src/app/api/x/route.ts", True), self.records_of("specifier_unresolved"))
        self.assertTrue(entry.problems)

    def star_chain(self, depth):
        """A route file that `export *`s from h1, which `export *`s from h2 ... up to h<depth>, which exports GET."""
        files = {"src/app/api/x/route.ts": f(['export * from "../../../lib/h1";'])}
        for i in range(1, depth):
            files[f"src/lib/h{i}.ts"] = f([f'export * from "./h{i + 1}";'])
        files[f"src/lib/h{depth}.ts"] = f(["export async function GET() {", "  return 1;", "}"])
        return files

    def test_export_star_is_followed_five_files_deep(self):
        self.repo({"files": self.star_chain(5)})
        entry = self.entry()
        self.assertEqual([(r.method, r.path) for r in entry.routes(None, self.sha("main"))], [("GET", "/api/x")])
        self.assertEqual((entry.problems, self.kinds()), ([], []))

    def test_export_star_deeper_than_five_files_is_incomplete_and_recorded_not_dropped(self):
        self.repo({"files": self.star_chain(6)})
        entry = self.entry()
        self.assertEqual(entry.routes(None, self.sha("main")), [])  # nothing is claimed, and it is said that this is not "no route"
        self.assertTrue(any("deep" in why and "src/lib/h5.ts" in why for why in entry.problems), entry.problems)
        self.assertIn(("src/app/api/x/route.ts", True), self.records_of("specifier_unresolved"))

    def test_a_reach_through_a_too_deep_export_star_is_not_complete(self):
        files = {**self.star_chain(7), "tsconfig.json": TSCONFIG, "src/dep.ts": dep()}
        files["src/app/api/x/route.ts"] = f(['import { dep } from "@/dep";', "", "export async function POST() {", "  return dep();", "}", "", 'export * from "../../../lib/h1";'])
        self.repo({"files": files}, {"files": {"src/dep.ts": dep(ret="2")}})
        self.entry()
        res = self.reach()
        self.assertEqual(self.labels(res), ["POST /api/x"])
        self.assertFalse(res.complete)
        self.assertTrue(any("deep" in why for why in res.reasons))

    def test_a_route_group_does_not_show_in_the_path_and_names_the_module(self):
        files = {"src/app/(v1)/api/ping/route.ts": f(["export async function GET() {", "  return 1;", "}"])}
        self.repo({"files": files})
        (route,) = self.entry().routes(None, self.sha("main"))
        self.assertEqual((route.path, route.module), ("/api/ping", "v1"))


class PagesApi(Base):
    API = f(["export default function handler(req: any, res: any) {", "  res.status(200).json({ ok: true });", "}"])

    def test_a_pages_api_file_answers_every_method(self):
        files = {"src/pages/api/status.ts": self.API, "src/pages/api/legacy/items/[id].ts": self.API}
        res = self.run_case(files, {"src/pages/api/status.ts": self.API.replace("ok: true", "ok: 1")})
        self.assertEqual(self.labels(res), ["ANY /api/status"])
        self.assertEqual(self.chain(res, "ANY /api/status"), [("src/pages/api/status.ts", 2), ("src/pages/api/status.ts", 1)])
        (r,) = res.reached
        self.assertEqual((r.area.kind, r.routes[0].method, r.routes[0].handler, r.area.module), ("api_route", "ANY", "handler", "status"))

    def test_a_dynamic_segment_of_an_api_file_is_a_brace_parameter(self):
        files = {"src/pages/api/legacy/items/[id].ts": self.API}
        res = self.run_case(files, {"src/pages/api/legacy/items/[id].ts": self.API.replace("ok: true", "ok: 1")})
        self.assertEqual(self.labels(res), ["ANY /api/legacy/items/{id}"])

    def test_a_file_that_is_not_under_pages_api_is_a_page_even_when_it_says_api(self):
        files = {"src/pages/api-docs.tsx": page("ApiDocs")}
        self.repo({"files": files})
        (route,) = self.entry().routes(None, self.sha("main"))
        self.assertEqual((route.kind, route.path), ("screen", "/api-docs"))


class EntryFilesThatImportEntryFiles(Base):
    def test_a_page_that_reexports_a_page_is_reached_with_it(self):
        files = {"tsconfig.json": TSCONFIG, "src/dep.ts": dep(), "src/app/photos/[id]/page.tsx": uses("Photo", "dep", "@/dep", "dep()").replace("import dep from", "import { dep } from"),
                 "src/app/feed/@modal/(..)photos/[id]/page.tsx": f(['export { default } from "../../../../photos/[id]/page";'])}
        res = self.run_case(files, {"src/dep.ts": dep(ret="2")})
        (r,) = res.reached
        self.assertEqual((r.area.label, len(r.area.chains)), ("/photos/:id", 2))
        self.assertEqual(self.chain(res, "/photos/:id", 1), [
            ("src/app/photos/[id]/page.tsx", 1), ("src/app/feed/@modal/(..)photos/[id]/page.tsx", 1)])  # the re-export line is the import and the default export

    def test_the_names_that_arrive_narrow_what_the_other_entry_file_takes(self):
        lib = f(["export function used() {", "  return 1;", "}", "", "export function other() {", "  return 2;", "}"])
        a = f(['import { dep } from "@/dep";', "", "export function GET() {", "  return dep();", "}", "", "export function POST() {", "  return 2;", "}"])
        files = {"tsconfig.json": TSCONFIG, "src/dep.ts": dep(), "src/app/api/a/route.ts": a,
                 "src/app/api/b/route.ts": f(['export { POST } from "../a/route";']), "src/app/api/c/route.ts": f(['export { GET } from "../a/route";'])}
        res = self.run_case(files, {"src/dep.ts": dep(ret="2")})
        self.assertEqual(self.labels(res), ["GET /api/a", "GET /api/c"])

    def test_a_helper_exported_from_a_page_carries_the_change_to_its_importers_through_the_walk(self):
        helper = f(['import { dep } from "@/dep";', "", "export function title() {", "  return dep();", "}", "", "export default function A() {", "  return 1;", "}"])
        files = {"tsconfig.json": TSCONFIG, "src/dep.ts": dep(), "src/app/a/page.tsx": helper,
                 "src/components/Header.tsx": f(['import { title } from "@/app/a/page";', "", "export default function Header() {", "  return title();", "}"]),
                 "src/app/b/page.tsx": uses("B", "Header", "@/components/Header")}
        res = self.run_case(files, {"src/dep.ts": dep(ret="2")})
        self.assertEqual(self.labels(res), ["/a", "/b"])

    def test_nothing_is_followed_past_a_file_that_only_wires_a_page(self):
        files = {"tsconfig.json": TSCONFIG, "src/dep.ts": dep(), "src/app/a/page.tsx": uses("A", "dep", "@/dep", "dep()").replace("import dep from", "import { dep } from"),
                 "src/components/Link.tsx": f(['import A from "@/app/a/page";', "", "export default function Link() {", "  return A;", "}"])}
        res = self.run_case(files, {"src/dep.ts": dep(ret="2")})
        self.assertEqual(self.labels(res), ["/a"])  # `Link` imports the default export of a page: the default export is Next's, not a helper


class RouteTable(Base):
    FILES = {
        "src/app/page.tsx": page("Home"),
        "src/app/(shop)/cart/page.tsx": page("Cart"),
        "src/app/docs/[...slug]/page.tsx": page("Docs"),
        "src/app/api/orders/route.ts": route("GET", "POST", imp=""),
        "src/pages/legacy/[id].tsx": page("Legacy"),
        "src/pages/api/ping.ts": PagesApi.API,
        "src/app/layout.tsx": layout("Root"),
        "src/app/api/hidden/_x/route.ts": route("GET", imp=""),
    }

    def test_every_screen_and_route_with_its_path_method_and_evidence(self):
        self.repo({"files": self.FILES})
        routes = self.entry().routes(None, self.sha("main"))
        self.assertEqual([(r.kind, r.method, r.path, r.file, r.evidence[0].line) for r in routes], [
            ("screen", "", "/cart", "src/app/(shop)/cart/page.tsx", 1),
            ("api_route", "GET", "/api/orders", "src/app/api/orders/route.ts", 3),
            ("api_route", "POST", "/api/orders", "src/app/api/orders/route.ts", 7),
            ("screen", "", "/docs/*", "src/app/docs/[...slug]/page.tsx", 1),
            ("screen", "", "/", "src/app/page.tsx", 1),
            ("api_route", "ANY", "/api/ping", "src/pages/api/ping.ts", 1),
            ("screen", "", "/legacy/:id", "src/pages/legacy/[id].tsx", 1),
        ])

    def test_the_repository_name_is_checked(self):
        self.repo({"files": self.FILES})
        with self.assertRaises(ValueError):
            self.entry().routes("t/other", self.sha("main"))

    def test_routes_changed_is_the_api_routes_only(self):
        files = dict(self.FILES)
        new = dict(files)
        new.pop("src/app/api/orders/route.ts")
        new["src/app/api/invoices/route.ts"] = route("GET", imp="")
        new["src/app/new/page.tsx"] = page("New")
        self.repo({"files": files}, {"delete": ["src/app/api/orders/route.ts"], "files": {"src/app/api/invoices/route.ts": new["src/app/api/invoices/route.ts"], "src/app/new/page.tsx": new["src/app/new/page.tsx"]}})
        entry = self.entry()
        removed, added = entry.routes_changed(self.sha("main~1"), self.sha("main"))
        self.assertEqual(([(r.method, r.path) for r in removed], [(r.method, r.path) for r in added]), ([("GET", "/api/orders"), ("POST", "/api/orders")], [("GET", "/api/invoices")]))
        self.assertEqual({e.ref for r in removed for e in r.evidence}, {self.sha("main~1")})
        self.assertEqual({e.ref for r in added for e in r.evidence}, {self.sha("main")})
        s_removed, s_added = entry.screens_changed(self.sha("main~1"), self.sha("main"))
        self.assertEqual(([r.path for r in s_removed], [r.path for r in s_added]), ([], ["/new"]))
        self.assertEqual(entry.routes_changed(None, self.sha("main")), ([], []))
        self.assertEqual(entry.screens_changed(None, self.sha("main")), ([], []))

    def test_the_table_is_complete_and_has_no_problem(self):
        self.repo({"files": self.FILES})
        entry = self.entry()
        entry.routes(None, self.sha("main"))
        self.assertEqual(entry.problems, [])

    def test_is_entry_file_is_the_table_of_routing_files(self):
        self.repo({"files": self.FILES})
        entry = self.entry()
        sha = self.sha("main")
        for path in ("src/app/page.tsx", "src/app/layout.tsx", "src/app/api/orders/route.ts", "src/pages/api/ping.ts", "src/pages/legacy/[id].tsx"):
            self.assertTrue(entry.is_entry_file(path, sha), path)
        for path in ("src/app/api/hidden/_x/route.ts", "src/components/x.tsx", "README.md"):
            self.assertFalse(entry.is_entry_file(path, sha), path)

    def test_the_names_next_reads_are_structural_and_a_helper_is_not(self):
        self.repo({"files": self.FILES})
        names = self.entry().structural_names("src/app/page.tsx", self.sha("main"))
        self.assertTrue({"default", "metadata", "GET", "generateStaticParams", "getServerSideProps"} <= names)
        self.assertFalse({"title", "helper", "Component"} & names)

    def test_a_root_option_reads_the_project_in_a_folder_of_the_repository(self):
        files = {"apps/web/src/app/page.tsx": page("Web"), "apps/admin/src/app/page.tsx": page("Admin"), "src/app/page.tsx": page("Top")}
        self.repo({"files": files})
        entry = self.entry(root="apps/web")
        self.assertEqual([r.file for r in entry.routes(None, self.sha("main"))], ["apps/web/src/app/page.tsx"])

    def test_page_extensions_can_be_narrowed(self):
        files = {"src/app/a/page.tsx": page("A"), "src/app/b/page.js": page("B")}
        self.repo({"files": files})
        self.assertEqual([r.path for r in self.entry(extensions=("tsx",)).routes(None, self.sha("main"))], ["/a"])


class WhatTheTestSourcesSee(Base):
    def test_the_screens_match_a_visited_url_the_way_react_router_screens_do(self):
        from reachmap.testsources.specs import match_routes

        files = {"src/app/products/[slug]/page.tsx": page("P"), "src/app/products/new/page.tsx": page("New"), "src/app/docs/[...slug]/page.tsx": page("D"),
                 "src/app/help/[[...t]]/page.tsx": page("H"), "src/app/api/x/route.ts": route("GET", imp="")}
        self.repo({"files": files})
        routes = self.entry().routes(None, self.sha("main"))
        got = lambda *segs: sorted(r.path for r in match_routes(segs, routes))  # noqa: E731
        self.assertEqual(got("products", "12"), ["/products/:slug"])
        self.assertEqual(got("products", "new"), ["/products/new"])
        self.assertEqual(got("docs", "a", "b"), ["/docs/*"])
        self.assertEqual(got("help"), ["/help"])
        self.assertEqual(got("help", "x", "y"), ["/help/*"])
        self.assertEqual(got("api", "x"), [])  # an API route is never a screen

    def test_the_kind_is_known_to_the_registries_and_makes_screens(self):
        from reachmap import config_sources

        self.assertIn("nextjs", entrypoints.KINDS)
        self.assertIn("nextjs", config_sources.SCREEN_ENTRY_KINDS)


class Config(unittest.TestCase):
    def load(self, entry, extra=""):
        text = f([
            'workspace = "t"', "", '[repos."t/shop"]', 'host = "git"', 'remote = "/nowhere"', 'language = "typescript"', 'roles = ["frontend"]', f"entry_points = {entry}", extra,
        ])
        return config.loads(text, fmt="toml", env={})

    def test_the_kind_loads_with_and_without_options(self):
        for entry in ('{ kind = "nextjs" }', '{ kind = "nextjs", root = "apps/web" }', '{ kind = "nextjs", page_extensions = ["tsx", "ts"] }',
                      '{ kind = "nextjs", base_path = "/docs" }'):
            ws = self.load(entry)
            self.assertEqual(ws.repos["t/shop"].entry_points["kind"], "nextjs")

    def test_bad_options_are_refused_by_key_without_quoting_the_value(self):
        for entry, key in (
            ('{ kind = "nextjs", root = 3 }', "root"), ('{ kind = "nextjs", root = "/abs/secret-path" }', "root"), ('{ kind = "nextjs", root = "../x" }', "root"),
            ('{ kind = "nextjs", page_extensions = "tsx" }', "page_extensions"), ('{ kind = "nextjs", page_extensions = [] }', "page_extensions"),
            ('{ kind = "nextjs", page_extensions = ["t sx"] }', "page_extensions"), ('{ kind = "nextjs", page_extensions = ["page.tsx"] }', "page_extensions"),  # `page.tsx` style extensions are not supported ('{ kind = "nextjs", routes_glob = "x" }', "routes_glob"),
            ('{ kind = "nextjs", base_path = "docs" }', "base_path"), ('{ kind = "nextjs", base_path = 3 }', "base_path"), ('{ kind = "nextjs", base_path = "/a?b=secret-q" }', "base_path"),
        ):
            with self.assertRaises(config.ConfigError) as ctx:
                self.load(entry)
            message = str(ctx.exception)
            self.assertIn(key, message, entry)
            self.assertNotIn("secret-path", message)
            self.assertNotIn("secret-q", message)

    def test_the_adapter_is_built_from_the_config(self):
        ws = self.load('{ kind = "nextjs", root = "apps/web", page_extensions = ["tsx"] }')
        adapter = NextJsAdapter.from_config(ws.repos["t/shop"], mock.Mock(spec=JsAdapter))
        self.assertEqual((adapter.root, adapter.extensions), ("apps/web", ("tsx",)))
        lang = mock.Mock(spec=JsAdapter)
        made = entrypoints.entry_adapter(ws.repos["t/shop"], lang)
        self.assertIsInstance(made, NextJsAdapter)

    def test_it_needs_the_javascript_adapter(self):
        ws = self.load('{ kind = "nextjs" }')
        with self.assertRaises(TypeError):
            entrypoints.entry_adapter(ws.repos["t/shop"], object())

    def test_a_spec_source_may_take_its_screens_from_a_nextjs_repository(self):
        ws = self.load('{ kind = "nextjs" }', f(["", "[[test_sources]]", 'kind = "playwright"', 'repo = "t/shop"']))
        self.assertEqual(ws.test_sources[0].kind, "playwright")


class ShapeA(Base):
    """A read that fails is a problem, never an absence."""

    def test_an_unreadable_page_is_a_problem_and_not_an_empty_route_table(self):
        self.repo({"files": {"src/app/a/page.tsx": page("A"), "src/app/b/page.tsx": page("B")}})
        entry = self.entry()
        real = self.language.file_text

        def unreadable(sha, path):
            return ReadResult.unreadable("simulated failure") if path.endswith("a/page.tsx") else real(sha, path)

        with mock.patch.object(self.language, "file_text", unreadable):
            routes = entry.routes(None, self.sha("main"))
        self.assertEqual([r.path for r in routes], ["/b"])
        self.assertTrue(any("a/page.tsx" in why for why in entry.problems))

    def test_a_file_the_tree_lists_that_is_absent_is_recorded_not_dropped(self):
        self.repo({"files": {"src/app/a/page.tsx": page("A")}})
        entry = self.entry()
        real = self.language.file_text

        def absent(sha, path):
            return ReadResult.absent() if path.endswith("page.tsx") else real(sha, path)

        with mock.patch.object(self.language, "file_text", absent):
            self.assertEqual(entry.routes(None, self.sha("main")), [])
        self.assertTrue(entry.problems)
        self.assertIn("read_unreadable", [r.kind for r in self.records])

    def test_a_tree_that_cannot_be_read_is_a_problem_and_not_no_routes(self):
        self.repo({"files": {"src/app/a/page.tsx": page("A")}})
        entry = self.entry()
        with mock.patch.object(self.language, "tree", lambda sha: frozenset()):
            self.assertEqual(entry.routes(None, self.sha("main")), [])
        self.assertTrue(any("file tree" in why for why in entry.problems))

    def test_a_chain_that_is_not_complete_makes_its_screens_not_complete(self):
        files = {"tsconfig.json": TSCONFIG, "src/dep.ts": dep(), "src/app/a/page.tsx": uses("A", "dep", "@/dep", "dep()").replace("import dep from", "import { dep } from")}
        self.repo({"files": files}, {"files": {"src/dep.ts": dep(ret="2")}})
        self.entry()
        analysis = self.analyze(self.language)
        from reachmap.walk import walk
        import dataclasses

        result = walk(analysis, self.language, self.next)
        broken = dataclasses.replace(result.chains[0], complete=False, reasons=("the importers of src/dep.ts were cut",))
        (reached,) = self.next.entry_points_reached([broken])
        self.assertEqual((reached.complete, reached.reasons), (False, ("the importers of src/dep.ts were cut",)))

    def test_a_chain_to_a_file_that_is_not_a_routing_file_is_a_problem(self):
        files = {"tsconfig.json": TSCONFIG, "src/dep.ts": dep(), "src/app/a/page.tsx": uses("A", "dep", "@/dep", "dep()").replace("import dep from", "import { dep } from")}
        self.repo({"files": files}, {"files": {"src/dep.ts": dep(ret="2")}})
        self.entry()
        analysis = self.analyze(self.language)
        from reachmap.walk import walk
        import dataclasses

        result = walk(analysis, self.language, self.next)
        stray = dataclasses.replace(result.chains[0], entry="src/lib/other.ts")
        self.assertEqual(self.next.entry_points_reached([stray]), [])
        self.assertTrue(any("src/lib/other.ts" in why for why in self.next.problems))


if __name__ == "__main__":
    unittest.main()
