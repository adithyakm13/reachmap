"""Edge guesses between repositories: package, contract, http and event.

A `package` edge needs the consumer's manifest to pin the producer's package: a dependency of the consumer whose normalised name is
a package the producer declares, with an exact version (`==`, or a number in package.json). The `pin_pattern` is written for the
exact manifest line, checked to compile, to have one group, and to read a version (`versions.parse`) from that line. A `contract`
edge goes with a package edge whose producer has a contract table and whose consumer is Python. An `http` edge goes from a repository
with an HTTP client to a repository that serves routes (evidence: the client and the routes; a matching URL segment makes it
medium, none makes it low). An `event` edge is guessed from a Celery worker with a module of topic names to the repositories that
serve routes; no adapter follows events, so it only records that the link exists.
"""

from __future__ import annotations

import re
from typing import Sequence

from reachmap import versions
from reachmap.init.guess import Dep, EdgeGuess, Note, RepoGuess
from reachmap.init.tree import line_of

__all__ = ["guess_edges", "pin_pattern"]

GENERIC_SEGMENTS = frozenset({"api", "v1", "v2", "v3", "rest", "public", "internal", "app", "service", "services"})
SERVER_KINDS = ("fastapi", "django", "nextjs", "openapi", "express", "nestjs", "spring")
_MANIFEST_ORDER = (r"^requirements\.txt$", r"(^|/)requirements[\w.-]*\.txt$", r"(^|/)pyproject\.toml$", r"(^|/)package\.json$")


def _escape(name: str) -> str:
    return re.sub(r"([.^$*+?{}\[\]|()\\])", r"\\\1", name)


def _name_as_written(dep: Dep) -> str:
    """The dependency name as the manifest line spells it (the pattern must match that spelling; a one line array holds several)."""
    return dep.spelling or dep.name


def pin_pattern(dep: Dep) -> str | None:
    """A regular expression with one group that reads the version out of the manifest line of `dep`, or None when the line has a shape
    this does not write a pattern for or the pattern does not read a version from the line (checked, not assumed)."""
    name_raw = _name_as_written(dep)
    name = _escape(name_raw)
    manifest = dep.manifest.rsplit("/", 1)[-1]
    if dep.ecosystem == "npm":
        pattern = rf'"{name}"\s*:\s*"[\^~=v]*([^"]+)"'
    elif manifest.endswith(".txt"):
        pattern = rf"^\s*{name}\s*(?:\[[^\]]*\])?\s*===?\s*([^\s;#,]+)"
    elif manifest == "pyproject.toml" and re.search(rf"[\"']{re.escape(name_raw)}(?![\w.-])", dep.text):
        pattern = rf"[\"']{name}\s*(?:\[[^\]]*\])?\s*===?\s*([^\s\"';,]+)"
    elif manifest == "pyproject.toml" and re.match(rf"^\s*{name}\s*=\s*\"", dep.text):
        pattern = rf'^\s*{name}\s*=\s*"[\^~=]*([^"]+)"'
    else:
        return None
    try:
        compiled = re.compile(pattern)
    except re.error:
        return None
    m = compiled.search(dep.text)
    if compiled.groups != 1 or m is None or not (m.group(1) or "").strip():
        return None
    return pattern if versions.parse(m.group(1).strip()) is not None else None


def _manifest_rank(path: str) -> int:
    for i, pattern in enumerate(_MANIFEST_ORDER):
        if re.search(pattern, path):
            return i
    return len(_MANIFEST_ORDER)


def _at(repo: str, where: str) -> str:
    """Evidence in a file of a named repository: `acme/api:requirements.txt:3` (an edge has two repositories, so the path alone is not enough)."""
    return f"{repo}:{where}"


