"""The JSON API of the runner: what each route does, apart from the HTTP of it (`server.py` has the guards and the routing).

Every method returns the body to send (a dict, or for a report page a `Page`) or raises `ApiError`, which carries a status and a body made
only of fixed words: an error never repeats what the request said, never holds a traceback or a path (the field of a refused job or
decision is named from a fixed list). What a job is allowed to be is decided here, against the loaded config: `{repo, pr | compare | range,
as_of?}`, strings capped, anything else refused. Nothing from a request reaches a path, a command line as anything but one validated
argument, or a config.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from reachmap import notes, pipeline
from reachmap import report as reports
from reachmap.errors import ModelError
from reachmap.model import Decision
from reachmap.review import attach, carry, store
from reachmap.review import formats
from reachmap.review.formats import TARGET_ID
from reachmap.runner import files, jobs

__all__ = ["Api", "ApiError", "Page", "MAX_BODY"]

MAX_BODY = 1024 * 1024
MAX_REPORT_BYTES = 64 * 1024 * 1024
MAX_LISTED = files.MAX_LISTED
CAPS = {"repo": 200, "pr": 20, "compare": 300, "range": 300, "as_of": 64}
_JOB_KEYS = frozenset({"repo", "pr", "compare", "range", "as_of"})
_DECISION_KEYS = frozenset({"target", "verdict", "reason", "author"})
_CHANGE_KINDS = ("pr", "compare", "range")
_JOB_ID = re.compile(r"^[0-9a-f]{12}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")
_NOT_PLAIN = re.compile(r"[\x00-\x20\x7f-\x9f\u2028\u2029\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")  # a ref and a number: no space, no control, no bidi
_DOTS = re.compile(r"\.{2,3}")
_FIELD_OF_RULE = {"decision_target": "target", "decision_verdict": "verdict", "decision_reason": "reason", "decision_author": "author"}


class ApiError(Exception):
    def __init__(self, status: int, error: str, **more: str) -> None:
        super().__init__(error)
        self.status, self.body = status, {"error": error, **more}


@dataclass(frozen=True)
class Page:
    """An HTML page: `text` is the whole document."""

    text: str


def _refuse_constant(name: str) -> Any:
    raise ValueError(name)


def parse_json(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"), parse_constant=_refuse_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ApiError(400, "invalid_json") from None


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class Api:
    def __init__(self, runner: jobs.Runner) -> None:
        self.runner = runner
        self.workspace = runner.workspace
        self.reports_dir = runner.reports_dir
        self._seen: dict[str, tuple[tuple[int, int], dict[str, Any] | None]] = {}

    # ---------------------------------------------------------------- the workspace

    def health(self) -> dict[str, Any]:
        return {"ok": True}

    def repos(self) -> dict[str, Any]:
        """The names, roles and branch patterns: no remote, no path, no token variable (the job form needs only these)."""
        return {
            "workspace": self.workspace.name,
            "repos": [{"name": r.name, "roles": list(r.roles), "branches": list(r.branches)} for r in self.workspace.repos.values()],
        }

    # ---------------------------------------------------------------- jobs

    def _job_spec(self, body: Any) -> tuple[str, dict[str, str], str | None]:
        def refuse(field: str) -> ApiError:
            return ApiError(400, "invalid_job", field=field)

        if not isinstance(body, dict):
            raise refuse("body")
        if not set(body) <= _JOB_KEYS:
            raise refuse("keys")
        repo = body.get("repo")
        if not isinstance(repo, str) or len(repo) > CAPS["repo"] or repo not in self.workspace.repos:
            raise refuse("repo")
        given = [k for k in _CHANGE_KINDS if k in body]
        if len(given) != 1:
            raise refuse("change")
        kind = given[0]
        value = body[kind]
        try:
            if kind == "pr":
                if _is_int(value):
                    if not 0 < value < 10 ** CAPS["pr"]:
                        raise ValueError
                elif not isinstance(value, str) or len(value) > CAPS["pr"] or _NOT_PLAIN.search(value):
                    raise ValueError
                spec = pipeline.ChangeSpec(pr=value)
            else:
                if not isinstance(value, str) or len(value) > CAPS[kind] or _NOT_PLAIN.search(value):
                    raise ValueError
                spec = pipeline.ChangeSpec(**{kind: value})
                if any(part.startswith("-") for part in _DOTS.split(value)):  # a ref is never an option of git
                    raise ValueError
        except ValueError:
            raise refuse(kind) from None
        as_of = body.get("as_of")
        if "as_of" in body:
            try:
                if not isinstance(as_of, str) or not as_of.strip() or len(as_of) > CAPS["as_of"] or _CONTROL.search(as_of):
                    raise ValueError
                pipeline.parse_as_of(as_of)
            except ValueError:
                raise refuse("as_of") from None
        return repo, spec.as_dict(), as_of

    def submit(self, raw: bytes) -> dict[str, Any]:
        repo, change, as_of = self._job_spec(parse_json(raw))
        try:
            job = self.runner.submit(repo, change, as_of)
        except jobs.QueueFull:
            raise ApiError(429, "queue_full") from None
        return {"id": job.id, "state": self.runner.snapshot(job.id)["state"]}

    def jobs(self) -> dict[str, Any]:
        return {"jobs": self.runner.listing()}

    def job(self, job_id: str) -> dict[str, Any]:
        if not _JOB_ID.match(job_id):
            raise ApiError(404, "not_found")
        try:
            return self.runner.snapshot(job_id)
        except jobs.UnknownJob:
            raise ApiError(404, "not_found") from None

    def cancel(self, job_id: str) -> dict[str, Any]:
        if not _JOB_ID.match(job_id):
            raise ApiError(404, "not_found")
        try:
            done = self.runner.cancel(job_id)
        except jobs.UnknownJob:
            raise ApiError(404, "not_found") from None
        except jobs.Finished:
            raise ApiError(409, "finished") from None
        return {"id": done["id"], "state": done["state"]}

    # ---------------------------------------------------------------- reports

    def _read(self, name: str) -> dict[str, Any] | None:
        """The report `<reports dir>/<name>.json` read and checked, or None. A name is plain (`files.NAME`), is matched exactly by the entry the
        folder has, and the file is a regular file with one link that is not a link itself (`files.read_regular`)."""
        if not files.NAME.match(name) or not files.listed(self.reports_dir, f"{name}.json"):
            return None
        raw = files.read_regular(self.reports_dir / f"{name}.json", MAX_REPORT_BYTES)
        if raw is None:
            return None
        try:
            return reports.parse(raw.decode("utf-8"))
        except (UnicodeDecodeError, reports.ReportError):
            return None

    def _with_decisions(self, name: str) -> dict[str, Any]:
        data = self._read(name)
        if data is None:
            raise ApiError(404, "not_found")
        try:
            attach.refresh(data, self.reports_dir)  # the decisions of the store as it is now (what `reachmap report` shows)
        except (OSError, store.StoreError, carry.ReviewError):
            pass
        try:
            notes.attach(data, self.reports_dir)  # the model notes of the store, as the report command shows them (M14)
        except (OSError, store.StoreError):
            pass
        return data

    def report(self, name: str) -> dict[str, Any]:
        return self._with_decisions(name)

    def report_page(self, name: str) -> Page:
        data = self._with_decisions(name)
        try:
            return Page(reports.render(data, "html"))
        except (reports.ReportError, ValueError):
            raise ApiError(404, "not_found") from None

    def reports_list(self) -> dict[str, Any]:
        try:
            names = [e for e in os.scandir(self.reports_dir) if e.name.endswith(".json") and not e.name.startswith(".") and e.is_file(follow_symlinks=False)]
            stats = sorted(((e.name[:-5], e.stat(follow_symlinks=False)) for e in names if files.NAME.match(e.name[:-5])), key=lambda x: (-x[1].st_mtime_ns, x[0]))[:MAX_LISTED]
        except OSError:
            return {"reports": []}
        items = []
        for name, st in stats:
            sig = (st.st_mtime_ns, st.st_size)
            cached = self._seen.get(name)
            if cached is None or cached[0] != sig:
                data = self._read(name)
                cached = self._seen[name] = (sig, None if data is None else _summary(name, data))
            if cached[1] is not None:
                items.append(cached[1])
        items.sort(key=lambda i: (i["created"] is None, "" if i["created"] is None else _desc(i["created"]), i["name"]))
        return {"reports": items}

    # ---------------------------------------------------------------- decisions

    def decide(self, name: str, raw: bytes) -> tuple[int, dict[str, Any]]:
        """One decision about an area or a finding of a report, channel `runner` (the same record `reachmap decide` makes, the same store)."""
        data = self._read(name)
        if data is None:
            raise ApiError(404, "not_found")
        body = parse_json(raw)

        def refuse(field: str) -> ApiError:
            return ApiError(400, "invalid_decision", field=field)

        if not isinstance(body, dict) or not set(body) <= _DECISION_KEYS:
            raise refuse("keys")
        if data["status"] != "completed":
            raise ApiError(409, "not_completed")
        try:
            key = attach.key_of(data)
            targets = carry.targets_of(data)
        except carry.ReviewError:
            raise ApiError(409, "no_review") from None
        target_id, verdict, reason, author = body.get("target"), body.get("verdict"), body.get("reason", ""), body.get("author")
        if not isinstance(target_id, str) or not TARGET_ID.match(target_id):
            raise refuse("target")
        if not isinstance(verdict, str):
            raise refuse("verdict")
        try:
            if not isinstance(reason, str) or len(reason) > store.MAX_REASON:
                raise formats.TextRefused("reason")
            formats.check_reason(reason)
        except formats.TextRefused:
            raise refuse("reason") from None
        try:
            if not isinstance(author, str) or not author.strip() or len(author) > store.MAX_AUTHOR:
                raise formats.TextRefused("author")
            formats.check_author(author)
        except formats.TextRefused:
            raise refuse("author") from None
        target = targets.get(target_id)
        if target is None:
            raise ApiError(404, "unknown_target")
        revision = data["review"].get("revision")
        if not _is_int(revision):
            raise ApiError(409, "no_review")
        try:
            decision = Decision(target.id, verdict, reason, author.strip(), "runner", target.fingerprint, revision)
        except ModelError as exc:
            raise refuse(_FIELD_OF_RULE.get(getattr(exc, "code", None) or "", "verdict")) from None
        stamp = _now_iso()
        try:
            store.append(self.reports_dir, key, [store.Stored(decision, stamp)])
        except store.StoreError:
            raise ApiError(409, "review_store") from None
        except OSError:
            raise ApiError(500, "internal") from None
        return 201, {**{k: v for k, v in decision.to_dict().items() if k != "fingerprint"}, "created_at": stamp}


def _desc(text: str) -> str:
    """A sort key that puts the later time first (the characters of the time, inverted)."""
    return "".join(chr(0x10FFFF - ord(c)) for c in text)


def _summary(name: str, data: Mapping[str, Any]) -> dict[str, Any]:
    review = data.get("review") if isinstance(data.get("review"), dict) else {}
    key = review.get("key") if isinstance(review.get("key"), dict) else None
    inputs = data.get("inputs") if isinstance(data.get("inputs"), dict) else {}
    if key is not None:
        shown = {k: key.get(k) for k in ("workspace", "repo", "change")}
    else:
        config = inputs.get("config") if isinstance(inputs.get("config"), dict) else {}
        shown = {"workspace": config.get("workspace"), "repo": inputs.get("repo"), "change": inputs.get("change")}
    run = data.get("run") if isinstance(data.get("run"), dict) else {}
    change = data.get("change") if isinstance(data.get("change"), dict) else {}
    head = change.get("head_sha")
    # a run that did not complete has no count (a partial list must never be countable): the list says nothing of it either
    counts = data.get("counts") if data.get("status") == "completed" and isinstance(data.get("counts"), dict) else {}
    areas, blocking = counts.get("areas"), counts.get("blocking_records")
    return {"name": name, "key": shown, "revision": review.get("revision"), "status": data.get("status"), "created": run.get("started_at"),
            "head_sha": head if isinstance(head, str) and head else None, "areas": areas if _is_int(areas) else None,
            "blocking_records": blocking if _is_int(blocking) else None}
