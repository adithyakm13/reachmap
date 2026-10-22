"""The http edge: client calls to server routes, both directions (context 7.4).

A client repository (`http_client`, the endpoints-file adapter) calls URLs; a server repository (entry points: FastAPI)
serves routes with their full mounted paths. The edge is declared `from` the client `to` the server.

- Client change (a frontend change reaches a client call): the call is matched to the route at the server's primary
  branch, and the route's handler modules are listed as backend calls. Nothing in the server changed, so the hop ends
  there.
- Server change (a backend change reaches a route): every client call whose URL fits the route best is found in the
  client's primary branch, and the client line is handed to the client repository's own pass (its callers, its screens).
  A removed route that a client still names, and an added route no client names, are findings.

Matching (context 7.4): URLs are segments; a client parameter segment fits only a route parameter segment; a client
literal may fit a route parameter (`literal_in_parameter`, said); the route with the most literal segments equal to the
client's wins; a tie is ambiguous and every tied route is kept with a note. Query strings are ignored, trailing slashes
normalised. A client URL that cannot be evaluated statically is a candidate (by name): recorded as `client_url_partial`
and never matched silently. A client call whose method cannot be told matches any method (`method_unknown` on the edge).
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from typing import Any, Iterable

from reachmap.adapters import Route, guard_route_changes
from reachmap.adapters.base import evidence_at, record_quoted
from reachmap.chains import crossing_prefix
from reachmap.adapters.http_client import ClientCall, ClientUrl, LIT, MIXED, PARAM, WILD
from reachmap.edges import FindingInput, Hop, Pin, Seed, edge_incomplete, incomplete_hop
from reachmap.incomplete import record
from reachmap.model import Evidence

__all__ = ["BackendCall", "BackendModule", "HttpEdgeAdapter", "HttpMatch", "RouteMatch", "best_matches", "match_url"]

_RE_PARAM = re.compile(r"^\{[^{}]*\}$")
_RE_REST = re.compile(r"^\{[^{}:]*:path\}$")
UNKNOWN_SEG = "<unknown"


# ------------------------------------------------------------------------------ matching


@dataclass(frozen=True, slots=True)
class RouteMatch:
    """How a client URL fits one route: `literals` is the number of route segments that are literals equal to the
    client's, `kind` is `exact` or `literal_in_parameter` (a client literal fits a route parameter), `partial` says the
    URL or the route has a part that is not known (the match is by name only), `fallback` that the call's method fits no route of its path and this
    route, of another method, is kept with a note."""

    route: Route
    kind: str
    literals: int
    partial: bool
    fallback: bool = False  # the call's method fits no route on this path: this route is of another method

    @property
    def key(self) -> tuple[str, str]:
        return (self.route.method, self.route.path)


def _route_segments(path: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for part in path.strip("/").split("/"):
        if not part:
            continue
        if _RE_REST.match(part):
            out.append(("rest", part))
        elif _RE_PARAM.match(part):
            out.append(("param", part))
        elif UNKNOWN_SEG in part:
            out.append(("unknown", part))
        else:
            out.append(("lit", part))
    return out


def match_url(url: ClientUrl, route_path: str) -> tuple[str, int, bool] | None:
    """How a client URL fits a route path: (kind, literals, partial), or None when it does not fit."""
    c, r = list(url.segments), _route_segments(route_path)
    memo: dict[tuple[int, int], tuple[int, int, bool] | None] = {}

    def better(a: tuple[int, int, bool] | None, b: tuple[int, int, bool] | None) -> tuple[int, int, bool] | None:
        if a is None:
            return b
        if b is None:
            return a
        return b if (b[0], -b[1], not b[2]) > (a[0], -a[1], not a[2]) else a

    def go(i: int, j: int) -> tuple[int, int, bool] | None:
        key = (i, j)
        if key in memo:
            return memo[key]
        memo[key] = None
        if i == len(c):
            out: tuple[int, int, bool] | None = (0, 0, False) if j == len(r) else None
        else:
            cs = c[i]
            best: tuple[int, int, bool] | None = None
            if cs.kind == WILD:  # an unknown part standing for any run of segments (also none)
                for k in range(j, len(r) + 1):
                    sub = go(i + 1, k)
                    if sub is not None:
                        best = better(best, (sub[0], sub[1], True))
            elif j < len(r):
                kind, _text = r[j]
                if kind == "rest":  # `{p:path}` takes this segment and every one after it up to the end of what the client names
                    for m in range(1, len(c) - i + 1):
                        sub = go(i + m, j + 1)
                        if sub is not None:
                            lits = sum(1 for s in c[i:i + m] if s.kind == LIT)
                            best = better(best, (sub[0], sub[1] + lits, sub[2]))
                else:
                    step: tuple[int, int, bool] | None = None
                    if cs.kind == LIT:
                        if kind == "lit":
                            step = (1, 0, False) if r[j][1] == cs.text else None
                        elif kind == "param":
                            step = (0, 1, False)  # literal_in_parameter
                        else:
                            step = (0, 0, True)
                    elif cs.kind == PARAM:
                        step = (0, 0, False) if kind == "param" else (0, 0, True) if kind == "unknown" else None
                    elif cs.kind == MIXED:
                        step = (0, 0, True)
                    if step is not None:
                        sub = go(i + 1, j + 1)
                        if sub is not None:
                            best = (step[0] + sub[0], step[1] + sub[1], step[2] or sub[2])
            out = best
        memo[key] = out
        return out

    found = go(0, 0)
    if found is None:
        return None
    lits, lip, partial = found
    return ("literal_in_parameter" if lip else "exact", lits, partial or url.partial)


def best_matches(url: ClientUrl, method: str | None, routes: Iterable[Route]) -> list[RouteMatch]:
    """The routes a client URL fits best: those with the most literal segments equal to the client's. A URL with an
    unknown part has no best: every route it can fit is returned, all as partial (candidates). With no method, routes
    of every method compete, so a path served by two methods is a tie (kept, with a note)."""
    table = [r for r in routes if r.kind == "api_route"]
    found = _fits(url, method, table)
    if not found and method is not None:  # no route of that method on the path: the method may be misread, so every method competes
        found = [dataclasses.replace(m, fallback=True) for m in _fits(url, None, table)]
    if not found:
        return []
    if url.partial:
        return sorted(found, key=lambda m: (m.route.path, m.route.method))
    top = max(m.literals for m in found)
    return sorted((m for m in found if m.literals == top), key=lambda m: (m.route.path, m.route.method))


def _fits(url: ClientUrl, method: str | None, routes: list[Route]) -> list[RouteMatch]:
    out: list[RouteMatch] = []
    for route in routes:
        if method is not None and route.method.upper() not in (method.upper(), "ANY"):  # ANY: a route an entry-point adapter says answers every method
            continue
        m = match_url(url, route.path)
        if m is not None:
            out.append(RouteMatch(route, m[0], m[1], m[2] or route.path_approximate))  # a route whose path reads a pattern that was not evaluated (a regex) fits by name only
    return out


# ------------------------------------------------------------------------------ results


@dataclass(frozen=True, slots=True)
class HttpMatch:
    """A client call matched to a route: `client` is the client line, `via` the lines that define the URL, `route` the
    route with `route.evidence` (the decorator, then each mount line from the router outwards). `direction` is
    `to_server` (a client change reaches the call) or `to_client` (a server change reaches the clients of the route).
    `removed` marks a route the change removes (its evidence is at the base); `rematched` is the route that answers
    the URL after the change, if any. `candidate` marks a match by name (a partial client URL or route)."""

    direction: str
    client_repo: str
    server_repo: str
    client: Evidence
    via: tuple[Evidence, ...]
    route: Route
    method: str | None
    kind: str
    ambiguous_with: tuple[str, ...] = ()
    removed: bool = False
    rematched: Route | None = None
    candidate: bool = False
    url: str = ""
    note: str = ""
    fallback: bool = False  # the call's method fits no route of the path; this route is of another method

    @property
    def method_unknown(self) -> bool:
        return self.method is None or self.fallback

    @property
    def literal_in_parameter(self) -> bool:
        return self.kind == "literal_in_parameter"

    @property
    def ambiguous(self) -> bool:
        return bool(self.ambiguous_with)

    def all_evidence(self) -> tuple[Evidence, ...]:
        return (self.client, *self.via, *self.route.evidence)


@dataclass(frozen=True, slots=True)
class BackendModule:
    """A module a handler's file imports, directly or through others: `depth` 1 is the handler's own imports."""

    path: str
    depth: int
    imported_by: str
    evidence: Evidence


@dataclass(frozen=True, slots=True)
class BackendCall:
    """For a frontend change: the route a call reaches and the modules its handler runs (up to three hops of imports
    in the server repository). `complete` is False when the modules could not be listed whole (`reasons`)."""

    client: Evidence
    route: Route
    handler: str
    modules: tuple[BackendModule, ...]
    complete: bool = True
    reasons: tuple[str, ...] = ()

    def all_evidence(self) -> tuple[Evidence, ...]:
        return (self.client, *self.route.evidence, *(m.evidence for m in self.modules))


# ------------------------------------------------------------------------------ the adapter


def _route_label(route: Route) -> str:
    return f"{route.method} {route.path}"


def _table_state(entry: Any, sha: str) -> tuple[bool, str]:
    """Whether an entry-point adapter read its route table at `sha` completely, and why not: what the adapter says of that version
    (`routes_complete`), else, as it cannot tell the versions apart, whether it has any problem at all."""
    fn = getattr(entry, "routes_complete", None)
    if fn is not None:
        ok, why = fn(sha)
        return bool(ok), "; ".join(why)
    problems = list(getattr(entry, "problems", ()))
    return not problems, "; ".join(problems)


def _entry(run: Any, repo: str) -> Any | None:
    entry = run.entry(repo)
    if entry is None or not hasattr(entry, "routes"):
        return None
    return entry


class HttpEdgeAdapter:
    kind = "http"

    # ---- which side

    def applies(self, run: Any, p: Any, edge: Any) -> bool:
        if p.repo == edge.to_repo:  # the pass is in the server
            if any(r.area.kind == "api_route" and not r.area.by_name for r in p.reached):
                return True
            removed, added = self._changed_routes(run, p)
            return p.depth == 0 and p.seed is None and bool(removed or added)
        if p.repo == edge.from_repo:  # the pass is in the client: only the change itself reaches backend calls
            if p.depth != 0 or p.seed is not None:
                return False
            # a client repository with no client configured still has something to say about the edge (follow says it)
            return run.client(p.repo) is not None or any(i.role == "source" and i.symbols for i in p.analysis.impacts)
        return False

    def follow(self, run: Any, p: Any, edge: Any) -> list[Hop]:
        if p.repo == edge.to_repo:
            return self._to_clients(run, p, edge)
        return self._to_server(run, p, edge)

    # ---- removed and added routes

    def _changed_routes(self, run: Any, p: Any) -> tuple[list[Route], list[Route]]:
        def make() -> tuple[list[Route], list[Route]]:
            entry = _entry(run, p.repo)
            pre = getattr(p.change, "pre_change_sha", None)
            if entry is None or pre is None or p.seed is not None:
                return [], []  # without a pre-change version nothing is removed or added by a claim (the change set said so)
            removed, added = entry.routes_changed(pre, p.change.head_sha)
            if (removed or added) and not getattr(entry, "withholds_unread", False):  # an adapter that does not guard itself is guarded here
                base_ok, base_why = _table_state(entry, pre)
                head_ok, head_why = _table_state(entry, p.change.head_sha)
                why = "; ".join(w for ok, w in ((base_ok, base_why), (head_ok, head_why)) if not ok and w)[:300]
                removed, added = guard_route_changes(run.target, p.repo, removed, added, base_ok=base_ok, head_ok=head_ok, why=why)
            return removed, added
        return run.memo(("routes-changed", p.repo, p.sha), make)

    def _fail(self, run: Any, edge: Any, p: Any, why: str) -> Hop:
        return incomplete_hop(run.target, edge, p.repo, p.depth, [why])

    # ---- the server changed: its clients

    def _to_clients(self, run: Any, p: Any, edge: Any) -> list[Hop]:
        server, client_repo = p.repo, edge.from_repo
        pin = run.primary(client_repo)
        if pin is None:
            return [self._fail(run, edge, p, f"the primary branch of {client_repo} could not be read: its calls to {server} were not found")]
        adapter = run.client(client_repo)
        if adapter is None:
            return [self._fail(run, edge, p, f"{client_repo} has no http client configured: its calls to {server} were not found")]
        entry = _entry(run, server)
        if entry is None:
            return [self._fail(run, edge, p, f"{server} has no entry-point adapter that lists routes: the calls to it were not matched")]
        calls = adapter.calls(pin.sha)
        reasons: list[str] = list(calls.reasons)
        table = entry.routes(None, p.sha)
        if getattr(entry, "problems", None):
            reasons += [f"the routes of {server} were not read completely: {why}" for why in entry.problems]
        self._record_partial(run, calls.calls)
        origin = p.depth == 0 and p.seed is None
        removed, added = self._changed_routes(run, p) if origin else ([], [])
        reached = {(r.routes[0].method, r.routes[0].path): (r, r.routes[0]) for r in p.reached if r.area.kind == "api_route" and not r.area.by_name and r.routes}
        base_table = entry.routes(None, p.change.pre_change_sha) if removed else []
        gone = {(rt.method, rt.path): rt for rt in removed}
        matched_added: set[tuple[str, str]] = set()
        unmatched: list[ClientCall] = []
        hops: list[Hop] = []
        for call in calls.calls:
            best = best_matches(call.url, call.method, table)
            if not best:
                unmatched.append(call)
            for m in best:
                if any(m.key == (a.method, a.path) for a in added):
                    matched_added.add(m.key)
                hit = reached.get(m.key)
                if hit is not None:
                    hops.append(self._reverse_hop(run, p, edge, pin, call, m, hit[1], hit[0], best))
            if gone:
                base_best = best_matches(call.url, call.method, base_table)
                for m in base_best:
                    old = gone.get(m.key)
                    if old is not None and not m.fallback:
                        now = [x for x in best if x.key != m.key]
                        hops.append(self._removed_hop(run, p, edge, pin, call, RouteMatch(old, m.kind, m.literals, m.partial), base_best, now))
        if added and unmatched:  # a call that fits no route may be the new one under a prefix the match does not know
            for c in unmatched:
                self._record_unmatched(run, c, "an added route may be what it calls")
            reasons.append(f"{len(unmatched)} client call(s) of {client_repo} fit no route of {server} (first: {unmatched[0].path}:{unmatched[0].line}): no added route is called uncalled")
        if reasons:
            for why in reasons:
                edge_incomplete(run.target, edge, why)
            hops = [dataclasses.replace(h, complete=False, reasons=tuple(dict.fromkeys(h.reasons + tuple(reasons)))) for h in hops]
            if not hops:
                hops.append(incomplete_hop(run.target, edge, p.repo, p.depth, reasons))
        self._remember_uncalled(run, p, edge, added, matched_added, not reasons)
        return hops

    @staticmethod
    def _record_partial(run: Any, calls: Iterable[ClientCall]) -> None:
        for c in calls:
            if c.partial:
                unknown = "; ".join(s.text for s in c.url.segments if s.kind in (WILD, MIXED))
                record_quoted(run.target, "client_url_partial", f"{c.repo}:{c.path}:{c.line}",
                              "part of the URL cannot be evaluated statically; the call is a candidate for every route it could fit, and is never matched silently",
                              unknown, once=True)

    @staticmethod
    def _record_unmatched(run: Any, c: ClientCall, why: str) -> None:
        record_quoted(run.target, "client_url_unmatched", f"{c.repo}:{c.path}:{c.line}",
                      f"the call to {c.url.text} fits no route of the server ({why})", "", once=True)

    def _reverse_hop(self, run: Any, p: Any, edge: Any, pin: Pin, call: ClientCall, m: RouteMatch, route: Route, reached: Any,
                     best: list[RouteMatch]) -> Hop:
        tied = tuple(_route_label(x.route) for x in best if x.key != m.key)
        match = HttpMatch(
            "to_client", edge.from_repo, p.repo, call.evidence, call.via, route, call.method, m.kind, tied, candidate=m.partial,
            url=call.url.text, note=_note(call, m, tied), fallback=m.fallback,
        )
        run.http_edges.append(match)
        chain = (*crossing_prefix(run, p, reached), call.evidence)
        seed = Seed(edge.from_repo, call.path, (call.line,), pin.sha, (call.evidence,))
        return Hop(
            "http", p.repo, edge.from_repo, (edge.from_repo, edge.to_repo), "reverse", p.depth + 1, (pin,), route.evidence,
            (call.evidence, *call.via), chain, (seed,), detail=match.note, candidate=m.partial, note=match.note,
        )

    def _removed_hop(self, run: Any, p: Any, edge: Any, pin: Pin, call: ClientCall, m: RouteMatch, base_best: list[RouteMatch],
                     now: list[RouteMatch]) -> Hop:
        tied = tuple(_route_label(x.route) for x in base_best if x.key != m.key)
        rematched = now[0] if now else None
        if rematched is not None:
            fits = f"{_route_label(rematched.route)} ({rematched.kind}" + ("; method_unknown: other methods fit" if rematched.fallback or call.method is None else "") + ")"
            after = f"; after the change the URL fits {fits}"
            note = f"the route is removed by the change; the URL now fits {fits}"
        else:
            after = "; after the change the URL fits no route"
            note = "the route is removed by the change; the URL fits no route after the change"
        match = HttpMatch(
            "to_client", edge.from_repo, p.repo, call.evidence, call.via, m.route, call.method, m.kind, tied, removed=True,
            rematched=rematched.route if rematched else None, candidate=m.partial, url=call.url.text, note=note,
        )
        run.http_edges.append(match)
        # a route narrowed from every method to some (or one method split from another) is removed as a (method, path) pair, but the call still fits the same
        # path with its own method: a call that still has its answer is not a removed route still called
        answered = call.method is not None and any(x.route.path == m.route.path and not x.fallback for x in now)
        if not m.partial and not answered:  # the route was this call's best match: whatever answers the URL now was not written for it
            run.findings.append(FindingInput(
                "removed_route_still_called", "high", "low",
                "the client file is read from its primary branch and nothing shows that the call is reached at run time",
                (call.evidence, m.route.evidence[0]),
                ("removed_route_still_called", p.repo, m.route.method, m.route.path, edge.from_repo, call.path, call.name, call.method or ""),
                f"{_route_label(m.route)} is removed and {call.path} still names its URL{after}",
            ))
        seed = Seed(edge.from_repo, call.path, (call.line,), pin.sha, (call.evidence,))
        return Hop(
            "http", p.repo, edge.from_repo, (edge.from_repo, edge.to_repo), "reverse", p.depth + 1, (pin,), m.route.evidence,
            (call.evidence, *call.via), (*p.prefix, call.evidence), (seed,), detail=match.note, candidate=m.partial, note=match.note,
        )

    def _remember_uncalled(self, run: Any, p: Any, edge: Any, added: list[Route], matched: set[tuple[str, str]], readable: bool) -> None:
        """An added route no client names is said, but only once every client edge into the server has been read
        completely and no client call fit nothing: a call that could not be looked for is not an absence."""
        if not added:
            return
        state = run.memo(("uncalled", p.repo, p.sha), lambda: {"done": 0, "ok": True, "matched": set()})
        state["done"] += 1
        state["ok"] = state["ok"] and readable
        state["matched"] |= matched
        total = sum(1 for e in run.workspace.edges if e.kind == "http" and e.to_repo == p.repo)
        if state["done"] < total or not state["ok"]:
            return
        for a in added:
            if (a.method, a.path) in state["matched"]:
                continue
            run.findings.append(FindingInput(
                "added_route_uncalled", "low", "medium",
                "only the client repositories of this workspace were read, at their primary branches",
                (a.evidence[0],), ("added_route_uncalled", p.repo, a.method, a.path),
                f"{_route_label(a)} is new and no client of the workspace names its URL yet",
            ))

    # ---- a client changed: the routes it calls

    def _to_server(self, run: Any, p: Any, edge: Any) -> list[Hop]:
        client_repo, server = p.repo, edge.to_repo
        adapter = run.client(client_repo)
        if adapter is None:
            return [self._fail(run, edge, p, f"{client_repo} has no http client configured: the routes its changed code calls were not found")]
        pin = run.primary(server)
        if pin is None:
            return [self._fail(run, edge, p, f"the primary branch of {server} could not be read: the routes its calls reach were not found")]
        entry = _entry(run, server)
        if entry is None:
            return [self._fail(run, edge, p, f"{server} has no entry-point adapter that lists routes: the calls to it were not matched")]
        calls = adapter.calls(p.sha)
        reasons = list(calls.reasons)
        table = entry.routes(None, pin.sha)
        if getattr(entry, "problems", None):
            reasons += [f"the routes of {server} were not read completely: {why}" for why in entry.problems]
        reached = self._reached_calls(run, p, calls.calls)
        self._record_partial(run, reached)
        hops: list[Hop] = []
        for call in reached:
            best = best_matches(call.url, call.method, table)
            if not best:
                self._record_unmatched(run, call, "a base URL or a proxy prefix the routes do not have?")
            for m in best:
                tied = tuple(_route_label(x.route) for x in best if x.key != m.key)
                match = HttpMatch(
                    "to_server", client_repo, server, call.evidence, call.via, m.route, call.method, m.kind, tied, candidate=m.partial,
                    url=call.url.text, note=_note(call, m, tied), fallback=m.fallback,
                )
                run.http_edges.append(match)
                run.backend_calls.append(self._backend(run, server, pin.sha, call, m.route))
                hops.append(Hop(
                    "http", client_repo, server, (edge.from_repo, edge.to_repo), "forward", p.depth + 1, (pin,), (call.evidence, *call.via),
                    m.route.evidence, (call.evidence,), (), detail=match.note, candidate=m.partial, note=match.note,
                    complete=not reasons, reasons=tuple(reasons),
                ))
        if reasons:
            for why in reasons:
                edge_incomplete(run.target, edge, why)
            if not hops:
                hops.append(incomplete_hop(run.target, edge, p.repo, p.depth, reasons))
        return hops

    def _reached_calls(self, run: Any, p: Any, calls: Iterable[ClientCall]) -> list[ClientCall]:
        """The client calls the change reaches: those in a changed declaration, in a declaration that uses one (the
        closure inside the file), in a changed file when a statement at module level changed, and in the files the walk
        reached, in the declarations that carry the change (or anywhere in a file the whole module of which is reached)."""
        lang = run.language(p.repo)
        sha = p.sha
        wanted: dict[str, set[str] | None] = {}  # path: the declaration names reached, None for every call in the file

        def add(path: str, names: set[str] | None) -> None:
            if path in wanted and wanted[path] is None:
                return
            if names is None:
                wanted[path] = None
            else:
                wanted[path] = (wanted.get(path) or set()) | names

        for impact in p.analysis.impacts:
            if impact.role == "test" or not impact.symbols:
                continue
            if impact.status == "deleted":
                continue
            if any(s.is_module for s in impact.symbols):
                add(impact.path, None)
                continue
            seeds = {s.name.split(".")[0] for s in impact.symbols if s.change != "removed"}
            names = set(seeds)
            own = lang.own_reach(impact.path, sha, seeds)
            if own.whole or not own.complete:
                add(impact.path, None)
            else:
                names |= set(own.locals)
                add(impact.path, names)
        if p.reach is not None:
            for v in p.reach.walk.visited:
                if v.sha != sha:
                    continue
                add(v.file, None if (v.reach.whole or not v.reach.complete) else set(v.reach.locals))
        out = [c for c in calls if c.path in wanted and (wanted[c.path] is None or c.enclosing in (wanted[c.path] or set()))]
        return sorted(out, key=lambda c: (c.path, c.line, c.name))

    # ---- backend calls

    def _backend(self, run: Any, server: str, sha: str, call: ClientCall, route: Route) -> BackendCall:
        lang = run.language(server)
        if not route.target:
            return BackendCall(call.evidence, route, route.file, (), False, (f"the handler of {_route_label(route)} is not known, so the modules it runs were not listed",))
        if not hasattr(lang, "resolve_module"):
            return BackendCall(call.evidence, route, route.target, (), False, ("the server's language adapter does not list imports",))
        modules: list[BackendModule] = []
        reasons: list[str] = []
        seen = {route.target}
        frontier = [route.target]
        cap = run.limits.module_cap
        for depth in range(1, 4):
            nxt: list[str] = []
            level: list[BackendModule] = []
            for path in sorted(frontier):
                parsed = lang.parsed(sha, path)
                if parsed is None:
                    reasons.append(f"{path} could not be read: what it imports was not listed")
                    continue
                if parsed.problems:
                    reasons.append(f"{path} did not parse ({parsed.problems[0]}): what it imports was not listed")
                    continue
                for rec in parsed.imports:
                    targets: list[str] = []
                    for f in lang.resolve_module(path, sha, rec.module, rec.level) if (rec.module or rec.level) else ():
                        targets.append(f)
                    for n in rec.names:
                        base = f"{rec.module}.{n.name}" if rec.module else n.name
                        targets += [f for f in lang.resolve_module(path, sha, base, rec.level)]
                    ev = evidence_at(lang.repo, path, sha, parsed, rec.line)
                    for t in sorted(set(targets)):
                        if t in seen or lang.is_test_path(t):
                            continue
                        if ev is None:
                            continue
                        seen.add(t)
                        level.append(BackendModule(t, depth, path, ev))
                        nxt.append(t)
            if len(level) > cap:  # the cap is per depth, as the walk's is per hop, and has a subject of its own
                record(run.target, "module_cap", f"backend:{server}:depth:{depth}",
                       detail=f"the handler of {_route_label(route)} runs {len(level)} modules at depth {depth}; the limit is {cap}, so the list stops here")
                reasons.append(f"the list of modules the handler of {_route_label(route)} runs was cut at {cap} at depth {depth}")
                modules += level[:cap]
                break
            modules += level
            frontier = nxt
        return BackendCall(call.evidence, route, route.target or route.file, tuple(modules), not reasons, tuple(reasons))


def _note(call: ClientCall, m: RouteMatch, tied: tuple[str, ...]) -> str:
    notes = []
    if call.method is None:
        notes.append("method_unknown: the call's method could not be told, so any method matches")
    elif m.fallback:
        notes.append(f"method_unknown: the call's method ({call.method}) fits no route of this path, so the routes of the other methods that fit are kept")
    if m.kind == "literal_in_parameter":
        notes.append("literal_in_parameter: a literal segment of the URL fits a parameter of the route")
    if tied:
        notes.append("ambiguous: " + ", ".join(tied) + " fit equally well")
    if m.partial:
        notes.append("candidate: a part of the URL or of the route is not known, the match is by name")
    return "; ".join(notes)
