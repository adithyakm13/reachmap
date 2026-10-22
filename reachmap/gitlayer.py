"""The git layer: every read of code goes through here (context 4 rules 5, 9, 11; 5 Shape A).

Written once, the same for every host (context 13.5). It answers every read three ways,
with the model's `ReadResult`: found, absent (git positively says so), unreadable (anything
else: a failed command, a timeout, a missing object, a remote that is gone). It records
every failure and every cut centrally, in the target given to `Git(record_to=...)` (a list
or an `Analysis`), through the incompleteness registry, so a caller cannot forget to.

Layout of the cache (`$REACHMAP_CACHE`, default `~/.cache/reachmap`, never inside an
analysed repository):

    repos/<sha256 of the remote URL without userinfo>.git     one bare repository per remote
    checkouts/<same hash>/<commit sha>/                       a materialised tree, cached by sha

History questions (pin, merge base, diff, file reads) go to the bare repository, which
holds commits and trees (a blobless partial clone when the server allows filters, a full
fetch otherwise) and fetches blobs on demand. Search and bulk reads use a checkout.

Credentials come from the user's own git credential helper or the host CLI. A password or
token in a remote URL is removed before git sees it, so it reaches no argv, no cache path,
no bare repository configuration, no message (all text passes through `redact`).
"""

from __future__ import annotations

import contextlib
import fnmatch
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import unicodedata
from typing import Any, Iterator, Mapping, Sequence

from reachmap import diffparse, redact
from reachmap.diffparse import ChangeSet
from reachmap.errors import ModelError, UnreadableError
from reachmap.incomplete import record
from reachmap.model import Read, ReadResult

__all__ = [
    "Checkout", "GitResult", "GitRunner", "Git", "GrepMatch", "HeadsResult", "IncompleteSearch",
    "ReadFailed", "RefNotFound", "SearchResult", "default_cache", "is_safe_relpath",
    "DEFAULT_MAX_BYTES",
]

DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_TIMEOUT = 120.0
DEFAULT_NETWORK_TIMEOUT = 600.0
GREP_CHUNK = 400  # file names per `git grep` call when a search is limited to a list of files
FILES_AT_CHUNK = 200  # paths per `git ls-tree` call in `files_at`

_FULL_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_ABBREV_SHA = re.compile(r"^[0-9a-f]{7,63}$")

# Options on every git call. They make output independent of the user's configuration and
# keep git from running anything the remote could name.
_BASE_CONFIG = (
    # output the code parses, whatever the user's global configuration says
    "core.quotePath=true", "color.ui=false", "core.pager=cat", "diff.submodule=short", "diff.ignoreSubmodules=none",
    "diff.noprefix=false", "diff.mnemonicPrefix=false", "diff.relative=false", "diff.interHunkContext=0",
    "diff.algorithm=myers", "diff.indentHeuristic=true", "diff.suppressBlankEmpty=false", "diff.renames=true",
    "grep.column=false", "grep.fullName=false", "grep.patternType=default", "log.showSignature=false",
    # nothing the remote or the repository could make git run
    "core.fsmonitor=false", "protocol.ext.allow=never", "gc.auto=0", "advice.detachedHead=false",
    "fetch.fsckObjects=false",
)
# GIT_* variables of the user's environment that are kept: the credential helper and
# transport settings. Every other GIT_* variable is dropped (GIT_DIR, GIT_TRACE, ...).
_KEEP_GIT_VARS = {"GIT_ASKPASS", "GIT_SSH", "GIT_SSH_COMMAND", "GIT_SSH_VARIANT", "GIT_PROXY_COMMAND"}
_KEEP_GIT_PREFIXES = ("GIT_SSL_", "GIT_HTTP_")
# Host token variables git has no use for: the environment of a git process is visible to
# whatever git runs (hooks, credential helpers, ssh). GH_TOKEN and GITHUB_TOKEN stay: the
# `gh auth git-credential` helper reads them. Callers add their configured token_env names.
DEFAULT_DROP_ENV = ("GITLAB_TOKEN", "BITBUCKET_TOKEN")
MAX_REFS = 2000  # refs/reachmap/<sha> refs kept in a cache repository
RENAME_LIMIT = 5000


class ReadFailed(UnreadableError):
    """A read that could not be done. The incompleteness record was already made; `record`
    is it, and `detail` says why (scrubbed)."""

    def __init__(self, message: str, detail: str | None = None, record: Any = None) -> None:
        super().__init__(message, detail)
        self.record = record


class RefNotFound(ModelError):
    """The remote answered, and has no such ref (or no commit on it at that time)."""


class IncompleteSearch(ModelError):
    """The full list of matches was asked of a search that was cut short or failed."""


class _Fail(Exception):
    """Internal: a step failed; the message is the detail of the unreadable record."""


# ---------------------------------------------------------------------------- the runner


@dataclass(frozen=True, slots=True)
class GitResult:
    returncode: int
    stdout: bytes
    stderr: str  # decoded and scrubbed
    timed_out: bool = False
    cut: bool = False  # stopped on purpose after `stop_after_lines`
    spawn_error: str | None = None
    timeout: float = 0.0  # the limit that applied

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and self.spawn_error is None


REGISTRY_ENV = "REACHMAP_PROCESS_REGISTRY"


def register_process_group(pgid: int) -> None:
    """When the runner asked for it (the variable names a file of its own for this job), note the process group of a process started in a group of
    its own, one line, so that the runner can end it even when this process is killed and cannot clean up. Nothing is ever removed from the file,
    and a failure to write it is not an error of the analysis."""
    path = os.environ.get(REGISTRY_ENV)
    if not path:
        return
    try:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            os.write(fd, f"{int(pgid)}\n".encode("ascii"))
        finally:
            os.close(fd)
    except (OSError, ValueError):
        pass


