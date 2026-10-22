from fastapi import FastAPI

from app.routers.cases import router as cases_router

app = FastAPI(title="acme cases")
app.include_router(cases_router, prefix="/api/cases")
