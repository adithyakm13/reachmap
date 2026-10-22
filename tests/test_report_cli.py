"""`reachmap analyse --format json,md,html` and `reachmap report` (M10), and what the renderers never do: they make no network
call and read no file but their assets."""

import io
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from reachmap import cli, report
from tests import scenario_support as ss
from tests import support_cli, support_git as sg
from tests import support_report as sr
from tests.test_cli import S11, S13, FIXTURE_TOML

ROOT = Path(__file__).resolve().parents[1]


class Case(sg.TempDirCase):
    def setUp(self):
        super().setUp()
        e = ss.env()
        self.config = self.tmp / "reachmap.toml"
        self.config.write_text(FIXTURE_TOML.read_text())
        self.env = {"REACHMAP_FIXTURES": "file://" + str(e.dest), "REACHMAP_CACHE": str(e.cache)}
        self.out = self.tmp / "out"

    def analyse(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        code = cli.main(["analyse", "--config", str(self.config), *args, "--out", str(self.out)], env=self.env, stdout=stdout, stderr=stderr,
                        git_env=sg.isolated_env(ss.env().home))
        return code, stdout.getvalue(), stderr.getvalue()

    def render(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        code = cli.main(["report", *args], stdout=stdout, stderr=stderr)
        return code, stdout.getvalue(), stderr.getvalue()

    def write(self, data, name="in.json"):
        path = self.tmp / name
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        return str(path)


class Analyse(Case):
    def test_each_requested_format_is_written_next_to_the_json_with_the_same_base_name(self):
        code, out, err = self.analyse(*S13, "--format", "json,md,html")
        self.assertEqual((code, err), (0, ""))
        files = sorted(p.name for p in self.out.iterdir() if not p.name.startswith("."))
        (base,) = {Path(n).stem for n in files}
        self.assertEqual(files, [f"{base}.html", f"{base}.json", f"{base}.md"])
        data = json.loads((self.out / f"{base}.json").read_text(encoding="utf-8"))
        self.assertEqual((self.out / f"{base}.md").read_text(encoding="utf-8"), report.render(data, "md"))
        self.assertEqual((self.out / f"{base}.html").read_text(encoding="utf-8"), report.render(data, "html"))
        self.assertEqual(data["inputs"]["formats"], ["json", "md", "html"])
        self.assertIn(f"Markdown: {self.out / (base + '.md')}", out)
        self.assertIn(f"HTML: {self.out / (base + '.html')}", out)

    def test_the_json_is_always_written_it_is_the_contract(self):
        code, _, _ = self.analyse(*S13, "--format", "md")
        self.assertEqual(code, 0)
        self.assertEqual(sorted(p.suffix for p in self.out.iterdir() if not p.name.startswith(".")), [".json", ".md"])

    def test_json_alone_is_as_before(self):
        self.analyse(*S13)
        self.assertEqual([p.suffix for p in self.out.iterdir() if not p.name.startswith(".")], [".json"])

    def test_names_and_aliases_and_what_is_not_a_format(self):
        code, _, _ = self.analyse(*S13, "--format", "JSON, markdown ,html, md")
        self.assertEqual(code, 0)
        self.assertEqual(sorted(p.suffix for p in self.out.iterdir() if not p.name.startswith(".")), [".html", ".json", ".md"])
        for bad in ("pdf", "json,docx", ""):
            code, out, err = self.analyse(*S13, "--format", bad)
            self.assertEqual(code, 2, bad)
            self.assertIn("--format", err)
            self.assertIn("json, md, html", err)

    def test_a_failed_run_writes_the_reports_of_its_header_and_records_and_exits_3(self):
        code, out, err = self.analyse("--repo", "acme/api", "--compare", "main...nope", "--format", "json,md,html")
        self.assertEqual(code, 3)
        md = next(self.out.glob("*.md")).read_text(encoding="utf-8")
        self.assertIn("No findings are shown because the run did not complete.", md)
        self.assertNotIn("## Areas to test", md)
        self.assertTrue(next(self.out.glob("*.html")).exists())

    def test_a_format_that_cannot_be_written_is_exit_3_after_the_others_and_the_summary(self):
        real = report.render

        def flaky(data, fmt, view="full"):
            if fmt == "html":
                raise OSError(28, "No space left on device")
            return real(data, fmt, view=view)

        with mock.patch.object(report, "render", flaky):
            code, out, err = self.analyse(*S13, "--format", "json,md,html")
        self.assertEqual(code, 3)
        self.assertIn("html report could not be written", err)
        self.assertEqual(sorted(p.suffix for p in self.out.iterdir() if not p.name.startswith(".")), [".json", ".md"])  # the json and the md are there, no temporary file
        self.assertIn("Areas to test: 2", out)
        self.assertIn("Markdown: ", out)
        self.assertNotIn("HTML: ", out)

    def test_the_summary_of_a_run_that_writes_only_json_is_unchanged(self):
        _, out, _ = self.analyse(*S11)
        self.assertEqual(out, support_cli.cli_run(*S11).out.replace(str(support_cli.cli_run(*S11).path.parent), str(self.out)))


class Report(Case):
    def test_a_report_with_a_hostile_value_where_a_choice_is_needed_is_exit_2_and_a_message_never_a_traceback(self):
        for where, key in (("areas", "priority"), ("findings", "risk"), ("findings", "confidence")):
            data = sr.fixture("complete")
            data[where][0][key] = "safe </script>"
            code, out, err = self.render(self.write(data, f"{key}.json"))
            with self.subTest(key):
                self.assertEqual((code, out), (2, ""))
                self.assertIn(key, err)
                self.assertNotIn("Traceback", err)

    def test_every_hostile_report_renders_to_a_file_through_the_command(self):
        for name in ("hostile", "failed", "timed_out"):
            data = sr.hostile_copy(sr.fixture(name))
            src = self.write(data, f"{name}.json")
            for fmt in ("md", "html"):
                code, out, err = self.render(src, "--format", fmt, "--out", str(self.tmp / f"{name}.{fmt}"))
                self.assertEqual((code, out, err), (0, "", ""), (name, fmt))
                (self.tmp / f"{name}.{fmt}").read_bytes().decode("utf-8")

    def test_standard_output_gets_utf_8_whatever_the_terminals_encoding(self):
        data = sr.fixture("complete")
        data["findings"][0]["title"] = "caf\u00e9 \u4e2d\u6587 and a lone \ud800"
        src = self.write(data)
        env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "ascii", "PYTHONUTF8": "0"}
        done = subprocess.run([sys.executable, "-m", "reachmap", "report", src], capture_output=True, cwd=ROOT, env=env)
        self.assertEqual((done.returncode, done.stderr), (0, b""))
        self.assertEqual(done.stdout.decode("utf-8"), report.render(data, "md"))
        self.assertIn("caf\u00e9 \u4e2d\u6587".encode("utf-8"), done.stdout)

    def test_markdown_goes_to_stdout_by_default(self):
        data = sr.fixture("complete")
        code, out, err = self.render(self.write(data))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, report.render(data, "md"))

    def test_html_and_md_go_to_a_file_with_out(self):
        data = sr.fixture("complete")
        src = self.write(data)
        for fmt, name in (("md", "o.md"), ("html", "o.html"), ("markdown", "p.md")):
            code, out, err = self.render(src, "--format", fmt, "--out", str(self.tmp / name))
            self.assertEqual((code, out, err), (0, "", ""))
            self.assertEqual((self.tmp / name).read_text(encoding="utf-8"), report.render(data, fmt))

    def test_the_author_view(self):
        data = sr.fixture("complete")
        code, out, _ = self.render(self.write(data), "--view", "author")
        self.assertEqual(code, 0)
        self.assertEqual(out, report.render(data, "md", view="author"))
        self.assertIn("## For the author", out)
        self.assertNotIn("## Findings", out)

    def test_an_unknown_major_version_is_refused_with_exit_2_and_nothing_is_written(self):
        data = sr.fixture("complete")
        data["schema"] = 2
        code, out, err = self.render(self.write(data), "--out", str(self.tmp / "o.md"))
        self.assertEqual((code, out), (2, ""))
        self.assertIn("schema", err)
        self.assertIn("2", err)
        self.assertFalse((self.tmp / "o.md").exists())

    def test_what_cannot_be_read_is_exit_2_with_a_message(self):
        good = self.write(sr.fixture("complete"), "good.json")
        cases = [(["nothing.json"], "nothing.json"), ([self.write("{", "bad.json")], "JSON"), ([self.write([], "array.json")], "object"),
                 ([good, "--format", "json"], "--format"), ([good, "--format", "pdf"], "--format"), ([good, "--view", "x"], "--view"), ([], "REPORT")]
        for args, word in cases:
            code, out, err = self.render(*args)
            with self.subTest(args=args):
                self.assertEqual((code, out), (2, ""))
                self.assertIn(word, err)

    def test_an_out_that_cannot_be_written_is_exit_2(self):
        code, _, err = self.render(self.write(sr.fixture("complete")), "--out", str(self.tmp / "no" / "such" / "dir" / "o.md"))
        self.assertEqual(code, 2)
        self.assertIn("--out", err)

    def test_a_failed_and_a_timed_out_report_render_with_exit_0(self):
        for name in ("failed", "timed_out"):
            code, out, _ = self.render(self.write(sr.fixture(name)))
            self.assertEqual(code, 0)
            self.assertIn("No findings are shown because the run did not complete.", out)

    def test_it_runs_as_a_separate_process_too(self):
        src = self.write(sr.fixture("hostile"))
        done = subprocess.run([sys.executable, "-m", "reachmap", "report", src, "--format", "html", "--out", str(self.tmp / "o.html")],
                              capture_output=True, text=True, cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)})
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "", ""))
        self.assertEqual((self.tmp / "o.html").read_text(encoding="utf-8"), report.render(sr.fixture("hostile"), "html"))

    def test_the_report_command_is_listed_in_the_usage(self):
        code, out, err = self.render("--help")
        self.assertEqual(code, 0)
        self.assertIn("--format", out)


