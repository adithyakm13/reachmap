"""The Spring route table on small repositories built for one rule each: every mapping form, paths made of constants, templates and
placeholders, the context path from the configuration, several paths and methods, Kotlin controllers, mappings inherited from an interface or
a base class, paths that cannot be evaluated, a controller that cannot be read, and removed and added routes.

What a change reaches is in test_spring_reach.py; the M16 scenarios in test_scenarios_sb.py.
"""

import unittest

from reachmap.adapters.jvm import JvmAdapter
from reachmap.adapters.spring import SpringAdapter
from tests.support_universe import Base as UniverseBase
from tests.support_universe import f

IMPORTS = ["import org.springframework.web.bind.annotation.*;"]


def controller(*body, pkg="com.acme.api", name="InvoiceController", head=('@RestController', '@RequestMapping("/invoices")'), imports=IMPORTS):
    return f([f"package {pkg};", "", *imports, "", *head, f"public class {name} {{", *body, "}"])


GET = ('    @GetMapping("/{id}")', "    public String get(@PathVariable String id) {", '        return "x";', "    }")
LIST = ("    @GetMapping", "    public String list() {", '        return "x";', "    }")
PATH = "src/main/java/com/acme/api/InvoiceController.java"


class Base(UniverseBase):
    UNIVERSE_PREFIX = "reachmap-spring-"
    REPO = "acme/ledger"

    def make_adapter(self, **kw):
        kw.setdefault("test_markers", ["src/test/"])
        return JvmAdapter(self.REPO, self.url, self.git, **kw)

    def spring(self, **kw):
        self.language = self.make_adapter()
        self.entry = SpringAdapter(self.REPO, self.language, **kw)
        return self.entry

    def routes(self, sha="main", **kw):
        return self.spring(**kw).routes(None, self.sha(sha))

    def labels(self, sha="main", **kw):
        return sorted((r.label, r.handler) for r in self.routes(sha, **kw))

    def one(self, label, sha="main"):
        (r,) = [r for r in self.entry.routes(None, self.sha(sha)) if r.label == label]
        return r


