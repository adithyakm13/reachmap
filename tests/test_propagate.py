"""The cross-repository walker (M8): the rules every edge obeys, in one place. A contract field changed in a library
reaches an api that reads it, the api's route reaches the web client, and the web client reaches its screen, with a chain
of evidence across the three repositories; the hop cap, the hub rule and the module cap apply in every repository's pass; a
cycle between repositories ends; a failed read is an incompleteness on its edge; and every Evidence resolves at the sha it is
pinned to."""

import dataclasses
import unittest
from datetime import datetime, timezone
from unittest import mock

from reachmap import edges, propagate
from reachmap.changeset import resolve
from reachmap.edges import Hop, Seed
from tests.fixtures import build
from tests.support_cross import CrossCase

FULL = """
workspace = "t"
[limits]
hops = {hops}
hub_importers = {hub}
distinct_max = 3

[repos."t/lib"]
host = "git"
remote = "{lib}"
language = "python"
roles = ["contract"]
branches = ["main"]
contract = {{ kind = "pydantic", root = "lib/models", package = "acme_lib", versions = "git-tags", tag_prefix = "v" }}

[repos."t/api"]
host = "git"
remote = "{api}"
language = "python"
roles = ["backend"]
branches = ["main"]
entry_points = {{ kind = "fastapi" }}

[repos."t/web"]
host = "git"
remote = "{web}"
language = "typescript"
roles = ["frontend"]
branches = ["main"]
entry_points = {{ kind = "react-router", routes_glob = "src/routes.tsx" }}
http_client = {{ kind = "endpoints-file", path = "src/api/endpoints.ts" }}

[[edges]]
kind = "package"
producer = "t/lib"
consumer = "t/api"
manifest = "requirements.txt"
pin_pattern = '^acme-lib==(.+)$'
import_marker = "acme_lib"

[[edges]]
kind = "contract"
producer = "t/lib"
consumer = "t/api"

[[edges]]
kind = "http"
from = "t/web"
to = "t/api"
"""

INVOICE = "from pydantic import BaseModel\n\n\nclass Invoice(BaseModel):\n    number: str\n    reference: str | None = None\n"
INVOICE_AFTER = INVOICE.replace("reference: str | None = None", "reference: str")

API = {
    "requirements.txt": "acme-lib==1.0.0\n",
    "app/__init__.py": "",
    "app/service.py": "from acme_lib.models.invoice import Invoice\n\n\ndef label(invoice: Invoice) -> str:\n    return invoice.reference or 'none'\n\n\ndef summarise(invoices):\n    return [label(i) for i in invoices]\n",
    "app/main.py": 'from fastapi import FastAPI\n\nfrom app.routers.orders import router\n\napp = FastAPI()\napp.include_router(router, prefix="/api/orders")\n',
    "app/routers/__init__.py": "",
    "app/routers/orders.py": "from fastapi import APIRouter\n\nfrom app import service\n\nrouter = APIRouter()\n\n\n@router.get(\"/summary\")\ndef summary():\n    return service.summarise([])\n",
}
WEB = {
    "src/api/endpoints.ts": 'export const SUMMARY_URL = "/api/orders/summary";\n',
    "src/api/http.ts": "export async function apiGet<T>(url: string): Promise<T> {\n  return fetch(url).then((r) => r.json());\n}\n",
    "src/modules/orders/api.ts": 'import { apiGet } from "../../api/http";\nimport { SUMMARY_URL } from "../../api/endpoints";\n\nexport function fetchSummary() {\n  return apiGet(SUMMARY_URL);\n}\n',
    "src/modules/orders/OrdersPage.tsx": 'import { fetchSummary } from "./api";\n\nexport default function OrdersPage() {\n  fetchSummary();\n  return <div />;\n}\n',
    "src/modules/orders/ReportPage.tsx": 'import { fetchSummary } from "./api";\n\nexport default function ReportPage() {\n  fetchSummary();\n  return <div />;\n}\n',
    "src/routes.tsx": 'import OrdersPage from "./modules/orders/OrdersPage";\nimport ReportPage from "./modules/orders/ReportPage";\n\nexport const routes = (\n  <Routes>\n    <Route path="/orders" element={<OrdersPage />} />\n    <Route path="/report" element={<ReportPage />} />\n  </Routes>\n);\n',
}


