"""M15: what the core model and the config say about suggestions and test sources (rule 4.3: a suggestion is a place to look,
never coverage)."""

from __future__ import annotations

import ast
import inspect
import json
import textwrap
import unittest

from reachmap import config, incomplete, model
from reachmap.config import ConfigError, loads
from reachmap.errors import ModelError
from reachmap.model import Analysis, MappedTest, Suggestion, coverage_gaps
from tests.support import analysis, area, ev, screens, suggestion
from tests.test_config import MINIMAL, message_at, paths

AREA = area()


class SuggestionFields(unittest.TestCase):
    def test_a_suggestion_ties_to_an_area_and_may_carry_evidence(self):
        s = suggestion(area_id=AREA.id, evidence=[ev(path="e2e/billing.spec.ts", line=7, detail="await page.goto('/cases');")])
        self.assertEqual(s.area_id, AREA.id)
        self.assertEqual(s.evidence[0].path, "e2e/billing.spec.ts")
        self.assertEqual(Suggestion.from_dict(json.loads(json.dumps(s.to_dict()))), s)

    def test_the_new_fields_default_to_none_and_nothing(self):
        s = suggestion()
        self.assertIsNone(s.area_id)
        self.assertEqual(s.evidence, ())

    def test_the_key_order_is_the_field_order_then_the_label(self):
        self.assertEqual(list(suggestion().to_dict()), ["source", "case_id", "title", "matched_words", "area_id", "evidence", "label"])

    def test_a_suggestion_written_before_the_fields_existed_still_loads(self):
        old = {"source": "qase", "case_id": "42", "title": "t", "matched_words": ["invoice"], "label": model.SUGGESTION_LABEL}
        self.assertEqual(Suggestion.from_dict(old), suggestion(title="t"))

    def test_an_area_id_must_look_like_one(self):
        for bad in ("billing", "a-12", "f-0123456789abcdef", ""):
            with self.subTest(bad), self.assertRaises(ModelError):
                suggestion(area_id=bad)

    def test_still_always_labelled_and_never_a_mapped_test(self):
        self.assertEqual(suggestion(area_id=AREA.id).to_dict()["label"], "suggested, not verified coverage")
        self.assertNotIn("mapping", {f.name for f in __import__("dataclasses").fields(Suggestion)})
        self.assertIsNot(Suggestion, MappedTest)


class NeverCoverage(unittest.TestCase):
    def test_coverage_gaps_does_not_read_suggestions(self):
        tree = ast.parse(textwrap.dedent(inspect.getsource(coverage_gaps)))
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        self.assertFalse({n for n in names if "suggest" in n.lower()}, names)  # code, not the docstring

    def test_every_area_suggested_still_shows_every_gap(self):
        areas = screens(3)
        a = analysis(areas=areas, suggestions=[suggestion(case_id=str(i), area_id=x.id) for i, x in enumerate(areas)])
        self.assertEqual([x.id for x in coverage_gaps(a)], [x.id for x in areas])

    def test_a_mapped_test_closes_a_gap_and_a_suggestion_of_the_same_case_does_not(self):
        areas = screens(2)
        a = analysis(areas=areas, suggestions=[suggestion(case_id="7", area_id=areas[0].id), suggestion(case_id="7", area_id=areas[1].id)],
                     mapped_tests=[MappedTest(areas[0].id, "7", "t", "qase", "areas.A.suites=1")])
        self.assertEqual([x.id for x in coverage_gaps(a)], [areas[1].id])


class Kinds(unittest.TestCase):
    def test_a_cut_list_is_a_record_that_blocks_suggestions(self):
        spec = incomplete.kind_spec("suggestions_incomplete")
        self.assertTrue(spec.blocks_suggestions and spec.always_blocking and not spec.blocks_selection)
        rec = incomplete.record([], "suggestions_incomplete", "qase:ACME", detail="read 200 of 350 cases")
        self.assertIn("qase:ACME", rec.not_established)
        self.assertIn("cut", rec.not_established)

    def test_a_non_literal_spec_address_is_a_record_that_blocks_nothing(self):
        spec = incomplete.kind_spec("spec_url_partial")
        self.assertFalse(spec.blocks_selection or spec.blocks_suggestions)

    def test_source_unconfigured_blocks_nothing_and_source_unavailable_blocks_suggestions(self):
        self.assertFalse(incomplete.kind_spec("source_unconfigured").blocks_suggestions)
        self.assertTrue(incomplete.kind_spec("source_unavailable").blocks_suggestions)


