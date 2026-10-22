"""The React Router entry-point adapter: the route parser on source text (JSX `<Route>` and object routes, nested path
composition, lazy pages, text that is not code), and the adapter on small repositories (routes files from the glob,
page files by import binding, screens with `also_mounted`, direct changes of a routes file, routes_changed).

The M1 scenarios (screens against expected.json) are in test_scenarios_js.py and test_scenarios_entry.py.
"""

import unittest

from reachmap import entrypoints
from reachmap.adapters.js import JsAdapter
from reachmap.adapters.js.syntax import parse
from reachmap.adapters.react_router import DEFAULT_ROUTES_GLOBS, ReactRouterAdapter, glob_regex
from reachmap.adapters.react_router.routes import compose, parse_routes
from tests import scenario_support as ss
from tests.support_universe import Base as UniverseBase
from tests.support_universe import f


def routes_of(text):
    parsed = parse(text)
    res = parse_routes(parsed)
    return res, {r.index: r for r in res.routes}


def composed(text):
    res, _ = routes_of(text)
    return [(r.path_full, r.is_leaf, r.tags) for r in compose(res)]


class JsxRoutes(unittest.TestCase):
    def test_single_and_multi_line_elements_with_attributes_in_any_order(self):
        text = "\n".join([
            'const r = (',
            '  <>',
            '    <Route path="/a" element={<A />} />',
            '    <Route element={<B prop={x} />} path="/b" />',
            '    <Route',
            '      path="/c"',
            '      element={',
            '        <C />',
            '      }',
            '    />',
            '    <Route path="/d" Component={D} />',
            '  </>',
            ');',
        ])
        self.assertEqual(composed(text), [
            ("/a", True, ("A",)), ("/b", True, ("B",)), ("/c", True, ("C",)), ("/d", True, ("D",)),
        ])
        res, by = routes_of(text)
        self.assertEqual((by[2].first_line, by[2].last_line, sorted(by[2].own_lines)), (5, 10, [5, 6, 7, 8, 9, 10]))
        self.assertEqual(sorted(by[0].own_lines), [3])

    def test_the_lines_of_a_route_are_its_own_tag_and_the_span_with_its_children(self):
        text = "\n".join([
            '<Route path="/p" element={<L />}>',
            '  <Route path="x" element={<X />} />',
            '</Route>',
        ])
        res, by = routes_of(text)
        self.assertEqual((sorted(by[0].own_lines), by[0].first_line, by[0].last_line), ([1], 1, 3))
        self.assertEqual((sorted(by[1].own_lines), by[1].parent), ([2], 0))

    def test_nested_routes_compose_their_paths(self):
        text = "\n".join([
            '<Route path="/billing" element={<Layout />}>',
            '  <Route index element={<Home />} />',
            '  <Route path="invoices" element={<List />} />',
            '  <Route path="invoices/:id" element={<Detail />} />',
            '  <Route path="/absolute" element={<Abs />} />',
            '  <Route element={<Wrap />}>',
            '    <Route path="deep" element={<Deep />} />',
            '  </Route>',
            '</Route>',
        ])
        got = [(r.path_full, r.tags) for r in compose(parse_routes(parse(text))) if r.is_leaf]
        self.assertEqual(got, [
            ("/billing", ("Layout", "Home")),
            ("/billing/invoices", ("Layout", "List")),
            ("/billing/invoices/:id", ("Layout", "Detail")),
            ("/absolute", ("Layout", "Abs")),
            ("/billing/deep", ("Layout", "Wrap", "Deep")),
        ])

    def test_a_parent_route_is_not_a_leaf_and_a_layout_route_adds_no_segment(self):
        text = '<Route path="/a" element={<L />}>\n<Route path="b" element={<B />} />\n</Route>'
        flags = [(r.path_full, r.is_leaf) for r in compose(parse_routes(parse(text)))]
        self.assertEqual(flags, [("/a", False), ("/a/b", True)])

    def test_the_element_may_wrap_the_page_in_other_components(self):
        text = '<Route path="/s" element={<React.Suspense fallback={<Spin />}><Lazy prop={1} /></React.Suspense>} />'
        (route,) = parse_routes(parse(text)).routes
        self.assertEqual(route.tags, ("React.Suspense", "Spin", "Lazy"))

    def test_a_commented_out_route_and_a_route_in_a_string_are_not_routes(self):
        text = "\n".join([
            '// <Route path="/old" element={<Old />} />',
            '/* <Route path="/older" element={<Older />} /> */',
            'const s = \'<Route path="/str" element={<S />} />\';',
            'const t = `<Route path="/tpl" element={<T />} />`;',
            '<Route path="/real" element={<R />} />',
            '{/* <Route path="/jsx-comment" element={<J />} /> */}',
        ])
        self.assertEqual([r.path_full for r in compose(parse_routes(parse(text)))], ["/real"])

    def test_a_path_that_is_not_a_literal_is_recorded_as_unknown_never_guessed(self):
        text = '<Route path={ROUTES.invoices} element={<I />} />\n<Route path={`/x/${id}`} element={<X />} />'
        got = [(r.path_full, r.partial, r.unknown) for r in compose(parse_routes(parse(text)))]
        self.assertEqual(got, [("<unknown:ROUTES.invoices>", True, ("ROUTES.invoices",)), ("<unknown:`/x/${id}`>", True, ("`/x/${id}`",))])

    def test_a_template_literal_without_substitution_and_a_string_expression_are_literals(self):
        text = '<Route path={`/t`} element={<T />} />\n<Route path={"/u"} element={<U />} />'
        self.assertEqual([r.path_full for r in compose(parse_routes(parse(text)))], ["/t", "/u"])

    def test_imports_and_lazy_declarations_are_the_page_bindings(self):
        text = "\n".join([
            'import Home from "./pages/Home";',
            'import { A, B as BB } from "./pages/ab";',
            'import * as pages from "./pages";',
            'import React from "react";',
            'const Sched = React.lazy(() => import("./pages/Sched"));',
            'const Plain = lazy(() => import(`./pages/Plain`));',
            'const Other = somethingElse();',
        ])
        res = parse_routes(parse(text))
        self.assertEqual(res.imports["Home"], ("./pages/Home", "default", 1))
        self.assertEqual(res.imports["BB"], ("./pages/ab", "B", 2))
        self.assertEqual(res.imports["pages"], ("./pages", "*", 3))
        self.assertEqual(res.lazies, {"Sched": ("./pages/Sched", 5), "Plain": ("./pages/Plain", 6)})

    def test_a_multi_line_import_is_cited_at_the_line_of_its_specifier(self):
        text = 'import {\n  A,\n  B,\n} from "./x";\n<Route path="/a" element={<A />} />'
        res = parse_routes(parse(text))
        self.assertEqual(res.imports["A"], ("./x", "A", 4))

    def test_text_that_does_not_lex_cleanly_is_a_problem_and_claims_no_route(self):
        res = parse_routes(parse('<Route path="/a" element={<A />} />\nconst x = "unterminated;\n'))
        self.assertTrue(res.problems)
        self.assertEqual(res.routes, [])


