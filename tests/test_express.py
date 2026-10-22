"""The Express entry-point adapter on small repositories: the route table (apps, routers, route calls, chains, mounts across files by ES
imports and `require`, prefixes and paths, constants), what is not Express (names that look like it), what the adapter says when it cannot
read what it needs, and what a change reaches (by import, by an edit of the file itself, per statement and per call of a chain).

The M16 fixture scenarios (tests/fixtures/express) run end to end in test_ex_scenarios.py.
"""

import unittest
from unittest import mock

from reachmap import entrypoints
from reachmap.adapters.express import ExpressAdapter
from reachmap.adapters.js import JsAdapter
from reachmap.model import Read
from tests import scenario_support as ss
from tests.support_universe import Base as UniverseBase
from tests.support_universe import f

TSCONFIG = '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}\n'
EXPRESS = 'import express, { Router } from "express";'


def ts(*lines):
    return f(list(lines))


def service(name="svc", ret="1"):
    return ts(f"export function {name}() {{", f"  return {ret};", "}")


class Base(UniverseBase):
    UNIVERSE_PREFIX = "reachmap-express-"
    REPO = "t/orders"

    def make_adapter(self, **kw):
        kw.setdefault("test_markers", ["__tests__/", ".test.", ".spec."])
        kw.setdefault("aliases_from", "tsconfig.json")
        return JsAdapter(self.REPO, self.url, self.git, **kw)

    def entry(self, **kw):
        self.language = self.make_adapter()
        self.ex = ExpressAdapter(self.REPO, self.language, **kw)
        return self.ex

    def table(self, files, ref="main", **kw):
        """The routes the adapter reads at `ref` of a repository made of `files`."""
        self.repo({"files": {"tsconfig.json": TSCONFIG, **files}})
        return self.entry(**kw).routes(None, self.sha(ref))

    def labels(self, files, **kw):
        return sorted(f"{r.method} {r.path}" for r in self.table(files, **kw))

    def route(self, files, label):
        (r,) = [r for r in self.table(files) if f"{r.method} {r.path}" == label]
        return r

    def reach(self, **kw):
        analysis = self.analyze(self.language)
        res = entrypoints.reach(analysis, self.language, self.ex, **kw)
        evidence = [e for r in res.reached for c in r.area.chains for e in c]
        self.assertEqual(ss.evidence_problems({self.REPO: self.path}, evidence), [])
        return res

    def run_case(self, files, changed, **kw):
        files = {"tsconfig.json": TSCONFIG, **files}
        self.repo({"files": files}, {"files": changed})
        self.entry()
        return self.reach(**kw)

    def reached(self, res):
        return sorted(r.area.label for r in res.reached)

    def chain(self, res, label, n=0):
        (r,) = [r for r in res.reached if r.area.label == label]
        return [(e.path, e.line) for e in r.area.chains[n]]

    def records_of(self, kind):
        return sorted((r.subject, r.blocks_selection) for r in self.records if r.kind == kind)


APP_FILE = ts(EXPRESS, 'import users from "./routes/users";', "", "const app = express();", 'app.use("/users", users);', "export default app;")
USERS_FILE = ts('import { Router } from "express";', 'import { list } from "../ctl";', "", "const router = Router();", 'router.get("/", list);', "export default router;")
CTL = ts("export function list(req, res) {", "  res.json(1);", "}")


class RouteCalls(Base):
    def test_every_method_of_an_app_and_all_is_any(self):
        files = {"src/app.ts": ts(EXPRESS, "const app = express();", 'app.get("/a", h);', 'app.post("/a", h);', 'app.put("/a", h);', 'app.delete("/a", h);',
                                  'app.patch("/a", h);', 'app.options("/a", h);', 'app.head("/a", h);', 'app.all("/b", h);')}
        self.assertEqual(self.labels(files), ["ANY /b", "DELETE /a", "GET /a", "HEAD /a", "OPTIONS /a", "PATCH /a", "POST /a", "PUT /a"])

    def test_an_app_get_with_one_argument_is_a_setting_and_not_a_route(self):
        files = {"src/app.ts": ts(EXPRESS, "const app = express();", 'app.get("title");', 'app.get("/a", h);')}
        self.assertEqual(self.labels(files), ["GET /a"])

    def test_a_route_chain_gives_a_route_per_method_and_cites_both_lines(self):
        files = {"src/app.ts": ts(EXPRESS, "const app = express();", "app", '  .route("/items/:id")', "  .get(show)", "  .put(update);")}
        self.assertEqual(self.labels(files), ["GET /items/{id}", "PUT /items/{id}"])
        r = self.route(files, "PUT /items/{id}")
        self.assertEqual([(e.line, e.detail.strip()) for e in r.evidence], [(6, ".put(update);"), (4, '.route("/items/:id")')])
        self.assertEqual(r.evidence[0].claim, "route:PUT /items/{id}:src/app.ts")

    def test_a_route_chain_on_one_line_cites_the_line_once(self):
        files = {"src/app.ts": ts(EXPRESS, "const app = express();", 'app.route("/x").get(a).post(b);')}
        r = self.route(files, "GET /x")
        self.assertEqual([e.line for e in r.evidence], [3])

    def test_a_route_call_over_several_lines_is_cited_at_the_line_of_its_method(self):
        files = {"src/app.ts": ts(EXPRESS, "const app = express();", "app.get(", '  "/a",', "  handler,", ");")}
        self.assertEqual([e.line for e in self.route(files, "GET /a").evidence], [3])

    def test_a_method_on_its_own_line_after_the_receiver_is_cited_at_the_method(self):
        files = {"src/app.ts": ts(EXPRESS, "const app = express();", "app", '  .get("/a", h);')}
        self.assertEqual([e.line for e in self.route(files, "GET /a").evidence], [4])

    def test_the_route_is_a_route_of_the_handlers_name_and_file(self):
        files = {"src/app.ts": ts(EXPRESS, 'import { show } from "./ctl";', 'import * as users from "./users";', "const app = express();", 'app.get("/a", show);',
                                  'app.get("/b", users.list);', 'app.get("/c", mw, (req, res) => res.end());', 'app.get("/d", wrap(show));', 'app.get("/e", [mw, show]);'),
                 "src/ctl.ts": CTL, "src/users.ts": CTL}
        by = {r.path: (r.handler, r.target) for r in self.table(files)}
        self.assertEqual(by["/a"], ("show", "src/ctl.ts"))
        self.assertEqual(by["/b"], ("list", "src/users.ts"))
        self.assertEqual(by["/c"], ("<anonymous>", "src/app.ts"))
        self.assertEqual(by["/d"], ("wrap(...)", "src/app.ts"))
        self.assertEqual(by["/e"], ("show", "src/ctl.ts"))

    def test_the_module_of_a_route_is_the_service_folder(self):
        files = {"services/orders/package.json": "{}", "services/orders/src/app.ts": ts(EXPRESS, "const app = express();", 'app.get("/a", h);')}
        self.assertEqual(self.table(files)[0].module, "orders")
        files = {"package.json": "{}", "src/app.ts": ts(EXPRESS, "const app = express();", 'app.get("/a", h);')}
        self.assertEqual(self.table(files)[0].module, "")

    def test_a_route_has_the_kind_api_route_and_is_not_partial(self):
        files = {"src/app.ts": ts(EXPRESS, "const app = express();", 'app.get("/a", h);')}
        (r,) = self.table(files)
        self.assertEqual((r.kind, r.partial, r.repo, r.file), ("api_route", False, self.REPO, "src/app.ts"))

    def test_typescript_annotations_and_non_null_assertions_do_not_hide_an_app(self):
        files = {"src/app.ts": ts(EXPRESS, 'import type { Express } from "express";', "const app: Express = express();", 'app.get("/a", h);')}
        self.assertEqual(self.labels(files), ["GET /a"])

    def test_statements_in_a_block_that_runs_when_the_file_loads_are_routes(self):
        files = {"src/app.ts": ts(EXPRESS, "const app = express();", "if (process.env.DEBUG) {", '  app.get("/debug", h);', "}", 'app.get("/a", h);')}
        self.assertEqual(self.labels(files), ["GET /a", "GET /debug"])

    def test_a_route_call_inside_a_function_is_not_read(self):
        files = {"src/app.ts": ts(EXPRESS, "const app = express();", "function later() {", '  app.get("/later", h);', "}", 'app.get("/a", h);')}
        self.assertEqual(self.labels(files), ["GET /a"])

    def test_a_commented_out_route_is_not_a_route(self):
        files = {"src/app.ts": ts(EXPRESS, "const app = express();", '// app.get("/gone", h);', '/* app.get("/also", h); */', 'app.get("/a", h);')}
        self.assertEqual(self.labels(files), ["GET /a"])

    def test_a_route_call_in_a_string_or_template_is_not_a_route(self):
        files = {"src/app.ts": ts(EXPRESS, "const app = express();", 'const doc = "app.get(\'/x\', h)";', "const t = `app.get('/y', h)`;", 'app.get("/a", h);')}
        self.assertEqual(self.labels(files), ["GET /a"])


