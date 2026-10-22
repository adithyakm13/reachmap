"""The Express adapter after the independent review: the route-change guard (an unreadable router removes nothing), an app handed to a method of an
imported module or object, a mount prefix that is not a visible constant, a router added to through a re-export barrel, `new Router()`, one hop of
re-export for the framework's own names, chained route calls, and Express path patterns.
"""

import unittest

from tests.test_express import EXPRESS, TSCONFIG, Base, ts


def two(before, after):
    return {"files": {"tsconfig.json": TSCONFIG, **before}}, {"files": after}


class Guard(Base):
    ROUTER = ts('import { Router } from "express";', "export const r = Router();", 'r.get("/x", h);', 'r.get("/y", h);')
    APP = ts(EXPRESS, 'import { r } from "./users";', "const app = express();", 'app.use("/m", r);')
    OTHER = ts(EXPRESS, "export const o = Router();", 'o.get("/o", h);')

    def changes(self, before, after):
        base, head = two(before, after)
        self.repo(base, head)
        ex = self.entry()
        return ex.routes_changed(self.sha("main~1"), self.sha("main"))

    def withheld(self):
        return [(r.subject, r.blocks_selection) for r in self.records if r.kind == "route_change_withheld"]

    def test_the_adapter_guards_its_own_changes(self):
        self.repo({"files": {"src/app.ts": self.APP, "src/users.ts": self.ROUTER}})
        ex = self.entry()
        self.assertTrue(ex.withholds_unread)
        self.assertEqual(ex.routes_complete(self.sha("main")), (True, []))

    def test_a_router_that_does_not_lex_at_the_head_removes_nothing_and_says_so(self):
        removed, added = self.changes({"src/app.ts": self.APP, "src/users.ts": self.ROUTER}, {"src/users.ts": self.ROUTER + "const s = `open\n"})
        self.assertEqual((removed, added), ([], []))
        self.assertEqual(self.withheld(), [(self.REPO, True)])

    def test_an_unrelated_unreadable_file_does_not_withhold_a_real_removal(self):
        before = {"src/app.ts": self.APP, "src/users.ts": self.ROUTER, "src/zzz.ts": self.OTHER}
        after = {"src/users.ts": self.ROUTER.replace('r.get("/y", h);\n', ""), "src/zzz.ts": self.OTHER + "const s = `open\n"}
        removed, added = self.changes(before, after)
        self.assertEqual(([r.path for r in removed], added), (["/m/y"], []))
        self.assertEqual(self.withheld(), [])

    def test_a_real_removal_between_two_readable_tables_is_still_a_removal_and_an_addition_is_one(self):
        before = {"src/app.ts": self.APP, "src/users.ts": self.ROUTER}
        after = {"src/users.ts": self.ROUTER.replace('r.get("/y", h);', 'r.get("/z", h);')}
        removed, added = self.changes(before, after)
        self.assertEqual(([r.path for r in removed], [r.path for r in added]), (["/m/y"], ["/m/z"]))
        self.assertEqual(self.withheld(), [])

    def test_an_app_that_does_not_lex_at_the_head_withholds_the_routes_it_mounts(self):
        removed, added = self.changes({"src/app.ts": self.APP, "src/users.ts": self.ROUTER}, {"src/app.ts": self.APP + "const s = `open\n"})
        self.assertEqual((removed, added), ([], []))
        self.assertEqual(self.withheld(), [(self.REPO, True)])

    def test_a_base_that_could_not_be_read_whole_claims_no_addition(self):
        removed, added = self.changes({"src/app.ts": self.APP, "src/users.ts": self.ROUTER + "const s = `open\n"}, {"src/users.ts": self.ROUTER})
        self.assertEqual(([r.path for r in removed], added), ([], []))
        self.assertEqual(self.withheld(), [(self.REPO, True)])

    def test_the_http_edge_does_not_guard_it_a_second_time(self):
        self.repo({"files": {"src/app.ts": self.APP, "src/users.ts": self.ROUTER}})
        self.assertTrue(getattr(self.entry(), "withholds_unread", False))

    def test_a_search_that_was_cut_withholds_every_change(self):
        from unittest import mock

        from reachmap.model import Read

        base, head = two({"src/app.ts": self.APP, "src/users.ts": self.ROUTER}, {"src/users.ts": self.ROUTER.replace('r.get("/y", h);\n', "")})
        self.repo(base, head)
        ex = self.entry()
        ex.table(self.sha("main~1"))
        cut = mock.Mock(state=Read.FOUND, detail=None, truncated_by="examine_cap", examined=1, total=9, not_searched=(), partial_matches=())
        with mock.patch.object(self.language, "search", return_value=cut):
            ex.table(self.sha("main"))  # the head is read with a search that was cut
        removed, added = ex.routes_changed(self.sha("main~1"), self.sha("main"))
        self.assertEqual((removed, added), ([], []))
        self.assertEqual(self.withheld(), [(self.REPO, True)])

    def test_the_refactor_that_moves_a_mount_into_a_function_is_not_a_removed_route(self):
        before = {"src/app.ts": self.APP, "src/users.ts": self.ROUTER}
        after = {"src/app.ts": ts(EXPRESS, 'import * as routes from "./routes";', "const app = express();", "routes.mount(app);"),
                 "src/routes.ts": ts('import { r } from "./users";', "export function mount(app) {", '  app.use("/m", r);', "}")}
        removed, added = self.changes(before, after)
        self.assertEqual((removed, added), ([], []))
        self.assertEqual(self.withheld(), [(self.REPO, True)])
        self.assertEqual(self.records_of("routes_unread"), [("src/app.ts:4", True)])


