"""M12 guards 1 to 4 and 9 to 11: the loopback bind, the Host check, cross-origin refusal, the token, the response headers,
the size and time caps, the log. Each against a real server on 127.0.0.1:0 and `http.client`."""

from __future__ import annotations

import hmac
import inspect
import os
import re
import socket
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from reachmap import runner
from reachmap.report import assets, html
from reachmap.runner import server
from tests.support_runner import Live

REFUSED = {"error": "refused"}


class Bind(unittest.TestCase):
    def test_binds_ipv4_loopback_only(self) -> None:
        with Live() as live:
            host, port = live.server.server_address
            self.assertEqual(host, "127.0.0.1")
            self.assertEqual(live.server.socket.getsockname()[0], "127.0.0.1")
            self.assertEqual(live.server.socket.family, socket.AF_INET)
            self.assertGreater(port, 0)
            self.assertNotIn(host, ("0.0.0.0", "::", ""))

    def test_nothing_listens_on_ipv6_loopback(self) -> None:
        with Live() as live:
            with self.assertRaises(OSError):
                socket.create_connection(("::1", live.port), timeout=2).close()

    def test_there_is_no_way_to_choose_the_host(self) -> None:
        params = inspect.signature(runner.create).parameters
        self.assertNotIn("host", params)
        self.assertNotIn("address", params)

    def test_the_command_has_no_host_option(self) -> None:
        import io

        from reachmap import cli

        err = io.StringIO()
        self.assertEqual(cli.main(["serve", "--host", "0.0.0.0"], stdout=io.StringIO(), stderr=err), 2)
        self.assertIn("--host", err.getvalue())


class HostHeader(unittest.TestCase):
    def test_good_hosts_are_served(self) -> None:
        with Live() as live:
            for host in (f"127.0.0.1:{live.port}", f"localhost:{live.port}", f"LocalHost:{live.port}", f"[::1]:{live.port}"):
                for path in ("/api/health", "/"):
                    with self.subTest(host=host, path=path):
                        self.assertEqual(live.get(path, host=host).status, 200)

    def test_every_other_host_is_refused_without_detail(self) -> None:
        with Live() as live:
            p = live.port
            bad = [
                "evil.example", "evil.example:%d" % p, "127.0.0.1.evil.example:%d" % p, "localhost.evil.example:%d" % p,
                "127.0.0.1:%d" % (p + 1), "127.0.0.1:1", "127.0.0.1", "localhost", "[::1]", "[::1]:%d" % (p + 1), "127.0.0.2:%d" % p,
                "0.0.0.0:%d" % p, "127.0.0.1:%d@evil.example" % p, "evil.example@127.0.0.1:%d" % p, "127.0.0.1:%dx" % p,
                "127.0.0.1:0%d" % p, "127.0.0.1:+%d" % p, " ", "localhost:", "[::1]:%d:%d" % (p, p), "127.0.0.1:%d, evil.example" % p,
                "1.2.3.4:%d" % p, "LOCALHOST.:%d" % p, "127.1:%d" % p,
            ]
            for host in bad:
                for path in ("/api/health", "/", "/api/repos"):
                    with self.subTest(host=host, path=path):
                        r = live.get(path, host=host)
                        self.assertIn(r.status, (421, 403))
                        self.assertEqual(r.json(), REFUSED)
                        self.assertNotIn("evil", r.body.decode())

    def test_missing_host_is_refused(self) -> None:
        with Live() as live:
            r = live.get("/api/health", host=None)
            self.assertIn(r.status, (421, 403))
            self.assertEqual(r.json(), REFUSED)

    def test_two_host_headers_are_refused(self) -> None:
        with Live() as live:
            r = live.get("/api/health", host=live.host, headers={"Host": "evil.example"})
            self.assertEqual(r.status, 400)

    def test_the_host_is_checked_before_the_token_and_for_every_method(self) -> None:
        with Live() as live:
            for method in ("GET", "POST", "DELETE", "OPTIONS", "PUT"):
                r = live.request(method, "/api/jobs", host="evil.example", token=False)
                self.assertEqual(r.status, 421, method)


