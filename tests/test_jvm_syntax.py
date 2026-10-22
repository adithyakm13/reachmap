"""The Java / Kotlin syntax facts: package and imports, declarations and members with their spans, placement of a line, annotations
and their arguments, supertypes, what a declaration spells, uses of a name, definitions, and what makes a file a parse problem."""

import time
import unittest

from reachmap.adapters.jvm.syntax import MODULE, parse_java, parse_kotlin


def lines(*rows):
    return "\n".join(rows) + "\n"


JAVA = lines(
    "package com.acme.billing;",                      # 1
    "",                                               # 2
    "import java.util.List;",                         # 3
    "import static com.acme.util.Money.format;",      # 4
    "import com.acme.model.*;",                       # 5
    "import static com.acme.util.Strings.*;",         # 6
    "",                                               # 7
    "/** The controller. */",                         # 8
    '@RestController',                                # 9
    '@RequestMapping("/invoices")',                   # 10
    "public class InvoiceController extends Base implements Auditable, Comparable<InvoiceController> {",  # 11
    "    private final InvoiceService service;",      # 12
    "    static final String NAME = \"x\", OTHER = \"y\";",  # 13
    "",                                               # 14
    "    public InvoiceController(InvoiceService service) {",  # 15
    "        this.service = service;",                # 16
    "    }",                                          # 17
    "",                                               # 18
    "    // between members",                         # 19
    '    @GetMapping("/{id}")',                       # 20
    "    public Invoice get(@PathVariable String id) {",  # 21
    "        return service.find(id);",               # 22
    "    }",                                          # 23
    "",                                               # 24
    "    static { init(); }",                         # 25
    "",                                               # 26
    "    public static class Inner {",                # 27
    "        int deep() { return 1; }",               # 28
    "    }",                                          # 29
    "}",                                              # 30
)


class JavaDeclarations(unittest.TestCase):
    def setUp(self):
        self.p = parse_java(JAVA)

    def test_it_parses_cleanly(self):
        self.assertEqual(self.p.problems, ())

    def test_package_and_imports(self):
        self.assertEqual(self.p.package, "com.acme.billing")
        got = [(r.kind, r.module, r.line) for r in self.p.imports]
        self.assertEqual(got, [
            ("import", "java.util.List", 3), ("static", "com.acme.util.Money.format", 4), ("wildcard", "com.acme.model", 5), ("static_wildcard", "com.acme.util.Strings", 6),
        ])
        self.assertEqual([r.specifier for r in self.p.imports][2:], ["com.acme.model.*", "com.acme.util.Strings.*"])

    def test_the_type_spans_from_its_first_annotation_to_its_closing_brace(self):
        (d,) = self.p.declarations
        self.assertEqual((d.kind, d.name, d.start, d.end, d.header_end), ("class", "InvoiceController", 9, 30, 11))
        self.assertEqual([a.name for a in d.annotations], ["RestController", "RequestMapping"])
        self.assertEqual([(s.name, s.kind) for s in d.supertypes], [("Base", "extends"), ("Auditable", "implements"), ("Comparable", "implements")])

    def test_members(self):
        (d,) = self.p.declarations
        got = [(c.kind, c.name, c.start, c.end) for c in d.children]
        self.assertEqual(got, [
            ("field", "service", 12, 12), ("field", "NAME", 13, 13), ("constructor", "InvoiceController", 15, 17), ("method", "get", 20, 23),
            ("init", "<init>", 25, 25), ("class", "Inner", 27, 29),
        ])
        self.assertEqual(d.children[1].names, ("NAME", "OTHER"))
        inner = d.children[-1]
        self.assertEqual([(c.kind, c.name, c.dotted) for c in inner.children], [("method", "deep", "InvoiceController.Inner.deep")])

    def test_a_method_span_holds_its_annotation_and_a_comment_between_members_is_nothing(self):
        got = {n: [(p.name, p.whole) for p in self.p.placements(n)] for n in (9, 11, 12, 14, 19, 20, 21, 22, 23, 25, 28, 29, 30)}
        self.assertEqual(got[9], [("InvoiceController", True)])
        self.assertEqual(got[11], [("InvoiceController", True)])
        self.assertEqual(got[12], [("InvoiceController.service", False)])
        self.assertEqual(got[14], [])  # a blank line between members
        self.assertEqual(got[19], [])  # a comment between members
        for n in (20, 21, 22, 23):
            self.assertEqual(got[n], [("InvoiceController.get", False)], n)
        self.assertEqual(got[25], [("InvoiceController.<init>", False)])
        self.assertEqual(got[28], [("InvoiceController.Inner.deep", False)])
        self.assertEqual(got[29], [("InvoiceController.Inner", True)])  # a closing brace of the nested type
        self.assertEqual(got[30], [("InvoiceController", True)])

    def test_a_line_outside_every_declaration_is_module_code_when_it_holds_code(self):
        self.assertEqual(sorted(self.p.code_lines), [1, 3, 4, 5, 6])
        self.assertEqual(self.p.enclosing(22), "InvoiceController.get")
        self.assertEqual(self.p.enclosing(1), MODULE)
        self.assertEqual(self.p.enclosing(11), "InvoiceController")

    def test_what_a_unit_spells_does_not_include_its_own_name(self):
        get = self.p.unit_of("InvoiceController.get")[0]
        spelled = self.p.spelled(get)
        self.assertIn("service", spelled)
        self.assertIn("find", spelled)
        self.assertIn("GetMapping", spelled)
        self.assertNotIn("get", spelled)

    def test_annotation_arguments_are_token_ranges(self):
        (d,) = self.p.declarations
        rm = d.annotations[1]
        self.assertEqual([(n, self.p.source(a, b - 1)) for n, a, b in rm.args], [(None, '"/invoices"')])

    def test_top_names(self):
        self.assertEqual(self.p.top_names, ("InvoiceController",))


