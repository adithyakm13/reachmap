"""`reachmap serve` as a process: the URL it prints (the token in the fragment), the usage errors, a clean stop."""

from __future__ import annotations

import io
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from reachmap import cli
from tests.support_runner import FIXTURE_TOML, NEVER

ROOT = Path(__file__).resolve().parent.parent
URL = re.compile(r"^http://127\.0\.0\.1:(\d+)/#token=([A-Za-z0-9_-]{43,})$")


@contextmanager
def serving(*extra: str) -> Iterator[tuple[subprocess.Popen, int, str, Path]]:
    with tempfile.TemporaryDirectory(prefix="reachmap-serve-") as tmp:
        root = Path(tmp)
        (root / "reachmap.toml").write_text(FIXTURE_TOML.read_text(encoding="utf-8"), encoding="utf-8")
        env = {**os.environ, **NEVER, "PYTHONPATH": str(ROOT), "PYTHONUNBUFFERED": "1"}
        proc = subprocess.Popen([sys.executable, "-m", "reachmap", "serve", "--port", "0", "--config", str(root / "reachmap.toml"), "--reports", str(root / "reports"), *extra],
                                cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            url = ""
            end = time.monotonic() + 20
            while time.monotonic() < end:
                line = proc.stdout.readline()
                if not line:
                    break
                found = [w for w in line.split() if "#token=" in w]
                if found:
                    url = found[0]
                    break
            m = URL.match(url)
            assert m, f"no URL printed ({url!r}); stderr: {proc.stderr.read() if proc.poll() is not None else ''}"
            yield proc, int(m.group(1)), m.group(2), root
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(10)
            proc.stdout.close()
            proc.stderr.close()


def http_get(port: int, path: str, token: str | None = None) -> tuple[int, bytes]:
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"X-Reachmap-Token": token} if token else {}
    conn.request("GET", path, headers=headers)
    r = conn.getresponse()
    return r.status, r.read()


class Serve(unittest.TestCase):
    def test_prints_the_url_with_the_token_in_the_fragment_and_serves_it(self) -> None:
        with serving() as (proc, port, token, _root):
            self.assertEqual(http_get(port, "/api/health")[0], 200)
            self.assertEqual(http_get(port, "/api/repos", token)[0], 200)
            self.assertEqual(http_get(port, "/api/repos", "wrong")[0], 401)
            self.assertEqual(http_get(port, "/api/repos")[0], 401)
            proc.send_signal(signal.SIGINT)
            self.assertEqual(proc.wait(10), 0)
            err = proc.stderr.read()
            self.assertNotIn(token, err)
            self.assertNotIn("Traceback", err)
            self.assertNotIn(token, proc.stdout.read().replace(f"#token={token}", ""))

    def test_the_default_port_and_the_options_are_in_the_help(self) -> None:
        out = io.StringIO()
        self.assertEqual(cli.main(["serve", "--help"], stdout=out), 0)
        text = out.getvalue()
        for word in ("--port", "--config", "--reports", "8787"):
            self.assertIn(word, text)
        self.assertNotIn("--host", text)

    def test_terminate_stops_it_too(self) -> None:
        with serving() as (proc, _port, _token, _root):
            proc.send_signal(signal.SIGTERM)
            self.assertIn(proc.wait(10), (0, -signal.SIGTERM, 143))

    def test_usage_errors_are_exit_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "reachmap.toml"
            cfg.write_text(FIXTURE_TOML.read_text(encoding="utf-8"), encoding="utf-8")
            reports = str(Path(tmp) / "reports")
            cases = [
                ["serve", "--config", str(Path(tmp) / "nope.toml"), "--reports", reports],
                ["serve", "--config", str(cfg), "--reports", reports, "--port", "70000"],
                ["serve", "--config", str(cfg), "--reports", reports, "--port", "-1"],
                ["serve", "--config", str(cfg), "--reports", reports, "--port", "abc"],
                ["serve", "--config", str(cfg), "--reports", reports, "--host", "0.0.0.0"],
                ["serve", "--config", str(cfg), "--reports", reports, "--bind", "0.0.0.0"],
            ]
            for argv in cases:
                with self.subTest(argv=argv[3:]):
                    err = io.StringIO()
                    self.assertEqual(cli.main(argv, env=NEVER, stdout=io.StringIO(), stderr=err), 2)
                    self.assertIn("reachmap: error:", err.getvalue())

    def test_a_config_with_an_unset_variable_is_a_usage_error_not_a_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "reachmap.toml"
            cfg.write_text(FIXTURE_TOML.read_text(encoding="utf-8"), encoding="utf-8")
            err = io.StringIO()
            self.assertEqual(cli.main(["serve", "--config", str(cfg), "--reports", str(Path(tmp) / "r")], env={}, stdout=io.StringIO(), stderr=err), 2)
            self.assertIn("config error", err.getvalue())

    def test_a_port_in_use_is_exit_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "reachmap.toml"
            cfg.write_text(FIXTURE_TOML.read_text(encoding="utf-8"), encoding="utf-8")
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                s.listen(1)
                err = io.StringIO()
                code = cli.main(["serve", "--config", str(cfg), "--reports", str(Path(tmp) / "r"), "--port", str(s.getsockname()[1])], env=NEVER, stdout=io.StringIO(), stderr=err)
            self.assertEqual(code, 2)
            self.assertIn("--port", err.getvalue())

    def test_reports_dir_that_is_a_file_is_exit_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "reachmap.toml"
            cfg.write_text(FIXTURE_TOML.read_text(encoding="utf-8"), encoding="utf-8")
            (Path(tmp) / "reports").write_text("x", encoding="utf-8")
            err = io.StringIO()
            self.assertEqual(cli.main(["serve", "--config", str(cfg), "--reports", str(Path(tmp) / "reports")], env=NEVER, stdout=io.StringIO(), stderr=err), 2)
            self.assertIn("--reports", err.getvalue())