class Mounts(Base):
    def test_a_router_mounted_in_another_file_gets_the_prefix(self):
        self.assertEqual(self.labels({"src/app.ts": APP_FILE, "src/routes/users.ts": USERS_FILE, "src/ctl.ts": CTL}), ["GET /users"])

    def test_the_evidence_is_the_route_then_each_mount_from_the_router_outwards(self):
        r = self.route({"src/app.ts": APP_FILE, "src/routes/users.ts": USERS_FILE, "src/ctl.ts": CTL}, "GET /users")
        self.assertEqual([(e.path, e.line) for e in r.evidence], [("src/routes/users.ts", 5), ("src/app.ts", 5)])

    def test_a_mount_without_a_prefix_adds_nothing(self):
        files = {"src/app.ts": ts(EXPRESS, 'import r from "./r";', "const app = express();", "app.use(r);"), "src/r.ts": ts(EXPRESS, "const r = Router();", 'r.get("/x", h);', "export default r;")}
        self.assertEqual(self.labels(files), ["GET /x"])

    def test_a_router_mounted_under_several_prefixes_gives_a_route_for_each(self):
        files = {"src/app.ts": ts(EXPRESS, 'import r from "./r";', "const app = express();", 'app.use("/a", r);', 'app.use("/b", r);'),
                 "src/r.ts": ts(EXPRESS, "const r = Router();", 'r.get("/x", h);', "export default r;")}
        self.assertEqual(self.labels(files), ["GET /a/x", "GET /b/x"])

    def test_a_list_of_prefixes_is_a_mount_for_each(self):
        files = {"src/app.ts": ts(EXPRESS, 'import r from "./r";', "const app = express();", 'app.use(["/a", "/b"], r);'),
                 "src/r.ts": ts(EXPRESS, "const r = Router();", 'r.get("/x", h);', "export default r;")}
        self.assertEqual(self.labels(files), ["GET /a/x", "GET /b/x"])

    def test_a_list_of_routers_is_mounted_and_middleware_before_them_is_not_a_mount(self):
        files = {"src/app.ts": ts(EXPRESS, 'import a from "./a";', 'import b from "./b";', 'import auth from "./auth";', "const app = express();", 'app.use("/p", auth, [a, b]);'),
                 "src/a.ts": ts(EXPRESS, "const r = Router();", 'r.get("/a", h);', "export default r;"), "src/b.ts": ts(EXPRESS, "const r = Router();", 'r.get("/b", h);', "export default r;"),
                 "src/auth.ts": ts("export default function auth(req, res, next) { next(); }")}
        self.assertEqual(self.labels(files), ["GET /p/a", "GET /p/b"])
        self.assertEqual(self.records_of("mount_unresolved"), [])

    def test_routers_nest_to_any_depth_and_the_prefixes_join_from_the_app_down(self):
        files = {"src/app.ts": ts(EXPRESS, 'import api from "./api";', "const app = express();", 'app.use("/api", api);'),
                 "src/api.ts": ts(EXPRESS, 'import v1 from "./v1";', "const api = Router();", 'api.use("/v1", v1);', "export default api;"),
                 "src/v1.ts": ts(EXPRESS, 'import items from "./items";', "const v1 = Router();", 'v1.use("/items", items);', "export default v1;"),
                 "src/items.ts": ts(EXPRESS, "const items = Router();", 'items.get("/:id", h);', "export default items;")}
        r = self.route(files, "GET /api/v1/items/{id}")
        self.assertEqual([(e.path, e.line) for e in r.evidence], [("src/items.ts", 3), ("src/v1.ts", 4), ("src/api.ts", 4), ("src/app.ts", 4)])

    def test_a_sub_application_is_mounted_like_a_router(self):
        files = {"src/app.ts": ts(EXPRESS, 'import admin from "./admin";', "const app = express();", 'app.use("/admin", admin);'),
                 "src/admin.ts": ts(EXPRESS, "const admin = express();", 'admin.get("/x", h);', "export default admin;")}
        self.assertEqual(self.labels(files), ["GET /admin/x"])

    def test_two_apps_are_two_roots(self):
        files = {"src/a.ts": ts(EXPRESS, "const a = express();", 'a.get("/a", h);'), "src/b.ts": ts(EXPRESS, "const b = express();", 'b.get("/b", h);')}
        self.assertEqual(self.labels(files), ["GET /a", "GET /b"])

    def test_a_cycle_of_mounts_ends(self):
        files = {"src/app.ts": ts(EXPRESS, 'import a from "./a";', "const app = express();", 'app.use("/a", a);'),
                 "src/a.ts": ts(EXPRESS, 'import b from "./b";', "const a = Router();", 'a.get("/x", h);', 'a.use("/b", b);', "export default a;"),
                 "src/b.ts": ts(EXPRESS, 'import a from "./a";', "const b = Router();", 'b.use("/a", a);', "export default b;")}
        self.assertIn("GET /a/x", self.labels(files))

    def test_routes_are_registered_on_an_app_imported_from_another_file(self):
        files = {"src/app.ts": ts(EXPRESS, "export const app = express();"), "src/routes.ts": ts('import { app } from "./app";', 'app.get("/late", h);'), "src/main.ts": ts('import "./routes";')}
        self.assertEqual(self.labels(files), ["GET /late"])

    def test_a_mount_registered_on_an_imported_router_is_followed(self):
        files = {"src/app.ts": ts(EXPRESS, 'import api from "./api";', "const app = express();", 'app.use("/api", api);'),
                 "src/api.ts": ts(EXPRESS, "const api = Router();", "export default api;"),
                 "src/more.ts": ts('import api from "./api";', 'import sub from "./sub";', 'api.use("/sub", sub);'),
                 "src/sub.ts": ts(EXPRESS, "const sub = Router();", 'sub.get("/x", h);', "export default sub;")}
        self.assertEqual(self.labels(files), ["GET /api/sub/x"])

    def test_a_mount_in_a_comment_is_not_a_mount(self):
        files = {"src/app.ts": ts(EXPRESS, 'import r from "./r";', "const app = express();", '// app.use("/r", r);'), "src/r.ts": ts(EXPRESS, "const r = Router();", 'r.get("/x", h);', "export default r;")}
        self.assertEqual(self.labels(files), [])
        self.assertEqual(self.ex.unmounted(self.sha("main")), [("src/r.ts", "r")])

    def test_a_router_nothing_mounts_has_no_route_and_is_listed_as_unmounted(self):
        files = {"src/app.ts": ts(EXPRESS, "const app = express();", 'app.get("/a", h);'), "src/r.ts": ts(EXPRESS, "export const r = Router();", 'r.get("/x", h);')}
        self.assertEqual(self.labels(files), ["GET /a"])
        self.assertEqual(self.ex.unmounted(self.sha("main")), [("src/r.ts", "r")])

    def test_middleware_is_not_a_mount_and_is_not_unresolved(self):
        files = {"src/app.ts": ts(EXPRESS, 'import cors from "cors";', 'import { log } from "./log";', "const app = express();", "app.use(express.json());", "app.use(cors());", "app.use(log);",
                                  'app.use("/static", express.static("public"));', "app.use((req, res, next) => next());", "app.use(function (req, res, next) { next(); });", 'app.get("/a", h);'),
                 "src/log.ts": ts("export function log(req, res, next) { next(); }")}
        self.assertEqual(self.labels(files), ["GET /a"])
        self.assertEqual(self.records_of("mount_unresolved"), [])
        self.assertEqual(self.ex.problems, [])

    def test_a_call_that_may_return_a_router_is_an_unresolved_mount_and_blocks(self):
        files = {"src/app.ts": ts(EXPRESS, 'import { build } from "./build";', "const app = express();", 'app.use("/x", build());', 'app.get("/a", h);'),
                 "src/build.ts": ts(EXPRESS, "export function build() {", "  const r = Router();", '  r.get("/y", h);', "  return r;", "}")}
        self.assertEqual(self.labels(files), ["GET /a"])
        self.assertEqual(self.records_of("mount_unresolved"), [("src/app.ts:4", True)])
        self.assertTrue(self.ex.problems)

    def test_a_name_from_a_file_that_declares_routers_but_that_does_not_resolve_is_unresolved(self):
        files = {"src/app.ts": ts(EXPRESS, 'import { other } from "./routers";', "const app = express();", 'app.use("/x", other);'),
                 "src/routers.ts": ts(EXPRESS, "export const first = Router();", "export const second = Router();")}
        self.labels(files)
        self.assertEqual(self.records_of("mount_unresolved"), [("src/app.ts:4", True)])

    def test_a_local_function_that_builds_a_router_is_an_unresolved_mount(self):
        files = {"src/app.ts": ts(EXPRESS, "function make() {", "  return Router();", "}", "const app = express();", 'app.use("/x", make());')}
        self.labels(files)
        self.assertEqual(self.records_of("mount_unresolved"), [("src/app.ts:6", True)])


