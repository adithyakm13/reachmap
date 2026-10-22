"""The contract edge (M8): how a model field changed (classified with `ast`), and the consumer code that reads it:
code and not comments, distinctive names only, files that import the package, every read with its evidence, and a
read that could not be looked for an incompleteness rather than none."""

import ast
import dataclasses
import unittest

from reachmap import propagate
from reachmap.changeset import resolve
from reachmap.edges.contract import ModelRefs, classify, defines_name, field_reads, field_shape, read_sites
from reachmap.adapters.python.syntax import parse
from tests.support_cross import CrossCase

WORKSPACE = """
workspace = "t"
[limits]
hops = 3
distinct_max = 3

[repos."t/lib"]
host = "git"
remote = "{lib}"
language = "python"
roles = ["contract"]
branches = ["main"]
contract = {{ kind = "pydantic", root = "lib/models", package = "acme_lib", versions = "git-tags", tag_prefix = "v" }}

[repos."t/app"]
host = "git"
remote = "{app}"
language = "python"
roles = ["backend"]
branches = ["main"]
tests = {{ markers = ["tests/", "test_"] }}

{worker_repo}[[edges]]
kind = "package"
producer = "t/lib"
consumer = "t/app"
manifest = "requirements.txt"
pin_pattern = '^acme-lib==(.+)$'
import_marker = "acme_lib"

[[edges]]
kind = "contract"
producer = "t/lib"
consumer = "t/app"

{worker_edges}"""


WORKER_REPO = """[repos."t/worker"]
host = "git"
remote = "{worker}"
language = "python"
roles = ["backend"]
branches = ["main"]

"""
WORKER_EDGES = """
[[edges]]
kind = "package"
producer = "t/lib"
consumer = "t/worker"
manifest = "requirements.txt"
pin_pattern = '^acme-lib==(.+)$'
import_marker = "acme_lib"

[[edges]]
kind = "contract"
producer = "t/lib"
consumer = "t/worker"
"""


def workspace_text(worker=False):
    return WORKSPACE.replace("{worker_repo}", WORKER_REPO if worker else "").replace("{worker_edges}", WORKER_EDGES if worker else "")


def shape(text, owner="M", name="x"):
    return field_shape(ast.parse(text), owner.split("."), name)


def kinds(before, after, owner="M", name="x"):
    return classify(shape(before, owner, name), shape(after, owner, name))


def cls(body, name="M", base=""):
    return f"class {name}{base}:\n" + "".join(f"    {line}\n" for line in body.split("\n"))


# ------------------------------------------------------------------------------ how a field changed


class Classification(unittest.TestCase):
    def test_optional_to_required_is_tightened_and_required_to_optional_is_relaxed(self):
        table = [
            ("x: str | None = None", "x: str", ["contract-tightened"]),
            ("x: str", "x: str | None = None", ["contract-relaxed"]),
            ("x: int = 5", "x: int", ["contract-tightened"]),
            ("x: int", "x: int = 5", ["contract-relaxed"]),
            ("x: Optional[int]", "x: int", ["contract-tightened"]),
            ("x: int", "x: Optional[int]", ["contract-relaxed"]),
            ("x: int = Field(default=5)", "x: int = Field(...)", ["contract-tightened"]),
            ("x: int = Field(...)", "x: int = Field(default_factory=list)", ["contract-relaxed"]),
            ("x: int = Field()", "x: int = Field(7)", ["contract-relaxed"]),
            ("x: Annotated[str | None, Field(max_length=3)] = None", "x: Annotated[str, Field(max_length=3)]", ["contract-tightened"]),
            ("x: 'str | None' = None", "x: str", ["contract-tightened"]),
            ("x: int = 1", "x: int = 2", ["default_changed"]),
        ]
        for before, after, expected in table:
            with self.subTest(before=before, after=after):
                self.assertEqual(list(kinds(cls(before), cls(after))), expected)

    def test_a_changed_type_and_a_changed_default_are_their_own_kinds(self):
        self.assertEqual(kinds(cls("x: int"), cls("x: str")), ("type_changed",))
        self.assertEqual(kinds(cls('x: str = "a"'), cls('x: str = "b"')), ("default_changed",))
        self.assertEqual(kinds(cls("x: str | None = None"), cls("x: int")), ("contract-tightened", "type_changed"))  # both, the tightening first
        self.assertEqual(kinds(cls("x: int = 1"), cls("x: int = 1")), ("modified",))  # a comment or spacing changed

    def test_nullable_alone_is_not_a_type_change(self):
        self.assertEqual(kinds(cls("x: str | None = None"), cls("x: Optional[str] = None")), ("modified",))

    def test_added_removed_and_members_that_are_not_fields(self):
        self.assertEqual(kinds(cls("y: int"), cls("y: int\nx: int = 0")), ("added",))
        self.assertEqual(kinds(cls("y: int"), cls("y: int\nx: int")), ("added_required",))
        self.assertEqual(kinds(cls("y: int\nx: int"), cls("y: int")), ("removed",))
        self.assertEqual(kinds(cls("def x(self):\n    return 1"), cls("def x(self):\n    return 2")), ("member_changed",))
        self.assertEqual(kinds(cls("x: int"), cls("def x(self):\n    return 1")), ("member_changed",))

    def test_a_nested_class_is_found_by_its_path(self):
        before = "class Outer:\n    class Inner:\n        x: int | None = None\n"
        after = "class Outer:\n    class Inner:\n        x: int\n"
        self.assertEqual(kinds(before, after, "Outer.Inner"), ("contract-tightened",))
        self.assertIsNone(shape(before, "Outer.Missing"))

    def test_nothing_known_about_either_side_is_unclassified_never_guessed(self):
        self.assertEqual(classify(None, None), ("unclassified",))


