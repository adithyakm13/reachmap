"""The JSON form of what the adapters and edges found, and the way back.

A report is plain JSON, but the things in it (changed symbols, callers, routes, http edges, consumers, contract reads)
are the dataclasses the analyser works with. `encode` turns one into JSON values (tuples become lists, an Evidence is its
five fields) and `decode(cls, data)` builds the same type again from the type hints of the dataclass, so a reader of a
report (the end to end harness now, the renderers later) never invents a second set of types. Both directions refuse
what does not fit: an unknown key, a missing one (also when the dataclass has a default for it: encoding writes every
field), a value of the wrong type. A field that holds anything but JSON (`Reached.sources`, the walk chains behind an area;
`ChangeAnalysis.change`, the change set, which a report keeps as its own `change`) is left out and decodes to its default or
to None. A text with a lone surrogate (a file name that is not UTF-8) is written with its bytes escaped (`redact.display`).

Nothing here does I/O or reads a repository.
"""

from __future__ import annotations

import dataclasses
import types
import typing
from typing import Any, Iterator, Sequence, Union

from reachmap.errors import ModelError
from reachmap.model import Evidence
from reachmap.redact import display

__all__ = ["decode", "encode", "evidence_in", "evidence_walk", "propagation_from_dict", "propagation_to_dict", "reach_to_dict"]

# Fields that hold objects with no JSON form, by class name: left out when encoding, their default (or None) when decoding.
LEFT_OUT: dict[str, frozenset[str]] = {"Reached": frozenset({"sources"}), "ChangeAnalysis": frozenset({"change"}), "Route": frozenset({"path_approximate"})}

_HINTS: dict[type, dict[str, Any]] = {}


def _hints(cls: type) -> dict[str, Any]:
    if cls not in _HINTS:
        _HINTS[cls] = typing.get_type_hints(cls)
    return _HINTS[cls]


