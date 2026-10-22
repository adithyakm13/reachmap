"""Version parsing and ordering (a PEP 440 subset and semver), and tags as versions.

Versions are ordered by their parsed parts, never as strings (`1.10.0` is after `1.9.0`). The forms read:

- release numbers `N(.N)*`, an optional epoch `N!`, a pre-release (`a`, `b`, `rc` and their long spellings, or the dotted
  semver identifiers `-alpha.1`, `-rc.2`), `.postN`, `.devN`, and a `+local` or `+build` part that is kept as text and
  not part of the order (trailing zeros do not matter: `1.0` is `1.0.0`);
- a leading `v` or `V`.

Text that is not one of these does not parse: `parse` answers None, and the caller says "unparsed" and does not decide
what depends on it. A version number never says what a commit contains: that is ancestry, read from git (`Tags`).

`Tags` is the other half: the tags of a producer repository read as versions, and whether the commit of a change is
contained in a tag (`git merge-base --is-ancestor`). Every answer is found, absent or unreadable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

__all__ = ["Tags", "Version", "parse", "tag_version"]

_PEP = re.compile(
    r"""^(?:(?P<epoch>\d+)!)?
        (?P<release>\d+(?:\.\d+)*)
        (?:[-_.]?(?P<pre_l>alpha|beta|preview|pre|rc|a|b|c)(?:[-_.]?(?P<pre_n>\d+))?)?
        (?:(?:-(?P<post_implicit>\d+))|(?:[-_.]?(?P<post_l>post|rev|r)(?:[-_.]?(?P<post_n>\d+))?))?
        (?:[-_.]?(?P<dev_l>dev)(?:[-_.]?(?P<dev_n>\d+))?)?
        (?:\+(?P<local>[a-z0-9]+(?:[-_.][a-z0-9]+)*))?$""",
    re.IGNORECASE | re.VERBOSE,
)
_SEMVER = re.compile(
    r"^(?P<release>\d+\.\d+\.\d+)(?:-(?P<pre>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+(?P<local>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_PRE_NAMES = {"alpha": "a", "a": "a", "beta": "b", "b": "b", "c": "rc", "rc": "rc", "pre": "rc", "preview": "rc"}


def _ident(text: str) -> tuple[int, Any]:
    return (0, int(text)) if text.isdigit() else (1, _PRE_NAMES.get(text.lower(), text.lower()))


@dataclass(frozen=True, slots=True, eq=False)
class Version:
    """A parsed version. `text` is the version as written without the prefix. Compare versions with `<`, `==` ...
    (only with another Version)."""

    text: str
    epoch: int
    release: tuple[int, ...]
    pre: tuple[tuple[int, Any], ...] | None
    post: int | None
    dev: int | None
    local: str | None = None

    @property
    def is_prerelease(self) -> bool:
        return self.pre is not None or self.dev is not None

    def _key(self) -> tuple[Any, ...]:
        release = list(self.release)
        while len(release) > 1 and release[-1] == 0:
            release.pop()
        if self.pre is None and self.post is None and self.dev is not None:
            pre: tuple[Any, ...] = (-1,)  # 1.0.dev1 sorts before 1.0a1
        elif self.pre is None:
            pre = (1,)
        else:
            pre = (0, self.pre)
        post = (-1,) if self.post is None else (0, self.post)
        dev = (1,) if self.dev is None else (0, self.dev)
        return (self.epoch, tuple(release), pre, post, dev)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Version) and self._key() == other._key()

    def __hash__(self) -> int:
        return hash(self._key())

    def _other(self, other: object) -> "Version":
        if not isinstance(other, Version):
            raise TypeError("a Version is ordered only against another Version")
        return other

    def __lt__(self, other: object) -> bool:
        return self._key() < self._other(other)._key()

    def __le__(self, other: object) -> bool:
        return self._key() <= self._other(other)._key()

    def __gt__(self, other: object) -> bool:
        return self._key() > self._other(other)._key()

    def __ge__(self, other: object) -> bool:
        return self._key() >= self._other(other)._key()

    def __str__(self) -> str:
        return self.text


def parse(text: Any) -> Version | None:
    """The version `text` spells (a leading `v` is accepted), or None when it is not one of the forms above."""
    if not isinstance(text, str) or text != text.strip() or not text:
        return None
    body = text[1:] if text[:1] in ("v", "V") else text
    if not body or not body[0].isdigit():
        return None
    m = _PEP.match(body)
    if m is not None:
        pre = None
        if m["pre_l"]:
            pre = ((1, _PRE_NAMES[m["pre_l"].lower()]), (0, int(m["pre_n"] or 0)))
        post = None
        if m["post_implicit"] is not None:
            post = int(m["post_implicit"])
        elif m["post_l"]:
            post = int(m["post_n"] or 0)
        dev = int(m["dev_n"] or 0) if m["dev_l"] else None
        return Version(body, int(m["epoch"] or 0), tuple(int(p) for p in m["release"].split(".")), pre, post, dev, m["local"])
    m = _SEMVER.match(body)
    if m is not None:
        pre = tuple(_ident(p) for p in m["pre"].split(".")) if m["pre"] else None
        return Version(body, 0, tuple(int(p) for p in m["release"].split(".")), pre, None, None, m["local"])
    return None


def tag_version(tag: str, prefix: str | None) -> Version | None:
    """The version a tag names. With a `prefix` (the producer's configured `tag_prefix`, possibly empty) the tag must
    start with it, exactly; with None a leading `v` is accepted or absent."""
    if not isinstance(tag, str):
        return None
    if prefix is None:
        return parse(tag)
    if not tag.startswith(prefix):
        return None
    rest = tag[len(prefix):]
    return parse(rest) if prefix else (parse(rest) if rest[:1].isdigit() else None)


class Tags:
    """The tags of one producer repository at its remote, as versions, and ancestry against them. Built by
    `Tags.read`; `state` is found, absent (the remote has no tag with the prefix) or unreadable (the git layer recorded it)."""

    def __init__(self, state: str, tags: dict[str, str], prefix: str | None, detail: str | None = None) -> None:
        self.state = state
        self.by_name = dict(tags)
        self.prefix = prefix
        self.detail = detail
        self.versions: dict[str, Version] = {}
        for name in sorted(tags):
            v = tag_version(name, prefix)
            if v is not None:
                self.versions[name] = v

    @classmethod
    def read(cls, git: Any, remote: str, prefix: str | None) -> "Tags":
        res = git.tags(remote, prefix if prefix else None, prefetch=True)
        if res.state == "unreadable":
            return cls("unreadable", {}, prefix, res.detail)
        if res.state == "absent":
            return cls("absent", {}, prefix)
        return cls("found", dict(res.tags), prefix)

    def tag_for_pin(self, pin: str) -> tuple[str | None, str]:
        """The tag a consumer's pin names: (tag, how). Exact `prefix + pin` first, then the one tag whose parsed version
        equals the parsed pin. (None, reason) when the pin parses and no tag (or more than one) names it."""
        if self.prefix is not None and f"{self.prefix}{pin}" in self.by_name:
            return f"{self.prefix}{pin}", "exact"
        if self.prefix is None:
            for name in (pin, f"v{pin}"):
                if name in self.by_name:
                    return name, "exact"
        want = parse(pin)
        if want is None:
            return None, "the pin is not a version"
        same = [n for n, v in self.versions.items() if v == want]
        if len(same) == 1:
            return same[0], "version"
        return None, "no tag names this version" if not same else f"several tags name this version: {', '.join(same)}"

    def containing(self, git: Any, remote: str, sha: str) -> tuple[list[str], list[str]]:
        """(tags that contain the commit, tags whose ancestry could not be read), lowest version first. A tag that
        does not parse as a version is not a release and is not asked. One `git for-each-ref --contains` answers for the tags
        the cache holds; the others are asked one by one."""
        found: list[str] = []
        unreadable: list[str] = []
        order = sorted(self.versions, key=lambda n: (self.versions[n], n))
        decided, undecided = git.tags_containing(remote, sha, {n: self.by_name[n] for n in order})
        for name in order:
            if name in undecided:  # the cache did not hold it, or the single question failed: ask for this tag alone
                res = git.is_ancestor(remote, sha, self.by_name[name])
                if res.state == "unreadable":
                    unreadable.append(name)
                elif res.text == "true":
                    found.append(name)
            elif name in decided:
                found.append(name)
        return found, unreadable
