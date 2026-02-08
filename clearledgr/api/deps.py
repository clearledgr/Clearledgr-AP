"""FastAPI dependencies for Clearledgr core services."""
from clearledgr.di.container import container


def get_audit_service():
    return container.audit()


def get_llm_service():
    return container.llm()


def get_exception_router():
    return container.exceptions()


def get_learning_service():
    return container.learning()


def get_ingestion_service():
    return container.ingestion()


def get_sap_adapter():
    return container.sap()