class Mappings(Base):
    def test_class_and_method_paths_are_joined_and_the_evidence_is_the_annotations_in_order(self):
        self.repo({"files": {PATH: controller(*LIST, "", *GET)}})
        self.assertEqual(self.labels(), [("GET /invoices", "InvoiceController.list"), ("GET /invoices/{id}", "InvoiceController.get")])
        r = self.one("GET /invoices/{id}")
        self.assertEqual((r.kind, r.method, r.path, r.file, r.target, r.handler, r.partial), ("api_route", "GET", "/invoices/{id}", PATH, PATH, "InvoiceController.get", False))
        self.assertEqual([(e.path, e.line, e.detail.strip()) for e in r.evidence], [(PATH, 13, '@GetMapping("/{id}")'), (PATH, 6, '@RequestMapping("/invoices")')])
        self.assertEqual(r.evidence[0].claim, f"route:GET /invoices/{{id}}:{PATH}")
        self.assertEqual(r.line, r.evidence[0])

    def test_every_mapping_annotation(self):
        body = [
            '    @PostMapping("/a")', "    void a() {}", '    @PutMapping("/b")', "    void b() {}", '    @DeleteMapping("/c")', "    void c() {}",
            '    @PatchMapping("/d")', "    void d() {}",
            '    @RequestMapping(value = "/e", method = RequestMethod.POST)', "    void e() {}",
            '    @RequestMapping(path = "/g", method = {RequestMethod.GET, RequestMethod.HEAD})', "    void g() {}",
            '    @RequestMapping("/h")', "    void h() {}",
            '    @GetMapping(value = {"/i", "/j"})', "    void i() {}",
            "    @GetMapping", "    void k() {}",
            '    @RequestMapping(method = RequestMethod.DELETE)', "    void l() {}",
        ]
        self.repo({"files": {PATH: controller(*body, head=("@RestController", '@RequestMapping("/v")'))}})
        self.assertEqual(self.labels(), sorted([
            ("POST /v/a", "InvoiceController.a"), ("PUT /v/b", "InvoiceController.b"), ("DELETE /v/c", "InvoiceController.c"), ("PATCH /v/d", "InvoiceController.d"),
            ("POST /v/e", "InvoiceController.e"), ("GET /v/g", "InvoiceController.g"), ("HEAD /v/g", "InvoiceController.g"), ("ANY /v/h", "InvoiceController.h"),
            ("GET /v/i", "InvoiceController.i"), ("GET /v/j", "InvoiceController.i"), ("GET /v", "InvoiceController.k"), ("DELETE /v", "InvoiceController.l"),
        ]))

    def test_a_multi_line_annotation_is_cited_at_its_first_line(self):
        body = ["    @GetMapping(", '        value = "/m",', '        produces = "text/plain")', "    String m() { return \"\"; }"]
        self.repo({"files": {PATH: controller(*body)}})
        self.assertEqual(self.labels(), [("GET /invoices/m", "InvoiceController.m")])
        self.assertEqual(self.one("GET /invoices/m").evidence[0].line, 8)

    def test_the_class_level_method_applies_to_a_handler_with_none(self):
        body = ['    @RequestMapping("/x")', "    void x() {}"]
        self.repo({"files": {PATH: controller(*body, head=("@RestController", '@RequestMapping(path = "/c", method = RequestMethod.POST)'))}})
        self.assertEqual(self.labels(), [("POST /c/x", "InvoiceController.x")])

    def test_path_variables_with_a_regex_a_rest_a_wildcard_and_a_mixed_segment(self):
        body = [
            '    @GetMapping("/{id:\\\\d+}")', "    void a() {}", '    @GetMapping("/files/{*rest}")', "    void b() {}", '    @GetMapping("/any/**")', "    void c() {}",
            '    @GetMapping("/one/*/x")', "    void d() {}", '    @GetMapping("/t/{id}.json")', "    void e() {}", '    @GetMapping("/p/{p:.+}")', "    void g() {}",
        ]
        self.repo({"files": {PATH: controller(*body, head=("@RestController",))}})
        got = {h: l for l, h in self.labels()}
        self.assertEqual(got["InvoiceController.a"], "GET /{id}")
        self.assertEqual(got["InvoiceController.b"], "GET /files/{rest:path}")
        self.assertEqual(got["InvoiceController.c"], "GET /any/{path:path}")
        self.assertEqual(got["InvoiceController.d"], "GET /one/{wildcard}/x")
        self.assertEqual(got["InvoiceController.e"], "GET /t/<unknown:{id}.json>")
        self.assertEqual(got["InvoiceController.g"], "GET /p/{p:path}")
        self.assertTrue(self.one("GET /t/<unknown:{id}.json>").partial)
        self.assertFalse(self.one("GET /{id}").partial)

    def test_a_trailing_slash_is_the_same_route_and_the_root_is_a_slash(self):
        body = ['    @GetMapping("/z/")', "    void z() {}", "    @GetMapping", "    void root() {}"]
        self.repo({"files": {PATH: controller(*body, head=("@RestController",))}})
        self.assertEqual(self.labels(), [("GET /", "InvoiceController.root"), ("GET /z", "InvoiceController.z")])

    def test_a_controller_without_a_stereotype_or_a_class_mapping_is_no_controller_and_a_look_alike_is_ignored(self):
        plain = f(["package com.acme.api;", "", *IMPORTS, "", "class Helper {", '    @GetMapping("/h")', "    void h() {}", "}"])
        look = f(["package com.acme.api;", "", "import com.other.RestController;", "import com.other.GetMapping;", "", "@RestController", "class Fake {", '    @GetMapping("/f")', "    void f() {}", "}"])
        self.repo({"files": {PATH: controller(*LIST), "src/main/java/com/acme/api/Helper.java": plain, "src/main/java/com/acme/api/Fake.java": look}})
        self.assertEqual(self.labels(), [("GET /invoices", "InvoiceController.list")])

    def test_a_wildcard_import_of_the_spring_package_is_enough_and_a_qualified_annotation_too(self):
        src = f(["package p;", "", "@org.springframework.web.bind.annotation.RestController", "class Q {", '    @org.springframework.web.bind.annotation.GetMapping("/q")', "    void q() {}", "}"])
        self.repo({"files": {"src/main/java/p/Q.java": src, PATH: controller(*LIST, imports=["import org.springframework.web.bind.annotation.*;"])}})
        self.assertEqual(self.labels(), [("GET /invoices", "InvoiceController.list"), ("GET /q", "Q.q")])

    def test_tests_are_not_controllers(self):
        self.repo({"files": {PATH: controller(*LIST), "src/test/java/com/acme/api/TestController.java": controller(*GET, name="TestController")}})
        self.assertEqual(self.labels(), [("GET /invoices", "InvoiceController.list")])

    def test_routes_are_listed_by_path_and_method(self):
        self.repo({"files": {PATH: controller('    @PostMapping("/b")', "    void b() {}", '    @GetMapping("/b")', "    void a() {}", '    @GetMapping("/a")', "    void c() {}")}})
        self.assertEqual([r.label for r in self.routes()], ["GET /invoices/a", "GET /invoices/b", "POST /invoices/b"])


