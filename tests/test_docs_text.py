"""What a person reads in the documents follows the style and the rules of the project.

- Plain punctuation only (CLAUDE.md: "No em dashes, en dashes, arrows or curly quotes in anything a person
  reads"): no dash other than the hyphen, no arrow (the arrow characters, and `->` or `=>` in prose), no
  curly quote, no ellipsis character, and no invisible or look-alike space (any character of the Unicode
  category Cf, any space that is not a plain space, a figure dash). Code blocks and code spans of a
  Markdown file may hold an arrow of a language.
- No wording that claims safety or coverage (`reachmap.wording`: the same phrases the analyser refuses in
  its own output). The design notes `docs/CONTEXT.md` and `docs/BOOTSTRAP.md`, the progress log and
  CONTRIBUTING.md quote those phrases as the rule and are left out of this one check.
- No tokens, no ticket keys, and nothing of the earlier internal tool the design grew out of (clean-room
  rule). These scans read the whole text, code blocks and code spans included. Further terms that must
  never appear (the employer's names, which this repository does not list) are read from the file named by
  REACHMAP_CLEAN_ROOM_TERMS, one term a line, when it is set; the check is skipped without it. A separate
  test reads every tracked text file (`git ls-files`) for the earlier tool's name.

The files are README.md, CHANGELOG.md, SECURITY.md, CONTRIBUTING.md, examples/README.md and every
`docs/*.md`, and, for the comments and strings that a person reads, examples/reachmap.toml and
examples/run_example.py. A file that does not exist yet is skipped (docs/releasing.md arrives with the release).

The characters this test names are written as code points, so that this file holds none of them.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import subprocess
import tokenize
import unicodedata
import unittest
from pathlib import Path

from reachmap import wording

ROOT = Path(__file__).resolve().parent.parent
READ = ["README.md", "CHANGELOG.md", "SECURITY.md", "CONTRIBUTING.md", "examples/README.md", "examples/reachmap.toml", "examples/run_example.py"]
QUOTES_THE_RULE = {"docs/CONTEXT.md", "docs/BOOTSTRAP.md", "docs/PROGRESS.md", "CONTRIBUTING.md"}  # they state the rule, so they name the phrases

FORBIDDEN_CHARS = {
    0x2010: "hyphen variant", 0x2011: "non-breaking hyphen", 0x2012: "figure dash", 0x2013: "en dash", 0x2014: "em dash",
    0x2015: "horizontal bar", 0x2212: "minus sign", 0x2018: "curly quote", 0x2019: "curly quote", 0x201C: "curly quote",
    0x201D: "curly quote", 0x201A: "low quote", 0x201E: "low quote", 0x2026: "ellipsis character",
}
ARROW_RANGES = ((0x2190, 0x21FF), (0x2794, 0x27BF), (0x27F0, 0x27FF), (0x2900, 0x297F), (0x2B00, 0x2BFF))  # arrows, dingbat arrows, long and supplemental arrows
ASCII_ARROW = re.compile(r"->|=>|<-|<=>")
TOKEN_SHAPES = [
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), re.compile(r"\bAKIA[0-9A-Z]{16}\b"), re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}"), re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]
TICKET = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d{1,6}\b")
NOT_TICKETS = {"UTF", "SHA", "ISO", "RFC", "HTTP", "TLS", "PEP", "ES", "JSON", "TOML", "CVE", "GHSA", "X", "MD",
               "Z0"}  # Z0: the end of the character class A-Z0-9 in the ticket_pattern example of the design notes


def files() -> list[Path]:
    found = [ROOT / name for name in READ]
    found += sorted((ROOT / "docs").glob("*.md"))
    return [p for p in found if p.exists()]


def readable(path: Path) -> str:
    """The text a person reads in a file: all of a Markdown file; the comments of a TOML file; the comments, docstrings and strings of
    a Python file (what the script prints and says), not its code."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".toml":
        return "\n".join(line[line.index("#"):] for line in text.split("\n") if "#" in line)
    if path.suffix == ".py":
        kinds = {tokenize.STRING, tokenize.COMMENT, getattr(tokenize, "FSTRING_MIDDLE", tokenize.STRING)}  # 3.12 splits an f-string into tokens
        parts = [t.string for t in tokenize.generate_tokens(io.StringIO(text).readline) if t.type in kinds]
        return "\n".join(parts)
    return text


