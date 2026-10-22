#!/usr/bin/env python3
"""Install a built wheel into a clean virtual environment and run the commands of the quickstart against it.

    python3 tests/release/check_install.py --wheel dist/reachmap-0.1.0-py3-none-any.whl [--python python3.12] [--work DIR] [--keep]

This is a release check, not part of the unit test suite (`python -m unittest discover -s tests` never runs it). Run it once per Python
the release supports (3.11, 3.12, 3.13), with the wheel that `python -m build` made after the frontend build (see docs/releasing.md).

What it does, in a temporary folder (removed at the end unless `--keep` or `--work` is given):

  1. builds the four synthetic repositories (tests/fixtures/build.py, the Python given with `--python`);
  2. creates a virtual environment with that Python and runs `pip install --no-index WHEEL` in it (no network: the package has no
     dependencies, and pip is not allowed to look anywhere but the file);
  3. with the installed `reachmap` command, from a folder that holds no source of the package, runs:
       reachmap --version                 the version of the wheel
       reachmap init                      on the four repositories, to standard output: a config that parses as TOML and that the
                                          installed package's own loader (`reachmap.config.load`, run with the venv's Python) accepts
       reachmap analyse                   one scenario (a shared component of the frontend), with examples/reachmap.toml
       reachmap report                    the report as Markdown and as HTML
       reachmap audit                     on that report: every cited line is re-read at its commit (some lines checked, none mismatched,
                                          absent or unreadable)
       reachmap serve --port 0            started; /api/health fetched without a token (it is unauthenticated); the workspace page, one of
                                          its assets and /api/reports fetched over 127.0.0.1 with the printed token, and /api/reports
                                          without the token answered 401 (the page and the assets are static files that the runner serves
                                          without a token; the script sends the token anyway); then stopped
       reachmap mcp                       initialize, tools/list and list_reports (a count of 1) over standard input and output

and checks that the installed package holds reachmap/runner/static/THIRD_PARTY_LICENSES.txt (the notices of the bundled frontend packages).

It prints one line per step and exits 0 only when every step passed. Standard library only; it needs `git` on the path.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "examples" / "reachmap.toml"
CHANGE = ("acme/web", "main...scenario/s01-shared-component")
REPOS = ("acme/web", "acme/api", "acme/contracts", "acme/worker")
SLUGS = {name: name.replace("/", "-") for name in REPOS}  # tests/fixtures/build.py names a folder after the repository
SERVE_WAIT = 30  # seconds to wait for the runner to print its address
STEP_TIMEOUT = 300
NOTICES = "reachmap/runner/static/THIRD_PARTY_LICENSES.txt"  # inside the installed package: the licence notices of the bundled frontend
NOTICES_SNIPPET = ("import pathlib, reachmap; p = pathlib.Path(reachmap.__file__).parent / 'runner' / 'static' / 'THIRD_PARTY_LICENSES.txt'; "
                   "print(len(p.read_bytes()) if p.is_file() else 'missing')")
LOAD_SNIPPET = "import sys; from reachmap import config; print(','.join(sorted(config.load(sys.argv[1]).repos)))"


class Failed(Exception):
    """A step did not pass: the message says why."""


def source_version() -> str:
    found = re.search(r'^__version__ = "([^"]+)"', (ROOT / "reachmap" / "__init__.py").read_text(encoding="utf-8"), re.M)
    if not found:
        raise Failed("cannot read __version__ from reachmap/__init__.py")
    return found.group(1)


def wheel_version(wheel: Path) -> str:
    found = re.fullmatch(r"reachmap-([^-]+)-[^-]+-[^-]+-[^-]+\.whl", wheel.name)
    if not found:
        raise Failed(f"{wheel.name} is not the name of a reachmap wheel")
    return found.group(1)


def clean_env(extra: dict[str, str]) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "PYTHONSTARTUP")}
    env.update(PYTHONNOUSERSITE="1", PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_INPUT="1", PYTHONIOENCODING="utf-8")
    env.update(extra)
    return env


def run(cmd: list[str], *, cwd: Path, env: dict[str, str], expect: int = 0, timeout: int = STEP_TIMEOUT) -> subprocess.CompletedProcess:
    try:
        done = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise Failed(f"timed out after {timeout} s: {' '.join(cmd[:3])}") from None
    if done.returncode != expect:
        tail = (done.stdout + done.stderr).strip()[-1200:]
        raise Failed(f"exit {done.returncode}, expected {expect}: {' '.join(cmd[1:4])}\n{tail}")
    return done


class Check:
    def __init__(self, python: str, wheel: Path, work: Path) -> None:
        self.python = python
        self.wheel = wheel
        self.work = work
        self.repos = work / "repos"
        self.reports = work / "reports"
        self.home = work / "home"  # the folder every installed command runs in: it holds no source of the package
        self.venv = work / "venv"
        scripts = "Scripts" if os.name == "nt" else "bin"
        self.vpython = self.venv / scripts / ("python.exe" if os.name == "nt" else "python")
        self.reachmap_cmd = self.venv / scripts / ("reachmap.exe" if os.name == "nt" else "reachmap")
        self.env = clean_env({"REACHMAP_FIXTURES": str(self.repos), "REACHMAP_CACHE": str(work / "cache"), "HOME": str(work / "userhome")})
        self.report_json: Path | None = None
        self.results: list[tuple[str, bool, str]] = []

    # ------------------------------------------------------------------ steps

    def reachmap(self, *args: str, expect: int = 0, timeout: int = STEP_TIMEOUT) -> subprocess.CompletedProcess:
        return run([str(self.reachmap_cmd), *args], cwd=self.home, env=self.env, expect=expect, timeout=timeout)

    def step_fixtures(self) -> str:
        run([self.python, str(ROOT / "tests" / "fixtures" / "build.py"), str(self.repos)], cwd=self.work, env=self.env)
        missing = [name for name, slug in SLUGS.items() if not (self.repos / slug / ".git").exists()]
        if missing:
            raise Failed(f"fixture repositories not built: {missing}")
        return f"{len(REPOS)} repositories"

    def step_install(self) -> str:
        run([self.python, "-m", "venv", str(self.venv)], cwd=self.work, env=self.env)
        run([str(self.vpython), "-m", "pip", "install", "--no-index", str(self.wheel)], cwd=self.home, env=self.env)
        frozen = run([str(self.vpython), "-m", "pip", "list", "--format=json"], cwd=self.home, env=self.env).stdout
        packages = sorted(p["name"].lower() for p in json.loads(frozen) if p["name"].lower() not in ("pip", "setuptools", "wheel"))
        if packages != ["reachmap"]:
            raise Failed(f"the clean environment holds more than reachmap: {packages}")
        where = run([str(self.vpython), "-I", "-c", "import reachmap, sys; print(reachmap.__file__)"], cwd=self.home, env=self.env).stdout.strip()
        if "site-packages" not in where.replace("\\", "/"):
            raise Failed(f"reachmap is not imported from the environment: {where}")
        if not self.reachmap_cmd.exists():
            raise Failed(f"the console script is missing: {self.reachmap_cmd}")
        notices = run([str(self.vpython), "-I", "-c", NOTICES_SNIPPET], cwd=self.home, env=self.env).stdout.strip()
        if not notices.isdigit() or int(notices) < 1000:
            raise Failed(f"the installed package has no usable {NOTICES} (read {notices!r})")
        return f"no other package installed, {NOTICES} present ({notices} bytes)"

    def step_version(self) -> str:
        want = wheel_version(self.wheel)
        if want != source_version():
            raise Failed(f"the wheel is {want} and reachmap/__init__.py says {source_version()}")
        out = self.reachmap("--version").stdout.strip()
        if out != f"reachmap {want}":
            raise Failed(f"--version printed {out!r}, expected 'reachmap {want}'")
        return out

    def step_init(self) -> str:
        specs = [f"{name}={self.repos / SLUGS[name]}" for name in REPOS]
        done = self.reachmap("init", *specs)
        config = tomllib.loads(done.stdout)
        repos = sorted(config.get("repos", {}))
        if repos != sorted(REPOS):
            raise Failed(f"the starter config lists {repos}")
        guesses = done.stdout.count("# guess:")
        if guesses < 4 or "confirm" not in done.stdout:
            raise Failed(f"the starter config has {guesses} marked guesses")
        # the installed package's own loader, not only a TOML parser: "a file that loads" means this
        starter = self.work / "starter" / "reachmap.toml"
        starter.parent.mkdir(exist_ok=True)
        starter.write_text(done.stdout, encoding="utf-8")
        loaded = run([str(self.vpython), "-I", "-c", LOAD_SNIPPET, str(starter)], cwd=self.home, env=self.env).stdout.strip()
        if loaded != ",".join(sorted(REPOS)):
            raise Failed(f"the installed config loader read the repositories {loaded!r} from the starter config")
        return f"{len(repos)} repositories, {guesses} guesses marked, loaded by reachmap.config.load"

    def step_analyse(self) -> str:
        repo, change = CHANGE
        done = self.reachmap("analyse", "--config", str(CONFIG), "--repo", repo, "--compare", change, "--out", str(self.reports), "--format", "json")
        first = done.stdout.splitlines()[0] if done.stdout else ""
        if not first.startswith(f"reachmap {wheel_version(self.wheel)}: {repo}, compare {change}"):
            raise Failed(f"unexpected summary line: {first!r}")
        if "Areas to test: 4 (must test 4)" not in done.stdout:
            raise Failed("the shared component did not reach its four screens:\n" + done.stdout[-800:])
        found = sorted(self.reports.glob("*-r1.json"))
        if len(found) != 1:
            raise Failed(f"expected one report, found {[p.name for p in found]}")
        self.report_json = found[0]
        return "4 areas, one report"

    def step_report(self) -> str:
        assert self.report_json is not None
        md, html = self.work / "report.md", self.work / "report.html"
        self.reachmap("report", str(self.report_json), "--format", "md", "--out", str(md))
        self.reachmap("report", str(self.report_json), "--format", "html", "--out", str(html))
        md_text, html_text = md.read_text(encoding="utf-8"), html.read_text(encoding="utf-8")
        if not md_text.startswith("# Reachmap report: acme/web") or f"reachmap `{wheel_version(self.wheel)}`" not in md_text:
            raise Failed("the Markdown report is not what was expected")
        if "<html" not in html_text.lower() or "/billing/invoices" not in html_text:
            raise Failed("the HTML report is not what was expected")
        if re.search(r"""(?:src|href)\s*=\s*["']https?://""", html_text):
            raise Failed("the HTML report loads something over the network")
        return f"md {len(md_text)} bytes, html {len(html_text)} bytes"

    def step_audit(self) -> str:
        assert self.report_json is not None
        done = self.reachmap("audit", str(self.report_json), "--config", str(CONFIG))
        summary = next((line for line in done.stdout.splitlines() if re.match(r"\d+ reports?: ", line)), "")
        if "mismatch 0, absent 0, unreadable 0" not in summary or "0 structure problems" not in summary:
            raise Failed(f"the audit summary is not a clean one: {summary!r}")
        ok = re.search(r"\bok (\d+)\b", summary)
        if not ok or int(ok.group(1)) <= 0:
            raise Failed(f"the audit checked no line (it must say ok with a count above 0): {summary!r}")
        return summary

    def step_serve(self) -> str:
        cmd = [str(self.reachmap_cmd), "serve", "--config", str(CONFIG), "--reports", str(self.reports), "--port", "0"]
        proc = subprocess.Popen(cmd, cwd=self.home, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
        lines: list[str] = []
        try:
            first = self.first_line(proc, lines)
            found = re.search(r"http://127\.0\.0\.1:(\d+)/#token=([A-Za-z0-9_-]+)", first)
            if not found:
                raise Failed(f"no address in the runner's first line: {first[:120]!r}")
            port, token = found.group(1), found.group(2)
            base = f"http://127.0.0.1:{port}"
            health = self.fetch(f"{base}/api/health", None)
            if json.loads(health[1]) != {"ok": True}:
                raise Failed(f"/api/health answered {health[1][:80]!r}")
            status, page = self.fetch(f"{base}/", token)
            if "<title>Reachmap</title>" not in page or "/assets/" not in page:
                raise Failed("the page served is not the workspace (the minimal page stands in when the frontend build is missing)")
            asset = re.search(r'(?:src|href)="(/assets/[^"]+\.js)"', page)
            if not asset:
                raise Failed("the workspace page names no script under /assets/")
            self.fetch(f"{base}{asset.group(1)}", token)
            listed = json.loads(self.fetch(f"{base}/api/reports", token)[1])["reports"]
            if len(listed) != 1:
                raise Failed(f"/api/reports lists {len(listed)} reports, expected 1")
            refused = self.fetch(f"{base}/api/reports", None, expect=401)
            return f"/api/health without a token, then with the token the workspace page, an asset and /api/reports (/api/reports without the token is {refused[0]})"
        finally:
            self.stop(proc)

    def step_mcp(self) -> str:
        cmd = [str(self.reachmap_cmd), "mcp", "--reports", str(self.reports), "--config", str(CONFIG)]
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "check-install", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_reports", "arguments": {}}},
        ]
        stdin = "".join(json.dumps(m) + "\n" for m in messages)
        try:
            done = subprocess.run(cmd, cwd=self.home, env=self.env, input=stdin, capture_output=True, text=True, encoding="utf-8", timeout=60)
        except subprocess.TimeoutExpired:
            raise Failed("reachmap mcp did not finish when its input ended") from None
        answers = {}
        for line in done.stdout.splitlines():
            message = json.loads(line)
            answers[message.get("id")] = message
        if set(answers) != {1, 2, 3}:
            raise Failed(f"answers for ids {sorted(map(str, answers))}; stderr: {done.stderr[-300:]}")
        init = answers[1]["result"]
        if init.get("serverInfo", {}).get("name") != "reachmap" or init["serverInfo"].get("version") != wheel_version(self.wheel):
            raise Failed(f"initialize answered {init.get('serverInfo')}")
        tools = sorted(t["name"] for t in answers[2]["result"]["tools"])
        want = ["add_note", "get_evidence", "get_report_summary", "get_section", "list_notes", "list_reports", "propose_file"]
        if tools != want:
            raise Failed(f"tools/list answered {tools}")
        call = answers[3]["result"]
        if call.get("isError"):
            raise Failed("list_reports reported an error")
        structured = call.get("structuredContent")
        counted = structured.get("count") if isinstance(structured, dict) else None
        if counted != 1 or len(structured.get("reports", [])) != 1:
            raise Failed(f"list_reports answered a count of {counted!r}, expected 1: {json.dumps(call)[:300]}")
        return f"initialize and tools/list ({len(tools)} tools), list_reports answered with a count of 1"

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def first_line(proc: subprocess.Popen, lines: list[str]) -> str:
        """The runner's first line of output, or a failure when it does not come in SERVE_WAIT seconds (read on a thread: a pipe has no timeout)."""
        got: list[str] = []
        done = threading.Event()

        def read() -> None:
            assert proc.stdout is not None
            got.append(proc.stdout.readline())
            done.set()

        threading.Thread(target=read, daemon=True).start()
        if not done.wait(SERVE_WAIT) or not got[0]:
            raise Failed(f"the runner printed no address within {SERVE_WAIT} s (exit code {proc.poll()})")
        lines.append(got[0])
        return got[0]

    @staticmethod
    def fetch(url: str, token: str | None, expect: int = 200) -> tuple[int, str]:
        if not url.startswith("http://127.0.0.1:"):
            raise Failed(f"refusing to fetch {url}: loopback only")
        request = urllib.request.Request(url, headers={"X-Reachmap-Token": token} if token else {})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never through a proxy
        try:
            with opener.open(request, timeout=20) as response:
                status, body = response.status, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            status, body = exc.code, exc.read().decode("utf-8", "replace")
        if status != expect:
            raise Failed(f"GET {url.split('127.0.0.1:', 1)[1].split('/', 1)[1] or '/'} answered {status}, expected {expect}")
        return status, body

    @staticmethod
    def stop(proc: subprocess.Popen) -> None:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT if os.name != "nt" else signal.CTRL_BREAK_EVENT)
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(10)
        if proc.stdout:
            proc.stdout.close()

    def go(self, name: str, step: Callable[[], str]) -> bool:
        try:
            detail = step()
        except Failed as exc:
            self.results.append((name, False, str(exc)))
            print(f"FAIL  {name}: {exc}", flush=True)
            return False
        except Exception as exc:  # a bug of this script is a failed check, never a silent pass
            self.results.append((name, False, f"{type(exc).__name__}: {exc}"))
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}", flush=True)
            return False
        self.results.append((name, True, detail))
        print(f"ok    {name}: {detail}", flush=True)
        return True

    def run_all(self) -> bool:
        self.home.mkdir(parents=True, exist_ok=True)
        (self.work / "userhome").mkdir(exist_ok=True)
        steps: list[tuple[str, Callable[[], str]]] = [
            ("fixtures", self.step_fixtures),
            ("install", self.step_install),
            ("version", self.step_version),
            ("init", self.step_init),
            ("analyse", self.step_analyse),
            ("report", self.step_report),
            ("audit", self.step_audit),
            ("serve", self.step_serve),
            ("mcp", self.step_mcp),
        ]
        for name, step in steps:
            if not self.go(name, step):
                return False  # every later step needs this one
        return True