class Styles(Base):
    def test_a_project_annotation_that_carries_the_stereotype_or_a_mapping_makes_a_controller_and_a_handler(self):
        stereotype = f(["package com.acme.api;", "", "import org.springframework.web.bind.annotation.RestController;", "", "@RestController", "public @interface ApiController {", "}"])
        get = f(["package com.acme.api;", "", "import org.springframework.web.bind.annotation.GetMapping;", "", "@GetMapping", "public @interface Fetch {", "    String[] value() default {};", "}"])
        ctrl = f(["package com.acme.api;", "", "@ApiController", "public class Composed {", '    @Fetch("/f")', "    void f() {}", "}"])
        self.repo({"files": {"src/main/java/com/acme/api/ApiController.java": stereotype, "src/main/java/com/acme/api/Fetch.java": get, "src/main/java/com/acme/api/Composed.java": ctrl}})
        self.assertEqual(self.labels(), [("GET /f", "Composed.f")])

    def test_a_look_alike_annotation_of_the_project_is_no_stereotype(self):
        look = f(["package com.acme.api;", "", "public @interface RestController {", "}"])
        ctrl = f(["package com.acme.api;", "", "@RestController", "public class Fake {", '    @GetMapping("/f")', "    void f() {}", "}"])
        self.repo({"files": {"src/main/java/com/acme/api/RestController.java": look, "src/main/java/com/acme/api/Fake.java": ctrl}})
        self.assertEqual(self.labels(), [])

    def test_functional_routes_and_jakarta_rest_are_said_not_taken_for_no_routes(self):
        fn = f(["package com.acme.api;", "", "import org.springframework.web.servlet.function.RouterFunction;", "", "class Routes {", "    RouterFunction<?> routes() { return null; }", "}"])
        jx = f(["package com.acme.api;", "", "import jakarta.ws.rs.Path;", "", "@Path(\"/jx\")", "class Res {", "}"])
        self.repo({"files": {PATH: controller(*LIST), "src/main/java/com/acme/api/Routes.java": fn, "src/main/java/com/acme/api/Res.java": jx}})
        a = self.spring()
        table = a._table(self.sha("main"))
        self.assertFalse(table.complete)
        self.assertEqual(sorted(r.subject for r in self.records if r.kind == "route_style_unread"), ["src/main/java/com/acme/api/Res.java", "src/main/java/com/acme/api/Routes.java"])
        self.assertEqual([r.label for r in table.routes()], ["GET /invoices"])

    def test_a_path_variable_with_a_regex_that_holds_braces(self):
        self.repo({"files": {PATH: controller('    @GetMapping("/{code:[a-z]{3}}/x")', "    void x() {}", head=("@RestController",))}})
        self.assertEqual(self.labels(), [("GET /{code}/x", "InvoiceController.x")])


