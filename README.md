# Reachmap

Reachmap tells a team what a code change reaches across any number of repositories, and what
to test before release, with evidence for every claim.

Point it at a pull request, a branch comparison or a commit range. It follows imports,
routes, packages and shared data contracts to the screens and entry points a person can test.
Every inclusion and every exclusion cites a line that can be re-read at the commit it names.

Status: 0.1.0, early. It has been tested on small invented repositories and on invented host
responses shaped like the real APIs, not yet on large real ones. Expect gaps; the report says
where it has them.

## What it never claims

- **It never claims safety.** A report says what a change reaches and what the run could not
  look at. Whether to release is a person's decision, and Reachmap only informs it.
- **It never claims coverage.** No report says a test covers a change or that a screen needs no
  testing. A suggested test case is a place to look and is never counted as a mapped test.
- **It never turns a failure into an absence.** If a file could not be read, a search was cut
  short or a route could not be resolved, the report says it was not established. A short list
  is not taken for the whole list, and a timeout is not a result.
- **A decision is risk acceptance.** Skipping an area reads "risk accepted, not tested" and
  changes nothing in the analysis.
- **It makes no model calls.** The analyser is deterministic and uses only the Python standard
  library.

[docs/rules.md](https://github.com/adithyakm13/reachmap/blob/v0.1.0/docs/rules.md) explains how to read a report with these rules in mind.

## Try it in about a minute

You need Python 3.11 or newer and `git`. Reachmap has no dependencies. Install it with
`pip install reachmap` if the package is on PyPI, or install a wheel file you were given, or run it
from a checkout:

```text
pip install reachmap                  # or: pip install <the file>.whl
                                      # or, in a checkout: python3 -m reachmap ... in place of reachmap ...
```

([The quickstart](https://github.com/adithyakm13/reachmap/blob/v0.1.0/docs/quickstart.md#install) has the details.)

Save this as `reachmap.toml` in a repository of React code that uses React Router (the
file lists the repository; `.` is the folder the file is in). Set `routes_glob` to the files of
your own that hold the `<Route>` elements; the default is `src/**/routes.tsx` (and `.jsx`,
`.ts`, `.js`):

```toml
workspace = "acme"

[repos."acme/web"]
host = "git"
remote = "."
language = "typescript"
roles = ["frontend"]
branches = ["main", "release/*"]
entry_points = { kind = "react-router", routes_glob = "src/modules/*/routes.tsx" }
```

Two things to know for a first run. A `routes_glob` that matches no file is a blocking
`routes_unmatched` record and an answer that says it is not complete, never an empty list of
screens. And a mistyped key in any table is refused with the nearest allowed name; see
[docs/config.md](https://github.com/adithyakm13/reachmap/blob/v0.1.0/docs/config.md).

You do not have to write the file by hand. `reachmap init` reads the repositories and writes a starter
`reachmap.toml` to standard output (`NAME=SPEC` names a repository in the file; a path, a `file://` URL,
`github:owner/name`, `gitlab:group/name`, `bitbucket:ws/name` or `git:<url>` says where it is). Every value it
guessed has a comment above it that says why, where in the repository (path:line) and how sure it is, and ends
in "confirm":

```text
$ reachmap init acme/web=. > reachmap.toml
reachmap: wrote the config for 1 repository: 12 guesses to confirm (each has a `# guess:` comment above it); review the file before running reachmap analyse
$ grep -B1 '^roles' reachmap.toml
# guess: frontend: the react-router entry points, evidence src/modules/billing/routes.tsx:9, src/modules/cases/routes.tsx:9, src/modules/reports/routes.tsx:10, confidence high; confirm
roles = ["frontend"]
```

Read the guesses and fix what is wrong before the first run. `--out FILE` writes a file instead (and `--force`
replaces one); a repository or a file it could not read is listed in the file and the exit code is 3.

Then analyse a topic branch against the main branch and write a report you can open or attach
(use your own branch in place of `scenario/s01-shared-component`, the branch of the example
workspace that the output below comes from):

```text
$ reachmap analyse --repo acme/web --compare main...scenario/s01-shared-component --format md,html
reachmap 0.1.0: acme/web, compare main...scenario/s01-shared-component, head 7fbf97d99854
Areas to test: 4 (must test 4)
  must test  screen /billing/invoices (acme/web)
  must test  screen /billing/invoices/:invoiceId (acme/web)
  must test  screen /cases (acme/web)
  must test  screen /cases/:caseId (acme/web)
Findings: 4 (direct 4)
Branches: main pre_change, release/2.0 pre_change, release/2.1 pre_change
Report: reports/acme-web-compare-main-scenario-s01-shared-component-941269bf-7fbf97d99854-r1.json
Markdown: reports/acme-web-compare-main-scenario-s01-shared-component-941269bf-7fbf97d99854-r1.md
HTML: reports/acme-web-compare-main-scenario-s01-shared-component-941269bf-7fbf97d99854-r1.html
```

The first line names the installed version, the change and the commit analysed. The HTML file is
a single page with no network use, ready to attach to a pull request. `reachmap serve` starts a
workspace on `127.0.0.1` to browse reports, read the chain of lines behind each screen and record
decisions.

To see everything on invented repositories first, run `python3 examples/run_example.py` in a
checkout.

## Documentation

| | |
|---|---|
| [Quickstart](https://github.com/adithyakm13/reachmap/blob/v0.1.0/docs/quickstart.md) | Install, configure, analyse a pull request, a comparison and a range, read the report, the workspace, decisions, reruns |
| [Configuration](https://github.com/adithyakm13/reachmap/blob/v0.1.0/docs/config.md) | Every key of `reachmap.toml` |
| [Adapters](https://github.com/adithyakm13/reachmap/blob/v0.1.0/docs/adapters.md) | What each adapter reads, its limits, and how to write one |
| [Rules](https://github.com/adithyakm13/reachmap/blob/v0.1.0/docs/rules.md) | What it never claims, what "could not establish" means, blocking, risk and confidence |
| [Security](https://github.com/adithyakm13/reachmap/blob/v0.1.0/docs/security.md) | Credentials, the local runner, the MCP server, untrusted report content |
| [Evaluation](https://github.com/adithyakm13/reachmap/blob/v0.1.0/docs/evaluation.md) | How to measure whether it helps, and how to report that honestly |
| [Examples](https://github.com/adithyakm13/reachmap/blob/v0.1.0/examples/README.md) | A small workspace and a script that runs the quickstart on it |
| [Changelog](https://github.com/adithyakm13/reachmap/blob/v0.1.0/CHANGELOG.md) | What is in each release |
| [Releasing](https://github.com/adithyakm13/reachmap/blob/v0.1.0/docs/releasing.md) | The release checklist: build order, checks, the clean install test, what needs the owner |
| [Contributing](https://github.com/adithyakm13/reachmap/blob/v0.1.0/CONTRIBUTING.md) | The rules a change has to keep, and the acceptance bar for an adapter |

## What it reads today

TypeScript, JavaScript, Python, Java and Kotlin code; screens from React Router and Next.js; API
routes from FastAPI, Django and Django REST Framework, Express, NestJS, Spring Boot and OpenAPI
specs; links between
repositories by HTTP, by package pin and by a shared data contract; suggested test cases from
Qase, TestRail, Playwright and Cypress; repositories on GitHub, GitLab, Bitbucket Cloud or any
plain git remote. The [adapters page](https://github.com/adithyakm13/reachmap/blob/v0.1.0/docs/adapters.md) lists what each one cannot read.

Some of this has only been tried against invented data. `gh` and `glab` were never run against a
real host, and `--pr` was exercised with a fake host only. Nothing was run against a real Qase or
TestRail. OpenAPI specs must be JSON. The [changelog](https://github.com/adithyakm13/reachmap/blob/v0.1.0/CHANGELOG.md#known-limits) lists the known
limits of this release.

## Licence

Apache-2.0. See [LICENSE](https://github.com/adithyakm13/reachmap/blob/v0.1.0/LICENSE).

The bundled workspace includes third-party code under its own licences, listed in `reachmap/runner/static/THIRD_PARTY_LICENSES.txt` inside the installed package.
