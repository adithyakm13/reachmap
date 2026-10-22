#!/usr/bin/env python3
"""Build the example repositories and run Reachmap on them, step by step.

    python3 examples/run_example.py                 # a temporary folder, removed at the end
    python3 examples/run_example.py --work DIR      # keep everything in DIR (it must not exist yet)

It needs a checkout of this repository (the example repositories are the synthetic fixtures under
`tests/fixtures/`), Python 3.11 or newer and git. It makes no network call and touches nothing outside the
work folder: the git cache is `REACHMAP_CACHE` inside it.

What it does, in this order (each command is printed as it is run, with the work folder shown as `$WORK`):

  1. build the four repositories of company "acme" into `$WORK/repos` (tests/fixtures/build.py);
  2. analyse a compare in one repository (a shared component in the frontend) and write JSON, Markdown and HTML;
  3. analyse a change in the contracts repository that reaches a screen in the frontend across three repositories;
  4. analyse a range between two tags;
  5. render the first report again, as the author view;
  6. record two decisions for the first report;
  7. add a second commit to the first change and analyse it again: the decisions are carried to the new
     revision, current, stale or orphaned;
  8. import a decisions file (the file the HTML report's "Export decisions" button writes, made here by hand).

The reports are left in `$WORK/reports` so that they can be opened (use --work to choose where).
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = "examples/reachmap.toml"   # relative to the repository root, where every command runs

WEB_CHANGE = ("acme/web", "main...scenario/s01-shared-component")
CONTRACT_CHANGE = ("acme/contracts", "main...scenario/s11-contract-field")
TAG_RANGE = ("acme/contracts", "v1.9.0..v1.10.0")


class Example:
    def __init__(self, work: Path) -> None:
        self.work = work
        self.repos = work / "repos"
        self.reports = work / "reports"
        self.env = dict(os.environ, REACHMAP_FIXTURES=str(self.repos), REACHMAP_CACHE=str(work / "cache"))

    def show(self, text: str) -> str:
        return text.replace(str(self.work), "$WORK")

    def reachmap(self, *args: str, expect: int = 0, head: int | None = None, start_at: str | None = None) -> str:
        """Run `reachmap ARGS`, print the command and its output (from the first line containing `start_at`, at most
        `head` lines), and return the whole output."""
        shown = " ".join(self.show(a) for a in args)
        print(f"\n$ reachmap {shown}")
        run = subprocess.run([sys.executable, "-m", "reachmap", *args], cwd=ROOT, env=self.env,
                             capture_output=True, text=True, encoding="utf-8")
        text = run.stdout + run.stderr
        lines = self.show(text).rstrip("\n").split("\n") if text.strip() else []
        if start_at is not None:
            lines = lines[next((i for i, line in enumerate(lines) if start_at in line), 0):]
        for line in (lines if head is None else lines[:head]):
            print(f"  {line}")
        if head is not None and len(lines) > head:
            print(f"  ... ({len(lines) - head} more lines)")
        if run.returncode != expect:
            raise SystemExit(f"reachmap {args[0]} exited {run.returncode}, expected {expect}")
        return text

    def analyse(self, repo: str, change: str, *extra: str) -> Path:
        flag = "--range" if ".." in change and "..." not in change else "--compare"
        self.reachmap("analyse", "--config", CONFIG, "--repo", repo, flag, change, "--out", str(self.reports), *extra)
        newest = max(self.reports.glob("*.json"), key=lambda p: p.stat().st_mtime_ns)
        return newest

    def latest(self, needle: str) -> Path:
        found = sorted((p for p in self.reports.glob("*.json") if needle in p.name), key=lambda p: p.name)
        if not found:
            raise SystemExit(f"no report with {needle!r} in its name")
        return found[-1]


def build_repositories(example: Example) -> None:
    print("1. Build the example repositories")
    command = [sys.executable, str(ROOT / "tests" / "fixtures" / "build.py"), str(example.repos)]
    print(f"\n$ python3 tests/fixtures/build.py $WORK/repos")
    run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if run.returncode != 0:
        raise SystemExit(run.stdout + run.stderr)
    for line in example.show(run.stdout).splitlines():
        print(f"  {line}")


def second_revision(example: Example) -> None:
    """Commit one more change on the branch of the first change (a pull request that got another commit)."""
    sys.path.insert(0, str(ROOT))
    from tests.fixtures import build

    sha = build.apply_revision(example.repos, "s01-shared-component", "r2")
    print(f"\n  (committed a second revision on scenario/s01-shared-component in acme-web: {sha[:12]})")


def write_decisions_file(example: Example, report: Path, path: Path) -> None:
    """The file the HTML report exports, made by hand from the report: one decision for the first area."""
    data = json.loads(report.read_text(encoding="utf-8"))
    review = data["review"]
    area = data["areas"][0]
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    exported = {
        "format": "reachmap-decisions",
        "version": 1,
        "report": {
            "key": {k: review["key"][k] for k in ("workspace", "repo", "change")},
            "head_sha": data["ref"],
            "revision": review["revision"],
        },
        "exported_at": now,
        "decisions": [{
            "target_id": area["id"], "verdict": "sample", "reason": "same component, one screen is enough",
            "author": "Quinn", "fingerprint": area["fingerprint"], "created_at": now,
        }],
    }
    path.write_text(json.dumps(exported, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    if sys.version_info < (3, 11):
        raise SystemExit(f"Reachmap needs Python 3.11 or newer; this is Python {sys.version_info.major}.{sys.version_info.minor}. "
                         "Run the script with a newer interpreter, for example python3.12 examples/run_example.py")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--work", metavar="DIR", help="keep everything here (must not exist yet); default: a temporary folder, removed at the end")
    ns = parser.parse_args(argv)
    if shutil.which("git") is None:
        raise SystemExit("git is not installed")
    if ns.work:
        work = Path(ns.work).absolute()
        if work.exists():
            raise SystemExit(f"{work} already exists; choose a folder that does not")
        work.mkdir(parents=True)
        cleanup = None
    else:
        cleanup = tempfile.TemporaryDirectory(prefix="reachmap-example-")
        work = Path(cleanup.name)
    try:
        example = Example(work)
        build_repositories(example)

        print("\n2. A change in one repository: a shared component of the frontend")
        first = example.analyse(*WEB_CHANGE, "--format", "json,md,html")

        print("\n3. A change that crosses repositories: a contract field that a backend route and a screen depend on")
        example.analyse(*CONTRACT_CHANGE, "--format", "json,md")

        print("\n4. A range between two tags")
        example.analyse(*TAG_RANGE)

        print("\n5. The author view of the first report")
        example.reachmap("report", str(first), "--view", "author", start_at="## For the author", head=22)

        print("\n6. Decide: list the targets, then record two decisions")
        listing = example.reachmap("decide", str(first), "--list")
        ids = {line.split("  ")[2]: line.split("  ")[0] for line in listing.splitlines() if line.startswith("a-")}
        example.reachmap("decide", str(first), "--target", ids["/billing/invoices"], "--verdict", "test", "--author", "Quinn")
        example.reachmap("decide", str(first), "--target", ids["/cases"], "--verdict", "skip", "--author", "Quinn",
                         "--reason", "only the badge colour changed here")

        print("\n7. Rerun on a later commit of the same change: the decisions follow the claims")
        second_revision(example)
        example.reachmap("analyse", "--config", CONFIG, "--repo", WEB_CHANGE[0], "--compare", WEB_CHANGE[1],
                         "--out", str(example.reports), "--format", "json,md")
        revised = example.latest("-r2.json")
        example.reachmap("decide", str(revised), "--list")

        print("\n8. Import a decisions file (what the HTML report exports)")
        decisions = work / "decisions.json"
        write_decisions_file(example, revised, decisions)
        example.reachmap("analyse", "--config", CONFIG, "--repo", WEB_CHANGE[0], "--compare", WEB_CHANGE[1],
                         "--out", str(example.reports), "--decisions", str(decisions))

        print(f"\nDone. The reports are in {example.reports if ns.work else '$WORK/reports (removed now; use --work DIR to keep them)'}")
        return 0
    finally:
        if cleanup is not None:
            cleanup.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
