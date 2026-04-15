"""Dependency injection container for core services.

Only stateless services live here. Anything that holds organization-
scoped state (e.g., LearningService binds to one org_id on init) must
NOT live as a process-wide singleton — the first caller's org would
be pinned forever and subsequent callers from other tenants would
silently see the wrong data. Per-org services use their own factory
function (e.g., learning.get_learning_service(organization_id)).
"""
from typing import Any


class ServiceContainer:
    def __init__(self) -> None:
        self._audit = None
        self._llm = None
        self._exceptions = None
        self._sap = None

    def audit(self) -> Any:
        if not self._audit:
            from clearledgr.services.audit import AuditTrailService

            self._audit = AuditTrailService()
        return self._audit

    def llm(self) -> Any:
        if not self._llm:
            from clearledgr.services.llm_multimodal import MultiModalLLMService

            self._llm = MultiModalLLMService()
        return self._llm

    def exceptions(self) -> Any:
        if not self._exceptions:
            from clearledgr.services.exception_routing import ExceptionRoutingService

            self._exceptions = ExceptionRoutingService()
        return self._exceptions

    def sap(self) -> Any:
        if not self._sap:
            from clearledgr.services.erp.sap import SAPAdapter

            self._sap = SAPAdapter()
        return self._sap


container = ServiceContainer()
