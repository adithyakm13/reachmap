"""The words this tool says about a model note (M14): its kind, its state, the stamp and what the check of a proposed file found. They are
fixed sentences, none of which quotes a note, a claim or a file, and they pass the wording check when this module is imported (a banned
phrase here is a bug). The renderers (`report.layout`) and the note store (`reachmap.notes`) both read them from here.

Nothing here does I/O.
"""

from __future__ import annotations

from reachmap import wording

__all__ = ["KIND_WORDS", "NOTES_LABEL", "REASON_WORDS", "STAMP", "STATE_WORDS"]

STAMP = "recorded via MCP, client reported as"
NOTES_LABEL = "Model notes, recorded via MCP; not evidence."
KIND_WORDS = {"observation": "observation", "question": "question", "proposed_test": "proposed test", "proposed_file": "proposed file"}
STATE_WORDS = {
    "current": "current: made on the latest revision, about the claim this report makes",
    "stale": "stale: made on an earlier revision, or about a claim that has changed since",
    "orphaned": "orphaned: what it was about is not in the latest report",
}
# What the check of a proposed file found (the codes are what is stored). None of these quotes the claim or the file.
REASON_WORDS = {
    "verified": "the file exists at a revision the report read and names a changed symbol in code, outside a comment or a string",
    "no_config": "no workspace configuration was given to reachmap mcp, so no file was read",
    "unknown_repo": "the repository is not in the workspace configuration, so no file was read",
    "repo_not_read": "the report did not read this repository at any revision, so there is no revision to read the file at",
    "symbol_not_changed": "the symbol is not the name of a symbol or a contract field that this change touched",
    "symbol_qualifier": "the symbol is qualified with a class or module other than the one the change touched",
    "symbol_not_distinctive": "the symbol is defined too often, or its definitions could not be counted, so it does not link code by name",
    "file_absent": "the file is not in the tree at the revision the report read",
    "file_unreadable": "the file could not be read, so nothing is established about it",
    "no_reader": "this kind of file is not read as code by this version",
    "unparsed": "the file could not be read as code",
    "not_in_code": "the symbol does not appear in the code of the file (a comment or a string does not count)",
    "unchecked_text": "a proposed file given as plain note text was not checked; propose_file checks one",
}

for _words in (STAMP, NOTES_LABEL, *KIND_WORDS.values(), *STATE_WORDS.values(), *REASON_WORDS.values()):
    wording.require_clean(_words, "note words")