class CrossOrigin(unittest.TestCase):
    def own(self, live: Live) -> str:
        return f"http://127.0.0.1:{live.port}"

    def test_state_changing_requests_need_the_runners_own_origin(self) -> None:
        with Live() as live:
            bad = ["http://evil.example", "null", f"http://127.0.0.1:{live.port + 1}", f"https://127.0.0.1:{live.port}",
                   f"http://localhost:{live.port}", f"http://127.0.0.1:{live.port}.evil.example", "http://127.0.0.1", "", f"http://127.0.0.1:{live.port}/"]
            for method in ("POST", "DELETE", "PUT"):
                for origin in bad:
                    with self.subTest(method=method, origin=origin):
                        r = live.request(method, "/api/jobs/nothing", headers={"Origin": origin}, json_body={"a": 1})
                        self.assertEqual(r.status, 403)
                        self.assertEqual(r.json(), REFUSED)

    def test_the_runners_own_origin_passes(self) -> None:
        with Live() as live:
            for host in (f"127.0.0.1:{live.port}", f"localhost:{live.port}", f"[::1]:{live.port}"):
                r = live.request("DELETE", "/api/jobs/nothing", host=host, headers={"Origin": f"http://{host}"})
                self.assertEqual(r.status, 404, host)  # past every guard: there is no such job
            self.assertEqual(live.request("DELETE", "/api/jobs/nothing").status, 404)  # no Origin at all (a command line client)

    def test_sec_fetch_site(self) -> None:
        with Live() as live:
            for value, ok in (("same-origin", True), ("none", True), ("cross-site", False), ("same-site", False), ("", False), ("junk", False)):
                with self.subTest(value=value):
                    r = live.request("DELETE", "/api/jobs/nothing", headers={"Sec-Fetch-Site": value})
                    self.assertEqual(r.status, 404 if ok else 403)

    def test_a_form_content_type_is_refused(self) -> None:
        with Live() as live:
            for ctype in ("application/x-www-form-urlencoded", "text/plain", "multipart/form-data; boundary=x", "text/json", "application/jsonx", "",
                          "application/json-seq"):
                with self.subTest(ctype=ctype):
                    headers = {"Content-Type": ctype}
                    r = live.request("POST", "/api/jobs", headers=headers, body=b'{"repo": "acme/web", "compare": "main...topic"}')
                    self.assertEqual(r.status, 415)
                    self.assertEqual(r.json(), {"error": "unsupported_media_type"})
            self.assertEqual(live.get("/api/jobs").json(), {"jobs": []})

    def test_json_content_type_forms_are_accepted(self) -> None:
        with Live(entry="recorder") as live:
            for ctype in ("application/json", "application/json; charset=utf-8", "Application/JSON"):
                with self.subTest(ctype=ctype):
                    body = b'{"repo": "acme/web", "compare": "main...topic"}'
                    self.assertEqual(live.request("POST", "/api/jobs", headers={"Content-Type": ctype}, body=body).status, 202)

    def test_report_data_gets_refuse_a_foreign_origin_or_site(self) -> None:
        with Live() as live:
            for path in ("/api/reports", "/api/jobs", "/api/repos", "/api/reports/x", "/api/reports/x.html", "/api/health"):
                for headers in ({"Origin": "http://evil.example"}, {"Sec-Fetch-Site": "cross-site"}, {"Origin": "null"}, {"Sec-Fetch-Site": "same-site"}):
                    with self.subTest(path=path, headers=headers):
                        self.assertEqual(live.get(path, headers=headers).status, 403)
            self.assertEqual(live.get("/api/repos", headers={"Origin": f"http://127.0.0.1:{live.port}", "Sec-Fetch-Site": "same-origin"}).status, 200)

    def test_preflight_is_refused_and_no_cors_header_is_ever_sent(self) -> None:
        with Live() as live:
            pre = {"Origin": "http://evil.example", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "x-reachmap-token"}
            for origin_headers in (pre, {}, {"Origin": f"http://127.0.0.1:{live.port}"}):
                r = live.request("OPTIONS", "/api/jobs", headers=origin_headers)
                self.assertEqual(r.status, 403)
            for r in (live.get("/api/health"), live.get("/api/health", headers={"Origin": "http://evil.example"}), live.get("/"), live.get("/api/repos"),
                      live.get("/api/repos", token=False), live.request("OPTIONS", "/api/jobs", headers=pre), live.get("/nothing-here")):
                self.assertEqual([k for k in r.headers if k.startswith("access-control-")], [])