# ------------------------------------------------------------------------------ the reads in a file


class Reads(unittest.TestCase):
    def reads(self, source, name="settlement_reference", **kw):
        return field_reads(parse(source), name, **kw)

    def test_attribute_keyword_subscript_and_getattr_are_reads(self):
        src = (
            "a = invoice.settlement_reference\n"
            "b = Invoice(number='1', settlement_reference='x')\n"
            "c = data['settlement_reference']\n"
            "d = getattr(invoice, 'settlement_reference', None)\n"
            "invoice.settlement_reference = 'y'\n"
        )
        self.assertEqual(self.reads(src), [(1, "attribute"), (2, "keyword"), (3, "subscript"), (4, "getattr"), (5, "attribute")])

    def test_a_comment_a_string_a_dict_key_and_a_longer_name_are_not_reads(self):
        src = (
            "# settlement_reference is filled in by the nightly job\n"
            "doc = 'the settlement_reference of an invoice'\n"
            "payload = {'settlement_reference': 1}\n"
            "x = invoice.settlement_reference_id\n"
            "def settlement_reference(): pass\n"
            "settlement_reference = 3\n"
        )
        self.assertEqual(self.reads(src), [])

    def test_a_multi_line_chain_is_cited_at_the_line_of_the_name(self):
        src = "value = (\n    invoice\n    .settlement_reference\n)\n"
        self.assertEqual(self.reads(src), [(3, "attribute")])

    def test_a_class_is_read_by_its_name_as_an_identifier(self):
        src = "from acme_lib.models.invoice import Invoice\nx = Invoice(number='1')\n'Invoice'\n# Invoice\n"
        self.assertEqual(self.reads(src, "Invoice", as_class=True), [(1, "import"), (2, "name")])

    def test_a_file_that_does_not_parse_has_no_reads_so_the_caller_must_not_call_it_empty(self):
        parsed = parse("def (:\n")
        self.assertTrue(parsed.problems)
        self.assertEqual(field_reads(parsed, "x"), [])


# ------------------------------------------------------------------------------ the edge


INVOICE = "from pydantic import BaseModel\n\n\nclass Invoice(BaseModel):\n    number: str\n    settlement_reference: str | None = None\n    interval: str = 'monthly'\n"
INVOICE_AFTER = INVOICE.replace("settlement_reference: str | None = None", "settlement_reference: str")
SETTLEMENT = "from pydantic import BaseModel\n\n\nclass Settlement(BaseModel):\n    settlement_reference: str | None = None\n    interval: str = 'daily'\n"
OTHERS = {f"lib/models/m{i}.py": f"class M{i}:\n    interval: int = {i}\n" for i in range(3)}
SERVICE = (
    "from acme_lib.models.invoice import Invoice\n\n\n"
    "def reference_for(invoice: Invoice) -> str:\n"
    "    # settlement_reference may be missing\n"
    "    return invoice.settlement_reference or 'pending'\n\n\n"
    "def build(number: str) -> Invoice:\n"
    "    return Invoice(number=number, settlement_reference=None)\n"
)


class Contract(CrossCase):
    SHARED = True

    def build(self, app_files, lib_after=INVOICE_AFTER, worker_files=None, lib_before=INVOICE, extra_lib=None):
        """The library at two commits, an app, and a worker only when the test gives it files."""
        lib_files = {"lib/models/invoice.py": lib_before, "lib/models/settlement.py": SETTLEMENT, **OTHERS, **(extra_lib or {})}
        repos = {
            "lib": [{"files": lib_files}, {"files": {"lib/models/invoice.py": lib_after}}],
            "app": [{"files": {"requirements.txt": "acme-lib==1.0.0\n", **app_files}}],
        }
        if worker_files is not None:
            repos["worker"] = [{"files": {"requirements.txt": "acme-lib==1.0.0\n", **worker_files}}]
        self.repos(**repos)
        return self.workspace(workspace_text(worker_files is not None))

    def reads(self, result):
        return sorted((r.repo, r.path, r.line, r.symbol, r.form) for r in result.contract_reads)


