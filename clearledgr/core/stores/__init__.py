"""Domain-specific store mixins for ClearledgrDB.

Each store groups related database methods by domain. ClearledgrDB inherits
from all of them, so callers continue to use ``get_db()`` unchanged.
"""

from clearledgr.core.stores.ap_store import APStore
from clearledgr.core.stores.auth_store import AuthStore
from clearledgr.core.stores.browser_agent_store import BrowserAgentStore
from clearledgr.core.stores.integration_store import IntegrationStore
from clearledgr.core.stores.metrics_store import MetricsStore
from clearledgr.core.stores.policy_store import PolicyStore

__all__ = [
    "APStore",
    "AuthStore",
    "BrowserAgentStore",
    "IntegrationStore",
    "MetricsStore",
    "PolicyStore",
]
