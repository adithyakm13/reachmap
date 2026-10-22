"""The localhost runner (M12): `reachmap serve`.

A runner, not a service: it listens on 127.0.0.1 only, refuses any request that is not a same-origin loopback request with the session
token, runs one analysis at a time in a child process that it can kill, serves the reports and records decisions (channel `runner`).

    create(workspace, config_path, reports_dir, port=..., env=..., git_env=...)   a `RunnerServer`, not yet serving
    run(server, out)                                                              print the URL, serve until interrupted

`jobs.py` is the queue and the child process, `api.py` what each route does, `server.py` the HTTP and its guards. Nothing here imports
more than the standard library and the rest of the package.
"""

from __future__ import annotations

import secrets
import signal
import threading
from pathlib import Path
from typing import Any, Callable, Mapping, TextIO

__all__ = ["create", "run"]


def create(workspace: Any, config_path: str | Path, reports_dir: str | Path, *, port: int = 8787, env: Mapping[str, str] | None = None,
           git_env: Mapping[str, str] | None = None, entry: str | None = None, entry_args: Mapping[str, Any] | None = None,
           log: Callable[[str], None] | None = None, timeout_s: float | None = None, queue_cap: int = 20, grace_s: float = 2.0,
           static_dir: str | Path | None = None, request_timeout: float = 10.0, max_headers: int = 64, request_deadline: float = 15.0,
           max_connections: int = 32, token: str | None = None) -> Any:
    """A runner bound to 127.0.0.1:`port` (0 picks a free port), with a fresh session token. There is no way to give another address.
    `entry` and `entry_args` (a function the child runs, by `module:name`) and the sizes are for tests."""
    from reachmap.runner import api, jobs, server

    runner = jobs.Runner(workspace, config_path, reports_dir, env=env, git_env=git_env, entry=entry, entry_args=entry_args,
                         timeout_s=timeout_s, queue_cap=queue_cap, grace_s=grace_s)
    return server.RunnerServer(api.Api(runner), token or secrets.token_urlsafe(32), port=port, static_dir=static_dir, log=log,
                               request_timeout=request_timeout, max_headers=max_headers, request_deadline=request_deadline,
                               max_connections=max_connections)


def run(app: Any, out: TextIO) -> int:
    """Print the URL to open (the token is in the fragment, which a browser never sends), serve until interrupted, then end the child.
    Returns 0."""
    port = app.server_address[1]
    print(f"Reachmap runner for workspace {app.runner.workspace.name}: http://127.0.0.1:{port}/#token={app.token}", file=out, flush=True)
    print("Press Ctrl-C to stop. Reports and decisions are kept in the reports directory.", file=out, flush=True)

    def stop(signum: int, frame: Any) -> None:
        raise KeyboardInterrupt

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)  # also when the shell started this in the background, which leaves SIGINT ignored
    try:
        app.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        app.runner.stop()
        app.server_close()
    return 0