def _kill(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


class GitRunner:
    """Runs git with an isolated environment, a per-call timeout and no pager.

    `env` overrides the environment (tests use it to cut git off from the user's config);
    `extra_config` is a list of `key=value` strings passed as `-c`. A timeout kills the
    whole process group and is reported in the result, never as an empty answer."""

    def __init__(
        self,
        *,
        git: str = "git",
        env: Mapping[str, str] | None = None,
        extra_config: Sequence[str] = (),
        timeout: float = DEFAULT_TIMEOUT,
        network_timeout: float = DEFAULT_NETWORK_TIMEOUT,
        drop_env: Sequence[str] = (),
    ) -> None:
        self.git = git
        self.drop_env = frozenset((*DEFAULT_DROP_ENV, *drop_env))
        self.overrides = dict(env or {})
        self.extra_config = tuple(extra_config)
        self.timeout = timeout
        self.network_timeout = network_timeout

    def environment(self) -> dict[str, str]:
        env = {
            k: v for k, v in os.environ.items()
            if (not k.startswith("GIT_") or k in _KEEP_GIT_VARS or k.startswith(_KEEP_GIT_PREFIXES)) and k not in self.drop_env
        }
        env.update(
            GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0", GIT_PAGER="cat", PAGER="cat",
            LC_ALL="C", LANG="C", GIT_LITERAL_PATHSPECS="1", GIT_OPTIONAL_LOCKS="0",
        )
        if "GIT_SSH" not in env and "GIT_SSH_COMMAND" not in env:
            env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"  # never wait for a password prompt
        env.update(self.overrides)
        return env

    def run(
        self,
        args: Sequence[str | bytes],
        *,
        git_dir: str | os.PathLike | None = None,
        cwd: str | os.PathLike | None = None,
        input: bytes | None = None,
        timeout: float | None = None,
        network: bool = False,
        stop_after_lines: int | None = None,
        lazy: bool = True,
    ) -> GitResult:
        limit = timeout if timeout is not None else (self.network_timeout if network else self.timeout)
        argv = [self.git]
        for item in (*_BASE_CONFIG, *self.extra_config):
            argv += ["-c", item]
        if git_dir is not None:
            argv += ["--git-dir", str(git_dir)]
        argv += list(args)
        env = self.environment()
        if not lazy:
            env["GIT_NO_LAZY_FETCH"] = "1"  # do not fetch a missing object just to look
        try:
            proc = subprocess.Popen(
                argv, cwd=cwd, env=env, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
            )
        except OSError as exc:
            return GitResult(-1, b"", "", spawn_error=f"cannot run git: {redact.scrub_exception(exc)}", timeout=limit)
        register_process_group(proc.pid)  # start_new_session: its process group is its pid
        timed_out = threading.Event()

        def on_timeout() -> None:
            timed_out.set()
            _kill(proc)

        timer = threading.Timer(limit, on_timeout)
        timer.daemon = True
        timer.start()
        cut = False
        try:
            if stop_after_lines is None:
                out, err = proc.communicate(input)
            else:
                out, err, cut = self._stream(proc, stop_after_lines, input)
        finally:
            timer.cancel()
        return GitResult(
            returncode=proc.returncode if proc.returncode is not None else -1,
            stdout=out,
            stderr=redact.scrub(err.decode("utf-8", errors="replace")).strip(),
            timed_out=timed_out.is_set(),
            cut=cut and not timed_out.is_set(),
            timeout=limit,
        )

    @staticmethod
    def _stream(proc: subprocess.Popen, stop_after_lines: int, input: bytes | None) -> tuple[bytes, bytes, bool]:
        err_parts: list[bytes] = []
        drain = threading.Thread(target=lambda: err_parts.append(proc.stderr.read()), daemon=True)
        drain.start()
        if input is not None:
            def feed() -> None:
                try:
                    proc.stdin.write(input)
                    proc.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
            threading.Thread(target=feed, daemon=True).start()
        lines: list[bytes] = []
        cut = False
        for raw in iter(proc.stdout.readline, b""):
            lines.append(raw)
            if len(lines) >= stop_after_lines:
                cut = True
                _kill(proc)
                break
        proc.stdout.close()
        proc.wait()
        drain.join(timeout=5)
        proc.stderr.close()
        return b"".join(lines), b"".join(err_parts), cut


# ---------------------------------------------------------------------------- results


@dataclass(frozen=True, slots=True)
class HeadsResult:
    """Branch heads of a remote: `heads` maps a branch name (slashes kept) to its sha. Like a
    ReadResult it has a state, and `heads` only exists when it is found."""

    state: str
    _heads: Mapping[str, str]
    detail: str | None = None

    @property
    def heads(self) -> Mapping[str, str]:
        if self.state == Read.UNREADABLE:
            raise UnreadableError(f"the branch list was unreadable: {self.detail}", self.detail)
        if self.state == Read.ABSENT:
            raise ModelError("no branch matched: there is no list of heads")
        return self._heads

    @property
    def is_found(self) -> bool:
        return self.state == Read.FOUND

    @property
    def tags(self) -> Mapping[str, str]:
        """The same mapping when the listing is of tags (`Git.tags`): a tag name to the commit it names."""
        return self.heads


@dataclass(frozen=True, slots=True)
class GrepMatch:
    path: str
    line: int
    text: str  # the line, without the newline and without a trailing carriage return


@dataclass(frozen=True, slots=True)
class SearchResult:
    """The result of a search at one commit.

    `state`: found (at least one match), absent (a complete search with no match, git's
    exit code 1 with nothing on stderr), unreadable (git failed, wrote errors, or timed out,
    or the search was cut by `examine_cap` and found nothing in what it examined).
    `truncated_by` is None, "examine_cap" (recorded as `search_truncated`) or "result_cap"
    (nothing recorded: it claims nothing absent). When it is set the matches are "at least"
    these: `at_least` is True and `complete` False.

    `matches` is the complete list: it raises `UnreadableError` for an unreadable search and
    `IncompleteSearch` for one that was cut, so neither can read as the whole answer.
    `partial_matches` is what was found, for a caller that knows it is partial.
    `examined` is the number of files searched, None when a result cap stopped the search
    early (the number is then unknown); `total` is the number of files that could be searched.
    `not_searched` lists the selected entries git grep does not search at all (symlinks,
    submodules) with the reason: a search that says absent covers the other files only."""

    state: str
    pattern: str
    sha: str
    _matches: tuple[GrepMatch, ...]
    examined: int | None
    total: int
    truncated_by: str | None = None
    detail: str | None = None
    not_searched: tuple[tuple[str, str], ...] = ()

    @property
    def partial_matches(self) -> tuple[GrepMatch, ...]:
        if self.state == Read.UNREADABLE:
            raise UnreadableError(f"the search was unreadable: {self.detail}", self.detail)
        return self._matches

    @property
    def matches(self) -> tuple[GrepMatch, ...]:
        found = self.partial_matches
        if self.truncated_by is not None:
            raise IncompleteSearch(
                f"the search for {self.pattern!r} was cut short ({self.truncated_by}): the matches are at least these, "
                "not the complete list (use partial_matches)"
            )
        return found

    @property
    def complete(self) -> bool:
        return self.state != Read.UNREADABLE and self.truncated_by is None

    @property
    def at_least(self) -> bool:
        return self.truncated_by is not None


@dataclass(frozen=True, slots=True)
class Checkout:
    """A read-only materialised tree at exactly `sha`. `files` lists the regular files in it
    (repository relative, tree order). `skipped` lists what was not written, with the reason:
    symlinks, submodules and paths that are not safe to write. Read those with `file_at`."""

    remote: str  # without any password or token
    sha: str
    path: Path
    files: tuple[str, ...]
    skipped: tuple[tuple[str, str], ...] = ()


def default_cache(env: Mapping[str, str] | None = None) -> Path:
    source = os.environ if env is None else env
    given = source.get("REACHMAP_CACHE")
    return Path(given).expanduser().absolute() if given else Path.home() / ".cache" / "reachmap"


def is_safe_relpath(path: str) -> bool:
    """A repository relative POSIX path that may be written under a checkout: no empty,
    `.` or `..` part, no `.git` part (any case), no NUL, not absolute."""
    if not isinstance(path, str) or not path or path.startswith("/") or "\0" in path:
        return False
    return all(part not in ("", ".", "..") and part.lower() != ".git" for part in path.split("/"))


def _is_sha(text: str) -> bool:
    return isinstance(text, str) and bool(_FULL_SHA.match(text))


# ---------------------------------------------------------------------------- the layer


class Git:
    """The git layer. One instance per run; cheap to make."""

    def __init__(
        self,
        cache: str | os.PathLike | None = None,
        *,
        runner: GitRunner | None = None,
        record_to: Any = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        git: str = "git",
        env: Mapping[str, str] | None = None,
        extra_config: Sequence[str] = (),
        timeout: float = DEFAULT_TIMEOUT,
        network_timeout: float = DEFAULT_NETWORK_TIMEOUT,
        drop_env: Sequence[str] = (),
        max_refs: int = MAX_REFS,
        rename_limit: int = RENAME_LIMIT,
    ) -> None:
        self.cache = Path(cache).expanduser().absolute() if cache is not None else default_cache()
        self.runner = runner or GitRunner(
            git=git, env=env, extra_config=extra_config, timeout=timeout, network_timeout=network_timeout, drop_env=drop_env,
        )
        self.target: Any = record_to if record_to is not None else []
        self.max_bytes = max_bytes
        self.max_refs = max_refs
        self.rename_limit = rename_limit
        self._last_record: Any = None

    @contextlib.contextmanager
    def quiet(self) -> Iterator[list]:
        """Reads made inside the block record nothing in the real target (they go to a
        throwaway list, yielded): for a check whose failure is not itself a finding."""
        saved, self.target = self.target, []
        try:
            yield self.target
        finally:
            self.target = saved

    # ---- recording

    @property
    def incompleteness(self) -> list:
        """Every record this layer made, in order."""
        if isinstance(self.target, list):
            return list(self.target)
        return list(self.target.incompleteness)

    def _unreadable(self, subject: str, detail: str, *, kind: str = "read_unreadable") -> ReadResult:
        clean = redact.for_record(detail) or "the read failed"
        self._last_record = record(self.target, kind, redact.for_record(subject, 300), detail=clean)
        return ReadResult.unreadable(clean)

    def _raise(self, subject: str, detail: str) -> None:
        result = self._unreadable(subject, detail)
        raise ReadFailed(f"unreadable: {subject}: {result.detail}", result.detail, self._last_record)

    @staticmethod
    def label(remote: str) -> str:
        return redact.scrub(redact.strip_userinfo(str(remote)))

    # ---- cache

    def cache_path(self, remote: str) -> Path:
        """The bare repository of a remote: named by the hash of its URL without userinfo."""
        key = hashlib.sha256(redact.strip_userinfo(str(remote)).encode("utf-8")).hexdigest()
        return self.cache / "repos" / f"{key}.git"

    def _checkout_root(self, remote: str) -> Path:
        return self.cache / "checkouts" / self.cache_path(remote).name[: -len(".git")]

    def _forbid_cache_in_remote(self, remote: str) -> None:
        local: Path | None = None
        text = str(remote)
        if text.startswith("file://"):
            local = Path(text[len("file://"):])
        elif "://" not in text and not re.match(r"^[^/\s@:]+@?[^/\s@:]+:(?!//)", text):
            local = Path(text)
        if local is None:
            return
        try:
            repo = local.resolve()
            cache = self.cache.resolve()
        except OSError:
            return
        if cache == repo or repo in cache.parents:
            raise ValueError(f"the cache {cache} is inside the analysed repository {repo}: use a cache directory outside it")

    def _bare(self, remote: str) -> Path:
        if not isinstance(remote, str) or not remote.strip() or remote.lstrip().startswith("-"):
            raise ValueError("a remote is a URL or a path, and may not start with a dash (git would read it as an option)")
        self._forbid_cache_in_remote(remote)
        bare = self.cache_path(remote)
        if (bare / "HEAD").exists():
            return bare
        bare.parent.mkdir(parents=True, exist_ok=True)
        tmp = bare.parent / f"{bare.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        try:
            res = self.runner.run(["init", "--bare", "-q", str(tmp)])
            if not res.ok:
                raise _Fail(self._describe(res, "init"))
            res = self.runner.run(["remote", "add", "origin", "--", redact.transport_url(str(remote))], git_dir=tmp)
            if not res.ok:
                raise _Fail(self._describe(res, "remote add"))
            try:
                os.rename(tmp, bare)
            except OSError:
                if not (bare / "HEAD").exists():
                    raise _Fail("cannot create the cache repository") from None
        finally:
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)
        return bare

    # ---- running git

    @staticmethod
    def _describe(res: GitResult, action: str) -> str:
        if res.spawn_error:
            return res.spawn_error
        if res.timed_out:
            return f"git {action} timed out after {_secs(res.timeout)}s"
        return res.stderr or f"git {action} failed with exit code {res.returncode}"

    def _run(self, bare: Path, args: Sequence[str], **kw: Any) -> GitResult:
        return self.runner.run(args, git_dir=bare, **kw)

    def _has_commit(self, bare: Path, sha: str) -> bool:
        return self._run(bare, ["cat-file", "-e", f"{sha}^{{commit}}"], lazy=False).returncode == 0

    def _fetch(self, bare: Path, refspecs: Sequence[str], *, blobless: bool = True) -> GitResult:
        args = ["fetch", "--no-tags", "--no-write-fetch-head", "--quiet"]
        if blobless:
            args.append("--filter=blob:none")
        res = self._run(bare, [*args, "origin", *refspecs], network=True)
        if blobless and not res.ok and not res.timed_out and "filter" in res.stderr.lower():
            # a server that refuses filters: fall back to a full fetch
            for key in ("remote.origin.promisor", "remote.origin.partialclonefilter"):
                self._run(bare, ["config", "--unset", key])
            res = self._fetch(bare, refspecs, blobless=False)
        return res

    def _ensure_commit(self, remote: str, sha: str) -> Path:
        """The bare repository, holding the commit `sha`; fetches what is needed."""
        bare = self._bare(remote)
        if self._has_commit(bare, sha):
            return bare
        first = self._fetch(bare, [f"+{sha}:refs/reachmap/{sha}"])
        if first.ok and self._has_commit(bare, sha):
            self._prune_refs(bare)
            return bare
        # the server may not serve an unadvertised sha: take every branch and tag
        second = self._fetch(bare, ["+refs/heads/*:refs/reachmap/heads/*", "+refs/tags/*:refs/reachmap/tags/*"])
        if second.ok and self._has_commit(bare, sha):
            return bare
        failure = second if not second.ok else first
        reason = self._describe(failure, "fetch") if not failure.ok else f"commit {sha[:12]} is not on the remote"
        raise _Fail(f"commit {sha[:12]} is not available: {reason}")

    def _prune_refs(self, bare: Path) -> None:
        """Keep at most `max_refs` of the refs/reachmap/<sha> refs (the newest commits): they
        only keep fetched commits from being collected, and would otherwise grow without bound."""
        res = self._run(bare, ["for-each-ref", "--sort=-committerdate", "--format=%(refname)", "refs/reachmap/"])
        if not res.ok:
            return
        mine = [r for r in res.stdout.decode("utf-8", errors="replace").split() if re.fullmatch(r"refs/reachmap/[0-9a-f]{40,64}", r)]
        extra = mine[self.max_refs:]
        if extra:
            self._run(bare, ["update-ref", "--stdin"], input="".join(f"delete {r}\n" for r in extra).encode())

    def _ls_remote(self, remote: str, patterns: Sequence[str], *, heads: bool = False) -> list[tuple[str, str]]:
        bare = self._bare(remote)
        args = ["ls-remote"] + (["--heads"] if heads else []) + ["--", redact.transport_url(str(remote)), *patterns]
        res = self._run(bare, args, network=True)
        if not res.ok:
            raise _Fail(self._describe(res, "ls-remote"))
        out: list[tuple[str, str]] = []
        for line in res.stdout.decode("utf-8", errors="replace").splitlines():
            sha, _, ref = line.partition("\t")
            if sha and ref:
                out.append((ref, sha))
        return out

    # ---- heads

    def heads(self, remote: str, patterns: Sequence[str] | None = None) -> HeadsResult:
        """Branch heads whose name matches any glob pattern (`release/*` matches `release/1.x`;
        an empty list matches every branch). State: found, absent (nothing matched), unreadable."""
        subject = f"branch heads of {self.label(remote)}"
        try:
            refs = self._ls_remote(remote, [], heads=True)
        except _Fail as exc:
            return HeadsResult(Read.UNREADABLE, {}, self._unreadable(subject, str(exc)).detail)
        found: dict[str, str] = {}
        for ref, sha in refs:
            if not ref.startswith("refs/heads/"):
                continue
            name = ref[len("refs/heads/"):]
            if not patterns or any(fnmatch.fnmatchcase(name, p) for p in patterns):
                found[name] = sha
        if not found:
            return HeadsResult(Read.ABSENT, {})
        return HeadsResult(Read.FOUND, dict(sorted(found.items())))

    def tags(self, remote: str, prefix: str | None = None, *, prefetch: bool = False) -> HeadsResult:
        """The tags of a remote (`git ls-remote --tags`), as a `HeadsResult`: `tags` maps a tag name to the commit it
        names (an annotated tag is peeled). `prefix` keeps the tags that start with it. State: found, absent (no tag
        matched), unreadable. With `prefetch` every tag is fetched into the cache in one request, so the ancestry
        questions that follow are answered locally (a failed prefetch is not an error: each commit is then fetched on
        its own, and a failure there is unreadable)."""
        subject = f"tags of {self.label(remote)}"
        try:
            refs = self._ls_remote(remote, ["refs/tags/*"])
        except _Fail as exc:
            return HeadsResult(Read.UNREADABLE, {}, self._unreadable(subject, str(exc)).detail)
        found: dict[str, str] = {}
        peeled: dict[str, str] = {}
        for ref, sha in refs:
            if not ref.startswith("refs/tags/"):
                continue
            name = ref[len("refs/tags/"):]
            if name.endswith("^{}"):
                peeled[name[:-3]] = sha
            else:
                found[name] = sha
        found.update({n: s for n, s in peeled.items() if n in found})
        if prefix:
            found = {n: s for n, s in found.items() if n.startswith(prefix)}
        if not found:
            return HeadsResult(Read.ABSENT, {})
        if prefetch:
            self._fetch(self._bare(remote), ["+refs/tags/*:refs/reachmap/tags/*"])
        return HeadsResult(Read.FOUND, dict(sorted(found.items())))

    def default_branch(self, remote: str) -> ReadResult:
        """The branch the remote's HEAD names (`git ls-remote --symref -- <remote> HEAD`), slashes kept. State: found (the
        branch name), absent (the remote answered and its HEAD is not a symbolic ref to a branch: a detached HEAD, an empty
        repository, a server that does not say), unreadable (the remote could not be asked; recorded)."""
        subject = f"HEAD of {self.label(remote)}"
        try:
            bare = self._bare(remote)
            res = self._run(bare, ["ls-remote", "--symref", "--", redact.transport_url(str(remote)), "HEAD"], network=True)
            if not res.ok:
                raise _Fail(self._describe(res, "ls-remote"))
        except _Fail as exc:
            return self._unreadable(subject, str(exc))
        for line in res.stdout.decode("utf-8", errors="replace").splitlines():
            head, _, ref = line.partition("\t")
            if head.startswith("ref: refs/heads/") and ref == "HEAD":
                name = head[len("ref: refs/heads/"):]
                if name:
                    return ReadResult.found(name)
        return ReadResult.absent()

    # ---- pin

    def pin_read(self, remote: str, ref: str, as_of: str | None = None, *, kind: str = "any") -> ReadResult:
        """A ref to a full commit sha, as a ReadResult: found (the sha), absent (the remote has
        no such ref, or no commit on it at or before `as_of`), unreadable.

        `kind` is "any", "branch" or "tag". A name that is both a branch and a tag is
        unreadable for "any" (never guessed): say which. `as_of` is ISO 8601 (a naive time
        is UTC); the commit is the last one on the first-parent line of the ref at or before
        that time (by committer date)."""
        if kind not in ("any", "branch", "tag"):
            raise ValueError(f"kind must be any, branch or tag, not {kind!r}")
        stamp = _parse_time(as_of) if as_of is not None else None
        subject = f"{ref} in {self.label(remote)}" + (f" as of {as_of}" if as_of else "")
        try:
            sha = self._resolve_ref(remote, ref, kind)
            if sha is None:
                return ReadResult.absent()
            bare = self._ensure_commit(remote, sha)
            res = self._run(bare, ["rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"])
            if not res.ok or not _is_sha(res.stdout.decode().strip()):
                raise _Fail(f"{ref} does not name a commit")
            sha = res.stdout.decode().strip()
            if stamp is not None:
                res = self._run(bare, ["rev-list", "-n", "1", "--first-parent", f"--before=@{stamp} +0000", sha])  # the zone: without it git reads a small epoch (before March 1973) as another kind of date and ignores it
                if not res.ok:
                    raise _Fail(self._describe(res, "rev-list"))
                found = res.stdout.decode().strip()
                if not found:
                    self._need_full_history(bare, sha, "no commit that old was seen")
                    return ReadResult.absent()
                sha = found
            return ReadResult.found(sha)
        except _Fail as exc:
            return self._unreadable(subject, str(exc))

    def _resolve_ref(self, remote: str, ref: str, kind: str) -> str | None:
        """The sha a ref names on the remote (a tag may be a tag object: peeled later), None
        when the remote has no such ref. Raises _Fail when it cannot be told."""
        if _is_sha(ref):
            return ref
        if ref.startswith("refs/"):
            names = {"ref": ref}
            patterns = [ref, f"{ref}^{{}}"]
        elif ref == "HEAD":
            names = {"ref": "HEAD"}
            patterns = ["HEAD"]
        else:
            names = {"head": f"refs/heads/{ref}", "tag": f"refs/tags/{ref}"}
            patterns = [names["head"], names["tag"], f"{names['tag']}^{{}}"]
        listed = dict(self._ls_remote(remote, patterns))  # ls-remote matches the tail of a name: keep exact ones only
        if "ref" in names:
            full = names["ref"]
            return listed.get(f"{full}^{{}}") or listed.get(full)
        head = listed.get(names["head"])
        tag_obj = listed.get(names["tag"])
        tag = listed.get(f"{names['tag']}^{{}}") or tag_obj
        if kind == "branch":
            return head
        if kind == "tag":
            return tag
        if head and tag:
            raise _Fail(f"{ref!r} is both a branch and a tag on the remote; say which one is meant")
        if head or tag:
            return head or tag
        if _ABBREV_SHA.match(ref):
            return self._expand_abbrev(remote, ref)
        return None

    def _expand_abbrev(self, remote: str, abbrev: str) -> str:
        """An abbreviated sha to the full one. A local miss is followed by a fetch of every
        branch and tag and a second try; a commit that is on neither is not found, but it is not
        known to be absent either, so that is unreadable. Never None."""
        bare = self._bare(remote)
        for attempt in (0, 1):
            res = self._run(bare, ["rev-parse", "--verify", "--quiet", f"{abbrev}^{{commit}}"], lazy=False)
            full = res.stdout.decode().strip()
            if res.ok and _is_sha(full):
                return full
            if res.returncode not in (0, 1):
                if "ambiguous" in res.stderr.lower():
                    raise _Fail(f"the abbreviation {abbrev} is ambiguous: {res.stderr}")
                raise _Fail(self._describe(res, "rev-parse"))
            if attempt == 0:
                fetched = self._fetch(bare, ["+refs/heads/*:refs/reachmap/heads/*", "+refs/tags/*:refs/reachmap/tags/*"])
                if not fetched.ok:
                    raise _Fail(f"the abbreviation {abbrev} was not expanded locally and the fetch failed: {self._describe(fetched, 'fetch')}")
        raise _Fail(
            f"the abbreviation {abbrev} was not expanded locally or among the branches and tags of the remote: "
            "no ref of that name exists and no known commit starts with it, which does not show that it is absent"
        )

    def _need_full_history(self, bare: Path, sha: str, what: str) -> None:
        """Raise when the cache is shallow: a history question answered "nothing there" from a
        truncated history is not an absence."""
        if (bare / "shallow").exists():
            count = self._run(bare, ["rev-list", "--count", sha])
            depth = count.stdout.decode().strip() if count.ok else "an unknown number of"
            raise _Fail(f"the history is shallow ({depth} commits are visible from {sha[:12]}): {what}, which does not show that it is absent")

    def pin(self, remote: str, ref: str, as_of: str | None = None, *, kind: str = "any") -> str:
        """`pin_read`, as a sha or an exception: `RefNotFound` when absent, `ReadFailed` (the
        record already made) when unreadable. Never returns a branch name."""
        res = self.pin_read(remote, ref, as_of, kind=kind)
        if res.is_found:
            return res.text
        if res.is_absent:
            what = f"no commit on {ref!r} at or before {as_of}" if as_of else f"no such ref: {ref!r}"
            raise RefNotFound(f"{what} in {self.label(remote)}")
        raise ReadFailed(f"unreadable: {ref} in {self.label(remote)}: {res.detail}", res.detail, self._last_record)

    # ---- merge base

    def merge_base(self, remote: str, a: str, b: str) -> ReadResult:
        """The merge base of two commit shas: found (its sha), absent when the histories are
        unrelated (git exit code 1, nothing on stderr, a history that is not shallow),
        unreadable on any other failure, including a history too short (the depth is in the
        detail)."""
        for name, value in (("a", a), ("b", b)):
            if not _is_sha(value):
                raise ValueError(f"merge_base needs full commit shas (pin the refs first); {name} is {value!r}")
        subject = f"merge base of {a[:12]} and {b[:12]} in {self.label(remote)}"
        try:
            bare = self._ensure_commit(remote, a)
            self._ensure_commit(remote, b)
            res = self._run(bare, ["merge-base", a, b])
            if res.ok:
                sha = res.stdout.decode().strip().splitlines()[0] if res.stdout.strip() else ""
                if not _is_sha(sha):
                    raise _Fail("git merge-base gave no commit")
                return ReadResult.found(sha)
            if res.returncode == 1 and not res.stderr and not res.timed_out:
                self._need_full_history(bare, a, "no merge base was seen")
                return ReadResult.absent()
            raise _Fail(self._describe(res, "merge-base"))
        except _Fail as exc:
            return self._unreadable(subject, str(exc))

    def is_ancestor(self, remote: str, ancestor: str, descendant: str) -> ReadResult:
        """Whether the commit `ancestor` is reachable from the commit `descendant` (`git merge-base --is-ancestor`; a
        commit is its own ancestor): found with the text `true` or `false`, unreadable on any failure, and for a
        `false` that only the shallow end of a history could explain. Never read as an answer: a version number."""
        for name, value in (("ancestor", ancestor), ("descendant", descendant)):
            if not _is_sha(value):
                raise ValueError(f"is_ancestor needs full commit shas (pin the refs first); {name} is {value!r}")
        subject = f"whether {ancestor[:12]} is an ancestor of {descendant[:12]} in {self.label(remote)}"
        try:
            bare = self._ensure_commit(remote, ancestor)
            self._ensure_commit(remote, descendant)
            res = self._run(bare, ["merge-base", "--is-ancestor", ancestor, descendant])
            if res.returncode == 0 and not res.timed_out:
                return ReadResult.found("true")
            if res.returncode == 1 and not res.stderr and not res.timed_out:
                self._need_full_history(bare, descendant, "the ancestry was not fully visible")
                return ReadResult.found("false")
            raise _Fail(self._describe(res, "merge-base --is-ancestor"))
        except _Fail as exc:
            return self._unreadable(subject, str(exc))

    def tags_containing(self, remote: str, sha: str, tags: Mapping[str, str]) -> tuple[list[str], list[str]]:
        """Which of `tags` (name to the commit it names, as `tags()` lists them) contain the commit `sha`, with one
        `git for-each-ref --contains` over the tags the cache holds (`tags(prefetch=True)` fetched them): (contained,
        undecided). A tag the cache does not hold at the commit the remote names, every tag when the cache is shallow, and
        every tag when git failed (a record is made) is undecided: ask `is_ancestor` for those. Never "none" for a failure."""
        if not _is_sha(sha):
            raise ValueError(f"tags_containing needs a full commit sha, not {sha!r}")
        names = sorted(tags)
        subject = f"which tags of {self.label(remote)} contain {sha[:12]}"
        try:
            bare = self._ensure_commit(remote, sha)
            if (bare / "shallow").exists():
                return [], names
            held = self._run(bare, ["for-each-ref", "--format=%(refname)%09%(objectname)%09%(*objectname)", "refs/reachmap/tags/"])
            if not held.ok:
                raise _Fail(self._describe(held, "for-each-ref"))
            local: dict[str, str] = {}
            for line in held.stdout.decode("utf-8", errors="replace").splitlines():
                ref, _, rest = line.partition("\t")
                obj, _, peeled = rest.partition("\t")
                local[ref[len("refs/reachmap/tags/"):]] = peeled or obj
            decided = {n for n in names if local.get(n) == tags[n]}
            hit = self._run(bare, ["for-each-ref", "--contains", sha, "--format=%(refname)", "refs/reachmap/tags/"])
            if not hit.ok:
                raise _Fail(self._describe(hit, "for-each-ref --contains"))
            contained = {r[len("refs/reachmap/tags/"):] for r in hit.stdout.decode("utf-8", errors="replace").split()}
            return [n for n in names if n in decided and n in contained], [n for n in names if n not in decided]
        except _Fail as exc:
            self._unreadable(subject, str(exc))
            return [], names

    # ---- commits

    def parents(self, remote: str, sha: str) -> ReadResult:
        """The parents of a commit, as found text of shas separated by spaces (empty for a root
        commit); unreadable on any failure, and for a commit that looks like a root only
        because the history is shallow."""
        if not _is_sha(sha):
            raise ValueError(f"parents needs a full commit sha, not {sha!r}")
        subject = f"parents of {sha[:12]} in {self.label(remote)}"
        try:
            bare = self._ensure_commit(remote, sha)
            res = self._run(bare, ["rev-list", "--parents", "-n", "1", sha])
            if not res.ok:
                raise _Fail(self._describe(res, "rev-list"))
            parts = res.stdout.decode("ascii", errors="replace").split()
            if not parts or parts[0] != sha or not all(_is_sha(p) for p in parts):
                raise _Fail("git rev-list gave an unexpected answer")
            if len(parts) == 1:
                self._need_full_history(bare, sha, "no parent was seen")
            return ReadResult.found(" ".join(parts[1:]))
        except _Fail as exc:
            return self._unreadable(subject, str(exc))

    def first_parent(self, remote: str, sha: str) -> ReadResult:
        """The first parent of a commit: found (its sha), absent for a root commit,
        unreadable on any failure (also a shallow history that ends at the commit)."""
        parents = self.parents(remote, sha)
        if not parents.is_found:
            return parents
        listed = parents.text.split()
        return ReadResult.found(listed[0]) if listed else ReadResult.absent()

    def first_parent_ancestor(self, remote: str, sha: str, n: int) -> ReadResult:
        """The commit `n` steps back along first parents (`sha~n`): found, absent when the
        history is shorter, unreadable on any failure (also a shallow history)."""
        if not _is_sha(sha) or isinstance(n, bool) or not isinstance(n, int) or n < 0:
            raise ValueError("first_parent_ancestor needs a full commit sha and a count of 0 or more")
        subject = f"{sha[:12]}~{n} in {self.label(remote)}"
        try:
            bare = self._ensure_commit(remote, sha)
            res = self._run(bare, ["rev-list", "--first-parent", f"--skip={n}", "-n", "1", sha])
            if not res.ok:
                raise _Fail(self._describe(res, "rev-list"))
            found = res.stdout.decode("ascii", errors="replace").strip()
            if not found:
                self._need_full_history(bare, sha, "the history looked shorter than asked")
                return ReadResult.absent()
            if not _is_sha(found):
                raise _Fail("git rev-list gave an unexpected answer")
            return ReadResult.found(found)
        except _Fail as exc:
            return self._unreadable(subject, str(exc))

    def commit_subject(self, remote: str, sha: str) -> ReadResult:
        """The first line of a commit message: found, or unreadable (a commit always has one,
        so there is no absent answer)."""
        if not _is_sha(sha):
            raise ValueError(f"commit_subject needs a full commit sha, not {sha!r}")
        subject = f"subject of {sha[:12]} in {self.label(remote)}"
        try:
            bare = self._ensure_commit(remote, sha)
            res = self._run(bare, ["log", "-n", "1", "--format=%s", sha, "--"])
            if not res.ok:
                raise _Fail(self._describe(res, "log"))
            return ReadResult.found(res.stdout.decode("utf-8", errors="replace").strip())
        except _Fail as exc:
            return self._unreadable(subject, str(exc))

    # ---- diff

    def diff(self, remote: str, base: str, head: str) -> ChangeSet:
        """The files changed from commit `base` to commit `head` (a plain tree comparison, two
        dots), renames detected, parsed with old and new line numbers. Raises `ReadFailed`
        (record made) when git cannot produce it."""
        for name, value in (("base", base), ("head", head)):
            if not _is_sha(value):
                raise ValueError(f"diff needs full commit shas (pin the refs first); {name} is {value!r}")
        subject = f"diff {base[:12]}..{head[:12]} in {self.label(remote)}"
        try:
            bare = self._ensure_commit(remote, base)
            self._ensure_commit(remote, head)
            res = self._run(bare, [
                "diff", "-M", "-l", str(self.rename_limit), "-U0", "--no-color", "--no-ext-diff", "--no-textconv",
                "--src-prefix=a/", "--dst-prefix=b/", base, head, "--",
            ])
            if not res.ok:
                raise _Fail(self._describe(res, "diff"))
            try:
                files = diffparse.parse(res.stdout)
            except diffparse.DiffParseError as exc:
                raise _Fail(f"the diff could not be parsed: {exc}") from None
            skipped = "rename detection was skipped" in res.stderr  # "inexact ..." or "exhaustive ..." by git version
            return ChangeSet(base_sha=base, head_sha=head, files=files, rename_detection_skipped=skipped)
        except _Fail as exc:
            self._raise(subject, str(exc))
            raise  # unreachable

    # ---- file_at

    def file_at(self, source: "str | Checkout", path: str, sha: str | None = None, *, max_bytes: int | None = None) -> ReadResult:
        """The text of `path` at commit `sha`: found, absent, unreadable.

        `path` may carry the bytes of a name that is not UTF-8 as surrogate escapes (what
        `FileChange.path` gives).

        Absent only when git positively reports that the path is not in the tree of that
        commit (`ls-tree` on a tree that was itself read). Everything else is unreadable and
        recorded: a missing object, a blob that could not be fetched (a partial clone whose
        remote is gone), a timeout, a directory or submodule, a file over `max_bytes` (default
        5 MB; files over 1 MB are read in full), text that is not UTF-8, a git error.
        `source` is a remote or a Checkout (then `sha` defaults to its own)."""
        if isinstance(source, Checkout):
            remote = source.remote
            if sha is None:
                sha = source.sha
            elif sha != source.sha:
                raise ValueError("the sha asked for is not the sha of the checkout")
        else:
            remote = source
        if not _is_sha(sha or ""):
            raise ValueError(f"file_at needs a full commit sha, not {sha!r}")
        limit = self.max_bytes if max_bytes is None else max_bytes
        subject = f"{path} at {sha[:12]} in {self.label(remote)}"
        if not is_safe_relpath(path):
            return self._unreadable(subject, "not a repository relative path")
        try:
            wanted = path.encode("utf-8", errors="surrogateescape")  # a name that is not UTF-8 comes back as its own bytes
        except UnicodeEncodeError:
            return self._unreadable(subject, "the path holds a character that stands for no byte of a file name")
        try:
            bare = self._ensure_commit(remote, sha)
            listing = self._run(bare, ["ls-tree", "-z", "-l", sha, "--", wanted])
            if not listing.ok:
                raise _Fail(self._describe(listing, "ls-tree"))
            entry = None
            for raw in listing.stdout.split(b"\0"):
                if not raw:
                    continue
                meta, _, name = raw.partition(b"\t")
                if name == wanted:
                    entry = meta.decode("ascii", errors="replace").split()
            if entry is None:
                return ReadResult.absent()  # the tree was read; the path is not in it
            mode, kind, oid, size_text = (entry + ["", "", "", ""])[:4]
            if kind == "tree":
                raise _Fail("the path is a directory")
            if kind == "commit":
                raise _Fail("the path is a submodule")
            if kind != "blob" or not size_text.isdigit():
                raise _Fail(f"unexpected tree entry: {' '.join(entry)}")
            size = int(size_text)
            if size > limit:
                raise _Fail(f"the file is {size} bytes, over max_bytes {limit}")
            blob = self._run(bare, ["cat-file", "blob", oid])
            if not blob.ok:
                raise _Fail(self._describe(blob, "cat-file"))
            if len(blob.stdout) != size:
                raise _Fail(f"read {len(blob.stdout)} bytes of a {size} byte file")
            try:
                return ReadResult.found(blob.stdout.decode("utf-8"))
            except UnicodeDecodeError as exc:
                raise _Fail(f"the file is not valid UTF-8 (byte {exc.start})") from None
        except _Fail as exc:
            return self._unreadable(subject, str(exc))

    def files_at(self, source: "str | Checkout", paths: Sequence[str], sha: str | None = None, *, max_bytes: int | None = None) -> dict[str, ReadResult]:
        """`file_at` for many paths of one commit, in a few git calls instead of two for each path: one recursive `ls-tree` per
        `FILES_AT_CHUNK` paths, one prefetch of the blobs a blobless clone lacks, one `cat-file --batch`. Each path gets the
        answer `file_at` would give it (found, absent only when the tree that was read lacks it, unreadable and recorded for
        anything else: a path that is not repository relative, a directory, a submodule, a file over `max_bytes`, text that is
        not UTF-8). When the commit itself cannot be had, every path is unreadable."""
        if isinstance(source, Checkout):
            remote = source.remote
            if sha is None:
                sha = source.sha
            elif sha != source.sha:
                raise ValueError("the sha asked for is not the sha of the checkout")
        else:
            remote = source
        if not _is_sha(sha or ""):
            raise ValueError(f"files_at needs a full commit sha, not {sha!r}")
        limit = self.max_bytes if max_bytes is None else max_bytes
        names = list(dict.fromkeys(paths))
        out: dict[str, ReadResult] = {}
        subject = lambda path: f"{path} at {sha[:12]} in {self.label(remote)}"  # noqa: E731
        wanted: dict[bytes, str] = {}
        for path in names:
            if not is_safe_relpath(path):
                out[path] = self._unreadable(subject(path), "not a repository relative path")
                continue
            try:
                wanted[path.encode("utf-8", errors="surrogateescape")] = path
            except UnicodeEncodeError:
                out[path] = self._unreadable(subject(path), "the path holds a character that stands for no byte of a file name")
        if not wanted:
            return {p: out[p] for p in names}
        try:
            bare = self._ensure_commit(remote, sha)
            entries: dict[bytes, list[str]] = {}
            keys = list(wanted)
            for start in range(0, len(keys), FILES_AT_CHUNK):
                # recursive, so that a path that is a directory shows its files (a listing that names two paths one inside the
                # other would otherwise show only the inner one) and is told from an absent path
                listing = self._run(bare, ["ls-tree", "-r", "-z", "-l", sha, "--", *keys[start:start + FILES_AT_CHUNK]])
                if not listing.ok:
                    raise _Fail(self._describe(listing, "ls-tree"))
                for raw in listing.stdout.split(b"\0"):
                    if raw:
                        meta, _, name = raw.partition(b"\t")
                        entries[name] = meta.decode("ascii", errors="replace").split()
            directories = {key for key in keys for name in entries if name.startswith(key + b"/")}
            blobs: dict[str, str] = {}  # path to the oid of the blob to read
            for key, path in wanted.items():
                entry = entries.get(key)
                if entry is None and key in directories:
                    out[path] = self._unreadable(subject(path), "the path is a directory")
                    continue
                if entry is None:
                    out[path] = ReadResult.absent()  # the tree was read; the path is not in it
                    continue
                mode, kind, oid, size_text = (entry + ["", "", "", ""])[:4]
                if kind == "tree":
                    out[path] = self._unreadable(subject(path), "the path is a directory")
                elif kind == "commit":
                    out[path] = self._unreadable(subject(path), "the path is a submodule")
                elif kind != "blob" or not size_text.isdigit():
                    out[path] = self._unreadable(subject(path), f"unexpected tree entry: {' '.join(entry)}")
                elif int(size_text) > limit:
                    out[path] = self._unreadable(subject(path), f"the file is {int(size_text)} bytes, over max_bytes {limit}")
                else:
                    blobs[path] = oid
            if blobs:
                distinct = list(dict.fromkeys(blobs.values()))
                self._prefetch(bare, distinct)
                texts = self._read_blobs(bare, distinct)
                for path, oid in blobs.items():
                    body = texts[oid]
                    if isinstance(body, str):
                        out[path] = self._unreadable(subject(path), body)
                        continue
                    try:
                        out[path] = ReadResult.found(body.decode("utf-8"))
                    except UnicodeDecodeError as exc:
                        out[path] = self._unreadable(subject(path), f"the file is not valid UTF-8 (byte {exc.start})")
        except _Fail as exc:
            for path in wanted.values():
                if path not in out:
                    out[path] = self._unreadable(subject(path), str(exc))
        return {p: out[p] for p in names}

    def _read_blobs(self, bare: Path, oids: list[str]) -> dict[str, "bytes | str"]:
        """The bytes of each blob through one `cat-file --batch` (a string is the reason one could not be read). A failed
        process raises `_Fail`: nothing was read."""
        res = self._run(bare, ["cat-file", "--batch"], input=("\n".join(oids) + "\n").encode())
        if not res.ok:
            raise _Fail(self._describe(res, "cat-file"))
        data, pos = res.stdout, 0
        out: dict[str, bytes | str] = {}
        for oid in oids:
            end = data.find(b"\n", pos)
            header = data[pos:end].decode("ascii", errors="replace").split() if end >= 0 else []
            if len(header) == 2 and header[1] == "missing":
                out[oid] = f"object {oid[:12]} is missing"
                pos = end + 1
                continue
            if len(header) != 3 or header[0] != oid or header[1] != "blob" or not header[2].isdigit():
                raise _Fail(f"object {oid[:12]} could not be read ({' '.join(header) or 'truncated output'})")
            size = int(header[2])
            body = data[end + 1:end + 1 + size]
            if len(body) != size:
                raise _Fail(f"object {oid[:12]} was cut short")
            out[oid] = body
            pos = end + 1 + size + 1
        return out

    # ---- checkout

    def checkout(self, remote: str, sha: str) -> Checkout:
        """A read-only materialised tree at exactly `sha`, cached by sha. Raises `ReadFailed`
        (record made) when the tree cannot be read completely."""
        if not _is_sha(sha):
            raise ValueError(f"checkout needs a full commit sha, not {sha!r}")
        subject = f"checkout of {sha[:12]} from {self.label(remote)}"
        try:
            return self._checkout(remote, sha)
        except _Fail as exc:
            self._raise(subject, str(exc))
            raise  # unreachable

    def _checkout(self, remote: str, sha: str) -> Checkout:
        bare = self._ensure_commit(remote, sha)
        root = self._checkout_root(remote)
        final = root / sha
        manifest = root / f"{sha}.json"
        shown = redact.transport_url(str(remote))
        if final.is_dir() and manifest.is_file():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                cached = Checkout(shown, sha, final, tuple(data["files"]), tuple((p, w) for p, w in data["skipped"]))
            except (OSError, ValueError, KeyError, TypeError):
                cached = None  # damaged: build it again
            if cached is not None:
                self._record_collisions(list(cached.skipped), f"checkout of {sha[:12]} from {self.label(remote)}")
                return cached
        entries = self._tree_entries(bare, sha, None)
        wanted: list[tuple[str, str, bool]] = []  # path, oid, executable
        skipped: list[tuple[str, str]] = []
        for raw_name, mode, kind, oid in entries:
            try:
                rel = raw_name.decode("utf-8")
            except UnicodeDecodeError:
                skipped.append((raw_name.decode("utf-8", errors="replace"), "name is not UTF-8 (read it with file_at)"))
                continue
            if kind == "commit":
                skipped.append((rel, "submodule"))
            elif mode == "120000":
                skipped.append((rel, "symlink"))
            elif kind != "blob":
                skipped.append((rel, f"unsupported entry {kind}"))
            elif not is_safe_relpath(rel):
                skipped.append((rel, "unsafe path"))
            else:
                wanted.append((rel, oid, mode == "100755"))
        colliding = _collisions([rel for rel, _oid, _x in wanted])
        if colliding:
            wanted = [item for item in wanted if item[0] not in colliding]
            skipped.extend((rel, COLLISION) for rel in sorted(colliding))
        self._record_collisions(skipped, f"checkout of {sha[:12]} from {self.label(remote)}")
        self._prefetch(bare, [oid for _, oid, _ in wanted])
        root.mkdir(parents=True, exist_ok=True)
        tmp = root / f"{sha}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        try:
            tmp.mkdir()
            for start in range(0, len(wanted), 500):
                self._write_blobs(bare, tmp, wanted[start:start + 500])
            for rel, _oid, _x in wanted:  # read only, once everything is written
                os.chmod(tmp / rel, os.stat(tmp / rel).st_mode & ~0o222)
            if final.exists():
                shutil.rmtree(final, ignore_errors=True)
            try:
                os.rename(tmp, final)
            except OSError as exc:
                if not final.is_dir():
                    raise _Fail(f"cannot place the checkout: {redact.scrub_exception(exc)}") from None
            files = tuple(rel for rel, _oid, _x in wanted)
            payload = json.dumps({"files": list(files), "skipped": [list(s) for s in skipped]})
            tmp_manifest = root / f"{sha}.json.tmp-{os.getpid()}"
            tmp_manifest.write_text(payload, encoding="utf-8")
            os.replace(tmp_manifest, manifest)
        except OSError as exc:
            raise _Fail(f"cannot write the checkout: {redact.scrub_exception(exc)}") from None
        finally:
            if tmp.exists():
                _rmtree_writable(tmp)
        return Checkout(shown, sha, final, files, tuple(skipped))

    def _tree_entries(self, bare: Path, sha: str, pathspecs: Sequence[bytes] | None) -> list[tuple[bytes, str, str, str]]:
        """Every entry of the tree at `sha` (recursively): (name bytes, mode, type, oid). A commit never changes, so a
        listing that was made is kept (every search at the commit lists the same tree); a failed one is not."""
        key = (str(bare), sha, tuple(pathspecs) if pathspecs else None)
        cache = self.__dict__.setdefault("_tree_cache", {})
        if key not in cache:
            cache[key] = self._list_tree(bare, sha, pathspecs)
        return list(cache[key])

    def _list_tree(self, bare: Path, sha: str, pathspecs: Sequence[bytes] | None) -> list[tuple[bytes, str, str, str]]:
        args: list[str | bytes] = ["ls-tree", "-r", "-z", sha]
        if pathspecs:
            args += ["--", *pathspecs]
        listing = self._run(bare, args)
        if not listing.ok:
            raise _Fail(self._describe(listing, "ls-tree"))
        out = []
        for raw in listing.stdout.split(b"\0"):
            if not raw:
                continue
            meta, _, name = raw.partition(b"\t")
            parts = meta.decode("ascii", errors="replace").split()
            if len(parts) != 3:
                raise _Fail(f"unexpected tree entry: {raw[:80]!r}")
            out.append((name, parts[0], parts[1], parts[2]))
        return out

    def _record_collisions(self, skipped: list[tuple[str, str]], subject: str) -> None:
        names = sorted(p for p, why in skipped if why == COLLISION)
        if names:
            shown = ", ".join(names[:20]) + (f" and {len(names) - 20} more" if len(names) > 20 else "")
            self._unreadable(
                subject,
                f"{len(names)} paths collide when case or Unicode normalisation is ignored, so none of them was written to the "
                f"checkout and a search of the files on disk would miss them (git still holds each: use file_at): {shown}",
            )

    def _prefetch(self, bare: Path, oids: list[str]) -> None:
        """Best effort: fetch the blobs a blobless clone lacks in a few large requests, not
        one request per blob. A failure here is not an answer: the read that follows reports it."""
        if not oids:
            return
        check = self._run(bare, ["cat-file", "--batch-check"], input=("\n".join(oids) + "\n").encode(), lazy=False)
        if not check.ok:
            return
        missing = [line.split()[0] for line in check.stdout.decode("utf-8", errors="replace").splitlines() if line.endswith(" missing")]
        for start in range(0, len(missing), 1000):
            self._run(bare, ["fetch", "--no-tags", "--no-write-fetch-head", "--quiet", "origin", *missing[start:start + 1000]], network=True)

    def _write_blobs(self, bare: Path, dest: Path, items: list[tuple[str, str, bool]]) -> None:
        if not items:
            return
        res = self._run(bare, ["cat-file", "--batch"], input=("\n".join(oid for _, oid, _ in items) + "\n").encode())
        if not res.ok:
            raise _Fail(self._describe(res, "cat-file"))
        data, pos = res.stdout, 0
        for rel, oid, executable in items:
            end = data.find(b"\n", pos)
            header = data[pos:end].decode("ascii", errors="replace").split() if end >= 0 else []
            if len(header) != 3 or header[0] != oid or header[1] != "blob" or not header[2].isdigit():
                raise _Fail(f"object {oid[:12]} for {rel} could not be read ({' '.join(header) or 'truncated output'})")
            size = int(header[2])
            body = data[end + 1:end + 1 + size]
            if len(body) != size:
                raise _Fail(f"object {oid[:12]} for {rel} was cut short")
            pos = end + 1 + size + 1
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "xb") as handle:  # "x": a path that already exists is an error, never an overwrite
                handle.write(body)
            os.chmod(target, 0o755 if executable else 0o644)

    # ---- grep

    def grep(
        self,
        checkout: Checkout,
        pattern: "str | Sequence[str]",
        *,
        fixed: bool = False,
        paths: Sequence[str] | None = None,
        examine_cap: int | None = None,
        result_cap: int | None = None,
        timeout: float | None = None,
    ) -> SearchResult:
        """`git grep -n -I` at the checkout's commit. `pattern` is an extended regular
        expression, or a plain string with `fixed=True`, or a list of them (one `-e` each: a line that
        matches any is a match, and a cut or a failure belongs to the one search); `paths` limits it to those files or
        directories (literal paths, not globs; a ValueError when they select no regular file).

        The files searched are the regular files of the tree at that commit, listed by git
        (so names that are not UTF-8 are included); git grep does not search symlinks or
        submodules, they are listed in `not_searched`.

        `examine_cap` limits how many files are examined; when it cuts, a blocking
        `search_truncated` record is made with the counts. `result_cap` stops after that many
        matches: nothing is claimed absent, so nothing is recorded, the result is marked "at
        least" and `examined` is None. Both must be 1 or more. A git failure, any message on
        git's stderr (whatever the exit code: a blob it could not read), or a timeout is state
        unreadable, recorded; the only "no matches" is git's exit code 1 with nothing on stderr."""
        for name, cap in (("examine_cap", examine_cap), ("result_cap", result_cap)):
            if cap is not None and (isinstance(cap, bool) or not isinstance(cap, int) or cap < 1):
                raise ValueError(f"{name} must be an integer of 1 or more, not {cap!r}")
        specs: list[bytes] | None = None
        if paths:
            specs = []
            for p in paths:
                clean = p.rstrip("/")
                if not is_safe_relpath(clean):
                    raise ValueError(f"paths must be repository relative paths, not {p!r}")
                specs.append(clean.encode("utf-8", errors="surrogateescape"))
        sha = checkout.sha
        patterns = [pattern] if isinstance(pattern, str) else list(pattern)
        if not patterns or any(not isinstance(p, str) or not p for p in patterns):
            raise ValueError("a search needs one or more non-empty patterns")
        pattern = patterns[0] if len(patterns) == 1 else " | ".join(patterns)
        subject = f"search for {pattern!r} at {sha[:12]} in {self.label(checkout.remote)}"
        try:
            bare = self._bare(checkout.remote)
            entries = self._tree_entries(bare, sha, specs)
        except _Fail as exc:
            res = self._unreadable(subject, str(exc))
            return SearchResult(Read.UNREADABLE, pattern, sha, (), None, 0, None, res.detail)
        searchable = [name for name, mode, kind, _oid in entries if kind == "blob" and mode in ("100644", "100755")]
        not_searched = tuple(
            (name.decode("utf-8", errors="replace"), "submodule" if kind == "commit" else "symlink" if mode == "120000" else f"{kind} entry")
            for name, mode, kind, _oid in entries if not (kind == "blob" and mode in ("100644", "100755"))
        )
        if specs and not searchable:
            raise ValueError(f"paths {list(paths)} select no regular file at {sha[:12]}" + (f" (only: {[n for n, _ in not_searched]})" if not_searched else ""))
        total = len(searchable)
        cut = examine_cap is not None and examine_cap < total
        selected = searchable[:examine_cap] if cut else searchable
        examined: int | None = None if result_cap is not None else len(selected)
        deadline = _Deadline(self.runner.timeout if timeout is None else timeout)
        try:
            matches, hit_result_cap = self._grep_files(bare, sha, patterns, fixed, selected, cut or bool(specs), result_cap, deadline)
        except _Fail as exc:
            res = self._unreadable(subject, str(exc))
            return SearchResult(Read.UNREADABLE, pattern, sha, (), None, total, None, res.detail, not_searched)
        truncated_by = "examine_cap" if cut else ("result_cap" if hit_result_cap else None)
        if cut:
            detail = f"examined {len(selected)} of {total} files (examine_cap {examine_cap})"
            record(self.target, "search_truncated", redact.for_record(subject, 300), detail=detail)
            if not matches:
                return SearchResult(Read.UNREADABLE, pattern, sha, (), examined, total, "examine_cap", detail, not_searched)
        state = Read.FOUND if matches else Read.ABSENT
        return SearchResult(state, pattern, sha, tuple(matches), examined, total, truncated_by, None, not_searched)

    def _grep_files(
        self, bare: Path, sha: str, patterns: list[str], fixed: bool, selected: list[bytes], listed: bool,
        result_cap: int | None, deadline: "_Deadline",
    ) -> tuple[list[GrepMatch], bool]:
        base: list[str | bytes] = ["grep", "-n", "-I", "-z", "--no-color", "-F" if fixed else "-E"]
        for pattern in patterns:
            base += ["-e", pattern]
        base += [sha, "--"]
        if not listed:
            groups: list[list[bytes]] = [[]]  # the whole tree
        elif not selected:
            return [], False
        else:
            groups = [selected[i:i + GREP_CHUNK] for i in range(0, len(selected), GREP_CHUNK)]
        matches: list[GrepMatch] = []
        prefix = f"{sha}:".encode()
        for group in groups:
            remaining = None if result_cap is None else result_cap - len(matches)
            res = self._run(bare, [*base, *group], timeout=deadline.left(), stop_after_lines=remaining)
            if res.timed_out:
                raise _Fail(self._describe(res, "grep"))
            if res.spawn_error:
                raise _Fail(res.spawn_error)
            complaints = _unharmless(res.stderr)
            if complaints:  # git said something went wrong (a blob it could not read), whatever the exit code
                raise _Fail(complaints)
            if not res.cut:
                if res.returncode == 1:
                    continue  # no match in this group, and nothing on stderr: the only "no matches"
                if res.returncode != 0:
                    raise _Fail(f"git grep failed with exit code {res.returncode}")
            for raw in res.stdout.split(b"\n"):
                if not raw:
                    continue
                if not raw.startswith(prefix):
                    raise _Fail(f"unexpected git grep output: {raw[:80]!r}")
                try:
                    name, number, text = raw[len(prefix):].split(b"\0", 2)
                    line = int(number)
                except ValueError:
                    raise _Fail(f"unexpected git grep output: {raw[:80]!r}") from None
                matches.append(GrepMatch(
                    name.decode("utf-8", errors="surrogateescape"), line,
                    text.decode("utf-8", errors="replace").removesuffix("\r"),
                ))
            if result_cap is not None and len(matches) >= result_cap:
                return matches[:result_cap], True
        return matches, False


