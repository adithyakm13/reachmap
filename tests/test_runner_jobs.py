"""M12 guards 5 to 8: one job at a time (FIFO, a cap), a separate process with a hard timeout, the job states, the validation of a job.

The jobs run a test entry in a real spawned child (`tests/runner_hooks.py`); no analysis is made here (the end-to-end test does that)."""

from __future__ import annotations

import itertools
import json
import os
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from reachmap import runner
from reachmap.runner import jobs
from tests.support_runner import Live, process_gone

SPEC = {"repo": "acme/web", "compare": "main...topic"}


class States(unittest.TestCase):
    def test_the_states(self) -> None:
        self.assertEqual(jobs.STATES, ("queued", "running", "completed", "failed", "timed_out", "cancelled"))

    def test_transitions_exhaustively(self) -> None:
        allowed = {("queued", "running"), ("queued", "cancelled"), ("running", "completed"), ("running", "failed"), ("running", "timed_out"), ("running", "cancelled")}
        for a, b in itertools.product(jobs.STATES, jobs.STATES):
            with self.subTest(a=a, b=b):
                job = jobs.Job("x", "acme/web", {"compare": "a...b"}, None, now=lambda: 0.0)
                job.state = a
                if (a, b) in allowed:
                    job.advance(b)
                    self.assertEqual(job.state, b)
                else:
                    with self.assertRaises(jobs.IllegalTransition):
                        job.advance(b)
                    self.assertEqual(job.state, a)

    def test_transitions_only_go_forward(self) -> None:
        rank = {"queued": 0, "running": 1, "completed": 2, "failed": 2, "timed_out": 2, "cancelled": 2}
        for a, nexts in jobs.TRANSITIONS.items():
            for b in nexts:
                self.assertGreater(rank[b], rank[a])
        for terminal in ("completed", "failed", "timed_out", "cancelled"):
            self.assertEqual(jobs.TRANSITIONS.get(terminal, frozenset()), frozenset())

    def test_a_job_starts_queued_and_unknown_states_are_refused(self) -> None:
        job = jobs.Job("x", "acme/web", {"compare": "a...b"}, None, now=lambda: 0.0)
        self.assertEqual(job.state, "queued")
        with self.assertRaises(jobs.IllegalTransition):
            job.advance("done")

    def test_the_times_follow_the_states(self) -> None:
        ticks = iter(range(100, 200))
        job = jobs.Job("x", "acme/web", {"compare": "a...b"}, None, now=lambda: float(next(ticks)))
        d = job.to_dict()
        self.assertIsNone(d["started_at"])
        self.assertIsNone(d["finished_at"])
        job.advance("running")
        job.advance("failed")
        d = job.to_dict()
        self.assertTrue(d["started_at"] and d["finished_at"] and d["created_at"])
        self.assertLessEqual(d["created_at"], d["started_at"])
        self.assertLessEqual(d["started_at"], d["finished_at"])

    def test_the_hard_ceiling_is_the_configs_timeout_unless_overridden(self) -> None:
        with Live() as live:
            self.assertEqual(live.server.runner.timeout_s, live.workspace.limits.timeout_s)
        with Live(timeout_s=2.5) as live:
            self.assertEqual(live.server.runner.timeout_s, 2.5)


