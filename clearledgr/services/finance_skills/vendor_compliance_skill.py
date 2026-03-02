"""Read-only vendor compliance skill for post-AP expansion on the same runtime."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from clearledgr.core.finance_contracts import SkillCapabilityManifest
from clearledgr.services.finance_skills.base import FinanceSkill


class VendorComplianceSkill(FinanceSkill):
    """Vendor compliance and documentation risk snapshot skill."""

    _INTENTS = frozenset({"read_vendor_compliance_health"})
    _MANIFEST = SkillCapabilityManifest(
        skill_id="vendor_compliance_v1",
        version="1.0",
        state_machine={
            "type": "read_only",
            "notes": "No AP state transitions; this skill surfaces vendor compliance posture for operators.",
        },
        action_catalog=[
            {
                "intent": "read_vendor_compliance_health",
                "class": "read_only",
                "description": "Read-only snapshot of vendor compliance/documentation risk signals.",
            }
        ],
        policy_pack={
            "deterministic_prechecks": [
                "limit_bounds_guard",
                "override_threshold_bounds_guard",
            ],
            "hitl_gates": [],
        },
        evidence_schema={
            "material_refs": [
                "summary.total_vendors",
                "summary.high_override_vendors_count",
                "top_high_override_vendors",
            ],
            "optional_refs": [
                "summary.top_anomaly_flags",
                "summary.bank_change_alerts_recent_30d",
            ],
        },
        adapter_bindings={
            "email": ["gmail"],
            "approval": ["slack", "teams", "email"],
            "erp": ["netsuite", "sap", "quickbooks", "xero"],
        },
        kpi_contract={
            "metrics": [
                "summary.total_vendors",
                "summary.high_override_vendors_count",
                "summary.override_rate_distribution",
            ],
            "promotion_gates": {
                "read_only_contract_compliance": 1.0,
            },
        },
    )

    @property
    def skill_id(self) -> str:
        return "vendor_compliance_v1"

    @property
    def intents(self) -> frozenset[str]:
        return self._INTENTS

    @property
    def manifest(self) -> SkillCapabilityManifest:
        return self._MANIFEST

    def policy_precheck(
        self,
        runtime,
        intent: str,
        input_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        _ = str(intent or "").strip().lower()
        payload = input_payload if isinstance(input_payload, dict) else {}
        try:
            limit = int(payload.get("limit", 200))
        except (TypeError, ValueError):
            limit = 200
        limit = max(1, min(limit, 1000))
        try:
            threshold = float(payload.get("override_threshold", 0.25))
        except (TypeError, ValueError):
            threshold = 0.25
        threshold = max(0.0, min(threshold, 1.0))
        return {
            "eligible": True,
            "reason_codes": [],
            "read_only": True,
            "limit": limit,
            "override_threshold": threshold,
        }

    def audit_contract(self, intent: str) -> Dict[str, Any]:
        return {
            "source": "finance_agent_runtime",
            "idempotent": False,
            "mutates_ap_state": False,
            "events": [],
            "read_only": True,
            "intent": str(intent or "").strip().lower(),
        }

    @staticmethod
    def _decode_list(value: Any) -> List[Any]:
        if isinstance(value, list):
            return value
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return parsed
            except Exception:
                return []
        return []

    @staticmethod
    def _parse_iso(raw: Any) -> Optional[datetime]:
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(str(raw))
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            return None

    def _load_vendor_profiles(self, runtime, *, limit: int) -> List[Dict[str, Any]]:
        if hasattr(runtime.db, "connect") and hasattr(runtime.db, "_prepare_sql"):
            sql = runtime.db._prepare_sql(
                "SELECT vendor_name, requires_po, contract_amount, payment_terms, "
                "bank_details_changed_at, approval_override_rate, anomaly_flags, invoice_count "
                "FROM vendor_profiles "
                "WHERE organization_id = ? "
                "ORDER BY invoice_count DESC, vendor_name ASC LIMIT ?"
            )
            try:
                with runtime.db.connect() as conn:
                    if getattr(runtime.db, "use_postgres", False):
                        cur = conn.cursor()
                        cur.execute(sql, (runtime.organization_id, int(limit)))
                        rows = [dict(row) for row in cur.fetchall()]
                    else:
                        conn.row_factory = __import__("sqlite3").Row
                        cur = conn.cursor()
                        cur.execute(sql, (runtime.organization_id, int(limit)))
                        rows = [dict(row) for row in cur.fetchall()]
                return rows
            except Exception:
                return []
        return []

    def _build_health_summary(
        self,
        runtime,
        *,
        limit: int,
        override_threshold: float,
    ) -> Dict[str, Any]:
        rows = self._load_vendor_profiles(runtime, limit=limit)
        total_vendors = len(rows)
        requires_po_count = 0
        missing_contract_limit_count = 0
        high_override_vendors: List[Dict[str, Any]] = []
        top_anomaly_flags: Dict[str, int] = {}
        bank_change_alerts_recent = 0
        now = datetime.now(timezone.utc)
        recent_cutoff = now - timedelta(days=30)

        for row in rows:
            requires_po = bool(row.get("requires_po"))
            contract_amount = row.get("contract_amount")
            override_rate = float(row.get("approval_override_rate") or 0.0)
            if requires_po:
                requires_po_count += 1
            if requires_po and contract_amount in (None, "", 0):
                missing_contract_limit_count += 1
            if override_rate >= override_threshold:
                high_override_vendors.append(
                    {
                        "vendor_name": row.get("vendor_name"),
                        "override_rate": round(override_rate, 4),
                        "invoice_count": int(row.get("invoice_count") or 0),
                    }
                )
            changed_at = self._parse_iso(row.get("bank_details_changed_at"))
            if changed_at and changed_at >= recent_cutoff:
                bank_change_alerts_recent += 1
            for flag in self._decode_list(row.get("anomaly_flags")):
                token = str(flag or "").strip().lower()
                if not token:
                    continue
                top_anomaly_flags[token] = top_anomaly_flags.get(token, 0) + 1

        high_override_vendors.sort(
            key=lambda entry: (
                -float(entry.get("override_rate") or 0.0),
                -int(entry.get("invoice_count") or 0),
                str(entry.get("vendor_name") or ""),
            )
        )
        top_anomaly = [
            {"flag": flag, "count": count}
            for flag, count in sorted(top_anomaly_flags.items(), key=lambda item: (-item[1], item[0]))[:5]
        ]
        avg_override = (
            round(
                sum(float(row.get("approval_override_rate") or 0.0) for row in rows) / max(1, total_vendors),
                4,
            )
            if total_vendors
            else 0.0
        )

        return {
            "organization_id": runtime.organization_id,
            "total_vendors": total_vendors,
            "vendors_requiring_po": requires_po_count,
            "vendors_missing_contract_limit": missing_contract_limit_count,
            "bank_change_alerts_recent_30d": bank_change_alerts_recent,
            "high_override_vendors_count": len(high_override_vendors),
            "override_rate_distribution": {
                "average": avg_override,
                "threshold": override_threshold,
            },
            "top_high_override_vendors": high_override_vendors[:10],
            "top_anomaly_flags": top_anomaly,
        }

    def preview(
        self,
        runtime,
        intent: str,
        input_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        normalized_intent = str(intent or "").strip().lower()
        precheck = self.policy_precheck(runtime, normalized_intent, input_payload)
        summary = self._build_health_summary(
            runtime,
            limit=precheck["limit"],
            override_threshold=precheck["override_threshold"],
        )
        return {
            "skill_id": self.skill_id,
            "intent": normalized_intent,
            "mode": "preview",
            "status": "ready",
            "organization_id": runtime.organization_id,
            "policy_precheck": precheck,
            "audit_contract": self.audit_contract(normalized_intent),
            "summary": summary,
            "next_step": "execute_intent",
            "operator_copy": {
                "what_happened": "Generated a vendor compliance risk snapshot from current vendor profiles.",
                "why_now": "Highlights which vendors need policy or documentation follow-up before scaling automation.",
                "recommended_now": "Review top high-override vendors and missing contract-limit vendors first.",
            },
        }

    async def execute(
        self,
        runtime,
        intent: str,
        input_payload: Optional[Dict[str, Any]] = None,
        *,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        _ = idempotency_key
        normalized_intent = str(intent or "").strip().lower()
        precheck = self.policy_precheck(runtime, normalized_intent, input_payload)
        summary = self._build_health_summary(
            runtime,
            limit=precheck["limit"],
            override_threshold=precheck["override_threshold"],
        )
        return {
            "skill_id": self.skill_id,
            "intent": normalized_intent,
            "status": "snapshot_ready",
            "organization_id": runtime.organization_id,
            "read_only": True,
            "policy_precheck": precheck,
            "audit_contract": self.audit_contract(normalized_intent),
            "summary": summary,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

