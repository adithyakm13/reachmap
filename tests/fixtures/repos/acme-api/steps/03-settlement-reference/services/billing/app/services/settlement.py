from acme_common.money import (
    cents_to_decimal,
    format_cents,
)
from acme_contracts.models.invoice import Invoice


def reference_for(invoice: Invoice) -> str:
    if invoice.settlement_reference is None:
        return f"pending-{invoice.id}"
    return invoice.settlement_reference


def summarise(invoices: list[Invoice]) -> dict:
    total_cents = sum(invoice.amount_cents for invoice in invoices)
    return {
        "count": len(invoices),
        "total_cents": total_cents,
        "total": format_cents(total_cents),
        "average": str(cents_to_decimal(total_cents) / max(len(invoices), 1)),
        "references": [reference_for(invoice) for invoice in invoices],
    }