class Edge(Contract):
    def test_a_read_is_code_in_a_file_that_imports_the_package_with_the_import_and_the_read_as_evidence(self):
        ws = self.build({"app/service.py": SERVICE})
        result = self.propagate(ws, "t/lib", "main~1...main")
        self.assertEqual(self.reads(result), [
            ("t/app", "app/service.py", 6, "reference_for", "attribute"), ("t/app", "app/service.py", 10, "build", "keyword"),
        ])  # the comment on line 5 is no read
        first = result.contract_reads[0]
        self.assertEqual([e.detail for e in first.evidence], ["from acme_lib.models.invoice import Invoice", "    return invoice.settlement_reference or 'pending'"])
        self.assertEqual({e.ref for e in first.evidence}, {self.sha("app")})  # the consumer's primary branch, pinned
        (change,) = result.contract_changes
        self.assertEqual((change.symbol, change.kinds, change.distinct, change.definitions), ("Invoice.settlement_reference", ("contract-tightened",), True, 2))
        self.assertEqual([p.repo for p in result.passes], ["t/lib", "t/app", "t/app"])  # a pass per consumer function that reads it
        self.assertEqual(sorted(p.seed.lines for p in result.passes if p.seed), [(6,), (10,)])
        self.assertEqual([k for k, _s, _b in self.kinds()], [])
        self.assert_evidence_resolves(result)

    def test_a_field_named_only_in_a_comment_is_no_read_and_the_absence_is_established(self):
        ws = self.build({"app/notes.py": "# settlement_reference is documented elsewhere\nx = 1\n"})
        result = self.propagate(ws, "t/lib", "main~1...main")
        self.assertEqual((result.contract_reads, result.contract_candidates), ((), ()))
        self.assertEqual(self.kinds(), [])  # every consumer file was read: no record
        self.assertTrue(result.complete)

    def test_a_generic_field_is_not_distinctive_and_matches_nothing(self):
        ws = self.build({"app/service.py": "from acme_lib.models.invoice import Invoice\n\n\ndef f(i):\n    return i.interval\n"},
                        lib_after=INVOICE.replace("interval: str = 'monthly'", "interval: str = 'weekly'"))
        result = self.propagate(ws, "t/lib", "main~1...main")
        self.assertEqual((result.contract_reads, result.contract_candidates), ((), ()))  # nothing proves `i` is an Invoice, and the name links nothing
        (change,) = result.contract_changes
        self.assertEqual((change.distinct, change.definitions, change.kinds), (False, 5, ("default_changed",)))
        self.assertEqual(self.kinds(), [("not_distinctive", "interval", False)])  # once, though two edges asked
        self.assertEqual([p.repo for p in result.passes], ["t/lib"])

    def test_a_read_in_a_file_that_does_not_import_the_package_is_a_candidate_listed_apart_and_not_followed(self):
        ws = self.build({"app/other.py": "def f(x):\n    return x.settlement_reference\n"})
        result = self.propagate(ws, "t/lib", "main~1...main")
        self.assertEqual(result.contract_reads, ())
        (cand,) = result.contract_candidates
        self.assertEqual((cand.path, cand.line, cand.candidate, [e.detail for e in cand.evidence]), ("app/other.py", 2, True, ["    return x.settlement_reference"]))
        self.assertEqual([p.repo for p in result.passes], ["t/lib"])  # nothing was handed on

    def test_the_import_cited_is_the_one_of_the_class_that_holds_the_field(self):
        src = "from acme_lib.models.settlement import Settlement\nfrom acme_lib.models.invoice import Invoice\n\n\ndef f(i: Invoice):\n    return i.settlement_reference\n"
        result = self.propagate(self.build({"app/service.py": src}), "t/lib", "main~1...main")
        (read,) = result.contract_reads
        self.assertEqual(read.evidence[0].detail, "from acme_lib.models.invoice import Invoice")

    def test_tests_of_a_consumer_are_not_reads(self):
        ws = self.build({"app/service.py": SERVICE, "tests/test_service.py": "from acme_lib.models.invoice import Invoice\n\n\ndef test_x():\n    assert Invoice(number='1').settlement_reference is None\n"})
        result = self.propagate(ws, "t/lib", "main~1...main")
        self.assertEqual({r.path for r in result.contract_reads}, {"app/service.py"})

    def test_subscript_and_getattr_reads_in_the_second_consumer(self):
        worker = {"worker.py": "from acme_lib.models.invoice import Invoice\n\n\ndef f(d, i):\n    a = d['settlement_reference']\n    return a, getattr(i, 'settlement_reference')\n"}
        result = self.propagate(self.build({"app/x.py": "x = 1\n"}, worker_files=worker), "t/lib", "main~1...main")
        self.assertEqual(self.reads(result), [("t/worker", "worker.py", 6, "f", "getattr")])  # `d[...]` is a subscript of something that is not proven to be the model
        self.assertEqual([(r.path, r.line, r.form) for r in result.contract_candidates], [("worker.py", 5, "subscript")])
        (hop,) = [h for h in result.hops if h.kind == "contract"]
        self.assertEqual(hop.seeds[0].lines, (6,))

    def test_a_consumer_that_cannot_be_read_is_an_incomplete_edge_never_no_reads(self):
        self.build({"app/service.py": SERVICE})
        self.urls["app"] = "file://" + str(self.tmp / "nowhere")
        ws = self.workspace(workspace_text())
        result = self.propagate(ws, "t/lib", "main~1...main")
        self.assertEqual(result.contract_reads, ())
        self.assertFalse(result.complete)
        self.assertIn(("edge_incomplete", "edge:contract:t/lib:t/app", True), self.kinds())
        self.assertTrue(any(h.kind == "contract" and h.to_repo == "t/app" and not h.complete for h in result.hops))

    def test_a_consumer_in_a_language_without_an_adapter_is_an_incomplete_edge_never_no_reads(self):
        from unittest import mock

        from reachmap import config

        text = workspace_text().replace('[repos."t/app"]\nhost = "git"\nremote = "{app}"\nlanguage = "python"', '[repos."t/app"]\nhost = "git"\nremote = "{app}"\nlanguage = "ruby"')
        self.build({"app/service.py": SERVICE})
        with mock.patch.object(config, "_LANGUAGES", {*config._LANGUAGES, "ruby"}):
            result = self.propagate(self.workspace(text), "t/lib", "main~1...main")
        self.assertEqual(result.contract_reads, ())
        self.assertFalse(result.complete)
        self.assertIn(("edge_incomplete", "edge:contract:t/lib:t/app", True), self.kinds())
        self.assertEqual([c.repo for c in result.consumers], ["t/app"])  # the package edge needs no language: the pin is read

    def test_a_search_cut_short_is_incomplete_and_recorded(self):
        ws = self.build({"app/service.py": SERVICE, **{f"app/f{i}.py": "x = 1\n" for i in range(5)}})
        git_run = propagate.Run(ws, self.git)
        git_run.language("t/app").examine_cap = 2
        change = resolve(ws, "t/lib", git=self.git, compare=f"{self.sha('lib', 'main~1')}...{self.sha('lib')}")
        result = propagate.walk_edges(git_run, "t/lib", change)
        self.assertFalse(result.complete)
        self.assertIn(("search_truncated", True), {(k, b) for k, _s, b in self.kinds()})
        self.assertIn(("edge_incomplete", "edge:contract:t/lib:t/app", True), self.kinds())

    def test_a_cut_search_that_still_found_a_read_is_not_taken_as_the_whole_list(self):
        """Mutation M19: the match was in the files examined, the cut was ignored, and the read list read as complete."""
        ws = self.build({"app/a_service.py": SERVICE, **{f"app/f{i}.py": "x = 1\n" for i in range(5)}})
        run = propagate.Run(ws, self.git)
        run.language("t/app").examine_cap = 2
        change = resolve(ws, "t/lib", git=self.git, compare=f"{self.sha('lib', 'main~1')}...{self.sha('lib')}")
        result = propagate.walk_edges(run, "t/lib", change)
        self.assertEqual([r.path for r in result.contract_reads], ["app/a_service.py", "app/a_service.py"])  # what was found is kept
        self.assertFalse(result.complete)
        self.assertTrue(any("was cut after" in why for why in result.reasons))
        self.assertIn(("edge_incomplete", "edge:contract:t/lib:t/app", True), self.kinds())
        self.assertTrue(all(not h.complete for h in result.hops if h.kind == "contract"))

    def test_a_consumer_file_that_does_not_parse_makes_the_edge_incomplete(self):
        ws = self.build({"app/broken.py": "def (:\n    x.settlement_reference\n"})
        result = self.propagate(ws, "t/lib", "main~1...main")
        self.assertFalse(result.complete)
        self.assertEqual(result.contract_reads, ())
        self.assertIn(("edge_incomplete", "edge:contract:t/lib:t/app", True), self.kinds())

    def test_a_statement_outside_every_class_is_a_stated_gap_not_no_consumers(self):
        ws = self.build({"app/service.py": SERVICE}, lib_after=INVOICE + "\nprint('x')\n")
        result = self.propagate(ws, "t/lib", "main~1...main")
        self.assertIn(("contract_change_unscoped", "lib/models/invoice.py", True), self.kinds())
        self.assertFalse(result.complete)

    def test_a_removed_class_is_followed_by_its_name(self):
        service = "from acme_lib.models.settlement import Settlement\n\n\ndef f():\n    return Settlement()\n"
        self.repos(
            lib=[{"files": {"lib/models/settlement.py": SETTLEMENT, "lib/models/invoice.py": INVOICE}}, {"delete": ["lib/models/settlement.py"]}],
            app=[{"files": {"requirements.txt": "acme-lib==1.0.0\n", "app/service.py": service}}],
        )
        result = self.propagate(self.workspace(workspace_text()), "t/lib", "main~1...main")
        (change,) = result.contract_changes
        self.assertEqual((change.symbol, change.kinds, change.change), ("Settlement", ("removed",), "removed"))
        self.assertEqual([(r.path, r.line, r.form) for r in result.contract_reads], [("app/service.py", 1, "import"), ("app/service.py", 5, "name")])

    def test_a_changed_method_is_read_as_an_attribute(self):
        before = INVOICE + "\n    def total(self):\n        return 1\n"
        after = INVOICE + "\n    def total(self):\n        return 2\n"
        service = "from acme_lib.models.invoice import Invoice\n\n\ndef f(i: Invoice):\n    return i.total()\n"
        result = self.propagate(self.build({"app/service.py": service}, lib_before=before, lib_after=after), "t/lib", "main~1...main")
        (change,) = result.contract_changes
        self.assertEqual((change.symbol, change.kinds), ("Invoice.total", ("member_changed",)))
        self.assertEqual([(r.path, r.line) for r in result.contract_reads], [("app/service.py", 5)])

    def test_an_added_field_has_no_readers_yet(self):
        after = INVOICE + "    due: str | None = None\n"
        result = self.propagate(self.build({"app/service.py": SERVICE}, lib_after=after), "t/lib", "main~1...main")
        (change,) = result.contract_changes
        self.assertEqual((change.symbol, change.kinds), ("Invoice.due", ("added",)))
        self.assertEqual(result.contract_reads, ())

    def test_the_consumers_pass_reaches_what_the_read_reaches(self):
        """The consumer line is the changed line of the consumer's own pass: its callers, and the entry points through them."""
        app = {
            "app/service.py": SERVICE,
            "app/handlers.py": "from app.service import reference_for\n\n\ndef handler(i):\n    return reference_for(i)\n",
            "tests/test_handlers.py": "from app import handlers\n\n\ndef test_h():\n    handlers.handler(None)\n",
        }
        result = self.propagate(self.build(app), "t/lib", "main~1...main")
        pass_ = next(p for p in result.passes if p.seed and p.seed.lines == (6,))
        (impact,) = pass_.analysis.impacts
        self.assertEqual([c.path for c in impact.callers], ["app/handlers.py"])
        self.assertEqual([s.name for s in impact.symbols], ["reference_for"])
        self.assertEqual(pass_.hop.kind, "contract")


