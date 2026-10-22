"""The file-system routing of Next.js, read from a list of paths alone (`reachmap.adapters.nextjs.routes`): which files are
pages, route handlers, wrappers (layouts and their kin) or API routes, and the URL each one answers.

No file is read and no repository is built here: the table is a pure function of the paths. The adapter on real
repositories is in test_nextjs.py, the fixture scenarios in test_nextjs_scenarios.py.
"""

import unittest

from reachmap.adapters.nextjs import routes as nr


def table(*paths, **kw):
    return nr.classify(paths, **kw)


def entry(t, path):
    return t.by_path[path]


class AppRouterPages(unittest.TestCase):
    def test_a_page_file_is_the_screen_of_its_folder(self):
        t = table("app/page.tsx", "app/about/page.tsx", "app/products/[slug]/page.tsx")
        self.assertEqual([entry(t, p).paths for p in ("app/page.tsx", "app/about/page.tsx", "app/products/[slug]/page.tsx")],
                         [("/",), ("/about",), ("/products/:slug",)])
        self.assertEqual({entry(t, p).role for p in t.by_path}, {"page"})

    def test_a_route_group_is_not_part_of_the_url_and_names_the_module(self):
        t = table("app/(shop)/cart/page.tsx", "app/(marketing)/about/page.tsx", "app/(shop)/page.tsx")
        self.assertEqual(entry(t, "app/(shop)/cart/page.tsx").paths, ("/cart",))
        self.assertEqual(entry(t, "app/(shop)/page.tsx").paths, ("/",))
        self.assertEqual(entry(t, "app/(shop)/cart/page.tsx").module, "shop")
        self.assertEqual(entry(t, "app/(marketing)/about/page.tsx").module, "marketing")

    def test_without_a_group_the_module_is_the_first_segment(self):
        t = table("app/dashboard/settings/page.tsx", "app/page.tsx", "app/[locale]/page.tsx")
        self.assertEqual(entry(t, "app/dashboard/settings/page.tsx").module, "dashboard")
        self.assertEqual(entry(t, "app/page.tsx").module, "")
        self.assertEqual(entry(t, "app/[locale]/page.tsx").module, "locale")

    def test_catch_all_and_optional_catch_all(self):
        t = table("app/docs/[...slug]/page.tsx", "app/help/[[...topic]]/page.tsx", "app/[[...all]]/page.tsx")
        self.assertEqual(entry(t, "app/docs/[...slug]/page.tsx").paths, ("/docs/*",))
        self.assertEqual(entry(t, "app/help/[[...topic]]/page.tsx").paths, ("/help", "/help/*"))
        self.assertEqual(entry(t, "app/[[...all]]/page.tsx").paths, ("/", "/*"))

    def test_a_parallel_slot_is_not_part_of_the_url(self):
        t = table("app/dashboard/page.tsx", "app/dashboard/@analytics/page.tsx", "app/dashboard/@analytics/revenue/page.tsx")
        self.assertEqual(entry(t, "app/dashboard/@analytics/page.tsx").paths, ("/dashboard",))
        self.assertEqual(entry(t, "app/dashboard/@analytics/revenue/page.tsx").paths, ("/dashboard/revenue",))
        self.assertTrue(entry(t, "app/dashboard/@analytics/page.tsx").slot)
        self.assertFalse(entry(t, "app/dashboard/page.tsx").slot)

    def test_intercepting_routes_take_the_url_they_intercept_and_are_partial(self):
        t = table("app/feed/@modal/(..)photos/[id]/page.tsx", "app/feed/@modal/(.)detail/page.tsx", "app/a/b/(..)(..)top/page.tsx",
                  "app/a/b/(...)root/page.tsx", "app/photos/[id]/page.tsx")
        up = entry(t, "app/feed/@modal/(..)photos/[id]/page.tsx")
        self.assertEqual((up.paths, up.intercepting), (("/photos/:id",), True))
        same = entry(t, "app/feed/@modal/(.)detail/page.tsx")
        self.assertEqual((same.paths, same.intercepting), (("/feed/detail",), True))
        self.assertEqual(entry(t, "app/a/b/(..)(..)top/page.tsx").paths, ("/top",))
        self.assertEqual(entry(t, "app/a/b/(...)root/page.tsx").paths, ("/root",))
        self.assertFalse(entry(t, "app/photos/[id]/page.tsx").intercepting)

    def test_a_private_folder_and_everything_below_it_is_not_routed(self):
        t = table("app/_components/page.tsx", "app/shop/_lib/route.ts", "app/shop/page.tsx", "app/_x/layout.tsx")
        self.assertEqual(sorted(t.by_path), ["app/shop/page.tsx"])

    def test_only_the_special_file_names_are_routed(self):
        t = table("app/shop/page.tsx", "app/shop/Page.tsx", "app/shop/page-header.tsx", "app/shop/pages.tsx", "app/shop/helpers.ts",
                  "app/shop/page.css", "app/shop/page.d.ts", "app/shop/page.test.tsx", "app/shop/mypage.tsx")
        self.assertEqual(sorted(t.by_path), ["app/shop/page.tsx"])

    def test_page_extensions(self):
        t = table("app/a/page.js", "app/b/page.jsx", "app/c/page.ts", "app/d/page.tsx", "app/e/page.mdx", "app/f/page.vue")
        self.assertEqual(sorted(t.by_path), ["app/a/page.js", "app/b/page.jsx", "app/c/page.ts", "app/d/page.tsx"])
        t = table("app/a/page.tsx", "app/b/page.js", extensions=("tsx",))
        self.assertEqual(sorted(t.by_path), ["app/a/page.tsx"])

    def test_a_test_file_is_not_a_route_even_in_a_routing_folder(self):
        t = table("app/a/page.tsx", "app/b/page.tsx", is_test=lambda p: p.startswith("app/b/"))
        self.assertEqual(sorted(t.by_path), ["app/a/page.tsx"])

    def test_a_segment_that_cannot_be_read_as_a_route_part_is_kept_and_marked_unknown(self):
        t = table("app/post-[id]/page.tsx", "app/[a]b/page.tsx")
        e = entry(t, "app/post-[id]/page.tsx")
        self.assertEqual((e.paths, e.unknown), (("/<unknown:post-[id]>",), ("post-[id]",)))
        self.assertEqual(entry(t, "app/[a]b/page.tsx").unknown, ("[a]b",))

    def test_a_catch_all_that_is_not_the_last_segment_is_unknown(self):
        t = table("app/[...rest]/more/page.tsx")
        e = entry(t, "app/[...rest]/more/page.tsx")
        self.assertEqual(e.unknown, ("[...rest]",))


