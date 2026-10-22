"""The advisory lock of the review layer (M11): one short critical section under `<reports dir>/.reachmap/lock` that numbers a revision, carries
the decisions, writes the report and enters the log; `store.append` and `decide` take it too. Two processes (and two threads with their own
file descriptors) racing end up with distinct numbers and whole lines."""

import json
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

from reachmap.model import Decision
from reachmap.review import lock, revisions, store
from tests import support_git as sg

ROOT = Path(__file__).resolve().parents[1]
KEY = store.make_key("acme", "acme/web", "compare:main...topic")
SHA = "a" * 40
WHEN = "2026-01-02T03:04:05Z"

RACER = """
import sys, time, random
from reachmap.review import lock, revisions, store
base, key = sys.argv[1], store.make_key("acme", "acme/web", "compare:main...topic")
with lock.held(base):
    log = revisions.read(base, key)
    n = revisions.next_number(log)
    time.sleep(random.random() * 0.05)   # the window in which an unlocked run would take the same number
    revisions.record(base, key, revisions.Rev(n, f"r{n}.json", "a" * 40, "2026-01-02T03:04:05Z"))
print(n)
"""

UNLOCKED = RACER.replace("with lock.held(base):\n    log", "if True:\n    log")


class Held(sg.TempDirCase):
    def test_the_lock_file_is_dot_reachmap_lock_beside_the_reports(self):
        with lock.held(self.tmp):
            self.assertTrue((self.tmp / ".reachmap" / "lock").is_file())

    def test_a_second_take_in_the_same_thread_is_the_same_hold_and_does_not_wait_for_itself(self):
        with lock.held(self.tmp):
            with lock.held(self.tmp):
                pass
            store.append(self.tmp, KEY, [store.Stored(Decision("a-" + "1" * 16, "test", "", "Q", "cli", "0" * 64, 1), WHEN)])  # append takes it too

    def test_the_lock_is_let_go_when_the_section_raises(self):
        with self.assertRaises(RuntimeError):
            with lock.held(self.tmp):
                raise RuntimeError("boom")
        with lock.held(self.tmp, timeout=0.5):
            pass

    def test_threads_with_their_own_descriptors_are_one_at_a_time(self):
        inside, peak, errors = [0], [0], []

        def work():
            try:
                with lock.held(self.tmp):
                    inside[0] += 1
                    peak[0] = max(peak[0], inside[0])
                    time.sleep(0.02)
                    inside[0] -= 1
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=work) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual((errors, peak[0]), ([], 1))

    @unittest.skipIf(lock.fcntl is None and lock.msvcrt is None, "no file locking on this platform")
    def test_a_hold_that_does_not_end_makes_the_next_one_give_up_with_a_message_not_wait_for_ever(self):
        started = threading.Event()
        release = threading.Event()

        def hold():
            with lock.held(self.tmp):
                started.set()
                release.wait(5)

        t = threading.Thread(target=hold)
        t.start()
        started.wait(5)
        try:
            with self.assertRaises(store.StoreError) as ctx:
                with lock.held(self.tmp, timeout=0.2):
                    pass
            self.assertIn("another", str(ctx.exception))
            self.assertNotIn(str(self.tmp), str(ctx.exception))
        finally:
            release.set()
            t.join()

    def test_a_lock_that_cannot_be_made_is_a_store_error_naming_the_file(self):
        (self.tmp / ".reachmap").write_text("a file where the folder must be")
        with self.assertRaises(store.StoreError) as ctx:
            with lock.held(self.tmp):
                pass
        self.assertIn(".reachmap", str(ctx.exception))
        self.assertNotIn(str(self.tmp), str(ctx.exception))


@unittest.skipIf(lock.fcntl is None and lock.msvcrt is None, "no file locking on this platform")
class Racing(sg.TempDirCase):
    def race(self, script, n=6):
        env = {**os.environ, "PYTHONPATH": str(ROOT)}
        procs = [subprocess.Popen([sys.executable, "-c", script, str(self.tmp)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(n)]
        outs = [p.communicate(timeout=60) for p in procs]
        return [(p.returncode, o.strip(), e.strip()) for p, (o, e) in zip(procs, outs)]

    def test_six_processes_taking_the_next_revision_get_1_to_6_and_a_log_that_reads(self):
        results = self.race(RACER)
        self.assertEqual([r[0] for r in results], [0] * 6, results)
        self.assertEqual(sorted(int(r[1]) for r in results), [1, 2, 3, 4, 5, 6])
        self.assertEqual([r.number for r in revisions.read(self.tmp, KEY)], [1, 2, 3, 4, 5, 6])

    def test_the_same_race_without_the_lock_loses_a_number_so_the_test_above_proves_the_lock(self):
        results = self.race(UNLOCKED)
        self.assertTrue(any(r[0] != 0 for r in results), "six unlocked runs must collide at least once for the lock to mean anything")

    def test_processes_adding_decisions_write_whole_lines(self):
        script = (
            "import sys\nfrom reachmap.model import Decision\nfrom reachmap.review import store\n"
            "key = store.make_key('acme', 'acme/web', 'compare:main...topic')\n"
            "for i in range(15):\n"
            "    store.append(sys.argv[1], key, [store.Stored(Decision('a-' + '1' * 16, 'skip', 'x' * 1500, 'Q', 'cli', '0' * 64, 1), '2026-01-02T03:04:05Z')])\n")
        results = self.race(script, n=4)
        self.assertEqual([r[0] for r in results], [0] * 4, results)
        got = store.read(self.tmp, KEY)
        self.assertIsNone(got.problem)
        self.assertEqual(len(got.items), 60)


if __name__ == "__main__":
    unittest.main()
