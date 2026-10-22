"""The NestJS adapter after the independent review: the route-change guard, dynamic modules that list controllers, global guards and the providers and class
body of a module, a global prefix per application (a monorepo), decorators re-exported from `@nestjs/common`, and versioning that is only a segment when it is enabled.
"""

import unittest

from tests.test_nest import APP_MODULE, COMMON, CTL, ITEMS_MODULE, LOG, MAIN, SERVICE, Base, controller, rig, ts

SIMPLE = controller("  @Get() a() {}", "  @Get(\":id\") b() {}")


class Guard(Base):
    def changes(self, before, after):
        self.repo({"files": before}, {"files": after})
        nest = self.entry()
        return nest.routes_changed(self.sha("main~1"), self.sha("main"))

    def withheld(self):
        return [(r.subject, r.blocks_selection) for r in self.records if r.kind == "route_change_withheld"]

    def test_the_adapter_guards_its_own_changes(self):
        self.repo({"files": rig(SIMPLE)})
        nest = self.entry()
        self.assertTrue(nest.withholds_unread)
        self.assertEqual(nest.routes_complete(self.sha("main")), (True, []))

    def test_a_controller_that_does_not_lex_at_the_head_removes_nothing(self):
        before = rig(SIMPLE)
        removed, added = self.changes(before, {"src/items/items.controller.ts": SIMPLE + "const s = `open\n"})
        self.assertEqual((removed, added), ([], []))
        self.assertEqual(self.withheld(), [(self.REPO, True)])

    def test_a_module_that_does_not_lex_at_the_head_removes_nothing(self):
        removed, added = self.changes(rig(SIMPLE), {"src/items/items.module.ts": ITEMS_MODULE + "const s = `open\n"})
        self.assertEqual((removed, added), ([], []))
        self.assertEqual(self.withheld(), [(self.REPO, True)])

    def test_the_main_file_that_does_not_lex_at_the_head_removes_nothing(self):
        removed, added = self.changes(rig(SIMPLE), {"src/main.ts": MAIN + "const s = `open\n"})
        self.assertEqual((removed, added), ([], []))
        self.assertEqual(self.withheld(), [(self.REPO, True)])

    def test_an_unrelated_unreadable_file_does_not_withhold_a_real_removal(self):
        before = rig(SIMPLE, **{"src/other/x.ts": ts(COMMON, "export class X {}")})
        after = {"src/items/items.controller.ts": controller('  @Get() a() {}'), "src/other/x.ts": ts(COMMON, "export class X {}", "const s = `open")}
        removed, added = self.changes(before, after)
        self.assertEqual(([r.path for r in removed], added), (["/api/items/{id}"], []))
        self.assertEqual(self.withheld(), [])

    def test_a_real_removal_and_a_real_addition_between_readable_tables_stand(self):
        before = rig(controller('  @Get("a") a() {}', '  @Get("b") b() {}'))
        after = {"src/items/items.controller.ts": controller('  @Get("a") a() {}', '  @Get("c") c() {}')}
        removed, added = self.changes(before, after)
        self.assertEqual(([r.path for r in removed], [r.path for r in added]), (["/api/items/b"], ["/api/items/c"]))
        self.assertEqual(self.withheld(), [])

    def test_a_base_that_could_not_be_read_whole_claims_no_addition(self):
        before = rig(controller('  @Get("a") a() {}') + "const s = `open\n")
        after = {"src/items/items.controller.ts": controller('  @Get("a") a() {}', '  @Get("c") c() {}')}
        removed, added = self.changes(before, after)
        self.assertEqual((removed, added), ([], []))
        self.assertEqual(self.withheld(), [(self.REPO, True)])


