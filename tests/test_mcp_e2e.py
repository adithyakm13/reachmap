"""`reachmap mcp` end to end (M14): the real command as a subprocess, driven over stdio with JSON-RPC lines: initialize, list the reports, read a
section, add a note, propose a file, and see the note in a rendering of the report. Also the command's own refusals (exit 2) in process."""

import io
import json
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

from reachmap import cli, notes
from tests import support_cli
from tests import support_git as sg
from tests import support_mcp as mc
from tests import scenario_support as ss

ROOT = Path(__file__).resolve().parent.parent


class Session:
    """A `reachmap mcp` process and a way to talk to it one line at a time."""

    def __init__(self, reports, config=None, env=None):
        args = [sys.executable, "-m", "reachmap", "mcp", "--reports", str(reports)] + (["--config", str(config)] if config else [])
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT, env=env)
        self._id = 0
        self._err = []
        threading.Thread(target=lambda: [self._err.append(x) for x in self.proc.stderr], daemon=True).start()

    def send_raw(self, data):
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def send(self, msg):
        self.send_raw(json.dumps(msg).encode() + b"\n")

    def read(self):
        line = self.proc.stdout.readline()
        assert line.endswith(b"\n"), line
        line.decode("ascii")
        return json.loads(line)

    def request(self, method, params=None):
        self._id += 1
        msg = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            msg["params"] = params
        self.send(msg)
        got = self.read()
        assert got["id"] == self._id, got
        return got

    def notify(self, method, params=None):
        self.send({"jsonrpc": "2.0", "method": method, **({"params": params} if params is not None else {})})

    def initialize(self, name="e2e-client"):
        got = self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": name, "version": "0.1"}})
        self.notify("notifications/initialized")
        return got

    def tool(self, name, arguments=None):
        got = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        return got["result"]

    def close(self):
        """End the input, let the command finish, and read everything it still wrote: every line must be a JSON-RPC message."""
        self.proc.stdin.close()
        try:
            rest = self.proc.stdout.read()
            self.proc.wait(timeout=20)
            for line in rest.split(b"\n")[:-1] if rest else []:
                assert json.loads(line)["jsonrpc"] == "2.0", line
            assert rest == b"" or rest.endswith(b"\n"), rest
            self.leftover = rest
        finally:
            if self.proc.poll() is None:
                self.proc.kill()
            for pipe in (self.proc.stdout, self.proc.stderr):
                pipe.close()
        return self.proc.returncode

    @property
    def stderr(self):
        time.sleep(0.05)
        return b"".join(self._err).decode("utf-8", "replace")