class Constants(Base):
    PATHS = f([
        "package com.acme.api;", "",
        "public final class Paths {",
        '    public static final String BASE = "/api";',
        '    public static final String INVOICES = BASE + "/invoices";',
        '    public static final String[] MANY = {"/a", "/b"};',
        "}",
    ])

    def test_a_constant_of_another_class_by_qualified_name_with_the_line_that_gives_the_value(self):
        src = controller(*GET, head=("@RestController", "@RequestMapping(Paths.INVOICES)"))
        self.repo({"files": {PATH: src, "src/main/java/com/acme/api/Paths.java": self.PATHS}})
        r = self.one("GET /api/invoices/{id}") if self.labels() else None
        self.assertEqual([(e.path.rsplit("/", 1)[1], e.line) for e in r.evidence], [("InvoiceController.java", 8), ("InvoiceController.java", 6), ("Paths.java", 5), ("Paths.java", 4)])

    def test_a_static_import_and_a_constant_of_the_same_class(self):
        src = controller('    @GetMapping(LOCAL + "/x")', "    void x() {}", '    static final String LOCAL = "/l";', head=("@RestController", "@RequestMapping(BASE)"),
                         imports=[*IMPORTS, "import static com.acme.api.Paths.BASE;"])
        self.repo({"files": {PATH: src, "src/main/java/com/acme/api/Paths.java": self.PATHS}})
        self.assertEqual(self.labels(), [("GET /api/l/x", "InvoiceController.x")])

    def test_an_array_constant_gives_several_routes(self):
        src = controller("    @GetMapping(Paths.MANY)", "    void m() {}", head=("@RestController",))
        self.repo({"files": {PATH: src, "src/main/java/com/acme/api/Paths.java": self.PATHS}})
        self.assertEqual([r.label for r in self.routes()], ["GET /a", "GET /b"])

    def test_a_constant_of_another_package_through_a_wildcard_import_and_a_fully_qualified_name(self):
        other = f(["package com.acme.shared;", "", "public interface Api {", '    String V1 = "/v1";', "}"])
        src = controller('    @GetMapping(com.acme.shared.Api.V1 + "/q")', "    void q() {}", '    @GetMapping(Api.V1)', "    void r() {}", head=("@RestController",),
                         imports=[*IMPORTS, "import com.acme.shared.*;"])
        self.repo({"files": {PATH: src, "src/main/java/com/acme/shared/Api.java": other}})
        self.assertEqual(self.labels(), [("GET /v1", "InvoiceController.r"), ("GET /v1/q", "InvoiceController.q")])

    def test_a_constant_that_is_a_call_or_not_a_project_class_is_unknown_and_the_route_is_partial(self):
        src = controller('    @GetMapping(Config.base() + "/x")', "    void x() {}", '    @GetMapping(org.lib.Consts.P)', "    void y() {}", head=("@RestController",))
        self.repo({"files": {PATH: src, "src/main/java/com/acme/api/Config.java": f(["package com.acme.api;", "class Config { static String base() { return \"/b\"; } }"])}})
        self.assertEqual(self.labels(), [("GET /<unknown:Config.base()>/x", "InvoiceController.x"), ("GET /<unknown:org.lib.Consts.P>", "InvoiceController.y")])
        self.assertTrue(all(r.partial for r in self.routes()))

    def test_a_constant_cycle_ends(self):
        other = f(["package com.acme.api;", "", "class C {", '    static final String A = B + "/a";', '    static final String B = A + "/b";', "}"])
        src = controller("    @GetMapping(C.A)", "    void x() {}", head=("@RestController",))
        self.repo({"files": {PATH: src, "src/main/java/com/acme/api/C.java": other}})
        (r,) = self.routes()
        self.assertTrue(r.partial)

    def test_a_method_that_is_not_a_request_method_is_unknown(self):
        src = controller('    @RequestMapping(value = "/m", method = VERBS.ONE)', "    void m() {}", head=("@RestController",))
        self.repo({"files": {PATH: src}})
        (r,) = self.routes()
        self.assertEqual((r.method, r.partial), ("<unknown:VERBS.ONE>", True))


