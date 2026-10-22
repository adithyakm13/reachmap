"""What a change reaches in a Spring application, member by member: a change inside one handler reaches that handler's routes and not its
siblings, a change to a service reaches the handlers that spell what changed, a class level change (annotation, constructor, initializer)
reaches every route of the class, a changed constant reaches the routes whose path it is part of, a changed import reaches what spells it, a
change to the application configuration or to a class that wires the application is not followed. Generic names are not followed by name.
"""

import unittest

from reachmap import entrypoints
from tests import scenario_support as ss
from tests.support_universe import f
from tests.test_spring_routes import GET, IMPORTS, LIST, PATH, Base as RoutesBase, controller

SVC_PATH = "src/main/java/com/acme/service/InvoiceService.java"
SERVICE = f([
    "package com.acme.service;", "",
    "public class InvoiceService {",
    "    public int total(String id) {",
    "        return 1;",
    "    }", "",
    "    public int count() {",
    "        return 2;",
    "    }",
    "}",
])
CTRL = f([
    "package com.acme.api;", "",
    "import com.acme.service.InvoiceService;",
    "import org.springframework.web.bind.annotation.*;", "",
    "@RestController",
    '@RequestMapping("/invoices")',
    "public class InvoiceController {",
    "    private final InvoiceService service;", "",
    "    public InvoiceController(InvoiceService service) {",
    "        this.service = service;",
    "    }", "",
    '    @GetMapping("/{id}/total")',
    "    public int total(@PathVariable String id) {",
    "        return service.total(id);",
    "    }", "",
    '    @GetMapping("/count")',
    "    public int count() {",
    "        return service.count();",
    "    }", "",
    '    @GetMapping("/health")',
    "    public String health() {",
    '        return "ok";',
    "    }",
    "}",
])
TREE = {SVC_PATH: SERVICE, PATH: CTRL}


class Base(RoutesBase):
    UNIVERSE_PREFIX = "reachmap-spring-reach-"

    def run_reach(self, tree_before, tree_after, **kw):
        self.repo({"files": tree_before}, {"files": {p: t for p, t in tree_after.items() if t is not None}, "delete": [p for p, t in tree_after.items() if t is None]})
        language = self.make_adapter(distinct_max=kw.pop("distinct_max", 3), hub_importers=kw.pop("hub_importers", 50))
        self.entry = self.spring()
        self.language = self.entry.language = language
        change = self.change(**kw)
        analysis = self.language.analyze(change)
        self.result = entrypoints.reach(analysis, self.language, self.entry)
        chains = [e for r in self.result.reached for c in r.area.chains for e in c]
        self.assertEqual(ss.evidence_problems({self.REPO: self.path}, chains), [])
        return self.result

    def labels_of(self):
        return sorted(r.area.label for r in self.result.reached)

    def chain_of(self, label):
        (r,) = [r for r in self.result.reached if r.area.label == label]
        return [(e.path.rsplit("/", 1)[1], e.line) for e in r.area.chains[0]]


