"""The HTTP side of the runner: one `ThreadingHTTPServer` on 127.0.0.1, the guards, the routing, the static files.

The runner is a runner, not a service (context section 8): it acts as whoever started it. So every request passes, in this order:

1. `Host` must be exactly `127.0.0.1:<port>`, `localhost:<port>` or `[::1]:<port>` (a name in any case): binding to loopback does not stop
   DNS rebinding, this does. A missing, repeated or other Host is 421. Nothing is said about why.
2. `OPTIONS` (a preflight) is 403: no CORS header is ever sent, so a browser never lets another site call this.
3. For every API call and every state-changing method: `Origin`, when present, must be this server's own origin (`http://<the Host>`) and
   `Sec-Fetch-Site`, when present, `same-origin` or `none`; else 403. A body must be `Content-Type: application/json` (a form post cannot send it).
4. Every API call but `GET /api/health` carries the session token in `X-Reachmap-Token`, compared with `hmac.compare_digest`.
5. A body is at most 1 MB (413 before it is read) and has a length (411); a socket that stalls is cut off (`timeout`); at most
   `max_headers` headers.

Responses: JSON (`application/json; charset=utf-8`, ASCII), `X-Content-Type-Options: nosniff`, `Cache-Control: no-store`, `Referrer-Policy:
no-referrer`; HTML and static files also a Content Security Policy in the header. An error body is a fixed word (`{"error": "..."}`), never the
request, never a traceback. The log has one line per request: method, the path without its query, status, milliseconds; never a header, a body
or a token. Connections are closed after each request (HTTP/1.0): a slow client holds one thread, for at most the socket timeout per read.
"""

from __future__ import annotations

import hmac
import http
import http.client
import json
import os
import re
import secrets
import socket
import stat
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, unquote

from reachmap.report import assets, html
from reachmap.runner import files
from reachmap.runner.api import MAX_BODY, Api, ApiError, Page

__all__ = ["RunnerServer", "report_policy", "style_nonce", "workspace_policy"]

HERE = Path(__file__).resolve().parent
MINIMAL_DIR = HERE / "minimal"
STATIC_DIR = HERE / "static"
MAX_HEADERS = 64
REQUEST_TIMEOUT = 10.0
MAX_STATIC = 32 * 1024 * 1024
MAX_HEAD_BYTES = 64 * 1024  # the header lines of one request, all together
REQUEST_DEADLINE = 15.0  # seconds to receive a whole request, however slowly it is sent
MAX_CONNECTIONS = 32
ONCE_ONLY = ("Host", "Origin", "Sec-Fetch-Site", "Content-Type", "Content-Length", "X-Reachmap-Token")
STATE_CHANGING = frozenset({"POST", "PUT", "DELETE", "PATCH"})
CLIENT_ROUTES = frozenset({"reports", "jobs"})  # the first segment of the routes of the workspace (`frontend/src/routes.tsx`): they answer with its index page
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
    ".ico": "image/x-icon", ".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8", ".map": "application/json; charset=utf-8",
}
_ERRORS = {400: "bad_request", 408: "timeout", 411: "length_required", 413: "too_large", 414: "uri_too_long", 431: "headers_too_large", 501: "not_implemented", 503: "busy", 505: "unsupported_version"}
_SEGMENT_BAD = re.compile(r"[/\\\x00]")
_LENGTH = re.compile(r"^\d{1,12}$")
_METHOD = re.compile(r"^[A-Z]{1,10}$")


def report_policy() -> str:
    """The policy of a report page: the M10 one (the page's inline script by its hash, no `connect-src`: the page makes no request), and that no
    page may frame it (a `frame-ancestors` in a meta tag is ignored, so it is only here)."""
    return f"{html.policy(assets.read('report.js'))}; frame-ancestors 'none'"


