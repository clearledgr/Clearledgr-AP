"""AP item router composition."""

from __future__ import annotations

from fastapi import APIRouter

from clearledgr.api.ap_items_action_routes import router as _action_router
from clearledgr.api.ap_items_read_routes import router as _read_router


router = APIRouter(prefix="/api/ap/items", tags=["ap-items"])
router.include_router(_read_router)
router.include_router(_action_router)
