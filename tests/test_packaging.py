"""M13 packaging: the sdist and the wheel build without a frontend build (and without npm), and with one the wheel holds the workspace
files as `reachmap/runner/static/`, whether it is built from the source tree or from the sdist.

Runs the setuptools build backend in a copy of the project in a temporary folder (no network, no isolation), so it needs `setuptools` in the
Python that runs the tests; without it the tests are skipped (the standard library alone cannot build a wheel), unless the environment says
`REACHMAP_REQUIRE_PACKAGING=1` (CI does), when a missing setuptools is a failure.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path

try:
    import setuptools  # noqa: F401

    HAVE_SETUPTOOLS = True
except ImportError:
    HAVE_SETUPTOOLS = False

ROOT = Path(__file__).resolve().parents[1]
NOTICES = "THIRD_PARTY_LICENSES.txt"  # written into frontend/dist by `npm run build` (frontend/scripts/third-party-licenses.mjs)
ROOT_DOCS = ("CHANGELOG.md", "SECURITY.md", "CONTRIBUTING.md")  # the sdist carries them (MANIFEST.in)
BACKEND = "from setuptools import build_meta as b; print(b.{call}('out'))"


def copy_project(dest: Path) -> None:
    for name in ("pyproject.toml", "setup.py", "MANIFEST.in", "README.md", "LICENSE", *ROOT_DOCS):
        shutil.copy(ROOT / name, dest / name)
    shutil.copytree(ROOT / "reachmap", dest / "reachmap", ignore=shutil.ignore_patterns("__pycache__"))


def make_dist(project: Path) -> None:
    dist = project / "frontend" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>workspace</title>", encoding="utf-8")
    (dist / "assets" / "index-abcd1234.js").write_text("// app", encoding="utf-8")
    (dist / "assets" / "style-abcd1234.css").write_text("a{}", encoding="utf-8")
    (dist / NOTICES).write_text("Third-party notices\n\nexample 1.0.0\nLicence: MIT\n", encoding="utf-8")


def build(project: Path, call: str) -> Path:
    (project / "out").mkdir(exist_ok=True)
    done = subprocess.run([sys.executable, "-c", BACKEND.format(call=call)], cwd=project, capture_output=True, text=True, timeout=300)
    if done.returncode != 0:
        raise AssertionError(done.stderr[-2000:])
    return project / "out" / done.stdout.strip().splitlines()[-1]


def wheel_names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as z:
        return sorted(z.namelist())


def sdist_names(path: Path) -> list[str]:
    with tarfile.open(path) as t:
        return sorted(t.getnames())


class Metadata(unittest.TestCase):
    """The version has one home, `reachmap/__init__.py`, and the project metadata of a release (no setuptools needed)."""

    def pyproject(self) -> dict:
        return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    def test_the_version_lives_in_the_package_only(self) -> None:
        import reachmap

        data = self.pyproject()
        self.assertNotIn("version", data["project"])
        self.assertIn("version", data["project"]["dynamic"])
        self.assertEqual(data["tool"]["setuptools"]["dynamic"]["version"], {"attr": "reachmap.__version__"})
        self.assertEqual(reachmap.__version__, "0.1.0")

    def test_no_runtime_dependency_and_the_release_metadata(self) -> None:
        project = self.pyproject()["project"]
        self.assertEqual(project["dependencies"], [])
        self.assertNotIn("optional-dependencies", project)
        self.assertIn("Development Status :: 3 - Alpha", project["classifiers"])
        self.assertIn("Operating System :: POSIX :: Linux", project["classifiers"])
        self.assertIn("Operating System :: MacOS", project["classifiers"])
        self.assertNotIn("Operating System :: OS Independent", project["classifiers"])
        self.assertTrue(project["urls"])
        for url in project["urls"].values():
            self.assertTrue(url.startswith("https://github.com/adithyakm13/reachmap"), url)


@unittest.skipUnless(shutil.which("node"), "node is not installed (the release build runs this script; CI has node)")
class NoticesScript(unittest.TestCase):
    """`frontend/scripts/third-party-licenses.mjs` on a small invented node_modules and lock file."""

    SCRIPT = ROOT / "frontend" / "scripts" / "third-party-licenses.mjs"

    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory(prefix="reachmap-notices-")
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name)
        self.packages: dict[str, dict] = {"": {}}

    def package(self, name: str, *, files: dict[str, str], license_field: str | None = "MIT", dev: bool = False, optional: bool = False,
                install: bool = True) -> None:
        entry: dict = {"version": "1.2.3"}
        if license_field:
            entry["license"] = license_field
        if dev:
            entry["dev"] = True
        if optional:
            entry["optional"] = True
        self.packages[f"node_modules/{name}"] = entry
        if install:
            folder = self.root / "nm" / name
            folder.mkdir(parents=True)
            manifest = {"name": name, "version": "1.2.3", **({"license": license_field} if license_field else {})}
            (folder / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
            for file, text in files.items():
                (folder / file).write_text(text, encoding="utf-8")

    def run_script(self) -> subprocess.CompletedProcess:
        (self.root / "lock.json").write_text(json.dumps({"lockfileVersion": 3, "packages": self.packages}), encoding="utf-8")
        return subprocess.run(["node", str(self.SCRIPT), "--lock", str(self.root / "lock.json"), "--modules", str(self.root / "nm"),
                               "--out", str(self.root / "dist" / NOTICES)], capture_output=True, text=True, timeout=60)

    def test_it_writes_every_production_package_with_the_text_of_its_licence_files(self) -> None:
        self.package("alpha", files={"LICENSE": "Alpha licence text\n", "NOTICE.md": "Alpha notice\n", "readme.md": "not a licence\n"})
        self.package("@scope/beta", files={"licence.txt": "Beta licence text\r\n"}, license_field="ISC")
        self.package("gamma", files={}, license_field="0BSD")  # no file, a licence field: allowed, and said so
        self.package("dev-only", files={"LICENSE": "dev text\n"}, dev=True)
        self.package("optional-missing", files={}, optional=True, install=False)
        done = self.run_script()
        self.assertEqual(done.returncode, 0, done.stderr)
        text = (self.root / "dist" / NOTICES).read_text(encoding="utf-8")
        for want in ("alpha 1.2.3\nLicence: MIT", "Alpha licence text", "Alpha notice", "@scope/beta 1.2.3\nLicence: ISC", "Beta licence text",
                     "gamma 1.2.3\nLicence: 0BSD", "ships no licence file"):
            self.assertIn(want, text)
        for unwanted in ("not a licence", "dev text", "dev-only", "optional-missing", "\r"):
            self.assertNotIn(unwanted, text)

    def test_a_production_package_with_no_licence_file_and_no_licence_field_fails_the_build(self) -> None:
        self.package("alpha", files={"LICENSE": "Alpha\n"})
        self.package("bare", files={}, license_field=None)
        done = self.run_script()
        self.assertEqual(done.returncode, 1)
        self.assertIn("node_modules/bare: no licence file and no licence field", done.stderr)
        self.assertFalse((self.root / "dist" / NOTICES).exists())

    def test_a_required_package_that_is_not_installed_fails_the_build(self) -> None:
        self.package("alpha", files={"LICENSE": "Alpha\n"})
        self.package("missing", files={}, install=False)
        done = self.run_script()
        self.assertEqual(done.returncode, 1)
        self.assertIn("node_modules/missing: listed in the lock file but not installed", done.stderr)


class Packaging(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not HAVE_SETUPTOOLS:
            # CI sets REACHMAP_REQUIRE_PACKAGING=1 and installs setuptools, so that these tests never skip there without anyone seeing it
            if os.environ.get("REACHMAP_REQUIRE_PACKAGING") == "1":
                raise AssertionError("REACHMAP_REQUIRE_PACKAGING=1 but setuptools is not installed in this Python")
            raise unittest.SkipTest("setuptools is not installed in this Python")

    def project(self, with_dist: bool) -> Path:
        holder = tempfile.TemporaryDirectory(prefix="reachmap-pack-")
        self.addCleanup(holder.cleanup)
        project = Path(holder.name)
        copy_project(project)
        if with_dist:
            make_dist(project)
        return project

    def test_the_built_metadata_has_the_package_version_and_no_requirement(self) -> None:
        import reachmap

        project = self.project(False)
        wheel = build(project, "build_wheel")
        with zipfile.ZipFile(wheel) as z:
            (name,) = [n for n in z.namelist() if n.endswith(".dist-info/METADATA")]
            lines = z.read(name).decode("utf-8").splitlines()
        self.assertIn(f"Version: {reachmap.__version__}", lines)
        self.assertEqual([line for line in lines if line.startswith("Requires-Dist")], [])
        self.assertIn(f"reachmap-{reachmap.__version__}-py3-none-any.whl", wheel.name)

    def test_the_sdist_and_the_wheel_build_without_a_frontend_build(self) -> None:
        project = self.project(False)
        wheel, sdist = build(project, "build_wheel"), build(project, "build_sdist")
        names = wheel_names(wheel)
        self.assertIn("reachmap/runner/minimal/index.html", names)
        self.assertIn("reachmap/tools/bundle_frontend.py", names)
        self.assertEqual([n for n in names if "/static/" in n], [])
        self.assertEqual([n for n in sdist_names(sdist) if "/frontend/" in n], [])

    def test_the_wheel_holds_the_frontend_build_when_there_is_one(self) -> None:
        project = self.project(True)
        names = wheel_names(build(project, "build_wheel"))
        for name in ("index.html", "assets/index-abcd1234.js", "assets/style-abcd1234.css", NOTICES):
            self.assertIn(f"reachmap/runner/static/{name}", names)
        self.assertIn("reachmap/runner/minimal/index.html", names)
        self.assertFalse((project / "reachmap" / "runner" / "static").exists(), "the copy goes into the build folder, not the source tree")

    def test_the_sdist_holds_no_tests_and_no_sources_of_the_frontend(self) -> None:
        project = self.project(True)
        (project / "tests").mkdir()
        (project / "tests" / "test_x.py").write_text("x = 1\n", encoding="utf-8")
        (project / "frontend" / "src").mkdir()
        (project / "frontend" / "src" / "main.tsx").write_text("export {}\n", encoding="utf-8")
        (project / "frontend" / "package.json").write_text("{}\n", encoding="utf-8")
        names = [n.split("/", 1)[1] for n in sdist_names(build(project, "build_sdist")) if "/" in n]
        self.assertEqual([n for n in names if n.startswith(("tests/", "docs/", "examples/", "frontend/src", "frontend/node_modules"))], [])
        self.assertIn("frontend/dist/index.html", names)
        self.assertIn(f"frontend/dist/{NOTICES}", names)
        self.assertIn("LICENSE", names)
        for name in ROOT_DOCS:
            self.assertIn(name, names)

    def test_a_wheel_built_from_the_sdist_has_the_workspace_too(self) -> None:
        project = self.project(True)
        sdist = build(project, "build_sdist")
        self.assertIn("frontend/dist/index.html", [n.split("/", 1)[1] for n in sdist_names(sdist) if "/" in n])
        holder = tempfile.TemporaryDirectory(prefix="reachmap-unpack-")
        self.addCleanup(holder.cleanup)
        with tarfile.open(sdist) as t:
            if hasattr(tarfile, "data_filter"):
                t.extractall(holder.name, filter="data")
            else:  # a Python before the extraction filters: the archive is one this test just made
                t.extractall(holder.name)
        (unpacked,) = Path(holder.name).iterdir()
        names = wheel_names(build(unpacked, "build_wheel"))
        self.assertIn("reachmap/runner/static/index.html", names)
        self.assertIn(f"reachmap/runner/static/{NOTICES}", names)

    def test_a_stale_static_folder_of_the_checkout_is_never_packaged(self) -> None:
        stale = "reachmap/runner/static"
        project = self.project(False)
        (project / stale / "assets").mkdir(parents=True)
        (project / stale / "index.html").write_text("stale", encoding="utf-8")
        (project / stale / "assets" / "old-aaaaaaaa.js").write_text("// stale", encoding="utf-8")
        wheel, sdist = build(project, "build_wheel"), build(project, "build_sdist")
        self.assertEqual([n for n in wheel_names(wheel) if "/static/" in n], [])
        self.assertEqual([n for n in sdist_names(sdist) if "/static/" in n], [])
        # with a build, the wheel has the build's files and none of the stale ones
        make_dist(project)
        names = wheel_names(build(project, "build_wheel"))
        self.assertIn("reachmap/runner/static/index.html", names)
        self.assertNotIn("reachmap/runner/static/assets/old-aaaaaaaa.js", names)
        with zipfile.ZipFile(build(project, "build_wheel")) as z:
            self.assertEqual(z.read("reachmap/runner/static/index.html"), b"<!doctype html><title>workspace</title>")

    def test_a_build_the_runner_would_not_serve_stops_the_build(self) -> None:
        project = self.project(True)
        (project / "frontend" / "dist" / "assets" / "run.exe").write_text("x", encoding="utf-8")
        with self.assertRaises(AssertionError):
            build(project, "build_wheel")


if __name__ == "__main__":
    unittest.main()