class DynamicModules(Base):
    DYN = ts(COMMON, 'import { ItemsController } from "./items.controller";', "", "@Module({})", "export class DynModule {", "  static forRoot() {", "    return {", "      module: DynModule,",
             "      controllers: [ItemsController],", "    };", "  }", "}")

    def test_a_dynamic_module_that_lists_controllers_in_for_root_serves_them(self):
        app = ts(COMMON, 'import { DynModule } from "./items/dyn.module";', "", "@Module({ imports: [DynModule.forRoot()] })", "export class AppModule {}")
        files = rig(controller("  @Get() a() {}"), app=app, module=ts(COMMON, "@Module({})", "export class ItemsModule {}"), **{"src/items/dyn.module.ts": self.DYN})
        self.assertEqual(self.labels(files), ["GET /api/items"])
        self.assertEqual(self.nest.unmounted(self.sha("main")), [])

    def test_the_evidence_of_such_a_route_is_the_line_that_lists_the_controller(self):
        app = ts(COMMON, 'import { DynModule } from "./items/dyn.module";', "", "@Module({ imports: [DynModule.forRoot()] })", "export class AppModule {}")
        files = rig(controller("  @Get() a() {}"), app=app, module=ts(COMMON, "@Module({})", "export class ItemsModule {}"), **{"src/items/dyn.module.ts": self.DYN})
        r = self.table(files)[0]
        self.assertEqual([(e.path, e.line) for e in r.evidence][2], ("src/items/dyn.module.ts", 9))

    def test_a_dynamic_module_imported_through_a_static_method_with_imports_reaches_its_controllers(self):
        dyn = ts(COMMON, 'import { ItemsModule } from "./items.module";', "", "@Module({})", "export class Wrap {", "  static register() {", "    return { module: Wrap, imports: [ItemsModule] };", "  }", "}")
        app = ts(COMMON, 'import { Wrap } from "./items/wrap";', "", "@Module({ imports: [Wrap.register()] })", "export class AppModule {}")
        files = rig(controller("  @Get() a() {}"), app=app, **{"src/items/wrap.ts": dyn})
        self.assertEqual(self.labels(files), ["GET /api/items"])

    def test_controllers_listed_by_a_function_outside_any_module_are_unresolved_and_block(self):
        helper = ts(COMMON, 'import { ItemsController } from "./items.controller";', "", "export function make() {", "  return { module: Dyn, controllers: [ItemsController] };", "}")
        files = rig(controller("  @Get() a() {}"), module=ts(COMMON, "@Module({})", "export class ItemsModule {}"), **{"src/items/helper.ts": helper})
        self.assertEqual(self.labels(files), [])
        self.assertEqual(self.records_of("mount_unresolved"), [("src/items/helper.ts:5", True)])
        self.assertEqual(self.nest.table(self.sha("main")).unresolved, True)

    def test_unmounted_is_not_claimed_while_such_a_list_is_unresolved(self):
        helper = ts(COMMON, "export function make() {", "  return { module: Dyn, controllers: [Other] };", "}")
        files = rig(CTL.replace("this.svc.all()", "1"), module=ts(COMMON, "@Module({})", "export class ItemsModule {}"), **{"src/items/helper.ts": helper, "src/items/svc.ts": SERVICE, "src/items/log.ts": LOG})
        res = self.run_case(files, {"src/items/items.controller.ts": files["src/items/items.controller.ts"].replace("return 2;", "return 3;")})
        self.assertEqual(self.records_of("router_unmounted"), [])
        self.assertFalse(res.complete)


GUARD = ts(COMMON, "export class Guard {", "  canActivate() {", "    return true;", "  }", "}")
GLOBAL_MODULE = ts(COMMON, 'import { APP_GUARD } from "@nestjs/core";', 'import { ItemsController } from "./items.controller";', 'import { Guard } from "./guard";', "", "@Module({", "  controllers: [ItemsController],",
                   "  providers: [{ provide: APP_GUARD, useClass: Guard }],", "})", "export class ItemsModule {}")


