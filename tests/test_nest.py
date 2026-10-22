"""The NestJS entry-point adapter on small repositories: the route table (controllers, verbs, paths, the global prefix, modules and what
they list, which controllers are served), what is not Nest (decorators that do not come from `@nestjs/common`), what the adapter says when
it cannot read what it needs, and what a change reaches (a method, the property a service is injected through, a module, the file that
creates the application).

The M16 fixture scenarios (tests/fixtures/express) run end to end in test_ex_scenarios.py.
"""

import unittest
from unittest import mock

from reachmap import entrypoints
from reachmap.adapters.express import NestAdapter
from reachmap.adapters.js import JsAdapter
from reachmap.model import Read
from tests import scenario_support as ss
from tests.support_universe import Base as UniverseBase
from tests.support_universe import f

TSCONFIG = '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}\n'
COMMON = 'import { Controller, Get, Post, Put, Patch, Delete, Options, Head, All, Sse, Module, Version, Param, Body, UseGuards } from "@nestjs/common";'
MAIN = f(['import { NestFactory } from "@nestjs/core";', 'import { AppModule } from "./app.module";', "", "async function bootstrap() {", "  const app = await NestFactory.create(AppModule);",
          '  app.setGlobalPrefix("api");', "  await app.listen(3000);", "}", "bootstrap();"])
APP_MODULE = f([COMMON, 'import { ItemsModule } from "./items/items.module";', "", "@Module({", "  imports: [ItemsModule],", "})", "export class AppModule {}"])
ITEMS_MODULE = f([COMMON, 'import { ItemsController } from "./items.controller";', "", "@Module({", "  controllers: [ItemsController],", "})", "export class ItemsModule {}"])


def ts(*lines):
    return f(list(lines))


def controller(*body, path='"items"', head=COMMON, extra=()):
    return ts(head, *extra, "", f"@Controller({path})" if path is not None else "@Controller", "export class ItemsController {", *body, "}")


def rig(controller_text, main=MAIN, app=APP_MODULE, module=ITEMS_MODULE, **more):
    return {"tsconfig.json": TSCONFIG, "src/main.ts": main, "src/app.module.ts": app, "src/items/items.module.ts": module, "src/items/items.controller.ts": controller_text, **more}


class Base(UniverseBase):
    UNIVERSE_PREFIX = "reachmap-nest-"
    REPO = "t/ledger"

    def make_adapter(self, **kw):
        kw.setdefault("test_markers", ["__tests__/", ".test.", ".spec."])
        kw.setdefault("aliases_from", "tsconfig.json")
        return JsAdapter(self.REPO, self.url, self.git, **kw)

    def entry(self, **kw):
        self.language = self.make_adapter()
        self.nest = NestAdapter(self.REPO, self.language, **kw)
        return self.nest

    def table(self, files, **kw):
        self.repo({"files": files})
        return self.entry(**kw).routes(None, self.sha("main"))

    def labels(self, files, **kw):
        return sorted(f"{r.method} {r.path}" for r in self.table(files, **kw))

    def route(self, files, label):
        (r,) = [r for r in self.table(files) if f"{r.method} {r.path}" == label]
        return r

    def reach(self, **kw):
        analysis = self.analyze(self.language)
        res = entrypoints.reach(analysis, self.language, self.nest, **kw)
        evidence = [e for r in res.reached for c in r.area.chains for e in c]
        self.assertEqual(ss.evidence_problems({self.REPO: self.path}, evidence), [])
        return res

    def run_case(self, files, changed, **kw):
        self.repo({"files": files}, {"files": changed})
        self.entry()
        return self.reach(**kw)

    def reached(self, res):
        return sorted(r.area.label for r in res.reached)

    def records_of(self, kind):
        return sorted((r.subject, r.blocks_selection) for r in self.records if r.kind == kind)

    def chain(self, res, label, n=0):
        (r,) = [r for r in res.reached if r.area.label == label]
        return [(e.path, e.line) for e in r.area.chains[n]]


