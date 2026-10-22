"""The TOML text of a starter `reachmap.toml`, written from a template (context 13.2: `tomllib` reads, it does not write).

Escaping rules, all pinned by tests that parse the output back:

- a string value is a TOML basic string: backslash and double quote escaped, every control, format (bidirectional marks and
  joiners), separator, surrogate, private-use and unassigned character written as `\\uXXXX` (never raw), the rest as it is;
- a regular expression is a single-quoted literal string, unless it holds a single quote or a character a literal string cannot
  hold (then an escaped basic string);
- `${` in a value is written `$${`, because the loader expands `${VAR}`: the value reads back as it was written;
- text in a comment (a path, a package name, anything read from a repository) is one line of printable characters: a newline or
  a control character in a name can never end the comment and start a table.

`build` returns the text and the repositories it had to leave out because the loader refused their tables (the output always loads:
each repository's tables are checked with `config.loads` before they are written, and a table the loader refuses is written as a
comment with the loader's message).
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field, fields
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

from reachmap import config, redact
from reachmap.init.edges import guess_edges
from reachmap.init.guess import EdgeGuess, Guess, Note, RepoGuess

__all__ = ["Rendered", "build", "comment_text", "render", "toml_regex", "toml_string", "toml_value"]

_UNPRINTABLE = {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}
_SECTION_ORDER = ("service_marker", "library_roots", "aliases", "entry_points", "http_server", "http_client", "contract", "tests")
_TABLE_KEYS = ("host", "remote", "language", "roles", "branches")
MAX_COMMENT = 480
MAX_UNREAD_LINES = 25


# ------------------------------------------------------------------------------------------------ escaping


def _escape_char(c: str) -> str:
    o = ord(c)
    if c == '"':
        return '\\"'
    if c == "\\":
        return "\\\\"
    if unicodedata.category(c) == "Cs":
        return "\\uFFFD"  # a lone surrogate stands for no character; TOML refuses it
    if unicodedata.category(c) in _UNPRINTABLE or o == 0x7F:
        return f"\\u{o:04X}" if o <= 0xFFFF else f"\\U{o:08X}"
    return c


def toml_string(text: str) -> str:
    """A TOML basic string for `text`, with `${` written `$${` (the loader's escape for a literal `${`)."""
    return '"' + "".join(_escape_char(c) for c in text.replace("${", "$${")) + '"'


def toml_regex(text: str) -> str:
    """A single-quoted literal string for a regular expression, a basic string when a literal cannot hold it."""
    text = text.replace("${", "$${")
    if "'" in text or any(unicodedata.category(c) in _UNPRINTABLE or ord(c) == 0x7F for c in text):
        return '"' + "".join(_escape_char(c) for c in text) + '"'
    return "'" + text + "'"


def toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return toml_string(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{_bare_key(k)} = {toml_value(v)}" for k, v in value.items()) + " }" if value else "{}"
    raise TypeError(f"cannot write {type(value).__name__} as a TOML value")


def _bare_key(key: str) -> str:
    return key if re.match(r"^[A-Za-z0-9_-]+$", key) else toml_string(key)


def comment_text(text: object, limit: int = MAX_COMMENT) -> str:
    """One line of printable characters: anything else (a newline, a tab, a bidirectional mark) becomes a space or `?`."""
    out = []
    for c in str(text):
        if c in "\n\r\t\v\f":
            out.append(" ")
        elif unicodedata.category(c) in _UNPRINTABLE or ord(c) == 0x7F:
            out.append("?")
        else:
            out.append(c)
    line = re.sub(r" {2,}", " ", "".join(out)).strip()
    return line if len(line) <= limit else line[: limit - 3].rstrip() + "..."


def note_line(note: Note) -> str:
    evidence = ", ".join(dict.fromkeys(comment_text(e, 200) for e in note.evidence))
    where = f", evidence {evidence}" if evidence else ""
    return f"# guess: {comment_text(note.why)}{where}, confidence {note.confidence}; confirm"


def _comments(notes: Sequence[Note]) -> list[str]:
    return [note_line(n) for n in notes]


# ------------------------------------------------------------------------------------------------ the file


@dataclass(slots=True)
class Rendered:
    text: str
    failures: list[tuple[str, str]] = field(default_factory=list)  # (spec, reason) for what was left out
    names: list[str] = field(default_factory=list)  # the repositories written
    unread: list[tuple[str, str, str]] = field(default_factory=list)  # (repository, path, why) of every file that could not be read


def render(guesses: Sequence[RepoGuess], failures: Sequence[tuple[str, str]] = (), out_dir: str | os.PathLike | None = None) -> str:
    return build(guesses, failures, out_dir).text


def build(guesses: Sequence[RepoGuess], failures: Sequence[tuple[str, str]] = (), out_dir: str | os.PathLike | None = None) -> Rendered:
    base = Path(os.path.abspath(out_dir if out_dir is not None else "."))
    written: list[tuple[RepoGuess, list[str]]] = []
    left_out = list(failures)
    for g in guesses:
        lines, problem = _repo_block(g, base)
        if problem is not None:
            left_out.append((g.spec or g.name, problem))
        else:
            written.append((g, lines))
    kept = [g for g, _ in written]
    edges, edge_notes = guess_edges(kept)
    out: list[str] = _header()
    if left_out:
        out += _could_not_read(left_out)
    if not written:
        return Rendered("\n".join(out) + "\n", left_out, [])
    unread = [(g.name, path, why) for g, _ in written for path, why in g.unread]
    out += _top(kept)
    out += _limits()
    out += ["", "# " + "-" * 64 + " repositories"]
    for g, lines in written:
        out += [""] + lines
    out += ["", "# " + "-" * 64 + " edges"]
    for note in edge_notes:
        out.append("# " + comment_text(note))
    if not edges:
        seen = f" in the files that could be read ({len(unread)} could not be read)" if unread else ""
        out += ["", f"# No edges were found between these repositories{seen}. Add an [[edges]] table where one repository calls or depends on another."]
    for e in edges:
        out += [""] + _edge_block(e)
    out += _test_sources(kept)
    text = "\n".join(out) + "\n"
    names = [g.name for g in kept]
    # the whole file must load: a failure here is a bug of a detector or the template, never a file for the user to repair
    config.loads(text, fmt="toml", env={}, source="reachmap init", base_dir=base)
    return Rendered(text, left_out, names, unread)


def _header() -> list[str]:
    return [
        "# reachmap.toml, written by `reachmap init`.",
        "#",
        "# Read it before you rely on it. Every value that was guessed has a comment above it that starts with \"guess:\"",
        "# and ends with \"confirm\": why, where in the repository (path:line) and how sure the guess is (high, medium, low).",
        "# A value with no such comment was read directly (the remote you gave, the default branch the remote names).",
        "# No secret is written here: a token is named by an environment variable (token_env), never given.",
    ]


def _could_not_read(failures: Sequence[tuple[str, str]]) -> list[str]:
    out = ["", "# Could not read (these repositories are not in this file; fix the cause and run init again):"]
    for spec, reason in failures:
        out.append(f"#   {comment_text(redact.scrub(spec), 200)}: {comment_text(redact.scrub(reason), 300)}")
    return out


def _derive_workspace(guesses: Sequence[RepoGuess]) -> tuple[str, str]:
    owners = {g.name.split("/")[0] for g in guesses if "/" in g.name}
    if len(owners) == 1 and all("/" in g.name for g in guesses):
        return owners.pop(), "the owner every repository name starts with"
    if owners:
        return sorted(owners)[0], "the owner of the first repository name that has one"
    return "workspace", "no repository name has an owner"


def _top(guesses: Sequence[RepoGuess]) -> list[str]:
    name, why = _derive_workspace(guesses)
    return [
        "",
        f"# guess: {why}, evidence {comment_text(guesses[0].name)}, confidence low; confirm",
        f"workspace = {toml_string(name)}",
        "",
        "# Optional: a pattern with one group that reads a ticket key out of a title, for example:",
        "# ticket_pattern = '\\[([A-Z][A-Z0-9]+-\\d+)\\]'",
    ]


def _limits() -> list[str]:
    defaults = {f.name: f.default for f in fields(config.Limits) if f.name != "module_cap"}
    return [
        "",
        "# The defaults, not guesses. hops: edges followed past the changed repository. hub_importers: a file imported by more files",
        "# than this is not walked through. distinct_max: a name defined more often than this is not used to link code. timeout_s:",
        "# the time limit of one run.",
        "[limits]",
        *[f"{k} = {v}" for k, v in defaults.items()],
    ]


def _remote_value(g: RepoGuess, base: Path) -> tuple[str | None, list[str]]:
    notes: list[str] = []
    if g.remote is None:
        return None, notes
    if not g.remote_local:
        return g.remote, notes
    target = Path(g.remote)
    try:
        rel = os.path.relpath(target, base)
    except ValueError:
        rel = None
    if rel is None or rel.split(os.sep).count("..") > 4:
        notes.append("# the remote is a path on this machine: a committed config should not hold one; replace it with the clone URL; confirm")
        return str(target), notes
    posix = PurePosixPath(*Path(rel).parts).as_posix()
    if not posix.startswith("."):
        posix = "./" + posix
    notes.append("# a path relative to this file")
    return posix, notes


def _repo_lines(g: RepoGuess, base: Path, omit: frozenset[str] = frozenset(), refused: dict[str, str] | None = None) -> list[str]:
    lines = [f"[repos.{toml_string(g.name)}]", f"host = {toml_string(g.host)}"]
    remote, remote_notes = _remote_value(g, base)
    if remote is not None:
        lines += [f"# {comment_text(n)}" for n in g.remote_notes]
        lines += remote_notes
        lines.append(f"remote = {toml_string(remote)}")
    lines += _comments(g.language.notes)
    lines.append(f"language = {toml_string(g.language.value)}")
    lines += _comments(g.roles.notes)
    lines.append(f"roles = {toml_value(g.roles.value)}")
    lines += _comments(g.branches.notes)
    lines.append(f"branches = {toml_value(g.branches.value)}")
    for key in _SECTION_ORDER:
        guess = g.sections.get(key)
        if guess is None:
            continue
        if key in omit:
            lines.append(f"# not written: {key} was guessed, but the loader refused it: {comment_text((refused or {}).get(key, 'no reason given'))}")
            continue
        lines += _comments(guess.notes)
        lines.append(f"{key} = {_section_value(key, guess.value)}")
    for note in g.notes:
        lines.append(f"# {comment_text(note)}")
    for path, why in g.unread[:MAX_UNREAD_LINES]:
        lines.append(f"# could not read {comment_text(path, 200)}: {comment_text(why, 200)}; what depends on it may be missing above; confirm")
    if len(g.unread) > MAX_UNREAD_LINES:
        lines.append(f"# and {len(g.unread) - MAX_UNREAD_LINES} more files could not be read; what depends on them may be missing above; confirm")
    return lines


def _section_value(key: str, value: Any) -> str:
    if key == "tests" and isinstance(value, dict):
        return "{ " + ", ".join(
            f"{k} = {toml_value(v)}" for k, v in value.items()) + " }"
    return toml_value(value)


def _repo_block(g: RepoGuess, base: Path) -> tuple[list[str], str | None]:
    """The lines of one repository, after the loader has checked its tables. A table the loader refuses is left out (and says why);
    a problem with a required key leaves the whole repository out, with the loader's message."""
    omit: set[str] = set()
    reasons: dict[str, str] = {}
    for _ in range(len(_SECTION_ORDER) + 1):
        lines = _repo_lines(g, base, frozenset(omit), reasons)
        try:
            config.loads("workspace = \"check\"\n\n" + "\n".join(lines) + "\n", fmt="toml", env={}, source="reachmap init", base_dir=base)
            return lines, None
        except config.ConfigError as exc:
            bad = {_key_of(p.path, g.name): p.message for p in exc.problems}
            sections = {k: m for k, m in bad.items() if k in _SECTION_ORDER and k not in omit}
            if not sections or any(k not in _SECTION_ORDER for k in bad if k not in omit):
                first = exc.problems[0]
                return [], f"the config loader refused it ({comment_text(first.path)}: {comment_text(first.message)})"
            omit |= set(sections)
            reasons.update(sections)
    return [], "the config loader kept refusing its tables"


def _key_of(path: str, repo: str) -> str:
    m = re.match(r'^repos\."(?:[^"\\]|\\.)*"\.([A-Za-z_]+)', path)
    return m.group(1) if m else ""


def _edge_block(e: EdgeGuess) -> list[str]:
    lines = _comments(e.notes)
    lines.append("[[edges]]")
    lines.append(f"kind = {toml_string(e.kind)}")
    if e.spelling == "producer_consumer":
        lines += [f"producer = {toml_string(e.producer)}", f"consumer = {toml_string(e.consumer)}"]
    else:
        lines += [f"from = {toml_string(e.producer)}", f"to = {toml_string(e.consumer)}"]
    for key, value in e.options.items():
        lines.append(f"{key} = {toml_regex(value) if key == 'pin_pattern' else toml_string(value)}")
    return lines


def _test_sources(guesses: Sequence[RepoGuess]) -> list[str]:
    out: list[str] = []
    screens = [g for g in guesses if g.kind_of("entry_points") in ("react-router", "nextjs")]
    for g in guesses:
        if g.facts.e2e is None:
            continue
        framework, where = g.facts.e2e
        target = g if g in screens else (screens[0] if screens else None)
        if target is None:
            out += ["", f"# {g.name} has {framework} specs that visit URLs ({comment_text(where)}), but no repository here has screens to match them to; add one with a",
                    f"# react-router or nextjs entry_points table, then a [[test_sources]] table of kind {framework!r} with repo = {g.name!r}."]
            continue
        out += ["", *_comments([Note(f"{framework} specs that visit URLs are in {g.name}; the screens they are matched to are in {target.name}", (where,), "medium")]),
                "[[test_sources]]", f"kind = {toml_string(framework)}", f"repo = {toml_string(g.name)}"]
        if target is not g:
            out.append(f"screens_repo = {toml_string(target.name)}")
    out += [
        "",
        "# Optional: suggested test cases from a test-management system. They are suggestions, never counted as coverage, and nothing is",
        "# written back. A token is never written in this file: name the environment variable that holds it.",
        "# [[test_sources]]",
        "# kind = \"qase\"",
        "# project = \"ACME\"                       # the Qase project code",
        "# # the NAME of the environment variable that holds the API token, never the token itself:",
        "# token_env = \"QASE_API_TOKEN\"",
        "#",
        "# [[test_sources]]",
        "# kind = \"testrail\"",
        "# base_url = \"https://example.testrail.io\"",
        "# project_id = 1",
        "# # the NAME of the environment variable that holds the API key:",
        "# token_env = \"TESTRAIL_API_KEY\"",
        "",
        "# Optional: requirements from an offline Jira export. An unset variable means \"not configured\".",
        "# [[context]]",
        "# kind = \"jira-snapshot\"",
        "# path = \"${JIRA_SNAPSHOT}\"",
    ]
    return out