class Robustness(unittest.TestCase):
    SOURCES = [
        'import A from "./A";\nexport const r = (\n  <>\n    <Route path="/a" element={<A />} />\n    <Route path="/b" element={<React.Suspense fallback={null}><B /></React.Suspense>}>\n      <Route index element={<C />} />\n    </Route>\n  </>\n);\n',
        'export const r = createBrowserRouter([\n  { path: "/", element: <Root />, children: [{ index: true, element: <Home /> }, { path: "x", lazy: () => import("./X") }] },\n]);\n',
    ]

    def test_a_file_cut_anywhere_is_parsed_without_a_crash_and_claims_no_route_when_it_does_not_balance(self):
        for text in self.SOURCES:
            for cut in range(0, len(text) + 1, 3):
                res = parse_routes(parse(text[:cut]))
                compose(res)
                if res.problems:
                    self.assertEqual(res.routes, [])

    def test_token_soup_does_not_crash_or_hang(self):
        import random

        rng = random.Random(7)
        words = ["<", "Route", "/", ">", "{", "}", "path", "=", '"/x"', "element", "index", "children", ":", "[", "]", ",", "(", ")", "import", "lazy", "A", "."]
        for _ in range(300):
            text = " ".join(rng.choice(words) for _ in range(rng.randint(1, 40)))
            compose(parse_routes(parse(text)))


class Paths(unittest.TestCase):
    def paths(self, text):
        return [(r.path_full, r.partial) for r in compose(parse_routes(parse(text))) if r.is_leaf]

    def test_slashes_are_joined_the_way_the_router_joins_them(self):
        self.assertEqual(self.paths('<Route path="/a/" element={<X/>}><Route path="b/" element={<Y/>} /></Route>'), [("/a/b", False)])
        self.assertEqual(self.paths('<Route path="/a" element={<X/>}><Route path="" element={<Y/>} /></Route>'), [("/a", False)])
        self.assertEqual(self.paths('<Route path="/a" element={<X/>}><Route path="/a/b" element={<Y/>} /></Route>'), [("/a/b", False)])
        self.assertEqual(self.paths('<Route path="/a" element={<X/>}><Route path="*" element={<Y/>} /></Route>'), [("/a/*", False)])
        self.assertEqual(self.paths('<Route path="/" element={<X/>}><Route path="b" element={<Y/>} /></Route>'), [("/b", False)])
        self.assertEqual(self.paths('<Route path="/" element={<X/>} />'), [("/", False)])
        self.assertEqual(self.paths('<Route path="//a//b/" element={<X/>} />'), [("/a/b", False)])

    def test_the_text_of_an_unknown_part_is_left_as_written(self):
        self.assertEqual(self.paths('<Route path={`/x//${id}/`} element={<X/>} />'), [("<unknown:`/x//${id}/`>", True)])

    def test_a_relative_path_with_no_parent_is_not_claimed_as_absolute(self):
        """`<Routes><Route path="invoices">` inside a component mounted at `/billing/*`: the mount is not known here."""
        self.assertEqual(self.paths('<Route path="invoices" element={<A />} />'), [("/<unknown:mount>/invoices", True)])
        (r,) = [c for c in compose(parse_routes(parse('<Route path="invoices" element={<A />} />')))]
        self.assertEqual(r.unknown, ("mount",))
        self.assertEqual(self.paths('<Route element={<L />}><Route path="a" element={<A />} /></Route>'), [("/a", False)])  # under a layout route at the root it is the root

    def test_an_absolute_path_with_no_parent_is_absolute(self):
        self.assertEqual(self.paths('<Route path="/invoices" element={<A />} />'), [("/invoices", False)])


