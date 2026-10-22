"""Bitbucket Cloud 2.0: `pullrequests/N`, a token (or `user:app_password`) from the environment."""

from __future__ import annotations

import base64
import re
from typing import Any

from reachmap.hosts import ChangeRequest, HostBase, _Bad, enc, register_host, split_repo

_STATES = {"OPEN": "open", "MERGED": "merged", "DECLINED": "closed", "SUPERSEDED": "closed"}
_FULL = re.compile(r"^[0-9a-f]{40}$")


class Bitbucket(HostBase):
    kind = "bitbucket"
    default_token_env = "BITBUCKET_TOKEN"
    api_base = "https://api.bitbucket.org/2.0"
    web_base = "https://bitbucket.org"
    change_word = "pull request"

    def authorization(self, token: str) -> str:
        if ":" in token:  # user:app_password
            return "Basic " + base64.b64encode(token.encode("utf-8")).decode("ascii")
        return f"Bearer {token}"

    def _repo(self, repo: str) -> str:
        workspace, slug = split_repo(repo)
        return f"/repositories/{enc(workspace)}/{enc(slug)}"

    def _branch_path(self, repo: str, branch: str) -> str:
        return f"{self._repo(repo)}/refs/branches/{enc(branch)}"

    def _branch_sha(self, data: dict[str, Any]) -> str:
        return self._sha(self._obj(data.get("target"), "target"), "hash", where="target.")

    def _full_hash(self, repo: str, short: str, what: str) -> str:
        """Bitbucket reports a pull request's commits by a 12 character hash: ask for the
        commit to get the full one."""
        if _FULL.match(short):
            return short
        if not re.fullmatch(r"[0-9a-f]{7,39}", short or ""):
            raise _Bad(f"unexpected response: {what} is not a commit hash")
        verdict, data, _ = self._get_json(f"{self.api_base}{self._repo(repo)}/commit/{short}")
        if verdict == "absent":
            raise _Bad(f"the commit {short} of {what} is not known to the host")
        full = self._sha(self._obj(data, "the commit"), "hash", where="commit.")
        if not full.startswith(short):
            raise _Bad(f"unexpected response: the commit for {what} does not match {short}")
        return full

    def _pr_path(self, repo: str, number: int) -> str:
        return f"{self._repo(repo)}/pullrequests/{number}"

    def _fetch_change_request(self, repo: str, number: int) -> ChangeRequest | None:
        path = self._pr_path(repo, number)
        verdict, data, _ = self._get_json(self.api_base + path)
        if verdict == "absent":
            return None
        pr = self._obj(data, "the pull request")
        if pr.get("id") != number:
            raise _Bad(f"unexpected response: it is for pull request {pr.get('id')!r}, not {number}")
        raw_state = pr.get("state")
        if raw_state not in _STATES:
            raise _Bad(f"unexpected response: state {raw_state!r}")
        state = _STATES[raw_state]
        source = self._obj(pr.get("source"), "source")
        dest = self._obj(pr.get("destination"), "destination")

        def commit_hash(side: dict[str, Any], name: str) -> str:
            commit = self._obj(side.get("commit"), f"{name}.commit")
            return self._str(commit, "hash", where=f"{name}.commit.")

        merge_sha = None
        if state == "merged":
            merge_commit = pr.get("merge_commit")
            if not isinstance(merge_commit, dict):
                raise _Bad("the pull request is merged but the host names no merge commit")
            merge_sha = self._full_hash(repo, self._str(merge_commit, "hash", where="merge_commit."), "merge_commit")
        links = pr.get("links") if isinstance(pr.get("links"), dict) else {}
        html = links.get("html") if isinstance(links.get("html"), dict) else {}
        count = self._count_files(path)
        return ChangeRequest(
            number=number,
            title=self._str(pr, "title"),
            body=self._str(pr, "description", allow_null=True),
            state=state,
            base_ref=self._str(self._obj(dest.get("branch"), "destination.branch"), "name", where="destination.branch."),
            head_ref=self._str(self._obj(source.get("branch"), "source.branch"), "name", where="source.branch."),
            base_sha=self._full_hash(repo, commit_hash(dest, "destination"), "destination.commit"),
            head_sha=self._full_hash(repo, commit_hash(source, "source"), "source.commit"),
            merge_sha=merge_sha,
            file_count=count,
            url=html.get("href") if isinstance(html.get("href"), str) else "",
            commit_count=self._commit_count(path) if state == "merged" else None,
        )

    @staticmethod
    def _values(data: Any) -> list:
        values = data.get("values") if isinstance(data, dict) else None
        if not isinstance(values, list):
            raise _Bad("unexpected response: the page has no values list")
        return values

    @staticmethod
    def _next(data: Any, _headers: dict[str, str]) -> str | None:
        following = data.get("next") if isinstance(data, dict) else None
        return following if isinstance(following, str) and following else None

    def _count_files(self, path: str) -> int:
        """The diffstat, followed through every `next` link."""
        return len(self._collect(f"{self.api_base}{path}/diffstat?pagelen=100", self._values, self._next))

    def _commit_count(self, path: str) -> int:
        """The commits of the pull request, followed through every `next` link."""
        commits = self._collect(f"{self.api_base}{path}/commits?pagelen=100", self._values, self._next)
        if not commits:
            raise _Bad("the host lists no commits for a merged pull request")
        return len(commits)


register_host("bitbucket", Bitbucket)
