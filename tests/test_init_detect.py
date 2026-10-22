"""M17: each detector of `reachmap init` on small in-memory repositories: what it finds with its evidence, what it leaves alone
(generic decoys: a `routes.ts` that is no router, a `models/` folder with no BaseModel, `<Routes>`, a comment, a stale jsconfig.json),
and what it says when it cannot tell."""

import json
import unittest

from reachmap import config
from reachmap.init import registry
from reachmap.init.detect import detect_language
from reachmap.init.entries import routes_glob
from reachmap.init.guess import Note, SpecError
from reachmap.init.contract import tag_prefix
from tests import support_init as si

ROUTE = '<Route path="/{p}" element={{<Page />}} />'


def routes_file(path="/a"):
    return f'import {{ Route }} from "react-router-dom";\nexport const r = (\n  <>\n    <Route path="{path}" element={{<Page />}} />\n  </>\n);\n'


def sections(g):
    return {k: v.value for k, v in g.sections.items()}


class Language(unittest.TestCase):
    def test_the_language_with_most_files_wins_and_vendored_folders_do_not_count(self):
        files = {f"src/a{i}.ts": "" for i in range(3)} | {f"node_modules/x/b{i}.js": "" for i in range(50)} | {"dist/c.js": "", "vendor/d.js": "", "src/e.d.ts": ""}
        g = si.guess(files)
        self.assertEqual(g.language.value, "typescript")
        self.assertEqual(g.language.notes[0].evidence, ("src/a0.ts:1",))

    def test_typescript_wins_a_tie_and_a_minority_language_is_said(self):
        g = si.guess({"a.ts": "", "b.js": ""})
        self.assertEqual(g.language.value, "typescript")
        self.assertTrue(any("also holds 1 javascript file;" in n for n in g.notes))

    def test_a_repository_with_no_supported_language_is_an_error_naming_what_was_found(self):
        with self.assertRaises(SpecError) as cm:
            detect_language(si.tree({"main.go": "", "x.go": "", "README.md": ""}))
        self.assertIn("no source file in a language", cm.exception.reason)
        self.assertIn("2 go", cm.exception.reason)


class Branches(unittest.TestCase):
    def test_release_patterns_come_from_existing_heads_only(self):
        g = si.guess({"a.py": ""}, heads=("main", "release/1.0", "release/1.1", "hotfix/x", "release-2", "feature/a"), branch="main")
        self.assertEqual(g.branches.value, ["main", "release/*", "release-*", "hotfix/*"])
        self.assertTrue(all(n.confidence in ("medium", "low") for n in g.branches.notes))

    def test_no_release_heads_means_the_default_branch_alone_and_no_guess(self):
        g = si.guess({"a.py": ""}, heads=("main", "scenario/s1", "release"), branch="main")
        self.assertEqual((g.branches.value, g.branches.notes), (["main"], ()))

    def test_a_default_branch_that_head_did_not_name_is_a_low_confidence_guess(self):
        g = si.guess({"a.py": ""}, heads=("trunk",), branch="trunk", branch_note="the remote's HEAD is not a symbolic ref, so trunk was taken as the default branch")
        self.assertEqual(g.branches.notes[0].confidence, "low")


class Aliases(unittest.TestCase):
    TS = '{\n  // comment\n  "compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"],},},\n}\n'

    def test_tsconfig_with_jsonc_is_read_and_the_stale_jsconfig_is_not_used(self):
        js = '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["app/*"]}}}'
        g = si.guess({"src/a.ts": "", "tsconfig.json": self.TS, "jsconfig.json": js})
        self.assertEqual(sections(g)["aliases"], {"from": "tsconfig.json"})
        why = [n.why for n in g.sections["aliases"].notes]
        self.assertTrue(any("every one of which exists" in w for w in why))
        self.assertTrue(any("jsconfig.json is not used: 1 of its 1 path targets do not exist" in w for w in why))

    def test_only_a_stale_config_is_still_named_but_with_low_confidence(self):
        g = si.guess({"src/a.ts": "", "jsconfig.json": '{"compilerOptions": {"paths": {"@/*": ["app/*"]}}}'})
        self.assertEqual(sections(g)["aliases"], {"from": "jsconfig.json"})
        self.assertEqual(g.sections["aliases"].notes[0].confidence, "low")

    def test_extends_is_followed_to_a_relative_base(self):
        g = si.guess({"src/a.ts": "", "tsconfig.json": '{"extends": "./tsconfig.base.json"}', "tsconfig.base.json": self.TS})
        self.assertEqual(sections(g)["aliases"], {"from": "tsconfig.json"})
        self.assertIn("tsconfig.base.json", g.sections["aliases"].notes[0].evidence[0])

    def test_extends_a_package_is_said_not_guessed(self):
        g = si.guess({"src/a.ts": "", "tsconfig.json": '{"extends": "@acme/tsconfig/base.json"}'})
        self.assertIn("not in the repository", g.sections["aliases"].notes[0].why)

    def test_a_config_with_nothing_to_alias_writes_no_aliases(self):
        self.assertNotIn("aliases", si.guess({"src/a.ts": "", "tsconfig.json": '{"compilerOptions": {"strict": true}}'}).sections)

    def test_a_config_that_does_not_parse_is_named_and_listed_unread(self):
        g = si.guess({"src/a.ts": "", "tsconfig.json": '{"compilerOptions": '})
        self.assertEqual(sections(g)["aliases"], {"from": "tsconfig.json"})
        self.assertEqual(g.sections["aliases"].notes[0].confidence, "low")
        self.assertIn("tsconfig.json", dict(g.unread))

    def test_a_build_tool_alias_is_said_never_pointed_at(self):
        g = si.guess({"src/a.ts": "", "tsconfig.json": self.TS, "vite.config.ts": "export default { resolve: { alias: { '@': '/src' } } };\n"})
        self.assertEqual(sections(g)["aliases"], {"from": "tsconfig.json"})
        self.assertTrue(any("vite.config.ts also sets aliases" in n.why for n in g.sections["aliases"].notes))

    def test_a_build_tool_alias_with_no_tsconfig_or_jsconfig_is_still_said(self):
        g = si.guess({"src/a.ts": "", "vite.config.ts": "export default { resolve: { alias: { '@': '/src' } } };\n"})
        self.assertNotIn("aliases", g.sections)
        self.assertTrue(any("vite.config.ts:1 sets aliases" in n and "add them there" in n for n in g.notes), g.notes)

    def test_python_repositories_have_no_aliases(self):
        self.assertNotIn("aliases", si.guess({"a.py": "", "tsconfig.json": self.TS}).sections)


