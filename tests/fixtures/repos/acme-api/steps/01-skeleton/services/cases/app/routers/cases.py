import acme_common.money as money_utils
from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.get("")
def list_cases(
    request: Request,
):
    cases = request.app.state.store.list_cases()
    return [
        {"id": row.id, "title": row.title, "status": row.status, "opened": row.opened}
        for row in cases
    ]


@router.get("/{case_id}")
def get_case(
    request: Request,
    case_id: str,
):
    row = request.app.state.store.get_case(case_id)
    if row is None:
        raise HTTPException(status_code=404, detail="case not found")
    return {
        "id": row.id,
        "title": row.title,
        "status": row.status,
        "opened": row.opened,
        "exposure": money_utils.format_cents(row.exposure_cents),
    }