def guess_edges(guesses: Sequence[RepoGuess]) -> tuple[list[EdgeGuess], list[str]]:
    """The edges between the repositories, and comment lines about links that were seen but could not be written."""
    edges: list[EdgeGuess] = []
    notes: list[str] = []
    by_name = {g.name: g for g in guesses}

    # ---- package and contract
    provided: dict[str, list[tuple[RepoGuess, tuple[str, str, str]]]] = {}
    for g in guesses:
        for key, value in g.facts.provides.items():
            provided.setdefault(key, []).append((g, value))
    package_edges: list[EdgeGuess] = []
    for consumer in guesses:
        by_producer: dict[str, list[tuple[Dep, tuple[str, str, str]]]] = {}
        for dep in consumer.facts.deps:
            for producer, info in provided.get(f"{dep.ecosystem}:{dep.name}", []):
                if producer.name != consumer.name:
                    by_producer.setdefault(producer.name, []).append((dep, info))
        for producer_name, found in by_producer.items():
            producer = by_name[producer_name]
            found.sort(key=lambda t: (_manifest_rank(t[0].manifest), t[0].manifest, t[0].line))
            written = None
            for dep, info in found:
                if not dep.pinned:
                    continue
                pattern = pin_pattern(dep)
                if pattern is not None:
                    written = (dep, info, pattern)
                    break
            if written is None:
                first = found[0][0]
                notes.append(f"{consumer.name} names the package of {producer_name} in {first.manifest}:{first.line} without a version this tool can read "
                             "(no exact pin), so no package edge was written; add one by hand if its pins matter; confirm")
                continue
            dep, info, pattern = written
            marker, marker_at, marker_why = info
            imported = consumer.facts.imports.get(marker)
            is_range = bool(re.search(r"""["'][\^~]|[\^~]\s*\d""", dep.text.split("=", 1)[-1])) and "==" not in dep.text
            at = _at(consumer.name, f"{dep.manifest}:{dep.line}")
            how = "with a range; its lower bound is read" if is_range else "with an exact version"
            confident = "high" if imported and not is_range else "medium"
            edge_notes = [Note(f"{consumer.name} pins {producer_name}'s package in {dep.manifest} {how}", (at,), confident),
                          Note(f"import_marker {marker}: {marker_why}", (_at(producer_name, marker_at),) + ((_at(consumer.name, imported),) if imported else ()),
                               "high" if imported else "medium")]
            if len(found) > 1:
                others = ", ".join(f"{d.manifest}:{d.line}" for d, _ in found if d is not dep)
                edge_notes.append(Note(f"the package is named in other manifests too ({others}); only one manifest is read per edge", (), "low"))
            package_edges.append(EdgeGuess("package", producer_name, consumer.name,
                                           {"manifest": dep.manifest, "pin_pattern": pattern, "import_marker": marker}, tuple(edge_notes), "producer_consumer"))
    contract_edges: list[EdgeGuess] = []
    for e in package_edges:
        producer, consumer = by_name[e.producer], by_name[e.consumer]
        if "contract" in producer.sections and consumer.language.value == "python":
            marker = e.options["import_marker"]
            contract_edges.append(EdgeGuess("contract", e.producer, e.consumer, {}, (
                Note(f"{e.producer} has a contract table and {e.consumer} (Python) pins its package, so changes to the models are read in {e.consumer}",
                     e.notes[0].evidence, e.notes[0].confidence),), "producer_consumer"))

    # ---- http
    http: list[EdgeGuess] = []
    clients = [g for g in guesses if "http_client" in g.sections]
    servers = [g for g in guesses if _serves(g)]
    for client in clients:
        wanted = _segments(client.facts.client_paths)
        candidates = [s for s in servers if s.name != client.name]
        hints = {s.name: _segments(s.facts.route_hints) for s in candidates}
        # a segment that several candidate servers have says nothing about which one is called: only the segments one server has count
        shared = {seg for seg in wanted if sum(seg in h for h in hints.values()) > 1}
        matched = [s for s in candidates if (wanted - shared) & hints[s.name]]
        if not matched and len(candidates) > 1:
            notes.append(f"{client.name} has an HTTP client, but no URL segment it calls is in the routes of one of {', '.join(c.name for c in candidates)} alone"
                         + (f" ({', '.join(sorted(shared)[:4])} are in several)" if shared else "") + ", so no http edge was written; add the one it calls by hand; confirm")
            continue
        for server in matched or candidates:
            overlap = sorted((wanted - shared) & hints[server.name])
            confidence = "medium" if overlap else "low"
            why = (f"{client.name} calls URLs that share {', '.join(overlap[:4])} with the routes {server.name} serves" if overlap
                   else f"{client.name} has an HTTP client and {server.name} serves routes; no URL segment was seen on both sides")
            where = tuple(x for x in ((_at(client.name, client.facts.client_evidence) if client.facts.client_evidence else None),
                                      (_at(server.name, server.facts.route_evidence) if server.facts.route_evidence else None)) if x)
            http.append(EdgeGuess("http", client.name, server.name, {}, (Note(why, where, confidence),), "from_to"))

    # ---- event
    events: list[EdgeGuess] = []
    for worker in guesses:
        if worker.kind_of("entry_points") != "celery" or not worker.facts.topics:
            continue
        glob = "**/" + worker.facts.topics.rsplit(":", 1)[0].rsplit("/", 1)[-1]
        producers = [p for p in guesses if p.name != worker.name and _serves(p) and "backend" in p.role_set]
        if not producers:
            continue
        first, others = producers[0], producers[1:]
        why = (f"{worker.name} runs Celery tasks named by the topics in its topics module; {first.name} serves routes and may publish them "
               "(no publisher was found; no adapter follows event edges, so this only records the link)")
        if others:
            why += f"; {', '.join(p.name for p in others)} could publish them too: add an edge for each that does"
        events.append(EdgeGuess("event", first.name, worker.name, {"topics_glob": glob}, (Note(why, (_at(worker.name, worker.facts.topics),), "low"),), "from_to"))
    edges = http + package_edges + contract_edges + events
    return edges, notes


def _serves(g: RepoGuess) -> bool:
    entry = g.kind_of("entry_points")
    return (entry is not None and entry in SERVER_KINDS) or "http_server" in g.sections


def _segments(paths: Sequence[str]) -> set[str]:
    out: set[str] = set()
    for p in paths:
        for s in re.split(r"/+", p):
            s = s.strip().lower()
            if s and s not in GENERIC_SEGMENTS and not s.startswith(("{", "<", ":", "$")) and re.match(r"^[a-z][a-z0-9_-]{2,}$", s):
                out.add(s)
    return out
