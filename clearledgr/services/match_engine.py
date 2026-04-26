"""MatchEngine protocol + MatchRecord primitive (Gap 3).

Matching is a fundamental operation that recurs across finance:

* **AP 3-way match** — invoice + PO + GRN
* **Bank reconciliation** — bank statement line + ERP GL transaction
* **AR cash application** — incoming payment + outstanding invoice
* **Vendor statement reconciliation** — vendor's statement of account ↔ our AP records
* **Intercompany pairing** — subsidiary A's AR ↔ subsidiary B's AP

Every match has the same shape: candidates + tolerances + a decision
+ audit. Today only AP's version exists, hardcoded in
:class:`PurchaseOrderService.match_invoice_to_po`. As Clearledgr
expands across the deck's roadmap (Q4 2026 AR, H1 2027 Recon, H2
2027 Close), each new workflow needs its own matcher — but they all
need the same plumbing for tolerance config, audit, replay, and
human override.

This module is the abstraction:

1. **`MatchEngine` protocol** — every matching variant implements
   ``find_candidates`` / ``score`` / ``decide`` / ``record``. The
   service layer (``run_match``) orchestrates the four steps + writes
   the resulting :class:`MatchRecord` to the persistent
   ``match_records`` table.

2. **`MatchRecord` as first-class primitive** — every match attempt
   produces a persistent auditable row. Left/right references
   identify what was matched against what; ``tolerance_version_id``
   links to the policy version active at match time so historical
   replays are exact.

3. **Tolerance configuration** versioned via
   :class:`PolicyService` under the new ``match_tolerances`` policy
   kind — so "we tightened price tolerance from 5% to 2% — what
   matches would have failed?" replays use the same Gap-2 endpoint.

4. **Concrete implementations** plug in by registering with
   :func:`register_match_engine`. Today: AP 3-way and bank
   reconciliation. Future: AR, intercompany, vendor statement.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import (
    Any, Awaitable, Callable, Dict, List, Optional, Protocol, runtime_checkable,
)

logger = logging.getLogger(__name__)


# ─── Canonical types ───────────────────────────────────────────────


class MatchType(str, Enum):
    """Each value names one matching variant. Adding a new variant:
    add the value, register an implementation."""
    AP_THREE_WAY = "ap_three_way"
    BANK_RECONCILIATION = "bank_reconciliation"
    AR_CASH_APPLICATION = "ar_cash_application"
    VENDOR_STATEMENT_RECON = "vendor_statement_recon"
    INTERCOMPANY = "intercompany"


class MatchStatus(str, Enum):
    """Match outcome states. Mirrors the Box state-machine
    pattern — every transition is explicit; status changes are
    audited via ``updated_at`` + the override_of_match_id link."""
    PENDING = "pending"
    MATCHED = "matched"
    PARTIAL_MATCH = "partial_match"
    NO_MATCH = "no_match"
    MULTIPLE_MATCHES = "multiple_matches"
    EXCEPTION = "exception"
    OVERRIDDEN = "overridden"


@dataclass
class MatchCandidate:
    """One potential right-side match for a left-side input.

    ``score`` is the engine's confidence (0.0-1.0).  ``variance``
    holds the field-level deltas (price variance %, quantity diff,
    date offset, etc.) so the audit row can reconstruct why this
    candidate was scored as it was.
    """
    right_type: str
    right_id: str
    score: float
    variance: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MatchInput:
    """Input to a match engine — what we're trying to match."""
    organization_id: str
    left_type: str
    left_id: str
    payload: Dict[str, Any]
    """Engine-specific payload: AP three-way wants
    ``{amount, vendor_name, invoice_lines, currency}``; bank recon
    wants ``{amount, currency, posted_at, description, account_id}``;
    each engine documents its own contract."""


@dataclass
class MatchRecord:
    """Persistent match-as-a-row."""
    id: str
    organization_id: str
    match_type: str
    status: str
    confidence: float
    left_type: str
    left_id: str
    right_type: str
    right_id: Optional[str]
    extra_refs: List[Dict[str, Any]]
    """Additional referenced records — e.g., the GRN that completed
    a 3-way match alongside the PO; Italian intercompany pairings
    that span 3+ rows."""
    tolerance_version_id: Optional[str]
    variance: Dict[str, Any]
    exceptions: List[str]
    metadata: Dict[str, Any]
    box_id: Optional[str]
    box_type: Optional[str]
    created_at: str
    updated_at: str
    created_by: str
    override_of_match_id: Optional[str] = None


