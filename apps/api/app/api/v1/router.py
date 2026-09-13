from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.events import router as events_router

# Versioned API boundary per docs/03-architecture/application-architecture.md (§9-10)
# and ADR-0004 (API architecture). Domain routers are added here by their own
# Issues. The error contract, collection envelope, request-id and
# DI-for-authorization foundation (Issue #6) live in app/api/errors.py,
# app/api/schemas.py, app/api/request_context.py and app/api/deps.py and
# apply globally regardless of which routes are mounted here.
router = APIRouter(prefix="/api/v1")
router.include_router(auth_router)
router.include_router(events_router)
