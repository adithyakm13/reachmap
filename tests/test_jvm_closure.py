"""The closure of a change inside one file (reachmap.adapters.jvm.closure), on parsed texts with no repository: what a changed line reaches, member by
member, what makes a whole type reached, which names other files are given, and what a name that many classes define does not reach.
"""

import unittest

from reachmap.adapters.jvm.closure import close
from reachmap.adapters.jvm.syntax import parse_java, parse_kotlin
from tests.support_universe import f

SERVICE = f([
    "package p;",                                  # 1
    "public class Service {",                      # 2
    "    private int rate = 1;",                   # 3
    "    public int total(String id) {",           # 4
    "        return id.length() * rate;",          # 5
    "    }",                                       # 6
    "    public int double_(String id) {",         # 7
    "        return total(id) * 2;",               # 8
    "    }",                                       # 9
    "    public int other() {",                    # 10
    "        return 3;",                           # 11
    "    }",                                       # 12
    "}",                                           # 13
    "class Helper {",                              # 14
    "    int use(Service s) {",                    # 15
    "        return s.total(\"x\");",              # 16
    "    }",                                       # 17
    "    int free() {",                            # 18
    "        return 0;",                           # 19
    "    }",                                       # 20
    "}",                                           # 21
])


def units_at(p, *lines):
    out = []
    for n in lines:
        out += [(0, pl.unit, pl.whole) for pl in p.placements(n)]
    return out


def reached(c, p):
    return sorted(u.dotted for u in c.units(0))


class Members(unittest.TestCase):
    def setUp(self):
        self.p = parse_java(SERVICE)

    def test_a_line_in_a_method_reaches_that_method_and_who_spells_it_by_name_across_the_file(self):
        c = close([self.p], units_at(self.p, 5), lambda n, rec=True: True)
        self.assertEqual(reached(c, self.p), ["Helper.use", "Service.double_", "Service.total"])
        self.assertEqual(sorted(c.exports()), ["Helper.use", "Service.double_", "Service.total"])

    def test_a_field_reaches_the_members_that_use_it(self):
        c = close([self.p], units_at(self.p, 3), lambda n, rec=True: True)
        self.assertEqual(reached(c, self.p), ["Helper.use", "Service.double_", "Service.rate", "Service.total"])

    def test_a_header_line_reaches_the_whole_type_and_its_name_is_the_export(self):
        c = close([self.p], units_at(self.p, 2), lambda n, rec=True: True)
        self.assertIn("Service", c.exports())
        self.assertNotIn("Service.total", c.exports())  # the whole type is one name
        self.assertEqual({u.dotted for u in c.units(0)} >= {"Service", "Service.total", "Service.other", "Service.rate"}, True)
        self.assertTrue(c.is_whole(0, self.p.declarations[0]))

    def test_a_member_name_that_is_too_common_is_followed_only_where_it_is_used_on_its_own(self):
        """`total` is not distinctive: Helper.use calls `s.total(...)` (a member of something), Service.double_ calls `total(...)` on its own."""
        c = close([self.p], units_at(self.p, 5), lambda n, rec=True: n != "total")
        self.assertEqual(reached(c, self.p), ["Service.double_", "Service.total"])

    def test_a_declaration_name_is_not_a_use_of_itself(self):
        c = close([self.p], units_at(self.p, 11), lambda n, rec=True: True)
        self.assertEqual(reached(c, self.p), ["Service.other"])

    def test_both_versions_are_closed_together(self):
        before = parse_java(SERVICE.replace("        return total(id) * 2;", "        return other() * 2;"))
        after = self.p
        c = close([after, before], units_at(before, 8)[:0] + [(1, pl.unit, pl.whole) for pl in before.placements(8)], lambda n, rec=True: True)
        self.assertIn("Service.double_", {u.dotted for u in c.units(1)})
        self.assertEqual(sorted(c.exports()), ["Service.double_"])


class Wide(unittest.TestCase):
    SRC = f([
        "package p;",
        "public class Ctrl {",                       # 2
        "    private final Svc svc;",                # 3
        "    static final int RATE = load();",       # 4
        "    public Ctrl(Svc svc) {",                # 5
        "        this.svc = svc;",                   # 6
        "    }",                                     # 7
        "    @PostConstruct void init() {",          # 8
        "        warm();",                           # 9
        "    }",                                     # 10
        "    int a() { return svc.go(); }",          # 11
        "    int b() { return 2; }",                 # 12
        "    static int load() { return 1; }",       # 13
        "}",
    ])

    def setUp(self):
        self.p = parse_java(self.SRC)

    def test_a_constructor_that_only_stores_what_it_is_given_does_not_reach_the_type_when_reached_by_a_name(self):
        c = close([self.p], units_at(self.p, 3), lambda n, rec=True: True)
        self.assertEqual(reached(c, self.p), ["Ctrl.Ctrl", "Ctrl.a", "Ctrl.svc"])  # the constructor is reached (it stores the field), the type is not
        self.assertEqual(sorted(c.exports()), ["Ctrl.a", "Ctrl.svc"])

    def test_a_changed_constructor_reaches_the_whole_type(self):
        c = close([self.p], units_at(self.p, 6), lambda n, rec=True: True)
        self.assertIn("Ctrl", c.exports())

    def test_a_method_that_runs_when_the_bean_is_built_reaches_the_whole_type(self):
        c = close([self.p], units_at(self.p, 9), lambda n, rec=True: True)
        self.assertIn("Ctrl", c.exports())

    def test_a_static_field_initialised_by_a_call_to_something_reached_reaches_the_whole_type(self):
        c = close([self.p], units_at(self.p, 13), lambda n, rec=True: True)
        self.assertIn("Ctrl", c.exports())

    def test_a_trivial_constructor_is_told_from_one_with_logic(self):
        p = parse_java("class A { A(int x) { this.x = x; } A() { init(); } A(String s) { super(s); } int x; }")
        ctors = [u for u in p.units() if u.kind == "constructor"]
        self.assertEqual([p.is_trivial_constructor(u) for u in ctors], [True, False, True])


class Kotlin(unittest.TestCase):
    SRC = f([
        "package p",                                 # 1
        "fun format(x: Int): String = x.toString()",  # 2
        "fun shout(x: Int): String = format(x) + \"!\"",  # 3
        "val CONFIG = load()",                       # 4
        "fun load() = 1",                            # 5
        "class K(private val svc: Svc) {",           # 6
        "    fun go() = svc.run()",                  # 7
        "}",
    ])

    def setUp(self):
        self.p = parse_kotlin(self.SRC)

    def test_a_top_level_function_is_exported_by_its_name_and_the_name_java_sees_it_under(self):
        c = close([self.p], units_at(self.p, 2), lambda n, rec=True: True)
        self.assertEqual(sorted(c.exports("UtilKt")), ["UtilKt.format", "UtilKt.shout", "format", "shout"])

    def test_a_top_level_property_initialised_by_a_call_that_was_reached_runs_for_every_importer(self):
        c = close([self.p], units_at(self.p, 5), lambda n, rec=True: True)
        self.assertTrue(c.import_time)

    def test_a_constructor_property_is_a_member_and_the_header_is_the_type(self):
        c = close([self.p], units_at(self.p, 6), lambda n, rec=True: True)
        self.assertIn("K", c.exports())
        c2 = close([self.p], [(0, u, False) for u in self.p.units() if u.dotted == "K.svc"], lambda n, rec=True: True)
        self.assertEqual(sorted(u.dotted for u in c2.units(0)), ["K.go", "K.svc"])


if __name__ == "__main__":
    unittest.main()