class ReactRouter(unittest.TestCase):
    def kinds(self, files):
        g = si.guess({"src/main.tsx": "", **files})
        return g.kind_of("entry_points"), sections(g).get("entry_points")

    def test_route_elements_are_found_and_the_glob_generalises_over_one_folder_name(self):
        files = {f"src/modules/{m}/routes.tsx": routes_file(f"/{m}") for m in ("a", "b", "c")} | {"src/modules/d/page.tsx": "export const x = 1;\n"}
        kind, table = self.kinds(files)
        self.assertEqual((kind, table["routes_glob"]), ("react-router", "src/modules/*/routes.tsx"))

    def test_a_glob_that_would_match_a_file_that_is_no_router_is_not_used(self):
        files = {f"src/modules/{m}/routes.tsx": routes_file(f"/{m}") for m in ("a", "b")} | {"src/modules/c/routes.tsx": "export const x = 1;\n"}
        self.assertEqual(self.kinds(files)[1]["routes_glob"], "{src/modules/a/routes.tsx,src/modules/b/routes.tsx}")

    def test_one_routes_file_is_itself(self):
        self.assertEqual(self.kinds({"src/routes.tsx": routes_file()})[1]["routes_glob"], "src/routes.tsx")

    def test_object_routes_through_createbrowserrouter_count(self):
        src = 'import { createBrowserRouter } from "react-router-dom";\nexport const r = createBrowserRouter([\n  { path: "/", element: null },\n]);\n'
        self.assertEqual(self.kinds({"src/router.tsx": src})[0], "react-router")

    def test_routes_with_a_capital_s_a_comment_a_test_and_a_plain_routes_file_are_not_routes(self):
        files = {
            "src/App.tsx": 'import { Routes } from "react-router-dom";\nexport const App = () => <Routes>{x}</Routes>;\n',
            "src/commented.tsx": '// <Route path="/old" element={<Old />} />\nexport const y = 1;\n',
            "src/__tests__/r.test.tsx": routes_file("/t"),
            "src/routes.ts": 'export const routes = { home: "/", about: "/about" };\n',
            "src/lib/routes.js": "module.exports = { a: 1 };\n",
        }
        self.assertEqual(self.kinds(files), (None, None))

    def test_another_routers_route_elements_and_look_alikes_are_not_react_router(self):
        solid = 'import { Route } from "@solidjs/router";\nexport const r = <Route path="/a" element={<A />} />;\n'
        files = {
            "src/solid.tsx": solid,
            "src/local.ts": "const routes = useRoutes([{ path: '/a' }]);\n",  # a local helper with the name, no react-router in sight
            "src/sample.ts": 'export const SAMPLE = `<Route path="/x" element={<X />} />`;\n',
        }
        g = si.guess({"src/main.tsx": "", **files})
        self.assertNotIn("entry_points", g.sections)
        self.assertTrue(any("imports another router" in n and "src/solid.tsx:2" in n for n in g.notes), g.notes)

    def test_a_route_element_with_no_import_is_read_with_a_low_confidence_and_says_so(self):
        g = si.guess({"src/routes.tsx": '<Route path="/a" element={<A />} />\n'})
        self.assertEqual(g.kind_of("entry_points"), "react-router")
        self.assertEqual(g.sections["entry_points"].notes[0].confidence, "low")
        self.assertIn("no react-router dependency and no import", g.sections["entry_points"].notes[1].why)

    def test_a_route_tag_with_no_routing_attribute_is_not_a_route(self):
        self.assertEqual(self.kinds({"src/x.tsx": "const a = <Route>hello</Route>;\n"})[0], None)

    def test_the_dependency_makes_it_high_confidence(self):
        g = si.guess({"src/routes.tsx": routes_file(), "package.json": '{\n "dependencies": {\n  "react-router-dom": "^6"\n }\n}\n'})
        self.assertEqual(g.sections["entry_points"].notes[0].confidence, "high")
        self.assertIn("package.json:3", g.sections["entry_points"].notes[-1].evidence)

    def test_glob_helper_fallbacks(self):
        files = tuple(f"a/{i}/x/routes.tsx" for i in range(3))
        self.assertEqual(routes_glob(list(files), files)[0], "a/*/x/routes.tsx")
        many = tuple(f"app{i}/sub/routes.tsx" for i in range(14)) + ("zz/routes.tsx",)
        self.assertEqual(routes_glob(list(many[:14]), many)[0], "*/sub/routes.tsx")  # more than a dozen files: still one glob when it matches them all and nothing else
        self.assertIsNone(routes_glob(list(many[:13]), many)[0])  # 13 of the 14 files that match: no brace list that long, and no glob that matches another
        self.assertIsNone(routes_glob(["a*b/routes.tsx"], ("a*b/routes.tsx",))[0])