class Routes(Base):
    def test_every_verb_and_the_path_of_the_decorator(self):
        text = controller('  @Get() a() {}', '  @Get(":id") b() {}', '  @Post("x") c() {}', '  @Put(":id") d() {}', '  @Patch(":id") e() {}', '  @Delete(":id") g() {}', '  @Options() h() {}',
                          '  @Head() i() {}', '  @All("any") j() {}', '  @Sse("events") k() {}')
        self.assertEqual(self.labels(rig(text)), ["ANY /api/items/any", "DELETE /api/items/{id}", "GET /api/items", "GET /api/items/events", "GET /api/items/{id}", "HEAD /api/items",
                                                  "OPTIONS /api/items", "PATCH /api/items/{id}", "POST /api/items/x", "PUT /api/items/{id}"])

    def test_the_controller_path_forms(self):
        for path, want in (('"items"', "/api/items"), ("'items'", "/api/items"), ('"/items/"', "/api/items"), ("", "/api"), ('{ path: "items" }', "/api/items"), ('{ path: "items", host: "h" }', "/api/items"),
                           ("`items`", "/api/items"), ('"a/b"', "/api/a/b")):
            with self.subTest(path=path):
                text = controller("  @Get() a() {}", path=path)
                self.assertEqual(self.labels(rig(text)), [f"GET {want}"])

    def test_a_controller_without_parentheses_has_no_path(self):
        text = ts(COMMON, "", "@Controller", "export class ItemsController {", "  @Get() a() {}", "}")
        self.assertEqual(self.labels(rig(text)), ["GET /api"])

    def test_a_list_of_controller_paths_gives_a_route_for_each(self):
        text = controller("  @Get() a() {}", path='["a", "b"]')
        self.assertEqual(self.labels(rig(text)), ["GET /api/a", "GET /api/b"])

    def test_a_list_of_method_paths_gives_a_route_for_each(self):
        text = controller('  @Get(["x", "y/:id"]) a() {}')
        self.assertEqual(self.labels(rig(text)), ["GET /api/items/x", "GET /api/items/y/{id}"])

    def test_a_constant_of_another_file_is_the_path(self):
        text = controller("  @Get(SUB) a() {}", path="BASE", extra=('import { BASE, SUB } from "./paths";',))
        files = rig(text, **{"src/items/paths.ts": ts('export const BASE = "things";', 'export const SUB = ":id";')})
        self.assertEqual(self.labels(files), ["GET /api/things/{id}"])

    def test_a_constant_of_the_same_file(self):
        text = ts(COMMON, 'const BASE = "things";', "", "@Controller(BASE)", "export class C {", "  @Get() a() {}", "}")
        self.assertEqual(self.labels(rig(text, module=ITEMS_MODULE.replace("ItemsController", "C").replace("./items.controller", "./items.controller"))), ["GET /api/things"])

    def test_a_path_that_cannot_be_evaluated_is_an_unknown_segment_and_partial(self):
        text = controller("  @Get(pathFor()) a() {}")
        (r,) = self.table(rig(text))
        self.assertEqual((r.path, r.partial), ("/api/items/<unknown:pathFor()>", True))

    def test_a_pattern_in_a_method_path_is_an_unknown_segment(self):
        text = controller('  @Get("ab*cd") a() {}')
        (r,) = self.table(rig(text))
        self.assertEqual((r.path, r.partial), ("/api/items/<unknown:ab*cd>", True))

    def test_the_root_path_is_a_slash_without_a_prefix(self):
        text = controller("  @Get() a() {}", path=None)
        files = rig(text, main=MAIN.replace('  app.setGlobalPrefix("api");\n', ""))
        self.assertEqual(self.labels(files), ["GET /"])

    def test_the_route_is_cited_at_the_method_decorator_the_controller_the_module_and_the_prefix(self):
        text = controller('  @Get(":id")', "  async one(@Param(\"id\") id: string) {}")
        r = self.route(rig(text), "GET /api/items/{id}")
        self.assertEqual([(e.path, e.line) for e in r.evidence], [("src/items/items.controller.ts", 5), ("src/items/items.controller.ts", 3), ("src/items/items.module.ts", 5), ("src/main.ts", 6)])
        self.assertEqual(r.evidence[0].claim, "route:GET /api/items/{id}:src/items/items.controller.ts")
        self.assertEqual((r.handler, r.target, r.kind, r.partial, r.file), ("one", "src/items/items.controller.ts", "api_route", False, "src/items/items.controller.ts"))

    def test_a_controller_without_a_path_is_not_cited_at_its_decorator(self):
        text = controller("  @Get() a() {}", path="")
        r = self.table(rig(text))[0]
        self.assertEqual([e.path for e in r.evidence], ["src/items/items.controller.ts", "src/items/items.module.ts", "src/main.ts"])

    def test_without_a_global_prefix_the_prefix_line_is_not_cited(self):
        files = rig(controller("  @Get() a() {}"), main=MAIN.replace('  app.setGlobalPrefix("api");\n', ""))
        r = self.table(files)[0]
        self.assertEqual([e.path for e in r.evidence], ["src/items/items.controller.ts", "src/items/items.controller.ts", "src/items/items.module.ts"])

    def test_the_decorator_over_several_lines_is_cited_at_its_first_line(self):
        text = controller("  @Get(", '    ":id",', "  )", "  one() {}")
        r = self.table(rig(text))[0]
        self.assertEqual(r.evidence[0].line, 5)

    def test_methods_that_are_not_routes_are_skipped_and_members_with_odd_shapes_are_read(self):
        text = controller(
            "  private readonly name = 'x';", "  static count = 0;", "  constructor(private readonly svc: Svc, @Inject(TOKEN) private t: Tok) {}", "  helper(): { a: string } {", "    return { a: 'b' };", "  }",
            "  @Get('a')", "  async one<T>(@Param('id') id: string): Promise<{ ok: T }> {", "    return { ok: id as T };", "  }", "  get value() { return 1; }", "  set value(v) {}",
            "  handler = () => 1;", "  @Get('b') two() {}", "  [computed]() {}", "  abstract three(): void;",
        )
        self.assertEqual(self.labels(rig(text)), ["GET /api/items/a", "GET /api/items/b"])

    def test_a_method_decorated_with_other_decorators_too(self):
        text = controller('  @UseGuards(AuthGuard("jwt"))', '  @Get("a")', "  @Version(\"1\")", "  one() {}")
        (r,) = self.table(rig(text))
        self.assertEqual(r.evidence[0].line, 6)

    def test_a_controller_that_is_the_default_export_is_registered_by_a_default_import(self):
        text = ts(COMMON, "", '@Controller("items")', "export default class ItemsController {", "  @Get() a() {}", "}")
        module = ITEMS_MODULE.replace("import { ItemsController }", "import ItemsController")
        self.assertEqual(self.labels(rig(text, module=module)), ["GET /api/items"])

    def test_a_controller_that_is_not_exported_cannot_be_registered(self):
        text = ts(COMMON, "", '@Controller("items")', "class ItemsController {", "  @Get() a() {}", "}")
        self.assertEqual(self.labels(rig(text)), [])
        self.assertEqual(self.nest.unmounted(self.sha("main")), [("src/items/items.controller.ts", "ItemsController")])