class JavaForms(unittest.TestCase):
    def test_interfaces_enums_records_annotations_and_generics(self):
        src = lines(
            "package p;",
            "import java.util.*;",
            "public interface Repo<T extends Comparable<T>> extends Base<T> {",
            "    Optional<T> find(String id);",
            "    default int count() { return 0; }",
            "    int LIMIT = 5;",
            "}",
            "enum Color { RED, GREEN(1) { void f() {} }, BLUE; private int x; Color() {} int g() { return x; } }",
            "record Point(int x, List<String> names) implements Shape { Point { if (x < 0) throw new IllegalArgumentException(); } int sum() { return x; } }",
            "@interface Marker { String value() default \"a\"; }",
            "abstract class A { abstract void f(); native int g(); @Override public String toString() { return \"\"; } }",
        )
        p = parse_java(src)
        self.assertEqual(p.problems, ())
        by = {d.name: d for d in p.declarations}
        self.assertEqual([(c.kind, c.name) for c in by["Repo"].children], [("method", "find"), ("method", "count"), ("field", "LIMIT")])
        self.assertEqual([(s.name, s.kind) for s in by["Repo"].supertypes], [("Base", "extends")])
        self.assertEqual(by["Color"].kind, "enum")
        self.assertEqual([(c.kind, c.name) for c in by["Color"].children], [("enum_constant", "RED"), ("enum_constant", "GREEN"), ("enum_constant", "BLUE"), ("field", "x"), ("constructor", "Color"), ("method", "g")])
        self.assertEqual(by["Point"].kind, "record")
        self.assertEqual([(c.kind, c.name, c.in_header) for c in by["Point"].children][:2], [("field", "x", True), ("field", "names", True)])
        self.assertEqual([c.kind for c in by["Point"].children][2:], ["constructor", "method"])
        self.assertEqual(by["Marker"].kind, "annotation")
        self.assertEqual([(c.kind, c.name) for c in by["A"].children], [("method", "f"), ("method", "g"), ("method", "toString")])

    def test_several_top_level_types_in_one_file(self):
        p = parse_java("package p;\nclass A {}\nclass B {\n  void f() {}\n}\n")
        self.assertEqual([(d.name, d.start, d.end) for d in p.declarations], [("A", 2, 2), ("B", 3, 5)])
        self.assertEqual(p.top_names, ("A", "B"))

    def test_generic_methods_arrays_varargs_and_throws(self):
        src = lines(
            "class A {",
            "    public <T extends List<String>> Map<String, List<T>> f(T[] a, String... rest) throws IOException, X { return null; }",
            "    int[] arr = {1, 2, 3};",
            "    int[][] grid() { return null; }",
            "    Runnable r = () -> { run(); };",
            "    Object anon = new Object() { void h() {} };",
            "}",
        )
        p = parse_java(src)
        self.assertEqual(p.problems, ())
        (a,) = p.declarations
        self.assertEqual([(c.kind, c.name) for c in a.children], [("method", "f"), ("field", "arr"), ("method", "grid"), ("field", "r"), ("field", "anon")])

    def test_a_text_block_and_strings_with_braces_do_not_end_a_member(self):
        src = lines(
            "class A {",
            '    String q = """',
            "        class B { void f() {",
            '    """;',
            '    String s = "}";',
            "    void g() { char c = '}'; }",
            "}",
        )
        p = parse_java(src)
        self.assertEqual(p.problems, ())
        (a,) = p.declarations
        self.assertEqual([(c.kind, c.name, c.start, c.end) for c in a.children], [("field", "q", 2, 4), ("field", "s", 5, 5), ("method", "g", 6, 6)])

    def test_a_comment_that_looks_like_code_is_not_a_declaration(self):
        p = parse_java("class A {\n  // void ghost() {}\n  /* int hidden; */\n  void real() {}\n}\n")
        (a,) = p.declarations
        self.assertEqual([c.name for c in a.children], ["real"])

    def test_two_declarations_on_one_line_are_both_placed(self):
        p = parse_java("class A {\n  int a; int b;\n}\n")
        self.assertEqual([x.name for x in p.placements(2)], ["A.a", "A.b"])


