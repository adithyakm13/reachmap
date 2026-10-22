# M12 brief: runner (localhost job queue)

Goal: `reachmap serve` starts a localhost HTTP runner that queues analyses, runs one at a
time in a separate process with a hard timeout, serves reports and records decisions, and
refuses anything that is not a same-origin loopback request. It is a runner, not a service
(context 8). Done when each guard has a test. Read `docs/CONTEXT.md` sections 1 (runner
row), 4 (rule 9), 5 (one job at a time), 8 (security posture), 13.4; M9 (pipeline, CLI,
exit codes, timed_out), M10 (report rendering), M11 (decision store and import) briefs and
notes.

Python 3.11+ stdlib only (`http.server` `ThreadingHTTPServer`, `multiprocessing` spawn
context, `json`, `secrets`).

## Files

```text
reachmap/runner/__init__.py
reachmap/runner/server.py     HTTP handler, routing, guards
reachmap/runner/jobs.py       queue, worker process, timeout, states
reachmap/runner/api.py        JSON API: jobs, reports, decisions
cli.py                        `reachmap serve [--port 8787] [--config reachmap.toml] [--reports DIR]`
tests/test_runner_*.py
```

## Guards (each with a test)

1. Binds `127.0.0.1` only (never `0.0.0.0`, never `::`); `--host` is not an option. A test
   asserts the bound address.
2. `Host` header must be `127.0.0.1:<port>`, `localhost:<port>` or `[::1]:<port>` exactly
   (case-insensitive name, port must match); anything else is 421/403 with no body detail
   (DNS rebinding). Missing Host is refused.
3. Cross-origin refused: for any state-changing method (POST, PUT, DELETE) the `Origin`
   header, when present, must equal the runner's own origin; `Sec-Fetch-Site`, when
   present, must be `same-origin` or `none`; otherwise 403. Requests with a body must be
   `Content-Type: application/json` (blocks simple-form CSRF). GET endpoints that return
   report data also refuse a foreign `Origin` / `Sec-Fetch-Site: cross-site`. No CORS
   headers are ever sent; preflight `OPTIONS` gets 403.
4. A per-process session token (`secrets.token_urlsafe(32)`) printed in the URL the CLI
   shows (`http://127.0.0.1:8787/#token=...`, fragment so it never reaches logs) and
   required as a header (`X-Reachmap-Token`) on every API call; the static workspace page
   reads it from the fragment. Constant-time comparison. Never logged, never in a report.
5. One job at a time: a FIFO queue; a second POST while one runs is queued (state
   `queued`), never run concurrently. Queue length cap (default 20): beyond it 429.
6. Separate process with a hard timeout: each job runs `pipeline.build` in a `spawn`
   child (`multiprocessing.get_context("spawn")`), with `limits.timeout_s` (config) as the
   wall clock; on timeout the child is terminated, then killed after a grace period, and
   the job's state is `timed_out` with no findings count; a report JSON for the timed-out
   run is written (M9 shape with nulls). A timed-out job is never `completed`. A child that
   crashes is `failed` with its exit code; a child that exits without writing its result is
   `failed`.
7. Job states: `queued`, `running`, `completed`, `failed`, `timed_out`, `cancelled`
   (DELETE on a queued or running job; running is terminated like a timeout but marked
   cancelled). Transitions only forward; tested exhaustively.
8. Input validation: job spec is `{repo, pr | compare | range, as_of?}` validated against
   the loaded config (repo must exist); strings length-capped; unknown keys refused. No
   arbitrary paths, no config path from the client, no shell.
9. Responses: JSON with `Content-Type: application/json; charset=utf-8`,
   `X-Content-Type-Options: nosniff`, `Cache-Control: no-store`, `Referrer-Policy:
   no-referrer`, a strict CSP on HTML responses (same as M10 but `connect-src 'self'` for
   the workspace). Error bodies never echo request content or tracebacks.
10. Request size cap (1 MB), slow-loris timeout on the socket, header count cap.
11. Logging: method, path without query, status, duration; never headers, bodies, tokens.

## API

- `GET /api/health` (no token: returns only `{"ok": true}`; still Host-checked).
- `GET /api/repos`: workspace name and the configured repo names with their roles and
  branch patterns (no remotes, no paths, no token env names), for the M13 job form.
- `POST /api/jobs` answers `{id, state}`; `GET /api/jobs`; `GET /api/jobs/<id>`;
  `DELETE /api/jobs/<id>`.
- `GET /api/reports` (list: key, revision, status, created); `GET /api/reports/<name>`
  (the report JSON with decisions computed, M11); `GET /api/reports/<name>.html` (M10
  page).
