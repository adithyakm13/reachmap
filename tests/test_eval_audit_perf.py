"""The audit of a large report reads in batches: a few git processes per commit, not one per citation (M18)."""

import collections
import time
import unittest
from types import SimpleNamespace

from reachmap import gitlayer
from reachmap.evaluate import audit
from tests import support_git as sg

FILES, LINES, PER_FILE = 150, 60, 30
REST = {"change": {}, "ref": "a" * 40, "incompleteness": [], "notes": [], "propagations": [], "backend_calls": [], "suggestions": []}  # what a completed report also holds


class Counting(gitlayer.GitRunner):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.calls = collections.Counter()

    def run(self, args, **kw):
        words = [a for a in args if isinstance(a, str) and not a.startswith("-")]
        self.calls[words[0] if words else "?"] += 1
        if "--batch" in args:
            self.calls["batch"] += 1
        if "blob" in args:
            self.calls["blob"] += 1
        return super().run(args, **kw)


def text(n, version):
    return "".join(f"line {n}.{i} v{version}\n" for i in range(1, LINES + 1))


class LargeReport(sg.TempDirCase):
    def setUp(self):
        super().setUp()
        first = {f"src/f{n:03d}.ts": text(n, 1) for n in range(FILES)}
        second = {f"src/f{n:03d}.ts": text(n, 2) for n in range(0, FILES, 4)}
        self.repo = sg.make_repo(self.tmp, "big", [{"files": first, "message": "one"}, {"files": second, "message": "two"}])
        self.shas = [sg.rev(self.repo, "main~1"), sg.rev(self.repo, "main")]
        self.remote = "file://" + str(self.repo)
        self.workspace = SimpleNamespace(repos={"acme/big": SimpleNamespace(remote=self.remote, host="git", name="acme/big")})

    def report(self, mutate=None):
        cited = []
        for sha, version, step in ((self.shas[0], 1, 1), (self.shas[1], 2, 4)):
            for n in range(0, FILES, step):
                for k in range(PER_FILE):
                    line = 1 + (k * 3) % LINES
                    cited.append({"repo": "acme/big", "path": f"src/f{n:03d}.ts", "line": line, "ref": sha, "detail": f"line {n}.{line} v{version}"})
        if mutate:
            mutate(cited)
        return {"schema": 1, "status": "completed", **REST, "findings": [], "areas": [], "branches": [{"branch": "main", "evidence": cited}]}

    def run_audit(self, report):
        runner = Counting(env=sg.isolated_env(self.tmp / "home"))
        git = gitlayer.Git(self.tmp / "cache", runner=runner)
        started = time.monotonic()
        result = audit.audit_report(report, audit.GitReader(self.workspace, git), "big.json")
        return result, runner.calls, time.monotonic() - started

    def test_thousands_of_citations_are_read_with_a_handful_of_git_processes(self):
        report = self.report()
        result, calls, _ = self.run_audit(report)
        cited = sum(1 for _ in audit.collect(report, []))
        self.assertGreater(cited, 5000)
        self.assertEqual(result.counts, {"ok": result.distinct, "mismatch": 0, "absent": 0, "unreadable": 0})
        self.assertEqual(result.occurrences, cited)
        self.assertEqual(result.structure, [])
        self.assertEqual(calls["batch"], 2, calls)  # one `cat-file --batch` per commit
        self.assertEqual(calls["blob"], 0, calls)  # and no blob read on its own
        self.assertLess(sum(calls.values()), 25, calls)  # fetch, the listings and the batches of two commits: not one process per citation or per file

    def test_the_failures_among_thousands_are_found_and_counted_once_each(self):
        def mutate(cited):
            cited[10]["detail"] = "edited"
            cited[3000]["path"] = "src/missing.ts"
            cited[5000]["line"] = LINES + 5

        result, _, _ = self.run_audit(self.report(mutate))
        self.assertEqual((result.counts["mismatch"], result.counts["absent"], result.counts["unreadable"]), (2, 1, 0))
        self.assertEqual(sorted(f.state for f in result.failures), ["absent", "mismatch", "mismatch"])


class ManyPaths(sg.TempDirCase):
    """More paths at one commit than any chunk: still one `cat-file --batch` for the commit (`files_at` lists the tree in chunks of its own)."""

    def test_more_than_500_paths_of_one_commit_are_one_batch(self):
        n = 520
        repo = sg.make_repo(self.tmp, "wide", [{"files": {f"d{i % 7}/f{i:03d}.txt": f"line {i}\n" for i in range(n)}, "message": "one"}])
        sha = sg.rev(repo, "main")
        workspace = SimpleNamespace(repos={"acme/wide": SimpleNamespace(remote="file://" + str(repo), host="git", name="acme/wide")})
        cited = [{"repo": "acme/wide", "path": f"d{i % 7}/f{i:03d}.txt", "line": 1, "ref": sha, "detail": f"line {i}"} for i in range(n)]
        report = {"schema": 1, "status": "completed", **REST, "findings": [], "areas": [], "branches": [{"branch": "main", "evidence": cited}]}
        runner = Counting(env=sg.isolated_env(self.tmp / "home"))
        result = audit.audit_report(report, audit.GitReader(workspace, gitlayer.Git(self.tmp / "cache", runner=runner)), "wide.json")
        self.assertEqual(result.counts, {"ok": n, "mismatch": 0, "absent": 0, "unreadable": 0})
        self.assertEqual(runner.calls["batch"], 1, runner.calls)
        self.assertEqual(runner.calls["blob"], 0, runner.calls)


if __name__ == "__main__":
    unittest.main()
