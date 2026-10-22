# M4 brief: git layer and hosts

Goal: every read of code goes through one git layer that answers found / absent /
unreadable, records every cut and failure centrally, and pins everything to a commit; a
thin host layer answers only what the host alone knows. Read `docs/CONTEXT.md` sections
3, 4 (rules 5, 9, 11), 5 (Shape A, all of it), 7.3, 8 and 13.5 first, and the M2 model
(`reachmap/model.py`, `reachmap/incomplete.py`) and M3 config.

Python 3.11+, standard library only. Git and host CLIs run through `subprocess`; HTTP
through `urllib.request`. The default test suite makes no network call: git tests use
the M1 fixture repos as `file://` remotes; host tests use recorded responses.

## Files

```text
reachmap/gitlayer.py        Git: cache, checkout, heads, pin, merge_base, diff, file_at, grep
reachmap/diffparse.py       unified diff parser with renames and line numbers
reachmap/hosts/__init__.py  Host protocol, ChangeRequest, registry, get_host(kind)
reachmap/hosts/github.py    via `gh api` (subprocess), paginated
reachmap/hosts/gitlab.py    via `glab api` when present, else token env + urllib
reachmap/hosts/bitbucket.py token env + urllib (Bitbucket Cloud 2.0 API)
reachmap/hosts/plain.py     host-less `git` mode: no change requests, compare and range only
reachmap/changeset.py       resolve a change (pr / compare / range) to a pinned ChangeSet
reachmap/redact.py          scrub credentials from URLs, argv, stderr, exceptions
tests/test_gitlayer.py
tests/test_diffparse.py
tests/test_hosts.py
tests/test_changeset.py
tests/test_redact.py
tests/fixtures/hosts/{github,gitlab,bitbucket}/*.json   recorded (synthetic) responses
```

## Git layer

- One `GitRunner` executes git with an isolated environment (`GIT_CONFIG_NOSYSTEM=1`,
  `GIT_TERMINAL_PROMPT=0`, no pager, `LC_ALL=C`), a per-call timeout, and returns
  (returncode, stdout bytes, stderr text). A timeout kills the process and is reported as
  unreadable with detail "timed out after Ns", never as an empty result. Credentials come
  from the user's own git credential helper or the host CLI; this layer never puts a
  token in argv, a URL, a cache key, a log line or an exception (all text passes through
  `redact.scrub`).
