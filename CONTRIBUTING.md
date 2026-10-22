# Contributing

Read `docs/CONTEXT.md` first. Sections 4 (rules) and 5 (traps) are the product.

## Rules, in short

1. Every finding needs evidence pinned to a commit. A finding without a citation is a bug.
2. Never claim safety or coverage. Wording such as "covered by", "already covers",
   "no need to test", "nothing to test" and "safe" is rejected in code.
3. A suggested test case is a place to look, never coverage.
4. A human decision is risk acceptance, never evidence.
5. An absence must be established, never inferred from a failure. Every read answers
   found, absent or unreadable; unreadable is a blocking incompleteness.
6. Undecidable means affected.
7. The analyser makes no model calls.
8. Report content is untrusted: escape it in HTML, fence it for a model.
9. A run has a ceiling, and a timeout is not a result.
10. Never write to a test management system.
11. Read at the revision you cite.

Every similarity check needs a distinctiveness cap that counts definitions, not
references. Match code, not comments. Place changed lines by diff line number, never by
text.

## Running the tests

    python3 -m unittest discover -s tests

Python 3.11 or newer. The default suite makes no network calls: it runs against synthetic
repositories built on disk and invented host responses shaped like the real APIs.

The `reachmap` package uses the standard library only. Adding a runtime dependency needs
the maintainer's agreement first.

## Frontend (`frontend/`)

The workspace that `reachmap serve` serves is React, TypeScript and Vite. Its runtime dependencies are `react`, `react-dom`,
`react-router-dom`, Mantine (`@mantine/core`, `@mantine/hooks`, `@mantine/notifications`, `@mantine/spotlight`), `@tabler/icons-react`,
`@xyflow/react`, `@dagrejs/dagre` and `prism-react-renderer` and nothing else (the owner added the last six for M13b, see
`docs/briefs/M13b-frontend-redesign.md`); the development dependencies are the ones listed in `docs/briefs/M13-frontend.md`. Every version is
pinned exactly (no `^` or `~`), was published at least two weeks before it was added, and is in the committed `package-lock.json`. The lock
file is made with `npm install --before=<the day two weeks ago>`, so that no package it holds (the ones npm adds because a dependency
needs them included) is younger than that either. Anything else needs the maintainer's agreement first.

Work in `frontend/` (Node 22.13 or newer; CI uses 22):

    npm ci                                  # install exactly what the lock file says
    npx tsc --noEmit -p tsconfig.app.json   # typecheck
    npm test                                # vitest, one run (no watch)
    npm run build                           # writes frontend/dist/ (hashed files, no inline script or style, no external URL)

To look at it, build it, put the build into the package folder and start the runner (it prints the address with the token):

    npm run build && cd .. && python3 -m reachmap.tools.bundle_frontend
    reachmap serve --config reachmap.toml --reports reports

`reachmap/runner/static/` is a build product and is ignored by git. Without it the runner serves its minimal page.

The report fixtures of the frontend tests are in `frontend/fixtures/`: the four hand-made ones of M10 and the reports of scenarios s11 and
s13. `python3.12 frontend/fixtures/make_reports.py` regenerates the two scenario reports and `order.json` (what the Python view model puts in
each section of each report). A Python test (`tests/test_frontend_static.py`) fails when they no longer match what the analyser and the view
model make, and the TypeScript view model is held to `order.json`, so a change to the report layout is made in both places.

### Release order

The wheel is the only thing users install, and it carries the built workspace, so a person who installs it needs no npm. In a checkout:

    cd frontend && npm ci && npm run build && cd ..
    python3 -m build                         # sdist and wheel (`pip install build` first)

`setup.py` copies `frontend/dist/` into the wheel as `reachmap/runner/static/` when `frontend/dist/index.html` exists, and `MANIFEST.in` puts
`frontend/dist/` in the sdist so that a wheel built from the sdist has it too. Without a build nothing is copied and nothing fails: the sdist
and the wheel build without npm and `reachmap serve` serves its minimal page. A build that holds a link, a hidden file or a kind of file the
runner does not serve stops the build. Publishing is the maintainer's decision (see the rules above).

## Adapter acceptance bar

An adapter ships when:

- It reproduces a hand trace on a synthetic fixture and invents nothing beyond it.
- Every Shape A trap (context section 5) that applies has a fixture that makes the read
  fail and asserts an incompleteness, not an absence.
- Every name match has a fixture with a generic word and asserts it is dropped.
- Every citation it produces resolves at its pinned commit.

## Style

Plain hyphens, colons and straight quotes in docs, messages and report wording.