def prose(text: str) -> str:
    """The text without fenced code blocks and inline code spans (where a language's own arrows may stand)."""
    out: list[str] = []
    fenced = False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:  # the delimiters of an HTML comment are not an arrow; the words inside it are still read
            out.append(re.sub(r"`[^`\n]*`", "", line.replace("<!--", "    ").replace("-->", "   ")))
    return "\n".join(out)


def odd_character(char: str) -> str | None:
    """Why a character is not plain, or None."""
    code = ord(char)
    if code in FORBIDDEN_CHARS:
        return FORBIDDEN_CHARS[code]
    if any(low <= code <= high for low, high in ARROW_RANGES):
        return "arrow"
    category = unicodedata.category(char)
    if category == "Cf":
        return "invisible format character"
    if category in ("Zs", "Zl", "Zp") and char != " ":
        return "space that is not a plain space"
    return None


def punctuation_problems(text: str, *, markdown: bool = True) -> list[str]:
    found: list[str] = []
    for number, line in enumerate(text.split("\n"), 1):
        for char in dict.fromkeys(line):
            why = odd_character(char)
            if why:
                found.append(f"line {number}: {why} (U+{ord(char):04X})")
    reading = prose(text) if markdown else text
    for number, line in enumerate(reading.split("\n"), 1):
        if ASCII_ARROW.search(line.replace("<!--", "    ").replace("-->", "   ")):
            found.append(f"prose line {number} (counted without code blocks): an ASCII arrow in prose")
    return found


def wording_problems(text: str) -> list[str]:
    return [f"banned wording {phrase!r}" for phrase in wording.check(text)]


def clean_room_problems(text: str, extra_terms: tuple[str, ...] = ()) -> list[str]:
    found = [f"a token shape: {pattern.pattern[:30]}" for pattern in TOKEN_SHAPES if pattern.search(text)]
    for match in TICKET.finditer(text):
        if match.group(0).split("-")[0] not in NOT_TICKETS:
            found.append(f"something shaped like a ticket key: {match.group(0)}")
    lowered = text.lower()
    found += ["a term of the clean-room list" for term in extra_terms if term.lower() in lowered]
    return found


def clean_room_terms() -> tuple[str, ...]:
    where = os.environ.get("REACHMAP_CLEAN_ROOM_TERMS")
    if not where or not Path(where).is_file():
        return ()
    return tuple(line.strip() for line in Path(where).read_text(encoding="utf-8").split("\n") if line.strip())


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


class DocumentText(unittest.TestCase):
    def test_there_are_documents_to_read(self) -> None:
        names = {p.name for p in files()}
        for expected in ("README.md", "quickstart.md", "config.md", "adapters.md", "rules.md", "security.md", "evaluation.md",
                         "reachmap.toml", "run_example.py"):
            self.assertIn(expected, names)

    def test_plain_punctuation(self) -> None:
        for path in files():
            with self.subTest(file=relative(path)):
                self.assertEqual(punctuation_problems(readable(path), markdown=path.suffix == ".md"), [])

    def test_no_wording_that_claims_safety_or_coverage(self) -> None:
        for path in files():
            if relative(path) in QUOTES_THE_RULE:
                continue
            with self.subTest(file=relative(path)):
                self.assertEqual(wording_problems(readable(path)), [])

    def test_clean_room(self) -> None:
        terms = clean_room_terms()
        for path in files():
            with self.subTest(file=relative(path)):
                self.assertEqual(clean_room_problems(readable(path), terms), [])


# The earlier internal tool's name must not come back, and this file must not spell it either: it holds only the SHA-256 of the lowercased
# name (10 letters). A text is lowercased and split into words of letters and digits; every 10-letter word and every pair of adjacent words
# whose lengths add up to 10 (joined with no separator, so "x y" and "xy" both count) is hashed and compared.
NAME_SHA256 = "ade3d77d0cdb5866104731123473dc1ab56e14d0e697aab3ee6634313879bcf8"
NAME_LENGTH = 10
WORD = re.compile(rb"[a-z0-9]+")


