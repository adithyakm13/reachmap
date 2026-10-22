"""Run a language adapter on the M1 scenarios and check its real output against expected.json.

The fixtures are built once per process (`tests.support_git.fixture_repos`) and served as
file:// remotes; each change is resolved by `reachmap.changeset.resolve` with the plain host
and analysed by the adapter the repository's config selects. Only the `expected.json` types a
milestone implements are checked; every other type is skipped explicitly, per scenario, and
the skipped list is part of the result (a test pins it), never silent.

Nothing here is imported by the package.
"""

from __future__ import annotations

import atexit
import dataclasses
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from reachmap import config, entrypoints, propagate
from reachmap.changeset import resolve
from reachmap.gitlayer import Git
from reachmap.model import Evidence
from tests import support_git as sg
from tests.fixtures import build, claims

FIXTURES = Path(build.__file__).resolve().parent

# What M5 (the TypeScript / JavaScript adapter) implements of expected.json, what M7 adds (the walk and the
# entry-point adapters: `screen` and `route` entries, the records `router_unmounted` and `module_cap`) and what M8 adds
# (the cross-repository walker: `http_edge`, `consumer`, `contract_read`, `finding`, the screens, routes and unit tests of
# other repositories, and `hop_cap`) and what M9 adds (`branch_state`, from the branch matrix). Every type is checked.
CROSS_TYPES = frozenset({"http_edge", "consumer", "contract_read", "finding"})
BRANCH_TYPES = frozenset({"branch_state"})
JS_TYPES = frozenset({"changed_symbol", "changed_file", "caller", "unit_test", "screen", "route"}) | CROSS_TYPES | BRANCH_TYPES
JS_NOT_ESTABLISHED = frozenset({"hub_cutoff", "not_distinctive", "pre_change_unavailable", "router_unmounted", "module_cap", "hop_cap", "edge_unfollowed", "entry_points_unread", "routes_unmatched"})
# The Python adapter has the same types and the same record kinds.
PY_TYPES = JS_TYPES
PY_NOT_ESTABLISHED = JS_NOT_ESTABLISHED
# The adapter says why a caller is affected in its own words; the fixtures name the reason after the scenario.
REASONS = {"literal_legacy": "literal_enters_guard", "undecidable": "undecidable"}


@dataclass
class Env:
    dest: Path
    repos: dict[str, Path]
    workspace: Any
    cache: Path
    home: Path


_ENV: Env | None = None


def env() -> Env:
    """The process-wide fixture environment (built on first use)."""
    global _ENV
    if _ENV is None:
        repos = sg.fixture_repos()
        dest = repos["acme/web"].parent
        tmp = Path(tempfile.mkdtemp(prefix="reachmap-scenarios-"))
        atexit.register(sg.remove_tree, tmp)
        ws = config.load(FIXTURES / "reachmap.toml", env={"REACHMAP_FIXTURES": "file://" + str(dest)})
        _ENV = Env(dest, repos, ws, tmp / "cache", tmp / "home")
    return _ENV


@dataclass
class Run:
    scenario: dict
    change: Any
    analysis: Any
    records: list
    git: Git
    adapter: Any
    seconds: float = 0.0
    errors: list[str] = field(default_factory=list)
    entry: Any = None  # the repository's entry-point adapter, when its config has one
    reach: Any = None  # entrypoints.EntryResult: the walk and the areas reached
    propagation: Any = None  # propagate.Propagation: the change followed along the edges into the other repositories
    branches: Any = None  # branches.Matrix: the change on every configured branch of the changed repository
    report_findings: Any = None  # the findings of a report (the end to end harness): checked for kinds and as mirrors of the areas and exclusions
    report_areas: Any = None  # the areas of the report


_RUNS: dict[str, Run] = {}


def make_git(record_to: Any = None) -> Git:
    e = env()
    return Git(e.cache, env=sg.isolated_env(e.home), record_to=record_to if record_to is not None else [])


def scenarios(repo: str) -> list[dict]:
    e = env()
    return [sc for sc in build.scenarios(e.dest) if sc["repo"] == repo]


def run_scenario(scenario: dict, make_adapter: Callable[[Any, Git], Any] | None = None) -> Run:
    """The analysis of the scenario's change (cached by scenario id). It is the one run the suite makes of a scenario: the
    pipeline's, through the CLI (`tests/support_cli.py`), with what its steps made kept (`Options.retain`), so the language
    analysis, the entry points, the propagation and the branch matrix checked here are those of the report the end to end
    scenarios read. `make_adapter` is accepted for the callers that used to build their own: the pipeline builds the adapter
    its config selects."""
    key = scenario["id"]
    if key not in _RUNS:
        from tests import support_cli  # (support_cli imports this module)

        result = support_cli.scenario_run(scenario)
        kept = result.internals
        if result.code != 0 or not kept or kept.get("prop") is None:
            raise AssertionError(f"{key}: the pipeline did not complete (exit {result.code}): {result.err}")
        _RUNS[key] = Run(
            scenario, kept["change"], kept["analysis"], kept["records"], kept["git"], kept["language"], entry=kept["entry"], reach=kept["reach"],
            propagation=kept["prop"], branches=kept["matrix"],
        )
    return _RUNS[key]


