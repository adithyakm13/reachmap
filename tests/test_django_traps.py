"""Shape A traps for the Django adapter (context 5): an absence manufactured by the tool must never be reported as evidence about the code.

Every test makes a read fail (a file that is not text, a symlink git grep does not search, a search that fails or is cut, a settings file that cannot be
read, a URLconf that does not parse) and asserts an incompleteness, not an absence: no route is called removed, no URLconf is called unmounted, no
answer is marked complete.
"""

import unittest
from types import SimpleNamespace
from unittest import mock

from reachmap.gitlayer import Git
from reachmap.model import Read
from tests import support_git as sg
from tests.support_django import ROOT, SETTINGS, URLS, VIEWS, Base, f, ln, tree

LEGACY_URLS = f(["from django.urls import path", "", "from shop import legacy_views", "", "urlpatterns = [", '    path("old/", legacy_views.export),', "]"])
LEGACY_VIEWS = f(["def export(request):", "    return None"])


def legacy(**more):
    return tree(**{"shop/legacy_urls.py": LEGACY_URLS, "shop/legacy_views.py": LEGACY_VIEWS, **more})


class UnreadableFiles(Base):
    def test_a_urlconf_that_is_not_text_is_unreadable_not_absent_and_its_routes_are_not_removed(self):
        broken = b"\xff\xfeurlpatterns = []\n"
        self.repo({"files": tree()}, {"files": {"shop/urls.py": broken}})
        entry = self.entry()
        removed, added = entry.routes_changed(self.sha("main~1"), self.sha("main"))
        self.assertEqual(entry.routes(None, self.sha("main")), [])
        self.assertEqual((removed, added), ([], []))  # the routes were not read, they were not deleted
        self.assertTrue(entry.problems)
        self.assertIn("read_unreadable", {r.kind for r in self.records})

    def test_a_base_that_cannot_be_read_does_not_make_every_route_new(self):
        broken = b"\xff\xfeurlpatterns = []\n"
        self.repo({"files": tree(**{"shop/urls.py": broken})}, {"files": {"shop/urls.py": URLS}})
        entry = self.entry()
        removed, added = entry.routes_changed(self.sha("main~1"), self.sha("main"))
        self.assertEqual(([r.label for r in removed], added), ([], []))
        self.assertEqual(self.route_labels(), ["ANY /shop/items", "ANY /shop/items/{item_id}"])

    def test_a_removal_between_two_readable_tables_is_still_a_removal(self):
        self.repo({"files": tree()}, {"files": {"shop/urls.py": URLS.replace('    path("items/", views.item_list, name="item-list"),\n', "")}})
        entry = self.entry()
        removed, added = entry.routes_changed(self.sha("main~1"), self.sha("main"))
        self.assertEqual(([r.label for r in removed], added), (["ANY /shop/items"], []))

    def test_a_settings_file_that_cannot_be_read_leaves_the_root_unknown_and_no_route_is_claimed(self):
        self.repo({"files": tree(**{"config/settings.py": b"\xff\xfeROOT_URLCONF = 'config.urls'\n"})})
        self.entry()
        self.assertEqual(self.route_labels(), [])
        kinds = self.kinds_of_records()
        self.assertIn(("urlconf_root_unknown", "acme/portal", True), kinds)
        self.assertIn("read_unreadable", {k for k, _s, _b in kinds})

    def test_an_unreadable_file_that_might_mount_a_urlconf_stops_it_being_called_unmounted(self):
        files = legacy(**{"shop/other_urls.py": b"\xff\xfeurlpatterns = []\n"})
        res = self.changed(files, {"shop/legacy_views.py": LEGACY_VIEWS.replace("None", "1")})
        self.assertEqual(res.reached, ())
        self.assertNotIn("urlconf_unmounted", {r.kind for r in self.records})
        self.assertFalse(res.complete)
        self.assertTrue(any("may be mounted" in why for why in self.django.problems), self.django.problems)

    def test_a_urlconf_that_does_not_parse_and_might_mount_a_urlconf_stops_it_being_called_unmounted(self):
        files = legacy(**{"shop/other_urls.py": "urlpatterns = [\n    path('x/', include('shop.legacy_urls')\n"})
        res = self.changed(files, {"shop/legacy_views.py": LEGACY_VIEWS.replace("None", "1")})
        self.assertEqual(res.reached, ())
        self.assertNotIn("urlconf_unmounted", {r.kind for r in self.records})
        self.assertIn(("parse_degraded", "shop/other_urls.py", True), self.kinds_of_records())

    def test_with_everything_readable_the_same_change_says_the_urlconf_is_unmounted(self):
        res = self.changed(legacy(), {"shop/legacy_views.py": LEGACY_VIEWS.replace("None", "1")})
        self.assertEqual(res.reached, ())
        self.assertEqual([r.kind for r in self.records], ["urlconf_unmounted"])
        self.assertTrue(res.complete)


