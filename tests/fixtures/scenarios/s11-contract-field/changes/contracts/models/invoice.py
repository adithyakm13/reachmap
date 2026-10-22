from pydantic import BaseModel


class Invoice(BaseModel):
    id: str
    number: str
    customer_id: str
    amount_cents: int
    created_at: str
    status: str = "draft"
    interval: str = "monthly"
    settlement_reference: str
    due_date: str | None = None