# ------------------------------------------------------------------------------ evidence


def iter_evidence(run: Run) -> Iterator[Evidence]:
    """Every Evidence the analysis produced."""
    for impact in run.analysis.impacts:
        for s in impact.symbols:
            yield from s.evidence
        for edge in impact.importers:
            yield edge.evidence
        for caller in impact.callers:
            yield from caller.evidence
            for cs in caller.call_sites:
                yield cs.evidence
                if cs.import_evidence is not None:
                    yield cs.import_evidence
        for ex in impact.exclusions:
            yield from ex.evidence
        for ut in impact.unit_tests:
            yield from ut.evidence
    if isinstance(run.reach, entrypoints.EntryResult):  # (a Mock stands in for a run in the adapter tests: it has no entry points)
        for chain in run.reach.walk.chains:
            yield from chain.hops
        for r in run.reach.reached:
            for hops in r.area.chains:
                yield from hops
            yield r.area.route_line
            for route in r.routes:
                yield from route.evidence
    if isinstance(run.propagation, propagate.Propagation):  # the other repositories (a Mock stands in for a run in the adapter tests): chains that cross, edges, pins, findings
        yield from run.propagation.evidence()


def _show_all(repo_path: Path, specs: list[str]) -> dict[str, str | None]:
    """The text of each `ref:path` (None when git cannot show it), through one `git cat-file --batch`."""
    proc = subprocess.run(
        ["git", *build.GIT_FLAGS, "cat-file", "--batch"], cwd=repo_path, env=build.git_env(Path(repo_path).parent),
        input="".join(spec + "\n" for spec in specs).encode(), capture_output=True, check=False,
    )
    out: dict[str, str | None] = {}
    data, pos = proc.stdout, 0
    for spec in specs:
        end = data.index(b"\n", pos)
        header = data[pos:end].decode()
        pos = end + 1
        parts = header.split()
        if len(parts) == 3 and parts[1] == "blob":
            size = int(parts[2])
            out[spec] = data[pos:pos + size].decode("utf-8", errors="replace")
            pos += size + 1
        else:
            out[spec] = None
    return out


def evidence_problems(repo_paths: dict[str, Path], evidence: Iterable[Evidence]) -> list[str]:
    """For each Evidence: `git show <ref>:<path>` line `<line>` equals `detail`."""
    unique = list(dict.fromkeys(evidence))
    by_repo: dict[str, list[Evidence]] = {}
    for ev in unique:
        by_repo.setdefault(ev.repo, []).append(ev)
    problems = []
    for repo, items in by_repo.items():
        shown = _show_all(repo_paths[repo], sorted({f"{ev.ref}:{ev.path}" for ev in items}))
        for ev in items:
            text = shown[f"{ev.ref}:{ev.path}"]
            if text is None:
                problems.append(f"{ev.locator}: cannot be shown")
                continue
            lines = text.split("\n")
            if ev.line > len(lines) or lines[ev.line - 1].rstrip("\r") != ev.detail:
                problems.append(f"{ev.locator}: line is {lines[ev.line - 1] if ev.line <= len(lines) else None!r}, detail is {ev.detail!r}")
    return problems


_REVS: dict[tuple[str, str], str] = {}


def _rev(repo: str, rev: str) -> str:
    """`sg.rev` kept per (repository, revision): the fixture refs are immutable and `_matches` asks for them for every item."""
    key = (repo, rev)
    if key not in _REVS:
        _REVS[key] = sg.rev(env().repos[repo], rev)
    return _REVS[key]


def _matches(ev: Evidence, expected: dict, run: Run, parent: dict | None = None) -> bool:
    sc = run.scenario
    rev = build.evidence_revision(expected, expected.get("repo", sc["repo"]), sc["repo"], sc["change"], parent)
    sha = _rev(expected.get("repo", sc["repo"]), rev)
    return ev.repo == expected["repo"] and ev.path == expected["path"] and ev.ref == sha and expected["contains"] in ev.detail


def _all(evs: Iterable[Evidence], expected: Iterable[dict], run: Run) -> list[str]:
    evs = list(evs)
    return [f"no evidence at {e['path']} containing {e['contains']!r}" for e in expected if not any(_matches(ev, e, run) for ev in evs)]


# ------------------------------------------------------------------------------ the checks


def skipped_types(
    expected: dict, implemented: Iterable[str] = JS_TYPES, not_established: Iterable[str] = JS_NOT_ESTABLISHED, repo: str | None = None,
) -> list[str]:
    """What is not checked of a scenario, as `section:type` strings, in order: the types that are not implemented and the
    record kinds that are not. (`repo` is accepted for the callers of the milestones before M8, when an entry about
    another repository was skipped: the walker answers them now, so nothing is skipped on its account.)"""
    out = []
    for section in ("must_find", "must_exclude"):
        for entry in expected.get(section, []):
            tag = f"{section}:{entry['type']}"
            if entry["type"] not in implemented and tag not in out:
                out.append(tag)
    for entry in expected.get("not_established", []):
        if entry["kind"] not in not_established:
            tag = f"not_established:{entry['kind']}"
            if tag not in out:
                out.append(tag)
    return out