class AppRouterHandlersAndWrappers(unittest.TestCase):
    def test_route_files_are_api_routes_in_brace_style(self):
        t = table("app/api/orders/[id]/route.ts", "app/api/files/[...path]/route.ts", "app/api/h/[[...rest]]/route.js")
        self.assertEqual(entry(t, "app/api/orders/[id]/route.ts").role, "route")
        self.assertEqual(entry(t, "app/api/orders/[id]/route.ts").paths, ("/api/orders/{id}",))
        self.assertEqual(entry(t, "app/api/files/[...path]/route.ts").paths, ("/api/files/{path:path}",))
        self.assertEqual(entry(t, "app/api/h/[[...rest]]/route.js").paths, ("/api/h", "/api/h/{rest:path}"))

    def test_a_route_group_and_a_slot_are_not_part_of_an_api_path(self):
        t = table("app/(v1)/api/ping/route.ts")
        self.assertEqual(entry(t, "app/(v1)/api/ping/route.ts").paths, ("/api/ping",))

    def test_wrappers_and_their_scope(self):
        names = ("layout", "template", "loading", "error", "not-found", "global-error", "forbidden", "unauthorized")
        t = table(*(f"app/shop/{n}.tsx" for n in names), "app/shop/x/page.tsx", "app/other/page.tsx")
        for n in names:
            e = entry(t, f"app/shop/{n}.tsx")
            self.assertEqual((e.role, e.kind, e.scope), ("wrapper", n, ("shop",)), n)
        self.assertEqual([p.path for p in t.wrapped(entry(t, "app/shop/layout.tsx"))], ["app/shop/x/page.tsx"])

    def test_a_layout_wraps_every_page_below_its_folder_groups_and_slots_included(self):
        t = table("app/layout.tsx", "app/(shop)/layout.tsx", "app/(shop)/cart/page.tsx", "app/(marketing)/about/page.tsx",
                  "app/dashboard/layout.tsx", "app/dashboard/page.tsx", "app/dashboard/@team/page.tsx", "app/page.tsx")
        under = lambda w: sorted(p.path for p in t.wrapped(entry(t, w)))  # noqa: E731
        self.assertEqual(under("app/layout.tsx"), ["app/(marketing)/about/page.tsx", "app/(shop)/cart/page.tsx", "app/dashboard/@team/page.tsx", "app/dashboard/page.tsx", "app/page.tsx"])
        self.assertEqual(under("app/(shop)/layout.tsx"), ["app/(shop)/cart/page.tsx"])
        self.assertEqual(under("app/dashboard/layout.tsx"), ["app/dashboard/@team/page.tsx", "app/dashboard/page.tsx"])

    def test_a_layout_does_not_wrap_a_route_handler_or_a_page_of_the_other_router(self):
        t = table("app/layout.tsx", "app/api/x/route.ts", "app/a/page.tsx", "pages/legacy/index.tsx")
        self.assertEqual([p.path for p in t.wrapped(entry(t, "app/layout.tsx"))], ["app/a/page.tsx"])

    def test_default_in_a_slot_is_scoped_to_the_folder_that_holds_the_slot(self):
        t = table("app/dashboard/@team/default.tsx", "app/dashboard/page.tsx", "app/dashboard/x/page.tsx", "app/other/page.tsx")
        e = entry(t, "app/dashboard/@team/default.tsx")
        self.assertEqual((e.role, e.kind, e.scope), ("wrapper", "default", ("dashboard",)))
        self.assertEqual(sorted(p.path for p in t.wrapped(e)), ["app/dashboard/page.tsx", "app/dashboard/x/page.tsx"])

    def test_wrapper_names_in_a_private_folder_or_with_another_extension_are_not_wrappers(self):
        t = table("app/_ui/layout.tsx", "app/a/layout.css", "app/a/layouts.tsx", "app/a/page.tsx")
        self.assertEqual(sorted(t.by_path), ["app/a/page.tsx"])