class JavaProblems(unittest.TestCase):
    def test_an_unterminated_string_comment_or_text_block_is_a_parse_problem(self):
        for src in ('class A { String s = "oops; }', "class A { /* never closed }", 'class A { String t = """ never closed }'):
            with self.subTest(src=src):
                p = parse_java(src)
                self.assertTrue(p.problems)
                self.assertEqual((p.declarations, p.imports), ((), ()))

    def test_an_unbalanced_brace_is_a_parse_problem(self):
        self.assertTrue(parse_java("class A {\n void f() {\n}\n").problems)
        self.assertTrue(parse_java("class A { } }").problems)

    def test_a_file_with_nothing_is_not_a_problem(self):
        p = parse_java("")
        self.assertEqual((p.problems, p.declarations), ((), ()))
        self.assertEqual(parse_java("// only a comment\n").problems, ())


KOTLIN = lines(
    "package com.acme.billing",                                   # 1
    "",                                                           # 2
    "import com.acme.service.InvoiceService",                     # 3
    "import com.acme.util.format as fmt",                         # 4
    "import com.acme.util.*",                                     # 5
    "",                                                           # 6
    "const val BASE = \"/customers\"",                            # 7
    "",                                                           # 8
    "fun helper(x: Int): Int = x + 1",                            # 9
    "",                                                           # 10
    "@RestController",                                            # 11
    "@RequestMapping(BASE)",                                      # 12
    "class CustomerController(",                                  # 13
    "    private val service: InvoiceService,",                   # 14
    "    val name: String,",                                      # 15
    ") : Base(), Auditable {",                                    # 16
    "    @GetMapping(\"/{id}\")",                                 # 17
    "    fun get(@PathVariable id: String): Customer {",          # 18
    "        return Customer(\"$id\", \"${service.find(id)}\")",  # 19
    "    }",                                                      # 20
    "",                                                           # 21
    "    @PostMapping",                                           # 22
    "    fun create(body: Customer) = service.save(body)",        # 23
    "",                                                           # 24
    "    val count: Int",                                         # 25
    "        get() = 3",                                          # 26
    "",                                                           # 27
    "    init { println(\"x\") }",                                # 28
    "",                                                           # 29
    "    companion object {",                                     # 30
    "        const val KEY = \"k\"",                              # 31
    "        fun make() = CustomerController(null, \"\")",        # 32
    "    }",                                                      # 33
    "}",                                                          # 34
    "",                                                           # 35
    "object Defaults { val x = 1 }",                              # 36
    "typealias Id = String",                                      # 37
)