def check(run: Run, *, not_established: Iterable[str] | None = None) -> list[str]:
    """Compare the adapter's output with expected.json for the implemented types. Returns the
    failures; an empty list is a pass. Entries about another repository than the scenario's are the
    answer of that repository's adapter and are not checked here. `not_established` limits the expected
    records to those kinds (the adapter's own records must then be exactly those); None expects all."""
    exp = run.scenario["expected"]
    repo = run.scenario["repo"]

    def _entries(expected: dict, section: str, type_: str) -> list[dict]:
        return [e for e in expected.get(section, []) if e["type"] == type_ and e.get("repo", repo) == repo]

    a = run.analysis
    failures: list[str] = []
    complete = set(exp.get("complete_for", []))

    symbols = [s for i in a.impacts for s in i.symbols]
    named = [s for s in symbols if not s.is_module]
    # --- changed_symbol
    found_syms = {(s.path, s.name) for s in named}
    for entry in _entries(exp, "must_find", "changed_symbol"):
        want_change = entry.get("change", "modified")
        hits = [s for s in named if s.path == entry["path"] and s.name == entry["symbol"] and s.change == want_change]
        if not hits:
            failures.append(f"changed_symbol {entry['path']} {entry['symbol']} ({want_change}) not reported; got {sorted(found_syms)}")
            continue
        if entry.get("nested"):
            if not any(entry["nested"] in s.nested for s in hits):
                failures.append(f"changed_symbol {entry['symbol']}: nested {entry['nested']!r} not reported")
        # the symbol's own changed lines and the guard lines that enclose them, plus the module-level
        # changes of the same file (an import line is evidence of `<module>`, not of a function)
        own = [ev for s in hits for ev in (*s.evidence, *s.guards)]
        module = [ev for s in symbols if s.path == entry["path"] and s.is_module for ev in s.evidence]
        pool = own + module
        failures += [f"changed_symbol {entry['symbol']}: {m}" for m in _all(own, entry.get("evidence", []), run)]
        if "removed_line" in entry:
            failures += [f"changed_symbol {entry['symbol']} removed_line: {m}" for m in _all(pool, [entry["removed_line"]], run)]
    for entry in _entries(exp, "must_exclude", "changed_symbol"):
        want_change = entry.get("change", "modified")
        if any(s.path == entry["path"] and s.name == entry["symbol"] and s.change == want_change for s in named):
            failures.append(f"changed_symbol {entry['path']} {entry['symbol']} ({want_change}) must not be reported")
    if "changed_symbol" in complete:
        want = {(e["path"], e["symbol"]) for e in _entries(exp, "must_find", "changed_symbol")}
        if found_syms != want:  # `<module>` is a change outside every declaration, not a function: see Implementation notes
            failures.append(f"changed_symbol is complete_for: expected {sorted(want)}, got {sorted(found_syms)}")

    # --- changed_file
    got_files = {(f.path, f.role) for f in a.files}
    want_files = {(e["path"], e["role"]) for e in _entries(exp, "must_find", "changed_file")}
    for item in sorted(want_files - got_files):
        failures.append(f"changed_file {item} not reported; got {sorted(got_files)}")
    if "changed_file" in complete and got_files != want_files:
        failures.append(f"changed_file is complete_for: expected {sorted(want_files)}, got {sorted(got_files)}")

    # --- caller
    callers = [c for i in a.impacts for c in i.callers]
    caller_paths = {c.path for c in callers}
    for entry in _entries(exp, "must_find", "caller"):
        hits = [c for c in callers if c.path == entry["path"]]
        if not hits:
            failures.append(f"caller {entry['path']} not reported; got {sorted(caller_paths)}")
            continue
        if entry.get("symbol") and not any(c.symbol == entry["symbol"] for c in hits):
            failures.append(f"caller {entry['path']}: symbol {entry['symbol']!r}, got {[c.symbol for c in hits]}")
        if entry.get("reason"):
            want = REASONS[entry["reason"]]
            if not any(c.reason == want for c in hits):
                failures.append(f"caller {entry['path']}: reason {want!r}, got {[c.reason for c in hits]}")
        failures += [f"caller {entry['path']}: {m}" for m in _all([ev for c in hits for ev in c.evidence], entry.get("evidence", []), run)]
    if "caller" in complete:
        want = {e["path"] for e in _entries(exp, "must_find", "caller")}
        if caller_paths != want:
            failures.append(f"caller is complete_for: expected {sorted(want)}, got {sorted(caller_paths)}")
    exclusions = [x for i in a.impacts for x in i.exclusions]
    for entry in _entries(exp, "must_exclude", "caller"):
        if "path_prefix" in entry or "path_suffix" in entry:
            prefix, suffix = entry.get("path_prefix", ""), entry.get("path_suffix", "")
            bad = [p for p in caller_paths if p.startswith(prefix) and p.endswith(suffix)]
            if bad:
                failures.append(f"caller {prefix}*{suffix} must not be reported: {sorted(bad)[:3]}")
            continue
        if entry["path"] in caller_paths:
            failures.append(f"caller {entry['path']} must be excluded, but is reported")
        if entry.get("evidence"):
            hits = [x for x in exclusions if x.subject_path == entry["path"]]
            targets = _import_targets(run, entry)  # the files the entry's import lines resolve to
            unit = [t for i in a.impacts for t in i.unit_tests if t.path == entry["path"] and t.via in targets]
            if hits:
                failures += [f"exclusion of {entry['path']}: {m}" for m in _all([ev for x in hits for ev in x.evidence], entry["evidence"], run)]
            elif unit:  # a test is never a caller: it is stated as a unit test, with its import line
                failures += [f"unit test {entry['path']}: {m}" for m in _all([ev for t in unit for ev in t.evidence], entry["evidence"], run)]
            else:
                failures.append(f"caller {entry['path']}: no exclusion stated")

    # --- unit_test
    tests = [t for i in a.impacts for t in i.unit_tests]
    test_paths = {t.path for t in tests}
    for entry in _entries(exp, "must_find", "unit_test"):
        hits = [t for t in tests if t.path == entry["path"]]
        if not hits:
            failures.append(f"unit_test {entry['path']} not reported; got {sorted(test_paths)}")
            continue
        failures += [f"unit_test {entry['path']}: {m}" for m in _all([ev for t in hits for ev in t.evidence], entry.get("evidence", []), run)]
    if "unit_test" in complete:
        want = {e["path"] for e in _entries(exp, "must_find", "unit_test")}
        if test_paths != want:
            failures.append(f"unit_test is complete_for: expected {sorted(want)}, got {sorted(test_paths)}")
    for entry in _entries(exp, "must_exclude", "unit_test"):
        if entry["path"] in test_paths:
            failures.append(f"unit_test {entry['path']} must not be reported")

    failures += _check_entry_points(run, exp, repo, complete)
    failures += _check_cross(run, exp, repo, complete)
    failures += _check_branches(run, exp, repo, complete)
    failures += _check_report_findings(run, exp, complete)

    # --- not_established: the complete list of records
    got_records = sorted((r.kind, r.subject, r.blocks_selection) for r in run.records)
    wanted = [e for e in exp.get("not_established", []) if not_established is None or e["kind"] in set(not_established)]
    want_records = sorted((e["kind"], e["subject"], e["blocking"]) for e in wanted)
    if got_records != want_records:
        failures.append(f"not_established: expected {want_records}, got {got_records}")
    for entry in wanted:
        if entry["kind"] == "hub_cutoff":
            counts = [i.importer_count for i in a.impacts if i.path == entry["subject"]]
            if counts != [entry["importers"]]:
                failures.append(f"hub_cutoff {entry['subject']}: importers {entry['importers']}, adapter counted {counts}")
        if entry["kind"] == "not_distinctive":
            sha = run.change.head_sha
            found = run.adapter.definitions(entry["subject"], sha)
            if found.count != entry["definitions"]:
                failures.append(f"not_distinctive {entry['subject']}: definitions {entry['definitions']}, adapter counted {found.count}")
    return failures


