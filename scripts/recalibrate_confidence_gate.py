#!/usr/bin/env python3
"""Re-evaluate AP records against the per-field confidence-gate calibration.

Why this exists
---------------
Commit ``b2cfe86`` replaced the flat ``0.95`` AND-applied threshold with
per-field severity tiers (critical / important / advisory). Records that
were placed in ``field_review_required`` under the old gate may no longer
warrant manual review:

- Old: vendor 0.97, amount 0.99, invoice_number 0.94, due_date 0.85 →
  blocked (due_date below 0.95 floor)
- New: same record → passes (vendor + amount above 0.92 critical;
  due_date is advisory, never gates)

This script walks one organisation's AP items, replays the new gate
against the persisted ``field_confidences``, and updates the records
whose decision changed. Every decision — cleared, blockers-changed, or
unchanged — emits an audit event so the timeline reflects the
re-evaluation.

Safety contract
---------------
- Default mode is **dry run**. Pass ``--commit`` to mutate.
- ``--organization-id`` is **required** so the operator names the
  tenant explicitly. There is no "all orgs" mode.
- Records without persisted ``field_confidences`` are skipped — without
  the per-field signal we cannot fairly re-evaluate, and a blanket
  clear would silently approve low-confidence records.
- Idempotent: re-running on the same org produces the same result.
  Audit events use deterministic idempotency keys so re-runs don't
  duplicate timeline entries.
- Pure additive on the audit trail: nothing is deleted.

Usage
-----
    # Local Postgres
    TEST_DATABASE_URL=postgresql://... \\
        python scripts/recalibrate_confidence_gate.py \\
            --organization-id acme-demo

    # Production via Railway
    railway run --service api python scripts/recalibrate_confidence_gate.py \\
            --organization-id <real-org-id> --commit

    # Dry run with per-record output
    python scripts/recalibrate_confidence_gate.py \\
            --organization-id acme-demo --verbose
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from clearledgr.core.ap_confidence import (
    SEVERITY_ADVISORY,
    SEVERITY_CRITICAL,
    SEVERITY_IMPORTANT,
    evaluate_critical_field_confidence,
)
from clearledgr.core.database import get_db


logger = logging.getLogger("recalibrate_confidence_gate")


DECISION_CLEARED = "cleared"
DECISION_BLOCKERS_CHANGED = "blockers_changed"
DECISION_NO_CHANGE = "no_change"
DECISION_SKIPPED = "skipped"


@dataclass
class RecordOutcome:
    ap_item_id: str
    vendor_name: str
    invoice_number: str
    decision: str
    reason: str
    old_blockers: List[str]
    new_blockers: List[str]
    new_advisories: List[str]


def _parse_metadata(item: Dict[str, Any]) -> Dict[str, Any]:
    metadata = item.get("metadata")
    if isinstance(metadata, dict):
        return dict(metadata)
    if isinstance(metadata, str) and metadata.strip():
        try:
            return json.loads(metadata)
        except json.JSONDecodeError:
            return {}
    return {}


def _parse_field_confidences(item: Dict[str, Any], metadata: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """Pull persisted field confidences from the item or its metadata.

    Returns ``None`` when nothing is persisted — caller treats that as
    "skip this record" (no fair re-evaluation possible).
    """
    raw = item.get("field_confidences") or metadata.get("field_confidences")
    if isinstance(raw, str) and raw.strip():
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, dict) or not raw:
        return None
    out: Dict[str, float] = {}
    for key, value in raw.items():
        try:
            out[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return out or None


def _resolve_field_values(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "vendor": item.get("vendor_name") or item.get("vendor"),
        "amount": item.get("amount"),
        "invoice_number": item.get("invoice_number"),
        "due_date": item.get("due_date"),
    }


def _is_field_review_record(item: Dict[str, Any], metadata: Dict[str, Any]) -> bool:
    """Identify records whose current state warrants re-evaluation."""
    if item.get("requires_field_review") in (True, 1, "1", "true", "True"):
        return True
    if metadata.get("requires_field_review"):
        return True
    if str(item.get("exception_code") or "").strip().lower() == "field_review_required":
        return True
    if str(metadata.get("exception_code") or "").strip().lower() == "field_review_required":
        return True
    return False


def _evaluate(item: Dict[str, Any], metadata: Dict[str, Any], field_confidences: Dict[str, float]) -> Dict[str, Any]:
    """Run the new gate against the item's persisted signal."""
    overall = (
        item.get("confidence")
        if item.get("confidence") is not None
        else metadata.get("confidence")
    )
    sender = item.get("sender") or metadata.get("source_sender") or ""
    document_type = (
        metadata.get("document_type")
        or item.get("document_type")
        or "invoice"
    )
    primary_source = (
        metadata.get("primary_source")
        or item.get("primary_source")
        or ("attachment" if metadata.get("has_attachment") else "email")
    )
    has_attachment = bool(metadata.get("has_attachment") or item.get("has_attachment"))
    sender_domain = (
        metadata.get("source_sender_domain")
        or metadata.get("sender_domain")
        or ""
    )

    return evaluate_critical_field_confidence(
        overall_confidence=overall,
        field_values=_resolve_field_values(item),
        field_confidences=field_confidences,
        vendor_name=item.get("vendor_name") or item.get("vendor"),
        sender=sender,
        document_type=document_type,
        primary_source=primary_source,
        has_attachment=has_attachment,
        sender_domain=sender_domain,
    )