class HandedToAMember(Base):
    """An app or a router handed to a method of an imported module or of an object: the method may add routes to it."""

    def check(self, files, line="src/app.ts:4", expect=True):
        self.labels(files)
        self.assertEqual(self.records_of("routes_unread"), [(line, True)] if expect else [])
        self.assertEqual(bool(self.ex.problems), expect)

    ROUTES = ts("export function mount(app) {", '  app.get("/late", h);', "}", "export function log(app) {", "  console.log(app);", "}")

    def test_a_method_of_a_namespace_import(self):
        self.check({"src/app.ts": ts(EXPRESS, 'import * as routes from "./routes";', "const app = express();", "routes.mount(app);"), "src/routes.ts": self.ROUTES})

    def test_a_method_of_a_default_import_and_of_a_named_one(self):
        self.check({"src/app.ts": ts(EXPRESS, 'import registry from "./registry";', "const app = express();", "registry.mount(app);"),
                    "src/registry.ts": ts("export default {", "  mount(app) {", '    app.use("/x", h);', "  },", "};")})
        self.check({"src/app.ts": ts(EXPRESS, 'import { registry } from "./registry";', "const app = express();", "registry.mount(app);"),
                    "src/registry.ts": ts("export const registry = {", "  mount(app) {", '    app.get("/x", h);', "  },", "};")})

    def test_a_method_of_a_commonjs_module(self):
        self.check({"src/app.js": ts('const express = require("express");', 'const routes = require("./routes");', "const app = express();", "routes.mount(app);"),
                    "src/routes.js": ts("exports.mount = function (app) {", '  app.get("/late", h);', "};")}, "src/app.js:4")
        self.check({"src/app.js": ts('const express = require("express");', "const app = express();", "", 'require("./routes").mount(app);'),
                    "src/routes.js": ts("exports.mount = function (app) {", '  app.get("/late", h);', "};")}, "src/app.js:4")

    def test_a_method_of_a_local_object(self):
        self.check({"src/app.ts": ts(EXPRESS, "const app = express();", "const registry = {", "  mount(app) {", '    app.get("/x", h);', "  },", "};", "registry.mount(app);")}, "src/app.ts:8")

    def test_a_deeper_member_chain_is_looked_up_from_its_root(self):
        self.check({"src/app.ts": ts(EXPRESS, 'import * as routes from "./routes";', "const app = express();", "routes.v1.mount(app);"), "src/routes.ts": self.ROUTES})

    def test_a_method_of_a_module_that_adds_no_routes_is_not_unread(self):
        self.check({"src/app.ts": ts(EXPRESS, 'import * as routes from "./routes";', "const app = express();", "routes.log(app);"),
                    "src/routes.ts": ts("export function log(app) {", "  console.log(app);", "}")}, expect=False)

    def test_a_method_of_a_package_or_a_global_is_not_unread(self):
        self.check({"src/app.ts": ts(EXPRESS, 'import http from "http";', 'import helmet from "helmet";', "const app = express();", "http.createServer(app);", "helmet.attach(app);", "console.log(app);", "JSON.stringify(app);")}, expect=False)

    def test_a_call_result_or_an_index_as_the_root_is_flagged(self):
        self.check({"src/app.ts": ts(EXPRESS, 'import { makeRegistry } from "./registry";', "const app = express();", "makeRegistry().mount(app);")})
        self.check({"src/app.ts": ts(EXPRESS, "const app = express();", "const registries = [];", "registries[0].mount(app);")}, "src/app.ts:4")

    def test_a_root_that_resolves_to_nothing_is_flagged_and_unmounted_is_not_claimed(self):
        files = {"src/app.ts": ts(EXPRESS, "const app = express();", "plugins.mount(app);"), "src/r.ts": ts(EXPRESS, "export const r = Router();", 'r.get("/x", (req, res) => res.end());')}
        res = self.run_case(files, {"src/r.ts": files["src/r.ts"].replace("res.end()", "res.send(1)")})
        self.assertEqual(self.records_of("routes_unread"), [("src/app.ts:3", True)])
        self.assertEqual(self.records_of("router_unmounted"), [])
        self.assertFalse(res.complete)

    def test_the_app_is_followed_when_it_is_an_imported_one(self):
        self.check({"src/app.ts": ts(EXPRESS, "export const app = express();"), "src/boot.ts": ts('import { app } from "./app";', 'import * as routes from "./routes";', "routes.mount(app);"),
                    "src/routes.ts": self.ROUTES}, "src/boot.ts:3")

    def test_a_router_handed_to_a_method_is_the_same(self):
        self.check({"src/app.ts": ts(EXPRESS, 'import * as routes from "./routes";', "const r = Router();", "routes.mount(r);"), "src/routes.ts": self.ROUTES})


