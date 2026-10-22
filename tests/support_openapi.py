"""Helpers for the M16 OpenAPI tests: small specs and repositories built for one rule each (this half), and the scenario world, the
fixtures of `tests/fixtures/repos/oa-*` run through `reachmap analyse` (the second half, below).

A spec is a plain dict written with `json.dumps(..., indent=2)`, so the line of every key is predictable from the text
(`line_of`)."""

from __future__ import annotations

import copy
import json
from typing import Any

SPEC_PATH = "api/openapi.json"
HANDLERS = "app/handlers/*.py"


def spec(**over: Any) -> dict[str, Any]:
    """A small OpenAPI 3 document (the ledger): four operations, a shared parameter, a cycle between two schemas."""
    ok = {"200": {"description": "ok"}}
    body = {"200": {"description": "ok", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Invoice"}}}}}
    base = {
        "openapi": "3.0.3",
        "info": {"title": "Ledger", "version": "1"},
        "servers": [{"url": "https://api.example.invalid/v1"}],
        "paths": {
            "/invoices": {
                "get": {"operationId": "listInvoices", "tags": ["invoices"], "responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {"type": "array", "items": {"$ref": "#/components/schemas/Invoice"}}}}}}},
            },
            "/invoices/{invoiceId}": {
                "parameters": [{"$ref": "#/components/parameters/InvoiceId"}],
                "get": {"operationId": "getInvoice", "tags": ["invoices"], "responses": body},
                "delete": {"operationId": "voidInvoice", "tags": ["invoices"], "responses": ok},
            },
            "/customers/{customerId}": {
                "get": {"operationId": "getCustomer", "tags": ["customers"], "parameters": [{"name": "customerId", "in": "path", "required": True, "schema": {"type": "string"}}], "responses": ok},
            },
        },
        "components": {
            "parameters": {"InvoiceId": {"name": "invoiceId", "in": "path", "required": True, "schema": {"type": "string"}}},
            "schemas": {
                "Invoice": {"type": "object", "properties": {"id": {"type": "string"}, "total": {"$ref": "#/components/schemas/Money"}, "customer": {"$ref": "#/components/schemas/Customer"}}},
                "Customer": {"type": "object", "properties": {"id": {"type": "string"}, "invoices": {"type": "array", "items": {"$ref": "#/components/schemas/Invoice"}}}},
                "Money": {"type": "object", "required": ["amount"], "properties": {"amount": {"type": "integer"}}},
            },
        },
    }
    base.update(over)
    return base


def edited(edit: Any, **over: Any) -> dict[str, Any]:
    """The default spec after `edit(spec)`."""
    doc = copy.deepcopy(spec(**over))
    edit(doc)
    return doc


def text_of(doc: dict[str, Any]) -> str:
    return json.dumps(doc, indent=2) + "\n"


def line_of(text: str, needle: str, nth: int = 1) -> int:
    hits = [i for i, line in enumerate(text.split("\n"), 1) if needle in line]
    return hits[nth - 1]


def f(lines: list[str]) -> str:
    return "\n".join(lines) + "\n"


SERVICE = f([
    "def total_for(invoice_id):",
    "    return 0",
    "",
    "",
    "def all_totals():",
    "    return []",
])

INVOICES = f([
    "from app.services.ledger import all_totals, total_for",
    "",
    "",
    "def get_invoice(invoice_id):",
    "    return total_for(invoice_id)",
    "",
    "",
    "def list_invoices():",
    "    return all_totals()",
    "",
    "",
    "def void_invoice(invoice_id):",
    "    return None",
])

CUSTOMERS = f([
    "def get_customer(customer_id):",
    "    return {}",
])


def api_files(**over: str | None) -> dict[str, str]:
    """The files of a small API repository: the spec, three handler modules, a service module and a test. A keyword replaces a
    file (`spec`, `invoices`, `customers`, `service`) and None drops it."""
    files = {
        SPEC_PATH: text_of(spec()),
        "app/__init__.py": "",
        "app/handlers/__init__.py": "",
        "app/handlers/invoices.py": INVOICES,
        "app/handlers/customers.py": CUSTOMERS,
        "app/services/__init__.py": "",
        "app/services/ledger.py": SERVICE,
        "tests/test_ledger.py": "from app.services import ledger\n\n\ndef test_total():\n    assert ledger.total_for('x') == 0\n",
    }
    names = {"spec": SPEC_PATH, "invoices": "app/handlers/invoices.py", "customers": "app/handlers/customers.py", "service": "app/services/ledger.py"}
    for key, value in over.items():
        path = names.get(key, key)
        if value is None:
            files.pop(path, None)
        else:
            files[path] = value
    return files
