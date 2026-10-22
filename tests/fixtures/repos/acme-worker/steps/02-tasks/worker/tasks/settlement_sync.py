from acme_contracts.models.invoice import Invoice

from worker import topics
from worker.app import celery_app

# respect the provider rate limit between batches
BATCH_PAUSE_SECONDS = 2


@celery_app.task(name=topics.SETTLEMENT_SYNC)
def sync_invoice(invoice_json: str) -> str:
    invoice = Invoice.model_validate_json(invoice_json)
    return invoice.settlement_reference or f"pending-{invoice.id}"
