from fastapi import FastAPI

from app.api.errors import register_exception_handlers
from app.api.health import router as health_router
from app.api.request_context import RequestIDMiddleware
from app.api.v1.router import router as v1_router

app = FastAPI(title="TourCRM API")
app.add_middleware(RequestIDMiddleware)
register_exception_handlers(app)
app.include_router(health_router)
app.include_router(v1_router)