class OverStdio(sg.TempDirCase):
    def setUp(self):
        super().setUp()
        self.r = mc.s01()
        mc.publish(self.tmp, self.r, "rep-r1")
        self.addCleanup(lambda: self.session.proc.poll() is None and self.session.proc.kill())

    def start(self, **kw):
        self.session = Session(self.tmp, **kw)
        return self.session

    def test_initialize_list_the_reports_read_a_section_add_a_note_and_list_it(self):
        s = self.start()
        init = s.initialize()["result"]
        self.assertEqual(init["protocolVersion"], "2025-06-18")
        self.assertEqual(init["serverInfo"]["name"], "reachmap")
        self.assertEqual(init["capabilities"], {"tools": {}})
        tools = s.request("tools/list")["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], ["list_reports", "get_report_summary", "get_section", "get_evidence", "list_notes", "add_note", "propose_file"])
        listed = s.tool("list_reports")
        self.assertFalse(listed["isError"])
        self.assertEqual([i["name"] for i in listed["structuredContent"]["reports"]], ["rep-r1"])
        section = s.tool("get_section", {"name": "rep-r1", "section": "areas"})
        self.assertEqual(section["structuredContent"]["total"], 4)
        self.assertTrue(section["content"][0]["text"].startswith("The text between the fences"))
        area = section["structuredContent"]["items"][0]["id"]
        added = s.tool("add_note", {"name": "rep-r1", "target_id": area, "text": "the badge is used here", "kind": "observation"})
        self.assertFalse(added["isError"], added)
        self.assertEqual(added["structuredContent"]["note"]["recorded_via"], "recorded via MCP, client reported as")
        self.assertEqual(added["structuredContent"]["note"]["client"]["name"]["text"], "e2e-client")
        got = s.tool("list_notes", {"name": "rep-r1"})
        self.assertEqual(got["structuredContent"]["count"], 1)
        self.assertEqual(s.close(), 0)
        (stored,) = notes.read(self.tmp, self.r["review"]["key"]).items
        self.assertEqual(stored.stamp, "recorded via MCP, client reported as e2e-client")
        self.assertEqual(self.session.stderr.count("client connected"), 1)

    def test_a_message_sent_in_pieces_is_read_when_its_line_ends_and_a_batch_is_refused(self):
        s = self.start()
        s.initialize()
        data = json.dumps({"jsonrpc": "2.0", "id": 50, "method": "ping"}).encode() + b"\n"
        s.send_raw(data[:10])
        time.sleep(0.2)
        s.send_raw(data[10:25])
        time.sleep(0.2)
        s.send_raw(data[25:])
        self.assertEqual(s.read(), {"jsonrpc": "2.0", "id": 50, "result": {}})
        s.send_raw(json.dumps([{"jsonrpc": "2.0", "id": 1, "method": "ping"}]).encode() + b"\n")
        self.assertEqual(s.read()["error"]["code"], -32600)
        s.send_raw(b"\xff not json\n")
        self.assertEqual(s.read()["error"]["code"], -32700)
        self.assertEqual(s.close(), 0)

    def test_standard_output_is_only_json_rpc_lines_and_standard_error_has_no_report_content(self):
        s = self.start()
        s.initialize()
        area = s.tool("get_section", {"name": "rep-r1", "section": "areas"})["structuredContent"]["items"][0]
        s.tool("add_note", {"name": "rep-r1", "text": "SENTINEL-NOTE-TEXT", "kind": "question"})
        s.send_raw(b"{broken\n")
        s.read()
        self.assertEqual(s.close(), 0)
        err = s.stderr
        self.assertNotIn("SENTINEL", err)
        self.assertNotIn(area["label"]["text"], err)
        self.assertNotIn(str(self.tmp), err)

    def test_a_request_before_initialize_is_refused_and_a_second_initialize_too(self):
        s = self.start()
        self.assertEqual(s.request("tools/list")["error"]["code"], -32002)
        s.initialize("first")
        again = s.request("initialize", {"protocolVersion": "2025-06-18", "clientInfo": {"name": "second", "version": "1"}})
        self.assertEqual(again["error"]["code"], -32600)
        s.tool("add_note", {"name": "rep-r1", "text": "x", "kind": "question"})
        self.assertEqual(s.close(), 0)
        self.assertEqual(notes.read(self.tmp, self.r["review"]["key"]).items[0].client.name, "first")

    def test_a_notification_that_asks_for_a_tool_changes_nothing_and_leaves_no_output(self):
        s = self.start()
        s.initialize()
        s.notify("tools/call", {"name": "add_note", "arguments": {"name": "rep-r1", "text": "x", "kind": "question"}})
        s.request("ping")  # the ping answer comes after the notification was read: it was read and ignored
        self.assertEqual(s.close(), 0)
        self.assertEqual(s.leftover, b"")
        self.assertEqual(notes.read(self.tmp, self.r["review"]["key"]).items, ())

    def test_closing_the_input_ends_the_command_with_exit_0(self):
        s = self.start()
        self.assertEqual(s.close(), 0)

    def test_the_note_shows_in_the_report_rendering_and_goes_stale_after_a_rerun(self):
        s = self.start()
        s.initialize()
        s.tool("add_note", {"name": "rep-r1", "text": "recorded via the model", "kind": "observation"})
        s.close()
        out = io.StringIO()
        self.assertEqual(cli.main(["report", str(self.tmp / "rep-r1.json")], stdout=out, stderr=io.StringIO()), 0)
        self.assertIn("## Model notes", out.getvalue())
        self.assertIn("recorded via MCP, client reported as `e2e-client`", out.getvalue())
        mc.publish(self.tmp, mc.rerun(self.r), "rep-r2")
        s2 = Session(self.tmp)
        self.session = s2
        s2.initialize("later-client")
        listed = s2.tool("list_notes", {"name": "rep-r2"})["structuredContent"]["notes"]
        s2.close()
        self.assertEqual([n["state"] for n in listed], ["stale"])


class WithFixtures(sg.TempDirCase):
    """With a configuration the command can check a proposed file against the real repositories (the M1 fixtures)."""

    def test_propose_a_file_over_stdio(self):
        run = support_cli.cli_run("--repo", "acme/api", "--compare", "main...scenario/s15-python-callers")
        report = json.loads(run.text)
        mc.publish(self.tmp, report, "s15-r1")
        home = self.tmp / "home"
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home), "XDG_CONFIG_HOME": str(home / ".xdg"), **support_cli.env_vars(), "PYTHONPATH": str(ROOT)}
        home.mkdir()
        s = Session(self.tmp, config=support_cli.config_path(), env=env)
        try:
            s.initialize()
            target = s.tool("get_section", {"name": "s15-r1", "section": "areas"})["structuredContent"]["items"][0]["id"]
            ok = s.tool("propose_file", {"name": "s15-r1", "target_id": target, "repo": "acme/api", "path": "services/billing/app/services/settlement.py", "symbol": "format_cents"})
            self.assertFalse(ok["isError"], ok)
            v = ok["structuredContent"]["note"]["verification"]
            self.assertEqual((v["status"], v["read"]), ("verified", "found"))
            no = s.tool("propose_file", {"name": "s15-r1", "target_id": target, "repo": "acme/api", "path": "services/billing/app/services/missing.py", "symbol": "format_cents"})
            self.assertEqual(no["structuredContent"]["note"]["verification"]["reason"], "file_absent")
            listed = s.tool("list_notes", {"name": "s15-r1"})["structuredContent"]
            self.assertEqual([n["kind"] for n in listed["notes"]], ["proposed_file", "proposed_file"])
        finally:
            self.assertEqual(s.close(), 0)
        self.assertNotIn("SECRET", s.stderr)