def workspace_policy(nonce: str | None = None) -> str:
    """The policy of the workspace and its minimal stand-in: its own files only, calls to this origin only, nothing inline. The index page of
    the packaged workspace is answered with a nonce (`style_nonce`): the UI kit it is built with writes its theme variables into style
    elements at run time, and those carry the nonce; no other inline style is allowed, and there is never `unsafe-inline`."""
    style = "'self'" if nonce is None else f"'self' 'nonce-{nonce}'"
    return (f"default-src 'none'; script-src 'self'; style-src {style}; img-src 'self' data:; font-src 'self'; connect-src 'self'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


NONCE_PLACEHOLDER = b"__REACHMAP_STYLE_NONCE__"  # in `<meta name="reachmap-style-nonce">` of the built index page (`frontend/index.html`)


def style_nonce() -> str:
    """A fresh random value for one response: 18 random bytes, 24 characters of the URL-safe alphabet (which the policy grammar allows)."""
    return secrets.token_urlsafe(18)


class _Reader:
    """The request stream: counts the bytes of the header lines (all together at most `MAX_HEAD_BYTES`, else the same refusal as a line that is
    too long), and passes everything else to the real stream."""

    def __init__(self, stream: Any) -> None:
        self._stream = stream
        self._lines = 0
        self.head = 0
        self.counting = True

    def readline(self, size: int = -1) -> bytes:
        line = self._stream.readline(size)
        self._lines += 1
        if self.counting and self._lines > 1:  # the first line is the request line (its own cap, 414)
            self.head += len(line)
            if self.head > MAX_HEAD_BYTES:
                raise http.client.LineTooLong("header")
        return line

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    timeout = REQUEST_TIMEOUT
    server: "RunnerServer"
    server_version = "reachmap"
    sys_version = ""

    # ---------------------------------------------------------------- the connection

    def setup(self) -> None:
        """One timer for the whole request: a client that sends a byte at a time keeps every read within the socket timeout, and still has
        `request_deadline` seconds, in all, to finish. The timer is stopped when the request has been read."""
        super().setup()
        self.rfile = _Reader(self.rfile)
        self._deadline = threading.Timer(self.server.request_deadline, self._expire)
        self._deadline.daemon = True
        self._deadline.start()

    def _expire(self) -> None:
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _request_read(self) -> None:
        self._deadline.cancel()

    def finish(self) -> None:
        self._deadline.cancel()
        super().finish()

    # ---------------------------------------------------------------- what the base class prints or sends is replaced

    def log_message(self, format: str, *args: Any) -> None:  # the base class would print the request line and the errors it caught
        pass

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        self.close_connection = True
        self._json(code, {"error": _ERRORS.get(code, "error")})

    def parse_request(self) -> bool:
        if not super().parse_request():
            return False
        if len(self.headers) > self.server.max_headers:
            self.send_error(431)
            return False
        self.rfile.counting = False
        return True

    # ---------------------------------------------------------------- replying

    def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self._status = status
        try:
            phrase = http.HTTPStatus(status).phrase
        except ValueError:
            phrase = "Status"
        head = [f"HTTP/1.0 {status} {phrase}", f"Content-Type: {content_type}", f"Content-Length: {len(body)}", "X-Content-Type-Options: nosniff",
                "Cache-Control: no-store", "Referrer-Policy: no-referrer", "Connection: close"]
        head += [f"{k}: {v}" for k, v in (extra or {}).items()]
        head_only = getattr(self, "command", None) == "HEAD"
        self.wfile.write(("\r\n".join(head) + "\r\n\r\n").encode("ascii") + (b"" if head_only else body))
        self.close_connection = True
        self._log()

    def _json(self, status: int, payload: Any) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("ascii"), "application/json; charset=utf-8")

    def _fail(self, status: int, error: str, **more: str) -> None:
        self._json(status, {"error": error, **more})

    def _log(self) -> None:
        if getattr(self, "_logged", False):
            return
        self._logged = True
        method = getattr(self, "command", None)
        method = method if isinstance(method, str) and _METHOD.match(method) else "-"
        path = getattr(self, "path", None)
        shown = "/"
        if isinstance(path, str) and path.startswith("/"):
            shown = quote(path.split("?", 1)[0].split("#", 1)[0], safe="/%._~-")[:200] or "/"
        ms = int((time.monotonic() - getattr(self, "_t0", time.monotonic())) * 1000)
        self.server.log(f"{method} {shown} {getattr(self, '_status', 0)} {ms}ms")

    # ---------------------------------------------------------------- entry points of the methods

    def handle_one_request(self) -> None:
        self._t0 = time.monotonic()
        super().handle_one_request()

    def _dispatch(self) -> None:
        try:
            self._serve()
        except (TimeoutError, ConnectionError, BrokenPipeError):
            self.close_connection = True
            if not getattr(self, "_logged", False):
                self._status = 408
                self._log()
        except Exception:  # never a traceback to the client or the log
            self.close_connection = True
            try:
                self._fail(500, "internal")
            except Exception:
                pass

    do_GET = do_POST = do_DELETE = do_PUT = do_PATCH = do_HEAD = do_OPTIONS = _dispatch

    # ---------------------------------------------------------------- the guards

    def _guards(self, method: str, is_api: bool) -> bool:
        """Host, preflight, cross-origin, token. Replies and returns False when a request is refused."""
        if any(len(self.headers.get_all(name) or []) > 1 for name in ONCE_ONLY):  # a repeated header is read one way by us and another by something else
            self._fail(400, "bad_request")
            return False
        hosts = self.headers.get_all("Host") or []
        if len(hosts) != 1 or hosts[0].strip().lower() not in self.server.allowed_hosts:
            self._fail(421, "refused")
            return False
        host = hosts[0].strip().lower()
        if method == "OPTIONS":
            self._fail(403, "refused")
            return False
        if is_api or method in STATE_CHANGING:
            origins = self.headers.get_all("Origin") or []
            if origins and origins[0].strip().lower() != f"http://{host}":
                self._fail(403, "refused")
                return False
            sites = self.headers.get_all("Sec-Fetch-Site") or []
            if any(s.strip().lower() not in ("same-origin", "none") for s in sites):
                self._fail(403, "refused")
                return False
        if method not in ("GET", "POST", "DELETE"):
            self._fail(405, "method_not_allowed")
            return False
        return True

    def _token_ok(self) -> bool:
        presented = (self.headers.get("X-Reachmap-Token") or "").encode("latin-1", "replace")
        return hmac.compare_digest(presented, self.server.token_bytes)

    def _body(self) -> bytes | None:
        """The request body of a POST: sized, within the cap, JSON when there is one. Replies and returns None when it is refused."""
        if self.headers.get("Transfer-Encoding") is not None:
            self._fail(411, "length_required")
            return None
        raw = self.headers.get("Content-Length")
        if raw is None:
            self._fail(411, "length_required")
            return None
        if not _LENGTH.match(raw.strip()):
            self._fail(400, "bad_request")
            return None
        length = int(raw.strip())
        if length > MAX_BODY:
            self._fail(413, "too_large")
            return None
        if length > 0:
            ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if ctype != "application/json":
                self._fail(415, "unsupported_media_type")
                return None
        try:
            data = self.rfile.read(length) if length else b""
        except TimeoutError:
            self._fail(408, "timeout")
            return None
        if len(data) != length:
            self._fail(400, "bad_request")
            return None
        return data

    # ---------------------------------------------------------------- routing

    def _segments(self) -> list[str] | None:
        """The decoded segments of the path (query dropped), or None when one cannot be a name: a separator, a NUL, a dot segment."""
        raw = self.path.split("?", 1)[0].split("#", 1)[0]
        out = []
        for part in raw[1:].split("/"):
            try:
                seg = unquote(part, encoding="utf-8", errors="strict")
            except UnicodeDecodeError:
                return None
            if _SEGMENT_BAD.search(seg) or seg in (".", ".."):
                return None
            out.append(seg)
        return out

    def _serve(self) -> None:
        method = self.command
        if not self.path.startswith("/"):
            self._fail(400, "bad_request")
            return
        segs = self._segments()
        is_api = self.path.split("?", 1)[0].split("#", 1)[0].split("/", 2)[1:2] == ["api"]
        if not self._guards(method, is_api):
            return
        if not is_api:
            self._request_read()
            self._static(method, segs)
            return
        if not (method == "GET" and segs == ["api", "health"]) and not self._token_ok():
            self._fail(401, "unauthorized")
            return
        body = self._body() if method == "POST" else None
        if method == "POST" and body is None:
            return
        self._request_read()
        try:
            self._route(method, segs, body)
        except ApiError as exc:
            self._json(exc.status, exc.body)

    def _route(self, method: str, segs: list[str] | None, body: bytes | None) -> None:
        api: Api = self.server.api
        if segs is None or segs[0] != "api":
            raise ApiError(404, "not_found")
        route = segs[1:]
        if route == ["health"]:
            return self._only(method, "GET", lambda: self._json(200, api.health()))
        if route == ["repos"]:
            return self._only(method, "GET", lambda: self._json(200, api.repos()))
        if route == ["jobs"]:
            if method == "POST":
                return self._json(202, api.submit(body or b""))
            return self._only(method, "GET", lambda: self._json(200, api.jobs()))
        if len(route) == 2 and route[0] == "jobs":
            if method == "DELETE":
                return self._json(200, api.cancel(route[1]))
            return self._only(method, "GET", lambda: self._json(200, api.job(route[1])))
        if route == ["reports"]:
            return self._only(method, "GET", lambda: self._json(200, api.reports_list()))
        if len(route) == 2 and route[0] == "reports":
            name = route[1]
            if name.endswith(".html"):
                return self._only(method, "GET", lambda: self._page(api.report_page(name[:-5])))
            return self._only(method, "GET", lambda: self._json(200, api.report(name)))
        if len(route) == 3 and route[0] == "reports" and route[2] == "decisions":
            if method != "POST":
                raise ApiError(405, "method_not_allowed")
            status, payload = api.decide(route[1], body or b"")
            return self._json(status, payload)
        raise ApiError(404, "not_found")

    @staticmethod
    def _only(method: str, allowed: str, then: Callable[[], None]) -> None:
        if method != allowed:
            raise ApiError(405, "method_not_allowed")
        then()

    def _page(self, page: Page) -> None:
        self._send(200, page.text.encode("utf-8"), "text/html; charset=utf-8", {"Content-Security-Policy": report_policy()})

    # ---------------------------------------------------------------- static files

    def _static(self, method: str, segs: list[str] | None) -> None:
        if method != "GET":
            self._fail(405, "method_not_allowed")
            return
        found = self.server.static_file(segs)
        if found is None:
            self._fail(404, "not_found")
            return
        data, ctype = found
        nonce = None
        if ctype.startswith("text/html") and NONCE_PLACEHOLDER in data:
            # the index page of the workspace: this response, and only this one, carries a nonce of its own in the page and in the policy
            nonce = style_nonce()
            data = data.replace(NONCE_PLACEHOLDER, nonce.encode("ascii"))
        extra = {"Content-Security-Policy": workspace_policy(nonce)}
        self._send(200, data, ctype, extra)