class Modules(Base):
    """The ways a router is exported and imported: ES modules and CommonJS."""

    def mounted(self, router_file, mount_line, imports='import r from "./r";'):
        files = {"src/app.ts": ts(EXPRESS, imports, "const app = express();", mount_line), "src/r.ts": router_file}
        return self.labels(files)

    ROUTER = ts('import { Router } from "express";', "const r = Router();", 'r.get("/x", h);')

    def test_default_export(self):
        self.assertEqual(self.mounted(self.ROUTER + "export default r;\n", 'app.use("/m", r);'), ["GET /m/x"])

    def test_named_export_and_an_alias_on_import(self):
        text = self.ROUTER.replace("const r", "export const r")
        self.assertEqual(self.mounted(text, 'app.use("/m", api);', 'import { r as api } from "./r";'), ["GET /m/x"])

    def test_export_list_with_a_rename(self):
        self.assertEqual(self.mounted(self.ROUTER + "export { r as users };\n", 'app.use("/m", users);', 'import { users } from "./r";'), ["GET /m/x"])

    def test_a_namespace_import_and_a_member(self):
        self.assertEqual(self.mounted(self.ROUTER + "export default r;\n", 'app.use("/m", ns.default);', 'import * as ns from "./r";'), ["GET /m/x"])
        self.assertEqual(self.mounted(self.ROUTER.replace("const r", "export const r"), 'app.use("/m", ns.r);', 'import * as ns from "./r";'), ["GET /m/x"])

    def test_a_re_export_through_a_barrel(self):
        files = {"src/app.ts": ts(EXPRESS, 'import { r } from "./routes";', "const app = express();", 'app.use("/m", r);'), "src/routes/index.ts": ts('export { r } from "./r";'),
                 "src/routes/r.ts": self.ROUTER.replace("const r", "export const r")}
        self.assertEqual(self.labels(files), ["GET /m/x"])

    def test_a_default_re_exported_as_a_name(self):
        files = {"src/app.ts": ts(EXPRESS, 'import { users } from "./routes";', "const app = express();", 'app.use("/m", users);'), "src/routes.ts": ts('export { default as users } from "./r";'),
                 "src/r.ts": self.ROUTER + "export default r;\n"}
        self.assertEqual(self.labels(files), ["GET /m/x"])

    def test_commonjs_module_exports_a_router_and_require_takes_it(self):
        text = ts('const express = require("express");', "const r = express.Router();", 'r.get("/x", h);', "module.exports = r;")
        files = {"src/app.js": ts('const express = require("express");', 'const r = require("./r");', "const app = express();", 'app.use("/m", r);'), "src/r.js": text}
        self.assertEqual(self.labels(files), ["GET /m/x"])

    def test_a_default_import_of_a_commonjs_module_is_what_module_exports_is(self):
        text = ts('const { Router } = require("express");', "const r = Router();", 'r.get("/x", h);', "module.exports = r;")
        files = {"src/app.ts": ts(EXPRESS, 'import r from "./r";', "const app = express();", 'app.use("/m", r);'), "src/r.js": text}
        self.assertEqual(self.labels(files), ["GET /m/x"])

    def test_commonjs_module_exports_an_object_with_the_router(self):
        text = ts('const { Router } = require("express");', "const r = Router();", 'r.get("/x", h);', "module.exports = { r };")
        files = {"src/app.js": ts('const express = require("express");', 'const { r } = require("./r");', "const app = express();", 'app.use("/m", r);'), "src/r.js": text}
        self.assertEqual(self.labels(files), ["GET /m/x"])

    def test_commonjs_exports_dot_name_and_a_member_of_require(self):
        text = ts('const { Router } = require("express");', "const r = Router();", 'r.get("/x", h);', "exports.api = r;")
        files = {"src/app.js": ts('const express = require("express");', "const app = express();", 'app.use("/m", require("./r").api);'), "src/r.js": text}
        self.assertEqual(self.labels(files), ["GET /m/x"])
        text = ts('const { Router } = require("express");', "const r = Router();", 'r.get("/x", h);', "module.exports.api = r;")
        files = {"src/app.js": ts('const express = require("express");', 'const api = require("./r").api;', "const app = express();", 'app.use("/m", api);'), "src/r.js": text}
        self.assertEqual(self.labels(files), ["GET /m/x"])

    def test_an_inline_require_in_the_mount(self):
        text = ts('const express = require("express");', "const r = express.Router();", 'r.get("/x", h);', "module.exports = r;")
        files = {"src/app.js": ts('const express = require("express");', "const app = express();", 'app.use("/m", require("./r"));'), "src/r.js": text}
        self.assertEqual(self.labels(files), ["GET /m/x"])

    def test_module_exports_of_a_require_is_followed(self):
        files = {"src/app.js": ts('const express = require("express");', 'const r = require("./index");', "const app = express();", 'app.use("/m", r);'),
                 "src/index.js": ts('module.exports = require("./real");'),
                 "src/real.js": ts('const { Router } = require("express");', "const r = Router();", 'r.get("/x", h);', "module.exports = r;")}
        self.assertEqual(self.labels(files), ["GET /m/x"])

    def test_a_folder_with_an_index_file_is_what_a_specifier_names(self):
        files = {"src/app.ts": ts(EXPRESS, 'import r from "./routes";', "const app = express();", 'app.use("/m", r);'), "src/routes/index.ts": self.ROUTER + "export default r;\n"}
        self.assertEqual(self.labels(files), ["GET /m/x"])

    def test_an_alias_from_the_tsconfig_resolves_the_import(self):
        files = {"src/app.ts": ts(EXPRESS, 'import r from "@/routes/r";', "const app = express();", 'app.use("/m", r);'), "src/routes/r.ts": self.ROUTER + "export default r;\n"}
        self.assertEqual(self.labels(files), ["GET /m/x"])

    def test_the_factories_of_express_and_of_express_promise_router(self):
        files = {"src/app.js": ts('const app = require("express")();', 'const r = require("express").Router();', 'const p = require("express-promise-router")();', 'app.use("/r", r);', 'app.use("/p", p);',
                                  'r.get("/x", h);', 'p.get("/y", h);')}
        self.assertEqual(self.labels(files), ["GET /p/y", "GET /r/x"])

    def test_a_router_made_with_new_and_the_namespace_import(self):
        files = {"src/app.ts": ts('import * as express from "express";', "const app = express();", "const r = new express.Router();", 'app.use("/m", r);', 'r.get("/x", h);')}
        self.assertEqual(self.labels(files), ["GET /m/x"])

    def test_options_of_the_router_such_as_merge_params_are_accepted(self):
        files = {"src/app.ts": ts(EXPRESS, "const app = express();", "const r = Router({ mergeParams: true, strict: true });", 'app.use("/a/:aid/b", r);', 'r.get("/:bid", h);')}
        self.assertEqual(self.labels(files), ["GET /a/{aid}/b/{bid}"])


