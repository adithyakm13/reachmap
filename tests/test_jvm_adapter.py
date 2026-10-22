"""The Java / Kotlin language adapter on small repositories built for one rule each: changed symbols placed by line (members, type
headers, Kotlin top-level declarations), importers by every form (import, static import, wildcard, same package, qualified name,
nested type), narrowing by member, generic names that are not followed, a class reached through its interface, tests as unit tests,
parse problems, and definitions.

The Spring scenarios are in test_scenarios_sb.py; the syntax reader has its own tests (test_jvm_syntax.py).
"""

import unittest

from reachmap.adapters.jvm import JvmAdapter, symbols_for_change
from tests.support_universe import Base as UniverseBase
from tests.support_universe import f

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
CONTROLLER = f([
    "package com.acme.api;", "",
    "import com.acme.service.InvoiceService;", "",
    "public class InvoiceController {",
    "    private final InvoiceService service;", "",
    "    public InvoiceController(InvoiceService service) {",
    "        this.service = service;",
    "    }", "",
    "    public int total(String id) {",
    "        return service.total(id);",
    "    }", "",
    "    public int count() {",
    "        return service.count();",
    "    }", "",
    "    public int other() {",
    "        return 3;",
    "    }",
    "}",
])


class Base(UniverseBase):
    UNIVERSE_PREFIX = "reachmap-jvm-"
    REPO = "acme/ledger"

    def make_adapter(self, **kw):
        kw.setdefault("test_markers", ["src/test/"])
        return JvmAdapter(self.REPO, self.url, self.git, **kw)

    def two(self, before, after, **kw):
        """A case: the files of `before`, then `after` on top of it (a mapping path to text; None deletes)."""
        delete = [p for p, t in after.items() if t is None]
        files = {p: t for p, t in after.items() if t is not None}
        self.repo({"files": before}, {"files": files, "delete": delete})

    def symbols(self, path):
        return sorted((s.name, s.change) for s in self.impact(path).symbols)

    def callers(self, path):
        return sorted((c.path, c.symbol, tuple(sorted({cs.line for cs in c.call_sites}))) for c in self.impact(path).callers)

    def excluded(self, path):
        return sorted(x.subject_path for x in self.impact(path).exclusions)


TREE = {
    "src/main/java/com/acme/service/InvoiceService.java": SERVICE,
    "src/main/java/com/acme/api/InvoiceController.java": CONTROLLER,
}


class ChangedSymbols(Base):
    def test_a_changed_line_in_a_method_is_that_member_and_not_its_siblings(self):
        self.two(TREE, {"src/main/java/com/acme/service/InvoiceService.java": SERVICE.replace("return 1;", "return 11;")})
        self.analyze()
        self.assertEqual(self.symbols("src/main/java/com/acme/service/InvoiceService.java"), [("InvoiceService.total", "modified")])
        (s,) = self.impact("src/main/java/com/acme/service/InvoiceService.java").symbols
        self.assertEqual((s.start, s.end, s.kind, s.exported, s.export_names, s.added_lines, s.removed_lines), (3, 11, "function", True, ("InvoiceService",), (5,), (5,)))

    def test_a_changed_annotation_or_header_line_is_the_type_itself(self):
        src = SERVICE.replace("public class InvoiceService {", "@Service\npublic class InvoiceService {")
        self.two(TREE, {"src/main/java/com/acme/service/InvoiceService.java": src})
        self.analyze()
        self.assertEqual(self.symbols("src/main/java/com/acme/service/InvoiceService.java"), [("InvoiceService", "modified")])

    def test_a_comment_between_members_changes_nothing_but_a_comment_inside_a_method_belongs_to_it(self):
        src = SERVICE.replace("    public int count() {", "    // the count\n    public int count() {").replace("        return 2;", "        // two\n        return 2;")
        self.two(TREE, {"src/main/java/com/acme/service/InvoiceService.java": src})
        self.analyze()
        self.assertEqual(self.symbols("src/main/java/com/acme/service/InvoiceService.java"), [("InvoiceService.count", "modified")])

    def test_an_added_method_is_a_modification_of_the_class_and_a_new_class_is_one_added_symbol(self):
        src = SERVICE.replace("    public int count() {", "    public int fresh() {\n        return 9;\n    }\n\n    public int count() {")
        extra = f(["package com.acme.service;", "", "public class Fresh {", "    int x() { return 1; }", "}"])
        self.two(TREE, {"src/main/java/com/acme/service/InvoiceService.java": src, "src/main/java/com/acme/service/Fresh.java": extra})
        self.analyze()
        self.assertEqual(self.symbols("src/main/java/com/acme/service/InvoiceService.java"), [("InvoiceService.fresh", "modified")])
        self.assertEqual(self.symbols("src/main/java/com/acme/service/Fresh.java"), [("<module>", "modified"), ("Fresh", "added")])  # the package line is module code

    def test_a_removed_method_is_placed_in_the_text_before(self):
        src = SERVICE.replace("    public int count() {\n        return 2;\n    }\n", "").replace("\n\n}", "\n}")
        self.two(TREE, {"src/main/java/com/acme/service/InvoiceService.java": src})
        self.analyze()
        self.assertEqual(self.symbols("src/main/java/com/acme/service/InvoiceService.java"), [("InvoiceService.count", "modified")])

    def test_an_import_line_is_a_module_level_change(self):
        src = CONTROLLER.replace("import com.acme.service.InvoiceService;", "import com.acme.service.InvoiceService;\nimport java.util.List;")
        self.two(TREE, {"src/main/java/com/acme/api/InvoiceController.java": src})
        self.analyze()
        self.assertEqual([(s.name, s.is_module) for s in self.impact("src/main/java/com/acme/api/InvoiceController.java").symbols], [("<module>", True)])

    def test_a_file_that_does_not_lex_is_one_module_level_change_and_a_parse_problem(self):
        src = SERVICE.replace("return 1;", 'return "oops;')
        self.two(TREE, {"src/main/java/com/acme/service/InvoiceService.java": src})
        self.analyze()
        imp = self.impact("src/main/java/com/acme/service/InvoiceService.java")
        self.assertEqual([(s.name, s.is_module) for s in imp.symbols], [("<module>", True)])
        self.assertEqual(self.kinds().count(("parse_degraded", False)), 1)

    def test_kotlin_top_level_and_members(self):
        kt = f([
            "package com.acme.util", "",
            "fun format(x: Int): String = x.toString()", "",
            "class Rounder(val step: Int) {",
            "    fun round(x: Int): Int {",
            "        return x - x % step",
            "    }",
            "}",
        ])
        self.two({"src/main/kotlin/com/acme/util/Util.kt": kt}, {"src/main/kotlin/com/acme/util/Util.kt": kt.replace("x - x % step", "x + 1").replace("x.toString()", "\"$x\"")})
        self.analyze()
        self.assertEqual(self.symbols("src/main/kotlin/com/acme/util/Util.kt"), [("Rounder.round", "modified"), ("format", "modified")])

    def test_a_symbol_function_cuts_without_a_repository(self):
        syms = symbols_for_change("r", "A.java", "class A {\n  void f() {\n    int a;\n  }\n  void g() {}\n}\n", "class A {\n  void f() {\n    int b;\n  }\n  void g() {}\n}\n",
                                  [3], [3], before_sha=None, after_sha=None)
        self.assertEqual([(s.name, s.change, s.added_lines, s.removed_lines) for s in syms], [("A.f", "modified", (3,), (3,))])


