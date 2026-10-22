"""Hardening: an entry-point adapter that looked for its routing files and found none says so.

A React Router `routes_glob` (explicit, or the default `src/**/routes.{tsx,jsx,ts,js}`) that matched no file used to give an empty answer with no
record ("No areas reached", incompleteness []): an absence the tool made up from a configuration that points at nothing. It is now a blocking
`routes_unmatched` record (scope selection, subject `<repo>:<what was looked for>`) and an incomplete entry result, as OpenAPI's
`handlers_unmatched` does for its handler globs. The other adapters with a file glob or a search that can come back empty (Next.js, FastAPI,
Express, NestJS, Spring) say the same; Django already says it (`urlconf_root_unknown`).

What each test pins:

- the record exists, blocks, names the repository and what was looked for, and the result is incomplete;
- a routing file that exists makes no record, and a change with no code in it makes none (a docs-only change reaches nothing, and that is true);
- a tree or a search that could not be read is the git layer's incompleteness and never an unmatched pattern (rule 5: an absence is established);
- routing files that the change deleted or moved out of the glob are still a record, and it says they matched N files before the change and
  that the change deleted or moved them (naming the rename target): an empty, complete answer would hide the screens that moved.
"""

from __future__ import annotations

import unittest
from unittest import mock

from reachmap import entrypoints
from reachmap.adapters.express import ExpressAdapter, NestAdapter
from reachmap.adapters.fastapi import FastApiAdapter
from reachmap.adapters.js import JsAdapter
from reachmap.adapters.jvm import JvmAdapter
from reachmap.adapters.nextjs import NextJsAdapter
from reachmap.adapters.python import PyAdapter
from reachmap.adapters.react_router import ReactRouterAdapter
from reachmap.adapters.spring import SpringAdapter
from reachmap.incomplete import kind_spec
from reachmap.model import Read
from tests.support_universe import Base as UniverseBase
from tests.support_universe import f

KIND = "routes_unmatched"
TSCONFIG = '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}\n'


def ts(*lines: str) -> str:
    return f(list(lines))


def helper(ret: str = "1") -> str:
    return ts("export function help() {", f"  return {ret};", "}")


class Rig(UniverseBase):
    """One adapter on a small repository; `run(files, changed)` reaches from the change and `unmatched()` lists the records."""

    UNIVERSE_PREFIX = "reachmap-unmatched-"
    REPO = "t/r"

    def language(self):  # pragma: no cover - overridden
        raise NotImplementedError

    def adapter(self, language, **kw):  # pragma: no cover - overridden
        raise NotImplementedError

    def make_adapter(self, **kw):
        return self.language()

    def go(self, files, changed=None, *, deleted=(), adapter_kw=None, wrap=None):
        """`files` as the first commit, `changed` (and `deleted`) as the second; the entry result of the change."""
        self.repo({"files": files}, {"files": changed if changed is not None else {}, "delete": list(deleted)})
        self.lang = self.language()
        self.entry = self.adapter(self.lang, **(adapter_kw or {}))
        analysis = self.analyze(self.lang)
        if wrap is None:
            return entrypoints.reach(analysis, self.lang, self.entry)
        with wrap(self):
            return entrypoints.reach(analysis, self.lang, self.entry)

    def unmatched(self):
        return [(r.subject, r.blocks_selection, r.scope) for r in self.records if r.kind == KIND]

    def says_unmatched(self, res, what):
        """Exactly one blocking record about `what`, for this repository, and an incomplete result that says why."""
        got = self.unmatched()
        self.assertEqual(len(got), 1, [(r.kind, r.subject) for r in self.records])
        subject, blocks, scope = got[0]
        self.assertEqual((subject, blocks, scope), (f"{self.REPO}:{what}", True, "selection"))
        self.assertFalse(res.complete)
        self.assertEqual(res.reached, ())
        self.assertTrue(any(what in why for why in res.reasons), res.reasons)

    def says_nothing(self, res):
        self.assertEqual(self.unmatched(), [])
        self.assertTrue(res.complete, res.reasons)


class TheKind(unittest.TestCase):
    def test_the_kind_blocks_selection_and_cannot_be_made_non_blocking(self):
        spec = kind_spec(KIND)
        self.assertEqual((spec.blocks_selection, spec.always_blocking, spec.scope), (True, True, "selection"))
        self.assertIn("{subject}", spec.template)


