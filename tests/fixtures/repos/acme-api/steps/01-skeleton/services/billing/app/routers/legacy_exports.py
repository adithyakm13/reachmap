from fastapi import APIRouter, Request

legacy_export_router = APIRouter()


@legacy_export_router.get("/invoices.csv")
def export_invoices_csv(
    request: Request,
):
    rows = request.app.state.store.list_invoices(status=None)
    return "\n".join(f"{row.id},{row.number}" for row in rows)


@legacy_export_router.get("/settlements.csv")
def export_settlements_csv(
    request: Request,
):
    rows = request.app.state.store.list_settlements()
    return "\n".join(f"{row.id},{row.invoice_id}" for row in rows)