REFS = ModelRefs(owner="Invoice", marker="acme_lib")


def sites(source, name="settlement_reference"):
    return [(line, form, proven) for line, form, proven in read_sites(parse(source), name, REFS)]


class Receivers(unittest.TestCase):
    """A read is backed when its receiver is provably the model in that function."""

    IMPORT = "from acme_lib.models.invoice import Invoice\n"

    def test_a_parameter_annotated_with_the_model_is_proven_in_every_spelling_of_the_annotation(self):
        for ann in ("Invoice", "Invoice | None", "Optional[Invoice]", "'Invoice'", "Union[Invoice, None]", "Annotated[Invoice, 1]", "m.Invoice"):
            with self.subTest(annotation=ann):
                src = self.IMPORT + f"def f(i: {ann}):\n    return i.settlement_reference\n"
                self.assertEqual(sites(src), [(3, "attribute", True)])

    def test_variables_bound_from_the_model_its_constructors_or_a_collection_of_it_are_proven(self):
        src = self.IMPORT + (
            "def f(raw, many: list[Invoice], seq: Sequence[Invoice]):\n"
            "    a = Invoice(number='1')\n"
            "    b = Invoice.model_validate(raw)\n"
            "    c = Invoice.model_validate_json(raw)\n"
            "    d = b\n"
            "    for e in many:\n        e.settlement_reference\n"
            "    for g in seq:\n        g.settlement_reference\n"
            "    return [h.settlement_reference for h in many], a.settlement_reference, b.settlement_reference, c.settlement_reference, d.settlement_reference\n"
        )
        self.assertEqual([(line, proven) for line, _f, proven in sites(src)], [(8, True), (10, True), (11, True)])  # the reads on line 11 are one site

    def test_an_import_alias_and_isinstance_prove_the_receiver_and_an_unrelated_name_does_not(self):
        src = "from acme_lib.models.invoice import Invoice as Inv\n\n\ndef f(a: Inv, b, c: Other):\n    if isinstance(b, Inv):\n        b.settlement_reference\n    return a.settlement_reference, c.settlement_reference\n"
        self.assertEqual(sites(src), [(6, "attribute", True), (7, "attribute", False), (7, "attribute", True)])

    def test_scopes_do_not_leak_and_a_nested_function_sees_the_outer_variables(self):
        src = self.IMPORT + (
            "def f(a: Invoice):\n    def inner():\n        return a.settlement_reference\n    return inner\n\n"
            "def g(a):\n    return a.settlement_reference\n\n"
            "def h():\n    return a.settlement_reference\n"
        )
        self.assertEqual(sites(src), [(4, "attribute", True), (8, "attribute", False), (11, "attribute", False)])

    def test_a_keyword_is_proven_by_the_callee_a_subscript_and_an_untyped_getattr_are_not(self):
        src = self.IMPORT + (
            "def f(i: Invoice, row, other):\n"
            "    Invoice(number='1', settlement_reference=None)\n"
            "    other(settlement_reference=1)\n"
            "    getattr(i, 'settlement_reference')\n"
            "    getattr(row, 'settlement_reference')\n"
            "    return row['settlement_reference']\n"
        )
        self.assertEqual(sites(src), [(3, "keyword", True), (4, "keyword", False), (5, "getattr", True), (6, "getattr", False), (7, "subscript", False)])

    def test_the_consumers_own_definitions_of_a_name_are_found_in_every_form(self):
        for source in ("class A:\n    settlement_reference: str\n", "class A:\n    settlement_reference = 1\n", "settlement_reference = 1\n",
                       "class A:\n    def settlement_reference(self):\n        pass\n", "def settlement_reference():\n    pass\n",
                       "class A:\n    def __init__(self):\n        self.settlement_reference = 1\n", "class settlement_reference:\n    pass\n"):
            with self.subTest(source=source):
                self.assertEqual(defines_name(parse(source), "settlement_reference"), 1)
        for source in ("def f():\n    settlement_reference = 1\n    return settlement_reference\n", "x = o.settlement_reference\n", "f(settlement_reference=1)\n"):
            with self.subTest(source=source):
                self.assertEqual(defines_name(parse(source), "settlement_reference"), 0)  # a local variable, a read and a keyword define nothing