class Importers(Base):
    def importers(self, path, sha="main"):
        a = self.make_adapter()
        res = a.importers(path, self.sha(sha))
        return sorted((e.importer_path, e.kind, e.names, e.evidence.line, e.evidence.detail.strip()) for e in res.edges), res

    def test_an_import_by_package_and_class_name(self):
        self.repo({"files": TREE})
        got, res = self.importers("src/main/java/com/acme/service/InvoiceService.java")
        self.assertEqual(got, [("src/main/java/com/acme/api/InvoiceController.java", "import", ("InvoiceService",), 3, "import com.acme.service.InvoiceService;")])
        self.assertTrue(res.complete)

    def test_a_class_with_the_same_name_in_another_package_is_not_an_importer(self):
        other = f(["package com.other;", "", "public class InvoiceService {}"])
        user = f(["package com.acme.api;", "", "import com.other.InvoiceService;", "", "class X { InvoiceService s; }"])
        self.repo({"files": {**TREE, "src/main/java/com/other/InvoiceService.java": other, "src/main/java/com/acme/api/X.java": user}})
        got, _res = self.importers("src/main/java/com/acme/service/InvoiceService.java")
        self.assertEqual([g[0] for g in got], ["src/main/java/com/acme/api/InvoiceController.java"])
        got2, _ = self.importers("src/main/java/com/other/InvoiceService.java")
        self.assertEqual([g[0] for g in got2], ["src/main/java/com/acme/api/X.java"])

    def test_a_wildcard_import_counts_when_the_importer_uses_a_name_of_the_package(self):
        wild = f(["package com.acme.web;", "", "import com.acme.service.*;", "", "class W { InvoiceService s; }"])
        unused = f(["package com.acme.web;", "", "import com.acme.service.*;", "", "class U { int x; }"])
        self.repo({"files": {**TREE, "src/main/java/com/acme/web/W.java": wild, "src/main/java/com/acme/web/U.java": unused}})
        got, _res = self.importers("src/main/java/com/acme/service/InvoiceService.java")
        self.assertEqual([(g[0], g[1], g[3]) for g in got], [("src/main/java/com/acme/api/InvoiceController.java", "import", 3), ("src/main/java/com/acme/web/W.java", "import", 5)])

    def test_a_reference_in_the_same_package_needs_no_import(self):
        same = f(["package com.acme.service;", "", "class Report {", "    InvoiceService s = new InvoiceService();", "}"])
        self.repo({"files": {**TREE, "src/main/java/com/acme/service/Report.java": same}})
        got, _res = self.importers("src/main/java/com/acme/service/InvoiceService.java")
        self.assertIn(("src/main/java/com/acme/service/Report.java", "same_package", ("InvoiceService",), 4, "InvoiceService s = new InvoiceService();"), got)

    def test_a_same_package_class_is_shadowed_by_an_import_of_another_one_of_the_same_name(self):
        other = f(["package com.other;", "", "public class InvoiceService {}"])
        user = f(["package com.acme.service;", "", "import com.other.InvoiceService;", "", "class Report { InvoiceService s; }"])
        self.repo({"files": {**TREE, "src/main/java/com/other/InvoiceService.java": other, "src/main/java/com/acme/service/Report.java": user}})
        got, _res = self.importers("src/main/java/com/acme/service/InvoiceService.java")
        self.assertEqual([g[0] for g in got], ["src/main/java/com/acme/api/InvoiceController.java"])

    def test_a_fully_qualified_name_in_code(self):
        user = f(["package com.acme.web;", "", "class Q {", "    int f() { return com.acme.service.InvoiceService.helper(); }", "}"])
        self.repo({"files": {**TREE, "src/main/java/com/acme/web/Q.java": user}})
        got, _res = self.importers("src/main/java/com/acme/service/InvoiceService.java")
        self.assertIn(("src/main/java/com/acme/web/Q.java", "qualified", ("InvoiceService",), 4, "int f() { return com.acme.service.InvoiceService.helper(); }"), got)

    def test_a_static_import_and_a_nested_type_import(self):
        util = f(["package com.acme.util;", "", "public final class Money {", "    public static String format(int x) { return \"\" + x; }", "    public static class Cents { }", "}"])
        user = f(["package com.acme.web;", "", "import static com.acme.util.Money.format;", "import com.acme.util.Money.Cents;", "", "class U { String s = format(1); Cents c; }"])
        self.repo({"files": {"src/main/java/com/acme/util/Money.java": util, "src/main/java/com/acme/web/U.java": user}})
        got, _res = self.importers("src/main/java/com/acme/util/Money.java")
        self.assertEqual([(g[0], g[1], g[3]) for g in got], [("src/main/java/com/acme/web/U.java", "import", 4), ("src/main/java/com/acme/web/U.java", "static", 3)])

    def test_a_static_wildcard_import_counts_for_a_member_used_on_its_own(self):
        util = f(["package com.acme.util;", "", "public final class Money {", "    public static String format(int x) { return \"\" + x; }", "    public static int unused() { return 0; }", "}"])
        user = f(["package com.acme.web;", "", "import static com.acme.util.Money.*;", "", "class U { String s = format(1); }"])
        self.repo({"files": {"src/main/java/com/acme/util/Money.java": util, "src/main/java/com/acme/web/U.java": user}})
        a = self.make_adapter()
        res = a.importers("src/main/java/com/acme/util/Money.java", self.sha("main"))
        self.assertEqual([(e.kind, e.bindings[0].imported, e.evidence.line) for e in res.edges], [("static", "Money.format", 5)])

    def test_a_comment_or_a_string_that_names_the_class_is_not_an_import_or_a_use(self):
        ghost = f(["package com.acme.service;", "", "// InvoiceService is used elsewhere", "class Ghost {", '    String s = "InvoiceService";', "    /* new InvoiceService() */", "}"])
        self.repo({"files": {**TREE, "src/main/java/com/acme/service/Ghost.java": ghost}})
        got, _res = self.importers("src/main/java/com/acme/service/InvoiceService.java")
        self.assertEqual([g[0] for g in got], ["src/main/java/com/acme/api/InvoiceController.java"])

    def test_kotlin_imports_a_java_class_with_an_alias_and_java_imports_kotlin(self):
        kt = f(["package com.acme.api", "", "import com.acme.service.InvoiceService as Svc", "", "class K(private val s: Svc)"])
        util = f(["package com.acme.util", "", "fun fmt(x: Int) = x.toString()"])
        user = f(["package com.acme.web;", "", "import com.acme.util.UtilKt;", "", "class J { String s = UtilKt.fmt(1); }"])
        self.repo({"files": {**TREE, "src/main/kotlin/com/acme/api/K.kt": kt, "src/main/kotlin/com/acme/util/Util.kt": util, "src/main/java/com/acme/web/J.java": user}})
        got, _ = self.importers("src/main/java/com/acme/service/InvoiceService.java")
        self.assertIn(("src/main/kotlin/com/acme/api/K.kt", "import", ("InvoiceService",), 3, "import com.acme.service.InvoiceService as Svc"), got)
        got2, _ = self.importers("src/main/kotlin/com/acme/util/Util.kt")
        self.assertEqual([(g[0], g[2]) for g in got2], [("src/main/java/com/acme/web/J.java", ("UtilKt",))])

    def test_a_kotlin_top_level_function_by_import_and_by_wildcard(self):
        util = f(["package com.acme.util", "", "fun toMoney(x: Int) = x.toString()"])
        one = f(["package com.acme.web", "", "import com.acme.util.toMoney", "", "fun a() = 1.toMoney()"])
        wild = f(["package com.acme.web", "", "import com.acme.util.*", "", "fun b() = toMoney(2)"])
        self.repo({"files": {"src/main/kotlin/com/acme/util/Util.kt": util, "src/main/kotlin/com/acme/web/One.kt": one, "src/main/kotlin/com/acme/web/Wild.kt": wild}})
        got, _ = self.importers("src/main/kotlin/com/acme/util/Util.kt")
        self.assertEqual([(g[0], g[1], g[2]) for g in got], [("src/main/kotlin/com/acme/web/One.kt", "import", ("toMoney",)), ("src/main/kotlin/com/acme/web/Wild.kt", "import", ("toMoney",))])

    def test_a_function_name_defined_many_times_is_not_followed_through_a_wildcard(self):
        util = f(["package com.acme.util", "", "fun get(x: Int) = x"])
        wild = f(["package com.acme.web", "", "import com.acme.util.*", "", "fun b() = get(2)"])
        others = {f"src/main/kotlin/com/acme/o{i}/O.kt": f([f"package com.acme.o{i}", "", "fun get(x: String) = x"]) for i in range(4)}
        self.repo({"files": {"src/main/kotlin/com/acme/util/Util.kt": util, "src/main/kotlin/com/acme/web/Wild.kt": wild, **others}})
        a = self.make_adapter(distinct_max=3)
        res = a.importers("src/main/kotlin/com/acme/util/Util.kt", self.sha("main"))
        self.assertEqual(res.edges, ())
        self.assertIn(("not_distinctive", False), [(r.kind, r.blocks_selection) for r in self.records])
        self.assertEqual([r.subject for r in self.records if r.kind == "not_distinctive"], ["get"])
        # the same file with an explicit import is an importer whatever the name
        explicit = f(["package com.acme.web", "", "import com.acme.util.get", "", "fun b() = get(2)"])
        self.repo({"files": {"src/main/kotlin/com/acme/util/Util.kt": util, "src/main/kotlin/com/acme/web/Wild.kt": explicit, **others}})
        res = self.make_adapter(distinct_max=3).importers("src/main/kotlin/com/acme/util/Util.kt", self.sha("main"))
        self.assertEqual([e.importer_path for e in res.edges], ["src/main/kotlin/com/acme/web/Wild.kt"])

    def test_a_candidate_that_does_not_lex_makes_the_list_incomplete_and_says_so(self):
        broken = f(["package com.acme.web;", "", "import com.acme.service.InvoiceService;", "", 'class B { String s = "oops; }'])
        self.repo({"files": {**TREE, "src/main/java/com/acme/web/B.java": broken}})
        got, res = self.importers("src/main/java/com/acme/service/InvoiceService.java")
        self.assertFalse(res.complete)
        self.assertIn(("parse_degraded", True), self.kinds())
        self.assertEqual([g[0] for g in got], ["src/main/java/com/acme/api/InvoiceController.java"])

    def test_an_unreadable_candidate_is_not_an_absence(self):
        from unittest import mock

        from reachmap.model import ReadResult

        self.repo({"files": TREE})
        a = self.make_adapter()
        real = a._read

        def read(sha, path):
            if path.endswith("InvoiceController.java"):
                return ReadResult.unreadable("simulated failure")
            return real(sha, path)

        with mock.patch.object(a, "_read", read):
            res = a.importers("src/main/java/com/acme/service/InvoiceService.java", self.sha("main"))
        self.assertFalse(res.complete)
        self.assertEqual(res.edges, ())

    def test_tests_are_unit_tests_and_never_callers(self):
        test = f(["package com.acme.service;", "", "class InvoiceServiceTest {", "    InvoiceService s = new InvoiceService();", "}"])
        self.two({**TREE, "src/test/java/com/acme/service/InvoiceServiceTest.java": test},
                 {"src/main/java/com/acme/service/InvoiceService.java": SERVICE.replace("return 1;", "return 11;")})
        self.analyze()
        imp = self.impact("src/main/java/com/acme/service/InvoiceService.java")
        self.assertEqual([c.path for c in imp.callers], ["src/main/java/com/acme/api/InvoiceController.java"])
        self.assertEqual([u.path for u in imp.unit_tests], ["src/test/java/com/acme/service/InvoiceServiceTest.java"])