class EndpointsFile(unittest.TestCase):
    ENDPOINTS = 'export const ORDERS_URL = "/api/orders";\nexport const orderUrl = (id: string) => `/api/orders/${id}`;\nexport const HEALTH =\n  "/api/health";\n'

    def client(self, files):
        return sections(si.guess({"src/main.ts": "", **files})).get("http_client")

    def test_the_module_with_the_most_url_constants_is_the_endpoints_file(self):
        c = self.client({"src/api/endpoints.ts": self.ENDPOINTS, "src/api/other.ts": 'export const A = "/a";\nexport const B = "/b";\n'})
        self.assertEqual(c, {"kind": "endpoints-file", "path": "src/api/endpoints.ts"})

    def test_a_navigation_table_of_routes_is_not_one(self):
        self.assertIsNone(self.client({"src/nav/routes.ts": 'export const HOME = "/home";\nexport const ABOUT = "/about";\nexport const X = "/x";\n'}))

    def test_a_file_that_declares_react_router_routes_is_not_one(self):
        self.assertIsNone(self.client({"src/r.tsx": self.ENDPOINTS + "const a = <Route path=\"/a\" element={<A />} />;\n"}))

    def test_constants_nothing_calls_with_no_api_looking_path_are_said_not_taken(self):
        files = {"src/urls.ts": 'export const A = "/orders";\nexport const B = "/customers";\nexport const C = "/items";\n'}
        g = si.guess({"src/main.ts": "", **files})
        self.assertNotIn("http_client", g.sections)
        self.assertTrue(any("no file makes an HTTP call with them" in n for n in g.notes), g.notes)

    def test_a_call_site_makes_constants_a_client_even_without_an_api_path(self):
        files = {"src/urls.ts": 'export const A = "/orders";\nexport const B = "/customers";\nexport const C = "/items";\n',
                 "src/svc.ts": 'import { A } from "./urls";\nexport const get = () => fetch(A);\n'}
        g = si.guess({"src/main.ts": "", **files})
        self.assertEqual(sections(g)["http_client"], {"kind": "endpoints-file", "path": "src/urls.ts"})
        self.assertIn("src/svc.ts:2", g.sections["http_client"].notes[0].evidence)

    def test_two_constants_with_no_call_are_not_enough_even_with_an_api_path(self):
        g = si.guess({"src/main.ts": "", "src/e.ts": 'export const A = "/api/a";\nexport const B = "/api/b";\n'})
        self.assertNotIn("http_client", g.sections)

    def test_assets_and_a_single_constant_are_not_enough(self):
        self.assertIsNone(self.client({"src/a.ts": 'export const LOGO = "/img/logo.png";\nexport const ICON = "/img/i.svg";\n'}))
        self.assertIsNone(self.client({"src/a.ts": 'export const ONE = "/api/one";\n'}))

    def test_urls_are_kept_for_the_http_edge(self):
        g = si.guess({"src/api/endpoints.ts": self.ENDPOINTS})
        self.assertIn("/api/orders", g.facts.client_paths)
        self.assertEqual(g.facts.client_evidence, "src/api/endpoints.ts:1")


class NextJs(unittest.TestCase):
    PKG = '{\n "dependencies": {\n  "next": "14.0.0"\n }\n}\n'

    def test_app_and_pages_routers_with_next_as_a_dependency(self):
        g = si.guess({"package.json": self.PKG, "src/app/page.tsx": "", "src/app/api/orders/route.ts": "", "src/app/orders/[id]/page.tsx": ""})
        self.assertEqual((sections(g)["entry_points"], g.roles.value), ({"kind": "nextjs"}, ["frontend", "backend"]))
        self.assertIn("/api/orders", g.facts.route_hints)

    def test_the_url_hints_are_the_adapters_own_routing_table(self):
        g = si.guess({"package.json": self.PKG, "app/(shop)/page.tsx": "", "app/_private/api/route.ts": "", "app/api/(g)/items/[id]/route.ts": ""})
        self.assertEqual(g.facts.route_hints, ["/api/items/{id}"])

    def test_a_frontend_only_project_has_no_backend_role(self):
        g = si.guess({"package.json": self.PKG, "src/app/page.tsx": ""})
        self.assertEqual(g.roles.value, ["frontend"])

    def test_a_nested_root_and_a_base_path(self):
        g = si.guess({"apps/web/package.json": self.PKG, "apps/web/pages/index.tsx": "", "apps/web/next.config.js": "module.exports = {\n  basePath: '/docs',\n};\n",
                      "apps/web/tsconfig.json": '{"compilerOptions": {"paths": {"@/*": ["./*"]}}}'})
        self.assertEqual(sections(g)["entry_points"], {"kind": "nextjs", "root": "apps/web", "base_path": "/docs"})
        self.assertEqual(sections(g)["aliases"], {"from": "apps/web/tsconfig.json"})

    def test_next_without_routing_folders_and_folders_without_next_are_nothing(self):
        self.assertNotIn("entry_points", si.guess({"package.json": self.PKG, "src/index.ts": ""}).sections)
        self.assertNotIn("entry_points", si.guess({"package.json": '{"dependencies": {"react": "1"}}', "src/app/page.tsx": ""}).sections)

    def test_two_projects_say_one_root_only_fits(self):
        g = si.guess({"a/package.json": self.PKG, "a/app/page.tsx": "", "b/package.json": self.PKG, "b/app/page.tsx": "", "b/app/x/page.tsx": ""})
        self.assertEqual(sections(g)["entry_points"]["root"], "b")
        self.assertTrue(any("2 Next.js projects" in n for n in g.notes))


