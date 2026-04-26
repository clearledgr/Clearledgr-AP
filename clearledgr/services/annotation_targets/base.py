"""AnnotationTarget protocol — the channel-agnostic seam (Gap 5).

Every concrete target implements :meth:`apply` to write the
Box-state change to its external surface, and :attr:`target_type`
to identify itself in the registry.

The dispatcher (:class:`AnnotationDispatchObserver`) reads the
per-tenant ``annotation_targets`` policy and enqueues one outbox
row per active target. The outbox handler resolves
``target='annotation:<target_type>'`` to the registered
implementation and calls ``apply``. The annotation-attempts audit
row is written by the handler regardless of whether ``apply``
succeeded or raised.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ─── Canonical types ───────────────────────────────────────────────


@dataclass
class AnnotationContext:
    """Input to a target's ``apply`` method.

    Carries the Box reference + state transition + per-target
    config. Built by :class:`AnnotationDispatchObserver` from the
    inbound :class:`StateTransitionEvent` and the resolved policy
    snapshot.
    """
    organization_id: str
    box_type: str
    box_id: str
    old_state: str
    new_state: str
    actor_id: Optional[str]
    correlation_id: Optional[str]
    source_type: str
    erp_native: bool
    metadata: Dict[str, Any]
    target_config: Dict[str, Any]
    """Per-target settings drawn from ``annotation_targets`` policy
    content. Each target documents its own contract in its module."""


@dataclass
class AnnotationResult:
    """What ``apply`` returns. Persisted to ``annotation_attempts``
    for the business-level audit trail."""
    status: str  # "succeeded" | "skipped" | "failed"
    applied_value: Optional[str] = None
    external_id: Optional[str] = None
    response_code: Optional[int] = None
    response_body_preview: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    skip_reason: Optional[str] = None


# ─── Protocol ──────────────────────────────────────────────────────


@runtime_checkable
class AnnotationTarget(Protocol):
    """Every external surface that wants to reflect Box state
    implements this."""

    target_type: str
    """Canonical identifier matching the key in ``annotation_targets``
    policy content. Examples: ``gmail_label``, ``netsuite_custom_field``,
    ``sap_z_field``, ``customer_webhook``, ``slack_card_update``."""

    async def apply(self, context: AnnotationContext) -> AnnotationResult:
        """Write the Box-state change to the external surface.

        Implementations should return a :class:`AnnotationResult`
        with status="skipped" + skip_reason when the target isn't
        applicable for this transition (e.g. NetSuite target for a
        Gmail-arrived bill that has no ns_internal_id), and raise on
        unexpected failures so the outbox retries.
        """


# ─── Registry ──────────────────────────────────────────────────────


_TARGET_REGISTRY: Dict[str, AnnotationTarget] = {}


def register_target(target: AnnotationTarget) -> None:
    """Register an annotation target at module-import time.
    Idempotent for identical re-registration."""
    existing = _TARGET_REGISTRY.get(target.target_type)
    if existing is not None:
        if existing is target:
            return
        raise ValueError(
            f"Annotation target for type={target.target_type!r} already registered "
            f"({type(existing).__name__}); refusing to overwrite with "
            f"{type(target).__name__}."
        )
    _TARGET_REGISTRY[target.target_type] = target
    logger.info("annotation_target: registered %s", target.target_type)


def get_target(target_type: str) -> Optional[AnnotationTarget]:
    return _TARGET_REGISTRY.get(target_type)


def list_registered_targets() -> List[str]:
    return sorted(_TARGET_REGISTRY.keys())


# ─── Dispatch observer ─────────────────────────────────────────────


class AnnotationDispatchObserver:
    """Replaces the legacy ``GmailLabelObserver``. On every state
    transition, reads the org's ``annotation_targets`` policy and
    enqueues one outbox row per active target. Each target's writer
    runs as an outbox handler (target prefix ``annotation:``) so it
    gets retry + dead-letter + replay for free from Gap 4.

    Why "observer" not just inline dispatch:
    The observer registry is the canonical fan-out seam for state
    transitions. Slot the dispatcher in there alongside other
    observers (audit, override-window, etc.) so the same registration
    + lifecycle path applies. Outbox mode handles the durability.
    """

    def __init__(self, db: Any) -> None:
        self._db = db

    async def on_transition(self, event) -> None:
        # event is a StateTransitionEvent — duck-typed to avoid
        # circular imports.
        from clearledgr.services.outbox import OutboxWriter

        active_targets = _resolve_active_targets(self._db, event.organization_id)
        if not active_targets:
            return

        writer = OutboxWriter(event.organization_id)
        for target_type, target_config in active_targets.items():
            payload = {
                "box_type": "ap_item",
                "box_id": event.ap_item_id,
                "old_state": event.old_state,
                "new_state": event.new_state,
                "actor_id": event.actor_id,
                "correlation_id": event.correlation_id,
                "source_type": getattr(event, "source_type", "gmail"),
                "erp_native": getattr(event, "erp_native", False),
                "metadata": dict(event.metadata or {}),
                "target_config": target_config or {},
                "target_type": target_type,
            }
            dedupe_key = (
                f"annotation:{target_type}:{event.ap_item_id}:"
                f"{event.old_state}->{event.new_state}:"
                f"{event.correlation_id or ''}"
            )
            try:
                writer.enqueue(
                    event_type=f"annotation.{event.new_state}",
                    target=f"annotation:{target_type}",
                    payload=payload,
                    dedupe_key=dedupe_key,
                    actor=event.actor_id or "system",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "annotation_dispatch: enqueue failed for target=%s ap_item=%s — %s",
                    target_type, event.ap_item_id, exc,
                )


def _resolve_active_targets(db: Any, organization_id: str) -> Dict[str, Dict[str, Any]]:
    """Read the org's ``annotation_targets`` policy and return the
    map of ``{target_type: target_config}`` for active targets.

    Inactive / missing targets are excluded. Format:

        {
            "gmail_label": {"enabled": true, "label_set": "finance"},
            "netsuite_custom_field": {"enabled": true, "field_id": "custbody_clearledgr_state"},
            ...
        }
    """
    try:
        from clearledgr.services.policy_service import PolicyService
        service = PolicyService(organization_id)
        version = service.get_active("annotation_targets")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "annotation_dispatch: policy lookup failed for org=%s — %s",
            organization_id, exc,
        )
        return {}
    content = version.content or {}
    active: Dict[str, Dict[str, Any]] = {}
    for target_type, target_config in content.items():
        if not isinstance(target_config, dict):
            continue
        if not target_config.get("enabled", False):
            continue
        active[target_type] = target_config
    return active


# ─── Outbox handler ────────────────────────────────────────────────


async def _outbox_handler_annotation(outbox_event) -> None:
    """Resolve ``target='annotation:<target_type>'`` to the registered
    target instance, build the AnnotationContext, call ``apply``,
    persist the audit row. Raised exceptions trigger the outbox's
    retry/dead-letter logic; ``status='skipped'`` returns persist
    without raising.
    """
    target_str = outbox_event.target
    if not target_str.startswith("annotation:"):
        raise ValueError(f"unexpected target {target_str!r}")
    target_type = target_str.split(":", 1)[1]
    target = get_target(target_type)
    if target is None:
        raise LookupError(
            f"no annotation target registered for {target_type!r} — "
            f"workers must import clearledgr.services.annotation_targets on boot"
        )
    payload = outbox_event.payload or {}
    context = AnnotationContext(
        organization_id=outbox_event.organization_id,
        box_type=str(payload.get("box_type") or "ap_item"),
        box_id=str(payload.get("box_id") or ""),
        old_state=str(payload.get("old_state") or ""),
        new_state=str(payload.get("new_state") or ""),
        actor_id=payload.get("actor_id"),
        correlation_id=payload.get("correlation_id"),
        source_type=str(payload.get("source_type") or "gmail"),
        erp_native=bool(payload.get("erp_native") or False),
        metadata=dict(payload.get("metadata") or {}),
        target_config=dict(payload.get("target_config") or {}),
    )
    result: Optional[AnnotationResult] = None
    error: Optional[Exception] = None
    try:
        result = await target.apply(context)
    except Exception as exc:  # noqa: BLE001
        error = exc

    _persist_annotation_attempt(
        context=context,
        target_type=target_type,
        result=result,
        error=error,
        outbox_event_id=outbox_event.id,
    )
    if error is not None:
        raise error


def _persist_annotation_attempt(
    *,
    context: AnnotationContext,
    target_type: str,
    result: Optional[AnnotationResult],
    error: Optional[Exception],
    outbox_event_id: Optional[str],
) -> None:
    """Insert one row per attempt into ``annotation_attempts``.
    Best-effort — audit failures don't sink the handler."""
    import json
    from clearledgr.core.database import get_db
    db = get_db()
    if not hasattr(db, "connect"):
        return
    db.initialize()
    now = datetime.now(timezone.utc).isoformat()
    if error is not None:
        status = "failed"
        applied_value = None
        external_id = None
        response_code = None
        response_body = str(error)[:500]
        meta = {"error_class": type(error).__name__}
    elif result is None:
        return
    else:
        status = result.status
        applied_value = result.applied_value
        external_id = result.external_id
        response_code = result.response_code
        response_body = (result.response_body_preview or "")[:500]
        meta = result.metadata or {}
        if result.status == "skipped" and result.skip_reason:
            meta = {**meta, "skip_reason": result.skip_reason}

    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO annotation_attempts
                  (id, organization_id, box_type, box_id, target_type,
                   old_state, new_state, applied_value, external_id,
                   status, response_code, response_body_preview,
                   outbox_event_id, attempted_at, metadata_json)
                VALUES
                  (%s, %s, %s, %s, %s,
                   %s, %s, %s, %s,
                   %s, %s, %s,
                   %s, %s, %s)
                """,
                (
                    f"AA-{uuid.uuid4().hex}",
                    context.organization_id,
                    context.box_type, context.box_id, target_type,
                    context.old_state, context.new_state,
                    applied_value, external_id,
                    status, response_code, response_body,
                    outbox_event_id, now, json.dumps(meta),
                ),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "annotation_target: audit row write failed for %s — %s",
            target_type, exc,
        )


def _register_outbox_handler() -> None:
    """One-shot registration of the annotation-prefix handler with
    the outbox."""
    try:
        from clearledgr.services.outbox import register_handler
        register_handler("annotation", _outbox_handler_annotation)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "annotation_target: outbox handler registration failed — %s", exc,
        )


_register_outbox_handler()
