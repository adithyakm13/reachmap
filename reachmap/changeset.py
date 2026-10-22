"""Resolve a change (a pull request, a compare or a range) to a pinned ChangeSet.

Everything the analysis reads later is pinned to the shas found here: the base, the head,
and the pre-change version (what the code was before the change), each with how it was
found. A pre-change version that cannot be established is carried as None together with a
blocking `pre_change_unavailable` record, never skipped silently. For a pull request the
local diff's file count must equal the host's: a mismatch is recorded as a blocking
`diff_count_mismatch` and the run is refused (`ChangeSetRefused`).

Semantics:
- `compare` is `base...head` (three dots): the change is what `head` has since the merge
  base of the two; the pre-change version is the merge base.
- `range` is `a..b` (two dots): the change is everything from the commit `a` to the commit
  `b`; the pre-change version is `a`.
- a merged pull request: the head is its merge commit and the pre-change version the first
  parent of it; an open (or closed, unmerged) one: the host's base and head, and the
  pre-change version is their merge base.
When there is no pre-change version the files are the plain tree difference between base
and head, and the record says so.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Sequence

from reachmap import redact
from reachmap.diffparse import ChangeSet
from reachmap.errors import ModelError
from reachmap.gitlayer import Git, ReadFailed, RefNotFound
from reachmap.hosts import ChangeRequest, HostUnsupported, get_host, parse_number
from reachmap.incomplete import record
from reachmap.model import ReadResult

__all__ = ["ChangeSetRefused", "NoSuchChangeRequest", "resolve", "title_for_commit"]

_SQUASH_SUFFIX = re.compile(r"\((?:#|!)(\d+)\)\s*$")
_FULL_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class ChangeSetRefused(ModelError):
    """The change cannot be analysed as it stands (for example the local diff and the host
    disagree about the files). `records` are the incompleteness records made on the way."""

    def __init__(self, message: str, records: Sequence[Any] = ()) -> None:
        super().__init__(message)
        self.records = tuple(records)


class NoSuchChangeRequest(ChangeSetRefused):
    """The host answered 404: there is no such change request (or the login cannot see it)."""


def _repo_of(workspace: Any, repo: Any) -> Any:
    if isinstance(repo, str):
        try:
            return workspace.repos[repo]
        except KeyError:
            raise ValueError(f"unknown repository {repo!r}; known: {', '.join(workspace.repos)}") from None
    return repo


def _two_refs(value: Any, dots: str, what: str) -> tuple[str, str]:
    if isinstance(value, str):
        if dots == "..." and "..." not in value:
            raise ValueError(f"{what} is written base...head (three dots), not {value!r}; use range for two dots")
        if dots == ".." and ("..." in value or ".." not in value):
            raise ValueError(f"{what} is written a..b (two dots), not {value!r}; use compare for three dots")
        left, _, right = value.partition(dots)
    else:
        try:
            left, right = value
        except (TypeError, ValueError):
            raise ValueError(f"{what} is a string base{dots}head or a pair of refs") from None
    if not isinstance(left, str) or not isinstance(right, str) or not left.strip() or not right.strip():
        raise ValueError(f"{what} needs two refs")
    return left.strip(), right.strip()


def _pin(git: Git, remote: str, ref: str, what: str, as_of: str | None = None) -> str:
    """A ref to a sha. A full sha is a pin already and is not moved by `as_of` (a change request's own shas stay as the
    host reports them); a name is read as of the time."""
    try:
        return git.pin(remote, ref, as_of=None if _FULL_SHA.match(ref) else as_of)
    except RefNotFound as exc:
        raise ChangeSetRefused(f"{what}: {exc}") from None


def change_label(kind: str, repo: str, number: int) -> str:
    return f"{repo}!{number}" if kind == "gitlab" else f"{repo}#{number}"


def resolve(
    workspace: Any,
    repo: Any,
    *,
    pr: int | str | None = None,
    compare: str | tuple[str, str] | None = None,
    range: str | tuple[str, str] | None = None,  # noqa: A002 (the keyword of the public API)
    git: Git | None = None,
    host: Any = None,
    record_to: Any = None,
    as_of: str | None = None,
) -> ChangeSet:
    """Resolve one of `pr`, `compare` or `range` for a repository of the workspace to a pinned
    ChangeSet. `git` and `host` default to the layers for the repository's configured host;
    `record_to` (a list or an Analysis) receives every record made. `as_of` (ISO 8601; a naive time is UTC) pins the refs
    that `compare` and `range` name as of that time, as `Git.pin` does; a full sha, and the shas a host reports for a
    change request, are left as they are."""
    given = [name for name, value in (("pr", pr), ("compare", compare), ("range", range)) if value is not None]
    if len(given) != 1:
        raise ValueError(f"give exactly one of pr, compare, range (got {', '.join(given) or 'none'})")
    cfg = _repo_of(workspace, repo)
    if git is not None and record_to is not None:
        raise ValueError("record_to is for the layers resolve builds itself; give it to Git(record_to=...) when you pass a git")
    target = git.target if git is not None else (record_to if record_to is not None else [])
    if host is None:
        options: dict[str, Any] = {"record_to": target}
        if cfg.host == "git":
            options["remotes"] = {cfg.name: cfg.remote} if cfg.remote else {}
        host = get_host(cfg.host, **options)
    if git is None:
        token_env = getattr(host, "token_env", None)
        git = Git(record_to=target, drop_env=[token_env] if token_env else ())  # the host's token stays out of git's environment
    started = len(git.incompleteness)
    try:
        remote = cfg.remote or host.remote(cfg.name)
    except HostUnsupported as exc:
        raise ChangeSetRefused(str(exc)) from None
    shown = redact.transport_url(remote)

    if pr is not None:
        result = _resolve_pr(git, host, cfg, remote, parse_number(pr))
    elif compare is not None:
        base_ref, head_ref = _two_refs(compare, "...", "compare")
        base = _pin(git, remote, base_ref, f"base {base_ref!r}", as_of)
        head = _pin(git, remote, head_ref, f"head {head_ref!r}", as_of)
        mb = git.merge_base(remote, base, head)
        how = {"base": f"ref {base_ref}", "head": f"ref {head_ref}"}
        result = _finish(git, remote, "compare", base, head, mb, head_ref, how,
                         f"merge base of {base_ref} and {head_ref}", "no merge base: the histories are unrelated")
    else:
        start_ref, end_ref = _two_refs(range, "..", "range")
        base = _pin(git, remote, start_ref, f"range start {start_ref!r}", as_of)
        head = _pin(git, remote, end_ref, f"range end {end_ref!r}", as_of)
        how = {"base": f"range start {start_ref}", "head": f"range end {end_ref}", "pre_change": f"range start {start_ref}"}
        result = _build(git, remote, "range", base, head, base, how)
    new = tuple(git.incompleteness[started:]) if len(git.incompleteness) >= started else ()
    return replace(result, repo=cfg.name, remote=shown, incompleteness=new)


def _finish(git: Git, remote: str, kind: str, base: str, head: str, merge_base: ReadResult, subject: str,
            how: dict[str, str], found_how: str, absent_detail: str, **extra: Any) -> ChangeSet:
    """Common end of compare and open pull requests: the merge base is the pre-change version,
    or a blocking record says there is none."""
    pre: str | None = None
    if merge_base.is_found:
        pre = merge_base.text
        how = {**how, "pre_change": found_how}
    else:
        detail = absent_detail if merge_base.is_absent else "the merge base could not be read (see the read_unreadable record)"
        record(git.target, "pre_change_unavailable", subject, detail=detail)
        how = {**how, "pre_change": f"unavailable: {detail}"}
    return _build(git, remote, kind, base, head, pre, how, **extra)


def _build(git: Git, remote: str, kind: str, base: str, head: str, pre: str | None, how: dict[str, str], **extra: Any) -> ChangeSet:
    diff = git.diff(remote, pre if pre is not None else base, head)
    return ChangeSet(
        base_sha=base, head_sha=head, files=diff.files, rename_detection_skipped=diff.rename_detection_skipped,
        kind=kind, pre_change_sha=pre, how=how, **extra,
    )


def _resolve_pr(git: Git, host: Any, cfg: Any, remote: str, number: int) -> ChangeSet:
    label = change_label(getattr(host, "kind", cfg.host), cfg.name, number)
    try:
        read = host.change_request(cfg.name, number)
    except HostUnsupported as exc:
        raise ChangeSetRefused(str(exc)) from None
    if read.is_absent:
        raise NoSuchChangeRequest(
            f"no such change request: {label} on {getattr(host, 'kind', cfg.host)} "
            "(the host answers 404 for a request that does not exist and, for some hosts, for one the login cannot see)"
        )
    if read.is_unreadable:
        records = tuple(getattr(host, "incompleteness", ())[-1:])
        raise ReadFailed(f"the change request {label} could not be read: {read.detail}", read.detail, records[0] if records else None)
    cr = ChangeRequest.from_read(read)
    how = {"base": f"base branch tip reported by the host ({cr.base_ref})"}
    if cr.state == "merged":
        if cr.merge_sha is None:
            raise ChangeSetRefused(f"{label} is merged but the host names no merge commit: the change cannot be pinned")
        head = _pin(git, remote, cr.merge_sha, f"merge commit of {label}")
        how["head"] = "merge commit of the change request"
        parents = git.parents(remote, head)
        pre: str | None = None
        if parents.is_found:
            listed = parents.text.split()
            if len(listed) >= 2:  # a merge commit: its first parent is the base branch just before the merge
                pre, how["pre_change"] = listed[0], "first parent of the merge commit"
            elif len(listed) == 1:  # squash, rebase or fast-forward: the commits of the request are on the base branch
                pre, how["pre_change"] = _before_single_parent_merge(git, remote, cr, head, listed[0], label)
            else:
                _no_pre_change(git, how, cr.head_ref, "the merge commit has no parent")
        else:
            _no_pre_change(git, how, cr.head_ref, "the parents of the merge commit could not be read (see the read_unreadable record)")
        if pre is not None:
            base = pre
            how["base"] = "the commit before the change (pre-change version)"
        else:
            base = _pin(git, remote, cr.base_sha, f"base of {label}")
        result = _build(git, remote, "pr", base, head, pre, how, change_request=cr)
    else:
        base = _pin(git, remote, cr.base_sha, f"base of {label}")
        head = _pin(git, remote, cr.head_sha, f"head of {label}")
        how["head"] = "head of the change request as reported by the host"
        mb = git.merge_base(remote, base, head)
        result = _finish(git, remote, "pr", base, head, mb, cr.head_ref, how,
                         "merge base of the host's base and head", "no merge base: the histories are unrelated",
                         change_request=cr)
    if result.file_count != cr.file_count:
        hint = " (git gave up on rename detection)" if result.rename_detection_skipped else ""
        detail = f"the local diff has {result.file_count} files and the host reports {cr.file_count}{hint}"
        rec = record(git.target, "diff_count_mismatch", label, detail=detail)
        raise ChangeSetRefused(f"refusing to continue with {label}: {detail}", [rec])
    return result


def _no_pre_change(git: Git, how: dict[str, str], subject: str, detail: str) -> Any:
    rec = record(git.target, "pre_change_unavailable", subject, detail=detail)
    how["pre_change"] = f"unavailable: {detail}"
    return rec


def _paths(git: Git, remote: str, before: str, after: str) -> set[str] | None:
    """The paths changed between two commits, or None when git could not say (nothing recorded:
    this is a check on another reading, not the read itself)."""
    with git.quiet():
        try:
            return set(git.diff(remote, before, after).paths)
        except ReadFailed:
            return None


def _own_paths(git: Git, remote: str, cr: ChangeRequest) -> set[str] | None:
    """The files of the request as its own head and base show them, when that head can still be
    fetched; None otherwise (a deleted fork branch)."""
    with git.quiet():
        head, base = git.pin_read(remote, cr.head_sha), git.pin_read(remote, cr.base_sha)
        if not (head.is_found and base.is_found):
            return None
        mb = git.merge_base(remote, base.text, head.text)
    return _paths(git, remote, mb.text, head.text) if mb.is_found else None


def _before_single_parent_merge(git: Git, remote: str, cr: ChangeRequest, head: str, parent: str, label: str) -> tuple[str, str]:
    """The pre-change version of a merged request whose merge commit has one parent.

    That is a squash (the request is one commit on the base branch: the version before is the
    parent), a rebase merge or a fast-forward (the request's commits are on the base branch one
    after the other, `head` is the last: the version before is `n` commits back along first
    parents, n being the request's commit count). The host does not say which, so both readings
    are tried and the one whose files equal the request's files wins: the count the host gives,
    and the paths of the request's own diff when its head can be fetched. Neither fitting, or
    both, is refused with a blocking `pre_change_unavailable`."""
    count = cr.commit_count
    if count == 1:
        return parent, "first parent of the merge commit"
    refuse = lambda detail: _refuse_pre_change(git, cr, label, detail)  # noqa: E731
    if count is None:
        refuse("the host gives no commit count for the merged request, so a rebase merge cannot be told from a squash")
    own = _own_paths(git, remote, cr)
    readings: list[tuple[str, str]] = [(parent, "first parent of the merge commit (squash merge: its files are the request's files)")]
    back = git.first_parent_ancestor(remote, head, count)
    if back.is_found:
        readings.append((back.text, f"{count} commits before the merge commit (rebase or fast-forward merge: its files are the request's files)"))
    fitting = []
    for pre, why in readings:
        paths = _paths(git, remote, pre, head)
        if paths is not None and len(paths) == cr.file_count and (own is None or paths == own):
            fitting.append((pre, why))
    if len(fitting) == 1:
        return fitting[0]
    if not fitting:
        refuse(f"neither reading of the merge commit fits the request's {cr.file_count} files "
               f"(first parent of the merge commit; {count} commits before it)")
    refuse(f"both readings of the merge commit (first parent; {count} commits before it) fit the request's {cr.file_count} files, "
           "so a squash cannot be told from a rebase merge")
    raise AssertionError("unreachable")


def _refuse_pre_change(git: Git, cr: ChangeRequest, label: str, detail: str) -> None:
    rec = record(git.target, "pre_change_unavailable", cr.head_ref, detail=detail)
    raise ChangeSetRefused(f"refusing to continue with {label}: the version before the change cannot be established: {detail}", [rec])


def title_for_commit(git: Git, host: Any, remote: str, repo: str, sha: str) -> ReadResult:
    """The pull request title behind a squash commit: follows `(#N)` (GitLab `(!N)`) at the end
    of the commit's subject to the host. Found (the title), absent (the subject names no
    change request, or the host has none with that number), unreadable (recorded)."""
    subject = git.commit_subject(remote, sha)
    if not subject.is_found:
        return subject
    match = _SQUASH_SUFFIX.search(subject.text)
    if not match:
        return ReadResult.absent()
    try:
        return host.title_of(repo, int(match.group(1)))
    except HostUnsupported:
        return ReadResult.absent()
