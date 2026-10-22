"""The host layer: only what the host alone knows (context 7.3 and 13.5).

A change request's title, description, state, base and head, merged commit and file
count, and the head of a branch by name. Everything else (diffs, file reads, search,
heads, merge bases) comes from git through `reachmap.gitlayer`, identical for every host.

Every host reader answers with the model's `ReadResult`: found, absent (a 404 detected
explicitly), unreadable (rate limit, 5xx, authentication, timeout, malformed or
unexpected JSON, a transport that failed). An unreadable answer is recorded as a blocking
`read_unreadable` incompleteness in the target given to the host; it is never "no files"
and never "no such change request".

A change request comes back as the JSON text of a `ChangeRequest` inside the ReadResult
(`ChangeRequest.from_read` turns it back into the value).

Transports: each host sends its requests through a `transport` callable
`transport(method, url, headers=None) -> (status, headers, body_bytes)`. The default one
runs `gh` or `glab` (`CliTransport`) or `urllib` (`UrllibTransport`) and is only built when
a request is actually sent; tests pass a fake that serves recorded responses. A token is
read from the environment variable named in the host's options at call time, sent in an
`Authorization` header only, never put in a URL, an argv, a log or a message.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.parse import quote, urlsplit

from reachmap import redact
from reachmap.errors import ModelError
from reachmap.incomplete import record
from reachmap.model import Read, ReadResult

__all__ = [
    "ChangeRequest", "CliTransport", "Host", "HostBase", "HostUnsupported", "Transport", "TransportError",
    "ResponseUnusable", "UrllibTransport", "get_host", "hosts", "parse_number", "register_host", "split_repo",
]

_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
STATES = ("open", "merged", "closed")
MAX_PAGES = 200  # a paginated list longer than this is reported as unreadable, not cut silently
MAX_BODY = 64 * 1024 * 1024


class HostUnsupported(ModelError):
    """The host cannot answer this (the host-less `git` mode has no change requests)."""


FORBID_DEFAULT_TRANSPORT_ENV = "REACHMAP_FORBID_DEFAULT_TRANSPORT"


class DefaultTransportForbidden(AssertionError):
    """A default transport was asked for while the test suite's guard is on."""


class TransportError(Exception):
    """The transport could not get an answer at all (a CLI that is missing or not logged in)."""


class _Bad(Exception):
    """Internal: the response is unusable. The message becomes the unreadable detail."""


ResponseUnusable = _Bad  # the name other readers of a web API (the test sources) catch: the message says why, with no credential in it


# ---------------------------------------------------------------------------- values


@dataclass(frozen=True, slots=True)
class ChangeRequest:
    """What a host knows about one change request (a pull or merge request).

    `base_sha` and `head_sha` are the base branch tip and the head as the host reports them.
    `merge_sha` is the commit the change request became on the base branch, only when it is
    merged (an open request has none, whatever the host's test merge says). `file_count` is
    the host's own count of changed files, paginated in full when the host pages it.
    `commit_count` is the number of commits in the request (for a merged request, what a
    rebase merge puts on the base branch), None when it was not read."""

    number: int
    title: str
    body: str
    state: str  # open | merged | closed
    base_ref: str
    head_ref: str
    base_sha: str
    head_sha: str
    merge_sha: str | None
    file_count: int
    url: str
    commit_count: int | None = None  # commits in the request, when the host was asked (merged requests)

    def __post_init__(self) -> None:
        if isinstance(self.number, bool) or not isinstance(self.number, int) or self.number < 1:
            raise ModelError(f"ChangeRequest.number must be a positive integer, got {self.number!r}")
        if self.state not in STATES:
            raise ModelError(f"ChangeRequest.state must be one of {list(STATES)}, got {self.state!r}")
        for name in ("title", "body", "base_ref", "head_ref", "url"):
            if not isinstance(getattr(self, name), str):
                raise ModelError(f"ChangeRequest.{name} must be a string")
        for name in ("base_sha", "head_sha"):
            if not isinstance(getattr(self, name), str) or not _SHA.match(getattr(self, name)):
                raise ModelError(f"ChangeRequest.{name} must be a full commit sha")
        if self.merge_sha is not None and not (isinstance(self.merge_sha, str) and _SHA.match(self.merge_sha)):
            raise ModelError("ChangeRequest.merge_sha must be a full commit sha or None")
        if self.merge_sha is not None and self.state != "merged":
            raise ModelError("only a merged change request has a merge_sha")
        if isinstance(self.file_count, bool) or not isinstance(self.file_count, int) or self.file_count < 0:
            raise ModelError("ChangeRequest.file_count must be a non-negative integer")
        if self.commit_count is not None and (isinstance(self.commit_count, bool) or not isinstance(self.commit_count, int) or self.commit_count < 1):
            raise ModelError("ChangeRequest.commit_count must be a positive integer or None")

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__slots__}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str) -> "ChangeRequest":
        return cls(**json.loads(text))

    @classmethod
    def from_read(cls, read: ReadResult) -> "ChangeRequest":
        """The value inside a found read. Raises like `ReadResult.text` for absent and
        unreadable reads."""
        return cls.from_json(read.text)


