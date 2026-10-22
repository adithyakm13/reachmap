"""Packaging hook (setuptools): put the built frontend into the wheel.

All the project's metadata is in `pyproject.toml`. This file only adds one step to the build of the package: when `frontend/dist/index.html`
exists (the release order is `npm ci`, `npm run build` in `frontend/`, then `python -m build`), the build is copied into the package as
`reachmap/runner/static/` (`reachmap.tools.bundle_frontend`), which is where `reachmap serve` looks for the workspace. Without a build
nothing is copied, nothing fails and the package serves its minimal page: the sdist and the wheel build without npm.

The copy goes into the build folder, not into the source tree, so a package build leaves the checkout as it was, and a `static/` folder left in the checkout is not packaged. The sdist carries
`frontend/dist/` when it exists (`MANIFEST.in`), so a wheel built from the sdist has the workspace too.
"""

import importlib.util
import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py

ROOT = Path(__file__).resolve().parent


def _bundler():
    """`reachmap/tools/bundle_frontend.py`, loaded by its path: the package itself is not imported while it is being built."""
    spec = importlib.util.spec_from_file_location("reachmap_bundle_frontend", ROOT / "reachmap" / "tools" / "bundle_frontend.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class build_py(_build_py):
    def run(self):
        super().run()
        if self.dry_run:
            return
        bundler = _bundler()
        dest = Path(self.build_lib) / "reachmap" / "runner" / "static"
        # the build in `frontend/dist/` is the only source of these files: whatever a checkout holds in `reachmap/runner/static/` (what
        # `bundle_frontend` put there for `reachmap serve`) or an earlier build left in the build folder is never packaged
        shutil.rmtree(dest, ignore_errors=True)
        count = bundler.bundle(ROOT / "frontend" / "dist", dest)
        if count:
            self.announce(f"bundled the frontend build: {count} files", 2)
        else:
            self.announce("no frontend build (frontend/dist/index.html): the package serves its minimal page", 2)


setup(cmdclass={"build_py": build_py})
