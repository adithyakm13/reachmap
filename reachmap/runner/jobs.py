"""The job queue of the runner: one analysis at a time, each in a `spawn` child with a hard timeout (context rules 4.9 and section 8).

A job is `queued`, `running`, and then one of `completed`, `failed`, `timed_out`, `cancelled`; it only goes forward (`TRANSITIONS`).
One worker thread takes the jobs in the order they were made and runs each in a child process of its own (`multiprocessing`, the `spawn`
start method: the child shares nothing with this process but what it is given). The child runs an entry function (by default
`analyse_entry`, which is `reachmap analyse` as the command line runs it, so a report, a revision and the decisions carried are what the
command makes) and sends one small result back on a pipe: the exit code the analysis ended with and the name of the report it wrote.

The wall clock of a job is `limits.timeout_s` of the config (`Runner(timeout_s=)` overrides it, for tests), measured from the start of the
child. At the limit the child is terminated, killed if it is still there after a grace period, and the job is `timed_out`: the runner writes
the report of the run itself (the shape of a run that did not complete: no lists, no count) because the child cannot. A timed out job is never
`completed`. A child that ends in any other way without a result is `failed` with the exit code it ended with (a crash, a signal), or with
the reason `no_result` when it ended cleanly without saying anything. A cancelled job (DELETE) is a queued one removed, or a running one
stopped the way a timeout stops it and marked `cancelled`.

Nothing here keeps what a child printed, a traceback or a message of the child: a job holds ids, states, times, a code, and a fixed reason.
"""

from __future__ import annotations

import collections
import io
import json
import multiprocessing
import os
import secrets
import shutil
import signal
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from reachmap.gitlayer import REGISTRY_ENV
from reachmap.runner import files

__all__ = ["DEFAULT_ENTRY", "Finished", "IllegalTransition", "Job", "QueueFull", "Runner", "STATES", "TRANSITIONS", "UnknownJob", "analyse_entry", "note_result", "sweep_registry"]

STATES = ("queued", "running", "completed", "failed", "timed_out", "cancelled")
TRANSITIONS: Mapping[str, frozenset[str]] = {
    "queued": frozenset({"running", "cancelled"}),
    "running": frozenset({"completed", "failed", "timed_out", "cancelled"}),
    "completed": frozenset(), "failed": frozenset(), "timed_out": frozenset(), "cancelled": frozenset(),
}
DEFAULT_ENTRY = "reachmap.runner.jobs:analyse_entry"
KEEP = 500  # jobs kept in memory: the oldest finished ones go first
EXIT_TIMED_OUT = 4  # cli: the run hit its own (cooperative) time limit


class IllegalTransition(ValueError):
    pass


class QueueFull(Exception):
    pass


class UnknownJob(KeyError):
    pass


class Finished(Exception):
    """A job that has ended cannot be cancelled."""


def _iso(moment: float) -> str:
    return datetime.fromtimestamp(moment, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(moment * 1000) % 1000:03d}Z"


class Job:
    """One analysis asked for. `change` is the validated change (`{"pr": "12"}`, `{"compare": "a...b"}` or `{"range": "a..b"}`)."""

    def __init__(self, id: str, repo: str, change: Mapping[str, str], as_of: str | None, *, now: Callable[[], float] = time.time) -> None:
        self.id, self.repo, self.change, self.as_of = id, repo, dict(change), as_of
        self._now = now
        self.state = "queued"
        self.created_at = _iso(now())
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.report: str | None = None  # the report's file name without `.json`
        self.exit_code: int | None = None
        self.reason: str | None = None
        self.cancel = threading.Event()
        self.done = threading.Event()

    def advance(self, state: str) -> None:
        """Move to `state`; only the moves of `TRANSITIONS` (forward) are allowed."""
        if state not in TRANSITIONS.get(self.state, frozenset()):
            raise IllegalTransition(f"a job that is {self.state} cannot become {state}")
        self.state = state
        if state == "running":
            self.started_at = _iso(self._now())
        else:
            self.finished_at = _iso(self._now())
            self.done.set()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "state": self.state, "repo": self.repo, "change": dict(self.change), "as_of": self.as_of,
            "created_at": self.created_at, "started_at": self.started_at, "finished_at": self.finished_at,
            "report": self.report, "exit_code": self.exit_code, "reason": self.reason,
        }


# ------------------------------------------------------------------------------ ending process trees


def _kill_group(pgid: Any) -> None:
    """SIGKILL a process group, if it is one that can be this job's: never this process's own group, never 0 or 1, never a number that is not one.
    A group that is gone, or that is not ours to signal, is not an error. (Where there is no `killpg`, as on Windows, nothing is done here.)"""
    if not isinstance(pgid, int) or isinstance(pgid, bool) or pgid <= 1 or not hasattr(os, "killpg"):
        return
    if pgid == os.getpgrp():
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except OSError:
        pass