class Kotlin(Base):
    KPATH = "src/main/kotlin/com/acme/api/CustomerController.kt"

    def kt(self, *body, head=("@RestController", '@RequestMapping("/customers")'), extra=()):
        return f(["package com.acme.api", "", "import org.springframework.web.bind.annotation.*", *extra, "", *head, "class CustomerController(private val service: Svc) {", *body, "}"])

    def test_a_kotlin_controller_with_arrays_templates_and_constants(self):
        body = [
            '    @GetMapping("/{id}")', "    fun get(@PathVariable id: String) = 1", '    @GetMapping(value = ["/a", "/b"])', "    fun many() = 2",
            '    @PostMapping(path = [BASE])', "    fun post() = 3", '    @GetMapping("$BASE/t")', "    fun t() = 4", '    @GetMapping("${BASE}/u")', "    fun u() = 5",
            '    @RequestMapping(value = ["/r"], method = [RequestMethod.PUT, RequestMethod.PATCH])', "    fun r() = 6",
            '    @GetMapping(arrayOf("/ar"))', "    fun ar() = 7",
        ]
        src = self.kt(*body, extra=("const val BASE = \"/b\"",))
        # the constant sits in the same file (a top-level const): the import lines are before it, so put it after the imports
        src = src.replace('const val BASE = "/b"\n', "")
        src += 'const val BASE = "/b"\n'
        self.repo({"files": {self.KPATH: src}})
        self.assertEqual(self.labels(), sorted([
            ("GET /customers/{id}", "CustomerController.get"), ("GET /customers/a", "CustomerController.many"), ("GET /customers/b", "CustomerController.many"),
            ("POST /customers/b", "CustomerController.post"), ("GET /customers/b/t", "CustomerController.t"), ("GET /customers/b/u", "CustomerController.u"),
            ("PUT /customers/r", "CustomerController.r"), ("PATCH /customers/r", "CustomerController.r"), ("GET /customers/ar", "CustomerController.ar"),
        ]))

    def test_a_const_in_an_object_or_a_companion_by_a_qualified_name(self):
        consts = f(["package com.acme.api", "", "object Routes {", '    const val ROOT = "/kt"', "}", "", "class Other {", "    companion object {", '        const val SUB = "/sub"', "    }", "}"])
        body = ['    @GetMapping(Other.SUB)', "    fun s() = 1"]
        self.repo({"files": {self.KPATH: self.kt(*body, head=("@RestController", "@RequestMapping(Routes.ROOT)")), "src/main/kotlin/com/acme/api/Routes.kt": consts}})
        self.assertEqual(self.labels(), [("GET /kt/sub", "CustomerController.s")])

    def test_a_java_service_does_not_matter_but_a_kotlin_and_a_java_controller_are_both_listed(self):
        self.repo({"files": {self.KPATH: self.kt('    @GetMapping("/k")', "    fun k() = 1"), PATH: controller(*LIST)}})
        self.assertEqual(self.labels(), [("GET /customers/k", "CustomerController.k"), ("GET /invoices", "InvoiceController.list")])