class ThroughImports(Base):
    def test_a_service_member_reaches_the_handlers_that_spell_it_and_not_the_others(self):
        res = self.run_reach(TREE, {SVC_PATH: SERVICE.replace("return 1;", "return 11;")})
        self.assertEqual(self.labels_of(), ["GET /invoices/{id}/total"])
        self.assertTrue(res.complete, res.reasons)
        # the chain: the import of the service in the controller, then the route's own lines (method mapping, class mapping)
        self.assertEqual(self.chain_of("GET /invoices/{id}/total"), [("InvoiceController.java", 3), ("InvoiceController.java", 15), ("InvoiceController.java", 7)])

    def test_a_class_level_change_of_the_service_reaches_every_handler_that_uses_it(self):
        res = self.run_reach(TREE, {SVC_PATH: SERVICE.replace("public class InvoiceService {", "@Transactional\npublic class InvoiceService {")})
        self.assertEqual(self.labels_of(), ["GET /invoices/count", "GET /invoices/{id}/total"])  # not /health: it does not use the service
        self.assertTrue(res.complete)

    def test_a_generic_member_name_is_not_followed_by_name(self):
        """`get` is defined in five classes: the handler that calls `cache.get` is not reached, the one that uses the service type is."""
        svc = f(["package com.acme.service;", "", "public class Cache {", "    public int get(String k) {", "        return 1;", "    }", "}"])
        ctrl = f([
            "package com.acme.api;", "", "import com.acme.service.Cache;", "import org.springframework.web.bind.annotation.*;", "", "@RestController", "public class CacheController {",
            "    private final Cache cache;", "    private final java.util.Map<String, Integer> map = new java.util.HashMap<>();", "",
            '    @GetMapping("/a")', "    public int a() {", '        return cache.get("a");', "    }", "",
            '    @GetMapping("/b")', "    public int b() {", '        return map.get("b");', "    }", "}",
        ])
        others = {f"src/main/java/com/acme/o{i}/O.java": f([f"package com.acme.o{i};", "", "class O {", "    int get() { return 0; }", "}"]) for i in range(5)}
        tree = {"src/main/java/com/acme/service/Cache.java": svc, "src/main/java/com/acme/api/CacheController.java": ctrl, **others}
        self.run_reach(tree, {"src/main/java/com/acme/service/Cache.java": svc.replace("return 1;", "return 2;")})
        self.assertEqual(self.labels_of(), ["GET /a"])
        self.assertIn(("not_distinctive", False), self.kinds())
        self.assertEqual([r.subject for r in self.records if r.kind == "not_distinctive"], ["get"])

    def test_a_service_that_nothing_but_a_test_uses_reaches_no_route(self):
        test = f(["package com.acme.service;", "", "class InvoiceServiceTest { InvoiceService s; }"])
        res = self.run_reach({**TREE, "src/test/java/com/acme/service/InvoiceServiceTest.java": test}, {SVC_PATH: SERVICE.replace("return 2;", "return 22;")})
        self.assertEqual(self.labels_of(), ["GET /invoices/count"])
        self.assertTrue(res.complete)

    def test_a_service_used_by_a_service_used_by_a_controller(self):
        inner = f(["package com.acme.service;", "", "public class Rates {", "    public int rate() {", "        return 1;", "    }", "}"])
        svc = f(["package com.acme.service;", "", "public class InvoiceService {", "    Rates rates;", "    public int total(String id) {", "        return rates.rate();", "    }", "", "    public int count() {", "        return 2;", "    }", "}"])
        self.run_reach({**TREE, SVC_PATH: svc, "src/main/java/com/acme/service/Rates.java": inner}, {"src/main/java/com/acme/service/Rates.java": inner.replace("return 1;", "return 3;")})
        self.assertEqual(self.labels_of(), ["GET /invoices/{id}/total"])  # Rates.rate reaches InvoiceService.total, which the controller spells in one handler
        self.assertEqual(self.chain_of("GET /invoices/{id}/total")[:2], [("InvoiceService.java", 4), ("InvoiceController.java", 3)])

    def test_a_constant_reaches_every_route_of_a_class_whose_path_it_is_part_of_and_the_route_of_a_method_that_spells_it(self):
        paths = f(["package com.acme.api;", "", "public final class Paths {", '    public static final String BASE = "/invoices";', '    public static final String ONE = "/one";', "}"])
        ctrl = f([
            "package com.acme.api;", "", *IMPORTS, "", "@RestController", "@RequestMapping(Paths.BASE)", "public class InvoiceController {",
            '    @GetMapping(Paths.ONE)', "    public int one() { return 1; }", '    @GetMapping("/two")', "    public int two() { return 2; }", "}",
        ])
        base = {PATH: ctrl, "src/main/java/com/acme/api/Paths.java": paths}
        self.run_reach(base, {"src/main/java/com/acme/api/Paths.java": paths.replace('"/invoices"', '"/bills"')})
        self.assertEqual(self.labels_of(), ["GET /bills/one", "GET /bills/two"])  # the class path changes: every route of the class
        self.run_reach(base, {"src/main/java/com/acme/api/Paths.java": paths.replace('"/one"', '"/uno"')})
        self.assertEqual(self.labels_of(), ["GET /invoices/uno"])  # one method's path: that route only

    def test_an_interface_implementation_reaches_the_handlers_that_call_the_interface(self):
        iface = f(["package com.acme.service;", "", "public interface Pricing {", "    int price(String id);", "    int tax(String id);", "}"])
        impl = f(["package com.acme.service;", "", "public class PricingImpl implements Pricing {", "    public int price(String id) {", "        return 1;", "    }", "", "    public int tax(String id) {", "        return 2;", "    }", "}"])
        ctrl = f(["package com.acme.api;", "", "import com.acme.service.Pricing;", *IMPORTS, "", "@RestController", "public class PriceController {", "    Pricing pricing;",
                  '    @GetMapping("/price")', "    public int price() { return pricing.price(\"x\"); }", '    @GetMapping("/tax")', "    public int tax() { return pricing.tax(\"x\"); }", "}"])
        tree = {"src/main/java/com/acme/service/Pricing.java": iface, "src/main/java/com/acme/service/PricingImpl.java": impl, "src/main/java/com/acme/api/PriceController.java": ctrl}
        self.run_reach(tree, {"src/main/java/com/acme/service/PricingImpl.java": impl.replace("return 1;", "return 11;")})
        self.assertEqual(self.labels_of(), ["GET /price"])

    def test_the_same_package_needs_no_import_to_reach_a_route(self):
        helper = f(["package com.acme.api;", "", "class Helper {", "    static int help() {", "        return 1;", "    }", "}"])
        ctrl = f(["package com.acme.api;", "", *IMPORTS, "", "@RestController", "class HelpController {", '    @GetMapping("/h")', "    int h() { return Helper.help(); }", '    @GetMapping("/g")', "    int g() { return 2; }", "}"])
        tree = {"src/main/java/com/acme/api/Helper.java": helper, "src/main/java/com/acme/api/HelpController.java": ctrl}
        self.run_reach(tree, {"src/main/java/com/acme/api/Helper.java": helper.replace("return 1;", "return 3;")})
        self.assertEqual(self.labels_of(), ["GET /h"])

    def test_a_kotlin_controller_that_uses_a_java_service(self):
        kt = f(["package com.acme.api", "", "import com.acme.service.InvoiceService", "import org.springframework.web.bind.annotation.*", "", "@RestController", "@RequestMapping(\"/k\")",
                "class KController(private val service: InvoiceService) {", '    @GetMapping("/t")', "    fun t(): Int = service.total(\"x\")", '    @GetMapping("/c")', "    fun c(): Int = service.count()", "}"])
        self.run_reach({SVC_PATH: SERVICE, "src/main/kotlin/com/acme/api/KController.kt": kt}, {SVC_PATH: SERVICE.replace("return 1;", "return 11;")})
        self.assertEqual(self.labels_of(), ["GET /k/t"])