MIRROR_AREA_KINDS = ("direct", "cross-repo")
MIRROR_EXCLUSION_KINDS = ("exclusion", "guard-excluded")


def _check_report_findings(run: Run, exp: dict, complete: set[str]) -> list[str]:
    """What the README says of `finding` for a whole report (see tests/fixtures/README.md): with `complete_for: finding` no kind
    but the listed ones and the mirror kinds; the mirror findings are exactly the reported areas and the exclusions stated."""
    found = getattr(run, "report_findings", None)
    if found is None:
        return []
    failures: list[str] = []
    listed = {e["kind"] for e in exp.get("must_find", []) if e["type"] == "finding"}
    if "finding" in complete:
        extra = sorted({f.kind for f in found} - listed - set(MIRROR_AREA_KINDS) - set(MIRROR_EXCLUSION_KINDS))
        if extra:
            failures.append(f"finding is complete_for: kinds {extra} are not listed and are not mirrors of an area or an exclusion")
    areas = {(a.repo, a.label) for a in (run.report_areas or ())}
    mirrored = [(f.identity[1], f.identity[-1]) for f in found if f.kind in MIRROR_AREA_KINDS]
    if sorted(mirrored) != sorted(areas) or len(mirrored) != len(set(mirrored)):
        failures.append(f"area findings are not one per reported area: findings {sorted(mirrored)[:4]}, areas {sorted(areas)[:4]}")
    prop = run.propagation
    stated = {(p.repo, i.path, x.subject_path, "guard-excluded" if x.reason == "guard" else "exclusion")
              for p in prop.passes if not p.candidate for i in p.analysis.impacts for x in i.exclusions}
    mirrored_x = [(f.identity[1], f.identity[2], f.identity[3], f.kind) for f in found if f.kind in MIRROR_EXCLUSION_KINDS]
    if sorted(mirrored_x) != sorted(stated) or len(mirrored_x) != len(set(mirrored_x)):
        failures.append(f"exclusion findings are not one per stated exclusion: findings {len(mirrored_x)}, exclusions {len(stated)}")
    return failures