class OneAtATime(unittest.TestCase):
    def test_a_fifo_queue_with_no_overlap(self) -> None:
        with Live(entry="recorder", hook_args={"seconds": 0.35}) as live:
            ids = [live.submit(compare=f"main...topic{i}")["id"] for i in range(3)]
            first = live.get("/api/jobs").json()["jobs"]
            states = {j["id"]: j["state"] for j in first}
            self.assertEqual(states[ids[1]], "queued")
            self.assertEqual(states[ids[2]], "queued")
            seen_running = 0
            end = time.monotonic() + 30
            while time.monotonic() < end:
                listing = live.get("/api/jobs").json()["jobs"]
                running = [j for j in listing if j["state"] == "running"]
                seen_running = max(seen_running, len(running))
                self.assertLessEqual(len(running), 1)  # never two at once
                if all(j["state"] == "completed" for j in listing):
                    break
                time.sleep(0.02)
            else:
                self.fail("the jobs did not finish")
            self.assertEqual(seen_running, 1)
            starts = [live.note(i, "start") for i in ids]
            ends = [live.note(i, "end") for i in ids]
            for n in range(3):
                self.assertLess(starts[n]["time"], ends[n]["time"])
            for n in range(2):  # the next job starts after the one before has ended, in the order they were given
                self.assertLessEqual(ends[n]["time"], starts[n + 1]["time"])
            self.assertEqual(len({s["pid"] for s in starts}), 3)  # each in a process of its own
            self.assertNotIn(os.getpid(), {s["pid"] for s in starts})
            done = [live.job(i) for i in ids]
            for n in range(2):
                self.assertLessEqual(done[n]["finished_at"], done[n + 1]["started_at"])
            self.assertEqual([live.job(i)["state"] for i in ids], ["completed"] * 3)

    def test_the_listing_is_newest_first_and_a_job_has_the_fields(self) -> None:
        with Live(entry="recorder", hook_args={"seconds": 0.0}) as live:
            a = live.submit(compare="main...a")["id"]
            b = live.submit(compare="main...b")["id"]
            for i in (a, b):
                live.wait_state(i, ("completed",))
            self.assertEqual([j["id"] for j in live.get("/api/jobs").json()["jobs"]], [b, a])
            job = live.job(a)
            self.assertEqual(set(job), {"id", "state", "repo", "change", "as_of", "created_at", "started_at", "finished_at", "report", "exit_code", "reason"})
            self.assertEqual(job["repo"], "acme/web")
            self.assertEqual(job["change"], {"compare": "main...a"})
            self.assertRegex(job["id"], r"^[0-9a-f]{12}$")

    def test_the_queue_is_capped(self) -> None:
        with Live(entry="sleeper", queue_cap=2) as live:
            first = live.submit()["id"]
            live.wait_state(first, ("running",))
            self.assertEqual(live.post("/api/jobs", SPEC).status, 202)
            self.assertEqual(live.post("/api/jobs", SPEC).status, 202)
            r = live.post("/api/jobs", SPEC)
            self.assertEqual(r.status, 429)
            self.assertEqual(r.json(), {"error": "queue_full"})
            self.assertEqual([j["state"] for j in live.get("/api/jobs").json()["jobs"]].count("queued"), 2)
            self.assertEqual(len(live.get("/api/jobs").json()["jobs"]), 3)  # the refused one was not kept

    def test_the_default_cap_is_twenty(self) -> None:
        with Live() as live:
            self.assertEqual(live.server.runner.queue_cap, 20)

    def test_a_job_starts_queued_and_the_post_returns_id_and_state(self) -> None:
        with Live(entry="sleeper") as live:
            r = live.post("/api/jobs", SPEC)
            self.assertEqual(r.status, 202)
            body = r.json()
            self.assertEqual(set(body), {"id", "state"})
            self.assertIn(body["state"], ("queued", "running"))


class Timeout(unittest.TestCase):
    def test_a_child_that_sleeps_is_killed_and_the_job_is_timed_out(self) -> None:
        with Live(entry="sleeper", timeout_s=2.0, grace_s=1.0) as live:
            job_id = live.submit()["id"]
            pid = live.wait_note(job_id, "start")["pid"]
            started = time.monotonic()
            job = live.wait_state(job_id, ("timed_out", "completed", "failed"), timeout=20)
            self.assertEqual(job["state"], "timed_out")
            self.assertLess(time.monotonic() - started, 8)
            self.assertTrue(process_gone(pid), "the child is still alive")
            self.assertNotIn(job["state"], ("completed",))
            for key in ("findings", "counts", "areas"):
                self.assertNotIn(key, job)  # a timed out run has no count
            self.assertEqual(job["reason"], "timed_out")
            self.assertIsNotNone(job["finished_at"])
            self.assertTrue(job["report"])
            path = live.reports / (job["report"] + ".json")
            self.assertTrue(path.is_file(), path)
            report = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "timed_out")
            self.assertIsNone(report["counts"])
            for key in ("findings", "areas", "branches", "propagations", "backend_calls", "suggestions"):
                self.assertIsNone(report[key], key)
            self.assertTrue(any(r["kind"] == "timed_out" and r["blocks_selection"] for r in report["incompleteness"]))
            self.assertEqual(report["inputs"]["repo"], "acme/web")
            self.assertEqual(report["inputs"]["change"], {"compare": "main...topic"})
            self.assertRegex(path.name, r"-failed-[0-9a-f]{8}\.json$")
            self.assertIsNone(report["review"]["revision"])
            self.assertFalse((live.reports / ".reachmap" / "revisions").exists() and any((live.reports / ".reachmap" / "revisions").iterdir()))
            served = live.get(f"/api/reports/{job['report']}")
            self.assertEqual(served.status, 200)
            self.assertEqual(served.json()["status"], "timed_out")
            listed = [r for r in live.get("/api/reports").json()["reports"] if r["name"] == job["report"]]
            self.assertEqual(listed[0]["status"], "timed_out")

    def test_a_child_that_ignores_terminate_is_killed_after_the_grace_period(self) -> None:
        with Live(entry="stubborn", timeout_s=2.0, grace_s=0.6) as live:
            job_id = live.submit()["id"]
            pid = live.wait_note(job_id, "start")["pid"]
            job = live.wait_state(job_id, ("timed_out",), timeout=20)
            self.assertTrue(process_gone(pid), "the child that ignores SIGTERM is still alive")
            self.assertEqual(job["state"], "timed_out")