class Providers(Base):
    def files(self, module=GLOBAL_MODULE, **more):
        other = ts(COMMON, 'import { OtherController } from "./other.controller";', "", "@Module({ controllers: [OtherController] })", "export class OtherModule {}")
        other_ctl = ts(COMMON, "", '@Controller("other")', "export class OtherController {", "  @Get() o() {}", "}")
        app = ts(COMMON, 'import { ItemsModule } from "./items/items.module";', 'import { OtherModule } from "./other/other.module";', "", "@Module({", "  imports: [ItemsModule, OtherModule],", "})", "export class AppModule {}")
        return rig(SIMPLE, module=module, app=app, **{"src/items/guard.ts": GUARD, "src/other/other.module.ts": other, "src/other/other.controller.ts": other_ctl, **more})

    def go(self, module=GLOBAL_MODULE, file="src/items/guard.ts", old="return true;", new="return false;", **more):
        files = self.files(module, **more)
        return self.run_case(files, {file: files[file].replace(old, new)})

    def test_a_global_guard_reaches_every_served_route_of_every_module(self):
        res = self.go()
        self.assertEqual(self.reached(res), ["GET /api/items", "GET /api/items/{id}", "GET /api/other"])

    def test_the_other_global_providers(self):
        for token in ("APP_INTERCEPTOR", "APP_PIPE", "APP_FILTER"):
            with self.subTest(token=token):
                res = self.go(GLOBAL_MODULE.replace("APP_GUARD", token))
                self.assertEqual(len(res.reached), 3)

    def test_a_custom_provider_reaches_the_routes_of_its_module_and_not_of_others(self):
        module = GLOBAL_MODULE.replace("{ provide: APP_GUARD, useClass: Guard }", '{ provide: "LIMIT", useClass: Guard }')
        res = self.go(module)
        self.assertEqual(self.reached(res), ["GET /api/items", "GET /api/items/{id}"])

    def test_a_factory_provider_is_a_custom_provider(self):
        module = GLOBAL_MODULE.replace("{ provide: APP_GUARD, useClass: Guard }", '{ provide: "LIMIT", useFactory: () => Guard }')
        self.assertEqual(self.reached(self.go(module)), ["GET /api/items", "GET /api/items/{id}"])

    def test_a_plain_class_provider_reaches_nothing_by_the_module(self):
        module = GLOBAL_MODULE.replace("{ provide: APP_GUARD, useClass: Guard }", "Guard")
        self.assertEqual(self.reached(self.go(module)), [])

    def test_middleware_configured_in_the_class_body_reaches_the_routes_of_the_module(self):
        module = ts(COMMON, 'import { ItemsController } from "./items.controller";', 'import { Guard } from "./guard";', "", "@Module({ controllers: [ItemsController] })",
                    "export class ItemsModule {", "  configure(consumer) {", '    consumer.apply(Guard).forRoutes("*");', "  }", "}")
        self.assertEqual(self.reached(self.go(module)), ["GET /api/items", "GET /api/items/{id}"])

    def test_the_routes_of_an_imported_module_are_reached_by_a_provider_of_its_importer(self):
        app = ts(COMMON, 'import { ItemsModule } from "./items/items.module";', 'import { Guard } from "./items/guard";', "", "@Module({", "  imports: [ItemsModule],", '  providers: [{ provide: "X", useClass: Guard }],', "})", "export class AppModule {}")
        files = rig(SIMPLE, app=app, **{"src/items/guard.ts": GUARD})
        res = self.run_case(files, {"src/items/guard.ts": GUARD.replace("return true;", "return false;")})
        self.assertEqual(self.reached(res), ["GET /api/items", "GET /api/items/{id}"])

    def test_a_global_provider_of_a_module_nobody_serves_reaches_nothing(self):
        module = GLOBAL_MODULE.replace("controllers: [ItemsController],", "controllers: [],")
        app = APP_MODULE.replace("imports: [ItemsModule],", "imports: [],")
        files = rig(SIMPLE, module=module, app=app, **{"src/items/guard.ts": GUARD})
        res = self.run_case(files, {"src/items/guard.ts": GUARD.replace("return true;", "return false;")})
        self.assertEqual(self.reached(res), [])