def encode(value: Any) -> Any:
    """JSON values for a dataclass, a tuple or list of them, a mapping or a scalar."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        skip = LEFT_OUT.get(type(value).__name__, frozenset())
        if isinstance(value, Evidence) and value.claim is None:
            skip = skip | {"claim"}  # in the JSON only when it is set, as `Evidence.to_dict` writes it
        return {f.name: encode(getattr(value, f.name)) for f in dataclasses.fields(value) if f.name not in skip}
    if isinstance(value, (tuple, list)):
        return [encode(v) for v in value]
    if isinstance(value, dict) or hasattr(value, "items"):
        return {str(k): encode(v) for k, v in value.items()}
    if isinstance(value, str):
        return display(value)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    raise ValueError(f"cannot encode a {type(value).__name__} as JSON")


_EVIDENCE_KEYS = (frozenset({"repo", "path", "line", "ref", "detail"}), frozenset({"repo", "path", "line", "ref", "detail", "claim"}))


def _site(parent: str, key: Any) -> str:
    return f"{parent}[{key}]" if isinstance(key, int) else f"{parent}.{key}" if parent else str(key)


_CORE = frozenset({"repo", "path", "line"})


def evidence_walk(data: Any, site: str = "") -> Iterator[tuple[str, dict[str, Any], bool]]:
    """Every object in a JSON structure that is, or looks like, a piece of evidence, as (where it is, the object, whether it has all five
    keys of Evidence): the one walker that the evidence readers and the citation audit share. An object has the look of one when it has
    `repo`, `path` and `line` and also `ref` or `detail` (the call sites and contract reads of a propagation have those three and
    neither, and are not evidence); one with only some of the five keys is yielded as incomplete, for a reader that must say so."""
    if isinstance(data, dict):
        keys = data.keys()
        if _CORE <= keys and ("ref" in keys or "detail" in keys):
            yield site, data, {"ref", "detail"} <= keys
            return
        for key, value in data.items():
            yield from evidence_walk(value, _site(site, key))
    elif isinstance(data, (list, tuple)):
        for i, item in enumerate(data):
            yield from evidence_walk(item, _site(site, i))


def evidence_in(data: Any) -> Iterator[Evidence]:
    """Every Evidence anywhere in a JSON structure (an object with exactly the five keys of an Evidence), in the order
    found. An object with the five keys and a bad value is an error (ValueError), never skipped."""
    for _, obj, complete in evidence_walk(data):
        if not complete or set(obj) not in _EVIDENCE_KEYS:  # not the keys of an Evidence: not one, but what is inside it may be
            for value in obj.values():
                yield from evidence_in(value)
            continue
        try:
            yield Evidence.from_dict(obj)
        except ModelError as exc:
            raise ValueError(f"evidence that cannot be read: {exc}") from None


def decode(tp: Any, data: Any) -> Any:
    """The value of type `tp` (a dataclass, `tuple[X, ...]`, `X | None`, a scalar) that `data` stands for."""
    if tp is Any:
        return data
    origin = typing.get_origin(tp)
    if origin in (Union, types.UnionType):
        options = [a for a in typing.get_args(tp) if a is not type(None)]
        if data is None:
            if len(options) == len(typing.get_args(tp)):
                raise ValueError("a value is missing: null is not accepted here")
            return None
        if len(options) != 1:
            raise ValueError(f"cannot decode the union {tp}")
        return decode(options[0], data)
    if origin is tuple:
        args = typing.get_args(tp)
        if not isinstance(data, (list, tuple)):
            raise ValueError(f"expected a list, got {type(data).__name__}")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(decode(args[0], item) for item in data)
        if len(args) != len(data):
            raise ValueError(f"expected {len(args)} items, got {len(data)}")
        return tuple(decode(a, item) for a, item in zip(args, data))
    if tp is Evidence:  # the `claim` key is there only when it is set
        try:
            return Evidence.from_dict(data)
        except ModelError as exc:
            raise ValueError(f"Evidence: {exc}") from None
    if isinstance(tp, type) and dataclasses.is_dataclass(tp):
        return _decode_dataclass(tp, data)
    if tp is bool:
        if not isinstance(data, bool):
            raise ValueError(f"expected a bool, got {data!r}")
        return data
    if tp is int:
        if isinstance(data, bool) or not isinstance(data, int):
            raise ValueError(f"expected an integer, got {data!r}")
        return data
    if tp is str:
        if not isinstance(data, str):
            raise ValueError(f"expected a string, got {data!r}")
        return data
    if tp is float:
        if isinstance(data, bool) or not isinstance(data, (int, float)):
            raise ValueError(f"expected a number, got {data!r}")
        return float(data)
    raise ValueError(f"cannot decode the type {tp}")


def _decode_dataclass(cls: type, data: Any) -> Any:
    if not isinstance(data, dict):
        raise ValueError(f"{cls.__name__}: expected an object, got {type(data).__name__}")
    fields = [f for f in dataclasses.fields(cls) if f.init]
    known = {f.name for f in fields}
    extra = sorted(set(data) - known)
    if extra:
        raise ValueError(f"{cls.__name__}: unexpected key(s) {extra}")
    hints = _hints(cls)
    skip = LEFT_OUT.get(cls.__name__, frozenset())
    values: dict[str, Any] = {}
    for f in fields:
        if f.name in data and f.name not in skip:
            try:
                values[f.name] = decode(hints[f.name], data[f.name])
            except ValueError as exc:
                raise ValueError(f"{cls.__name__}.{f.name}: {exc}") from None
        elif f.name in skip:  # left out of the JSON form: its default, or None when it has none
            if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:  # type: ignore[misc]
                values[f.name] = None
        else:
            raise ValueError(f"{cls.__name__}: missing key {f.name!r}")
    return cls(**values)


# ------------------------------------------------------------------------------ a whole propagation


def reach_to_dict(reach: Any) -> dict[str, Any] | None:
    """The entry result of one pass (the areas it reached and whether it is complete); None for a repository with no entry points."""
    if reach is None:
        return None
    return {"complete": reach.complete, "reasons": list(reach.reasons), "reached": encode(reach.reached)}


def propagation_to_dict(prop: Any, unit_test_commands: Sequence[dict[str, Any]] = ()) -> dict[str, Any]:
    """What a change reaches across repositories as JSON: every pass (one repository's own analysis and entry points), the
    hops between them, the areas with their routes, the http edges, consumers, contract changes and reads, the unit tests,
    and whether the whole is complete and why not. The findings and the areas themselves are the report's own lists."""
    from reachmap.edges import edge_subject

    hops = list(prop.hops)
    passes = []
    for p in prop.passes:
        passes.append({
            "repo": p.repo, "depth": p.depth, "sha": p.sha, "candidate": p.candidate, "prefix": encode(p.prefix), "seed": encode(p.seed),
            "hop": hops.index(p.hop) if p.hop is not None and p.hop in hops else None,
            "analysis": encode(p.analysis), "reach": reach_to_dict(p.reach),
        })
    return {
        "repo": prop.repo, "complete": prop.complete, "reasons": list(prop.reasons), "passes": passes, "hops": encode(prop.hops),
        "reached": encode(prop.reached), "http_edges": encode(prop.http_edges), "consumers": encode(prop.consumers),
        "contract_changes": encode(prop.contract_changes), "contract_reads": encode(prop.contract_reads),
        "contract_candidates": encode(prop.contract_candidates), "unit_tests": encode(prop.unit_tests),
        "unit_test_commands": [dict(c) for c in unit_test_commands],
        "unfollowed": [edge_subject(e) for e in prop.unfollowed], "unread_entry_points": list(prop.unread_entry_points),
    }


def propagation_from_dict(data: dict[str, Any]) -> Any:
    """The `Propagation` a report's propagation stands for (the change set and the findings are not part of it: a report
    keeps them in `change` and `findings`; `records` are the report's `incompleteness`)."""
    from reachmap.adapters import ChangeAnalysis, Reached, UnitTest
    from reachmap.edges import Hop, Seed
    from reachmap.edges.contract import ContractChange, ContractRead
    from reachmap.edges.http import HttpMatch
    from reachmap.edges.package import Consumer
    from reachmap.entrypoints import EntryResult
    from reachmap.propagate import Pass, Propagation

    hops = decode(tuple[Hop, ...], data["hops"])
    passes = []
    for p in data["passes"]:
        reach = p["reach"]
        entry = None if reach is None else EntryResult(None, decode(tuple[Reached, ...], reach["reached"]), reach["complete"], tuple(reach["reasons"]))  # type: ignore[arg-type]
        passes.append(Pass(
            p["repo"], p["depth"], p["sha"], None, decode(ChangeAnalysis, p["analysis"]), entry, hops[p["hop"]] if p["hop"] is not None else None,
            decode(tuple[Evidence, ...], p["prefix"]), decode(Seed | None, p["seed"]), p["candidate"],
        ))
    return Propagation(
        repo=data["repo"], change=None, passes=tuple(passes), hops=hops, reached=decode(tuple[Reached, ...], data["reached"]),
        http_edges=decode(tuple[HttpMatch, ...], data["http_edges"]), backend_calls=(), consumers=decode(tuple[Consumer, ...], data["consumers"]),
        contract_changes=decode(tuple[ContractChange, ...], data["contract_changes"]), contract_reads=decode(tuple[ContractRead, ...], data["contract_reads"]),
        contract_candidates=decode(tuple[ContractRead, ...], data["contract_candidates"]), findings=(), unit_tests=decode(tuple[UnitTest, ...], data["unit_tests"]),
        records=(), unfollowed=(), complete=data["complete"], reasons=tuple(data["reasons"]), unread_entry_points=tuple(data["unread_entry_points"]),
    )
