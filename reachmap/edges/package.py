"""The package edge: a producer repository's tags and its consumers' pins (context 7.4, section 5).

A change in a producer matters before anyone adopts it, so consumers are listed from their own repositories:

- a change contained in no released tag (unreleased) lists each consumer once, from its primary branch, as
  `on_adoption`;
- a change contained in a released tag lists one entry per configured branch of each consumer (the branches that exist),
  `adopted` when the tag the branch pins contains the change commit, else `on_adoption`; `contains_change` says which.

Versions are real tags (`tag_prefix` + version on the producer): a placeholder version in a manifest (`0.0.0`) is never
read. A pin is read from the consumer's manifest at each branch head with the edge's `pin_pattern` (one group). What a
tag contains is ancestry, read from git (`merge-base --is-ancestor`), never inferred from the number: release lines
diverge, and `1.10.0` is after `1.9.0` only as a number. A pin that does not parse as a version, or that no tag names,
leaves adoption undecided (`version_unparsed`, `pin_unresolved`: recorded). A manifest that cannot be read is an
incompleteness on the edge, never "not adopted": the consumer is listed as `unreadable` and `undecided`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from reachmap.edges import Hop, Pin, edge_incomplete, incomplete_hop
from reachmap.incomplete import record
from reachmap.model import Evidence, Read
from reachmap.versions import Tags, parse

__all__ = ["Consumer", "PackageEdgeAdapter", "manifest_pin"]


@dataclass(frozen=True, slots=True)
class Consumer:
    """One consumer of a producer, on one branch.

    `state`: `pinned` (the manifest names a version), `no_pin` (the manifest was read and does not name the package, or
    the file is not on the branch: an absence that was established), `unmatched` (it names the package but no line matches
    the pin pattern: a range, an extra, another spelling), `unreadable` (the manifest could not be read: nothing is
    established about it). `adoption`: `adopted`, `on_adoption` or `undecided` (the pin
    does not parse, no tag names it, or its ancestry could not be read). `contains_change` is True or False when the
    pinned tag's ancestry was read, False for an unreleased change, else None. `evidence` is the pin line at `sha`."""

    repo: str
    producer: str
    branch: str
    sha: str
    pin: str | None
    state: str
    adoption: str
    contains_change: bool | None
    evidence: tuple[Evidence, ...]
    tag: str | None = None
    released: bool = False
    manifest: str = ""
    detail: str = ""
    first_release: str | None = None  # the lowest tag that contains the change

    def all_evidence(self) -> tuple[Evidence, ...]:
        return self.evidence


def manifest_pin(consumer: str, manifest: str, sha: str, text: str, pattern: Any) -> list[tuple[int, str, Evidence]]:
    """Every (line, pin, evidence) in the manifest text whose line matches the pin pattern (its one group is the version)."""
    found: list[tuple[int, str, Evidence]] = []
    for n, line in enumerate(text.split("\n"), 1):
        line = line.rstrip("\r")
        m = pattern.search(line)
        if m is None or not line.strip():
            continue
        pin = (m.group(1) or "").strip()
        if pin:
            found.append((n, pin, Evidence(repo=consumer, path=manifest, line=n, ref=sha, detail=line)))
    return found