class PagesRouter(unittest.TestCase):
    def test_files_are_routes_and_index_is_the_folder(self):
        t = table("pages/index.tsx", "pages/legacy/index.tsx", "pages/legacy/[id].tsx", "pages/legacy/archive/[...path].tsx",
                  "pages/docs/[[...slug]].tsx", "pages/404.tsx", "pages/about.js")
        got = {p: e.paths for p, e in t.by_path.items()}
        self.assertEqual(got, {
            "pages/index.tsx": ("/",), "pages/legacy/index.tsx": ("/legacy",), "pages/legacy/[id].tsx": ("/legacy/:id",),
            "pages/legacy/archive/[...path].tsx": ("/legacy/archive/*",), "pages/docs/[[...slug]].tsx": ("/docs", "/docs/*"),
            "pages/404.tsx": ("/404",), "pages/about.js": ("/about",),
        })
        self.assertEqual({e.role for e in t.by_path.values()}, {"page"})
        self.assertEqual({e.router for e in t.by_path.values()}, {"pages"})

    def test_app_and_document_are_wrappers_of_every_page_and_error_is_not_a_route(self):
        t = table("pages/_app.tsx", "pages/_document.tsx", "pages/_error.tsx", "pages/a.tsx", "pages/api/x.ts", "app/b/page.tsx")
        self.assertEqual((entry(t, "pages/_app.tsx").role, entry(t, "pages/_app.tsx").kind), ("wrapper", "_app"))
        self.assertEqual((entry(t, "pages/_document.tsx").role, entry(t, "pages/_document.tsx").kind), ("wrapper", "_document"))
        self.assertNotIn("pages/_error.tsx", t.by_path)
        self.assertEqual([p.path for p in t.wrapped(entry(t, "pages/_app.tsx"))], ["pages/a.tsx"])
        self.assertEqual([p.path for p in t.wrapped(entry(t, "pages/_document.tsx"))], ["pages/a.tsx"])

    def test_api_files_are_api_routes(self):
        t = table("pages/api/health.ts", "pages/api/legacy/items/[id].ts", "pages/api/files/[...path].ts", "pages/api/index.ts", "pages/api/s/[[...r]].js")
        self.assertEqual({p: (e.role, e.paths) for p, e in t.by_path.items()}, {
            "pages/api/health.ts": ("api", ("/api/health",)), "pages/api/legacy/items/[id].ts": ("api", ("/api/legacy/items/{id}",)),
            "pages/api/files/[...path].ts": ("api", ("/api/files/{path:path}",)), "pages/api/index.ts": ("api", ("/api",)),
            "pages/api/s/[[...r]].js": ("api", ("/api/s", "/api/s/{r:path}")),
        })

    def test_module_is_the_first_segment_and_an_api_route_skips_the_api_folder(self):
        t = table("pages/legacy/[id].tsx", "pages/index.tsx", "pages/api/legacy/status.ts", "pages/api/ping.ts", "app/api/orders/route.ts")
        self.assertEqual([t.by_path[p].module for p in ("pages/legacy/[id].tsx", "pages/index.tsx", "pages/api/legacy/status.ts", "pages/api/ping.ts", "app/api/orders/route.ts")],
                         ["legacy", "", "legacy", "ping", "orders"])

    def test_a_page_that_is_a_test_or_a_declaration_file_is_not_a_route(self):
        t = table("pages/a.tsx", "pages/a.test.tsx", "pages/a.d.ts", "pages/styles.css", "pages/a.spec.js", is_test=lambda p: ".test." in p or ".spec." in p)
        self.assertEqual(sorted(t.by_path), ["pages/a.tsx"])