class Prefixes(Base):
    def mounted(self, first, imports="", more=None):
        files = {"src/app.ts": ts(EXPRESS, 'import r from "./r";', imports, "const app = express();", f"app.use({first}, r);"), "src/r.ts": ts(EXPRESS, "const r = Router();", 'r.get("/x", h);', "export default r;"), **(more or {})}
        return [(r.path, r.partial) for r in self.table(files)]

    def test_a_member_of_an_object_is_an_unknown_segment_and_partial(self):
        self.assertEqual(self.mounted("cfg.prefix", 'import cfg from "./cfg";', {"src/cfg.ts": ts('export default { prefix: "/api" };')}), [("/<unknown:cfg.prefix>/x", True)])
        self.assertEqual(self.mounted("env.API_PREFIX"), [("/<unknown:env.API_PREFIX>/x", True)])

    def test_the_partial_prefix_is_said_when_a_change_reaches_it(self):
        files = {"src/app.ts": ts(EXPRESS, 'import r from "./r";', "const app = express();", "app.use(env.API_PREFIX, r);"), "src/r.ts": ts(EXPRESS, "const r = Router();", 'r.get("/x", (req, res) => res.end());', "export default r;")}
        self.run_case(files, {"src/r.ts": files["src/r.ts"].replace("res.end()", "res.send(1)")})
        self.assertEqual(self.records_of("route_path_partial"), [("src/app.ts:4", False)])

    def test_a_re_exported_constant_is_followed(self):
        more = {"src/consts.ts": ts('export { P } from "./base";'), "src/base.ts": ts('export const P = "/api";')}
        self.assertEqual(self.mounted("P", 'import { P } from "./consts";', more), [("/api/x", False)])
        more = {"src/consts.ts": ts('export * from "./base";'), "src/base.ts": ts('export const P = "/api";')}
        self.assertEqual(self.mounted("P", 'import { P } from "./consts";', more), [("/api/x", False)])

    def test_a_constant_of_a_commonjs_module_is_followed(self):
        self.assertEqual(self.mounted("P", 'import { P } from "./c";', {"src/c.js": ts('module.exports = { P: "/api" };')}), [("/api/x", False)])
        self.assertEqual(self.mounted("P", 'import { P } from "./c";', {"src/c.js": ts('exports.P = "/api";')}), [("/api/x", False)])
        self.assertEqual(self.mounted("P", 'import { P } from "./c";', {"src/c.js": ts('module.exports.P = `/api`;')}), [("/api/x", False)])

    def test_a_constant_assigned_twice_is_unknown(self):
        more = {"src/c.ts": ts('export var P = "/a";', 'var P = "/b";')}
        self.assertEqual(self.mounted("P", 'import { P } from "./c";', more), [("/<unknown:P>/x", True)])

    def test_middleware_before_a_router_is_not_a_prefix(self):
        for first, imports in (("(req, res, next) => next()", ""), ("function (req, res, next) { next(); }", ""), ("express.json()", ""), ("cors()", 'import cors from "cors";'),
                               ("requireAuth", 'import { requireAuth } from "./auth";')):
            with self.subTest(first=first):
                self.assertEqual(self.mounted(first, imports, {"src/auth.ts": ts("export function requireAuth(req, res, next) { next(); }")}), [("/x", False)])

    def test_a_local_function_is_middleware(self):
        files = {"src/app.ts": ts(EXPRESS, 'import r from "./r";', "function auth(req, res, next) { next(); }", "const app = express();", "app.use(auth, r);"),
                 "src/r.ts": ts(EXPRESS, "const r = Router();", 'r.get("/x", h);', "export default r;")}
        self.assertEqual([(r.path, r.partial) for r in self.table(files)], [("/x", False)])