class ProcessTree(unittest.TestCase):
    """Rule 9: ending a job ends everything it started, also the git processes (each in a group of its own) and also when the child is killed."""

    def start(self, live: Live) -> tuple[str, int, int, int]:
        job_id = live.submit()["id"]
        child = live.wait_note(job_id, "start")["pid"]
        sleeper = self.wait_pid(live, job_id, "sleep")
        beside = self.wait_pid(live, job_id, "sleep.child")
        return job_id, child, sleeper, beside

    @staticmethod
    def wait_pid(live: Live, job_id: str, suffix: str) -> int:
        path = live.hooks / f"{job_id}.{suffix}"
        end = time.monotonic() + 15
        while time.monotonic() < end:
            try:
                text = path.read_text(encoding="utf-8").strip()
                if text:
                    return int(text)
            except (OSError, ValueError):
                pass
            time.sleep(0.03)
        raise AssertionError(f"no {suffix} for {job_id}")

    def gone(self, *pids: int) -> None:
        for pid in pids:
            self.assertTrue(process_gone(pid, 6.0), f"process {pid} is still alive")

    def test_a_timeout_ends_the_git_processes_too(self) -> None:
        with Live(entry="git_sleeper", timeout_s=2.0, grace_s=0.5) as live:
            job_id, child, sleeper, beside = self.start(live)
            own_group = self.wait_pid(live, job_id, "group")
            self.assertEqual(live.wait_state(job_id, ("timed_out",), timeout=20)["state"], "timed_out")
            self.gone(child, sleeper, beside, own_group)  # the git processes (own groups), and a process of the child's own group

    def test_a_cancel_ends_the_git_processes_too(self) -> None:
        with Live(entry="git_sleeper", grace_s=0.5) as live:
            job_id, child, sleeper, beside = self.start(live)
            live.delete(f"/api/jobs/{job_id}")
            self.assertEqual(live.wait_state(job_id, ("cancelled",))["state"], "cancelled")
            self.gone(child, sleeper, beside)

    def test_stopping_the_runner_ends_the_git_processes_too(self) -> None:
        with Live(entry="git_sleeper", grace_s=0.5) as live:
            job_id, child, sleeper, beside = self.start(live)
            live.server.runner.stop()
            self.gone(child, sleeper, beside)
            self.assertEqual(live.job(job_id)["state"], "cancelled")

    def test_a_child_killed_by_a_signal_leaves_nothing_behind(self) -> None:
        with Live(entry="git_sleeper") as live:
            job_id, child, sleeper, beside = self.start(live)
            os.kill(child, 9)
            self.assertEqual(live.wait_state(job_id, ("failed",))["state"], "failed")
            self.gone(sleeper, beside)

    def test_the_next_job_starts_only_after_the_git_processes_of_the_last_are_gone(self) -> None:
        with Live(entry="git_sleeper", timeout_s=2.0, grace_s=0.5) as live:
            first = live.submit()["id"]
            second = live.submit(compare="main...b")["id"]
            sleeper = self.wait_pid(live, first, "sleep")
            live.wait_state(first, ("timed_out",), timeout=20)
            self.gone(sleeper)  # gone as soon as the job is over, before the next one is waited for
            live.wait_note(second, "start")

    def test_the_registry_is_written_only_when_the_runner_asks(self) -> None:
        from reachmap import gitlayer

        self.assertEqual(gitlayer.REGISTRY_ENV, "REACHMAP_PROCESS_REGISTRY")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reg"
            with mock.patch.dict(os.environ, {gitlayer.REGISTRY_ENV: str(path)}):
                gitlayer.register_process_group(4242)
                gitlayer.register_process_group(4243)
            self.assertEqual(path.read_text(encoding="ascii").split(), ["4242", "4243"])
            with mock.patch.dict(os.environ):
                os.environ.pop(gitlayer.REGISTRY_ENV, None)
                gitlayer.register_process_group(1)
            self.assertEqual(path.read_text(encoding="ascii").split(), ["4242", "4243"])

    def test_the_sweep_never_kills_the_runners_own_group_or_a_bad_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg = Path(tmp) / "reg"
            reg.write_text(f"{os.getpgrp()}\n1\n0\n-5\nnot a number\n999999999\n", encoding="ascii")
            jobs.sweep_registry(reg)  # returns, and this process is still here