# ─── Protocol ──────────────────────────────────────────────────────


@runtime_checkable
class MatchEngine(Protocol):
    """Every matching variant implements this protocol.

    Lifecycle:
      1. ``find_candidates`` returns up to N right-side records that
         might match (sorted descending by raw score).
      2. ``score`` returns a confidence + variance per candidate.
         Pure function — no DB writes; tolerance is read here.
      3. ``decide`` picks a single best candidate (or none) and
         classifies it (MATCHED / PARTIAL_MATCH / NO_MATCH /
         EXCEPTION). Channel-specific business rules.
      4. ``record`` is invoked by ``run_match`` to persist the
         resulting :class:`MatchRecord`. Implementations don't
         override this typically — the default ``MatchEngineBase``
         handles persistence.
    """
    match_type: str

    async def find_candidates(
        self, input: MatchInput, *, limit: int = 10,
    ) -> List[MatchCandidate]: ...

    async def score(
        self, input: MatchInput, candidate: MatchCandidate,
    ) -> MatchCandidate:
        """Returns the candidate with refreshed score + variance.
        Engines that compute score during find_candidates can
        return the candidate unchanged."""

    async def decide(
        self,
        input: MatchInput,
        candidates: List[MatchCandidate],
    ) -> tuple[MatchStatus, Optional[MatchCandidate], List[str]]:
        """Returns (status, chosen_candidate_or_none, exception_codes).
        ``exception_codes`` is populated for non-MATCHED outcomes —
        downstream coordination uses these for routing decisions."""


# ─── Registry ──────────────────────────────────────────────────────


_ENGINE_REGISTRY: Dict[str, MatchEngine] = {}


def register_match_engine(engine: MatchEngine) -> None:
    """Register an engine at module-import time. Idempotent for
    identical instance re-registration."""
    existing = _ENGINE_REGISTRY.get(engine.match_type)
    if existing is not None:
        if existing is engine:
            return
        raise ValueError(
            f"Match engine for match_type={engine.match_type!r} already registered "
            f"({type(existing).__name__}); refusing to overwrite with "
            f"{type(engine).__name__}."
        )
    _ENGINE_REGISTRY[engine.match_type] = engine
    logger.info("match_engine: registered %s", engine.match_type)


def get_match_engine(match_type: str) -> Optional[MatchEngine]:
    return _ENGINE_REGISTRY.get(match_type)


def list_registered_engines() -> List[str]:
    return sorted(_ENGINE_REGISTRY.keys())


# ─── Universal orchestrator ────────────────────────────────────────


async def run_match(
    *,
    match_type: str,
    input: MatchInput,
    candidate_limit: int = 10,
    actor: str = "system",
    box_id: Optional[str] = None,
    box_type: Optional[str] = None,
) -> MatchRecord:
    """Run a match through the registered engine + persist the result.

    Steps:
      1. Look up the engine for match_type
      2. Resolve the active match_tolerances policy version (so the
         resulting MatchRecord can replay against historical
         tolerances)
      3. find_candidates → score each → decide
      4. Build a MatchRecord and persist to ``match_records``

    Idempotency: if a MatchRecord already exists for
    ``(organization_id, left_type, left_id, match_type)`` with a
    non-overridden status, this returns it instead of creating a
    new one. Overrides are explicit (call ``record_override``) — no
    silent re-matches.
    """
    engine = get_match_engine(match_type)
    if engine is None:
        raise ValueError(
            f"No match engine registered for match_type={match_type!r}. "
            f"Registered: {list_registered_engines()}"
        )

    existing = _find_existing_match(
        organization_id=input.organization_id,
        left_type=input.left_type,
        left_id=input.left_id,
        match_type=match_type,
    )
    if existing and existing.status != MatchStatus.OVERRIDDEN.value:
        return existing

    tolerance_version_id = _resolve_tolerance_version_id(input.organization_id)

    candidates = await engine.find_candidates(input, limit=candidate_limit)
    scored: List[MatchCandidate] = []
    for c in candidates:
        scored.append(await engine.score(input, c))
    status, chosen, exception_codes = await engine.decide(input, scored)

    record = _build_match_record(
        match_type=match_type,
        status=status,
        chosen=chosen,
        all_candidates=scored,
        exception_codes=exception_codes,
        input=input,
        tolerance_version_id=tolerance_version_id,
        actor=actor,
        box_id=box_id,
        box_type=box_type,
    )
    _persist_match_record(record)
    return record