def sweep_registry(path: str | os.PathLike) -> None:
    """End every process group a job's registry names (`gitlayer.register_process_group` wrote them, one per line). Safe to repeat."""
    try:
        with open(path, "rb") as f:
            raw = f.read(1 << 20)
    except OSError:
        return
    for line in raw.split(b"\n"):
        try:
            _kill_group(int(line))
        except ValueError:
            continue


def note_result(request: Mapping[str, Any], report: str | None, status: str) -> None:
    """The child tells the runner, before it does anything else, which report it has written and how the run ended, in a file of this job. The
    pipe is how a child answers; this file is what is left when it cannot (it was ended between writing the report and answering)."""
    path = request.get("result")
    if not path:
        return
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"report": report, "status": status}, f)
        os.replace(tmp, path)
    except OSError:
        pass


class _Outcome(dict):
    """What `cli.main` fills (`outcome=`): every update is also written to the job's result file."""

    def __init__(self, request: Mapping[str, Any]) -> None:
        super().__init__()
        self._request = request

    def update(self, *args: Any, **kw: Any) -> None:  # type: ignore[override]
        super().update(*args, **kw)
        note_result(self._request, self.get("report"), str(self.get("status")))


# ------------------------------------------------------------------------------ the child


def analyse_entry(request: Mapping[str, Any]) -> dict[str, Any]:
    """`reachmap analyse` for one validated job, in this (child) process. Standard output and error are dropped."""
    from reachmap import cli

    spec, outcome = request["spec"], _Outcome(request)
    flag, value = next(iter(spec["change"].items()))
    argv = ["analyse", "--config", request["config"], "--repo", spec["repo"], f"--{flag}={value}", "--out", request["reports"], "--format", "json"]
    if spec.get("as_of") is not None:
        argv.append(f"--as-of={spec['as_of']}")
    code = cli.main(argv, env=request["env"], stdout=io.StringIO(), stderr=io.StringIO(), git_env=request["git_env"], outcome=outcome)
    return {"code": code, "report": outcome.get("report")}


def _resolve(name: str) -> Callable[[Mapping[str, Any]], Any]:
    import importlib

    module, _, attr = name.partition(":")
    return getattr(importlib.import_module(module), attr)


def _watch_runner(registry: str) -> None:
    """If the runner dies (its pid is no longer this process's parent) end this job's git processes and then this process: nobody is left to
    answer, and nobody would end them."""
    parent = os.getppid()

    def watch() -> None:
        while True:
            time.sleep(0.5)
            if os.getppid() != parent:
                sweep_registry(registry)
                os._exit(1)

    threading.Thread(target=watch, name="reachmap-watch", daemon=True).start()


def _child(entry: str, request: dict[str, Any], conn: Any) -> None:
    """The target of the child process (spawn: it is imported by name). It leads a session and a process group of its own (so the runner can
    end everything it starts with one signal), it never prints, and a failure is an exit code."""
    try:
        os.setsid()
    except (AttributeError, OSError):
        pass
    try:
        devnull = os.open(os.devnull, os.O_RDWR)
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
        sys.stdout = sys.stderr = open(os.devnull, "w")
    except OSError:
        pass
    try:
        if request.get("registry"):
            os.environ[REGISTRY_ENV] = request["registry"]
            _watch_runner(request["registry"])
        result = _resolve(entry)(request)
        conn.send({"code": result["code"], "report": result.get("report")})
        conn.close()
    except BaseException:
        os._exit(1)


# ------------------------------------------------------------------------------ the runner