class KotlinDeclarations(unittest.TestCase):
    def setUp(self):
        self.p = parse_kotlin(KOTLIN)

    def test_it_parses_cleanly(self):
        self.assertEqual(self.p.problems, ())

    def test_imports_with_alias_and_wildcard(self):
        self.assertEqual([(r.kind, r.module, r.alias, r.line) for r in self.p.imports], [
            ("import", "com.acme.service.InvoiceService", None, 3), ("import", "com.acme.util.format", "fmt", 4), ("wildcard", "com.acme.util", None, 5),
        ])
        self.assertEqual(self.p.imports[1].local, "fmt")

    def test_top_level_declarations(self):
        got = [(d.kind, d.name, d.start, d.end) for d in self.p.declarations]
        self.assertEqual(got, [("property", "BASE", 7, 7), ("function", "helper", 9, 9), ("class", "CustomerController", 11, 34), ("object", "Defaults", 36, 36), ("typealias", "Id", 37, 37)])
        self.assertEqual(self.p.top_names, ("BASE", "helper", "CustomerController", "Defaults", "Id"))

    def test_class_members_constructor_properties_companion_and_init(self):
        cls = self.p.declarations[2]
        got = [(c.kind, c.name, c.start, c.end, c.in_header) for c in cls.children]
        self.assertEqual(got, [
            ("property", "service", 14, 14, True), ("property", "name", 15, 15, True), ("method", "get", 17, 20, False), ("method", "create", 22, 23, False),
            ("property", "count", 25, 26, False), ("init", "<init>", 28, 28, False), ("companion", "Companion", 30, 33, False),
        ])
        self.assertEqual([(s.name, s.kind) for s in cls.supertypes], [("Base", "colon"), ("Auditable", "colon")])
        companion = cls.children[-1]
        self.assertEqual([(c.name, c.dotted) for c in companion.children], [("KEY", "CustomerController.KEY"), ("make", "CustomerController.make")])
        self.assertEqual(cls.header_end, 16)

    def test_placement(self):
        got = {n: [(p.name, p.whole) for p in self.p.placements(n)] for n in (11, 13, 14, 16, 19, 22, 23, 26, 28, 31, 34)}
        self.assertEqual(got[11], [("CustomerController", True)])
        self.assertEqual(got[13], [("CustomerController", True)])
        self.assertEqual(got[14], [("CustomerController.service", False)])
        self.assertEqual(got[16], [("CustomerController", True)])
        self.assertEqual(got[19], [("CustomerController.get", False)])
        self.assertEqual(got[22], [("CustomerController.create", False)])
        self.assertEqual(got[23], [("CustomerController.create", False)])
        self.assertEqual(got[26], [("CustomerController.count", False)])
        self.assertEqual(got[28], [("CustomerController.<init>", False)])
        self.assertEqual(got[31], [("CustomerController.KEY", False)])
        self.assertEqual(got[34], [("CustomerController", True)])

    def test_the_names_a_string_template_spells_count(self):
        get = self.p.unit_of("CustomerController.get")[0]
        self.assertIn("service", self.p.spelled(get))
        self.assertIn("find", self.p.spelled(get))
        self.assertIn("id", self.p.spelled(get))

    def test_a_multi_line_expression_body_and_a_chain_that_continues_on_the_next_line(self):
        src = lines(
            "class A {",
            "    fun f() = listOf(1, 2)",
            "        .map { it * 2 }",
            "        .filter { it > 1 }",
            "    fun g() = 1 +",
            "        2",
            "    fun h(): Int {",
            "        return 3",
            "    }",
            "    val x = 5",
            "    val y = x ?: 0",
            "}",
        )
        p = parse_kotlin(src)
        self.assertEqual(p.problems, ())
        (a,) = p.declarations
        self.assertEqual([(c.name, c.start, c.end) for c in a.children], [("f", 2, 4), ("g", 5, 6), ("h", 7, 9), ("x", 10, 10), ("y", 11, 11)])

    def test_data_sealed_enum_interface_and_secondary_constructor(self):
        src = lines(
            "data class P(val a: Int, val b: String = \"x\")",
            "sealed class S { object One : S() ; data class Two(val v: Int) : S() }",
            "enum class E(val code: Int) { A(1), B(2) { override fun f() = 1 }; open fun f() = 0 }",
            "interface I { fun a(): Int; fun b() = 2 }",
            "class C { constructor(x: Int) : this() { println(x) } constructor() }",
            "fun interface F { fun run() }",
            "annotation class Marker(val v: String)",
        )
        p = parse_kotlin(src)
        self.assertEqual(p.problems, ())
        by = {d.name: d for d in p.declarations}
        self.assertEqual([(c.kind, c.name) for c in by["P"].children], [("property", "a"), ("property", "b")])
        self.assertEqual([(c.kind, c.name) for c in by["S"].children], [("object", "One"), ("class", "Two")])
        self.assertEqual([(c.kind, c.name) for c in by["E"].children], [("property", "code"), ("enum_constant", "A"), ("enum_constant", "B"), ("method", "f")])
        self.assertEqual([(c.kind, c.name) for c in by["I"].children], [("method", "a"), ("method", "b")])
        self.assertEqual([c.kind for c in by["C"].children], ["constructor", "constructor"])
        self.assertEqual(by["F"].kind, "interface")
        self.assertEqual(by["Marker"].kind, "annotation")

    def test_a_backtick_function_name_and_generic_extension(self):
        p = parse_kotlin("fun <T> List<T>.second(): T = this[1]\nfun `a test`() {}\nval String.size2: Int get() = length\n")
        self.assertEqual(p.problems, ())
        self.assertEqual([(d.kind, d.name) for d in p.declarations], [("function", "second"), ("function", "a test"), ("property", "size2")])

    def test_file_annotations(self):
        p = parse_kotlin('@file:JvmName("Util")\npackage a.b\nfun f() = 1\n')
        self.assertEqual((p.package, [a.name for a in p.file_annotations], [a.target for a in p.file_annotations]), ("a.b", ["JvmName"], ["file"]))

    def test_an_unterminated_template_or_raw_string_is_a_parse_problem(self):
        self.assertTrue(parse_kotlin('val s = "a ${ b').problems)
        self.assertTrue(parse_kotlin('val s = """ never').problems)

    def test_nested_comments_do_not_end_early(self):
        p = parse_kotlin("/* a /* b */ fun ghost() = 1 */\nfun real() = 2\n")
        self.assertEqual([d.name for d in p.declarations], ["real"])


