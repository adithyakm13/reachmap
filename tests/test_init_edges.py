"""M17: edge guesses. A package edge needs an exact pin in the consumer's manifest of a package the producer declares, and its
pin_pattern is checked against the line it was written for (one group, a version out of it); a contract edge goes with a package
edge from a contract repository to a Python consumer; an http edge needs a client and routes, and says when no URL segment is shared."""

import re
import unittest

from reachmap import config, versions
from reachmap.init.edges import guess_edges, pin_pattern
from reachmap.init.guess import Dep
from reachmap.init.template import build
from tests import support_init as si

CONTRACTS = {
    "pyproject.toml": '[project]\nname = "acme-contracts"\n\n[tool.setuptools]\npackage-dir = { acme_contracts = "contracts" }\n',
    "contracts/__init__.py": "",
    **{f"contracts/models/{n}.py": f"from pydantic import BaseModel\n\n\nclass {n.title()}(BaseModel):\n    id: str\n" for n in ("a", "b")},
}


def edges(**repos):
    guesses = [si.guess(files, name=name.replace("_", "/")) for name, files in repos.items()]
    return guess_edges(guesses)


class PinPatterns(unittest.TestCase):
    def pattern(self, ecosystem, manifest, text, name="acme-contracts", spelling=""):
        return pin_pattern(Dep(ecosystem, name, manifest, 1, text, True, spelling))

    def reads(self, pattern, line):
        compiled = re.compile(pattern)
        self.assertEqual(compiled.groups, 1)
        m = compiled.search(line)
        return m.group(1) if m else None

    def test_each_manifest_shape(self):
        cases = [
            ("pip", "requirements.txt", "acme-contracts==1.9.0", "1.9.0"),
            ("pip", "requirements.txt", "acme-contracts[extra]==1.9.0  ; python_version > '3.9'  # pinned", "1.9.0"),
            ("pip", "pyproject.toml", '    "acme-contracts==1.9.0",', "1.9.0"),
            ("pip", "pyproject.toml", 'dependencies = ["fastapi", "acme-contracts>=1", "acme-contracts==1.2.3"]', None),
            ("pip", "pyproject.toml", 'acme-contracts = "^1.9.0"', "1.9.0"),
            ("npm", "package.json", '    "acme-contracts": "^1.9.0",', "1.9.0"),
        ]
        for eco, manifest, line, want in cases:
            with self.subTest(line=line):
                pattern = self.pattern(eco, manifest, line)
                if want is None:
                    continue
                self.assertEqual(self.reads(pattern, line), want)

    def test_a_name_in_a_one_line_array_is_found_by_its_own_spelling_and_an_indented_line_matches(self):
        line = 'dependencies = ["fastapi", "acme-contracts==1.2.3", "pydantic>=2"]'
        p = self.pattern("pip", "pyproject.toml", line, spelling="acme-contracts")
        self.assertEqual(self.reads(p, line), "1.2.3")
        self.assertEqual(self.reads(self.pattern("pip", "requirements.txt", "    acme-contracts==1.9.0"), "    acme-contracts==1.9.0"), "1.9.0")
        other = self.pattern("pip", "pyproject.toml", line, name="fastapi", spelling="fastapi")
        self.assertIsNone(other)  # fastapi is named there with no pin

    def test_a_scoped_npm_name_and_a_dotted_pip_name_are_escaped(self):
        p = self.pattern("npm", "package.json", '  "@acme/ui": "~2.0.1"', name="@acme/ui")
        self.assertEqual(self.reads(p, '  "@acme/ui": "~2.0.1"'), "2.0.1")
        self.assertIsNone(self.reads(p, '  "@acme/uix": "~2.0.1"'))
        q = self.pattern("pip", "requirements.txt", "zope.interface==5.0", name="zope-interface", spelling="zope.interface")
        self.assertIsNone(self.reads(q, "zopexinterface==5.0"))

    def test_a_line_that_does_not_read_as_a_version_gets_no_pattern(self):
        self.assertIsNone(self.pattern("npm", "package.json", '  "acme-contracts": "workspace:*"'))
        self.assertIsNone(self.pattern("pip", "requirements.txt", "acme-contracts==not-a-version-at-all"))
        self.assertIsNone(self.pattern("pip", "Pipfile", 'acme-contracts = "==1.0"'))

    def test_every_pattern_it_writes_reads_a_parseable_version(self):
        for line in ("acme-contracts==1.9.0", "acme-contracts==v1.2.3"):
            m = re.search(self.pattern("pip", "requirements.txt", line), line)
            self.assertIsNotNone(versions.parse(m.group(1)))


