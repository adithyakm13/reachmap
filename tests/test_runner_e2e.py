"""M12 end to end on the fixture workspace: start the runner, POST s13, poll to completed, GET the report and the page, POST a decision
(channel `runner`), GET the report again with the decision current. Then the refusals of the decision endpoint on that report."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from reachmap.review import store
from reachmap.runner import server
from tests import scenario_support as ss
from tests import support_cli, support_git as sg
from tests.support_runner import Live

S13 = {"repo": "acme/api", "compare": "main...scenario/s13-branch-matrix"}


class EndToEnd(unittest.TestCase):
    live: Live
    job: dict
    name: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.live = Live(config_path=support_cli.config_path(), env=support_cli.env_vars(), git_env=sg.isolated_env(ss.env().home))
        cls.live.__enter__()
        cls.addClassCleanup(cls.live.__exit__, None, None, None)
        posted = cls.live.post("/api/jobs", S13)
        assert posted.status == 202, posted.body
        cls.job = cls.live.wait_state(posted.json()["id"], ("completed", "failed", "timed_out", "cancelled"), timeout=120)
        cls.name = cls.job.get("report") or ""

    def test_the_job_completed_with_a_report(self) -> None:
        self.assertEqual(self.job["state"], "completed", self.job)
        self.assertEqual(self.job["exit_code"], 0)
        self.assertRegex(self.name, r"^acme-api-compare-main-scenario-s13-branch-matrix-[0-9a-f]{8}-[0-9a-f]{12}-r1$")
        self.assertTrue((self.live.reports / f"{self.name}.json").is_file())
        self.assertTrue(self.job["started_at"] <= self.job["finished_at"])

    def test_the_flow(self) -> None:
        live = self.live
        listed = live.get("/api/reports").json()["reports"]
        self.assertIn(self.name, [r["name"] for r in listed])
        item = [r for r in listed if r["name"] == self.name][0]
        self.assertEqual((item["status"], item["revision"]), ("completed", 1))
        self.assertEqual(item["key"]["repo"], "acme/api")

        r = live.get(f"/api/reports/{self.name}")
        self.assertEqual(r.status, 200)
        report = r.json()
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["review"]["revision"], 1)
        self.assertEqual(report["review"]["decisions"]["current"], [])
        self.assertTrue(report["findings"] or report["areas"])

        page = live.get(f"/api/reports/{self.name}.html")
        self.assertEqual(page.status, 200)
        self.assertIn("text/html", page.headers["content-type"])
        self.assertEqual(page.headers["content-security-policy"], server.report_policy())
        self.assertNotIn(b"Quinn", page.body)

        target = report["areas"][0] if report["areas"] else report["findings"][0]
        verdict = "skip" if report["areas"] else "reject"
        body = {"target": target["id"], "verdict": verdict, "reason": "retired in this release", "author": "Quinn"}
        d = live.post(f"/api/reports/{self.name}/decisions", body)
        self.assertEqual(d.status, 201, d.body)
        recorded = d.json()
        self.assertEqual((recorded["target_id"], recorded["verdict"], recorded["author"], recorded["channel"]), (target["id"], verdict, "Quinn", "runner"))
        self.assertEqual(recorded["revision"], 1)

        again = live.get(f"/api/reports/{self.name}").json()
        current = again["review"]["decisions"]["current"]
        self.assertEqual([c["target_id"] for c in current], [target["id"]])
        self.assertEqual((current[0]["verdict"], current[0]["channel"], current[0]["author"]), (verdict, "runner", "Quinn"))
        self.assertEqual(again["review"]["decisions"]["stale"], [])
        self.assertNotIn(target["id"], again["review"]["decisions"]["undecided"]["areas" if report["areas"] else "findings"])
        # the analysis itself is what it was: a decision is risk acceptance, it clears nothing
        for key in ("areas", "findings", "branches", "counts", "incompleteness", "propagations"):
            self.assertEqual(again[key], report[key], key)

        page2 = live.get(f"/api/reports/{self.name}.html")
        self.assertIn(b"Quinn", page2.body)
        self.assertIn(b"retired in this release", page2.body)

        lines = store.decisions_path(live.reports, store.make_key(report["review"]["key"]["workspace"], report["review"]["key"]["repo"], report["review"]["key"]["change"])).read_text(encoding="ascii").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["channel"], "runner")

        # the file on disk is not rewritten by reading it (the decisions are computed when it is served)
        on_disk = json.loads((live.reports / f"{self.name}.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk["review"]["decisions"]["current"], [])

        # a second run of the same change is revision two and the decision is carried to it
        job = live.wait_state(live.post("/api/jobs", S13).json()["id"], ("completed", "failed", "timed_out"), timeout=120)
        self.assertEqual(job["state"], "completed", job)
        self.assertTrue(job["report"].endswith("-r2"))
        second = live.get(f"/api/reports/{job['report']}").json()
        self.assertEqual(second["review"]["revision"], 2)
        self.assertEqual([d["target_id"] for d in second["review"]["decisions"]["current"]], [target["id"]])

    def test_the_decision_endpoint_refuses_what_is_not_a_decision(self) -> None:
        live = self.live
        report = live.get(f"/api/reports/{self.name}").json()
        area = report["areas"][0]["id"] if report["areas"] else None
        finding = report["findings"][0]["id"]
        mark = "MARK-dec-55"

        def stored() -> int:
            folder = live.reports / ".reachmap" / "decisions"
            return sum(len(p.read_text(encoding="ascii").splitlines()) for p in folder.glob("*.jsonl")) if folder.exists() else 0

        before = stored()
        base = {"target": finding, "verdict": "accept", "author": "Quinn"}
        cases = [
            ({**base, "target": "f-" + "0" * 16}, 404, {"error": "unknown_target"}),
            ({**base, "target": "not-an-id"}, 400, {"error": "invalid_decision", "field": "target"}),
            ({**base, "target": 5}, 400, {"error": "invalid_decision", "field": "target"}),
            ({**base, "verdict": "skip"}, 400, {"error": "invalid_decision", "field": "verdict"}),
            ({**base, "verdict": mark}, 400, {"error": "invalid_decision", "field": "verdict"}),
            ({**base, "verdict": "reject"}, 400, {"error": "invalid_decision", "field": "reason"}),
            ({**base, "reason": "x" * 2001}, 400, {"error": "invalid_decision", "field": "reason"}),
            ({**base, "reason": 5}, 400, {"error": "invalid_decision", "field": "reason"}),
            ({k: v for k, v in base.items() if k != "author"}, 400, {"error": "invalid_decision", "field": "author"}),
            ({**base, "author": "   "}, 400, {"error": "invalid_decision", "field": "author"}),
            ({**base, "author": "q" * 101}, 400, {"error": "invalid_decision", "field": "author"}),
            ({**base, "author": "Quinn\u202e"}, 400, {"error": "invalid_decision", "field": "author"}),
            ({**base, "author": "Q\x1bq"}, 400, {"error": "invalid_decision", "field": "author"}),
            ({**base, "author": "Quinn\n"}, 400, {"error": "invalid_decision", "field": "author"}),
            ({**base, "reason": "a\u202eb"}, 400, {"error": "invalid_decision", "field": "reason"}),
            ({**base, "reason": "a\x1bb"}, 400, {"error": "invalid_decision", "field": "reason"}),
            ({**base, "reason": "a\u2028b"}, 400, {"error": "invalid_decision", "field": "reason"}),
            ({**base, "author": None}, 400, {"error": "invalid_decision", "field": "author"}),
            ({**base, "channel": "mcp"}, 400, {"error": "invalid_decision", "field": "keys"}),
            ({**base, "fingerprint": "0" * 64}, 400, {"error": "invalid_decision", "field": "keys"}),
            ({**base, mark: 1}, 400, {"error": "invalid_decision", "field": "keys"}),
            ({k: v for k, v in base.items() if k != "target"}, 400, {"error": "invalid_decision", "field": "target"}),
        ]
        if area:
            cases.append(({**base, "target": area, "verdict": "accept"}, 400, {"error": "invalid_decision", "field": "verdict"}))
        for payload, status, expected in cases:
            with self.subTest(payload=str(payload)[:60]):
                r = live.post(f"/api/reports/{self.name}/decisions", payload)
                self.assertEqual((r.status, r.json()), (status, expected))
                self.assertNotIn(mark.encode(), r.body)
        for body in (b"", b"{", b"[]", b'"x"'):
            self.assertEqual(live.request("POST", f"/api/reports/{self.name}/decisions", body=body).status, 400)
        self.assertEqual(stored(), before)  # nothing refused was written


class RealFailureAndTimeout(unittest.TestCase):
    def test_a_change_that_cannot_be_resolved_is_failed_with_a_report_and_the_timeout_is_not_reached(self) -> None:
        with Live(config_path=support_cli.config_path(), env=support_cli.env_vars(), git_env=sg.isolated_env(ss.env().home)) as live:
            job_id = live.post("/api/jobs", {"repo": "acme/api", "compare": "main...no-such-branch"}).json()["id"]
            job = live.wait_state(job_id, ("completed", "failed", "timed_out"), timeout=120)
            self.assertEqual(job["state"], "failed")
            self.assertEqual(job["exit_code"], 3)
            self.assertTrue(job["report"])
            report = live.get(f"/api/reports/{job['report']}").json()
            self.assertEqual(report["status"], "failed")
            self.assertIsNone(report["findings"])
            self.assertIsNone(report["counts"])


if __name__ == "__main__":
    unittest.main()
