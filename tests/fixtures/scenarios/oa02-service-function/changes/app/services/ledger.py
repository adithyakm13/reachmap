"""Totals and the life of an invoice."""

INVOICES = {
    "inv-1": {"id": "inv-1", "customer": "cus-1", "total": {"amount": 1200, "currency": "EUR"}, "status": "open"},
    "inv-2": {"id": "inv-2", "customer": "cus-2", "total": {"amount": 560, "currency": "EUR"}, "status": "paid"},
}


def invoice_total(invoice_id):
    invoice = INVOICES[invoice_id]
    return dict(invoice["total"])


def all_totals():
    return [invoice["total"] for invoice in INVOICES.values()]


def new_invoice(body):
    invoice = {"id": "inv-3", "customer": body["customerId"], "total": body["total"], "status": "open"}
    INVOICES[invoice["id"]] = invoice
    return invoice


def mark_void(invoice_id):
    INVOICES[invoice_id]["status"] = "void"
