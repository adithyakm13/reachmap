# Recorded host responses (synthetic)

Invented for the tests, shaped like the real GitHub, GitLab and Bitbucket Cloud API
responses (field names, nesting, headers, error bodies, pagination). Nothing here was
recorded from a live service, and every name, id and sha is made up. Each file is one
response:

```json
{"status": 200, "headers": {"Link": "<...>; rel=\"next\""}, "body": {...}}
```

`body_text` replaces `body` for a response that is not JSON (an HTML error page).

The shas are obvious placeholders: `1111...` (base branch tip), `2222...` (head), `3333...`
(merge commit), `4444...` and `5555...` (base and head of the merged requests), `8888...`
(a merge base) and `9999...` (GitHub's test merge of an open pull request). Tests that
need real commits replace them in the text before serving it (`tests/support_hosts.py`).

```text
github/     pr_open, pr_merged, pr_301_no_count (+ 11 pages of files, 30 per page),
            pr_301_with_count, branch_release_1x, 404, 403 rate limit, 401, 500, 503,
            malformed, wrong_shape
gitlab/     mr_opened (!12), mr_merged, mr_squashed, mr_many_changes ("1000+", 11 pages
            of diffs, 100 per page), mr_no_diff_refs, branch_release_1x, 404, 429, 403,
            401, 500, malformed
bitbucket/  pr_open (12 character hashes), pr_merged, pr_301 (4 diffstat pages linked by
            next), commit_* (the full hash of each short one), diffstat_21, diffstat_8,
            branch_release_1x, 404, 429, 401, 502, malformed
```