class Barrels(Base):
    def test_a_file_that_adds_routes_to_a_router_imported_through_a_barrel_is_read(self):
        files = {"src/app.ts": ts(EXPRESS, 'import { r } from "./r";', "const app = express();", 'app.use("/m", r);'), "src/r.ts": ts(EXPRESS, "export const r = Router();"),
                 "src/barrel.ts": ts('export { r } from "./r";'), "src/add.ts": ts('import { r } from "./barrel";', 'r.get("/late", h);'), "src/main.ts": ts('import "./add";')}
        self.assertEqual(self.labels(files), ["GET /m/late"])

    def test_a_chain_of_barrels_and_export_star(self):
        files = {"src/app.ts": ts(EXPRESS, 'import { r } from "./r";', "const app = express();", 'app.use("/m", r);'), "src/r.ts": ts(EXPRESS, "export const r = Router();"),
                 "src/a.ts": ts('export * from "./r";'), "src/b.ts": ts('export * from "./a";'), "src/add.ts": ts('import { r } from "./b";', 'r.get("/late", h);')}
        self.assertEqual(self.labels(files), ["GET /m/late"])


class Constructors(Base):
    def test_new_router_and_new_express_router_are_one_router_and_no_record(self):
        files = {"src/app.ts": ts(EXPRESS, "const app = express();", "const a = new express.Router();", "const b = new Router();", 'app.use("/a", a);', 'app.use("/b", b);', 'a.get("/x", h);', 'b.get("/y", h);')}
        self.assertEqual(self.labels(files), ["GET /a/x", "GET /b/y"])
        self.assertEqual([(r.kind, r.subject) for r in self.records if r.kind in ("routes_unread", "mount_unresolved")], [])
        self.assertEqual(self.ex.problems, [])

    def test_a_router_built_in_a_function_with_new_is_still_unread(self):
        files = {"src/f.ts": ts(EXPRESS, "export function build() {", "  const r = new Router();", "  return r;", "}")}
        self.labels(files)
        self.assertEqual(self.records_of("routes_unread"), [("src/f.ts:3", True)])


class FrameworkNames(Base):
    def test_a_router_imported_through_a_re_export_of_express(self):
        files = {"src/lib/express.ts": ts('export { Router } from "express";', 'export { default } from "express";'),
                 "src/app.ts": ts('import express, { Router } from "./lib/express";', "const app = express();", "const r = Router();", 'app.use("/m", r);', 'r.get("/x", h);')}
        self.assertEqual(self.labels(files), ["GET /m/x"])

    def test_export_star_of_express(self):
        files = {"src/lib/express.ts": ts('export * from "express";'), "src/app.ts": ts('import { Router } from "./lib/express";', "const r = Router();", "export default r;")}
        self.table(files)
        self.assertEqual(self.ex.problems, [])
        self.assertIn("src/app.ts", self.ex.table(self.sha("main")).entry_files)


class ChainedCalls(Base):
    def test_every_call_of_a_chain_of_route_calls_is_a_route(self):
        files = {"src/app.ts": ts(EXPRESS, "const app = express();", 'app.get("/a", h).post("/b", h2).delete("/c", h3);')}
        self.assertEqual(self.labels(files), ["DELETE /c", "GET /a", "POST /b"])

    def test_a_chain_over_several_lines_cites_each_call_at_its_line(self):
        files = {"src/app.ts": ts(EXPRESS, "const app = express();", 'app.get("/a", h)', '  .post("/b", h2);')}
        self.assertEqual([(f"{r.method} {r.path}", r.evidence[0].line) for r in self.table(files)], [("GET /a", 3), ("POST /b", 4)])

    def test_a_change_to_one_call_of_the_chain_reaches_its_route_only(self):
        files = {"src/app.ts": ts(EXPRESS, "const app = express();", 'app.get("/a", h)', '  .post("/b", h2);')}
        res = self.run_case(files, {"src/app.ts": files["src/app.ts"].replace("h2", "h3")})
        self.assertEqual(self.reached(res), ["POST /b"])


class Patterns(Base):
    def test_a_pattern_after_a_parameter_makes_the_route_partial(self):
        files = {"src/app.ts": ts(EXPRESS, "const app = express();", 'app.get("/n/:id(\\\\d+)", h);', 'app.get("/f/:rest*", h);', 'app.get("/g/*", h);')}
        self.assertEqual([(r.path, r.partial) for r in self.table(files)], [("/n/{id}", True), ("/f/{rest:path}", True), ("/g/{rest:path}", False)])

    def test_express_5_optional_groups_are_two_routes(self):
        files = {"src/app.ts": ts(EXPRESS, "const app = express();", 'app.get("/users{/:id}", h);')}
        self.assertEqual([(r.path, r.partial) for r in self.table(files)], [("/users", False), ("/users/{id}", False)])


if __name__ == "__main__":
    unittest.main()