class ObjectRoutes(unittest.TestCase):
    def test_an_object_layout_route_has_no_path(self):
        text = 'const r = [{ element: <Layout />, children: [{ path: "/home", element: <Home /> }] }];'
        got = [(r.path_full, r.tags, r.is_leaf) for r in compose(parse_routes(parse(text)))]
        self.assertEqual(got, [("/", ("Layout",), False), ("/home", ("Layout", "Home"), True)])

    def test_an_object_with_only_children_and_a_component_is_a_layout_route(self):
        text = 'const r = [{ Component: Shell, children: [{ index: true, Component: Home }] }];'
        got = [(r.path_full, r.tags) for r in compose(parse_routes(parse(text))) if r.is_leaf]
        self.assertEqual(got, [("/", ("Shell", "Home"))])

    def test_object_routes_with_children_compose_like_jsx(self):
        text = "\n".join([
            'const router = createBrowserRouter([',
            '  {',
            '    path: "/",',
            '    element: <Root />,',
            '    children: [',
            '      { index: true, element: <Home /> },',
            '      { path: "about", Component: About },',
            '      { path: "blog", element: <Blog />, children: [{ path: ":slug", element: <Post /> }] },',
            '    ],',
            '  },',
            '  { path: "/login", element: <Login /> },',
            ']);',
        ])
        got = [(r.path_full, r.tags) for r in compose(parse_routes(parse(text))) if r.is_leaf]
        self.assertEqual(got, [
            ("/", ("Root", "Home")),
            ("/about", ("Root", "About")),
            ("/blog/:slug", ("Root", "Blog", "Post")),
            ("/login", ("Login",)),
        ])

    def test_a_lazy_property_is_a_page_by_its_import(self):
        text = 'const r = [{ path: "/s", lazy: () => import("./pages/S") }];'
        (route,) = parse_routes(parse(text)).routes
        self.assertEqual(route.lazy, (("./pages/S", 1),))

    def test_an_object_that_is_not_a_route_is_not_one(self):
        text = 'const cfg = [{ name: "x", path2: "/y" }, { mode: 1 }];\nfoo({ path: "/not", other: 1 });'
        self.assertEqual(compose(parse_routes(parse(text))), [])

    def test_the_lines_of_an_object_route_exclude_its_children(self):
        text = "\n".join([
            'const r = [',
            '  {',
            '    path: "/a",',
            '    element: <A />,',
            '    children: [',
            '      { path: "b", element: <B /> },',
            '    ],',
            '  },',
            '];',
        ])
        res = parse_routes(parse(text))
        parent, child = res.routes
        self.assertEqual(sorted(parent.own_lines), [2, 3, 4, 5, 7, 8])
        self.assertEqual((sorted(child.own_lines), child.parent), ([6], 0))


TSCONFIG = '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}\n'


def page(name, ret="1"):
    return f([f"export default function {name}() {{", f"  return {ret};", "}"])


def dep(name="dep"):
    return f([f"export function {name}() {{", "  return 1;", "}"])


def user(name, dep_name="dep", dep_path="../../dep"):
    return f([f'import {{ {dep_name} }} from "{dep_path}";', "", f"export default function {name}() {{", f"  return {dep_name}();", "}"])


class Base(UniverseBase):
    UNIVERSE_PREFIX = "reachmap-react-router-"
    REPO = "acme/web"

    def make_adapter(self, **kw):
        kw.setdefault("test_markers", ["__tests__/", ".test.", ".spec."])
        kw.setdefault("aliases_from", "tsconfig.json")
        return JsAdapter(self.REPO, self.url, self.git, **kw)

    def entry(self, glob="src/modules/*/routes.tsx", **kw):
        self.language = self.make_adapter(**kw)
        self.router = ReactRouterAdapter(self.REPO, self.language, glob)
        return self.router

    def reach(self, **kw):
        analysis = self.analyze(self.language)
        res = entrypoints.reach(analysis, self.language, self.router, **kw)
        evidence = [e for r in res.reached for c in r.area.chains for e in c]
        self.assertEqual(ss.evidence_problems({self.REPO: self.path}, evidence), [])
        return res

    def labels(self, res):
        return [r.area.label for r in res.reached]


class Globs(unittest.TestCase):
    def test_a_star_stays_in_one_folder_and_double_star_crosses_folders(self):
        rx = glob_regex("src/modules/*/routes.tsx")
        self.assertTrue(rx.match("src/modules/billing/routes.tsx"))
        self.assertFalse(rx.match("src/modules/billing/deep/routes.tsx"))
        self.assertFalse(rx.match("src/modules/billing/routes.tsx.bak"))
        self.assertEqual(rx.match("src/modules/billing/routes.tsx").group("m"), "billing")
        deep = glob_regex("src/**/routes.{ts,tsx}")
        self.assertTrue(deep.match("src/routes.ts") and deep.match("src/a/b/routes.tsx"))
        self.assertFalse(deep.match("lib/routes.ts"))

    def test_the_default_globs_are_used_when_the_config_names_none(self):
        self.assertEqual(ReactRouterAdapter("r", None).globs, DEFAULT_ROUTES_GLOBS)  # type: ignore[arg-type]