class PackageAndContract(unittest.TestCase):
    API = {"requirements.txt": "fastapi==0.1\nacme-contracts==1.9.0\n", "app/m.py": "from acme_contracts.models.a import A\n"}

    def test_a_pin_of_a_package_the_other_repository_declares_is_an_edge_with_the_import_marker(self):
        found, notes = edges(acme_contracts=CONTRACTS, acme_api=self.API)
        pkg = [e for e in found if e.kind == "package"]
        self.assertEqual([(e.producer, e.consumer, e.options["manifest"], e.options["import_marker"]) for e in pkg], [("acme/contracts", "acme/api", "requirements.txt", "acme_contracts")])
        self.assertEqual(pkg[0].notes[0].confidence, "high")  # the consumer imports the package too
        self.assertIn("acme/api:app/m.py:1", pkg[0].notes[1].evidence)  # evidence names its repository: an edge has two
        self.assertEqual(pkg[0].notes[0].evidence, ("acme/api:requirements.txt:2",))
        self.assertEqual([(e.kind, e.consumer) for e in found if e.kind == "contract"], [("contract", "acme/api")])

    def test_no_contract_edge_for_a_consumer_that_is_not_python_or_a_producer_with_no_contract_table(self):
        web = {"package.json": '{"dependencies": {"acme-contracts": "^1.0.0"}}', "src/a.ts": ""}
        found, _ = edges(acme_contracts=CONTRACTS, acme_web=web)
        self.assertEqual([e.kind for e in found], [])  # a pip package is not an npm one
        lib = {"pyproject.toml": '[project]\nname = "acme-lib"\n', "acme_lib/__init__.py": ""}
        found, _ = edges(acme_lib=lib, acme_api={"requirements.txt": "acme-lib==1.0\n", "a.py": ""})
        self.assertEqual([e.kind for e in found], ["package"])

    def test_an_npm_package_between_two_repositories(self):
        ui = {"package.json": '{\n "name": "@acme/ui",\n "version": "1.0.0"\n}\n', "src/a.ts": ""}
        app = {"package.json": '{\n "dependencies": {\n  "@acme/ui": "^1.2.0"\n }\n}\n', "src/a.ts": 'import { B } from "@acme/ui";\n'}
        found, _ = edges(acme_ui=ui, acme_app=app)
        self.assertEqual([(e.kind, e.options["import_marker"], e.options["manifest"]) for e in found], [("package", "@acme/ui", "package.json")])
        self.assertEqual(found[0].notes[0].confidence, "medium")  # a caret range: its lower bound is read
        self.assertIn("with a range; its lower bound is read", found[0].notes[0].why)

    def test_a_dependency_with_no_exact_pin_is_said_and_no_edge_is_written(self):
        for text in ("acme-contracts>=1.0\n", "acme-contracts\n", "acme-contracts~=1.0\n"):
            found, notes = edges(acme_contracts=CONTRACTS, acme_api={"requirements.txt": text, "a.py": ""})
            self.assertEqual([e for e in found if e.kind == "package"], [], text)
            self.assertTrue(any("without a version this tool can read" in n for n in notes), text)

    def test_a_repository_that_names_its_own_package_is_not_its_own_producer(self):
        own = dict(CONTRACTS) | {"requirements.txt": "acme-contracts==1.0\n"}
        self.assertEqual(edges(acme_contracts=own)[0], [])

    def test_the_first_manifest_by_priority_is_read_and_the_others_are_said(self):
        api = {"requirements.txt": "acme-contracts==1.9.0\n", "pyproject.toml": '[project]\nname = "api"\ndependencies = [\n  "acme-contracts==1.8.0",\n]\n', "a.py": ""}
        found, _ = edges(acme_contracts=CONTRACTS, acme_api=api)
        pkg = [e for e in found if e.kind == "package"]
        self.assertEqual(len(pkg), 1)
        self.assertEqual(pkg[0].options["manifest"], "requirements.txt")
        self.assertTrue(any("other manifests" in n.why for n in pkg[0].notes))


class Ranges(unittest.TestCase):
    def test_a_caret_range_is_a_medium_confidence_edge_that_says_its_lower_bound_is_read_and_an_exact_pin_is_high(self):
        ui = {"package.json": '{\n "name": "@acme/ui"\n}\n', "src/a.ts": ""}
        caret = {"package.json": '{\n "dependencies": {\n  "@acme/ui": "^1.2.0"\n }\n}\n', "src/a.ts": 'import x from "@acme/ui";\n'}
        exact = {"package.json": '{\n "dependencies": {\n  "@acme/ui": "1.2.0"\n }\n}\n', "src/a.ts": 'import x from "@acme/ui";\n'}
        a = edges(acme_ui=ui, acme_app=caret)[0][0].notes[0]
        b = edges(acme_ui=ui, acme_app=exact)[0][0].notes[0]
        self.assertEqual((a.confidence, b.confidence), ("medium", "high"))
        self.assertIn("with a range; its lower bound is read", a.why)
        self.assertIn("with an exact version", b.why)


