"""IntakeAdapter protocol — uniform contract for every bill-intake channel.

Architectural gap #1 from the post-Phase-A-E review. Today
``erp_webhook_dispatch.py`` (NetSuite) and ``sap_webhook_dispatch.py``
(SAP S/4HANA) carry parallel-but-divergent implementations of the
same idea: receive a webhook, verify the signature, parse it, enrich
it, build :class:`InvoiceData`, hand off to the coordination
pipeline. Same shape, different code per channel — and the same
divergence will multiply with every future channel (Outlook, vendor
portals, EDI gateways, scanned upload, manual entry, additional
ERPs).

This module is the channel-agnostic seam. Each intake channel
implements :class:`IntakeAdapter`; the routes in
:mod:`clearledgr.api.erp_webhooks` (and the future Gmail/Outlook
equivalents) delegate to :func:`handle_intake_event`, which:

1. Looks up the registered adapter for the source.
2. Verifies the signature.
3. Records the inbound audit event (channel-agnostic).
4. Parses to a canonical :class:`IntakeEnvelope`.
5. For create/posted events: calls :meth:`IntakeAdapter.enrich` to
   build :class:`InvoiceData`, then hands off to
   ``InvoiceWorkflowService.process_new_invoice`` — the one entry
   point every coordination call site reads from.
6. For update/paid/cancelled events: calls
   :meth:`IntakeAdapter.derive_state_update`, applies the resulting
   transition through the canonical state-update path.

The dispatch branching (create/update/paid/cancelled) lives here
**not** inside each adapter — that's deliberate. It forces uniformity:
you can't accidentally have one channel handle ``Paid`` differently
from another. Channel-specific quirks live in the adapter's
:meth:`parse_envelope` (which canonicalises the event_type) and
:meth:`derive_state_update` (which decides the target state from
the raw payload).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional, Protocol, runtime_checkable

from clearledgr.core.ap_states import validate_transition
from clearledgr.core.database import get_db
from clearledgr.services.invoice_models import InvoiceData

logger = logging.getLogger(__name__)


# ─── Canonical envelope + intent shapes ────────────────────────────


# Canonical event types every adapter normalises to. Channel-specific
# names (NetSuite ``vendorbill.create``, SAP ``…SupplierInvoice.Created.v1``,
# Gmail ``message_received``) all map onto these.
INTAKE_EVENT_TYPES = frozenset({
    "create",       # bill arrived — run full coordination pipeline
    "posted",       # bill posted in ERP — same pipeline (treated as 'create' downstream)
    "update",       # field-level edit; refresh + maybe transition
    "blocked",      # payment block added → may transition to needs_approval
    "released",     # payment block cleared → may transition forward
    "paid",         # payment executed → close
    "cancelled",    # bill voided/reversed in ERP → close
    "delete",       # bill removed from ERP → close
})

CREATE_LIKE_EVENTS = frozenset({"create", "posted"})


@dataclass(frozen=True)
class IntakeEnvelope:
    """Channel-agnostic envelope — what every adapter's
    :meth:`parse_envelope` returns.

    The intake handler routes off ``event_type``; adapters consume
    the rest of the fields when building ``InvoiceData`` / state
    updates. ``raw_payload`` is the original unwrapped event body
    so adapters can read channel-specific extensions without
    burdening the canonical fields.
    """

    source_type: str
    event_type: str  # one of INTAKE_EVENT_TYPES
    source_id: str   # canonical per-source idempotency key (NetSuite ns_internal_id,
                     # SAP composite-key, Gmail message_id, etc.)
    organization_id: str
    raw_payload: Dict[str, Any] = field(default_factory=dict)
    event_id: Optional[str] = None
    received_at: Optional[str] = None
    # Per-channel extension fields. Adapters set these when relevant
    # so the dispatch handler can pass them downstream (e.g. NetSuite
    # uses account_id to disambiguate orgs sharing a tenant).
    channel_metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StateUpdate:
    """What an adapter's :meth:`derive_state_update` returns.

    ``target_state`` is the desired terminal state for this transition
    (``None`` if the event shouldn't change state — e.g. a memo edit).
    ``field_updates`` carry any AP-item field writes that should
    accompany the transition. Both pieces are applied atomically by
    the dispatch handler.
    """

    target_state: Optional[str]
    field_updates: Dict[str, Any] = field(default_factory=dict)
    # If True, the transition handler tolerates "current_state ==
    # target_state" as a no-op rather than logging an invalid-transition
    # warning. Set by adapters for events that are idempotent-by-design
    # (e.g. SAP `Released` arriving when we already cleared the block
    # locally on a Slack approve).
    idempotent_no_op_allowed: bool = False


# ─── Adapter protocol ──────────────────────────────────────────────


@runtime_checkable
class IntakeAdapter(Protocol):
    """Contract every intake channel implements.

    Concrete implementations live alongside their integration code
    (e.g. ``clearledgr/integrations/erp_netsuite_intake_adapter.py``)
    and are registered via :func:`register_adapter` at module-import
    time.
    """

    source_type: str
    """The canonical source name. Matches ``InvoiceData.source_type``
    and the URL segment in the webhook route
    (``/erp/webhooks/{source_type}/{org_id}``)."""

    async def verify_signature(
        self,
        raw: bytes,
        headers: Mapping[str, str],
        secret: str,
    ) -> bool: ...

    async def parse_envelope(
        self,
        raw: bytes,
        headers: Mapping[str, str],
        organization_id: str,
    ) -> IntakeEnvelope: ...

    async def enrich(
        self,
        organization_id: str,
        envelope: IntakeEnvelope,
    ) -> InvoiceData:
        """Build :class:`InvoiceData` from the envelope by fetching
        any additional coordination context from the source (vendor
        master, PO/GRN linkage, line items, bank history). Only
        called for ``create`` / ``posted`` events.

        Implementations should set ``erp_native=True`` when the
        bill originates inside the ERP (so the workflow's
        ``_post_to_erp`` short-circuit fires correctly)."""

    async def derive_state_update(
        self,
        organization_id: str,
        envelope: IntakeEnvelope,
    ) -> StateUpdate:
        """Decide the target state + field updates for non-create
        events (``update`` / ``blocked`` / ``released`` / ``paid`` /
        ``cancelled`` / ``delete``).

        Adapters that don't recognise the event_type should return
        ``StateUpdate(target_state=None)`` — the handler treats that
        as a no-op."""


# ─── Registry ──────────────────────────────────────────────────────


_REGISTRY: Dict[str, IntakeAdapter] = {}


def register_adapter(adapter: IntakeAdapter) -> None:
    """Register an adapter at module-import time.

    Idempotent for identical re-registration (so re-importing during
    tests doesn't crash); raises if a different instance tries to
    claim the same source_type."""
    existing = _REGISTRY.get(adapter.source_type)
    if existing is not None:
        if existing is adapter:
            return
        raise ValueError(
            f"Intake adapter for source_type={adapter.source_type!r} already registered "
            f"({type(existing).__name__}); refusing to overwrite with "
            f"{type(adapter).__name__}."
        )
    _REGISTRY[adapter.source_type] = adapter
    logger.info("intake_adapter: registered %s", adapter.source_type)


def get_adapter(source_type: str) -> Optional[IntakeAdapter]:
    return _REGISTRY.get(source_type)


def list_registered_sources() -> list:
    return sorted(_REGISTRY.keys())


# ─── Universal dispatch handler ────────────────────────────────────


async def handle_intake_event(
    *,
    source_type: str,
    organization_id: str,
    raw: bytes,
    headers: Mapping[str, str],
    secret: Optional[str],
    audit_received_fn: Optional[Callable[[], None]] = None,
    signature_already_verified: bool = False,
) -> Dict[str, Any]:
    """Channel-agnostic dispatch.

    Called by every webhook route. Looks up the registered adapter,
    verifies the signature, parses + enriches + dispatches. Returns
    a status dict suitable for the route's response body (logs +
    debugging, not for end-users).

    ``audit_received_fn`` is the route-side audit-event writer that
    captures the inbound HTTP call (signature pre-verified). Called
    after signature verification so we never audit forged events.

    ``signature_already_verified``: set when the route fans out a
    pre-verified outer envelope into per-entity synthetic payloads
    (QB / Xero). The synthetic payloads aren't signed individually,
    so the adapter's verify_signature would always fail — but the
    outer envelope's signature was already checked at the route
    layer, so it's safe to skip the re-verification here. The
    parameter is explicit (no default-True at the route layer) so
    callers must opt in deliberately rather than getting the
    weakened check by accident.
    """
    adapter = get_adapter(source_type)
    if adapter is None:
        return {"ok": False, "reason": "no_adapter", "source_type": source_type}

    if not signature_already_verified:
        if secret is None or not secret.strip():
            return {"ok": False, "reason": "no_secret_provisioned", "source_type": source_type}
        if not await adapter.verify_signature(raw, headers, secret):
            logger.warning(
                "intake_adapter: signature verification failed for source=%s org=%s (bytes=%d)",
                source_type, organization_id, len(raw),
            )
            return {"ok": False, "reason": "signature_invalid"}

    if audit_received_fn is not None:
        try:
            audit_received_fn()
        except Exception as exc:  # noqa: BLE001
            logger.warning("intake_adapter: audit_received_fn raised — %s", exc)

    try:
        envelope = await adapter.parse_envelope(raw, headers, organization_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "intake_adapter: parse_envelope failed for source=%s — %s",
            source_type, exc,
        )
        return {"ok": False, "reason": "parse_failed", "error": str(exc)}

    if not envelope.event_type:
        return {"ok": True, "reason": "ignored_event_no_type", "source_type": source_type}
    if envelope.event_type not in INTAKE_EVENT_TYPES:
        return {
            "ok": True, "reason": "ignored_unknown_event_type",
            "event_type": envelope.event_type,
        }

    if envelope.event_type in CREATE_LIKE_EVENTS:
        return await _dispatch_create_like(adapter, envelope)
    return await _dispatch_state_update(adapter, envelope)


# ─── Internal dispatch paths ───────────────────────────────────────


async def _dispatch_create_like(
    adapter: IntakeAdapter, envelope: IntakeEnvelope,
) -> Dict[str, Any]:
    db = get_db()
    organization_id = envelope.organization_id
    source_id = envelope.source_id

    existing = db.get_ap_item_by_erp_reference(organization_id, source_id) if source_id else None
    if existing:
        # Replay or out-of-order delivery — fall through to update path
        # so any state drift is reconciled.
        return await _dispatch_state_update(adapter, envelope, existing=existing)

    try:
        invoice = await adapter.enrich(organization_id, envelope)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "intake_adapter: enrich failed for source=%s id=%s — %s",
            envelope.source_type, source_id, exc,
        )
        return {"ok": False, "reason": "enrich_failed", "error": str(exc)}

    # Adapter-signalled skip: enrich returns InvoiceData with
    # ``erp_metadata.not_a_bill`` when the source event isn't an
    # accounts-payable bill (e.g. Xero ACCREC sales invoice arrives
    # via the same INVOICE webhook channel as ACCPAY vendor bills).
    # Short-circuit before pipeline creation rather than minting a
    # phantom AP item.
    erp_meta = invoice.erp_metadata if isinstance(invoice.erp_metadata, dict) else {}
    if erp_meta.get("not_a_bill"):
        return {
            "ok": True,
            "reason": "skipped_non_bill",
            "skip_reason": erp_meta.get("skip_reason"),
            "source_type": envelope.source_type,
            "source_id": source_id,
        }

    try:
        from clearledgr.services.invoice_workflow import get_invoice_workflow
        workflow = get_invoice_workflow(organization_id)
        result = await workflow.process_new_invoice(invoice)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "intake_adapter: process_new_invoice raised for source=%s id=%s — %s",
            envelope.source_type, source_id, exc, exc_info=True,
        )
        return {"ok": False, "reason": "pipeline_failed", "error": str(exc)}

    ap_item_id = _resolve_ap_item_id(db, invoice, result)
    if ap_item_id and source_id:
        try:
            db.update_ap_item(
                ap_item_id,
                erp_reference=source_id,
                _actor_type="erp_webhook",
                _actor_id=envelope.source_type,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "intake_adapter: stamp erp_reference failed ap_item=%s — %s",
                ap_item_id, exc,
            )

    _record_intake_audit(
        organization_id=organization_id,
        ap_item_id=ap_item_id or "",
        envelope=envelope,
        action="created",
        target_state=str(result.get("state") or ""),
    )
    return {
        "ok": True,
        "action": "created",
        "ap_item_id": ap_item_id,
        "state": result.get("state"),
        "pipeline_status": result.get("status"),
        "source_type": envelope.source_type,
        "source_id": source_id,
    }


async def _dispatch_state_update(
    adapter: IntakeAdapter,
    envelope: IntakeEnvelope,
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    db = get_db()
    organization_id = envelope.organization_id
    source_id = envelope.source_id

    if existing is None:
        existing = db.get_ap_item_by_erp_reference(organization_id, source_id) if source_id else None

    if not existing:
        if envelope.event_type in {"paid", "cancelled", "delete"}:
            # Out-of-order: terminal event arrived before we ever saw
            # the create. Synthesize a create from this envelope so the
            # bill at least exists in our records, then return.
            synth_envelope = IntakeEnvelope(
                source_type=envelope.source_type,
                event_type="create",
                source_id=envelope.source_id,
                organization_id=envelope.organization_id,
                raw_payload=envelope.raw_payload,
                event_id=envelope.event_id,
                received_at=envelope.received_at,
                channel_metadata=envelope.channel_metadata,
            )
            return await _dispatch_create_like(adapter, synth_envelope)
        return {"ok": True, "action": "noop_no_box", "source_id": source_id}

    ap_item_id = str(existing.get("id") or "").strip()
    current_state = str(existing.get("state") or "").strip().lower()

    try:
        update = await adapter.derive_state_update(organization_id, envelope)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "intake_adapter: derive_state_update failed for source=%s id=%s — %s",
            envelope.source_type, source_id, exc,
        )
        return {"ok": False, "reason": "derive_failed", "error": str(exc)}

    field_updates: Dict[str, Any] = dict(update.field_updates)
    target_state = update.target_state

    if target_state and target_state != current_state:
        if not validate_transition(current_state, target_state):
            return {
                "ok": False, "reason": "invalid_transition",
                "from": current_state, "to": target_state,
                "ap_item_id": ap_item_id,
            }
        field_updates["state"] = target_state
        field_updates["_actor_type"] = "erp_webhook"
        field_updates["_actor_id"] = envelope.source_type

    if field_updates:
        try:
            db.update_ap_item(ap_item_id, **field_updates)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "intake_adapter: state update failed ap_item=%s — %s",
                ap_item_id, exc,
            )
            return {"ok": False, "reason": "update_failed", "error": str(exc)}

    final_state = str(field_updates.get("state") or current_state)
    _record_intake_audit(
        organization_id=organization_id,
        ap_item_id=ap_item_id,
        envelope=envelope,
        action=envelope.event_type,
        target_state=final_state,
    )
    return {
        "ok": True,
        "action": envelope.event_type,
        "ap_item_id": ap_item_id,
        "state": final_state,
        "fields_updated": [k for k in field_updates.keys() if not k.startswith("_")],
    }


# ─── Helpers ────────────────────────────────────────────────────────


def _resolve_ap_item_id(db: Any, invoice: InvoiceData, result: Dict[str, Any]) -> str:
    candidate = str(result.get("ap_item_id") or "").strip()
    if candidate:
        return candidate
    if hasattr(db, "get_invoice_status"):
        try:
            row = db.get_invoice_status(invoice.gmail_id)
            if row:
                return str(row.get("ap_item_id") or "").strip()
        except Exception:
            pass
    return ""


def _record_intake_audit(
    *,
    organization_id: str,
    ap_item_id: str,
    envelope: IntakeEnvelope,
    action: str,
    target_state: str,
) -> None:
    if not ap_item_id:
        return
    db = get_db()
    if not hasattr(db, "record_audit_event"):
        return
    try:
        db.record_audit_event(
            actor_id=envelope.source_type,
            actor_type="erp_webhook",
            action=f"erp_native_intake.{action}",
            box_id=ap_item_id,
            box_type="ap_item",
            entity_type="ap_item",
            entity_id=ap_item_id,
            organization_id=organization_id,
            metadata={
                "target_state": target_state,
                "event_type": envelope.event_type,
                "event_id": envelope.event_id,
                "source_id": envelope.source_id,
                "source_type": envelope.source_type,
                "channel_metadata": envelope.channel_metadata,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "intake_adapter: audit write failed for %s — %s",
            ap_item_id, exc,
        )
