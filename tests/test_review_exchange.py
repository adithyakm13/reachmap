"""The decisions file the HTML report exports and `--decisions` imports (M11): strict about the schema, the key, the target kinds and the
fingerprints, and it keeps the fingerprint the page exported (the one in the report the person looked at)."""

import copy
import json
import unittest

from reachmap import wording
from reachmap.review import exchange, store

KEY = store.make_key("acme", "acme/web", "compare:main...topic")
SHA = "a" * 40
A1, A2, F1 = "a-" + "1" * 16, "a-" + "2" * 16, "f-" + "3" * 16
FP1, FP2, FP3 = "1" * 64, "2" * 64, "3" * 64


def doc(**over):
    base = {
        "format": "reachmap-decisions", "version": 1,
        "report": {"key": dict(KEY), "head_sha": SHA, "revision": 2},
        "exported_at": "2026-02-03T04:05:06.789Z",
        "decisions": [
            {"target_id": A1, "verdict": "skip", "reason": "not in this release", "author": "Quinn", "fingerprint": FP1, "created_at": "2026-02-03T04:00:00.000Z"},
            {"target_id": F1, "verdict": "reject", "reason": "wrong service", "author": "Quinn", "fingerprint": FP3, "created_at": "2026-02-03T04:01:00.000Z"},
            {"target_id": A2, "verdict": "test", "reason": "", "author": "Sam", "fingerprint": FP2, "created_at": "2026-02-03T04:02:00.000Z"},
        ],
    }
    base.update(over)
    return base


def text(d):
    return json.dumps(d)


