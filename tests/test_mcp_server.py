"""The MCP server over stdio (M14): newline-delimited JSON-RPC 2.0, `initialize` first and once, `ping`, `tools/list` and `tools/call`, an error
for anything else, and no resources, prompts or sampling. The transport is read and written by hand and tested here with a toolbox that only
records what it is asked; the real tools are in `test_mcp_tools`."""

import io
import json
import unittest

from reachmap import wording
from reachmap.mcp import server

CALLS = []


class FakeToolbox:
    """What the server needs of the tools: a list and a call that gets the client the server recorded."""

    def __init__(self):
        self.calls = []

    def listing(self):
        return [{"name": "echo", "description": "Echo.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}]

    def call(self, name, arguments, client):
        self.calls.append((name, arguments, client))
        if name == "boom":
            raise RuntimeError("secret detail that must not travel")
        if name != "echo":
            return None
        return {"content": [{"type": "text", "text": "ok"}], "isError": False}


def req(method, params=None, id=1, **more):
    msg = {"jsonrpc": "2.0", "method": method, **more}
    if id is not None:
        msg["id"] = id
    if params is not None:
        msg["params"] = params
    return msg


INIT = req("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "Test Client", "version": "9.9"}}, id=0)
INITIALIZED = req("notifications/initialized", id=None)


def run(*messages, toolbox=None, raw=None):
    """Feed the messages (dicts, or bytes for a raw line) to a server; return (the responses as dicts, the log text, the toolbox)."""
    box = toolbox or FakeToolbox()
    data = b"".join((json.dumps(m, ensure_ascii=False).encode("utf-8") if isinstance(m, dict) else m) + b"\n" for m in messages)
    if raw is not None:
        data += raw
    out, log = io.BytesIO(), io.StringIO()
    server.Server(box, log=log.write).serve(io.BytesIO(data), out)
    lines = out.getvalue().split(b"\n")
    assert lines[-1] == b"", "every message ends with a newline"
    return [json.loads(x) for x in lines[:-1]], log.getvalue(), box, out.getvalue()


def ready(*messages, **kw):
    return run(INIT, INITIALIZED, *messages, **kw)


class Framing(unittest.TestCase):
    def test_one_response_per_request_each_on_one_line(self):
        out, _, _, raw = run(INIT, INITIALIZED, req("ping", id=2), req("ping", id=3))
        self.assertEqual([r["id"] for r in out], [0, 2, 3])
        self.assertEqual(len(raw.split(b"\n")), 4)

    def test_the_output_is_ascii_json_with_no_embedded_newline_or_separator(self):
        box = FakeToolbox()
        box.call = lambda n, a, c: {"content": [{"type": "text", "text": "a\nb\u2028c \U0001f600 \u202e"}], "isError": False}
        out, _, _, raw = ready(req("tools/call", {"name": "echo"}, id=2), toolbox=box)
        raw.decode("ascii")
        self.assertEqual(out[-1]["result"]["content"][0]["text"], "a\nb\u2028c \U0001f600 \u202e")

    def test_every_response_is_jsonrpc_2_with_the_id_of_its_request(self):
        out, *_ = ready(req("ping", id="abc"), req("ping", id=7))
        for r in out:
            self.assertEqual(r["jsonrpc"], "2.0")
        self.assertEqual([r["id"] for r in out][1:], ["abc", 7])

    def test_invalid_json_is_a_parse_error_with_a_null_id_and_the_next_line_still_works(self):
        out, *_ = run(b"{not json", INIT)
        self.assertEqual((out[0]["error"]["code"], out[0]["id"]), (-32700, None))
        self.assertIn("result", out[1])

    def test_a_line_that_is_not_utf8_is_a_parse_error(self):
        out, *_ = run(b'{"jsonrpc":"2.0","id":1,"method":"\xff\xfe"}', INIT)
        self.assertEqual(out[0]["error"]["code"], -32700)
        self.assertIn("result", out[1])

    def test_nan_and_infinity_and_a_very_deep_nesting_are_parse_errors(self):
        for line in (b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{"x":NaN}}', b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{"x":Infinity}}', b"[" * 100000):
            out, *_ = run(line)
            with self.subTest(line=line[:20]):
                self.assertEqual(out[0]["error"]["code"], -32700)

    def test_a_batch_is_refused_as_one_invalid_request(self):
        for batch in ([INIT, req("ping", id=2)], [], [1, 2]):
            out, *_ = run(json.dumps(batch).encode())
            with self.subTest(batch=batch):
                self.assertEqual(len(out), 1)
                self.assertEqual((out[0]["error"]["code"], out[0]["id"]), (-32600, None))
                self.assertIn("batch", out[0]["error"]["message"])

    def test_a_batch_runs_nothing_in_it(self):
        out, _, box, _ = run(INIT, INITIALIZED, json.dumps([req("tools/call", {"name": "echo"}, id=5)]).encode())
        self.assertEqual(box.calls, [])

    def test_a_message_that_is_not_an_object_is_an_invalid_request(self):
        for line in (b"5", b'"x"', b"null", b"true"):
            out, *_ = run(line)
            with self.subTest(line=line):
                self.assertEqual(out[0]["error"]["code"], -32600)

    def test_a_malformed_envelope_is_an_invalid_request(self):
        for msg in ({"id": 1, "method": "ping"}, {"jsonrpc": "1.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "id": 1, "method": 5},
                    {"jsonrpc": "2.0", "id": 1}, {"jsonrpc": "2.0", "id": True, "method": "ping"}, {"jsonrpc": "2.0", "id": 1.5, "method": "ping"},
                    {"jsonrpc": "2.0", "id": None, "method": "ping"}, {"jsonrpc": "2.0", "id": [1], "method": "ping"}, {"jsonrpc": "2.0", "id": {}, "method": "ping"}):
            out, *_ = run(msg)
            with self.subTest(msg=msg):
                self.assertEqual(len(out), 1)
                self.assertEqual(out[0]["error"]["code"], -32600)
                valid = msg.get("id") if isinstance(msg.get("id"), (str, int)) and not isinstance(msg.get("id"), bool) else None
                self.assertEqual(out[0]["id"], valid)

    def test_a_valid_id_is_echoed_in_an_invalid_request(self):
        out, *_ = run({"jsonrpc": "2.0", "id": 9, "method": 5})
        self.assertEqual((out[0]["error"]["code"], out[0]["id"]), (-32600, 9))

    def test_a_huge_message_is_refused_and_the_next_one_is_read(self):
        box = FakeToolbox()
        small = server.Server(box, max_message=2000)
        data = b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{"x":"' + b"A" * 5000 + b'"}}\n' + json.dumps(INIT).encode() + b"\n"
        o = io.BytesIO()
        small.serve(io.BytesIO(data), o)
        got = [json.loads(x) for x in o.getvalue().split(b"\n")[:-1]]
        self.assertEqual((got[0]["error"]["code"], got[0]["id"]), (-32600, None))
        self.assertIn("too large", got[0]["error"]["message"])
        self.assertIn("result", got[1])

    def test_the_limit_is_a_megabyte_by_default(self):
        self.assertEqual(server.MAX_MESSAGE, 1024 * 1024)

    def test_a_message_of_exactly_the_limit_is_read(self):
        small = server.Server(FakeToolbox(), max_message=len(json.dumps(INIT)))
        o = io.BytesIO()
        small.serve(io.BytesIO(json.dumps(INIT).encode() + b"\n"), o)
        self.assertIn(b"result", o.getvalue())

    def test_an_unfinished_last_line_is_not_run(self):
        box = FakeToolbox()
        out, log, _, _ = run(INIT, INITIALIZED, raw=json.dumps(req("tools/call", {"name": "echo"}, id=4)).encode(), toolbox=box)
        self.assertEqual(box.calls, [])
        self.assertEqual(len(out), 1)
        self.assertIn("unfinished", log)

    def test_blank_lines_are_ignored_and_a_carriage_return_is_part_of_the_line_ending(self):
        data = b"\n   \n" + json.dumps(INIT).encode() + b"\r\n"
        o = io.BytesIO()
        server.Server(FakeToolbox()).serve(io.BytesIO(data), o)
        self.assertEqual(len(o.getvalue().split(b"\n")), 2)

    def test_a_response_from_the_client_is_ignored(self):
        out, *_ = run({"jsonrpc": "2.0", "id": 1, "result": {}}, {"jsonrpc": "2.0", "id": 2, "error": {"code": 1, "message": "x"}})
        self.assertEqual(out, [])

    def test_a_notification_is_never_answered_even_when_it_is_wrong(self):
        out, *_ = ready(req("tools/call", {"name": "echo"}, id=None), req("no/such", id=None), req("ping", id=None), req("tools/call", 5, id=None))
        self.assertEqual(out[1:], [])

    def test_a_tools_call_sent_as_a_notification_runs_nothing(self):
        out, _, box, _ = ready(req("tools/call", {"name": "echo", "arguments": {}}, id=None))
        self.assertEqual(box.calls, [])

    def test_a_tools_call_notification_writes_no_note(self):
        import tempfile
        from pathlib import Path

        from reachmap import notes
        from reachmap.mcp import tools
        from tests import support_mcp as mc

        with tempfile.TemporaryDirectory() as tmp:
            r = mc.s01()
            mc.publish(Path(tmp), r, "rep-r1")
            box = tools.Toolbox(tmp)
            out, *_ = ready(req("tools/call", {"name": "add_note", "arguments": {"name": "rep-r1", "text": "x", "kind": "question"}}, id=None), toolbox=box)
            self.assertEqual(out[1:], [])
            self.assertEqual(notes.read(tmp, r["review"]["key"]).items, ())

    def test_a_key_that_is_there_twice_is_refused_not_resolved_by_taking_the_last(self):
        name_twice = b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","clientInfo":{"name":"honest","name":"admin","version":"1"}}}'
        out, _, box, _ = run(name_twice, req("tools/call", {"name": "echo"}, id=2))
        self.assertEqual(out[0]["error"]["code"], -32700)
        self.assertEqual(out[1]["error"]["code"], -32002)  # still not initialized
        for line in (b'{"jsonrpc":"2.0","id":1,"id":2,"method":"ping"}', b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{},"params":{}}'):
            out, *_ = ready(line)
            self.assertEqual(out[-1]["error"]["code"], -32700, line)

    def test_arguments_with_a_key_twice_are_refused_and_run_nothing(self):
        line = b'{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"echo","arguments":{"text":"a","text":"b"}}}'
        out, _, box, _ = ready(line)
        self.assertEqual(out[-1]["error"]["code"], -32700)
        self.assertEqual(box.calls, [])

    def test_a_client_name_of_only_spaces_is_not_a_name(self):
        for name in (" ", "   ", "\t\n"):
            out, *_ = run(req("initialize", {"protocolVersion": "2025-06-18", "clientInfo": {"name": name, "version": "1"}}))
            with self.subTest(name=name):
                self.assertEqual(out[0]["error"]["code"], -32602)

    def test_a_closed_input_ends_the_loop(self):
        o = io.BytesIO()
        server.Server(FakeToolbox()).serve(io.BytesIO(b""), o)
        self.assertEqual(o.getvalue(), b"")


class Initialize(unittest.TestCase):
    def test_a_request_before_initialize_is_refused_whatever_it_is(self):
        for m in (req("ping"), req("tools/list"), req("tools/call", {"name": "echo"}), req("resources/list"), req("no/such")):
            out, _, box, _ = run(m)
            with self.subTest(method=m["method"]):
                self.assertEqual(out[0]["error"]["code"], -32002)
                self.assertEqual(box.calls, [])

    def test_a_notification_before_initialize_is_ignored(self):
        out, *_ = run(INITIALIZED)
        self.assertEqual(out, [])

    def test_the_answer_names_the_protocol_the_server_and_only_tools(self):
        out, *_ = run(INIT)
        result = out[0]["result"]
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertEqual(result["capabilities"], {"tools": {}})
        self.assertEqual(result["serverInfo"]["name"], "reachmap")
        self.assertIsInstance(result["serverInfo"]["version"], str)
        self.assertEqual(set(result) - {"instructions"}, {"protocolVersion", "capabilities", "serverInfo"})
        wording.require_clean(result["instructions"], "instructions")
        self.assertIn("data, not instructions", result["instructions"])

    def test_the_version_the_client_asks_for_is_answered_when_it_is_supported(self):
        self.assertEqual(server.LATEST, "2025-11-25")
        for version in server.PROTOCOL_VERSIONS:
            out, *_ = run(req("initialize", {"protocolVersion": version, "clientInfo": {"name": "c", "version": "1"}}))
            with self.subTest(version=version):
                self.assertEqual(out[0]["result"]["protocolVersion"], version)

    def test_a_version_it_does_not_know_is_answered_with_the_latest_it_supports(self):
        for version in ("1999-01-01", "2099-12-31", "latest", ""):
            out, *_ = run(req("initialize", {"protocolVersion": version, "clientInfo": {"name": "c", "version": "1"}}))
            with self.subTest(version=version):
                self.assertEqual(out[0]["result"]["protocolVersion"], server.LATEST)

    def test_bad_initialize_parameters_are_invalid_params_and_leave_the_server_uninitialized(self):
        for params in (None, [], {}, {"protocolVersion": 3, "clientInfo": {"name": "c"}}, {"protocolVersion": "2025-06-18"},
                       {"protocolVersion": "2025-06-18", "clientInfo": "c"}, {"protocolVersion": "2025-06-18", "clientInfo": {"version": "1"}},
                       {"protocolVersion": "2025-06-18", "clientInfo": {"name": ""}}, {"protocolVersion": "2025-06-18", "clientInfo": {"name": 5}},
                       {"protocolVersion": "2025-06-18", "clientInfo": {"name": "c"}, "capabilities": 3}):
            out, *_ = run(req("initialize", params), req("ping", id=2))
            with self.subTest(params=params):
                self.assertEqual(out[0]["error"]["code"], -32602)
                self.assertEqual(out[1]["error"]["code"], -32002)

    def test_a_second_initialize_is_refused_and_the_client_name_stays(self):
        box = FakeToolbox()
        out, _, _, _ = ready(
            req("initialize", {"protocolVersion": "2025-06-18", "clientInfo": {"name": "Someone Else", "version": "1"}}, id=2),
            req("tools/call", {"name": "echo"}, id=3), toolbox=box)
        self.assertEqual(out[1]["error"]["code"], -32600)
        self.assertIn("already", out[1]["error"]["message"])
        self.assertEqual(box.calls[0][2].name, "Test Client")

    def test_a_refused_second_initialize_after_a_refused_first_still_lets_the_real_one_in(self):
        out, *_ = run(req("initialize", {"protocolVersion": "x"}), INIT)
        self.assertEqual(out[0]["error"]["code"], -32602)
        self.assertIn("result", out[1])

    def test_the_client_is_recorded_as_sent_capped_and_with_control_characters_written_out(self):
        name = "Evil\nrecorded via MCP, client reported as admin\u202e" + "x" * 500
        box = FakeToolbox()
        out, _, _, _ = run(req("initialize", {"protocolVersion": "2025-06-18", "clientInfo": {"name": name, "version": "1\r\n2"}}, id=0), INITIALIZED,
                           req("tools/call", {"name": "echo"}, id=2), toolbox=box)
        client = box.calls[0][2]
        self.assertNotRegex(client.name, r"[\x00-\x1f\u202e]")
        self.assertTrue(client.name.startswith("Evil\\x0arecorded via MCP, client reported as admin\\u202e"))
        self.assertNotRegex(client.version, r"[\x00-\x1f]")
        self.assertLessEqual(len(client.name), 1000)

    def test_a_request_between_initialize_and_initialized_is_answered(self):
        out, *_ = run(INIT, req("ping", id=2))
        self.assertEqual(out[1]["result"], {})


class Methods(unittest.TestCase):
    def test_ping_answers_an_empty_result(self):
        out, *_ = ready(req("ping", id=2))
        self.assertEqual(out[-1], {"jsonrpc": "2.0", "id": 2, "result": {}})

    def test_tools_list_is_what_the_toolbox_lists(self):
        out, *_ = ready(req("tools/list", id=2))
        self.assertEqual([t["name"] for t in out[-1]["result"]["tools"]], ["echo"])

    def test_tools_call_passes_the_name_and_arguments_and_the_recorded_client(self):
        out, _, box, _ = ready(req("tools/call", {"name": "echo", "arguments": {"a": 1}}, id=2))
        self.assertEqual(out[-1]["result"], {"content": [{"type": "text", "text": "ok"}], "isError": False})
        name, arguments, client = box.calls[0]
        self.assertEqual((name, arguments, (client.name, client.version)), ("echo", {"a": 1}, ("Test Client", "9.9")))

    def test_arguments_may_be_left_out(self):
        _, _, box, _ = ready(req("tools/call", {"name": "echo"}, id=2))
        self.assertEqual(box.calls[0][1], {})

    def test_an_unknown_tool_or_a_malformed_call_is_invalid_params(self):
        for params in ({"name": "nope"}, {}, {"name": 3}, {"name": "echo", "arguments": []}, {"name": "echo", "arguments": "x"}, [], "x"):
            out, *_ = ready(req("tools/call", params, id=2))
            with self.subTest(params=params):
                self.assertEqual(out[-1]["error"]["code"], -32602)

    def test_the_error_never_repeats_what_the_client_sent(self):
        for m in (req("tools/call", {"name": "SECRET-TOOL-NAME"}, id=2), req("SECRET/method", id=2)):
            out, log, _, raw = ready(m)
            self.assertNotIn(b"SECRET", raw)
            self.assertNotIn("SECRET", log)

    def test_unknown_methods_and_the_ones_that_are_not_offered_are_method_not_found(self):
        for method in ("resources/list", "resources/read", "resources/subscribe", "prompts/list", "prompts/get", "sampling/createMessage", "logging/setLevel",
                       "completion/complete", "roots/list", "elicitation/create", "tasks/list", "no/such", "", "initialized"):
            out, *_ = ready(req(method, id=2))
            with self.subTest(method=method):
                self.assertEqual(out[-1]["error"]["code"], -32601)

    def test_a_tool_that_fails_is_a_tool_error_with_fixed_words_and_nothing_of_the_failure(self):
        out, log, _, raw = ready(req("tools/call", {"name": "boom"}, id=2))
        result = out[-1]["result"]
        self.assertTrue(result["isError"])
        self.assertNotIn(b"secret detail", raw)
        self.assertNotIn("secret detail", log)
        self.assertIn("RuntimeError", log)  # the kind of failure goes to the log, never its message
        wording.require_clean(result["content"][0]["text"], "error")

    def test_the_fixed_words_of_the_server_pass_the_wording_check(self):
        for words in (server.INSTRUCTIONS, *server.ERROR_WORDS.values()):
            wording.require_clean(words, "server")


class Logging(unittest.TestCase):
    def test_logs_carry_no_content_of_a_message(self):
        sentinel = "SENTINEL-9f3a"
        msgs = [req("initialize", {"protocolVersion": "2025-06-18", "clientInfo": {"name": "c", "version": "1"}, "extra": sentinel}, id=0), INITIALIZED,
                req("tools/call", {"name": "echo", "arguments": {"x": sentinel}}, id=2), req(sentinel, {"y": sentinel}, id=3)]
        _, log, _, _ = run(*msgs, b"{" + sentinel.encode() + b"\n")
        self.assertNotIn(sentinel, log)

    def test_the_client_that_connected_is_logged_capped_and_visible(self):
        _, log, _, _ = run(req("initialize", {"protocolVersion": "2025-06-18", "clientInfo": {"name": "A\nB", "version": "1"}}))
        self.assertIn("A\\x0aB", log)
        self.assertNotIn("A\nB", log)


if __name__ == "__main__":
    unittest.main()
