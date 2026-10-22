"""The decision store and the revision log (M11): JSON lines beside the reports, append only, atomic per line, a corrupt line is
reported and never read as "no decisions", the later decision for a target supersedes the earlier (both kept), and nothing in
either file is a secret or a path of this machine."""

import json
import os
import re
import unittest
from pathlib import Path

from reachmap import wording
from reachmap.model import Decision
from reachmap.review import revisions, store
from tests import support_git as sg

KEY = store.make_key("acme", "acme/web", "compare:main...topic")
A1 = "a-" + "1" * 16
A2 = "a-" + "2" * 16
F1 = "f-" + "3" * 16
FP = "0" * 64
FP2 = "f" * 64


def stored(target=A1, verdict="test", reason="", author="Quinn", channel="cli", fingerprint=FP, revision=1, at="2026-01-02T03:04:05Z"):
    return store.Stored(Decision(target, verdict, reason, author, channel, fingerprint, revision), at)


class Keys(unittest.TestCase):
    def test_a_pull_request_is_known_by_its_host_and_number_and_a_comparison_by_its_refs_as_written(self):
        self.assertEqual(store.change_identity({"pr": "12"}, "github"), "pr:github:12")
        self.assertEqual(store.change_identity({"compare": "release/3...main"}, "gitlab"), "compare:release/3...main")
        self.assertEqual(store.change_identity({"range": "v1..v2"}, "git"), "range:v1..v2")

    def test_the_same_change_on_another_host_or_with_other_refs_is_another_key(self):
        slugs = {
            store.key_slug(store.make_key("w", "r", store.change_identity(spec, host)))
            for spec, host in (({"pr": "12"}, "github"), ({"pr": "12"}, "gitlab"), ({"pr": "13"}, "github"), ({"compare": "a...b"}, "git"), ({"compare": "a...c"}, "git"))
        }
        self.assertEqual(len(slugs), 5)

    def test_the_slug_is_a_safe_file_name_whatever_the_names_are(self):
        key = store.make_key("a/b", "../../etc/passwd", "compare:x...y\n..\\z")
        slug = store.key_slug(key)
        self.assertRegex(slug, r"^[A-Za-z0-9._-]+$")
        self.assertNotIn("..", slug)
        self.assertLessEqual(len(slug), 150)

    def test_two_keys_whose_readable_parts_are_equal_still_differ(self):
        self.assertNotEqual(store.key_slug(store.make_key("w", "a/b", "compare:x...y")), store.key_slug(store.make_key("w", "a-b", "compare:x...y")))