async def record_override(
    *,
    organization_id: str,
    overridden_match_id: str,
    new_right_type: str,
    new_right_id: str,
    actor: str,
    reason: str,
) -> MatchRecord:
    """Record a human override: previous match is marked OVERRIDDEN,
    a new match record is created linking to it via
    ``override_of_match_id``. Old record's status flips but its
    other fields stay frozen — the overridden_at history is
    captured by ``updated_at`` + the new record's existence."""
    from clearledgr.core.database import get_db
    db = get_db()
    if not hasattr(db, "connect"):
        raise RuntimeError("DB connection unavailable")

    existing = _fetch_match_by_id(organization_id, overridden_match_id)
    if existing is None:
        raise LookupError(f"match {overridden_match_id!r} not found for org {organization_id!r}")

    now = datetime.now(timezone.utc).isoformat()
    db.initialize()
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE match_records SET status = %s, updated_at = %s WHERE id = %s",
            (MatchStatus.OVERRIDDEN.value, now, overridden_match_id),
        )
        conn.commit()

    new_record = MatchRecord(
        id=f"MR-{uuid.uuid4().hex}",
        organization_id=organization_id,
        match_type=existing.match_type,
        status=MatchStatus.MATCHED.value,
        confidence=1.0,  # human says yes
        left_type=existing.left_type,
        left_id=existing.left_id,
        right_type=new_right_type,
        right_id=new_right_id,
        extra_refs=[],
        tolerance_version_id=existing.tolerance_version_id,
        variance={},
        exceptions=[],
        metadata={"override_reason": reason, "overrode_match_id": overridden_match_id},
        box_id=existing.box_id,
        box_type=existing.box_type,
        created_at=now,
        updated_at=now,
        created_by=actor,
        override_of_match_id=overridden_match_id,
    )
    _persist_match_record(new_record)
    return new_record


# ─── Persistence helpers ───────────────────────────────────────────


def _build_match_record(
    *,
    match_type: str,
    status: MatchStatus,
    chosen: Optional[MatchCandidate],
    all_candidates: List[MatchCandidate],
    exception_codes: List[str],
    input: MatchInput,
    tolerance_version_id: Optional[str],
    actor: str,
    box_id: Optional[str],
    box_type: Optional[str],
) -> MatchRecord:
    now = datetime.now(timezone.utc).isoformat()
    return MatchRecord(
        id=f"MR-{uuid.uuid4().hex}",
        organization_id=input.organization_id,
        match_type=match_type,
        status=status.value,
        confidence=chosen.score if chosen else 0.0,
        left_type=input.left_type,
        left_id=input.left_id,
        right_type=chosen.right_type if chosen else "",
        right_id=chosen.right_id if chosen else None,
        extra_refs=[
            {"right_type": c.right_type, "right_id": c.right_id, "score": c.score}
            for c in all_candidates if (chosen is None or c.right_id != chosen.right_id)
        ],
        tolerance_version_id=tolerance_version_id,
        variance=chosen.variance if chosen else {},
        exceptions=list(exception_codes),
        metadata={"input_payload_keys": sorted(input.payload.keys())},
        box_id=box_id,
        box_type=box_type,
        created_at=now,
        updated_at=now,
        created_by=actor,
        override_of_match_id=None,
    )