class PathsAndConstants(Base):
    def test_paths_of_constants_of_the_file_an_imported_one_a_template_and_a_sum(self):
        files = {"src/config.ts": ts('export const BASE = "/api";', 'export const V = "v2";'),
                 "src/app.ts": ts(EXPRESS, 'import { BASE, V } from "./config";', 'const LOCAL = "/local";', "const app = express();", "app.get(BASE, h);", "app.get(LOCAL, h);",
                                  "app.get(`${BASE}/${V}/t`, h);", 'app.get(BASE + "/sum", h);', 'app.get(LOCAL + "/" + V, h);')}
        self.assertEqual(self.labels(files), ["GET /api", "GET /api/sum", "GET /api/v2/t", "GET /local", "GET /local/v2"])

    def test_a_constant_through_a_namespace_import_member_is_not_evaluated(self):
        files = {"src/config.ts": ts('export const BASE = "/api";'), "src/app.ts": ts(EXPRESS, 'import * as cfg from "./config";', "const app = express();", "app.get(cfg.BASE, h);")}
        (r,) = self.table(files)
        self.assertEqual((r.path, r.partial), ("/<unknown:cfg.BASE>", True))

    def test_a_constant_assigned_twice_is_unknown(self):
        files = {"src/app.ts": ts(EXPRESS, 'var P = "/a";', 'var P = "/b";', "const app = express();", "app.get(P, h);")}
        (r,) = self.table(files)
        self.assertTrue(r.partial)

    def test_parameters_wildcards_and_optional_segments(self):
        files = {"src/app.ts": ts(EXPRESS, "const app = express();", 'app.get("/u/:id", h);', 'app.get("/o/:id?", h);', 'app.get("/f/*", h);', 'app.get("/n/:id(\\\\d+)", h);')}
        self.assertEqual(self.labels(files), ["GET /f/{rest:path}", "GET /n/{id}", "GET /o", "GET /o/{id}", "GET /u/{id}"])

    def test_a_pattern_in_a_segment_is_an_unknown_segment_and_partial(self):
        files = {"src/app.ts": ts(EXPRESS, "const app = express();", 'app.get("/a/b*c", h);')}
        (r,) = self.table(files)
        self.assertEqual((r.path, r.partial), ("/a/<unknown:b*c>", True))

    def test_a_regular_expression_path_is_an_unknown_segment(self):
        files = {"src/app.ts": ts(EXPRESS, "const app = express();", "app.get(/^\\/ab+c/, h);")}
        (r,) = self.table(files)
        self.assertTrue(r.partial)
        self.assertIn("<unknown:", r.path)

    def test_a_list_of_paths_is_a_route_for_each(self):
        files = {"src/app.ts": ts(EXPRESS, "const app = express();", 'app.get(["/a", "/b/:id"], h);')}
        self.assertEqual(self.labels(files), ["GET /a", "GET /b/{id}"])

    def test_a_path_that_is_a_call_is_one_unknown_segment_and_the_route_is_partial(self):
        files = {"src/app.ts": ts(EXPRESS, "const app = express();", 'app.use("/api", sub);', "const sub = Router();", "sub.get(pathFor(), h);")}
        self.assertEqual(self.labels({"src/app.ts": ts(EXPRESS, "const app = express();", "app.get(pathFor(), h);")}), ["GET /<unknown:pathFor()>"])

    def test_a_prefix_that_is_unknown_makes_the_routes_below_partial(self):
        files = {"src/app.ts": ts(EXPRESS, 'import r from "./r";', "const P = pick();", "const app = express();", "app.use(P, r);"), "src/r.ts": ts(EXPRESS, "const r = Router();", 'r.get("/x", h);', "export default r;")}
        (r,) = self.table(files)
        self.assertEqual((r.path, r.partial), ("/<unknown:pick()>/x", True))

    def test_a_first_argument_that_is_a_call_is_middleware_and_not_a_prefix(self):
        files = {"src/app.ts": ts(EXPRESS, 'import r from "./r";', "const app = express();", "app.use(auth(), r);"), "src/r.ts": ts(EXPRESS, "const r = Router();", 'r.get("/x", h);', "export default r;")}
        self.assertEqual(self.labels(files), ["GET /x"])

    def test_trailing_slashes_and_doubled_slashes_are_normalised(self):
        files = {"src/app.ts": ts(EXPRESS, 'import r from "./r";', "const app = express();", 'app.use("/a/", r);'), "src/r.ts": ts(EXPRESS, "const r = Router();", 'r.get("/b/", h);', 'r.get("/", h);', "export default r;")}
        self.assertEqual(self.labels(files), ["GET /a", "GET /a/b"])

    def test_the_root_path_is_a_slash(self):
        self.assertEqual(self.labels({"src/app.ts": ts(EXPRESS, "const app = express();", 'app.get("/", h);')}), ["GET /"])


class NotExpress(Base):
    """Names that look like Express and are not: a name of a variable is never what makes a router."""

    def test_a_router_that_is_not_made_by_express_claims_no_route(self):
        files = {"src/events.ts": ts('import { createRouter } from "./bus";', "const router = createRouter();", 'router.get("/ping", (m) => m.reply("pong"));', "export default router;"),
                 "src/bus.ts": ts("export function createRouter() { return { get() {} }; }")}
        self.assertEqual(self.labels(files), [])

    def test_an_app_that_is_not_made_by_express_claims_no_route(self):
        files = {"src/m.ts": ts("const app = new Map();", 'app.get("/x", h);', "const server = koa();", 'server.get("/y", h);')}
        self.assertEqual(self.labels(files), [])

    def test_a_module_called_express_of_the_repository_is_not_the_framework(self):
        files = {"src/app.ts": ts('import express from "./express";', "const app = express();", 'app.get("/a", h);'), "src/express.ts": ts("export default function express() { return {}; }")}
        self.assertEqual(self.labels(files), [])

    def test_a_get_on_a_module_of_the_repository_is_not_a_route(self):
        files = {"src/warm.ts": ts('import * as cache from "./cache";', 'cache.get("/orders/:id", load);'), "src/cache.ts": ts("export function get(k, f) { return f; }")}
        self.assertEqual(self.labels(files), [])

    def test_a_client_call_with_a_path_and_options_is_not_a_route(self):
        files = {"src/api.ts": ts('import axios from "axios";', 'axios.get("/api/x", { params: {} });', 'axios.post("/api/y", body);', "const c = axios.create();", 'c.get("/api/z", opts);')}
        self.assertEqual(self.labels(files), [])

    def test_router_is_not_a_name_that_makes_a_router(self):
        files = {"src/m.ts": ts('import { Router } from "./mine";', "export const r = Router();", 'r.get("/x", h);'), "src/mine.ts": ts("export function Router() { return {}; }"),
                 "src/app.ts": ts(EXPRESS, 'import { r } from "./m";', "const app = express();", 'app.use("/m", r);')}
        self.assertEqual(self.labels(files), [])

    def test_a_file_that_calls_express_in_a_test_is_not_read(self):
        files = {"src/__tests__/app.test.ts": ts(EXPRESS, "const app = express();", 'app.get("/t", h);'), "src/app.ts": ts(EXPRESS, "const app = express();", 'app.get("/a", h);')}
        self.assertEqual(self.labels(files), ["GET /a"])

    def test_a_declaration_file_and_a_dependency_folder_are_not_read(self):
        files = {"src/app.d.ts": ts(EXPRESS, "declare const app: any;", 'app.get("/d", h);'), "node_modules/x/index.js": ts('const express = require("express");', "const app = express();", 'app.get("/n", h);'),
                 "src/app.ts": ts(EXPRESS, "const app = express();", 'app.get("/a", h);')}
        self.assertEqual(self.labels(files), ["GET /a"])

    def test_the_name_of_a_route_handler_does_not_link_code(self):
        files = {"src/app.ts": ts(EXPRESS, 'import { list } from "./a";', "const app = express();", 'app.get("/a", list);'), "src/a.ts": ts("export function list() { return 1; }"),
                 "src/b.ts": ts("export function list() { return 2; }"), "src/c.ts": ts('import { list } from "./b";', "export const x = list;")}
        res = self.run_case(files, {"src/b.ts": ts("export function list() { return 3; }")})
        self.assertEqual(self.reached(res), [])