class Appending(sg.TempDirCase):
    def test_the_store_is_a_file_per_key_under_dot_reachmap_decisions_beside_the_reports(self):
        store.append(self.tmp, KEY, [stored()])
        path = store.decisions_path(self.tmp, KEY)
        self.assertEqual(path.parent, self.tmp / ".reachmap" / "decisions")
        self.assertEqual(path.name, store.key_slug(KEY) + ".jsonl")
        self.assertTrue(path.is_file())

    def test_a_decision_is_one_json_line_and_a_second_is_the_next_line(self):
        store.append(self.tmp, KEY, [stored(A1, "test")])
        store.append(self.tmp, KEY, [stored(A2, "skip", "not used in this release", "Sam")])
        lines = store.decisions_path(self.tmp, KEY).read_bytes().split(b"\n")
        self.assertEqual(lines[-1], b"")
        rows = [json.loads(x) for x in lines[:-1]]
        self.assertEqual([r["target_id"] for r in rows], [A1, A2])
        self.assertEqual(rows[1]["reason"], "not used in this release")
        for row in rows:
            self.assertEqual(set(row), {"v", "target_id", "verdict", "reason", "author", "channel", "fingerprint", "revision", "created_at"})

    def test_a_line_holds_ascii_only_so_that_no_separator_in_a_reason_can_split_it(self):
        reason = "a\u2028b\u2029c\x85d \u202e \U0001f600 \x1b[31m ]]> <script>"
        store.append(self.tmp, KEY, [stored(A1, "skip", reason)])
        raw = store.decisions_path(self.tmp, KEY).read_bytes()
        raw.decode("ascii")
        self.assertEqual(raw.count(b"\n"), 1)
        (item,) = store.read(self.tmp, KEY).items
        self.assertEqual(item.decision.reason, reason)

    def test_the_folder_is_made_and_the_line_is_flushed_and_synced(self):
        calls = []
        real = os.fsync
        os.fsync = lambda fd: (calls.append(fd), real(fd))[1]
        try:
            store.append(self.tmp / "deep" / "reports", KEY, [stored()])
        finally:
            os.fsync = real
        self.assertTrue(calls)
        self.assertEqual(len(store.read(self.tmp / "deep" / "reports", KEY).items), 1)

    def test_every_line_is_written_with_a_write_of_its_own(self):
        writes = []
        real = os.write
        os.write = lambda fd, data: (writes.append(bytes(data)), real(fd, data))[1]
        try:
            store.append(self.tmp, KEY, [stored(A1), stored(A2)])
        finally:
            os.write = real
        self.assertEqual([w.count(b"\n") for w in writes], [1, 1])
        self.assertTrue(all(w.endswith(b"\n") for w in writes))

    def test_a_store_with_an_unfinished_last_line_is_not_appended_to(self):
        store.append(self.tmp, KEY, [stored(A1)])
        path = store.decisions_path(self.tmp, KEY)
        with open(path, "ab") as f:
            f.write(b'{"v":1,"target_id":"a-')
        before = path.read_bytes()
        with self.assertRaises(store.StoreError) as ctx:
            store.append(self.tmp, KEY, [stored(A2)])
        self.assertIn("line 2", str(ctx.exception))
        self.assertEqual(path.read_bytes(), before)

    def test_what_cannot_be_written_is_a_store_error_naming_the_file_not_a_path(self):
        blocker = self.tmp / "blocked"
        blocker.write_text("a file where a folder must be")
        with self.assertRaises(store.StoreError) as ctx:
            store.append(blocker, KEY, [stored()])
        self.assertNotIn(str(self.tmp), str(ctx.exception))

    def test_a_reason_or_an_author_that_is_too_long_or_a_blank_author_is_refused_before_anything_is_written(self):
        for bad in (stored(verdict="skip", reason="x" * (store.MAX_REASON + 1)), stored(author="n" * (store.MAX_AUTHOR + 1))):
            with self.subTest(bad.decision.author[:3]):
                with self.assertRaises(store.StoreError):
                    store.append(self.tmp, KEY, [bad])
        self.assertFalse(store.decisions_path(self.tmp, KEY).exists())

    def test_the_time_must_be_an_iso_utc_time(self):
        for bad in ("yesterday", "2026-01-02", "2026-01-02 03:04:05", "2026-01-02T03:04:05+01:00", ""):
            with self.subTest(bad):
                with self.assertRaises(store.StoreError):
                    store.append(self.tmp, KEY, [stored(at=bad)])