class Token(unittest.TestCase):
    def test_every_api_call_but_health_needs_the_token(self) -> None:
        with Live() as live:
            calls = [("GET", "/api/repos"), ("GET", "/api/jobs"), ("GET", "/api/jobs/x"), ("GET", "/api/reports"), ("GET", "/api/reports/x"),
                     ("GET", "/api/reports/x.html"), ("POST", "/api/jobs"), ("DELETE", "/api/jobs/x"), ("POST", "/api/reports/x/decisions"),
                     ("GET", "/api/nothing")]
            for method, path in calls:
                for token in (False, "", "wrong", live.token + "x", live.token[:-1], live.token.upper(), "\u00e9" * 43):
                    with self.subTest(call=(method, path), token=token):
                        r = live.request(method, path, token=token, json_body={} if method == "POST" else None)
                        self.assertEqual(r.status, 401)
                        self.assertEqual(r.json(), {"error": "unauthorized"})
            self.assertEqual(live.get("/api/health", token=False).json(), {"ok": True})
            self.assertEqual(live.get("/api/health").json(), {"ok": True})
            self.assertEqual(live.get("/api/repos").status, 200)

    def test_the_static_page_needs_no_token(self) -> None:
        with Live() as live:
            self.assertEqual(live.get("/", token=False).status, 200)

    def test_the_token_is_a_session_secret(self) -> None:
        with Live() as a, Live() as b:
            self.assertNotEqual(a.token, b.token)
            self.assertGreaterEqual(len(a.token), 43)
            self.assertRegex(a.token, r"^[A-Za-z0-9_-]+$")
            self.assertEqual(b.get("/api/repos", token=a.token).status, 401)

    def test_the_comparison_is_constant_time(self) -> None:
        with Live() as live:
            real = hmac.compare_digest
            with mock.patch("reachmap.runner.server.hmac.compare_digest", wraps=real) as spy:
                live.get("/api/repos")
                live.get("/api/repos", token="not-the-token")
                live.get("/api/repos", token=False)
            self.assertGreaterEqual(spy.call_count, 3)
            for call in spy.call_args_list:  # bytes: compare_digest takes ASCII str only, and a header may be anything
                self.assertTrue(all(isinstance(a, bytes) for a in call.args))
            self.assertIn(live.token.encode(), [a for call in spy.call_args_list for a in call.args])

    def test_the_token_is_not_in_any_response(self) -> None:
        with Live() as live:
            for r in (live.get("/api/repos"), live.get("/api/health"), live.get("/"), live.get("/api/repos", token="x"), live.get("/api/jobs"),
                      live.get("/nothing"), live.get("/api/reports")):
                self.assertNotIn(live.token.encode(), r.body)
                self.assertNotIn(live.token, " ".join(r.headers.values()))