class Unread(Base):
    """Every read answers found, absent or unreadable: what could not be read is a record and an incomplete answer, never an absence."""

    def test_a_router_built_in_a_function_is_a_blocking_record_and_not_a_route(self):
        files = {"src/f.ts": ts(EXPRESS, "export function build() {", "  const r = Router();", '  r.get("/x", h);', "  return r;", "}")}
        self.assertEqual(self.labels(files), [])
        self.assertEqual(self.records_of("routes_unread"), [("src/f.ts:3", True)])
        self.assertTrue(self.ex.problems)

    def test_an_app_built_in_a_function_is_the_same(self):
        files = {"src/f.ts": ts(EXPRESS, "export function createApp() {", "  const app = express();", '  app.get("/x", h);', "  return app;", "}")}
        self.labels(files)
        self.assertEqual(self.records_of("routes_unread"), [("src/f.ts:3", True)])

    def test_a_router_built_in_a_class_is_the_same(self):
        files = {"src/f.ts": ts(EXPRESS, "export class Api {", "  router = Router();", "}")}
        self.labels(files)
        self.assertEqual(self.records_of("routes_unread"), [("src/f.ts:3", True)])

    def test_an_app_handed_to_a_function_that_adds_routes_is_a_blocking_record(self):
        files = {"src/app.ts": ts(EXPRESS, 'import { register } from "./register";', "const app = express();", "register(app);"),
                 "src/register.ts": ts("export function register(app) {", '  app.get("/late", h);', "}")}
        self.assertEqual(self.labels(files), [])
        self.assertEqual(self.records_of("routes_unread"), [("src/app.ts:4", True)])

    def test_the_same_through_a_require_call_and_a_local_function(self):
        files = {"src/app.js": ts('const express = require("express");', "const app = express();", 'require("./routes")(app);'), "src/routes.js": ts("module.exports = (app) => {", '  app.get("/late", h);', "};")}
        self.labels(files)
        self.assertEqual(self.records_of("routes_unread"), [("src/app.js:3", True)])
        files = {"src/app.ts": ts(EXPRESS, "const app = express();", "setup(app);", "function setup(a) {", '  a.use("/x", h);', "}")}
        self.labels(files)
        self.assertEqual(self.records_of("routes_unread"), [("src/app.ts:3", True)])

    def test_an_app_handed_to_something_that_adds_no_routes_is_not_unread(self):
        files = {"src/app.ts": ts(EXPRESS, 'import http from "http";', 'import { start } from "./start";', 'import serverless from "serverless-http";', "const app = express();", "http.createServer(app);",
                                  "start(app);", "export const handler = serverless(app);"),
                 "src/start.ts": ts("export function start(app) {", "  app.listen(3000);", "}")}
        self.assertEqual(self.labels(files), [])
        self.assertEqual(self.records_of("routes_unread"), [])
        self.assertEqual(self.ex.problems, [])

    def test_a_file_that_does_not_lex_claims_no_route_and_is_blocking(self):
        files = {"src/app.ts": ts(EXPRESS, "const app = express();", 'app.get("/a", h);', "const s = `never closed;")}
        self.assertEqual(self.labels(files), [])
        self.assertEqual(self.records_of("parse_degraded"), [("src/app.ts", True)])
        self.assertTrue(self.ex.problems)

    def test_a_file_that_the_tree_lists_and_is_absent_is_a_problem_not_an_absence(self):
        self.repo({"files": {"tsconfig.json": TSCONFIG, "src/app.ts": ts(EXPRESS, "const app = express();", 'app.get("/a", h);')}})
        ex = self.entry()
        absent = mock.Mock(is_found=False, is_absent=True, is_unreadable=False)
        with mock.patch.object(self.language, "file_text", return_value=absent):
            routes = ex.routes(None, self.sha("main"))
        self.assertEqual(routes, [])
        self.assertTrue(ex.problems)

    def test_a_search_that_cannot_be_read_is_a_problem_and_not_no_routes(self):
        self.repo({"files": {"tsconfig.json": TSCONFIG, "src/app.ts": ts(EXPRESS, "const app = express();", 'app.get("/a", h);')}})
        ex = self.entry()
        bad = mock.Mock(state=Read.UNREADABLE, detail="timed out", truncated_by=None, not_searched=(), partial_matches=())
        with mock.patch.object(self.language, "search", return_value=bad):
            ex.routes(None, self.sha("main"))
        self.assertTrue(any("unreadable" in p for p in ex.problems))

    def test_a_search_that_was_cut_is_a_problem(self):
        self.repo({"files": {"tsconfig.json": TSCONFIG, "src/app.ts": ts(EXPRESS, "const app = express();", 'app.get("/a", h);')}})
        ex = self.entry()
        cut = mock.Mock(state=Read.FOUND, detail=None, truncated_by="examine_cap", examined=40, total=900, not_searched=(), partial_matches=())
        with mock.patch.object(self.language, "search", return_value=cut):
            ex.routes(None, self.sha("main"))
        self.assertTrue(any("search_truncated" in p for p in ex.problems))

    def test_a_tree_that_could_not_be_read_is_a_problem(self):
        self.repo({"files": {"tsconfig.json": TSCONFIG, "src/app.ts": ts(EXPRESS, "const app = express();", 'app.get("/a", h);')}})
        ex = self.entry()
        with mock.patch.object(self.language, "search", return_value=None):
            self.assertEqual(ex.routes(None, self.sha("main")), [])
        self.assertTrue(ex.problems)

    def test_a_file_that_git_grep_does_not_search_is_a_problem(self):
        self.repo({"files": {"tsconfig.json": TSCONFIG, "src/app.ts": ts(EXPRESS, "const app = express();", 'app.get("/a", h);')}})
        ex = self.entry()
        link = mock.Mock(state=Read.ABSENT, detail=None, truncated_by=None, not_searched=(("src/linked.ts", "symlink"),), partial_matches=())
        with mock.patch.object(self.language, "search", return_value=link):
            ex.routes(None, self.sha("main"))
        self.assertTrue(any("src/linked.ts" in p for p in ex.problems))

    def test_unmounted_is_not_claimed_while_a_mount_is_unresolved(self):
        files = {"src/app.ts": ts(EXPRESS, 'import { build } from "./build";', "const app = express();", 'app.use("/x", build());'), "src/build.ts": ts(EXPRESS, "export function build() { return Router(); }"),
                 "src/r.ts": ts(EXPRESS, "export const r = Router();", 'r.get("/x", h);'), "src/ctl.ts": CTL}
        res = self.run_case(files, {"src/ctl.ts": CTL.replace("1", "2"), "src/r.ts": ts(EXPRESS, "export const r = Router();", 'r.get("/x", h, h);')})
        self.assertEqual(self.records_of("router_unmounted"), [])
        self.assertTrue(any("may be mounted" in p for p in self.ex.problems))
        self.assertFalse(res.complete)


