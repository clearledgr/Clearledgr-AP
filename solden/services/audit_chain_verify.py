"""Audit-chain verification helpers.

Backs the marketing claim "every event hashes the prior; strip a row
and the chain breaks" with a runtime check operators (and auditors)
can run against a live tenant. Re-implements the SHA-256 hash formula
exactly as the BEFORE-INSERT trigger ``clearledgr_audit_hash_chain``
in migration v77, then walks the chain head and verifies each row's
stored hash matches the recomputed one + each row's ``prev_hash``
matches the prior row's ``hash``.

Used by both:

* ``GET /api/workspace/audit/chain-status`` — operator-facing
  endpoint surfaces "chain intact" status with last-verified
  timestamp.
* ``tests/test_audit_chain_integrity.py`` — re-uses the same
  helpers (previously duplicated inline in the test file).

Extracting the helpers here keeps a single source of truth: the
trigger's hash formula, the genesis sentinel, and the canonical
row representation are now defined once and consumed in both
places. A regression in any of them shows up identically in
production verification and in the test suite.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# Hash chain formula constants must match
# clearledgr/core/migrations.py:_v77_audit_events_hash_chain exactly.

GENESIS_PREFIX = "solden:audit:genesis:"
HASH_FIELD_SEPARATOR = "|"
PREV_HASH_SEPARATOR = "||"

# Default sample size when verifying — caps verification cost on
# orgs with very long chains. The head-N rows are the most likely
# tampering target (recent activity), so a sample at the head gives
# the highest signal per row examined.
DEFAULT_SAMPLE_SIZE = 100


def genesis_hash(organization_id: str) -> str:
    """Per-org genesis sentinel. The first row in an org's chain
    has ``prev_hash`` set to this value — deterministic, distinct
    per org so chains can't cross-link, and verifiable from the
    org_id alone."""
    sentinel = f"{GENESIS_PREFIX}{organization_id or ''}"
    return hashlib.sha256(sentinel.encode("utf-8")).hexdigest()


def canonical_row(row: Dict[str, Any]) -> str:
    """Pipe-separated identity fields. Must match the SQL trigger's
    ``concat_ws('|', ...)`` call exactly. Order matters; reorder a
    field and every recomputed hash mismatches."""
    fields = [
        row.get("id") or "",
        row.get("ts") or "",
        row.get("box_id") or "",
        row.get("box_type") or "",
        row.get("event_type") or "",
        row.get("prev_state") or "",
        row.get("new_state") or "",
        row.get("actor_type") or "",
        row.get("actor_id") or "",
        row.get("idempotency_key") or "",
        row.get("payload_json") or "",
        row.get("organization_id") or "",
    ]
    return HASH_FIELD_SEPARATOR.join(str(f) for f in fields)


def expected_hash(prev_hash: str, row: Dict[str, Any]) -> str:
    """Reproduce the trigger's hash formula:
    ``sha256(prev_hash || "||" || canonical(row))``. Returns hex
    digest matching the ``hash`` column the trigger writes."""
    canonical = canonical_row(row)
    payload = f"{prev_hash}{PREV_HASH_SEPARATOR}{canonical}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _fetch_raw_chain_tail(
    db: Any, *, organization_id: str, limit: int,
) -> List[Dict[str, Any]]:
    """Return the most recent ``limit`` audit rows for an org,
    ordered by chain_seq ASC. Reads the canonical fields the
    trigger hashed (raw payload_json string, not deserialized).
    """
    sql = (
        "SELECT id, ts, box_id, box_type, event_type, prev_state, "
        "new_state, actor_type, actor_id, idempotency_key, "
        "payload_json, organization_id, prev_hash, hash, chain_seq "
        "FROM audit_events "
        "WHERE organization_id = %s AND chain_seq IS NOT NULL "
        "ORDER BY chain_seq DESC LIMIT %s"
    )
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (organization_id, int(limit)))
        rows = [dict(r) for r in cur.fetchall() if r is not None]
    # We selected DESC so the head is first; flip to ASC so the
    # caller can walk forward from oldest-in-window to newest.
    rows.reverse()
    return rows


def verify_chain_head(
    db: Any,
    *,
    organization_id: str,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
) -> Dict[str, Any]:
    """Verify the head ``sample_size`` rows of the org's audit
    chain. Returns a structured status dict.

    Outcomes:

    * ``chain_intact: True`` — every recomputed hash matches the
      stored hash AND every row's prev_hash equals the prior row's
      hash (or genesis sentinel for chain_seq=1).
    * ``chain_intact: False`` with ``broken_at_chain_seq`` /
      ``break_kind`` populated — first divergence found.
    * Empty chain (no rows) → ``chain_intact: True``,
      ``chain_length: 0``. An empty chain is technically
      consistent.
    """
    sample_size = max(1, min(int(sample_size or DEFAULT_SAMPLE_SIZE), 5000))
    organization_id = str(organization_id or "").strip()
    if not organization_id:
        raise ValueError("verify_chain_head: organization_id required")

    rows = _fetch_raw_chain_tail(db, organization_id=organization_id, limit=sample_size)

    # Read the head separately so we can report length even when the
    # sample window doesn't reach the absolute head (e.g. someone
    # passed sample_size smaller than the chain).
    chain_length = 0
    head_chain_seq = 0
    head_event_id: Optional[str] = None
    head_hash: Optional[str] = None
    head_ts: Optional[str] = None
    if rows:
        head_row = rows[-1]
        head_chain_seq = int(head_row.get("chain_seq") or 0)
        chain_length = head_chain_seq
        head_event_id = str(head_row.get("id") or "")
        head_hash = str(head_row.get("hash") or "")
        head_ts = str(head_row.get("ts") or "")

    verified_at = datetime.now(timezone.utc).isoformat()

    if not rows:
        return {
            "organization_id": organization_id,
            "chain_intact": True,
            "chain_length": 0,
            "head_chain_seq": 0,
            "head_event_id": None,
            "head_hash_prefix": None,
            "head_ts": None,
            "verified_rows": 0,
            "verified_at": verified_at,
            "genesis_hash_prefix": genesis_hash(organization_id)[:16],
        }

    # Walk the sample from oldest-in-window to newest, verifying
    # each row's hash and prev_hash linkage.
    prior_hash: Optional[str] = None
    for idx, row in enumerate(rows):
        chain_seq = int(row.get("chain_seq") or 0)
        stored_hash = str(row.get("hash") or "")
        stored_prev = str(row.get("prev_hash") or "")

        # Determine the expected prev_hash for this row.
        if idx == 0:
            # First row in our sample window. If chain_seq is 1,
            # the row is the genesis row and prev_hash must equal
            # the genesis sentinel. Otherwise we don't know the
            # prior row's hash from outside the window — trust
            # the stored prev_hash for the linkage check, but
            # still verify the row's own hash recomputes.
            if chain_seq == 1:
                expected_prev = genesis_hash(organization_id)
                if stored_prev != expected_prev:
                    return _broken(
                        organization_id, chain_length, idx, row,
                        kind="genesis_prev_hash_mismatch",
                        verified_at=verified_at,
                        head_event_id=head_event_id, head_hash=head_hash, head_ts=head_ts,
                    )
            prior_hash = stored_prev
        else:
            # Linkage: this row's prev_hash must equal the prior
            # row's hash. A break here means a row was inserted /
            # modified / removed between them.
            if stored_prev != prior_hash:
                return _broken(
                    organization_id, chain_length, idx, row,
                    kind="prev_hash_breaks_linkage",
                    verified_at=verified_at,
                    head_event_id=head_event_id, head_hash=head_hash, head_ts=head_ts,
                )

        # Recompute and verify the row's own hash.
        recomputed = expected_hash(prior_hash or "", row)
        if recomputed != stored_hash:
            return _broken(
                organization_id, chain_length, idx, row,
                kind="hash_recompute_mismatch",
                verified_at=verified_at,
                head_event_id=head_event_id, head_hash=head_hash, head_ts=head_ts,
            )

        prior_hash = stored_hash

    return {
        "organization_id": organization_id,
        "chain_intact": True,
        "chain_length": chain_length,
        "head_chain_seq": head_chain_seq,
        "head_event_id": head_event_id,
        "head_hash_prefix": (head_hash or "")[:16],
        "head_ts": head_ts,
        "verified_rows": len(rows),
        "verified_at": verified_at,
        "genesis_hash_prefix": genesis_hash(organization_id)[:16],
    }


def _broken(
    organization_id: str,
    chain_length: int,
    idx: int,
    row: Dict[str, Any],
    *,
    kind: str,
    verified_at: str,
    head_event_id: Optional[str],
    head_hash: Optional[str],
    head_ts: Optional[str],
) -> Dict[str, Any]:
    """Return a structured break report. The break point's
    chain_seq + event_id let an auditor jump directly to the
    affected row."""
    chain_seq = int(row.get("chain_seq") or 0)
    return {
        "organization_id": organization_id,
        "chain_intact": False,
        "chain_length": chain_length,
        "head_chain_seq": chain_length,
        "head_event_id": head_event_id,
        "head_hash_prefix": (head_hash or "")[:16] or None,
        "head_ts": head_ts,
        "verified_rows": idx + 1,
        "verified_at": verified_at,
        "broken_at_chain_seq": chain_seq,
        "broken_at_event_id": str(row.get("id") or ""),
        "break_kind": kind,
    }