class Responses(unittest.TestCase):
    COMMON = {"x-content-type-options": "nosniff", "cache-control": "no-store", "referrer-policy": "no-referrer"}

    def check_common(self, r, label: str) -> None:
        for name, value in self.COMMON.items():
            self.assertEqual(r.headers.get(name), value, f"{label}: {name}")

    def test_json_responses(self) -> None:
        with Live() as live:
            cases = {
                "health": live.get("/api/health"), "repos": live.get("/api/repos"), "jobs": live.get("/api/jobs"), "401": live.get("/api/repos", token=False),
                "404": live.get("/api/nothing"), "421": live.get("/api/health", host="evil.example"), "403": live.request("OPTIONS", "/api/jobs"),
                "405": live.request("PATCH", "/api/jobs"), "400": live.post("/api/jobs", {"x": 1}), "415": live.request("POST", "/api/jobs", body=b"{}", content_type="text/plain"),
            }
            for label, r in cases.items():
                with self.subTest(label):
                    self.check_common(r, label)
                    self.assertEqual(r.headers["content-type"], "application/json; charset=utf-8")
                    self.assertIsInstance(r.json(), dict)

    def test_html_gets_a_strict_content_security_policy(self) -> None:
        with Live() as live:
            r = live.get("/")
            self.check_common(r, "page")
            self.assertTrue(r.headers["content-type"].startswith("text/html"))
            csp = r.headers["content-security-policy"]
            self.assertIn("default-src 'none'", csp)
            self.assertIn("connect-src 'self'", csp)
            self.assertIn("base-uri 'none'", csp)
            self.assertIn("form-action 'none'", csp)
            self.assertNotIn("unsafe-eval", csp)
            self.assertNotRegex(csp, r"(?<![-\w])\*")
            for part in re.findall(r"script-src ([^;]*)", csp):
                self.assertNotIn("unsafe-inline", part)

    def test_the_report_page_policy_is_the_reports_own(self) -> None:
        # the header the runner puts on a report page is the M10 policy for the page's inline script (the page makes no request)
        self.assertTrue(server.report_policy().startswith(html.policy(assets.read("report.js"))))
        self.assertNotIn("connect-src", server.report_policy())
        self.assertIn("connect-src 'self'", server.workspace_policy())
        base = server.workspace_policy().replace("connect-src 'self'; ", "")
        self.assertIn("default-src 'none'", base)

    def test_error_bodies_never_echo_the_request(self) -> None:
        with Live() as live:
            mark = "MARK-Zq81"
            cases = [
                live.get(f"/api/{mark}"), live.get(f"/{mark}.js"), live.get(f"/api/jobs/{mark}"), live.get(f"/api/reports/{mark}"),
                live.get(f"/api/reports/{mark}.html"), live.post(f"/api/reports/{mark}/decisions", {"target": mark}),
                live.post("/api/jobs", {"repo": mark}), live.post("/api/jobs", {mark: mark}), live.request("POST", "/api/jobs", body=("{" + mark).encode()),
                live.request("POST", "/api/jobs", body=b"{}", content_type=mark), live.get("/api/health", host=mark), live.request(mark.upper(), "/api/jobs"),
                live.get("/api/repos", headers={"Origin": "http://" + mark}), live.get("/api/repos", token=mark), live.delete(f"/api/jobs/{mark}"),
                live.get(f"/api/jobs?x={mark}"), live.get("/api/repos", headers={mark: mark}),
            ]
            for r in cases:
                self.assertNotIn(mark.lower().encode(), r.body.lower())
                self.assertNotIn(b"Traceback", r.body)
                self.assertNotIn(b"File \"", r.body)
                self.assertIsInstance(r.json(), dict)

    def test_malformed_requests_get_a_json_body_that_repeats_nothing(self) -> None:
        with Live() as live:
            for raw in (b"GARBAGE-MARK\r\n\r\n", b"GET /api/health HTTP/9.9 MARK\r\nHost: x\r\n\r\n", b"GET http://evil.example/api/health HTTP/1.0\r\nHost: x\r\n\r\n",
                        b"GET /" + b"a" * 70000 + b" HTTP/1.0\r\n\r\n"):
                with socket.create_connection(("127.0.0.1", live.port), timeout=5) as s:
                    s.sendall(raw)
                    data = b""
                    while True:
                        chunk = s.recv(65536)
                        if not chunk:
                            break
                        data += chunk
                self.assertNotIn(b"MARK", data)
                self.assertNotIn(b"evil.example", data)
                self.assertNotIn(b"<html", data.lower())
                head, _, body = data.partition(b"\r\n\r\n")
                self.assertIn(b"application/json", head)
                self.assertTrue(body.startswith(b"{"))


