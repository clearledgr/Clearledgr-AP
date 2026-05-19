# Lazy imports to avoid dependency chains at startup
def __getattr__(name):
    if name == "AuditTrailService":
        from solden.services.audit import AuditTrailService
        return AuditTrailService
    elif name == "ExceptionRoutingService":
        from solden.services.exception_routing import ExceptionRoutingService
        return ExceptionRoutingService
    elif name == "LearningService":
        from solden.services.learning import LearningService
        return LearningService
    elif name == "MultiModalLLMService":
        from solden.services.llm_multimodal import MultiModalLLMService
        return MultiModalLLMService
    elif name == "PatternStore":
        from solden.services.pattern_store import PatternStore
        return PatternStore
    raise AttributeError(f"module 'solden.services' has no attribute '{name}'")

__all__ = [
    "AuditTrailService",
    "ExceptionRoutingService",
    "LearningService",
    "MultiModalLLMService",
    "PatternStore",
]