class FastApi(unittest.TestCase):
    MAIN = "from fastapi import FastAPI\n\napp = FastAPI()\napp.include_router(r, prefix='/billing')\n"

    def test_an_app_and_routers_in_files_that_import_fastapi(self):
        g = si.guess({"app/main.py": self.MAIN, "app/routers/a.py": 'from fastapi import APIRouter\nrouter = APIRouter(prefix="/orders")\n@router.get("/{id}")\ndef g(id): ...\n'})
        self.assertEqual((sections(g)["entry_points"], g.roles.value), ({"kind": "fastapi"}, ["backend"]))
        self.assertEqual(g.sections["entry_points"].notes[0].evidence, ("app/main.py:3",))
        self.assertIn("billing", g.facts.route_hints)
        self.assertIn("orders", g.facts.route_hints)

    def test_decoys_a_docstring_a_wrapper_class_a_file_that_does_not_import_fastapi_and_tests(self):
        files = {
            "a.py": '"""Uses FastAPI() to serve."""\nx = 1\n',
            "b.py": "class FastAPIWrapper:\n    pass\n",
            "c.py": "app = FastAPI()\n",
            "tests/test_d.py": "from fastapi import FastAPI\napp = FastAPI()\n",
        }
        self.assertNotIn("entry_points", si.guess(files).sections)

    def test_a_comment_line_is_not_code(self):
        self.assertNotIn("entry_points", si.guess({"a.py": "from fastapi import FastAPI\n# app = FastAPI()\n"}).sections)


class Django(unittest.TestCase):
    URLS = "from django.urls import path\nurlpatterns = [\n    path('billing/', include('b.urls')),\n]\n"

    def test_urlpatterns_and_a_root_urlconf(self):
        g = si.guess({"config/urls.py": self.URLS, "config/settings.py": 'ROOT_URLCONF = "config.urls"\n', "billing/__init__.py": ""})
        self.assertEqual(sections(g)["entry_points"], {"kind": "django"})
        self.assertEqual(g.sections["entry_points"].notes[0].confidence, "high")
        self.assertIn("billing", g.facts.route_hints)

    def test_manage_py_alone_is_medium_confidence(self):
        g = si.guess({"a/urls.py": self.URLS, "manage.py": 'os.environ.setdefault("DJANGO_SETTINGS_MODULE", "x.settings")\n'})
        self.assertEqual(g.sections["entry_points"].notes[0].confidence, "medium")

    def test_a_urls_module_without_urlpatterns_or_without_django_is_not_a_urlconf(self):
        self.assertNotIn("entry_points", si.guess({"a/urls.py": 'HOME = "/"\n', "s.py": 'ROOT_URLCONF = "a.urls"\n'}).sections)
        self.assertNotIn("entry_points", si.guess({"a/urls.py": "urlpatterns = []\n", "s.py": 'ROOT_URLCONF = "a.urls"\n'}).sections)


class Celery(unittest.TestCase):
    def test_tasks_decorated_through_an_app_made_in_another_file(self):
        g = si.guess({"w/app.py": 'from celery import Celery\ncelery_app = Celery("w")\n', "w/tasks.py": "from w.app import celery_app\n\n@celery_app.task\ndef t(): ...\n"})
        self.assertEqual(sections(g)["entry_points"], {"kind": "celery"})
        self.assertIn("not followed yet", g.sections["entry_points"].notes[0].why)

    def test_shared_task(self):
        self.assertEqual(si.guess({"t.py": "from celery import shared_task\n\n@shared_task\ndef t(): ...\n"}).kind_of("entry_points"), "celery")

    def test_a_task_decorator_of_something_else_is_not_celery(self):
        self.assertNotIn("entry_points", si.guess({"t.py": "import pytest\n\n@pytest.mark.task\ndef t(): ...\n\n@app.task\ndef u(): ...\n"}).sections)

    def test_a_server_beats_celery_in_one_repository_and_the_other_is_said(self):
        g = si.guess({"m.py": "from fastapi import FastAPI\napp = FastAPI()\n", "t.py": "from celery import shared_task\n\n@shared_task\ndef t(): ...\n"})
        self.assertEqual(g.kind_of("entry_points"), "fastapi")
        self.assertTrue(any("also found: celery" in n for n in g.notes))

    def test_a_topics_module_is_remembered_for_an_event_edge(self):
        g = si.guess({"t.py": "from celery import shared_task\n\n@shared_task\ndef t(): ...\n", "w/topics.py": 'A = "billing.a.sync"\nB = "billing.b.sync"\n'})
        self.assertEqual(g.facts.topics, "w/topics.py:1")
        self.assertIsNone(si.guess({"t.py": "from celery import shared_task\n\n@shared_task\ndef t(): ...\n", "w/topics.py": 'A = "one"\n'}).facts.topics)


class ExpressAndNest(unittest.TestCase):
    def test_express_apps_and_routers_by_import(self):
        g = si.guess({"package.json": '{"dependencies": {"express": "4"}}', "src/app.ts": 'import express from "express";\nconst app = express();\napp.get("/health", h);\n',
                      "src/r.js": 'const express = require("express");\nconst router = express.Router();\nrouter.get("/users", h);\n'})
        self.assertEqual((sections(g)["entry_points"], g.roles.value), ({"kind": "express"}, ["backend"]))
        self.assertIn("health", g.facts.route_hints)

    def test_a_router_of_the_repository_called_express_is_not_express(self):
        self.assertNotIn("entry_points", si.guess({"src/a.ts": 'import { express } from "./lib";\nconst app = express();\n', "src/lib.ts": "export const express = () => 1;\n"}).sections)

    def test_nest_controllers_from_nestjs_common_only(self):
        ctl = 'import { Controller, Get } from "@nestjs/common";\n\n@Controller(PATH)\nexport class A {\n  @Get("ledger")\n  list() {}\n}\n'
        g = si.guess({"package.json": '{"dependencies": {"@nestjs/core": "10"}}', "src/a.controller.ts": ctl, "src/c.ts": 'export const PATH = "entries";\n'})
        self.assertEqual(sections(g)["entry_points"], {"kind": "nestjs"})
        self.assertEqual(sorted(set(g.facts.route_hints)), ["entries", "ledger"])
        own = 'import { Controller } from "./decorators";\n\n@Controller("x")\nexport class B {}\n'
        self.assertNotIn("entry_points", si.guess({"src/b.ts": own}).sections)