class Screens(Base):
    def routes_tree(self, **extra):
        files = {
            "tsconfig.json": TSCONFIG,
            "src/dep.ts": dep(),
            "src/modules/a/pages/A.tsx": user("A", dep_path="@/dep"),
            "src/modules/a/pages/B.tsx": user("B", dep_path="../../../dep"),
            "src/modules/a/pages/C.tsx": page("C"),
            "src/modules/a/routes.tsx": f([
                'import A from "./pages/A";', 'import B from "./pages/B";', 'import C from "./pages/C";', "", "export const routes = (", "  <>",
                '    <Route path="/a" element={<A />} />', '    <Route path="/a/second" element={<A />} />', '    <Route path="/z/third" element={<A />} />',
                '    <Route path="/b" element={<B />} />', '    <Route path="/c" element={<C />} />', "  </>", ");",
            ]),
        }
        files.update(extra)
        return files

    def changed_dep(self, files):
        self.repo({"files": files}, {"files": {"src/dep.ts": dep().replace("return 1;", "return 2;")}})
        self.entry()
        return self.reach()

    def test_a_page_on_three_routes_is_one_screen_with_two_also_mounted_and_a_chain_each(self):
        res = self.changed_dep(self.routes_tree())
        first = res.reached[0]
        self.assertEqual(self.labels(res), ["/a", "/b"])  # C does not use dep
        a = first.area
        self.assertEqual((a.label, a.also_mounted, len(a.chains), a.module), ("/a", ("/a/second", "/z/third"), 3, "a"))
        self.assertEqual([r.path for r in first.routes], ["/a", "/a/second", "/z/third"])
        self.assertEqual([[(e.path, e.line) for e in c] for c in a.chains], [
            [("src/modules/a/pages/A.tsx", 1), ("src/modules/a/routes.tsx", 1), ("src/modules/a/routes.tsx", 7)],
            [("src/modules/a/pages/A.tsx", 1), ("src/modules/a/routes.tsx", 1), ("src/modules/a/routes.tsx", 8)],
            [("src/modules/a/pages/A.tsx", 1), ("src/modules/a/routes.tsx", 1), ("src/modules/a/routes.tsx", 9)],
        ])
        self.assertEqual(a.route_line.line, 7)
        self.assertTrue(all(r.complete for r in res.reached))

    def test_two_changed_pages_of_one_routes_file_are_two_screens(self):
        self.repo({"files": self.routes_tree()}, {"files": {"src/modules/a/pages/B.tsx": user("B", dep_path="../../../dep").replace("return dep();", "return dep() + 1;"),
                                                           "src/modules/a/pages/C.tsx": page("C", "2")}})
        self.entry()
        self.assertEqual(self.labels(self.reach()), ["/b", "/c"])

    def test_the_route_table_has_every_leaf_with_its_page_and_evidence(self):
        self.repo({"files": self.routes_tree()})
        entry = self.entry()
        routes = entry.routes(None, self.sha("main"))
        self.assertEqual([(r.path, r.handler, r.target, r.module) for r in routes], [
            ("/a", "A", "src/modules/a/pages/A.tsx", "a"), ("/a/second", "A", "src/modules/a/pages/A.tsx", "a"), ("/z/third", "A", "src/modules/a/pages/A.tsx", "a"),
            ("/b", "B", "src/modules/a/pages/B.tsx", "a"), ("/c", "C", "src/modules/a/pages/C.tsx", "a"),
        ])
        self.assertEqual([e.line for e in routes[0].evidence], [1, 7])  # the import of the page, then the route line
        self.assertEqual(routes[0].line.line, 7)
        with self.assertRaises(ValueError):
            entry.routes("acme/other", self.sha("main"))

    def test_only_the_routes_that_render_a_changed_page_are_reached(self):
        self.repo({"files": self.routes_tree()}, {"files": {"src/modules/a/pages/C.tsx": page("C", "2")}})
        self.entry()
        res = self.reach()
        self.assertEqual(self.labels(res), ["/c"])
        self.assertEqual([(e.path, e.line) for e in res.reached[0].area.chains[0]], [("src/modules/a/routes.tsx", 3), ("src/modules/a/routes.tsx", 11)])

    def test_a_named_import_of_a_page_is_narrowed_by_the_names_that_arrive(self):
        pages = f(["export function PageA() {", "  return helper();", "}", "", "export function PageB() {", "  return 2;", "}", "", "function helper() {", "  return 1;", "}"])
        routes = f(['import { PageA, PageB } from "./pages";', "", "export const routes = (", "  <>", '    <Route path="/pa" element={<PageA />} />', '    <Route path="/pb" element={<PageB />} />', "  </>", ");"])
        self.repo({"files": {"src/modules/m/pages.tsx": pages, "src/modules/m/routes.tsx": routes}},
                  {"files": {"src/modules/m/pages.tsx": pages.replace("return 1;", "return 3;")}})
        self.entry()
        self.assertEqual(self.labels(self.reach()), ["/pa"])  # helper is used by PageA only

    def test_a_lazy_page_is_reached_through_its_dynamic_import(self):
        routes = f([
            'import React from "react";', "", 'const Lazy = React.lazy(() => import("./pages/Lazy"));', "", "export const routes = (", "  <>",
            '    <Route', '      path="/lazy"', "      element={", "        <React.Suspense fallback={null}>", "          <Lazy />", "        </React.Suspense>", "      }", "    />", "  </>", ");",
        ])
        files = {"src/modules/l/routes.tsx": routes, "src/modules/l/pages/Lazy.tsx": page("Lazy")}
        self.repo({"files": files}, {"files": {"src/modules/l/pages/Lazy.tsx": page("Lazy", "2")}})
        self.entry()
        res = self.reach()
        self.assertEqual(self.labels(res), ["/lazy"])
        self.assertEqual([(e.path, e.line) for e in res.reached[0].area.chains[0]], [("src/modules/l/routes.tsx", 3), ("src/modules/l/routes.tsx", 7)])

    def test_a_layout_route_and_its_child_are_two_screens_when_the_layout_changes_and_one_when_a_page_does(self):
        routes = f([
            'import Layout from "./Layout";', 'import List from "./List";', 'import Detail from "./Detail";', "", "export const routes = (",
            '  <Route path="/p" element={<Layout />}>', '    <Route index element={<List />} />', '    <Route path=":id" element={<Detail />} />', "  </Route>", ");",
        ])
        files = {"src/modules/p/routes.tsx": routes, "src/modules/p/Layout.tsx": page("Layout"), "src/modules/p/List.tsx": page("List"), "src/modules/p/Detail.tsx": page("Detail")}
        self.repo({"files": files}, {"files": {"src/modules/p/Layout.tsx": page("Layout", "2")}})
        self.entry()
        res = self.reach()
        self.assertEqual(self.labels(res), ["/p", "/p/:id"])  # each child is a screen of its own: the layout renders around it
        self.assertEqual([(e.path, e.line) for e in res.reached[1].area.chains[0]], [("src/modules/p/routes.tsx", 1), ("src/modules/p/routes.tsx", 6), ("src/modules/p/routes.tsx", 8)])
        self.repo({"files": files}, {"files": {"src/modules/p/Detail.tsx": page("Detail", "2")}})
        self.entry()
        self.assertEqual(self.labels(self.reach()), ["/p/:id"])

    def test_object_routes_in_a_router_file(self):
        router = f([
            'import Root from "./Root";', 'import Home from "./Home";', "", "export const router = createBrowserRouter([", "  {", '    path: "/",', "    element: <Root />,",
            "    children: [", "      { index: true, element: <Home /> },", "    ],", "  },", "]);",
        ])
        files = {"src/router.tsx": router, "src/Root.tsx": page("Root"), "src/Home.tsx": page("Home")}
        self.repo({"files": files}, {"files": {"src/Home.tsx": page("Home", "2")}})
        self.entry(glob="src/router.tsx")
        res = self.reach()
        self.assertEqual(self.labels(res), ["/"])
        # the import of the page, then the route that renders it (the index child), not its parent
        self.assertEqual([(e.path, e.line) for e in res.reached[0].area.chains[0]], [("src/router.tsx", 2), ("src/router.tsx", 9)])

    def screens_through_one_dependency(self, n):
        files = {"tsconfig.json": TSCONFIG, "src/dep.ts": dep()}
        imports, routes = [], ["export const routes = (", "  <>"]
        for i in range(n):
            files[f"src/modules/m/P{i}.tsx"] = user(f"P{i}", dep_path="../../dep")
            imports.append(f'import P{i} from "./P{i}";')
            routes.append(f'    <Route path="/p{i}" element={{<P{i} />}} />')
        files["src/modules/m/routes.tsx"] = f(imports + [""] + routes + ["  </>", ");"])
        return self.changed_dep(files)

    def test_the_priority_is_must_test_up_to_eight_screens_and_sample_past_that(self):
        res = self.screens_through_one_dependency(9)
        self.assertEqual((len(res.reached), {r.area.priority for r in res.reached}), (9, {"sample"}))
        res = self.screens_through_one_dependency(8)
        self.assertEqual((len(res.reached), {r.area.priority for r in res.reached}), (8, {"must_test"}))