MONO_A_MODULE = ts(COMMON, 'import { AController } from "./a.controller";', "", "@Module({ controllers: [AController] })", "export class AModule {}")
MONO_B_MODULE = ts(COMMON, 'import { BController } from "./b.controller";', "", "@Module({ controllers: [BController] })", "export class BModule {}")


def mono(a_main, b_main, **more):
    ctl = lambda name, path: ts(COMMON, "", f'@Controller("{path}")', f"export class {name} {{", "  @Get() x() {}", "}")  # noqa: E731
    return {"tsconfig.json": "{}\n", "apps/a/src/main.ts": a_main, "apps/a/src/a.module.ts": MONO_A_MODULE, "apps/a/src/a.controller.ts": ctl("AController", "things"),
            "apps/b/src/main.ts": b_main, "apps/b/src/b.module.ts": MONO_B_MODULE, "apps/b/src/b.controller.ts": ctl("BController", "things"), **more}


def main_of(module, prefix, var="app"):
    return ts('import { NestFactory } from "@nestjs/core";', f'import {{ {module} }} from "./{module[0].lower()}.module";', "", "async function bootstrap() {", f"  const {var} = await NestFactory.create({module});",
              f'  {var}.setGlobalPrefix("{prefix}");', f"  await {var}.listen(3000);", "}", "bootstrap();")


class Monorepo(Base):
    def test_each_application_prefix_applies_to_the_controllers_of_its_own_module(self):
        files = mono(main_of("AModule", "a"), main_of("BModule", "b"))
        self.assertEqual(self.labels(files), ["GET /a/things", "GET /b/things"])
        self.assertEqual([r.partial for r in self.table(files)], [False, False])

    def test_the_evidence_names_the_prefix_line_of_its_own_application(self):
        files = mono(main_of("AModule", "a"), main_of("BModule", "b"))
        by = {r.path: r for r in self.table(files)}
        self.assertEqual(by["/a/things"].evidence[-1].path, "apps/a/src/main.ts")
        self.assertEqual(by["/b/things"].evidence[-1].path, "apps/b/src/main.ts")

    def test_two_applications_in_one_function_are_paired_by_the_variable(self):
        main = ts('import { NestFactory } from "@nestjs/core";', 'import { AModule } from "./a.module";', 'import { BModule } from "../../b/src/b.module";', "", "async function bootstrap() {",
                  "  const first = await NestFactory.create(AModule);", "  const second = await NestFactory.create(BModule);", '  first.setGlobalPrefix("one");', '  second.setGlobalPrefix("two");', "}")
        files = mono(main, "export const x = 1;\n")
        self.assertEqual(self.labels(files), ["GET /one/things", "GET /two/things"])

    def test_an_application_without_a_prefix_has_none_whatever_another_has(self):
        files = mono(main_of("AModule", "a"), ts('import { NestFactory } from "@nestjs/core";', 'import { BModule } from "./b.module";', "NestFactory.create(BModule);"))
        self.assertEqual(self.labels(files), ["GET /a/things", "GET /things"])

    def test_a_prefix_that_cannot_be_paired_with_an_application_is_partial_when_there_are_several(self):
        a = ts('import { NestFactory } from "@nestjs/core";', 'import { AModule } from "./a.module";', 'import { configure } from "../../shared";', "const app = NestFactory.create(AModule);", "configure(app);")
        b = ts('import { NestFactory } from "@nestjs/core";', 'import { BModule } from "./b.module";', "NestFactory.create(BModule);")
        shared = ts("export function configure(app) {", '  app.setGlobalPrefix("api");', "}")
        files = mono(a, b, **{"shared.ts": shared})
        routes = self.table(files)
        self.assertEqual(sorted((r.path, r.partial) for r in routes), [("/api/things", True), ("/api/things", True)])

    def test_a_prefix_that_cannot_be_paired_is_not_partial_with_one_application(self):
        files = rig(controller("  @Get() a() {}"), main=MAIN.replace('  app.setGlobalPrefix("api");\n', "  configure(app);\n"), **{"src/setup.ts": ts("export function configure(app) {", '  app.setGlobalPrefix("v1");', "}")})
        (r,) = self.table(files)
        self.assertEqual((r.path, r.partial), ("/v1/items", False))

    def test_an_application_context_is_not_an_application_and_sets_no_prefix(self):
        worker = ts('import { NestFactory } from "@nestjs/core";', 'import { AppModule } from "./app.module";', "async function run() {", "  const ctx = await NestFactory.createApplicationContext(AppModule);", '  ctx.setGlobalPrefix("worker");', "}")
        files = rig(controller("  @Get() a() {}"), **{"src/worker.ts": worker})
        self.assertEqual(self.labels(files), ["GET /api/items"])


