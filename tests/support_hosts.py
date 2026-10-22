"""A fake transport that serves the synthetic host responses in tests/fixtures/hosts."""

import json
from pathlib import Path
from typing import Mapping
from unittest import mock

HOSTS_DIR = Path(__file__).resolve().parent / "fixtures" / "hosts"

B, H, M, P, Q = "1" * 40, "2" * 40, "3" * 40, "4" * 40, "5" * 40


def load(rel: str, substitutions: Mapping[str, str] | None = None) -> tuple[int, dict, bytes]:
    """(status, headers, body bytes) of one recorded response."""
    doc = json.loads((HOSTS_DIR / rel).read_text(encoding="utf-8"))
    if "body_text" in doc:
        body = doc["body_text"].encode("utf-8")
    else:
        body = json.dumps(doc["body"]).encode("utf-8")
    text = body.decode("utf-8")
    for old, new in (substitutions or {}).items():
        text = text.replace(old, new)
    return doc["status"], dict(doc["headers"]), text.encode("utf-8")


class FakeTransport:
    """`routes` maps a full URL to a fixture path (served with its recorded status and
    headers), to a (status, headers, body) tuple, or to an exception to raise. Every request
    is recorded in `requests` as (method, url, headers). An unexpected URL fails the test."""

    uses_cli_login = False

    def __init__(self, routes: Mapping[str, object], substitutions: Mapping[str, str] | None = None, cli_login: bool = False):
        self.routes = dict(routes)
        self.substitutions = dict(substitutions or {})
        self.requests: list[tuple[str, str, dict]] = []
        self.uses_cli_login = cli_login

    @property
    def urls(self) -> list[str]:
        return [url for _, url, _ in self.requests]

    def __call__(self, method, url, headers=None):
        self.requests.append((method, url, dict(headers or {})))
        route = self.routes.get(url)
        if route is None:
            raise AssertionError(f"unexpected request: {method} {url}")
        if isinstance(route, BaseException):
            raise route
        if isinstance(route, tuple):
            return route
        return load(route, self.substitutions)


def forbid_default_transports():
    """Patch the constructors of the default transports to raise, for the life of a test.
    Returns the started patchers; the caller stops them (use TestCase.addCleanup)."""
    patchers = []
    for name in ("UrllibTransport", "CliTransport"):
        patcher = mock.patch(
            f"reachmap.hosts.{name}.__init__",
            side_effect=AssertionError(f"a default transport ({name}) was constructed in a test: no network in tests"),
        )
        patcher.start()
        patchers.append(patcher)
    return patchers