class DirectChanges(Base):
    ROUTES = f([
        'import A from "./pages/A";', 'import B from "./pages/B";', "", "export const routes = (", "  <>", '    <Route path="/a" element={<A />} />',
        '    <Route', '      path="/b"', "      element={<B />}", "    />", "  </>", ");",
    ])

    def build(self, new, base=None):
        base = self.ROUTES if base is None else base
        files = {"src/modules/m/pages/A.tsx": page("A"), "src/modules/m/pages/B.tsx": page("B")}
        self.repo({"files": {**files, "src/modules/m/routes.tsx": base}}, {"files": {"src/modules/m/routes.tsx": new}})
        self.entry()
        return self.reach()

    def test_a_changed_attribute_of_a_route_reaches_that_route_only_and_the_chain_starts_at_the_line(self):
        res = self.build(self.ROUTES.replace('path="/a"', 'path="/a2"'))
        self.assertEqual(self.labels(res), ["/a2"])
        self.assertEqual([(e.path, e.line) for e in res.reached[0].area.chains[0]], [("src/modules/m/routes.tsx", 6)])

    def test_a_line_inside_a_multi_line_route_reaches_it(self):
        res = self.build(self.ROUTES.replace("      element={<B />}", "      element={<B x={1} />}"))
        self.assertEqual(self.labels(res), ["/b"])
        self.assertEqual([e.line for e in res.reached[0].area.chains[0]], [9, 7])

    def test_a_changed_import_of_a_page_reaches_the_routes_that_use_the_binding(self):
        res = self.build(self.ROUTES.replace('import A from "./pages/A";', 'import A from "./pages/A2";'), None)
        self.assertEqual(self.labels(res), ["/a"])

    def test_a_changed_line_that_belongs_to_no_route_reaches_every_route_of_the_file(self):
        res = self.build(self.ROUTES.replace("export const routes = (", "export const wrapped = ("))
        self.assertEqual(self.labels(res), ["/a", "/b"])

    def test_a_removed_route_reaches_nothing_and_is_a_removed_route(self):
        new = self.ROUTES.replace('    <Route path="/a" element={<A />} />\n', "")
        res = self.build(new)
        self.assertEqual(res.reached, ())
        removed, added = self.router.routes_changed(self.sha("main~1"), self.sha("main"))
        self.assertEqual(([r.path for r in removed], added), (["/a"], []))
        self.assertEqual({e.ref for e in removed[0].evidence}, {self.sha("main~1")})
        self.assertEqual(ss.evidence_problems({self.REPO: self.path}, [e for r in removed for e in r.evidence]), [])

    def test_a_removed_attribute_of_a_route_that_is_still_there_reaches_it(self):
        before = self.ROUTES.replace('      path="/b"\n', '      path="/b"\n      errorElement={<E />}\n')
        res = self.build(self.ROUTES, before)
        self.assertEqual(self.labels(res), ["/b"])  # the removed line sat in the route's own tag and the route is still there

    def test_a_removed_line_outside_every_route_reaches_every_route(self):
        base = self.ROUTES.replace("export const routes = (", "const debug = true;\nexport const routes = (")
        res = self.build(self.ROUTES, base)
        self.assertEqual(self.labels(res), ["/a", "/b"])  # a removed line that belonged to no route could have changed any of them

    def test_a_renamed_routes_file_is_read_at_the_head(self):
        old, new = "src/modules/x/routes.tsx", "src/modules/y/routes.tsx"
        files = {"src/modules/x/pages/A.tsx": page("A"), old: self.ROUTES.replace("./pages/B", "./pages/A").replace("import B", "import B2")}
        edited = files[old].replace('path="/a"', 'path="/a-new"')
        self.repo({"files": files}, {"delete": [old], "files": {new: edited}})
        self.entry()
        analysis = self.analyze(self.language)
        self.assertEqual(analysis.impact_for(new).status, "renamed")
        res = entrypoints.reach(analysis, self.language, self.router)
        self.assertEqual(self.labels(res), ["/a-new"])

    def test_an_added_route_is_an_added_route_and_the_area_of_its_line(self):
        new = self.ROUTES.replace("  </>", '    <Route path="/new" element={<A />} />\n  </>')
        res = self.build(new)
        self.assertEqual(self.labels(res), ["/new"])
        removed, added = self.router.routes_changed(self.sha("main~1"), self.sha("main"))
        self.assertEqual(([r.path for r in removed], [r.path for r in added]), ([], ["/new"]))
        self.assertEqual({e.ref for e in added[0].evidence}, {self.sha("main")})

    def test_without_a_pre_change_version_a_removed_route_is_not_computed_and_nothing_is_claimed(self):
        self.repo({"files": {"src/modules/m/pages/A.tsx": page("A"), "src/modules/m/pages/B.tsx": page("B"), "src/modules/m/routes.tsx": self.ROUTES}},
                  {"files": {"src/modules/m/routes.tsx": self.ROUTES.replace('    <Route path="/a" element={<A />} />\n', "")}})
        self.entry()
        analysis = self.analyze(self.language, pre=None)
        res = entrypoints.reach(analysis, self.language, self.router)
        self.assertEqual(res.reached, ())  # the removed line is only in the before text, which is not there
        self.assertEqual(self.router.routes_changed(None, self.sha("main")), ([], []))


