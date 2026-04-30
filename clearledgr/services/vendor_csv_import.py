"""Bulk vendor import via CSV (Module 4 Pass E).

Two-step flow:
  1. ``parse_and_validate(csv_text)`` returns a structured preview
     with per-row validation results + summary counts. No DB writes.
     The dashboard renders this so the operator can fix bad rows
     before committing.
  2. ``commit_rows(db, organization_id, rows, *, actor)`` upserts
     each valid row via ``VendorStore.upsert_vendor_profile`` and
     emits one ``vendor_bulk_imported`` audit event with the
     per-row outcome.

CSV contract (header row required):
  * ``vendor_name`` (required)
  * ``primary_contact_email`` (optional)
  * ``registered_address`` (optional)
  * ``payment_terms`` (optional)
  * ``vat_number`` (optional)
  * ``registration_number`` (optional)
  * ``status`` (optional, defaults to ``active``; one of
    {active, blocked, archived})

Header keys are case-insensitive and tolerant of common variations
(``vendor`` / ``name`` aliasing to ``vendor_name``, ``email`` →
``primary_contact_email``). Unknown columns are silently dropped
rather than rejecting the whole upload — operators paste CSV
exports from many sources and a strict header would be hostile.

Limits:
  * 5 000 rows per upload (cap below the practical XLS export size
    so a misclick doesn't accidentally overwrite a year of data).
  * 1 MB CSV text size (operators wanting larger imports should
    split into chunks).
"""
from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)


MAX_ROWS = 5_000
MAX_CSV_BYTES = 1_000_000

_VALID_STATUSES = {"active", "blocked", "archived"}

# Header aliasing — lowercase the input and look up here.
_COLUMN_ALIASES: Dict[str, str] = {
    "vendor_name": "vendor_name",
    "vendor": "vendor_name",
    "name": "vendor_name",
    "supplier": "vendor_name",
    "supplier_name": "vendor_name",
    "primary_contact_email": "primary_contact_email",
    "email": "primary_contact_email",
    "contact_email": "primary_contact_email",
    "registered_address": "registered_address",
    "address": "registered_address",
    "payment_terms": "payment_terms",
    "terms": "payment_terms",
    "vat_number": "vat_number",
    "vat": "vat_number",
    "registration_number": "registration_number",
    "company_number": "registration_number",
    "status": "status",
}

# RFC 5322 simplification — good enough for boundary validation.
# We don't validate domain MX or anything fancy here; the email
# normaliser elsewhere in the codebase does that. This catches
# malformed CSV-paste typos.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class RowResult:
    """One row's validation + final shape."""

    row_number: int  # 1-indexed (matches the CSV file's row number)
    raw: Dict[str, str]
    parsed: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    valid: bool = True


@dataclass
class ImportPreview:
    """Result of a dry-run parse + validation."""

    total_rows: int
    valid_rows: int
    error_rows: int
    rows: List[RowResult]
    fatal_error: Optional[str] = None  # set when CSV itself is malformed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_rows": self.total_rows,
            "valid_rows": self.valid_rows,
            "error_rows": self.error_rows,
            "fatal_error": self.fatal_error,
            "rows": [asdict(r) for r in self.rows],
        }


def parse_and_validate(csv_text: str) -> ImportPreview:
    """Parse the CSV string and validate every row.

    No DB access — pure function so the SPA can preview without a
    write. Returns an ``ImportPreview`` whose rows carry per-row
    parse output + error messages.
    """
    if not csv_text:
        return ImportPreview(total_rows=0, valid_rows=0, error_rows=0, rows=[])
    if len(csv_text.encode("utf-8")) > MAX_CSV_BYTES:
        return ImportPreview(
            total_rows=0, valid_rows=0, error_rows=0, rows=[],
            fatal_error=f"csv_too_large:max={MAX_CSV_BYTES}_bytes",
        )

    try:
        # Sniff delimiter — operators paste tab-separated data from
        # spreadsheet selections more often than properly-quoted CSV.
        sample = csv_text[:2048]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(csv_text), dialect=dialect)
        raw_headers = reader.fieldnames or []
    except Exception as exc:
        logger.warning("[vendor_csv_import] csv parse failed: %s", exc)
        return ImportPreview(
            total_rows=0, valid_rows=0, error_rows=0, rows=[],
            fatal_error="csv_parse_failed",
        )

    if not raw_headers:
        return ImportPreview(
            total_rows=0, valid_rows=0, error_rows=0, rows=[],
            fatal_error="missing_header_row",
        )

    # Normalise header → canonical column name.
    header_map: Dict[str, str] = {}
    for h in raw_headers:
        if h is None:
            continue
        normalised = h.strip().lower().replace(" ", "_").replace("-", "_")
        canonical = _COLUMN_ALIASES.get(normalised)
        if canonical:
            header_map[h] = canonical

    if "vendor_name" not in header_map.values():
        return ImportPreview(
            total_rows=0, valid_rows=0, error_rows=0, rows=[],
            fatal_error="missing_required_column:vendor_name",
        )

    rows: List[RowResult] = []
    valid_count = 0
    error_count = 0

    # csv.DictReader's row counter — header is row 1, first data row 2.
    for line_index, raw_row in enumerate(reader, start=2):
        if len(rows) >= MAX_ROWS:
            return ImportPreview(
                total_rows=len(rows), valid_rows=valid_count,
                error_rows=error_count, rows=rows,
                fatal_error=f"too_many_rows:max={MAX_ROWS}",
            )

        # Project to canonical keys, dropping unknowns.
        canonical_row: Dict[str, str] = {}
        for raw_key, canonical_key in header_map.items():
            value = raw_row.get(raw_key)
            if value is None:
                continue
            canonical_row[canonical_key] = str(value).strip()

        # Drop fully-empty lines silently (common when operators
        # paste with trailing newlines).
        if not any(canonical_row.values()):
            continue

        result = _validate_row(line_index, canonical_row)
        rows.append(result)
        if result.valid:
            valid_count += 1
        else:
            error_count += 1

    return ImportPreview(
        total_rows=len(rows),
        valid_rows=valid_count,
        error_rows=error_count,
        rows=rows,
    )