JAVA_ZOO = r'''package com.acme.zoo;

import java.util.*;
import java.util.function.Function;
import static java.util.stream.Collectors.toList;

@SuppressWarnings({"unchecked", "rawtypes"})
public sealed interface Shape<T extends Comparable<? super T>> extends Serializable permits Circle, Square {
    double area();
    default String describe() { return "shape " + area(); }
    static <R> Shape<R> of(Supplier<? extends R> s) { return null; }
    private int helper() { return 1; }
    int[][] GRID = new int[][] {{1, 2}, {3, 4}};
}

record Circle(double r, @Deprecated List<Map<String, ? extends Number>> extra) implements Shape<Double> {
    Circle {
        if (r < 0) throw new IllegalArgumentException("negative");
    }
    public double area() { return Math.PI * r * r; }
    static Circle unit() { return new Circle(1, List.of()); }
}

abstract class Base<K, V extends List<K>> implements Comparable<Base<K, V>>, AutoCloseable {
    protected final Map<K, V> map = new HashMap<>();
    private volatile transient int counter;
    static { System.out.println("loaded"); }
    { counter = 1; }

    @SafeVarargs
    protected static <E> List<E> listOf(E... items) { return Arrays.asList(items); }

    public <T extends Number & Comparable<T>> T max(T a, T b) {
        var cmp = (Comparator<T> & java.io.Serializable) (x, y) -> x.compareTo(y);
        Runnable r = () -> { for (int i = 0; i < 3 >>> 1; i++) { counter += i < 2 ? 1 : 2; } };
        Object o = new Object() { @Override public String toString() { return "anon" + (a.compareTo(b) > 0 ? "a" : "b"); } };
        String text = switch (counter) { case 1, 2 -> "low"; default -> { yield "high"; } };
        if (o instanceof String s && !s.isEmpty()) { counter++; }
        label: for (;;) { break label; }
        try (var in = new java.io.StringReader("x")) { in.read(); } catch (java.io.IOException | RuntimeException e) { throw new RuntimeException(e); } finally { counter = 0; }
        return a.compareTo(b) > 0 ? a : b;
    }

    @Override public int compareTo(Base<K, V> other) { return 0; }
    @Override public void close() throws Exception {}

    enum Mode implements Function<String, String> {
        FAST("f") { @Override public String apply(String s) { return s + "!"; } },
        SLOW("s") { @Override public String apply(String s) { return s; } };
        private final String code;
        Mode(String code) { this.code = code; }
        public String code() { return code; }
    }

    interface Visitor<R> { R visit(Base<?, ?> b); }
    public static class Builder<B extends Builder<B>> { public B self() { return (B) this; } }
    @interface Marker { String[] value() default {}; int level() default 1; Class<?>[] types() default {}; }
    String json = """
        { "a": [1, 2], "b": "//not a comment" }
        """;
    public String pad(String s) {
        return s + """
            tail""";
    }
    char quote = '"';
    char apos = '\'';
    long big = 1_000_000L, other = 0xFFL;
}
'''

