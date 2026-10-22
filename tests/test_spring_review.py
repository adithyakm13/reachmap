"""What an independent review found wrong in the Spring adapter, one test for the evidence of each finding: answers that were complete and wrong on
layouts the fixtures do not have (a placeholder default used when the key is set to something unknown, a context path borrowed from a sibling
module, a class name declared in two modules, controllers made of project annotations, Kotlin files whose folders omit the package, profile
documents in `.properties`), and the smaller rules that came with them.
"""

import re
import unittest
from pathlib import Path
from unittest import mock

from reachmap.adapters.jvm import lexer
from reachmap.adapters.spring import props
from tests.support_universe import f
from tests.test_spring_reach import Base as ReachBase
from tests.test_spring_routes import GET, IMPORTS, LIST, PATH, Base, controller

APP = "src/main/resources/application.properties"
YML = "src/main/resources/application.yml"


def kinds_of(test):
    return {(r.kind, r.blocks_selection) for r in test.records}


class Placeholders(Base):
    SRC = controller('    @GetMapping("${app.base:/dflt}/x")', "    void x() {}", head=("@RestController",))

    def test_a_default_is_not_used_when_a_profile_sets_the_key_to_another_value(self):
        self.repo({"files": {PATH: self.SRC, APP: "app.base=/a\n", "src/main/resources/application-prod.properties": "app.base=/b\n"}})
        (r,) = self.routes()
        self.assertEqual((r.path, r.partial), ("/<unknown:${app.base}>/x", True))
        self.assertIn(("config_unread", True), kinds_of(self))
        self.assertFalse(self.entry._table(self.sha("main")).complete)

    def test_a_default_is_not_used_when_a_yaml_the_reader_does_not_understand_may_hold_the_key(self):
        self.repo({"files": {PATH: self.SRC, YML: "app: {base: /v2}\n"}})
        (r,) = self.routes()
        self.assertEqual((r.path, r.partial), ("/<unknown:${app.base}>/x", True))
        self.assertIn(("config_unread", True), kinds_of(self))

    def test_the_default_still_stands_for_a_key_nobody_sets(self):
        self.repo({"files": {PATH: self.SRC, APP: "other=1\n"}})
        (r,) = self.routes()
        self.assertEqual((r.path, r.partial), ("/dflt/x", False))
        self.assertNotIn("config_unread", [k for k, _b in kinds_of(self)])

    def test_a_key_set_in_a_profile_only_is_unknown_not_the_default(self):
        self.repo({"files": {PATH: self.SRC, "src/main/resources/application-prod.properties": "app.base=/b\n"}})
        (r,) = self.routes()
        self.assertEqual((r.path, r.partial), ("/<unknown:${app.base}>/x", True))


class Modules(Base):
    def tree(self, **extra):
        return {"a/pom.xml": "<project/>\n", "b/pom.xml": "<project/>\n", "a/src/main/java/com/acme/api/InvoiceController.java": controller(*LIST), **extra}

    def test_a_module_with_no_configuration_of_its_own_does_not_borrow_a_sibling_context_path(self):
        self.repo({"files": self.tree(**{"b/src/main/resources/application.properties": "server.servlet.context-path=/b\n"})})
        (r,) = self.routes()
        self.assertEqual((r.path, r.partial), ("/<unknown:server.servlet.context-path>/invoices", True))
        self.assertIn(("config_unread", True), kinds_of(self))

    def test_a_module_with_its_own_configuration_uses_it_and_says_nothing(self):
        self.repo({"files": self.tree(**{"a/src/main/resources/application.properties": "server.servlet.context-path=/a\n", "b/src/main/resources/application.properties": "server.servlet.context-path=/b\n"})})
        (r,) = self.routes()
        self.assertEqual((r.path, r.partial), ("/a/invoices", False))
        self.assertNotIn("config_unread", [k for k, _b in kinds_of(self)])

    def test_a_repository_without_build_files_and_one_configuration_applies_it_everywhere(self):
        self.repo({"files": {PATH: controller(*LIST), APP: "server.servlet.context-path=/one\n"}})
        (r,) = self.routes()
        self.assertEqual((r.path, r.partial), ("/one/invoices", False))

    def test_a_module_with_no_configuration_and_no_sibling_with_one_has_no_prefix(self):
        self.repo({"files": self.tree()})
        (r,) = self.routes()
        self.assertEqual((r.path, r.partial), ("/invoices", False))


