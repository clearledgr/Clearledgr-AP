"""Clearledgr v1 core API routes."""
from fastapi import APIRouter

router = APIRouter(prefix="/v1", tags=["v1"])


@router.get("/health")
def health_check():
    return {"status": "ok", "service": "clearledgr-core"}
