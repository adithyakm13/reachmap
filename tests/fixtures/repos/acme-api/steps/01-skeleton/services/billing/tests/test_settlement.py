import app.services.settlement as settlement_svc
from acme_contracts.models.invoice import Invoice


def test_summarise_counts_invoices():
    invoices = [
        Invoice(id="i1", number="INV-1", customer_id="c1", amount_cents=1250, created_at="2024-03-05"),
        Invoice(id="i2", number="INV-2", customer_id="c1", amount_cents=300, created_at="2024-03-06"),
    ]
    summary = settlement_svc.summarise(invoices)
    assert summary["count"] == 2
    assert summary["total_cents"] == 1550