class Prefix(Base):
    def test_every_global_prefix_gives_its_own_routes(self):
        main = MAIN.replace('  app.setGlobalPrefix("api");\n', '  app.setGlobalPrefix("api");\n  other.setGlobalPrefix("v2");\n')
        self.assertEqual(self.labels(rig(controller("  @Get() a() {}"), main=main)), ["GET /api/items", "GET /v2/items"])

    def test_the_prefix_may_be_in_any_file(self):
        files = rig(controller("  @Get() a() {}"), main=MAIN.replace('  app.setGlobalPrefix("api");\n', "  configure(app);\n"),
                    **{"src/setup.ts": ts("export function configure(app) {", '  app.setGlobalPrefix("v1");', "}")})
        self.assertEqual(self.labels(files), ["GET /v1/items"])

    def test_the_prefix_may_be_a_constant(self):
        main = MAIN.replace('import { AppModule }', 'import { PREFIX } from "./prefix";\nimport { AppModule }').replace('setGlobalPrefix("api")', "setGlobalPrefix(PREFIX)")
        files = rig(controller("  @Get() a() {}"), main=main, **{"src/prefix.ts": ts('export const PREFIX = "api/v3";')})
        self.assertEqual(self.labels(files), ["GET /api/v3/items"])

    def test_a_prefix_that_cannot_be_evaluated_makes_the_routes_partial(self):
        files = rig(controller("  @Get() a() {}"), main=MAIN.replace('setGlobalPrefix("api")', "setGlobalPrefix(process.env.PREFIX)"))
        (r,) = self.table(files)
        self.assertEqual((r.path, r.partial), ("/<unknown:process.env.PREFIX>/items", True))

    def test_the_exclude_option_makes_the_routes_partial_and_is_said(self):
        files = rig(controller("  @Get() a() {}"), main=MAIN.replace('setGlobalPrefix("api")', 'setGlobalPrefix("api", { exclude: ["health"] })'))
        (r,) = self.table(files)
        self.assertEqual((r.path, r.partial), ("/api/items", True))
        self.reach_unknown()

    def reach_unknown(self):
        table = self.nest.table(self.sha("main"))
        comp = table.composed()[0]
        self.assertEqual([u[0] for u in comp.unknown], ["exclude"])


class Serving(Base):
    def test_a_controller_that_no_module_lists_is_not_served(self):
        module = ITEMS_MODULE.replace("controllers: [ItemsController],", "controllers: [],")
        files = rig(controller("  @Get() a() {}"), module=module)
        self.assertEqual(self.labels(files), [])
        self.assertEqual(self.nest.unmounted(self.sha("main")), [("src/items/items.controller.ts", "ItemsController")])

    def test_a_module_that_the_application_does_not_import_does_not_serve_its_controllers(self):
        app = APP_MODULE.replace("imports: [ItemsModule],", "imports: [],")
        files = rig(controller("  @Get() a() {}"), app=app)
        self.assertEqual(self.labels(files), [])
        self.assertEqual(self.nest.unmounted(self.sha("main")), [("src/items/items.controller.ts", "ItemsController")])

    def test_modules_import_modules_to_any_depth(self):
        app = ts(COMMON, 'import { MidModule } from "./mid.module";', "", "@Module({ imports: [MidModule] })", "export class AppModule {}")
        mid = ts(COMMON, 'import { ItemsModule } from "./items/items.module";', "", "@Module({", "  imports: [", "    ItemsModule,", "  ],", "})", "export class MidModule {}")
        self.assertEqual(self.labels(rig(controller("  @Get() a() {}"), app=app, **{"src/mid.module.ts": mid})), ["GET /api/items"])

    def test_a_dynamic_module_and_a_forward_reference_are_imports(self):
        app = ts(COMMON, 'import { ItemsModule } from "./items/items.module";', 'import { Other } from "./other";', 'import { TypeOrmModule } from "@nestjs/typeorm";', "",
                 "@Module({ imports: [TypeOrmModule.forRoot({}), ItemsModule.forRoot(), forwardRef(() => Other)] })", "export class AppModule {}")
        files = rig(controller("  @Get() a() {}"), app=app, **{"src/other.ts": ts(COMMON, "@Module({})", "export class Other {}")})
        self.assertEqual(self.labels(files), ["GET /api/items"])
        self.assertEqual(self.records_of("mount_unresolved"), [])

    def test_a_module_that_the_application_imports_through_forward_ref_serves_its_controllers(self):
        app = ts(COMMON, 'import { ItemsModule } from "./items/items.module";', "", "@Module({ imports: [forwardRef(() => ItemsModule)] })", "export class AppModule {}")
        self.assertEqual(self.labels(rig(controller("  @Get() a() {}"), app=app)), ["GET /api/items"])

    def test_without_a_factory_call_the_modules_nobody_imports_are_the_roots(self):
        files = rig(controller("  @Get() a() {}"), main="export const x = 1;\n")
        self.assertEqual(self.labels(files), ["GET /items"])

    def test_a_controller_listed_by_two_modules_has_a_route_for_each(self):
        second = ts(COMMON, 'import { ItemsController } from "./items/items.controller";', "", "@Module({", "  controllers: [ItemsController],", "})", "export class SecondModule {}")
        app = APP_MODULE.replace("imports: [ItemsModule],", "imports: [ItemsModule, SecondModule],").replace("export class", 'import { SecondModule } from "./second.module";\nexport class', 1)
        files = rig(controller("  @Get() a() {}"), app=app, **{"src/second.module.ts": second})
        self.assertEqual(len(self.table(files)), 2)

    def test_a_list_with_a_spread_is_an_unresolved_mount_and_blocks(self):
        module = ITEMS_MODULE.replace("controllers: [ItemsController],", "controllers: [...others, ItemsController],")
        self.assertEqual(self.labels(rig(controller("  @Get() a() {}"), module=module)), ["GET /api/items"])
        self.assertEqual(self.records_of("mount_unresolved"), [("src/items/items.module.ts:5", True)])
        self.assertTrue(self.nest.problems)

    def test_an_import_of_a_repository_name_that_is_not_a_module_is_unresolved(self):
        app = ts(COMMON, 'import { Missing } from "./missing";', 'import { ItemsModule } from "./items/items.module";', "", "@Module({ imports: [ItemsModule, Missing] })", "export class AppModule {}")
        files = rig(controller("  @Get() a() {}"), app=app, **{"src/missing.ts": ts("export const Missing = 1;")})
        self.labels(files)
        self.assertEqual(self.records_of("mount_unresolved"), [("src/app.module.ts:5", True)])

    def test_router_module_register_is_a_blocking_record(self):
        main = MAIN + 'import { RouterModule } from "@nestjs/core";\nRouterModule.register([{ path: "x", module: ItemsModule }]);\n'
        files = rig(controller("  @Get() a() {}"), main=main)
        self.labels(files)
        self.assertEqual(self.records_of("mount_unresolved"), [("src/main.ts:11", True)])

    def test_the_application_created_with_something_that_is_not_a_module_is_unresolved(self):
        files = rig(controller("  @Get() a() {}"), main=MAIN.replace("NestFactory.create(AppModule)", "NestFactory.create(makeModule())"))
        self.labels(files)
        self.assertEqual(self.records_of("mount_unresolved"), [("src/main.ts:5", True)])

    def test_a_controller_that_extends_a_class_with_routes_is_unread_and_blocks(self):
        text = ts(COMMON, 'import { Base } from "./base";', "", '@Controller("items")', "export class ItemsController extends Base {", "  @Get() a() {}", "}")
        files = rig(text, **{"src/items/base.ts": ts(COMMON, "export class Base {", '  @Get("health") health() {}', "}")})
        self.assertEqual(self.labels(files), ["GET /api/items"])
        self.assertEqual(self.records_of("routes_unread"), [("src/items/items.controller.ts:5", True)])

    def test_a_controller_that_extends_a_class_without_routes_is_read(self):
        text = ts(COMMON, 'import { Base } from "./base";', "", '@Controller("items")', "export class ItemsController extends Base {", "  @Get() a() {}", "}")
        files = rig(text, **{"src/items/base.ts": ts("export class Base {", "  log() {}", "}")})
        self.assertEqual(self.labels(files), ["GET /api/items"])
        self.assertEqual(self.records_of("routes_unread"), [])

    def test_a_controller_that_extends_a_class_of_a_package_is_read(self):
        text = ts(COMMON, 'import { Thing } from "some-package";', "", '@Controller("items")', "export class ItemsController extends Thing {", "  @Get() a() {}", "}")
        self.assertEqual(self.labels(rig(text)), ["GET /api/items"])
        self.assertEqual(self.records_of("routes_unread"), [])


