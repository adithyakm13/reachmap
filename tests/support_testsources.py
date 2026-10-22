"""Helpers for the M15 tests: recorded (synthetic) responses of Qase and TestRail served by a fake transport that only ever
answers a GET, config tables for the sources, and the checks every test of a hosted source ends with (every request was a GET,
the token is in the Authorization header and nowhere else)."""

from __future__ import annotations

import base64
import json
import unittest
from pathlib import Path
from typing import Any, Mapping

from reachmap import config

DIR = Path(__file__).resolve().parent / "fixtures" / "testsources"

TOKEN = "qs-tok-0123456789abcdef-NOT-A-REAL-TOKEN"
TESTRAIL_LOGIN = "qa.lead@example.invalid"
TESTRAIL_KEY = "tr-key-fedcba9876543210-NOT-A-REAL-KEY"
TESTRAIL_TOKEN = f"{TESTRAIL_LOGIN}:{TESTRAIL_KEY}"
SECRETS = (TOKEN, TESTRAIL_KEY, TESTRAIL_TOKEN, base64.b64encode(TESTRAIL_TOKEN.encode()).decode())

QASE = "https://api.qase.io/v1"
TESTRAIL = "https://acme.testrail.example"


def load(rel: str) -> tuple[int, dict, bytes]:
    """(status, headers, body bytes) of one recorded response under tests/fixtures/testsources."""
    doc = json.loads((DIR / rel).read_text(encoding="utf-8"))
    body = doc["body_text"].encode("utf-8") if "body_text" in doc else json.dumps(doc["body"]).encode("utf-8")
    return doc["status"], dict(doc["headers"]), body


def recorded(rel: str) -> dict:
    """The JSON body of a recorded response, to be changed by a test that needs a variation."""
    return json.loads((DIR / rel).read_text(encoding="utf-8"))["body"]


def respond(body: Any, status: int = 200, headers: Mapping[str, str] | None = None) -> tuple[int, dict, bytes]:
    return status, dict(headers or {"Content-Type": "application/json"}), json.dumps(body).encode("utf-8")


def own_header(url: str) -> str:
    """The header the credential of the source at `url` goes in: Qase's is `Token`, TestRail's is `Authorization`."""
    return "Token" if url.startswith(QASE) else "Authorization"


ALL: list["Fake"] = []  # every fake transport made since the last reset: a test that ends can look at every request


class Fake:
    """Serves a route (a full URL) from a recorded file, a (status, headers, body) tuple or an exception to raise. Any method
    but GET fails the test on the spot, and every request is kept in `requests` as (method, url, headers)."""

    uses_cli_login = False

    def __init__(self, routes: Mapping[str, object]) -> None:
        self.routes = dict(routes)
        self.requests: list[tuple[str, str, dict]] = []
        ALL.append(self)

    @property
    def urls(self) -> list[str]:
        return [url for _, url, _ in self.requests]

    def __call__(self, method, url, headers=None):
        self.requests.append((method, url, dict(headers or {})))
        if method != "GET":
            raise AssertionError(f"a test source made a {method} request to {url}: only GET is allowed (rule 4.10)")
        route = self.routes.get(url)
        if route is None:
            raise AssertionError(f"unexpected request: GET {url}")
        if isinstance(route, BaseException):
            raise route
        if isinstance(route, tuple):
            return route
        return load(route)


def table(kind: str, areas: list[dict] | None = None, **options: Any) -> config.TestSource:
    return config.TestSource(kind, config.FrozenMap({k: config._freeze(v) for k, v in options.items()}),
                             tuple(config.FrozenMap({k: config._freeze(v) for k, v in a.items()}) for a in (areas or [])))


def qase_table(areas: list[dict] | None = None, **options: Any) -> config.TestSource:
    opts = {"project": "ACME", "token_env": "QASE_API_TOKEN", "page_size": 15}
    opts.update(options)
    return table("qase", areas if areas is not None else [BILLING], **opts)


def testrail_table(areas: list[dict] | None = None, **options: Any) -> config.TestSource:
    opts = {"base_url": TESTRAIL, "project_id": 3, "token_env": "TESTRAIL_API_KEY", "page_size": 15}
    opts.update(options)
    return table("testrail", areas if areas is not None else [dict(BILLING, suites=[31])], **opts)