def _check_branches(run: Run, exp: dict, repo: str, complete: set[str]) -> list[str]:
    """`branch_state` entries: the rows of the branch matrix. A row says the state of the branch at its tip sha;
    `complete_for` means the matrix has no other branch."""
    wanted = [e for e in exp.get("must_find", []) if e["type"] == "branch_state" and e.get("repo", repo) == repo]
    matrix = getattr(run, "branches", None)
    if matrix is None:
        return [f"branch_state {e['branch']} expected, but no branch matrix was made" for e in wanted]
    failures: list[str] = []
    rows = list(matrix.rows)
    for entry in wanted:
        hits = [r for r in rows if r.branch == entry["branch"]]
        if not hits:
            failures.append(f"branch_state {entry['branch']} not reported; got {sorted(r.branch for r in rows)}")
            continue
        if not any(r.state == entry["state"] for r in hits):
            failures.append(f"branch_state {entry['branch']}: state is {[r.state for r in hits]}, expected {entry['state']!r}")
            continue
        row = [r for r in hits if r.state == entry["state"]][0]
        sha = _rev(entry.get("repo", repo), entry["branch"])
        if row.commit != sha:
            failures.append(f"branch_state {entry['branch']}: read at {row.commit[:12]}, the branch is at {sha[:12]}")
        if entry["state"] == "present" and row.added_lines_found == 0:
            failures.append(f"branch_state {entry['branch']}: present with no added line found")
        if not row.evidence and entry["state"] in ("present", "partial", "pre_change", "diverged"):
            failures.append(f"branch_state {entry['branch']}: {entry['state']} without evidence")
    if "branch_state" in complete:
        want = {e["branch"] for e in wanted}
        got = {r.branch for r in rows}
        if got != want:
            failures.append(f"branch_state is complete_for: expected {sorted(want)}, got {sorted(got)}")
    return failures


def _import_targets(run: Run, entry: dict) -> set[str]:
    """The files that the import lines an entry cites (`evidence`, in the entry's own file at the scenario head)
    resolve to, with the independent resolver of tests/fixtures/claims.py. A line that is not an import line
    (the name line of a multi-line import) resolves to nothing."""
    e = env()
    repo, head = run.scenario["repo"], run.scenario["change"]["head"]
    text = claims.text_at(e.repos[repo], head, entry["path"]) or ""
    tree = claims.Tree(e.repos)
    out: set[str] = set()
    for item in entry.get("evidence", []):
        for line in text.split("\n"):
            if item["contains"] in line:
                target = claims.import_target(tree, repo, head, entry["path"], line)
                if target and target[1]:
                    out.add(target[1])
    return out


def oracle_importers(run: Run, path: str) -> set[str]:
    """The files that import `path` at the scenario head, by the independent parser of
    tests/fixtures/claims.py (tsconfig aliases and relative paths, one regex per import)."""
    e = env()
    return claims.direct_importers(claims.Tree(e.repos), e.repos, run.scenario["repo"], run.scenario["change"]["head"], path)


def _entries(expected: dict, section: str, type_: str) -> list[dict]:
    return [e for e in expected.get(section, []) if e["type"] == type_]


def _in_order(run: Run, actual: Iterable[Evidence], expected: list[dict]) -> list[str]:
    """The actual evidence matches the expected locators one for one, in order (a chain is an ordered list)."""
    actual = list(actual)
    if len(actual) != len(expected):
        return [f"{len(actual)} evidence items, expected {len(expected)}: {[e.detail for e in actual]}"]
    return [f"item {i}: {e.detail!r} is not {x['contains']!r} in {x['path']}" for i, (e, x) in enumerate(zip(actual, expected)) if not _matches(e, x, run)]