- `POST /api/reports/<name>/decisions` (M11 decision, channel `runner`; author required).
- Static: `/` serves the M13 workspace files when present (packaged under
  `reachmap/runner/static/`), else a minimal page that lists reports (server-rendered,
  escaped). Path traversal impossible (resolve and check prefix; no symlinks followed).

## Tests

- Each guard 1 to 11 with a real server on an ephemeral port in a thread (bind 127.0.0.1:0)
  and `http.client`; Host spoofing (`evil.example`, `127.0.0.1.evil.example`, wrong port,
  missing), Origin and Sec-Fetch-Site variants, form content type, preflight, missing and
  wrong token, timing-safe compare used (mock hmac.compare_digest call), queue ordering and
  no overlap (two jobs with a fake pipeline that records start/end times), timeout kills a
  child that sleeps (fake pipeline entry via a test hook in the child, spawn-safe), crash
  is failed, cancel, request size cap, traversal attempts (`/..%2f`, encoded, absolute),
  logs contain no token.
- End to end: start the server on the fixture workspace, POST s13, poll to completed, GET
  the report and the HTML, POST a decision, GET the report again with the decision current.
- All tests run in well under 30 s total; no network beyond loopback.

## Implementation notes

Where the code differs from, fills in, or goes beyond the text above. Each point is covered by a test unless it says otherwise.

Files added: `reachmap/runner/{__init__,jobs,api,server}.py`, `reachmap/runner/minimal/{index.html,minimal.js,minimal.css}` (the stand-in page, see below),
`tests/test_runner_{guards,jobs,reports,e2e,cli}.py`, `tests/support_runner.py` (a real server on 127.0.0.1:0 in a thread and a raw `http.client`
that sends exactly the headers a test asks for), `tests/runner_hooks.py` (entry functions for the child process). Changed: `reachmap/cli.py`
(`serve`, `main(outcome=)`, `write_unfinished`), `reachmap/pipeline.py` (`failed_analysis(status=)`, default unchanged), `pyproject.toml` (package data of the runner).
`docs/PROGRESS.md`, the fixtures and every `expected.json` are untouched.

### Guards and the tests that prove them

