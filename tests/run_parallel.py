"""Run the suite's test modules in several worker processes.

    python3 tests/run_parallel.py                  # the whole suite, 4 workers
    python3 tests/run_parallel.py -j 6             # 6 workers
    python3 tests/run_parallel.py tests.test_model tests.test_cli      # some modules
    python3 tests/run_parallel.py --record         # run, then rewrite tests/.timings.json with what was measured

`python3 -m unittest discover -s tests` still runs everything in one process and is what CI uses; this is the quick way to
run it locally. Modules are binned into workers by the per-module times in `tests/.timings.json` (longest first, each into
the least loaded worker; a module with no recorded time costs `DEFAULT_COST`), and the modules of a worker run one after the
other in one process, so they share what a process caches (the fixture build, the git caches). The result is the sum: the
tests run, the failures with their text, and a table of the time of every module. The exit code is 0 when every module
passed and no worker died, 1 otherwise. A module that cannot be imported is a failure, never a skip.

The timing file only balances the workers; a stale one makes the run slower, never wrong. Refresh it with `--record` after the
suite changes much (it keeps the entries of modules it did not run).

Standard library only.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Mapping, Sequence

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TIMINGS = HERE / ".timings.json"
DEFAULT_COST = 5.0  # seconds assumed for a module the timing file does not know
DEFAULT_WORKERS = 4


def suite_modules() -> list[str]:
    """The dotted names of the suite's modules: `tests/test_*.py`, in name order."""
    return sorted(f"tests.{p.stem}" for p in HERE.glob("test_*.py"))


def costs_for(modules: Sequence[str], recorded: Mapping[str, float]) -> dict[str, float]:
    return {m: float(recorded.get(m, DEFAULT_COST)) for m in modules}


def bin_modules(costs: Mapping[str, float], workers: int) -> list[list[str]]:
    """Longest module first into the least loaded of `workers` bins (ties by name, so the result does not depend on the order
    the modules are given in); empty bins are dropped. The modules of a bin run in the order they were put in."""
    bins: list[list[Any]] = [[0.0, []] for _ in range(max(1, workers))]
    for module in sorted(costs, key=lambda m: (-costs[m], m)):
        target = min(bins, key=lambda b: b[0])
        target[0] += costs[module]
        target[1].append(module)
    return [b[1] for b in bins if b[1]]


# ------------------------------------------------------------------------------ a worker


def run_modules(modules: Sequence[str]) -> list[dict[str, Any]]:
    """Run each module on its own (a failure to import it is a failing test of that module) and describe what happened."""
    loader = unittest.TestLoader()
    out = []
    for name in modules:
        stream = io.StringIO()
        started = time.monotonic()
        suite = loader.loadTestsFromName(name)
        result = unittest.TextTestRunner(stream=stream, verbosity=1).run(suite)
        out.append({
            "module": name, "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
            "skipped": len(result.skipped), "seconds": round(time.monotonic() - started, 3),
            "output": "" if result.wasSuccessful() else stream.getvalue(),
        })
    return out


def worker_main(modules: Sequence[str], result_file: str) -> int:
    results = run_modules(modules)
    Path(result_file).write_text(json.dumps(results), encoding="utf-8")
    return 0


# ------------------------------------------------------------------------------ the parent


def spawn(modules: Sequence[str], result_file: Path, extra_paths: Sequence[str]) -> subprocess.Popen:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT), *extra_paths, *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])])
    return subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--worker", str(result_file), *modules],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def load_timings(path: Path) -> dict[str, float]:
    try:
        return {str(k): float(v) for k, v in json.loads(path.read_text(encoding="utf-8")).get("modules", {}).items()}
    except (OSError, ValueError, AttributeError):
        return {}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the test modules in several worker processes.")
    parser.add_argument("modules", nargs="*", help="dotted module names (default: every tests/test_*.py)")
    parser.add_argument("-j", "--jobs", type=int, default=DEFAULT_WORKERS, help=f"worker processes (default {DEFAULT_WORKERS})")
    parser.add_argument("--record", action="store_true", help="rewrite the timing file with the measured time of every module run")
    parser.add_argument("--timings", default=str(TIMINGS), help="the timing file (default tests/.timings.json)")
    parser.add_argument("--path", action="append", default=[], help="a directory added to the workers' import path (for modules outside tests/)")
    parser.add_argument("--worker", metavar="RESULT_FILE", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        return worker_main(args.modules, args.worker)
    if args.jobs < 1:
        parser.error("-j: at least one worker")
    modules = args.modules or suite_modules()
    timings = Path(args.timings)
    recorded = load_timings(timings)
    bins = bin_modules(costs_for(modules, recorded), args.jobs)
    started = time.monotonic()
    results: list[dict[str, Any]] = []
    problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix="reachmap-parallel-") as tmp:
        procs = [(i, b, Path(tmp) / f"worker{i}.json", spawn(b, Path(tmp) / f"worker{i}.json", args.path)) for i, b in enumerate(bins, 1)]
        for i, b, file, proc in procs:
            _out, err = proc.communicate()
            got = None
            if file.exists():
                try:
                    got = json.loads(file.read_text(encoding="utf-8"))
                except ValueError:
                    got = None
            if proc.returncode != 0 or got is None:
                problems.append(f"worker {i} ({', '.join(b)}) died with exit {proc.returncode} before it reported:\n{err.strip()[-2000:]}")
            else:
                results.extend(got)
    wall = time.monotonic() - started
    return report(results, problems, wall, args.record, timings, recorded, bins)


def report(results: list[dict[str, Any]], problems: list[str], wall: float, record: bool, timings: Path, recorded: Mapping[str, float],
           bins: Sequence[Sequence[str]]) -> int:
    width = max([len(r["module"]) for r in results] + [6])
    print(f"{'module'.ljust(width)}  {'tests':>10}  {'time':>9}  result")
    for r in sorted(results, key=lambda r: -r["seconds"]):
        ok = r["failures"] == 0 and r["errors"] == 0
        count = f"{r['tests']} test{'s' if r['tests'] != 1 else ''}"
        print(f"{r['module'].ljust(width)}  {count:>10}  {r['seconds']:>7.1f} s  {'ok' if ok else 'FAILED'}")
    for r in results:
        if r["failures"] or r["errors"]:
            print(f"\n===== {r['module']}\n{r['output'].rstrip()}")
    for text in problems:
        print(f"\n{text}")
    tests = sum(r["tests"] for r in results)
    skipped = sum(r["skipped"] for r in results)
    bad = sum(r["failures"] + r["errors"] for r in results) + len(problems)
    busy = sum(r["seconds"] for r in results)
    print(f"\nRan {tests} tests in {wall:.1f} s on {len(bins)} worker{'s' if len(bins) != 1 else ''} ({busy:.0f} s of work)"
          + (f", {skipped} skipped" if skipped else ""))
    print("OK" if bad == 0 else f"FAILED ({bad} problem{'s' if bad != 1 else ''})")
    if record:
        merged = dict(recorded)
        merged.update({r["module"]: r["seconds"] for r in results})
        timings.write_text(json.dumps({
            "note": "seconds per test module, recorded with tests/run_parallel.py --record; only used to balance the workers",
            "python": f"{sys.version_info.major}.{sys.version_info.minor}", "modules": dict(sorted(merged.items())),
        }, indent=2) + "\n", encoding="utf-8")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
