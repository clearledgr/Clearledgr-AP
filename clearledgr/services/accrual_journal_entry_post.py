"""Accrual JE ERP post + reversal scheduler (Wave 5 / G5 carry-over).

G5 shipped the proposal generator. This module wires the actual
ERP-side post (via :func:`erp_router.post_journal_entry`) and the
reversal scheduling so month-end close completes end-to-end.

Three entry points:

  * :func:`post_accrual_je` — convert :class:`AccrualJEProposal`
    to the ERP-router journal-entry shape, post it, persist a
    ``accrual_je_runs`` row tracking the JE id + reversal date.
  * :func:`post_pending_reversals` — sweep ``accrual_je_runs``
    for posted-but-unreversed rows whose reversal_date is today
    or earlier, post the reversal, update the row.
  * :func:`run_month_end_close` — single-shot orchestrator the
    Celery beat task calls: per-org, build the proposal for the
    just-closed month, persist, post, return summary.

Idempotency:
  - DB partial unique index on (org, period_start, period_end,
    jurisdiction) WHERE status != 'failed' blocks duplicate
    successful posts for the same period.
  - Reversal sweep is keyed off ``reversal_posted_at IS NULL``,
    so a re-run skips already-reversed rows.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


_TWO_PLACES = Decimal("0.01")


def _money(value: Any) -> Decimal:
    if value is None:
        return Decimal("0.00")
    if isinstance(value, Decimal):
        return value.quantize(_TWO_PLACES)
    return Decimal(str(value)).quantize(_TWO_PLACES)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ── Outcomes ───────────────────────────────────────────────────────


@dataclass
class AccrualPostOutcome:
    accrual_run_id: str
    status: str               # posted | failed | duplicate_existing
    erp_type: str
    provider_reference: Optional[str] = None
    error_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "accrual_run_id": self.accrual_run_id,
            "status": self.status,
            "erp_type": self.erp_type,
            "provider_reference": self.provider_reference,
            "error_reason": self.error_reason,
        }


@dataclass
class ReversalSweepResult:
    swept: int = 0
    reversed_ok: int = 0
    failed: int = 0
    details: List[Dict[str, Any]] = field(default_factory=list)


# ── Proposal -> ERP entry shape ───────────────────────────────────


def _proposal_to_erp_entry(
    proposal,
    *,
    posting_date: str,
    description: str,
) -> Dict[str, Any]:
    """Convert :class:`AccrualJEProposal` to the dict shape
    erp_router.post_journal_entry / post_to_xero / post_to_qb expect.

    Lines: ``{debit, credit, account, account_name}`` with debit
    populated for Dr lines, credit for Cr.
    """
    lines: List[Dict[str, Any]] = []
    for je in proposal.je_lines:
        amount = float(je.amount)
        if je.direction == "debit":
            lines.append({
                "debit": amount,
                "credit": 0.0,
                "account": je.account_code,
                "account_name": je.account_label,
                "description": je.description,
            })
        else:
            lines.append({
                "debit": 0.0,
                "credit": amount,
                "account": je.account_code,
                "account_name": je.account_label,
                "description": je.description,
            })
    return {
        "date": posting_date,
        "description": description,
        "currency": proposal.currency,
        "lines": lines,
    }


def _proposal_to_reversal_entry(
    *,
    proposal_dict: Dict[str, Any],
    posting_date: str,
    description: str,
) -> Dict[str, Any]:
    """Build the reversal entry from a stored proposal dict.

    Reversal flips Dr <-> Cr on every line, same date, same amount.
    """
    je_lines = (proposal_dict or {}).get("je_lines") or []
    reversed_lines: List[Dict[str, Any]] = []
    for je in je_lines:
        amount = float(je.get("amount") or 0)
        direction = je.get("direction") or "debit"
        reversed_lines.append({
            "debit": amount if direction == "credit" else 0.0,
            "credit": amount if direction == "debit" else 0.0,
            "account": je.get("account_code"),
            "account_name": je.get("account_label"),
            "description": (
                f"REVERSAL — {je.get('description') or ''}"
            ),
        })
    return {
        "date": posting_date,
        "description": description,
        "currency": (proposal_dict or {}).get("currency") or "GBP",
        "lines": reversed_lines,
    }


# ── Async runner helper ──────────────────────────────────────────


def _run_async_in_thread(coro_factory) -> Dict[str, Any]:
    """Drive an async coroutine from a sync caller without colliding
    with an outer ASGI loop. Returns the coroutine's result or a
    dict with status='error' on exception."""
    holder: Dict[str, Any] = {}

    def _runner():
        new_loop = asyncio.new_event_loop()
        try:
            holder["value"] = new_loop.run_until_complete(coro_factory())
        except Exception as exc:
            holder["value"] = {
                "status": "error",
                "reason": str(exc)[:500],
            }
        finally:
            new_loop.close()

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join(timeout=120)
    return holder.get("value") or {
        "status": "error",
        "reason": "post_thread_timed_out",
    }


# ── Persist + post ────────────────────────────────────────────────


def _insert_accrual_run_pending(
    db,
    *,
    proposal,
    organization_id: str,
    jurisdiction: str,
    actor_id: Optional[str],
) -> str:
    run_id = f"AR-{uuid.uuid4().hex[:24]}"
    sql = (
        "INSERT INTO accrual_je_runs "
        "(id, organization_id, period_start, period_end, jurisdiction, "
        " erp_type, currency, accrual_amount, line_count, proposal_json, "
        " status, reversal_date, created_at, created_by, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
        "        'pending', %s, %s, %s, %s)"
    )
    now = _now_iso()
    proposal_dict = proposal.to_dict()
    db.initialize()
    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (
                run_id,
                organization_id,
                proposal.period_start,
                proposal.period_end,
                jurisdiction,
                proposal.erp_type,
                proposal.currency,
                _money(proposal.debit_total),
                len(proposal.lines),
                json.dumps(proposal_dict),
                proposal.reversal_date,
                now, actor_id, now,
            ))
            conn.commit()
    except Exception as exc:
        msg = str(exc).lower()
        if "duplicate key" in msg or "unique constraint" in msg:
            raise ValueError(
                "duplicate_period_run: an active accrual run for "
                f"({organization_id}, {proposal.period_start}, "
                f"{proposal.period_end}, {jurisdiction}) already exists"
            )
        raise
    return run_id


def _update_run_with_post_result(
    db, *, run_id: str, result: Dict[str, Any],
) -> None:
    db.initialize()
    status = "posted" if result.get("status") == "success" else "failed"
    error_reason = (
        None if status == "posted" else (
            str(result.get("reason") or "post_failed")[:500]
        )
    )
    posted_at = _now_iso() if status == "posted" else None
    sql = (
        "UPDATE accrual_je_runs "
        "SET status = %s, provider_reference = %s, "
        "    provider_response_json = %s, error_reason = %s, "
        "    posted_at = %s, updated_at = %s "
        "WHERE id = %s"
    )
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (
            status,
            result.get("entry_id") or result.get("erp_reference"),
            json.dumps(result),
            error_reason,
            posted_at,
            _now_iso(),
            run_id,
        ))
        conn.commit()


def post_accrual_je(
    db,
    *,
    proposal,
    organization_id: str,
    jurisdiction: str = "GB",
    actor_id: Optional[str] = None,
) -> AccrualPostOutcome:
    """Persist + post the accrual JE proposal.

    Returns :class:`AccrualPostOutcome`. Idempotent at the DB layer:
    a duplicate active run for the same period raises ValueError
    with a 409-shaped message — caller must mark the prior failed
    or supersede it before retrying.
    """
    if not proposal.balanced:
        raise ValueError("proposal_not_balanced")
    if not proposal.je_lines:
        raise ValueError("proposal_has_no_je_lines")

    run_id = _insert_accrual_run_pending(
        db,
        proposal=proposal,
        organization_id=organization_id,
        jurisdiction=jurisdiction,
        actor_id=actor_id,
    )

    entry = _proposal_to_erp_entry(
        proposal,
        posting_date=proposal.accrual_date[:10],
        description=(
            f"Month-end accrual {proposal.period_end} "
            "(received-not-billed); reverses on "
            f"{proposal.reversal_date}"
        ),
    )

    from clearledgr.integrations.erp_router import post_journal_entry

    result = _run_async_in_thread(
        lambda: post_journal_entry(organization_id, entry),
    )
    _update_run_with_post_result(db, run_id=run_id, result=result)

    if result.get("status") == "success":
        try:
            db.append_audit_event({
                "box_id": run_id,
                "box_type": "accrual_run",
                "event_type": "accrual_je_posted",
                "actor_type": "user" if actor_id else "system",
                "actor_id": actor_id or "accrual_je_post",
                "organization_id": organization_id,
                "source": "accrual_je_post",
                "idempotency_key": (
                    f"accrual_je_posted:{organization_id}:{run_id}"
                ),
                "metadata": {
                    "run_id": run_id,
                    "period_start": proposal.period_start,
                    "period_end": proposal.period_end,
                    "erp_type": proposal.erp_type,
                    "amount": float(proposal.debit_total),
                    "provider_reference": result.get("entry_id"),
                    "reversal_date": proposal.reversal_date,
                },
            })
        except Exception:
            logger.exception("accrual_je_post: audit emit failed")
        return AccrualPostOutcome(
            accrual_run_id=run_id,
            status="posted",
            erp_type=proposal.erp_type,
            provider_reference=(
                result.get("entry_id") or result.get("erp_reference")
            ),
        )

    return AccrualPostOutcome(
        accrual_run_id=run_id,
        status="failed",
        erp_type=proposal.erp_type,
        error_reason=str(result.get("reason") or "post_failed"),
    )


# ── Reversal sweep ────────────────────────────────────────────────


def post_pending_reversals(
    db,
    *,
    organization_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    today: Optional[str] = None,
) -> ReversalSweepResult:
    """Find posted accrual runs whose reversal_date is today or
    earlier, post the reversal entry, mark reversed.

    Org-scoped when organization_id is set; otherwise sweeps every
    org (Celery beat task calls without an org filter)."""
    cutoff = today or _today()
    db.initialize()

    clauses = [
        "status = 'posted'",
        "reversal_posted_at IS NULL",
        "reversal_date <= %s",
    ]
    params: List[Any] = [cutoff]
    if organization_id:
        clauses.append("organization_id = %s")
        params.append(organization_id)
    sql = (
        "SELECT * FROM accrual_je_runs "
        "WHERE " + " AND ".join(clauses) + " "
        "ORDER BY reversal_date ASC"
    )
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()

    summary = ReversalSweepResult()
    for r in rows:
        row = dict(r)
        run_id = row["id"]
        org = row["organization_id"]
        proposal_json = row.get("proposal_json")
        try:
            proposal_dict = (
                json.loads(proposal_json)
                if isinstance(proposal_json, str) else proposal_json
            )
        except Exception:
            proposal_dict = {}
        reversal_entry = _proposal_to_reversal_entry(
            proposal_dict=proposal_dict,
            posting_date=row["reversal_date"][:10],
            description=(
                f"Reversal of accrual {run_id} "
                f"(period {row['period_start']} to {row['period_end']})"
            ),
        )

        from clearledgr.integrations.erp_router import post_journal_entry
        result = _run_async_in_thread(
            lambda: post_journal_entry(org, reversal_entry),
        )

        with db.connect() as conn:
            cur = conn.cursor()
            if result.get("status") == "success":
                cur.execute(
                    "UPDATE accrual_je_runs "
                    "SET status = 'reversal_posted', "
                    "    reversal_provider_reference = %s, "
                    "    reversal_response_json = %s, "
                    "    reversal_posted_at = %s, updated_at = %s "
                    "WHERE id = %s",
                    (
                        result.get("entry_id") or result.get("erp_reference"),
                        json.dumps(result),
                        _now_iso(),
                        _now_iso(),
                        run_id,
                    ),
                )
                conn.commit()
                summary.reversed_ok += 1
                summary.details.append({
                    "run_id": run_id, "status": "reversal_posted",
                    "provider_reference": (
                        result.get("entry_id")
                        or result.get("erp_reference")
                    ),
                })
                try:
                    db.append_audit_event({
                        "box_id": run_id,
                        "box_type": "accrual_run",
                        "event_type": "accrual_je_reversed",
                        "actor_type": "user" if actor_id else "system",
                        "actor_id": actor_id or "accrual_reversal_sweep",
                        "organization_id": org,
                        "source": "accrual_reversal_sweep",
                        "idempotency_key": (
                            f"accrual_je_reversed:{org}:{run_id}"
                        ),
                        "metadata": {
                            "run_id": run_id,
                            "reversal_provider_reference": (
                                result.get("entry_id")
                                or result.get("erp_reference")
                            ),
                        },
                    })
                except Exception:
                    logger.exception(
                        "accrual_reversal: audit emit failed",
                    )
            else:
                cur.execute(
                    "UPDATE accrual_je_runs "
                    "SET error_reason = %s, updated_at = %s "
                    "WHERE id = %s",
                    (
                        str(result.get("reason") or "reversal_post_failed")[:500],
                        _now_iso(),
                        run_id,
                    ),
                )
                conn.commit()
                summary.failed += 1
                summary.details.append({
                    "run_id": run_id, "status": "failed",
                    "error_reason": str(result.get("reason") or "")[:200],
                })
        summary.swept += 1
    return summary


# ── Month-end close orchestrator ──────────────────────────────────


def run_month_end_close(
    db,
    *,
    organization_id: str,
    period_start: str,
    period_end: str,
    erp_type: str = "xero",
    currency: str = "GBP",
    jurisdiction: str = "GB",
    actor_id: Optional[str] = None,
) -> AccrualPostOutcome:
    """Single-shot: build proposal -> post -> return outcome.

    Used by the Celery monthly task and the operator's manual
    'post month-end accrual' button."""
    from clearledgr.services.accrual_journal_entry import (
        build_accrual_je_proposal,
    )

    proposal = build_accrual_je_proposal(
        db,
        organization_id=organization_id,
        period_start=period_start,
        period_end=period_end,
        erp_type=erp_type,
        currency=currency,
    )
    if not proposal.lines:
        # Nothing to accrue — return a successful no-op outcome with
        # provider_reference=None so callers can distinguish from
        # error paths.
        return AccrualPostOutcome(
            accrual_run_id="",
            status="posted",  # no-op success
            erp_type=erp_type,
            provider_reference=None,
            error_reason=None,
        )

    return post_accrual_je(
        db,
        proposal=proposal,
        organization_id=organization_id,
        jurisdiction=jurisdiction,
        actor_id=actor_id,
    )


# ── Run lookups ───────────────────────────────────────────────────


def get_accrual_run(db, run_id: str) -> Optional[Dict[str, Any]]:
    db.initialize()
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM accrual_je_runs WHERE id = %s", (run_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    out = dict(row)
    for col in ("proposal_json", "provider_response_json",
                "reversal_response_json"):
        raw = out.pop(col, None)
        target = col.removesuffix("_json")
        if raw:
            try:
                out[target] = (
                    json.loads(raw) if isinstance(raw, str) else raw
                )
            except Exception:
                out[target] = None
        else:
            out[target] = None
    return out


def list_accrual_runs(
    db,
    *,
    organization_id: str,
    status: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    db.initialize()
    clauses = ["organization_id = %s"]
    params: List[Any] = [organization_id]
    if status:
        clauses.append("status = %s")
        params.append(status)
    safe_limit = max(1, min(int(limit or 50), 500))
    params.append(safe_limit)
    sql = (
        "SELECT id, organization_id, period_start, period_end, "
        "       jurisdiction, erp_type, currency, accrual_amount, "
        "       line_count, status, provider_reference, posted_at, "
        "       reversal_date, reversal_provider_reference, "
        "       reversal_posted_at, error_reason, created_at "
        "FROM accrual_je_runs "
        "WHERE " + " AND ".join(clauses) + " "
        "ORDER BY period_end DESC, created_at DESC LIMIT %s"
    )
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
    return [dict(r) for r in rows]