def _check_entry_points(run: Run, exp: dict, repo: str, complete: set[str]) -> list[str]:
    """`screen` and `route` entries (the walk and the entry-point adapter of the repository)."""
    def entries(section: str, type_: str) -> list[dict]:
        return [e for e in exp.get(section, []) if e["type"] == type_ and e.get("repo", repo) == repo]

    wanted = entries("must_find", "screen") + entries("must_exclude", "screen") + entries("must_find", "route") + entries("must_exclude", "route")
    reached = run.reach.reached if run.reach is not None else None
    failures: list[str] = []
    if run.reach is not None:
        # whether the answer says it is complete follows the expected records: a hub, a module cap or a blocking record
        # that makes something unreadable means it is not (what the pre-change version lacks is another matter, and an edge that could not be
        # read is the propagation's incompleteness, not that of the entry points of the changed repository)
        expect_cut = any(
            e["kind"] in ("hub_cutoff", "module_cap") or (e["blocking"] and e["kind"] not in ("pre_change_unavailable", "edge_incomplete")) for e in exp.get("not_established", [])
        )
        if run.reach.complete == expect_cut:
            failures.append(f"the entry-point result says complete={run.reach.complete} ({list(run.reach.reasons)[:2]}), the expected records say it is {'not ' if expect_cut else ''}complete")
    if reached is None:  # no entry points configured: a scenario can only claim that none are reached (complete_for with no entry of the type)
        return failures + [f"{e['type']} {e.get('route') or e.get('path')} expected, but the repository has no entry-point adapter" for e in wanted if e in entries("must_find", e["type"])]
    screens = [r for r in reached if r.area.kind == "screen"]
    labels = {r.area.label for r in screens}
    mounted = labels | {m for r in screens for m in r.area.also_mounted}
    for entry in entries("must_find", "screen"):
        hits = [r for r in screens if r.area.label == entry["route"]]
        if not hits:
            failures.append(f"screen {entry['route']} not reported; got {sorted(labels)}")
            continue
        area = hits[0].area
        failures += [f"screen {entry['route']} chain: {m}" for m in _in_order(run, area.chains[0], entry["chain"])]
        if sorted(area.also_mounted) != sorted(entry.get("also_mounted", [])):
            failures.append(f"screen {entry['route']}: also_mounted {sorted(area.also_mounted)}, expected {sorted(entry.get('also_mounted', []))}")
        failures += [f"screen {entry['route']} also_mounted_evidence: {m}" for m in _all([ev for c in area.chains[1:] for ev in c], entry.get("also_mounted_evidence", []), run)]
    for entry in entries("must_exclude", "screen"):
        if entry["route"] in mounted:
            failures.append(f"screen {entry['route']} must not be reported")
    if "screen" in complete:
        want = {e["route"] for e in entries("must_find", "screen")}
        if labels != want:
            failures.append(f"screen is complete_for: expected {sorted(want)}, got {sorted(labels)}")
    routes = [(r.routes[0], r) for r in reached if r.area.kind == "api_route"]
    got_routes = {(rt.method, rt.path) for rt, _ in routes}
    for entry in entries("must_find", "route"):
        hits = [r for rt, r in routes if (rt.method, rt.path) == (entry["method"], entry["path"])]
        if not hits:
            failures.append(f"route {entry['method']} {entry['path']} not reported; got {sorted(got_routes)}")
            continue
        r = hits[0]
        failures += [f"route {entry['path']} evidence: {m}" for m in _in_order(run, r.routes[0].evidence, entry["evidence"])]
        tail = r.area.chains[0][-len(r.routes[0].evidence):]
        if tuple(tail) != tuple(r.routes[0].evidence):
            failures.append(f"route {entry['path']}: the chain does not end with the route's evidence")
    for entry in entries("must_exclude", "route"):
        bad = [(m, p) for m, p in got_routes if m == entry.get("method", m) and (p == entry["path"] if "path" in entry else p.endswith(entry["path_suffix"]))]
        if bad:
            failures.append(f"route {bad} must not be reported")
    if "route" in complete:
        want_routes = {(e["method"], e["path"]) for e in entries("must_find", "route")}
        if got_routes != want_routes:
            failures.append(f"route is complete_for: expected {sorted(want_routes)}, got {sorted(got_routes)}")
    return failures


# ------------------------------------------------------------------------------ across repositories (M8)


def _at_base(entries: list[dict]) -> list[dict]:
    """The locators of a removed route read at the base: the route as a whole existed there (the README reads a mount
    line at the head, where it is the same line; the propagation pins the whole removed route at the base)."""
    return [{**e, "at": "base"} for e in entries]