class Backing(Contract):
    def reads(self, result):
        return sorted((r.path, r.line, r.form) for r in result.contract_reads), sorted((r.path, r.line, r.form) for r in result.contract_candidates)

    def test_an_untyped_receiver_is_backed_when_the_consumer_defines_the_name_nowhere_and_a_candidate_when_it_does(self):
        service = "from acme_lib.models.invoice import Invoice\n\n\ndef f(row):\n    return row.settlement_reference\n"
        result = self.propagate(self.build({"app/service.py": service}), "t/lib", "main~1...main")
        self.assertEqual(self.reads(result), ([("app/service.py", 5, "attribute")], []))
        self.assertEqual(result.contract_reads[0].basis, "name")
        for definition in ("class Row:\n    settlement_reference: str\n", "class Row:\n    def __init__(self):\n        self.settlement_reference = 1\n",
                           "class Row:\n    def settlement_reference(self):\n        return 1\n"):
            with self.subTest(definition=definition):
                self.records.clear()
                result = self.propagate(self.build({"app/service.py": service, "app/rows.py": definition}), "t/lib", "main~1...main")
                self.assertEqual(self.reads(result), ([], [("app/service.py", 5, "attribute")]))
                self.assertEqual([p.repo for p in result.passes], ["t/lib"])  # a candidate is not followed

    def test_a_typed_receiver_is_backed_even_when_the_consumer_defines_the_name_and_even_for_a_generic_name(self):
        service = "from acme_lib.models.invoice import Invoice\n\n\ndef f(i: Invoice, s):\n    return i.settlement_reference, s.settlement_reference\n"
        result = self.propagate(self.build({"app/service.py": service, "app/rows.py": "class Row:\n    settlement_reference: str\n"}), "t/lib", "main~1...main")
        self.assertEqual(self.reads(result), ([("app/service.py", 5, "attribute")], [("app/service.py", 5, "attribute")]))  # the typed read and, apart, the untyped one
        self.assertEqual([(r.basis, r.candidate) for r in result.contract_reads], [("typed", False)])
        generic = "from acme_lib.models.invoice import Invoice\n\n\ndef f(i: Invoice, s):\n    return i.interval, s.interval\n"
        result = self.propagate(self.build({"app/service.py": generic}, lib_after=INVOICE.replace("interval: str = 'monthly'", "interval: str = 'weekly'")), "t/lib", "main~1...main")
        self.assertEqual(self.reads(result), ([("app/service.py", 5, "attribute")], []))  # `interval` is in five models: only the typed read counts
        self.assertEqual(result.contract_reads[0].basis, "typed")
        self.assertEqual(self.kinds(), [("not_distinctive", "interval", False)])

    def test_a_string_subscript_on_an_unknown_receiver_is_a_candidate(self):
        service = "from acme_lib.models.invoice import Invoice\n\n\ndef f(row: dict):\n    return row['settlement_reference']\n"
        result = self.propagate(self.build({"app/service.py": service}), "t/lib", "main~1...main")
        self.assertEqual(self.reads(result), ([], [("app/service.py", 5, "subscript")]))

    def test_the_known_limit_an_untyped_receiver_with_a_name_only_the_producer_defines_is_backed(self):
        """`resp.status` on a requests response is read as the field when the producer defines `status` once and nothing proves
        the receiver: the distinctiveness cap and the consumer's own definitions are the guard, and they do not see this."""
        before, after = INVOICE + "    status: str | None = None\n", INVOICE + "    status: str\n"
        service = "from acme_lib.models.invoice import Invoice\nimport requests\n\n\ndef ping(url):\n    resp = requests.get(url)\n    return resp.status\n"
        result = self.propagate(self.build({"app/service.py": service}, lib_before=before, lib_after=after), "t/lib", "main~1...main")
        self.assertEqual(self.reads(result), ([("app/service.py", 7, "attribute")], []))
        self.assertEqual(result.contract_reads[0].basis, "name")

    def test_a_definition_count_that_could_not_be_made_whole_is_an_incomplete_edge_and_only_typed_reads_follow(self):
        extra = {"lib/models/broken.py": "class Broken(:\n    settlement_reference = 1\n"}
        service = "from acme_lib.models.invoice import Invoice\n\n\ndef f(i: Invoice, row):\n    return i.settlement_reference, row.settlement_reference\n"
        result = self.propagate(self.build({"app/service.py": service}, extra_lib=extra), "t/lib", "main~1...main")
        (change,) = result.contract_changes
        self.assertEqual((change.distinct, change.counted), (False, False))
        self.assertEqual(self.reads(result), ([("app/service.py", 5, "attribute")], []))  # the typed read stays, the name based one does not
        self.assertEqual([r.basis for r in result.contract_reads], ["typed"])
        self.assertIn(("edge_incomplete", "edge:contract:t/lib:t/app", True), self.kinds())
        self.assertFalse(result.complete)
        self.assertTrue(any("could not be counted completely" in why for why in result.reasons))


