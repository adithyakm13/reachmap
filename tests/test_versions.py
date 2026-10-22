"""Version parsing and ordering (a PEP 440 subset and semver): ordered by parsed parts, never as strings."""

import unittest

from reachmap import versions
from reachmap.versions import Version, parse, tag_version


def order(*texts):
    return [v.text for v in sorted(parse(t) for t in texts)]


class Parse(unittest.TestCase):
    def test_a_plain_release_and_its_parts(self):
        v = parse("1.10.0")
        self.assertEqual((v.release, v.pre, v.post, v.dev), ((1, 10, 0), None, None, None))
        self.assertEqual(v.text, "1.10.0")
        self.assertFalse(v.is_prerelease)

    def test_a_v_prefix_is_accepted_and_not_part_of_the_text(self):
        self.assertEqual(parse("v1.2.3").text, "1.2.3")
        self.assertEqual(parse("V2.0").release, (2, 0))

    def test_an_explicit_prefix_is_required_when_given(self):
        self.assertEqual(tag_version("rel-1.2.0", "rel-").text, "1.2.0")
        self.assertIsNone(tag_version("v1.2.0", "rel-"))
        self.assertEqual(tag_version("v1.2.0", "v").text, "1.2.0")
        self.assertEqual(tag_version("1.2.0", "").text, "1.2.0")
        self.assertIsNone(tag_version("1.2.0", "v"))  # a configured prefix is exact: this tag is not one of the producer's versions
        self.assertEqual(tag_version("1.2.0", None).text, "1.2.0")  # no prefix configured: with or without a `v`
        self.assertEqual(tag_version("v1.2.0", None).text, "1.2.0")

    def test_text_that_is_not_a_version_does_not_parse(self):
        for text in ("", "latest", "1.x", "1..2", "1.2.3.", "main", "1.2-beta-", "v", "==1.0", "1.0 ", None, 7):
            with self.subTest(text=text):
                self.assertIsNone(parse(text))

    def test_pre_post_dev_and_local_forms(self):
        self.assertTrue(parse("1.0rc1").is_prerelease)
        self.assertTrue(parse("1.0.0-beta.2").is_prerelease)
        self.assertTrue(parse("1.0.dev3").is_prerelease)
        self.assertFalse(parse("1.0.post1").is_prerelease)
        self.assertEqual(parse("1.0.post1").post, 1)
        self.assertEqual(parse("1.0+local.7").text, "1.0+local.7")


class Ordering(unittest.TestCase):
    def test_1_10_0_is_after_1_9_0_which_is_after_1_2_0(self):
        self.assertGreater(parse("1.10.0"), parse("1.9.0"))
        self.assertGreater(parse("1.9.0"), parse("1.2.0"))
        self.assertLess("1.10.0", "1.9.0")  # the string order, which is the trap
        self.assertEqual(order("1.10.0", "1.9.0", "1.2.0", "1.100.0"), ["1.2.0", "1.9.0", "1.10.0", "1.100.0"])

    def test_trailing_zeros_do_not_matter(self):
        self.assertEqual(parse("1.0"), parse("1.0.0"))
        self.assertEqual(parse("2"), parse("v2.0.0"))
        self.assertEqual(hash(parse("1.0")), hash(parse("1.0.0.0")))
        self.assertNotEqual(parse("1.0.1"), parse("1.0"))

    def test_prereleases_sort_before_the_release_and_dev_before_them(self):
        self.assertEqual(
            order("1.0", "1.0rc1", "1.0b2", "1.0a1", "1.0.dev1", "1.0.post1", "1.0.1"),
            ["1.0.dev1", "1.0a1", "1.0b2", "1.0rc1", "1.0", "1.0.post1", "1.0.1"],
        )

    def test_semver_prerelease_identifiers(self):
        self.assertEqual(
            order("1.0.0", "1.0.0-rc.1", "1.0.0-beta.11", "1.0.0-beta.2", "1.0.0-alpha", "1.0.0-alpha.1"),
            ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0"],
        )

    def test_epochs_and_build_metadata(self):
        self.assertGreater(parse("1!1.0"), parse("2.0"))
        self.assertEqual(parse("1.0.0+build.5"), parse("1.0.0"))  # build and local metadata are not part of the order

    def test_a_version_compares_equal_only_to_a_version(self):
        self.assertNotEqual(parse("1.0"), "1.0")
        with self.assertRaises(TypeError):
            parse("1.0") < "1.0"

    def test_max_of_versions_skips_none(self):
        found = [parse(t) for t in ("1.9.0", "1.10.0", "1.2.0")]
        self.assertEqual(max(found).text, "1.10.0")
        self.assertIsNone(parse("latest"))


if __name__ == "__main__":
    unittest.main()