class Runner:
    def __init__(self, workspace: Any, config_path: str | Path, reports_dir: str | Path, *, env: Mapping[str, str] | None = None,
                 git_env: Mapping[str, str] | None = None, entry: str | None = None, entry_args: Mapping[str, Any] | None = None,
                 timeout_s: float | None = None, queue_cap: int = 20, grace_s: float = 2.0, now: Callable[[], float] = time.time) -> None:
        self.workspace = workspace
        self.config_path = str(config_path)
        self.reports_dir = Path(reports_dir)
        self.env = dict(os.environ if env is None else env)
        self.git_env = None if git_env is None else dict(git_env)
        self.entry = entry or DEFAULT_ENTRY
        self.entry_args = dict(entry_args or {})
        self.timeout_s = float(timeout_s if timeout_s is not None else workspace.limits.timeout_s)
        self.queue_cap = queue_cap
        self.grace_s = grace_s
        self._now = now
        self._cv = threading.Condition()
        self._queue: collections.deque[Job] = collections.deque()
        self._jobs: dict[str, Job] = {}
        self._stopping = False
        self._thread: threading.Thread | None = None
        self._scratch: Path | None = None  # the runner's own folder for the registry and the result file of each job

    # ---------------------------------------------------------------- the API's view

    def submit(self, repo: str, change: Mapping[str, str], as_of: str | None) -> Job:
        with self._cv:
            if self._stopping:
                raise QueueFull
            if len(self._queue) >= self.queue_cap:
                raise QueueFull
            job = Job(secrets.token_hex(6), repo, change, as_of, now=self._now)
            self._jobs[job.id] = job
            self._queue.append(job)
            self._trim()
            if self._thread is None:
                self._thread = threading.Thread(target=self._loop, name="reachmap-runner", daemon=True)
                self._thread.start()
            self._cv.notify_all()
            return job

    def get(self, job_id: str) -> Job:
        with self._cv:
            job = self._jobs.get(job_id)
            if job is None:
                raise UnknownJob(job_id)
            return job

    def snapshot(self, job_id: str) -> dict[str, Any]:
        with self._cv:
            return self.get(job_id).to_dict()

    def listing(self) -> list[dict[str, Any]]:
        with self._cv:
            return [j.to_dict() for j in reversed(list(self._jobs.values()))]

    def cancel(self, job_id: str) -> dict[str, Any]:
        """A queued job is removed; a running one is stopped (the answer comes when it has ended, or after a bounded wait).
        `UnknownJob`, or `Finished` for a job that has ended."""
        with self._cv:
            job = self.get(job_id)
            if job.state == "queued":
                self._queue.remove(job)
                job.advance("cancelled")
                job.reason = "cancelled"
                return job.to_dict()
            if job.state != "running":
                raise Finished(job_id)
            job.cancel.set()
        job.done.wait(self.grace_s * 2 + 5)
        with self._cv:
            return job.to_dict()

    def stop(self) -> None:
        """Stop the worker: a running child is ended, the jobs still queued are cancelled. Returns when the worker has ended."""
        with self._cv:
            self._stopping = True
            for job in self._jobs.values():
                if job.state == "running":
                    job.cancel.set()
            for job in list(self._queue):
                job.advance("cancelled")
                job.reason = "cancelled"
            self._queue.clear()
            self._cv.notify_all()
            thread = self._thread
        if thread is not None:
            thread.join(self.grace_s * 2 + 5)
        scratch = self._scratch
        if scratch is not None:  # whatever a job left (the worker ends its own, this is for one that could not)
            for registry in scratch.glob("*.pgids"):
                sweep_registry(registry)
            shutil.rmtree(scratch, ignore_errors=True)

    def _trim(self) -> None:
        extra = len(self._jobs) - KEEP
        for job_id in [j.id for j in self._jobs.values() if j.state not in ("queued", "running")][:max(extra, 0)]:
            del self._jobs[job_id]

    # ---------------------------------------------------------------- the worker

    def _loop(self) -> None:
        while True:
            with self._cv:
                while not self._queue and not self._stopping:
                    self._cv.wait()
                if self._stopping:
                    return
                job = self._queue.popleft()
                job.advance("running")
            try:
                self._execute(job)
            except BaseException:  # the worker must not die with a job half done
                self._finish(job, "failed", reason="internal")

    def _finish(self, job: Job, state: str, *, reason: str | None = None, exit_code: int | None = None, report: str | None = None) -> None:
        with self._cv:
            if job.state != "running":
                return
            job.reason, job.exit_code, job.report = reason, exit_code, report
            job.advance(state)

    def _paths(self, job: Job) -> tuple[Path, Path]:
        """The registry (process groups the job started) and the result file of a job, in a folder only this runner makes and reads."""
        with self._cv:
            if self._scratch is None:
                self._scratch = Path(tempfile.mkdtemp(prefix="reachmap-runner-"))
            return self._scratch / f"{job.id}.pgids", self._scratch / f"{job.id}.result"

    def _request(self, job: Job, registry: Path, result: Path) -> dict[str, Any]:
        return {
            "job": job.id, "spec": {"repo": job.repo, "change": dict(job.change), "as_of": job.as_of}, "config": self.config_path, "reports": str(self.reports_dir),
            "env": self.env, "git_env": self.git_env, "args": self.entry_args, "registry": str(registry), "result": str(result),
        }

    def _noted(self, result: Path) -> tuple[str, str] | None:
        """What the child wrote in its result file: the report it made (a file of the reports directory) and how the run ended; or None."""
        raw = files.read_regular(result, 4096)
        try:
            data = json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            return None
        if not isinstance(data, dict) or not files.is_report_file(data.get("report")) or not isinstance(data.get("status"), str):
            return None
        if files.read_regular(self.reports_dir / data["report"], 1 << 30) is None:
            return None
        return _stem(data["report"]) or "", data["status"]

    @staticmethod
    def _receive(conn: Any) -> dict[str, Any] | None:
        try:
            got = conn.recv()
        except Exception:  # EOF: the child ended without a result
            return None
        if not isinstance(got, dict) or isinstance(got.get("code"), bool) or not isinstance(got.get("code"), int):
            return None
        report = got.get("report")
        return {"code": got["code"], "report": report if files.is_report_file(report) else None}

    def _reap(self, proc: Any, polite: bool, registry: Path) -> None:
        """End the child and everything it started. One that has answered (`polite`) is given the grace period to exit by itself; any other is
        terminated first. A child still alive after the grace period is killed together with its process group (the child leads one). Then the
        process groups the job registered (the git processes, each in a group of its own) are killed, whatever became of the child."""
        if not polite and proc.is_alive():
            proc.terminate()
        proc.join(self.grace_s)
        if proc.is_alive():
            _kill_group(proc.pid)
            proc.kill()
            proc.join(5)
        if not polite:
            _kill_group(proc.pid)  # what the child left in its group (a command it started without a group of its own)
        sweep_registry(registry)

    def _execute(self, job: Job) -> None:
        registry, result_file = self._paths(job)
        try:
            self._run(job, registry, result_file)
        finally:
            sweep_registry(registry)
            for leftover in (registry, result_file, Path(f"{result_file}.tmp")):
                try:
                    leftover.unlink()
                except OSError:
                    pass

    def _run(self, job: Job, registry: Path, result_file: Path) -> None:
        ctx = multiprocessing.get_context("spawn")
        recv, send = ctx.Pipe(duplex=False)
        proc = ctx.Process(target=_child, args=(self.entry, self._request(job, registry, result_file), send), name="reachmap-job", daemon=True)
        started_at, started = self._now(), time.monotonic()
        try:
            proc.start()
        except Exception:
            recv.close()
            send.close()
            self._finish(job, "failed", reason="no_start")
            return
        send.close()
        outcome: str | None = None
        result: dict[str, Any] | None = None
        try:
            while True:
                if job.cancel.is_set() or self._stopping:
                    outcome = "cancelled"
                    break
                if recv.poll(0.05):
                    result = self._receive(recv)
                    break
                if not proc.is_alive():
                    result = self._receive(recv) if recv.poll(0) else None
                    break
                if time.monotonic() - started >= self.timeout_s:
                    if recv.poll(0):
                        result = self._receive(recv)
                    else:
                        outcome = "timed_out"
                    break
            self._reap(proc, polite=outcome is None and result is not None, registry=registry)
            exit_code = proc.exitcode
        finally:
            recv.close()
        elapsed = time.monotonic() - started
        noted = self._noted(result_file) if result is None else None  # the child was ended, or died, before it answered: did it say what it made?
        if result is not None:
            code = result["code"]
            if code == 0:
                self._finish(job, "completed", exit_code=0, report=_stem(result["report"]))
            elif code == EXIT_TIMED_OUT:
                self._finish(job, "timed_out", reason="timed_out", exit_code=code, report=_stem(result["report"]))
            else:
                self._finish(job, "failed", reason="analysis_failed", exit_code=code, report=_stem(result["report"]))
        elif outcome == "cancelled":
            self._finish(job, "cancelled", reason="cancelled", report=noted[0] if noted else None)
        elif noted is not None:  # a report was written before the child was ended: that report is the answer, no second one is written
            name, status = noted
            if status == "completed":
                self._finish(job, "completed", exit_code=0, report=name)
            elif status == "timed_out":
                self._finish(job, "timed_out", reason="timed_out", report=name)
            else:
                self._finish(job, "failed", reason="analysis_failed", report=name)
        elif outcome == "timed_out":
            report = self._write_timed_out(job, started_at, elapsed)
            self._finish(job, "timed_out", reason="timed_out", report=report)
        elif exit_code not in (0, None):
            self._finish(job, "failed", reason="crashed", exit_code=exit_code)
        else:
            self._finish(job, "failed", reason="no_result", exit_code=exit_code)

    def _write_timed_out(self, job: Job, started_at: float, elapsed: float) -> str | None:
        """The report of the run the runner ended: the shape of a run that did not complete. A report that cannot be written is no report."""
        try:
            from reachmap import cli, pipeline

            change = job.change
            spec = pipeline.ChangeSpec(pr=change.get("pr"), compare=change.get("compare"), range=change.get("range"))
            detail = f"the runner ended the run at the time limit of {self.timeout_s:g} s"
            return _stem(cli.write_unfinished(self.workspace, self.config_path, self.reports_dir, job.repo, spec, job.as_of, "timed_out", detail, started_at, elapsed))
        except Exception:
            return None


def _stem(name: str | None) -> str | None:
    return name[:-5] if name and name.endswith(".json") else name