def python_version(python: str) -> str:
    done = subprocess.run([python, "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"], capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(f"check_install: cannot run {python}")
    return done.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install a reachmap wheel into a clean venv and run its commands.")
    parser.add_argument("--wheel", type=Path, required=True, help="the wheel file made by `python -m build`")
    parser.add_argument("--python", default=sys.executable, help="the Python to make the venv with (default: the one running this script)")
    parser.add_argument("--work", type=Path, help="a folder to work in (it must not exist yet); kept afterwards")
    parser.add_argument("--keep", action="store_true", help="keep the temporary folder")
    args = parser.parse_args(argv)
    wheel = args.wheel.resolve()
    if not wheel.is_file():
        raise SystemExit(f"check_install: no such file: {wheel}")
    if shutil.which("git") is None:
        raise SystemExit("check_install: git is needed to build the fixture repositories")
    version = python_version(args.python)
    if tuple(map(int, version.split("."))) < (3, 11):
        raise SystemExit(f"check_install: Python {version} is older than the 3.11 that reachmap needs")
    if args.work:
        if args.work.exists():
            raise SystemExit(f"check_install: {args.work} exists already")
        args.work.mkdir(parents=True)
        work, temp = args.work.resolve(), None
    else:
        temp = tempfile.mkdtemp(prefix="reachmap-install-")
        work = Path(temp).resolve()
    print(f"check_install: {wheel.name} on Python {version} ({args.python}), work folder {work}", flush=True)
    try:
        passed = Check(args.python, wheel, work).run_all()
    finally:
        if temp and not args.keep:
            shutil.rmtree(temp, ignore_errors=True)
    print(f"check_install: {'PASSED' if passed else 'FAILED'} on Python {version}", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