class Reexports(Base):
    def test_decorators_re_exported_from_nestjs_common_are_the_decorators(self):
        lib = ts('export { Controller, Get, Module } from "@nestjs/common";')
        text = ts('import { Controller, Get } from "../lib/nest";', "", '@Controller("items")', "export class ItemsController {", "  @Get() a() {}", "}")
        files = rig(text, **{"src/lib/nest.ts": lib})
        self.assertEqual(self.labels(files), ["GET /api/items"])

    def test_export_star_of_nestjs_common(self):
        lib = ts('export * from "@nestjs/common";')
        text = ts('import { Controller, Get } from "../lib/nest";', "", '@Controller("items")', "export class ItemsController {", "  @Get() a() {}", "}")
        self.assertEqual(self.labels(rig(text, **{"src/lib/nest.ts": lib})), ["GET /api/items"])

    def test_a_re_export_of_something_that_is_not_nestjs_is_not_a_decorator(self):
        lib = ts('export { Controller, Get } from "./decorators";', "export {};")
        text = ts('import { Controller, Get } from "../lib/nest";', "", '@Controller("items")', "export class ItemsController {", "  @Get() a() {}", "}")
        self.assertEqual(self.labels(rig(text, **{"src/lib/nest.ts": lib, "src/lib/decorators.ts": ts("export function Controller() {}", "export function Get() {}")})), [])


class Versioning(Base):
    VERSIONED = controller("  @Get() a() {}", path='{ path: "items", version: "1" }')

    def main(self, line):
        return MAIN.replace('  app.setGlobalPrefix("api");\n', f'  app.setGlobalPrefix("api");\n  {line}\n') if line else MAIN

    def test_a_version_is_no_segment_when_versioning_is_not_enabled(self):
        (r,) = self.table(rig(self.VERSIONED, main=self.main("")))
        self.assertEqual((r.path, r.partial), ("/api/items", False))

    def test_uri_versioning_adds_an_unknown_segment_and_is_partial(self):
        for line in ("app.enableVersioning({ type: VersioningType.URI });", "app.enableVersioning();", "app.enableVersioning({ defaultVersion: '1' });"):
            with self.subTest(line=line):
                (r,) = self.table(rig(self.VERSIONED, main=self.main(line)))
                self.assertEqual((r.path, r.partial), ("/api/<unknown:version>/items", True))

    def test_header_and_media_type_versioning_add_no_segment(self):
        for kind in ("HEADER", "MEDIA_TYPE", "CUSTOM"):
            with self.subTest(kind=kind):
                (r,) = self.table(rig(self.VERSIONED, main=self.main("app.enableVersioning({ type: VersioningType.%s, header: 'v' });" % kind)))
                self.assertEqual((r.path, r.partial), ("/api/items", False))

    def test_a_method_version_is_the_same(self):
        text = controller('  @Get() @Version("2") a() {}')
        (r,) = self.table(rig(text, main=self.main("app.enableVersioning();")))
        self.assertEqual((r.path, r.partial), ("/api/<unknown:version>/items", True))
        (r,) = self.table(rig(text, main=self.main("")))
        self.assertEqual((r.path, r.partial), ("/api/items", False))


if __name__ == "__main__":
    unittest.main()
