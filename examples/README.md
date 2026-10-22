# Examples

A small workspace you can run Reachmap on without any repository of your own.

- `reachmap.toml`: the configuration of four invented repositories (company "acme": a React
  frontend, a FastAPI backend, a contracts package and a worker). Every key is explained in
  `docs/config.md`.
- `run_example.py`: builds the four repositories into a temporary folder (they are the
  synthetic fixtures under `tests/fixtures/`), then runs the commands of the quickstart in
  order and prints each command with its output.

Run it from a checkout of this repository, with Python 3.11 or newer and git:

    python3 examples/run_example.py                 # a temporary folder, removed at the end
    python3 examples/run_example.py --work /tmp/reachmap-example    # keep the reports to open

It makes no network call and writes nothing outside the work folder. The steps are:

1. build the repositories;
2. analyse a change in one repository and write JSON, Markdown and HTML reports;
3. analyse a contract change that reaches a backend route and a frontend screen;
4. analyse a range between two tags;
5. render the author view of the first report;
6. record two decisions;
7. commit a second revision of the first change, analyse it again and see which decisions
   are current, which need review and which are orphaned;
8. import a decisions file, the way the HTML report's export does.

With `--work` the reports stay in `<work>/reports`. Open an `.html` file in a browser, or
start the workspace on them:

    python3 -m reachmap serve --config examples/reachmap.toml --reports /tmp/reachmap-example/reports

The configuration reads the repositories from `${REACHMAP_FIXTURES}`; the script sets that
variable (to `<work>/repos`) for its own commands. To use the file yourself, set it to the
folder that holds `acme-web`, `acme-api`, `acme-contracts` and `acme-worker`.