class ChildReportedResults(unittest.TestCase):
    def test_a_child_that_reports_its_own_timeout_is_timed_out_and_has_no_count(self) -> None:
        with Live(entry="cooperative_timeout") as live:
            job = live.wait_state(live.submit()["id"], ("completed", "failed", "timed_out"))
            self.assertEqual(job["state"], "timed_out")
            self.assertEqual(job["exit_code"], 4)
            self.assertTrue(job["report"])
            for key in ("findings", "counts", "areas"):
                self.assertNotIn(key, job)
            report = live.get(f"/api/reports/{job['report']}").json()
            self.assertEqual(report["status"], "timed_out")
            self.assertIsNone(report["counts"])
            self.assertIsNone(report["findings"])
            self.assertEqual(len(list(live.reports.glob("*.json"))), 1)  # the runner wrote no second report

    def test_a_completed_report_is_kept_when_the_child_is_ended_before_it_answers(self) -> None:
        with Live(entry="completed_then_hang", timeout_s=2.0, grace_s=0.5) as live:
            job_id = live.submit()["id"]
            job = live.wait_state(job_id, ("completed", "failed", "timed_out"), timeout=20)
            self.assertEqual(job["state"], "completed")
            self.assertRegex(job["report"], r"-r1$")
            self.assertEqual([p.name for p in live.reports.glob("*.json")], [job["report"] + ".json"])  # no `-failed-` report beside it

    def test_the_same_when_the_child_is_killed(self) -> None:
        with Live(entry="completed_then_hang") as live:
            job_id = live.submit()["id"]
            pid = live.wait_note(job_id, "start")["pid"]
            os.kill(pid, 9)
            job = live.wait_state(job_id, ("completed", "failed", "timed_out"))
            self.assertEqual(job["state"], "completed")
            self.assertEqual([p.name for p in live.reports.glob("*.json")], [job["report"] + ".json"])

    def test_a_cancelled_job_that_had_completed_names_its_report(self) -> None:
        with Live(entry="completed_then_hang", grace_s=0.5) as live:
            job_id = live.submit()["id"]
            live.wait_note(job_id, "start")
            live.delete(f"/api/jobs/{job_id}")
            job = live.wait_state(job_id, ("cancelled", "completed"))
            self.assertEqual(job["state"], "cancelled")
            self.assertRegex(job["report"], r"-r1$")
            self.assertEqual(len(list(live.reports.glob("*.json"))), 1)

    def test_the_child_sees_no_token_in_its_environment_argv_or_request(self) -> None:
        with Live(entry="dump") as live:
            job_id = live.submit()["id"]
            live.wait_state(job_id, ("completed",))
            dumped = json.dumps(live.note(job_id, "dump"))
            self.assertNotIn(live.token, dumped)
            self.assertNotIn("X-Reachmap", dumped)
            self.assertIn("REACHMAP_PROCESS_REGISTRY", dumped)


