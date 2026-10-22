"""A real runner on 127.0.0.1:0 in a thread, and a raw HTTP client for the tests of `reachmap serve`.

`Live` makes a workspace config file (the M1 fixture config with remotes that are never read, unless a test passes real ones),
starts the server and gives `request(...)`, which sends exactly the headers asked for (a wrong or missing Host, an Origin, no
token) through `http.client`. Nothing leaves the loopback interface.
"""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from reachmap import config
from reachmap.runner import create
from tests import support_git as sg
from tests.fixtures import build

FIXTURE_TOML = Path(build.__file__).resolve().parent / "reachmap.toml"
NEVER = {"REACHMAP_FIXTURES": "file:///reachmap-test-no-such-place"}
HOOKS = "tests.runner_hooks"


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


class Live:
    """`with Live(entry="recorder") as live:`; `live.get(...)`, `live.post_job(...)`; the server is shut down on exit."""

    def __init__(self, *, entry: str | None = None, hook_args: Mapping[str, Any] | None = None, env: Mapping[str, str] | None = None,
                 git_env: Mapping[str, str] | None = None, config_path: Path | None = None, reports: Path | None = None, **options: Any) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="reachmap-runner-")
        self.root = Path(self._tmp.name)
        self.env = dict(env if env is not None else NEVER)
        if config_path is None:
            config_path = self.root / "reachmap.toml"
            config_path.write_text(FIXTURE_TOML.read_text(encoding="utf-8"), encoding="utf-8")
        self.config_path = config_path
        self.reports = reports or self.root / "reports"
        self.hooks = self.root / "hooks"
        self.hooks.mkdir()
        self.workspace = config.load(config_path, env=self.env)
        # the workspace files of the package (`reachmap/runner/static/`, there once the frontend is bundled) are not what these tests are about:
        # a folder that does not exist makes the runner serve its minimal page, whatever the checkout holds
        options.setdefault("static_dir", self.root / "no-static")
        self.logs: list[str] = []
        args = {"dir": str(self.hooks), "fake_git": fake_git(self.hooks), **(hook_args or {})}
        self.server = create(
            self.workspace, config_path, self.reports, port=0, env=self.env, git_env=git_env,
            entry=f"{HOOKS}:{entry}" if entry and ":" not in entry else entry, entry_args=args, log=self.logs.append, **options)
        self.token: str = self.server.token
        self.port: int = self.server.server_address[1]
        self._thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)

    def __enter__(self) -> "Live":
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.shutdown()
        self.server.runner.stop()
        self.server.server_close()
        self._thread.join(5)
        self._tmp.cleanup()

    # ---------------------------------------------------------------- requests

    @property
    def host(self) -> str:
        return f"127.0.0.1:{self.port}"

    def request(self, method: str, path: str, *, headers: Mapping[str, str] | None = None, body: bytes | None = None, token: bool | str = True,
                host: str | None | bool = True, json_body: Any = None, content_type: str | None = None, timeout: float = 20.0) -> Response:
        """One request. `host=True` sends the right Host, a string sends that one, None sends none. `token=True` sends the session
        token, a string sends that, False sends none. A JSON body gets `Content-Type: application/json` unless `content_type` says otherwise."""
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            if host is True:
                conn.putheader("Host", self.host)
            elif isinstance(host, str):
                conn.putheader("Host", host)
            if token is True:
                conn.putheader("X-Reachmap-Token", self.token)
            elif isinstance(token, str):
                conn.putheader("X-Reachmap-Token", token)
            sent = {k.lower() for k in (headers or {})}
            for name, value in (headers or {}).items():
                conn.putheader(name, value)
            if body is not None:
                if "content-type" not in sent:
                    conn.putheader("Content-Type", content_type or "application/json")
                if "content-length" not in sent:
                    conn.putheader("Content-Length", str(len(body)))
            conn.endheaders(body)
            resp = conn.getresponse()
            return Response(resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read())
        finally:
            conn.close()

    def get(self, path: str, **kw: Any) -> Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, payload: Any, **kw: Any) -> Response:
        return self.request("POST", path, json_body=payload, **kw)

    def delete(self, path: str, **kw: Any) -> Response:
        return self.request("DELETE", path, **kw)

    # ---------------------------------------------------------------- jobs

    def submit(self, repo: str = "acme/web", **change: Any) -> dict[str, Any]:
        spec = {"repo": repo, **(change or {"compare": "main...topic"})}
        r = self.post("/api/jobs", spec)
        assert r.status in (200, 202), (r.status, r.body)
        return r.json()

    def job(self, job_id: str) -> dict[str, Any]:
        r = self.get(f"/api/jobs/{job_id}")
        assert r.status == 200, r.status
        return r.json()

    def wait_state(self, job_id: str, states: tuple[str, ...], timeout: float = 20.0) -> dict[str, Any]:
        end = time.monotonic() + timeout
        while True:
            job = self.job(job_id)
            if job["state"] in states:
                return job
            if time.monotonic() > end:
                raise AssertionError(f"job {job_id} is {job['state']}, waited for {states}")
            time.sleep(0.03)

    def note(self, job_id: str, name: str) -> dict[str, Any]:
        return json.loads((self.hooks / f"{job_id}.{name}.json").read_text(encoding="utf-8"))

    def wait_note(self, job_id: str, name: str, timeout: float = 20.0) -> dict[str, Any]:
        path = self.hooks / f"{job_id}.{name}.json"
        end = time.monotonic() + timeout
        while not path.exists():
            if time.monotonic() > end:
                raise AssertionError(f"the child of {job_id} never wrote {name}")
            time.sleep(0.03)
        for _ in range(100):  # the file is made before it is complete
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                time.sleep(0.02)
        raise AssertionError("note unreadable")


def process_gone(pid: int, wait: float = 3.0) -> bool:
    """Whether no process has this id (a killed child is reaped by its parent, so it is gone, not a zombie)."""
    import os

    end = time.monotonic() + wait
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        if _is_zombie(pid):
            return True  # dead; only its exit status waits for a reaper (a reparented grandchild on a CI runner)
        time.sleep(0.05)
    return False


def _is_zombie(pid: int) -> bool:
    """Whether the process has exited and only waits to be reaped. A zombie runs nothing."""
    import subprocess

    try:
        with open(f"/proc/{pid}/stat", encoding="ascii", errors="replace") as f:
            return f.read().rsplit(")", 1)[1].split()[0] == "Z"
    except FileNotFoundError:
        pass
    except OSError:
        return False
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return out.stdout.strip().startswith("Z")


def isolated_git_env(home: Path) -> dict[str, str]:
    return sg.isolated_env(home)


FAKE_GIT = """#!/bin/sh
echo $$ > "$REACHMAP_TEST_PIDFILE"
/bin/sleep 47 &
echo $! > "$REACHMAP_TEST_PIDFILE.child"
wait
"""


def fake_git(folder: Path) -> str:
    """A stand-in for git that notes its pid (`<pidfile>`) and the pid of a sleep it starts beside itself (`<pidfile>.child`)."""
    path = folder / "fakegit"
    path.write_text(FAKE_GIT, encoding="utf-8")
    path.chmod(0o755)
    return str(path)
