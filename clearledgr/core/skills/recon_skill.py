"""Reconciliation PlanningSkill for the AgentPlanningEngine.

Provides tools for Claude to execute bank reconciliation:
1. Import transactions from a Google Sheet
2. Match against posted AP items
3. Flag exceptions for human review
4. Write results back to the output sheet

This proves the architecture supports multiple workflow types
on the same runtime without building parallel silos.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from clearledgr.core.skills.base import FinanceSkill, AgentTool, AgentTask

logger = logging.getLogger(__name__)


class ReconciliationSkill(FinanceSkill):
    """Reconciliation planning skill — tools for Claude during recon."""

    @property
    def skill_name(self) -> str:
        return "bank_reconciliation"

    def get_tools(self) -> List[AgentTool]:
        return [
            AgentTool(
                name="import_transactions",
                description=(
                    "Import transactions from a Google Sheet into the reconciliation session. "
                    "Reads the specified range and creates recon_items for each row."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "spreadsheet_id": {"type": "string", "description": "Google Sheet ID"},
                        "range": {"type": "string", "description": "A1 notation, e.g. 'Sheet1!A2:F100'"},
                        "session_id": {"type": "string", "description": "Reconciliation session ID"},
                    },
                    "required": ["spreadsheet_id", "range", "session_id"],
                },
                handler=self._import_transactions,
            ),
            AgentTool(
                name="match_transactions",
                description=(
                    "Match imported transactions against posted AP items by amount and date. "
                    "Updates recon_items with matched_ap_item_id and match_confidence."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string", "description": "Reconciliation session ID"},
                        "amount_tolerance": {"type": "number", "description": "Tolerance %, e.g. 0.5 for 0.5%", "default": 0.5},
                        "date_tolerance_days": {"type": "integer", "description": "Match window in days", "default": 3},
                    },
                    "required": ["session_id"],
                },
                handler=self._match_transactions,
            ),
            AgentTool(
                name="flag_exceptions",
                description=(
                    "Flag unmatched or ambiguous transactions as exceptions requiring human review. "
                    "Returns a summary of exceptions found."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string", "description": "Reconciliation session ID"},
                    },
                    "required": ["session_id"],
                },
                handler=self._flag_exceptions,
            ),
            AgentTool(
                name="write_results",
                description=(
                    "Write reconciliation results back to the Google Sheet. "
                    "Adds match status, matched invoice, and confidence columns."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string", "description": "Reconciliation session ID"},
                        "output_spreadsheet_id": {"type": "string", "description": "Output sheet ID (can be same as source)"},
                        "output_range": {"type": "string", "description": "Output range, e.g. 'Results!A1'"},
                    },
                    "required": ["session_id"],
                },
                handler=self._write_results,
            ),
        ]

    def build_system_prompt(self, task: AgentTask) -> str:
        payload = task.payload or {}
        spreadsheet_id = payload.get("spreadsheet_id", "")
        sheet_range = payload.get("range", "")

        return f"""You are the Clearledgr reconciliation agent. Your job is to reconcile
bank or card transactions against posted AP items (invoices that were approved and posted to ERP).

Source spreadsheet: {spreadsheet_id}
Source range: {sheet_range}

Steps:
1. Call import_transactions to read the source data from Google Sheets.
2. Call match_transactions to find AP items that match each transaction by amount and date.
3. Call flag_exceptions to identify unmatched or ambiguous transactions.
4. Call write_results to output the reconciliation results back to a sheet.

