"""The read-only web client of the hosted test sources.

It is the host layer's request code (`reachmap.hosts.HostBase`: one injected transport, no redirect followed, rate limits and
5xx told apart from a 404, the credential registered with `redact` and sent in the `Authorization` header only, never over a
connection that is not https), used for GET alone: `get` is the only method that sends anything, and it names the verb itself,
so no caller can ask for another. A failure comes out as `SourceError` with a message that holds no credential.
"""

from __future__ import annotations

import base64
from typing import Any, Callable, Mapping

from reachmap import redact
from reachmap.hosts import HostBase, ResponseUnusable
from reachmap.model import Read
from reachmap.testsources.base import SourceError

__all__ = ["Client"]


class Client(HostBase):
    kind = "test source"

    def __init__(self, base_url: str, *, token_env: str, transport: Any = None, env: Mapping[str, str] | None = None, basic: bool = False,
                 header: str = "Authorization", check: Callable[[], None] | None = None, timeout: float = 30.0) -> None:
        super().__init__(transport=transport, env=env, token_env=token_env, api_url=base_url, timeout=timeout)
        self.basic = basic
        self.header = header
        self._check = check

    def auth_header(self, token: str) -> tuple[str, str]:
        """`Token: <token>` for a service that wants the bare token in a header of its own (Qase); else the `Authorization` header."""
        if self.basic and ":" in token:
            redact.register_secret(token.partition(":")[2])  # the key alone is as secret as the pair: it is scrubbed wherever it turns up
        if self.header.lower() != "authorization":
            return self.header, token
        return super().auth_header(token)

    def authorization(self, token: str) -> str:
        """`Basic` for a login and a key (TestRail), `Bearer` otherwise."""
        if self.basic:
            return "Basic " + base64.b64encode(token.encode("utf-8")).decode("ascii")
        return f"Bearer {token}"

    def _get_json(self, url: str) -> tuple[str, Any, dict[str, str]]:
        if self._check is not None:
            self._check()  # the run's deadline, before every request: raises to stop the run, it is not a failure of the source
        return super()._get_json(url)

    def pages(self, url: str, items_of: Callable[[Any], list], next_of: Callable[[Any, dict[str, str]], str | None], cap: int, what: str) -> tuple[list, bool]:
        """(the items of up to `cap` pages of a list that links to its next page, whether it goes on past them); `SourceError` for
        a page that fails, a loop or a link to another host."""
        try:
            return self._collect_pages(url, items_of, next_of, cap)
        except ResponseUnusable as exc:
            raise SourceError(f"the list of {what}: {exc}") from None

    def get(self, url: str, what: str) -> tuple[Any, dict[str, str]]:
        """The JSON answer of one GET, and the response headers. Raises `SourceError` (`what` is named in it) for anything that is
        not a usable answer: a 404 as well, because the thing asked for is the project or a page of a list that has to exist."""
        try:
            verdict, data, headers = self._get_json(url)
        except ResponseUnusable as exc:
            raise SourceError(f"{what}: {exc}") from None
        if verdict == Read.ABSENT:
            raise SourceError(f"{what}: not found (HTTP 404)")
        return data, headers