KOTLIN_ZOO = r'''@file:JvmName("Zoo")
package com.acme.zoo

import kotlin.collections.List as KList
import java.util.*

typealias Handler<T> = (T) -> Unit

@JvmInline
value class UserId(val raw: String)

sealed interface Event { val at: Long }
data class Created(override val at: Long, val id: UserId) : Event
data object Ping : Event { override val at: Long = 0 }

enum class Level(val weight: Int) {
    LOW(1), HIGH(10) { override fun label() = "HIGH!" };
    open fun label(): String = name
    companion object { fun of(w: Int) = entries.first { it.weight == w } }
}

interface Repo<T : Any> {
    suspend fun find(id: String): T?
    fun all(): List<T> = emptyList()
}

abstract class Service<T : Any>(
    private val repo: Repo<T>,
    protected val cache: MutableMap<String, T> = mutableMapOf(),
    @Deprecated("old") val name: String = "svc",
) : Repo<T> by repo, AutoCloseable where T : Comparable<T> {
    lateinit var late: String
    var counter = 0
        private set
        get() = field + 1
    val lazyValue: String by lazy { "x" + name }
    init { counter = 1 }
    constructor(repo: Repo<T>, n: String) : this(repo, mutableMapOf(), n) { counter = 2 }

    inline fun <reified R> convert(x: Any): R? = x as? R

    operator fun plus(other: Service<T>): Service<T> = this
    infix fun and(other: Int) = this
    fun String.shout(): String = uppercase() + "!"

    open fun process(items: List<T>,
                     limit: Int = 10,
                     callback: (T) -> Unit = { println("item $it and ${it.hashCode() + 1}") }): Int {
        val (a, b) = items.partition { it.compareTo(items.first()) > 0 }
        for ((i, x) in items.withIndex()) { callback(x) }
        return when {
            a.isEmpty() -> 0
            b.size > limit -> limit
            else -> a.size +
                b.size
        }
    }

    val total: Int get() = cache.size
    val doubled
        get() = total * 2

    fun chained() = cache.values
        .filter { it.toString().isNotEmpty() }
        .map { it.hashCode() }
        .sum()

    override fun close() {}

    inner class Cursor(val pos: Int) { fun next() = Cursor(pos + 1) }
    object Registry { val all = mutableListOf<String>() }
    companion object Factory {
        const val VERSION = "1.0"
        @JvmStatic fun <T : Comparable<T>> create(repo: Repo<T>): Service<T> = object : Service<T>(repo) {}
    }
}

fun <T> List<T>.second(): T = this[1]
val Int.isEven: Boolean get() = this % 2 == 0
fun main(args: Array<String>) = println("hello " + args.size)
private fun helper(vararg xs: Int, f: () -> Unit = {}): String = xs.joinToString { "$it" }
const val RAW = """
    raw ${1 + 2} text "quoted"
"""
'''