class Spring(unittest.TestCase):
    CTL = 'package a;\n\nimport org.springframework.web.bind.annotation.*;\n\n@RestController\n@RequestMapping("/api/invoices")\npublic class InvoiceController {\n  @GetMapping("/{id}")\n  public String one() { return ""; }\n}\n'

    def test_a_java_controller_with_a_spring_import_and_the_build_file_as_a_second_witness(self):
        g = si.guess({"pom.xml": "<project>\n  <parent>spring-boot-starter-parent</parent>\n</project>\n", "src/main/java/a/InvoiceController.java": self.CTL,
                      "src/test/java/a/InvoiceControllerTest.java": self.CTL})
        self.assertEqual((g.language.value, sections(g)["entry_points"], g.roles.value), ("java", {"kind": "spring"}, ["backend"]))
        self.assertEqual(g.sections["entry_points"].notes[0].evidence, ("src/main/java/a/InvoiceController.java:5",))
        self.assertEqual(g.sections["entry_points"].notes[-1].evidence, ("pom.xml:2",))
        self.assertEqual(sections(g)["tests"]["markers"], ["src/test/"])
        self.assertEqual(sections(g)["tests"]["command"], "mvn test")
        self.assertIn("invoices", g.facts.route_hints)

    def test_kotlin_and_a_gradle_build(self):
        ctl = "import org.springframework.web.bind.annotation.RestController\n\n@RestController\nclass C {\n}\n"
        g = si.guess({"build.gradle.kts": 'implementation("org.springframework.boot:spring-boot-starter-web")\n', "src/main/kotlin/C.kt": ctl})
        self.assertEqual((g.language.value, g.kind_of("entry_points")), ("kotlin", "spring"))
        self.assertNotIn("tests", g.sections)  # no test file, so no markers and no command

    def test_a_controller_annotation_without_a_spring_import_a_comment_and_a_test_are_nothing(self):
        files = {"src/main/java/a/A.java": "@RestController\npublic class A {}\n", "src/main/java/a/B.java": "import org.springframework.stereotype.Service;\n// @RestController\npublic class B {}\n",
                 "src/test/java/a/T.java": self.CTL}
        self.assertNotIn("entry_points", si.guess(files).sections)

    def test_jvm_languages_are_read_only_when_the_config_registry_has_them(self):
        from reachmap import config
        self.assertTrue({"java", "kotlin"} <= set(config.languages()))
        self.assertEqual(si.guess({"A.java": "class A {}\n", "B.java": "", "c.py": ""}).language.value, "java")


class OpenApi(unittest.TestCase):
    SPEC = {"openapi": "3.0.3", "paths": {"/invoices": {"get": {"operationId": "listInvoices"}, "post": {"operationId": "createInvoice"}}, "/ping": {"get": {"operationId": "ping"}}}}

    def spec(self, path="api/openapi.json", doc=None):
        return {path: json.dumps(doc if doc is not None else self.SPEC, indent=2)}

    def test_a_python_server_gets_its_handler_files_as_a_glob(self):
        files = self.spec() | {"app/handlers/invoices.py": "def list_invoices():\n    pass\n\ndef create_invoice(b):\n    pass\n", "app/handlers/__init__.py": "", "app/other.py": "def helper(): ...\n"}
        g = si.guess(files)
        self.assertEqual(sections(g)["http_server"], {"kind": "openapi", "spec": "api/openapi.json", "handlers": "app/handlers/*.py"})
        self.assertEqual(g.roles.value, ["backend"])
        self.assertEqual(g.sections["http_server"].notes[0].evidence, ("api/openapi.json:3",))

    def test_a_server_without_handlers_still_names_the_spec_and_says_what_is_missing(self):
        g = si.guess(self.spec() | {"app/x.py": "x = 1\n"})
        self.assertEqual(sections(g)["http_server"], {"kind": "openapi", "spec": "api/openapi.json"})
        self.assertTrue(any("handlers left out" in n for n in g.notes))

    def test_a_generated_client_is_found_by_operation_names(self):
        client = "// Generated from api/openapi.json. Do not edit.\nexport function listInvoices() { return 1; }\nexport function createInvoice(b) { return 2; }\n"
        g = si.guess(self.spec() | {"src/api/generated/client.ts": client, "src/main.ts": ""})
        self.assertEqual(sections(g)["http_client"], {"kind": "openapi", "spec": "api/openapi.json", "client": "src/api/generated"})
        self.assertIn("/invoices", g.facts.client_paths)
        self.assertNotIn("http_server", g.sections)

    def test_decoys_a_json_file_with_no_paths_a_yaml_spec_and_a_broken_one_are_each_said(self):
        cases = (({"openapi.json": '{"info": 1}', "a.py": ""}, "is named like an OpenAPI spec but is ignored: no openapi 3 or swagger 2.0 version key"),
                 ({"openapi.json": '{"openapi": "3.0.0", "paths": {}}', "a.py": ""}, "ignored: no paths"),
                 ({"api/openapi.yaml": "openapi: 3.0.0\n", "a.py": ""}, "api/openapi.yaml is a YAML OpenAPI spec; YAML is not read"),
                 ({"openapi.json": "{not json", "a.py": ""}, "is named like an OpenAPI spec but is ignored: not valid JSON"))
        for files, said in cases:
            with self.subTest(files=list(files)):
                g = si.guess(files)
                self.assertNotIn("http_server", g.sections)
                self.assertTrue(any(said in n for n in g.notes), g.notes)
        self.assertTrue(any("convert the spec to JSON" in n for n in si.guess(cases[2][0]).notes))
        self.assertIn("openapi.json", dict(si.guess(cases[3][0]).unread))  # a spec that does not parse is a file that could not be read

    def test_a_code_framework_wins_and_the_spec_is_said(self):
        files = self.spec() | {"app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n"}
        g = si.guess(files)
        self.assertEqual(g.kind_of("entry_points"), "fastapi")
        self.assertNotIn("http_server", g.sections)
        self.assertTrue(any("committed OpenAPI spec" in n for n in g.notes))


