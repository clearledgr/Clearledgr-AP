from clearledgr.api.gmail_extension import router as gmail_extension_router
from clearledgr.api.slack_invoices import router as slack_invoices_router
from clearledgr.api.teams_invoices import router as teams_invoices_router

# Try to import optional routers
__all__ = ["gmail_extension_router", "slack_invoices_router", "teams_invoices_router"]