class NotNest(Base):
    def test_a_decorator_that_does_not_come_from_nestjs_common_is_not_a_route(self):
        text = ts('import { Controller, Get } from "./decorators";', "", '@Controller("items")', "export class ItemsController {", "  @Get() a() {}", "}")
        self.assertEqual(self.labels(rig(text)), [])
        self.assertEqual(self.nest.unmounted(self.sha("main")), [])

    def test_a_get_from_another_package_is_not_a_route(self):
        text = ts('import { Controller } from "@nestjs/common";', 'import { Get } from "some-http-lib";', "", '@Controller("items")', "export class ItemsController {", "  @Get() a() {}", "}")
        self.assertEqual(self.labels(rig(text)), [])

    def test_a_controller_class_whose_only_nest_decorator_is_the_verb_has_no_route_without_controller(self):
        text = ts(COMMON, "", "export class Plain {", "  @Get() a() {}", "}")
        self.assertEqual(self.labels(rig(text)), [])

    def test_an_aliased_import_is_the_decorator(self):
        text = ts('import { Controller as C, Get as G } from "@nestjs/common";', "", '@C("items")', "export class ItemsController {", "  @G(\":id\") a() {}", "}")
        self.assertEqual(self.labels(rig(text)), ["GET /api/items/{id}"])

    def test_the_namespace_import_of_the_package(self):
        text = ts('import * as nest from "@nestjs/common";', "", '@nest.Controller("items")', "export class ItemsController {", "  @nest.Get(\":id\") a() {}", "}")
        self.assertEqual(self.labels(rig(text)), ["GET /api/items/{id}"])

    def test_a_decorator_in_a_comment_or_a_string_is_not_one(self):
        text = controller('  // @Get("gone")', '  /* @Post("also") */', "  doc = '@Get(\"x\")';", '  @Get("a") one() {}')
        self.assertEqual(self.labels(rig(text)), ["GET /api/items/a"])

    def test_a_test_file_is_not_read(self):
        files = rig(controller("  @Get() a() {}"), **{"src/items/items.controller.spec.ts": controller("  @Get(\"t\") a() {}")})
        self.assertEqual(self.labels(files), ["GET /api/items"])


