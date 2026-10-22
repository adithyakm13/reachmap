"""Handlers of the `invoices` tag."""

from app.services.ledger import all_totals, invoice_total, mark_void, new_invoice
from app.services.render import statement_pdf


def list_invoices():
    return all_totals()


def create_invoice(body):
    return new_invoice(body)


def get_invoice(invoice_id):
    total = invoice_total(invoice_id)
    return {"id": invoice_id, "total": total}


def void_invoice(invoice_id):
    mark_void(invoice_id)
    return None


def get_invoice_pdf(invoice_id):
    return statement_pdf(invoice_id)
