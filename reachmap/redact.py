"""Scrub credentials from anything that could be logged, recorded or raised (context 8).

A token never appears in a cache key, a cache file, a log, a report, an argv or an
exception. The git layer and the host layer pass every piece of text that leaves them
(subprocess stderr, URLs, exception messages, incompleteness details) through `scrub`.

Three kinds of thing are removed:
- the user and password part of a URL (`https://user:token@host/path` becomes
  `https://host/path`);
- values that sit behind a credential-looking name (an `Authorization` header, a `token=`
  query parameter, a `PRIVATE-TOKEN` header);
- tokens of well-known shapes, and every secret registered with `register_secret` (the
  host layer registers the value it reads from the environment, so even a token that
  arrives in an unexpected place is removed).
"""

from __future__ import annotations

import base64
import re
import threading
from typing import Iterable, Sequence
from urllib.parse import quote, quote_plus, urlsplit, urlunsplit

from reachmap import wording

REDACTED = "[REDACTED]"
MIN_SECRET_LENGTH = 6  # shorter registered values would mangle ordinary text

_URL_USERINFO = re.compile(r"(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)[^/\s@?#]*@")
_SCP_PASSWORD = re.compile(r"(?<![\w/@.-])[\w.-]+:[^\s/@:]+@(?=[\w.-]+:)")
_HEADER = re.compile(
    r"(?P<name>(?i:authorization|proxy-authorization|private-token|job-token|x-[\w-]*token|[\w-]*api-?key|(?<![\w-])token(?![\w-])))"
    r"(?P<close>[\"']?)(?P<sep>\s*[:=]\s*)(?P<open>[\"']?)"
    r"(?P<value>(?:(?i:bearer|basic|token)\s+)?[^\s,;\"'}\]]+)"
)
_QUERY = re.compile(
    r"(?P<lead>[?&;\s](?i:access[_-]?token|private[_-]?token|auth[_-]?token|token|password|passwd|secret|api[_-]?key|apikey|key))"
    r"=(?P<value>[^&\s\"']+)"
)
_CREDENTIAL_PARAMS = {
    "access_token", "private_token", "auth_token", "token", "password", "passwd", "secret", "api_key", "apikey", "key",
}
_SHAPES = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,}|glpat-[A-Za-z0-9_-]{16,}"
    r"|glcbt-[A-Za-z0-9_-]{16,}|xox[abprs]-[A-Za-z0-9-]{10,}|ATBB[A-Za-z0-9_=-]{20,}|ATATT[A-Za-z0-9_=-]{20,})"
)

_lock = threading.Lock()
_secrets: set[str] = set()


def register_secret(value: str | None) -> None:
    """Remember a secret so `scrub` removes it wherever it turns up, in the forms it can take:
    as given, URL quoted (three ways), and (when it holds a colon, as `user:app_password`
    does) the base64 an `Authorization: Basic` header carries, with and without padding.
    Short values are ignored."""
    if not isinstance(value, str) or len(value) < MIN_SECRET_LENGTH:
        return
    forms = {
        value, quote(value, safe=""), quote(value), quote_plus(value), quote_plus(value, safe=""),
    }
    if ":" in value:
        encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
        forms.update({encoded, encoded.rstrip("=")})
    with _lock:
        _secrets.update(f for f in forms if len(f) >= MIN_SECRET_LENGTH)


def forget_secrets() -> None:
    """Drop every registered secret (tests)."""
    with _lock:
        _secrets.clear()


_SSH_SCHEMES = ("ssh", "git+ssh", "ssh+git")
_SCP_USER = re.compile(r"^(?P<user>[^/\s@:]+)(?::[^/\s@]*)?@(?P<rest>[^/\s@:]+:(?!//).*)$")


_USER_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,31}$")


def _keep_user(user: str | None) -> str | None:
    """A login name is kept for ssh; anything that could be a token is not."""
    if user and _USER_NAME.match(user) and not _SHAPES.search(user):
        return user
    return None


def _drop_credential_params(query: str) -> str:
    kept = [part for part in query.split("&") if part and part.split("=", 1)[0].lower().replace("-", "_") not in _CREDENTIAL_PARAMS]
    return "&".join(kept)


def _clean_url(url: str, keep_ssh_user: bool) -> str:
    """A URL without a password or token: no userinfo (an ssh login name may stay), no
    credential query parameters (`access_token=`, `private_token=`, `token=` ...)."""
    if not isinstance(url, str):
        return url
    if "://" in url:
        try:
            parts = urlsplit(url)
            host = parts.netloc.rpartition("@")[2]
            user = _keep_user(parts.username) if keep_ssh_user and parts.scheme.lower() in _SSH_SCHEMES else None
            return urlunsplit((
                parts.scheme, f"{user}@{host}" if user else host, parts.path,
                _drop_credential_params(parts.query), parts.fragment,
            ))
        except ValueError:
            return _URL_USERINFO.sub(r"\g<scheme>", url)
    match = _SCP_USER.match(url)
    if match:
        user = _keep_user(match["user"]) if keep_ssh_user else None
        return f"{user}@{match['rest']}" if user else match["rest"]
    return url


