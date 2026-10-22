import app.services.settlement as settlement_svc
from fastapi import APIRouter, HTTPException, Request

router = APIRouter()

# settlement_reference is filled in by the nightly settlement job.


@router.get("/summary")
def get_settlement_summary(
    request: Request,
):
    invoices = request.app.state.store.list_invoices(status=None)
    return settlement_svc.summarise(invoices)


@router.get("/{settlement_id}")
def get_settlement(
    request: Request,
    settlement_id: str,
):
    def _load():
        return request.app.state.store.get_settlement(settlement_id)

    settlement = _load()
    if settlement is None:
        raise HTTPException(status_code=404, detail="settlement not found")
    return {"id": settlement.id, "invoice_id": settlement.invoice_id}
