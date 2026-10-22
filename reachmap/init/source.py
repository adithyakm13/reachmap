"""Repository specs and reading a repository through the git layer (context 13.5: git does most of the work).

A spec is `github:owner/name`, `gitlab:group/name`, `bitbucket:ws/name`, `git:<url>`, a local path or a `file://` URL, optionally
written `NAME=SPEC` to choose the name the repository has in the config. A host spec needs no host API call: the clone URL is the
host's web address (`hosts` `remote`), and everything else is read with git: the default branch (`ls-remote --symref`), the
branch heads, and a checkout of the default branch at the pinned commit.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from reachmap import hosts, redact
from reachmap.gitlayer import Git
from reachmap.init.guess import SpecError
from reachmap.init.tree import Tree
from reachmap.model import Read

__all__ = ["Opened", "Spec", "name_collisions", "open_repo", "parse_spec"]

HOST_PREFIXES = ("github", "gitlab", "bitbucket")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*$")
_FALLBACK_BRANCHES = ("main", "master", "trunk", "develop")


@dataclass(frozen=True, slots=True)
class Spec:
    raw: str  # as the user wrote it (never printed with a credential: see `shown`)
    kind: str  # github | gitlab | bitbucket | git | local
    ref: str  # owner/name, a URL or a path
    name: str  # the name in the config

    @property
    def shown(self) -> str:
        return redact.scrub(redact.strip_userinfo(self.raw))


def parse_spec(text: str) -> Spec:
    """Parse one spec. Raises `ValueError` (a usage problem, with the form that was expected) for text that is no spec."""
    raw = text.strip()
    if not raw:
        raise ValueError("an empty repository spec")
    name: str | None = None
    left, eq, right = raw.partition("=")
    if eq and right and ":" not in left and _NAME.match(left):
        name, raw = left, right
    shown = redact.scrub(redact.strip_userinfo(raw))
    if raw.startswith("-"):
        raise ValueError(f"{shown}: a spec may not start with a dash")
    prefix, colon, rest = raw.partition(":")
    if colon and prefix in HOST_PREFIXES:
        try:
            parts = hosts.split_repo(rest, min_parts=2, max_parts=None if prefix == "gitlab" else 2)
        except ValueError:
            raise ValueError(f"{shown}: write {prefix}:{'group/name' if prefix == 'gitlab' else 'owner/name'}") from None
        return Spec(raw, prefix, "/".join(parts), name or "/".join(parts))
    if colon and prefix == "git":
        if not rest.strip() or rest.startswith("-"):
            raise ValueError("git: needs a URL, as in git:https://example.com/acme/web.git")
        return Spec(raw, "git", rest, name or derive_name(rest))
    if raw.startswith("file://"):
        path = _file_path(raw)
        return Spec(raw, "local", path, name or derive_name(path))
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", raw) or re.match(r"^[\w.-]+@[\w.-]+:", raw):
        raise ValueError(f"{shown}: a remote URL is written git:<url> (or github:owner/name for GitHub)")
    path = os.path.abspath(os.path.expanduser(raw))
    return Spec(raw, "local", path, name or derive_name(path))


def _file_path(url: str) -> str:
    parts = urlsplit(url)
    if parts.netloc not in ("", "localhost"):
        raise ValueError(f"{url}: a file:// URL names a path on this machine (file:///path/to/repo)")
    return unquote(parts.path)


def derive_name(where: str) -> str:
    """The name a repository gets when the user gives none: the last two path parts of a URL (`owner/name`), the folder name
    of a local path, without a `.git` suffix."""
    path = where
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", where):
        path = urlsplit(where).path
    elif re.match(r"^[\w.-]+@[\w.-]+:", where):
        path = where.partition(":")[2]
    parts = [p for p in re.split(r"[\\/]", path) if p]
    if parts and parts[-1].endswith(".git"):
        parts[-1] = parts[-1][:-4]
    parts = [p for p in parts if p]
    if not parts:
        return "repo"
    local = not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://|^[\w.-]+@[\w.-]+:", where)
    keep = parts[-1:] if local else parts[-2:]
    return "/".join(re.sub(r"[^A-Za-z0-9._-]+", "-", p).strip("-") or "repo" for p in keep)


def name_collisions(specs: list[Spec]) -> list[str]:
    seen: dict[str, str] = {}
    out: list[str] = []
    for s in specs:
        if s.name in seen:
            out.append(f"{s.name}: {seen[s.name]} and {s.shown} get the same name; write NAME=SPEC to choose one")
        else:
            seen[s.name] = s.shown
    return out


@dataclass(slots=True)
class Opened:
    spec: Spec
    remote: str  # what git reads
    config_remote: str | None  # what the config says (None: the host derives it)
    local: bool
    branch: str
    heads: tuple[str, ...]
    sha: str
    tree: Tree
    notes: list[str]  # what was done to the remote, and what git could not say
    branch_note: str | None  # why the default branch is a guess (HEAD names no branch), or None when HEAD named it


def open_repo(spec: Spec, git: Git) -> Opened:
    """Read the repository: raises `SpecError` with a scrubbed reason when git cannot (a missing repository, no access, an empty
    repository, no HEAD). Everything it reads is also recorded by the git layer."""
    notes: list[str] = []
    local = False
    if spec.kind == "local":
        path = Path(spec.ref)
        if not path.is_dir():
            raise SpecError(spec.shown, "no such directory")
        remote, config_remote, local = str(path), str(path), True
    elif spec.kind == "git":
        remote = spec.ref
        config_remote = redact.transport_url(remote)
        if config_remote != remote:
            notes.append("credentials were removed from the remote: git's own login supplies them")
    else:
        remote = hosts.get_host(spec.kind).remote(spec.ref)
        config_remote = None
    label = spec.shown
    heads = git.heads(remote)
    if heads.state == Read.UNREADABLE:
        raise SpecError(label, f"the remote could not be read: {redact.for_record(heads.detail)}")
    if heads.state == Read.ABSENT:
        raise SpecError(label, "the repository has no branches (is it empty?)")
    names = tuple(heads.heads)
    branch_note = None
    head = git.default_branch(remote)
    if head.state == Read.UNREADABLE:
        raise SpecError(label, f"HEAD could not be read: {redact.for_record(head.detail)}")
    if head.state == Read.FOUND and head.text in names:
        branch = head.text
    else:
        branch = next((b for b in _FALLBACK_BRANCHES if b in names), names[0])
        branch_note = ("the remote's HEAD names no branch that exists, so " if head.state == Read.FOUND else "the remote's HEAD is not a symbolic ref, so ") + \
            f"{branch} was taken as the default branch"
    pinned = git.pin_read(remote, branch, kind="branch")
    if pinned.state != Read.FOUND:
        raise SpecError(label, f"{branch} could not be read: {redact.for_record(pinned.detail or 'no such branch')}")
    sha = pinned.text
    try:
        checkout = git.checkout(remote, sha)
    except Exception as exc:  # ReadFailed (the record was made) or a ValueError for an odd remote
        raise SpecError(label, f"{branch} could not be checked out: {redact.for_record(getattr(exc, 'detail', None) or str(exc))}") from None
    return Opened(spec, remote, config_remote, local, branch, names, sha, Tree.from_checkout(checkout), notes, branch_note)
