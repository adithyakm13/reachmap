"""The branch matrix: for every configured branch of the changed repository, whether the change is there.

This is how a fix that reached the release branches and never `main` shows. For each branch that matches the repository's
`branches` patterns (heads from the git layer, slashes in names kept), every changed file is read at the branch tip and
compared with what the change added and removed:

- `present`: every added line is in the file at the tip (see below for what "is in" means);
- `partial`: some of them are;
- `pre_change`: none of them is, and the removed lines are all still there;
- `diverged`: neither (the file was changed another way);
- `file_absent`: git positively says the path is not in the tip's tree (for a file the change deletes, the deletion
  landed or the file never existed there; the row cannot tell which);
- `unreadable`: any failure to read the file. The git layer records it (a blocking `read_unreadable`) and the row says
  unreadable; it blocks that branch only: the other branches are still compared and the areas are still selected.

Lines are compared by exact text after stripping trailing whitespace, per file, counting multiplicity against the
pre-change file: a line the change adds counts as found at a tip when the tip holds more copies of that text than the file
had before the change (less the copies the change removed): `found = clamp(tip_count - (pre_count - removed_count), 0,
added_count)`. A line that merely occurs elsewhere in the file (`}`, `return None`, a null guard in another function) is
therefore not found at a tip that has not got the change. An added text that the change also removes cannot be told from what
was there and is left out of the comparison; a file whose every added text is like that, and every file of a change with no
pre-change version, is `not_compared` (and said in a note). Blank lines are not compared (a blank line cannot be cited and
matches anything). The evidence of `present` and `partial` is the found lines, verbatim, with their line numbers at the tip
sha: of the tip's copies of a text, the ones nearest to the line numbers the change put its copies at (a heuristic, since
copies of a line are interchangeable: the claim is the count, the cited lines are where the extra copies most likely are); of
`pre_change` and `diverged`, the removed lines found there (or, with none, the first line of the file as the tip has
it); `file_absent` has none. At most `MAX_EVIDENCE_PER_FILE` lines are cited per file and the row's detail says when
that cut was made.

A branch with several changed files is one row: all files the same state is that state, a mix is `partial` when an added
line was found in some file and `diverged` when none was (the files are old, absent or changed another way), and the
detail lists each file. An unreadable file is its own row (so a branch whose files cannot all be read has one row per
unreadable file, besides the row over the readable ones). A file that cannot be compared (the change only removes
lines in it and they are gone, so there is nothing added to find and `present` would be a claim without a line to cite;
a name that is not valid text; no text lines at all) is left out of the row and said in `notes`, never dropped quietly.

The change's own head branch is left out. `as_of` reads each tip as of a time (a branch with no commit then has no row,
and a note says so).

A file the change adds that is absent at a tip is that file's `pre_change` state (the change is not there); the row of a branch
where that is all there is reads `file_absent`, since there is nothing to cite. `file_absent` is for a modified, renamed or deleted
file whose path is not in the tip's tree.

Known limits: another edit that adds or removes copies of the same text at a tip shifts the count (a copy added elsewhere
looks like the change; a copy removed elsewhere hides it); a renamed file is read at its new path and, when that is absent,
at its old path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from reachmap import redact
from reachmap.diffparse import FileChange
from reachmap.model import BranchState, Evidence

__all__ = ["Classification", "FileState", "MAX_EVIDENCE_PER_FILE", "Matrix", "classify_text", "matrix"]

MAX_EVIDENCE_PER_FILE = 50
MAX_FILES_IN_DETAIL = 20
FILE_STATES = ("present", "partial", "pre_change", "diverged", "file_absent", "unreadable", "not_compared")


@dataclass(frozen=True, slots=True)
class Classification:
    """The comparison of one file's lines at a tip. `state` is None when there is nothing to compare (no added line,
    and no removed line either or all of them gone). `found` are (line number at the tip, the line verbatim) of the lines
    cited, in line order; `cut` says there were more."""

    state: str | None
    added: int
    added_found: int
    removed: int
    removed_found: int
    found: tuple[tuple[int, str], ...]
    cut: bool = False
    reason: str = ""  # why there is no state


def _comparable(lines: Sequence[str]) -> list[str]:
    return [line.rstrip() for line in lines if line.strip()]


def _found(wanted: list[str], index: dict[str, list[tuple[int, str]]]) -> list[tuple[int, str]]:
    """The lines at the tip that match `wanted`, counting multiplicity: a text wanted n times is found at most n times."""
    out: list[tuple[int, str]] = []
    counts: dict[str, int] = {}
    for text in wanted:
        counts[text] = counts.get(text, 0) + 1
    for text, n in counts.items():
        out.extend(index.get(text, [])[:n])
    return sorted(out)


def _nearest(copies: list[tuple[int, str]], wanted: int, hints: list[int]) -> list[tuple[int, str]]:
    """`wanted` of the tip's copies of a text: the ones nearest to the line numbers the change put its copies at (the
    last ones when it is not known). Copies of a line are interchangeable, so which are cited is a heuristic."""
    if wanted >= len(copies):
        return list(copies)
    if not hints or len(copies) * len(hints) > 250_000:
        return list(copies[-wanted:])
    pairs = sorted((abs(c[0] - h), c[0], i, h) for c in copies for i, h in enumerate(hints))
    used_copy: set[int] = set()
    used_hint: set[int] = set()
    chosen: list[int] = []
    for _d, line, i, _h in pairs:
        if line not in used_copy and i not in used_hint:
            used_copy.add(line)
            used_hint.add(i)
            chosen.append(line)
            if len(chosen) == wanted:
                break
    return [c for c in copies if c[0] in used_copy]


def classify_text(
    added: Sequence[str], removed: Sequence[str], tip_lines: Sequence[str], pre_lines: Sequence[str], added_at: Sequence[int] | None = None,
) -> Classification:
    """Compare what a change added and removed in a file with the file at a branch tip. `pre_lines` is the file before the
    change (empty for a file the change adds); `added_at` the line numbers the change put each added line at (the same
    order as `added`), used only to choose which copies at the tip are cited."""
    r = _comparable(removed)
    a_all = [(i, line.rstrip()) for i, line in enumerate(added) if line.strip()]
    r_count: dict[str, int] = {}
    for text in r:
        r_count[text] = r_count.get(text, 0) + 1
    usable = [(i, text) for i, text in a_all if text not in r_count]  # a text that is also removed cannot be told from what was there
    a_count: dict[str, int] = {}
    for _i, text in usable:
        a_count[text] = a_count.get(text, 0) + 1
    pre_count: dict[str, int] = {}
    for line in pre_lines:
        if line.strip():
            pre_count[line.rstrip()] = pre_count.get(line.rstrip(), 0) + 1
    index: dict[str, list[tuple[int, str]]] = {}
    for number, line in enumerate(tip_lines, 1):
        if line.strip():
            index.setdefault(line.rstrip(), []).append((number, line.rstrip("\r")))
    found_removed = _found(r, index)
    found_added: list[tuple[int, str]] = []
    for text, n in a_count.items():
        copies = index.get(text, [])
        extra = min(max(len(copies) - pre_count.get(text, 0), 0), n)  # more copies than the file had before the change
        if extra:
            hints = sorted(added_at[i] for i, t in usable if t == text) if added_at is not None else []
            found_added.extend(_nearest(copies, extra, hints))
    found_added.sort()
    state: str | None
    reason = ""
    if usable:
        total = len(usable)
        if len(found_added) == total:
            state = "present"
        elif found_added:
            state = "partial"
        else:
            state = "pre_change" if len(found_removed) == len(r) else "diverged"
    elif a_all:
        state, reason = None, "every added line also occurs among the removed lines, so none can be told from what was there"
    elif r:
        state = "pre_change" if len(found_removed) == len(r) else ("diverged" if found_removed else None)
    else:
        state = None
    if state is None and not reason:
        reason = "the change only removes lines here and they are gone" if r else "the change adds and removes no text line"
    shown = found_added if state in ("present", "partial") else found_removed
    return Classification(state, len(usable), len(found_added), len(r), len(found_removed), tuple(shown[:MAX_EVIDENCE_PER_FILE]),
                          len(shown) > MAX_EVIDENCE_PER_FILE, reason)


@dataclass(frozen=True, slots=True)
class FileState:
    """One changed file on one branch: the state (`not_compared` besides the model's), what was read and compared."""

    branch: str
    path: str  # the path of the change (the new one for a rename)
    state: str
    read_path: str  # the path that was read at the tip (the old one for a rename not yet applied)
    commit: str
    added: int = 0
    added_found: int = 0
    removed: int = 0
    removed_found: int = 0
    evidence: tuple[Evidence, ...] = ()
    detail: str = ""
    cut: bool = False

    def __post_init__(self) -> None:
        if self.state not in FILE_STATES:
            raise ValueError(f"FileState.state must be one of {FILE_STATES}, got {self.state!r}")


@dataclass(frozen=True, slots=True)
class Matrix:
    """The rows of the branch matrix (`BranchState`), the state of every file on every branch they are made from, the
    notes for what was not compared, and whether every branch could be read (`complete` is False when a row is unreadable)."""

    rows: tuple[BranchState, ...]
    files: tuple[FileState, ...]
    notes: tuple[str, ...]
    primary: str
    complete: bool = True

    def kind_of(self, branch: str) -> str | None:
        """What the branch's readable files say together (`aggregate`): the state the findings go by. It is the row's state
        except where the row has nothing to cite (a branch missing only files the change adds reads `file_absent` in its
        row and `pre_change` here). None when no file could be compared."""
        return aggregate([f for f in self.files if f.branch == branch])[0]


def aggregate(files: Sequence[FileState]) -> tuple[str | None, int]:
    """The state of a branch over its changed files and the number of added lines found: all the same is that state; a mix
    is `partial` when an added line was found in some file and `diverged` when none was (the files are old, absent or changed
    another way). Files that were not compared or could not be read are not part of it. None when nothing is left."""
    classified = [fs for fs in files if fs.state not in ("not_compared", "unreadable")]
    if not classified:
        return None, 0
    kinds = {fs.state for fs in classified}
    found = sum(fs.added_found for fs in classified)
    state = next(iter(kinds)) if len(kinds) == 1 else ("partial" if found else "diverged")
    return state, (found if state in ("present", "partial") else 0)


def _clean(text: str, limit: int = 2000) -> str:
    return redact.for_record(text, limit)


def matrix(
    git: Any, remote: str, repo: Any, change: Any, *, head_ref: str | None = None, as_of: str | None = None,
) -> Matrix:
    """The branch matrix of `change` (a resolved `ChangeSet`) over the branches `repo` configures, read through `git`
    at `remote`. `head_ref` is the change's own head branch name (left out); `as_of` reads the tips as of a time."""
    patterns = list(repo.branches)
    primary = repo.primary_branch
    notes: list[str] = []
    res = git.heads(remote, patterns)
    if res.state == "unreadable":  # the git layer recorded it (blocking): one row for each pattern says so
        rows = tuple(BranchState(p, "", "unreadable", 0, (), _clean(f"the branches matching {p} could not be listed: {res.detail}")) for p in patterns)
        return Matrix(rows, (), (), primary, complete=False)
    if res.state == "absent":
        return Matrix((), (), (_clean(f"no branch of the repository matches {', '.join(patterns)}: there is no matrix"),), primary)
    names = sorted(res.heads, key=lambda b: (b != primary, b))
    files = sorted(change.files, key=lambda f: f.path)
    for fc in files:
        if not fc.path_is_text:
            notes.append(_clean(f"{redact.display(fc.path)} is not compared on any branch: its name is not valid text, so it cannot be cited"))
    comparable = [fc for fc in files if fc.path_is_text]
    pre_lines = _read_pre(git, remote, change, comparable, notes)
    rows: list[BranchState] = []
    states: list[FileState] = []
    skipped: list[str] = []
    complete = True
    for name in names:
        tip, tip_error = res.heads[name], None
        if as_of is not None:
            pinned = git.pin_read(remote, name, as_of, kind="branch")
            if pinned.is_absent:
                skipped.append(name)
                continue
            if pinned.is_unreadable:
                tip, tip_error = "", pinned.detail
            else:
                tip = pinned.text
        if head_ref == name and change.head_sha in (tip, res.heads[name]):
            notes.append(_clean(f"{name} is left out of the matrix: it is the change's own head branch (its tip is the change's head)"))
            continue
        if tip_error is not None:  # the git layer recorded the failed pin
            rows.append(BranchState(name, "", "unreadable", 0, (), _clean(f"the tip as of {as_of} could not be read: {tip_error}")))
            complete = False
            continue
        reads = _read_all(git, remote, tip, comparable)  # two git calls for the branch, not two for each file
        mine = [_file_state(reads[fc.path], pre_lines[fc.path], repo.name, name, tip, fc) for fc in comparable]
        states.extend(mine)
        row_set, branch_complete = _rows_for(name, tip, mine, notes)
        rows.extend(row_set)
        complete = complete and branch_complete
    if skipped:
        notes.append(_clean(f"no row for {', '.join(skipped)}: no commit of it exists at or before {as_of}"))
    if not rows and not notes:
        notes.append(_clean(f"no branch other than the change's own head branch matches {', '.join(patterns)}: there is no matrix"))
    return Matrix(tuple(rows), tuple(states), tuple(dict.fromkeys(notes)), primary, complete)


def _read_all(git: Any, remote: str, tip: str, files: Sequence[FileChange]) -> dict[str, tuple[Any, str]]:
    """Each changed file read at the tip: (the read, the path that was read). A renamed file that is absent at its new path is
    read at its old one."""
    reads = git.files_at(remote, [fc.path for fc in files], tip)
    out = {fc.path: (reads[fc.path], fc.path) for fc in files}
    renamed = [fc for fc in files if fc.status == "renamed" and fc.old_path and reads[fc.path].is_absent]
    if renamed:
        old = git.files_at(remote, [fc.old_path for fc in renamed], tip)
        for fc in renamed:
            if not old[fc.old_path].is_absent:
                out[fc.path] = (old[fc.old_path], fc.old_path)
    return out


def _read_pre(git: Any, remote: str, change: Any, files: Sequence[FileChange], notes: list[str]) -> dict[str, list[str] | None]:
    """Each changed file as it was before the change, as lines (empty for a file the change adds); None when it cannot be
    compared (no pre-change version, or the read failed: the git layer recorded that)."""
    pre = change.pre_change_sha
    if pre is None:
        if files:
            notes.append("no file is compared on any branch: the change has no pre-change version, and what a tip holds more than the file had before the change is what shows the change there")
        return {fc.path: None for fc in files}
    out: dict[str, list[str] | None] = {fc.path: [] for fc in files if fc.status == "added"}
    asked = {fc.path: (fc.old_path if fc.status in ("renamed", "copied") and fc.old_path else fc.path) for fc in files if fc.status != "added"}
    reads = git.files_at(remote, sorted(set(asked.values())), pre) if asked else {}
    for path, where in asked.items():
        read = reads[where]
        if read.is_unreadable:
            out[path] = None
            notes.append(_clean(f"{path} is not compared on any branch: its pre-change version could not be read ({read.detail})"))
        else:
            out[path] = read.text.split("\n") if read.is_found else []
    return out


def _file_state(got: tuple[Any, str], pre: list[str] | None, repo_name: str, branch: str, tip: str, fc: FileChange) -> FileState:
    read, read_path = got
    if pre is None:
        return FileState(branch, fc.path, "not_compared", read_path, tip, detail=_clean(f"{fc.path} has no pre-change version to compare with"))
    if read.is_unreadable:
        return FileState(branch, fc.path, "unreadable", read_path, tip, detail=_clean(f"{read_path}: {read.detail}"))
    if read.is_absent:
        if fc.status == "added":  # the change is not here: the file it adds is not (there is no line to cite)
            return FileState(branch, fc.path, "pre_change", read_path, tip, detail=_clean(f"{fc.path}, a file the change adds, is not in the tree at this tip"))
        return FileState(branch, fc.path, "file_absent", read_path, tip, detail=_clean(f"{fc.path} is not in the tree at this tip"))
    text = read.text
    lines = text.split("\n")
    cls = classify_text([x.text for x in fc.added_lines], [x.text for x in fc.removed_lines], lines, pre, [x.number for x in fc.added_lines])
    if cls.state is None:
        return FileState(branch, fc.path, "not_compared", read_path, tip, cls.added, cls.added_found, cls.removed, cls.removed_found,
                         detail=_clean(_why_not_compared(fc, cls)))
    cited = [Evidence(repo_name, read_path, n, tip, line) for n, line in cls.found]
    if not cited:  # a claim of pre_change or diverged still needs a line: the first the tip has, verbatim
        first = next(((n, line) for n, line in enumerate(lines, 1) if line.strip()), None)
        if first is None:
            return FileState(branch, fc.path, "not_compared", read_path, tip, detail=_clean(f"{read_path} has no text line at this tip: nothing to cite"))
        cited = [Evidence(repo_name, read_path, first[0], tip, first[1].rstrip("\r"))]
    return FileState(branch, fc.path, cls.state, read_path, tip, cls.added, cls.added_found, cls.removed, cls.removed_found,
                     tuple(cited), _clean(_counts(cls)), cls.cut)


def _counts(c: Classification) -> str:
    if c.added:
        text = f"{c.added_found} of {c.added} added lines found"
    else:
        text = "the change adds no line"
    if c.removed:
        text += f", {c.removed_found} of {c.removed} removed lines still there"
    if c.cut:
        text += f"; the first {MAX_EVIDENCE_PER_FILE} found lines are cited"
    return text


def _why_not_compared(fc: FileChange, c: Classification) -> str:
    if not c.added and c.removed and "only removes" in c.reason:
        return f"the change only removes lines in {fc.path} and they are gone here: not shown as present, since there is no added line to find"
    return f"{fc.path}: {c.reason}"


def _rows_for(branch: str, tip: str, mine: list[FileState], notes: list[str]) -> tuple[list[BranchState], bool]:
    rows: list[BranchState] = []
    classified = []
    for fs in mine:
        if fs.state == "not_compared":
            notes.append(_clean(f"{fs.path} on {branch}: {fs.detail}"))
        elif fs.state == "unreadable":
            rows.append(BranchState(branch, tip, "unreadable", 0, (), fs.detail))
        else:
            classified.append(fs)
    if classified:
        state, found = aggregate(classified)
        assert state is not None
        evidence = tuple(e for fs in classified for e in fs.evidence)
        if not evidence:  # every file is a new one that is absent: nothing to cite, and `file_absent` is the honest row
            state, found = "file_absent", 0
        shown = classified[:MAX_FILES_IN_DETAIL]
        detail = "; ".join(f"{fs.path}: {fs.state}" + (f" ({fs.detail})" if fs.state not in ("file_absent",) else "") for fs in shown)
        if len(classified) > len(shown):
            detail += f"; and {len(classified) - len(shown)} more files"
        rows.insert(0, BranchState(branch, tip, state, found, evidence, _clean(detail)))
    return rows, not any(r.state == "unreadable" for r in rows)