class WhatTheRoutesFileUses(Base):
    """A change reaches a route through more than the page it renders: a component the routes file declares, a loader
    helper, a statement that runs when the file loads. Undecidable means affected."""

    def run_case(self, files, changed, glob="src/modules/*/routes.tsx"):
        self.repo({"files": files}, {"files": changed})
        self.entry(glob=glob)
        return self.reach()

    def test_a_component_declared_in_the_routes_file_that_uses_a_changed_import_reaches_its_routes(self):
        routes = f([
            'import Shell from "./Shell";', 'import A from "./pages/A";', "", "function Layout() {", "  return <Shell />;", "}", "",
            "export const routes = (", '  <Route path="/m" element={<Layout />}>', '    <Route path="a" element={<A />} />', "  </Route>", ");",
        ])
        files = {"src/modules/m/routes.tsx": routes, "src/modules/m/Shell.tsx": page("Shell"), "src/modules/m/pages/A.tsx": page("A")}
        res = self.run_case(files, {"src/modules/m/Shell.tsx": page("Shell", "2")})
        self.assertEqual(self.labels(res), ["/m/a"])  # a route under the layout that renders Shell
        self.assertEqual([(e.path, e.line) for e in res.reached[0].area.chains[0]][-3:], [("src/modules/m/routes.tsx", 1), ("src/modules/m/routes.tsx", 9), ("src/modules/m/routes.tsx", 10)])

    def test_a_loader_helper_that_wraps_a_lazy_import_reaches_the_route_that_renders_it(self):
        helper = f(["export function lazyPage(load: any) {", "  return load;", "}"])
        routes = f(['import { lazyPage } from "@/lazy";', "", 'const Reports = lazyPage(() => import("./pages/Reports"));', "", "export const routes = (", '  <Route path="/reports" element={<Reports />} />', ");"])
        files = {"tsconfig.json": TSCONFIG, "src/lazy.ts": helper, "src/modules/m/routes.tsx": routes, "src/modules/m/pages/Reports.tsx": page("Reports")}
        res = self.run_case(files, {"src/lazy.ts": helper.replace("return load;", "return load || 1;")})
        self.assertEqual(self.labels(res), ["/reports"])

    def test_a_wrapper_component_from_another_file_reaches_the_routes_that_use_it(self):
        guard = f(["export function RequireAuth(props: any) {", "  return props.children;", "}"])
        routes = f(['import { RequireAuth } from "@/auth";', 'import A from "./pages/A";', 'import B from "./pages/B";', "", "export const routes = (", "  <>",
                    '    <Route path="/a" element={<RequireAuth><A /></RequireAuth>} />', '    <Route path="/b" element={<B />} />', "  </>", ");"])
        files = {"tsconfig.json": TSCONFIG, "src/auth.tsx": guard, "src/modules/m/routes.tsx": routes, "src/modules/m/pages/A.tsx": page("A"), "src/modules/m/pages/B.tsx": page("B")}
        res = self.run_case(files, {"src/auth.tsx": guard.replace("props.children;", "props.children || null;")})
        self.assertEqual(self.labels(res), ["/a"])

    def test_a_statement_that_runs_when_the_routes_file_loads_reaches_every_route_of_it(self):
        cfg = f(["export function setup() {", "  return 1;", "}"])
        routes = f(['import { setup } from "@/cfg";', 'import A from "./pages/A";', 'import B from "./pages/B";', "", "setup();", "", "export const routes = (", "  <>",
                    '    <Route path="/a" element={<A />} />', '    <Route path="/b" element={<B />} />', "  </>", ");"])
        files = {"tsconfig.json": TSCONFIG, "src/cfg.ts": cfg, "src/modules/m/routes.tsx": routes, "src/modules/m/pages/A.tsx": page("A"), "src/modules/m/pages/B.tsx": page("B")}
        res = self.run_case(files, {"src/cfg.ts": cfg.replace("return 1;", "return 2;")})
        self.assertEqual(self.labels(res), ["/a", "/b"])
        self.assertTrue(res.walk.chains[0].reach.whole)

    def test_an_object_layout_route_without_a_path_is_reached_through_its_component(self):
        router = f([
            'import Layout from "./Layout";', 'import Home from "./Home";', "", "export const router = createBrowserRouter([", "  {", "    element: <Layout />,", "    children: [",
            '      { path: "/home", element: <Home /> },', "    ],", "  },", "]);",
        ])
        files = {"src/router.tsx": router, "src/Layout.tsx": page("Layout"), "src/Home.tsx": page("Home")}
        res = self.run_case(files, {"src/Layout.tsx": page("Layout", "2")}, glob="src/router.tsx")
        self.assertEqual(self.labels(res), ["/home"])

    def test_descendant_routes_in_a_component_are_not_claimed_as_absolute(self):
        routes = f([
            'import A from "./pages/A";', "", "function Inner() {", "  return (", "    <Routes>", '      <Route path="invoices" element={<A />} />', "    </Routes>", "  );", "}", "",
            "export const routes = (", '  <Route path="/billing/*" element={<Inner />} />', ");",
        ])
        files = {"src/modules/m/routes.tsx": routes, "src/modules/m/pages/A.tsx": page("A")}
        res = self.run_case(files, {"src/modules/m/pages/A.tsx": page("A", "2")})
        self.assertEqual(self.labels(res), ["/<unknown:mount>/invoices", "/billing/*"])  # the route that renders Inner reaches A too
        self.assertTrue(res.reached[0].routes[0].partial)
        self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in self.records], [("route_path_partial", "src/modules/m/routes.tsx:6", False)])

    def test_a_helper_the_routes_file_exports_carries_the_change_to_the_routes_files_that_import_it(self):
        a = f(['import { dep } from "../../dep";', "", "export function Frame() {", "  return dep();", "}", "", "export const routes = (", '  <Route path="/a" element={<Frame />} />', ");"])
        b = f(['import { Frame } from "../a/routes";', 'import B from "./B";', "", "export const routes = (", "  <>", '    <Route path="/b" element={<Frame />} />', '    <Route path="/b2" element={<B />} />', "  </>", ");"])
        files = {"tsconfig.json": TSCONFIG, "src/dep.ts": dep(), "src/modules/a/routes.tsx": a, "src/modules/b/routes.tsx": b, "src/modules/b/B.tsx": page("B")}
        res = self.run_case(files, {"src/dep.ts": dep().replace("return 1;", "return 2;")})
        self.assertEqual(self.labels(res), ["/a", "/b"])  # /b renders the Frame that a/routes.tsx exports; /b2 does not
        self.assertEqual(res.walk.chains[1].files, ("src/dep.ts", "src/modules/a/routes.tsx", "src/modules/b/routes.tsx"))
        self.assertNotIn("src/App.tsx", [p for c in res.walk.chains for p in c.files])

    def test_one_page_under_two_layouts_and_a_bare_route_is_one_screen(self):
        routes = f([
            'import L1 from "./L1";', 'import L2 from "./L2";', 'import A from "./pages/A";', "", "export const routes = (", "  <>",
            '  <Route path="/x" element={<L1 />}>', '    <Route path="a" element={<A />} />', "  </Route>",
            '  <Route path="/y" element={<L2 />}>', '    <Route path="a" element={<A />} />', "  </Route>",
            '  <Route path="/b" element={<A />} />', "  </>", ");",
        ])
        files = {"src/modules/m/routes.tsx": routes, "src/modules/m/L1.tsx": page("L1"), "src/modules/m/L2.tsx": page("L2"), "src/modules/m/pages/A.tsx": page("A")}
        res = self.run_case(files, {"src/modules/m/pages/A.tsx": page("A", "2")})
        (r,) = res.reached
        self.assertEqual((r.area.label, r.area.also_mounted, len(r.area.chains)), ("/b", ("/x/a", "/y/a"), 3))  # the layouts do not split the screen
        self.assertEqual([x.path for x in r.routes], ["/b", "/x/a", "/y/a"])

    def test_a_wrapped_page_is_the_screen_of_the_page_not_of_the_wrapper(self):
        guard = f(["export function RequireAuth(props: any) {", "  return props.children;", "}"])
        routes = f(['import { RequireAuth } from "@/auth";', 'import A from "./pages/A";', 'import B from "./pages/B";', "", "export const routes = (", "  <>",
                    '    <Route path="/a" element={<RequireAuth><A /></RequireAuth>} />', '    <Route path="/b" element={<RequireAuth><B /></RequireAuth>} />', "  </>", ");"])
        files = {"tsconfig.json": TSCONFIG, "src/auth.tsx": guard, "src/modules/m/routes.tsx": routes, "src/modules/m/pages/A.tsx": page("A"), "src/modules/m/pages/B.tsx": page("B")}
        res = self.run_case(files, {"src/auth.tsx": guard.replace("props.children;", "props.children || null;")})
        self.assertEqual([(r.area.label, r.area.also_mounted) for r in res.reached], [("/a", ()), ("/b", ())])  # two pages behind one wrapper
        self.repo({"files": files}, {"files": {"src/modules/m/pages/A.tsx": page("A", "2")}})
        self.entry()
        self.assertEqual([r.routes[0].target for r in self.reach().reached], ["src/modules/m/pages/A.tsx"])  # the route's page is the innermost component


