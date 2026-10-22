"""Reports for the M10 tests: the committed fixtures (`tests/fixtures/reports/`), reports of real fixture runs, and one real run
whose changed source line holds the hostile strings of the brief (a closing script tag, markup, a backtick run, link syntax ...).

`hostile_run()` builds a one repository workspace in a temporary folder, commits a change whose `def` line (cited as evidence
of the area), whose route (the label of the area) and whose file name (a row of the branch matrix) carry those strings, and runs
`reachmap analyse` on it in process. It is made once per process.
"""

from __future__ import annotations

import atexit
import copy
import io
import json
import tempfile
from pathlib import Path
from typing import Any

from reachmap import cli
from tests import support_cli
from tests import support_git as sg

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "reports"
GOLDEN = Path(__file__).resolve().parent / "golden"

HOSTILE_LINES = (
    "</script><script>alert(1)</script>", "<!--", "<img src=x onerror=alert(1)>", '"><svg onload=alert(1)>', "javascript:alert(1)",
    "\u2028", "\u2029", "]]>", "`" * 10, "[x](javascript:alert(1))", "<details open>", "�", "\u202e",
)

S13 = ("--repo", "acme/api", "--compare", "main...scenario/s13-branch-matrix")
S11 = ("--repo", "acme/contracts", "--compare", "main...scenario/s11-contract-field")


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


HOSTILE_ALL = (*HOSTILE_LINES, "\ud800", "&amp; &#60;", "\x1b[31m", "\n# heading", "safe", "$x$ :smile:", "x@y.invalid https://x.invalid/ www.x.invalid")
# What the logic of the renderers needs as it is: the status, the risk and the confidence, the priority, a time, and the kind of the record
# that says why a run failed or timed out. Every other string of a report is content, and `hostile_copy` makes all of it hostile.
KEEP_KEYS = frozenset({"schema", "status", "risk", "confidence", "priority", "started_at", "formats"})
KEEP_VALUES = frozenset({"run_failed", "timed_out"})


def hostile_copy(obj: Any, key: str | None = None) -> Any:
    """A copy of a report in which every string that is not what the logic needs verbatim (see `KEEP_KEYS`) ends with every hostile
    string: in every `kind`, `state`, `adoption`, `basis`, edge kind, path, title, detail, branch and so on. The shape is the same."""
    if isinstance(obj, dict):
        return {k: hostile_copy(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [hostile_copy(v, key) for v in obj]
    if isinstance(obj, str) and key not in KEEP_KEYS and obj not in KEEP_VALUES:
        return obj + " " + " ".join(HOSTILE_ALL)
    return obj


def mark(obj: Any, key: str | None = None, marker: str = "ZQX") -> Any:
    """The same with one short marker, to find where a string of a report reaches something that is checked or sent as the tool's own."""
    if isinstance(obj, dict):
        return {k: mark(v, k, marker) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mark(v, key, marker) for v in obj]
    if isinstance(obj, str) and key not in KEEP_KEYS and obj not in KEEP_VALUES:
        return obj + marker
    return obj


def real(*args: str) -> dict[str, Any]:
    """The report of `reachmap analyse` on the M1 fixtures (made once per process by `support_cli`)."""
    result = support_cli.cli_run(*args)
    assert result.code == 0, result.err
    return json.loads(json.dumps(result.report))


_HOSTILE: dict[int, dict[str, Any]] = {}


def _source(version: int, big: int) -> str:
    tail = " ".join(HOSTILE_LINES)
    if version == 0:
        return f"def compute(value, label={'old ' + tail!r}):\n    return value\n"
    return f"def compute(value, label='new'):  # {tail}\n    return value + 1\nBIG = '{'A' * big}'\n"


def hostile_run(big: int = 0) -> dict[str, Any]:
    """The report of a real run on a change whose source holds the hostile strings. `big` makes one added line that long."""
    if big not in _HOSTILE:
        root = Path(tempfile.mkdtemp(prefix="reachmap-hostile-"))
        atexit.register(sg.remove_tree, root)
        repo = sg.make_repo(root, "solo", [
            {"files": {
                "svc/pyproject.toml": "[project]\nname = 'svc'\nversion = '0'\n",
                "svc/app/__init__.py": "",
                "svc/app/main.py": "from fastapi import FastAPI\nfrom app.routes import router\n\napp = FastAPI()\napp.include_router(router, prefix='/api')\n",
                "svc/app/routes.py": "from fastapi import APIRouter\nfrom app.service import compute\n\nrouter = APIRouter()\n\n\n@router.get(\"/<img src=x onerror=alert(1)>\")\ndef handler():\n    return compute(1)\n",
                "svc/app/service.py": _source(0, 0),
                "javascript:alert(1)": "old\n",
            }},
            {"branch": "topic", "from": "main", "files": {"svc/app/service.py": _source(1, big), "javascript:alert(1)": "new </script>\n"}},
        ])
        config = root / "reachmap.toml"
        config.write_text(
            'workspace = "solo"\n[limits]\nhops = 3\ntimeout_s = 900\n[repos."acme/solo"]\nhost = "git"\n'
            f'remote = "file://{repo}"\nlanguage = "python"\nroles = ["backend"]\nbranches = ["main"]\nservice_marker = "pyproject.toml"\n'
            'entry_points = { kind = "fastapi" }\ntests = { command = "pytest {paths}" }\n', encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(["analyse", "--config", str(config), "--repo", "acme/solo", "--compare", "main...topic", "--out", str(root / "out")],
                        env={"REACHMAP_CACHE": str(root / "cache")}, stdout=out, stderr=err, git_env=sg.isolated_env(root / "home"))
        assert code == 0, err.getvalue()
        (path,) = (root / "out").glob("*.json")
        _HOSTILE[big] = json.loads(path.read_text(encoding="utf-8"))
    return copy.deepcopy(_HOSTILE[big])


def with_long_line(report: dict[str, Any], length: int = 2_000_000) -> dict[str, Any]:
    """A copy of a report whose first finding cites a line of `length` characters that ends in a closing script tag."""
    out = copy.deepcopy(report)
    out["findings"][0]["evidence"][0]["detail"] = "X" * length + "</script><script>alert(1)</script>"
    return out