class Versions(Base):
    VERSIONING = MAIN.replace('  app.setGlobalPrefix("api");\n', '  app.setGlobalPrefix("api");\n  app.enableVersioning();\n')

    def test_a_controller_version_is_an_unknown_segment_and_partial_when_versioning_is_enabled(self):
        text = controller("  @Get() a() {}", path='{ path: "items", version: "1" }')
        (r,) = self.table(rig(text, main=self.VERSIONING))
        self.assertEqual((r.path, r.partial), ("/api/<unknown:version>/items", True))

    def test_a_method_version_is_the_same(self):
        text = controller('  @Get() @Version("2") a() {}')
        (r,) = self.table(rig(text, main=self.VERSIONING))
        self.assertEqual((r.path, r.partial), ("/api/<unknown:version>/items", True))


class Unread(Base):
    def test_a_file_that_does_not_lex_claims_nothing_and_is_blocking(self):
        text = controller("  @Get() a() {}") + "const s = `never closed;\n"
        self.assertEqual(self.labels(rig(text)), [])
        self.assertEqual(self.records_of("parse_degraded"), [("src/items/items.controller.ts", True)])
        self.assertTrue(self.nest.problems)

    def test_a_search_that_cannot_be_read_is_a_problem(self):
        self.repo({"files": rig(controller("  @Get() a() {}"))})
        nest = self.entry()
        bad = mock.Mock(state=Read.UNREADABLE, detail="timed out", truncated_by=None, not_searched=(), partial_matches=())
        with mock.patch.object(self.language, "search", return_value=bad):
            nest.routes(None, self.sha("main"))
        self.assertTrue(any("unreadable" in p for p in nest.problems))

    def test_a_search_that_was_cut_is_a_problem(self):
        self.repo({"files": rig(controller("  @Get() a() {}"))})
        nest = self.entry()
        cut = mock.Mock(state=Read.FOUND, detail=None, truncated_by="examine_cap", examined=1, total=9, not_searched=(), partial_matches=())
        with mock.patch.object(self.language, "search", return_value=cut):
            nest.routes(None, self.sha("main"))
        self.assertTrue(any("search_truncated" in p for p in nest.problems))

    def test_a_file_the_tree_lists_that_is_absent_is_a_problem(self):
        self.repo({"files": rig(controller("  @Get() a() {}"))})
        nest = self.entry()
        absent = mock.Mock(is_found=False, is_absent=True, is_unreadable=False)
        with mock.patch.object(self.language, "file_text", return_value=absent):
            self.assertEqual(nest.routes(None, self.sha("main")), [])
        self.assertTrue(nest.problems)


SERVICE = ts("export class Svc {", "  all() {", "    return 1;", "  }", "}")
CTL = ts(
    COMMON, 'import { Svc } from "./svc";', 'import { Log } from "./log";', "", '@Controller("items")', "export class ItemsController {", "  constructor(", "    private readonly svc: Svc,",
    "    private readonly log: Log,", "    plain: Other,", "  ) {}", "", "  @Get()", "  list() {", "    return this.svc.all();", "  }", "", "  @Get(\":id\")", "  one() {", "    return this.helper();", "  }", "",
    "  helper() {", "    return this.svc.all();", "  }", "", "  @Post()", "  create() {", "    this.log.write();", "    return 1;", "  }", "", "  @Delete(\":id\")", "  remove() {", "    return 2;", "  }", "}",
)
LOG = ts("export class Log {", "  write() {}", "}")