class ReactRouter(Rig):
    def language(self):
        return JsAdapter(self.REPO, self.url, self.git, test_markers=["__tests__/", ".test.", ".spec."], aliases_from="tsconfig.json")

    def adapter(self, language, glob="src/modules/*/routes.tsx"):
        return ReactRouterAdapter(self.REPO, language, glob)

    FILES = {"tsconfig.json": TSCONFIG, "src/util.ts": helper(),
             "src/modules/m/routes.tsx": 'import P from "./P";\nexport const r = <Route path="/p" element={<P />} />;\n',
             "src/modules/m/P.tsx": 'import { help } from "../../util";\nexport default function P() {\n  return help();\n}\n'}
    CHANGE = {"src/util.ts": helper("2")}

    def test_an_explicit_glob_that_matches_no_file_is_a_blocking_record_and_an_incomplete_result(self):
        res = self.go(self.FILES, self.CHANGE, adapter_kw={"glob": "src/modules/*/rutes.tsx"})  # the typo that gave an empty answer
        self.says_unmatched(res, "src/modules/*/rutes.tsx")

    def test_the_default_globs_that_match_no_file_are_one_record_naming_the_default(self):
        files = {**self.FILES}
        files["app/routes.tsx"] = files.pop("src/modules/m/routes.tsx")  # outside src/
        files["app/P.tsx"] = files.pop("src/modules/m/P.tsx")
        res = self.go(files, self.CHANGE, adapter_kw={"glob": None})
        self.says_unmatched(res, "src/**/routes.{tsx,jsx,ts,js}")

    def test_a_default_glob_that_matches_is_no_record(self):
        files = {"tsconfig.json": TSCONFIG, "src/util.ts": helper(), "src/routes.tsx": self.FILES["src/modules/m/routes.tsx"],
                 "src/P.tsx": self.FILES["src/modules/m/P.tsx"].replace("../../util", "./util")}
        res = self.go(files, self.CHANGE, adapter_kw={"glob": None})
        self.says_nothing(res)
        self.assertEqual([r.area.label for r in res.reached], ["/p"])

    def test_a_glob_that_matches_is_no_record(self):
        res = self.go(self.FILES, self.CHANGE)
        self.says_nothing(res)
        self.assertEqual([r.area.label for r in res.reached], ["/p"])

    def test_in_a_list_only_the_pattern_that_matches_nothing_is_a_record(self):
        res = self.go(self.FILES, self.CHANGE, adapter_kw={"glob": ["src/modules/*/routes.tsx", "src/legacy/routes.tsx"]})
        self.assertEqual(self.unmatched(), [(f"{self.REPO}:src/legacy/routes.tsx", True, "selection")])
        self.assertFalse(res.complete)
        self.assertEqual([r.area.label for r in res.reached], ["/p"])  # what the other pattern found is still said

    def test_a_change_with_no_code_in_it_is_not_an_unmatched_glob(self):
        res = self.go(self.FILES, {"README.md": "docs\n"}, adapter_kw={"glob": "src/modules/*/rutes.tsx"})
        self.says_nothing(res)

    def detail(self):
        (rec,) = [r for r in self.records if r.kind == KIND]
        return rec.detail

    def test_routing_files_the_change_deleted_are_a_record_that_says_they_were_there(self):
        res = self.go(self.FILES, self.CHANGE, deleted=["src/modules/m/routes.tsx"])
        self.says_unmatched(res, "src/modules/*/routes.tsx")
        self.assertIn("matched 1 file before this change", self.detail())
        self.assertIn("deleted or moved", self.detail())
        self.assertIn("src/modules/m/routes.tsx deleted", self.detail())

    def test_routing_files_moved_out_of_the_glob_name_where_they_went(self):
        self.repo({"files": self.FILES}, {"files": {"src/util.ts": helper("2"), "src/screens/routes.tsx": self.FILES["src/modules/m/routes.tsx"]},
                                           "delete": ["src/modules/m/routes.tsx"]})
        self.lang = self.language()
        self.entry = self.adapter(self.lang)
        res = entrypoints.reach(self.analyze(self.lang), self.lang, self.entry)
        self.says_unmatched(res, "src/modules/*/routes.tsx")
        self.assertIn("matched 1 file before this change", self.detail())
        self.assertIn("src/modules/m/routes.tsx renamed to src/screens/routes.tsx", self.detail())
        self.assertNotIn("deleted", self.detail().replace("deleted or moved", ""))

    def test_two_files_that_matched_before_are_counted(self):
        files = {**self.FILES, "src/modules/n/routes.tsx": self.FILES["src/modules/m/routes.tsx"]}
        res = self.go(files, self.CHANGE, deleted=["src/modules/m/routes.tsx", "src/modules/n/routes.tsx"])
        self.says_unmatched(res, "src/modules/*/routes.tsx")
        self.assertIn("matched 2 files before this change", self.detail())

    def test_a_pattern_that_never_matched_says_so_and_not_that_files_moved(self):
        self.go(self.FILES, self.CHANGE, adapter_kw={"glob": "src/modules/*/rutes.tsx"})
        self.assertNotIn("before this change", self.detail())

    def test_a_tree_that_could_not_be_read_is_not_an_unmatched_glob(self):
        def no_tree(rig):
            return mock.patch.object(rig.lang, "tree", return_value=frozenset())

        res = self.go(self.FILES, self.CHANGE, wrap=no_tree)
        self.assertEqual(self.unmatched(), [])
        self.assertFalse(res.complete)  # not established: the result says so, and it is not an absence
        self.assertTrue(any("could not be listed" in why or "could not be read" in why for why in res.reasons), res.reasons)

    def test_the_record_is_made_once_however_often_the_result_is_asked_for(self):
        self.go(self.FILES, self.CHANGE, adapter_kw={"glob": "nope/routes.tsx"})
        entrypoints.reach(self.analysis, self.lang, self.entry)
        self.assertEqual(len(self.unmatched()), 1)