def tracked_files() -> list[Path] | None:
    """Every file git tracks, or None when this is not a git checkout (an unpacked sdist has no .git)."""
    try:
        done = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return [ROOT / name for name in done.stdout.decode("utf-8").split("\0") if name]


def names_the_earlier_tool(data: bytes, digest: str = NAME_SHA256, length: int = NAME_LENGTH) -> bool:
    """True when a text holds the word whose SHA-256 is `digest`, in any case, as one word or as two adjacent words. A binary file (it has a
    NUL byte) is not text and is not read."""
    if b"\0" in data:
        return False
    words = WORD.findall(data.lower())
    candidates = {w for w in words if len(w) == length}
    candidates |= {a + b for a, b in zip(words, words[1:]) if len(a) + len(b) == length}
    return any(hashlib.sha256(c).hexdigest() == digest for c in candidates)


class NoEarlierToolName(unittest.TestCase):
    """The design grew out of an earlier internal tool whose name must not come back: not in a document, a test, a fixture or a script."""

    def test_no_tracked_text_file_names_the_earlier_tool(self) -> None:
        tracked = tracked_files()
        if tracked is None:
            self.skipTest("not a git checkout")
        self.assertGreater(len(tracked), 100)
        offenders = []
        for path in tracked:
            if path.is_file() and names_the_earlier_tool(path.read_bytes()):
                offenders.append(relative(path))
        self.assertEqual(offenders, [], "these tracked files name the earlier internal tool")

    def test_the_scan_sees_the_word_in_any_case_and_leaves_binary_files_alone(self) -> None:
        digest = hashlib.sha256(b"quillmarsh").hexdigest()  # a dummy 10-letter word stands in for the name
        for sample in (b"quillmarsh", b"QUILLMARSH", b"see QuillMarsh here\n", b"quill marsh", b"quill_marsh", b"a Quill-Marsh b", b"x\nquill\nmarsh\n"):
            self.assertTrue(names_the_earlier_tool(sample, digest), sample)
        for sample in (b"nothing of the kind here", b"quillmarsh2", b"quill a marsh", b"quillmars", b"\0quillmarsh", b""):
            self.assertFalse(names_the_earlier_tool(sample, digest), sample)

    def test_the_stored_digest_is_a_sha256_of_ten_letters(self) -> None:
        self.assertRegex(NAME_SHA256, r"^[0-9a-f]{64}$")
        self.assertEqual(NAME_LENGTH, 10)


PUBLISHED_BASE = "https://github.com/adithyakm13/reachmap/blob/v0.1.0/"  # where a link of the README points once the package is on PyPI
RAW_BASE = "https://raw.githubusercontent.com/adithyakm13/reachmap/v0.1.0/"
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")


def local_target(url: str) -> tuple[Path, str] | None:
    """The file in this checkout that a link of the README names (and the anchor), or None for a link to somewhere else."""
    for base in (PUBLISHED_BASE, RAW_BASE):
        if url.startswith(base):
            path, _, anchor = url[len(base):].partition("#")
            return ROOT / path, anchor
    return None


def anchors(markdown: str) -> set[str]:
    """The anchors GitHub makes for the headings of a Markdown file (outside code blocks)."""
    found = set()
    for line in prose(markdown).split("\n"):
        match = re.match(r"#{1,6}\s+(.*?)\s*#*\s*$", line)
        if match:
            heading = re.sub(r"[^\w\- ]", "", match.group(1).replace("`", "").lower())
            found.add(heading.replace(" ", "-"))
    return found