class Problems(Base):
    def test_a_routes_file_that_does_not_lex_claims_no_route_and_blocks(self):
        broken = 'import A from "./pages/A";\nexport const routes = <Route path="/a" element={<A />} />;\nconst s = "unterminated;\n'
        self.repo({"files": {"src/modules/m/pages/A.tsx": page("A"), "src/modules/m/routes.tsx": broken}},
                  {"files": {"src/modules/m/pages/A.tsx": page("A", "2")}})
        self.entry()
        res = self.reach()
        self.assertEqual(res.reached, ())
        self.assertIn(("parse_degraded", "src/modules/m/routes.tsx", True), [(r.kind, r.subject, r.blocks_selection) for r in self.records])
        self.assertEqual(self.router.routes(None, self.sha("main")), [])

    def test_a_path_that_is_not_a_literal_is_marked_and_recorded(self):
        routes = f(['import A from "./pages/A";', "", "export const routes = (", "  <>", "    <Route path={PATHS.a} element={<A />} />", "  </>", ");"])
        self.repo({"files": {"src/modules/m/pages/A.tsx": page("A"), "src/modules/m/routes.tsx": routes}},
                  {"files": {"src/modules/m/pages/A.tsx": page("A", "2")}})
        self.entry()
        res = self.reach()
        self.assertEqual(self.labels(res), ["<unknown:PATHS.a>"])
        self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in self.records], [("route_path_partial", "src/modules/m/routes.tsx:5", False)])
        self.assertTrue(res.reached[0].routes[0].partial)

    def test_a_page_the_routes_file_imports_from_a_package_is_no_page(self):
        routes = f(['import { Navigate } from "react-router-dom";', 'import A from "./pages/A";', "", "export const routes = (", "  <>", '    <Route path="/old" element={<Navigate to="/a" />} />',
                    '    <Route path="/a" element={<A />} />', "  </>", ");"])
        self.repo({"files": {"src/modules/m/pages/A.tsx": page("A"), "src/modules/m/routes.tsx": routes}},
                  {"files": {"src/modules/m/pages/A.tsx": page("A", "2")}})
        entry = self.entry()
        self.assertEqual([(r.path, r.target) for r in entry.routes(None, self.sha("main"))], [("/old", ""), ("/a", "src/modules/m/pages/A.tsx")])
        self.assertEqual(self.labels(self.reach()), ["/a"])

    def test_an_entry_file_that_cannot_be_read_makes_the_result_incomplete_and_is_not_taken_for_empty(self):
        from unittest import mock

        from reachmap.model import ReadResult

        files = {"tsconfig.json": TSCONFIG, "src/dep.ts": dep(), "src/modules/m/pages/A.tsx": user("A", dep_path="@/dep"),
                 "src/modules/m/routes.tsx": f(['import A from "./pages/A";', 'export const routes = <Route path="/a" element={<A />} />;'])}
        self.repo({"files": files}, {"files": {"src/dep.ts": dep().replace("return 1;", "return 2;")}})
        self.entry()
        real = self.language.file_text

        def unreadable(sha, path):
            return ReadResult.unreadable("simulated failure") if path.endswith("routes.tsx") else real(sha, path)

        with mock.patch.object(self.language, "file_text", unreadable):
            res = self.reach()
        self.assertEqual(res.reached, ())
        self.assertFalse(res.complete)  # the screens behind the file are not established: that is not "no screen"
        self.assertTrue(any("routes.tsx" in why for why in res.reasons))

    def test_the_result_is_complete_when_nothing_was_cut_short(self):
        self.repo({"files": {"src/modules/m/pages/A.tsx": page("A"), "src/modules/m/routes.tsx": 'import A from "./pages/A";\nexport const routes = <Route path="/a" element={<A />} />;\n'}},
                  {"files": {"src/modules/m/pages/A.tsx": page("A", "2")}})
        self.entry()
        res = self.reach()
        self.assertEqual((self.labels(res), res.complete, res.reasons), (["/a"], True, ()))

    def test_a_routes_file_that_does_not_lex_is_recorded_once_and_the_result_is_incomplete(self):
        broken = 'import A from "./pages/A";\nexport const routes = <Route path="/a" element={<A />} />;\nconst s = "unterminated;\n'
        self.repo({"files": {"src/modules/m/pages/A.tsx": page("A"), "src/modules/m/routes.tsx": broken}}, {"files": {"src/modules/m/pages/A.tsx": page("A", "2")}})
        self.entry()
        res = self.reach()
        degraded = [(r.kind, r.subject, r.blocks_selection) for r in self.records if r.kind == "parse_degraded"]
        self.assertEqual(degraded, [("parse_degraded", "src/modules/m/routes.tsx", True)])  # the JavaScript adapter and this one say it once
        self.assertFalse(res.complete)


if __name__ == "__main__":
    unittest.main()