class SearchFailures(Base):
    def test_a_search_that_fails_is_an_incomplete_table_never_an_empty_one(self):
        self.repo({"files": tree()})
        self.entry()
        failed = SimpleNamespace(state=Read.UNREADABLE, detail="simulated failure", truncated_by=None, examined=0, total=0, not_searched=[], partial_matches=[])
        with mock.patch.object(self.language, "search", return_value=failed):
            self.assertEqual(self.django.routes(None, self.sha("main")), [])
        self.assertTrue(any("unreadable" in why for why in self.django.problems), self.django.problems)
        self.assertIn(("urlconf_root_unknown", "acme/portal", True), self.kinds_of_records())  # and no root was found, which is said too

    def test_a_tree_that_cannot_be_read_is_an_incomplete_table(self):
        self.repo({"files": tree()})
        self.entry()
        with mock.patch.object(self.language, "search", return_value=None):
            self.assertEqual(self.django.routes(None, self.sha("main")), [])
        self.assertTrue(any("tree could not be read" in why for why in self.django.problems), self.django.problems)

    def test_a_cut_search_marks_the_routes_it_found_as_not_established_completely(self):
        self.repo({"files": tree()}, {"files": {"shop/views.py": VIEWS.replace("JsonResponse([], safe=False)", "JsonResponse([5], safe=False)")}})
        self.entry(examine_cap=1)
        res = self.reach()
        self.assertFalse(res.complete)
        self.assertTrue(all(not r.complete for r in res.reached))
        self.assertIn("search_truncated", {r.kind for r in self.records})
        removed, added = self.django.routes_changed(self.sha("main~1"), self.sha("main"))
        self.assertEqual(removed, [])  # a table that was cut claims no removal

    def test_a_symlinked_python_file_git_grep_does_not_search_is_an_incomplete_table(self):
        repo, shas = sg.make_raw_repo(self.tmp, "links", [[
            ("100644", b"config/__init__.py", b""), ("100644", b"config/settings.py", SETTINGS.encode()), ("100644", b"config/urls.py", ROOT.encode()),
            ("100644", b"shop/__init__.py", b""), ("100644", b"shop/views.py", VIEWS.encode()), ("100644", b"shop/urls.py", URLS.encode()),
            ("120000", b"shop/more_urls.py", b"urls.py"),
        ]])
        self.path, self.url, self.records, self.shas = repo, sg.remote_url(repo), [], shas
        self.git = Git(self.tmp / "cache", env=sg.isolated_env(self.tmp / "home"), record_to=self.records)
        self.entry()
        self.django.routes(None, shas[-1])
        self.assertTrue(any("symlink" in why or "does not search" in why for why in self.django.problems), self.django.problems)
        self.assertIn("read_unreadable", {r.kind for r in self.records})


class WhatIsNotUnderstood(Base):
    def test_a_pattern_element_that_is_not_understood_is_blocking_and_removes_nothing(self):
        urls = URLS.replace("]", "] + build_more()")
        self.repo({"files": tree()}, {"files": {"shop/urls.py": urls}})
        entry = self.entry()
        removed, added = entry.routes_changed(self.sha("main~1"), self.sha("main"))
        self.assertEqual(([r.label for r in removed], added), ([], []))
        self.assertIn(("mount_unresolved", f"shop/urls.py:{ln(urls, 'build_more')}", True), self.kinds_of_records())
        self.assertEqual(self.route_labels(), ["ANY /shop/items", "ANY /shop/items/{item_id}"])  # what could be read is kept, and is not the whole table

    def test_a_view_that_cannot_be_resolved_is_blocking_and_is_never_matched_by_its_name(self):
        urls = URLS.replace("views.item_list", "views.missing_view")
        self.repo({"files": tree()}, {"files": {"shop/urls.py": urls}})
        entry = self.entry()
        removed, added = entry.routes_changed(self.sha("main~1"), self.sha("main"))
        self.assertEqual(([r.label for r in removed], added), ([], []))
        self.assertIn("view_unresolved", {k for k, _s, _b in self.kinds_of_records()})
        self.assertEqual(self.route_labels(), ["ANY /shop/items/{item_id}"])


if __name__ == "__main__":
    unittest.main()