class Http(unittest.TestCase):
    ENDPOINTS = 'export const A = "/api/orders";\nexport const B = (id: string) => `/api/orders/${id}`;\nexport const C = "/api/orders/open";\n'
    WEB = {"src/api/endpoints.ts": ENDPOINTS, "src/main.ts": ""}
    API = {"app/main.py": "from fastapi import FastAPI\nfrom fastapi import APIRouter\napp = FastAPI()\nr = APIRouter(prefix='/orders')\n"}

    def test_a_client_and_a_server_that_share_a_url_segment_are_a_medium_confidence_edge(self):
        found, _ = edges(acme_web=self.WEB, acme_api=self.API)
        self.assertEqual([(e.kind, e.producer, e.consumer, e.notes[0].confidence) for e in found], [("http", "acme/web", "acme/api", "medium")])
        self.assertIn("orders", found[0].notes[0].why)

    def test_one_server_and_no_shared_segment_is_a_low_confidence_edge(self):
        other = {"app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n"}
        found, _ = edges(acme_web=self.WEB, acme_api=other)
        self.assertEqual([e.notes[0].confidence for e in found], ["low"])

    def test_several_servers_and_no_shared_segment_write_no_edge_and_say_so(self):
        other = {"app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n"}
        found, notes = edges(acme_web=self.WEB, acme_one=other, acme_two={"m.py": "from fastapi import FastAPI\napp = FastAPI()\n"})
        self.assertEqual(found, [])
        self.assertTrue(any("no http edge was written" in n for n in notes))

    def test_a_segment_two_servers_share_says_nothing_about_which_is_called(self):
        one = {"app/main.py": "from fastapi import FastAPI\nfrom fastapi import APIRouter\napp = FastAPI()\nr = APIRouter(prefix='/orders')\nq = APIRouter(prefix='/billing')\n"}
        two = {"app/main.py": "from fastapi import FastAPI\nfrom fastapi import APIRouter\napp = FastAPI()\nr = APIRouter(prefix='/orders')\n"}
        web = {"src/api/endpoints.ts": 'export const A = "/api/orders";\nexport const B = "/api/billing/x";\nexport const C = "/api/orders/open";\n', "src/main.ts": ""}
        found, notes = edges(acme_web=web, acme_one=one, acme_two=two)
        self.assertEqual([(e.consumer) for e in found], ["acme/one"])  # billing is only in one; orders is in both and is ignored
        found, notes = edges(acme_web=self.WEB, acme_one=one, acme_two=two)
        self.assertEqual(found, [])
        self.assertTrue(any("orders are in several" in n or "orders" in n and "in several" in n for n in notes), notes)

    def test_a_server_is_not_connected_to_itself(self):
        found, _ = edges(acme_web=self.WEB | {"app/main.ts": ""})
        self.assertEqual(found, [])


class Event(unittest.TestCase):
    WORKER = {"w/tasks.py": "from celery import shared_task\n\n@shared_task\ndef t(): ...\n", "w/topics.py": 'A = "billing.a.sync"\nB = "billing.b.sync"\n'}
    API = {"app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n"}

    def test_a_worker_with_topics_and_a_server_make_a_low_confidence_event_edge(self):
        found, _ = edges(acme_api=self.API, acme_worker=self.WORKER)
        self.assertEqual([(e.kind, e.producer, e.consumer, e.options, e.notes[0].confidence) for e in found],
                         [("event", "acme/api", "acme/worker", {"topics_glob": "**/topics.py"}, "low")])

    def test_several_servers_give_one_edge_and_a_note_naming_the_others(self):
        found, _ = edges(acme_api=self.API, acme_other=self.API, acme_worker=self.WORKER)
        self.assertEqual([(e.producer, e.consumer) for e in found], [("acme/api", "acme/worker")])
        self.assertIn("acme/other could publish them too", found[0].notes[0].why)

    def test_a_worker_without_a_topics_module_has_none(self):
        worker = {k: v for k, v in self.WORKER.items() if k != "w/topics.py"}
        self.assertEqual(edges(acme_api=self.API, acme_worker=worker)[0], [])


class InTheFile(unittest.TestCase):
    def test_the_edges_written_load_with_the_config_loader(self):
        guesses = [si.guess(CONTRACTS, name="acme/contracts", remote="file:///x/c"), si.guess(PackageAndContract.API | {"requirements.txt": "acme-contracts==1.9.0\n"}, name="acme/api", remote="file:///x/a")]
        text = build(guesses).text
        ws = config.loads(text, fmt="toml", env={})
        self.assertEqual(sorted((e.kind, e.from_repo, e.to_repo) for e in ws.edges), [("contract", "acme/contracts", "acme/api"), ("package", "acme/contracts", "acme/api")])


if __name__ == "__main__":
    unittest.main()
