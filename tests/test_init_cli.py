"""M17: `reachmap init` as a command: the file it writes always loads, it is written whole or not at all, it is only replaced with
--force, a repository that cannot be read is listed in the file and makes the exit code 3 (the others are still written), and no
secret reaches the output."""

import io
import os
import tempfile
import unittest
from pathlib import Path

from reachmap import cli, config
from tests import support_git as sg
from tests import support_init as si

WEB = {
    "package.json": '{\n  "name": "acme-web",\n  "scripts": {"test": "jest"},\n  "dependencies": {"react-router-dom": "6"}\n}\n',
    "src/routes.tsx": 'import { Route } from "react-router-dom";\nexport const r = <Route path="/a" element={<A />} />;\n',
    "src/api/endpoints.ts": 'export const A = "/api/orders";\nexport const B = (id: string) => `/api/orders/${id}`;\nexport const C = "/api/orders/open";\n',
    "src/services/orders.ts": 'import { A } from "../api/endpoints";\nexport const list = () => fetch(A);\n',
    "src/a.test.ts": "",
}
API = {"app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n", "tests/test_a.py": "def test_a():\n    assert True\n"}


class InitCommand(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="reachmap-init-cli-"))
        cls.addClassCleanup(sg.remove_tree, cls.root)
        cls.web = sg.make_repo(cls.root / "repos", "web", [{"files": WEB, "branch": "main"}])
        cls.api = sg.make_repo(cls.root / "repos", "api", [{"files": API}])
        cls.n = 0

    def run_init(self, *args, out="reachmap.toml", cwd=None):
        self.__class__.n += 1
        work = Path(cwd) if cwd else self.root / f"w{self.n}"
        work.mkdir(parents=True, exist_ok=True)
        stdout, stderr = io.StringIO(), io.StringIO()
        old = os.getcwd()
        os.chdir(work)
        try:
            code = cli.main(["init", *args, *(["--out", out] if out else [])], env={"REACHMAP_CACHE": str(self.root / "cache")}, stdout=stdout, stderr=stderr,
                            git_env=sg.isolated_env(self.root / "home"))
        finally:
            os.chdir(old)
        return code, stdout.getvalue(), stderr.getvalue(), work

    def test_it_writes_a_file_that_loads_and_says_how_many_guesses_to_confirm(self):
        code, out, err, work = self.run_init(f"acme/web={self.web}", f"acme/api={self.api}")
        self.assertEqual((code, err), (0, ""))
        self.assertRegex(out, r"wrote reachmap\.toml for 2 repositories: \d+ guesses to confirm")
        ws = config.load(work / "reachmap.toml")
        self.assertEqual(sorted(ws.repos), ["acme/api", "acme/web"])
        self.assertEqual([(e.kind, e.from_repo, e.to_repo) for e in ws.edges], [("http", "acme/web", "acme/api")])
        self.assertEqual(si.plain(ws.repos["acme/web"].tests), {"markers": [".test."], "command": "npx jest {paths}"})
        self.assertEqual(list(work.iterdir()), [work / "reachmap.toml"])  # no temporary file left

    def test_remotes_are_relative_to_the_file_not_to_the_working_directory(self):
        work = self.root / "elsewhere"
        code, _, _, _ = self.run_init(f"acme/web={self.web}", out=str(work / "conf" / "reachmap.toml"), cwd=self.root)
        self.assertEqual(code, 0)
        self.assertEqual(config.load(work / "conf" / "reachmap.toml").repos["acme/web"].remote, str(self.web))
        self.assertIn('remote = "../../repos/web"', (work / "conf" / "reachmap.toml").read_text())

    def test_an_existing_file_is_only_replaced_with_force_and_is_left_alone_otherwise(self):
        work = self.root / "force"
        work.mkdir()
        (work / "reachmap.toml").write_text("# mine\n")
        code, _, err, _ = self.run_init(f"acme/web={self.web}", cwd=work)
        self.assertEqual(code, 2)
        self.assertIn("use --force", err)
        self.assertEqual((work / "reachmap.toml").read_text(), "# mine\n")
        code, _, _, _ = self.run_init(f"acme/web={self.web}", "--force", cwd=work)
        self.assertEqual(code, 0)
        self.assertIn("[repos.", (work / "reachmap.toml").read_text())

    def test_usage_problems_are_exit_2_and_write_nothing(self):
        for args, why in (
            (("https://github.com/acme/web",), "git:<url>"),
            ((f"{self.web}", f"{self.web}"), "get the same name"),
            ((str(self.web), str(self.root / "other" / "web")), "get the same name"),
            (("github:onlyone",), "owner/name"),
        ):
            with self.subTest(args=args):
                code, _, err, work = self.run_init(*args)
                self.assertEqual(code, 2)
                self.assertIn(why, err)
                self.assertEqual(list(work.iterdir()), [])

    def test_no_spec_and_an_output_that_is_a_folder_are_usage_errors(self):
        self.assertEqual(self.run_init()[0], 2)
        work = self.root / "isdir"
        (work / "reachmap.toml").mkdir(parents=True)
        code, _, err, _ = self.run_init(str(self.web), cwd=work)
        self.assertEqual(code, 2)
        self.assertIn("is a folder", err)

    def test_a_repository_that_cannot_be_read_is_listed_and_the_exit_code_is_3_and_the_others_are_written(self):
        code, out, err, work = self.run_init(f"acme/web={self.web}", f"file://{self.root}/missing/repo", f"git:file://{self.root}/nowhere.git")
        self.assertEqual(code, 3)
        text = (work / "reachmap.toml").read_text()
        self.assertIn("# Could not read", text)
        self.assertIn(f"#   file://{self.root}/missing/repo: no such directory", text)
        self.assertIn("nowhere.git", text)
        self.assertEqual(list(config.load(work / "reachmap.toml").repos), ["acme/web"])
        self.assertEqual(err.count("could not read"), 2)

    def test_when_nothing_can_be_read_no_file_is_written_and_an_existing_one_is_untouched(self):
        work = self.root / "none"
        work.mkdir()
        (work / "reachmap.toml").write_text("# mine\n")
        code, _, err, _ = self.run_init(f"file://{self.root}/missing", "--force", cwd=work)
        self.assertEqual(code, 3)
        self.assertIn("no config was written", err)
        self.assertEqual((work / "reachmap.toml").read_text(), "# mine\n")
        self.assertEqual(sorted(p.name for p in work.iterdir()), ["reachmap.toml"])

    def test_without_out_the_text_goes_to_standard_output_and_the_summary_to_standard_error(self):
        """`reachmap init SPEC... > reachmap.toml` (context section 11) works as written."""
        code, out, err, work = self.run_init(f"acme/web={self.web}", out=None)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("# reachmap.toml, written by `reachmap init`."))
        self.assertRegex(err, r"^reachmap: wrote the config for 1 repository: \d+ guesses to confirm")
        self.assertEqual(list(work.iterdir()), [])
        self.assertEqual(config.loads(out, fmt="toml", env={}, base_dir=work).repos["acme/web"].remote, str(self.web))
        dash = self.run_init(f"acme/web={self.web}", out="-")  # `--out -` says the same
        self.assertEqual((dash[0], dash[1].startswith("# reachmap.toml, written")), (0, True))

    def test_a_file_that_could_not_be_read_is_listed_counted_on_stderr_and_makes_the_exit_code_3(self):
        files = dict(WEB) | {"src/link.ts": "x"}
        repo = sg.make_repo(self.root / "repos", "withlink", [{"files": files}])
        sg.git_raw(repo, "update-index", "--add", "--cacheinfo", "120000," + sg.git_raw(repo, "hash-object", "-w", "--stdin", input=b"a.ts") + ",src/sym.ts")
        sg.git_raw(repo, "commit", "-q", "-m", "link")
        code, out, err, work = self.run_init(f"acme/web={repo}", out="reachmap.toml")
        self.assertEqual(code, 3)
        self.assertIn("could not read acme/web:src/sym.ts", err)
        text = (work / "reachmap.toml").read_text()
        self.assertIn("# could not read src/sym.ts: not written to the checkout", text)
        self.assertRegex(out, r"1 file could not be read")
        config.load(work / "reachmap.toml")

    def test_a_credential_in_a_spec_is_in_no_output(self):
        secret = "s3cretvalue1234"
        code, out, err, work = self.run_init(f"acme/web={self.web}", f"git:https://user:{secret}@example.invalid/acme/api.git")
        self.assertEqual(code, 3)
        for text in (out, err, (work / "reachmap.toml").read_text()):
            self.assertNotIn(secret, text)

    def test_a_token_is_named_never_given(self):
        _, _, _, work = self.run_init(f"acme/web={self.web}")
        for line in (work / "reachmap.toml").read_text().splitlines():
            if "token_env" in line:
                self.assertTrue(line.startswith("#"))

    def test_the_command_is_in_the_help_and_a_second_run_gives_the_same_text(self):
        _, a, _, w1 = self.run_init(f"acme/web={self.web}", out="-")
        _, b, _, w2 = self.run_init(f"acme/web={self.web}", out="-")
        self.assertEqual(a.replace(str(w1), ""), b.replace(str(w2), ""))
        out = io.StringIO()
        self.assertEqual(cli.main(["init", "--help"], stdout=out), 0)
        self.assertIn("--force", out.getvalue())


if __name__ == "__main__":
    unittest.main()