class InsideTheController(Base):
    def test_a_change_inside_one_handler_reaches_its_routes_only(self):
        res = self.run_reach(TREE, {PATH: CTRL.replace('return "ok";', 'return "fine";')})
        self.assertEqual(self.labels_of(), ["GET /invoices/health"])
        self.assertEqual(self.chain_of("GET /invoices/health"), [("InvoiceController.java", 27), ("InvoiceController.java", 25), ("InvoiceController.java", 7)])
        self.assertTrue(res.complete)

    def test_a_change_to_the_mapping_line_reaches_that_route(self):
        self.run_reach(TREE, {PATH: CTRL.replace('@GetMapping("/health")', '@GetMapping("/healthz")')})
        self.assertEqual(self.labels_of(), ["GET /invoices/healthz"])

    def test_a_change_to_the_class_annotation_reaches_every_route(self):
        self.run_reach(TREE, {PATH: CTRL.replace("@RestController", "@RestController\n@CrossOrigin")})
        self.assertEqual(self.labels_of(), ["GET /invoices/count", "GET /invoices/health", "GET /invoices/{id}/total"])

    def test_a_change_to_the_constructor_reaches_every_route(self):
        self.run_reach(TREE, {PATH: CTRL.replace("this.service = service;", "this.service = service; init();")})
        self.assertEqual(self.labels_of(), ["GET /invoices/count", "GET /invoices/health", "GET /invoices/{id}/total"])

    def test_a_change_to_a_field_reaches_the_handlers_that_use_it(self):
        self.run_reach(TREE, {PATH: CTRL.replace("private final InvoiceService service;", "private final InvoiceService service = null;")})
        self.assertEqual(self.labels_of(), ["GET /invoices/count", "GET /invoices/{id}/total"])

    def test_a_private_helper_reaches_the_handlers_that_call_it(self):
        src = CTRL.replace('return "ok";', "return helper();").replace("    @GetMapping(\"/count\")", "    private String helper() {\n        return \"h\";\n    }\n\n    @GetMapping(\"/count\")")
        self.run_reach({SVC_PATH: SERVICE, PATH: src}, {PATH: src.replace('return "h";', 'return "hh";')})
        self.assertEqual(self.labels_of(), ["GET /invoices/health"])

    def test_an_added_import_reaches_what_spells_the_name_not_the_whole_class(self):
        src = CTRL.replace("import org.springframework.web.bind.annotation.*;", "import org.springframework.web.bind.annotation.*;\nimport java.util.List;")
        self.run_reach(TREE, {PATH: src})
        self.assertEqual(self.labels_of(), [])  # nothing spells List

    def test_a_removed_import_of_a_name_a_handler_spells_reaches_that_handler(self):
        before = CTRL.replace("import org.springframework.web.bind.annotation.*;", "import org.springframework.web.bind.annotation.*;\nimport java.util.List;").replace('return "ok";', "return List.of().toString();")
        after = before.replace("import java.util.List;", "import java.util.*;")
        self.run_reach({SVC_PATH: SERVICE, PATH: before}, {PATH: after})
        self.assertEqual(self.labels_of(), ["GET /invoices/count", "GET /invoices/health", "GET /invoices/{id}/total"])  # a wildcard import: which names it binds is not told

    def test_a_removed_handler_reaches_nothing_and_a_removed_line_in_a_kept_one_reaches_it(self):
        src = CTRL.replace("        return service.total(id);", "        int t = 0;\n        return service.total(id);")
        self.run_reach({SVC_PATH: SERVICE, PATH: src}, {PATH: CTRL})
        self.assertEqual(self.labels_of(), ["GET /invoices/{id}/total"])

    def test_a_class_with_several_handlers_on_one_method_name(self):
        self.run_reach(TREE, {PATH: CTRL.replace("    public int count() {\n        return service.count();", "    public int count() {\n        return service.count() + 1;")})
        self.assertEqual(self.labels_of(), ["GET /invoices/count"])