class Hardening(unittest.TestCase):
    """A repository is other people's files: nothing in it crashes a run, and what could not be read is said."""

    DEEP = "[" * 100000 + "]" * 100000

    def test_a_file_nested_past_the_parser_is_unread_never_a_crash(self):
        files = {"src/a.ts": "", "package.json": self.DEEP, "tsconfig.json": '{"compilerOptions": {"paths": {"a": [' + self.DEEP + ']}}}', "openapi.json": self.DEEP,
                 "pyproject.toml": "a = " + self.DEEP + "\n", "b.py": "x = 1\n"}
        g = si.guess(files)
        unread = dict(g.unread)
        for path in ("package.json", "tsconfig.json", "openapi.json", "pyproject.toml"):
            self.assertIn(path, unread, path)
        self.assertIn("nested too deeply", unread["package.json"])
        self.assertEqual(g.language.value, "typescript")  # the rest of the repository is still read

    def test_a_repository_that_breaks_a_detector_is_listed_with_why_and_the_others_are_written(self):
        import io
        import unittest.mock as mock
        from reachmap import init
        from reachmap.init.guess import SpecError
        from tests.test_init_source import FILES

        class Git:
            pass

        def fake(spec, git):
            if spec.name == "bad":
                raise RuntimeError("boom with https://user:s3cretvalue1@example.com/x")
            raise SpecError(spec.shown, "gone")

        with mock.patch("reachmap.init.detect", fake):
            built = init.generate(["bad=/a/bad", "ok=/a/ok"], Git(), "/r")
        self.assertEqual([f[0] for f in built.failures], ["/a/bad", "/a/ok"])
        self.assertIn("could not be analysed: RuntimeError: boom", built.failures[0][1])
        self.assertNotIn("s3cretvalue1", built.text + str(built.failures))

    def test_entries_the_checkout_did_not_write_are_listed_and_a_skipped_tsconfig_is_a_candidate_with_an_error(self):
        from reachmap.init.detect import guess_repo
        from reachmap.init.tree import Tree

        tree = Tree({"src/a.ts": ""}, lambda p: __import__("reachmap.model", fromlist=["ReadResult"]).ReadResult.found(""),
                    skipped={"tsconfig.json": "a symlink", "vendor/x\u00ff": "not UTF-8"})
        g = guess_repo("a/b", tree)
        self.assertEqual(sections(g)["aliases"], {"from": "tsconfig.json"})
        self.assertIn("could not be read", g.sections["aliases"].notes[0].why)
        self.assertEqual(dict(g.unread)["tsconfig.json"], "not written to the checkout: a symlink")
        self.assertIn("vendor/x\u00ff", dict(g.unread))

    def test_absence_notes_are_conditional_on_what_could_be_read(self):
        from reachmap.init.detect import guess_repo
        from reachmap.init.tree import Tree
        from reachmap.model import ReadResult

        tree = Tree({"a.py": "x = 1\n"}, lambda p: ReadResult.found("x = 1\n"), skipped={"b.py": "a symlink", "c.py": "a symlink"})
        why = guess_repo("a/b", tree).roles.notes[0].why
        self.assertIn("in the files that could be read (2 could not be read)", why)
        self.assertIn("in the files that were read", si.guess({"a.py": "x = 1\n"}).roles.notes[0].why)

    def test_the_tree_cache_is_bounded_and_still_answers(self):
        from reachmap.init import tree as tree_mod

        t = si.tree({f"f{i}.txt": "x" * 100 for i in range(50)})
        old = tree_mod.MAX_CACHE_BYTES
        tree_mod.MAX_CACHE_BYTES = 1000
        try:
            for i in range(50):
                self.assertEqual(len(t.text(f"f{i}.txt")), 100)
            self.assertLessEqual(len(t._cache), 11)
            self.assertEqual(len(t.text("f0.txt")), 100)
        finally:
            tree_mod.MAX_CACHE_BYTES = old

    def test_one_file_is_singular(self):
        self.assertEqual(si.guess({"a.py": ""}).language.notes[0].why, "1 python file")