class Walk(CrossCase):
    SHARED = True

    def build(self, hops=3, hub=50, api=None, web=None):
        self.repos(
            lib=[{"files": {"lib/models/invoice.py": INVOICE}}, {"files": {"lib/models/invoice.py": INVOICE_AFTER}}],
            api=[{"files": {**API, **(api or {})}}], web=[{"files": {**WEB, **(web or {})}}],
        )
        return self.workspace(FULL.replace("{hops}", str(hops)).replace("{hub}", str(hub)))

    def run_lib(self, ws, **kw):
        return self.propagate(ws, "t/lib", "main~1...main", **kw)


class ThreeRepositories(Walk):
    def test_a_contract_field_reaches_the_web_screens_through_the_api_with_evidence_at_every_hop(self):
        result = self.run_lib(self.build())
        self.assertEqual([r.area.label for r in result.reached], ["GET /api/orders/summary", "/orders", "/report"])
        screens = {r.area.label: r for r in result.reached if r.area.kind == "screen"}
        chain = screens["/orders"].area.chains[0]
        self.assertEqual([(e.repo, e.path, e.line) for e in chain], [
            ("t/api", "app/service.py", 1), ("t/api", "app/service.py", 5), ("t/api", "app/service.py", 9), ("t/api", "app/routers/orders.py", 3),
            ("t/api", "app/routers/orders.py", 10),
            ("t/web", "src/modules/orders/api.ts", 5), ("t/web", "src/modules/orders/OrdersPage.tsx", 1), ("t/web", "src/routes.tsx", 1), ("t/web", "src/routes.tsx", 6),
        ])
        self.assertEqual({e.repo for e in chain}, {"t/api", "t/web"})
        self.assertEqual([(p.repo, p.depth) for p in result.passes], [("t/lib", 0), ("t/api", 1), ("t/web", 2)])
        self.assertEqual([h.depth for h in result.hops if h.kind == "http"], [2])
        self.assertTrue(result.complete)
        self.assertEqual(self.kinds(), [])
        self.assert_evidence_resolves(result)  # every Evidence of every chain, across the three repositories, is its line at its pinned sha

    def test_the_hop_cap_is_recorded_for_the_edge_past_it_in_its_declared_direction(self):
        result = self.run_lib(self.build(hops=1))
        self.assertEqual(self.kinds(), [("hop_cap", "edge:http:t/web:t/api", False)])
        self.assertEqual([p.repo for p in result.passes], ["t/lib", "t/api"])
        self.assertEqual([r.area.label for r in result.reached], ["GET /api/orders/summary"])
        self.assertFalse(result.complete)
        self.assertEqual(self.run_lib(self.build(hops=2)).complete, True)  # one more hop and the web is followed
        self.assertEqual(len([r for r in self.run_lib(self.build(hops=2)).reached if r.area.kind == "screen"]), 2)

    def test_a_cap_of_zero_follows_no_edge_and_says_so_for_each_edge_that_had_something_to_carry(self):
        ws = self.build()
        result = self.run_lib(ws, limits=dataclasses.replace(ws.limits, hops=0))
        self.assertEqual(sorted(s for k, s, b in self.kinds() if k == "hop_cap"), ["edge:contract:t/lib:t/api", "edge:package:t/lib:t/api"])
        self.assertEqual((result.consumers, result.contract_reads, [p.repo for p in result.passes]), ((), (), ["t/lib"]))

    def test_the_hub_rule_applies_in_the_other_repositorys_pass_and_the_walk_does_not_pass_through_it(self):
        result = self.run_lib(self.build(hub=1))
        hubs = [(k, s) for k, s, _b in self.kinds() if k == "hub_cutoff"]
        self.assertEqual(hubs, [("hub_cutoff", "t/web:src/modules/orders/api.ts")])  # api.ts is imported by two pages: more than the limit of 1; the subject names the repository, which is not the changed one
        self.assertEqual([r.area.kind for r in result.reached], ["api_route"])  # no screen past the hub
        self.assertFalse(result.complete)
        self.assertTrue(any("hub" in why for why in result.reasons))

    def test_the_module_cap_applies_in_the_other_repositorys_pass(self):
        ws = self.build()
        result = self.run_lib(ws, limits=dataclasses.replace(ws.limits, module_cap=1))
        self.assertEqual([(k, s) for k, s, _b in self.kinds() if k == "module_cap"], [("module_cap", "t/web:hop:1")])
        self.assertFalse(result.complete)

    def test_a_blocking_record_never_sits_beside_a_complete_propagation_whatever_its_kind(self):
        """The core invariant: a record that blocks selection means something was not established, so `complete` is False.
        Every kind of record there is, `pre_change_unavailable` (the change set's) included; the baseline is complete."""
        from reachmap import incomplete
        from reachmap.incomplete import record

        ws = self.build()
        self.assertTrue(self.run_lib(ws).complete)
        checked = []
        for kind in incomplete.kinds():
            self.records.clear()
            record(self.records, kind, "something")
            result = self.run_lib(ws)
            blocking = incomplete.kind_spec(kind).blocks_selection
            if blocking:
                self.assertFalse(result.complete, kind)
                self.assertTrue(any(kind in why for why in result.reasons), kind)
                checked.append(kind)
            self.records.clear()
        self.assertIn("pre_change_unavailable", checked)
        self.assertIn("edge_incomplete", checked)
        self.assertIn("read_unreadable", checked)
        self.assertGreater(len(checked), 8)

    def test_a_non_blocking_stated_limit_is_a_record_and_does_not_make_the_run_complete_either(self):
        """edge_unfollowed and entry_points_unread are said like hop_cap: once, non-blocking, and the answer is not complete."""
        changed = API["app/service.py"].replace("'none'", "'n/a'")
        self.repos(lib=[{"files": {"lib/models/invoice.py": INVOICE}}], api=[{"files": API}, {"files": {"app/service.py": changed}}], web=[{"files": WEB}])
        text = FULL.replace("{hops}", "3").replace("{hub}", "50") + '\n[[edges]]\nkind = "event"\nfrom = "t/api"\nto = "t/web"\ntopics_glob = "**/topics.py"\n'
        result = self.propagate(self.workspace(text), "t/api", "main~1...main")
        self.assertEqual(self.kinds(), [("edge_unfollowed", "edge:event:t/api:t/web", False)])
        self.assertFalse(result.complete)
        text2 = text.replace('entry_points = {{ kind = "react-router", routes_glob = "src/routes.tsx" }}', 'entry_points = {{ kind = "celery" }}')
        self.records.clear()
        result = self.propagate(self.workspace(text2), "t/api", "main~1...main")
        self.assertIn(("entry_points_unread", "t/web", False), self.kinds())
        self.assertEqual(result.unread_entry_points, ("t/web",))

    def test_the_results_do_not_depend_on_the_order_the_edges_and_repositories_are_declared_in(self):
        ws = self.build()
        reordered = dataclasses.replace(ws, edges=tuple(reversed(ws.edges)), repos=dict(reversed(list(ws.repos.items()))))
        a = self.run_lib(ws)
        self.records.clear()
        b = self.run_lib(reordered)
        for name in ("http_edges", "consumers", "contract_reads", "contract_changes", "findings", "unit_tests"):
            self.assertEqual(getattr(a, name), getattr(b, name), name)
        self.assertEqual([(r.area.repo, r.area.label, r.area.chains) for r in a.reached], [(r.area.repo, r.area.label, r.area.chains) for r in b.reached])
        self.assertEqual([(h.kind, h.from_repo, h.to_repo, h.seeds) for h in a.hops], [(h.kind, h.from_repo, h.to_repo, h.seeds) for h in b.hops])

    def test_a_failed_read_on_an_edge_is_recorded_on_that_edge_and_the_propagation_is_incomplete(self):
        self.build()
        self.urls["web"] = "file://" + str(self.tmp / "nowhere")
        ws = self.workspace(FULL.replace("{hops}", "3").replace("{hub}", "50"))
        result = self.run_lib(ws)
        self.assertFalse(result.complete)
        self.assertEqual([r.area.label for r in result.reached], ["GET /api/orders/summary"])  # what was found is kept, and said to be partial
        self.assertIn(("edge_incomplete", "edge:http:t/web:t/api", True), self.kinds())
        self.assertTrue(any("t/web" in why for why in result.reasons))
        self.assertTrue(any(h.kind == "http" and not h.complete for h in result.hops))
        self.assertFalse(any(r.kind == "hop_cap" for r in self.records))