def _classify_outcome(item: Dict[str, Any], metadata: Dict[str, Any], gate: Dict[str, Any]) -> tuple[str, str]:
    """Compare the new gate to the persisted state. Returns (decision, reason)."""
    was_blocked = _is_field_review_record(item, metadata)
    now_blocked = bool(gate.get("requires_field_review"))

    old_blocker_fields = sorted(
        str(b.get("field"))
        for b in (metadata.get("confidence_blockers") or [])
        if isinstance(b, dict) and b.get("field")
    )
    new_blocker_fields = sorted(
        str(b.get("field"))
        for b in (gate.get("confidence_blockers") or [])
        if isinstance(b, dict) and b.get("field")
    )

    if was_blocked and not now_blocked:
        return (DECISION_CLEARED, "all critical fields cleared under per-field calibration")
    if was_blocked and now_blocked and old_blocker_fields != new_blocker_fields:
        return (DECISION_BLOCKERS_CHANGED, f"blockers shifted from {old_blocker_fields} to {new_blocker_fields}")
    return (DECISION_NO_CHANGE, "gate decision identical under new calibration")


def _apply_clear(db, item: Dict[str, Any], gate: Dict[str, Any]) -> None:
    """Clear field_review_required state when the new gate passes."""
    metadata = _parse_metadata(item)
    metadata.pop("requires_field_review", None)
    metadata["confidence_blockers"] = []
    metadata["confidence_advisories"] = gate.get("confidence_advisories") or []
    metadata["confidence_gate"] = {
        "calibration_decisions": gate.get("calibration_decisions") or [],
        "field_severities": gate.get("field_severities") or {},
        "recalibrated_at": datetime.now(timezone.utc).isoformat(),
        "recalibrated_by": "scripts/recalibrate_confidence_gate.py",
    }

    update_kwargs: Dict[str, Any] = {
        "metadata": json.dumps(metadata),
    }
    # Only clear the exception code if it matches; don't blow away
    # unrelated downstream codes (e.g. erp_post_failed).
    if str(item.get("exception_code") or "").strip().lower() == "field_review_required":
        update_kwargs["exception_code"] = None
        update_kwargs["exception_severity"] = None

    db.update_ap_item(item["id"], **update_kwargs)


def _apply_blockers_changed(db, item: Dict[str, Any], gate: Dict[str, Any]) -> None:
    """Update persisted blockers + advisories when the gate decision shifted
    but still blocks. Keeps the record in field_review state with the new
    blockers reflected."""
    metadata = _parse_metadata(item)
    metadata["requires_field_review"] = True
    metadata["confidence_blockers"] = gate.get("confidence_blockers") or []
    metadata["confidence_advisories"] = gate.get("confidence_advisories") or []
    metadata["confidence_gate"] = {
        "calibration_decisions": gate.get("calibration_decisions") or [],
        "field_severities": gate.get("field_severities") or {},
        "recalibrated_at": datetime.now(timezone.utc).isoformat(),
        "recalibrated_by": "scripts/recalibrate_confidence_gate.py",
    }
    db.update_ap_item(item["id"], metadata=json.dumps(metadata))


def _emit_audit(
    db,
    item: Dict[str, Any],
    gate: Dict[str, Any],
    decision: str,
    reason: str,
) -> None:
    """Write a confidence_gate_recalibrated audit event so the timeline
    records every decision the script made on this record."""
    idempotency_key = (
        f"recalibrate:{item['id']}:"
        f"{','.join(sorted(b.get('field', '') for b in (gate.get('confidence_blockers') or [])))}"
    )
    db.append_audit_event(
        {
            "id": f"EVT-{uuid.uuid4().hex}",
            "ap_item_id": item["id"],
            # M20 tenant-rename: an AP item without an organization_id
            # is malformed — record the audit row against the item's
            # actual org or skip. Pre-fix this fell back to the legacy
            # ``"default"`` bucket and silently bound recalibration
            # audit rows to the wrong tenant. Stripping plus an empty-
            # check keeps the script idempotent on partial data.
            "organization_id": str(item.get("organization_id") or "").strip() or "_unprovisioned",
            "event_type": "confidence_gate_recalibrated",
            "actor_type": "system",
            "actor_id": "scripts/recalibrate_confidence_gate.py",
            "ts": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "metadata": {
                "decision": decision,
                "blockers": gate.get("confidence_blockers") or [],
                "advisories": gate.get("confidence_advisories") or [],
                "field_severities": gate.get("field_severities") or {},
                "recalibration_commit": "b2cfe86",
            },
            "idempotency_key": idempotency_key,
        }
    )