class Reading(sg.TempDirCase):
    def test_no_file_is_no_decisions_and_that_is_established(self):
        got = store.read(self.tmp, KEY)
        self.assertEqual((got.items, got.problem), ((), None))

    def test_an_empty_file_is_no_decisions(self):
        path = store.decisions_path(self.tmp, KEY)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"")
        self.assertEqual((store.read(self.tmp, KEY).items, store.read(self.tmp, KEY).problem), ((), None))

    def test_decisions_come_back_in_the_order_they_were_written(self):
        store.append(self.tmp, KEY, [stored(A1, "test"), stored(F1, "reject", "wrong repo", revision=2), stored(A2, "sample")])
        got = store.read(self.tmp, KEY)
        self.assertIsNone(got.problem)
        self.assertEqual([i.decision.target_id for i in got.items], [A1, F1, A2])
        self.assertEqual(got.items[1].decision, Decision(F1, "reject", "wrong repo", "Quinn", "cli", FP, 2))
        self.assertEqual(got.items[1].created_at, "2026-01-02T03:04:05Z")

    def test_a_later_decision_for_the_same_target_supersedes_the_earlier_and_both_are_kept(self):
        store.append(self.tmp, KEY, [stored(A1, "test", at="2026-01-01T00:00:00Z")])
        store.append(self.tmp, KEY, [stored(A2, "sample")])
        store.append(self.tmp, KEY, [stored(A1, "skip", "not in this release", at="2026-01-03T00:00:00Z")])
        got = store.read(self.tmp, KEY)
        self.assertEqual(len(got.items), 3)  # nothing is rewritten or removed
        latest = store.latest(got.items)
        self.assertEqual(set(latest), {A1, A2})
        self.assertEqual(latest[A1].decision.verdict, "skip")
        self.assertEqual(len([r for r in store.decisions_path(self.tmp, KEY).read_bytes().split(b"\n") if r]), 3)

    def test_the_order_of_the_file_decides_not_the_time_stamp(self):
        store.append(self.tmp, KEY, [stored(A1, "test", at="2030-01-01T00:00:00Z")])
        store.append(self.tmp, KEY, [stored(A1, "sample", at="2020-01-01T00:00:00Z")])  # a clock set back
        self.assertEqual(store.latest(store.read(self.tmp, KEY).items)[A1].decision.verdict, "sample")

    def corrupt(self, tail: bytes, keep=1):
        store.append(self.tmp, KEY, [stored(A1)] * keep)
        with open(store.decisions_path(self.tmp, KEY), "ab") as f:
            f.write(tail)
        return store.read(self.tmp, KEY)

    def test_a_partial_last_line_is_a_problem_naming_the_line_and_no_decision_is_shown(self):
        got = self.corrupt(b'{"v":1,"target_id":"a-1111')
        self.assertIsNotNone(got.problem)
        self.assertEqual(got.problem.line, 2)
        self.assertEqual(got.items, ())  # never read as the decisions that were intact: a later line may have superseded one of them

    def test_a_complete_but_wrong_last_line_is_a_problem_too(self):
        for tail in (b"not json\n", b"[]\n", b"{}\n", b'{"v":2}\n', b"\n", b'{"v":1,"target_id":"x"}\n'):
            with self.subTest(tail):
                got = self.corrupt(tail)
                self.assertIsNotNone(got.problem)
                self.assertEqual(got.items, ())
                os.unlink(store.decisions_path(self.tmp, KEY))

    def test_a_corrupt_line_in_the_middle_is_a_problem_not_skipped(self):
        store.append(self.tmp, KEY, [stored(A1, "test")])
        with open(store.decisions_path(self.tmp, KEY), "ab") as f:
            f.write(b"garbage\n")
        # a store with garbage is not appended to, so the later line is written by hand
        with open(store.decisions_path(self.tmp, KEY), "ab") as f:
            f.write(json.dumps({"v": 1, **stored(A1, "skip", "r").decision.to_dict(), "created_at": "2026-01-02T03:04:05Z"}).encode() + b"\n")
        got = store.read(self.tmp, KEY)
        self.assertEqual(got.problem.line, 2)
        self.assertEqual(got.items, ())

    def test_a_decision_that_breaks_a_rule_of_the_model_is_a_problem(self):
        row = {"v": 1, **stored(A1, "skip", "r").decision.to_dict(), "created_at": "2026-01-02T03:04:05Z"}
        for change in ({"verdict": "accept"}, {"reason": ""}, {"target_id": "a-short"}, {"fingerprint": "abc"}, {"revision": 0}, {"created_at": "soon"}):
            with self.subTest(change):
                path = store.decisions_path(self.tmp, KEY)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(json.dumps({**row, **change}).encode() + b"\n")
                got = store.read(self.tmp, KEY)
                self.assertIsNotNone(got.problem, change)
                self.assertEqual(got.items, ())

    def test_a_file_that_is_not_utf_8_or_cannot_be_read_is_a_problem(self):
        path = store.decisions_path(self.tmp, KEY)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"\xff\xfe\n")
        self.assertIsNotNone(store.read(self.tmp, KEY).problem)
        os.unlink(path)
        path.mkdir()  # a folder where the file should be
        self.assertIsNotNone(store.read(self.tmp, KEY).problem)

    def test_the_problem_says_what_is_wrong_in_words_the_tool_chose_and_echoes_nothing_from_the_file(self):
        got = self.corrupt(b'{"v":1,"target_id":"a-<script>alert(1)</script>"}\n')
        text = f"{got.problem.why} {got.problem.line}"
        self.assertNotIn("script", text)
        self.assertEqual(wording.check(got.problem.why), [])

    def test_the_record_for_the_decisions_view_is_blocking_and_worded_cleanly(self):
        got = self.corrupt(b"garbage")
        rec = store.unreadable_record(KEY, got.problem)
        self.assertEqual(rec["kind"], "decision_store_unreadable")
        self.assertIs(rec["blocks_selection"], True)
        for field in ("subject", "not_established", "detail"):
            self.assertIsInstance(rec[field], str)
            self.assertEqual(wording.check(rec[field]), [], field)
        self.assertIn("line 2", rec["detail"])
        self.assertNotIn(str(self.tmp), json.dumps(rec))
        self.assertEqual(rec["subject"], store.decisions_path(self.tmp, KEY).name)