class Caps(unittest.TestCase):
    def test_a_body_over_one_megabyte_is_refused_unread(self) -> None:
        with Live() as live:
            r = live.request("POST", "/api/jobs", headers={"Content-Length": str(1024 * 1024 + 1)}, body=b"")
            self.assertEqual(r.status, 413)
            self.assertEqual(r.json(), {"error": "too_large"})
            r = live.request("POST", "/api/jobs", headers={"Content-Length": "99999999999"}, body=b"")
            self.assertEqual(r.status, 413)

    def test_a_body_of_exactly_the_cap_is_read(self) -> None:
        with Live() as live:
            body = b'{"repo": "' + b"a" * (1024 * 1024 - 12) + b'"}'
            self.assertEqual(len(body), 1024 * 1024)
            r = live.request("POST", "/api/jobs", body=body)
            self.assertEqual(r.status, 400)  # read, and refused as a job (the repository is not one)

    def test_a_body_without_a_length_is_refused(self) -> None:
        with Live() as live:
            with socket.create_connection(("127.0.0.1", live.port), timeout=5) as s:
                s.sendall(f"POST /api/jobs HTTP/1.1\r\nHost: {live.host}\r\nX-Reachmap-Token: {live.token}\r\nContent-Type: application/json\r\n"
                          "Transfer-Encoding: chunked\r\n\r\n2\r\n{}\r\n0\r\n\r\n".encode())
                data = s.recv(65536)
            self.assertTrue(data.startswith(b"HTTP/1.0 411") or data.startswith(b"HTTP/1.1 411"), data[:40])
            for bad in ("-1", "abc", "1.5", "+3", " "):
                r = live.request("POST", "/api/jobs", headers={"Content-Length": bad}, body=b"")
                self.assertIn(r.status, (400, 411))

    def test_slow_headers_are_cut_off(self) -> None:
        with Live(request_timeout=0.4) as live:
            started = time.monotonic()
            with socket.create_connection(("127.0.0.1", live.port), timeout=10) as s:
                s.sendall(b"GET /api/he")
                s.settimeout(5)
                data = s.recv(1024)  # the server gives up and closes: nothing, or an error
            self.assertLess(time.monotonic() - started, 4)
            self.assertNotIn(b"200", data)
            self.assertEqual(live.get("/api/health").status, 200)  # one stalled client does not stop the server

    def test_a_stalled_body_is_cut_off(self) -> None:
        with Live(request_timeout=0.4) as live:
            started = time.monotonic()
            with socket.create_connection(("127.0.0.1", live.port), timeout=10) as s:
                s.sendall(f"POST /api/jobs HTTP/1.1\r\nHost: {live.host}\r\nX-Reachmap-Token: {live.token}\r\nContent-Type: application/json\r\n"
                          "Content-Length: 100\r\n\r\n{".encode())
                s.settimeout(5)
                data = s.recv(1024)
            self.assertLess(time.monotonic() - started, 4)
            self.assertNotIn(b"202", data)
            self.assertEqual(live.get("/api/jobs").json(), {"jobs": []})

    def test_the_default_socket_timeout_is_set(self) -> None:
        with Live() as live:
            self.assertGreater(live.server.RequestHandlerClass.timeout, 0)
            self.assertLessEqual(live.server.RequestHandlerClass.timeout, 30)

    def test_too_many_headers_are_refused(self) -> None:
        with Live(max_headers=20) as live:
            few = {f"X-H{i}": "v" for i in range(5)}
            many = {f"X-H{i}": "v" for i in range(40)}
            self.assertEqual(live.get("/api/health", headers=few).status, 200)
            r = live.get("/api/health", headers=many)
            self.assertEqual(r.status, 431)
            self.assertEqual(r.json(), {"error": "headers_too_large"})
        with Live() as live:  # the default cap is below what the standard library would allow (100)
            self.assertEqual(live.get("/api/health", headers={f"X-H{i}": "v" for i in range(150)}).status, 431)