class SameName(Base):
    def paths(self, root, value):
        return f(["package com.acme.api;", "", "public class Paths {", f'    public static final String BASE = "{value}";', "}"])

    def ctl(self, name):
        return controller('    @GetMapping("/x")', "    void x() {}", name=name, head=("@RestController", '@RequestMapping(Paths.BASE + "/i")'))

    def test_a_class_declared_in_two_modules_is_the_one_of_the_controllers_module(self):
        tree = {"a/pom.xml": "<project/>\n", "b/pom.xml": "<project/>\n"}
        for m in ("a", "b"):
            tree[f"{m}/src/main/java/com/acme/api/Paths.java"] = self.paths(m, f"/{m}")
            tree[f"{m}/src/main/java/com/acme/api/{m.upper()}Controller.java"] = self.ctl(f"{m.upper()}Controller")
        self.repo({"files": tree})
        self.assertEqual(self.labels(), [("GET /a/i/x", "AController.x"), ("GET /b/i/x", "BController.x")])
        self.assertTrue(self.entry._table(self.sha("main")).complete)

    def test_the_module_is_preferred_to_the_source_root_when_a_module_has_several(self):
        tree = {"a/pom.xml": "<project/>\n", "b/pom.xml": "<project/>\n",
                "a/src/main/java/com/acme/api/Paths.java": self.paths("a", "/a"), "b/src/main/kotlin/com/acme/api/Paths.java": self.paths("b", "/b"),
                "a/src/main/kotlin/com/acme/api/AController.java": self.ctl("AController")}
        self.repo({"files": tree})
        self.assertEqual(self.labels(), [("GET /a/i/x", "AController.x")])

    def test_a_class_declared_in_two_source_roots_is_the_one_of_the_controllers_root(self):
        tree = {}
        for m in ("one", "two"):
            tree[f"{m}/src/main/java/com/acme/api/Paths.java"] = self.paths(m, f"/{m}")
        tree["one/src/main/java/com/acme/api/OneController.java"] = self.ctl("OneController")
        self.repo({"files": tree})
        self.assertEqual(self.labels(), [("GET /one/i/x", "OneController.x")])

    def test_a_class_declared_twice_where_nothing_tells_which_is_meant_is_unknown_and_said(self):
        tree = {f"{m}/src/main/java/com/acme/api/Paths.java": self.paths(m, f"/{m}") for m in ("one", "two")}
        tree["three/src/main/java/com/acme/api/ThreeController.java"] = self.ctl("ThreeController")
        self.repo({"files": tree})
        (r,) = self.routes()
        self.assertTrue(r.partial)
        self.assertIn("unknown", r.path)
        self.assertNotIn("/one", r.path)
        self.assertNotIn("/two", r.path)
        self.assertIn(("type_ambiguous", True), kinds_of(self))
        self.assertFalse(self.entry._table(self.sha("main")).complete)

    def test_a_supertype_declared_twice_where_nothing_tells_which_is_meant_makes_the_table_incomplete(self):
        base = lambda: f(["package com.acme.api;", "", *IMPORTS, "", "public abstract class BaseController {", '    @GetMapping("/ping")', "    public String ping() { return \"pong\"; }", "}"])  # noqa: E731
        tree = {f"{m}/src/main/java/com/acme/api/BaseController.java": base() for m in ("one", "two")}
        tree["three/src/main/java/com/acme/api/ThreeController.java"] = controller(*LIST, name="ThreeController").replace("public class ThreeController", "public class ThreeController extends BaseController")
        self.repo({"files": tree})
        a = self.spring()
        table = a._table(self.sha("main"))
        self.assertFalse(table.complete)
        self.assertIn(("type_ambiguous", True), kinds_of(self))