Response = tuple[int, Mapping[str, str], bytes]


class Transport(Protocol):
    def __call__(self, method: str, url: str, headers: Mapping[str, str] | None = None) -> Response: ...


class Host(Protocol):
    """Context 7.3. The readers return `ReadResult`s."""

    kind: str

    def remote(self, repo: str) -> str: ...
    def change_request(self, repo: str, number: int) -> ReadResult: ...
    def title_of(self, repo: str, number: int) -> ReadResult: ...
    def branch_head(self, repo: str, branch: str) -> ReadResult: ...


# ---------------------------------------------------------------------------- default transports


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect: a redirect to another host would carry the Authorization header
    along (urllib forwards it), and the answer of the other host is not the host's answer."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class UrllibTransport:
    """HTTP through `urllib`. A redirect is not followed (it comes back as a 3xx response, which
    the host layer reports as unreadable); an HTTP error status is a response, not an exception."""

    uses_cli_login = False

    def __init__(self, timeout: float = 30.0, handlers: Sequence[Any] = ()) -> None:
        self.timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirect, *handlers)

    def __call__(self, method: str, url: str, headers: Mapping[str, str] | None = None) -> Response:
        request = urllib.request.Request(url, method=method, headers=dict(headers or {}))
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                return response.status, dict(response.headers.items()), response.read(MAX_BODY)
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, dict(exc.headers.items()), exc.read(MAX_BODY)
            finally:
                exc.close()
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, TimeoutError):
                raise TimeoutError(f"no answer within {self.timeout:g}s") from None
            raise TransportError(redact.scrub_exception(exc)) from None


class CliTransport:
    """A host CLI's `api` command (`gh api`, `glab api`), which supplies the user's own
    login. The request URL is turned into the CLI's endpoint path, with `--hostname` when the
    API is not the CLI's default host (an enterprise instance); no credential is put in the
    argument list. `-i` makes the CLI print the status line and headers first."""

    uses_cli_login = True

    def __init__(self, binary: str, base_urls: Sequence[str], timeout: float = 30.0, hostname: str | None = None) -> None:
        self.binary = binary
        self.base_urls = tuple(u.rstrip("/") for u in base_urls)
        self.timeout = timeout
        self.hostname = hostname

    def endpoint(self, url: str) -> str:
        for base in self.base_urls:
            if url == base or url.startswith(base + "/") or url.startswith(base + "?"):
                return url[len(base):].lstrip("/")
        raise TransportError(f"{redact.scrub(url)} is not under the API of this CLI")

    def __call__(self, method: str, url: str, headers: Mapping[str, str] | None = None) -> Response:
        argv = [self.binary, "api"] + (["--hostname", self.hostname] if self.hostname else []) + ["-i", "-X", method]
        for name, value in (headers or {}).items():
            if name.lower() not in ("authorization", "private-token"):  # the CLI logs in by itself
                argv += ["-H", f"{name}: {value}"]
        argv.append(self.endpoint(url))
        try:
            proc = subprocess.run(argv, capture_output=True, timeout=self.timeout, check=False, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise TimeoutError(f"{self.binary} did not answer within {self.timeout:g}s") from None
        except OSError as exc:
            raise TransportError(f"cannot run {self.binary}: {redact.scrub_exception(exc)}") from None
        return self.parse(proc.stdout, proc.stderr.decode("utf-8", errors="replace"), proc.returncode)

    @staticmethod
    def parse(stdout: bytes, stderr: str, returncode: int) -> Response:
        head, sep, body = stdout.partition(b"\r\n\r\n")
        if not sep:
            head, sep, body = stdout.partition(b"\n\n")
        lines = head.decode("iso-8859-1").splitlines()
        match = re.match(r"^HTTP/\S+\s+(\d{3})", lines[0]) if lines else None
        if not match:
            detail = redact.scrub(stderr).strip() or f"exit code {returncode} and no HTTP response"
            raise TransportError(detail)
        headers = {}
        for line in lines[1:]:
            name, _, value = line.partition(":")
            if name:
                headers[name.strip()] = value.strip()
        return int(match.group(1)), headers, body


# ---------------------------------------------------------------------------- shared base


def split_repo(repo: str, *, min_parts: int = 2, max_parts: int | None = 2) -> list[str]:
    """`owner/name` (GitLab: any depth of groups) as its parts."""
    parts = [p for p in str(repo).split("/") if p] if isinstance(repo, str) else []
    if len(parts) < min_parts or (max_parts is not None and len(parts) > max_parts) or "/".join(parts) != repo:
        raise ValueError(f"a repository is written owner/name, not {repo!r}")
    return parts


def enc(part: str) -> str:
    """One URL path segment: every character that could be read as a separator is encoded,
    so `release/1.x` becomes `release%2F1.x`."""
    return quote(part, safe="")


def parse_number(text: str | int) -> int:
    """`12`, `#12` and `!12` (GitLab) as the number 12."""
    if isinstance(text, bool):
        raise ValueError("a change request number is a positive integer")
    if isinstance(text, int):
        number = text
    else:
        match = re.fullmatch(r"\s*[#!]?(\d+)\s*", str(text))
        if not match:
            raise ValueError(f"a change request number looks like 12, #12 or !12, not {text!r}")
        number = int(match.group(1))
    if number < 1:
        raise ValueError("a change request number is a positive integer")
    return number


def _lower(headers: Mapping[str, str] | None) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in (headers or {}).items()}