class RootsAndSrc(unittest.TestCase):
    def test_src_app_and_src_pages_are_read_when_the_root_has_none(self):
        t = table("src/app/page.tsx", "src/pages/legacy.tsx", "src/app/layout.tsx")
        self.assertEqual((t.app_dir, t.pages_dir), ("src/app", "src/pages"))
        self.assertEqual(entry(t, "src/app/page.tsx").paths, ("/",))
        self.assertEqual(entry(t, "src/pages/legacy.tsx").paths, ("/legacy",))

    def test_the_root_folders_win_over_src(self):
        t = table("app/page.tsx", "src/app/page.tsx", "pages/a.tsx", "src/pages/b.tsx")
        self.assertEqual((t.app_dir, t.pages_dir), ("app", "pages"))
        self.assertEqual(sorted(t.by_path), ["app/page.tsx", "pages/a.tsx"])

    def test_a_configured_root_holds_the_app(self):
        t = table("apps/web/src/app/page.tsx", "apps/admin/app/page.tsx", "app/page.tsx", root="apps/web")
        self.assertEqual(sorted(t.by_path), ["apps/web/src/app/page.tsx"])
        self.assertEqual(entry(t, "apps/web/src/app/page.tsx").paths, ("/",))

    def test_files_outside_the_router_folders_are_not_routes(self):
        t = table("src/components/layout.tsx", "src/lib/route.ts", "src/utils/page.tsx", "lib/pages/x.tsx", "src/app/page.tsx",
                  "components/page.tsx", "src/middleware.ts/x.tsx")
        self.assertEqual(sorted(t.by_path), ["src/app/page.tsx"])

    def test_nothing_is_routed_when_there_is_no_router_folder(self):
        t = table("src/index.ts", "package.json")
        self.assertEqual((t.app_dir, t.pages_dir, list(t.by_path)), (None, None, []))


class Middleware(unittest.TestCase):
    def test_middleware_at_the_project_root_or_src_wraps_every_route(self):
        t = table("src/middleware.ts", "src/app/page.tsx", "src/app/api/x/route.ts", "src/pages/a.tsx")
        m = entry(t, "src/middleware.ts")
        self.assertEqual((m.role, m.kind), ("wrapper", "middleware"))
        self.assertEqual(sorted(p.path for p in t.wrapped(m)), ["src/app/api/x/route.ts", "src/app/page.tsx", "src/pages/a.tsx"])

    def test_a_file_called_middleware_anywhere_else_is_not(self):
        t = table("src/utils/middleware.ts", "src/lib/middleware.js", "src/app/page.tsx", "src/app/middleware.ts", "src/middleware/index.ts")
        self.assertEqual(sorted(t.by_path), ["src/app/page.tsx"])

    def test_the_root_one_is_found_when_there_is_no_src(self):
        t = table("middleware.js", "app/page.tsx")
        self.assertEqual(entry(t, "middleware.js").kind, "middleware")

    def test_only_the_one_beside_the_router_folders_counts(self):
        t = table("middleware.ts", "src/middleware.ts", "src/app/page.tsx")
        self.assertEqual([p for p in t.by_path if p.endswith("middleware.ts")], ["src/middleware.ts"])


