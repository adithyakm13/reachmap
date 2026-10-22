"""HTTP client adapters: the calls a frontend (or any client) repository makes to a server, as URLs with evidence.

A client adapter answers one question for a repository at a pinned commit: which lines of code call which URL, with
which method (when it can be told), and how the URL is made (the lines that define it). It never says "no calls" for
a failure: the answer carries `complete` and the reasons, and a URL that cannot be evaluated statically is kept as a
partial URL (never matched silently, never dropped).

A URL is a list of segments (`ClientUrl`): a literal segment, a parameter (`${id}` in a builder), a wildcard (an
unknown part that stands for any run of segments) or a mixed segment (an unknown part inside one segment).
The matching against server routes is the edge adapter's (`reachmap.edges.http`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol, Sequence

from reachmap.model import Evidence

__all__ = ["ClientCall", "ClientCalls", "ClientUrl", "HttpClientAdapter", "Seg", "client_adapter", "register", "url_from_parts"]

LIT, PARAM, WILD, MIXED = "lit", "param", "wild", "mixed"
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")


@dataclass(frozen=True, slots=True)
class Seg:
    """One URL segment. `lit`: a fixed string; `param`: a segment with a substituted parameter (it fits only a route
    parameter); `wild`: an unknown part on its own, standing for any run of segments (also none); `mixed`: an unknown
    part inside one segment, standing for exactly one segment of anything. `text` is the literal text, the parameter
    name or the unknown expression."""

    kind: str
    text: str = ""


@dataclass(frozen=True, slots=True)
class ClientUrl:
    """A client URL as segments, without its query string or fragment and without a trailing slash. `partial` is
    True when some part could not be evaluated statically."""

    segments: tuple[Seg, ...]
    partial: bool = False

    @property
    def text(self) -> str:
        parts = []
        for s in self.segments:
            parts.append(s.text if s.kind == LIT else "{}" if s.kind == PARAM else f"<unknown:{s.text}>")
        return "/" + "/".join(parts)


def url_from_parts(parts: Sequence[tuple[str, str]]) -> ClientUrl:
    """The URL a list of atoms spells: `("lit", text)`, `("param", name)` and `("unknown", expression)`. The query
    string and fragment (the first `?` or `#` of the literal text, and everything after it) are dropped; empty
    segments (a leading, doubled or trailing slash) are dropped, so `/a/b/` and `/a//b` are `/a/b`."""
    done: list[list[tuple[str, str]]] = []
    cur: list[tuple[str, str]] = []

    def flush() -> None:
        nonlocal cur
        if cur:
            done.append(cur)
        cur = []

    stopped = False
    for kind, text in parts:
        if stopped:
            break
        if kind != "lit":
            cur.append((kind, text))
            continue
        piece = ""
        for ch in text:
            if ch in "?#":
                stopped = True
                break
            if ch == "/":
                if piece:
                    cur.append(("lit", piece))
                    piece = ""
                flush()
            else:
                piece += ch
        if piece:
            cur.append(("lit", piece))
    flush()
    segs: list[Seg] = []
    partial = False
    for atoms in done:
        kinds = [k for k, _t in atoms]
        if all(k == "lit" for k in kinds):
            segs.append(Seg(LIT, "".join(t for _k, t in atoms)))
        elif "unknown" in kinds:
            partial = True
            unknown = [t for k, t in atoms if k == "unknown"]
            segs.append(Seg(WILD if len(atoms) == 1 else MIXED, " + ".join(unknown)))
        else:
            segs.append(Seg(PARAM, next(t for k, t in atoms if k == "param")))
    return ClientUrl(tuple(segs), partial)


@dataclass(frozen=True, slots=True)
class ClientCall:
    """One line of client code that uses an endpoint (a constant, or a builder that it calls).

    `evidence` is that line; `via` the lines that define the URL (the declaration of the constant or builder, and of
    the constants it is made of); `method` is the HTTP method the call makes, or None when it cannot be told
    (`method_unknown`: it then matches any method); `name` is the endpoint's name in the endpoints file and `local` the
    name the call site uses for it. `tok` is the position of the use in the file's tokens (for adapters that need it)."""

    repo: str
    path: str
    line: int
    name: str
    local: str
    url: ClientUrl
    method: str | None
    evidence: Evidence
    via: tuple[Evidence, ...]
    enclosing: str = "<module>"  # the top-level declaration the call sits in
    form: str = "reference"  # call (a builder call) | reference (a constant)

    @property
    def method_unknown(self) -> bool:
        return self.method is None

    @property
    def partial(self) -> bool:
        return self.url.partial


@dataclass(frozen=True, slots=True)
class ClientCalls:
    """Every call of a repository at a commit. `complete` is False when the list could not be established whole (an
    unreadable endpoints file, an importer list that was cut, a file that did not parse); `reasons` says why. A short
    list is never "the" list then."""

    repo: str
    sha: str
    calls: tuple[ClientCall, ...]
    complete: bool = True
    reasons: tuple[str, ...] = ()
    endpoints: int = 0  # how many endpoints the endpoints file defines


class HttpClientAdapter(Protocol):
    kind: str

    def calls(self, sha: str) -> ClientCalls: ...


Factory = Callable[..., Any]
_FACTORIES: dict[str, Factory] = {}


def register(kind: str, factory: Factory) -> None:
    """Add a client kind: `factory(repo_config, language_adapter)` returns the adapter."""
    _FACTORIES[kind] = factory


def client_adapter(repo: Any, language: Any) -> HttpClientAdapter | None:
    """The client adapter a repository's `http_client` config selects; None when it declares none (a configuration
    answer: it has no client side, not a failed read)."""
    kind = (repo.http_client or {}).get("kind")
    factory = _FACTORIES.get(kind) if isinstance(kind, str) else None
    return factory(repo, language) if factory is not None else None


def _endpoints_file(repo: Any, language: Any) -> Any:
    from reachmap.adapters.http_client.endpoints_file import EndpointsFileClient

    return EndpointsFileClient.from_config(repo, language)


def _openapi(repo: Any, language: Any) -> Any:
    from reachmap.adapters.openapi.client import OpenApiClient

    return OpenApiClient.from_config(repo, language)


register("endpoints-file", _endpoints_file)
register("openapi", _openapi)
