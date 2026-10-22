"""M15 review: a test source can fail in ways nobody listed (JSON nested deeper than the parser allows, a reader with a bug); that is
a source that did not answer, and never a run that fails. Only the run's own deadline stops the run."""

from __future__ import annotations

import unittest
from unittest import mock

from reachmap import testsources
from reachmap.hosts import HostBase, ResponseUnusable
from reachmap.testsources import StopRun, qase, run_all
from tests import support_testsources as st
from tests.support_testsources import QASE, QASE_ENV, QASE_ROUTES, TESTRAIL_ENV, TESTRAIL_ROUTES, Fake
from tests.test_testsources_run import analysis, workspace

DEEP = b"[" * 200000 + b"]" * 200000


class Deep(st.HostedCase):
    def test_json_nested_too_deeply_is_not_valid_json_to_the_host_layer(self):
        h = HostBase(transport=Fake({"https://x.example/a": (200, {}, DEEP)}), api_url="https://x.example")
        with self.assertRaises(ResponseUnusable) as caught:
            h._get_json("https://x.example/a")
        self.assertIn("not valid JSON", str(caught.exception))

    def test_a_deep_answer_is_a_source_that_did_not_answer(self):
        out = run_all(workspace(st.qase_table()), analysis(), env=QASE_ENV, transport=Fake({f"{QASE}/project/ACME": (200, {}, DEEP)}))
        (rec,) = out.records
        self.assertEqual((rec.kind, rec.blocks_suggestions, rec.blocks_selection), ("source_unavailable", True, False))
        self.assertEqual(out.suggestions, ())


class AnythingElse(st.HostedCase):
    def test_a_reader_that_raises_is_unavailable_and_the_record_names_the_type_only(self):
        boom = RuntimeError(f"it broke with {st.TOKEN} in the message")
        with mock.patch.object(qase.Qase, "fetch", side_effect=boom):
            out = run_all(workspace(st.qase_table()), analysis(), env=QASE_ENV, transport=Fake(QASE_ROUTES))
        (rec,) = out.records
        self.assertEqual(rec.kind, "source_unavailable")
        self.assertIn("RuntimeError", rec.detail)
        self.assertNotIn("it broke", rec.detail)  # a message may hold anything; the type says what kind of failure it was
        self.assertNotIn(st.TOKEN, rec.detail)
        self.assertEqual(out.reads[0].state, "unavailable")

    def test_the_other_sources_are_still_read(self):
        with mock.patch.object(qase.Qase, "fetch", side_effect=KeyError("x")):
            out = run_all(workspace(st.qase_table(), st.testrail_table()), analysis(), env={**QASE_ENV, **TESTRAIL_ENV}, transport=Fake({**QASE_ROUTES, **TESTRAIL_ROUTES}))
        self.assertEqual([r.kind for r in out.records], ["source_unavailable"])
        self.assertEqual({s.source for s in out.suggestions}, {"testrail:3"})

    def test_a_source_that_cannot_even_be_asked_whether_it_is_configured_is_unavailable_too(self):
        with mock.patch.object(qase.Qase, "unconfigured", side_effect=ValueError("x")):
            out = run_all(workspace(st.qase_table()), analysis(), env=QASE_ENV, transport=Fake(QASE_ROUTES))
        self.assertEqual([r.kind for r in out.records], ["source_unavailable"])

    def test_the_deadline_stops_the_run_and_is_not_swallowed(self):
        class Over(StopRun):
            pass

        def check():
            raise Over("time is up")

        with self.assertRaises(Over):
            run_all(workspace(st.qase_table()), analysis(), env=QASE_ENV, transport=Fake(QASE_ROUTES), check=check)

    def test_a_keyboard_interrupt_is_not_a_source_failure(self):
        with mock.patch.object(qase.Qase, "fetch", side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                run_all(workspace(st.qase_table()), analysis(), env=QASE_ENV, transport=Fake(QASE_ROUTES))


if __name__ == "__main__":
    unittest.main()