if __name__ == "__main__":
    unittest.main()


class RunnerDies(unittest.TestCase):
    def test_the_child_and_its_git_processes_end_when_the_runner_is_killed(self) -> None:
        import http.client
        import json

        from tests.support_runner import fake_git, process_gone

        with tempfile.TemporaryDirectory(prefix="reachmap-orphan-") as tmp:
            root = Path(tmp)
            (root / "reachmap.toml").write_text(FIXTURE_TOML.read_text(encoding="utf-8"), encoding="utf-8")
            hooks = root / "hooks"
            hooks.mkdir()
            env = {**os.environ, **NEVER, "PYTHONPATH": str(ROOT), "PYTHONUNBUFFERED": "1"}
            proc = subprocess.Popen([sys.executable, "-m", "tests.runner_main", str(root / "reachmap.toml"), str(root / "reports"), str(hooks), fake_git(hooks)],
                                    cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            pids: list[int] = []
            try:
                info = json.loads(proc.stdout.readline())
                conn = http.client.HTTPConnection("127.0.0.1", info["port"], timeout=10)
                conn.request("POST", "/api/jobs", body=json.dumps({"repo": "acme/web", "compare": "main...topic"}),
                             headers={"X-Reachmap-Token": info["token"], "Content-Type": "application/json"})
                job = json.loads(conn.getresponse().read())["id"]
                end = time.monotonic() + 20
                needed = [hooks / f"{job}.start.json", hooks / f"{job}.sleep", hooks / f"{job}.sleep.child"]
                while time.monotonic() < end and not all(p.exists() and p.read_text(encoding="utf-8").strip() for p in needed):
                    time.sleep(0.05)
                pids = [json.loads(needed[0].read_text())["pid"], int(needed[1].read_text()), int(needed[2].read_text())]
                proc.kill()
                proc.wait(10)
                for pid in pids:
                    self.assertTrue(process_gone(pid, 10.0), f"process {pid} outlived the runner")
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait(10)
                proc.stdout.close()
                for pid in pids:
                    try:
                        os.kill(pid, 9)
                    except OSError:
                        pass