class ProjectAnnotations(Base):
    API = f(["package com.acme.api;", "", "import org.springframework.web.bind.annotation.*;", "", "@RestController", '@RequestMapping("/api")', "public @interface Api {", "}"])
    PING = f(["package com.acme.api;", "", "import org.springframework.web.bind.annotation.GetMapping;", "", '@GetMapping("/fixed")', "public @interface Ping {", "    String[] value() default {};", "}"])

    def files(self, usage):
        return {"src/main/java/com/acme/api/Api.java": self.API, "src/main/java/com/acme/api/Ping.java": self.PING, "src/main/java/com/acme/api/Users.java": usage}

    def test_a_controller_that_uses_only_project_annotations_is_found(self):
        usage = f(["package com.acme.api;", "", "@Api", "public class Users {", "    @Ping", "    void ping() {}", "}"])
        self.repo({"files": self.files(usage)})
        self.assertEqual(self.labels(), [("GET /api/fixed", "Users.ping")])

    def test_the_path_of_the_project_annotation_is_used_when_the_usage_has_none_and_the_usage_wins_when_it_has_one(self):
        usage = f(["package com.acme.api;", "", "@Api", "public class Users {", "    @Ping", "    void ping() {}", '    @Ping("/mine")', "    void mine() {}", "}"])
        self.repo({"files": self.files(usage)})
        self.assertEqual(self.labels(), [("GET /api/fixed", "Users.ping"), ("GET /api/mine", "Users.mine")])
        r = self.one("GET /api/fixed")
        self.assertEqual([(e.path.rsplit("/", 1)[1], e.line) for e in r.evidence], [("Users.java", 5), ("Ping.java", 5), ("Users.java", 3), ("Api.java", 6)])

    def test_a_path_that_an_alias_attribute_may_set_is_partial(self):
        alias = f(["package com.acme.api;", "", "import org.springframework.core.annotation.AliasFor;", "import org.springframework.web.bind.annotation.*;", "", "@RestController", '@RequestMapping("/api")', "public @interface Api {",
                   '    @AliasFor(annotation = RequestMapping.class, attribute = "path")', "    String base() default \"\";", "}"])
        usage = f(["package com.acme.api;", "", '@Api(base = "/zz")', "public class Users {", "    @Ping", "    void ping() {}", "}"])
        files = self.files(usage)
        files["src/main/java/com/acme/api/Api.java"] = alias
        self.repo({"files": files})
        (r,) = self.routes()
        self.assertTrue(r.partial)
        (c,) = self.entry._table(self.sha("main")).composed()
        self.assertIn("AliasFor", " ".join(u[0] for u in c.unknown))

    def test_the_method_of_a_project_annotation_that_is_a_request_mapping_is_read(self):
        post = f(["package com.acme.api;", "", "import org.springframework.web.bind.annotation.*;", "", "@RequestMapping(path = \"/p\", method = RequestMethod.POST)", "public @interface Send {", "}"])
        usage = f(["package com.acme.api;", "", "@Api", "public class Users {", "    @Send", "    void send() {}", "}"])
        files = self.files(usage)
        files["src/main/java/com/acme/api/Send.java"] = post
        self.repo({"files": files})
        self.assertEqual(self.labels(), [("POST /api/p", "Users.send")])


class KotlinRoots(Base):
    def test_a_kotlin_file_whose_folder_omits_the_package_is_found_by_its_package_line(self):
        paths = "package com.acme.api\n\nobject Paths {\n    const val BASE = \"/v9\"\n}\n"
        ctl = ("package com.acme.api\n\nimport org.springframework.web.bind.annotation.GetMapping\nimport org.springframework.web.bind.annotation.RequestMapping\n"
               "import org.springframework.web.bind.annotation.RestController\n\n@RestController\n@RequestMapping(Paths.BASE)\nclass Customers {\n    @GetMapping(\"/c\")\n    fun c() = 1\n}\n")
        self.repo({"files": {"src/main/kotlin/Paths.kt": paths, "src/main/kotlin/Customers.kt": ctl}})
        self.assertEqual(self.labels(), [("GET /v9/c", "Customers.c")])
        self.assertEqual(self.language.files_in_package(self.sha("main"), "com.acme.api"), ["src/main/kotlin/Customers.kt", "src/main/kotlin/Paths.kt"])

    def test_a_java_file_in_the_wrong_folder_is_found_by_its_package_line_too(self):
        self.repo({"files": {"src/main/java/Paths.java": f(["package com.acme.api;", "", "public class Paths {", '    public static final String BASE = "/j";', "}"]),
                            PATH: controller("    @GetMapping(Paths.BASE)", "    void x() {}", head=("@RestController",))}})
        self.assertEqual(self.labels(), [("GET /j", "InvoiceController.x")])

    def test_an_index_that_could_not_be_made_is_said_when_the_folders_do_not_tell(self):
        self.repo({"files": {PATH: controller(*LIST)}})
        a = self.spring()
        with mock.patch.object(self.language, "search", wraps=self.language.search) as spy:
            spy.side_effect = lambda sha, pattern: None if pattern == "package " else self.language.__class__.search(self.language, sha, pattern)
            table = a._table(self.sha("main"))
            self.assertFalse(table.complete)