class ReachByImport(Base):
    def files(self, ctl=CTL, **more):
        return rig(ctl, **{"src/items/svc.ts": SERVICE, "src/items/log.ts": LOG, **more})

    def go(self, changed, ctl=CTL):
        return self.run_case(self.files(ctl), changed)

    def test_a_service_reaches_the_methods_that_use_the_property_it_is_injected_through(self):
        res = self.go({"src/items/svc.ts": SERVICE.replace("return 1;", "return 2;")})
        self.assertEqual(self.reached(res), ["GET /api/items", "GET /api/items/{id}"])

    def test_the_method_that_calls_another_method_that_uses_the_property_is_reached_too(self):
        res = self.go({"src/items/svc.ts": SERVICE.replace("return 1;", "return 2;")})
        self.assertIn("GET /api/items/{id}", self.reached(res))

    def test_another_service_reaches_its_own_methods_only(self):
        res = self.go({"src/items/log.ts": LOG.replace("write() {}", "write() { return 1; }")})
        self.assertEqual(self.reached(res), ["POST /api/items"])

    def test_the_chain_is_the_import_then_the_route_evidence(self):
        res = self.go({"src/items/log.ts": LOG.replace("write() {}", "write() { return 1; }")})
        self.assertEqual(self.chain(res, "POST /api/items"), [("src/items/items.controller.ts", 3), ("src/items/items.controller.ts", 27), ("src/items/items.controller.ts", 5),
                                                              ("src/items/items.module.ts", 5), ("src/main.ts", 6)])

    def test_a_parameter_of_the_constructor_that_is_not_a_property_reaches_the_whole_class(self):
        ctl = CTL.replace('import { Log } from "./log";', 'import { Log } from "./log";\nimport { Other } from "./other";')
        files = self.files(ctl, **{"src/items/other.ts": ts("export class Other {}")})
        res = self.run_case(files, {"src/items/other.ts": ts("export class Other { x = 1; }")})
        self.assertEqual(self.reached(res), ["DELETE /api/items/{id}", "GET /api/items", "GET /api/items/{id}", "POST /api/items"])

    def test_a_name_the_constructor_body_uses_reaches_the_whole_class(self):
        ctl = CTL.replace("  ) {}", "  ) {\n    boot();\n  }").replace('import { Log } from "./log";', 'import { Log } from "./log";\nimport { boot } from "./boot";')
        files = self.files(ctl, **{"src/items/boot.ts": ts("export function boot() {}")})
        res = self.run_case(files, {"src/items/boot.ts": ts("export function boot() { return 1; }")})
        self.assertEqual(len(res.reached), 4)

    def test_a_name_used_by_a_decorator_of_the_class_reaches_the_whole_class(self):
        ctl = CTL.replace('@Controller("items")', '@UseGuards(Guard)\n@Controller("items")').replace('import { Log } from "./log";', 'import { Log } from "./log";\nimport { Guard } from "./guard";')
        files = self.files(ctl, **{"src/items/guard.ts": ts("export class Guard {}")})
        res = self.run_case(files, {"src/items/guard.ts": ts("export class Guard { x = 1; }")})
        self.assertEqual(len(res.reached), 4)

    def test_a_name_used_by_a_decorator_of_a_method_reaches_that_method_only(self):
        ctl = CTL.replace("  @Post()", "  @UseGuards(Guard)\n  @Post()").replace('import { Log } from "./log";', 'import { Log } from "./log";\nimport { Guard } from "./guard";')
        files = self.files(ctl, **{"src/items/guard.ts": ts("export class Guard {}")})
        res = self.run_case(files, {"src/items/guard.ts": ts("export class Guard { x = 1; }")})
        self.assertEqual(self.reached(res), ["POST /api/items"])

    def test_a_helper_function_of_the_file_that_a_method_uses_reaches_that_method(self):
        ctl = CTL.replace("    return 2;", "    return fmt(2);") + "function fmt(x: number) {\n  return x;\n}\n"
        res = self.run_case(self.files(ctl), {"src/items/items.controller.ts": ctl.replace("  return x;", "  return x + 1;")})
        self.assertEqual(self.reached(res), ["DELETE /api/items/{id}"])

    def test_a_property_with_an_initializer_is_reached_through_the_methods_that_use_it(self):
        ctl = ts(COMMON, 'import { Svc } from "./svc";', "", '@Controller("items")', "export class ItemsController {", "  private readonly svc = new Svc();", "  private other = 1;", "",
                 "  @Get()", "  list() {", "    return this.svc.all();", "  }", "", "  @Get(\":id\")", "  one() {", "    return this.other;", "  }", "}")
        res = self.run_case(rig(ctl, **{"src/items/svc.ts": SERVICE}), {"src/items/svc.ts": SERVICE.replace("return 1;", "return 2;")})
        self.assertEqual(self.reached(res), ["GET /api/items"])

    def test_a_service_that_the_module_provides_reaches_nothing_by_the_module(self):
        module = ITEMS_MODULE.replace('import { ItemsController }', 'import { Svc } from "./svc";\nimport { ItemsController }').replace("controllers: [ItemsController],", "controllers: [ItemsController],\n  providers: [Svc],")
        files = rig(CTL.replace("this.svc.all()", "1"), module=module, **{"src/items/svc.ts": SERVICE, "src/items/log.ts": LOG})
        res = self.run_case(files, {"src/items/svc.ts": SERVICE.replace("return 1;", "return 2;")})
        self.assertEqual(self.reached(res), [])

    def test_a_controller_that_is_not_served_is_said_and_claims_no_route(self):
        module = ITEMS_MODULE.replace("controllers: [ItemsController],", "controllers: [],")
        res = self.run_case(self.files(module=module), {"src/items/svc.ts": SERVICE.replace("return 1;", "return 2;")})
        self.assertEqual(self.reached(res), [])
        self.assertEqual(self.records_of("router_unmounted"), [("src/items/items.controller.ts:ItemsController", False)])

    def test_router_unmounted_is_not_claimed_while_a_mount_is_unresolved(self):
        module = ITEMS_MODULE.replace("controllers: [ItemsController],", "controllers: [...x],")
        res = self.run_case(self.files(module=module), {"src/items/svc.ts": SERVICE.replace("return 1;", "return 2;")})
        self.assertEqual(self.records_of("router_unmounted"), [])
        self.assertTrue(any("may be mounted" in p for p in self.nest.problems))
        self.assertFalse(res.complete)


