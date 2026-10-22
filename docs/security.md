# Security

How Reachmap handles credentials, what the local runner and the MCP server allow, and why the
content of a report is treated as untrusted. To report a vulnerability, follow
[SECURITY.md](../SECURITY.md): privately, not in a public issue.

Reachmap reads your code and quotes it. It is meant to run on your own machine, with your own
access, on repositories you are allowed to read. It is not a hosted service and makes no claim
to be one.

Contents: [credentials](#credentials), [what leaves your machine](#what-leaves-your-machine),
[reports are internal data](#reports-are-internal-data),
[report content is untrusted](#report-content-is-untrusted),
[the HTML report](#the-html-report), [the runner](#the-runner-reachmap-serve),
[the MCP server](#the-mcp-server-reachmap-mcp), [supply chain](#supply-chain),
[known limits](#known-limits).

## Credentials

Reachmap stores no credential. It uses one of two things, both yours:

- **The login of a host's own command line**: `gh auth login` for GitHub, `glab auth login` for
  GitLab. The `gh` or `glab` command makes the requests; Reachmap never sees the token. A
  `GH_TOKEN` or `GITHUB_TOKEN` in the environment is read by `gh` itself.
- **A token read from an environment variable by name**: `GITLAB_TOKEN` and `BITBUCKET_TOKEN`
  for the hosts, and the variable you name in `token_env` for a test source such as Qase or
  TestRail. The value is read when a request is sent, sent in one `Authorization` header (or the
  service's own header, such as Qase's `Token`) and nowhere else, and only over https.

How the rest of the tool keeps them out of sight:

- **The config never holds a credential.** A variable whose name looks like a credential is
  refused inside `${...}`. `token_env` holds a variable's name, never its value, and a value that
  is not shaped like a name is refused without being shown. A remote URL with a password, or with
  a user name other than for ssh, is refused: git's own login supplies credentials.
- **`reachmap init` writes only names.** A test source token is named by its environment variable in a comment
  (`# token_env = "QASE_API_TOKEN"`), never given, and a user name or password in a `git:` URL is removed before the
  remote is written, shown or put in an error. The text of a repository that goes into a comment (a path, a package
  name) is made one printable line, so a hostile file name cannot start a line of the file.
- **Messages never repeat a value** you configured. A problem in the config names the key and the
  expected type.
- **A token never appears** in a cache key, a cache file, a log, a report, a command line or an
  error. Every piece of text that leaves the git layer or a host reader is scrubbed: userinfo in
  URLs, values behind `Authorization`, `token=` and similar names, tokens of well-known shapes,
  and every secret that was read from the environment, in the forms it can take (as given, URL
  quoted, the base64 of a Basic header). Tests walk a used cache and the written reports for it.
- **Redirects are refused.** An `Authorization` header never follows a redirect, and a "next page"
  link that leaves the host is refused.
- **`GITLAB_TOKEN` and `BITBUCKET_TOKEN` are removed** from the environment that `git` itself is
  run with.
- A test source is read with GET requests only. Nothing in Reachmap writes to a test management
  system, a host or a repository.

If a token was ever pasted into a config file, remove it and revoke it. Reachmap will have refused
the file, and will not have shown it.

## What leaves your machine

Reachmap makes the network calls you configured and no others:

- `git` fetches from the remotes in your config (shallow, one commit at a time) into a local
  cache. A plain git remote is any URL or path git can clone.
- The host's API, through `gh`, `glab` or direct requests, for a pull or merge request's title,
  state, base, head, merge commit and file count.
- GET requests to a configured test source.

The analyser makes **no model calls** and sends nothing to any other service. The report, the cache
and the decisions stay on your disk. The HTML report makes no network call when you open it.

## Reports are internal data

A report quotes source lines from every repository it read, and lists what a change reaches: it
holds your architecture, your routes, your data contracts and live findings about them. Treat a
report as you would treat the repository.

- Do not attach a report to a public issue or a public pull request of a private repository.
- The git cache (`REACHMAP_CACHE`, default `~/.cache/reachmap`) holds copies of the commits that
  were read. It never holds a credential, and it holds code.
- Machine paths and the name of the person using the machine are kept out of reports (a local
  remote is shown by the repository's name), and a decision's author is the name given on the
  command line or in the workspace, never taken from the account of the machine.

## Report content is untrusted

Everything a report quotes came from a repository, and a repository can contain anything. So a
report is never trusted as the tool's own text:

- **The tool's own sentences** are separate from quoted content. Every sentence the tool writes is
  checked by the wording rules; quoted content is shown as content and never checked as the
  tool's words. A test marks every string of a report and fails if any marked string is ever
  treated as the tool's own.
- **Markdown** escapes quoted text, and puts text that a renderer would turn into a link or an
  emoji in a code span. The tests check the output with the package's own scanner, and a review checked it against a real CommonMark parser.
- **The command line summary** shows control characters, direction marks, invisible characters
  and lone surrogates as visible escapes (`\x0a`, `\u202e`), so they can be read and cannot act.
  Content over 2,000 characters is cut in the rendering and kept whole in the JSON.
- **The HTML report and the workspace** render content as text. See below.
- **The MCP server** fences quoted content for a model. See below.

## The HTML report

The single-file HTML report is meant to be attached to a pull request or a ticket and opened with
nothing installed.

- **No network calls.** No `href`, `src` or `action` anywhere on the page, no font, script or style
  from anywhere else, and a data URL for the favicon. A policy in the page enforces it:

  ```text
  default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-...'; img-src data:; base-uri 'none'; form-action 'none'
  ```

  There is no `connect-src`, so the page cannot make a request. The page's one script is allowed
  by its hash and does nothing but enhance a page that is complete without it (filters, a theme
  toggle, print, and the decision controls). The report itself is embedded as a JSON data block,
  which is not executed. `style-src 'unsafe-inline'` is for the page's own static stylesheet;
  the policy allows nothing else.
- **Content cannot become markup.** The report JSON is embedded for the script with `<`, `>` and
  `&` escaped, so no quoted line can close the script element. Keys of the JSON are never turned
  into markup, and the script writes with text properties only. Tests feed it script-closing
  variants, comment and CDATA openers, U+2028 and U+2029, NUL, surrogates and markup in keys.
- **Decisions in the page stay in the page.** They live in memory and in the browser's
  `localStorage` (a convenience, always inside a `try`), and leave it only when you press "Export
  decisions", which downloads a file made in the page. Nothing is sent anywhere.

## The runner: `reachmap serve`

`reachmap serve` starts a local runner that queues analyses, serves the reports and the
workspace, and records decisions. **It is a runner, not a service: it acts as whoever is logged in
on that machine.** It runs git, and `gh` or `glab`, with that person's access.

| Guard | What it does |
|---|---|
| Loopback only | It binds `127.0.0.1` and there is no option to change that. |
| Host check | A request whose `Host` is not exactly a loopback name with the server's port is refused (421). Binding alone does not stop DNS rebinding; this does. |
| Same origin | A request with another `Origin`, or a `Sec-Fetch-Site` other than same-origin or none, is refused, and no cross-origin header is ever sent. State changes must be JSON. |
| Session token | A random token, printed once in the address you open (in the fragment, so it reaches no log), is required for every API call, in a header and nowhere else, and is compared in constant time. It is never in a response. |
| One job at a time | Jobs run in order, never at once. |
| Separate process, hard limit | Each analysis runs in its own process under `limits.timeout_s`. A timeout ends the process and every git process it started, and the report says `timed_out`. |
| Validated input | Every field of a job or a decision is checked against a fixed list of rules, and a refusal names the field and never echoes the request. There is no shell, no `eval` and no path from the request to a command. |
| Fixed responses | `nosniff`, `no-store`, no referrer, a content security policy on the workspace and on report pages, and `frame-ancestors 'none'`. |
| Caps | A body is at most 1 MB, at most 32 connections are served at once, a request has 15 seconds to arrive and a stalled socket is cut off. |
| Quiet logs | A log line is the method, the path and the status. It never holds a token, a query, a body or a header. |

Do not make the runner a shared service without deciding two things first: whose credentials it
acts with, and where report data is allowed to go. Do not forward its port, and do not put it
behind a proxy that rewrites the `Host` header. Run it as the person whose access you intend to
use.

Static files and reports are served only from their folders: names with dots or empty segments,
links, and anything that is not a regular file are refused. A decision made in the workspace is
stamped with the channel `runner`. Its author and reason refuse control, invisible and direction
characters, in the workspace, on the command line and in an import alike.

## The MCP server: `reachmap mcp`

The Model Context Protocol server lets a model client read reports and write notes beside them.
It speaks over standard input and output: **there is no socket, no port and no host option.** The
client is the process that starts it.

- **A fixed, small tool list**: `list_reports`, `get_report_summary`, `get_section`,
  `get_evidence`, `list_notes`, `add_note` and `propose_file`. None decides, none runs an analysis,
  none reads an arbitrary file, and none reads the configuration. A test asserts the list and that
  no tool name contains a word for deciding, running, or reading a file.
- **What it reads** is the reports folder and its `.reachmap/` files. A proposed file is the one
  exception: it is checked with git at the revisions the report read, and only when you gave the
  server a `--config`.
- **Report content is fenced.** Every string from a report or a note reaches the model between
  lines such as `<<<UNTRUSTED REPORT CONTENT id=...>>>` and an end line, with a random id per
  response that the content cannot contain, and, in the structured result, as `{"untrusted": true,
  "source": "finding.title", "text": "..."}`. A run of three `<` or three `>` in the content is
  broken so that nothing in it can look like a fence line. The preface of every result says the
  text is data and not instructions.
- **Every write is stamped** `recorded via MCP, client reported as <name>`, where the name is the
  one the client gave when it connected. The server builds the stamp; no tool argument can set,
  suppress, double or forge it, and the words say that the name is the client's own claim.
- **Notes are kept apart.** A note is never a finding, evidence or coverage; it goes stale when the
  analysis is rerun; it is shown under "Model notes, recorded via MCP; not evidence". It cannot
  change a count, a priority or a decision. A proposed-file note is marked verified only when the
  file exists at a revision the report read and names a distinctive changed symbol in code, outside
  a comment, and even then it is a note.
- **Stdout carries only protocol messages.** Logs go to standard error and never hold report
  content.

**Analysed repositories are an injection surface** the moment a model reads a report: a comment in
a repository can say anything, and the report quotes it. The fence is a defence in depth, not a
guarantee that a model will treat fenced text as data. Connect a model client with the least access
it needs: give it these tools and nothing that can act on what it reads (no shell, no write access
to a repository, no ability to post), and read what it records before you act on it.

## Supply chain

The `reachmap` Python package has no runtime dependencies: only the standard library. The workspace
that `reachmap serve` serves is built at release time and shipped as static files inside the
package, so installing it needs no npm and runs no install scripts. The frontend's own
dependencies are few, pinned exactly, published at least two weeks before they were added, and
held in a committed lock file; they are build-time only for you.

## Known limits

- A token that is split across two lines in some output is not recognised by the scrubbing. A
  registered secret read from the environment is removed in all its forms when it appears whole.
- The lock that guards revisions, decisions and notes in a reports folder is advisory, and the
  Windows branch of it is written and not run. Keep one reports folder to one machine: the lock
  may not hold on a network file system that does not honour it.
- On Windows the runner does not sweep the git processes of a job that was ended: the job's process
  is ended, its git processes may stay until they finish.
- Jobs are held in memory (the last 500); a restart forgets them. The reports on disk are the
  record.
- `gh` and `glab` were not run in the project's own tests against real hosts, and no test source was
  run against a real service. Treat the first run against each as a trial, with a read-only
  credential.
- The model-client defences are tested against hostile reports and a hostile client name, not
  against any particular model.