class Narrowing(Base):
    def test_an_importer_that_spells_the_changed_member_is_a_caller_and_one_that_spells_only_others_is_excluded(self):
        other = f(["package com.acme.api;", "", "import com.acme.service.InvoiceService;", "", "class Other {", "    InvoiceService s;", "    int c() { return s.count(); }", "}"])
        self.two({**TREE, "src/main/java/com/acme/api/Other.java": other},
                 {"src/main/java/com/acme/service/InvoiceService.java": SERVICE.replace("return 1;", "return 11;")})
        self.analyze()
        imp = self.impact("src/main/java/com/acme/service/InvoiceService.java")
        self.assertTrue(imp.narrowed)
        self.assertEqual(imp.reached_names, ("InvoiceService.total",))
        self.assertEqual([c.path for c in imp.callers], ["src/main/java/com/acme/api/InvoiceController.java"])
        self.assertEqual([x.subject_path for x in imp.exclusions], ["src/main/java/com/acme/api/Other.java"])
        self.assertEqual(self.callers("src/main/java/com/acme/service/InvoiceService.java"), [("src/main/java/com/acme/api/InvoiceController.java", "InvoiceController.total", (13,))])

    def test_a_member_that_calls_the_changed_one_in_the_changed_file_widens_the_set(self):
        src = SERVICE.replace("        return 2;", "        return total(\"x\") + 1;")
        self.two({**TREE, "src/main/java/com/acme/api/Z.java": f(["package com.acme.api;", "", "import com.acme.service.InvoiceService;", "", "class Z { int c() { return svc.count(); } InvoiceService svc; }"])},
                 {"src/main/java/com/acme/service/InvoiceService.java": src.replace("return 1;", "return 11;")})
        self.analyze()
        imp = self.impact("src/main/java/com/acme/service/InvoiceService.java")
        self.assertEqual(imp.reached_names, ("InvoiceService.count", "InvoiceService.total"))
        self.assertEqual(sorted(c.path for c in imp.callers), ["src/main/java/com/acme/api/InvoiceController.java", "src/main/java/com/acme/api/Z.java"])

    def test_a_class_level_change_reaches_every_importer(self):
        other = f(["package com.acme.api;", "", "import com.acme.service.InvoiceService;", "", "class Other {", "    InvoiceService s;", "    int c() { return s.count(); }", "}"])
        self.two({**TREE, "src/main/java/com/acme/api/Other.java": other},
                 {"src/main/java/com/acme/service/InvoiceService.java": SERVICE.replace("public class InvoiceService {", "@Transactional\npublic class InvoiceService {")})
        self.analyze()
        imp = self.impact("src/main/java/com/acme/service/InvoiceService.java")
        self.assertEqual(sorted(c.path for c in imp.callers), ["src/main/java/com/acme/api/InvoiceController.java", "src/main/java/com/acme/api/Other.java"])
        self.assertEqual(imp.exclusions, ())

    def test_a_generic_member_name_is_not_linked_by_name_only_by_the_type(self):
        """`get` is defined five times: an importer that spells `.get` of a map is not reached by it; one that uses the type is."""
        svc = f(["package com.acme.service;", "", "public class Cache {", "    public int get(String k) {", "        return 1;", "    }", "}"])
        uses_type = f(["package com.acme.api;", "", "import com.acme.service.Cache;", "", "class A { Cache c; int f() { return c.get(\"k\"); } }"])
        maps_only = f(["package com.acme.api;", "", "import com.acme.service.Cache;", "", "class B { java.util.Map<String, Integer> m; Cache unused; int f() { return m.get(\"k\"); } }"])
        others = {f"src/main/java/com/acme/o{i}/O.java": f([f"package com.acme.o{i};", "", "class O {", "    int get() { return 0; }", "}"]) for i in range(5)}
        tree = {"src/main/java/com/acme/service/Cache.java": svc, "src/main/java/com/acme/api/A.java": uses_type, "src/main/java/com/acme/api/B.java": maps_only, **others}
        self.two(tree, {"src/main/java/com/acme/service/Cache.java": svc.replace("return 1;", "return 2;")})
        a = self.make_adapter(distinct_max=3)
        self.analysis = a.analyze(self.change())
        imp = self.impact("src/main/java/com/acme/service/Cache.java")
        self.assertEqual(sorted(c.path for c in imp.callers), ["src/main/java/com/acme/api/A.java", "src/main/java/com/acme/api/B.java"])  # both import it: the type is the link
        self.assertEqual(imp.not_distinctive, ("get",))
        # the sites of the importer that has the type are the uses of the type, never the `.get` of the map
        b = next(c for c in imp.callers if c.path.endswith("B.java"))
        self.assertEqual([(cs.symbol, cs.line) for cs in b.call_sites], [("Cache", 5)])

    def test_a_distinctive_member_is_found_by_its_name_through_any_receiver(self):
        user = f(["package com.acme.api;", "", "import com.acme.service.InvoiceService;", "", "class A {", "    InvoiceService c;", "    int f() { return c.total(\"k\"); }", "    int g() { return c.count(); }", "}"])
        self.two({**TREE, "src/main/java/com/acme/api/A.java": user},
                 {"src/main/java/com/acme/service/InvoiceService.java": SERVICE.replace("return 1;", "return 11;")})
        self.analyze(self.make_adapter(distinct_max=3))
        a = next(c for c in self.impact("src/main/java/com/acme/service/InvoiceService.java").callers if c.path.endswith("A.java"))
        self.assertEqual([(cs.symbol, cs.line, cs.caller_symbol, cs.args) for cs in a.call_sites], [("InvoiceService.total", 7, "A.f", ('"k"',))])

    def test_without_a_pre_change_version_nothing_is_narrowed(self):
        other = f(["package com.acme.api;", "", "import com.acme.service.InvoiceService;", "", "class Other {", "    InvoiceService s;", "}"])
        self.two({**TREE, "src/main/java/com/acme/api/Other.java": other},
                 {"src/main/java/com/acme/service/InvoiceService.java": SERVICE.replace("return 1;", "return 11;")})
        self.analyze(pre=None)
        imp = self.impact("src/main/java/com/acme/service/InvoiceService.java")
        self.assertFalse(imp.narrowed)
        self.assertEqual(len(imp.callers), 2)

    def test_reflection_in_the_changed_file_stops_narrowing(self):
        src = SERVICE.replace("        return 2;", "        return Class.forName(\"x\").hashCode();")
        other = f(["package com.acme.api;", "", "import com.acme.service.InvoiceService;", "", "class Other {", "    InvoiceService s;", "}"])
        self.two({**TREE, "src/main/java/com/acme/api/Other.java": other}, {"src/main/java/com/acme/service/InvoiceService.java": src.replace("return 1;", "return 11;")})
        self.analyze()
        self.assertFalse(self.impact("src/main/java/com/acme/service/InvoiceService.java").narrowed)

    def test_a_static_initializer_that_calls_a_reached_member_reaches_the_whole_class(self):
        src = f([
            "package com.acme.service;", "",
            "public class Rates {",
            "    static final int RATE = load();",
            "    public static int load() { return 1; }",
            "    public static int other() { return 2; }",
            "}",
        ])
        user = f(["package com.acme.api;", "", "import static com.acme.service.Rates.other;", "", "class U { int f() { return other(); } }"])
        self.two({"src/main/java/com/acme/service/Rates.java": src, "src/main/java/com/acme/api/U.java": user},
                 {"src/main/java/com/acme/service/Rates.java": src.replace("return 1;", "return 3;")})
        self.analyze()
        imp = self.impact("src/main/java/com/acme/service/Rates.java")
        self.assertEqual(imp.reached_names, ("Rates",))  # the static field runs `load()` when the class loads: every importer is reached
        self.assertEqual([c.path for c in imp.callers], ["src/main/java/com/acme/api/U.java"])