| Guard | Test |
|---|---|
| 1 loopback bind | `test_runner_guards.Bind` (bound address and family, nothing on `::1`, no `host` parameter, `--host` is exit 2) |
| 2 Host | `HostHeader` (23 bad hosts on three paths: wrong name, suffix and prefix tricks, wrong port, no port, userinfo, trailing dot; missing; repeated; checked first for every method) |
| 3 cross-origin | `CrossOrigin` (Origin, Sec-Fetch-Site, form and other content types, GET refusals, preflight, no `Access-Control-*` header on any response) |
| 4 token | `Token` (every API route without or with a wrong token, `compare_digest` is called on bytes, the token is in no response); `test_runner_cli` (the printed URL, token in the fragment, not on stderr) |
| 5 one at a time | `test_runner_jobs.OneAtATime` (FIFO, never two `running` while polling, intervals the child wrote itself do not overlap and are in order, a child per job), cap 429 |
| 6 separate process, hard timeout | `Timeout` (a sleeping child is killed, its pid is gone, the job is `timed_out`, its report has nulls; a child that ignores SIGTERM is killed after the grace period; the next job runs), `Failures` (crash code, SIGKILL, no result, an exception in the child) |
| 7 states | `States` (all 36 pairs against the table, forward only), `Cancel` (queued never runs, running is killed and marked, finished is 409, unknown is 404) |
| 8 validation | `Validation` (about 55 bad specs each with its fixed field name, non-objects, unknown keys, nothing queued after a refusal, the child receives only the validated spec; the package has no `subprocess`, `shell=`, `eval`) |
| 9 responses | `Responses` (nosniff, no-store, no-referrer and the JSON type on 10 kinds of reply; the report page header is the M10 policy and equals the page's meta tag; error bodies never contain the request, tested with a marker in the path, query, body, method, Host, Origin, token and header name; malformed request lines get a JSON body) |
| 10 caps | `Caps` (413 before the body is read, exactly 1 MB is read, 411 without a length or with chunked, a stalled header and a stalled body are cut off with the server still serving, header count) |
| 11 log | `Logging` (every line is `METHOD /path STATUS Nms`; a marker in the token, query, body, Host, Origin, header and request line never appears; control characters in a path are written as percent escapes) |

Also: `test_runner_reports` (path traversal in report names and static files, links not followed, the list, the page, the refusals of the decision endpoint) and `test_runner_e2e`
(the done-when: the fixture workspace, POST s13, poll to completed, the report JSON and page, a decision with channel `runner`, the report again with the decision current,
the analysis fields unchanged, a second run is revision 2 with the decision carried; a failed run through the real pipeline has a report with nulls).

### How a job runs

- The child runs `reachmap.runner.jobs.analyse_entry`, which is `cli.main(["analyse", ... "--format", "json"])` in the child (output dropped), not `pipeline.build` alone. That keeps one
  code path for the report, the revision number, the lock and the carried decisions: a job and the command line make the same file. `cli.main` gained an `outcome` dict (the report name and
  status of the run) so the runner does not parse a summary. The child sends `{code, report}` on a pipe; `code` is the command's exit code (0 completed, 4 timed out, anything else failed).
- The hard timeout is `limits.timeout_s` from the child's start (spawn included); the pipeline's own cooperative deadline is the same number and starts later, so the runner's normally fires first.
  If the child answers in the instant of the deadline the answer is used. At the timeout the child gets SIGTERM, then SIGKILL after `grace_s` (2 s). The runner then writes the report itself
  (`cli.write_unfinished`, built with `pipeline.failed_analysis(status="timed_out")`): the M9 shape, no counts, `-failed-<hash>` name, no revision. A child that answers with code 4 by itself leaves its own such report.
- A failed job has `exit_code` (the process's, for a crash or a signal such as -9, or the command's) and a fixed `reason`: `crashed`, `no_result`, `analysis_failed`, `no_start`, `internal`.
  A job holds no output of the child, no message and no traceback; the child's stdout and stderr go to the null device.
- A cancelled running job is stopped as a timeout is and has no report. DELETE waits for it to end (bounded) and answers with the final state.
- Spawn-safe test hook: `create(entry="module:function", entry_args={...})` makes the child call that function instead (tests use `tests/runner_hooks.py`: it records start and end times, sleeps, ignores SIGTERM, exits with a code, ends without a result).

### Choices (small ones I made; say if you want any of them the other way)

- `POST /api/jobs` answers 202 with `{id, state}`; a decision answers 201 with the recorded decision (without its fingerprint). Job ids are 12 random hex characters.
- Order of checks: Host (421), preflight (403), Origin and Sec-Fetch-Site for every API call and every state-changing method (403, also for GET `/api/health`), method (405), token (401), body (411, 400, 413, 415), then the route. A missing token on an unknown API path is 401 (no route is revealed).
- The Origin must equal `http://<the Host header>` (so `localhost` and `127.0.0.1` are different origins, as a browser sees them); no trailing slash, `null` refused, two Origin headers refused. `Sec-Fetch-Site` present in any form other than `same-origin` or `none` (even empty) is refused.
- The Host table is exact strings, lower-cased: no default port, no trailing dot, no `127.1`.
- The minimal page is not server-rendered. The brief says it lists reports "server-rendered, escaped"; but the server cannot know the token (it is in the fragment), so a server-rendered list would show report names to any local process without the token. It is a static page and `minimal.js`, which reads the token from the fragment, calls the API with the header and writes with `textContent`, and opens a report page through a blob. This is the one deviation from the text. The M13 files, when built into `reachmap/runner/static/` (with an `index.html`), replace it.
- Static files: only `GET`, plain segments (no dot or empty segment, none starting with a dot), no link at any step (`lstat`), a regular file, resolved under the resolved root, opened with `O_NOFOLLOW`, known extensions only; `/api/` is never a file. The policy header for the workspace and the stand-in is `default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`. A report page gets exactly the M10 policy as a header too (it already has it in a meta tag), with no `connect-src`: the page makes no request.
- Report names in URLs are the file name without `.json`, `[A-Za-z0-9][A-Za-z0-9._-]*`, at most 201 characters; the file must be a regular file and not a link. The report JSON is served with `review.decisions` recomputed from the store (`attach.refresh`, as `reachmap report` does); the file on disk is never rewritten. A file that is not a readable report is 404 and is left out of the list (the list reads at most 500 files, newest first, and caches by size and time).
- A decision body is `{target, verdict, reason?, author}` and nothing else (a `channel`, a `fingerprint` or any other key is refused: the channel is always `runner`, the fingerprint and revision come from the report). The same rules as `reachmap decide` (verdict by kind, a reason for skip and reject, 2,000 and 100 characters) and the same `store.append` under the same lock. A refusal names a field from a fixed list. A report with no review data or one that did not complete is 409.
- Connections are HTTP/1.0 (closed after each request), so there is no keep-alive state to hold open; the socket timeout is 10 s per read and one thread serves a connection (`ThreadingHTTPServer`, daemon threads). There is no cap on the number of concurrent connections.
- The header count cap is 64 (the standard library's is 100); the request line is capped at 64 KB by the standard library (414).
- `serve` prints `Reachmap runner for workspace <name>: http://127.0.0.1:<port>/#token=...` on stdout (the only place the token is written) and runs until SIGINT or SIGTERM (exit 0, the child is ended). `--port 0` picks a free port. A bad port, config, reports folder or a port in use is exit 2.
- The environment of the job is the runner's own (`os.environ` at start); `git_env` and `env` can be given to `create` (the tests cut git off from the user's config with them).
- A job's `as_of` is passed as `--as-of=VALUE` and the change as `--compare=VALUE` (never a separate argument, so a value cannot be read as an option); validation also refuses whitespace, control and bidi characters and a ref part that starts with `-`.

### Not done, or not covered

- No Windows run (the runner uses nothing Windows-specific beyond what the lock already had, but nothing was run there).
- No cap on concurrent connections (a flood of slow connections holds threads for the socket timeout each).
- The jobs are kept in memory (the last 500); a restart forgets them. The reports on disk are the record.
- The M13 workspace is not built: `reachmap/runner/static/` does not exist, so the stand-in page is what `/` serves.

### Tests, speed

2,434 tests (2,342 after M11; 92 new: guards 37, jobs 31, reports 13, cli 7, e2e 4), all passing on 3.11, 3.12 and 3.13 and in one serial process.

| Python | serial (`unittest discover`) | parallel (`run_parallel.py`, 4 workers) |
|---|---|---|
| 3.11 | not run | 179.5 s |
| 3.12 | 320.7 s | 134.3 s |
| 3.13 | not run | 167.2 s |

The M12 modules cost, in the 3.12 parallel run: `test_runner_jobs` 15.9 s (real spawned children; the timeout tests each wait out a 2 s ceiling), `guards` 3.5 s, `e2e` 2.8 s (three analyses of the fixtures),
`cli` 0.9 s, `reports` 0.8 s: about 24 s of work, 22 s when the five modules are run alone on 3.12/3.11/3.13. `tests/.timings.json` was refreshed with `--record`.

Found by the full run, not by a module run: a shell that starts `serve` in the background leaves SIGINT ignored, and Python then installs no handler for it, so Ctrl-C (or a test's SIGINT) did nothing.
`serve` now installs its own SIGINT handler as well as SIGTERM.

Mutations that were tried and fail the tests: the bind address 0.0.0.0, the Host check removed, the Origin check removed, the Sec-Fetch-Site check removed, the token compared with `==`, the JSON content type not required,
the body cap removed, the header cap removed, the queue cap removed, the timeout never firing, the kill after the grace period removed (the SIGTERM-ignoring child then survives).

### Review fixes (the process boundary and the request boundary)

An independent review attacked the live server. The HTTP checks held; the process boundary did not. What changed, each with tests (the tests were written first).

- **Ending a job ends everything it started.** `gitlayer` starts every git in a process group of its own, so killing the child left git running, under init, past `timed_out`, overlapping the next job in the shared cache and outliving `reachmap serve`.
  Now the runner makes a private registry file for each job (a folder it owns, `tempfile.mkdtemp`) and names it to the child in `REACHMAP_PROCESS_REGISTRY`; `gitlayer.register_process_group` appends the group id of each git it starts (one line, nothing is ever removed). After the child is reaped, whatever ended it (the answer, the timeout, a cancel,
  a crash, a SIGKILL, `stop()`), the runner reads the registry and `killpg(SIGKILL)`s each group (`jobs.sweep_registry`): never its own group, never 0 or 1, a group that is gone is no error. The child also calls `os.setsid()` first and leads a group, so the runner kills that group too (a `gh` or any command run without a group of its own).
  `stop()` sweeps every registry again and removes the folder. Tests: a stand-in git script run through `GitRunner` leaves a `sleep` and a second `sleep` beside it; both are gone after a timeout, a cancel, `stop()` and a child SIGKILL; a `sleep` in the child's own group is gone; the next job runs only after.
  Known limits: the line is written by the child after `Popen` returns, so a child killed in that instant leaves one unregistered git (the window is microseconds); a group id is not guarded against reuse (needs a wrap of the pid space within seconds); Windows has no `killpg` and nothing is swept there (the child is still killed, its git processes are not).
- **The child ends when the runner dies.** The child polls `os.getppid()` every 0.5 s in a daemon thread; when its parent is no longer the runner it sweeps its registry and exits. Test: a runner in a subprocess is SIGKILLed; the child and both sleeps are gone.
- **A job that completed and was then ended.** The child writes `{report, status}` to a result file of its job (`jobs.note_result`, atomic) the moment `analyse` has written its report and entered the revision log (the `outcome` dict of `cli.main` is a `_Outcome` that does it), before it answers. If the child is ended or dies without answering, the runner reads that file: a `completed` report makes the job `completed` with that report; a `timed_out` or failed one is kept as it is; no second `-failed-` report is written. A job that was cancelled after it had completed stays `cancelled` and names the report. The remaining window is the instant between the revision log entry and the result file; there the job is `timed_out`, `failed` or `cancelled` and a report for it may exist unnamed.
- **Rule 9 on the child-reported path.** A test hook returns code 4 with a timed-out report (what `analyse` does at its own limit): the job is `timed_out`, has no count, and the report has nulls; mapping 4 to `completed` fails it.
- **Request boundary:** a total deadline per request (`request_deadline`, 15 s, a timer that shuts the socket; stopped once the request is read), at most 32 connections (`max_connections`; one more gets `503 {"error": "busy"}` and is closed without a thread), `request_queue_size` 64, at most 64 KB of header lines in all (431), a repeated `Host`, `Origin`, `Sec-Fetch-Site`, `Content-Type`, `Content-Length` or `X-Reachmap-Token` is 400, HEAD is 405 with no body, `frame-ancestors 'none'` on the report page's header policy (the page's meta tag stays the M10 policy) and on the workspace policy.
- **Files:** one reader for reports and static files (`runner/files.py`): not a link, `O_NOFOLLOW`, regular, one hard link, size cap; names are matched against the directory's own entry (a case-insensitive file system does not answer `COMPLETE-R1` with `complete-r1`); one name pattern for reports (used by the API, the jobs and the list).
- **Decision text, one rule for every channel** (`review/formats.py`: `check_author`, `check_reason`, applied by `reachmap decide`, the import of a decisions file and the runner): an author's name may not hold any control, format (invisible and direction marks), line or paragraph separator or lone surrogate character; a reason may hold a newline and a tab and no other control, no separator or surrogate, no direction mark (emoji joiners are fine). The messages are fixed words. The store itself is unchanged (it still holds any text, as its tests show). Four M11 tests that sent bidi or control characters through `decide` or an import now send visible hostile text (markup, quotes, `]]>`) and a new test module (`test_review_text`) holds the refusals.
- **minimal.js:** the token is read once, removed from the address bar and history (`history.replaceState`), kept in a variable only, never stored; a report is offered as "Download HTML" (a blob download, the blob revoked after use), not opened: a page opened from a blob would inherit the workspace policy and lose the report's inline script and style. A reload needs the printed address again.
- **Readability:** unused `KNOWN_METHODS` removed, `json` imported at the top, `_ERRORS[505]` is `unsupported_version`.
- **Tests for mutations that had survived:** the token in a query string (401), no token in the child's environment, argv or request (a hook dumps them), `frame-ancestors`, a repeated `Origin`, an `as_of` with a trailing newline, and the common response headers on the report page, `.js` and `.css`.

### Tests after the review fixes

2,467 tests (2,434 before; 33 more: guards 46 (was 37), jobs 41 (was 31), reports 18 (was 13), cli 8 (was 7), review_text 5 new, and the M11 modules changed in place), all passing on 3.11, 3.12, 3.13 and in one serial process.

| Python | serial (`unittest discover`) | parallel (`run_parallel.py`, 4 workers) |
|---|---|---|
| 3.11 | not run | 169.4 s |
| 3.12 | 322.9 s | 118.5 s |
| 3.13 | not run | 167.3 s |

The M12 modules in the 3.12 parallel run: `test_runner_jobs` 17.9 s (26.2 s on 3.11 under load; the 2 s timeout tests were merged to keep it down), `guards` 5.5 s, `e2e` 1.7 s, `cli` 1.1 s, `reports` 1.0 s: about 27 s of work, 25 s when run alone.
A first version of the jobs tests deleted two of its own classes by a slip while merging tests; it was caught by the test count and restored from git before the commit. `tests/.timings.json` was refreshed with `--record`.
Mutations tried on the new code and failing the tests: the sweep of the registry removed, the child not leaving when its parent dies, the result code 4 not mapped to `timed_out`.