Rules:
- Match by amount (within tolerance) AND date (within window).
- A transaction can match at most one AP item. An AP item can match at most one transaction.
- Flag as exception if: no match found, multiple possible matches, or amount difference exceeds tolerance.
- Never fabricate matches. If unsure, flag as exception for human review.
- Always complete all 4 steps. Do not skip write_results.
"""

    # ---- Tool handlers (stubs — domain logic comes later) ----

    async def _import_transactions(self, **kwargs) -> Dict[str, Any]:
        """Import transactions from Google Sheets into recon_items."""
        from clearledgr.core.database import get_db
        from clearledgr.services.sheets_api import SheetsAPIClient

        session_id = kwargs.get("session_id", "")
        spreadsheet_id = kwargs.get("spreadsheet_id", "")
        sheet_range = kwargs.get("range", "")

        db = get_db()
        session = db.get_recon_session(session_id)
        if not session:
            return {"ok": False, "error": f"Session {session_id} not found"}

        # Read from Google Sheets
        try:
            client = SheetsAPIClient(user_id="system")
            if not await client.ensure_authenticated():
                return {"ok": False, "error": "Google Sheets authentication failed"}

            rows = await client.read_sheet(spreadsheet_id, sheet_range)
        except Exception as e:
            return {"ok": False, "error": f"Failed to read sheet: {e}"}

        if not rows:
            return {"ok": True, "imported": 0, "message": "No rows found in range"}

        # First row is headers, rest are data
        headers = [str(h).strip().lower() for h in rows[0]] if rows else []
        imported = 0
        for i, row in enumerate(rows[1:], start=1):
            cells = dict(zip(headers, row)) if headers else {}
            db.create_recon_item(
                session_id=session_id,
                organization_id=session["organization_id"],
                row_index=i,
                transaction_date=cells.get("date") or cells.get("transaction_date"),
                description=cells.get("description") or cells.get("memo") or cells.get("payee"),
                amount=_parse_amount(cells.get("amount") or cells.get("total")),
                reference=cells.get("reference") or cells.get("ref") or cells.get("check_number"),
            )
            imported += 1

        db.update_recon_session_counts(session_id)
        return {"ok": True, "imported": imported, "headers": headers}

    async def _match_transactions(self, **kwargs) -> Dict[str, Any]:
        """Match imported transactions against posted AP items.

        Multi-signal matching:
        1. Amount (weighted 0.35) — within configurable tolerance
        2. Date (weighted 0.25) — transaction date vs due_date/created_at
        3. Description/vendor fuzzy match (weighted 0.25) — Levenshtein-like similarity
        4. Reference number exact match (weighted 0.15) — invoice number in description/reference

        Handles:
        - Ambiguous matches (multiple candidates above threshold) → flagged as exception
        - One-to-one enforcement (each AP item matched at most once)
        - Confidence threshold (below 0.6 → exception, not match)
        """
        from clearledgr.core.database import get_db

        session_id = kwargs.get("session_id", "")
        amount_tolerance = float(kwargs.get("amount_tolerance", 0.5)) / 100.0
        date_tolerance_days = int(kwargs.get("date_tolerance_days", 3))

        db = get_db()
        session = db.get_recon_session(session_id)
        if not session:
            return {"ok": False, "error": f"Session {session_id} not found"}

        items = db.list_recon_items(session_id, state="imported")
        org_id = session["organization_id"]

        posted_items = db.list_ap_items(org_id, limit=5000)
        posted_items = [p for p in posted_items if str(p.get("state", "")).lower() in ("posted_to_erp", "closed")]

        # Build match candidates for each recon item
        claimed_ap_ids = set()  # enforce one-to-one matching
        matched_count = 0
        ambiguous_count = 0

        for item in items:
            candidates = _score_all_candidates(item, posted_items, amount_tolerance, date_tolerance_days)
            # Filter out already-claimed AP items
            candidates = [c for c in candidates if c["ap_id"] not in claimed_ap_ids]

            if not candidates:
                db.update_recon_item(item["id"], state="matching")
                continue

            best = candidates[0]

            # Check for ambiguity: multiple candidates with close scores
            if len(candidates) >= 2 and (candidates[1]["score"] / max(best["score"], 0.01)) > 0.85:
                db.update_recon_item(
                    item["id"],
                    state="exception",
                    exception_reason="ambiguous_match",
                    metadata=json.dumps({
                        "candidates": [{"ap_id": c["ap_id"], "score": c["score"], "vendor": c.get("vendor", "")} for c in candidates[:3]],
                    }),
                )
                ambiguous_count += 1
                continue

            # Confidence threshold
            if best["score"] < 0.6:
                db.update_recon_item(item["id"], state="matching")  # low confidence, goes to flag_exceptions
                continue

            # Match
            db.update_recon_item(
                item["id"],
                state="matched",
                matched_ap_item_id=best["ap_id"],
                match_confidence=best["score"],
            )
            claimed_ap_ids.add(best["ap_id"])
            matched_count += 1

        db.update_recon_session_counts(session_id)
        return {
            "ok": True,
            "matched": matched_count,
            "ambiguous": ambiguous_count,
            "unmatched": len(items) - matched_count - ambiguous_count,
        }

    async def _flag_exceptions(self, **kwargs) -> Dict[str, Any]:
        """Flag unmatched transactions as exceptions."""
        from clearledgr.core.database import get_db

        session_id = kwargs.get("session_id", "")
        db = get_db()

        unmatched = db.list_recon_items(session_id, state="matching")
        for item in unmatched:
            db.update_recon_item(
                item["id"],
                state="exception",
                exception_reason="no_match_found",
            )

        db.update_recon_session_counts(session_id)
        return {"ok": True, "exceptions": len(unmatched)}

    async def _write_results(self, **kwargs) -> Dict[str, Any]:
        """Write reconciliation results back to a Google Sheet."""
        from clearledgr.core.database import get_db
        from clearledgr.services.sheets_api import SheetsAPIClient

        session_id = kwargs.get("session_id", "")
        output_id = kwargs.get("output_spreadsheet_id", "")
        output_range = kwargs.get("output_range", "Results!A1")

        db = get_db()
        items = db.list_recon_items(session_id)
        if not items:
            return {"ok": True, "rows_written": 0}

        # Build output rows
        header = ["Row", "Date", "Description", "Amount", "Status", "Matched Invoice", "Confidence", "Exception"]
        rows = [header]
        for item in items:
            rows.append([
                item.get("row_index", ""),
                item.get("transaction_date", ""),
                item.get("description", ""),
                item.get("amount", ""),
                item.get("state", ""),
                item.get("matched_ap_item_id", ""),
                item.get("match_confidence", ""),
                item.get("exception_reason", ""),
            ])

        if not output_id:
            # Use source spreadsheet
            session = db.get_recon_session(session_id)
            output_id = (session or {}).get("spreadsheet_id", "")

        if not output_id:
            return {"ok": False, "error": "No output spreadsheet specified"}

        try:
            client = SheetsAPIClient(user_id="system")
            if not await client.ensure_authenticated():
                return {"ok": False, "error": "Google Sheets authentication failed"}
            await client.write_sheet(output_id, output_range, rows)
        except Exception as e:
            return {"ok": False, "error": f"Failed to write results: {e}"}

        return {"ok": True, "rows_written": len(rows) - 1}


# ---- Helpers ----

def _parse_amount(raw) -> float:
    """Parse an amount from a spreadsheet cell."""
    if raw is None:
        return 0.0
    try:
        cleaned = str(raw).replace(",", "").replace("$", "").replace("£", "").replace("€", "").strip()
        return float(cleaned) if cleaned else 0.0
    except (ValueError, TypeError):
        return 0.0


def _score_all_candidates(
    recon_item: Dict[str, Any],
    posted_items: List[Dict[str, Any]],
    amount_tolerance: float,
    date_tolerance_days: int,
) -> List[Dict[str, Any]]:
    """Score all AP items against a recon item. Returns sorted list (best first).

    Multi-signal scoring:
    - Amount match (0.35): within tolerance, scaled by closeness
    - Date match (0.25): transaction date vs due_date/created_at, within window
    - Description/vendor fuzzy match (0.25): trigram similarity on text
    - Reference match (0.15): invoice number found in description/reference
    """
    from datetime import datetime

    recon_amount = float(recon_item.get("amount") or 0)
    recon_desc = str(recon_item.get("description") or "").lower().strip()
    recon_ref = str(recon_item.get("reference") or "").lower().strip()
    recon_text = f"{recon_desc} {recon_ref}"

    recon_date = None
    try:
        raw_date = recon_item.get("transaction_date", "")
        if raw_date:
            recon_date = datetime.fromisoformat(str(raw_date)).date()
    except (ValueError, TypeError):
        pass

    candidates = []

    for ap_item in posted_items:
        ap_amount = float(ap_item.get("amount") or 0)
        if not ap_amount and not recon_amount:
            continue

        # --- Signal 1: Amount (weight 0.35) ---
        if recon_amount and ap_amount:
            diff = abs(recon_amount - ap_amount) / max(abs(ap_amount), 0.01)
            if diff > amount_tolerance * 3:  # hard cutoff at 3x tolerance
                continue
            amount_score = max(0, 1.0 - (diff / max(amount_tolerance, 0.001)))
        else:
            amount_score = 0.0

        # --- Signal 2: Date (weight 0.25) ---
        date_score = 0.5  # neutral default
        if recon_date:
            ap_date_str = ap_item.get("due_date") or ap_item.get("created_at", "")
            if ap_date_str:
                try:
                    ap_date = datetime.fromisoformat(str(ap_date_str)).date()
                    day_diff = abs((recon_date - ap_date).days)
                    if day_diff <= date_tolerance_days:
                        date_score = 1.0 - (day_diff / max(date_tolerance_days, 1))
                    elif day_diff <= date_tolerance_days * 3:
                        date_score = 0.3 * (1.0 - (day_diff / (date_tolerance_days * 3)))
                    else:
                        date_score = 0.0
                except (ValueError, TypeError):
                    pass

        # --- Signal 3: Description/vendor fuzzy match (weight 0.25) ---
        ap_vendor = str(ap_item.get("vendor_name") or ap_item.get("vendor") or "").lower()
        ap_subject = str(ap_item.get("subject") or "").lower()
        ap_text = f"{ap_vendor} {ap_subject}"
        text_score = _trigram_similarity(recon_text, ap_text) if recon_text.strip() and ap_text.strip() else 0.0

        # --- Signal 4: Reference/invoice number match (weight 0.15) ---
        ref_score = 0.0
        ap_inv_num = str(ap_item.get("invoice_number") or "").lower().strip()
        if ap_inv_num and len(ap_inv_num) >= 3:
            if ap_inv_num in recon_text:
                ref_score = 1.0
            elif recon_ref and recon_ref in ap_inv_num:
                ref_score = 0.8
        # Also check if vendor name appears in description
        if not ref_score and ap_vendor and len(ap_vendor) >= 3 and ap_vendor in recon_desc:
            ref_score = 0.5

        # --- Weighted composite score ---
        score = round(
            (amount_score * 0.35) +
            (date_score * 0.25) +
            (text_score * 0.25) +
            (ref_score * 0.15),
            4,
        )

        if score > 0.2:  # minimum viable candidate
            candidates.append({
                "ap_id": ap_item.get("id", ""),
                "vendor": ap_vendor,
                "amount": ap_amount,
                "score": score,
                "signals": {
                    "amount": round(amount_score, 3),
                    "date": round(date_score, 3),
                    "text": round(text_score, 3),
                    "reference": round(ref_score, 3),
                },
            })

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates


def _trigram_similarity(a: str, b: str) -> float:
    """Trigram (3-gram) similarity between two strings. Returns 0.0-1.0."""
    if not a or not b:
        return 0.0

    def trigrams(s):
        s = f"  {s} "  # pad for edge trigrams
        return set(s[i:i+3] for i in range(len(s) - 2))

    t_a = trigrams(a)
    t_b = trigrams(b)
    if not t_a or not t_b:
        return 0.0
    intersection = len(t_a & t_b)
    union = len(t_a | t_b)
    return intersection / union if union else 0.0
