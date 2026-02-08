"""Dependency injection container for core services."""
from clearledgr.services.audit import AuditTrailService
from clearledgr.services.exception_routing import ExceptionRoutingService
from clearledgr.services.ingestion import IngestionService
from clearledgr.services.learning import LearningService
from clearledgr.services.llm_multimodal import MultiModalLLMService
from clearledgr.services.erp.sap import SAPAdapter


class ServiceContainer:
    def __init__(self) -> None:
        self._audit = None
        self._llm = None
        self._exceptions = None
        self._learning = None
        self._ingestion = None
        self._sap = None

    def audit(self) -> AuditTrailService:
        if not self._audit:
            self._audit = AuditTrailService()
        return self._audit

    def llm(self) -> MultiModalLLMService:
        if not self._llm:
            self._llm = MultiModalLLMService()
        return self._llm

    def exceptions(self) -> ExceptionRoutingService:
        if not self._exceptions:
            self._exceptions = ExceptionRoutingService()
        return self._exceptions

    def learning(self) -> LearningService:
        if not self._learning:
            self._learning = LearningService()
        return self._learning

    def ingestion(self) -> IngestionService:
        if not self._ingestion:
            self._ingestion = IngestionService(audit=self.audit(), llm=self.llm())
        return self._ingestion

    def sap(self) -> SAPAdapter:
        if not self._sap:
            self._sap = SAPAdapter()
        return self._sap


container = ServiceContainer()