class Configuration(Base):
    def test_the_context_path_from_application_properties_is_put_before_every_route(self):
        cfg = "# the service\nserver.port=8080\nserver.servlet.context-path=/ledger\n"
        self.repo({"files": {PATH: controller(*LIST), "src/main/resources/application.properties": cfg}})
        r = self.one("GET /ledger/invoices") if self.labels() else None
        self.assertEqual([(e.path.rsplit("/", 1)[1], e.line) for e in r.evidence], [("InvoiceController.java", 8), ("InvoiceController.java", 6), ("application.properties", 3)])

    def test_the_context_path_from_yaml_nested_and_flat(self):
        nested = "spring:\n  application:\n    name: x\nserver:\n  port: 1\n  servlet:\n    context-path: /y\n"
        self.repo({"files": {PATH: controller(*LIST), "src/main/resources/application.yml": nested}})
        self.assertEqual([r.path for r in self.routes()], ["/y/invoices"])
        self.repo({"files": {PATH: controller(*LIST), "src/main/resources/application.yaml": "server.servlet.context-path: '/flat'\n"}})
        self.assertEqual([r.path for r in self.routes()], ["/flat/invoices"])

    def test_a_property_placeholder_in_a_mapping(self):
        cfg = "app.api.base=/v2\n"
        src = controller("    @GetMapping(\"${app.api.base}/x\")", "    void x() {}", '    @GetMapping("${missing.key}/y")', "    void y() {}", '    @GetMapping("${other:/dflt}/z")', "    void z() {}", head=("@RestController",))
        self.repo({"files": {PATH: src, "src/main/resources/application.properties": cfg}})
        got = {h: (l, self.one(l).partial) for l, h in self.labels()}
        self.assertEqual(got["InvoiceController.x"], ("GET /v2/x", False))
        self.assertEqual(got["InvoiceController.y"], ("GET /<unknown:${missing.key}>/y", True))
        self.assertEqual(got["InvoiceController.z"], ("GET /dflt/z", False))
        self.assertEqual([(e.path.rsplit("/", 1)[1], e.line) for e in self.one("GET /v2/x").evidence], [("InvoiceController.java", 7), ("application.properties", 1)])

    def test_the_servlet_path_comes_after_the_context_path(self):
        cfg = "server.servlet.context-path=/c\nspring.mvc.servlet.path=/s\n"
        self.repo({"files": {PATH: controller(*LIST), "src/main/resources/application.properties": cfg}})
        self.assertEqual([r.path for r in self.routes()], ["/c/s/invoices"])

    def test_a_context_path_set_under_a_profile_makes_the_prefix_unknown(self):
        self.repo({"files": {PATH: controller(*LIST), "src/main/resources/application.properties": "server.servlet.context-path=/a\n", "src/main/resources/application-prod.properties": "server.servlet.context-path=/b\n"}})
        (r,) = self.routes()
        self.assertEqual((r.path, r.partial), ("/<unknown:server.servlet.context-path>/invoices", True))

    def test_a_profile_that_sets_the_same_value_changes_nothing(self):
        self.repo({"files": {PATH: controller(*LIST), "src/main/resources/application.properties": "server.servlet.context-path=/a\n", "src/main/resources/application-prod.properties": "server.servlet.context-path=/a\n"}})
        (r,) = self.routes()
        self.assertEqual((r.path, r.partial), ("/a/invoices", False))

    def test_a_yaml_the_reader_does_not_understand_hides_the_key_and_is_said(self):
        self.repo({"files": {PATH: controller(*LIST), "src/main/resources/application.yml": "server: {servlet: {context-path: /x}}\n"}})
        (r,) = self.routes()
        self.assertTrue(r.partial)
        self.assertIn(("config_unread", True), [(x.kind, x.blocks_selection) for x in self.records])
        self.assertTrue(self.entry.problems)

    def test_the_option_overrides_the_configuration(self):
        self.repo({"files": {PATH: controller(*LIST), "src/main/resources/application.properties": "server.servlet.context-path=/a\n"}})
        self.assertEqual([r.path for r in self.routes(context_path="/over")], ["/over/invoices"])

    def test_a_test_configuration_is_not_read(self):
        self.repo({"files": {PATH: controller(*LIST), "src/test/resources/application.properties": "server.servlet.context-path=/t\n"}})
        self.assertEqual([r.path for r in self.routes()], ["/invoices"])