class ReadmeLinks(unittest.TestCase):
    """The README is the long description on PyPI, where a relative link goes nowhere: every link is absolute, at the tag of the release,
    and names a file that this checkout has (so a link that would be dead on GitHub fails here before the tag is made)."""

    def test_every_link_is_absolute_and_names_a_file_of_the_checkout(self) -> None:
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        urls = LINK.findall(text)
        self.assertTrue(urls)
        for url in urls:
            with self.subTest(url=url):
                self.assertRegex(url, r"^https://", "a relative link does not resolve on PyPI")
                target = local_target(url)
                self.assertIsNotNone(target, "a link to this repository must point at the tag of the release")
                path, anchor = target
                self.assertTrue(path.is_file(), f"{relative(path)} does not exist")
                if anchor:
                    self.assertIn(anchor, anchors(path.read_text(encoding="utf-8")), f"no heading {anchor!r} in {relative(path)}")

    def test_the_link_resolver(self) -> None:
        self.assertEqual(local_target(PUBLISHED_BASE + "docs/rules.md"), (ROOT / "docs" / "rules.md", ""))
        self.assertEqual(local_target(PUBLISHED_BASE + "CHANGELOG.md#known-limits"), (ROOT / "CHANGELOG.md", "known-limits"))
        self.assertEqual(local_target(RAW_BASE + "docs/shot.png"), (ROOT / "docs" / "shot.png", ""))
        self.assertIsNone(local_target("https://example.com/x"))
        self.assertIsNone(local_target("https://github.com/adithyakm13/reachmap/blob/main/README.md"))


class TheScannersCatchWhatTheyShould(unittest.TestCase):
    def test_punctuation(self) -> None:
        bad = [f"a {chr(0x2014)} b", f"a {chr(0x2013)} b", f"to {chr(0x2192)} from", f"it{chr(0x2019)}s", f"{chr(0x201C)}quoted{chr(0x201D)}",
               f"wait{chr(0x2026)}", "a -> b in prose", "a => b", f"a{chr(0xA0)}b", f"a{chr(0x200B)}b", f"1{chr(0x2012)}2", f"a {chr(0x2794)} b",
               f"a{chr(0x202E)}b", f"a{chr(0xFEFF)}b", f"a{chr(0x2009)}b", f"a{chr(0x200D)}b", f"a {chr(0x27F6)} b"]
        for sample in bad:
            self.assertTrue(punctuation_problems(sample), ascii(sample))
        for sample in ("a - b", "a `->` b", "```\nx -> y\n```\n", "it's \"quoted\"", "three dots...", "<!-- TODO: a comment -->"):
            self.assertEqual(punctuation_problems(sample), [], sample)

    def test_a_python_or_toml_file_is_read_for_its_words_not_its_code(self) -> None:
        text = readable_text('def f() -> None:\n    print("a -> b")\n', ".py")
        self.assertTrue(punctuation_problems(text, markdown=False))
        self.assertEqual(punctuation_problems(readable_text('def f() -> None:\n    print("fine")\n', ".py"), markdown=False), [])
        self.assertTrue(punctuation_problems(readable_text('key = "x"  # a -> b\n', ".toml"), markdown=False))
        self.assertEqual(punctuation_problems(readable_text('key = "a -> b"\n', ".toml"), markdown=False), [])

    def test_wording(self) -> None:
        for sample in ("the release is safe", "already covers it", "covered by tests", "nothing to test here"):
            self.assertTrue(wording_problems(sample), sample)
        for sample in ("it never claims safety", "an unsafe default", "safely ignored", "a test covers nothing here"):
            self.assertEqual(wording_problems(sample), [], sample)

    def test_clean_room(self) -> None:
        for prefix in ("ghp_", "gho_", "ghs_", "ghu_", "ghr_"):
            self.assertTrue(clean_room_problems(prefix + "a" * 30), prefix)
        for sample in ("see ABC-123 for", "`ABC-123` in code is scanned too", "Bearer " + "x" * 30):
            self.assertTrue(clean_room_problems(sample), sample)
        self.assertTrue(clean_room_problems("a Term of the list", ("term of",)))
        for sample in ("UTF-8 text", "SHA-256 of it", "ISO-8601", "no terms here"):
            self.assertEqual(clean_room_problems(sample), [], sample)


def readable_text(source: str, suffix: str) -> str:
    """`readable` for a text instead of a file."""
    class Fake:
        def __init__(self, text: str, suffix: str) -> None:
            self.suffix = suffix
            self._text = text

        def read_text(self, encoding: str = "utf-8") -> str:
            return self._text

    return readable(Fake(source, suffix))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
