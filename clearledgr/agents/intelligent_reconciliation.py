"""Autonomous reconciliation agent with multi-factor + LLM + learning + anomaly detection."""
from typing import Dict, List, Optional

from clearledgr.agents.base import AgentContext, BaseAgent
from clearledgr.models.reconciliation import ReconciliationConfig, ReconciliationResult
from clearledgr.models.transactions import BankTransaction, GLTransaction
from clearledgr.services.intelligent_matching import IntelligentMatchingService
from clearledgr.services.pattern_store import PatternStore
from clearledgr.services.llm_multimodal import MultiModalLLMService


class IntelligentReconciliationAgent(BaseAgent):
    name = "IntelligentReconciliationAgent"

    def __init__(self, enable_anomaly_detection: bool = True) -> None:
        self.matcher = IntelligentMatchingService(
            config=ReconciliationConfig(match_threshold=0.8, llm_enabled=True),
            llm=MultiModalLLMService(),
            patterns=PatternStore(),
        )
        self.enable_anomaly_detection = enable_anomaly_detection
        self._ai_service: Optional["EnhancedAIService"] = None

    @property
    def ai_service(self):
        """Lazy load enhanced AI service."""
        if self._ai_service is None:
            try:
                from clearledgr.services.ai_enhanced import get_enhanced_ai_service
                self._ai_service = get_enhanced_ai_service()
            except ImportError:
                self._ai_service = None
        return self._ai_service

    def validate(self, ctx: AgentContext) -> None:
        if "bank_transactions" not in ctx.state or "gl_transactions" not in ctx.state:
            raise ValueError("Missing transactions for reconciliation")

    def execute(self, ctx: AgentContext) -> Dict:
        self.validate(ctx)
        bank_txns: List[BankTransaction] = ctx.state["bank_transactions"]
        gl_txns: List[GLTransaction] = ctx.state["gl_transactions"]
        config = ctx.state.get("config") or ReconciliationConfig()
        self.matcher.config = config

        # Run core reconciliation
        result: ReconciliationResult = self.matcher.match(bank_txns, gl_txns)
        ctx.state["reconciliation_result"] = result

        # Run anomaly detection on unmatched transactions
        anomalies = []
        if self.enable_anomaly_detection and self.ai_service and result.unmatched_bank:
            historical_txns = ctx.state.get("historical_transactions") or []
            
            for bank_txn in result.unmatched_bank[:20]:  # Limit to avoid too many API calls
                try:
                    txn_dict = {
                        "id": bank_txn.transaction_id,
                        "amount": bank_txn.amount.amount if bank_txn.amount else 0,
                        "date": str(bank_txn.transaction_date) if bank_txn.transaction_date else "",
                        "description": bank_txn.description or "",
                        "vendor": bank_txn.counterparty or "",
                    }
                    anomaly_result = self.ai_service.detect_anomaly(
                        transaction=txn_dict,
                        historical_transactions=historical_txns,
                    )
                    if anomaly_result.is_anomaly:
                        anomalies.append({
                            "transaction_id": bank_txn.transaction_id,
                            "severity": anomaly_result.severity.value,
                            "type": anomaly_result.anomaly_type,
                            "explanation": anomaly_result.explanation,
                            "action": anomaly_result.suggested_action,
                        })
                except Exception:
                    pass  # Continue on error

        ctx.state["anomalies_detected"] = anomalies

        self.log_event(
            ctx,
            action="reconciliation_completed",
            entity_type="reconciliation",
            metadata={
                "match_rate": result.match_rate, 
                "matches": len(result.matches),
                "anomalies_detected": len(anomalies),
            },
        )
        return {"result": result, "anomalies": anomalies}

