# Lazy imports to avoid dependency chains at startup
def __getattr__(name):
    if name == "AuditTrailService":
        from clearledgr.services.audit import AuditTrailService
        return AuditTrailService
    elif name == "ExceptionRoutingService":
        from clearledgr.services.exception_routing import ExceptionRoutingService
        return ExceptionRoutingService
    elif name == "LearningService":
        from clearledgr.services.learning import LearningService
        return LearningService
    elif name == "IngestionService":
        from clearledgr.services.ingestion import IngestionService
        return IngestionService
    elif name == "MultiModalLLMService":
        from clearledgr.services.llm_multimodal import MultiModalLLMService
        return MultiModalLLMService
    elif name == "match_bank_to_gl":
        from clearledgr.services.matching import match_bank_to_gl
        return match_bank_to_gl
    elif name == "IntelligentMatchingService":
        from clearledgr.services.intelligent_matching import IntelligentMatchingService
        return IntelligentMatchingService
    elif name == "PatternStore":
        from clearledgr.services.pattern_store import PatternStore
        return PatternStore
    elif name == "JournalEntryService":
        from clearledgr.services.journal_entries import JournalEntryService
        return JournalEntryService
    raise AttributeError(f"module 'clearledgr.services' has no attribute '{name}'")

__all__ = [
    "AuditTrailService",
    "ExceptionRoutingService",
    "LearningService",
    "IngestionService",
    "MultiModalLLMService",
    "match_bank_to_gl",
    "IntelligentMatchingService",
    "PatternStore",
    "JournalEntryService",
]