class Boundaries(unittest.TestCase):
    """The review's additions: a total deadline per request, a cap on connections, a cap on header bytes, repeated headers, HEAD, frame-ancestors."""

    def test_a_trickling_client_is_cut_off_by_the_total_deadline(self) -> None:
        with Live(request_timeout=5.0, request_deadline=0.8) as live:
            started = time.monotonic()
            closed = False
            with socket.create_connection(("127.0.0.1", live.port), timeout=10) as s:
                s.settimeout(0.2)
                for byte in b"GET /api/health HTTP/1.0\r\nX-Slow: " + b"a" * 200:
                    try:
                        s.sendall(bytes([byte]))
                        time.sleep(0.2)  # every wait is far below the socket timeout, the whole is not
                        s.recv(1)
                    except socket.timeout:
                        continue
                    except OSError:
                        closed = True
                        break
                    else:
                        closed = True  # an answer or EOF
                        break
            self.assertTrue(closed)
            self.assertLess(time.monotonic() - started, 4)
            self.assertEqual(live.get("/api/health").status, 200)

    def test_the_default_deadline_is_fifteen_seconds(self) -> None:
        with Live() as live:
            self.assertEqual(live.server.request_deadline, 15.0)
            self.assertEqual(live.server.max_connections, 32)
            self.assertEqual(live.server.request_queue_size, 64)

    def test_connections_beyond_the_cap_get_503_and_are_closed(self) -> None:
        with Live(max_connections=3, request_timeout=20.0, request_deadline=30.0) as live:
            held = []
            try:
                for _ in range(3):
                    s = socket.create_connection(("127.0.0.1", live.port), timeout=5)
                    s.sendall(b"GET /api/he")  # stalled: holds a slot
                    held.append(s)
                time.sleep(0.3)
                with socket.create_connection(("127.0.0.1", live.port), timeout=5) as extra:
                    extra.sendall(f"GET /api/health HTTP/1.0\r\nHost: {live.host}\r\n\r\n".encode())
                    data = b""
                    while chunk := extra.recv(4096):
                        data += chunk
                self.assertTrue(data.startswith(b"HTTP/1.0 503"), data[:30])
                self.assertIn(b'{"error":"busy"}', data)
            finally:
                for s in held:
                    s.close()
            time.sleep(0.4)
            self.assertEqual(live.get("/api/health").status, 200)  # the slots are free again

    def test_more_than_64_kilobytes_of_headers_are_refused(self) -> None:
        with Live() as live:
            small = {f"X-H{i}": "v" * 100 for i in range(10)}
            self.assertEqual(live.get("/api/health", headers=small).status, 200)
            big = {f"X-H{i}": "v" * 4000 for i in range(20)}  # 80 KB in 20 headers
            r = live.get("/api/health", headers=big)
            self.assertEqual(r.status, 431)
            self.assertEqual(r.json(), {"error": "headers_too_large"})

    def test_a_header_that_may_be_given_once_is_refused_when_repeated(self) -> None:
        with Live() as live:
            own = f"http://127.0.0.1:{live.port}"
            for name, first, second in (("Origin", own, own), ("Sec-Fetch-Site", "same-origin", "same-origin"), ("X-Reachmap-Token", live.token, live.token)):
                with self.subTest(name=name):
                    with socket.create_connection(("127.0.0.1", live.port), timeout=5) as s:
                        lines = [f"GET /api/repos HTTP/1.0", f"Host: {live.host}", f"X-Reachmap-Token: {live.token}" if name != "X-Reachmap-Token" else f"X-Reachmap-Token: {first}",
                                 f"{name}: {first}" if name != "X-Reachmap-Token" else f"{name}: {second}"]
                        if name != "X-Reachmap-Token":
                            lines.append(f"{name}: {second}")
                        s.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
                        data = b""
                        while chunk := s.recv(4096):
                            data += chunk
                    self.assertTrue(data.startswith(b"HTTP/1.0 400"), (name, data[:30]))
            for name in ("Content-Type", "Content-Length", "Host"):
                with self.subTest(name=name):
                    with socket.create_connection(("127.0.0.1", live.port), timeout=5) as s:
                        body = b'{"repo": "acme/web", "compare": "a...b"}'
                        lines = ["POST /api/jobs HTTP/1.0", f"Host: {live.host}", f"X-Reachmap-Token: {live.token}", "Content-Type: application/json", f"Content-Length: {len(body)}"]
                        lines.append({"Content-Type": "Content-Type: application/json", "Content-Length": f"Content-Length: {len(body)}", "Host": f"Host: {live.host}"}[name])
                        s.sendall(("\r\n".join(lines) + "\r\n\r\n").encode() + body)
                        data = b""
                        while chunk := s.recv(4096):
                            data += chunk
                    self.assertTrue(data.startswith(b"HTTP/1.0 400"), (name, data[:30]))
            self.assertEqual(live.get("/api/jobs").json(), {"jobs": []})

    def test_head_is_405_with_no_body(self) -> None:
        with Live() as live:
            for path in ("/api/health", "/", "/api/repos", "/nothing"):
                r = live.request("HEAD", path)
                self.assertEqual(r.status, 405, path)
                self.assertEqual(r.body, b"")

    def test_the_token_is_never_taken_from_the_query(self) -> None:
        with Live() as live:
            for query in (f"token={live.token}", f"access_token={live.token}", f"x-reachmap-token={live.token}", f"X-Reachmap-Token={live.token}"):
                r = live.get(f"/api/repos?{query}", token=False)
                self.assertEqual(r.status, 401, query)
            r = live.request("POST", f"/api/jobs?token={live.token}", token=False, json_body={"repo": "acme/web", "compare": "a...b"})
            self.assertEqual(r.status, 401)

    def test_every_html_response_forbids_framing(self) -> None:
        self.assertIn("frame-ancestors 'none'", server.report_policy())
        self.assertIn("frame-ancestors 'none'", server.workspace_policy())
        self.assertTrue(server.report_policy().startswith(html.policy(assets.read("report.js"))))  # the page's own policy, unchanged, then the ancestors
        with Live() as live:
            self.assertIn("frame-ancestors 'none'", live.get("/").headers["content-security-policy"])

    def test_the_common_headers_are_on_static_files_too(self) -> None:
        with Live() as live:
            for path in ("/", "/minimal.js", "/minimal.css", "/index.html"):
                r = live.get(path, token=False)
                self.assertEqual(r.status, 200, path)
                for name, value in Responses.COMMON.items():
                    self.assertEqual(r.headers.get(name), value, f"{path}: {name}")
                self.assertIn("content-security-policy", r.headers)


