"""Qase (https://developers.qase.io), read only.

Four GET requests in a run of a small project: the project (it has to exist), the suite tree, and the cases, paged with
`limit` and `offset` until the `filtered` total is reached. A list is never cut quietly: a page cap (`max_pages`) leaves the
corpus incomplete and says how much was read of how much there is; a page that fails or comes back empty before the total is
reached is `SourceError` (no first half is returned as if it were the list).

The credential is read from the variable `token_env` names, at the moment of the request, registered with `redact`, and goes in
the `Token` header Qase's API takes it in (`AUTH_HEADER`), never over a connection that is not https and nowhere else. Nothing
here sends anything but a GET.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from reachmap import config
from reachmap.hosts import enc
from reachmap.testsources import match, words
from reachmap.testsources.base import Case, Context, Corpus, Found, SourceError, clean_title
from reachmap.testsources.client import Client

__all__ = ["DEFAULT_MAX_PAGES", "DEFAULT_PAGE_SIZE", "Qase", "DEFAULT_BASE_URL"]

DEFAULT_BASE_URL = "https://api.qase.io/v1"
AUTH_HEADER = "Token"  # Qase API v1 takes the API token in this header, as it is
DEFAULT_PAGE_SIZE = 100  # the largest page the service allows
DEFAULT_MAX_PAGES = 200  # 20,000 cases at the default size: a project past it is reported as cut


def _bad(why: str) -> SourceError:
    return SourceError(f"unexpected response: {why}")


class Qase:
    kind = "qase"

    def __init__(self, source: config.TestSource, *, env: Mapping[str, str] | None = None, transport: Any = None,
                 check: Callable[[], None] | None = None) -> None:
        import os

        self.source = source
        self.options = source.options
        self.areas = source.areas
        self.env = os.environ if env is None else env
        self._transport = transport
        self._check = check

    @property
    def project(self) -> str | None:
        value = self.options.get("project")
        return value if isinstance(value, str) and value.strip() else None

    @property
    def label(self) -> str:
        return f"qase:{self.project}" if self.project else "qase"

    def unconfigured(self) -> str | None:
        """Why this source cannot be read, or None. Each reason names a key or a variable, never a value."""
        why: list[str] = []
        if self.source.unset_vars:
            why.append(f"the variable {', '.join(self.source.unset_vars)} in the configuration is unset or empty")
        token_env = self.options.get("token_env")
        if not isinstance(token_env, str) or not token_env:
            why.append("token_env is not set: it names the variable that holds the API token")
        elif not self.env.get(token_env):
            why.append(f"the variable {token_env} is unset or empty")
        if self.project is None and not self.source.unset_vars:
            why.append("project is not set")
        if not self.areas:
            why.append("no [[test_sources.areas]] are configured, so no case can be tied to an area")
        return "; ".join(why) or None

    def suggest(self, infos: Sequence[match.AreaInfo], ctx: Context | None = None) -> Found:
        """Read the whole project and suggest its cases for the areas; `SourceError` when it cannot be read."""
        corpus = self.fetch()
        note = words.small_corpus_note(self.label, len(corpus.cases))
        return Found(tuple(match.suggest(self.label, corpus, self.areas, infos)), (), corpus.cut, (note,) if note else ())

    # ------------------------------------------------------------------------------ reading

    def fetch(self) -> Corpus:
        """Every case and the suite tree, or `SourceError`. Call `unconfigured()` first."""
        base = str(self.options.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        token_env = str(self.options["token_env"])
        client = Client(base, token_env=token_env, transport=self._transport, env=self.env, header=AUTH_HEADER, check=self._check)
        code = enc(str(self.project))
        size = int(self.options.get("page_size") or DEFAULT_PAGE_SIZE)
        cap = int(self.options.get("max_pages") or DEFAULT_MAX_PAGES)
        data, _ = client.get(f"{base}/project/{code}", f"project {self.project}")
        if not isinstance(data, dict) or data.get("status") is False:
            raise SourceError(f"the service reported an error for project {self.project}")
        if not isinstance(data.get("result"), dict):
            raise _bad("the project has no result")
        suites, cut_suites = self._pages(client, f"{base}/suite/{code}", size, cap, "suites")
        cases, cut_cases = self._pages(client, f"{base}/case/{code}", size, cap, "cases")
        parents: dict[int, int | None] = {}
        for s in suites:
            parents[self._int(s, "id")] = self._opt_int(s, "parent_id")
        out = []
        for c in cases:
            title = c.get("title")
            if not isinstance(title, str):
                raise _bad("a case has no title")
            out.append(Case(f"{self.project}-{self._int(c, 'id')}", clean_title(title), self._opt_int(c, "suite_id")))
        cuts = [x for x in (cut_suites, cut_cases) if x]
        return Corpus(tuple(out), parents, "; ".join(cuts))

    def _pages(self, client: Client, url: str, size: int, cap: int, what: str) -> tuple[list[dict[str, Any]], str]:
        """All entities of a list paged by `limit` and `offset`, and the cut (empty when the list was read to its total)."""
        items: list[dict[str, Any]] = []
        total = 0
        for page in range(cap):
            data, _ = client.get(f"{url}?limit={size}&offset={len(items)}", f"the list of {what} (page {page + 1})")
            entities, total = self._entities(data, what)
            items.extend(entities)
            if len(items) >= total:
                return items, ""
            if not entities:
                raise SourceError(f"the list of {what} came back empty at {len(items)} of {total}: the pages do not add up")
        return items, f"{what}: read {len(items)} of {total} (max_pages {cap})"

    @staticmethod
    def _entities(data: Any, what: str) -> tuple[list[dict[str, Any]], int]:
        if not isinstance(data, dict):
            raise _bad(f"the list of {what} is not an object")
        if data.get("status") is False:
            raise SourceError(f"the service reported an error for the list of {what}")
        result = data.get("result")
        if not isinstance(result, dict):
            raise _bad(f"the list of {what} has no result")
        entities = result.get("entities")
        total, filtered = result.get("total"), result.get("filtered")
        if not isinstance(entities, list) or not all(isinstance(e, dict) for e in entities):
            raise _bad(f"the {what} are not a list of objects")
        number = lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0  # noqa: E731
        if not number(total) and not number(filtered):
            raise _bad(f"the total of the {what} is not a number")
        if number(total) and number(filtered) and total != filtered:  # no filter is sent, so no list should be a filtered one
            raise SourceError(f"the service says the list of {what} was filtered ({filtered} of {total}) although no filter was sent")
        return entities, total if number(total) else filtered

    @staticmethod
    def _int(obj: Mapping[str, Any], key: str) -> int:
        value = obj.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise _bad(f"{key} of a suite or case is not a number")
        return value

    @classmethod
    def _opt_int(cls, obj: Mapping[str, Any], key: str) -> int | None:
        return None if obj.get(key) is None else cls._int(obj, key)
