"""Thin AP item router and compatibility exports."""

from __future__ import annotations

from fastapi import APIRouter

import clearledgr.services.ap_item_service as _service
from clearledgr.api.ap_items_action_routes import router as _action_router
from clearledgr.api.ap_items_read_routes import router as _read_router
from clearledgr.services.finance_agent_runtime import FinanceAgentRuntime


router = APIRouter(prefix="/api/ap/items", tags=["ap-items"])
router.include_router(_read_router)
router.include_router(_action_router)


for _name in dir(_service):
    if _name.startswith("__"):
        continue
    globals()[_name] = getattr(_service, _name)


globals()["FinanceAgentRuntime"] = FinanceAgentRuntime