class Command(sg.TempDirCase):
    def run_main(self, *args):
        out, err = io.BytesIO(), io.StringIO()

        text_out = io.TextIOWrapper(out, encoding="utf-8", newline="\n")
        code = cli.main(["mcp", *args], stdout=text_out, stderr=err, stdin=io.BytesIO(b""))
        text_out.flush()
        return code, out.getvalue(), err.getvalue()

    def test_a_missing_reports_option_a_missing_folder_and_a_file_are_exit_2(self):
        code, out, err = self.run_main()
        self.assertEqual((code, out), (2, b""))
        self.assertIn("--reports", err)
        code, _, err = self.run_main("--reports", str(self.tmp / "nope"))
        self.assertEqual(code, 2)
        self.assertNotIn(str(self.tmp), err)
        (self.tmp / "file").write_text("x")
        self.assertEqual(self.run_main("--reports", str(self.tmp / "file"))[0], 2)

    def test_a_config_that_cannot_be_read_is_exit_2_and_names_no_value(self):
        (self.tmp / "bad.toml").write_text("workspace = 5\n")
        code, out, err = self.run_main("--reports", str(self.tmp), "--config", str(self.tmp / "bad.toml"))
        self.assertEqual((code, out), (2, b""))
        self.assertIn("config error", err)
        code, _, err = self.run_main("--reports", str(self.tmp), "--config", str(self.tmp / "missing.toml"))
        self.assertEqual(code, 2)

    def test_a_reports_directory_whose_review_folder_is_a_file_is_exit_2(self):
        (self.tmp / ".reachmap").write_text("x")
        self.assertEqual(self.run_main("--reports", str(self.tmp))[0], 2)

    def test_the_command_serves_the_input_it_is_given_and_ends_when_it_ends(self):
        out, err = io.BytesIO(), io.StringIO()
        text_out = io.TextIOWrapper(out, encoding="utf-8", newline="\n")
        msg = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "in-process", "version": "1"}}}) + "\n"
        code = cli.main(["mcp", "--reports", str(self.tmp)], stdout=text_out, stderr=err, stdin=io.BytesIO(msg.encode()))
        text_out.flush()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["result"]["serverInfo"]["name"], "reachmap")
        self.assertIn("client connected: in-process", err.getvalue())

    def test_help_names_the_command(self):
        out = io.StringIO()
        code = cli.main(["mcp", "--help"], stdout=out, stderr=io.StringIO())
        self.assertEqual(code, 0)
        self.assertIn("--reports", out.getvalue())
        self.assertIn("--config", out.getvalue())

    def test_the_reports_option_has_no_default_and_there_is_no_host_or_port_option(self):
        out = io.StringIO()
        cli.main(["mcp", "--help"], stdout=out, stderr=io.StringIO())
        for word in ("--port", "--host", "--listen", "--token", "--socket"):
            self.assertNotIn(word, out.getvalue())


if __name__ == "__main__":
    unittest.main()