class Construction(Contract):
    LIB_AFTER = INVOICE + "    due_date: str\n"

    def test_an_added_required_field_is_followed_to_the_code_that_builds_the_model(self):
        service = (
            "from acme_lib.models.invoice import Invoice\nfrom acme_lib.models.invoice import Invoice as Inv\n\n\n"
            "def a(number):\n    return Invoice(number=number)\n\n\n"
            "def b(raw):\n    return Inv.model_validate(raw)\n\n\n"
            "def c(data):\n    return Invoice(**data)\n\n\n"
            "def d(data):\n    return Invoice.parse_obj(data)\n\n\n"
            "def e(x: Invoice):\n    return x.number\n"
        )
        result = self.propagate(self.build({"app/service.py": service}, lib_after=self.LIB_AFTER), "t/lib", "main~1...main")
        (change,) = result.contract_changes
        self.assertEqual((change.symbol, change.kinds), ("Invoice.due_date", ("added_required",)))
        self.assertEqual([(r.line, r.symbol, r.form, r.basis) for r in result.contract_reads], [
            (6, "a", "construct", "typed"), (10, "b", "construct", "typed"), (14, "c", "construct", "typed"), (18, "d", "construct", "typed")])
        self.assertEqual([e.detail for e in result.contract_reads[0].evidence], ["from acme_lib.models.invoice import Invoice", "    return Invoice(number=number)"])
        self.assertEqual(len([p for p in result.passes if p.seed]), 4)  # each construction is the changed line of the consumer's own pass

    def test_the_constructions_that_cannot_be_seen_are_said(self):
        service = "from acme_lib.models.invoice import Invoice\n\n\ndef a(number):\n    return Invoice(number=number)\n"
        result = self.propagate(self.build({"app/service.py": service}, lib_after=self.LIB_AFTER), "t/lib", "main~1...main")
        self.assertIn(("contract_construction_unseen", "t/app", False), self.kinds())
        self.assertFalse(result.complete)
        self.records.clear()
        optional = self.propagate(self.build({"app/service.py": service}, lib_after=INVOICE + "    due_date: str | None = None\n"), "t/lib", "main~1...main")
        self.assertEqual(self.kinds(), [])  # an optional field breaks no construction
        self.assertEqual(optional.contract_reads, ())

    def test_a_file_that_does_not_import_the_package_does_not_build_its_model(self):
        service = "def a(number):\n    return Invoice(number=number)\n"
        result = self.propagate(self.build({"app/service.py": service}, lib_after=self.LIB_AFTER), "t/lib", "main~1...main")
        self.assertEqual(result.contract_reads, ())