class Zoo(unittest.TestCase):
    """Modern Java and Kotlin in one file each: sealed types, records, enums with bodies, annotations, generics, lambdas, anonymous classes, text
    blocks, delegation, extension functions, accessors over several lines, expression bodies that continue on the next line."""

    def test_java_declarations_of_a_file_that_uses_most_of_the_language(self):
        p = parse_java(JAVA_ZOO)
        self.assertEqual(p.problems, ())
        self.assertEqual([(u.kind, u.dotted) for u in p.units()], [('interface', 'Shape'), ('method', 'Shape.area'), ('method', 'Shape.describe'), ('method', 'Shape.of'), ('method', 'Shape.helper'), ('field', 'Shape.GRID'), ('record', 'Circle'), ('field', 'Circle.r'), ('field', 'Circle.extra'), ('constructor', 'Circle.Circle'), ('method', 'Circle.area'), ('method', 'Circle.unit'), ('class', 'Base'), ('field', 'Base.map'), ('field', 'Base.counter'), ('init', 'Base.<init>'), ('init', 'Base.<init>'), ('method', 'Base.listOf'), ('method', 'Base.max'), ('method', 'Base.compareTo'), ('method', 'Base.close'), ('enum', 'Base.Mode'), ('enum_constant', 'Base.Mode.FAST'), ('enum_constant', 'Base.Mode.SLOW'), ('field', 'Base.Mode.code'), ('constructor', 'Base.Mode.Mode'), ('method', 'Base.Mode.code'), ('interface', 'Base.Visitor'), ('method', 'Base.Visitor.visit'), ('class', 'Base.Builder'), ('method', 'Base.Builder.self'), ('annotation', 'Base.Marker'), ('method', 'Base.Marker.value'), ('method', 'Base.Marker.level'), ('method', 'Base.Marker.types'), ('field', 'Base.json'), ('method', 'Base.pad'), ('field', 'Base.quote'), ('field', 'Base.apos'), ('field', 'Base.big')])

    def test_kotlin_declarations_of_a_file_that_uses_most_of_the_language(self):
        p = parse_kotlin(KOTLIN_ZOO)
        self.assertEqual(p.problems, ())
        self.assertEqual([(u.kind, u.dotted) for u in p.units()], [('typealias', 'Handler'), ('class', 'UserId'), ('property', 'UserId.raw'), ('interface', 'Event'), ('property', 'Event.at'), ('class', 'Created'), ('property', 'Created.at'), ('property', 'Created.id'), ('object', 'Ping'), ('property', 'Ping.at'), ('enum', 'Level'), ('property', 'Level.weight'), ('enum_constant', 'Level.LOW'), ('enum_constant', 'Level.HIGH'), ('method', 'Level.label'), ('companion', 'Level'), ('method', 'Level.of'), ('interface', 'Repo'), ('method', 'Repo.find'), ('method', 'Repo.all'), ('class', 'Service'), ('property', 'Service.repo'), ('property', 'Service.cache'), ('property', 'Service.name'), ('property', 'Service.late'), ('property', 'Service.counter'), ('property', 'Service.lazyValue'), ('init', 'Service.<init>'), ('constructor', 'Service.Service'), ('method', 'Service.convert'), ('method', 'Service.plus'), ('method', 'Service.and'), ('method', 'Service.shout'), ('method', 'Service.process'), ('property', 'Service.total'), ('property', 'Service.doubled'), ('method', 'Service.chained'), ('method', 'Service.close'), ('class', 'Service.Cursor'), ('property', 'Service.Cursor.pos'), ('method', 'Service.Cursor.next'), ('object', 'Service.Registry'), ('property', 'Service.Registry.all'), ('companion', 'Service.Factory'), ('property', 'Service.Factory.VERSION'), ('method', 'Service.Factory.create'), ('function', 'second'), ('property', 'isEven'), ('function', 'main'), ('function', 'helper'), ('property', 'RAW')])

    def test_the_span_of_a_member_is_where_it_ends_not_where_a_lambda_or_a_string_closes(self):
        p = parse_java(JAVA_ZOO)
        by = {u.dotted: u for u in p.units()}
        self.assertEqual((by["Base.max"].start, by["Base.max"].end), (33, 42))
        self.assertEqual((by["Base.json"].start, by["Base.json"].end), (58, 60))
        self.assertEqual((by["Base.pad"].start, by["Base.pad"].end), (61, 64))
        k = parse_kotlin(KOTLIN_ZOO)
        kb = {u.dotted: u for u in k.units()}
        self.assertEqual((kb["Service.process"].start, kb["Service.process"].end), (46, 57))
        self.assertEqual((kb["Service.chained"].start, kb["Service.chained"].end), (63, 66))
        self.assertEqual((kb["Service.counter"].start, kb["Service.counter"].end), (33, 35))