class ReachByImport(Base):
    FILES = {
        "src/svc.ts": ts("export function one() {", "  return 1;", "}", "", "export function two() {", "  return 2;", "}"),
        "src/ctl.ts": ts('import { one, two } from "./svc";', "", "export function a(req, res) {", "  res.json(one());", "}", "", "export function b(req, res) {", "  res.json(two());", "}"),
        "src/routes.ts": ts(EXPRESS, 'import { a, b } from "./ctl";', "", "export const router = Router();", "", 'router.get("/a", a);', 'router.get("/b", b);'),
        "src/app.ts": ts(EXPRESS, 'import { router } from "./routes";', "", "const app = express();", 'app.use("/m", router);'),
    }

    def go(self, changed=None, files=None):
        changed = changed or {"src/svc.ts": self.FILES["src/svc.ts"].replace("return 1;", "return 3;")}
        return self.run_case(files or self.FILES, changed)

    def test_a_service_function_reaches_the_route_of_the_handler_that_uses_it(self):
        res = self.go()
        self.assertEqual(self.reached(res), ["GET /m/a"])
        self.assertTrue(res.complete)

    def test_the_chain_is_the_imports_then_the_route_and_the_mounts(self):
        res = self.go()
        self.assertEqual(self.chain(res, "GET /m/a"), [("src/ctl.ts", 1), ("src/routes.ts", 2), ("src/routes.ts", 6), ("src/app.ts", 5)])

    def test_the_area_has_the_route_the_module_and_the_route_line(self):
        res = self.go()
        (r,) = res.reached
        self.assertEqual((r.area.kind, r.area.also_mounted, r.area.by_name), ("api_route", (), False))
        (route,) = r.routes
        self.assertEqual((route.method, route.path, route.handler, route.target), ("GET", "/m/a", "a", "src/ctl.ts"))
        self.assertEqual((r.area.route_line.path, r.area.route_line.line), ("src/routes.ts", 6))

    def test_the_priority_of_a_route_is_must_test(self):
        res = self.go()
        self.assertEqual({r.area.priority for r in res.reached}, {"must_test"})

    def test_a_named_import_is_narrowed_by_the_names_that_arrive(self):
        res = self.go({"src/svc.ts": self.FILES["src/svc.ts"].replace("return 2;", "return 4;")})
        self.assertEqual(self.reached(res), ["GET /m/b"])

    def test_a_namespace_import_is_narrowed_by_the_member_the_statement_spells(self):
        files = dict(self.FILES)
        files["src/routes.ts"] = ts(EXPRESS, 'import * as ctl from "./ctl";', "", "export const router = Router();", "", 'router.get("/a", ctl.a);', 'router.get("/b", ctl.b);')
        res = self.go(files=files)
        self.assertEqual(self.reached(res), ["GET /m/a"])

    def test_a_namespace_used_as_a_whole_is_not_narrowed(self):
        files = dict(self.FILES)
        files["src/routes.ts"] = ts(EXPRESS, 'import * as ctl from "./ctl";', "", "export const router = Router();", "", 'router.get("/a", ctl.a);', 'router.get("/b", wrap(ctl));')
        res = self.go(files=files)
        self.assertEqual(self.reached(res), ["GET /m/a", "GET /m/b"])

    def test_a_default_import_is_not_narrowed(self):
        files = dict(self.FILES)
        files["src/ctl.ts"] = ts('import { one, two } from "./svc";', "", "function a(req, res) {", "  res.json(one());", "}", "", "function b(req, res) {", "  res.json(two());", "}", "export default { a, b };")
        files["src/routes.ts"] = ts(EXPRESS, 'import ctl from "./ctl";', "", "export const router = Router();", "", 'router.get("/a", ctl.a);', 'router.get("/b", ctl.b);')
        res = self.go(files=files)
        self.assertEqual(self.reached(res), ["GET /m/a", "GET /m/b"])

    def test_commonjs_require_is_not_narrowed_by_name(self):
        files = dict(self.FILES)
        files["src/ctl.js"] = ts('const { one, two } = require("./svc");', "", "exports.a = function a(req, res) {", "  res.json(one());", "};", "", "exports.b = function b(req, res) {", "  res.json(two());", "};")
        files.pop("src/ctl.ts")
        files["src/svc.js"] = ts("exports.one = function one() {", "  return 1;", "};", "exports.two = function two() {", "  return 2;", "};")
        files.pop("src/svc.ts")
        files["src/routes.ts"] = ts(EXPRESS, 'const ctl = require("./ctl");', "", "export const router = Router();", "", 'router.get("/a", ctl.a);', 'router.get("/b", ctl.b);')
        res = self.run_case(files, {"src/svc.js": files["src/svc.js"].replace("return 1;", "return 3;")})
        self.assertEqual(self.reached(res), ["GET /m/a", "GET /m/b"])

    def test_an_aliased_binding_is_followed(self):
        files = dict(self.FILES)
        files["src/routes.ts"] = ts(EXPRESS, 'import { a as first, b } from "./ctl";', "", "export const router = Router();", "", 'router.get("/a", first);', 'router.get("/b", b);')
        self.assertEqual(self.reached(self.go(files=files)), ["GET /m/a"])

    def test_a_handler_declared_in_the_routes_file_that_uses_a_changed_import_is_reached(self):
        files = dict(self.FILES)
        files["src/routes.ts"] = ts(EXPRESS, 'import { one } from "./svc";', "", "export const router = Router();", "", "function a(req, res) {", "  res.json(one());", "}", 'router.get("/a", a);', 'router.get("/b", (req, res) => res.end());')
        res = self.go(files=files)
        self.assertEqual(self.reached(res), ["GET /m/a"])

    def test_an_inline_handler_that_uses_a_changed_import_is_reached(self):
        files = dict(self.FILES)
        files["src/routes.ts"] = ts(EXPRESS, 'import { one } from "./svc";', "", "export const router = Router();", "", 'router.get("/a", (req, res) => res.json(one()));', 'router.get("/b", (req, res) => res.end());')
        self.assertEqual(self.reached(self.go(files=files)), ["GET /m/a"])

    def test_a_middleware_named_in_a_route_call_reaches_that_route_only(self):
        files = dict(self.FILES)
        files["src/mw.ts"] = ts("export function mw(req, res, next) {", "  next();", "}")
        files["src/routes.ts"] = ts(EXPRESS, 'import { a, b } from "./ctl";', 'import { mw } from "./mw";', "", "export const router = Router();", "", 'router.get("/a", mw, a);', 'router.get("/b", b);')
        res = self.run_case(files, {"src/mw.ts": files["src/mw.ts"].replace("next();", "next(); // x")})
        self.assertEqual(self.reached(res), ["GET /m/a"])

    def test_middleware_of_the_app_reaches_every_route_in_and_through_the_file(self):
        files = dict(self.FILES)
        files["src/mw.ts"] = ts("export function mw(req, res, next) {", "  next();", "}")
        files["src/app.ts"] = ts(EXPRESS, 'import { router } from "./routes";', 'import { mw } from "./mw";', "", "const app = express();", "app.use(mw);", 'app.use("/m", router);', 'app.get("/own", (req, res) => res.end());')
        res = self.run_case(files, {"src/mw.ts": files["src/mw.ts"].replace("next();", "next(); // x")})
        self.assertEqual(self.reached(res), ["GET /m/a", "GET /m/b", "GET /own"])

    def test_middleware_of_a_router_reaches_every_route_of_the_file(self):
        files = dict(self.FILES)
        files["src/mw.ts"] = ts("export function mw(req, res, next) {", "  next();", "}")
        files["src/routes.ts"] = ts(EXPRESS, 'import { a, b } from "./ctl";', 'import { mw } from "./mw";', "", "export const router = Router();", "router.use(mw);", 'router.get("/a", a);', 'router.get("/b", b);')
        res = self.run_case(files, {"src/mw.ts": files["src/mw.ts"].replace("next();", "next(); // x")})
        self.assertEqual(self.reached(res), ["GET /m/a", "GET /m/b"])

    def test_a_change_that_nothing_in_the_routes_file_spells_reaches_no_route(self):
        files = dict(self.FILES)
        files["src/other.ts"] = service("o")
        res = self.run_case(files, {"src/other.ts": service("o", "2")})
        self.assertEqual(self.reached(res), [])

    def test_a_statement_that_runs_when_the_file_loads_reaches_every_route(self):
        files = dict(self.FILES)
        files["src/boot.ts"] = service("boot")
        files["src/routes.ts"] = ts(EXPRESS, 'import { a, b } from "./ctl";', 'import { boot } from "./boot";', "", "export const router = Router();", "boot();", 'router.get("/a", a);', 'router.get("/b", b);')
        res = self.run_case(files, {"src/boot.ts": service("boot", "2")})
        self.assertEqual(self.reached(res), ["GET /m/a", "GET /m/b"])

    def test_app_listen_is_not_a_route_and_a_port_constant_reaches_none(self):
        files = dict(self.FILES)
        files["src/config.ts"] = ts("export const PORT = 3000;")
        files["src/app.ts"] = ts(EXPRESS, 'import { router } from "./routes";', 'import { PORT } from "./config";', "", "const app = express();", 'app.use("/m", router);', "app.listen(PORT);")
        res = self.run_case(files, {"src/config.ts": ts("export const PORT = 3001;")})
        self.assertEqual(self.reached(res), [])
        self.assertTrue(res.complete)

    def test_an_entry_file_that_exports_a_helper_carries_the_change_to_its_importers(self):
        files = dict(self.FILES)
        files["src/routes.ts"] = self.FILES["src/routes.ts"].replace('import { a, b } from "./ctl";', 'import { a, b } from "./ctl";\nimport { one } from "./svc";') + "export function helper() {\n  return one();\n}\n"
        files["src/other.ts"] = ts(EXPRESS, 'import { helper } from "./routes";', "export const r2 = Router();", 'r2.get("/h", helper);')
        files["src/app.ts"] = ts(EXPRESS, 'import { router } from "./routes";', 'import { r2 } from "./other";', "", "const app = express();", 'app.use("/m", router);', 'app.use("/o", r2);')
        res = self.run_case(files, {"src/svc.ts": self.FILES["src/svc.ts"].replace("return 1;", "return 3;")})
        self.assertEqual(self.reached(res), ["GET /m/a", "GET /o/h"])

    def test_a_name_that_a_route_statement_uses_is_not_followed_past_the_routes_file(self):
        """A route statement is code that runs when the file loads: it uses what reached it, so the whole module is reached and the walk does not go on."""
        files = dict(self.FILES)
        files["src/routes.ts"] = self.FILES["src/routes.ts"] + "export function helper() {\n  return a;\n}\n"
        files["src/other.ts"] = ts(EXPRESS, 'import { helper } from "./routes";', "export const r2 = Router();", 'r2.get("/h", helper);')
        files["src/app.ts"] = ts(EXPRESS, 'import { router } from "./routes";', 'import { r2 } from "./other";', "", "const app = express();", 'app.use("/m", router);', 'app.use("/o", r2);')
        res = self.run_case(files, {"src/svc.ts": self.FILES["src/svc.ts"].replace("return 1;", "return 3;")})
        self.assertEqual(self.reached(res), ["GET /m/a"])

    def test_a_router_that_nothing_mounts_is_said_and_claims_no_route(self):
        files = dict(self.FILES)
        files["src/app.ts"] = ts(EXPRESS, 'import { router } from "./routes";', "", "const app = express();", '// app.use("/m", router);')
        res = self.go(files=files)
        self.assertEqual(self.reached(res), [])
        self.assertEqual(self.records_of("router_unmounted"), [("src/routes.ts:router", False)])
        self.assertTrue(res.complete)

    def test_a_route_reached_twice_is_one_area_with_both_chains(self):
        files = dict(self.FILES)
        files["src/routes.ts"] = ts(EXPRESS, 'import { a, b } from "./ctl";', "", "export const router = Router();", "", 'router.get("/a", a);', 'router.get("/b", b);')
        files["src/svc.ts"] = self.FILES["src/svc.ts"]
        changed = {"src/svc.ts": self.FILES["src/svc.ts"].replace("return 1;", "return 3;").replace("return 2;", "return 4;")}
        res = self.run_case(files, changed)
        self.assertEqual(self.reached(res), ["GET /m/a", "GET /m/b"])

    def test_an_unreadable_chain_makes_the_area_not_complete(self):
        res = self.go()
        self.assertTrue(res.complete)
        analysis = self.analysis
        entry = self.ex
        chain = res.walk.chains[0]
        from dataclasses import replace

        bad = replace(chain, complete=False, reasons=("importers of x were not established",))
        reached = entry.entry_points_reached([bad])
        self.assertEqual([r.complete for r in reached], [False])
        self.assertIn("importers of x were not established", reached[0].reasons)


