"""Evaluation tools (M18, context section 9): the measures of whether Reachmap's output can be trusted and whether it helps.

    audit.py     the citation audit: every cited line re-read at its pinned commit (`reachmap audit`)
    witness.py   the reach-beyond-the-diff harness: counts, never percentages, over independent cases (`reachmap witness`)
    sheets.py    judging sheets for people, and reading filled sheets back (`reachmap sheet`)

No model call is made anywhere in here. The sheets are filled in by people and a model must not fill them: the MCP server has no tool that
writes or scores a sheet, a test lists its tools to keep it so, and `docs/evaluation.md` says the same.
"""

from __future__ import annotations

EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 2


class UsageError(ValueError):
    """Something wrong with what the person gave a command (a file, an option); the command turns it into exit 2 and a message."""


def emit(out: object, text: str, end: str = "\n") -> None:
    """`text` and `end` (a newline) on `out` as UTF-8 bytes when it has a byte stream (the real standard output), with anything that cannot be encoded
    replaced: what these commands print quotes source lines and file names from any repository, and a terminal or pipe set to ASCII must not make a
    command fail after the work is done. A stream with no bytes (a test's StringIO) gets the text."""
    raw = getattr(out, "buffer", None)
    if raw is None:
        out.write(text + end)  # type: ignore[attr-defined]
        return
    out.flush()  # type: ignore[attr-defined]
    raw.write((text + end).encode("utf-8", errors="replace"))
    raw.flush()


def remote_of(workspace: object, repo: str) -> str:
    """The remote of a repository of a workspace, as the pipeline finds it (the configured remote, else the host's own for the name). `LookupError`
    with words for a reader when there is no workspace, the repository is not in it, or the host cannot name the remote: such a repository is not
    read, which is never the same as nothing found in it."""
    if workspace is None:
        raise LookupError("no configuration was given, so no repository can be read (use --config)")
    cfg = workspace.repos.get(repo)  # type: ignore[attr-defined]
    if cfg is None:
        raise LookupError("the repository is not in the configuration")
    if cfg.remote:
        return cfg.remote
    from reachmap.hosts import get_host

    try:
        options: dict[str, object] = {"record_to": []}
        if cfg.host == "git":
            options["remotes"] = {}
        return get_host(cfg.host, **options).remote(cfg.name)
    except Exception as exc:  # a host that cannot name the remote: not read, not absent
        raise LookupError(f"the remote of the repository cannot be named ({type(exc).__name__})") from None