class NextJs(Rig):
    def language(self):
        return JsAdapter(self.REPO, self.url, self.git, test_markers=["__tests__/", ".test.", ".spec."], aliases_from="tsconfig.json")

    def adapter(self, language, **kw):
        return NextJsAdapter(self.REPO, language, **kw)

    PAGE = 'import { help } from "@/util";\nexport default function Page() {\n  return help();\n}\n'
    BASE = {"tsconfig.json": TSCONFIG, "src/util.ts": helper()}
    CHANGE = {"src/util.ts": helper("2")}

    def test_no_app_or_pages_folder_is_a_blocking_record(self):
        res = self.go({**self.BASE, "lib/page.tsx": self.PAGE}, self.CHANGE)
        self.says_unmatched(res, "{app,pages}")

    def test_the_folder_of_a_root_option_is_named(self):
        res = self.go({**self.BASE, "app/page.tsx": self.PAGE}, self.CHANGE, adapter_kw={"root": "apps/web"})
        self.says_unmatched(res, "apps/web/{app,pages}")

    def test_a_folder_that_holds_no_routing_file_is_the_same_record(self):
        res = self.go({**self.BASE, "app/README.md": "x\n"}, self.CHANGE)
        self.says_unmatched(res, "{app,pages}")

    def test_a_routing_folder_is_no_record(self):
        res = self.go({**self.BASE, "app/page.tsx": self.PAGE}, self.CHANGE)
        self.says_nothing(res)
        self.assertEqual([r.area.label for r in res.reached], ["/"])

    def test_the_src_folder_is_looked_in_too(self):
        res = self.go({**self.BASE, "src/pages/index.tsx": self.PAGE}, self.CHANGE)
        self.says_nothing(res)

    def test_no_code_change_no_record(self):
        res = self.go({**self.BASE, "lib/page.tsx": self.PAGE}, {"README.md": "x\n"})
        self.says_nothing(res)

    def test_a_tree_that_could_not_be_read_is_not_an_unmatched_folder(self):
        res = self.go({**self.BASE, "app/page.tsx": self.PAGE}, self.CHANGE, wrap=lambda rig: mock.patch.object(rig.lang, "tree", return_value=frozenset()))
        self.assertEqual(self.unmatched(), [])
        self.assertFalse(res.complete)


class PyRig(Rig):
    def language(self):
        return PyAdapter(self.REPO, self.url, self.git, test_markers=["tests/", "test_"])

    UTIL = "def help():\n    return 1\n"
    CHANGE = {"app/util.py": "def help():\n    return 2\n"}

    def unreadable_search(self, rig):
        res = mock.Mock(state=Read.UNREADABLE, detail="boom", truncated_by=None, not_searched=(), partial_matches=())
        return mock.patch.object(rig.lang, "search", return_value=res)