def _validate_row(row_number: int, row: Dict[str, str]) -> RowResult:
    """Validate one canonical row, populating .parsed + .errors."""
    result = RowResult(row_number=row_number, raw=dict(row))

    vendor_name = (row.get("vendor_name") or "").strip()
    if not vendor_name:
        result.errors.append("vendor_name_required")
    elif len(vendor_name) > 200:
        result.errors.append("vendor_name_too_long")
    if vendor_name:
        result.parsed["vendor_name"] = vendor_name

    email = (row.get("primary_contact_email") or "").strip().lower()
    if email:
        if not _EMAIL_RE.match(email):
            result.errors.append(f"invalid_email:{email}")
        else:
            result.parsed["primary_contact_email"] = email

    address = (row.get("registered_address") or "").strip()
    if address:
        if len(address) > 500:
            result.errors.append("address_too_long")
        else:
            result.parsed["registered_address"] = address

    terms = (row.get("payment_terms") or "").strip()
    if terms:
        if len(terms) > 100:
            result.errors.append("payment_terms_too_long")
        else:
            result.parsed["payment_terms"] = terms

    for field_name in ("vat_number", "registration_number"):
        value = (row.get(field_name) or "").strip()
        if value:
            if len(value) > 64:
                result.errors.append(f"{field_name}_too_long")
            else:
                result.parsed[field_name] = value

    status = (row.get("status") or "").strip().lower()
    if status:
        if status not in _VALID_STATUSES:
            result.errors.append(f"invalid_status:{status}")
        else:
            result.parsed["status"] = status

    result.valid = len(result.errors) == 0
    return result


def commit_rows(
    db,
    organization_id: str,
    rows: Iterable[RowResult],
    *,
    actor: str,
) -> Dict[str, Any]:
    """Upsert each valid row's profile, skip invalid rows, audit-emit.

    Returns a summary dict with applied_count + skipped_count +
    per-vendor outcomes. Caller is the API endpoint that just
    parsed the upload through ``parse_and_validate``.
    """
    rows = list(rows)
    applied: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for row in rows:
        if not row.valid:
            skipped.append({
                "row_number": row.row_number,
                "errors": row.errors,
            })
            continue
        parsed = dict(row.parsed)
        vendor_name = parsed.pop("vendor_name", None)
        if not vendor_name:
            skipped.append({
                "row_number": row.row_number,
                "errors": ["vendor_name_required"],
            })
            continue
        try:
            db.upsert_vendor_profile(
                organization_id=organization_id,
                vendor_name=vendor_name,
                **parsed,
            )
            applied.append({
                "row_number": row.row_number,
                "vendor_name": vendor_name,
                "fields": sorted(parsed.keys()),
            })
        except Exception as exc:
            logger.warning(
                "[vendor_csv_import] upsert failed for %r: %s", vendor_name, exc,
            )
            skipped.append({
                "row_number": row.row_number,
                "vendor_name": vendor_name,
                "errors": [f"upsert_failed:{exc}"],
            })

    summary = {
        "applied_count": len(applied),
        "skipped_count": len(skipped),
        "applied": applied,
        "skipped": skipped,
    }

    try:
        db.append_audit_event({
            "event_type": "vendor_bulk_imported",
            "actor_type": "user",
            "actor_id": actor,
            "organization_id": organization_id,
            "box_id": organization_id,
            "box_type": "organization",
            "source": "vendor_csv_import",
            "payload_json": {
                "actor": actor,
                "applied_count": summary["applied_count"],
                "skipped_count": summary["skipped_count"],
                # Don't dump the full applied list to audit — it
                # could be 5 000 rows. Just the counts + the
                # vendor_names that landed.
                "applied_vendor_names": [
                    a["vendor_name"] for a in applied[:100]
                ],
            },
        })
    except Exception as exc:
        logger.warning(
            "[vendor_csv_import] audit emit failed: %s", exc,
        )

    return summary