class ReachByEdit(Base):
    ROUTES = ts(EXPRESS, 'import { a } from "./ctl";', "const PREFIX = \"/p\";", "", "export const router = Router({ mergeParams: true });", "", "function helper(x) {", "  return x;", "}", "",
                'router.get("/a", a);', 'router.get("/b", (req, res) => {', "  res.json(helper(1));", "});", 'router.route("/c")', "  .get(ca)", "  .post(cb);")
    APP = ts(EXPRESS, 'import { router } from "./routes";', "", "const app = express();", 'app.use("/m", router);', 'app.get("/own", (req, res) => res.end());')
    FILES = {"src/routes.ts": ROUTES, "src/app.ts": APP, "src/ctl.ts": ts("export function a() {}")}

    def edit(self, old, new, file="src/routes.ts", files=None):
        base = dict(files or self.FILES)
        return self.run_case(base, {file: base[file].replace(old, new)})

    def test_a_line_inside_an_inline_handler_reaches_its_route_only(self):
        res = self.edit("  res.json(helper(1));", "  res.json(helper(2));")
        self.assertEqual(self.reached(res), ["GET /m/b"])
        self.assertEqual(self.chain(res, "GET /m/b")[0], ("src/routes.ts", 13))

    def test_a_line_in_the_route_call_itself_reaches_that_route_only(self):
        res = self.edit('router.get("/a", a);', 'router.get("/a", a, a);')
        self.assertEqual(self.reached(res), ["GET /m/a"])

    def test_a_line_inside_a_declaration_that_a_route_uses_reaches_the_routes_that_use_it(self):
        res = self.edit("  return x;", "  return x + 1;")
        self.assertEqual(self.reached(res), ["GET /m/b"])

    def test_a_line_in_one_call_of_a_chain_reaches_that_call_only(self):
        res = self.edit("  .post(cb);", "  .post(cb, cc);")
        self.assertEqual(self.reached(res), ["POST /m/c"])
        res = self.edit("  .get(ca)", "  .get(ca, cc)")
        self.assertEqual(self.reached(res), ["GET /m/c"])

    def test_the_route_path_line_of_a_chain_reaches_every_call_of_it(self):
        res = self.edit('router.route("/c")', 'router.route("/c2")')
        self.assertEqual(self.reached(res), ["GET /m/c2", "POST /m/c2"])

    def test_the_line_that_builds_the_router_reaches_every_route_on_it(self):
        res = self.edit("Router({ mergeParams: true })", "Router({ mergeParams: false })")
        self.assertEqual(self.reached(res), ["GET /m/a", "GET /m/b", "GET /m/c", "POST /m/c"])

    def test_the_mount_line_reaches_every_route_through_it_and_not_the_routes_of_the_app(self):
        res = self.edit('app.use("/m", router);', 'app.use("/n", router);', "src/app.ts")
        self.assertEqual(self.reached(res), ["GET /n/a", "GET /n/b", "GET /n/c", "POST /n/c"])

    def test_a_changed_import_line_reaches_the_routes_that_spell_the_names_it_binds(self):
        files = dict(self.FILES)
        files["src/ctl.ts"] = ts("export function a() {}", "export function z() {}")
        res = self.edit('import { a } from "./ctl";', 'import { a, z } from "./ctl";', files=files)
        self.assertEqual(self.reached(res), ["GET /m/a"])

    def test_a_statement_that_is_not_a_route_reaches_every_route_in_and_through_the_file(self):
        res = self.edit('router.get("/a", a);', 'router.use(mw);\nrouter.get("/a", a);')
        self.assertEqual(self.reached(res), ["GET /m/a", "GET /m/b", "GET /m/c", "POST /m/c"])

    def test_a_removed_route_statement_reaches_nothing_in_the_file(self):
        res = self.edit('router.get("/a", a);\n', "")
        self.assertEqual(self.reached(res), [])

    def test_a_removed_line_in_a_route_that_is_still_there_reaches_it(self):
        base = dict(self.FILES)
        base["src/routes.ts"] = self.ROUTES.replace('router.get("/a", a);', 'router.get(\n  "/a",\n  mw,\n  a,\n);')
        res = self.run_case(base, {"src/routes.ts": base["src/routes.ts"].replace("  mw,\n", "")})
        self.assertEqual(self.reached(res), ["GET /m/a"])

    def test_a_removed_middleware_statement_reaches_every_route(self):
        base = dict(self.FILES)
        base["src/routes.ts"] = self.ROUTES.replace('router.get("/a", a);', "router.use(mw);\nrouter.get(\"/a\", a);")
        res = self.run_case(base, {"src/routes.ts": base["src/routes.ts"].replace("router.use(mw);\n", "")})
        self.assertEqual(self.reached(res), ["GET /m/a", "GET /m/b", "GET /m/c", "POST /m/c"])

    def test_a_removed_import_line_reaches_the_routes_that_spelled_its_names(self):
        res = self.edit('import { a } from "./ctl";\n', "")
        self.assertEqual(self.reached(res), ["GET /m/a"])

    def test_without_a_pre_change_version_the_added_lines_still_reach_their_statement(self):
        files = {"tsconfig.json": TSCONFIG, **self.FILES}
        self.repo({"files": files}, {"files": {**files, "src/routes.ts": files["src/routes.ts"].replace("  res.json(helper(1));", "  res.json(helper(1), 2);")}})
        self.entry()
        analysis = self.analyze(self.language, pre=None)
        res = entrypoints.reach(analysis, self.language, self.ex)
        self.assertEqual(self.reached(res), ["GET /m/b"])

    def test_a_blank_line_or_a_comment_between_statements_changes_nothing(self):
        res = self.edit('router.get("/a", a);', '// a note\n\nrouter.get("/a", a);')
        self.assertEqual(self.reached(res), [])

    def test_an_edit_in_an_unmounted_router_says_so_and_claims_no_route(self):
        files = {"src/r.ts": ts(EXPRESS, "export const r = Router();", 'r.get("/x", (req, res) => res.end());'), "src/app.ts": ts(EXPRESS, "const app = express();", 'app.get("/a", h);')}
        res = self.run_case(files, {"src/r.ts": files["src/r.ts"].replace("res.end()", "res.send(1)")})
        self.assertEqual(self.reached(res), [])
        self.assertEqual(self.records_of("router_unmounted"), [("src/r.ts:r", False)])

    def test_a_change_inside_a_router_file_that_does_not_lex_is_incomplete_and_blocking(self):
        files = {"src/r.ts": ts(EXPRESS, "const r = Router();", 'r.get("/x", h);'), "src/app.ts": ts(EXPRESS, 'import r from "./r";', "const app = express();", 'app.use("/m", r);')}
        res = self.run_case(files, {"src/r.ts": files["src/r.ts"] + "const s = `open\n"})
        self.assertFalse(res.complete)
        self.assertEqual(self.reached(res), [])
        self.assertIn(("src/r.ts", True), self.records_of("parse_degraded"))


