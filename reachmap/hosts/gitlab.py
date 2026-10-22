"""GitLab: merge requests (`!N`, numbered per project), through `glab api` when it is
installed, else a token from the environment and urllib."""

from __future__ import annotations

import shutil
from typing import Any
from urllib.parse import urlsplit

from reachmap.hosts import (
    ChangeRequest, CliTransport, HostBase, UrllibTransport, _Bad, enc, register_host, split_repo,
)

_STATES = {"opened": "open", "merged": "merged", "closed": "closed", "locked": "closed"}


class GitLab(HostBase):
    kind = "gitlab"
    default_token_env = "GITLAB_TOKEN"
    api_base = "https://gitlab.com/api/v4"
    web_base = "https://gitlab.com"
    change_word = "merge request"

    def subject(self, repo: str, number: int) -> str:
        return f"gitlab {repo} merge request !{number}"

    def default_transport(self) -> Any:
        binary = shutil.which("glab")
        if binary:
            netloc = urlsplit(self.api_base).netloc
            hostname = None if netloc == urlsplit(GitLab.api_base).netloc else netloc  # a self-managed instance
            return CliTransport(binary, [self.api_base], self.timeout, hostname=hostname)
        return UrllibTransport(self.timeout)

    def _split(self, repo: str) -> list[str]:
        return split_repo(repo, max_parts=None)  # groups may nest: group/subgroup/project

    def _project(self, repo: str) -> str:
        self._split(repo)
        return f"/projects/{enc(repo)}"  # the whole path is one encoded segment: acme%2Fapi

    def _branch_path(self, repo: str, branch: str) -> str:
        return f"{self._project(repo)}/repository/branches/{enc(branch)}"

    def _branch_sha(self, data: dict[str, Any]) -> str:
        return self._sha(self._obj(data.get("commit"), "commit"), "id", where="commit.")

    def _pr_path(self, repo: str, number: int) -> str:
        return f"{self._project(repo)}/merge_requests/{number}"

    def _fetch_change_request(self, repo: str, number: int) -> ChangeRequest | None:
        path = self._pr_path(repo, number)
        verdict, data, _ = self._get_json(self.api_base + path)
        if verdict == "absent":
            return None
        mr = self._obj(data, "the merge request")
        if mr.get("iid") != number:
            raise _Bad(f"unexpected response: it is for merge request {mr.get('iid')!r}, not {number}")
        raw_state = mr.get("state")
        if raw_state not in _STATES:
            raise _Bad(f"unexpected response: state {raw_state!r}")
        state = _STATES[raw_state]
        refs = mr.get("diff_refs")
        if not isinstance(refs, dict):
            raise _Bad("the merge request has no diff_refs yet (the host is still computing it)")
        merge_sha = None
        if state == "merged":
            merge_sha = mr.get("merge_commit_sha") or mr.get("squash_commit_sha")
            if not merge_sha:
                raise _Bad("the merge request is merged but the host names no merge commit")
            self._sha({"merge_commit_sha": merge_sha}, "merge_commit_sha")
        return ChangeRequest(
            number=number,
            title=self._str(mr, "title"),
            body=self._str(mr, "description", allow_null=True),
            state=state,
            base_ref=self._str(mr, "target_branch"),
            head_ref=self._str(mr, "source_branch"),
            base_sha=self._sha(refs, "start_sha", where="diff_refs."),
            head_sha=self._sha(refs, "head_sha", where="diff_refs."),
            merge_sha=merge_sha,
            file_count=self._file_count(mr, path),
            commit_count=self._commit_count(path) if state == "merged" else None,
            url=self._str(mr, "web_url", allow_null=True),
        )

    def _commit_count(self, path: str) -> int:
        """The commits of the merge request, counted from the paginated list."""
        first = f"{self.api_base}{path}/commits?per_page=100"
        commits = self._collect(first, self._list, lambda data, headers: self._next_page(first, headers))
        if not commits:
            raise _Bad("the host lists no commits for a merged merge request")
        return len(commits)

    def _file_count(self, mr: dict[str, Any], path: str) -> int:
        raw = mr.get("changes_count")
        if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
            return raw
        if isinstance(raw, str) and raw.isdigit():
            return int(raw)
        if raw == "1000+" or (isinstance(raw, str) and raw.endswith("+")):
            # GitLab stops counting at 1000: count the diffs themselves, page by page
            return len(self._collect(
                f"{self.api_base}{path}/diffs?per_page=100",
                self._list,
                lambda data, headers: self._next_page(f"{self.api_base}{path}/diffs?per_page=100", headers),
            ))
        raise _Bad(f"unexpected response: changes_count {raw!r}")

    @staticmethod
    def _next_page(first: str, headers: dict[str, str]) -> str | None:
        following = headers.get("x-next-page", "").strip()
        return f"{first}&page={following}" if following else None

    @staticmethod
    def _list(data: Any) -> list:
        if not isinstance(data, list):
            raise _Bad("unexpected response: the diff list is not a list")
        return data


register_host("gitlab", GitLab)