class Dispatch(Base):
    IFACE = f(["package com.acme.service;", "", "public interface Pricing {", "    int price(String id);", "    int tax(String id);", "}"])
    IMPL = f([
        "package com.acme.service;", "",
        "public class PricingImpl implements Pricing {",
        "    public int price(String id) {",
        "        return 1;",
        "    }", "",
        "    public int tax(String id) {",
        "        return 2;",
        "    }", "",
        "    int helper() {",
        "        return 3;",
        "    }",
        "}",
    ])
    USER = f(["package com.acme.api;", "", "import com.acme.service.Pricing;", "", "class User {", "    Pricing p;", "    int a() { return p.price(\"x\"); }", "    int b() { return p.tax(\"x\"); }", "}"])
    TREE = {
        "src/main/java/com/acme/service/Pricing.java": IFACE, "src/main/java/com/acme/service/PricingImpl.java": IMPL, "src/main/java/com/acme/api/User.java": USER,
    }

    def test_a_change_in_the_class_reaches_the_importers_of_its_interface_that_spell_the_member(self):
        self.two(self.TREE, {"src/main/java/com/acme/service/PricingImpl.java": self.IMPL.replace("return 1;", "return 11;")})
        self.analyze(self.make_adapter(distinct_max=3))
        imp = self.impact("src/main/java/com/acme/service/PricingImpl.java")
        self.assertEqual(imp.reached_names, ("Pricing.price", "PricingImpl.price"))
        (c,) = imp.callers
        self.assertEqual((c.path, [(cs.symbol, cs.line, cs.caller_symbol) for cs in c.call_sites]), ("src/main/java/com/acme/api/User.java", [("Pricing.price", 7, "User.a")]))

    def test_a_member_the_interface_does_not_declare_reaches_nobody_through_it(self):
        self.two(self.TREE, {"src/main/java/com/acme/service/PricingImpl.java": self.IMPL.replace("return 3;", "return 33;")})
        self.analyze(self.make_adapter(distinct_max=3))
        imp = self.impact("src/main/java/com/acme/service/PricingImpl.java")
        self.assertEqual(imp.callers, ())
        self.assertEqual(imp.reached_names, ("PricingImpl.helper",))

    def test_a_class_level_change_reaches_every_importer_of_the_interface(self):
        self.two(self.TREE, {"src/main/java/com/acme/service/PricingImpl.java": self.IMPL.replace("public class", "@Primary\npublic class")})
        self.analyze(self.make_adapter(distinct_max=3))
        imp = self.impact("src/main/java/com/acme/service/PricingImpl.java")
        self.assertEqual([c.path for c in imp.callers], ["src/main/java/com/acme/api/User.java"])
        self.assertEqual(sorted(cs.line for c in imp.callers for cs in c.call_sites), [6])  # the use of the type: every use of it counts

    def test_a_test_that_imports_only_the_interface_is_not_a_unit_test_of_the_class(self):
        test = f(["package com.acme.service;", "", "class PricingTest { Pricing p; }"])
        self.two({**self.TREE, "src/test/java/com/acme/service/PricingTest.java": test}, {"src/main/java/com/acme/service/PricingImpl.java": self.IMPL.replace("return 1;", "return 11;")})
        self.analyze(self.make_adapter(distinct_max=3))
        self.assertEqual(self.impact("src/main/java/com/acme/service/PricingImpl.java").unit_tests, ())

    def test_a_supertype_that_is_not_in_the_repository_is_ignored(self):
        impl = self.IMPL.replace("implements Pricing", "implements org.springframework.Ordered")
        self.two({**self.TREE, "src/main/java/com/acme/service/PricingImpl.java": impl}, {"src/main/java/com/acme/service/PricingImpl.java": impl.replace("return 1;", "return 11;")})
        self.analyze(self.make_adapter(distinct_max=3))
        self.assertEqual(self.impact("src/main/java/com/acme/service/PricingImpl.java").callers, ())