class Completeness(Base):
    def test_a_controller_that_stops_parsing_claims_no_route_and_makes_the_answer_incomplete(self):
        res = self.run_reach(TREE, {PATH: CTRL.replace('return "ok";', 'return "oops;')})
        self.assertEqual(self.labels_of(), [])
        self.assertFalse(res.complete)
        self.assertIn(("parse_degraded", True), self.kinds())

    def test_a_service_that_stops_parsing_is_a_module_level_change_that_reaches_every_importer(self):
        res = self.run_reach(TREE, {SVC_PATH: SERVICE.replace("return 1;", 'return "oops;')})
        self.assertEqual(self.labels_of(), ["GET /invoices/count", "GET /invoices/{id}/total"])
        self.assertIn(("parse_degraded", False), self.kinds())
        self.assertTrue(res.complete or res.reasons is not None)

    def test_a_candidate_importer_that_does_not_lex_makes_the_answer_incomplete(self):
        broken = f(["package com.acme.api;", "", "import com.acme.service.InvoiceService;", "", 'class Broken { String s = "oops; }'])
        res = self.run_reach({**TREE, "src/main/java/com/acme/api/Broken.java": broken}, {SVC_PATH: SERVICE.replace("return 1;", "return 11;")})
        self.assertFalse(res.complete)
        self.assertIn(("parse_degraded", True), self.kinds())
        self.assertEqual(self.labels_of(), ["GET /invoices/{id}/total"])  # what could be read is still listed