class ProfileDocuments(Base):
    def test_a_properties_document_separator_makes_a_profile_document(self):
        text = "server.servlet.context-path=/a\n#---\nspring.config.activate.on-profile=prod\nserver.servlet.context-path=/b\n"
        self.repo({"files": {PATH: controller(*LIST), APP: text}})
        (r,) = self.routes()
        self.assertEqual((r.path, r.partial), ("/<unknown:server.servlet.context-path>/invoices", True))

    def test_a_profile_document_with_the_same_value_changes_nothing(self):
        text = "server.servlet.context-path=/a\n#---\nspring.config.activate.on-profile=prod\nserver.servlet.context-path=/a\n"
        self.repo({"files": {PATH: controller(*LIST), APP: text}})
        (r,) = self.routes()
        self.assertEqual((r.path, r.partial), ("/a/invoices", False))

    def test_the_reader_gives_each_document_its_profile(self):
        text = "a=1\n#---\nspring.profiles=dev\na=2\n!---\nb=3\n"
        cfg = props.parse_properties(text, "application.properties")
        self.assertEqual([(e.key, e.value, e.profile) for e in cfg.entries], [("a", "1", None), ("a", "2", "dev"), ("b", "3", None)])

    def test_the_last_value_of_a_key_wins(self):
        self.repo({"files": {PATH: controller(*LIST), APP: "server.servlet.context-path=/a\nserver.servlet.context-path=/b\n"}})
        self.assertEqual([r.path for r in self.routes()], ["/b/invoices"])


class Combined(Base):
    def test_a_request_method_of_the_class_is_added_to_the_methods_of_a_handler(self):
        src = controller('    @GetMapping("/y")', "    void y() {}", head=("@RestController", '@RequestMapping(value = "/x", method = RequestMethod.POST)'))
        self.repo({"files": {PATH: src}})
        self.assertEqual(self.labels(), [("GET /x/y", "InvoiceController.y"), ("POST /x/y", "InvoiceController.y")])

    def test_a_handler_with_no_method_takes_the_methods_of_the_class(self):
        src = controller('    @RequestMapping("/y")', "    void y() {}", head=("@RestController", '@RequestMapping(value = "/x", method = RequestMethod.POST)'))
        self.repo({"files": {PATH: src}})
        self.assertEqual(self.labels(), [("POST /x/y", "InvoiceController.y")])