class Inheritance(Base):
    API = f([
        "package com.acme.api;", "", *IMPORTS, "",
        '@RequestMapping("/base")',
        "public interface InvoicesApi {",
        '    @GetMapping("/{id}")',
        "    String get(@PathVariable String id);",
        '    @PostMapping',
        "    String create();",
        "}",
    ])

    def test_a_controller_has_the_mappings_of_its_interface_and_a_class_mapping_it_has_none_of(self):
        impl = f(["package com.acme.api;", "", "@org.springframework.web.bind.annotation.RestController", "public class InvoicesApiController implements InvoicesApi {", "    public String get(String id) { return id; }", "    public String create() { return \"\"; }", "}"])
        self.repo({"files": {"src/main/java/com/acme/api/InvoicesApi.java": self.API, "src/main/java/com/acme/api/InvoicesApiController.java": impl}})
        got = self.labels()
        self.assertEqual(got, [("GET /base/{id}", "InvoicesApiController.get"), ("POST /base", "InvoicesApiController.create")])
        r = self.one("GET /base/{id}")
        self.assertEqual((r.file, r.target), ("src/main/java/com/acme/api/InvoicesApiController.java", "src/main/java/com/acme/api/InvoicesApiController.java"))
        self.assertEqual([(e.path.rsplit("/", 1)[1], e.line) for e in r.evidence], [("InvoicesApi.java", 7), ("InvoicesApi.java", 5)])
        self.assertTrue(self.entry.is_entry_file("src/main/java/com/acme/api/InvoicesApi.java", self.sha("main")))

    def test_a_base_class_handler_the_subclass_does_not_override_is_a_route_of_the_subclass_run_by_the_base(self):
        base = f(["package com.acme.api;", "", *IMPORTS, "", "public abstract class BaseController {", '    @GetMapping("/ping")', "    public String ping() { return \"pong\"; }", "}"])
        sub = controller(*LIST, head=("@RestController", '@RequestMapping("/invoices")'), name="InvoiceController").replace("public class InvoiceController", "public class InvoiceController extends BaseController")
        self.repo({"files": {"src/main/java/com/acme/api/BaseController.java": base, PATH: sub}})
        self.assertEqual(self.labels(), [("GET /invoices", "InvoiceController.list"), ("GET /invoices/ping", "BaseController.ping")])
        self.assertEqual(self.one("GET /invoices/ping").file, "src/main/java/com/acme/api/BaseController.java")

    def test_the_controller_that_maps_a_method_itself_wins(self):
        impl = f(["package com.acme.api;", "", *IMPORTS, "", "@RestController", "public class InvoicesApiController implements InvoicesApi {", '    @GetMapping("/mine/{id}")', "    public String get(String id) { return id; }", "    public String create() { return \"\"; }", "}"])
        self.repo({"files": {"src/main/java/com/acme/api/InvoicesApi.java": self.API, "src/main/java/com/acme/api/InvoicesApiController.java": impl}})
        self.assertEqual(self.labels(), [("GET /base/mine/{id}", "InvoicesApiController.get"), ("POST /base", "InvoicesApiController.create")])