class Rules(Base):
    def test_more_importers_than_the_hub_limit_are_not_walked_and_the_hub_is_recorded(self):
        users = {f"src/main/java/com/acme/api/U{i}.java": f(["package com.acme.api;", "", "import com.acme.service.InvoiceService;", "", f"class U{i} {{ InvoiceService s; }}"]) for i in range(4)}
        self.two({SVC: SERVICE, **users}, {SVC: SERVICE.replace("return 1;", "return 11;")})
        a = self.make_adapter(hub_importers=3)
        self.analysis = a.analyze(self.change())
        imp = self.impact(SVC)
        self.assertTrue(imp.hub)
        self.assertEqual(imp.callers, ())
        self.assertIn(("hub_cutoff", False), self.kinds())
        self.assertEqual([r.subject for r in self.records if r.kind == "hub_cutoff"], [SVC])

    def test_importers_of_a_deleted_file_are_searched_at_the_pre_change_commit(self):
        self.two(TREE, {SVC: None})
        self.analyze()
        imp = self.impact(SVC)
        self.assertEqual([(s.name, s.change) for s in imp.symbols if not s.is_module], [("InvoiceService", "removed")])
        self.assertEqual([c.path for c in imp.callers], ["src/main/java/com/acme/api/InvoiceController.java"])
        self.assertEqual(imp.callers[0].evidence[0].ref, self.sha("main~1"))  # the import is read where it was

    def test_a_class_declared_twice_keeps_both_files_and_says_so(self):
        dup = f(["package com.acme.service;", "", "public class InvoiceService {", "    public int total(String id) { return 9; }", "}"])
        self.repo({"files": {**TREE, "src/other/java/com/acme/service/InvoiceService.java": dup}})
        a = self.make_adapter()
        res = a.importers(SVC, self.sha("main"))
        self.assertEqual([e.importer_path for e in res.edges], ["src/main/java/com/acme/api/InvoiceController.java"])
        res2 = a.importers("src/other/java/com/acme/service/InvoiceService.java", self.sha("main"))
        self.assertEqual([e.importer_path for e in res2.edges], ["src/main/java/com/acme/api/InvoiceController.java"])
        self.assertIn(("type_ambiguous", False), self.kinds())

    def test_a_removed_import_reaches_the_members_that_spell_the_name_it_bound(self):
        src = f(["package p;", "", "import java.util.List;", "", "public class C {", "    int a() { return List.of().size(); }", "    int b() { return 2; }", "}"])
        user = f(["package p;", "", "class U { C c; int f() { return c.a() + c.b(); } }"])
        self.two({"src/main/java/p/C.java": src, "src/main/java/p/U.java": user}, {"src/main/java/p/C.java": src.replace("import java.util.List;", "import java.util.*;")})
        self.analyze()
        imp = self.impact("src/main/java/p/C.java")
        self.assertEqual([s.name for s in imp.symbols], ["<module>"])
        self.assertFalse(imp.narrowed)  # a wildcard import: which names it binds is not told

    def test_an_import_that_binds_a_name_a_member_spells_is_followed_by_that_name(self):
        src = f(["package p;", "", "import java.util.List;", "import java.util.Map;", "", "public class C {", "    int a() { return List.of().size(); }", "    int b() { return 2; }", "}"])
        user = f(["package p;", "", "class U { C c; int f() { return c.a() + c.b(); } }"])
        self.two({"src/main/java/p/C.java": src, "src/main/java/p/U.java": user}, {"src/main/java/p/C.java": src.replace("import java.util.List;\n", "")})
        self.analyze(self.make_adapter(distinct_max=5))
        imp = self.impact("src/main/java/p/C.java")
        self.assertTrue(imp.narrowed)
        self.assertEqual(imp.reached_names, ("C.a",))
        self.assertEqual(imp.callers[0].call_sites[0].symbol, "C.a")

    def test_an_import_that_is_never_used_makes_the_whole_module_reached(self):
        user = f(["package com.acme.api;", "", "import com.acme.service.InvoiceService;", "", "class U { int f() { return 1; } }"])
        self.repo({"files": {**TREE, "src/main/java/com/acme/api/U.java": user}})
        a = self.make_adapter()
        res = a.importers(SVC, self.sha("main"))
        edges = [e for e in res.edges if e.importer_path.endswith("U.java")]
        sites = a.sites_for("src/main/java/com/acme/api/U.java", edges, self.sha("main"), None)
        self.assertEqual([(c.form, c.caller_symbol) for c in sites], [("import", "<module>")])
        reach = a.reach("src/main/java/com/acme/api/U.java", edges, self.sha("main"), None, sites)
        self.assertTrue(reach.whole)
        self.assertIsNone(reach.names)

    def test_a_kotlin_constructor_property_carries_the_change_to_the_members_that_use_it(self):
        kt = f(["package com.acme.api", "", "import com.acme.service.InvoiceService", "", "class K(private val svc: InvoiceService, val label: String) {", "    fun a() = svc.total(\"x\")", "    fun b() = label", "}"])
        self.two({**TREE, "src/main/kotlin/com/acme/api/K.kt": kt}, {SVC: SERVICE.replace("return 1;", "return 11;")})
        a = self.make_adapter(distinct_max=5)
        self.analysis = a.analyze(self.change())
        imp = self.impact(SVC)
        k = next(c for c in imp.callers if c.path.endswith("K.kt"))
        reach = a.reach("src/main/kotlin/com/acme/api/K.kt", [e for e in imp.importers if e.importer_path == k.path], self.sha("main"), frozenset(imp.reached_names), k.call_sites)
        self.assertEqual(sorted(reach.locals), ["K.a"])

    def test_a_renamed_file_has_the_removed_class_and_the_added_one_and_its_importers_are_found_at_the_old_path(self):
        new = "src/main/java/com/acme/service/InvoiceServiceRenamed.java"
        self.two(TREE, {SVC: None, new: SERVICE.replace("InvoiceService", "InvoiceServiceRenamed")})
        self.analyze()
        imp = self.impact(new)
        self.assertEqual(imp.status, "renamed")
        self.assertEqual([(s.name, s.change) for s in imp.symbols], [("InvoiceService", "removed"), ("InvoiceServiceRenamed", "added")])
        self.assertEqual([c.path for c in imp.callers], ["src/main/java/com/acme/api/InvoiceController.java"])
        self.assertTrue(imp.importers_complete)


