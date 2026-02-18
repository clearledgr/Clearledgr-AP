from clearledgr.api.v1 import router as v1_router
from clearledgr.api.erp import router as erp_router
from clearledgr.api.gmail_extension import router as gmail_extension_router
from clearledgr.api.slack_invoices import router as slack_invoices_router
from clearledgr.api.teams_invoices import router as teams_invoices_router

# Try to import optional routers
try:
    from clearledgr.api.autonomous import router as autonomous_router
except ImportError:
    autonomous_router = None

try:
    from clearledgr.api.ai_enhanced import router as ai_enhanced_router
except ImportError:
    ai_enhanced_router = None

try:
    from clearledgr.api.ap_workflow import router as ap_workflow_router
except ImportError:
    ap_workflow_router = None

try:
    from clearledgr.api.ap_advanced import router as ap_advanced_router
except ImportError:
    ap_advanced_router = None

__all__ = ["v1_router", "erp_router", "autonomous_router", "ai_enhanced_router", "ap_workflow_router", "ap_advanced_router"]
__all__.extend(["gmail_extension_router", "slack_invoices_router", "teams_invoices_router"])