class NotFollowed(Base):
    def test_a_change_to_the_context_path_reaches_every_route_with_the_line_as_evidence(self):
        cfg = "server.servlet.context-path=/a\n"
        tree = {PATH: controller(*LIST, *GET), "src/main/resources/application.properties": cfg}
        res = self.run_reach(tree, {"src/main/resources/application.properties": "server.servlet.context-path=/b\n"})
        self.assertEqual(self.labels_of(), ["GET /b/invoices", "GET /b/invoices/{id}"])
        self.assertEqual(self.chain_of("GET /b/invoices"), [("InvoiceController.java", 8), ("InvoiceController.java", 6), ("application.properties", 1)])  # the changed line is where the path comes from: the last line of the route's evidence
        self.assertTrue(res.complete)

    def test_a_change_to_another_key_is_not_followed_and_says_so(self):
        tree = {PATH: controller(*LIST), "src/main/resources/application.properties": "server.port=1\n"}
        res = self.run_reach(tree, {"src/main/resources/application.properties": "server.port=2\n"})
        self.assertEqual(self.labels_of(), [])
        self.assertFalse(res.complete)
        self.assertIn(("config_not_followed", True), self.kinds())

    def test_a_comment_in_the_configuration_changes_nothing(self):
        tree = {PATH: controller(*LIST), "src/main/resources/application.properties": "server.port=1\n"}
        res = self.run_reach(tree, {"src/main/resources/application.properties": "# a comment\nserver.port=1\n"})
        self.assertEqual((self.labels_of(), res.complete, self.kinds()), ([], True, []))

    def test_a_placeholder_key_is_part_of_the_path(self):
        src = controller('    @GetMapping("${app.base}/x")', "    void x() {}", head=("@RestController",))
        tree = {PATH: src, "src/main/resources/application.properties": "app.base=/one\nserver.port=1\n"}
        self.run_reach(tree, {"src/main/resources/application.properties": "app.base=/two\nserver.port=1\n"})
        self.assertEqual(self.labels_of(), ["GET /two/x"])

    def test_a_class_that_wires_the_application_is_not_followed(self):
        cfg = f(["package com.acme.config;", "", "import org.springframework.context.annotation.*;", "", "@Configuration", "public class WebConfig {", "    @Bean", "    public int rate() {", "        return 1;", "    }", "}"])
        tree = {PATH: controller(*LIST), "src/main/java/com/acme/config/WebConfig.java": cfg}
        res = self.run_reach(tree, {"src/main/java/com/acme/config/WebConfig.java": cfg.replace("return 1;", "return 2;")})
        self.assertEqual(self.labels_of(), [])
        self.assertFalse(res.complete)
        self.assertIn(("config_not_followed", True), self.kinds())

    def test_a_filter_or_an_interceptor_or_a_security_class_is_wiring_too(self):
        for name, body in (
            ("AuthFilter", "public class AuthFilter extends OncePerRequestFilter {\n    void f() { int a = 1; }\n}"),
            ("Guard", "public class Guard implements HandlerInterceptor {\n    void f() { int a = 1; }\n}"),
        ):
            with self.subTest(name=name):
                src = f(["package com.acme.config;", "", body])
                tree = {PATH: controller(*LIST), f"src/main/java/com/acme/config/{name}.java": src}
                res = self.run_reach(tree, {f"src/main/java/com/acme/config/{name}.java": src.replace("int a = 1;", "int a = 2;")})
                self.assertFalse(res.complete)
                self.assertIn(("config_not_followed", True), self.kinds())

    def test_an_ordinary_class_that_is_not_wiring_is_not_a_blocking_record(self):
        res = self.run_reach(TREE, {SVC_PATH: SERVICE.replace("return 2;", "return 22;")})
        self.assertNotIn("config_not_followed", [k for k, _b in self.kinds()])
        self.assertTrue(res.complete)


if __name__ == "__main__":
    unittest.main()