SVC = "src/main/java/com/acme/service/InvoiceService.java"


class Definitions(Base):
    def test_definitions_count_types_methods_fields_and_not_uses(self):
        a = f(["package p;", "", "class A {", "    int get() { return get2(); }", "    int get2() { return 1; }", "}"])
        b = f(["package p;", "", "class B {", "    int x = new A().get();", "    int get;", "}"])
        test = f(["package p;", "", "class T { int get() { return 0; } }"])
        self.repo({"files": {"src/main/java/p/A.java": a, "src/main/java/p/B.java": b, "src/test/java/p/T.java": test}})
        adapter = self.make_adapter()
        found = adapter.definitions("get", self.sha("main"))
        self.assertEqual((found.state, found.count, found.complete), ("found", 2, True))  # A.get and B.get; the test's is not counted; uses are not
        self.assertEqual(adapter.definitions("A", self.sha("main")).count, 1)
        self.assertEqual(adapter.definitions("nothing", self.sha("main")).count, 0)

    def test_a_file_that_does_not_lex_makes_the_count_a_lower_bound(self):
        a = f(["package p;", "", "class A {", "    int get() { return 1; }", "}"])
        broken = f(["package p;", "", 'class B { String s = "oops; int get() { return 1; } }'])
        self.repo({"files": {"src/main/java/p/A.java": a, "src/main/java/p/B.java": broken}})
        found = self.make_adapter().definitions("get", self.sha("main"))
        self.assertEqual((found.count, found.complete), (1, False))
        self.assertFalse(found.distinctive(3))


class Tests(Base):
    def test_test_paths_by_the_marker(self):
        self.repo({"files": TREE})
        a = self.make_adapter()
        self.assertTrue(a.is_test_path("src/test/java/p/ATest.java"))
        self.assertTrue(a.is_test_path("service/src/test/kotlin/p/ATest.kt"))
        self.assertFalse(a.is_test_path("src/main/java/p/Contest.java"))
        self.assertFalse(a.is_test_path("src/testing/java/p/A.java"))


if __name__ == "__main__":
    unittest.main()