class Models(Contract):
    def test_an_import_added_to_the_models_file_is_not_a_change_outside_every_class(self):
        after = INVOICE_AFTER.replace("from pydantic import BaseModel\n", "from typing import Optional\n\nfrom pydantic import BaseModel\n")
        result = self.propagate(self.build({"app/service.py": SERVICE}, lib_after=after), "t/lib", "main~1...main")
        self.assertEqual([k for k, _s, _b in self.kinds()], [])
        self.assertTrue(result.complete)
        self.assertEqual([c.symbol for c in result.contract_changes], ["Invoice.settlement_reference"])
        self.records.clear()
        removed = self.propagate(self.build({"app/service.py": SERVICE}, lib_before=INVOICE.replace("from pydantic import BaseModel\n", "import os\nfrom pydantic import BaseModel\n"), lib_after=INVOICE_AFTER), "t/lib", "main~1...main")
        self.assertEqual([k for k, _s, _b in self.kinds()], [])
        self.assertTrue(removed.complete)
        self.records.clear()
        statement = self.propagate(self.build({"app/service.py": SERVICE}, lib_after=INVOICE_AFTER + "\nprint('x')\n"), "t/lib", "main~1...main")
        self.assertIn(("contract_change_unscoped", "lib/models/invoice.py", True), self.kinds())  # a statement is still said
        self.assertFalse(statement.complete)