class Failures(unittest.TestCase):
    def test_a_crash_is_failed_with_its_exit_code(self) -> None:
        with Live(entry="crasher") as live:
            job = live.wait_state(live.submit()["id"], ("failed", "completed", "timed_out"))
            self.assertEqual(job["state"], "failed")
            self.assertEqual(job["exit_code"], 7)
            self.assertEqual(job["reason"], "crashed")

    def test_a_child_killed_by_a_signal_is_failed(self) -> None:
        with Live(entry="sleeper") as live:
            job_id = live.submit()["id"]
            pid = live.wait_note(job_id, "start")["pid"]
            os.kill(pid, 9)
            job = live.wait_state(job_id, ("failed", "completed", "timed_out"))
            self.assertEqual(job["state"], "failed")
            self.assertEqual(job["exit_code"], -9)

    def test_a_child_that_ends_without_a_result_is_failed(self) -> None:
        with Live(entry="silent") as live:
            job = live.wait_state(live.submit()["id"], ("failed", "completed", "timed_out"))
            self.assertEqual(job["state"], "failed")
            self.assertEqual(job["reason"], "no_result")
            self.assertIsNone(job["report"])

    def test_an_exception_in_the_child_is_failed_and_says_nothing_of_it(self) -> None:
        with Live(entry="raiser") as live:
            job_id = live.submit()["id"]
            job = live.wait_state(job_id, ("failed", "completed", "timed_out"))
            self.assertEqual(job["state"], "failed")
            text = json.dumps(job) + "\n".join(live.logs)
            self.assertNotIn("boom-hook-secret", text)
            self.assertNotIn("Traceback", text)
            self.assertNotIn("RuntimeError", text)

    def test_a_refused_or_failed_analysis_is_failed_with_the_code_the_analysis_gave(self) -> None:
        with Live(entry="refused") as live:
            job = live.wait_state(live.submit()["id"], ("failed", "completed", "timed_out"))
            self.assertEqual(job["state"], "failed")
            self.assertEqual(job["exit_code"], 3)

    def test_the_runner_goes_on_after_a_failure(self) -> None:
        with Live(entry="crasher") as live:
            a = live.submit()["id"]
            b = live.submit(compare="main...b")["id"]
            self.assertEqual(live.wait_state(a, ("failed",))["state"], "failed")
            self.assertEqual(live.wait_state(b, ("failed",))["state"], "failed")


class Cancel(unittest.TestCase):
    def test_a_queued_job_is_cancelled_and_never_runs(self) -> None:
        with Live(entry="sleeper") as live:
            a = live.submit()["id"]
            live.wait_state(a, ("running",))
            b = live.submit(compare="main...b")["id"]
            self.assertEqual(live.job(b)["state"], "queued")
            r = live.delete(f"/api/jobs/{b}")
            self.assertEqual(r.status, 200)
            self.assertEqual(r.json(), {"id": b, "state": "cancelled"})
            self.assertEqual(live.job(a)["state"], "running")
            live.delete(f"/api/jobs/{a}")
            live.wait_state(a, ("cancelled",))
            time.sleep(0.3)
            self.assertFalse((live.hooks / f"{b}.start.json").exists())
            self.assertEqual(live.job(b)["state"], "cancelled")

    def test_a_running_job_is_terminated_and_marked_cancelled(self) -> None:
        with Live(entry="sleeper", grace_s=1.0) as live:
            a = live.submit()["id"]
            pid = live.wait_note(a, "start")["pid"]
            b = live.submit(compare="main...b")["id"]
            r = live.delete(f"/api/jobs/{a}")
            self.assertEqual(r.status, 200)
            self.assertEqual(r.json()["id"], a)
            job = live.wait_state(a, ("cancelled", "completed", "failed", "timed_out"))
            self.assertEqual(job["state"], "cancelled")
            self.assertTrue(process_gone(pid))
            self.assertIsNone(job["report"])
            live.wait_note(b, "start")  # the queue goes on
            self.assertEqual(live.job(b)["state"], "running")

    def test_a_stubborn_running_job_is_killed_when_cancelled(self) -> None:
        with Live(entry="stubborn", grace_s=0.5) as live:
            a = live.submit()["id"]
            pid = live.wait_note(a, "start")["pid"]
            live.delete(f"/api/jobs/{a}")
            self.assertEqual(live.wait_state(a, ("cancelled", "timed_out"))["state"], "cancelled")
            self.assertTrue(process_gone(pid))

    def test_finished_and_unknown_jobs(self) -> None:
        with Live(entry="recorder", hook_args={"seconds": 0.0}) as live:
            a = live.submit()["id"]
            live.wait_state(a, ("completed",))
            r = live.delete(f"/api/jobs/{a}")
            self.assertEqual(r.status, 409)
            self.assertEqual(r.json(), {"error": "finished"})
            self.assertEqual(live.job(a)["state"], "completed")  # unchanged
            for bad in ("000000000000", "nope", "..", "%2e%2e", "a" * 300):
                self.assertEqual(live.delete(f"/api/jobs/{bad}").status, 404, bad)
                self.assertEqual(live.get(f"/api/jobs/{bad}").status, 404, bad)

    def test_stopping_the_runner_ends_the_child(self) -> None:
        live = Live(entry="sleeper")
        with live:
            a = live.submit()["id"]
            pid = live.wait_note(a, "start")["pid"]
        self.assertTrue(process_gone(pid), "the child outlived the runner")