class Cycles(CrossCase):
    """Two repositories that hand each other seeds for ever: the walker processes a repository and a set of seeds once."""

    SHARED = True

    WS = """
workspace = "t"
[limits]
hops = 50

[repos."t/a"]
host = "git"
remote = "{a}"
language = "python"
roles = ["backend"]
branches = ["main"]

[repos."t/b"]
host = "git"
remote = "{b}"
language = "python"
roles = ["backend"]
branches = ["main"]

[[edges]]
kind = "http"
from = "t/a"
to = "t/b"
"""

    class Ping:
        kind = "http"

        def applies(self, run, p, edge):
            return True

        def follow(self, run, p, edge):
            other = edge.to_repo if p.repo == edge.from_repo else edge.from_repo
            path = "b.py" if other == "t/b" else "a.py"
            pin = run.primary(other)
            ev = run.language(other).file_text(pin.sha, path).text.split("\n")[1]
            from reachmap.model import Evidence

            e = Evidence(repo=other, path=path, line=2, ref=pin.sha, detail=ev)
            return [Hop("http", p.repo, other, (edge.from_repo, edge.to_repo), "forward", p.depth + 1, (pin,), (), (e,), (e,), (Seed(other, path, (2,), pin.sha, (e,)),))]

    def test_a_cycle_between_repositories_terminates(self):
        self.repos(a=[{"files": {"a.py": "def fa():\n    return 1\n"}}, {"files": {"a.py": "def fa():\n    return 2\n"}}], b=[{"files": {"b.py": "def fb():\n    return 1\n"}}])
        ws = self.workspace(self.WS)
        with mock.patch.dict(edges._FACTORIES, {"http": self.Ping}):
            change = resolve(ws, "t/a", git=self.git, compare=f"{self.sha('a', 'main~1')}...{self.sha('a')}")
            result = propagate.propagate(ws, "t/a", change, git=self.git)
        self.assertEqual([(p.repo, p.seed.path if p.seed else None) for p in result.passes], [("t/a", None), ("t/b", "b.py"), ("t/a", "a.py")])
        self.assertEqual(len(result.hops), 3)  # the third hop meets a seed that was processed: it is a hop, and no new pass
        self.assertEqual([p.depth for p in result.passes], [0, 1, 2])