class FastApi(PyRig):
    def adapter(self, language, **kw):
        return FastApiAdapter(self.REPO, language)

    ROUTER = 'from fastapi import APIRouter\nfrom app.util import help\n\nrouter = APIRouter()\n\n\n@router.get("/a")\ndef a():\n    return help()\n'
    MAIN = "from fastapi import FastAPI\nfrom app.api import router\n\napp = FastAPI()\napp.include_router(router)\n"

    def test_no_file_that_declares_an_app_or_router_is_a_blocking_record(self):
        res = self.go({"app/__init__.py": "", "app/util.py": self.UTIL, "app/other.py": "x = 1\n"}, self.CHANGE)
        self.says_unmatched(res, "FastAPI or APIRouter")

    def test_a_repository_with_routes_is_no_record(self):
        res = self.go({"app/__init__.py": "", "app/util.py": self.UTIL, "app/api.py": self.ROUTER, "app/main.py": self.MAIN}, self.CHANGE)
        self.says_nothing(res)
        self.assertEqual([r.area.label for r in res.reached], ["GET /a"])

    def test_a_search_that_failed_is_not_an_unmatched_search(self):
        res = self.go({"app/__init__.py": "", "app/util.py": self.UTIL}, self.CHANGE, wrap=self.unreadable_search)
        self.assertEqual(self.unmatched(), [])
        self.assertFalse(res.complete)

    def test_no_code_change_no_record(self):
        res = self.go({"app/__init__.py": "", "app/util.py": self.UTIL}, {"README.md": "x\n"})
        self.says_nothing(res)

    def test_routes_the_change_deleted_are_a_record_that_says_they_were_there(self):
        files = {"app/__init__.py": "", "app/util.py": self.UTIL, "app/api.py": self.ROUTER, "app/main.py": self.MAIN}
        res = self.go(files, self.CHANGE, deleted=["app/api.py", "app/main.py"])
        self.assertEqual([r[0] for r in self.unmatched()], [f"{self.REPO}:FastAPI or APIRouter"])
        self.assertFalse(res.complete)  # (the walk still reaches what the deleted files held at the base: that is not this record's business)
        (rec,) = [r for r in self.records if r.kind == KIND]
        self.assertIn("it matched 2 files before this change", rec.detail)


class JsServiceRig(Rig):
    def language(self):
        return JsAdapter(self.REPO, self.url, self.git, test_markers=["__tests__/", ".test.", ".spec."], aliases_from="tsconfig.json")

    CHANGE = {"src/util.ts": helper("2")}

    def unreadable_search(self, rig):
        res = mock.Mock(state=Read.UNREADABLE, detail="boom", truncated_by=None, not_searched=(), partial_matches=())
        return mock.patch.object(rig.lang, "search", return_value=res)


class Express(JsServiceRig):
    def adapter(self, language, **kw):
        return ExpressAdapter(self.REPO, language, **kw)

    APP = 'import express from "express";\nimport { help } from "./util";\n\nconst app = express();\napp.get("/a", (req, res) => res.json(help()));\nexport default app;\n'
    BASE = {"tsconfig.json": TSCONFIG, "src/util.ts": helper()}

    def test_no_file_that_declares_an_app_or_router_is_a_blocking_record(self):
        res = self.go({**self.BASE, "src/other.ts": "export const x = 1;\n"}, self.CHANGE)
        self.says_unmatched(res, "express() or Router()")

    def test_a_repository_with_routes_is_no_record(self):
        res = self.go({**self.BASE, "src/app.ts": self.APP}, self.CHANGE)
        self.says_nothing(res)
        self.assertEqual(len(res.reached), 1)

    def test_a_search_that_failed_is_not_an_unmatched_search(self):
        res = self.go(self.BASE, self.CHANGE, wrap=self.unreadable_search)
        self.assertEqual(self.unmatched(), [])
        self.assertFalse(res.complete)

    def test_no_code_change_no_record(self):
        res = self.go(self.BASE, {"README.md": "x\n"})
        self.says_nothing(res)


class Nest(JsServiceRig):
    def adapter(self, language, **kw):
        return NestAdapter(self.REPO, language, **kw)

    COMMON = 'import { Controller, Get, Module } from "@nestjs/common";\nimport { help } from "./util";\n'
    CTRL = COMMON + '\n@Controller("items")\nexport class ItemsController {\n  @Get()\n  list() {\n    return help();\n  }\n}\n'
    MODULE = 'import { Module } from "@nestjs/common";\nimport { ItemsController } from "./items.controller";\n\n@Module({ controllers: [ItemsController] })\nexport class ItemsModule {}\n'
    MAIN = 'import { NestFactory } from "@nestjs/core";\nimport { ItemsModule } from "./items.module";\n\nasync function main() {\n  const app = await NestFactory.create(ItemsModule);\n  await app.listen(3000);\n}\nmain();\n'
    BASE = {"tsconfig.json": TSCONFIG, "src/util.ts": helper()}

    def test_no_controller_or_module_is_a_blocking_record(self):
        res = self.go({**self.BASE, "src/other.ts": "export const x = 1;\n"}, self.CHANGE)
        self.says_unmatched(res, "@Controller or @Module")

    def test_a_repository_with_a_controller_is_no_record(self):
        files = {**self.BASE, "src/items.controller.ts": self.CTRL, "src/items.module.ts": self.MODULE, "src/main.ts": self.MAIN}
        res = self.go(files, self.CHANGE)
        self.says_nothing(res)

    def test_a_search_that_failed_is_not_an_unmatched_search(self):
        res = self.go(self.BASE, self.CHANGE, wrap=self.unreadable_search)
        self.assertEqual(self.unmatched(), [])
        self.assertFalse(res.complete)


