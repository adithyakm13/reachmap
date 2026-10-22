# Releasing Reachmap

This is the checklist for a release: the order of the build, the checks, the clean install test, the tag, and the two decisions that
only the owner of the repository can make. Nothing here publishes anything by itself. The release workflow
(`.github/workflows/release.yml`) builds and checks, keeps the files as workflow artifacts and stops. It has no step that uploads to a
package index, creates a GitHub release or reads a secret, and none should be added without the owner's go-ahead.

## What needs the owner

Two things are not done by the build, by CI or by an assistant working in the repository:

1. **Making the repository public.** The repository is private until the owner changes its visibility on GitHub.
2. **Publishing to PyPI.** The owner runs `twine upload` with their own credentials, or sets up trusted publishing themselves (see
   [Publishing](#publishing-the-owner-decides)). A token never goes into the repository, into a workflow file, into a
   chat or into an issue.

Pushing the release tag is also the owner's call, because the tag is what the release workflow reacts to and what a release is named after.

## Before the build

- `main` has every change of the release, and its CI is green on Python 3.11, 3.12 and 3.13 and for the frontend.
- The version is in one place: `__version__` in `reachmap/__init__.py`. `pyproject.toml` reads it (`dynamic = ["version"]`), so there is
  nothing else to edit. A test (`tests/test_packaging.py`) holds the wheel's metadata to it.
- `CHANGELOG.md` has an entry for the version with its date, lists every pull request that landed, and its Known limits agree with
  `docs/PROGRESS.md`.
- The output snippets in `README.md`, `docs/quickstart.md` and `docs/rules.md` show the new version. Run
  `python3.12 examples/run_example.py --work DIR` (a folder that does not exist yet) and compare. The first line of a summary names
  the version, and a snippet that shows an old one is out of date.
- No `<!-- TODO` comment is left in a document (`git grep -n "<!-- TODO"` prints nothing).

## Build order

The order matters: the wheel holds the workspace only when the frontend was built first. A wheel built without
`frontend/dist/index.html` is valid and serves the minimal page, which is not what a release should ship (the install check below fails
on it for that reason).

Packaging tools are development tools. Put them in a throwaway virtual environment, never in the package's dependencies:

```text
python3.12 -m venv /tmp/reachmap-build
/tmp/reachmap-build/bin/pip install build setuptools
```

1. **Build the frontend** (Node 22.13 or newer):

   ```text
   cd frontend
   npm ci
   npm run build
   cd ..
   ```

   `npm ci` installs exactly what `package-lock.json` says. The build is written to `frontend/dist/`, which is not tracked.

2. **Bundle.** `python -m build` does this by itself: the packaging hook in `setup.py` copies `frontend/dist/` into the build folder as
   `reachmap/runner/static/` (`reachmap/tools/bundle_frontend.py`), after refusing a build that holds a link, a hidden file, a source map
   or a kind of file the runner does not serve. The copy goes into the build folder, never into the checkout, and an old
   `reachmap/runner/static/` in the checkout is never packaged. To serve the workspace from a checkout without installing, run
   `python3.12 -m reachmap.tools.bundle_frontend` by hand. That is not part of a release.

3. **Build the sdist and the wheel:**

   ```text
   rm -rf dist build
   /tmp/reachmap-build/bin/python -m build
   ```

   This leaves `dist/reachmap-VERSION.tar.gz` and `dist/reachmap-VERSION-py3-none-any.whl`. The sdist carries `frontend/dist/`
   (`MANIFEST.in`), so a wheel built from the sdist has the workspace too, and it carries no tests, docs sources or frontend sources.

## Checks

Run all of them on the files that will be released, in this order.

1. **The tests.** `python3.12 tests/run_parallel.py` (the whole suite in worker processes) and, once, `python3.12 -m unittest discover -s tests`
   (what CI runs). With `setuptools` in that Python, add `REACHMAP_REQUIRE_PACKAGING=1` so that the packaging tests cannot skip.
2. **The frontend.** In `frontend/`: `npx tsc --noEmit -p tsconfig.app.json`, `npm test` and `npm run build`.
3. **The audit.** The evaluation tests run `reachmap audit` on the reports of all six fixture workspaces, and they are part of the suite.
4. **The wheel.** List it with `python -m zipfile -l dist/reachmap-*.whl` and check:
   - `reachmap/runner/static/index.html` and `reachmap/runner/static/assets/*.js` and `*.css` are there, and so is
     `reachmap/runner/static/THIRD_PARTY_LICENSES.txt` (the licence notices of the bundled frontend packages, written by the `postbuild`
     step of `npm run build`, which fails when a production package has neither a licence file nor a licence field);
   - there is no `tests/`, no fixture, no `node_modules`, no frontend source (`.ts`, `.tsx`), no source map, no `.claude/`, no
     `docs/briefs/`, no screenshot, no `.env` file;
   - `reachmap-VERSION.dist-info/licenses/LICENSE` is there;
   - `reachmap-VERSION.dist-info/METADATA` has the right `Version`, no `Requires-Dist` line (Reachmap has no runtime dependency), the
     Alpha classifier and the project URLs.

   Read the sdist the same way (`tar tzf dist/reachmap-*.tar.gz`): it has `frontend/dist/THIRD_PARTY_LICENSES.txt`, `CHANGELOG.md`,
   `SECURITY.md` and `CONTRIBUTING.md`. The release workflow runs these listing checks too.
5. **The secrets scan.** Search the unpacked wheel and sdist, and `git ls-files`, for token shapes (`ghp_`, `gho_`, `ghs_`, `ghu_`,
   `github_pat_`, `glpat-`, `xox`), private key headers and email addresses other than `noreply` or `example` ones. The code that
   redacts tokens (`reachmap/redact.py`) and the tests that feed it fake ones name the shapes on purpose; any other hit is a problem.
6. **The punctuation scan.** `tests/test_docs_text.py` holds every document to plain punctuation (no em or en dashes, arrows, curly quotes
   or invisible characters) and to the wording rules. It is part of the suite.
7. **The clean install** (next section), once for each supported Python.

## The clean install check

`tests/release/check_install.py` is a standard library script, not part of the unit test suite. For a given Python it makes a clean
virtual environment, installs the wheel with `pip install --no-index`, so that nothing can be fetched, and runs the installed commands:

| Step | What is checked |
|---|---|
| `reachmap --version` | prints `reachmap` and the version of the wheel, which equals `__version__` |
| `reachmap init` | on the four fixture repositories (built with `tests/fixtures/build.py`) to standard output: a file that the installed package's own config loader loads, with every repository listed and its guesses marked |
| `reachmap analyse` | one scenario (a shared component of the frontend) with `examples/reachmap.toml`: its four screens |
| `reachmap report` | the report as Markdown and as HTML (the HTML loads nothing over the network) |
| `reachmap audit` | on that report: every cited line still matches at its commit (some lines checked, none mismatched, absent or unreadable) |
| `reachmap serve --port 0` | started; the workspace page (not the minimal page), one of its assets and `/api/reports` fetched over `127.0.0.1` with the printed token (and `/api/reports` without it refused with 401), `/api/health` fetched without a token (it is unauthenticated; the page and the assets are static files that need no token either); then stopped |
| `reachmap mcp` | `initialize`, `tools/list` and one `tools/call` (`list_reports` answers with a count of 1) over standard input and output |

It also checks that the installed package holds `reachmap/runner/static/THIRD_PARTY_LICENSES.txt`, that the environment holds nothing but `reachmap` (apart from pip and setuptools of the venv itself), and that the commands
run from a folder that holds no source of the package. It needs `git` and a checkout of the repository (for the fixtures and the example
config), and it exits 0 only when every step passed:

```text
python3.12 tests/release/check_install.py --wheel dist/reachmap-0.1.0-py3-none-any.whl --python python3.11
python3.12 tests/release/check_install.py --wheel dist/reachmap-0.1.0-py3-none-any.whl --python python3.12
python3.12 tests/release/check_install.py --wheel dist/reachmap-0.1.0-py3-none-any.whl --python python3.13
```

`--work DIR` keeps what it made (the venv, the repositories, the reports) for a look, and `--keep` keeps the temporary folder. On the
machine where a Python 3.11 or 3.13 is not installed, install one with `uv python install 3.11 3.13` or let the release workflow do it.

The same thing by hand, for a look at what a person who receives the wheel sees:

```text
python3.12 -m venv /tmp/clean && /tmp/clean/bin/pip install --no-index dist/reachmap-0.1.0-py3-none-any.whl
cd /tmp && /tmp/clean/bin/reachmap --version
```

## The release workflow

`.github/workflows/release.yml` runs when a tag `v*` is pushed, and on request (`workflow_dispatch`, for a dry run on any branch). It:

1. builds the frontend (`npm ci`, `npm run build`) and the sdist and wheel (`python -m build`);
2. on a tag, fails when the tag is not `v` followed by the version in `reachmap/__init__.py`;
3. checks the listings of the wheel and the sdist as above;
4. keeps `dist/` as the workflow artifact `dist` (30 days);
5. in a second job, on Python 3.11, 3.12 and 3.13, downloads that artifact and runs `tests/release/check_install.py` on it.

It only reads the repository (`permissions: contents: read`) and uses no secret. Download the artifact from the run and publish those
exact files, not a build made again later.

## Tagging

After the release changes are merged and the checks above pass on the merged `main`:

```text
git checkout main && git pull
git tag -a v0.1.0 -m "Reachmap 0.1.0"
git push origin v0.1.0
```

The tag is `v` plus the version. Do not move or delete a tag that was pushed: if something is wrong, fix it on `main` and release the next
version. Pushing the tag is the owner's decision, as said above. Until then, a dry run is `workflow_dispatch` on the release branch.

## Publishing (the owner decides)

Neither of these is done for the owner.

**1. The repository.** Making it public is the setting under the repository's Settings on GitHub. Before that, read the checks above once
more, and the history, which is public from then on. `SECURITY.md` tells people to use GitHub's private vulnerability reporting, so switch that on in the repository's settings
when it goes public.

**2. PyPI.** The files to publish are the two in the `dist` artifact of the release run. Check the metadata first with
`python -m twine check dist/*` (twine is a development tool, used from a throwaway environment). Then one of:

- **`twine upload`, run by the owner.** The owner makes a PyPI account and a project-scoped API token there, and runs
  `python -m twine upload dist/*` on their own machine with the token in their own environment (`TWINE_USERNAME=__token__`, `TWINE_PASSWORD`
  from their password manager). The token is never put in the repository, a workflow file, a chat or a log. A first run against TestPyPI
  (`--repository testpypi`) shows what the project page will look like.
- **Trusted publishing.** The owner registers the repository, a workflow file name and an environment as a trusted publisher in the
  project's settings on PyPI (for a project that does not exist yet, as a pending publisher), and adds a separate publish workflow that
  runs on a published release, has `id-token: write` and calls `pypa/gh-action-pypi-publish`, with no token anywhere. This repository does
  not ship that workflow, because it belongs to a decision that is not made.

A version on PyPI cannot be replaced. If a release is bad, yank it on PyPI (it stays installable when pinned, and is skipped otherwise) and
release the next version.

## After the release

- Start a new `## Unreleased` section at the top of `CHANGELOG.md`.
- Update `docs/PROGRESS.md` with what was published, where, and anything that was found.
- `README.md` (the long description on PyPI) and `docs/quickstart.md` say to `pip install reachmap` if the package is on PyPI, or to use a
  wheel file, so they are true before and after the upload and need no edit. The links in `README.md` are absolute and point at the tag
  `v0.1.0` (`tests/test_docs_text.py` checks that each names a file of the checkout), so they work on PyPI and do not move with `main`.
  For the next release, change `v0.1.0` in those links to the new tag. After the upload, add a line to the changelog.