def recalibrate(
    organization_id: str,
    *,
    commit: bool = False,
    verbose: bool = False,
    fetch_limit: int = 1000,
) -> List[RecordOutcome]:
    """Walk an org's AP items and re-evaluate gate decisions.

    Returns a list of RecordOutcome — one per item considered. Side-
    effects (updates + audit events) are only applied when ``commit``
    is True.
    """
    db = get_db()
    db.initialize()

    items = db.list_ap_items(organization_id, limit=fetch_limit)
    outcomes: List[RecordOutcome] = []

    for item in items:
        metadata = _parse_metadata(item)
        if not _is_field_review_record(item, metadata):
            continue

        field_confidences = _parse_field_confidences(item, metadata)
        if field_confidences is None:
            outcomes.append(
                RecordOutcome(
                    ap_item_id=str(item["id"]),
                    vendor_name=str(item.get("vendor_name") or ""),
                    invoice_number=str(item.get("invoice_number") or ""),
                    decision=DECISION_SKIPPED,
                    reason="no persisted field_confidences — cannot fairly re-evaluate",
                    old_blockers=[],
                    new_blockers=[],
                    new_advisories=[],
                )
            )
            continue

        gate = _evaluate(item, metadata, field_confidences)
        decision, reason = _classify_outcome(item, metadata, gate)

        old_blockers = sorted(
            str(b.get("field"))
            for b in (metadata.get("confidence_blockers") or [])
            if isinstance(b, dict) and b.get("field")
        )
        new_blockers = sorted(
            str(b.get("field"))
            for b in (gate.get("confidence_blockers") or [])
            if isinstance(b, dict) and b.get("field")
        )
        new_advisories = sorted(
            str(a.get("field"))
            for a in (gate.get("confidence_advisories") or [])
            if isinstance(a, dict) and a.get("field")
        )

        outcomes.append(
            RecordOutcome(
                ap_item_id=str(item["id"]),
                vendor_name=str(item.get("vendor_name") or ""),
                invoice_number=str(item.get("invoice_number") or ""),
                decision=decision,
                reason=reason,
                old_blockers=old_blockers,
                new_blockers=new_blockers,
                new_advisories=new_advisories,
            )
        )

        if commit:
            if decision == DECISION_CLEARED:
                _apply_clear(db, item, gate)
            elif decision == DECISION_BLOCKERS_CHANGED:
                _apply_blockers_changed(db, item, gate)
            # NO_CHANGE → still emit audit so the timeline shows the
            # re-eval happened (idempotency key prevents duplicates).
            _emit_audit(db, item, gate, decision, reason)

    return outcomes


def _print_report(outcomes: List[RecordOutcome], commit: bool, verbose: bool) -> None:
    counts: Dict[str, int] = {}
    for outcome in outcomes:
        counts[outcome.decision] = counts.get(outcome.decision, 0) + 1

    mode = "COMMIT" if commit else "DRY RUN"
    print(f"=== Confidence-gate recalibration ({mode}) ===")
    print(f"Records considered: {len(outcomes)}")
    for decision in (DECISION_CLEARED, DECISION_BLOCKERS_CHANGED, DECISION_NO_CHANGE, DECISION_SKIPPED):
        n = counts.get(decision, 0)
        if n:
            print(f"  {decision:24s}  {n}")

    if verbose or not commit:
        print()
        print("Per-record:")
        for outcome in outcomes:
            tag = {
                DECISION_CLEARED: "✓ CLEAR ",
                DECISION_BLOCKERS_CHANGED: "~ SHIFT ",
                DECISION_NO_CHANGE: "  same  ",
                DECISION_SKIPPED: "? skip  ",
            }.get(outcome.decision, "?")
            line = (
                f"  {tag} {outcome.ap_item_id[:18]:18s} "
                f"{(outcome.vendor_name or '<no-vendor>')[:28]:28s} "
                f"{outcome.invoice_number or '—':16s}"
            )
            if outcome.decision == DECISION_BLOCKERS_CHANGED:
                line += f"  was={outcome.old_blockers} now={outcome.new_blockers}"
            elif outcome.decision == DECISION_CLEARED and outcome.new_advisories:
                line += f"  advisories={outcome.new_advisories}"
            elif outcome.decision == DECISION_SKIPPED:
                line += f"  ({outcome.reason})"
            print(line)

    if not commit:
        print()
        print("Re-run with --commit to apply these changes.")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--organization-id", required=True, help="Tenant to scope the re-evaluation to")
    parser.add_argument("--commit", action="store_true", help="Apply the changes (default: dry run)")
    parser.add_argument("--verbose", action="store_true", help="Print every record decision")
    parser.add_argument("--limit", type=int, default=1000, help="Max records to fetch (default 1000)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    outcomes = recalibrate(
        organization_id=args.organization_id,
        commit=args.commit,
        verbose=args.verbose,
        fetch_limit=args.limit,
    )
    _print_report(outcomes, commit=args.commit, verbose=args.verbose)
    return 0


if __name__ == "__main__":
    sys.exit(main())