class Spring(Rig):
    def language(self):
        return JvmAdapter(self.REPO, self.url, self.git, test_markers=["src/test/"])

    def adapter(self, language, **kw):
        return SpringAdapter(self.REPO, language, **kw)

    SERVICE = "src/main/java/com/acme/Service.java"
    CONTROLLER = "src/main/java/com/acme/InvoiceController.java"
    SVC = "package com.acme;\n\npublic class Service {\n    public int help() {\n        return 1;\n    }\n}\n"
    CTRL = ("package com.acme;\n\nimport org.springframework.web.bind.annotation.*;\n\n@RestController\n@RequestMapping(\"/invoices\")\n"
            "public class InvoiceController {\n    private final Service service = new Service();\n\n    @GetMapping\n    public int list() {\n        return service.help();\n    }\n}\n")
    CHANGE = {SERVICE: SVC.replace("return 1", "return 2")}

    def test_no_controller_is_a_blocking_record(self):
        res = self.go({self.SERVICE: self.SVC}, self.CHANGE)
        self.says_unmatched(res, "@Controller or a request mapping")

    def test_a_repository_with_a_controller_is_no_record(self):
        res = self.go({self.SERVICE: self.SVC, self.CONTROLLER: self.CTRL}, self.CHANGE)
        self.says_nothing(res)
        self.assertEqual([r.area.label for r in res.reached], ["GET /invoices"])

    def test_a_search_that_failed_is_not_an_unmatched_search(self):
        def unreadable(rig):
            res = mock.Mock(state=Read.UNREADABLE, detail="boom", truncated_by=None, not_searched=(), partial_matches=())
            return mock.patch.object(rig.lang, "search", return_value=res)

        res = self.go({self.SERVICE: self.SVC}, self.CHANGE, wrap=unreadable)
        self.assertEqual(self.unmatched(), [])
        self.assertFalse(res.complete)

    def test_no_code_change_no_record(self):
        res = self.go({self.SERVICE: self.SVC}, {"README.md": "x\n"})
        self.says_nothing(res)


class Django(Rig):
    """Django already said it: no literal ROOT_URLCONF that names a module is the blocking `urlconf_root_unknown`, not an empty answer. Pinned here
    with the others, so the check of every adapter for this shape stays visible."""

    def language(self):
        return PyAdapter(self.REPO, self.url, self.git, test_markers=["tests/", "test_"], framework="django")

    def adapter(self, language, **kw):
        from reachmap.adapters.django import DjangoAdapter

        return DjangoAdapter(self.REPO, language, **kw)

    def test_no_settings_with_a_root_urlconf_is_a_blocking_record_and_an_incomplete_result(self):
        res = self.go({"shop/__init__.py": "", "shop/util.py": "def help():\n    return 1\n"}, {"shop/util.py": "def help():\n    return 2\n"})
        self.assertEqual([(r.kind, r.blocks_selection) for r in self.records if r.kind == "urlconf_root_unknown"], [("urlconf_root_unknown", True)])
        self.assertFalse(res.complete)
        self.assertEqual(res.reached, ())

    def test_a_settings_option_that_matches_no_file_says_the_same(self):
        files = {"config/__init__.py": "", "config/settings.py": 'ROOT_URLCONF = "config.urls"\n', "config/urls.py": "urlpatterns = []\n", "shop/util.py": "def help():\n    return 1\n"}
        res = self.go(files, {"shop/util.py": "def help():\n    return 2\n"}, adapter_kw={"settings": "nowhere/settings.py"})
        self.assertEqual([r.kind for r in self.records if r.kind == "urlconf_root_unknown"], ["urlconf_root_unknown"])
        self.assertFalse(res.complete)


if __name__ == "__main__":
    unittest.main()