class Uses(unittest.TestCase):
    SRC = lines(
        "package p;",
        "class A {",
        "    Money m = new Money(1, 2);",
        "    void f() {",
        "        Money.format(m);",
        "        other.Money.thing();",
        "        String s = \"Money\";  // Money",
        "        g(Money::of);",
        "    }",
        "}",
    )

    def test_uses_of_a_name_with_chain_form_and_arguments(self):
        p = parse_java(self.SRC)
        got = [(u.line, u.chain, u.form) for u in p.uses("Money")]
        self.assertEqual(got, [(3, (), "reference"), (3, (), "call"), (5, ("format",), "call"), (8, (), "reference")])
        call = [u for u in p.uses("Money") if u.line == 3 and u.form == "call"][0]
        self.assertEqual(p.args_of(call), ("1", "2"))
        self.assertEqual(p.args_of([u for u in p.uses("Money") if u.line == 5][0]), ("m",))

    def test_a_name_after_a_dot_is_not_a_use_unless_asked(self):
        p = parse_java(self.SRC)
        self.assertEqual([u.line for u in p.uses("thing")], [])
        self.assertEqual([u.line for u in p.uses("thing", member=True)], [6])

    def test_a_string_and_a_comment_are_not_uses(self):
        p = parse_java(self.SRC)
        self.assertNotIn(7, [u.line for u in p.uses("Money")])

    def test_a_kotlin_template_name_is_a_use(self):
        p = parse_kotlin('fun f() { println("hi $name and ${svc.go()}") }')
        self.assertEqual([u.local for u in p.uses("name")], ["name"])
        self.assertEqual([u.local for u in p.uses("svc")], ["svc"])

    def test_qualified_names_in_code(self):
        p = parse_java("package p;\nclass A { void f() { com.acme.util.Money.format(1); a.b(); } @com.acme.Marker int x; }\n")
        self.assertEqual([c[0] for c in p.chains()], ["com.acme.util.Money.format", "com.acme.Marker"])

    def test_dynamic_access(self):
        self.assertTrue(parse_java("class A { void f() throws Exception { Class.forName(\"x\"); } }").uses_dynamic_access())
        self.assertTrue(parse_java("class A { Object o = ctx.getBean(\"x\"); }").uses_dynamic_access())
        self.assertFalse(parse_java("class A { void f() {} }").uses_dynamic_access())


class Definitions(unittest.TestCase):
    def test_what_counts_as_a_definition(self):
        src = lines(
            "package p;",
            "class get {",
            "    int get;",
            "    void get() {}",
            "    void get(int a) {}",
            "    void other() { int get = 1; get(); }",
            "    get() {}",
            "    enum E { get }",
            "}",
        )
        p = parse_java(src)
        self.assertEqual(p.definition_lines("get"), [2, 3, 4, 5, 8])
        self.assertEqual(p.definition_lines("other"), [6])
        self.assertEqual(p.definition_lines("missing"), [])

    def test_kotlin_definitions(self):
        p = parse_kotlin("class A(val a: Int) { fun a2() = 1; val b = 2 }\nfun top() = 1\ntypealias T = Int\n")
        self.assertEqual(p.definition_lines("a"), [1])
        self.assertEqual(p.definition_lines("a2"), [1])
        self.assertEqual(p.definition_lines("top"), [2])
        self.assertEqual(p.definition_lines("T"), [3])


class Robustness(unittest.TestCase):
    def test_a_file_cut_at_every_offset_parses_without_raising(self):
        for kotlin, src in ((False, JAVA), (True, KOTLIN)):
            for cut in range(0, len(src), 3):
                (parse_kotlin if kotlin else parse_java)(src[:cut])

    def test_token_soup(self):
        import random

        rng = random.Random(11)
        pieces = ["class", "A", "{", "}", "(", ")", ";", "@", "X", "fun", "val", "<", ">", "=", ",", "\n", "interface", "enum", "extends", ":", "import", "package", "a.b", "\"s\"", "record"]
        for _ in range(500):
            text = " ".join(rng.choice(pieces) for _ in range(rng.randint(1, 40)))
            parse_java(text)
            parse_kotlin(text)

    def test_speed(self):
        src = (JAVA.replace("package com.acme.billing;", "")) * 1
        big = "package p;\n" + "\n".join(f"class C{i} {{\n  @GetMapping(\"/x\")\n  public String get(String id) {{ return service.find(id) + \"v\"; }}\n  int f{i};\n}}" for i in range(1500))
        start = time.monotonic()
        p = parse_java(big)
        self.assertEqual(p.problems, ())
        self.assertEqual(len(p.declarations), 1500)
        self.assertLess(time.monotonic() - start, 6)
        del src


if __name__ == "__main__":
    unittest.main()