class RemovedAndAdded(Base):
    def test_a_removed_and_an_added_route(self):
        self.repo({"files": {PATH: controller(*LIST, *GET)}}, {"files": {PATH: controller(*LIST, '    @DeleteMapping("/{id}")', "    void d() {}")}})
        a = self.spring()
        removed, added = a.routes_changed(self.sha("main~1"), self.sha("main"))
        self.assertEqual(([r.label for r in removed], [r.label for r in added]), (["GET /invoices/{id}"], ["DELETE /invoices/{id}"]))
        self.assertEqual(removed[0].evidence[0].ref, self.sha("main~1"))
        self.assertEqual(added[0].evidence[0].ref, self.sha("main"))

    def test_without_a_pre_change_version_nothing_is_computed(self):
        self.repo({"files": {PATH: controller(*LIST)}})
        self.assertEqual(self.spring().routes_changed(None, self.sha("main")), ([], []))

    def test_a_controller_that_stops_parsing_is_not_a_removed_route(self):
        broken = controller(*LIST, '    String s = "oops;')
        self.repo({"files": {PATH: controller(*LIST, *GET), "src/main/java/com/acme/api/Other.java": controller(*LIST, name="Other", head=("@RestController", '@RequestMapping("/o")'))}},
                  {"files": {PATH: broken}})
        a = self.spring()
        removed, added = a.routes_changed(self.sha("main~1"), self.sha("main"))
        self.assertEqual((removed, added), ([], []))  # the routes of the unreadable file were not read, they were not deleted
        self.assertFalse(a._table(self.sha("main")).complete)
        self.assertTrue(a.problems)
        self.assertIn(("parse_degraded", True), self.kinds())
        self.assertIn(("route_change_withheld", True), self.kinds())  # the one shared record of what the guard left out (`guard_route_changes`)
        self.assertTrue(a.withholds_unread)
        self.assertEqual(a.routes_complete(self.sha("main"))[0], False)
        self.assertEqual([r.label for r in a.routes(None, self.sha("main"))], ["GET /o"])  # what could be read is still listed

    def test_a_controller_that_did_not_parse_at_the_base_is_not_an_added_route(self):
        broken = controller(*LIST, '    String s = "oops;')
        self.repo({"files": {PATH: broken}}, {"files": {PATH: controller(*LIST, *GET)}})
        removed, added = self.spring().routes_changed(self.sha("main~1"), self.sha("main"))
        self.assertEqual((removed, added), ([], []))
        self.assertIn(("route_change_withheld", True), self.kinds())

    def test_a_readable_change_makes_no_withheld_record(self):
        self.repo({"files": {PATH: controller(*LIST, *GET)}}, {"files": {PATH: controller(*LIST)}})
        removed, _added = self.spring().routes_changed(self.sha("main~1"), self.sha("main"))
        self.assertEqual([r.label for r in removed], ["GET /invoices/{id}"])
        self.assertNotIn("route_change_withheld", [k for k, _b in self.kinds()])

    def test_a_search_that_cannot_be_made_is_an_incomplete_table_never_an_empty_one(self):
        from unittest import mock

        self.repo({"files": {PATH: controller(*LIST)}})
        a = self.spring()
        with mock.patch.object(self.language, "search", return_value=None):
            table = a._table(self.sha("main"))
        self.assertFalse(table.complete)
        self.assertEqual(table.routes(), [])
        self.assertTrue(a.problems)

    def test_an_unreadable_file_is_an_incomplete_table(self):
        from unittest import mock

        from reachmap.model import ReadResult

        self.repo({"files": {PATH: controller(*LIST)}})
        a = self.spring()
        real = self.language.file_text
        with mock.patch.object(self.language, "file_text", lambda sha, p: ReadResult.unreadable("simulated") if p == PATH else real(sha, p)):
            table = a._table(self.sha("main"))
        self.assertFalse(table.complete)
        self.assertEqual(table.routes(), [])


class Options(unittest.TestCase):
    def test_the_option_validator(self):
        from reachmap.adapters.spring import validate_options

        d = lambda v: type(v).__name__  # noqa: E731
        self.assertEqual(validate_options({"kind": "spring"}, d), [])
        self.assertEqual(validate_options({"kind": "spring", "context_path": "/api"}, d), [])
        for bad in ("api", "/a/../b", "${x}", 5):
            self.assertTrue(validate_options({"kind": "spring", "context_path": bad}, d), bad)
        self.assertTrue(validate_options({"kind": "spring", "root": "x"}, d))

    def test_the_kind_and_the_languages_are_registered(self):
        from reachmap import config, entrypoints

        self.assertIn("spring", entrypoints.KINDS)
        self.assertIn("java", config._LANGUAGES)
        self.assertIn("kotlin", config._LANGUAGES)


if __name__ == "__main__":
    unittest.main()