class Holes(Base):
    OTHER = "src/main/java/com/acme/api/Other.java"

    def other(self, *body):
        return controller(*body, name="Other", head=("@RestController", '@RequestMapping("/o")'))

    def test_only_the_routes_a_hole_may_hide_are_withheld(self):
        before = {PATH: controller(*LIST, *GET), self.OTHER: self.other("    @GetMapping", "    void a() {}", '    @GetMapping("/gone")', "    void gone() {}")}
        after = {PATH: controller(*LIST, '    String s = "oops;'), self.OTHER: self.other("    @GetMapping", "    void a() {}")}
        self.repo({"files": before}, {"files": after})
        removed, added = self.spring().routes_changed(self.sha("main~1"), self.sha("main"))
        self.assertEqual(([r.label for r in removed], added), (["GET /o/gone"], []))  # the routes of the file that does not parse are not claimed removed, the others are
        self.assertIn(("route_change_withheld", True), kinds_of(self))

    def test_a_route_that_moved_into_a_file_that_does_not_parse_is_not_claimed_removed(self):
        before = {PATH: controller(*LIST), self.OTHER: self.other("    @GetMapping", "    void a() {}")}
        after = {PATH: f(["package com.acme.api;", "", "class Gone {}"]), self.OTHER: self.other("    @GetMapping", "    void a() {}", "    String s = \"oops;", "    // InvoiceController moved here")}
        self.repo({"files": before}, {"files": after})
        removed, _added = self.spring().routes_changed(self.sha("main~1"), self.sha("main"))
        self.assertEqual(removed, [])

    def test_a_route_that_could_not_be_cited_makes_the_table_incomplete_and_is_not_dropped_silently(self):
        self.repo({"files": {PATH: controller(*LIST)}})
        a = self.spring()
        with mock.patch("reachmap.adapters.spring.table.evidence_at", return_value=None):
            table = a._table(self.sha("main"))
            routes = table.routes()
        self.assertEqual(routes, [])
        self.assertFalse(table.complete)

    def test_a_client_of_jakarta_rest_is_not_a_resource(self):
        client = f(["package com.acme.api;", "", "import jakarta.ws.rs.client.ClientBuilder;", "", "class Caller {}"])
        resource = f(["package com.acme.api;", "", "import jakarta.ws.rs.*;", "", "@Path(\"/r\")", "class Res {}"])
        self.repo({"files": {PATH: controller(*LIST), "src/main/java/com/acme/api/Caller.java": client}})
        self.assertTrue(self.spring()._table(self.sha("main")).complete)
        self.repo({"files": {PATH: controller(*LIST), "src/main/java/com/acme/api/Res.java": resource}})
        table = self.spring()._table(self.sha("main"))
        self.assertFalse(table.complete)
        self.assertIn(("route_style_unread", True), kinds_of(self))


class Advice(ReachBase):
    OTHER = "src/main/java/com/acme/other/OtherController.java"
    ADVICE = "src/main/java/com/acme/config/Advice.java"

    def advice(self, head="@RestControllerAdvice"):
        return f(["package com.acme.config;", "", "import org.springframework.web.bind.annotation.*;", "", head, "public class Advice {", "    @ExceptionHandler", "    String handle(Exception e) {", '        return "a";', "    }", "}"])

    def tree(self, advice):
        other = controller("    @GetMapping", "    void o() {}", pkg="com.acme.other", name="OtherController", head=("@RestController", '@RequestMapping("/o")'))
        return {PATH: controller(*LIST), self.OTHER: other, self.ADVICE: advice}

    def reached(self, head):
        before = self.advice(head)
        res = self.run_reach(self.tree(before), {self.ADVICE: before.replace('return "a";', 'return "b";')})
        return self.labels_of(), res

    def test_an_advice_with_no_scope_reaches_every_route(self):
        labels, res = self.reached("@RestControllerAdvice")
        self.assertEqual(labels, ["GET /invoices", "GET /o"])
        self.assertTrue(res.complete)
        self.assertNotIn("config_not_followed", [k for k, _b in self.kinds()])
        self.assertEqual([p for p, _l in self.chain_of("GET /o")][:1], ["Advice.java"])

    def test_an_advice_scoped_by_package_reaches_the_controllers_of_that_package(self):
        labels, res = self.reached('@RestControllerAdvice(basePackages = "com.acme.other")')
        self.assertEqual(labels, ["GET /o"])
        self.assertTrue(res.complete)

    def test_an_advice_scoped_by_type_reaches_that_controller(self):
        labels, _res = self.reached("@ControllerAdvice(assignableTypes = {InvoiceController.class})")
        self.assertEqual(labels, ["GET /invoices"])

    def test_an_advice_scoped_in_a_way_that_is_not_read_reaches_every_route(self):
        labels, _res = self.reached("@ControllerAdvice(annotations = RestController.class)")
        self.assertEqual(labels, ["GET /invoices", "GET /o"])

    def test_a_handler_of_exceptions_or_of_binding_inside_a_controller_reaches_every_route_of_it(self):
        for ann in ("@ExceptionHandler", "@InitBinder", "@ModelAttribute"):
            with self.subTest(ann=ann):
                body = ("    @GetMapping", "    public String list() {", '        return "x";', "    }", "", '    @GetMapping("/{id}")', "    String get() {", '        return "g";', "    }", "", f"    {ann}", "    void helper() {", "        int a = 1;", "    }")
                src = controller(*body)
                self.run_reach({PATH: src}, {PATH: src.replace("int a = 1;", "int a = 2;")})
                self.assertEqual(self.labels_of(), ["GET /invoices", "GET /invoices/{id}"])