class AsOf(CrossCase):
    """`as_of` pins the primary branch and the branch tips the edges read as of a time (the commits of a built repository are an hour apart)."""

    def build(self):
        gone = API["app/service.py"].replace("invoice.reference or 'none'", "'none'")  # the api stops reading the field in its second commit
        self.repos(
            lib=[{"files": {"lib/models/invoice.py": INVOICE}}, {"files": {"lib/models/invoice.py": INVOICE_AFTER}}],
            api=[{"files": API}, {"files": {"app/service.py": gone}}], web=[{"files": WEB}],
        )
        return self.workspace(FULL.replace("{hops}", "3").replace("{hub}", "50"))

    @staticmethod
    def at(hours: int, extra: int = 0) -> str:
        return datetime.fromtimestamp(build.EPOCH + 3600 * hours + extra, timezone.utc).isoformat()

    def test_the_consumer_is_read_as_it_was_at_the_time(self):
        ws = self.build()
        now = self.propagate(ws, "t/lib", "main~1...main")
        self.assertEqual(now.contract_reads, ())  # the api's primary branch has stopped reading the field
        then = self.propagate(ws, "t/lib", "main~1...main", as_of=self.at(1, 10))
        self.assertEqual([(r.repo, r.path) for r in then.contract_reads], [("t/api", "app/service.py")])
        self.assertEqual({c.sha for c in then.consumers if c.repo == "t/api"}, {self.sha("api", "main~1")})  # the manifest was read at the api's first commit
        self.assertEqual(self.sha("api", "main~1"), then.contract_reads[0].evidence[0].ref)

    def test_a_time_before_the_consumer_had_a_commit_is_an_unreadable_branch_and_not_an_empty_consumer(self):
        ws = self.build()
        result = self.propagate(ws, "t/lib", "main~1...main", as_of="2000-01-01T00:00:00Z")
        self.assertFalse(result.complete)
        self.assertTrue(any(r.kind == "read_unreadable" and "t/api" in r.subject and r.blocks_selection for r in self.records))
        self.assertEqual(result.contract_reads, ())

    def test_the_branch_pins_of_a_released_change_are_read_as_of_the_time(self):
        ws = self.build()
        pins = propagate.Run(ws, self.git, as_of=self.at(1, 10)).branch_pins("t/api")
        self.assertEqual({b: p.sha for b, p in pins.items()}, {"main": self.sha("api", "main~1")})
        later = propagate.Run(ws, self.git).branch_pins("t/api")
        self.assertEqual({b: p.sha for b, p in later.items()}, {"main": self.sha("api")})

    def test_an_invalid_time_is_a_value_error_and_not_a_record(self):
        run = propagate.Run(self.build(), self.git, as_of="yesterday")
        with self.assertRaises(ValueError):
            run.primary("t/api")