class RoutesChanged(Base):
    def test_removed_and_added_routes_are_the_difference_of_the_tables(self):
        before = ts(EXPRESS, "const app = express();", 'app.get("/a", h);', 'app.get("/b", h);')
        after = ts(EXPRESS, "const app = express();", 'app.get("/a", h);', 'app.get("/c", h);')
        self.repo({"files": {"tsconfig.json": TSCONFIG, "src/app.ts": before}}, {"files": {"src/app.ts": after}})
        ex = self.entry()
        removed, added = ex.routes_changed(self.sha("main~1"), self.sha("main"))
        self.assertEqual(([r.path for r in removed], [r.path for r in added]), (["/b"], ["/c"]))
        self.assertEqual(removed[0].evidence[0].ref, self.sha("main~1"))
        self.assertEqual(added[0].evidence[0].ref, self.sha("main"))

    def test_without_a_pre_change_version_nothing_is_computed(self):
        self.repo({"files": {"tsconfig.json": TSCONFIG, "src/app.ts": ts(EXPRESS, "const app = express();", 'app.get("/a", h);')}})
        self.assertEqual(self.entry().routes_changed(None, self.sha("main")), ([], []))

    def test_a_route_that_moves_to_another_mount_is_a_removed_and_an_added_route(self):
        files = lambda prefix: {"src/app.ts": ts(EXPRESS, 'import r from "./r";', "const app = express();", f'app.use("{prefix}", r);'), "src/r.ts": ts(EXPRESS, "const r = Router();", 'r.get("/x", h);', "export default r;")}  # noqa: E731
        self.repo({"files": {"tsconfig.json": TSCONFIG, **files("/a")}}, {"files": files("/b")})
        removed, added = self.entry().routes_changed(self.sha("main~1"), self.sha("main"))
        self.assertEqual(([r.path for r in removed], [r.path for r in added]), (["/a/x"], ["/b/x"]))

    def test_the_routes_of_a_repository_of_another_name_are_refused(self):
        self.repo({"files": {"tsconfig.json": TSCONFIG, "src/app.ts": ts(EXPRESS, "const app = express();")}})
        with self.assertRaises(ValueError):
            self.entry().routes("other/repo", self.sha("main"))


class Registry(Base):
    def test_the_adapter_is_built_from_the_config_by_kind(self):
        from reachmap import config

        ws = config.loads(
            'workspace = "w"\n[repos."t/orders"]\nhost = "git"\nremote = "file:///x"\nlanguage = "typescript"\nroles = ["backend"]\nentry_points = { kind = "express" }\nservice_marker = "pkg.json"\n',
            source="inline", fmt="toml", env={},
        )
        repo = ws.repos["t/orders"]
        self.repo({"files": {"tsconfig.json": TSCONFIG, "src/app.ts": ts(EXPRESS, "const app = express();")}})
        language = self.make_adapter()
        adapter = entrypoints.entry_adapter(repo, language)
        self.assertIsInstance(adapter, ExpressAdapter)
        self.assertEqual((adapter.kind, adapter.service_marker), ("express", "pkg.json"))

    def test_an_adapter_needs_the_javascript_adapter(self):
        from reachmap import config
        from reachmap.adapters.python import PyAdapter

        ws = config.loads('workspace = "w"\n[repos."t/orders"]\nhost = "git"\nremote = "file:///x"\nlanguage = "typescript"\nroles = ["backend"]\nentry_points = { kind = "express" }\n', source="inline", fmt="toml", env={})
        self.repo({"files": {"src/app.ts": ts(EXPRESS, "const app = express();")}})
        with self.assertRaises(TypeError):
            entrypoints.entry_adapter(ws.repos["t/orders"], PyAdapter(self.REPO, self.url, self.git))

    def test_the_kinds_are_registered_after_the_two_of_m7(self):
        self.assertEqual(entrypoints.KINDS[:2], ("react-router", "fastapi"))
        self.assertIn("express", entrypoints.KINDS)
        self.assertIn("nestjs", entrypoints.KINDS)

    def test_is_entry_file_and_structural_names(self):
        files = {"tsconfig.json": TSCONFIG, "src/app.ts": APP_FILE, "src/routes/users.ts": USERS_FILE + "export const helper = 1;\n", "src/ctl.ts": CTL}
        self.repo({"files": files})
        ex = self.entry()
        sha = self.sha("main")
        self.assertTrue(ex.is_entry_file("src/app.ts", sha))
        self.assertTrue(ex.is_entry_file("src/routes/users.ts", sha))
        self.assertFalse(ex.is_entry_file("src/ctl.ts", sha))
        self.assertEqual(ex.structural_names("src/routes/users.ts", sha), frozenset({"default"}))
        self.assertEqual(ex.structural_names("src/ctl.ts", sha), frozenset())


class AnyMethod(Base):
    def test_a_route_that_answers_every_method_fits_a_client_call_of_any_method(self):
        from reachmap.adapters.http_client import url_from_parts
        from reachmap.edges.http import best_matches

        files = {"src/app.ts": ts(EXPRESS, "const app = express();", 'app.all("/hook", h);', 'app.get("/only", h);')}
        routes = self.table(files)
        url = url_from_parts([("lit", "/hook")])
        for method in ("GET", "POST", "DELETE"):
            self.assertEqual([m.route.path for m in best_matches(url, method, routes)], ["/hook"], method)
        only = url_from_parts([("lit", "/only")])
        self.assertEqual([(m.route.path, m.fallback) for m in best_matches(url, "POST", routes)], [("/hook", False)])  # an exact fit, not a fallback to another method
        self.assertEqual([(m.route.path, m.fallback) for m in best_matches(only, "POST", routes)], [("/only", True)])  # a method no route has: the route of another method, flagged


class Robust(Base):
    """Code that is cut in the middle or is token soup is never a crash: what cannot be read is a problem and a record."""

    SOURCES = {
        "src/app.ts": ts(EXPRESS, 'import users from "./users";', "const app = express();", 'app.use("/users", users);', "app", '  .route("/x/:id")', "  .get(a)", "  .post(b);",
                         'module.exports = { app };', "if (x) { app.get(`/t/${y}`, (req, res) => { res.end(); }); }"),
        "src/users.ts": ts(EXPRESS, "const router = Router({ mergeParams: true });", 'router.get("/", list);', 'router.use("/:id", sub, [a, b]);', "export default router;"),
    }

    def test_a_file_cut_at_any_point_does_not_crash_the_adapter(self):
        for name, text in self.SOURCES.items():
            for cut in range(0, len(text), 13):
                with self.subTest(file=name, cut=cut):
                    files = dict(self.SOURCES)
                    files[name] = text[:cut]
                    self.repo({"files": {"tsconfig.json": TSCONFIG, **files}})
                    ex = self.entry()
                    ex.routes(None, self.sha("main"))
                    ex.unmounted(self.sha("main"))

    def test_token_soup_does_not_crash_the_adapter(self):
        import random

        words = ["app", "router", "express", "Router", ".", "(", ")", "get", "use", "route", "post", ",", ";", '"/a"', "`x${", "}`", "{", "}", "=>", "const", "=", "require", "import", "from", "export", "default", "[", "]", "+"]
        rng = random.Random(7)
        for n in range(25):
            text = " ".join(rng.choice(words) for _ in range(rng.randint(5, 60)))
            with self.subTest(n=n):
                self.repo({"files": {"tsconfig.json": TSCONFIG, "src/app.ts": EXPRESS + "\n" + text}})
                ex = self.entry()
                ex.routes(None, self.sha("main"))

    def test_a_changed_file_cut_at_any_point_does_not_crash_the_reach(self):
        text = self.SOURCES["src/users.ts"]
        for cut in range(10, len(text), 17):
            with self.subTest(cut=cut):
                files = {"tsconfig.json": TSCONFIG, **self.SOURCES}
                self.repo({"files": files}, {"files": {"src/users.ts": text[:cut]}})
                self.entry()
                self.reach()


if __name__ == "__main__":
    unittest.main()