COLLISION = "collides with another path on a case-insensitive or Unicode-normalising filesystem"

# Lines git grep may write to stderr that do not make a search incomplete. None are known: a
# search that writes anything else is unreadable, whatever its exit code.
_HARMLESS_GREP_STDERR: tuple[re.Pattern[str], ...] = ()


def _unharmless(stderr: str) -> str:
    kept = [line for line in stderr.splitlines() if line.strip() and not any(p.search(line) for p in _HARMLESS_GREP_STDERR)]
    return "\n".join(kept)


def _fold(path: str) -> str:
    return unicodedata.normalize("NFD", unicodedata.normalize("NFD", path).casefold())


def _collisions(paths: Sequence[str]) -> set[str]:
    """The paths that would overwrite one another (or need a directory where another has a
    file) on a filesystem that ignores case and Unicode normalisation form."""
    by_fold: dict[str, list[str]] = {}
    for path in paths:
        by_fold.setdefault(_fold(path), []).append(path)
    colliding: set[str] = set()
    for group in by_fold.values():
        if len(group) > 1:
            colliding.update(group)
    for path in paths:
        parts = path.split("/")
        for i in range(1, len(parts)):
            clash = by_fold.get(_fold("/".join(parts[:i])))
            if clash:  # a file elsewhere has the name of one of this path's directories
                colliding.add(path)
                colliding.update(clash)
    return colliding


class _Deadline:
    """One time limit shared by the several git calls of a search."""

    def __init__(self, seconds: float) -> None:
        self.end = time.monotonic() + seconds

    def left(self) -> float:
        return max(self.end - time.monotonic(), 0.05)


def _secs(seconds: float) -> str:
    return (f"{seconds:.1f}".rstrip("0").rstrip(".")) or "0"


def _parse_time(text: str) -> int:
    try:
        moment = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        raise ValueError(f"as_of must be an ISO 8601 time such as 2024-05-01T12:00:00+00:00, not {text!r}") from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp())


def _rmtree_writable(path: Path) -> None:
    def fix(func: Any, p: str, _exc: Any) -> None:
        try:
            os.chmod(p, 0o700)
            func(p)
        except OSError:
            pass

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=fix)
    else:
        shutil.rmtree(path, onerror=fix)