class Released(Contract):
    SHARED = False  # tags and branches: a repository of its own per test
    LEGACY = "from acme_lib.models.invoice import Invoice\n\n\ndef old(i: Invoice):\n    return i.settlement_reference\n"

    def build_released(self, release_pin="1.1.0", main_pin="1.0.0", legacy=None):
        lib_files = {"lib/models/invoice.py": INVOICE, "lib/models/settlement.py": SETTLEMENT, **OTHERS}
        legacy = self.LEGACY if legacy is None else legacy
        self.repos(
            lib=[{"files": lib_files, "tag": "v1.0.0"}, {"files": {"lib/models/invoice.py": INVOICE_AFTER}, "tag": "v1.1.0"}],
            app=[{"files": {"requirements.txt": f"acme-lib=={main_pin}\n", "app/x.py": "x = 1\n"}},
                 {"files": {"requirements.txt": f"acme-lib=={release_pin}\n", "app/legacy.py": legacy}, "branch": "release/1.x", "from": "main"}],
        )
        text = workspace_text().replace('[repos."t/app"]\nhost = "git"\nremote = "{app}"\nlanguage = "python"\nroles = ["backend"]\nbranches = ["main"]',
                                        '[repos."t/app"]\nhost = "git"\nremote = "{app}"\nlanguage = "python"\nroles = ["backend"]\nbranches = ["main", "release/*"]')
        return self.workspace(text)

    def test_a_released_change_is_searched_on_every_adopted_branch_and_each_read_is_pinned_where_it_was_found(self):
        ws = self.build_released()
        result = self.propagate(ws, "t/lib", "v1.0.0..v1.1.0", kind="range")
        (read,) = result.contract_reads
        self.assertEqual((read.path, read.branch, read.adoption, read.pin), ("app/legacy.py", "release/1.x", "adopted", "1.1.0"))
        self.assertEqual({e.ref for e in read.evidence}, {self.sha("app", "release/1.x")})  # the release branch, not main
        self.assertEqual([(h.kind, h.pins[0].ref) for h in result.hops if h.kind == "contract"], [("contract", "release/1.x")])
        self.assert_evidence_resolves(result)

    def test_a_branch_that_has_not_adopted_the_change_is_not_searched_and_identical_reads_are_one(self):
        ws = self.build_released(release_pin="1.0.0")
        self.assertEqual(self.propagate(ws, "t/lib", "v1.0.0..v1.1.0", kind="range").contract_reads, ())
        self.records.clear()
        # main and release/1.x both adopt it and both have the same line: one read, from the primary branch
        self.repos(
            lib=[{"files": {"lib/models/invoice.py": INVOICE, "lib/models/settlement.py": SETTLEMENT, **OTHERS}, "tag": "v1.0.0"}, {"files": {"lib/models/invoice.py": INVOICE_AFTER}, "tag": "v1.1.0"}],
            app=[{"files": {"requirements.txt": "acme-lib==1.1.0\n", "app/legacy.py": self.LEGACY}}, {"files": {"requirements.txt": "acme-lib==1.1.0\n"}, "branch": "release/1.x", "from": "main"}],
        )
        text = workspace_text().replace('branches = ["main"]\ntests', 'branches = ["main", "release/*"]\ntests')
        result = self.propagate(self.workspace(text), "t/lib", "v1.0.0..v1.1.0", kind="range")
        self.assertEqual([(r.branch, r.path) for r in result.contract_reads], [("main", "app/legacy.py")])


class Bundle(Contract):
    def test_the_result_lists_are_deterministic_and_every_item_has_evidence(self):
        ws = self.build({"app/service.py": SERVICE}, worker_files={"worker.py": "from acme_lib.models.invoice import Invoice\n\n\ndef f(i):\n    return i.settlement_reference\n"})
        a = self.propagate(ws, "t/lib", "main~1...main")
        self.assertEqual([(r.repo, r.path, r.line) for r in a.contract_reads], [("t/app", "app/service.py", 6), ("t/app", "app/service.py", 10), ("t/worker", "worker.py", 5)])
        for group in (a.contract_reads, a.contract_changes, a.consumers):
            for item in group:
                self.assertTrue(item.all_evidence())
        self.assertEqual([c.repo for c in a.consumers], ["t/app", "t/worker"])
        self.assertTrue(dataclasses.is_dataclass(a))


if __name__ == "__main__":
    unittest.main()
