"""TestRail (API v2), read only.

Requests: the project (it has to exist), the sections tree and the cases, each list followed through `_links.next` until there is
none. The address of the next page is taken from the answer only when it stays on the instance's host (the credential would follow
it anywhere else), a link that loops or a list past `max_pages` is a cut or a failure, never a quiet end, and a page that fails
in the middle is `SourceError`. An older server that answers a plain list (no pagination) is read as one complete page.

The credential is the login and the API key in one variable, `login:key`, read from the variable `token_env` names and sent as a
Basic `Authorization` header; neither half is ever in a URL. Only `get_*` endpoints are built (`endpoint` refuses anything else),
and nothing here sends anything but a GET.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from reachmap import config
from reachmap.testsources import match, words
from reachmap.testsources.base import Case, Context, Corpus, Found, SourceError, clean_title
from reachmap.testsources.client import Client

__all__ = ["DEFAULT_MAX_PAGES", "DEFAULT_PAGE_SIZE", "TestRail", "endpoint"]

DEFAULT_PAGE_SIZE = 250  # the largest page the service allows
DEFAULT_MAX_PAGES = 80  # 20,000 cases at the default size

READ_ENDPOINTS = ("get_project", "get_sections", "get_cases")  # the only endpoints this module builds


def endpoint(name: str, project_id: int) -> str:
    """`get_cases/3`: the path of a read endpoint. A name that is not one of the `get_*` endpoints it reads is a `ValueError`."""
    if name not in READ_ENDPOINTS:
        raise ValueError(f"{name!r} is not an endpoint this reader builds: only {', '.join(READ_ENDPOINTS)}")
    return f"{name}/{int(project_id)}"


def _bad(why: str) -> SourceError:
    return SourceError(f"unexpected response: {why}")


class TestRail:
    __test__ = False  # not a test case, whatever its name says
    kind = "testrail"

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
    def project_id(self) -> int | None:
        value = self.options.get("project_id")
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    @property
    def base(self) -> str | None:
        value = self.options.get("base_url")
        return value.rstrip("/") if isinstance(value, str) and value else None

    @property
    def label(self) -> str:
        return f"testrail:{self.project_id}" if self.project_id is not None else "testrail"

    def unconfigured(self) -> str | None:
        why: list[str] = []
        if self.source.unset_vars:
            why.append(f"the variable {', '.join(self.source.unset_vars)} in the configuration is unset or empty")
        token_env = self.options.get("token_env")
        if not isinstance(token_env, str) or not token_env:
            why.append("token_env is not set: it names the variable that holds the login and the API key")
        else:
            value = self.env.get(token_env)
            if not value:
                why.append(f"the variable {token_env} is unset or empty")
            elif ":" not in value or value.startswith(":") or value.endswith(":"):
                why.append(f"the variable {token_env} must hold the login and the API key as login:key")
        if self.base is None and not self.source.unset_vars:
            why.append("base_url is not set: it is the address of the TestRail instance")
        if self.project_id is None and not self.source.unset_vars:
            why.append("project_id is not set")
        if not self.areas:
            why.append("no [[test_sources.areas]] are configured, so no case can be tied to an area")
        return "; ".join(why) or None

    def suggest(self, infos: Sequence[match.AreaInfo], ctx: Context | None = None) -> Found:
        """Read the whole project and suggest its cases for the areas; `SourceError` when it cannot be read."""
        corpus = self.fetch()
        note = words.small_corpus_note(self.label, len(corpus.cases))
        return Found(tuple(match.suggest(self.label, corpus, self.areas, infos)), (), corpus.cut, (note,) if note else ())

    # ------------------------------------------------------------------------------ reading

    def _url(self, name: str, extra: str, size: int) -> str:
        assert self.base is not None and self.project_id is not None
        suite = self.options.get("suite_id")
        suite_part = f"&suite_id={int(suite)}" if isinstance(suite, int) and not isinstance(suite, bool) else ""
        return f"{self.base}/index.php?/api/v2/{endpoint(name, self.project_id)}{suite_part}{extra}&limit={size}&offset=0"

    def fetch(self) -> Corpus:
        assert self.base is not None and self.project_id is not None
        client = Client(self.base, token_env=str(self.options["token_env"]), transport=self._transport, env=self.env, basic=True, check=self._check)
        size = int(self.options.get("page_size") or DEFAULT_PAGE_SIZE)
        cap = int(self.options.get("max_pages") or DEFAULT_MAX_PAGES)
        project, _ = client.get(f"{self.base}/index.php?/api/v2/{endpoint('get_project', self.project_id)}", f"project {self.project_id}")
        if not isinstance(project, dict):
            raise _bad("the project is not an object")
        sections, cut_sections = self._pages(client, self._url("get_sections", "", size), "sections", cap, "get_sections")
        cases, cut_cases = self._pages(client, self._url("get_cases", "", size), "cases", cap, "get_cases")
        parents: dict[int, int | None] = {}
        for s in sections:
            parents[self._int(s, "id")] = self._opt_int(s, "parent_id")
        out = []
        for c in cases:
            title = c.get("title")
            if not isinstance(title, str):
                raise _bad("a case has no title")
            out.append(Case(f"C{self._int(c, 'id')}", clean_title(title), self._opt_int(c, "section_id")))
        cuts = [x for x in (cut_sections, cut_cases) if x]
        return Corpus(tuple(out), parents, "; ".join(cuts))

    def _resolver(self, name: str) -> Callable[[Any], str]:
        """What a `_links.next` of the list `name` may be: the same endpoint and the same project, on the instance's own host. It is
        rebuilt from the instance address as configured, so a link is never requested as written (a link to a write endpoint is not a read,
        and the credential would follow a link anywhere)."""
        assert self.base is not None and self.project_id is not None
        want = f"/api/v2/{endpoint(name, self.project_id)}"
        base = urlsplit(self.base)

        def resolve(link: Any) -> str:
            if not isinstance(link, str) or not link:
                raise _bad("a next link is not text")
            if link.startswith("/index.php?"):
                rest = link[len("/index.php?"):]
            elif link.startswith("/api/v2/"):
                rest = link
            elif "://" in link:
                got = urlsplit(link)
                if (got.scheme.lower(), got.netloc.lower()) != (base.scheme.lower(), base.netloc.lower()):
                    raise SourceError("the list points at another host for its next page; not followed")
                if got.path != "/index.php":
                    raise _bad("a next link is not an address on the instance")
                rest = got.query
            else:
                raise _bad("a next link is not an address on the instance")
            if rest != want and not rest.startswith(want + "&"):
                raise _bad("a next link names another endpoint or project than the list being read")
            return f"{self.base}/index.php?{rest}"

        return resolve

    def _pages(self, client: Client, url: str, key: str, cap: int, name: str) -> tuple[list[dict[str, Any]], str]:
        resolve = self._resolver(name)

        def items_of(data: Any) -> list:
            return self._entities(data, key)[0]

        def next_of(data: Any, _headers: Any) -> str | None:
            link = self._entities(data, key)[1]
            return resolve(link) if link else None

        items, more = client.pages(url, items_of, next_of, cap, key)
        if more:
            return items, f"{key}: read {len(items)} in {cap} page(s) and the list goes on (max_pages {cap})"
        return items, ""

    @staticmethod
    def _entities(data: Any, key: str) -> tuple[list[dict[str, Any]], Any]:
        if isinstance(data, list):  # a server older than the paginated API: the whole list in one answer
            entities, link = data, None
        elif isinstance(data, dict):
            entities = data.get(key)
            links = data.get("_links")
            if links is not None and not isinstance(links, dict):
                raise _bad("_links is not an object")
            link = (links or {}).get("next")
        else:
            raise _bad(f"the list of {key} is not an object or a list")
        if not isinstance(entities, list) or not all(isinstance(e, dict) for e in entities):
            raise _bad(f"the {key} are not a list of objects")
        return entities, link

    @staticmethod
    def _int(obj: Mapping[str, Any], key: str) -> int:
        value = obj.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise _bad(f"{key} of a section or case is not a number")
        return value

    @classmethod
    def _opt_int(cls, obj: Mapping[str, Any], key: str) -> int | None:
        return None if obj.get(key) is None else cls._int(obj, key)
