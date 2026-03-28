from __future__ import annotations

from importlib import import_module

_ROUTER_MODULES = {
    "v1_router": "clearledgr.api.v1",
    "erp_router": "clearledgr.api.erp",
    "gmail_extension_router": "clearledgr.api.gmail_extension",
    "slack_invoices_router": "clearledgr.api.slack_invoices",
    "teams_invoices_router": "clearledgr.api.teams_invoices",
}

__all__ = list(_ROUTER_MODULES.keys())


def __getattr__(name: str):
    module_name = _ROUTER_MODULES.get(name)
    if not module_name:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name)
    return getattr(module, "router")
