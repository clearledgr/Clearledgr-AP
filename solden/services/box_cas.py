"""Compare-and-swap helper for AP-item box updates.

Sprint 4 Phase 1 ships the merge-semantics primitive that makes
specialist agents safe to run concurrently against the same Box.

The story:

* Multiple specialists may want to mutate the same AP item — e.g.,
  the AP agent updates ``state`` to ``needs_approval``, the
  vendor-compliance agent attaches a sanctions-flag annotation, the
  workflow-health agent records a heartbeat. If they all read the
  row, mutate locally, and write back, the last-writer-wins
  pattern silently drops the others' changes.
* ``ap_store.update_ap_item`` already supports an
  ``_expected_updated_at`` timestamp gate — the actual locking
  primitive lives there.
* This module wraps that primitive in the **read-mutate-write
  retry loop** specialists actually want to write: "read the box,
  let me mutate it, write it back; if anyone else moved it,
  retry; if they keep moving it, give up with a clear error".

The contract a specialist gets:

    update_box_with_cas(
        ap_item_id="ap-123",
        organization_id="org-x",
        mutator=lambda box: {**box, "state": "needs_approval"},
        actor_id="agent:vendor-compliance",
        max_retries=3,
    )

The mutator runs against a snapshot of the box; its return value
is the desired post-state. The helper walks the snapshot's
``updated_at`` through the gated update; on conflict (someone
moved the box between read and write), the loop reads + retries.
After ``max_retries`` consecutive conflicts, raises ``BoxConflict``
with both the observed-vs-expected timestamps so the caller can
log the contention.

This module is small, pure, and DB-aware via a ``SoldenDB``
handle. Tests use a fake handle. The actual DB-backed integration
test path is in ``test_box_cas.py``.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional


logger = logging.getLogger(__name__)


class BoxConflict(RuntimeError):
    """Raised when ``update_box_with_cas`` exhausts its retry budget.

    Carries ``observed_updated_at`` (what we saw on the final
    attempt) and ``expected_updated_at`` (what we wrote with). The
    contention is structural, not transient — same writer can't
    progress without re-reading the box and re-applying its
    mutation against the new state.
    """

    def __init__(
        self,
        message: str,
        *,
        ap_item_id: str,
        observed_updated_at: Optional[str] = None,
        expected_updated_at: Optional[str] = None,
        attempts: int = 0,
    ) -> None:
        super().__init__(message)
        self.ap_item_id = ap_item_id
        self.observed_updated_at = observed_updated_at
        self.expected_updated_at = expected_updated_at
        self.attempts = attempts


# Columns the CAS helper is permitted to update via mutator return
# values. Mirrors the production ``_AP_ITEM_ALLOWED_COLUMNS``
# whitelist on ``SoldenDB`` — copied here as a service-layer
# defense. A mutator returning a key not in this set raises
# ``ValueError`` so a buggy specialist can't sneak through arbitrary
# column writes.
_CAS_ALLOWED_COLUMNS = frozenset({
    "state", "vendor_name", "amount", "currency", "invoice_number",
    "received_at", "due_date", "approved_at", "rejected_at",
    "posted_at", "last_error", "metadata", "metadata_json",
    "thread_id", "subject", "attachment_url",
    "bank_account_number", "bank_routing_number",
    "approval_target", "confidence", "approver",
    "rejection_reason", "approval_reason", "needs_info_reason",
    "snooze_until", "erp_reference", "erp_type",
})


def update_box_with_cas(
    db: Any,
    *,
    ap_item_id: str,
    organization_id: str,
    mutator: Callable[[Dict[str, Any]], Dict[str, Any]],
    actor_id: str,
    actor_type: str = "agent",
    decision_reason: Optional[str] = None,
    max_retries: int = 3,
) -> Dict[str, Any]:
    """Compare-and-swap update on an AP-item box.

    Reads the current box, runs ``mutator`` over a copy, computes
    the diff vs the snapshot, and writes it through
    ``db.update_ap_item`` with the snapshot's ``updated_at`` as the
    optimistic-lock gate. On conflict (the row moved between read
    and write), retries up to ``max_retries`` times. The mutator
    runs **fresh** on each retry — it always sees the latest box
    state, never a stale snapshot.

    The mutator's return value must be a dict; only keys whose
    values differ from the snapshot are written. Keys not in
    ``_CAS_ALLOWED_COLUMNS`` raise ``ValueError`` to prevent buggy
    specialists from writing arbitrary columns.

    Returns the post-update box dict. Raises:

    * ``BoxConflict`` — retry budget exhausted; concurrent writers
      kept moving the box.
    * ``LookupError`` — ap_item_id not found in this org. (Caller
      should never see a foreign-tenant box because tenancy is
      enforced upstream, but this layer fail-closes anyway.)
    * ``ValueError`` — mutator returned an invalid column or
      non-dict result.
    """
    last_observed_updated_at: Optional[str] = None
    last_expected_updated_at: Optional[str] = None

    for attempt in range(1, max_retries + 1):
        # Re-read on every attempt so the mutator gets fresh state.
        box = db.get_ap_item(ap_item_id)
        if not box:
            raise LookupError(
                f"ap_item_id={ap_item_id!r} not found"
            )
        # Tenancy gate: the row must belong to this org. The store
        # is org-agnostic on the read side; this is the
        # service-layer defense that mirrors M19/M20.
        if str(box.get("organization_id") or "").strip() != organization_id:
            raise LookupError(
                f"ap_item_id={ap_item_id!r} not found in organization "
                f"{organization_id!r}"
            )

        snapshot = dict(box)
        post = mutator(dict(snapshot))
        if not isinstance(post, dict):
            raise ValueError(
                f"mutator returned {type(post).__name__}, expected dict"
            )

        # Diff: only write changed keys. Caller can return the full
        # box dict back (immutable functional style) or just the
        # patch — both work because we diff on values.
        diff: Dict[str, Any] = {}
        for key, new_value in post.items():
            if key in {"id", "organization_id", "updated_at", "created_at"}:
                # Identity + timestamp columns are managed by the
                # store; the CAS helper never writes them.
                continue
            if key not in _CAS_ALLOWED_COLUMNS:
                raise ValueError(
                    f"mutator wrote column {key!r} which is not in the "
                    f"CAS-allowed whitelist; add it to _CAS_ALLOWED_COLUMNS "
                    f"after reviewing the implications"
                )
            if snapshot.get(key) != new_value:
                diff[key] = new_value

        if not diff:
            # Mutator returned a no-op. Nothing to write; return
            # the snapshot unchanged.
            return snapshot

        last_expected_updated_at = snapshot.get("updated_at")
        update_kwargs = dict(diff)
        update_kwargs["_actor_type"] = actor_type
        update_kwargs["_actor_id"] = actor_id
        if decision_reason:
            update_kwargs["_decision_reason"] = decision_reason
        update_kwargs["_expected_updated_at"] = last_expected_updated_at

        ok = db.update_ap_item(ap_item_id, **update_kwargs)
        if ok:
            # Re-read to return the post-update box (the store
            # doesn't return the row from update_ap_item).
            return db.get_ap_item(ap_item_id) or snapshot

        # Conflict: someone else moved the box between our read
        # and write. Capture the observed updated_at for
        # diagnostics and retry.
        post_box = db.get_ap_item(ap_item_id) or {}
        last_observed_updated_at = post_box.get("updated_at")
        logger.info(
            "[box_cas] CAS conflict on %s attempt=%d expected=%s observed=%s",
            ap_item_id, attempt, last_expected_updated_at,
            last_observed_updated_at,
        )

    raise BoxConflict(
        f"CAS update on {ap_item_id!r} exhausted retries (max={max_retries})",
        ap_item_id=ap_item_id,
        observed_updated_at=last_observed_updated_at,
        expected_updated_at=last_expected_updated_at,
        attempts=max_retries,
    )