class MetadataAndProxy(unittest.TestCase):
    def test_the_metadata_routes_are_get_routes_at_the_url_the_file_is_served_at(self):
        t = table("src/app/sitemap.ts", "src/app/blog/robots.ts", "src/app/(g)/manifest.js", "src/app/icon.tsx", "src/app/blog/[slug]/opengraph-image.tsx",
                  "src/app/apple-icon.tsx", "src/app/twitter-image.tsx")
        got = {p: (e.role, e.kind, e.paths, e.module) for p, e in t.by_path.items()}
        self.assertEqual(got, {
            "src/app/sitemap.ts": ("route", "metadata", ("/sitemap.xml",), ""),
            "src/app/blog/robots.ts": ("route", "metadata", ("/blog/robots.txt",), "blog"),
            "src/app/(g)/manifest.js": ("route", "metadata", ("/manifest.webmanifest",), "g"),
            "src/app/icon.tsx": ("route", "metadata", ("/icon",), ""),
            "src/app/blog/[slug]/opengraph-image.tsx": ("route", "metadata", ("/blog/{slug}/opengraph-image",), "blog"),
            "src/app/apple-icon.tsx": ("route", "metadata", ("/apple-icon",), ""),
            "src/app/twitter-image.tsx": ("route", "metadata", ("/twitter-image",), ""),
        })

    def test_a_metadata_name_outside_the_app_folder_or_in_a_private_folder_is_nothing(self):
        t = table("src/app/page.tsx", "src/lib/sitemap.ts", "src/app/_x/sitemap.ts", "src/components/icon.tsx", "src/app/icons.tsx", "src/app/sitemap.xml")
        self.assertEqual(sorted(t.by_path), ["src/app/page.tsx"])

    def test_a_file_in_the_pages_router_called_sitemap_is_a_page(self):
        t = table("src/pages/sitemap.ts", "src/pages/a.tsx")
        self.assertEqual({p: e.role for p, e in t.by_path.items()}, {"src/pages/sitemap.ts": "page", "src/pages/a.tsx": "page"})

    def test_proxy_is_the_middleware_of_next_16_and_only_beside_the_router_folders(self):
        t = table("src/proxy.ts", "src/utils/proxy.ts", "src/app/page.tsx", "src/lib/proxy.js")
        self.assertEqual({p: (e.role, e.kind) for p, e in t.by_path.items()}, {"src/proxy.ts": ("wrapper", "middleware"), "src/app/page.tsx": ("page", "page")})


class InstrumentationApiAndRoots(unittest.TestCase):
    def test_instrumentation_beside_the_router_folders_wraps_everything_like_middleware(self):
        t = table("src/instrumentation.ts", "src/app/page.tsx", "src/app/api/x/route.ts", "src/pages/a.tsx", "src/lib/instrumentation.ts", "src/app/instrumentation.ts")
        e = entry(t, "src/instrumentation.ts")
        self.assertEqual((e.role, e.kind), ("wrapper", "instrumentation"))
        self.assertEqual(sorted(p.path for p in t.wrapped(e)), ["src/app/api/x/route.ts", "src/app/page.tsx", "src/pages/a.tsx"])
        self.assertNotIn("src/lib/instrumentation.ts", t.by_path)
        self.assertNotIn("src/app/instrumentation.ts", t.by_path)

    def test_forbidden_and_unauthorized_wrap_the_pages_below_their_folder(self):
        t = table("app/forbidden.tsx", "app/unauthorized.tsx", "app/a/page.tsx", "app/a/forbidden.tsx", "app/b/page.tsx")
        self.assertEqual(sorted(p.path for p in t.wrapped(entry(t, "app/a/forbidden.tsx"))), ["app/a/page.tsx"])
        self.assertEqual(len(t.wrapped(entry(t, "app/unauthorized.tsx"))), 2)

    def test_pages_api_ts_is_the_api_route_of_slash_api_itself(self):
        t = table("pages/api.ts", "pages/api.tsx", "pages/api-docs.tsx", "pages/api/index.ts")
        self.assertEqual({p: (e.role, e.paths) for p, e in t.by_path.items()}, {
            "pages/api.ts": ("api", ("/api",)), "pages/api.tsx": ("api", ("/api",)), "pages/api-docs.tsx": ("page", ("/api-docs",)), "pages/api/index.ts": ("api", ("/api",)),
        })

    def test_the_middleware_root_is_the_parent_of_the_pages_folder_else_of_the_app_folder(self):
        t = table("pages/a.tsx", "src/app/page.tsx", "middleware.ts", "src/middleware.ts")  # Next: join(pagesDir || appDir, "..")
        self.assertEqual([p for p in t.by_path if p.endswith("middleware.ts")], ["middleware.ts"])
        t = table("app/page.tsx", "src/pages/a.tsx", "middleware.ts", "src/middleware.ts")
        self.assertEqual([p for p in t.by_path if p.endswith("middleware.ts")], ["src/middleware.ts"])
        t = table("src/app/page.tsx", "middleware.ts", "src/middleware.ts")
        self.assertEqual([p for p in t.by_path if p.endswith("middleware.ts")], ["src/middleware.ts"])


