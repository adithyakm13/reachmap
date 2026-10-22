# Reachmap: working notes

Read `docs/CONTEXT.md` before any work: design, rules, traps, plan, decisions.
Process and milestones: `docs/BOOTSTRAP.md`. Where we are: `docs/PROGRESS.md`.

- Python 3.11+, standard library only in `reachmap/`. No model calls in the analyser.
- Frontend in `frontend/`: react, react-dom, react-router-dom, Mantine (core, hooks, notifications, spotlight), @tabler/icons-react, @xyflow/react, @dagrejs/dagre and prism-react-renderer at runtime (owner decision of M13b); anything else needs the owner.
- Clean-room: no employer code, names, reports, tickets, test cases or tokens.
- Context sections 4 (rules) and 5 (traps) are not negotiable. Every read answers
  found / absent / unreadable; an unreadable read is a blocking incompleteness, never an
  absence. Every finding has pinned evidence. Never claim safety or coverage.
- No network in the default tests. Fixtures in `tests/fixtures/`.
- No em dashes, en dashes, arrows or curly quotes in anything a person reads.
- Ask the owner before: going public, publishing a package, a new runtime dependency.
- No co-author trailer naming an AI and no "generated with" footer in commits or pull
  requests. Commit identity: the owner's name with the GitHub noreply address.

Run the tests:
    python3 -m unittest discover -s tests               # everything in one process (what CI runs)
    python3.12 tests/run_parallel.py                    # the same modules in 4 worker processes, about half the time
    python3.12 tests/run_parallel.py -j 6 tests.test_cli tests.test_pipeline    # more workers, or only some modules
    python3.12 tests/run_parallel.py --record           # refresh tests/.timings.json (it only balances the workers)

On this machine the system `python3` is 3.9; use `python3.12` (installed with uv).
