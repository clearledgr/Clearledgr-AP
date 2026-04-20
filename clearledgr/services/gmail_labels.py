"""Gmail label management for Clearledgr finance workflow.

DESIGN_THESIS.md §6.4 defines a three-level nested label hierarchy:

  Clearledgr/
    Invoice/
      Received        — Email classified, Box created, extraction in progress
      Matched         — 3-way match passed, awaiting approval
      Exception       — Match failed or flagged, requires human resolution
      Approved        — Approved by AP Manager or auto-approved
      Paid            — Payment executed and confirmed by ERP
    Vendor/
      Onboarding      — Email related to an active vendor onboarding engagement
    Finance/
      Credit Note     — Classified as a credit note
      Statement       — Vendor statement of account
      Query           — Vendor payment query or dispute
      Renewal         — Contract renewal notice
    Review Required   — Agent confidence below threshold, needs manual classification
    Not Finance       — Promotional/irrelevant, no action taken

This module owns the canonical label taxonomy, the mapping from AP
state machine states to labels, and backward-compatible migration of
labels from the old flat structure (``Clearledgr/Invoices``, etc.) to
the thesis hierarchy.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import OrderedDict
from typing import Any, Dict, Iterable, Mapping, Optional, Set

from clearledgr.core.utils import safe_int

logger = logging.getLogger(__name__)

# ── Canonical label taxonomy (DESIGN_THESIS.md §6.4) ──
#
# Keys are internal identifiers used everywhere in the codebase.
# Values are the Gmail label display names — three-level nested.
#
# The thesis hierarchy supersedes the old flat structure. The
# LEGACY_LABEL_ALIASES dict maps old names so migration is seamless.
CLEARLEDGR_LABELS = {
    # ── Invoice pipeline stages ──
    "invoice_received":     "Clearledgr/Invoice/Received",
    "invoice_matched":      "Clearledgr/Invoice/Matched",
    "invoice_exception":    "Clearledgr/Invoice/Exception",
    "invoice_approved":     "Clearledgr/Invoice/Approved",
    "invoice_paid":         "Clearledgr/Invoice/Paid",
    # ── Vendor pipeline ──
    "vendor_onboarding":    "Clearledgr/Vendor/Onboarding",
    # ── Finance document types ──
    "finance_credit_note":  "Clearledgr/Finance/Credit Note",
    "finance_statement":    "Clearledgr/Finance/Statement",
    "finance_query":        "Clearledgr/Finance/Query",
    "finance_renewal":      "Clearledgr/Finance/Renewal",
    # ── Classification states ──
    "review_required":      "Clearledgr/Review Required",
    "not_finance":          "Clearledgr/Not Finance",
    # ── Backward-compat aliases: old keys still work via _LABEL_KEY_ALIASES ──
    # These are NOT labels — they map old internal keys to new ones so
    # callers that pass e.g. "invoices" still resolve correctly.
}

# Map old internal keys → canonical new keys so every call site that
# passes the old key continues to work without code changes.
_LABEL_KEY_ALIASES = {
    "processed":        "invoice_received",
    "invoices":         "invoice_received",
    "needs_approval":   "invoice_matched",
    "needs_review":     "review_required",
    "exceptions":       "invoice_exception",
    "approved":         "invoice_approved",
    "posted":           "invoice_paid",
    "rejected":         "invoice_exception",
    "payment_requests": "invoice_received",
    "payments":         "invoice_paid",
    "receipts":         "invoice_received",
    "refunds":          "finance_credit_note",
    "credit_notes":     "finance_credit_note",
    "bank_statements":  "finance_statement",
}


def _resolve_label_key(key: str) -> str:
    """Resolve an old or new label key to the canonical key."""
    k = str(key).strip()
    if k in CLEARLEDGR_LABELS:
        return k
    return _LABEL_KEY_ALIASES.get(k, k)


LEGACY_LABEL_ALIASES = {
    "invoice_received": (
        "Clearledgr/Processed",
        "Clearledgr/Invoices",
        "Clearledgr/Invoice",
        "Clearledgr/Invoices/Matched",
        "Clearledgr/Invoices/Unmatched",
        "Clearledgr/Payment Requests",
        "Clearledgr/Receipts",
    ),
    "invoice_matched": (
        "Clearledgr/Needs Approval",
    ),
    "invoice_exception": (
        "Clearledgr/Exceptions",
        "Clearledgr/Rejected",
    ),
    "invoice_approved": (
        "Clearledgr/Approved",
    ),
    "invoice_paid": (
        "Clearledgr/Posted",
        "Clearledgr/Payments",
        "Clearledgr/Invoices/Posted",
    ),
    "review_required": (
        "Clearledgr/Needs Review",
        "Clearledgr/Pending",
    ),
    "finance_credit_note": (
        "Clearledgr/Credit Notes",
        "Clearledgr/Refunds",
    ),
    "finance_statement": (
        "Clearledgr/Bank Statements",
    ),
}

STALE_LABEL_NAMES = frozenset({
    "Clearledgr/Skipped",
})

LEGACY_LABEL_MIGRATIONS = {
    # Old flat labels → new thesis hierarchy keys
    "Clearledgr/Invoices":            {"invoice_received"},
    "Clearledgr/Invoice":             {"invoice_received"},
    "Clearledgr/Invoices/Matched":    {"invoice_matched"},
    "Clearledgr/Invoices/Unmatched":  {"invoice_received", "review_required"},
    "Clearledgr/Invoices/Posted":     {"invoice_paid"},
    "Clearledgr/Processed":           {"invoice_received"},
    "Clearledgr/Payment Requests":    {"invoice_received"},
    "Clearledgr/Payment Request":     {"invoice_received"},
    "Clearledgr/Payments":            {"invoice_paid"},
    "Clearledgr/Receipts":            {"invoice_received"},
    "Clearledgr/Refunds":             {"finance_credit_note"},
    "Clearledgr/Credit Notes":        {"finance_credit_note"},
    "Clearledgr/Bank Statements":     {"finance_statement"},
    "Clearledgr/Needs Review":        {"review_required"},
    "Clearledgr/Pending":             {"review_required"},
    "Clearledgr/Exceptions":          {"invoice_exception"},
    "Clearledgr/Needs Approval":      {"invoice_matched"},
    "Clearledgr/Approved":            {"invoice_approved"},
    "Clearledgr/Posted":              {"invoice_paid"},
    "Clearledgr/Rejected":            {"invoice_exception"},
    "Clearledgr/Skipped":             set(),
}

# AP state machine → label key mapping (DESIGN_THESIS.md §6.4).
# The invoice label reflects the stage the AP item is in.
AP_STATE_TO_LABEL = {
    "received":         "invoice_received",
    "validated":        "invoice_received",
    "needs_info":       "invoice_exception",
    "needs_approval":   "invoice_matched",
    "pending_approval": "invoice_matched",
    "approved":         "invoice_approved",
    "ready_to_post":    "invoice_approved",
    "posted_to_erp":    "invoice_paid",
    "closed":           "invoice_paid",
    "reversed":         "invoice_exception",
    "failed_post":      "invoice_exception",
    "rejected":         "invoice_exception",
}

# ── Bidirectional label → intent mapping (Phase 2) ──
#
# When a user manually applies one of these labels in Gmail, the agent
# reacts by running the matching intent on the linked AP box. Labels NOT
# in this dict are treated as status-only (e.g. "Matched", "Paid") and
# never trigger an action — only a human decision should approve or
# reject, and users already have the sidebar / Slack for those today.
#
# Scope is deliberately narrow: only decision verbs that a user could
# reasonably want to express by dragging a thread into a Gmail label.
# New entries require product sign-off.
LABEL_TO_INTENT = {
    "Clearledgr/Invoice/Approved":    "approve_invoice",
    "Clearledgr/Invoice/Exception":   "needs_info",
    "Clearledgr/Review Required":     "needs_info",
    "Clearledgr/Not Finance":         "reject_invoice",
}

# Reverse lookup: intent → label-text-for-audit. Used when we want to
# record "vendor applied this label so we moved to that state".
INTENT_FOR_AUDIT = {v: k for k, v in LABEL_TO_INTENT.items()}


def intent_for_label(label_name: str) -> Optional[str]:
    """Return the intent string for a Clearledgr label, or None.

    Case-sensitive against the canonical label display name. Handles
    the case where the caller passes a label_key (``invoice_approved``)
    instead of the display name by resolving through CLEARLEDGR_LABELS.
    """
    if not label_name:
        return None
    # Direct display-name match
    if label_name in LABEL_TO_INTENT:
        return LABEL_TO_INTENT[label_name]
    # Resolve label_key → display name and retry
    display = CLEARLEDGR_LABELS.get(label_name) or CLEARLEDGR_LABELS.get(
        _LABEL_KEY_ALIASES.get(label_name, "")
    )
    if display and display in LABEL_TO_INTENT:
        return LABEL_TO_INTENT[display]
    return None


# Cache label name → id per Gmail identity to avoid repeated list_labels
# calls. Bounded by _LABEL_CACHE_MAX_SCOPES (one entry per Gmail mailbox
# we've seen recently) so a long-running worker that's processed many
# tenants doesn't grow the cache forever. OrderedDict gives us cheap
# LRU semantics: most recently used scope gets move_to_end'd, oldest
# scope drops off the front when we breach the cap.
_LABEL_CACHE_MAX_SCOPES = 128
_label_name_cache: "OrderedDict[str, Dict[str, str]]" = OrderedDict()

# Per-scope asyncio locks serialise ensure_label for the same mailbox.
# Without this, two concurrent events racing to create the same label
# both see a cache miss, both call create_label, and one of the two
# returns a 409-style error that our exception handler silently swallows
# — so the losing message never gets labeled. The lock is per-scope
# (not global) so different mailboxes don't serialise against each
# other.
_label_scope_locks: Dict[str, asyncio.Lock] = {}


def _cache_key(cache_scope: str = "") -> str:
    return str(cache_scope or "default").strip() or "default"


def _scope_lock(cache_scope: str) -> asyncio.Lock:
    key = _cache_key(cache_scope)
    lock = _label_scope_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _label_scope_locks[key] = lock
        # Evict old locks alongside cache entries to keep dict bounded.
        # Lazy cleanup: if we've collected way more locks than scopes we
        # serve, drop the oldest N. (The lock dict isn't LRU so we
        # approximate via insertion order.)
        if len(_label_scope_locks) > _LABEL_CACHE_MAX_SCOPES * 2:
            for stale_key in list(_label_scope_locks.keys())[: _LABEL_CACHE_MAX_SCOPES]:
                _label_scope_locks.pop(stale_key, None)
    return lock


def _cache_get(cache_scope: str) -> Optional[Dict[str, str]]:
    key = _cache_key(cache_scope)
    entry = _label_name_cache.get(key)
    if entry is not None:
        _label_name_cache.move_to_end(key)
    return entry


def _cache_set(cache_scope: str, mapping: Dict[str, str]) -> None:
    key = _cache_key(cache_scope)
    _label_name_cache[key] = mapping
    _label_name_cache.move_to_end(key)
    while len(_label_name_cache) > _LABEL_CACHE_MAX_SCOPES:
        _label_name_cache.popitem(last=False)


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _normalize_record_value(record: Any, key: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(key, default)
    return getattr(record, key, default)


def _parse_metadata(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _normalize_document_type(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "invoice": "invoice",
        "bill": "invoice",
        "payment_request": "payment_request",
        "paymentrequest": "payment_request",
        "payment": "payment",
        "payment_confirmation": "payment",
        "paymentconfirmed": "payment",
        "receipt": "receipt",
        "refund": "refund",
        "credit_note": "credit_note",
        "creditnote": "credit_note",
        "credit_memo": "credit_note",
        "creditmemo": "credit_note",
        "statement": "statement",
        "bank_statement": "statement",
        "card_statement": "statement",
        "credit_card_statement": "statement",
    }
    return aliases.get(raw, raw)


def _subject_document_type_hint(record: Any) -> str:
    subject = str(_normalize_record_value(record, "subject", "") or "").strip().lower()
    if not subject:
        return ""
    if re.search(r"\b(credit note|credit memo)\b", subject):
        return "credit_note"
    if re.search(r"\brefund\b", subject):
        return "refund"
    if re.search(r"\b(payment confirmation|payment received|payment processed|payment successful|payment completed)\b", subject):
        return "payment"
    if re.search(r"\b(receipt|order confirmation)\b", subject):
        return "receipt"
    if re.search(r"\b(bank|card|account)\s+statement\b", subject):
        return "statement"
    if re.search(r"\bpayment request\b", subject):
        return "payment_request"
    if re.search(r"\binvoice\b", subject):
        return "invoice"
    return ""


def _document_label_keys(document_type: str) -> Set[str]:
    """Map a document type to the thesis-hierarchy label key(s)."""
    normalized = _normalize_document_type(document_type)
    if normalized == "invoice":
        return {"invoice_received"}
    if normalized == "payment_request":
        return {"invoice_received"}
    if normalized == "payment":
        return {"invoice_paid"}
    if normalized == "receipt":
        return {"invoice_received"}
    if normalized == "refund":
        return {"finance_credit_note"}
    if normalized == "credit_note":
        return {"finance_credit_note"}
    if normalized == "statement":
        return {"finance_statement"}
    return set()


def _all_managed_label_names() -> Set[str]:
    names = set(CLEARLEDGR_LABELS.values())
    for values in LEGACY_LABEL_ALIASES.values():
        names.update(str(value).strip() for value in values if str(value).strip())
    names.update(STALE_LABEL_NAMES)
    return names


async def _load_label_name_map(client, cache_scope: str = "") -> Dict[str, str]:
    cached = _cache_get(cache_scope)
    if cached is not None:
        return dict(cached)

    labels = await client.list_labels()
    mapping = {
        str(label.get("name") or "").strip(): str(label.get("id") or "").strip()
        for label in (labels or [])
        if str(label.get("name") or "").strip() and str(label.get("id") or "").strip()
    }
    _cache_set(cache_scope, dict(mapping))
    return mapping


def _remember_label(cache_scope: str, label_name: str, label_id: str) -> None:
    if not label_name or not label_id:
        return
    key = _cache_key(cache_scope)
    entry = _label_name_cache.get(key)
    if entry is None:
        entry = {}
        _cache_set(cache_scope, entry)
    entry[label_name] = label_id
    _label_name_cache.move_to_end(key)


def _forget_label(cache_scope: str, label_name: str) -> None:
    cached = _label_name_cache.get(_cache_key(cache_scope))
    if cached is not None:
        cached.pop(label_name, None)


def _label_names_for_key(label_key: str) -> Set[str]:
    resolved = _resolve_label_key(label_key)
    canonical = CLEARLEDGR_LABELS.get(resolved)
    if not canonical:
        return set()
    names = {canonical}
    names.update(LEGACY_LABEL_ALIASES.get(resolved, ()))
    return {str(name).strip() for name in names if str(name).strip()}


async def ensure_label(client, label_key: str, user_email: str = "") -> Optional[str]:
    """Get or create a canonical Clearledgr label and return its Gmail label ID.

    Serialised per mailbox so two concurrent events can't both miss the
    cache and both try to create the same label (which fails on the
    loser and used to silently drop the label from that message).
    """
    resolved = _resolve_label_key(label_key)
    label_name = CLEARLEDGR_LABELS.get(resolved)
    if not label_name:
        return None

    async with _scope_lock(user_email):
        try:
            labels = await _load_label_name_map(client, user_email)
            label_id = labels.get(label_name)
            if label_id:
                return label_id

            try:
                label = await client.create_label(label_name)
            except Exception as create_exc:
                # Gmail returns 409 when the label already exists (e.g.
                # the other racer's label creation committed between our
                # cache read and our create call, or an operator created
                # it manually). Drop the stale cache and re-list once —
                # if the label truly now exists, this recovers it for us
                # without bubbling an error to the caller.
                logger.info(
                    "create_label('%s') raised (%s); reloading label map to recover",
                    label_name,
                    create_exc,
                )
                _forget_label(user_email, label_name)
                _label_name_cache.pop(_cache_key(user_email), None)
                labels = await _load_label_name_map(client, user_email)
                label_id = labels.get(label_name)
                if label_id:
                    return label_id
                raise

            label_id = str((label or {}).get("id") or "").strip() or None
            if label_id:
                _remember_label(user_email, label_name, label_id)
            return label_id
        except Exception as exc:
            logger.warning("Could not ensure label %s: %s", label_name, exc)
            return None


async def apply_label(client, message_id: str, label_key: str, user_email: str = ""):
    """Apply a canonical Clearledgr label to a Gmail message."""
    label_id = await ensure_label(client, label_key, user_email)
    if label_id:
        try:
            await client.add_label(message_id, [label_id])
        except Exception as exc:
            logger.warning("Could not apply label %s to %s: %s", label_key, message_id, exc)


async def remove_label(client, message_id: str, label_key: str, user_email: str = ""):
    """Remove a Clearledgr label and any legacy aliases from a Gmail message.

    Gmail's modify API silently succeeds when the target label isn't
    applied to the message, so we don't have to swallow a 404 for that
    case. Real failures (auth expiry, rate limits, network timeouts)
    used to be silently discarded by a blanket ``except: pass``; now
    each sub-step logs at WARNING with context so monitoring can
    catch repeated failures. Gmail labels are a display layer — we
    don't raise and break the caller's state transition.
    """
    try:
        labels = await _load_label_name_map(client, user_email)
    except Exception as exc:
        logger.warning(
            "Could not load label map to remove %s from %s: %s",
            label_key, message_id, exc,
        )
        return
    label_ids = [
        labels.get(label_name)
        for label_name in _label_names_for_key(label_key)
        if labels.get(label_name)
    ]
    if not label_ids:
        return
    try:
        await client.remove_label(
            message_id, [label_id for label_id in label_ids if label_id],
        )
    except Exception as exc:
        logger.warning(
            "Could not remove label %s from %s: %s",
            label_key, message_id, exc,
        )


async def update_ap_label(client, message_id: str, new_state: str, user_email: str = ""):
    """Update Gmail labels to reflect a new AP state.

    Removes old status labels (needs_approval, approved, posted, rejected)
    and applies the label matching the new state.
    """
    new_label_key = AP_STATE_TO_LABEL.get(new_state)
    if not new_label_key:
        return

    await sync_labels(client, message_id, {"invoice_received", new_label_key}, user_email)
    logger.info("Gmail AP labels synced: %s → %s for message %s", new_state, new_label_key, message_id)


def finance_label_keys(
    *,
    ap_item: Optional[Mapping[str, Any]] = None,
    finance_email: Optional[Any] = None,
    document_type: Optional[str] = None,
) -> Set[str]:
    """Return the canonical Clearledgr label keys for a finance record."""
    keys: Set[str] = {"invoice_received"}

    ap_row = dict(ap_item or {})
    ap_metadata = _parse_metadata(ap_row.get("metadata"))

    finance_meta = _parse_metadata(_normalize_record_value(finance_email, "metadata", {}))
    finance_subject_hint = _subject_document_type_hint(finance_email)
    normalized_document_type = _normalize_document_type(
        document_type
        or finance_subject_hint
        or ap_metadata.get("document_type")
        or finance_meta.get("document_type")
        or ap_metadata.get("email_type")
        or finance_meta.get("email_type")
        or _normalize_record_value(finance_email, "email_type")
    )
    keys.update(_document_label_keys(normalized_document_type))

    state = str(ap_row.get("state") or ap_row.get("status") or "").strip().lower()
    state_label_key = AP_STATE_TO_LABEL.get(state)
    if state_label_key:
        keys.add(state_label_key)

    requires_field_review = _coerce_bool(
        ap_row.get("requires_field_review")
        or ap_metadata.get("requires_field_review")
        or _normalize_record_value(finance_email, "requires_field_review")
        or finance_meta.get("requires_field_review")
    )
    requires_extraction_review = _coerce_bool(
        ap_metadata.get("requires_extraction_review")
        or finance_meta.get("requires_extraction_review")
    )

    confidence_blockers = ap_row.get("confidence_blockers")
    if not isinstance(confidence_blockers, list):
        confidence_blockers = ap_metadata.get("confidence_blockers")
    if not isinstance(confidence_blockers, list):
        confidence_blockers = finance_meta.get("confidence_blockers")
    has_confidence_blockers = bool(confidence_blockers)

    source_conflicts = ap_row.get("source_conflicts")
    if not isinstance(source_conflicts, list):
        source_conflicts = ap_metadata.get("source_conflicts")
    if not isinstance(source_conflicts, list):
        source_conflicts = finance_meta.get("source_conflicts")
    blocking_conflicts = any(
        isinstance(conflict, dict) and _coerce_bool(conflict.get("blocking"))
        for conflict in (source_conflicts or [])
    )

    if requires_field_review or requires_extraction_review or has_confidence_blockers or blocking_conflicts:
        keys.add("review_required")

    exception_code = str(
        ap_row.get("exception_code")
        or ap_metadata.get("exception_code")
        or _normalize_record_value(finance_email, "exception_code")
        or finance_meta.get("exception_code")
        or ""
    ).strip()

    finance_status = str(_normalize_record_value(finance_email, "status") or "").strip().lower()
    if state in {"failed_post", "needs_info"} or exception_code or finance_status in {"error", "failed"}:
        if state != "rejected":
            keys.add("invoice_exception")

    if not state and normalized_document_type == "payment_request" and finance_status not in {"error", "failed", "ignored"}:
        keys.add("invoice_matched")

    return keys


async def sync_labels(
    client,
    message_id: str,
    desired_keys: Iterable[str],
    user_email: str = "",
) -> Set[str]:
    """Synchronize all managed Clearledgr labels on a Gmail message."""
    normalized_keys = {
        _resolve_label_key(str(label_key).strip())
        for label_key in (desired_keys or [])
        if _resolve_label_key(str(label_key).strip()) in CLEARLEDGR_LABELS
    }
    if not normalized_keys:
        return set()

    try:
        desired_ids = []
        for label_key in sorted(normalized_keys):
            label_id = await ensure_label(client, label_key, user_email)
            if label_id:
                desired_ids.append(label_id)

        existing = await _load_label_name_map(client, user_email)
        desired_names = {CLEARLEDGR_LABELS[label_key] for label_key in normalized_keys}
        stale_names = _all_managed_label_names() - desired_names
        stale_ids = [
            existing.get(label_name)
            for label_name in stale_names
            if existing.get(label_name)
        ]

        if desired_ids:
            await client.add_label(message_id, desired_ids)
        if stale_ids:
            await client.remove_label(message_id, [label_id for label_id in stale_ids if label_id])
    except Exception as exc:
        logger.warning("Could not sync Clearledgr labels for %s: %s", message_id, exc)

    return normalized_keys


async def sync_finance_labels(
    client,
    message_id: str,
    *,
    ap_item: Optional[Mapping[str, Any]] = None,
    finance_email: Optional[Any] = None,
    document_type: Optional[str] = None,
    user_email: str = "",
) -> Set[str]:
    """Compute and synchronize the finance labels for a Gmail message."""
    return await sync_labels(
        client,
        message_id,
        finance_label_keys(
            ap_item=ap_item,
            finance_email=finance_email,
            document_type=document_type,
        ),
        user_email,
    )


async def cleanup_legacy_labels(
    client,
    *,
    user_email: str = "",
    dry_run: bool = False,
    max_messages_per_label: int = 1000,
) -> Dict[str, Any]:
    """Migrate message labels off legacy names, then delete obsolete label objects."""
    label_rows = await client.list_labels()
    cache_scope = user_email
    label_map = {
        str(label.get("name") or "").strip(): dict(label)
        for label in (label_rows or [])
        if str(label.get("name") or "").strip()
    }
    _label_name_cache[_cache_key(cache_scope)] = {
        name: str((row or {}).get("id") or "").strip()
        for name, row in label_map.items()
        if str((row or {}).get("id") or "").strip()
    }

    results = []
    total_messages_relabelled = 0
    total_deleted = 0

    for legacy_name, target_keys in LEGACY_LABEL_MIGRATIONS.items():
        label = label_map.get(legacy_name)
        if not label:
            continue

        label_id = str(label.get("id") or "").strip()
        approx_messages_total = safe_int(label.get("messagesTotal"), 0)
        target_keys = {
            _resolve_label_key(str(key).strip())
            for key in (target_keys or set())
            if _resolve_label_key(str(key).strip()) in CLEARLEDGR_LABELS
        }
        target_label_names = [CLEARLEDGR_LABELS[key] for key in sorted(target_keys)]
        target_label_ids = []
        missing_target_labels = []

        if target_keys:
            if dry_run:
                cached_map = await _load_label_name_map(client, cache_scope)
                for key in sorted(target_keys):
                    label_name = CLEARLEDGR_LABELS[key]
                    label_id_value = cached_map.get(label_name)
                    if label_id_value:
                        target_label_ids.append(label_id_value)
                    else:
                        missing_target_labels.append(label_name)
            else:
                for key in sorted(target_keys):
                    label_id_value = await ensure_label(client, key, cache_scope)
                    if label_id_value:
                        target_label_ids.append(label_id_value)
                    else:
                        missing_target_labels.append(CLEARLEDGR_LABELS[key])

        relabelled_messages = 0
        truncated = False
        delete_skipped_reason = None
        page_token = None

        if label_id:
            remaining_budget = max(1, min(safe_int(max_messages_per_label, 1000), 5000))
            while remaining_budget > 0:
                response = await client.list_messages(
                    max_results=min(100, remaining_budget),
                    page_token=page_token,
                    label_ids=[label_id],
                )
                messages = response.get("messages") or []
                if not messages:
                    page_token = None
                    break

                for message in messages:
                    message_id = str(message.get("id") or "").strip()
                    if not message_id:
                        continue
                    if target_label_ids and not dry_run:
                        await client.add_label(message_id, target_label_ids)
                    if target_keys and not missing_target_labels and not dry_run:
                        await client.remove_label(message_id, [label_id])
                    relabelled_messages += 1

                remaining_budget -= len(messages)
                page_token = response.get("nextPageToken")
                if not page_token:
                    break

            truncated = bool(page_token)

        if missing_target_labels:
            delete_skipped_reason = "missing_target_labels"
        elif truncated:
            delete_skipped_reason = "message_limit_reached"
        elif relabelled_messages > 0 and not target_keys:
            delete_skipped_reason = "active_messages_without_migration_target"

        deleted = False
        if not delete_skipped_reason and label_id:
            if dry_run:
                deleted = False
            else:
                await client.delete_label(label_id)
                deleted = True
                total_deleted += 1
                _forget_label(cache_scope, legacy_name)

        total_messages_relabelled += relabelled_messages
        results.append(
            {
                "label_name": legacy_name,
                "label_id": label_id or None,
                "target_labels": target_label_names,
                "approx_messages_total": approx_messages_total,
                "messages_relabelled": relabelled_messages,
                "truncated": truncated,
                "missing_target_labels": missing_target_labels,
                "deleted": deleted,
                "would_delete": bool(not delete_skipped_reason and label_id),
                "delete_skipped_reason": delete_skipped_reason,
            }
        )

    return {
        "status": "completed",
        "dry_run": bool(dry_run),
        "labels_scanned": len(results),
        "labels_deleted": total_deleted,
        "messages_relabelled": total_messages_relabelled,
        "results": results,
    }