class PackageEdgeAdapter:
    kind = "package"

    def applies(self, run: Any, p: Any, edge: Any) -> bool:
        """A change in the producer itself (the seed of another edge is not a change of its repository)."""
        if p.repo != edge.from_repo or p.seed is not None or p.depth != 0:
            return False
        return any(i.role == "source" for i in p.analysis.impacts) or any(f.role == "source" for f in p.analysis.files)

    def follow(self, run: Any, p: Any, edge: Any) -> list[Hop]:
        producer, consumer = edge.from_repo, edge.to_repo
        release = self.release(run, p)
        pins = self._pins(run, release, consumer)
        if pins is None:
            return [incomplete_hop(run.target, edge, producer, p.depth, [f"the branches of {consumer} could not be listed: its pins were not read"])]
        if not pins:
            return [incomplete_hop(run.target, edge, producer, p.depth, [f"none of the configured branches of {consumer} exists: its pins were not read"])]
        hops: list[Hop] = []
        for pin in pins:
            c = self._consumer(run, p, edge, release, pin)
            run.consumers.append(c)
            reasons: list[str] = []
            if c.state == "unreadable" or (c.adoption == "undecided" and c.state != "no_pin"):
                reasons.append(c.detail)  # an adoption that was not decided is not "not adopted": the edge is incomplete
            if release["unknown"] and release["unknown"] not in reasons:
                reasons.append(release["unknown"])
            for why in reasons:
                edge_incomplete(run.target, edge, why)
            hops.append(Hop(
                "package", producer, consumer, (producer, consumer), "forward", p.depth + 1, (pin,), (), c.evidence, c.evidence, (),
                detail=c.detail, complete=not reasons, reasons=tuple(reasons),
            ))
        return hops

    # ---- what the change is contained in

    def release(self, run: Any, p: Any) -> dict[str, Any]:
        """Whether the change is in a released tag of the producer: {tags, released (tag names that contain the change,
        lowest version first), undecided (tags whose ancestry could not be read), unknown (why it cannot be said, or ''),
        prerelease_only}. One per run and producer."""
        def make() -> dict[str, Any]:
            producer = p.repo
            cfg = run.repo(producer)
            prefix = (cfg.contract or {}).get("tag_prefix")
            remote = run.remote(producer)
            tags = Tags.read(run.git, remote, prefix)
            out: dict[str, Any] = {"tags": tags, "released": [], "undecided": [], "unknown": "", "head": p.change.head_sha, "prerelease_only": False}
            if tags.state == "unreadable":
                out["unknown"] = f"the tags of {producer} could not be read: whether the change is released was not established ({tags.detail})"
                return out
            if tags.state == "absent" and prefix:
                every = run.git.tags(remote)
                if every.state == "unreadable":
                    out["unknown"] = f"the tags of {producer} could not be listed: whether the tag prefix {prefix!r} matches any was not established"
                    return out
                if every.state == "found":  # the prefix names nothing while the repository has tags: the change is not quietly unreleased
                    record(run.target, "tag_prefix_unmatched", producer,
                           detail=f"tag_prefix {prefix!r} matches none of its {len(every.tags)} tags (for example {sorted(every.tags)[0]!r}), so the change is read as unreleased")
                    run.problem(f"tag_prefix {prefix!r} of {producer} matches none of its tags: the change may be released under other names")
            contained, undecided = tags.containing(run.git, remote, p.change.head_sha)
            out["released"], out["undecided"] = contained, undecided
            out["prerelease_only"] = bool(contained) and all(tags.versions[t].is_prerelease for t in contained)
            if not contained and undecided:
                out["unknown"] = f"the ancestry of {len(undecided)} tag(s) of {producer} could not be read ({', '.join(undecided[:3])}): whether the change is released was not established"
            return out
        return run.memo(("release", p.repo, p.change.head_sha), make)

    def _pins(self, run: Any, release: dict[str, Any], consumer: str) -> list[Pin] | None:
        """The revisions of the consumer to read: its primary branch for a change nobody has released, every configured
        branch that exists for a released one."""
        if release["released"]:
            by_branch = run.branch_pins(consumer)
            return None if by_branch is None else [by_branch[b] for b in sorted(by_branch)]
        pin = run.primary(consumer)
        return None if pin is None else [Pin(consumer, pin.ref, pin.sha, "primary")]

    # ---- one consumer on one branch

    @staticmethod
    def _names_package(text: str, edge: Any, run: Any) -> bool:
        """Whether the manifest mentions the package at all (the import marker, in any of the spellings a package name
        has: `acme_lib`, `acme-lib`, `acme.lib`)."""
        name = edge.import_marker or (run.repo(edge.from_repo).contract or {}).get("package")
        if not name:
            return False
        return re.search(r"(?<![A-Za-z0-9])" + re.sub(r"[-_.]", "[-_.]", re.escape(name)) + r"(?![A-Za-z0-9])", text, re.IGNORECASE) is not None

    def _consumer(self, run: Any, p: Any, edge: Any, release: dict[str, Any], pin: Pin) -> Consumer:
        producer, consumer = edge.from_repo, edge.to_repo
        manifest = edge.manifest or ""
        res = run.git.file_at(run.remote(consumer), manifest, pin.sha)  # the manifest is read as a file: whatever language the consumer is written in
        first = release["released"][0] if release["released"] else None
        base = dict(repo=consumer, producer=producer, branch=pin.ref, sha=pin.sha, manifest=manifest, released=bool(release["released"]), first_release=first)
        subject = f"{consumer}:{pin.ref}"
        if res.state == Read.UNREADABLE:
            return Consumer(**base, pin=None, state="unreadable", adoption="undecided", contains_change=None, evidence=(),
                            detail=f"{manifest} could not be read on {pin.ref} of {consumer}: {res.detail}")
        if res.state == Read.ABSENT:
            return Consumer(**base, pin=None, state="no_pin", adoption="undecided", contains_change=None, evidence=(),
                            detail=f"{manifest} is not on {pin.ref} of {consumer} at {pin.sha[:12]}: it pins nothing there")
        found = manifest_pin(consumer, manifest, pin.sha, res.text, edge.pin_pattern)
        if not found:
            if self._names_package(res.text, edge, run):
                record(run.target, "pin_unresolved", subject,
                       detail=f"{manifest} names the package but no line matches the pin pattern (a range, an extra or another spelling?): adoption was not decided")
                return Consumer(**base, pin=None, state="unmatched", adoption="undecided", contains_change=None, evidence=(),
                                detail=f"{manifest} on {pin.ref} of {consumer} names the package and no line matches the pin pattern")
            return Consumer(**base, pin=None, state="no_pin", adoption="undecided", contains_change=None, evidence=(),
                            detail=f"{manifest} on {pin.ref} of {consumer} was read and does not name the package")
        _n, text, ev = found[0]
        extra = f"; {len(found)} lines match the pin pattern, the first is used" if len(found) > 1 and len({f[1] for f in found}) > 1 else ""
        version = parse(text)
        if version is None:
            record(run.target, "version_unparsed", subject, detail=f"the pin {text!r} in {manifest} is not a version this tool reads: adoption was not decided")
            return Consumer(**base, pin=text, state="pinned", adoption="undecided", contains_change=None, evidence=(ev,),
                            detail=f"the pin {text!r} cannot be read as a version{extra}")
        if not release["released"]:
            if release["unknown"]:
                return Consumer(**base, pin=text, state="pinned", adoption="undecided", contains_change=None, evidence=(ev,), detail=release["unknown"] + extra)
            return Consumer(**base, pin=text, state="pinned", adoption="on_adoption", contains_change=False, evidence=(ev,),
                            detail="the change is in no tag of the producer, so no pin has it yet" + extra)
        tags: Tags = release["tags"]
        tag, how = tags.tag_for_pin(text)
        released = f"; first released in {first}" + (" (only pre-release tags contain it)" if release["prerelease_only"] else "")
        if tag is None:
            record(run.target, "pin_unresolved", subject, detail=f"the pin {text} names no tag of {producer} ({how}): adoption was not decided")
            return Consumer(**base, pin=text, state="pinned", adoption="undecided", contains_change=None, evidence=(ev,),
                            detail=f"no tag of {producer} names {text}: {how}{released}{extra}")
        if tag in release["released"]:
            contains: bool | None = True
        elif tag in release["undecided"]:
            res = run.git.is_ancestor(run.remote(producer), release["head"], tags.by_name[tag])
            contains = None if res.state == Read.UNREADABLE else res.text == "true"
            if contains is None:
                return Consumer(**base, pin=text, state="pinned", adoption="undecided", contains_change=None, evidence=(ev,), tag=tag,
                                detail=f"the ancestry of {tag} could not be read: {res.detail}{extra}")
        else:
            contains = False
        return Consumer(
            **base, pin=text, state="pinned", adoption="adopted" if contains else "on_adoption", contains_change=contains, evidence=(ev,), tag=tag,
            detail=(f"{tag} contains the change commit" if contains else f"{tag} does not contain the change commit") + released + extra,
        )
