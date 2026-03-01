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
    elif name == "MultiModalLLMService":
        from clearledgr.services.llm_multimodal import MultiModalLLMService
        return MultiModalLLMService
    elif name == "PatternStore":
        from clearledgr.services.pattern_store import PatternStore
        return PatternStore
    raise AttributeError(f"module 'clearledgr.services' has no attribute '{name}'")

__all__ = [
    "AuditTrailService",
    "ExceptionRoutingService",
    "LearningService",
    "MultiModalLLMService",
    "PatternStore",
]
