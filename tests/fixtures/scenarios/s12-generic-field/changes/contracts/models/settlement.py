from pydantic import BaseModel


class Settlement(BaseModel):
    id: str
    invoice_id: str
    amount_cents: int
    interval: str = "weekly"
    settlement_reference: str | None = None