def _check_cross(run: Run, exp: dict, repo: str, complete: set[str]) -> list[str]:
    """What the walker (`propagate`) found beyond the changed repository: the `http_edge`, `consumer`, `contract_read`
    and `finding` entries, the screens, routes and unit tests of the other repositories, and whether the propagation
    says it is complete. The entries of the changed repository's own screens, routes and unit tests are the in-repo
    checks above."""
    prop = run.propagation
    failures: list[str] = []
    wanted_types = {e["type"] for sec in ("must_find", "must_exclude") for e in exp.get(sec, [])}
    foreign = {(sec, e["type"]) for sec in ("must_find", "must_exclude") for e in exp.get(sec, []) if e["type"] in ("screen", "route", "unit_test") and e.get("repo", repo) != repo}
    if prop is None:
        return [f"{t} expected, but the change was not followed across repositories" for t in sorted(wanted_types & (CROSS_TYPES | {"screen", "route"})) if foreign or t in CROSS_TYPES]

    def entries(section: str, type_: str, *, other: bool = False) -> list[dict]:
        return [e for e in exp.get(section, []) if e["type"] == type_ and (not other or e.get("repo", repo) != repo)]

    # --- whether the answer says it is complete follows the expected records (see `_check_entry_points`)
    # (a blocking record always makes the propagation incomplete, `pre_change_unavailable` too; the stated limits do as hop_cap does)
    expect_cut = any(
        e["kind"] in ("hub_cutoff", "module_cap", "hop_cap", "edge_unfollowed", "entry_points_unread") or e["blocking"] for e in exp.get("not_established", [])
    )
    if prop.complete == expect_cut:
        failures.append(f"the propagation says complete={prop.complete} ({list(prop.reasons)[:2]}), the expected records say it is {'not ' if expect_cut else ''}complete")

    reached = [r for r in prop.reached if not r.area.by_name]
    every = list(prop.reached)  # an exclusion also rejects what was only a candidate, by name: "must not appear" is not "must not be must-test"

    # --- screens and routes of other repositories: chain across repositories, evidence in order, exclusions
    for type_, kind in (("screen", "screen"), ("route", "api_route")):
        have = [r for r in reached if r.area.kind == kind]
        have_all = [r for r in every if r.area.kind == kind]
        for entry in entries("must_find", type_, other=True):
            label = entry["route"] if type_ == "screen" else f"{entry['method']} {entry['path']}"
            hits = [r for r in have if r.area.repo == entry["repo"] and r.area.label == label]
            if not hits:
                failures.append(f"{type_} {label} of {entry['repo']} not reported; got {sorted((r.area.repo, r.area.label) for r in have)}")
                continue
            r = hits[0]
            if type_ == "screen":
                failures += [f"screen {label} chain: {m}" for m in _in_order(run, r.area.chains[0], entry["chain"])]
                if sorted(r.area.also_mounted) != sorted(entry.get("also_mounted", [])):
                    failures.append(f"screen {label}: also_mounted {sorted(r.area.also_mounted)}, expected {sorted(entry.get('also_mounted', []))}")
                failures += [f"screen {label} also_mounted_evidence: {m}" for m in _all([ev for c in r.area.chains[1:] for ev in c], entry.get("also_mounted_evidence", []), run)]
            else:
                failures += [f"route {label} evidence: {m}" for m in _in_order(run, r.routes[0].evidence, entry["evidence"])]
                if tuple(r.area.chains[0][-len(r.routes[0].evidence):]) != tuple(r.routes[0].evidence):
                    failures.append(f"route {label}: the chain does not end with the route's evidence")
        for entry in entries("must_exclude", type_, other=True):
            if type_ == "screen":
                bad = [r for r in have_all if r.area.repo == entry["repo"] and (r.area.label == entry["route"] or entry["route"] in r.area.also_mounted)]
            else:
                bad = [r for r in have_all if r.area.repo == entry["repo"] and r.routes[0].method == entry.get("method", r.routes[0].method)
                       and (r.routes[0].path == entry["path"] if "path" in entry else r.routes[0].path.endswith(entry["path_suffix"]))]
            if bad:
                failures.append(f"{type_} {entry.get('route') or entry.get('path') or entry.get('path_suffix')} of {entry['repo']} must not be reported")
        if type_ in complete and entries("must_find", type_, other=True):
            want = {(e["repo"], e["route"] if type_ == "screen" else f"{e['method']} {e['path']}") for e in entries("must_find", type_)}
            got = {(r.area.repo, r.area.label) for r in have}
            if got != want:
                failures.append(f"{type_} is complete_for: expected {sorted(want)}, got {sorted(got)}")

    # --- unit tests of other repositories
    tests = {(t.repo, t.path): t for t in prop.unit_tests}
    for entry in entries("must_find", "unit_test", other=True):
        t = tests.get((entry["repo"], entry["path"]))
        if t is None:
            failures.append(f"unit_test {entry['path']} of {entry['repo']} not reported; got {sorted(tests)}")
        else:
            failures += [f"unit_test {entry['path']}: {m}" for m in _all(t.evidence, entry.get("evidence", []), run)]
    if "unit_test" in complete and entries("must_find", "unit_test", other=True):
        want_t = {(e.get("repo", repo), e["path"]) for e in entries("must_find", "unit_test")}
        if set(tests) != want_t:
            failures.append(f"unit_test is complete_for: expected {sorted(want_t)}, got {sorted(tests)}")

    # --- http edges
    edges = [m for m in prop.http_edges if not m.candidate]

    def edge_key(m: Any) -> tuple[str, str, str, str, str]:
        return (m.client.repo, m.client.path, m.client.detail.strip(), m.route.method, m.route.path)

    for entry in entries("must_find", "http_edge"):
        fr, to = entry["from"], entry["to"]
        hits = [m for m in edges if m.client.repo == fr["repo"] and m.client.path == fr["path"] and fr["contains"] in m.client.detail
                and (m.route.method, m.route.path) == (to["method"], to["path"]) and m.removed == to.get("removed", False)]
        if not hits:
            failures.append(f"http_edge {fr['path']} {fr['contains']!r} to {to['method']} {to['path']} not reported; got {sorted(edge_key(m)[1:] for m in edges)}")
            continue
        m = hits[0]
        failures += [f"http_edge client line: {x}" for x in _all([m.client], [fr], run)]
        failures += [f"http_edge via: {x}" for x in _all(m.via, entry.get("via", []), run)]
        want = _at_base(to.get("evidence", [])) if to.get("removed") else to.get("evidence", [])
        failures += [f"http_edge route evidence: {x}" for x in _in_order(run, m.route.evidence, want)]
    for entry in entries("must_exclude", "http_edge"):
        fr, to = entry["from"], entry["to"]
        bad = [m for m in prop.http_edges if m.client.path == fr["path"] and fr["contains"] in m.client.detail and (m.route.method, m.route.path) == (to["method"], to["path"])]
        if bad:
            failures.append(f"http_edge to {to['method']} {to['path']} must not be reported ({entry['why']})")
    if "http_edge" in complete:
        want_e = {(e["from"]["repo"], e["from"]["path"], e["to"]["method"], e["to"]["path"]) for e in entries("must_find", "http_edge")}
        got_e = {(m.client.repo, m.client.path, m.route.method, m.route.path) for m in edges}
        if got_e != want_e:
            failures.append(f"http_edge is complete_for: expected {sorted(want_e)}, got {sorted(got_e)}")

    # --- findings
    for entry in entries("must_find", "finding"):
        hits = [f for f in prop.findings if f.kind == entry["kind"] and (f.risk, f.confidence) == (entry["risk"], entry["confidence"])]
        ok = [f for f in hits if not _all(f.evidence, entry["evidence"], run)]
        if not ok:
            failures.append(f"finding {entry['kind']} ({entry['risk']}, {entry['confidence']}) not reported with its evidence; got {[(f.kind, f.risk, f.confidence) for f in prop.findings]}")
    for entry in entries("must_exclude", "finding"):
        if any(f.kind == entry["kind"] for f in prop.findings):
            failures.append(f"finding {entry['kind']} must not be reported ({entry['why']})")
    if "finding" in complete:
        want_f = sorted(e["kind"] for e in entries("must_find", "finding"))
        if sorted(f.kind for f in prop.findings) != want_f:
            failures.append(f"finding is complete_for: expected {want_f}, got {sorted(f.kind for f in prop.findings)}")

    # --- consumers
    for entry in entries("must_find", "consumer"):
        hits = [c for c in prop.consumers if c.repo == entry["repo"] and c.branch == entry["branch"]]
        if not hits:
            failures.append(f"consumer {entry['repo']} on {entry['branch']} not reported; got {sorted((c.repo, c.branch) for c in prop.consumers)}")
            continue
        c = hits[0]
        for key, got in (("pin", c.pin), ("adoption", c.adoption), ("contains_change", c.contains_change)):
            if key in entry and entry[key] != got:
                failures.append(f"consumer {entry['repo']} on {entry['branch']}: {key} is {got!r}, expected {entry[key]!r}")
        sha = _rev(entry["repo"], entry["branch"])
        if c.sha != sha:
            failures.append(f"consumer {entry['repo']} on {entry['branch']}: read at {c.sha[:12]}, the branch is at {sha[:12]}")
        failures += [f"consumer {entry['repo']} on {entry['branch']}: {m}" for m in _all(c.evidence, entry.get("evidence", []), run)]  # evidence of a consumer entry is read at its branch
    if "consumer" in complete:
        want_c = {(e["repo"], e["branch"]) for e in entries("must_find", "consumer")}
        got_c = {(c.repo, c.branch) for c in prop.consumers}
        if got_c != want_c:
            failures.append(f"consumer is complete_for: expected {sorted(want_c)}, got {sorted(got_c)}")

    # --- contract reads
    for entry in entries("must_find", "contract_read"):
        hits = [r for r in prop.contract_reads if (r.repo, r.path, r.symbol) == (entry["repo"], entry["path"], entry["symbol"])]
        if not hits:
            failures.append(f"contract_read {entry['path']} ({entry['symbol']}) not reported; got {sorted((r.repo, r.path, r.symbol) for r in prop.contract_reads)}")
        elif not any(not _all(r.evidence, entry["evidence"], run) for r in hits):
            failures.append(f"contract_read {entry['path']} ({entry['symbol']}): no read has the evidence {[e['contains'] for e in entry['evidence']]}")
    for entry in entries("must_exclude", "contract_read"):
        bad = [r for r in (*prop.contract_reads, *prop.contract_candidates) if (r.repo, r.path) == (entry["repo"], entry["path"])]
        if bad:
            failures.append(f"contract_read {entry['path']} must not be reported ({entry['why']})")
    if "contract_read" in complete:
        want_r = {(e["repo"], e["path"], e["symbol"]) for e in entries("must_find", "contract_read")}
        got_r = {(r.repo, r.path, r.symbol) for r in prop.contract_reads}
        if got_r != want_r:
            failures.append(f"contract_read is complete_for: expected {sorted(want_r)}, got {sorted(got_r)}")
    return failures
