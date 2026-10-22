from fastapi import APIRouter

from app.routers.billing import billing_router

api_router = APIRouter()
api_router.include_router(billing_router, prefix="/billing")