class ReachByEdit(Base):
    def files(self, ctl=CTL, **more):
        return rig(ctl, **{"src/items/svc.ts": SERVICE, "src/items/log.ts": LOG, **more})

    def edit(self, old, new, file="src/items/items.controller.ts", files=None):
        base = files or self.files()
        return self.run_case(base, {file: base[file].replace(old, new, 1)})

    def test_a_line_in_a_method_reaches_its_route_only(self):
        res = self.edit("    return 2;", "    return 3;")
        self.assertEqual(self.reached(res), ["DELETE /api/items/{id}"])
        self.assertEqual(self.chain(res, "DELETE /api/items/{id}")[0], ("src/items/items.controller.ts", 35))

    def test_a_line_in_a_decorator_of_a_method_reaches_that_method(self):
        res = self.edit('  @Delete(":id")', '  @Delete(":uid")')
        self.assertEqual(self.reached(res), ["DELETE /api/items/{uid}"])

    def test_a_line_in_the_class_decorator_reaches_every_route_of_the_controller(self):
        res = self.edit('@Controller("items")', '@Controller("things")')
        self.assertEqual(self.reached(res), ["DELETE /api/things/{id}", "GET /api/things", "GET /api/things/{id}", "POST /api/things"])

    def test_a_line_in_the_constructor_reaches_the_methods_that_use_the_parameter_property(self):
        res = self.edit("    private readonly log: Log,", "    private readonly log: Log, // changed")
        self.assertEqual(self.reached(res), ["POST /api/items"])

    def test_a_line_in_the_constructor_that_is_not_a_property_reaches_the_whole_class(self):
        res = self.edit("    plain: Other,", "    plain: Other, // changed")
        self.assertEqual(len(res.reached), 4)

    def test_a_line_in_a_member_that_is_not_a_route_reaches_the_routes_that_call_it(self):
        res = self.edit("  helper() {", "  helper(x = 1) {")
        self.assertEqual(self.reached(res), ["GET /api/items/{id}"])

    def test_a_comment_between_members_changes_nothing_and_a_line_of_code_there_reaches_the_class(self):
        res = self.edit("  @Get()\n  list() {", "  // a note\n  @Get()\n  list() {")
        self.assertEqual(self.reached(res), [])
        res = self.edit("  @Get()\n  list() {", "  ;\n  @Get()\n  list() {")
        self.assertEqual(len(res.reached), 4)

    def test_a_removed_method_is_a_removed_route_and_reaches_nothing(self):
        base = self.files()
        res = self.run_case(base, {"src/items/items.controller.ts": base["src/items/items.controller.ts"].replace('  @Delete(":id")\n  remove() {\n    return 2;\n  }\n', "")})
        self.assertEqual(self.reached(res), [])

    def test_a_removed_line_in_a_method_that_is_still_there_reaches_it(self):
        base = self.files()
        res = self.run_case(base, {"src/items/items.controller.ts": base["src/items/items.controller.ts"].replace("    this.log.write();\n", "")})
        self.assertEqual(self.reached(res), ["POST /api/items"])

    def test_a_removed_line_of_the_class_decorator_reaches_the_whole_class(self):
        files = self.files(CTL.replace('@Controller("items")', '@UseGuards(G)\n@Controller("items")'))
        res = self.run_case(files, {"src/items/items.controller.ts": files["src/items/items.controller.ts"].replace("@UseGuards(G)\n", "")})
        self.assertEqual(len(res.reached), 4)

    def test_the_module_that_lists_the_controller_reaches_its_routes(self):
        res = self.edit("controllers: [ItemsController],", "controllers: [ItemsController],\n  providers: [],", "src/items/items.module.ts")
        self.assertEqual(len(res.reached), 4)

    def test_a_change_to_a_module_reaches_the_controllers_of_the_modules_it_imports(self):
        files = rig(CTL, app=APP_MODULE.replace("imports: [ItemsModule],", "imports: [ItemsModule],\n  providers: [],").replace("  providers: [],\n", "  providers: [],\n"), **{"src/items/svc.ts": SERVICE, "src/items/log.ts": LOG})
        res = self.run_case(files, {"src/app.module.ts": files["src/app.module.ts"].replace("providers: [],", "providers: [X],")})
        self.assertEqual(len(res.reached), 4)

    def test_a_removed_controller_from_a_module_reaches_nothing(self):
        files = self.files()
        res = self.run_case(files, {"src/items/items.module.ts": files["src/items/items.module.ts"].replace("controllers: [ItemsController],", "controllers: [],")})
        self.assertEqual(self.reached(res), [])

    def test_the_file_that_creates_the_application_reaches_every_route_it_serves(self):
        res = self.edit("  await app.listen(3000);", "  app.enableCors();\n  await app.listen(3000);", "src/main.ts")
        self.assertEqual(len(res.reached), 4)

    def test_the_prefix_line_is_in_every_route_and_reaches_all(self):
        res = self.edit('app.setGlobalPrefix("api");', 'app.setGlobalPrefix("v9");', "src/main.ts")
        self.assertEqual(len(res.reached), 4)

    def test_routes_of_controllers_that_are_not_served_are_not_reached_by_the_application(self):
        module = ITEMS_MODULE.replace("controllers: [ItemsController],", "controllers: [],")
        files = self.files(module=module)
        res = self.run_case(files, {"src/main.ts": files["src/main.ts"].replace("  await app.listen(3000);", "  app.enableCors();\n  await app.listen(3000);")})
        self.assertEqual(self.reached(res), [])
        self.assertEqual(self.records_of("router_unmounted"), [])

    def test_an_edit_in_a_controller_that_is_not_served_is_said(self):
        module = ITEMS_MODULE.replace("controllers: [ItemsController],", "controllers: [],")
        files = self.files(module=module)
        res = self.run_case(files, {"src/items/items.controller.ts": files["src/items/items.controller.ts"].replace("    return 2;", "    return 3;")})
        self.assertEqual(self.reached(res), [])
        self.assertEqual(self.records_of("router_unmounted"), [("src/items/items.controller.ts:ItemsController", False)])

    def test_an_edit_in_a_file_that_does_not_lex_is_incomplete_and_blocking(self):
        files = self.files()
        res = self.run_case(files, {"src/items/items.controller.ts": files["src/items/items.controller.ts"] + "const s = `open\n"})
        self.assertFalse(res.complete)
        self.assertIn(("src/items/items.controller.ts", True), self.records_of("parse_degraded"))

    def test_a_partial_route_is_said_when_a_change_reaches_it(self):
        ctl = CTL.replace('@Controller("items")', '@Controller({ path: "items", version: "1" })')
        files = self.files(ctl)
        files["src/main.ts"] = Versions.VERSIONING
        res = self.run_case(files, {"src/items/items.controller.ts": files["src/items/items.controller.ts"].replace("    return 2;", "    return 3;")})
        self.assertEqual(self.reached(res), ["DELETE /api/<unknown:version>/items/{id}"])
        self.assertEqual(self.records_of("route_path_partial"), [("src/items/items.controller.ts:5", False)])

    def test_a_module_that_nobody_lists_reaches_nothing(self):
        files = self.files(**{"src/items/lonely.module.ts": ts(COMMON, "@Module({})", "export class Lonely {}")})
        res = self.run_case(files, {"src/items/lonely.module.ts": ts(COMMON, "@Module({ providers: [] })", "export class Lonely {}")})
        self.assertEqual(self.reached(res), [])


