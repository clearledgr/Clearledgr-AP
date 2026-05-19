"""Domain-specific store mixins for SoldenDB.

Each store groups related database methods by domain. SoldenDB inherits
from all of them, so callers continue to use ``get_db()`` unchanged.
"""

from solden.core.stores.ap_store import APStore
from solden.core.stores.ap_runtime_store import APRuntimeStore
from solden.core.stores.auth_store import AuthStore
from solden.core.stores.entity_store import EntityStore
from solden.core.stores.integration_store import IntegrationStore
from solden.core.stores.metrics_store import MetricsStore
from solden.core.stores.policy_store import PolicyStore

__all__ = [
    "APStore",
    "APRuntimeStore",
    "AuthStore",
    "EntityStore",
    "IntegrationStore",
    "MetricsStore",
    "PolicyStore",
]