class Parsing(unittest.TestCase):
    def test_a_file_the_page_exports_is_read(self):
        got = exchange.parse(text(doc()))
        self.assertEqual(got.key, KEY)
        self.assertEqual((got.head_sha, got.revision), (SHA, 2))
        self.assertEqual([d.target_id for d in got.decisions], [A1, F1, A2])
        self.assertEqual(got.decisions[0].reason, "not in this release")

    def test_the_decisions_become_stored_decisions_with_the_channel_html_import_and_the_exported_fingerprint_and_revision(self):
        items = exchange.parse(text(doc())).to_stored()
        self.assertEqual([i.decision.channel for i in items], ["html-import"] * 3)
        self.assertEqual([i.decision.fingerprint for i in items], [FP1, FP3, FP2])
        self.assertEqual([i.decision.revision for i in items], [2, 2, 2])
        self.assertEqual(items[0].created_at, "2026-02-03T04:00:00.000Z")
        self.assertEqual(items[0].decision.author, "Quinn")

    def test_a_channel_the_file_names_is_not_trusted_the_import_always_stamps_html_import(self):
        d = doc()
        d["decisions"][0]["channel"] = "runner"
        with self.assertRaises(exchange.ExchangeError):
            exchange.parse(text(d))  # not a key of the format at all

    def test_a_file_for_another_analysis_key_is_refused_with_a_message_that_says_which(self):
        got = exchange.parse(text(doc()))
        for other in (store.make_key("acme", "acme/api", "compare:main...topic"), store.make_key("acme", "acme/web", "compare:main...other"), store.make_key("other", "acme/web", "compare:main...topic")):
            with self.subTest(other):
                with self.assertRaises(exchange.ExchangeError) as ctx:
                    got.require_key(other)
                self.assertIn("another analysis", str(ctx.exception))
        got.require_key(KEY)

    def test_the_message_for_another_key_shows_the_files_key_without_letting_it_act(self):
        hostile = store.make_key("acme", "acme/web\x1b[31m\u202e", "compare:safe...<img src=x>")
        got = exchange.parse(text(doc(report={"key": hostile, "head_sha": SHA, "revision": 1})))
        with self.assertRaises(exchange.ExchangeError) as ctx:
            got.require_key(KEY)
        msg = str(ctx.exception)
        self.assertNotIn("\x1b", msg)
        self.assertNotIn("\u202e", msg)
        self.assertEqual(wording.check(msg.replace("safe", "")), [])

    def test_a_decision_without_an_id_of_an_area_or_finding_or_with_a_verdict_that_does_not_fit_its_kind_is_refused(self):
        for index, change in (
            (0, {"target_id": "x-" + "1" * 16}), (0, {"target_id": "a-short"}), (0, {"verdict": "accept"}), (1, {"verdict": "skip"}), (1, {"verdict": "test"}),
            (0, {"verdict": "maybe"}), (0, {"verdict": None}),
        ):
            with self.subTest(change):
                d = doc()
                d["decisions"][index].update(change)
                with self.assertRaises(exchange.ExchangeError) as ctx:
                    exchange.parse(text(d))
                self.assertIn(f"decision {index + 1}", str(ctx.exception))

    def test_skip_and_reject_need_a_reason(self):
        for index in (0, 1):
            for reason in ("", "   "):
                d = doc()
                d["decisions"][index]["reason"] = reason
                with self.assertRaises(exchange.ExchangeError):
                    exchange.parse(text(d))

    def test_the_fingerprint_must_be_the_exported_64_hex(self):
        for bad in ("", "abc", "G" * 64, "1" * 63, None, 5):
            d = doc()
            d["decisions"][0]["fingerprint"] = bad
            with self.subTest(bad):
                with self.assertRaises(exchange.ExchangeError):
                    exchange.parse(text(d))

    def test_the_schema_is_exact_a_missing_or_an_extra_key_a_wrong_format_or_a_wrong_version_is_refused(self):
        cases = []
        for key in ("format", "version", "report", "exported_at", "decisions"):
            d = doc()
            del d[key]
            cases.append(d)
        cases += [doc(format="other"), doc(version=2), doc(version=True), doc(extra=1), doc(decisions="x"), doc(decisions=[1]), doc(report="x")]
        for sub in ("key", "head_sha", "revision"):
            d = doc()
            del d["report"][sub]
            cases.append(d)
        d = doc()
        d["decisions"][0]["extra"] = 1
        cases.append(d)
        d = doc()
        del d["decisions"][0]["author"]
        cases.append(d)
        for d in cases:
            with self.subTest(str(sorted(d))[:50]):
                with self.assertRaises(exchange.ExchangeError):
                    exchange.parse(text(d))

    def test_the_head_the_revision_and_the_times_are_checked(self):
        for report in ({"key": dict(KEY), "head_sha": "abc", "revision": 1}, {"key": dict(KEY), "head_sha": SHA, "revision": 0}, {"key": dict(KEY), "head_sha": SHA, "revision": "1"},
                       {"key": dict(KEY), "head_sha": SHA, "revision": True}, {"key": {"workspace": "a", "repo": "b"}, "head_sha": SHA, "revision": 1},
                       {"key": {**KEY, "change": ""}, "head_sha": SHA, "revision": 1}):
            with self.subTest(report):
                with self.assertRaises(exchange.ExchangeError):
                    exchange.parse(text(doc(report=report)))
        for bad in ("yesterday", "2026-02-03", "", 5, None):
            d = doc()
            d["decisions"][0]["created_at"] = bad
            with self.assertRaises(exchange.ExchangeError):
                exchange.parse(text(d))
        with self.assertRaises(exchange.ExchangeError):
            exchange.parse(text(doc(exported_at="later")))

    def test_a_reason_or_author_too_long_is_refused_and_so_are_too_many_decisions(self):
        d = doc()
        d["decisions"][0]["reason"] = "x" * (store.MAX_REASON + 1)
        with self.assertRaises(exchange.ExchangeError):
            exchange.parse(text(d))
        d = doc()
        d["decisions"][0]["author"] = "x" * (store.MAX_AUTHOR + 1)
        with self.assertRaises(exchange.ExchangeError):
            exchange.parse(text(d))
        d = doc()
        d["decisions"] = d["decisions"][:1] * (exchange.MAX_DECISIONS + 1)
        with self.assertRaises(exchange.ExchangeError):
            exchange.parse(text(d))

    def test_text_that_cannot_be_written_to_the_store_is_refused_not_a_traceback(self):
        d = doc()
        d["decisions"][0]["reason"] = "\ud800"
        with self.assertRaises(exchange.ExchangeError):
            exchange.parse(json.dumps(d))  # json.dumps writes the lone surrogate as a \ud800 escape; json.loads reads it back as one

    def test_hostile_but_writable_strings_are_kept_as_they_are(self):
        reason = "</script><script>alert(1)</script> <!-- <img src=x onerror=alert(1)> \"><svg onload=alert(1)> javascript:alert(1) ]]> " + "`" * 10 + " [x](javascript:alert(1)) <details open> safe"
        d = doc()
        d["decisions"][0]["reason"] = reason
        d["decisions"][0]["author"] = "</b>Quinn"
        got = exchange.parse(text(d))
        self.assertEqual(got.decisions[0].reason, reason)
        self.assertEqual(got.decisions[0].author, "</b>Quinn")

    def test_control_and_direction_characters_are_refused_in_the_author_and_the_reason(self):
        for field, value in (("author", "Quinn\u202e"), ("author", "Quinn\x1b"), ("author", "Quinn\n"), ("author", "Quinn\u200b"), ("reason", "a \u202e b"),
                             ("reason", "a \u2028 b"), ("reason", "a \x1b[31m b"), ("reason", "a\rb")):
            with self.subTest(field=field, value=value):
                d = doc()
                d["decisions"][0][field] = value
                with self.assertRaises(exchange.ExchangeError) as ctx:
                    exchange.parse(text(d))
                msg = str(ctx.exception)
                self.assertIn(f"decision 1.{field}", msg)
                for raw in ("\u202e", "\x1b", "\u200b", "\u2028", "\r", "\n"):
                    self.assertNotIn(raw, msg)

    def test_a_newline_and_a_tab_in_a_reason_are_allowed(self):
        d = doc()
        d["decisions"][0]["reason"] = "line one\nline two\tend"
        self.assertEqual(exchange.parse(text(d)).decisions[0].reason, "line one\nline two\tend")

    def test_what_is_not_json_or_not_an_object_or_too_deep_or_not_finite_is_refused(self):
        for bad in ("", "not json", "[]", "5", "null", "[" * 100000, '{"format": NaN}', '{"version": 1e999}', "\ufeff" + text(doc())):
            with self.subTest(bad[:20]):
                with self.assertRaises(exchange.ExchangeError):
                    exchange.parse(bad)

    def test_the_messages_name_the_decision_and_the_rule_and_never_echo_the_file(self):
        d = doc()
        d["decisions"][0]["target_id"] = "<script>alert(1)</script>"
        with self.assertRaises(exchange.ExchangeError) as ctx:
            exchange.parse(text(d))
        self.assertNotIn("script", str(ctx.exception))
        self.assertEqual(wording.check(str(ctx.exception)), [])

    def test_a_file_that_is_too_big_is_refused_before_it_is_parsed(self):
        with self.assertRaises(exchange.ExchangeError):
            exchange.parse("x" * (exchange.MAX_CHARS + 1))

    def test_the_parse_does_not_change_what_it_is_given(self):
        raw = text(doc())
        before = copy.copy(raw)
        exchange.parse(raw)
        self.assertEqual(raw, before)


if __name__ == "__main__":
    unittest.main()