def strip_userinfo(url: str) -> str:
    """The URL without its user and password (a scp-style `git@host:path` loses its user
    too) and without credential query parameters. Used for cache keys and logs, so a token
    pasted into a URL cannot reach the cache path."""
    return _clean_url(url, keep_ssh_user=False)


def transport_url(url: str) -> str:
    """The URL to hand to git: a password or token is always removed, and so is the user of a
    web URL. The user of an ssh URL (`git@host`) is kept, because ssh needs it to log in;
    it is a name, not a secret (a user that is long or token-shaped is dropped)."""
    return _clean_url(url, keep_ssh_user=True)


def scrub(text: object, extra: Iterable[str] = ()) -> str:
    """`text` with credentials removed (see the module docstring). Always returns a str."""
    out = text if isinstance(text, str) else str(text)
    out = _URL_USERINFO.sub(r"\g<scheme>", out)
    out = _SCP_PASSWORD.sub("", out)
    out = _HEADER.sub(lambda m: f"{m['name']}{m['close']}{m['sep']}{m['open']}{REDACTED}", out)
    out = _QUERY.sub(lambda m: f"{m['lead']}={REDACTED}", out)
    out = _SHAPES.sub(REDACTED, out)
    with _lock:
        secrets = [s for s in (*_secrets, *extra) if isinstance(s, str) and len(s) >= MIN_SECRET_LENGTH]
    for secret in sorted(secrets, key=len, reverse=True):
        out = out.replace(secret, REDACTED)
    return out


def scrub_argv(argv: Sequence[str]) -> list[str]:
    """A copy of an argument vector with credentials removed, for messages and logs. The
    value after `-H`, `--header`, `--password`, `--token` is dropped whole."""
    out: list[str] = []
    hide_next = False
    for arg in argv:
        if hide_next:
            out.append(REDACTED)
            hide_next = False
            continue
        if arg in ("-H", "--header", "--password", "--token", "-u", "--user"):
            out.append(arg)
            hide_next = True
            continue
        out.append(scrub(arg))
    return out


def scrub_exception(exc: BaseException) -> str:
    """`TypeName: message` of an exception, scrubbed."""
    message = scrub(str(exc))
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def for_record(text: object, limit: int = 600) -> str:
    """Text fit for an incompleteness detail: scrubbed, on one line, cut at `limit`, and with
    any phrase the product never says (a git message may contain "safe.directory") defused,
    because a record's detail is checked for banned wording as written."""
    out = " ".join(scrub(text).split())
    out = out.encode("utf-8", errors="replace").decode("utf-8")  # a lone surrogate (a non-UTF-8 file name) cannot be written out
    for phrase in wording.check(out):
        pattern = re.compile(r"[\s-]+".join(re.escape(w) for w in phrase.split()), re.IGNORECASE)
        out = pattern.sub(lambda m: m.group(0)[0] + "_" + m.group(0)[1:] if len(m.group(0)) > 1 else m.group(0), out)
    if len(out) > limit:
        out = out[: limit - 3] + "..."
    return out


def display(text: str) -> str:
    """A file name or other text fit to show: the bytes of a name that is not valid UTF-8 (Python holds them as lone surrogates)
    become `\\xNN` escapes, so that the text can be written as JSON."""
    try:
        text.encode("utf-8")
        return text
    except UnicodeEncodeError:
        return text.encode("utf-8", errors="surrogateescape").decode("utf-8", errors="backslashreplace")


_USER_HOME = re.compile(r"(?<![\w.-])/(?:Users|home)/[^/\s\"'`:;,)]+")
_TMP_FOLDERS = re.compile(r"(?:/private)?/var/folders/[^/\s]+/[^/\s]+/T")
_TMP = re.compile(r"(?<![\w./-])/tmp(?![\w-])")


def _at_path_start(directory: str) -> re.Pattern:
    """The directory only where a path starts with it: not inside a longer path such as `/api/tmp/list`."""
    return re.compile(r"(?<![\w./-])" + re.escape(directory) + r"(?![\w-])")


def hide_machine_paths(text: str) -> str:
    """A report says nothing about the machine that made it: the home directory (or any `/Users/<name>`, `/home/<name>`)
    becomes `~` and a temporary folder `<tmp>`."""
    import os
    import tempfile

    tmp_dirs = {tempfile.gettempdir(), os.path.realpath(tempfile.gettempdir())}
    for d in sorted(tmp_dirs, key=len, reverse=True):
        if d and d != "/":
            text = _at_path_start(d).sub("<tmp>", text)
    text = _TMP_FOLDERS.sub("<tmp>", text)
    home = os.path.expanduser("~")
    if home and home != "/" and home not in tmp_dirs:
        text = _at_path_start(home).sub("~", text)
    text = _USER_HOME.sub("~", text)
    return _TMP.sub("<tmp>", text)