class Hardening(sg.TempDirCase):
    def test_a_write_that_takes_only_part_of_the_line_is_repeated_until_the_line_is_whole(self):
        real = os.write
        os.write = lambda fd, data: real(fd, bytes(data)[:7])  # a short write each time
        try:
            store.append(self.tmp, KEY, [stored(A1, "skip", "some reason", "Quinn")])
        finally:
            os.write = real
        got = store.read(self.tmp, KEY)
        self.assertIsNone(got.problem)
        self.assertEqual(got.items[0].decision.reason, "some reason")

    def test_a_write_that_takes_nothing_is_an_error_not_a_loop(self):
        real = os.write
        os.write = lambda fd, data: 0
        try:
            with self.assertRaises(store.StoreError):
                store.append(self.tmp, KEY, [stored()])
        finally:
            os.write = real

    @unittest.skipUnless(hasattr(os, "O_NOFOLLOW"), "no O_NOFOLLOW here")
    def test_a_store_that_is_a_link_is_neither_written_nor_read(self):
        path = store.decisions_path(self.tmp, KEY)
        path.parent.mkdir(parents=True)
        target = self.tmp / "elsewhere.jsonl"
        target.write_text("")
        path.symlink_to(target)
        with self.assertRaises(store.StoreError):
            store.append(self.tmp, KEY, [stored()])
        self.assertEqual(target.read_text(), "")
        self.assertIsNotNone(store.read(self.tmp, KEY).problem)

    def test_the_decision_in_force_orders_by_the_last_write_so_an_orphan_list_follows_it(self):
        items = [stored(A1, "test"), stored(A2, "test"), stored(A1, "skip", "later")]
        self.assertEqual(list(store.latest(items)), [A2, A1])

    def test_the_reachmap_folder_must_be_a_folder_or_absent(self):
        store.check_folder(self.tmp)
        (self.tmp / ".reachmap").mkdir()
        store.check_folder(self.tmp)
        other = self.tmp / "other"
        other.mkdir()
        (other / ".reachmap").write_text("x")
        with self.assertRaises(store.StoreError) as ctx:
            store.check_folder(other)
        self.assertIn(".reachmap", str(ctx.exception))
        self.assertNotIn(str(self.tmp), str(ctx.exception))

    def test_append_lines_is_part_of_the_stores_interface_and_the_key_is_made_in_one_place(self):
        self.assertIn("append_lines", store.__all__)
        from reachmap.review import attach

        self.assertFalse(hasattr(attach, "make_key"))

    def test_the_shapes_come_from_one_module(self):
        from reachmap.review import carry, exchange, formats, revisions

        for module in (store, revisions, exchange, carry):
            source = open(module.__file__, encoding="utf-8").read()
            self.assertFalse("re.compile" in source, module.__name__)
        self.assertTrue(formats.SHA.match("a" * 40))


class NoSecrets(sg.TempDirCase):
    def test_a_store_line_holds_the_decision_and_nothing_else_no_source_line_no_path_no_user_name(self):
        reason = "checked with the product owner"
        store.append(self.tmp, KEY, [stored(A1, "skip", reason, "Quinn")])
        text = store.decisions_path(self.tmp, KEY).read_text(encoding="ascii")
        row = json.loads(text)
        self.assertEqual(row["reason"], reason)
        self.assertEqual(row["author"], "Quinn")
        for private in (str(self.tmp), str(Path.home()), os.environ.get("USER", "\0"), os.environ.get("LOGNAME", "\0"), "/Users/", "/home/", "/tmp/", "file://"):
            self.assertNotIn(private, text)
        self.assertIsNone(re.search(r"(?i)token|password|secret", text))

    def test_the_author_is_what_the_caller_gave_never_the_account_of_the_machine(self):
        env = dict(os.environ, USER="machine-user", LOGNAME="machine-user")
        old = os.environ.copy()
        os.environ.update(env)
        try:
            store.append(self.tmp, KEY, [stored(A1, author="A. Person")])
        finally:
            os.environ.clear()
            os.environ.update(old)
        self.assertNotIn("machine-user", store.decisions_path(self.tmp, KEY).read_text())