class Contract(unittest.TestCase):
    MODEL = "from pydantic import BaseModel\n\n\nclass {n}(BaseModel):\n    id: str\n"
    PYPROJECT = '[project]\nname = "acme-contracts"\n\n[tool.setuptools]\npackage-dir = { acme_contracts = "contracts" }\n'

    def files(self):
        return {f"contracts/models/{n}.py": self.MODEL.format(n=n.title()) for n in ("a", "b", "c")} | {"contracts/__init__.py": "", "contracts/models/__init__.py": "", "pyproject.toml": self.PYPROJECT}

    def test_models_in_one_folder_the_package_name_and_the_tag_prefix(self):
        g = si.guess(self.files(), tags_loader=lambda: ("v1.2.0", "v1.10.0"))
        self.assertEqual(sections(g)["contract"], {"kind": "pydantic", "root": "contracts/models", "package": "acme_contracts", "versions": "git-tags", "tag_prefix": "v"})
        self.assertEqual(g.roles.value, ["contract"])

    def test_models_through_a_local_base_class_count_and_so_does_the_dotted_name(self):
        files = self.files() | {"contracts/models/d.py": "import pydantic\nfrom .a import A\n\n\nclass D(A):\n    x: int\n\n\nclass E(pydantic.BaseModel):\n    y: int\n"}
        self.assertIn("5 of 5", si.guess(files).sections["contract"].notes[0].why)

    def test_a_models_folder_without_basemodel_is_not_a_contract(self):
        files = {f"models/{n}.py": f"@dataclass\nclass {n.title()}:\n    id: str\n" for n in ("a", "b", "c")}
        self.assertNotIn("contract", si.guess(files).sections)

    def test_models_spread_over_the_repository_are_not_concentrated(self):
        files = {f"{d}/m.py": self.MODEL.format(n="M" + d) for d in ("a", "b", "c", "d", "e")}
        self.assertNotIn("contract", si.guess(files).sections)

    def test_no_tags_no_prefix_and_the_reason_is_said(self):
        g = si.guess(self.files(), tags_loader=lambda: ())
        self.assertNotIn("tag_prefix", sections(g)["contract"])
        self.assertTrue(any("no tags" in n for n in g.notes))

    def test_a_service_with_schemas_is_not_a_contract_repository(self):
        files = self.files() | {"app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n"}
        g = si.guess(files)
        self.assertNotIn("contract", g.sections)
        self.assertEqual(g.roles.value, ["backend"])
        self.assertTrue(any("look like a contract" in n for n in g.notes))

    def test_tag_prefixes(self):
        self.assertEqual(tag_prefix(("v1.0.0", "v1.1.0"))[0], "v")
        self.assertEqual(tag_prefix(("1.0.0", "2.0.0"))[0], "")
        self.assertEqual(tag_prefix(("release-1.0", "release-1.1", "v2"))[0], "release-")
        self.assertIsNone(tag_prefix(("a1", "b1"))[0])
        self.assertIsNone(tag_prefix(("latest", "stable"))[0])


class Tests(unittest.TestCase):
    def test_markers_come_from_names_and_folders_never_substrings(self):
        files = {"src/contest/engine.ts": "", "src/components/__tests__/A.test.tsx": "", "src/lib/b.spec.ts": "", "src/latest.ts": ""}
        self.assertEqual(sections(si.guess(files))["tests"]["markers"], ["__tests__/", ".test.", ".spec."])
        self.assertNotIn("tests", si.guess({"src/contest/engine.ts": "", "src/a.ts": ""}).sections)

    def test_python_markers_and_a_file_that_only_contains_test(self):
        files = {"svc/tests/test_a.py": "", "svc/latest_rates.py": "", "svc/money_test.py": ""}
        self.assertEqual(sections(si.guess(files))["tests"]["markers"], ["tests/", "test_", "_test"])
        self.assertNotIn("tests", si.guess({"latest_rates.py": ""}).sections)

    def test_js_test_commands_from_package_json_scripts(self):
        for script, command in (("jest --watch", "npx jest {paths}"), ("vitest run", "npx vitest run {paths}"), ("mocha", "npx mocha {paths}")):
            g = si.guess({"src/a.test.ts": "", "package.json": json.dumps({"scripts": {"test": script}}, indent=2)})
            self.assertEqual(sections(g)["tests"]["command"], command)
        g = si.guess({"src/a.test.ts": "", "package.json": '{"scripts": {"test": "node check.js"}}'})
        self.assertNotIn("command", sections(g)["tests"])

    def test_pytest_by_configuration_import_and_style_unittest_by_import(self):
        base = {"t/test_a.py": "def test_a():\n    assert True\n"}
        self.assertEqual(sections(si.guess({**base, "pyproject.toml": "[tool.pytest.ini_options]\naddopts = '-q'\n"}))["tests"]["command"], "pytest {paths}")
        self.assertEqual(sections(si.guess({"t/test_a.py": "import pytest\n"}))["tests"]["command"], "pytest {paths}")
        g = si.guess(base)
        self.assertEqual((sections(g)["tests"]["command"], g.sections["tests"].notes[-1].confidence), ("pytest {paths}", "medium"))
        self.assertEqual(sections(si.guess({"t/test_a.py": "import unittest\nclass T(unittest.TestCase):\n    pass\n"}))["tests"]["command"], "python -m unittest {paths}")
        self.assertNotIn("command", sections(si.guess({"t/test_a.py": "x = 1\n"}))["tests"])


class Monorepos(unittest.TestCase):
    def test_service_marker_needs_several_folders_below_the_root(self):
        files = {"pyproject.toml": "", "a/pyproject.toml": "", "a/m.py": ""}
        self.assertNotIn("service_marker", si.guess(files).sections)
        files |= {"b/pyproject.toml": "", "b/m.py": ""}
        self.assertEqual(sections(si.guess(files))["service_marker"], "pyproject.toml")

    def test_a_javascript_monorepo_marker_says_only_python_reads_it(self):
        g = si.guess({"packages/a/package.json": "{}", "packages/b/package.json": "{}", "packages/a/x.ts": "", "packages/b/y.ts": ""})
        self.assertEqual(sections(g)["service_marker"], "package.json")
        self.assertIn("only the Python adapter", g.sections["service_marker"].notes[0].why)

    def test_a_library_root_is_a_marker_folder_without_routes_that_others_import(self):
        files = {"libs/common/pyproject.toml": "", "libs/common/acme_common/__init__.py": "", "libs/common/acme_common/money.py": "",
                 "svc/pyproject.toml": "", "svc/app/main.py": "from fastapi import FastAPI\nfrom acme_common.money import f\napp = FastAPI()\n",
                 "svc2/pyproject.toml": "", "svc2/app/main.py": "x = 1\n"}
        g = si.guess(files)
        self.assertEqual(sections(g)["library_roots"], ["libs/common"])
        self.assertEqual(g.sections["library_roots"].notes[0].evidence, ("svc/app/main.py:2",))

    def test_a_service_that_others_import_and_a_library_nobody_imports_are_not_library_roots(self):
        files = {"a/pyproject.toml": "", "a/pkg_a/__init__.py": "from fastapi import FastAPI\napp = FastAPI()\n", "b/pyproject.toml": "", "b/pkg_b/m.py": "from pkg_a import app\n",
                 "c/pyproject.toml": "", "c/pkg_c/m.py": "x = 1\n"}
        self.assertNotIn("library_roots", si.guess(files).sections)