class Wiring(ReachBase):
    def test_more_wiring_classes_are_not_followed(self):
        for name, body in (
            ("Auto", "@AutoConfiguration\npublic class Auto {\n    void f() { int a = 1; }\n}"),
            ("Boot", "@SpringBootConfiguration\npublic class Boot {\n    void f() { int a = 1; }\n}"),
            ("Methods", "@EnableMethodSecurity\npublic class Methods {\n    void f() { int a = 1; }\n}"),
            ("Global", "@EnableGlobalMethodSecurity\npublic class Global {\n    void f() { int a = 1; }\n}"),
            ("Runner", "public class Runner implements CommandLineRunner {\n    void f() { int a = 1; }\n}"),
            ("AppRunner", "public class AppRunner implements ApplicationRunner {\n    void f() { int a = 1; }\n}"),
            ("Errors", "public class Errors implements ErrorController {\n    void f() { int a = 1; }\n}"),
        ):
            with self.subTest(name=name):
                src = f(["package com.acme.config;", "", body])
                tree = {PATH: controller(*LIST), f"src/main/java/com/acme/config/{name}.java": src}
                res = self.run_reach(tree, {f"src/main/java/com/acme/config/{name}.java": src.replace("int a = 1;", "int a = 2;")})
                self.assertFalse(res.complete)
                self.assertIn(("config_not_followed", True), self.kinds())

    def test_the_test_folders_of_gradle_and_maven_integration_tests_are_tests(self):
        from reachmap.adapters.jvm import JvmAdapter

        self.repo({"files": {PATH: controller(*LIST)}})
        language = JvmAdapter(self.REPO, self.url, self.git)
        for p in ("a/src/integrationTest/java/A.java", "a/src/it/java/B.java", "a/src/test/java/C.java"):
            self.assertTrue(language.is_test_path(p), p)
        self.assertFalse(language.is_test_path("a/src/main/java/D.java"))


class Wording(unittest.TestCase):
    def test_no_ascii_arrow_in_a_comment_or_a_docstring_of_the_jvm_and_spring_packages(self):
        import ast
        import io
        import tokenize

        root = Path(__file__).resolve().parents[1] / "reachmap" / "adapters"
        seen = 0
        for pkg in ("jvm", "spring"):
            for path in sorted((root / pkg).glob("*.py")):
                seen += 1
                text = path.read_text(encoding="utf-8")
                for tok in tokenize.generate_tokens(io.StringIO(text).readline):
                    if tok.type == tokenize.COMMENT:
                        self.assertNotIn("->", tok.string, f"{path.name}:{tok.start[0]}")
                for node in ast.walk(ast.parse(text)):
                    if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                        self.assertNotIn("->", ast.get_docstring(node) or "", path.name)
        self.assertGreater(seen, 8)


class Lexer(unittest.TestCase):
    def test_a_unicode_escape_is_decoded(self):
        self.assertEqual(lexer.unescape(r"Abé"), "Abé")
        toks = lexer.tokenize('class A { String s = "\\u0041"; }', False).tokens
        (s,) = [t for t in toks if t.kind == "string"]
        self.assertEqual(lexer.unescape(s.text), "A")

    def test_a_kotlin_template_with_braces_inside_is_one_template_with_its_inner_tokens(self):
        res = lexer.tokenize('val s = "${ list.map { it } } and $x"\nval t = 1\n', True)
        self.assertEqual(res.problems, ())
        (s,) = [t for t in res.tokens if t.kind == "string"]
        self.assertEqual([k for k, _v in s.parts], ["expr", "lit", "expr"])
        self.assertEqual([t.value for t in res.tokens if t.kind == "ident" and t.line == 2], ["val", "t"])
        self.assertEqual([t.value for t in res.tokens if t.inner], ["list", ".", "map", "{", "it", "}", "x"])


if __name__ == "__main__":
    unittest.main()
