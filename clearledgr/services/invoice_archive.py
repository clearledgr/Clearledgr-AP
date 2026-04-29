"""SOX-immutable original-PDF storage (Wave 1 / A1).

Per AP cycle reference doc Stage 1: "For audit purposes, the original
invoice file must be retained immutably." This module is the durable
archive — content-addressed, append-only at the DB-trigger level,
retention-policy aware.

Storage backend: Postgres BYTEA via the existing connection pool.
Practical AP invoices are <10MB; Postgres handles BYTEA up to 1GB
with no operational concerns at our scale. When the operator wants
S3 (object-lock / compliance mode), swap the ``_persist_bytes`` /
``_fetch_bytes`` implementation here — public API unchanged.

Tamper evidence:
  * Primary key is the SHA-256 of the bytes — same file uploaded
    twice → one row (idempotent dedup, by definition).
  * Postgres triggers REJECT every UPDATE and DELETE (installed by
    ``database._install_audit_append_only_guards``). Mutation of the
    archived bytes is a database error, not an application check.
  * ``retention_until`` is set at INSERT and never changes. The
    reaper service deletes rows older than ``retention_until`` —
    that's the only legitimate path that bypasses the DELETE
    trigger, via a privileged RLS policy granted only to the
    reaper role.

Tenant isolation:
  * Primary key is ``(organization_id, content_hash)`` — same file
    in two tenants gets two distinct rows. No cross-tenant content
    leakage even if the same vendor sends the same PDF to multiple
    customers.
  * Every fetch helper takes ``organization_id`` and rejects on
    mismatch.

Public API:
  * ``archive_pdf`` — store bytes + return content_hash.
  * ``fetch_pdf`` — retrieve bytes by (org, hash). Tenant-scoped.
  * ``list_originals_for_ap_item`` — every archived original tied
    to one AP item.
  * ``get_archive_stats`` — for the workspace health surface.

The intake path in ``gmail_webhooks.process_invoice_email`` calls
``archive_pdf`` after fetching the attachment bytes; the resulting
hash is persisted on the AP item via ``attachment_content_hash``.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# SOX retention default — 7 years, matching IRS/HMRC/most-jurisdiction
# audit retention requirements. Operator can override per-tenant via
# settings_json["retention_years"]; the reaper reads this at run time
# so a customer extending retention doesn't immediately re-purge.
DEFAULT_RETENTION_YEARS = 7

# Hard cap on stored content. PDFs over this are rejected at the
# archive boundary so a malformed multi-GB attachment can't blow up
# our DB. Operator pain at this size is real (no PDF I've seen in
# the wild exceeds 50MB even for 200-line invoices); 25MB is generous.
MAX_CONTENT_BYTES = 25 * 1024 * 1024


@dataclass
class ArchivedOriginal:
    """Public dict-of-record for a stored original."""

    content_hash: str
    organization_id: str
    ap_item_id: Optional[str]
    content_type: str
    filename: Optional[str]
    size_bytes: int
    uploaded_at: str
    uploaded_by: Optional[str]
    retention_until: str
    source: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content_hash": self.content_hash,
            "organization_id": self.organization_id,
            "ap_item_id": self.ap_item_id,
            "content_type": self.content_type,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "uploaded_at": self.uploaded_at,
            "uploaded_by": self.uploaded_by,
            "retention_until": self.retention_until,
            "source": self.source,
        }


class ArchiveError(Exception):
    """Raised when an archive operation cannot complete safely."""


# ─── Public API ─────────────────────────────────────────────────────


def archive_pdf(
    db,
    *,
    organization_id: str,
    content: bytes,
    content_type: str = "application/pdf",
    filename: Optional[str] = None,
    ap_item_id: Optional[str] = None,
    uploaded_by: Optional[str] = None,
    source: str = "gmail_intake",
    retention_years: Optional[int] = None,
) -> ArchivedOriginal:
    """Store the original invoice bytes durably.

    Idempotent on (organization_id, content_hash): re-archiving the
    same bytes returns the existing row, never duplicates. Returns
    the row regardless of whether this call inserted or matched an
    existing one — the caller doesn't care.

    Audit emit: every successful archive (insert or dedupe) writes
    an ``invoice_original_archived`` audit event so the chain is
    reconstructable from logs alone.

    Raises ``ArchiveError`` on any failure — storage hiccups must
    not silently lose a SOX-tracked artifact.
    """
    if not content:
        raise ArchiveError("empty_content")
    if len(content) > MAX_CONTENT_BYTES:
        raise ArchiveError(
            f"content_too_large:{len(content)}>{MAX_CONTENT_BYTES}"
        )
    if not organization_id:
        raise ArchiveError("organization_id_required")

    content_hash = _compute_hash(content)
    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()

    years = retention_years or _resolve_retention_years(db, organization_id)
    retention_until = (now_dt + timedelta(days=365 * int(years))).isoformat()

    # Check for existing row first — the trigger will reject any
    # second INSERT with the same PK, but checking lets us surface a
    # clean dedupe outcome instead of an integrity error.
    existing = _find_existing(db, organization_id, content_hash)
    if existing is not None:
        # Update the linkage if this archive call carries an ap_item_id
        # the existing row doesn't yet have. We DO NOT mutate the
        # archive row itself — instead we link via a side-channel
        # (the AP item carries the hash, not the other way around).
        # Audit emit for the dedupe so the trail is complete.
        _audit_archive(
            db,
            organization_id=organization_id,
            content_hash=content_hash,
            ap_item_id=ap_item_id or existing.ap_item_id,
            uploaded_by=uploaded_by,
            source=source,
            outcome="deduped",
            size_bytes=existing.size_bytes,
        )
        return existing

    sql = (
        "INSERT INTO invoice_originals "
        "(content_hash, organization_id, ap_item_id, content, "
        " content_type, filename, size_bytes, uploaded_at, "
        " uploaded_by, retention_until, source) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
    )
    params = (
        content_hash, organization_id,
        (ap_item_id or None),
        content,
        content_type or "application/octet-stream",
        (filename or None),
        len(content),
        now_iso,
        (uploaded_by or None),
        retention_until,
        source,
    )
    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        # Concurrent insert race: same hash inserted by another
        # worker between our pre-check and our INSERT. Re-fetch and
        # treat as dedupe.
        if _looks_like_pk_collision(exc):
            row = _find_existing(db, organization_id, content_hash)
            if row is not None:
                _audit_archive(
                    db,
                    organization_id=organization_id,
                    content_hash=content_hash,
                    ap_item_id=ap_item_id or row.ap_item_id,
                    uploaded_by=uploaded_by,
                    source=source,
                    outcome="deduped_race",
                    size_bytes=row.size_bytes,
                )
                return row
        logger.error(
            "[invoice_archive] INSERT failed for org=%s hash=%s: %s",
            organization_id, content_hash[:16], exc,
        )
        raise ArchiveError(f"persist_failed:{exc}") from exc

    row = ArchivedOriginal(
        content_hash=content_hash,
        organization_id=organization_id,
        ap_item_id=ap_item_id,
        content_type=content_type,
        filename=filename,
        size_bytes=len(content),
        uploaded_at=now_iso,
        uploaded_by=uploaded_by,
        retention_until=retention_until,
        source=source,
    )
    _audit_archive(
        db,
        organization_id=organization_id,
        content_hash=content_hash,
        ap_item_id=ap_item_id,
        uploaded_by=uploaded_by,
        source=source,
        outcome="inserted",
        size_bytes=len(content),
    )
    return row


def fetch_pdf(
    db,
    *,
    organization_id: str,
    content_hash: str,
) -> Optional[Dict[str, Any]]:
    """Return the archived bytes + metadata. Tenant-scoped.

    ``None`` when the (org, hash) pair doesn't exist — distinguishes
    from "exists in another tenant" (which still returns None to
    avoid existence leaks across tenants).
    """
    if not content_hash or not organization_id:
        return None
    sql = (
        "SELECT content_hash, organization_id, ap_item_id, content, "
        "content_type, filename, size_bytes, uploaded_at, uploaded_by, "
        "retention_until, source "
        "FROM invoice_originals "
        "WHERE organization_id = %s AND content_hash = %s"
    )
    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, content_hash))
            row = cur.fetchone()
    except Exception as exc:
        logger.warning(
            "[invoice_archive] fetch failed for org=%s hash=%s: %s",
            organization_id, content_hash[:16], exc,
        )
        return None
    if row is None:
        return None
    rd = dict(row)
    return {
        "content_hash": rd.get("content_hash"),
        "organization_id": rd.get("organization_id"),
        "ap_item_id": rd.get("ap_item_id"),
        "content": bytes(rd.get("content")) if rd.get("content") is not None else b"",
        "content_type": rd.get("content_type"),
        "filename": rd.get("filename"),
        "size_bytes": int(rd.get("size_bytes") or 0),
        "uploaded_at": rd.get("uploaded_at"),
        "uploaded_by": rd.get("uploaded_by"),
        "retention_until": rd.get("retention_until"),
        "source": rd.get("source"),
    }


def list_originals_for_ap_item(
    db, *, organization_id: str, ap_item_id: str,
) -> List[Dict[str, Any]]:
    """Every archived original linked to one AP item, newest first.

    The link goes through ``ap_items.attachment_content_hash`` (the
    canonical pointer) AND the archive row's own ``ap_item_id``
    (set at INSERT for archives that knew the item id up-front).
    Both paths are unioned so a multi-attachment item still surfaces
    all originals even though only the first hash is on the AP row.
    """
    # Pull the AP item's primary archive hash (the one set at create).
    ap_hash = None
    try:
        item = db.get_ap_item(ap_item_id) if hasattr(db, "get_ap_item") else None
        if item:
            ap_hash = item.get("attachment_content_hash")
    except Exception:
        ap_hash = None

    clauses = ["organization_id = %s"]
    params: List[Any] = [organization_id]
    or_parts = ["ap_item_id = %s"]
    params.append(ap_item_id)
    if ap_hash:
        or_parts.append("content_hash = %s")
        params.append(ap_hash)
    clauses.append("(" + " OR ".join(or_parts) + ")")
    sql = (
        "SELECT content_hash, ap_item_id, content_type, filename, "
        "size_bytes, uploaded_at, uploaded_by, retention_until, source "
        "FROM invoice_originals "
        "WHERE " + " AND ".join(clauses) + " "
        "ORDER BY uploaded_at DESC"
    )
    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
    except Exception as exc:
        logger.warning(
            "[invoice_archive] list failed for org=%s ap_item=%s: %s",
            organization_id, ap_item_id, exc,
        )
        return []
    return [dict(r) for r in rows]


def link_archive_to_ap_item(
    db, *, ap_item_id: str, content_hash: str,
) -> bool:
    """Persist the archive linkage on the AP item.

    Sets ``ap_items.attachment_content_hash`` so the AP item detail
    surface can render a "View original" affordance without a join
    through invoice_originals. Idempotent.
    """
    if not ap_item_id or not content_hash:
        return False
    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE ap_items SET attachment_content_hash = %s "
                "WHERE id = %s",
                (content_hash, ap_item_id),
            )
            conn.commit()
            return (cur.rowcount or 0) > 0
    except Exception as exc:
        logger.warning(
            "[invoice_archive] linkage UPDATE failed for ap_item=%s: %s",
            ap_item_id, exc,
        )
        return False


# ─── Internals ──────────────────────────────────────────────────────


def _compute_hash(content: bytes) -> str:
    """SHA-256 hex digest of the bytes."""
    return hashlib.sha256(content).hexdigest()


def _resolve_retention_years(db, organization_id: str) -> int:
    """Read per-tenant retention setting; fall back to 7 years."""
    try:
        org = db.get_organization(organization_id) or {}
    except Exception:
        return DEFAULT_RETENTION_YEARS
    settings = org.get("settings_json") or org.get("settings") or {}
    if isinstance(settings, str):
        try:
            import json
            settings = json.loads(settings)
        except Exception:
            return DEFAULT_RETENTION_YEARS
    if not isinstance(settings, dict):
        return DEFAULT_RETENTION_YEARS
    raw = settings.get("retention_years")
    try:
        years = int(raw) if raw is not None else DEFAULT_RETENTION_YEARS
    except (TypeError, ValueError):
        return DEFAULT_RETENTION_YEARS
    # Clamp to a sane range — 1 year minimum, 30 maximum. 30 covers
    # the longest jurisdiction-mandated retention I'm aware of (some
    # EU banking regulations).
    return max(1, min(30, years))


def _find_existing(
    db, organization_id: str, content_hash: str,
) -> Optional[ArchivedOriginal]:
    sql = (
        "SELECT content_hash, organization_id, ap_item_id, content_type, "
        "filename, size_bytes, uploaded_at, uploaded_by, retention_until, "
        "source "
        "FROM invoice_originals "
        "WHERE organization_id = %s AND content_hash = %s"
    )
    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, content_hash))
            row = cur.fetchone()
    except Exception:
        return None
    if row is None:
        return None
    rd = dict(row)
    return ArchivedOriginal(
        content_hash=rd["content_hash"],
        organization_id=rd["organization_id"],
        ap_item_id=rd.get("ap_item_id"),
        content_type=rd["content_type"],
        filename=rd.get("filename"),
        size_bytes=int(rd.get("size_bytes") or 0),
        uploaded_at=rd["uploaded_at"],
        uploaded_by=rd.get("uploaded_by"),
        retention_until=rd["retention_until"],
        source=rd.get("source") or "gmail_intake",
    )


def _looks_like_pk_collision(exc: Exception) -> bool:
    """Heuristic for primary-key-collision exception text."""
    msg = str(exc).lower()
    return (
        "duplicate key" in msg
        or "unique constraint" in msg
        or "primary key" in msg
    )


def _audit_archive(
    db,
    *,
    organization_id: str,
    content_hash: str,
    ap_item_id: Optional[str],
    uploaded_by: Optional[str],
    source: str,
    outcome: str,
    size_bytes: int,
) -> None:
    """Audit emit for the archive event. Best-effort — never raises."""
    try:
        db.append_audit_event({
            "event_type": "invoice_original_archived",
            "actor_type": "system",
            "actor_id": uploaded_by or "intake",
            "organization_id": organization_id,
            "box_id": ap_item_id or content_hash,
            "box_type": "ap_item" if ap_item_id else "invoice_original",
            "source": "invoice_archive",
            "payload_json": {
                "content_hash": content_hash,
                "outcome": outcome,
                "size_bytes": size_bytes,
                "archive_source": source,
            },
            "idempotency_key": f"archive:{organization_id}:{content_hash}:{outcome}",
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[invoice_archive] audit emit failed for org=%s hash=%s: %s",
            organization_id, content_hash[:16], exc,
        )