class Roles(unittest.TestCase):
    def test_nothing_found_is_a_low_confidence_library(self):
        g = si.guess({"a.py": ""})
        self.assertEqual((g.roles.value, g.roles.notes[0].confidence), (["library"], "low"))

    def test_a_react_dependency_without_routes_is_a_low_confidence_frontend(self):
        g = si.guess({"src/a.tsx": "", "package.json": '{"dependencies": {"react": "18"}}'})
        self.assertEqual((g.roles.value, g.roles.notes[0].confidence), (["frontend"], "low"))

    def test_roles_are_ordered_and_distinct(self):
        g = si.guess({"package.json": '{"dependencies": {"next": "1"}}', "app/page.tsx": "", "app/api/x/route.ts": ""})
        self.assertEqual(g.roles.value, ["frontend", "backend"])


class Registry(unittest.TestCase):
    def test_another_adapters_detector_is_one_registration(self):
        calls = []

        def detect_spring(ctx):
            calls.append(ctx.language)
            return [registry.Found("fake", {}, ("backend",), (Note("Spring", ("pom.xml:1",), "high"),), score=9)]

        with self.assertRaises(ValueError) as cm:  # a kind the config does not accept could only write a file that does not load
            registry.register("entry_points", "fake", detect_spring, languages=("java",))
        self.assertIn("the config does not accept kind 'fake'", str(cm.exception))
        config.register_kind("entry_points", "fake")
        registry.register("entry_points", "fake", detect_spring, languages=("java",))
        try:
            self.assertEqual([k for k, _ in registry.detectors("entry_points", "java")], ["spring", "fake"])
            self.assertNotIn("fake", [k for k, _ in registry.detectors("entry_points", "python")])
        finally:
            registry._REGISTRY["entry_points"].pop("fake")
            config._SECTIONS["entry_points"].kinds.pop("fake")


if __name__ == "__main__":
    unittest.main()


class AlwaysLoads(unittest.TestCase):
    """Whatever the detectors find in whatever mix of files, the output loads with `config.loads` and every guess comment is well formed."""

    POOL = {
        "src/routes.tsx": routes_file(), "src/api/endpoints.ts": EndpointsFile.ENDPOINTS, "src/App.tsx": "export const App = () => <Routes />;\n",
        "package.json": '{"name": "@acme/web", "scripts": {"test": "vitest"}, "dependencies": {"next": "14", "react": "18", "express": "4"}}',
        "app/page.tsx": "", "app/api/x/route.ts": "", "tsconfig.json": TS if (TS := Aliases.TS) else "", "jsconfig.json": "{not json",
        "main.py": "from fastapi import FastAPI\napp = FastAPI()\n", "urls.py": Django.URLS, "settings.py": 'ROOT_URLCONF = "urls"\n',
        "tasks.py": "from celery import shared_task\n\n@shared_task\ndef t(): ...\n", "w/topics.py": 'A = "x.y"\nB = "x.z"\n',
        "models/a.py": Contract.MODEL.format(n="A"), "models/b.py": Contract.MODEL.format(n="B"), "pyproject.toml": Contract.PYPROJECT,
        "requirements.txt": "acme-contracts==1.0\nfastapi\n", "api/openapi.json": json.dumps(OpenApi.SPEC), "app/handlers/h.py": "def list_invoices(): ...\n",
        "tests/test_a.py": "def test_a():\n    assert 1\n", "src/a.test.ts": "", "libs/c/pyproject.toml": "", "libs/c/cpkg/__init__.py": "", "svc/pyproject.toml": "",
        "svc/m.py": "from cpkg import x\n", "bad name\n[x].py": "x = 1\n", "unicode/\u202e.ts": "",
    }

    def test_random_mixes_of_files_always_produce_a_loadable_config(self):
        import random
        import tomllib

        from reachmap import config
        from reachmap.init.template import build

        names = sorted(self.POOL)
        for seed in range(150):
            rnd = random.Random(seed)
            guesses = []
            for i in range(rnd.randint(1, 3)):
                files = {n: self.POOL[n] for n in rnd.sample(names, rnd.randint(1, len(names)))}
                files.setdefault("a.py", "x = 1\n")
                guesses.append(si.guess(files, name=f"o{i}/r{i}", remote=f"/r/{i}", remote_local=True, tags_loader=lambda: ("v1.0.0",), heads=("main", "release/1")))
            with self.subTest(seed=seed):
                built = build(guesses, out_dir="/r")
                ws = config.loads(built.text, fmt="toml", env={}, base_dir="/r")
                tomllib.loads(built.text)
                self.assertEqual(sorted(ws.repos), sorted(built.names))
                for line in built.text.splitlines():
                    if line.startswith("# guess:"):
                        self.assertRegex(line, r"^# guess: .+, confidence (high|medium|low); confirm$")
