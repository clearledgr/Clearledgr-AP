"""
Exception Resolution Service

Auto-resolves common AP invoice exceptions so they can re-enter the normal
processing flow without human intervention where safe to do so.

Rules:
- Missing PO is auto-resolved if a matching PO is found in ERP.
- ERP vendor-not-found is auto-resolved by creating the vendor in ERP.
- Amount anomalies, duplicates, and low-confidence fields are NEVER
  auto-resolved -- they return suggestions for human review.
- Never raises -- returns ``{"resolved": False, ...}`` on error.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ExceptionResolver:
    """Resolves common AP invoice exceptions automatically."""

    def __init__(self, organization_id: str):
        self.organization_id = organization_id
        self._db: Any = None

    @property
    def db(self):
        if self._db is None:
            from clearledgr.core.database import get_db
            self._db = get_db()
        return self._db

    # ------------------------------------------------------------------
    # Public dispatch
    # ------------------------------------------------------------------

    async def resolve(self, ap_item: Dict[str, Any], exception_code: str) -> Dict[str, Any]:
        """Dispatch to the right resolution strategy.

        Returns a dict with at least ``resolved`` (bool) plus ``action`` or
        ``reason`` and optionally ``suggestion``.
        """
        strategies = {
            "po_required_missing": self._resolve_missing_po,
            "missing_required_field_po_number": self._resolve_missing_po,
            "amount_anomaly_high": self._resolve_amount_anomaly,
            "amount_anomaly_moderate": self._resolve_amount_anomaly,
            "erp_vendor_not_found": self._resolve_vendor_not_found,
            "erp_duplicate_bill": self._resolve_duplicate_invoice,
            "duplicate_invoice": self._resolve_duplicate_invoice,
            "confidence_field_review_required": self._resolve_low_confidence,
            "currency_mismatch": self._resolve_currency_mismatch,
            "vendor_mismatch": self._resolve_vendor_mismatch,
            "vendor_unresponsive": self._resolve_vendor_unresponsive,
            "posting_exhausted": self._resolve_posting_exhausted,
            "erp_sync_mismatch": self._resolve_erp_sync_mismatch,
        }

        strategy = strategies.get(exception_code)
        if not strategy:
            return {
                "resolved": False,
                "reason": "no_strategy_for_exception",
                "exception_code": exception_code,
            }

        try:
            return await strategy(ap_item, exception_code)
        except Exception as exc:
            logger.warning(
                "[ExceptionResolver] strategy %s failed for ap_item %s: %s",
                exception_code,
                ap_item.get("id"),
                exc,
            )
            return {
                "resolved": False,
                "reason": f"strategy_error: {exc}",
                "exception_code": exception_code,
            }

    # ------------------------------------------------------------------
    # Strategy: Missing PO
    # ------------------------------------------------------------------

    async def _resolve_missing_po(
        self, ap_item: Dict[str, Any], exception_code: str
    ) -> Dict[str, Any]:
        """Search ERP for a matching PO by vendor name, auto-attach if found."""
        try:
            from clearledgr.integrations.erp_router import find_open_payables_for_vendor
        except ImportError:
            return {"resolved": False, "reason": "erp_router_unavailable"}

        vendor = ap_item.get("vendor_name") or ""

        # Try to find open payables for the vendor (may include POs)
        try:
            payables = await find_open_payables_for_vendor(
                organization_id=self.organization_id,
                vendor_name=vendor,
            )
        except Exception:
            payables = []

        # Also attempt a direct PO lookup if the invoice has a candidate PO reference
        # buried in metadata
        metadata = self._parse_metadata(ap_item)
        candidate_po = metadata.get("extracted_po_number") or ""

        if candidate_po:
            try:
                from clearledgr.integrations.erp_router import lookup_purchase_order_from_erp

                po = await lookup_purchase_order_from_erp(
                    organization_id=self.organization_id,
                    po_number=str(candidate_po),
                )
                if po:
                    po_number = po.get("po_number") or po.get("number") or str(candidate_po)
                    self.db.update_ap_item(
                        ap_item["id"],
                        po_number=po_number,
                        exception_code=None,
                        exception_severity=None,
                    )
                    return {
                        "resolved": True,
                        "action": "po_auto_attached",
                        "po_number": po_number,
                        "source": "erp_lookup",
                    }
            except Exception as exc:
                logger.debug("[ExceptionResolver] PO lookup failed: %s", exc)

        # If payables returned something useful with a PO number, use it
        for payable in (payables or []):
            po_num = payable.get("po_number") or payable.get("number")
            if po_num:
                self.db.update_ap_item(
                    ap_item["id"],
                    po_number=str(po_num),
                    exception_code=None,
                    exception_severity=None,
                )
                return {
                    "resolved": True,
                    "action": "po_auto_attached",
                    "po_number": str(po_num),
                    "source": "erp_payables",
                }

        return {
            "resolved": False,
            "reason": "no_matching_po_in_erp",
            "suggestion": "Request PO from vendor",
        }

    # ------------------------------------------------------------------
    # Strategy: Amount Anomaly (never auto-resolves)
    # ------------------------------------------------------------------

    async def _resolve_amount_anomaly(
        self, ap_item: Dict[str, Any], exception_code: str
    ) -> Dict[str, Any]:
        """Calculate discrepancy and use AI to reason about likely cause."""
        vendor = ap_item.get("vendor_name") or ""
        try:
            amount = float(ap_item.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0.0

        profile = self.db.get_vendor_profile(self.organization_id, vendor) or {}
        avg = float(profile.get("avg_invoice_amount") or 0)

        if avg <= 0:
            return {
                "resolved": False,
                "reason": "no_vendor_history",
                "action": "discrepancy_calculated",
                "suggestion": "No vendor history to compare against. Manual review required.",
            }

        deviation = abs(amount - avg) / avg
        direction = "above" if amount > avg else "below"

        # AI reasoning about why the amount differs
        ai_reason = await self._ai_reason_amount_variance(
            vendor=vendor,
            invoice_amount=amount,
            average_amount=avg,
            deviation_pct=round(deviation * 100, 1),
            direction=direction,
            ap_item=ap_item,
        )

        return {
            "resolved": False,  # Amount anomalies still need human review
            "action": "discrepancy_calculated",
            "invoice_amount": amount,
            "vendor_average": avg,
            "deviation_percent": round(deviation * 100, 1),
            "ai_analysis": ai_reason,
            "suggestion": ai_reason or (
                f"Invoice is {round(deviation * 100)}% {direction} vendor average "
                f"(${avg:,.2f}). Verify with vendor."
            ),
        }

    async def _ai_reason_amount_variance(
        self,
        vendor: str,
        invoice_amount: float,
        average_amount: float,
        deviation_pct: float,
        direction: str,
        ap_item: Dict[str, Any],
    ) -> str:
        """Ask Claude to reason about why the invoice amount differs from history."""
        try:
            import os, json, httpx
            api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
            if not api_key:
                return ""

            metadata = ap_item.get("metadata") or {}
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            line_items = metadata.get("line_items") or []
            po_number = ap_item.get("po_number") or ""

            prompt = f"""You are an AP automation expert. An invoice amount differs from this vendor's history.

