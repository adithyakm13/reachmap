"""Host-less `git` mode: any git remote, no change requests, compare and range only."""

from __future__ import annotations

from typing import Any, Mapping

from reachmap.hosts import HostUnsupported, register_host
from reachmap.model import ReadResult


class PlainHost:
    kind = "git"

    def __init__(self, *, remotes: Mapping[str, str] | None = None, record_to: Any = None, **_ignored: Any) -> None:
        self.remotes = dict(remotes or {})
        self.target = record_to if record_to is not None else []

    def remote(self, repo: str) -> str:
        try:
            return self.remotes[repo]
        except KeyError:
            raise HostUnsupported(f"host git has no clone URL for {repo!r}: set `remote` in its config table") from None

    def _unsupported(self) -> HostUnsupported:
        return HostUnsupported("host git has no change requests: use a compare (base...head) or a range (a..b)")

    def change_request(self, repo: str, number: int) -> ReadResult:
        raise self._unsupported()

    def title_of(self, repo: str, number: int) -> ReadResult:
        raise self._unsupported()

    def branch_head(self, repo: str, branch: str) -> ReadResult:
        raise self._unsupported()


register_host("git", PlainHost)
