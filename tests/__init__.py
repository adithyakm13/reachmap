"""Tests for reachmap. The suite makes no network call: no host adapter may build a default
transport (`gh`, `glab` or urllib) while this flag is set (reachmap.hosts checks it)."""

import os

os.environ.setdefault("REACHMAP_FORBID_DEFAULT_TRANSPORT", "1")
