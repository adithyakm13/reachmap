"""The Django entry-point kind in the config (`entry_points = { kind = "django", ... }`), the registry that builds its adapter, and the hooks it
gives the Python adapter (installed packages, conventional names).
"""

import unittest
from unittest import mock

from reachmap import config, entrypoints
from reachmap.adapters import third_party
from reachmap.adapters.django import DjangoAdapter
from reachmap.adapters.python import PyAdapter
from reachmap.config import ConfigError
from tests.support_django import ROOT, URLS, VIEWS, Base, f, tree

HEAD = 'workspace = "acme"\n[repos."acme/portal"]\nhost = "github"\nlanguage = "python"\nroles = ["backend"]\n'


def load(entry: str) -> config.Workspace:
    return config.loads(HEAD + f"entry_points = {entry}\n", fmt="toml", env={})


class ConfigKind(unittest.TestCase):
    def test_the_kind_is_known_without_options(self):
        ws = load('{ kind = "django" }')
        self.assertEqual(dict(ws.repos["acme/portal"].entry_points), {"kind": "django"})

    def test_the_two_options_are_kept(self):
        ws = load('{ kind = "django", root_urlconf = "config.urls", settings = "config/settings" }')
        self.assertEqual(dict(ws.repos["acme/portal"].entry_points), {"kind": "django", "root_urlconf": "config.urls", "settings": "config/settings"})

    def test_a_key_the_kind_does_not_know_is_a_problem_naming_it(self):
        with self.assertRaises(ConfigError) as cm:
            load('{ kind = "django", rooturlconf = "config.urls" }')
        self.assertIn("rooturlconf", str(cm.exception))
        self.assertIn("root_urlconf", str(cm.exception))  # the keys that are allowed

    def test_a_root_urlconf_must_be_a_dotted_module_name_and_the_message_never_quotes_it(self):
        for bad in ("config/urls.py", "config.urls.", "1config.urls", "config urls"):
            with self.subTest(bad=bad), self.assertRaises(ConfigError) as cm:
                load(f'{{ kind = "django", root_urlconf = "{bad}" }}')
            self.assertIn("root_urlconf", str(cm.exception))
            self.assertNotIn(bad, str(cm.exception))

    def test_settings_must_be_a_path_inside_the_repository(self):
        for bad in ("/etc/settings.py", "../settings.py", "a/../../b"):
            with self.subTest(bad=bad), self.assertRaises(ConfigError) as cm:
                load(f'{{ kind = "django", settings = "{bad}" }}')
            self.assertIn("settings", str(cm.exception))
            self.assertNotIn(bad, str(cm.exception))

    def test_an_option_that_is_not_a_string_says_the_type_not_the_value(self):
        with self.assertRaises(ConfigError) as cm:
            load('{ kind = "django", root_urlconf = 42 }')
        self.assertIn("root_urlconf", str(cm.exception))
        self.assertNotIn("42", str(cm.exception))


class Registry(Base):
    def test_the_config_selects_the_adapter_on_the_python_adapter(self):
        ws = config.loads(HEAD + 'entry_points = { kind = "django", root_urlconf = "config.urls", settings = "config/settings.py" }\n', fmt="toml", env={})
        self.repo({"files": tree()})
        language = self.make_adapter()
        entry = entrypoints.entry_adapter(ws.repos["acme/portal"], language)
        self.assertIsInstance(entry, DjangoAdapter)
        self.assertEqual((entry.root_urlconf, entry.settings, entry.repo), ("config.urls", "config/settings.py", "acme/portal"))
        self.assertIn("django", entrypoints.KINDS)
        self.assertEqual([r.label for r in entry.routes(None, self.sha("main"))], ["ANY /shop/items", "ANY /shop/items/{item_id}"])

    def test_a_language_that_is_not_python_is_refused(self):
        ws = config.loads(HEAD.replace('"python"', '"typescript"') + 'entry_points = { kind = "django" }\n', fmt="toml", env={})
        with self.assertRaises(TypeError):
            entrypoints.entry_adapter(ws.repos["acme/portal"], mock.Mock())

    def test_the_records_the_adapter_makes_are_registered_with_the_wording_the_kinds_have(self):
        from reachmap import incomplete

        self.assertFalse(incomplete.kind_spec("include_external").blocks_selection)
        self.assertFalse(incomplete.kind_spec("route_methods_unknown").blocks_selection)
        for kind in ("urlconf_root_unknown", "view_unresolved", "settings_not_followed"):
            self.assertTrue(incomplete.kind_spec(kind).blocks_selection, kind)
        rec = incomplete.record([], "view_unresolved", "shop/urls.py:5")
        self.assertEqual(rec.not_established, "What shop/urls.py:5 runs was not established, because a view there could not be resolved to code of the repository")


class PythonAdapterHooks(Base):
    """`reachmap.adapters.third_party`: what the Django kind tells the Python adapter about its framework."""

    DJANGO_IMPORTER = f(["from django.urls import path", "", "urlpatterns = [path('x/', lambda r: None)]"])

    def analyze_urls_change(self, other_import):
        files = tree(**{"shop/other.py": other_import})
        self.repo({"files": files}, {"files": {"shop/urls.py": URLS + "\n# changed\nextra = 1\n"}})
        return self.analyze(self.make_adapter())

    def test_an_import_of_an_installed_package_that_spells_the_name_of_the_changed_file_is_not_a_miss(self):
        self.assertIn("django", third_party.names_for("django")[0])
        self.assertEqual(third_party.names_for("fastapi"), (frozenset(), frozenset()))
        self.analyze_urls_change("from django.urls import path\n")
        self.assertEqual(self.records, [])
        self.assertTrue(self.impact("shop/urls.py").importers_complete)

    def test_a_package_nobody_registered_still_is_a_miss(self):
        self.analyze_urls_change("from flask.urls import path\n")
        self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in self.records], [("specifier_unresolved", "flask", True)])
        self.assertFalse(self.impact("shop/urls.py").importers_complete)

    def test_a_module_next_to_the_importer_that_could_shadow_the_package_keeps_the_record(self):
        files = tree(**{"shop/other.py": "from django.urls import path\n", "shop/django/__init__.py": "", "shop/django/urls.py": "path = 1\n"})
        self.repo({"files": files}, {"files": {"shop/urls.py": URLS + "\nextra = 1\n"}})
        self.analyze(self.make_adapter())
        self.assertEqual([(r.kind, r.subject) for r in self.records], [("specifier_unresolved", "django")])  # a script run from that folder would import the sibling

    def test_urlpatterns_is_never_counted_for_distinctiveness_and_another_repeated_name_still_is(self):
        files = tree(**{"shop/a_urls.py": "urlpatterns = []\nlabel = 1\n", "shop/b_urls.py": "urlpatterns = []\nlabel = 2\n", "shop/c_urls.py": "urlpatterns = []\nlabel = 3\n"})
        urls = URLS + "\nlabel = 0\n"
        self.repo({"files": {**files, "shop/urls.py": URLS + "\nlabel = 4\n"}}, {"files": {"shop/urls.py": urls.replace("urlpatterns = [", "urlpatterns = [  # changed")}})
        self.analyze(self.make_adapter(distinct_max=3))
        subjects = [(r.kind, r.subject) for r in self.records]
        self.assertIn(("not_distinctive", "label"), subjects)
        self.assertNotIn(("not_distinctive", "urlpatterns"), subjects)
        self.assertEqual(third_party.names_for("django")[1], frozenset({"urlpatterns", "app_name"}))


if __name__ == "__main__":
    unittest.main()
