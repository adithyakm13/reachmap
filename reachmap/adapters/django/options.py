"""The options of `entry_points = { kind = "django", ... }` in a repository's config.

- `root_urlconf`: the dotted name of the root URLconf (`config.urls`), when `ROOT_URLCONF` cannot be read from the settings (it comes from the
  environment, or several settings files disagree). It replaces what the settings say.
- `settings`: where `ROOT_URLCONF` is read from: a file, a glob or a folder of the repository. By default every file that assigns it.

A validator is called as `validator(options, describe)` and returns messages that never quote a configured value.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping

from reachmap.adapters import third_party

__all__ = ["KEYS", "validate"]

KEYS = ("root_urlconf", "settings")
# the files of a Django project are named like the modules it imports (`urls`, `models`, `views`, `settings`), and every URLconf defines `urlpatterns`
third_party.register("django", installed=("django", "rest_framework"), conventions=("urlpatterns", "app_name"))
_DOTTED = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


def validate(options: Mapping[str, Any], describe: Callable[[Any], str]) -> list[str]:
    out: list[str] = []
    for key in options:
        if key != "kind" and key not in KEYS:
            out.append(f"{key}: not a key of a django entry_points table; allowed keys: {', '.join(KEYS)}")
    for key in KEYS:
        value = options.get(key)
        if value is None:
            continue  # absent, or a variable that was not set
        if not isinstance(value, str) or not value.strip():
            out.append(f"{key}: expected a non-empty string, got {describe(value)}")
        elif key == "root_urlconf" and not _DOTTED.match(value):
            out.append("root_urlconf: expected a dotted module name such as config.urls")
        elif key == "settings" and (value.startswith("/") or ".." in value.split("/")):
            out.append("settings: expected a path inside the repository, with no leading slash and no '..'")
    return out
