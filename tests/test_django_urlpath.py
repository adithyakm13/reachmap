"""Django route strings and regular expressions as paths (`reachmap.adapters.django.urlpath`): converters, groups, what is unknown."""

import unittest

from reachmap.adapters import normalize_path
from reachmap.adapters.django.urlpath import convert_regex, convert_route, has_marker, marker, substitute


class Route(unittest.TestCase):
    def test_converters_and_bare_names(self):
        self.assertEqual(convert_route("items/<int:pk>/"), "items/{pk}/")
        self.assertEqual(convert_route("<slug:s>/<str:n>/<uuid:u>/<bare>"), "{s}/{n}/{u}/{bare}")

    def test_the_path_converter_takes_the_rest(self):
        self.assertEqual(convert_route("files/<path:name>"), "files/{name:path}")

    def test_a_literal_route_is_unchanged_and_an_empty_one_is_empty(self):
        self.assertEqual((convert_route("a/b/"), convert_route("")), ("a/b/", ""))

    def test_a_stand_in_for_an_unknown_part_survives_the_conversion(self):
        text = f"{marker(0)}/<int:pk>"
        self.assertEqual(convert_route(text), f"{marker(0)}/{{pk}}")
        self.assertTrue(has_marker(text))
        self.assertEqual(substitute(convert_route(text), ("settings.PREFIX",)), "<unknown:settings.PREFIX>/{pk}")


class Regex(unittest.TestCase):
    def path(self, regex):
        return normalize_path("/" + convert_regex(regex))

    def test_literals_with_anchors_and_an_optional_final_slash(self):
        self.assertEqual(self.path(r"^archive/$"), "/archive")
        self.assertEqual(self.path(r"^archive/?$"), "/archive")
        self.assertEqual(self.path(r"archive"), "/archive")

    def test_escaped_characters_are_literals(self):
        self.assertEqual(self.path(r"^feed\.xml$"), "/feed.xml")
        self.assertEqual(self.path(r"^a\-b/$"), "/a-b")

    def test_a_named_group_that_makes_a_segment_is_a_parameter(self):
        self.assertEqual(self.path(r"^archive/(?P<year>[0-9]{4})/(?P<month>\d{2})/$"), "/archive/{year}/{month}")
        self.assertEqual(self.path(r"^(?P<slug>[\w-]+)/$"), "/{slug}")

    def test_a_named_group_that_can_match_a_slash_takes_the_rest(self):
        self.assertEqual(self.path(r"^files/(?P<name>.+)$"), "/files/{name:path}")
        self.assertEqual(self.path(r"^files/(?P<name>[^/]+)$"), "/files/{name}")  # the slash is in a class that excludes it
        self.assertEqual(self.path(r"^f/(?P<name>(?:\w+/)*\w+)$"), "/f/{name:path}")

    def test_an_unnamed_group_or_a_mixed_segment_or_any_other_syntax_is_an_unknown_segment(self):
        self.assertEqual(self.path(r"^old/(\d+)/$"), r"/old/<unknown:(\d+)>")
        self.assertEqual(self.path(r"^item-(?P<id>\d+)/$"), r"/<unknown:item-(?P<id>\d+)>")
        self.assertEqual(self.path(r"^opt(ional)?/$"), "/<unknown:opt(ional)?>")
        self.assertEqual(self.path(r"^a|b/$"), "/<unknown:a|b>")

    def test_a_slash_inside_a_group_does_not_cut_the_segment_and_never_appears_in_an_unknown_part(self):
        self.assertEqual(self.path(r"^x/(a/b)/y$"), "/x/<unknown:(a%2Fb)>/y")

    def test_a_slash_inside_a_character_class_is_not_a_separator(self):
        self.assertEqual(self.path(r"^x/[a/b]+/$"), "/x/<unknown:[a%2Fb]+>")


if __name__ == "__main__":
    unittest.main()