class RevisionLog(sg.TempDirCase):
    SHA = "a" * 40

    def rev(self, n, file="r.json", sha=None, at="2026-01-02T03:04:05Z"):
        return revisions.Rev(n, file, sha or self.SHA, at)

    def test_no_log_is_no_revisions_and_the_first_run_is_revision_1(self):
        self.assertEqual(revisions.read(self.tmp, KEY), [])
        self.assertEqual(revisions.next_number([]), 1)
        self.assertIsNone(revisions.previous([]))

    def test_the_log_is_a_file_per_key_under_dot_reachmap_revisions(self):
        revisions.record(self.tmp, KEY, self.rev(1))
        path = revisions.revisions_path(self.tmp, KEY)
        self.assertEqual(path.parent, self.tmp / ".reachmap" / "revisions")
        self.assertEqual(path.name, store.key_slug(KEY) + ".jsonl")

    def test_three_runs_on_one_key_are_revisions_1_2_3_and_the_previous_is_the_one_before(self):
        numbers = []
        for i in range(3):
            log = revisions.read(self.tmp, KEY)
            n = revisions.next_number(log)
            numbers.append((n, revisions.previous(log)))
            revisions.record(self.tmp, KEY, self.rev(n, f"r{n}.json", sha=str(n) * 40))
        self.assertEqual([n for n, _ in numbers], [1, 2, 3])
        self.assertIsNone(numbers[0][1])
        self.assertEqual(numbers[2][1], self.rev(2, "r2.json", sha="2" * 40))

    def test_a_different_key_starts_at_1_again(self):
        revisions.record(self.tmp, KEY, self.rev(1))
        revisions.record(self.tmp, KEY, self.rev(2))
        other = store.make_key("acme", "acme/web", "compare:main...other")
        self.assertEqual(revisions.next_number(revisions.read(self.tmp, other)), 1)
        self.assertEqual(revisions.next_number(revisions.read(self.tmp, KEY)), 3)

    def test_the_number_is_the_stored_order_not_the_time_stamps(self):
        revisions.record(self.tmp, KEY, self.rev(1, at="2030-01-01T00:00:00Z"))
        revisions.record(self.tmp, KEY, self.rev(2, at="2020-01-01T00:00:00Z"))  # a clock set back
        log = revisions.read(self.tmp, KEY)
        self.assertEqual(revisions.next_number(log), 3)
        self.assertEqual(revisions.previous(log).number, 2)

    def test_a_log_with_a_gap_or_a_bad_line_is_a_store_error_never_renumbered(self):
        path = revisions.revisions_path(self.tmp, KEY)
        path.parent.mkdir(parents=True)
        good = json.dumps({"v": 1, "number": 1, "file": "a.json", "head_sha": self.SHA, "created_at": "2026-01-02T03:04:05Z"})
        for body in (good + "\n" + good.replace('"number": 1', '"number": 3') + "\n", good + "\nnot json\n", good + "\n" + good[:20], '{"v":1}\n'):
            with self.subTest(body[-30:]):
                path.write_text(body, encoding="ascii")
                with self.assertRaises(store.StoreError):
                    revisions.read(self.tmp, KEY)

    def test_a_revision_is_found_by_its_number(self):
        for n in (1, 2):
            revisions.record(self.tmp, KEY, self.rev(n, f"r{n}.json"))
        log = revisions.read(self.tmp, KEY)
        self.assertEqual(revisions.find(log, 2).file, "r2.json")
        self.assertIsNone(revisions.find(log, 7))

    def test_the_log_holds_no_machine_path(self):
        revisions.record(self.tmp, KEY, self.rev(1, "acme-web-compare-main-topic-12345678-abcdef012345.json"))
        text = revisions.revisions_path(self.tmp, KEY).read_text()
        self.assertNotIn(str(self.tmp), text)
        self.assertNotIn("/", json.loads(text)["file"])


if __name__ == "__main__":
    unittest.main()
