from decimal import Decimal

from fastapi import APIRouter, HTTPException, Request

from acme_common.money import format_cents as fmt_cents
from app.services.totals import round_total

router = APIRouter()


@router.get("")
def list_invoices(
    request: Request,
    status: str | None = None,
):
    invoices = request.app.state.store.list_invoices(status=status)
    return [
        {
            "id": invoice.id,
            "number": invoice.number,
            "status": invoice.status,
            "amount_cents": invoice.amount_cents,
            "created_at": invoice.created_at,
        }
        for invoice in invoices
    ]


@router.get("/{invoice_id}")
def get_invoice(
    request: Request,
    invoice_id: str,
):
    def _load():
        return request.app.state.store.get_invoice(invoice_id)

    invoice = _load()
    if invoice is None:
        raise HTTPException(status_code=404, detail="invoice not found")
    if invoice.status == "void":
        raise HTTPException(status_code=410, detail="invoice is void")
    total = round_total(Decimal(invoice.amount_cents) / 100)
    return {
        "id": invoice.id,
        "number": invoice.number,
        "status": invoice.status,
        "amount_cents": invoice.amount_cents,
        "created_at": invoice.created_at,
        "total": str(total),
        "label": fmt_cents(invoice.amount_cents),
    }


@router.post("/{invoice_id}/void")
def void_invoice(
    request: Request,
    invoice_id: str,
):
    request.app.state.store.void_invoice(invoice_id)
    return {"id": invoice_id, "status": "void"}