class RunnerServer(ThreadingHTTPServer):
    """The runner's listening socket. The address is 127.0.0.1 and nothing a caller gives can change it."""

    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64

    def __init__(self, api: Api, token: str, *, port: int = 8787, static_dir: str | Path | None = None, log: Callable[[str], None] | None = None,
                 request_timeout: float = REQUEST_TIMEOUT, max_headers: int = MAX_HEADERS, request_deadline: float = REQUEST_DEADLINE,
                 max_connections: int = MAX_CONNECTIONS) -> None:
        handler = type("RunnerHandler", (_Handler,), {"timeout": request_timeout})
        super().__init__(("127.0.0.1", port), handler)
        self.api = api
        self.runner = api.runner
        self.token = token
        self.token_bytes = token.encode("latin-1")
        self.max_headers = max_headers
        self.request_deadline = request_deadline
        self.max_connections = max_connections
        self._slots = threading.BoundedSemaphore(max_connections)
        self.static_dir = Path(static_dir) if static_dir is not None else STATIC_DIR
        self.log: Callable[[str], None] = log or self.default_log
        p = self.server_address[1]
        self.allowed_hosts = frozenset({f"127.0.0.1:{p}", f"localhost:{p}", f"[::1]:{p}"})

    def process_request(self, request: Any, client_address: Any) -> None:
        """At most `max_connections` at once: another is told so (503) and closed, without a thread."""
        if not self._slots.acquire(blocking=False):
            body = b'{"error":"busy"}'
            try:
                request.settimeout(1.0)
                request.sendall(b"HTTP/1.0 503 Service Unavailable\r\nContent-Type: application/json; charset=utf-8\r\nContent-Length: %d\r\n"
                                b"X-Content-Type-Options: nosniff\r\nCache-Control: no-store\r\nReferrer-Policy: no-referrer\r\nConnection: close\r\n\r\n" % len(body) + body)
            except OSError:
                pass
            try:
                request.settimeout(0.2)
                request.recv(65536)  # what the client already sent: closing with it unread would reset the connection and lose the answer
            except OSError:
                pass
            self.log("- / 503 0ms")
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    @staticmethod
    def default_log(line: str) -> None:
        print(line, file=sys.stderr, flush=True)

    def handle_error(self, request: Any, client_address: Any) -> None:  # the base class prints a traceback
        pass

    # ---------------------------------------------------------------- static

    def _root(self) -> Path:
        index = self.static_dir / "index.html"
        try:
            if stat.S_ISREG(os.lstat(index).st_mode):
                return self.static_dir
        except OSError:
            pass
        return MINIMAL_DIR

    def static_file(self, segs: list[str] | None) -> tuple[bytes, str] | None:
        """A file under the static root, or None. Segments are plain names (no dot segment, no leading dot, no separator, none empty) that the
        folder has exactly as written, no step of the way is a link, and the file is a regular file with one link (`files.read_regular`)
        whose resolved path is under the resolved root."""
        if segs is None:
            return None
        parts = segs if segs != [""] else ["index.html"]
        if not parts or any(not p or p.startswith(".") for p in parts):
            return None
        root = self._root()
        found = self._file(root, parts)
        if found is None and root == self.static_dir and parts[0] in CLIENT_ROUTES:
            # a route of the workspace (a reload, a bookmark, a link): the page itself answers, and its router shows what is at that address
            return self._file(root, ["index.html"])
        return found

    def _file(self, root: Path, parts: list[str]) -> tuple[bytes, str] | None:
        cur = root
        try:
            for part in parts:
                if not files.listed(cur, part):
                    return None
                cur = cur / part
                if stat.S_ISLNK(os.lstat(cur).st_mode):
                    return None
            ctype = CONTENT_TYPES.get(cur.suffix.lower())
            if ctype is None:
                return None
            real_root, real = os.path.realpath(root), os.path.realpath(cur)
            if os.path.commonpath([real_root, real]) != real_root:
                return None
            data = files.read_regular(cur, MAX_STATIC)
            return None if data is None else (data, ctype)
        except (OSError, ValueError):
            return None
