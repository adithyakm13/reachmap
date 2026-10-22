# M19 brief: release preparation

Goal: everything needed for a `0.1.0` release, short of making the repository public or
publishing to PyPI (both need the owner's go-ahead: ground rule 4; this milestone ends by
asking). Read `docs/BOOTSTRAP.md` (M19 row, ground rules), `docs/CONTEXT.md` sections 1,
4, 7, 8, 9, 11, 13; `docs/PROGRESS.md`; every brief's Implementation notes (known limits
feed the docs).

## Docs (`docs/` and README; plain punctuation, no em/en dashes, arrows or curly quotes)

- `README.md`: what Reachmap is, what it never claims, a 60-second quickstart on a local
  repo with the plain git host, links to the other docs, status "0.1.0, early".
- `docs/quickstart.md`: install, `reachmap init`, confirm the guesses, `reachmap analyse`
  for a PR, a compare and a range, reading the report, `reachmap serve`, decisions,
  `--decisions` import, rerun and staleness.
- `docs/config.md`: every `reachmap.toml` key (generated from the M3 registries where
  possible, checked by a test that every registered key is documented), `${VAR}` rules,
  limits, edges, test sources, tokens by name.
- `docs/adapters.md`: what each adapter reads, its records, its known limits, and the
  adapter guide for contributors (protocols, registries, acceptance bar, fixture rules).
- `docs/rules.md`: context sections 4 and 5 for users: what the tool will never say, what
  "could not establish" means, blocking vs non-blocking, how to read risk and confidence.
- `docs/security.md` (or extend SECURITY.md): credentials, the runner posture, MCP
  posture, report content as untrusted, HTML report guarantees.
- `docs/evaluation.md`: how to run the audit, witness harness and judging sheets, and how
  to report results (counts, independence, weak small samples, no recall without linked
  defects).
- `CHANGELOG.md`: 0.1.0 with the milestone contents.
- `examples/`: a small example workspace (the fixture repos built by a script into a temp
  folder, the config, the commands, and a sample report rendered to Markdown and HTML).

## Version and packaging

- Version `0.1.0` in one place (package `__version__`, pyproject reads it dynamically or a
  test asserts they match); classifiers; project URLs pointing at the GitHub repo
  (private now; fine).
- Build the wheel with the frontend inside: `cd frontend && npm ci && npm run build`, then
  `python -m build`. Inspect the wheel: package data, static files, no tests, no fixtures,
  no frontend sources, no node_modules, no secrets, licence included.
- Clean-venv install test for 3.11, 3.12, 3.13: `python -m venv`, `pip install
  dist/reachmap-0.1.0-py3-none-any.whl` (no network needed: no dependencies), then:
  `reachmap --version`, `reachmap init` on the example repos, `reachmap analyse` on one
  scenario, `reachmap report` to md and html, `reachmap serve` started and the workspace
  page fetched with the token, `reachmap mcp` initialize over stdio, `reachmap audit` on
  the report. A script under `tests/release/` runs this; a CI job runs it on tags.
- A release checklist in `docs/releasing.md` (build order, checks, tag, what needs the
  owner).

## Final checks

- Full suite green on 3.11, 3.12, 3.13; frontend tsc, tests and build green; audit green on
  all fixture reports; banned punctuation scan over docs and code; no employer names,
  tickets or tokens (grep for the clean-room terms listed in CONTRIBUTING plus a
  token-shape scan); licence headers or NOTICE as Apache-2.0 requires.

## Then stop

Give the owner a status: what is ready, test counts, wheel contents, what remains known
not to work, and ask two questions: make the repository public? publish 0.1.0 to PyPI
(they run `twine upload` or set up trusted publishing themselves; never ask for a token)?