class Conflicts(unittest.TestCase):
    def test_a_page_and_a_route_file_in_one_folder_answer_one_url(self):
        t = table("app/a/page.tsx", "app/a/route.ts", "app/b/page.tsx")
        self.assertEqual(t.conflicts, {"app/a/page.tsx": ("app/a/route.ts",), "app/a/route.ts": ("app/a/page.tsx",)})

    def test_an_app_router_page_and_a_pages_router_page_of_one_url(self):
        t = table("app/about/page.tsx", "pages/about.tsx", "app/x/page.tsx", "pages/y.tsx")
        self.assertEqual(t.conflicts, {"app/about/page.tsx": ("pages/about.tsx",), "pages/about.tsx": ("app/about/page.tsx",)})

    def test_two_route_groups_that_answer_one_url_and_parameter_names_that_differ(self):
        t = table("app/(a)/x/page.tsx", "app/(b)/x/page.tsx", "app/p/[id]/page.tsx", "app/p/[slug]/route.ts")
        self.assertEqual(sorted(t.conflicts), ["app/(a)/x/page.tsx", "app/(b)/x/page.tsx", "app/p/[id]/page.tsx", "app/p/[slug]/route.ts"])

    def test_a_route_handler_and_a_pages_api_file_of_one_url(self):
        t = table("app/api/x/route.ts", "pages/api/x.ts", "app/api/y/route.ts")
        self.assertEqual(t.conflicts, {"app/api/x/route.ts": ("pages/api/x.ts",), "pages/api/x.ts": ("app/api/x/route.ts",)})

    def test_a_slot_page_an_intercepting_page_and_a_page_of_another_url_are_no_conflict(self):
        t = table("app/d/page.tsx", "app/d/@s/page.tsx", "app/feed/@modal/(..)photos/[id]/page.tsx", "app/photos/[id]/page.tsx", "app/sitemap.ts", "app/z/page.tsx")
        self.assertEqual(t.conflicts, {})

    def test_a_sitemap_and_a_page_of_other_names_are_no_conflict(self):
        self.assertEqual(table("app/sitemap.ts", "app/sitemap.xml/page.tsx").conflicts, {})


class Names(unittest.TestCase):
    def test_http_methods_are_exact_upper_case_names(self):
        self.assertEqual(nr.HTTP_METHODS, ("GET", "HEAD", "OPTIONS", "POST", "PUT", "DELETE", "PATCH"))
        self.assertTrue(nr.is_method("GET") and nr.is_method("PATCH"))
        for name in ("get", "Get", "handler", "GETS", "dynamic", "default", "POST_URL", "TRACE"):
            self.assertFalse(nr.is_method(name), name)

    def test_reserved_exports_of_an_entry_file(self):
        for name in ("default", "metadata", "generateMetadata", "generateStaticParams", "dynamic", "revalidate", "config",
                     "getServerSideProps", "getStaticProps", "getStaticPaths", "runtime", "GET", "POST"):
            self.assertTrue(nr.is_reserved_export(name), name)
        for name in ("formatTitle", "helper", "Component", "get", "handler"):
            self.assertFalse(nr.is_reserved_export(name), name)

    def test_ordering_is_by_path(self):
        t = table("app/z/page.tsx", "app/a/page.tsx", "pages/b.tsx")
        self.assertEqual([e.path for e in t.entries], sorted(e.path for e in t.entries))


if __name__ == "__main__":
    unittest.main()