ROUTER = 'entry_points = { kind = "react-router" }\n'
WEB = MINIMAL + ROUTER  # acme/web, whose routes are read, so that its screens can be matched
WEB_AND_E2E = WEB + '\n[repos."acme/e2e"]\nhost = "github"\nlanguage = "typescript"\nroles = ["frontend"]\n' + ROUTER


def load_with(sources: str, base: str = MINIMAL, env=None):
    return loads(base + "\n" + sources, fmt="toml", env={} if env is None else env)


class SourceKinds(unittest.TestCase):
    def test_the_four_kinds_are_known(self):
        for kind in ("qase", "testrail", "playwright", "cypress"):
            self.assertIn(kind, config._SECTIONS["test_sources"].kinds)

    def test_a_full_qase_table_loads(self):
        ws = load_with('[[test_sources]]\nkind = "qase"\nproject = "ACME"\ntoken_env = "QASE_API_TOKEN"\npage_size = 50\nmax_pages = 20\n'
                       'base_url = "https://qase.example.test/v1"\n\n[[test_sources.areas]]\nname = "Billing"\nroute_prefixes = ["/billing"]\n'
                       'modules = ["billing"]\nsuites = [12, 14]\n')
        (src,) = ws.test_sources
        self.assertTrue(src.configured)
        self.assertEqual(src.options["page_size"], 50)
        self.assertEqual(tuple(src.areas[0]["suites"]), (12, 14))

    def test_a_testrail_table_loads(self):
        ws = load_with('[[test_sources]]\nkind = "testrail"\nbase_url = "https://acme.testrail.example.test"\nproject_id = 3\n'
                       'token_env = "TESTRAIL_API_KEY"\n\n[[test_sources.areas]]\nname = "Billing"\nroute_prefixes = ["/billing"]\nsuites = [31]\n')
        self.assertEqual(ws.test_sources[0].options["project_id"], 3)

    def test_playwright_and_cypress_tables_load_and_name_a_configured_repository(self):
        text = ('[[test_sources]]\nkind = "playwright"\nrepo = "acme/e2e"\nscreens_repo = "acme/web"\nglobs = ["e2e/**/*.spec.ts"]\n\n'
                '[[test_sources]]\nkind = "cypress"\nrepo = "acme/e2e"\n')
        ws = load_with(text, WEB_AND_E2E)
        self.assertEqual([s.kind for s in ws.test_sources], ["playwright", "cypress"])

    def test_a_spec_source_that_names_no_configured_repository_is_a_problem(self):
        with self.assertRaises(ConfigError) as caught:
            load_with('[[test_sources]]\nkind = "playwright"\nrepo = "acme/nope"\n', WEB)
        self.assertIn("test_sources[0].repo", paths(caught.exception))
        self.assertNotIn("acme/nope", message_at(caught.exception, "test_sources[0].repo"))  # a configured value is never quoted back

    def test_the_screens_repository_is_checked_too(self):
        with self.assertRaises(ConfigError) as caught:
            load_with('[[test_sources]]\nkind = "cypress"\nrepo = "acme/web"\nscreens_repo = "acme/nope"\n', WEB)
        self.assertIn("test_sources[0].screens_repo", paths(caught.exception))

    def test_unknown_keys_and_wrong_types_are_problems_that_name_the_key(self):
        cases = {
            "colour": '[[test_sources]]\nkind = "playwright"\nrepo = "acme/web"\ncolour = "red"\n',
            "page_size": '[[test_sources]]\nkind = "qase"\npage_size = 0\n',
            "max_pages": '[[test_sources]]\nkind = "qase"\nmax_pages = "many"\n',
            "project": '[[test_sources]]\nkind = "qase"\nproject = 12\n',
            "base_url": '[[test_sources]]\nkind = "testrail"\nbase_url = "https://user:pw@host.example.test"\n',
            "project_id": '[[test_sources]]\nkind = "testrail"\nproject_id = "one"\n',
            "globs": '[[test_sources]]\nkind = "playwright"\nrepo = "acme/web"\nglobs = "e2e"\n',
        }
        for key, text in cases.items():
            with self.subTest(key), self.assertRaises(ConfigError) as caught:
                load_with(text, WEB)
            self.assertTrue(any(key in m.message or key in m.path for m in caught.exception.problems), caught.exception.problems)

    def test_a_credential_in_a_url_is_refused_without_being_shown(self):
        with self.assertRaises(ConfigError) as caught:
            load_with('[[test_sources]]\nkind = "testrail"\nbase_url = "https://user:hunter2xyz@host.example.test"\n')
        self.assertNotIn("hunter2xyz", str(caught.exception))

    def test_an_area_has_a_name_and_the_four_keys_only(self):
        bad = {
            "colour": '[[test_sources]]\nkind = "qase"\n[[test_sources.areas]]\nname = "A"\ncolour = "red"\n',
            "route_prefixes": '[[test_sources]]\nkind = "qase"\n[[test_sources.areas]]\nname = "A"\nroute_prefixes = "/a"\n',
            "suites": '[[test_sources]]\nkind = "qase"\n[[test_sources.areas]]\nname = "A"\nsuites = ["x"]\n',
            "modules": '[[test_sources]]\nkind = "qase"\n[[test_sources.areas]]\nname = "A"\nmodules = [1]\n',
        }
        for key, text in bad.items():
            with self.subTest(key), self.assertRaises(ConfigError) as caught:
                load_with(text)
            self.assertTrue(any(key in m.message for m in caught.exception.problems), caught.exception.problems)

    def test_a_hosted_source_refuses_a_key_it_does_not_know_and_says_which_it_does(self):
        for key in ("max_page", "tokn_env", "projekt"):
            with self.subTest(key), self.assertRaises(ConfigError) as caught:
                load_with(f'[[test_sources]]\nkind = "qase"\nproject = "ACME"\n{key} = 3\n')
            message = " ".join(m.message for m in caught.exception.problems)
            self.assertIn(key, message)
            self.assertIn("allowed keys", message)
        with self.assertRaises(ConfigError) as caught:
            load_with('[[test_sources]]\nkind = "qase"\nproject = "ACME"\ntokn_env = "X"\n')
        self.assertIn("token_env", " ".join(m.message for m in caught.exception.problems))  # a near miss is named

    def test_a_hosted_area_refuses_token_env(self):
        with self.assertRaises(ConfigError) as caught:
            load_with('[[test_sources]]\nkind = "qase"\nproject = "ACME"\n[[test_sources.areas]]\nname = "A"\nroute_prefixes = ["/a"]\ntoken_env = "QASE_API_TOKEN"\n')
        self.assertTrue(any("areas[0].token_env" in m.message for m in caught.exception.problems), caught.exception.problems)

    def test_the_spec_source_error_does_not_offer_areas(self):
        with self.assertRaises(ConfigError) as caught:
            load_with('[[test_sources]]\nkind = "playwright"\nrepo = "acme/web"\ncolour = "red"\n', WEB)
        message = " ".join(m.message for m in caught.exception.problems)
        self.assertIn("allowed keys", message)
        self.assertNotIn("areas", message)

    def test_a_spec_source_needs_a_repository_whose_entry_points_produce_screens(self):
        for entry, why in (("", "no entry_points"), ('entry_points = { kind = "fastapi" }\n', "fastapi"), ('entry_points = { kind = "celery" }\n', "celery")):
            with self.subTest(why), self.assertRaises(ConfigError) as caught:
                load_with('[[test_sources]]\nkind = "playwright"\nrepo = "acme/web"\n', MINIMAL + entry)
            self.assertIn("test_sources[0].repo", paths(caught.exception))
            self.assertIn("screens", message_at(caught.exception, "test_sources[0].repo"))

    def test_the_problem_is_at_screens_repo_when_the_table_names_one(self):
        base = WEB + '\n[repos."acme/api"]\nhost = "github"\nlanguage = "python"\nroles = ["backend"]\nentry_points = { kind = "fastapi" }\n'
        with self.assertRaises(ConfigError) as caught:
            load_with('[[test_sources]]\nkind = "cypress"\nrepo = "acme/web"\nscreens_repo = "acme/api"\n', base)
        self.assertEqual(paths(caught.exception), ["test_sources[0].screens_repo"])
        self.assertTrue(load_with('[[test_sources]]\nkind = "cypress"\nrepo = "acme/api"\nscreens_repo = "acme/web"\n', base).test_sources)

    def test_react_router_entry_points_are_accepted(self):
        self.assertTrue(load_with('[[test_sources]]\nkind = "playwright"\nrepo = "acme/web"\n', WEB).test_sources)

    def test_the_existing_minimal_qase_table_still_loads(self):
        ws = load_with('[[test_sources]]\nkind = "qase"\ntoken_env = "QASE_API_TOKEN"\n')
        self.assertEqual(ws.test_sources[0].options["token_env"], "QASE_API_TOKEN")

    def test_an_unset_variable_still_means_not_configured(self):
        ws = load_with('[[test_sources]]\nkind = "qase"\nproject = "${QASE_PROJECT}"\n')
        self.assertFalse(ws.test_sources[0].configured)
        self.assertEqual(ws.test_sources[0].unset_vars, ("QASE_PROJECT",))


if __name__ == "__main__":
    unittest.main()