Vendor: {vendor}
Invoice amount: {ap_item.get('currency', 'USD')} {invoice_amount:,.2f}
Vendor average: {ap_item.get('currency', 'USD')} {average_amount:,.2f}
Deviation: {deviation_pct}% {direction} average
PO reference: {po_number or 'None'}
Line items: {json.dumps(line_items[:5]) if line_items else 'Not available'}

What is the most likely reason for this variance? Consider:
- Tax or VAT changes
- Discount applied or removed
- Partial delivery / partial billing
- Price increase / new pricing tier
- Currency conversion difference
- Additional services or items
- Credit or debit adjustment

Respond in ONE sentence with the most likely explanation and whether this needs human review or can be auto-approved."""

            response = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 150,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=10,
            )
            if response.status_code == 200:
                return response.json().get("content", [{}])[0].get("text", "").strip()
        except Exception as exc:
            logger.debug("AI amount variance reasoning failed: %s", exc)
        return ""

    # ------------------------------------------------------------------
    # Strategy: Vendor Not Found in ERP (auto-resolves)
    # ------------------------------------------------------------------

    async def _resolve_vendor_not_found(
        self, ap_item: Dict[str, Any], exception_code: str
    ) -> Dict[str, Any]:
        """Attempt to create vendor in ERP."""
        try:
            from clearledgr.integrations.erp_router import create_vendor, Vendor
        except ImportError:
            return {"resolved": False, "reason": "erp_router_unavailable"}

        vendor_name = ap_item.get("vendor_name") or ""
        if not vendor_name:
            return {"resolved": False, "reason": "no_vendor_name_on_item"}

        try:
            vendor = Vendor(name=vendor_name)
            result = await create_vendor(
                organization_id=self.organization_id,
                vendor=vendor,
            )
            if result.get("vendor_id"):
                self.db.update_ap_item(
                    ap_item["id"],
                    exception_code=None,
                    exception_severity=None,
                )
                return {
                    "resolved": True,
                    "action": "vendor_created_in_erp",
                    "vendor_id": result["vendor_id"],
                }
            if result.get("status") == "success":
                self.db.update_ap_item(
                    ap_item["id"],
                    exception_code=None,
                    exception_severity=None,
                )
                return {
                    "resolved": True,
                    "action": "vendor_created_in_erp",
                    "vendor_id": result.get("vendor_id", "unknown"),
                }
        except Exception as exc:
            return {"resolved": False, "reason": f"vendor_creation_failed: {exc}"}

        return {"resolved": False, "reason": "vendor_creation_returned_no_id"}

    # ------------------------------------------------------------------
    # Strategy: Duplicate Invoice (never auto-resolves)
    # ------------------------------------------------------------------

    async def _resolve_duplicate_invoice(
        self, ap_item: Dict[str, Any], exception_code: str
    ) -> Dict[str, Any]:
        """Link to original and suggest action."""
        metadata = self._parse_metadata(ap_item)
        existing_id = (
            metadata.get("duplicate_ap_item_id")
            or metadata.get("original_ap_item_id")
            or ""
        )

        if existing_id:
            try:
                existing = self.db.get_ap_item(existing_id)
            except Exception:
                existing = None
            if existing:
                return {
                    "resolved": False,
                    "action": "duplicate_identified",
                    "original_ap_item_id": existing_id,
                    "original_state": existing.get("state"),
                    "original_amount": existing.get("amount"),
                    "suggestion": "Reject this invoice (duplicate) or merge if different version",
                }

        return {
            "resolved": False,
            "reason": "no_original_found",
            "suggestion": "Review manually — possible duplicate but original not identified",
        }

    # ------------------------------------------------------------------
    # Strategy: Low Confidence Fields (never auto-resolves)
    # ------------------------------------------------------------------

    async def _resolve_low_confidence(
        self, ap_item: Dict[str, Any], exception_code: str
    ) -> Dict[str, Any]:
        """Identify which fields are low confidence and suggest sources."""
        metadata = self._parse_metadata(ap_item)
        confidence_gate = (
            metadata.get("confidence_gate")
            or metadata.get("validation_gate", {}).get("confidence_gate")
            or {}
        )
        blockers = confidence_gate.get("confidence_blockers") or []

        field_suggestions = []
        for blocker in blockers:
            field = blocker.get("field", "unknown")
            confidence = blocker.get("confidence", 0)
            field_suggestions.append({
                "field": field,
                "confidence": confidence,
                "suggestion": (
                    f"Review '{field}' (confidence: {round(confidence * 100)}%). "
                    f"Check original email/attachment."
                ),
            })

        return {
            "resolved": False,
            "action": "fields_identified",
            "low_confidence_fields": field_suggestions,
            "suggestion": f"{len(field_suggestions)} field(s) need manual review",
        }

    # ------------------------------------------------------------------
    # Strategy: Currency Mismatch (never auto-resolves)
    # ------------------------------------------------------------------

    async def _resolve_currency_mismatch(
        self, ap_item: Dict[str, Any], exception_code: str
    ) -> Dict[str, Any]:
        """Surface the mismatch details for human review."""
        metadata = self._parse_metadata(ap_item)
        invoice_currency = ap_item.get("currency") or metadata.get("currency") or "unknown"
        expected_currency = metadata.get("expected_currency") or "unknown"

        return {
            "resolved": False,
            "action": "currency_mismatch_surfaced",
            "invoice_currency": invoice_currency,
            "expected_currency": expected_currency,
            "suggestion": (
                f"Invoice currency ({invoice_currency}) does not match expected "
                f"({expected_currency}). Confirm with vendor or adjust GL mapping."
            ),
        }

    # ------------------------------------------------------------------
    # Strategy: Vendor Mismatch (never auto-resolves)
    # ------------------------------------------------------------------

    async def _resolve_vendor_mismatch(
        self, ap_item: Dict[str, Any], exception_code: str
    ) -> Dict[str, Any]:
        """Suggest correct vendor from known aliases via fuzzy matching."""
        try:
            from clearledgr.services.fuzzy_matching import vendor_similarity
        except ImportError:
            return {"resolved": False, "reason": "fuzzy_matching_unavailable"}

        vendor = ap_item.get("vendor_name") or ""
        if not vendor:
            return {"resolved": False, "reason": "no_vendor_name"}

        # Load known vendor profiles for this org
        profiles: list = []
        try:
            if hasattr(self.db, "get_vendor_profiles_bulk"):
                # Fall back to listing via AP items if no dedicated list method
                pass
            # Use a direct SQL query to get vendor names
            profiles_data = self._list_vendor_names()
        except Exception:
            profiles_data = []

        if not profiles_data:
            return {"resolved": False, "reason": "no_known_vendors"}

        # Score each known vendor
        matches = []
        for known_vendor in profiles_data:
            score = vendor_similarity(vendor, known_vendor)
            if score >= 0.6:
                matches.append({"name": known_vendor, "score": score})

        matches.sort(key=lambda m: m["score"], reverse=True)

        if matches:
            best = matches[0]
            return {
                "resolved": False,
                "action": "vendor_suggestion",
                "extracted_vendor": vendor,
                "suggested_vendor": best["name"],
                "match_score": best["score"],
                "suggestion": (
                    f"Did you mean '{best['name']}'? "
                    f"(match: {round(best['score'] * 100)}%)"
                ),
            }

        return {"resolved": False, "reason": "no_similar_vendors_found"}

    # ------------------------------------------------------------------
    # Strategy: Vendor Unresponsive (never auto-resolves)
    # ------------------------------------------------------------------

    async def _resolve_vendor_unresponsive(
        self, ap_item: Dict[str, Any], exception_code: str
    ) -> Dict[str, Any]:
        """Surface follow-up history and suggest escalation."""
        metadata = self._parse_metadata(ap_item)
        attempt_count = int(metadata.get("followup_attempt_count") or 0)
        last_sent = metadata.get("followup_sent_at") or "never"

        return {
            "resolved": False,
            "action": "escalation_suggested",
            "followup_attempts": attempt_count,
            "last_followup_at": last_sent,
            "suggestion": (
                f"Vendor has been contacted {attempt_count} time(s) "
                f"(last: {last_sent}). Consider escalating to manager or "
                f"putting invoice on hold."
            ),
        }

    # ------------------------------------------------------------------
    # Strategy: Posting Exhausted (never auto-resolves)
    # ------------------------------------------------------------------

    async def _resolve_posting_exhausted(
        self, ap_item: Dict[str, Any], exception_code: str
    ) -> Dict[str, Any]:
        """ERP posting retries have been exhausted."""
        last_error = ap_item.get("last_error") or "unknown"
        metadata = self._parse_metadata(ap_item)
        retry_count = int(metadata.get("erp_post_attempts") or metadata.get("retry_count") or 0)

        return {
            "resolved": False,
            "action": "posting_failure_surfaced",
            "retry_count": retry_count,
            "last_error": last_error,
            "suggestion": (
                f"ERP posting failed after {retry_count} retries. "
                f"Last error: {last_error}. "
                f"Check ERP connection status and retry manually."
            ),
        }

    async def _resolve_erp_sync_mismatch(
        self, ap_item: Dict[str, Any], exception_code: str
    ) -> Dict[str, Any]:
        """Bill posted in Clearledgr but not found in ERP.

        Attempts to re-post the bill. If re-post succeeds, clears the
        exception. If it fails, surfaces the error for manual review.
        """
        ap_item_id = ap_item.get("id")
        erp_reference = ap_item.get("erp_reference")
        invoice_number = ap_item.get("invoice_number")
        state = str(ap_item.get("state") or "").lower()

        # Only attempt re-post if still in posted_to_erp state
        if state != "posted_to_erp":
            return {
                "resolved": False,
                "reason": f"item_in_wrong_state:{state}",
                "suggestion": "Item is no longer in posted_to_erp state. Review manually.",
            }

        # First, re-verify — the bill might have appeared since the last check
        try:
            from clearledgr.integrations.erp_router import verify_bill_posted

            verify = await verify_bill_posted(
                organization_id=self.organization_id,
                invoice_number=str(invoice_number or erp_reference or ""),
                expected_amount=float(ap_item["amount"]) if ap_item.get("amount") else None,
            )
            if verify.get("verified"):
                # Bill is actually there — false alarm, clear exception
                self.db.update_ap_item(
                    ap_item_id,
                    exception_code=None,
                    exception_severity=None,
                )
                return {
                    "resolved": True,
                    "action": "erp_sync_confirmed_on_recheck",
                    "erp_bill": verify.get("bill"),
                }
        except Exception as ver_exc:
            logger.debug("ERP re-verify failed for %s: %s", ap_item_id, ver_exc)

        # Bill genuinely missing — attempt re-post
        try:
            from clearledgr.integrations.erp_router import post_bill, Bill

            metadata = self._parse_metadata(ap_item)
            line_items = metadata.get("line_items")

            bill = Bill(
                vendor_name=ap_item.get("vendor_name") or "",
                amount=float(ap_item.get("amount") or 0),
                invoice_number=invoice_number or "",
                date=ap_item.get("invoice_date") or ap_item.get("due_date") or "",
                currency=ap_item.get("currency") or "USD",
                description=f"Re-post: {invoice_number or ap_item_id}",
                gl_code=metadata.get("gl_code") or "",
                line_items=line_items,
            )

            result = await post_bill(
                organization_id=self.organization_id,
                bill=bill,
                entity_id=ap_item.get("entity_id"),
            )

            if result.get("status") == "success":
                new_ref = result.get("erp_reference") or result.get("bill_id")
                self.db.update_ap_item(
                    ap_item_id,
                    erp_reference=new_ref or erp_reference,
                    exception_code=None,
                    exception_severity=None,
                )
                return {
                    "resolved": True,
                    "action": "re_posted_to_erp",
                    "new_erp_reference": new_ref,
                }
            else:
                return {
                    "resolved": False,
                    "action": "re_post_failed",
                    "erp_error": result.get("error") or result.get("reason"),
                    "suggestion": "Re-post to ERP failed. Check ERP connection and retry manually.",
                }
        except Exception as post_exc:
            return {
                "resolved": False,
                "action": "re_post_exception",
                "error": str(post_exc),
                "suggestion": "Could not re-post to ERP. Manual intervention required.",
            }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_metadata(ap_item: Dict[str, Any]) -> Dict[str, Any]:
        raw = ap_item.get("metadata") or "{}"
        if isinstance(raw, dict):
            return raw
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}

    def _list_vendor_names(self) -> list:
        """Return a list of known vendor names for the org."""
        try:
            sql = self.db._prepare_sql(
                "SELECT DISTINCT vendor_name FROM vendor_profiles "
                "WHERE organization_id = ? LIMIT 500"
            )
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (self.organization_id,))
                rows = cur.fetchall()
            return [str(row[0]) for row in rows if row[0]]
        except Exception:
            return []


def get_exception_resolver(organization_id: str) -> ExceptionResolver:
    """Factory -- no caching, each call is cheap."""
    return ExceptionResolver(organization_id)
