"""GitHub, through `gh api` (the user's own login) or any injected transport."""

from __future__ import annotations

import re
import shutil
from typing import Any
from urllib.parse import urlsplit

from reachmap.hosts import (
    ChangeRequest, CliTransport, HostBase, _Bad, enc, register_host, split_repo,
)

_NEXT = re.compile(r'<([^>]+)>\s*;\s*rel="next"')


class GitHub(HostBase):
    kind = "github"
    default_token_env = None  # gh supplies the login; a token_env is only used with a non-CLI transport
    api_base = "https://api.github.com"
    web_base = "https://github.com"
    change_word = "pull request"

    def default_transport(self) -> Any:
        binary = shutil.which("gh") or "gh"
        netloc = urlsplit(self.api_base).netloc
        hostname = None if netloc == urlsplit(GitHub.api_base).netloc else netloc  # an enterprise instance
        return CliTransport(binary, [self.api_base], self.timeout, hostname=hostname)

    def extra_headers(self) -> dict[str, str]:
        return {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}

    def _repo_path(self, repo: str) -> str:
        owner, name = split_repo(repo)
        return f"/repos/{enc(owner)}/{enc(name)}"

    def _branch_path(self, repo: str, branch: str) -> str:
        return f"{self._repo_path(repo)}/branches/{enc(branch)}"

    def _branch_sha(self, data: dict[str, Any]) -> str:
        return self._sha(self._obj(data.get("commit"), "commit"), "sha", where="commit.")

    def _pr_path(self, repo: str, number: int) -> str:
        return f"{self._repo_path(repo)}/pulls/{number}"

    def _fetch_change_request(self, repo: str, number: int) -> ChangeRequest | None:
        base = self._pr_path(repo, number)
        verdict, data, _ = self._get_json(self.api_base + base)
        if verdict == "absent":
            return None
        pr = self._obj(data, "the pull request")
        number_seen = pr.get("number")
        if number_seen != number:
            raise _Bad(f"unexpected response: it is for pull request {number_seen!r}, not {number}")
        head = self._obj(pr.get("head"), "head")
        target = self._obj(pr.get("base"), "base")
        raw_state = pr.get("state")
        if raw_state not in ("open", "closed"):
            raise _Bad(f"unexpected response: state {raw_state!r}")
        merged = pr.get("merged")
        if not isinstance(merged, bool):
            merged = bool(pr.get("merged_at"))
        state = "merged" if merged else raw_state
        # while a pull request is open, merge_commit_sha is GitHub's test merge, not a commit on the base branch
        merge_sha = self._sha(pr, "merge_commit_sha") if state == "merged" else None
        commits = pr.get("commits")
        count = pr.get("changed_files")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            count = self._count_files(base)
        return ChangeRequest(
            number=number,
            title=self._str(pr, "title"),
            body=self._str(pr, "body", allow_null=True),
            state=state,
            base_ref=self._str(target, "ref", where="base."),
            head_ref=self._str(head, "ref", where="head."),
            base_sha=self._sha(target, "sha", where="base."),
            head_sha=self._sha(head, "sha", where="head."),
            merge_sha=merge_sha,
            file_count=count,
            url=self._str(pr, "html_url", allow_null=True),
            commit_count=commits if isinstance(commits, int) and not isinstance(commits, bool) and commits >= 1 else None,
        )

    def _count_files(self, base: str) -> int:
        """The pull request's file list, paginated in full (a page holds at most 100, often
        30), when the pull request itself carries no count."""
        files = self._collect(
            f"{self.api_base}{base}/files?per_page=100",
            lambda data: self._list(data),
            lambda data, headers: (_NEXT.search(headers.get("link", "")) or [None, None])[1],
        )
        return len(files)

    @staticmethod
    def _list(data: Any) -> list:
        if not isinstance(data, list):
            raise _Bad("unexpected response: the file list is not a list")
        return data


register_host("github", GitHub)