- Cache root: `$REACHMAP_CACHE` or `~/.cache/reachmap`, never inside an analysed repo.
  One bare repo per remote, directory named by sha256 of the remote URL with userinfo
  stripped (so a token in a URL cannot reach the cache path). Fetch what is needed:
  prefer a blobless partial clone (`--filter=blob:none`) for history and fall back to a
  full clone when the server refuses filters (local `file://` remotes: enable
  `uploadpack.allowFilter` and `uploadpack.allowAnySHA1InWant` in the test fixtures only,
  through `-c` on the server side or by fixture config; never change the user's config).
- `checkout(remote, sha) -> Checkout`: a read-only materialised tree at exactly `sha`
  (worktree or `git archive` export), cached by sha, used for grep and bulk reads. The
  context calls it a shallow checkout; history questions go to the bare cache.
- `heads(remote, patterns) -> HeadsResult`: `git ls-remote --heads`, glob patterns
  (`release/*` matches `release/1.x`, slashes kept), result carries `state`.
- `pin(remote, ref, as_of=None) -> str`: always a 40-hex sha (or raises `ReadFailed` with
  an unreadable record). `as_of` (ISO 8601) gives the last commit on that ref at or
  before the time. Ambiguous refs (tag and branch with the same name) resolve to
  `refs/heads/` only when asked for a branch, `refs/tags/` for a tag; never guess.
- `merge_base(remote, a, b) -> ReadResult`: absent when histories are unrelated (fixture
  s16), unreadable on any other failure (including a shallow history too short, with the
  depth in the detail).
- `diff(remote, base, head) -> ChangeSet`: `git diff -M --find-renames -U0` (plus a full
  context diff only if a later milestone needs it), parsed by `diffparse` into
  `FileChange(path, old_path, status, hunks, binary, content_available)` where every
  hunk records old and new line numbers for each removed and added line. Never place a
  line by its text.
- `file_at(remote_or_checkout, path, sha) -> ReadResult`: found with text; absent only
  when git positively reports the path is not in that commit's tree (check with `git
  ls-tree`/`cat-file -e` on a tree that was itself read successfully); unreadable for
  everything else (missing object, failed lazy blob fetch, timeout, decoding failure,
  file over `max_bytes`, git error). Files over 1 MB must still be read (no host contents
  API limit applies locally); `max_bytes` default 5 MB, recorded when hit.
- `grep(checkout, pattern, *, fixed=False, paths=None, examine_cap=None,
  result_cap=None) -> SearchResult(state, matches, examined, truncated_by)`: `git grep -n
  -I` at the checkout's sha. `examine_cap` cut (files examined) is an incompleteness
  `search_truncated` (blocking) recorded centrally with counts; `result_cap` (stop after N
  confirmed hits) claims nothing absent and is not recorded. A git failure is state
  unreadable, never zero matches. Exit code 1 with empty stderr is the only "no matches".

## Host layer

- `Host` protocol (context 7.3): `kind`, `remote(repo)`, `change_request(repo, number)`,
  `title_of(repo, number)`. `ChangeRequest(number, title, body, state, base_ref,
  head_ref, base_sha, head_sha, merge_sha, file_count, url)`.
- Every host reader returns found / absent (404 detected explicitly) / unreadable
  (rate limit, 5xx, auth, timeout, malformed JSON) and records the unreadable one.
- Branch names with slashes are URL-encoded in every API path (`release%2F1.x`); test it.
- GitHub: `gh api` with `--paginate` for the PR files count fallback; GitLab: merge
  requests are `!N` and per project (`projects/<url-encoded path>/merge_requests/N`);
  Bitbucket: `pullrequests/N`, `diffstat` paginated with `next` links.
- Transport injection: each host takes a `transport` callable (`(method, url_or_argv) ->
  (status, headers, body)`), the default runs `gh`/`glab`/urllib; tests pass a fake that
  serves `tests/fixtures/hosts/...` and records every request, and a test asserts no
  default transport is ever constructed in the suite (patch them to raise).
- Tokens: read from the env var named in config at call time, sent only in an
  `Authorization` header, never logged. A test sets a fake token value and asserts it is
  absent from every recorded request URL, every exception, every log record (capture the
  `logging` root), and every file under the cache dir.

## Change sets (`changeset.py`)

- `resolve(workspace, repo, *, pr=None, compare=None, range=None) -> ChangeSet` pinned:
  base sha, head sha, pre-change sha (parent of the merge commit for a merged PR; merge
  base for an open PR or a compare; the range start for a range), each with how it was
  found. If the pre-change version cannot be established, the ChangeSet carries it as
  `None` plus a blocking `pre_change_unavailable` record (never silently skipped).
- For a PR, the local diff's file count must equal the host's `file_count`; on mismatch,
  record `diff_count_mismatch` (blocking) and refuse to continue (raise
  `ChangeSetRefused`). Host file lists that page (GitHub 30 per page, Bitbucket `next`)
  are fully paginated before comparing.
- `compare` uses three dots semantics (`base...head`: from the merge base). `range` uses
  two dots.
- Squash commits: `title_of` follows `(#N)` in a commit subject to the PR title.

## Shape A traps to pin with tests (each makes a read fail and asserts an incompleteness)

1. git grep killed by timeout: unreadable, not zero matches.
2. grep over more files than `examine_cap`: `search_truncated` recorded with examined and
   total counts; the partial matches are not presented as the complete list.
3. result cap stops early: nothing recorded, matches marked as "at least".
4. `file_at` with the blob missing from a partial clone whose remote is gone: unreadable,
   not absent.
5. `file_at` of a path that does not exist: absent. Of a 2 MB file: found, full text.
6. Host 404 on a PR: absent (clear error "no such change request"); host 403 rate limit,
   500, timeout, malformed JSON: unreadable recorded, never "no files".
7. PR with 301 files: paginated host count used; mismatch with the local diff refuses.
8. Open change with unrelated histories (fixture s16): `pre_change_unavailable`, blocking.
9. Branch `release/1.x`: ls-remote pattern matches, API path encoded, a read succeeds.
10. Token never in cache path, URL, argv, log, exception, report (see above).
11. `pin` never returns a branch name; `as_of` before the first commit is absent.

## Tests use M1 fixtures

Build the fixtures once per test class into a temp dir, use `file://` URLs as remotes,
point `REACHMAP_CACHE` at a temp dir. Whole M4 test module under 20 seconds.

## Implementation notes

Where the code differs from, fills in, or goes beyond the text above. Each point is covered
by a test unless it says otherwise.

Files added beyond the list: `tests/support_git.py` (fixture repos as `file://` remotes, an
isolated git environment, small repository builder), `tests/support_hosts.py` (the fake
transport and the guard that makes the default transports raise),
`tests/fixtures/hosts/README.md`.

No incompleteness kind was added. The git and host layers use `read_unreadable`,
`search_truncated`, `pre_change_unavailable` and `diff_count_mismatch`, all blocking.
`timed_out` stays a run-level kind: a git or host timeout is an unreadable read with the
detail "timed out after Ns".

### Recording

- `Git(cache, record_to=...)` takes the target of every record (a list or an `Analysis`);
  `git.incompleteness` lists what it made. Hosts take `record_to` the same way. The
  subject of a record names the thing read (`path at <sha12> in <remote>`); an equal
  record is not added twice (the model's `record`).
- Text that goes into a record passes through `redact.for_record`: scrubbed, one line, at
  most 600 characters, and with banned wording defused (git's own messages can contain
  `safe.directory`, and a record's detail is checked for banned wording as written).
- A failure inside a read always records, even when the caller probes (a missing object is
  unreadable whoever asked). A path that git positively reports absent records nothing.

### Git layer

- `GitRunner` environment: the user's environment minus every `GIT_*` variable except the
  credential and transport ones (`GIT_ASKPASS`, `GIT_SSH*`, `GIT_SSL_*`, `GIT_HTTP_*`,
  `GIT_PROXY_COMMAND`), plus `GIT_CONFIG_NOSYSTEM=1`, `GIT_TERMINAL_PROMPT=0`, no pager,
  `LC_ALL=C`, `GIT_LITERAL_PATHSPECS=1`, and ssh in batch mode unless the user set an ssh
  command. The user's global config is kept (it holds the credential helper); output-shaping
  options are forced with `-c` (no quoting of paths, no colour, `protocol.ext.allow=never`).
  A timeout kills the whole process group.
- Remote URLs: a password or token is always removed before git sees it
  (`redact.transport_url`); an ssh login name (`git@host`) is kept because ssh needs it. The
  cache key and the bare repository's `remote.origin.url` never hold userinfo.
- Fetching: the bare repository is created empty and fetched into on demand, one commit at a
  time (`+<sha>:refs/reachmap/<sha>`, blobless), falling back to every branch and tag. Server
  side filters need `uploadpack.allowFilter` and `uploadpack.allowAnySHA1InWant`; a `-c` on the
  client does not reach a `file://` upload-pack, so the tests set them in the fixture
  repositories' own config (never in the user's). A server that ignores or refuses filters
  gets a full fetch.
- `pin_read` returns a `ReadResult` (found sha, absent, unreadable); `pin` is the raising
  form (`RefNotFound`, `ReadFailed`). `kind` is `any`, `branch` or `tag`; `any` with both a
  branch and a tag of that name is unreadable, never guessed. Accepted refs: a name, a full
  `refs/...` name, `HEAD`, a full sha (checked to exist as a commit) and an abbreviation
  (expanded from the local cache only). `as_of` takes the last commit on the first-parent
  line at or before the time by committer date, inclusive, a naive time meaning UTC.
- `heads`: `*` in a pattern also matches `/`. State is absent when no branch matched.
- `merge_base` and `diff` take full shas (a `ValueError` otherwise): callers pin first.
  `diff` is a plain tree comparison (two dots); three dots is `changeset.resolve`'s job. It
  uses `-M -l 5000`; a deletion plus an addition of one path (file to symlink) is merged into
  one `type_changed` file; a trailing carriage return is dropped from line text; git giving
  up on inexact renames sets `rename_detection_skipped` (the count check then catches it).
  `diff` raises `ReadFailed` (record made) because it has no state to carry.
- `file_at` decodes strictly as UTF-8 (anything else is unreadable); a directory, a submodule
  and a file over `max_bytes` are unreadable; the byte count read must equal the size git
  reported. A symlink reads as its target text. `source` may be a `Checkout`.
- `checkout` writes blobs exactly as git stores them (no attribute or filter processing, so
  no `export-ignore` can drop a file) through `cat-file --batch`, prefetching missing blobs
  of a partial clone in large requests. Symlinks, submodules and unsafe paths (`..`, `.git`,
  non-UTF-8 names) are not written and are listed in `Checkout.skipped`; read them with
  `file_at`. Files are read only. The tree is built beside the target and renamed into
  place, with a manifest; a damaged cache entry is rebuilt.
- `grep` runs `git grep -n -I -z` in the bare repository at the checkout's sha (not on the
  files on disk), with extended regular expressions (`fixed=True` for a plain string). The
  files searched are the regular files of the tree, listed by git for that search (names that
  are not UTF-8 included); symlinks and submodules, which git grep never searches, are listed
  in `not_searched`. `examine_cap` limits the files examined to the first N in tree order;
  `result_cap` stops the process after N matches and makes `examined` None. Both must be 1
  or more. A search cut by `examine_cap` that found nothing is state unreadable (an absence
  was not established). `matches` is the complete list and raises `UnreadableError` or
  `IncompleteSearch`; `partial_matches` is what was found, for a caller that knows it is
  partial. Any message on git's stderr, whatever the exit code, makes the search unreadable;
  exit 1 with an empty stderr is the only "no matches". When both caps apply the examine cut
  wins and is recorded. `paths` that select no regular file are a `ValueError`.

### Host layer

- Transport signature is `transport(method, url, headers=None)`: one argument more than the
  brief's `(method, url_or_argv)`, so a fake can see the `Authorization` header. Hosts always
  pass a URL; the CLI transport turns it into the `gh api` / `glab api` endpoint path and
  never puts a credential in argv. A transport that sets `uses_cli_login = True` gets no
  `Authorization` header.
- `change_request` answers with a `ReadResult` whose text is the JSON of a `ChangeRequest`
  (`ChangeRequest.from_read` gives the value back), so the model's three states are used
  as they are. `title_of` answers with the title as text. `branch_head(repo, branch)` is an
  addition (the sha at the head of a branch): it is where the encoded branch name appears in
  an API path (`release%2F1.x`).
- Token variables: no config key names a host token yet, so each host takes `token_env`
  (defaults: GitLab `GITLAB_TOKEN`, Bitbucket `BITBUCKET_TOKEN`, GitHub none because `gh`
  logs in). The value is read at call time, registered with `redact`, and sent only as
  `Authorization` (`Bearer`; Bitbucket uses `Basic` when the value is `user:app_password`).
- GitHub pages the file list itself (a request per page, following `Link: rel="next"`)
  instead of `gh api --paginate`, because `--paginate` hides the status of each page; the
  list is only fetched when the pull request carries no `changed_files`. An open pull
  request's `merge_commit_sha` is GitHub's test merge and is ignored: `merge_sha` is only
  set when the request is merged. A 404 from GitHub can also mean "the login cannot see it";
  the refusal message says so.
- GitLab: the project path is one encoded segment (`acme%2Fapi`, any depth of groups);
  `base_sha` is `diff_refs.start_sha` (the target branch tip), `merge_sha` falls back to
  `squash_commit_sha`; `changes_count` of `1000+` is counted from the paginated diffs
  (`X-Next-Page`); a merge request without `diff_refs` is unreadable.
- Bitbucket reports a request's commits by 12 character hashes; the host asks the commit
  endpoint for the full ones and refuses a mismatch. The diffstat is followed through every
  `next` link.
- Pagination never returns a partial list: a failed page, a loop, a `next` link to another
  host (the token would follow it) and more than 200 pages are all unreadable.
- The host-less `git` mode raises `HostUnsupported` for a change request (a usage error, not
  a failed read).

### Change sets

- `resolve(workspace, repo, *, pr, compare, range, git=None, host=None, record_to=None)`:
  exactly one of the three. `compare` and `range` are `a...b` / `a..b` strings or pairs of
  refs; the wrong number of dots is a `ValueError`. `ChangeSet` is defined in `diffparse`
  (the same type `Git.diff` returns); `resolve` fills `kind`, `repo`, `remote`,
  `pre_change_sha`, `how`, `change_request` and `incompleteness` (the records made while
  resolving).
- Merged pull request: head is the merge commit. When it has two parents the pre-change
  version and base are its first parent. When it has one parent (squash, rebase merge,
  fast-forward) the host's commit count n decides between two readings, tried against the host
  file count and, when the request's own head can still be fetched, the paths of its own diff:
  the first parent (squash) or the commit n back along first parents (rebase, fast-forward). The
  one that fits wins; neither or both fitting, or no commit count, records a blocking
  `pre_change_unavailable` and refuses. Open or closed (unmerged): the host's base and head,
  pre-change version their merge base. When there is no pre-change version (no merge base, an
  unreadable one, a root merge commit) the files are the plain tree difference between base
  and head, and the blocking `pre_change_unavailable` record names the head ref.
- Failures: absent request is `NoSuchChangeRequest`, unreadable is `ReadFailed` (record
  already made), an unknown ref or a count mismatch is `ChangeSetRefused` (it carries the
  records).
- `title_for_commit(git, host, remote, repo, sha)` follows `(#N)` (or `(!N)`) only at the end
  of the subject.

### Changes after the independent review

Each is covered by a test (`test_gitlayer_hardening.py`, `test_hosts.py`, `test_changeset.py`,
`test_diffparse.py`, `test_redact.py`).

- Rebase and fast-forward merges (blocker): see "Change sets". `ChangeRequest` gained
  `commit_count` (GitHub's `commits`; GitLab and Bitbucket page the commits endpoint, only for
  merged requests). `Git.parents`, `Git.first_parent_ancestor` and `Git.quiet()` (a block whose
  reads record nothing in the real target, used for the optional own-diff check) were added.
- grep: stderr rule above; paths that select nothing; caps validated; `examined` None under a
  result cap; `all_matches` removed in favour of `matches` / `partial_matches`.
- Abbreviated shas: after a local miss every branch and tag is fetched and the expansion tried
  again; a commit that is on neither, or an ambiguous prefix, is unreadable ("not expanded"),
  never absent. This also means a hex-looking name that is no ref is unreadable, not absent.
- User configuration: the base options now pin `diff.submodule`, `diff.ignoreSubmodules`,
  `diff.noprefix`, `diff.mnemonicPrefix`, `diff.relative`, `diff.interHunkContext`,
  `diff.algorithm`, `diff.renames`, `grep.column`, `grep.fullName`, `grep.patternType`,
  `log.showSignature`, `core.quotePath=true` and `color.ui=false` (with `--no-ext-diff` and
  `--no-textconv`); `core.quotePath=true` means non-ASCII names are octal quoted, which
  the parser decodes exactly. They sit before `extra_config`, which can still override them.
- Names that are not UTF-8: diff and grep return them as surrogate escapes
  (`FileChange.path_is_text` is False, and the model's `Evidence` refuses such a path);
  `file_at` accepts them and reads the file; a path with a lone surrogate that stands for no
  byte is unreadable and recorded; a checkout lists them in `skipped`.
- Checkout collisions: paths that collide when case and Unicode normalisation are ignored
  (README and readme, a file and a directory of the same name, composed and decomposed forms)
  are all left out, listed in `skipped` with the reason, and one blocking `read_unreadable`
  names them (also when the checkout comes from the cache). Files are opened exclusively.
- Shallow caches: `parents`, `first_parent`, `first_parent_ancestor` and `pin` with `as_of`
  answer unreadable (with the depth) when the history ends at the shallow boundary, never
  absent.
- Redirects are refused (`UrllibTransport` installs a handler that does not follow them; a 3xx
  is unreadable "redirect refused"); a token is never sent over a URL that is not https;
  `gh` and `glab` get `--hostname` when the API host is not their default; `title_of` makes
  one request and needs only a title.
- Redaction: header names in JSON or repr form, registered secrets in URL quoted forms and,
  for `user:password` values, the base64 of a Basic header; credential query parameters are
  dropped by `strip_userinfo` and `transport_url`, which now share one implementation. A token
  split across two lines is not recognised (documented limit, tested as such).
- Parser: a quoted name ending in a backslash, `copy from` / `copy to` (status `copied`).
- `GitRunner` drops `GITLAB_TOKEN`, `BITBUCKET_TOKEN` and any `drop_env` names from git's
  environment (`GH_TOKEN` and `GITHUB_TOKEN` stay: `gh auth git-credential` reads them);
  `resolve` passes the host's `token_env`. A remote that starts with a dash is a `ValueError`
  and `--` precedes it in `ls-remote` and `remote add`. `refs/reachmap/<sha>` refs are kept to
  `max_refs` (2000) newest. `Git(rename_limit=...)` exposes the rename limit. `--before=@epoch`.
- `resolve` no longer changes the target of a `Git` it is given (giving both is a `ValueError`).
- Tests: the no-network guard is suite wide (`tests/__init__.py` sets
  `REACHMAP_FORBID_DEFAULT_TRANSPORT`, which `HostBase._transport` checks); the token cache walk
  covers a cache that was used; crafted trees with `..`, `.git` and odd entries go through
  `checkout`; tests for the failures that used to read as success (cat-file failing after
  ls-tree, byte count, rev-parse failing after a fetch, page limit, Bitbucket hash prefix,
  parents unreadable, host base different from the first parent, rename detection skipped).

### Not done

- A pull request head that is not reachable by sha (a fork on a server that refuses
  unadvertised shas) is unreadable for an open request; for a merged one it only removes the
  optional own-diff check. `refs/pull/N/head` and `refs/merge-requests/N/head` are not fetched.
- A GitLab fast-forward merge reports no `merge_commit_sha` (and no squash sha); that is
  unreadable ("names no merge commit"), not guessed from the head.
- A rebase merge whose commits were also squashed into fewer by the host, or a request whose
  commit count the host reports wrongly, is refused rather than guessed; so is the rare case
  where both readings give the same files.
- Two search limits are by design: git grep does not search symlinks or submodules (they are
  in `not_searched`, and an "absent" answer covers the other files), and a checkout skips
  symlinks, submodules, unsafe, non-UTF-8 and colliding names (listed in `skipped`).
- No test runs a real redirect through a socket: the redirect handler is exercised with a fake
  `https_open`.
- The context-diff variant (`git diff` with more than `-U0`) is not produced; the parser
  reads it.
- `gh` and `glab` were never run (no network in the suite): their argv construction and
  output parsing are tested with a mocked `subprocess.run`, the HTTP transport with a mocked
  `urlopen`. Enterprise base URLs (`api_url`, `web_url`) are accepted and untested.
- One process at a time on a cache directory: creation uses an atomic rename, there is no
  lock. Windows is untested (process groups).
