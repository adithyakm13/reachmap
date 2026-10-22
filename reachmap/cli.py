"""Command line entry point: `reachmap analyse` and `reachmap report`.

    reachmap analyse --config reachmap.toml --repo acme/api (--pr N | --compare A...B | --range A..B)
                     [--as-of ISO] [--out DIR] [--format json,md,html] [--decisions FILE] [--quiet]
    reachmap report REPORT.json [--format md|html] [--out FILE] [--view full|author]
    reachmap decide REPORT.json (--target ID --verdict V [--reason TEXT] --author NAME | --import FILE | --list)
    reachmap serve [--port 8787] [--config reachmap.toml] [--reports DIR]
    reachmap mcp --reports DIR [--config reachmap.toml]
    reachmap audit REPORT.json... [--config reachmap.toml] [--show N] [--allow unreadable]
    reachmap witness CASES.toml [--config reachmap.toml]
    reachmap sheet REPORT.json [--out sheet.csv] [--format csv|md]  |  reachmap sheet --score FILLED.csv
    reachmap init SPEC [SPEC ...] [--out reachmap.toml [--force]]      (without --out the TOML goes to standard output)

`analyse` writes `<out>/<repo-slug>-<change-slug>-<short sha>.json` (the report: `"schema": 1`, the tool and its version, when and
how long the run took, the inputs, the counts, then everything `Analysis.to_dict` holds) and, for each other format asked for,
`.md` and `.html` files with the same base name next to it, then prints a short summary: blocking records first, then the areas
to test, the findings by kind, the branches. The summary never says a release is safe or that nothing needs testing, and a whole
answer never calls itself complete: only an incomplete one says so. The JSON is always written: it is the contract the other
formats are rendered from.

`report` renders an existing report file (to stdout, or to `--out`); a report with an unknown major version is refused (exit 2).

Review (M11): every completed run is a revision of the analyses with the same key (workspace, repository, change as written), numbered in a log
beside the reports; the report carries its revision, the fingerprint of every area and finding and the decisions of the store, carried to this run:
current, stale (the claim changed) and orphaned (the target is gone). `decide` records one decision (or lists the targets, or imports the file
the HTML report exports; `analyse --decisions FILE` imports it before the run is reported). A decision is risk acceptance: it changes nothing
in the analysis. The store and the log are `<reports dir>/.reachmap/`.

`serve` (M12) starts the localhost runner (`reachmap.runner`): it queues analyses, runs them one at a time in a child process with a hard
timeout, serves the reports from `--reports` and records decisions (channel `runner`). It listens on 127.0.0.1 only (there is no host option)
and prints the one URL to open, `http://127.0.0.1:PORT/#token=...`; the token is in the fragment so that it never reaches a log. It runs
until interrupted (exit 0); a config, a port or a reports folder that cannot be used is exit 2.

`mcp` (M14) serves the Model Context Protocol over standard input and output (newline-delimited JSON-RPC, no socket): a model client reads the
reports in `--reports`, in fenced text, and records notes beside them, each stamped with the client's declared name (`reachmap.mcp`, `reachmap.notes`).
`--config` lets it check a proposed file against the repositories through the git layer; without it a proposed file is kept as a note that says no
file was read. Only JSON-RPC goes to standard output; logs go to standard error and never hold report content. It ends, exit 0, when its input
ends; a reports folder or a config that cannot be used is exit 2.

`init` (M17) reads each repository (`github:owner/name`, `gitlab:group/name`, `bitbucket:ws/name`, `git:<url>`, a local path or a `file://` URL;
`NAME=SPEC` chooses the name in the config) through the git layer and writes a starter `reachmap.toml` (`reachmap.init`): every guess has a
`# guess:` comment with its evidence (path:line) and a confidence word, ends in "confirm", and no secret is ever written. Without `--out` the text goes
to standard output (`reachmap init github:acme/web > reachmap.toml`) and the summary to standard error; `--out FILE` writes the file atomically and
replaces an existing one only with `--force`. The text always loads with `config.load`. A repository that cannot be read is listed in a "Could not
read" comment block (never skipped silently) and so is every file that could not be read (a symlink, a submodule, a name that is not UTF-8, text that is
not UTF-8, a file nested too deeply to parse); the exit code is 3 for either (also when no repository could be read: then nothing is written). A spec that
is no spec, a name two repositories share and a file that exists without `--force` are exit 2.

With `--quiet` nothing is printed for a run that completes with nothing blocking; blocking records still go to stderr.

Exit codes: 0 the run completed (also with blocking records, which the summary starts with); 2 usage or config error (the
message names the option or the config key, never a configured value; for `report` also a file that is not a readable report);
3 the change set was refused or the run failed (the report is still written, with no findings and no count), or a format
could not be written; 4 the run hit its time limit (same as 3 for the report).

`main` takes its inputs and outputs as arguments so that a test can run it in process: `env` (the variables the config's
`${VAR}` and `REACHMAP_CACHE` read), `stdout`, `stderr`, `git_env` (overrides of git's environment), `clock` (the deadline's
time source), `now` (the wall clock of the report's `run` object), `retain` (a dict the pipeline fills with what its steps made) and
`outcome` (a dict `analyse` fills with the name of the report file it wrote and the run's status: what the runner reads) and
`transport` (the transport the hosted test sources send their GET requests through; a test passes a fake, the default sends real ones).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Callable, Mapping, Sequence, TextIO

import reachmap
from reachmap import config, gitlayer, notes, pipeline, testsources
from reachmap import report as reports
from reachmap.model import Analysis, Decision, describe, verdict_text
from reachmap.errors import ModelError
from reachmap.report import summary, text
from reachmap.report.summary import AREAS_SHOWN
from reachmap.review import attach, carry, exchange, formats, lock, revisions, store
from reachmap.review.formats import rule_words

__all__ = ["AREAS_SHOWN", "main", "summary_lines"]

SCHEMA = 1
FORMATS = ("json", "md", "html")
_ALIASES = {"markdown": "md"}
EXIT_OK, EXIT_USAGE, EXIT_FAILED, EXIT_TIMED_OUT = 0, 2, 3, 4


class _Usage(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Any:  # argparse would exit; main turns it into exit code 2 on stderr
        raise _Usage(message)


def _parser() -> argparse.ArgumentParser:
    top = _Parser(prog="reachmap", description="What a change reaches across repositories, and what to test, with evidence.")
    top.add_argument("--version", "-V", action="store_true", help="print the version")
    sub = top.add_subparsers(dest="command", parser_class=_Parser)
    an = sub.add_parser("analyse", help="analyse one change and write its report")
    an.add_argument("--config", default="reachmap.toml", help="the workspace file (default: reachmap.toml)")
    an.add_argument("--repo", required=True, help="the repository the change is in, as the config names it")
    what = an.add_mutually_exclusive_group(required=True)
    what.add_argument("--pr", help="a pull or merge request number (12, #12 or !12)")
    what.add_argument("--compare", metavar="BASE...HEAD", help="what HEAD has since the merge base with BASE (three dots)")
    what.add_argument("--range", metavar="A..B", help="everything from A to B (two dots)")
    an.add_argument("--as-of", metavar="ISO", help="pin the refs read by name as of this time (ISO 8601; a naive time is UTC)")
    an.add_argument("--out", default="reports", help="the directory the report is written to (default: reports)")
    an.add_argument("--format", default="json", help="report formats, comma separated: json, md, html (json is always written; default: json)")
    an.add_argument("--decisions", metavar="FILE", help="import the decisions file the HTML report exports before the run is reported")
    an.add_argument("--quiet", action="store_true", help="print nothing when the run completes")
    rep = sub.add_parser("report", help="render an existing report file as Markdown or HTML")
    rep.add_argument("report", metavar="REPORT.json", help="a report written by reachmap analyse")
    rep.add_argument("--format", default="md", help="md (default) or html")
    rep.add_argument("--out", metavar="FILE", help="write here instead of standard output")
    rep.add_argument("--view", default="full", help="full (default) or author (the header and the For the author section)")
    srv = sub.add_parser("serve", help="start the localhost runner: queue analyses, serve reports, record decisions (127.0.0.1 only)")
    srv.add_argument("--port", default="8787", help="the port on 127.0.0.1 (default: 8787; 0 picks a free one)")
    srv.add_argument("--config", default="reachmap.toml", help="the workspace file (default: reachmap.toml)")
    srv.add_argument("--reports", default="reports", help="the directory the reports, the revision log and the decisions are kept in (default: reports)")
    mcp = sub.add_parser("mcp", help="serve MCP over stdio: a model client reads reports (fenced) and records notes beside them")
    mcp.add_argument("--reports", required=True, help="the directory the reports, the revision log and the notes are kept in")
    mcp.add_argument("--config", help="the workspace file, to check a proposed file against the repositories (without it none is read)")
    aud = sub.add_parser("audit", help="re-read every line a report cites at its commit and say which still match (exit 1 when any does not)")
    aud.add_argument("reports", nargs="+", metavar="REPORT.json", help="reports written by reachmap analyse")
    aud.add_argument("--config", default="reachmap.toml", help="the workspace file the reports were made with, to find the repositories (default: reachmap.toml)")
    aud.add_argument("--show", type=int, default=10, help="how many failures to list per report (default: 10)")
    aud.add_argument("--allow", choices=["unreadable"], help="do not fail on citations that could not be read (they are still counted and said)")
    wit = sub.add_parser("witness", help="count whether the reports of past changes cite files a later follow-up fix touched (counts, never percentages)")
    wit.add_argument("cases", metavar="CASES.toml", help="the cases: an original change, its merge time and a later witness change each")
    wit.add_argument("--config", help="the workspace file (default: the file's `config` key, else reachmap.toml)")
    sht = sub.add_parser("sheet", help="write a judging sheet for a report (for people to fill in, never a model), or count a filled one")
    sht.add_argument("report", nargs="?", metavar="REPORT.json", help="a report written by reachmap analyse")
    sht.add_argument("--out", metavar="FILE", help="write the sheet here instead of standard output")
    sht.add_argument("--format", help="csv (default) or md; a file name ending in .md is md")
    sht.add_argument("--score", metavar="FILLED.csv", help="count the answers of a filled sheet (counts per column; refuses a sheet it cannot read)")
    dec = sub.add_parser("decide", help="record a decision about an area or a finding of a report, list the targets, or import a decisions file")
    dec.add_argument("report", metavar="REPORT.json", help="a report written by reachmap analyse; the decision is kept beside it")
    dec.add_argument("--target", help="the id of an area (a-...) or a finding (f-...): reachmap decide REPORT.json --list shows them")
    dec.add_argument("--verdict", help="areas: test, sample, skip; findings: accept, reject")
    dec.add_argument("--reason", default="", help="why (needed for skip and reject)")
    dec.add_argument("--author", help="your name, as you want it shown (never taken from the account of this machine)")
    dec.add_argument("--import", dest="import_file", metavar="FILE", help="import a decisions file exported by the HTML report")
    dec.add_argument("--list", action="store_true", help="list the ids, kinds and labels of the targets with their decisions")
    ini = sub.add_parser("init", help="read repositories and write a starter reachmap.toml with every guess marked for review")
    ini.add_argument("specs", nargs="+", metavar="SPEC", help="github:owner/name, gitlab:group/name, bitbucket:ws/name, git:URL, a local path or a file:// URL; "
                     "NAME=SPEC chooses the name in the config (NAME is letters, digits, . _ - and slashes, with no colon; a path that holds an = needs ./ in front)")
    ini.add_argument("--out", default=None, metavar="FILE", help="write the config to this file (atomically; --force to replace it); without it the config goes to standard output")
    ini.add_argument("--force", action="store_true", help="with --out: replace the file when it exists")
    return top


def main(
    argv: Sequence[str] | None = None, *, env: Mapping[str, str] | None = None, stdout: TextIO | None = None, stderr: TextIO | None = None,
    git_env: Mapping[str, str] | None = None, clock: Callable[[], float] | None = None, now: Callable[[], float] | None = None,
    retain: dict[str, Any] | None = None, outcome: dict[str, Any] | None = None, stdin: BinaryIO | None = None, transport: Any = None,
) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    out, err = stdout or sys.stdout, stderr or sys.stderr
    environment = os.environ if env is None else env
    if args and args[0] in ("--version", "-V"):
        print(f"reachmap {reachmap.__version__}", file=out)
        return EXIT_OK
    try:
        with contextlib.redirect_stdout(out):  # --help prints through argparse
            ns = _parser().parse_args(args)
    except _Usage as exc:
        print(f"reachmap: error: {exc}", file=err)
        return EXIT_USAGE
    except SystemExit as exc:  # --help
        return int(exc.code or 0) if not isinstance(exc.code, str) else EXIT_USAGE
    if ns.command not in ("analyse", "report", "decide", "serve", "mcp", "init", "audit", "witness", "sheet"):
        print("reachmap: error: give a command: analyse, report, decide, serve, mcp, init, audit, witness or sheet (see reachmap analyse --help)", file=err)
        return EXIT_USAGE
    try:
        if ns.command == "report":
            return _report_command(ns, out)
        if ns.command == "decide":
            return _decide_command(ns, out, err, now)
        if ns.command == "serve":
            return _serve_command(ns, environment, out, git_env)
        if ns.command == "mcp":
            return _mcp_command(ns, environment, out, err, git_env, stdin)
        if ns.command == "init":
            return _init_command(ns, environment, out, err, git_env)
        if ns.command in ("audit", "witness", "sheet"):
            return _evaluate_command(ns, environment, out, err, git_env)
        return _analyse(ns, environment, out, err, git_env, clock, now, retain, outcome, transport)
    except _Usage as exc:
        print(f"reachmap: error: {exc}", file=err)
        return EXIT_USAGE


def _formats(text: str) -> list[str]:
    names = [_ALIASES.get(n.strip().lower(), n.strip().lower()) for n in text.split(",") if n.strip()]
    if not names:
        raise _Usage("--format: name at least one format (json, md, html)")
    for name in names:
        if name not in FORMATS:
            raise _Usage(f"--format: {name} is not a report format; use json, md, html")
    return list(dict.fromkeys(names))


def _analyse(ns: argparse.Namespace, env: Mapping[str, str], out: TextIO, err: TextIO, git_env: Mapping[str, str] | None,
             clock: Callable[[], float] | None, now: Callable[[], float] | None, retain: dict[str, Any] | None = None,
             outcome: dict[str, Any] | None = None, transport: Any = None) -> int:
    formats = _formats(ns.format)
    try:
        spec = pipeline.ChangeSpec(pr=ns.pr, compare=ns.compare, range=ns.range)
    except ValueError as exc:
        raise _Usage(str(exc)) from None
    if ns.as_of is not None:
        try:
            pipeline.parse_as_of(ns.as_of)
        except ValueError as exc:
            raise _Usage(f"--as-of: {exc}") from None
    try:
        workspace = config.load(ns.config, env=env)
    except config.ConfigError as exc:
        raise _Usage(f"config error: {exc}") from None
    if ns.repo not in workspace.repos:
        raise _Usage(f"--repo: {ns.repo!r} is not in the config; known: {', '.join(sorted(workspace.repos))}")
    out_dir = Path(ns.out)
    key = store.make_key(workspace.name, ns.repo, store.change_identity(spec.as_dict(), workspace.repos[ns.repo].host))
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise _Usage(f"--out: cannot write the report to {out_dir.name or out_dir}: {exc.strerror or type(exc).__name__}") from None
    incoming = _read_exchange(ns.decisions, key) if ns.decisions else None
    try:  # a folder, a log or a store that cannot be used stops the command before the run, not after it
        store.check_folder(out_dir)
        log = revisions.read(out_dir, key)
        if incoming is not None:
            attach.require_known_revision(incoming, log)
            if (problem := store.read(out_dir, key).problem) is not None:
                raise store.StoreError(f"{store.decisions_path(out_dir, key).name}: line {problem.line}: {problem.why}; repair the file before importing (nothing was imported)")
    except (store.StoreError, exchange.ExchangeError) as exc:
        raise _Usage(f"decisions file {Path(ns.decisions).name}: {exc}" if isinstance(exc, exchange.ExchangeError) else f"the review files beside the reports: {exc}") from None
    stamp = _stamp(now)
    started = (now or time.time)()
    t0 = time.monotonic()
    options = pipeline.Options(as_of=ns.as_of, cache=gitlayer.default_cache(env), git_env=git_env, retain=retain, test_transport=transport, env=env,
                               **({"clock": clock} if clock else {}))
    try:
        analysis = pipeline.build(workspace, ns.repo, spec, options)
        report = _report(analysis, workspace, ns, spec, formats, started, time.monotonic() - t0, key)
    except Exception as exc:  # whatever went wrong, there is a report: a failed run says so (exit 3)
        analysis = pipeline.failed_analysis(workspace, ns.repo, spec, ns.as_of, f"{type(exc).__name__} in the command: {exc}")
        report = _report(analysis, workspace, ns, spec, formats, started, time.monotonic() - t0, key)
    done = attach.Imported()
    if analysis.status == "completed":
        try:
            with lock.held(out_dir):  # one short step: number the revision, carry the decisions, write the report, enter the log
                done = attach.finish(report, out_dir, key, incoming, stamp)
                path = _write(out_dir, analysis, ns.repo, spec, report, ns.as_of)
                attach.record(report, out_dir, key, path.name, stamp)
        except store.StoreError as exc:
            raise _Usage(f"the review files beside the reports: {exc}") from None
        except exchange.ExchangeError as exc:
            raise _Usage(f"decisions file {Path(ns.decisions).name}: {exc}") from None
    else:
        path = _write(out_dir, analysis, ns.repo, spec, report, ns.as_of)
    if outcome is not None:
        outcome.update(report=path.name, status=analysis.status)
    written, broken = _write_formats(path, formats)
    lines = summary.lines(report, str(path), written)
    if broken:
        print(f"reachmap: error: {broken}", file=err)
    if incoming is not None and analysis.status != "completed":
        print("reachmap: the decisions in the file were not imported because the run did not complete", file=err)
    if analysis.status == "completed":
        blocking = [line for line in lines if line.startswith("Blocking: ")]
        if not ns.quiet:
            print("\n".join(lines), file=out)
            if incoming is not None:
                print(f"Imported {_n(done.count, 'decision', 'decisions')} from {Path(ns.decisions).name} (channel {exchange.CHANNEL}); {done.replaced} replaced a decision in force", file=out)
        elif blocking:  # quiet is for what needs no attention: what blocks is always said
            print("\n".join(blocking), file=err)
        for warning in done.warnings:
            print(warning, file=err)
        return EXIT_FAILED if broken else EXIT_OK
    for line in lines:
        print(line, file=err)
    return EXIT_TIMED_OUT if analysis.status == "timed_out" else EXIT_FAILED


# ------------------------------------------------------------------------------ the report


def _write_formats(json_path: Path, formats: list[str]) -> tuple[dict[str, str], str]:
    """The Markdown and HTML of the report just written, rendered from the file as `reachmap report` would read it and written
    next to it with the same base name. Returns the files written and, if one could not be made, why (the others are still written)."""
    written: dict[str, str] = {}
    problems: list[str] = []
    wanted = [f for f in formats if f != "json"]
    if not wanted:
        return written, ""
    try:
        data = reports.load(json_path)
    except reports.ReportError as exc:
        return written, f"the {', '.join(wanted)} report could not be made from {json_path.name}: {exc}"
    for fmt in wanted:
        target = json_path.with_suffix("." + fmt)
        tmp = target.with_name(target.name + ".tmp")
        try:
            tmp.write_text(reports.render(data, fmt), encoding="utf-8", newline="\n")
            os.replace(tmp, target)
            written[fmt] = str(target)
        except (OSError, ValueError) as exc:
            problems.append(f"the {fmt} report could not be written: {exc.strerror if isinstance(exc, OSError) and exc.strerror else type(exc).__name__}")
    return written, "; ".join(problems)


def _write_stdout(out: TextIO, rendered: str) -> None:
    """The report as UTF-8 bytes on the real standard output (it holds source lines from any repository; a terminal or pipe set to ASCII
    must not make the command fail); a stream that has no bytes (a test's StringIO) gets the text."""
    raw = getattr(out, "buffer", None)
    if raw is None:
        out.write(rendered)
        return
    out.flush()
    raw.write(rendered.encode("utf-8", errors="replace"))
    raw.flush()


def _report_command(ns: argparse.Namespace, out: TextIO) -> int:
    """`reachmap report`: render a report file. Everything that can be wrong with the inputs is exit 2 and nothing is written."""
    fmt = _ALIASES.get(ns.format.strip().lower(), ns.format.strip().lower())
    if fmt not in reports.FORMATS:
        raise _Usage(f"--format: {ns.format} is not a format for a report; use md or html")
    if ns.view not in reports.VIEWS:
        raise _Usage(f"--view: {ns.view} is not a view; use full or author")
    try:
        data = reports.load(ns.report)
        attach.refresh(data, Path(ns.report).parent)
        notes.attach(data, Path(ns.report).parent)  # the model notes of the store beside the report (M14), for this rendering only
        rendered = reports.render(data, fmt, view=ns.view)
    except reports.UnsupportedSchema as exc:
        raise _Usage(f"{ns.report}: {exc}") from None
    except reports.ReportError as exc:
        raise _Usage(f"{ns.report}: not a report reachmap can read: {exc}") from None
    if ns.out is None:
        _write_stdout(out, rendered)
        return EXIT_OK
    target = Path(ns.out)
    try:
        target.write_text(rendered, encoding="utf-8", newline="\n")
    except OSError as exc:
        raise _Usage(f"--out: cannot write {target.name}: {exc.strerror or type(exc).__name__}") from None
    return EXIT_OK


def _stamp(now: Callable[[], float] | None) -> str:
    """The time a decision or a revision is recorded: the caller's clock, as an ISO time in UTC."""
    return datetime.fromtimestamp((now or time.time)(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _n(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _read_exchange(name: str, key: dict[str, str] | None) -> exchange.Exchange:
    """The decisions file (`--decisions`, `decide --import`), read and checked; for the analysis key given. Usage error otherwise."""
    path = Path(name)
    try:
        with open(path, "rb") as f:
            raw = f.read(exchange.MAX_BYTES + 1)
        if len(raw) > exchange.MAX_BYTES:  # the size is of the bytes, checked before they are decoded
            raise _Usage(f"decisions file {path.name}: is too big (at most {exchange.MAX_BYTES} bytes)")
        raw_text = raw.decode("utf-8")
    except OSError as exc:
        raise _Usage(f"decisions file {path.name}: cannot be read: {exc.strerror or type(exc).__name__}") from None
    except UnicodeDecodeError:
        raise _Usage(f"decisions file {path.name}: is not UTF-8 text") from None
    try:
        got = exchange.parse(raw_text)
        if key is not None:
            got.require_key(key)
    except exchange.ExchangeError as exc:
        raise _Usage(f"decisions file {path.name}: {exc}") from None
    return got


def _decide_command(ns: argparse.Namespace, out: TextIO, err: TextIO, now: Callable[[], float] | None) -> int:
    """`reachmap decide`: one decision about a target of the report, a list of the targets, or an import. The store is beside the report.
    Everything that can be wrong is exit 2 and nothing is written."""
    path = Path(ns.report)
    try:
        data = reports.load(path)
    except reports.UnsupportedSchema as exc:
        raise _Usage(f"{ns.report}: {exc}") from None
    except reports.ReportError as exc:
        raise _Usage(f"{ns.report}: not a report reachmap can read: {exc}") from None
    if data["status"] != "completed":
        raise _Usage(f"{ns.report}: the run did not complete, so there is no area or finding to decide on")
    try:
        key = attach.key_of(data)
        targets = carry.targets_of(data)
    except carry.ReviewError as exc:
        raise _Usage(f"{ns.report}: {exc}") from None
    base = path.parent
    single = ns.target is not None or ns.verdict is not None or bool(ns.reason) or ns.author is not None
    if sum([bool(ns.list), ns.import_file is not None, single]) != 1:
        raise _Usage("give exactly one of --list, --import FILE, or --target with --verdict and --author")
    if ns.list:
        attach.refresh(data, base)
        decisions = data["review"].get("decisions") or {}
        current = {x["target_id"]: x for x in decisions.get("current") or []}
        stale = {x["target_id"] for x in decisions.get("stale") or []}
        unread = bool(decisions.get("records"))  # the store could not be read: nothing is known, so no target is said to have none
        for t in targets.values():
            state = (
                "decisions could not be read" if unread else verdict_text(current[t.id]["verdict"]) if t.id in current
                else "decision needs review" if t.id in stale else "no decision")
            print(f"{t.id}  {t.kind}  {text.show(t.label, 80)}  {state}", file=out)
        return EXIT_OK
    try:
        if ns.import_file is not None:
            incoming = _read_exchange(ns.import_file, key)
            with lock.held(base):
                try:
                    attach.require_known_revision(incoming, revisions.read(base, key))
                except exchange.ExchangeError as exc:
                    raise _Usage(f"decisions file {Path(ns.import_file).name}: {exc}") from None
                items = incoming.to_stored()
                before = store.read(base, key)
                store.append(base, key, items)
            done = attach.import_effects(before, items)
            print(f"Imported {_n(done.count, 'decision', 'decisions')} from {Path(ns.import_file).name} (channel {exchange.CHANNEL}); {done.replaced} replaced a decision in force", file=out)
            for warning in done.warnings:
                print(warning, file=err)
            return EXIT_OK
        if ns.target is None or ns.verdict is None:
            raise _Usage("--target and --verdict go together")
        if ns.author is None or not ns.author.strip():
            raise _Usage("--author: give your name; it is never taken from the account of this machine")
        try:  # the one rule for the text of a decision, in every channel
            formats.check_author(ns.author)
        except formats.TextRefused as exc:
            raise _Usage(f"--author: {exc}") from None
        try:
            formats.check_reason(ns.reason)
        except formats.TextRefused as exc:
            raise _Usage(f"--reason: {exc}") from None
        target = targets.get(ns.target)
        if target is None:
            raise _Usage(f"--target: {text.show(ns.target, 40)} is not an area or finding of this report (reachmap decide {ns.report} --list shows them)")
        try:
            decision = Decision(target.id, ns.verdict, ns.reason, ns.author, "cli", target.fingerprint, int(data["review"]["revision"]))
        except ModelError as exc:
            raise _Usage(f"--verdict: {rule_words(exc)}") from None
        store.append(base, key, [store.Stored(decision, _stamp(now))])
    except store.StoreError as exc:
        raise _Usage(str(exc)) from None
    print(text.visible(f"{target.kind} {text.show(target.label, 80)} ({target.id}): {describe(decision)}"), file=out)
    return EXIT_OK


def write_unfinished(workspace: config.Workspace, config_path: str | Path, out_dir: str | Path, repo: str, spec: pipeline.ChangeSpec,
                     as_of: str | None, status: str, detail: str, started: float, elapsed: float) -> str:
    """For the runner, which ends a run it started: the report of a run that did not complete (`timed_out` when it killed the child),
    written as `analyse` writes one (the same shape, no result lists, no count, never entered in the revision log). Returns the file's name.
    `_Usage` when the folder cannot be written."""
    analysis = pipeline.failed_analysis(workspace, repo, spec, as_of, detail, status=status)
    key = store.make_key(workspace.name, repo, store.change_identity(spec.as_dict(), workspace.repos[repo].host))
    ns = argparse.Namespace(repo=repo, as_of=as_of, config=str(config_path))
    report = _report(analysis, workspace, ns, spec, ["json"], started, elapsed, key)
    return _write(Path(out_dir), analysis, repo, spec, report, as_of).name


def _serve_command(ns: argparse.Namespace, env: Mapping[str, str], out: TextIO, git_env: Mapping[str, str] | None) -> int:
    """`reachmap serve`: the runner on 127.0.0.1. Everything that can be wrong before it listens is exit 2."""
    from reachmap import runner

    try:
        port = int(ns.port)
    except ValueError:
        raise _Usage(f"--port: {text.show(ns.port, 20)} is not a port number") from None
    if not 0 <= port <= 65535:
        raise _Usage("--port: a port is a number from 0 to 65535")
    try:
        workspace = config.load(ns.config, env=env)
    except config.ConfigError as exc:
        raise _Usage(f"config error: {exc}") from None
    reports_dir = Path(ns.reports)
    try:
        reports_dir.mkdir(parents=True, exist_ok=True)
        store.check_folder(reports_dir)
    except OSError as exc:
        raise _Usage(f"--reports: cannot use {reports_dir.name or reports_dir}: {exc.strerror or type(exc).__name__}") from None
    except store.StoreError as exc:
        raise _Usage(f"--reports: {exc}") from None
    if not reports_dir.is_dir():
        raise _Usage(f"--reports: {reports_dir.name or reports_dir} is not a folder")
    try:
        app = runner.create(workspace, Path(ns.config).absolute(), reports_dir.absolute(), port=port, env=dict(env), git_env=git_env)
    except OSError as exc:
        raise _Usage(f"--port: cannot listen on 127.0.0.1:{port}: {exc.strerror or type(exc).__name__}") from None
    return runner.run(app, out)


def _init_command(ns: argparse.Namespace, env: Mapping[str, str], out: TextIO, err: TextIO, git_env: Mapping[str, str] | None) -> int:
    """`reachmap init`: read the repositories and write the starter config. Exit 0 when every repository and file was read, 3 when any could not
    be (the file is written for the rest), 2 for a usage problem (nothing is read or written)."""
    from reachmap import init as initializer

    to_stdout = ns.out in (None, "-")
    target = Path(ns.out) if not to_stdout else None
    if target is not None:
        if target.is_dir():
            raise _Usage(f"--out: {target.name or target} is a folder; name the file")
        if target.exists() and not ns.force:
            raise _Usage(f"--out: {target.name} exists; use --force to replace it")
    git = gitlayer.Git(gitlayer.default_cache(env), env=git_env)
    try:
        result = initializer.generate(ns.specs, git, Path(os.path.abspath("." if target is None else target.parent)))
    except ValueError as exc:
        raise _Usage(str(exc)) from None
    for spec, reason in result.failures:
        print(f"reachmap: could not read {text.one_line(text.show(spec, 200))}: {text.one_line(text.show(reason, 400))}", file=err)
    for repo, path, why in result.unread[:10]:
        print(f"reachmap: could not read {text.one_line(text.show(repo + ':' + path, 200))}: {text.one_line(text.show(why, 200))}", file=err)
    if len(result.unread) > 10:
        print(f"reachmap: and {len(result.unread) - 10} more files could not be read (all are listed in the config)", file=err)
    code = EXIT_FAILED if result.failures or result.unread else EXIT_OK
    if not result.names:
        print("reachmap: no repository could be read, so no config was written", file=err)
        return code
    guesses = sum(1 for line in result.text.splitlines() if line.startswith("# guess:"))
    unread = f"; {len(result.unread)} file{'s' if len(result.unread) != 1 else ''} could not be read" if result.unread else ""
    summary = (f"{len(result.names)} repositor{'y' if len(result.names) == 1 else 'ies'}: {guesses} guesses to confirm "
               f"(each has a `# guess:` comment above it){unread}; review the file before running reachmap analyse")
    if target is None:
        out.write(result.text)
        print(f"reachmap: wrote the config for {summary}", file=err)
        return code
    tmp = target.with_name(f".{target.name}.tmp-{os.getpid()}")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(result.text, encoding="utf-8")
        os.replace(tmp, target)
    except OSError as exc:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise _Usage(f"--out: cannot write {target.name or target}: {exc.strerror or type(exc).__name__}") from None
    print(f"wrote {target} for {summary}", file=out)
    return code


class _Bytes:
    """A text stream as the bytes writer the MCP server wants (what it writes is ASCII: every other character is a JSON escape)."""

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def write(self, data: bytes) -> int:
        self._stream.write(data.decode("ascii"))
        return len(data)

    def flush(self) -> None:
        self._stream.flush()


def _mcp_command(ns: argparse.Namespace, env: Mapping[str, str], out: TextIO, err: TextIO, git_env: Mapping[str, str] | None, stdin: BinaryIO | None) -> int:
    """`reachmap mcp`: the MCP server on standard input and output. Everything that can be wrong before it starts is exit 2 (a message that
    names the option, never a path or a configured value). Only the server's answers go to standard output: standard output is handed to
    the server and everything else that is printed while it runs goes to standard error."""
    from reachmap.mcp import server, tools

    reports_dir = Path(ns.reports)
    if not reports_dir.is_dir():
        raise _Usage("--reports: the reports directory must be an existing folder")
    try:
        store.check_folder(reports_dir)
    except store.StoreError as exc:
        raise _Usage(f"--reports: {exc}") from None
    workspace = git = None
    if ns.config:
        try:
            workspace = config.load(ns.config, env=env)
        except config.ConfigError as exc:
            raise _Usage(f"config error: {exc}") from None
        git = gitlayer.Git(gitlayer.default_cache(env), env=git_env)
    toolbox = tools.Toolbox(reports_dir.absolute(), workspace=workspace, git=git)
    reader = stdin if stdin is not None else sys.stdin.buffer
    writer = out.buffer if hasattr(out, "buffer") else _Bytes(out)
    try:
        with contextlib.redirect_stdout(err):
            server.Server(toolbox, log=err.write).serve(reader, writer)
    except KeyboardInterrupt:
        pass
    return EXIT_OK


def _evaluate_command(ns: argparse.Namespace, env: Mapping[str, str], out: TextIO, err: TextIO, git_env: Mapping[str, str] | None) -> int:
    """The evaluation commands (M18, `reachmap.evaluate`): the work is there; a file or option that cannot be used is exit 2 here."""
    from reachmap import evaluate
    from reachmap.evaluate import audit, sheets, witness

    try:
        if ns.command == "audit":
            return audit.command(ns, env, out, git_env)
        if ns.command == "witness":
            return witness.command(ns, env, out, git_env)
        if ns.command == "sheet":
            return sheets.command(ns, env, out)
    except evaluate.UsageError as exc:
        raise _Usage(str(exc)) from None
    raise _Usage(f"{ns.command}: not an evaluation command")


def _report(a: Analysis, workspace: config.Workspace, ns: argparse.Namespace, spec: pipeline.ChangeSpec, formats: list[str],
            started: float, elapsed: float, key: dict[str, str]) -> dict[str, Any]:
    counts: dict[str, int] | None = None
    if a.status == "completed":
        counts = {
            "areas": len([x for x in a.areas if not x.by_name]), "must_test": sum(x.priority == "must_test" for x in a.areas),
            "sample": sum(x.priority == "sample" for x in a.areas), "candidates": sum(x.by_name for x in a.areas),
            "findings": a.finding_count, "exclusions": sum(f.is_exclusion for f in a.findings),
            "records": len(a.incompleteness), "blocking_records": len(a.blocking()),
            "blocking_suggestion_records": len(a.blocking_suggestions()),
        }
    data = {
        "schema": SCHEMA,
        "tool": {"name": "reachmap", "version": reachmap.__version__},
        "run": {"started_at": datetime.fromtimestamp(started, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "elapsed_s": round(elapsed, 3)},
        "inputs": {
            "repo": ns.repo, "change": spec.as_dict(), "as_of": ns.as_of, "formats": formats,
            "config": {"workspace": workspace.name, "path_sha256": hashlib.sha256(str(Path(ns.config).resolve()).encode("utf-8")).hexdigest()},
        },
        "counts": counts,
        **a.to_dict(),
    }
    if a.status == "completed" and workspace.test_sources:  # what became of each source, so that "none suggested" is only said of a source that was read
        reads = testsources.read_states(workspace, a)
        data["inputs"]["test_sources"] = [r.to_dict() for r in reads]
        data["inputs"]["test_source_read"] = testsources.all_read(reads)
    attach.seed(data, key)
    return data


def _slug(text: str, limit: int = 60) -> str:
    text = re.sub(r"\.{2,}", "-", text)
    text = re.sub(r"[^A-Za-z0-9._]+", "-", text).strip("-.")
    return (text[:limit].strip("-.")) or "x"


def report_name(a: Analysis, repo: str, spec: pipeline.ChangeSpec, as_of: str | None = None, revision: int | None = None, run: Mapping[str, Any] | None = None) -> str:
    """`<repo>-<kind>-<change>-<hash>-<12 hex of the head>-r<revision>.json` for a completed run, so a rerun on the same commit is a file of its
    own and the file a revision log entry names never changes; `...-<12 hex>-failed-<8 hex of the run's time>.json` for a run that did not
    complete (it never replaces a completed report). The slugs keep names readable and drop what tells `a...b` from `a-b`; the hash of the
    repository, the whole change and the time keeps two different changes from sharing a file."""
    short = a.ref[:12] if a.ref and a.change.get("resolved") else "unresolved"
    key = hashlib.sha256(json.dumps([repo, spec.as_dict(), as_of], sort_keys=True).encode("utf-8")).hexdigest()[:8]
    if revision is not None:
        tail = f"r{revision}"
    else:
        moment = json.dumps([(run or {}).get("started_at"), (run or {}).get("elapsed_s")])
        tail = "failed-" + hashlib.sha256(moment.encode("utf-8")).hexdigest()[:8]
    return f"{_slug(repo)}-{spec.kind}-{_slug(str(spec.value))}-{key}-{short}-{tail}.json"


def _write(out_dir: Path, a: Analysis, repo: str, spec: pipeline.ChangeSpec, report: Mapping[str, Any], as_of: str | None = None) -> Path:
    """Write the report whole (a temporary file renamed into place). A directory that cannot be made or written is a usage error."""
    path = out_dir / report_name(a, repo, spec, as_of, (report.get("review") or {}).get("revision"), report.get("run"))
    tmp = path.with_name(path.name + ".tmp")
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        raise _Usage(f"--out: cannot write the report to {out_dir.name or out_dir}: {exc.strerror or type(exc).__name__}") from None
    return path


# ------------------------------------------------------------------------------ the summary


def summary_lines(a: Analysis, report_path: str | None, complete: bool | None) -> list[str]:
    """The summary of an analysis: the words of `report.summary`, made from the report form of the analysis (`complete` is the
    propagation's: what a report keeps as `propagations[0].complete`)."""
    data = a.to_dict()
    if a.status == "completed":
        data["propagations"] = [] if complete is None else [{"complete": complete}]
    return summary.lines(data, report_path)


if __name__ == "__main__":
    sys.exit(main())
