from fastapi import APIRouter, Request

from app.routers.invoices import router as invoices_router
from app.routers.settlements import router as settlements_router

billing_router = APIRouter()
billing_router.include_router(invoices_router, prefix="/invoices")
billing_router.include_router(settlements_router, prefix="/settlements")

# Retired in 1.2: the CSV exports are no longer served.
# billing_router.include_router(legacy_export_router, prefix="/legacy-exports")


@billing_router.get("/audit-log")
def audit_log(
    request: Request,
):
    return request.app.state.store.audit_entries()