class Validation(unittest.TestCase):
    BAD = [
        ({}, "repo"), ({"repo": "acme/web"}, "change"), ({"repo": "acme/web", "pr": 1, "compare": "a...b"}, "change"),
        ({"repo": "acme/web", "compare": "a...b", "range": "a..b"}, "change"),
        ({"repo": "nope/nope", "pr": 1}, "repo"), ({"repo": 5, "pr": 1}, "repo"), ({"repo": None, "pr": 1}, "repo"), ({"repo": ["acme/web"], "pr": 1}, "repo"),
        ({"repo": "x" * 5000, "pr": 1}, "repo"), ({"repo": "acme/web\n", "pr": 1}, "repo"), ({"repo": "", "pr": 1}, "repo"),
        ({"repo": "acme/web", "pr": "abc"}, "pr"), ({"repo": "acme/web", "pr": True}, "pr"), ({"repo": "acme/web", "pr": 0}, "pr"),
        ({"repo": "acme/web", "pr": -3}, "pr"), ({"repo": "acme/web", "pr": 1.5}, "pr"), ({"repo": "acme/web", "pr": "1" * 40}, "pr"),
        ({"repo": "acme/web", "pr": "12\n"}, "pr"), ({"repo": "acme/web", "pr": [1]}, "pr"), ({"repo": "acme/web", "pr": None}, "pr"),
        ({"repo": "acme/web", "compare": "a..b"}, "compare"), ({"repo": "acme/web", "compare": "a..."}, "compare"), ({"repo": "acme/web", "compare": "...b"}, "compare"),
        ({"repo": "acme/web", "compare": "main...topic extra"}, "compare"), ({"repo": "acme/web", "compare": "-x...y"}, "compare"),
        ({"repo": "acme/web", "compare": "a...-y"}, "compare"), ({"repo": "acme/web", "compare": "a...b\n"}, "compare"), ({"repo": "acme/web", "compare": "a...b\x00"}, "compare"),
        ({"repo": "acme/web", "compare": "a..." + "b" * 400}, "compare"), ({"repo": "acme/web", "compare": 5}, "compare"),
        ({"repo": "acme/web", "compare": "a...b; touch x"}, "compare"), ({"repo": "acme/web", "compare": "a...b\u202e"}, "compare"),
        ({"repo": "acme/web", "range": "a...b"}, "range"), ({"repo": "acme/web", "range": "a"}, "range"), ({"repo": "acme/web", "range": "--upload-pack=x..y"}, "range"),
        ({"repo": "acme/web", "pr": 1, "as_of": "yesterday"}, "as_of"), ({"repo": "acme/web", "pr": 1, "as_of": 5}, "as_of"),
        ({"repo": "acme/web", "pr": 1, "as_of": "1969-01-01T00:00:00Z"}, "as_of"), ({"repo": "acme/web", "pr": 1, "as_of": "2026-01-01" + " " * 100}, "as_of"),
        ({"repo": "acme/web", "pr": 1, "as_of": ""}, "as_of"), ({"repo": "acme/web", "pr": 1, "as_of": "2026-01-01T00:00:00Z\n"}, "as_of"),
        ({"repo": "acme/web", "pr": 1, "config": "/etc/reachmap.toml"}, "keys"), ({"repo": "acme/web", "pr": 1, "path": "/etc"}, "keys"),
        ({"repo": "acme/web", "pr": 1, "remote": "https://evil.example/x.git"}, "keys"), ({"repo": "acme/web", "pr": 1, "out": "/tmp"}, "keys"),
        ({"repo": "acme/web", "pr": 1, "formats": ["html"]}, "keys"), ({"repo": "acme/web", "pr": 1, "timeout_s": 1e9}, "keys"),
        ({"repo": "acme/web", "pr": 1, "command": "id"}, "keys"), ({"repo": "acme/web", "pr": 1, "": 1}, "keys"),
    ]

    def test_bad_specs_are_refused_with_a_fixed_field_name_and_nothing_is_queued(self) -> None:
        mark = "MARK-Qw23"
        with Live(entry="recorder") as live:
            for payload, field in self.BAD:
                with self.subTest(payload=str(payload)[:80]):
                    r = live.post("/api/jobs", payload)
                    self.assertEqual(r.status, 400)
                    self.assertEqual(r.json(), {"error": "invalid_job", "field": field})
            for payload in ({mark: 1, "repo": mark, "pr": mark}, {"repo": mark}):
                r = live.post("/api/jobs", payload)
                self.assertEqual(r.status, 400)
                self.assertNotIn(mark.encode(), r.body)
            self.assertEqual(live.get("/api/jobs").json(), {"jobs": []})

    def test_bodies_that_are_not_a_json_object(self) -> None:
        with Live(entry="recorder") as live:
            for body, error in ((b"", "invalid_json"), (b"{", "invalid_json"), (b"not json", "invalid_json"), (b"\xff\xfe", "invalid_json"),
                                (b'{"repo": NaN}', "invalid_json"), (b"[" * 5000, "invalid_json"), (b"[]", "invalid_job"), (b'"x"', "invalid_job"),
                                (b"null", "invalid_job"), (b"1", "invalid_job"), (b'{"repo": "acme/web", "pr": 1} {"x": 1}', "invalid_json")):
                with self.subTest(body=body[:20]):
                    r = live.request("POST", "/api/jobs", body=body)
                    self.assertEqual(r.status, 400)
                    self.assertEqual(r.json()["error"], error)
                    if error == "invalid_job":
                        self.assertEqual(r.json()["field"], "body")
            self.assertEqual(live.get("/api/jobs").json(), {"jobs": []})

    def test_good_specs_are_queued_and_the_child_gets_only_what_was_validated(self) -> None:
        with Live(entry="echo") as live:
            cases = [
                ({"repo": "acme/web", "pr": 12}, {"pr": "12"}), ({"repo": "acme/web", "pr": "#12"}, {"pr": "12"}), ({"repo": "acme/api", "pr": "!7"}, {"pr": "7"}),
                ({"repo": "acme/api", "compare": "main...scenario/s13-branch-matrix"}, {"compare": "main...scenario/s13-branch-matrix"}),
                ({"repo": "acme/api", "range": "v1.0..v1.1", "as_of": "2026-01-01T00:00:00Z"}, {"range": "v1.0..v1.1"}),
            ]
            for payload, change in cases:
                with self.subTest(payload=payload):
                    job_id = live.post("/api/jobs", payload).json()["id"]
                    live.wait_state(job_id, ("completed",))
                    echoed = live.note(job_id, "echo")
                    self.assertEqual(echoed["spec"], {"repo": payload["repo"], "change": change, "as_of": payload.get("as_of")})
                    self.assertEqual(echoed["keys"], ["args", "config", "env", "git_env", "job", "registry", "reports", "result", "spec"])
                    self.assertEqual(live.job(job_id)["change"], change)

    def test_the_runner_never_uses_a_shell_or_evaluates_input(self) -> None:
        package = Path(runner.__file__).parent
        text = "\n".join(p.read_text(encoding="utf-8") for p in package.glob("*.py"))
        for banned in ("subprocess", "shell=", "os.system", "os.popen", "eval(", "exec(", "pickle", "__import__"):
            self.assertNotIn(banned, text, banned)
        self.assertIsNone(re.search(r"\bimport (subprocess|pickle)\b", text))

    def test_repos_lists_names_roles_and_branches_only(self) -> None:
        with Live() as live:
            body = live.get("/api/repos").json()
            self.assertEqual(body["workspace"], "acme")
            by_name = {r["name"]: r for r in body["repos"]}
            self.assertEqual(sorted(by_name), ["acme/api", "acme/contracts", "acme/web", "acme/worker"])
            self.assertEqual(by_name["acme/web"], {"name": "acme/web", "roles": ["frontend"], "branches": ["main", "release/*"]})
            text = json.dumps(body)
            for secret in ("reachmap-test-no-such-place", "remote", "file://", "_TOKEN", "tsconfig", "acme-web", "http_client"):
                self.assertNotIn(secret, text)
            self.assertEqual(set(body), {"workspace", "repos"})


if __name__ == "__main__":
    unittest.main()