class RoutesChanged(Base):
    def test_removed_and_added_routes(self):
        before = rig(controller('  @Get("a") a() {}', '  @Get("b") b() {}'))
        after = dict(before)
        after["src/items/items.controller.ts"] = controller('  @Get("a") a() {}', '  @Get("c") c() {}')
        self.repo({"files": before}, {"files": after})
        nest = self.entry()
        removed, added = nest.routes_changed(self.sha("main~1"), self.sha("main"))
        self.assertEqual(([r.path for r in removed], [r.path for r in added]), (["/api/items/b"], ["/api/items/c"]))

    def test_a_route_that_loses_its_module_is_a_removed_route(self):
        before = rig(controller("  @Get() a() {}"))
        after = dict(before)
        after["src/items/items.module.ts"] = ITEMS_MODULE.replace("controllers: [ItemsController],", "controllers: [],")
        self.repo({"files": before}, {"files": after})
        removed, added = self.entry().routes_changed(self.sha("main~1"), self.sha("main"))
        self.assertEqual(([r.path for r in removed], added), (["/api/items"], []))

    def test_without_a_pre_change_version_nothing_is_computed(self):
        self.repo({"files": rig(controller("  @Get() a() {}"))})
        self.assertEqual(self.entry().routes_changed(None, self.sha("main")), ([], []))


class Registry(Base):
    def test_the_adapter_is_built_from_the_config_by_kind(self):
        from reachmap import config

        ws = config.loads(
            'workspace = "w"\n[repos."t/ledger"]\nhost = "git"\nremote = "file:///x"\nlanguage = "typescript"\nroles = ["backend"]\nentry_points = { kind = "nestjs" }\n',
            source="inline", fmt="toml", env={},
        )
        self.repo({"files": rig(controller("  @Get() a() {}"))})
        adapter = entrypoints.entry_adapter(ws.repos["t/ledger"], self.make_adapter())
        self.assertIsInstance(adapter, NestAdapter)
        self.assertEqual((adapter.kind, adapter.service_marker), ("nestjs", "package.json"))

    def test_is_entry_file_and_structural_names(self):
        self.repo({"files": rig(controller("  @Get() a() {}"))})
        nest = self.entry()
        sha = self.sha("main")
        for path in ("src/items/items.controller.ts", "src/items/items.module.ts", "src/app.module.ts", "src/main.ts"):
            self.assertTrue(nest.is_entry_file(path, sha), path)
        self.assertEqual(nest.structural_names("src/items/items.controller.ts", sha), frozenset({"ItemsController"}))
        self.assertEqual(nest.structural_names("src/items/items.module.ts", sha), frozenset({"ItemsModule"}))
        self.assertEqual(nest.structural_names("src/main.ts", sha), frozenset())

    def test_the_routes_of_a_repository_of_another_name_are_refused(self):
        self.repo({"files": rig(controller("  @Get() a() {}"))})
        with self.assertRaises(ValueError):
            self.entry().routes("other/repo", self.sha("main"))


class Robust(Base):
    """Code that is cut in the middle or is token soup is never a crash."""

    def test_a_controller_file_cut_at_any_point_does_not_crash_the_adapter(self):
        text = CTL
        for cut in range(0, len(text), 19):
            with self.subTest(cut=cut):
                self.repo({"files": rig(text[:cut], **{"src/items/svc.ts": SERVICE, "src/items/log.ts": LOG})})
                nest = self.entry()
                nest.routes(None, self.sha("main"))
                nest.unmounted(self.sha("main"))

    def test_a_module_and_main_cut_at_any_point_do_not_crash_the_adapter(self):
        for name, text in (("src/items/items.module.ts", ITEMS_MODULE), ("src/main.ts", MAIN), ("src/app.module.ts", APP_MODULE)):
            for cut in range(0, len(text), 23):
                with self.subTest(file=name, cut=cut):
                    files = rig(CTL, **{"src/items/svc.ts": SERVICE, "src/items/log.ts": LOG})
                    files[name] = text[:cut]
                    self.repo({"files": files})
                    self.entry().routes(None, self.sha("main"))

    def test_token_soup_in_a_controller_does_not_crash_the_adapter(self):
        import random

        words = ["@Controller", "@Get", "(", ")", "{", "}", "class", "export", "constructor", "private", "readonly", ":", ",", ";", "this", ".", "x", '"a"', "=>", "[", "]", "@Module", "controllers", "imports", "async"]
        rng = random.Random(11)
        for n in range(25):
            text = COMMON + "\n" + " ".join(rng.choice(words) for _ in range(rng.randint(5, 60)))
            with self.subTest(n=n):
                self.repo({"files": rig(text)})
                self.entry().routes(None, self.sha("main"))

    def test_a_changed_controller_cut_at_any_point_does_not_crash_the_reach(self):
        base = rig(CTL, **{"src/items/svc.ts": SERVICE, "src/items/log.ts": LOG})
        for cut in range(10, len(CTL), 29):
            with self.subTest(cut=cut):
                self.repo({"files": base}, {"files": {"src/items/items.controller.ts": CTL[:cut]}})
                self.entry()
                self.reach()


if __name__ == "__main__":
    unittest.main()
