"""`reachmap.tools.bundle_frontend`: the build of the frontend (`frontend/dist/`) copied into the package (`reachmap/runner/static/`).

The copy is a plain, checked one: only regular files of the kinds the runner serves, no link, no hidden file, and nothing at all when there
is no build (the package then serves its minimal page). The packaging hook (`setup.py`) and the command line use this one function.
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from reachmap.runner import server
from reachmap.tools import bundle_frontend as bf


def make_dist(root: Path, index: str = "<!doctype html><title>w</title>") -> Path:
    dist = root / "frontend" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(index, encoding="utf-8")
    (dist / "assets" / "index-abcd1234.js").write_text("// js", encoding="utf-8")
    (dist / "assets" / "style-abcd1234.css").write_text("a{}", encoding="utf-8")
    return dist


class Bundle(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory(prefix="reachmap-bundle-")
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name)

    def test_copies_a_build_and_says_how_many_files(self) -> None:
        dist = make_dist(self.root)
        dest = self.root / "out" / "static"
        self.assertEqual(bf.bundle(dist, dest), 3)
        self.assertEqual(sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()), ["assets/index-abcd1234.js", "assets/style-abcd1234.css", "index.html"])
        self.assertEqual((dest / "index.html").read_text(encoding="utf-8"), "<!doctype html><title>w</title>")

    def test_does_nothing_without_a_build(self) -> None:
        dest = self.root / "static"
        self.assertEqual(bf.bundle(self.root / "frontend" / "dist", dest), 0)
        self.assertFalse(dest.exists())
        (self.root / "frontend" / "dist").mkdir(parents=True)  # a folder without the page is not a build
        (self.root / "frontend" / "dist" / "x.js").write_text("//", encoding="utf-8")
        self.assertEqual(bf.bundle(self.root / "frontend" / "dist", dest), 0)
        self.assertFalse(dest.exists())

    def test_replaces_what_an_earlier_build_left(self) -> None:
        dist = make_dist(self.root)
        dest = self.root / "static"
        bf.bundle(dist, dest)
        (dist / "assets" / "index-abcd1234.js").unlink()
        (dist / "assets" / "index-ffff9999.js").write_text("// new", encoding="utf-8")
        bf.bundle(dist, dest)
        self.assertEqual(sorted(p.name for p in (dest / "assets").iterdir()), ["index-ffff9999.js", "style-abcd1234.css"])

    def test_refuses_a_destination_that_is_not_called_static(self) -> None:
        dist = make_dist(self.root)
        precious = self.root / "precious"
        precious.mkdir()
        (precious / "keep.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(bf.BundleError):
            bf.bundle(dist, precious)
        self.assertTrue((precious / "keep.txt").is_file())

    @unittest.skipUnless(hasattr(os, "symlink"), "no symbolic links")
    def test_refuses_a_link_in_the_build(self) -> None:
        dist = make_dist(self.root)
        outside = self.root / "secret.txt"
        outside.write_text("secret", encoding="utf-8")
        os.symlink(outside, dist / "assets" / "evil.js")
        dest = self.root / "static"
        with self.assertRaises(bf.BundleError):
            bf.bundle(dist, dest)
        self.assertFalse(dest.exists())

    def test_refuses_a_file_the_runner_would_not_serve(self) -> None:
        for name in ("assets/run.exe", "assets/.hidden.js", "assets/data.bin", "assets/index.js.map"):
            with self.subTest(name=name):
                holder = tempfile.TemporaryDirectory(prefix="reachmap-bundle-")
                self.addCleanup(holder.cleanup)
                root = Path(holder.name)
                dist = make_dist(root)
                (dist / name).write_text("x", encoding="utf-8")
                with self.assertRaises(bf.BundleError):
                    bf.bundle(dist, root / "static")
                self.assertFalse((root / "static").exists())

    def test_the_kinds_it_accepts_are_the_kinds_the_runner_serves(self) -> None:
        self.assertEqual(bf.KINDS, frozenset(k for k in server.CONTENT_TYPES if k != ".map"))

    def test_an_unfinished_copy_is_never_left_in_place(self) -> None:
        dist = make_dist(self.root)
        dest = self.root / "static"
        bf.bundle(dist, dest)
        (dist / "assets" / "bad.exe").write_text("x", encoding="utf-8")
        with self.assertRaises(bf.BundleError):
            bf.bundle(dist, dest)
        self.assertEqual((dest / "index.html").read_text(encoding="utf-8"), "<!doctype html><title>w</title>")  # the earlier copy is whole

    def test_the_command_line(self) -> None:
        dist = make_dist(self.root)
        dest = self.root / "x" / "static"
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(bf.main(["--dist", str(dist), "--dest", str(dest)]), 0)
        self.assertIn("3 files", out.getvalue())
        self.assertTrue((dest / "index.html").is_file())
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(bf.main(["--dist", str(self.root / "none"), "--dest", str(dest)]), 0)
        self.assertIn("no frontend build", out.getvalue())
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(bf.main(["--dist", str(dist), "--dest", str(self.root / "elsewhere")]), 2)

    def test_the_defaults_are_the_frontend_build_and_the_package_folder(self) -> None:
        self.assertEqual(bf.DEFAULT_DIST, Path(bf.__file__).resolve().parents[2] / "frontend" / "dist")
        self.assertEqual(bf.DEFAULT_DEST, server.STATIC_DIR)


if __name__ == "__main__":
    unittest.main()