class Logging(unittest.TestCase):
    LINE = re.compile(r"^(GET|POST|DELETE|PUT|OPTIONS|PATCH|HEAD|[A-Z]+|-) /[A-Za-z0-9_./%~-]* \d{3} \d+ms$")

    def test_a_line_is_method_path_status_duration_and_nothing_else(self) -> None:
        with Live(entry="recorder") as live:
            secret_body = "BODY-SECRET-77"
            live.get("/api/repos")
            live.get("/api/health", host="host-secret.example")
            live.get("/api/repos?token=" + live.token + "&q=QUERY-SECRET", headers={"Origin": "http://origin-secret.example"})
            live.post("/api/jobs", {"repo": secret_body})
            live.get("/api/repos", token="TOKEN-SECRET-99")
            live.get("/api/nothing-%41-here?x=1")
            live.request("PATCH", "/api/jobs")
            with socket.create_connection(("127.0.0.1", live.port), timeout=5) as s:
                s.sendall(b"GARBAGE-LINE-SECRET\r\n\r\n")
                s.recv(4096)
            time.sleep(0.2)
            text = "\n".join(live.logs)
            self.assertGreaterEqual(len(live.logs), 8)
            for secret in (live.token, secret_body, "QUERY-SECRET", "host-secret", "origin-secret", "TOKEN-SECRET", "GARBAGE-LINE-SECRET", "x-reachmap", "Content-Type", "token="):
                self.assertNotIn(secret.lower(), text.lower())
            for line in live.logs:
                self.assertRegex(line, self.LINE)
            self.assertNotIn("?", text)
            self.assertTrue(any(line.startswith("GET /api/repos 200 ") for line in live.logs))
            self.assertTrue(any(line.startswith("GET /api/repos 401 ") for line in live.logs))

    def test_a_path_with_control_characters_is_logged_encoded(self) -> None:
        with Live() as live:
            live.get("/api/x%0d%0aFORGED%20200%200ms")
            with socket.create_connection(("127.0.0.1", live.port), timeout=5) as s:
                s.sendall(b"GET /api/\x7f\x01y HTTP/1.0\r\nHost: " + live.host.encode() + b"\r\n\r\n")
                s.recv(4096)
            time.sleep(0.2)
            self.assertGreaterEqual(len(live.logs), 2)
            for line in live.logs:
                self.assertEqual(len(line.splitlines()), 1)
                self.assertRegex(line, self.LINE)

    def test_the_default_log_goes_to_stderr_in_the_same_form(self) -> None:
        import io
        from contextlib import redirect_stderr

        err = io.StringIO()
        with redirect_stderr(err):
            with Live() as live:
                live.server.log = live.server.default_log  # the sink a person gets
                live.get("/api/repos", token="SECRET-T")
                live.get("/api/health")
                time.sleep(0.1)
        lines = err.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertNotIn("SECRET-T", err.getvalue())
        for line in lines:
            self.assertRegex(line.split("] ", 1)[-1] if line.startswith("[") else line, self.LINE)

    def test_no_traceback_or_header_reaches_stderr_in_a_failed_request(self) -> None:
        import io
        from contextlib import redirect_stderr

        err = io.StringIO()
        with redirect_stderr(err):
            with Live() as live:
                live.server.log = live.server.default_log
                with socket.create_connection(("127.0.0.1", live.port), timeout=5) as s:
                    s.sendall(b"GET /\xff\xfe HTTP/1.0\r\nHost: x\r\nX-Reachmap-Token: SECRET-T2\r\n\r\n")
                    s.recv(4096)
                time.sleep(0.1)
        self.assertNotIn("SECRET-T2", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())


if __name__ == "__main__":
    unittest.main()