class Isolation(unittest.TestCase):
    def test_every_renderer_makes_no_network_call_and_reads_no_file_but_its_assets(self):
        """The summary, the Markdown, the page and the author view under a patched `open`, `Path.read_text`, `Path.read_bytes` and `os.open`:
        a read of anything but the three assets (`Path(__file__).read_text()` in one of them, say) fails here."""
        from pathlib import Path as P
        from reachmap.report import assets, summary
        opened = []
        real_io_open, real_read_text, real_read_bytes, real_os_open = io.open, P.read_text, P.read_bytes, os.open

        def spy_open(file, *a, **k):
            opened.append(str(file))
            return real_io_open(file, *a, **k)

        def spy_text(self, *a, **k):
            opened.append(str(self))
            return real_read_text(self, *a, **k)

        def spy_bytes(self, *a, **k):
            opened.append(str(self))
            return real_read_bytes(self, *a, **k)

        def spy_os_open(path, *a, **k):
            opened.append(str(path))
            return real_os_open(path, *a, **k)

        def no_network(*a, **k):
            raise AssertionError("a renderer called the network")

        data, failed = sr.hostile_run(), sr.fixture("failed")  # read before the spies are in
        assets.clear_cache()
        with mock.patch("socket.socket", no_network), mock.patch("socket.create_connection", no_network), mock.patch("socket.getaddrinfo", no_network), \
                mock.patch("builtins.open", spy_open), mock.patch("io.open", spy_open), mock.patch.object(P, "read_text", spy_text), \
                mock.patch.object(P, "read_bytes", spy_bytes), mock.patch("os.open", spy_os_open):
            for fmt in ("md", "html"):
                report.render(data, fmt)
                report.render(data, fmt, view="author")
            summary.lines(data, "x.json", {"md": "x.md"})
            summary.lines(failed, None)
        names = {Path(p).name for p in opened}
        self.assertEqual(names, {"report.html", "report.css", "report.js"})
        for p in opened:
            self.assertIn(os.path.join("reachmap", "report", "assets"), p)

    def test_the_renderer_modules_import_nothing_that_could_reach_out_and_open_no_file(self):
        banned = re.compile(r"^\s*(import|from)\s+(socket|ssl|urllib|http|ftplib|smtplib|subprocess|webbrowser|xmlrpc|requests|asyncio|shutil|tempfile)\b", re.M)
        for name in ("model", "layout", "blocks", "markdown", "html", "summary", "author", "text", "assets", "read"):
            src = (ROOT / "reachmap" / "report" / f"{name}.py").read_text(encoding="utf-8")
            with self.subTest(name):
                self.assertIsNone(banned.search(src))
                self.assertNotRegex(src, r"\bopen\(")
                self.assertNotRegex(src, r"os\.(system|popen|environ)")

    def test_the_renderers_take_the_json_report_only(self):
        """Not the model, the config, a repository or the environment: a report renders the same anywhere."""
        data = sr.fixture("complete")
        a = report.render(data, "html")
        with mock.patch.dict(os.environ, {"HOME": "/nonexistent", "REACHMAP_CACHE": "/nonexistent"}):
            cwd = os.getcwd()
            os.chdir("/")
            try:
                b = report.render(data, "html")
            finally:
                os.chdir(cwd)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