class HostBase:
    """What the three hosts share: sending, status handling, pagination, recording."""

    kind = "?"
    default_token_env: str | None = None
    api_base = ""
    web_base = ""
    change_word = "change request"

    def __init__(
        self,
        *,
        transport: Transport | None = None,
        record_to: Any = None,
        env: Mapping[str, str] | None = None,
        token_env: str | None = None,
        api_url: str | None = None,
        web_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._transport_given = transport
        self._transport_built: Any = None
        self.target: Any = record_to if record_to is not None else []
        self.env = os.environ if env is None else env
        self.token_env = token_env if token_env is not None else self.default_token_env
        self.api_base = (api_url or self.api_base).rstrip("/")
        self.web_base = (web_url or self.web_base).rstrip("/")
        self.timeout = timeout

    # ---- transport and credentials

    def _transport(self) -> Any:
        if self._transport_given is not None:
            return self._transport_given
        if os.environ.get(FORBID_DEFAULT_TRANSPORT_ENV):
            raise DefaultTransportForbidden(
                f"a default transport was asked for while {FORBID_DEFAULT_TRANSPORT_ENV} is set: the test suite makes no network call"
            )
        if self._transport_built is None:
            self._transport_built = self.default_transport()
        return self._transport_built

    def default_transport(self) -> Any:
        return UrllibTransport(self.timeout)

    def _headers(self, transport: Any, url: str) -> dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": "reachmap"}
        headers.update(self.extra_headers())
        if getattr(transport, "uses_cli_login", False) or not self.token_env:
            return headers
        token = self.env.get(self.token_env, "")
        if token:
            redact.register_secret(token)
            if urlsplit(url).scheme.lower() != "https":
                raise _Bad(f"refusing to send the token in {self.token_env} over a connection that is not https")
            name, value = self.auth_header(token)
            headers[name] = value
        return headers

    def auth_header(self, token: str) -> tuple[str, str]:
        """The header the credential goes in: `Authorization` unless the service names another (Qase's is `Token`). It is the only
        header that ever holds the value."""
        return "Authorization", self.authorization(token)

    def extra_headers(self) -> dict[str, str]:
        return {}

    def authorization(self, token: str) -> str:
        return f"Bearer {token}"

    @property
    def incompleteness(self) -> list:
        if isinstance(self.target, list):
            return list(self.target)
        return list(self.target.incompleteness)

    def _unreadable(self, subject: str, detail: str) -> ReadResult:
        clean = redact.for_record(detail) or "the read failed"
        record(self.target, "read_unreadable", redact.for_record(subject, 300), detail=clean)
        return ReadResult.unreadable(clean)

    # ---- one request

    def _send(self, url: str) -> tuple[int, dict[str, str], bytes]:
        """One GET. Raises _Bad when no usable answer came back."""
        transport = self._transport()  # built outside the try: a bug there must not read as a failed read
        headers = self._headers(transport, url)
        try:
            status, resp_headers, body = transport("GET", url, headers)
        except TimeoutError:
            raise _Bad("the host did not answer in time") from None
        except TransportError as exc:
            raise _Bad(f"the request could not be made: {redact.scrub(str(exc))}") from None
        except (OSError, ValueError, http.client.HTTPException, subprocess.SubprocessError) as exc:
            raise _Bad(f"the request failed: {redact.scrub_exception(exc)}") from None
        if isinstance(status, bool) or not isinstance(status, int):
            raise _Bad("the transport gave no HTTP status")
        return status, _lower(resp_headers), body if isinstance(body, (bytes, bytearray)) else str(body).encode()

    def _classify(self, status: int, headers: dict[str, str], body: bytes) -> str:
        """`found`, `absent` (an explicit 404), or raises _Bad with the reason."""
        if 200 <= status < 300:
            return Read.FOUND
        if status == 404:
            return Read.ABSENT
        text = body[:300].decode("utf-8", errors="replace")
        if status == 429 or (status == 403 and (headers.get("x-ratelimit-remaining") == "0" or "retry-after" in headers
                                               or "rate limit" in text.lower() or "too many requests" in text.lower())):
            reset = headers.get("x-ratelimit-reset") or headers.get("ratelimit-reset") or headers.get("retry-after")
            raise _Bad(f"rate limited by the host (HTTP {status}" + (f", try again after {reset}" if reset else "") + ")")
        if 300 <= status < 400:
            where = redact.scrub(headers.get("location", ""))
            raise _Bad(f"redirect refused (HTTP {status}" + (f" to {where}" if where else "") + "): it could be another host's answer")
        if status == 401:
            if not self.token_env:
                hint = ""
            elif self.env.get(self.token_env):
                hint = f"; the token in {self.token_env} was refused"
            else:
                hint = f"; set {self.token_env}"
            raise _Bad(f"the host refused the credentials (HTTP 401{hint})")
        if status == 403:
            raise _Bad("the host refused access (HTTP 403)")
        if status >= 500:
            raise _Bad(f"the host failed (HTTP {status})")
        raise _Bad(f"unexpected answer from the host (HTTP {status})")

    def _get_json(self, url: str) -> tuple[str, Any, dict[str, str]]:
        """(`found`, parsed JSON, headers) or (`absent`, None, headers); raises _Bad otherwise."""
        status, headers, body = self._send(url)
        verdict = self._classify(status, headers, body)
        if verdict == Read.ABSENT:
            return verdict, None, headers
        try:
            return verdict, json.loads(body.decode("utf-8")), headers
        except (ValueError, UnicodeDecodeError, RecursionError):  # JSON nested deeper than the parser allows is not an answer either
            raise _Bad("the host answered with text that is not valid JSON") from None

    def _collect(
        self,
        url: str,
        items_of: Callable[[Any], list],
        next_of: Callable[[Any, dict[str, str]], str | None],
    ) -> list:
        """Every item of a paginated list: follows the next link until there is none. A page
        that fails, a loop, a next link to another host (the token would follow it) and a list
        longer than MAX_PAGES are all unreadable: a partial list is never returned."""
        items, more = self._collect_pages(url, items_of, next_of, MAX_PAGES)
        if more:
            raise _Bad(f"the list has more than {MAX_PAGES} pages and was not read completely")
        return items

    def _collect_pages(
        self,
        url: str,
        items_of: Callable[[Any], list],
        next_of: Callable[[Any, dict[str, str]], str | None],
        cap: int,
    ) -> tuple[list, bool]:
        """(the items of up to `cap` pages, whether the list goes on past them). For a reader that says a cut and does not fail on
        one: the test sources, whose page cap is a configured limit. The rest is `_collect`'s: a page that fails, a loop and a next link
        to another host are unreadable."""
        items: list = []
        seen: set[str] = set()
        api_host = urlsplit(self.api_base).netloc
        for _ in range(cap):
            verdict, data, headers = self._get_json(url)
            if verdict == Read.ABSENT:
                raise _Bad("a page of the list answered 404")
            items.extend(items_of(data))
            following = next_of(data, headers)
            if not following:
                return items, False
            if urlsplit(following).netloc != api_host:
                raise _Bad("the list points at another host for its next page; not followed")
            if following in seen or following == url:
                raise _Bad("the pages of the list loop")
            seen.add(url)
            url = following
        return items, True

    def _guarded(self, subject: str, work: Callable[[], ReadResult]) -> ReadResult:
        try:
            return work()
        except _Bad as exc:
            return self._unreadable(subject, str(exc))

    # ---- shapes

    @staticmethod
    def _obj(value: Any, name: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise _Bad(f"unexpected response: {name} is not an object")
        return value

    @staticmethod
    def _str(obj: Mapping[str, Any], key: str, *, allow_null: bool = False, where: str = "") -> str:
        value = obj.get(key)
        if value is None and allow_null:
            return ""
        if not isinstance(value, str):
            raise _Bad(f"unexpected response: {where}{key} is missing or not text")
        return value

    @staticmethod
    def _sha(obj: Mapping[str, Any], key: str, *, where: str = "") -> str:
        value = obj.get(key)
        if not isinstance(value, str) or not _SHA.match(value):
            raise _Bad(f"unexpected response: {where}{key} is missing or not a full commit sha")
        return value

    # ---- the readers (hosts fill in `_fetch_change_request` and `_branch_path`)

    def _split(self, repo: str) -> list[str]:
        return split_repo(repo)  # owner/name (GitLab allows nested groups and overrides this)

    def remote(self, repo: str) -> str:
        self._split(repo)
        return f"{self.web_base}/{repo}.git"

    def subject(self, repo: str, number: int) -> str:
        return f"{self.kind} {repo} {self.change_word} #{number}"

    def change_request(self, repo: str, number: int) -> ReadResult:
        number = parse_number(number)
        subject = self.subject(repo, number)

        def work() -> ReadResult:
            found = self._fetch_change_request(repo, number)
            return ReadResult.absent() if found is None else ReadResult.found(found.to_json())

        return self._guarded(subject, work)

    def title_of(self, repo: str, number: int) -> ReadResult:
        """The title of a change request, found / absent / unreadable (the text is the title)."""
        number = parse_number(number)
        subject = f"{self.kind} {repo} {self.change_word} #{number} title"

        def work() -> ReadResult:  # one request, and only the title is required of the answer
            verdict, data, _ = self._get_json(self.api_base + self._pr_path(repo, number))
            if verdict == Read.ABSENT:
                return ReadResult.absent()
            return ReadResult.found(self._str(self._obj(data, "the change request"), "title"))

        return self._guarded(subject, work)

    def branch_head(self, repo: str, branch: str) -> ReadResult:
        """The sha at the head of a branch, by name (slashes are encoded in the API path)."""
        if not isinstance(branch, str) or not branch:
            raise ValueError("a branch name is needed")
        subject = f"{self.kind} {repo} branch {branch}"

        def work() -> ReadResult:
            verdict, data, _ = self._get_json(self.api_base + self._branch_path(repo, branch))
            if verdict == Read.ABSENT:
                return ReadResult.absent()
            return ReadResult.found(self._branch_sha(self._obj(data, "the branch")))

        return self._guarded(subject, work)

    def _pr_path(self, repo: str, number: int) -> str:
        raise NotImplementedError

    def _fetch_change_request(self, repo: str, number: int) -> ChangeRequest | None:
        raise NotImplementedError

    def _branch_path(self, repo: str, branch: str) -> str:
        raise NotImplementedError

    def _branch_sha(self, data: dict[str, Any]) -> str:
        raise NotImplementedError


# ---------------------------------------------------------------------------- registry

_REGISTRY: dict[str, Callable[..., Any]] = {}


def register_host(kind: str, factory: Callable[..., Any]) -> None:
    """Make `kind` known to `get_host` (the hosts of this package register themselves)."""
    _REGISTRY[kind] = factory


def hosts() -> tuple[str, ...]:
    _load_builtin()
    return tuple(_REGISTRY)


def get_host(kind: str, **options: Any) -> Any:
    """A host adapter by its config kind (`github`, `gitlab`, `bitbucket`, `git`). Options:
    `transport`, `record_to`, `env`, `token_env`, `api_url`, `web_url`, `timeout`
    (`remotes` for `git`)."""
    _load_builtin()
    try:
        factory = _REGISTRY[kind]
    except (KeyError, TypeError):
        raise ValueError(f"unknown host {kind!r}; known: {', '.join(_REGISTRY)}") from None
    return factory(**options)


def _load_builtin() -> None:
    from reachmap.hosts import bitbucket, github, gitlab, plain  # noqa: F401  (each registers itself)
