from fastapi import APIRouter

# Versioned API boundary per docs/03-architecture/application-architecture.md (§9-10).
# Domain routers are added here by their own Issues; this skeleton intentionally
# registers no endpoints.
router = APIRouter(prefix="/api/v1")