def _persist_match_record(record: MatchRecord) -> None:
    from clearledgr.core.database import get_db
    db = get_db()
    if not hasattr(db, "connect"):
        return
    db.initialize()
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO match_records
              (id, organization_id, match_type, status, confidence,
               left_type, left_id, right_type, right_id,
               extra_refs_json, tolerance_version_id,
               variance_json, exceptions_json, metadata_json,
               box_id, box_type,
               created_at, updated_at, created_by, override_of_match_id)
            VALUES
              (%s, %s, %s, %s, %s,
               %s, %s, %s, %s,
               %s, %s,
               %s, %s, %s,
               %s, %s,
               %s, %s, %s, %s)
            """,
            (
                record.id, record.organization_id, record.match_type,
                record.status, record.confidence,
                record.left_type, record.left_id,
                record.right_type, record.right_id,
                json.dumps(record.extra_refs),
                record.tolerance_version_id,
                json.dumps(record.variance),
                json.dumps(record.exceptions),
                json.dumps(record.metadata),
                record.box_id, record.box_type,
                record.created_at, record.updated_at,
                record.created_by, record.override_of_match_id,
            ),
        )
        conn.commit()


def _find_existing_match(
    *, organization_id: str, left_type: str, left_id: str, match_type: str,
) -> Optional[MatchRecord]:
    from clearledgr.core.database import get_db
    db = get_db()
    if not hasattr(db, "connect"):
        return None
    db.initialize()
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM match_records
            WHERE organization_id = %s AND left_type = %s AND left_id = %s AND match_type = %s
            ORDER BY created_at DESC LIMIT 1
            """,
            (organization_id, left_type, left_id, match_type),
        )
        row = cur.fetchone()
    return _row_to_record(dict(row)) if row else None


def _fetch_match_by_id(organization_id: str, match_id: str) -> Optional[MatchRecord]:
    from clearledgr.core.database import get_db
    db = get_db()
    if not hasattr(db, "connect"):
        return None
    db.initialize()
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM match_records WHERE id = %s AND organization_id = %s",
            (match_id, organization_id),
        )
        row = cur.fetchone()
    return _row_to_record(dict(row)) if row else None


def _row_to_record(row: Dict[str, Any]) -> MatchRecord:
    def _load_json(value: Any, default):
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, str) and value.strip():
            try:
                return json.loads(value)
            except Exception:
                return default
        return default

    return MatchRecord(
        id=str(row.get("id") or ""),
        organization_id=str(row.get("organization_id") or ""),
        match_type=str(row.get("match_type") or ""),
        status=str(row.get("status") or ""),
        confidence=float(row.get("confidence") or 0.0),
        left_type=str(row.get("left_type") or ""),
        left_id=str(row.get("left_id") or ""),
        right_type=str(row.get("right_type") or ""),
        right_id=str(row.get("right_id")) if row.get("right_id") else None,
        extra_refs=_load_json(row.get("extra_refs_json"), []),
        tolerance_version_id=str(row.get("tolerance_version_id")) if row.get("tolerance_version_id") else None,
        variance=_load_json(row.get("variance_json"), {}),
        exceptions=_load_json(row.get("exceptions_json"), []),
        metadata=_load_json(row.get("metadata_json"), {}),
        box_id=str(row.get("box_id")) if row.get("box_id") else None,
        box_type=str(row.get("box_type")) if row.get("box_type") else None,
        created_at=str(row.get("created_at") or ""),
        updated_at=str(row.get("updated_at") or ""),
        created_by=str(row.get("created_by") or ""),
        override_of_match_id=str(row.get("override_of_match_id")) if row.get("override_of_match_id") else None,
    )


def _resolve_tolerance_version_id(organization_id: str) -> Optional[str]:
    """Look up the active match_tolerances policy version for this
    org. Returns None when the org hasn't configured tolerances —
    engines fall back to their hard-coded defaults in that case."""
    try:
        from clearledgr.services.policy_service import PolicyService
        service = PolicyService(organization_id)
        version = service.get_active("match_tolerances")
        return version.id
    except Exception as exc:  # noqa: BLE001
        logger.debug("match_engine: tolerance version lookup failed — %s", exc)
        return None


def get_tolerance_for(
    organization_id: str, *, match_type: str, key: str, default: Any,
) -> Any:
    """Fetch a single tolerance value for a match engine.

    Tolerance data lives in the ``match_tolerances`` policy kind under
    a per-match-type sub-namespace. Default shape:

        {
            "ap_three_way": {
                "price_tolerance_percent": 2.0,
                "quantity_tolerance_percent": 5.0,
                "amount_tolerance": 10.0,
            },
            "bank_reconciliation": {
                "amount_tolerance": 0.01,
                "date_window_days": 3,
            }
        }
    """
    try:
        from clearledgr.services.policy_service import PolicyService
        service = PolicyService(organization_id)
        version = service.get_active("match_tolerances")
        engine_section = (version.content or {}).get(match_type, {})
        if isinstance(engine_section, dict) and key in engine_section:
            return engine_section[key]
    except Exception as exc:  # noqa: BLE001
        logger.debug("match_engine: tolerance lookup failed — %s", exc)
    return default