BILLING = {"name": "Billing", "route_prefixes": ["/billing"], "modules": ["billing"], "suites": [10]}

QASE_ENV = {"QASE_API_TOKEN": TOKEN}
TESTRAIL_ENV = {"TESTRAIL_API_KEY": TESTRAIL_TOKEN}

QASE_ROUTES = {
    f"{QASE}/project/ACME": "qase/project.json",
    f"{QASE}/suite/ACME?limit=15&offset=0": "qase/suites.json",
    f"{QASE}/case/ACME?limit=15&offset=0": "qase/cases-page1.json",
    f"{QASE}/case/ACME?limit=15&offset=15": "qase/cases-page2.json",
}
TR = f"{TESTRAIL}/index.php?/api/v2"
TESTRAIL_ROUTES = {
    f"{TR}/get_project/3": "testrail/project.json",
    f"{TR}/get_sections/3&limit=15&offset=0": "testrail/sections.json",
    f"{TR}/get_cases/3&limit=15&offset=0": "testrail/cases-page1.json",
    f"{TR}/get_cases/3&limit=15&offset=15": "testrail/cases-page2.json",
}


class HostedCase(unittest.TestCase):
    """A test of a hosted source: it ends by checking what every test of one must hold for every transport it made."""

    def setUp(self) -> None:
        ALL.clear()
        self.addCleanup(self.check_every_request)

    def check_every_request(self) -> None:
        for fake in ALL:
            for method, url, headers in fake.requests:
                self.assertEqual(method, "GET", url)
                own = own_header(url)
                for secret in SECRETS:
                    self.assertNotIn(secret, url, "a token is never in a URL")
                for name, value in headers.items():
                    if name != own:
                        for secret in SECRETS:
                            self.assertNotIn(secret, value, f"a token is only in the source's own header ({own}), not in {name}")
                if url.startswith(QASE):
                    self.assertNotIn("Authorization", headers, "Qase takes its token in the Token header")


# ------------------------------------------------------------------------------------------ the spec fixture repository

E2E = DIR / "e2e"


class E2eRepo:
    """The small invented repository of tests/fixtures/testsources/e2e: a web app with billing, cases and reports screens, Playwright
    specs under e2e/, Cypress specs under cypress/e2e/, and a branch `topic/money-rounding` that changes the `Money` component
    that five of the screens render. Built once per process; `config` loads a workspace that has it."""

    def __init__(self) -> None:
        import atexit
        import tempfile

        from tests import support_git as sg
        from tests.fixtures import build

        self.root = Path(tempfile.mkdtemp(prefix="reachmap-e2e-"))
        atexit.register(sg.remove_tree, self.root)
        self.home = self.root / "home"
        self.cache = self.root / "cache"
        repo = build.Repo(self.root / "acme-e2e", build.git_env(self.root))
        repo.init()
        history = json.loads((E2E / "history.json").read_text(encoding="utf-8"))
        for step in history["steps"]:
            repo.checkout(step["branch"], step.get("from"))
            build.apply_overlay(E2E / "steps" / step["dir"], repo.path)
            repo.commit(step["message"])
        repo.git("checkout", "-q", "main")
        sg.enable_server_filters(repo.path)
        self.path = repo.path
        self.remote = sg.remote_url(repo.path)
        self.main = sg.rev(repo.path, "main")
        self.topic = sg.rev(repo.path, "topic/money-rounding")
        self.git_env = sg.isolated_env(self.home)

    def config(self, sources: str = "", name: str = "acme/e2e") -> "config.Workspace":
        text = f'''
workspace = "acme"
[limits]
hops = 3
hub_importers = 50
distinct_max = 10
timeout_s = 900

[repos."{name}"]
host = "git"
remote = "{self.remote}"
language = "typescript"
roles = ["frontend"]
branches = ["main"]
aliases = {{ from = "tsconfig.json" }}
entry_points = {{ kind = "react-router", routes_glob = "src/modules/*/routes.tsx" }}

{sources}
'''
        return config.loads(text, fmt="toml", env={})

    def git(self, record_to=None):
        from reachmap.gitlayer import Git

        return Git(self.cache, env=self.git_env, record_to=record_to if record_to is not None else [])


_E2E: list[E2eRepo] = []


def e2e() -> E2eRepo:
    if not _E2E:
        _E2E.append(E2eRepo())
    return _E2E[0]