class Edges(CrossCase):
    SHARED = True

    def test_a_declared_edge_of_a_kind_with_no_adapter_is_listed_not_followed_and_not_an_absence(self):
        changed = API["app/service.py"].replace("'none'", "'n/a'")
        self.repos(
            lib=[{"files": {"lib/models/invoice.py": INVOICE}}], api=[{"files": API}, {"files": {"app/service.py": changed}}], web=[{"files": WEB}],
        )
        text = FULL.replace("{hops}", "3").replace("{hub}", "50") + '\n[[edges]]\nkind = "event"\nfrom = "t/api"\nto = "t/web"\ntopics_glob = "**/topics.py"\n'
        result = self.propagate(self.workspace(text), "t/api", "main~1...main")
        self.assertEqual([(e.kind, e.from_repo, e.to_repo) for e in result.unfollowed], [("event", "t/api", "t/web")])
        self.assertEqual([p.repo for p in result.passes], ["t/api", "t/web"])  # the http edge was followed, the event edge was not
        self.assertFalse(any(r.kind == "hop_cap" for r in self.records))  # not a cap, not a failed read, not an answer of nothing
        self.assertEqual(sorted(edges.KINDS), ["contract", "http", "package"])
        self.assertIsNone(edges.adapter_for("event"))

    def test_the_registry_replaces_a_kind(self):
        saved = dict(edges._FACTORIES)
        try:
            edges.register("library", lambda: object())
            self.assertIsNotNone(edges.adapter_for("library"))
        finally:
            edges._FACTORIES.clear()
            edges._FACTORIES.update(saved)
        self.assertIsNone(edges.adapter_for("library"))


if __name__ == "__main__":
    unittest.main()
