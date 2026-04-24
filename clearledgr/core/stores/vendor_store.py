"""Vendor intelligence store mixin for ClearledgrDB.

Tracks vendor profiles (patterns, risk signals) and per-vendor invoice history
so the AP reasoning layer can make context-aware decisions.

``VendorStore`` is a mixin — no ``__init__``, expects:
  self.connect(), self._prepare_sql(), self.use_postgres
"""
from __future__ import annotations

import json
import logging
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_TABLE_VENDOR_PROFILES = """
CREATE TABLE IF NOT EXISTS vendor_profiles (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    vendor_name TEXT NOT NULL,
    vendor_aliases TEXT NOT NULL DEFAULT '[]',
    sender_domains TEXT NOT NULL DEFAULT '[]',
    typical_gl_code TEXT,
    requires_po INTEGER NOT NULL DEFAULT 0,
    contract_amount REAL,
    payment_terms TEXT,
    invoice_count INTEGER NOT NULL DEFAULT 0,
    last_invoice_date TEXT,
    last_invoice_amount REAL,
    avg_invoice_amount REAL,
    amount_stddev REAL,
    typical_invoice_day INTEGER,
    bank_details_changed_at TEXT,
    always_approved INTEGER NOT NULL DEFAULT 0,
    approval_override_rate REAL NOT NULL DEFAULT 0.0,
    anomaly_flags TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    -- Phase 2.1.a: Fernet-encrypted bank details (DESIGN_THESIS.md §19).
    -- Never store plaintext IBANs or account numbers in metadata.
    bank_details_encrypted TEXT,
    -- Phase 2.1.b: IBAN change freeze state (DESIGN_THESIS.md §8).
    -- pending_bank_details_encrypted holds the NEW unverified details
    -- that triggered the freeze; the verified bank_details_encrypted
    -- stays untouched until three-factor verification completes.
    pending_bank_details_encrypted TEXT,
    iban_change_pending INTEGER NOT NULL DEFAULT 0,
    iban_change_detected_at TEXT,
    iban_change_verification_state TEXT,
    -- Phase 2.4: KYC fields (DESIGN_THESIS.md §3). First-class columns
    -- so operational queries (stale KYC, missing VAT numbers) are
    -- simple SQL, not JSON scans. director_names stored as a JSON
    -- array so the common "split by comma" ambiguity is avoided.
    -- ytd_spend and risk_score are NOT stored — they're computed at
    -- read time from invoice history and live signals.
    registration_number TEXT,
    vat_number TEXT,
    registered_address TEXT,
    director_names TEXT NOT NULL DEFAULT '[]',
    kyc_completion_date TEXT,
    vendor_kyc_updated_at TEXT,
    primary_contact_email TEXT,
    UNIQUE(organization_id, vendor_name)
)
"""

_TABLE_VENDOR_INVOICE_HISTORY = """
CREATE TABLE IF NOT EXISTS vendor_invoice_history (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    vendor_name TEXT NOT NULL,
    ap_item_id TEXT NOT NULL,
    invoice_number TEXT,
    invoice_date TEXT,
    amount REAL,
    currency TEXT NOT NULL DEFAULT 'USD',
    final_state TEXT,
    exception_code TEXT,
    was_approved INTEGER NOT NULL DEFAULT 0,
    approval_override INTEGER NOT NULL DEFAULT 0,
    agent_recommendation TEXT,
    human_decision TEXT,
    created_at TEXT NOT NULL
)
"""

_TABLE_VENDOR_DECISION_FEEDBACK = """
CREATE TABLE IF NOT EXISTS vendor_decision_feedback (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    vendor_name TEXT NOT NULL,
    ap_item_id TEXT,
    human_decision TEXT NOT NULL,
    agent_recommendation TEXT,
    decision_override INTEGER NOT NULL DEFAULT 0,
    reason TEXT,
    source_channel TEXT,
    actor_id TEXT,
    correlation_id TEXT,
    action_outcome TEXT,
    created_at TEXT NOT NULL
)
"""

# Phase 3.1.a: vendor onboarding session state (DESIGN_THESIS.md §9).
# A session is the temporal workflow record that drives a single vendor
# from invited → active. The vendor's durable identity lives in
# vendor_profiles; this table carries the workflow state machine.
#
# is_active distinguishes the currently-running session from historical
# ones — a vendor can have many sessions over time (re-onboarding),
# but at most one active session at any moment. The state column is
# enforced by the VendorOnboardingState machine in the
# clearledgr.core.vendor_onboarding_states module — never write it
# directly, always go through transition_onboarding_session_state.
_TABLE_VENDOR_ONBOARDING_SESSIONS = """
CREATE TABLE IF NOT EXISTS vendor_onboarding_sessions (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    vendor_name TEXT NOT NULL,
    state TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    invited_at TEXT NOT NULL,
    invited_by TEXT NOT NULL,
    last_activity_at TEXT NOT NULL,
    last_chase_at TEXT,
    chase_count INTEGER NOT NULL DEFAULT 0,
    kyc_submitted_at TEXT,
    bank_submitted_at TEXT,
    microdeposit_initiated_at TEXT,
    microdeposit_initiated_by TEXT,
    bank_verified_at TEXT,
    erp_activated_at TEXT,
    erp_vendor_id TEXT,
    completed_at TEXT,
    escalated_at TEXT,
    escalated_reason TEXT,
    rejected_at TEXT,
    rejected_by TEXT,
    rejection_reason TEXT,
    abandoned_at TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(v: Any) -> Any:
    if isinstance(v, (list, dict)):
        return v
    if isinstance(v, str) and v.strip():
        try:
            return json.loads(v)
        except Exception:
            pass
    return None


class VendorStore:
    """Mixin providing vendor intelligence persistence methods."""

    # ------------------------------------------------------------------ #
    # Schema SQL (consumed by database.py initialize())                   #
    # ------------------------------------------------------------------ #

    VENDOR_PROFILE_TABLE_SQL = _TABLE_VENDOR_PROFILES
    VENDOR_INVOICE_HISTORY_TABLE_SQL = _TABLE_VENDOR_INVOICE_HISTORY
    VENDOR_DECISION_FEEDBACK_TABLE_SQL = _TABLE_VENDOR_DECISION_FEEDBACK
    VENDOR_ONBOARDING_SESSIONS_TABLE_SQL = _TABLE_VENDOR_ONBOARDING_SESSIONS

    # ------------------------------------------------------------------ #
    # vendor_profiles                                                      #
    # ------------------------------------------------------------------ #

    def get_vendor_profile(
        self, organization_id: str, vendor_name: str
    ) -> Optional[Dict[str, Any]]:
        """Return the vendor profile dict or None if not seen before."""
        sql = self._prepare_sql(
            "SELECT * FROM vendor_profiles WHERE organization_id = ? AND vendor_name = ?"
        )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (organization_id, vendor_name))
                row = cur.fetchone()
                if row is None:
                    return None
                parsed = dict(row)
                for key, default in (
                    ("vendor_aliases", []),
                    ("sender_domains", []),
                    ("anomaly_flags", []),
                    ("metadata", {}),
                    ("iban_change_verification_state", None),
                    ("director_names", []),
                ):
                    decoded = _loads(parsed.get(key))
                    parsed[key] = decoded if decoded is not None else default
                return parsed
        except Exception as exc:
            logger.warning("[VendorStore] get_vendor_profile failed: %s", exc)
            return None

    def get_vendor_profiles_bulk(
        self,
        organization_id: str,
        vendor_names: List[str],
    ) -> Dict[str, Dict[str, Any]]:
        """Return vendor profiles keyed by canonical vendor name."""
        normalized_names = [
            str(name or "").strip()
            for name in (vendor_names or [])
            if str(name or "").strip()
        ]
        if not normalized_names:
            return {}

        placeholders = ", ".join("?" for _ in normalized_names)
        sql = self._prepare_sql(
            "SELECT * FROM vendor_profiles "
            f"WHERE organization_id = ? AND vendor_name IN ({placeholders})"
        )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (organization_id, *normalized_names))
                rows = cur.fetchall()
        except Exception as exc:
            logger.warning("[VendorStore] get_vendor_profiles_bulk failed: %s", exc)
            return {}

        profiles: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            parsed = dict(row)
            for key, default in (
                ("vendor_aliases", []),
                ("sender_domains", []),
                ("anomaly_flags", []),
                ("metadata", {}),
            ):
                decoded = _loads(parsed.get(key))
                parsed[key] = decoded if decoded is not None else default
            profiles[str(parsed.get("vendor_name") or "").strip()] = parsed
        return profiles

    def upsert_vendor_profile(
        self,
        organization_id: str,
        vendor_name: str,
        **fields: Any,
    ) -> Dict[str, Any]:
        """Create or update a vendor profile with the given fields.

        Only whitelisted fields are written to prevent injection.
        Returns the updated profile dict.
        """
        _ALLOWED = {
            "vendor_aliases", "sender_domains", "typical_gl_code",
            "requires_po", "contract_amount", "payment_terms",
            "invoice_count", "last_invoice_date", "last_invoice_amount",
            "avg_invoice_amount", "amount_stddev", "typical_invoice_day",
            "bank_details_changed_at", "always_approved",
            "approval_override_rate", "anomaly_flags", "metadata",
            # Phase 2.1.a: Fernet ciphertext column. Direct callers must
            # pass the already-encrypted value; the typed accessors below
            # (set_vendor_bank_details / get_vendor_bank_details) handle
            # encryption + decryption + masking correctly.
            "bank_details_encrypted",
            # Phase 2.1.b: IBAN change freeze state. Use
            # start_iban_change_freeze / complete_iban_change_freeze /
            # reject_iban_change_freeze for typed access.
            "pending_bank_details_encrypted",
            "iban_change_pending",
            "iban_change_detected_at",
            "iban_change_verification_state",
            # Phase 2.4: KYC fields. Writes should go through
            # update_vendor_kyc which handles validation + audit.
            "registration_number",
            "vat_number",
            "registered_address",
            "director_names",
            "kyc_completion_date",
            "vendor_kyc_updated_at",
            # §3: primary AP contact email on the Vendor record.
            "primary_contact_email",
        }
        safe_fields = {k: v for k, v in fields.items() if k in _ALLOWED}

        # JSON-encode list/dict values
        for key in (
            "vendor_aliases",
            "sender_domains",
            "anomaly_flags",
            "metadata",
            "iban_change_verification_state",
            "director_names",
        ):
            if key in safe_fields and isinstance(safe_fields[key], (list, dict)):
                safe_fields[key] = json.dumps(safe_fields[key])

        # Several boolean-semantic columns are typed INTEGER (legacy
        # SQLite-era schema). Postgres rejects bool→int implicit
        # coercion ("integer ... is of type boolean"), so coerce here.
        for key, value in list(safe_fields.items()):
            if isinstance(value, bool):
                safe_fields[key] = int(value)

        now = _now()
        existing = self.get_vendor_profile(organization_id, vendor_name)

        if existing is None:
            row_id = str(uuid.uuid4())
            cols = ["id", "organization_id", "vendor_name", "created_at", "updated_at"] + list(safe_fields.keys())
            vals = [row_id, organization_id, vendor_name, now, now] + list(safe_fields.values())
            placeholders = ", ".join(["?"] * len(cols))
            sql = self._prepare_sql(
                f"INSERT INTO vendor_profiles ({', '.join(cols)}) VALUES ({placeholders})"
            )
            try:
                with self.connect() as conn:
                    conn.execute(sql, vals)
                    conn.commit()
            except Exception as exc:
                logger.warning("[VendorStore] upsert insert failed: %s", exc)
        else:
            if not safe_fields:
                return existing
            set_clause = ", ".join(f"{k} = ?" for k in safe_fields)
            vals = list(safe_fields.values()) + [now, organization_id, vendor_name]
            sql = self._prepare_sql(
                f"UPDATE vendor_profiles SET {set_clause}, updated_at = ? "
                f"WHERE organization_id = ? AND vendor_name = ?"
            )
            try:
                with self.connect() as conn:
                    conn.execute(sql, vals)
                    conn.commit()
            except Exception as exc:
                logger.warning("[VendorStore] upsert update failed: %s", exc)

        return self.get_vendor_profile(organization_id, vendor_name) or {}

    # ------------------------------------------------------------------ #
    # Phase 2.1.a: Bank-details typed accessors                            #
    # ------------------------------------------------------------------ #

    def get_vendor_bank_details(
        self, organization_id: str, vendor_name: str
    ) -> Optional[Dict[str, str]]:
        """Decrypt and return the bank-details dict for a vendor.

        Returns the canonical normalized shape or ``None`` when no bank
        details are stored. Caller is responsible for masking before
        returning to user-facing surfaces.
        """
        from clearledgr.core.stores.bank_details import decrypt_bank_details

        sql = self._prepare_sql(
            "SELECT bank_details_encrypted FROM vendor_profiles "
            "WHERE organization_id = ? AND vendor_name = ?"
        )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (organization_id, vendor_name))
                row = cur.fetchone()
        except Exception as exc:
            logger.warning("[VendorStore] get_vendor_bank_details failed: %s", exc)
            return None
        if not row:
            return None
        ciphertext = (
            row["bank_details_encrypted"]
            if hasattr(row, "keys")
            else (row[0] if row else None)
        )
        return decrypt_bank_details(ciphertext, decrypt_fn=self._decrypt_secret)

    def get_vendor_bank_details_masked(
        self, organization_id: str, vendor_name: str
    ) -> Optional[Dict[str, str]]:
        """Return the masked-for-display bank-details dict for a vendor.

        API-safe accessor — output is what every outbound API surface
        should return when surfacing vendor bank details.
        """
        from clearledgr.core.stores.bank_details import mask_bank_details

        plaintext = self.get_vendor_bank_details(organization_id, vendor_name)
        return mask_bank_details(plaintext)

    def set_vendor_bank_details(
        self,
        organization_id: str,
        vendor_name: str,
        bank_details: Optional[Dict[str, Any]],
        *,
        actor_id: Optional[str] = None,
    ) -> bool:
        """Encrypt + persist bank details on a vendor profile.

        Pass ``None`` to clear the column. Also bumps the vendor's
        ``bank_details_changed_at`` timestamp via ``upsert_vendor_profile``
        so the validation gate's bank-details-mismatch check has a
        signal even when reading just the timestamp without decrypting.
        """
        from clearledgr.core.stores.bank_details import (
            encrypt_bank_details,
            normalize_bank_details,
        )

        cleaned = normalize_bank_details(bank_details)
        if cleaned is None:
            ciphertext = None
        else:
            try:
                ciphertext = encrypt_bank_details(
                    cleaned, encrypt_fn=self._encrypt_secret
                )
            except Exception as exc:
                logger.error(
                    "set_vendor_bank_details encryption failed for %s/%s: %s",
                    organization_id, vendor_name, exc,
                )
                return False
        now = _now()
        try:
            self.upsert_vendor_profile(
                organization_id,
                vendor_name,
                bank_details_encrypted=ciphertext,
                bank_details_changed_at=now,
            )
            return True
        except Exception as exc:
            logger.warning(
                "[VendorStore] set_vendor_bank_details upsert failed: %s", exc
            )
            return False

    # ------------------------------------------------------------------ #
    # Phase 2.2: Vendor domain lock — trusted sender-domain allowlist    #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _normalize_domain(value: Any) -> str:
        """Lowercase + strip a domain string. Returns empty string on None."""
        if value is None:
            return ""
        return str(value).strip().strip(">").strip("<").strip().lower()

    def get_trusted_sender_domains(
        self, organization_id: str, vendor_name: str
    ) -> List[str]:
        """Return the vendor's list of trusted sender domains (normalized).

        Returns an empty list if the vendor does not exist or has no
        domains recorded yet. Duplicates and empty strings are removed.
        """
        profile = self.get_vendor_profile(organization_id, vendor_name)
        if not profile:
            return []
        raw = profile.get("sender_domains") or []
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = []
        if not isinstance(raw, list):
            return []
        seen: set = set()
        normalized: List[str] = []
        for entry in raw:
            domain = self._normalize_domain(entry)
            if not domain or domain in seen:
                continue
            seen.add(domain)
            normalized.append(domain)
        return normalized

    def add_trusted_sender_domain(
        self,
        organization_id: str,
        vendor_name: str,
        domain: str,
        *,
        actor_id: Optional[str] = None,
    ) -> bool:
        """Append ``domain`` to the vendor's trusted sender-domain allowlist.

        No-op when the domain is already present. Returns True on a
        successful write (including the no-op case where the domain
        already exists — the caller can distinguish "added" from
        "already present" by diffing the returned list vs. the
        pre-call list).
        """
        normalized = self._normalize_domain(domain)
        if not normalized:
            return False

        existing = self.get_trusted_sender_domains(organization_id, vendor_name)
        if normalized in existing:
            return True

        new_domains = list(existing) + [normalized]
        try:
            self.upsert_vendor_profile(
                organization_id,
                vendor_name,
                sender_domains=new_domains,
            )
            return True
        except Exception as exc:
            logger.warning(
                "[VendorStore] add_trusted_sender_domain upsert failed: %s", exc
            )
            return False

    def remove_trusted_sender_domain(
        self,
        organization_id: str,
        vendor_name: str,
        domain: str,
    ) -> bool:
        """Remove ``domain`` from the vendor's trusted sender-domain allowlist.

        No-op when the domain is not present. Returns True if the
        domain was removed, False if it wasn't found or the write
        failed. Callers that need to distinguish "not found" from
        "write failed" should check ``get_trusted_sender_domains``
        before and after.
        """
        normalized = self._normalize_domain(domain)
        if not normalized:
            return False

        existing = self.get_trusted_sender_domains(organization_id, vendor_name)
        if normalized not in existing:
            return False

        new_domains = [d for d in existing if d != normalized]
        try:
            self.upsert_vendor_profile(
                organization_id,
                vendor_name,
                sender_domains=new_domains,
            )
            return True
        except Exception as exc:
            logger.warning(
                "[VendorStore] remove_trusted_sender_domain upsert failed: %s", exc
            )
            return False

    def ensure_trusted_sender_domain_tracked(
        self,
        organization_id: str,
        vendor_name: str,
        domain: str,
    ) -> bool:
        """Observer-friendly helper: record ``domain`` on first sighting.

        If the vendor has no known sender domains yet, adds ``domain``.
        Otherwise no-op. Returns True only when a new domain was
        actually recorded. Used by the VendorDomainTrackingObserver to
        auto-populate the allowlist from the first successful post
        (which must have passed the first-payment-hold human review,
        so the domain is trusted at the point the observer fires).
        """
        normalized = self._normalize_domain(domain)
        if not normalized:
            return False
        existing = self.get_trusted_sender_domains(organization_id, vendor_name)
        if existing:
            return False
        return self.add_trusted_sender_domain(
            organization_id, vendor_name, normalized
        )

    # ------------------------------------------------------------------ #
    # Phase 2.1.b: IBAN change freeze state                                #
    # ------------------------------------------------------------------ #

    def is_iban_change_pending(
        self, organization_id: str, vendor_name: str
    ) -> bool:
        """Return True iff the vendor is currently under an IBAN freeze."""
        profile = self.get_vendor_profile(organization_id, vendor_name)
        if not profile:
            return False
        return bool(profile.get("iban_change_pending"))

    def get_iban_change_verification_state(
        self, organization_id: str, vendor_name: str
    ) -> Optional[Dict[str, Any]]:
        """Return the JSON-decoded verification state dict (or None)."""
        profile = self.get_vendor_profile(organization_id, vendor_name)
        if not profile:
            return None
        state = profile.get("iban_change_verification_state")
        if isinstance(state, dict):
            return state
        return None

    def get_pending_bank_details(
        self, organization_id: str, vendor_name: str
    ) -> Optional[Dict[str, str]]:
        """Decrypt and return the pending (unverified) bank details."""
        from clearledgr.core.stores.bank_details import decrypt_bank_details

        sql = self._prepare_sql(
            "SELECT pending_bank_details_encrypted FROM vendor_profiles "
            "WHERE organization_id = ? AND vendor_name = ?"
        )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (organization_id, vendor_name))
                row = cur.fetchone()
        except Exception as exc:
            logger.warning("[VendorStore] get_pending_bank_details failed: %s", exc)
            return None
        if not row:
            return None
        ciphertext = (
            row["pending_bank_details_encrypted"]
            if hasattr(row, "keys")
            else (row[0] if row else None)
        )
        return decrypt_bank_details(ciphertext, decrypt_fn=self._decrypt_secret)

    def get_pending_bank_details_masked(
        self, organization_id: str, vendor_name: str
    ) -> Optional[Dict[str, str]]:
        """Return the masked-for-display pending bank details dict."""
        from clearledgr.core.stores.bank_details import mask_bank_details

        plaintext = self.get_pending_bank_details(organization_id, vendor_name)
        return mask_bank_details(plaintext)

    def start_iban_change_freeze(
        self,
        organization_id: str,
        vendor_name: str,
        *,
        pending_bank_details: Dict[str, Any],
        sender_domain: str,
    ) -> Optional[Dict[str, Any]]:
        """Freeze the vendor and record the new unverified bank details.

        Initializes the verification state dict:
          - email_domain_factor auto-passes iff ``sender_domain`` is in
            the vendor's known ``sender_domains`` list. Otherwise the
            factor is auto-failed and the verifier must manually
            override or reject.
          - phone_factor starts unverified
          - sign_off_factor starts unverified

        Returns the updated verification state dict, or None on failure.
        Callers that need to know whether the freeze is new vs. a
        no-op (vendor already frozen) should check
        ``is_iban_change_pending`` first — this method is idempotent
        and will NOT overwrite an existing freeze.
        """
        from clearledgr.core.stores.bank_details import (
            encrypt_bank_details,
            normalize_bank_details,
        )

        profile = self.get_vendor_profile(organization_id, vendor_name)
        if not profile:
            logger.warning(
                "[VendorStore] Cannot freeze unknown vendor %s/%s",
                organization_id, vendor_name,
            )
            return None

        # Idempotent: if already frozen, return current state unchanged
        if profile.get("iban_change_pending"):
            existing = profile.get("iban_change_verification_state")
            return existing if isinstance(existing, dict) else None

        cleaned = normalize_bank_details(pending_bank_details)
        if not cleaned:
            logger.warning(
                "[VendorStore] start_iban_change_freeze called with empty bank details"
            )
            return None

        try:
            ciphertext = encrypt_bank_details(
                cleaned, encrypt_fn=self._encrypt_secret
            )
        except Exception as exc:
            logger.error(
                "[VendorStore] pending bank details encryption failed: %s", exc
            )
            return None

        # Auto-check email-domain factor against known sender_domains
        known_domains = profile.get("sender_domains") or []
        if isinstance(known_domains, str):
            try:
                known_domains = json.loads(known_domains)
            except json.JSONDecodeError:
                known_domains = []
        normalized_sender = str(sender_domain or "").strip().lower()
        normalized_known = [
            str(d or "").strip().lower() for d in known_domains if d
        ]
        domain_matches = bool(
            normalized_sender and normalized_sender in normalized_known
        )
        now = _now()
        verification_state = {
            "email_domain_factor": {
                "verified": domain_matches,
                "sender_domain": normalized_sender,
                "matched_known_domain": domain_matches,
                "recorded_at": now,
            },
            "phone_factor": {
                "verified": False,
                "verified_phone_number": None,
                "caller_name_at_vendor": None,
                "verified_by": None,
                "verified_at": None,
                "notes": None,
            },
            "sign_off_factor": {
                "verified": False,
                "verified_by": None,
                "verified_at": None,
            },
        }

        try:
            self.upsert_vendor_profile(
                organization_id,
                vendor_name,
                pending_bank_details_encrypted=ciphertext,
                iban_change_pending=1,
                iban_change_detected_at=now,
                iban_change_verification_state=verification_state,
            )
        except Exception as exc:
            logger.error(
                "[VendorStore] start_iban_change_freeze upsert failed: %s", exc
            )
            return None
        return verification_state

    def record_iban_change_factor(
        self,
        organization_id: str,
        vendor_name: str,
        *,
        factor: str,
        payload: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Record a single verification factor on an active freeze.

        ``factor`` must be one of:
          - ``"email_domain_factor"``
          - ``"phone_factor"``
          - ``"sign_off_factor"``

        Merges ``payload`` into the existing factor sub-dict and sets
        ``verified=True`` if the payload does not explicitly pass
        ``verified=False``. Returns the full updated verification state
        dict, or None if the vendor isn't frozen or the factor name is
        unknown.
        """
        _ALLOWED_FACTORS = {
            "email_domain_factor",
            "phone_factor",
            "sign_off_factor",
        }
        if factor not in _ALLOWED_FACTORS:
            return None

        profile = self.get_vendor_profile(organization_id, vendor_name)
        if not profile or not profile.get("iban_change_pending"):
            return None

        state = profile.get("iban_change_verification_state")
        if not isinstance(state, dict):
            state = {}
        current_factor = dict(state.get(factor) or {})
        current_factor.update(payload or {})
        # Default verified=True unless the caller explicitly set it
        if "verified" not in (payload or {}):
            current_factor["verified"] = True
        state[factor] = current_factor

        try:
            self.upsert_vendor_profile(
                organization_id,
                vendor_name,
                iban_change_verification_state=state,
            )
        except Exception as exc:
            logger.error(
                "[VendorStore] record_iban_change_factor upsert failed: %s", exc
            )
            return None
        return state

    def complete_iban_change_freeze(
        self,
        organization_id: str,
        vendor_name: str,
    ) -> bool:
        """Lift the freeze: promote pending bank details to verified.

        Caller MUST have checked all three factors are verified before
        calling this. The service layer enforces that check; direct
        store callers are trusted.

        Copies ``pending_bank_details_encrypted`` to
        ``bank_details_encrypted``, clears the pending column, clears
        the freeze flag + detected_at + verification_state, and bumps
        ``bank_details_changed_at`` to now for audit.
        """
        profile = self.get_vendor_profile(organization_id, vendor_name)
        if not profile or not profile.get("iban_change_pending"):
            return False

        # Read the pending ciphertext directly so we don't decrypt +
        # re-encrypt unnecessarily.
        sql = self._prepare_sql(
            "SELECT pending_bank_details_encrypted FROM vendor_profiles "
            "WHERE organization_id = ? AND vendor_name = ?"
        )
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, vendor_name))
            row = cur.fetchone()
        if not row:
            return False
        pending_ct = (
            row["pending_bank_details_encrypted"]
            if hasattr(row, "keys")
            else (row[0] if row else None)
        )
        if not pending_ct:
            return False

        now = _now()
        try:
            self.upsert_vendor_profile(
                organization_id,
                vendor_name,
                bank_details_encrypted=pending_ct,
                bank_details_changed_at=now,
                pending_bank_details_encrypted=None,
                iban_change_pending=0,
                iban_change_detected_at=None,
                iban_change_verification_state=None,
            )
            return True
        except Exception as exc:
            logger.error(
                "[VendorStore] complete_iban_change_freeze upsert failed: %s", exc
            )
            return False

    def reject_iban_change_freeze(
        self, organization_id: str, vendor_name: str
    ) -> bool:
        """Reject the unverified change and clear the freeze.

        The pending bank details are discarded; the verified
        ``bank_details_encrypted`` column keeps its old value. Every
        invoice that presented the rejected details should be
        escalated to human review via the normal gate path (the new
        details never reach the verified column, so future invoices
        with the old details will pass).
        """
        profile = self.get_vendor_profile(organization_id, vendor_name)
        if not profile or not profile.get("iban_change_pending"):
            return False
        try:
            self.upsert_vendor_profile(
                organization_id,
                vendor_name,
                pending_bank_details_encrypted=None,
                iban_change_pending=0,
                iban_change_detected_at=None,
                iban_change_verification_state=None,
            )
            return True
        except Exception as exc:
            logger.error(
                "[VendorStore] reject_iban_change_freeze upsert failed: %s", exc
            )
            return False

    # ------------------------------------------------------------------ #
    # Phase 2.4: Vendor KYC typed accessors + computed fields              #
    # ------------------------------------------------------------------ #

    # Whitelisted KYC field set. Keep in sync with migration v16.
    _KYC_FIELD_NAMES = (
        "registration_number",
        "vat_number",
        "registered_address",
        "director_names",
        "kyc_completion_date",
    )

    def get_vendor_kyc(
        self, organization_id: str, vendor_name: str
    ) -> Optional[Dict[str, Any]]:
        """Return the KYC sub-dict for a vendor (or None if vendor absent).

        Shape:
            {
                "registration_number": str | None,
                "vat_number": str | None,
                "registered_address": str | None,
                "director_names": List[str],
                "kyc_completion_date": str | None,
                "vendor_kyc_updated_at": str | None,
            }
        """
        profile = self.get_vendor_profile(organization_id, vendor_name)
        if not profile:
            return None
        return {
            "registration_number": profile.get("registration_number"),
            "vat_number": profile.get("vat_number"),
            "registered_address": profile.get("registered_address"),
            "director_names": profile.get("director_names") or [],
            "kyc_completion_date": profile.get("kyc_completion_date"),
            "vendor_kyc_updated_at": profile.get("vendor_kyc_updated_at"),
        }

    def update_vendor_kyc(
        self,
        organization_id: str,
        vendor_name: str,
        *,
        patch: Dict[str, Any],
        actor_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Apply a partial KYC patch to a vendor profile.

        Only fields in ``_KYC_FIELD_NAMES`` are accepted. The
        ``director_names`` field is validated to be a list of non-empty
        strings if provided. Bumps ``vendor_kyc_updated_at`` to now on
        every successful write. Returns the updated KYC sub-dict on
        success, None if the vendor doesn't exist or the patch was
        entirely empty after filtering.

        This method does NOT emit an audit event — that's the service
        layer's responsibility so the caller can supply ``actor_id``
        from the authenticated user context.
        """
        if not patch:
            return None
        profile = self.get_vendor_profile(organization_id, vendor_name)
        if not profile:
            return None

        cleaned: Dict[str, Any] = {}
        for field in self._KYC_FIELD_NAMES:
            if field not in patch:
                continue
            value = patch[field]
            if field == "director_names":
                if value is None:
                    cleaned[field] = []
                elif isinstance(value, list):
                    cleaned[field] = [
                        str(name).strip()
                        for name in value
                        if name and str(name).strip()
                    ]
                else:
                    # Reject non-list shapes rather than silently coercing
                    continue
            elif field == "kyc_completion_date":
                if value is None:
                    cleaned[field] = None
                else:
                    # Accept ISO dates; leave validation strictness to the
                    # API layer's pydantic model
                    cleaned[field] = str(value).strip() or None
            else:
                cleaned[field] = (
                    str(value).strip() if value is not None else None
                )

        if not cleaned:
            return None

        cleaned["vendor_kyc_updated_at"] = _now()

        try:
            self.upsert_vendor_profile(
                organization_id, vendor_name, **cleaned
            )
        except Exception as exc:
            logger.error(
                "[VendorStore] update_vendor_kyc upsert failed for %s/%s: %s",
                organization_id, vendor_name, exc,
            )
            return None

        return self.get_vendor_kyc(organization_id, vendor_name)

    def compute_vendor_ytd_spend(
        self,
        organization_id: str,
        vendor_name: str,
        *,
        year: Optional[int] = None,
    ) -> float:
        """Sum of posted invoice amounts for the vendor in the given year.

        Defaults to the current calendar year (UTC). Uses
        ``vendor_invoice_history`` rows that reached a terminal state
        of ``posted_to_erp`` — anything not posted doesn't count as
        spend. Returns 0.0 on error or empty history.
        """
        if year is None:
            year = datetime.now(timezone.utc).year
        start_iso = f"{year}-01-01T00:00:00+00:00"
        end_iso = f"{year + 1}-01-01T00:00:00+00:00"

        sql = self._prepare_sql(
            """
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM vendor_invoice_history
            WHERE organization_id = ?
              AND vendor_name = ?
              AND final_state = 'posted_to_erp'
              AND created_at >= ?
              AND created_at < ?
            """
        )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    sql, (organization_id, vendor_name, start_iso, end_iso)
                )
                row = cur.fetchone()
        except Exception as exc:
            logger.warning(
                "[VendorStore] compute_vendor_ytd_spend failed for %s/%s: %s",
                organization_id, vendor_name, exc,
            )
            return 0.0
        if row is None:
            return 0.0
        try:
            total = row["total"] if hasattr(row, "keys") else row[0]
            return float(total or 0.0)
        except (TypeError, ValueError):
            return 0.0

    # ------------------------------------------------------------------ #
    # vendor_invoice_history                                               #
    # ------------------------------------------------------------------ #

    def get_vendor_invoice_history(
        self, organization_id: str, vendor_name: str, limit: int = 6
    ) -> List[Dict[str, Any]]:
        """Return the last N invoice history records for a vendor (newest first)."""
        sql = self._prepare_sql(
            "SELECT * FROM vendor_invoice_history "
            "WHERE organization_id = ? AND vendor_name = ? "
            "ORDER BY created_at DESC LIMIT ?"
        )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (organization_id, vendor_name, limit))
                return [dict(r) for r in cur.fetchall()]
        except Exception as exc:
            logger.warning("[VendorStore] get_vendor_invoice_history failed: %s", exc)
            return []

    def record_vendor_invoice(
        self,
        organization_id: str,
        vendor_name: str,
        ap_item_id: str,
        *,
        invoice_number: Optional[str] = None,
        invoice_date: Optional[str] = None,
        amount: Optional[float] = None,
        currency: str = "USD",
        final_state: Optional[str] = None,
        exception_code: Optional[str] = None,
        was_approved: bool = False,
        approval_override: bool = False,
        agent_recommendation: Optional[str] = None,
        human_decision: Optional[str] = None,
    ) -> None:
        """Insert one invoice outcome into vendor_invoice_history."""
        sql = self._prepare_sql(
            "INSERT INTO vendor_invoice_history "
            "(id, organization_id, vendor_name, ap_item_id, invoice_number, "
            "invoice_date, amount, currency, final_state, exception_code, "
            "was_approved, approval_override, agent_recommendation, human_decision, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        try:
            with self.connect() as conn:
                conn.execute(sql, (
                    str(uuid.uuid4()), organization_id, vendor_name, ap_item_id,
                    invoice_number, invoice_date, amount, currency,
                    final_state, exception_code,
                    1 if was_approved else 0,
                    1 if approval_override else 0,
                    agent_recommendation, human_decision,
                    _now(),
                ))
                conn.commit()
        except Exception as exc:
            logger.warning("[VendorStore] record_vendor_invoice failed: %s", exc)

    def record_vendor_decision_feedback(
        self,
        organization_id: str,
        vendor_name: str,
        *,
        ap_item_id: Optional[str] = None,
        human_decision: str,
        agent_recommendation: Optional[str] = None,
        decision_override: bool = False,
        reason: Optional[str] = None,
        source_channel: Optional[str] = None,
        actor_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        action_outcome: Optional[str] = None,
    ) -> None:
        """Persist one human AP decision outcome for vendor-level learning."""
        sql = self._prepare_sql(
            "INSERT INTO vendor_decision_feedback "
            "(id, organization_id, vendor_name, ap_item_id, human_decision, "
            "agent_recommendation, decision_override, reason, source_channel, actor_id, "
            "correlation_id, action_outcome, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        try:
            with self.connect() as conn:
                conn.execute(
                    sql,
                    (
                        str(uuid.uuid4()),
                        organization_id,
                        vendor_name,
                        ap_item_id,
                        str(human_decision or "").strip().lower(),
                        (str(agent_recommendation).strip().lower() if agent_recommendation else None),
                        1 if decision_override else 0,
                        reason,
                        source_channel,
                        actor_id,
                        correlation_id,
                        action_outcome,
                        _now(),
                    ),
                )
                conn.commit()
        except Exception as exc:
            logger.warning("[VendorStore] record_vendor_decision_feedback failed: %s", exc)

    def get_vendor_decision_feedback_summary(
        self,
        organization_id: str,
        vendor_name: str,
        *,
        limit: int = 200,
        window_days: int = 180,
    ) -> Dict[str, Any]:
        """Return aggregate learning signals from recent human decisions.

        This summary is fed into AP decision routing so future recommendations
        adapt per tenant/vendor based on real human outcomes.
        """
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=max(1, int(window_days)))).isoformat()
        sql = self._prepare_sql(
            "SELECT * FROM vendor_decision_feedback "
            "WHERE organization_id = ? AND vendor_name = ? AND created_at >= ? "
            "ORDER BY created_at DESC LIMIT ?"
        )
        rows: List[Dict[str, Any]] = []
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (organization_id, vendor_name, cutoff, limit))
                rows = [dict(r) for r in cur.fetchall()]
        except Exception as exc:
            logger.warning("[VendorStore] get_vendor_decision_feedback_summary failed: %s", exc)
            rows = []

        if not rows:
            return {
                "window_days": window_days,
                "total_feedback": 0,
                "approve_count": 0,
                "reject_count": 0,
                "request_info_count": 0,
                "override_count": 0,
                "override_rate": 0.0,
                "strictness_bias": "neutral",
                "reject_after_approve_count": 0,
                "request_info_after_approve_count": 0,
                "latest_human_decision": None,
                "latest_action_outcome": None,
                "recent_reasons": [],
            }

        approve_count = 0
        reject_count = 0
        request_info_count = 0
        override_count = 0
        reject_after_approve = 0
        request_info_after_approve = 0
        recent_reasons: List[str] = []

        for row in rows:
            human_decision = str(row.get("human_decision") or "").strip().lower()
            agent_rec = str(row.get("agent_recommendation") or "").strip().lower()
            if human_decision == "approve":
                approve_count += 1
            elif human_decision == "reject":
                reject_count += 1
            elif human_decision == "request_info":
                request_info_count += 1

            if bool(row.get("decision_override")):
                override_count += 1
            if human_decision == "reject" and agent_rec == "approve":
                reject_after_approve += 1
            if human_decision == "request_info" and agent_rec == "approve":
                request_info_after_approve += 1

            reason = str(row.get("reason") or "").strip()
            if reason and reason not in recent_reasons and len(recent_reasons) < 3:
                recent_reasons.append(reason)

        total_feedback = len(rows)
        override_rate = round(override_count / total_feedback, 4) if total_feedback else 0.0
        strictness_ratio = (reject_count + request_info_count) / total_feedback if total_feedback else 0.0
        approve_ratio = approve_count / total_feedback if total_feedback else 0.0
        if strictness_ratio >= 0.45:
            strictness_bias = "strict"
        elif approve_ratio >= 0.75 and override_rate <= 0.25:
            strictness_bias = "permissive"
        else:
            strictness_bias = "neutral"

        latest = rows[0]
        return {
            "window_days": window_days,
            "total_feedback": total_feedback,
            "approve_count": approve_count,
            "reject_count": reject_count,
            "request_info_count": request_info_count,
            "override_count": override_count,
            "override_rate": override_rate,
            "strictness_bias": strictness_bias,
            "reject_after_approve_count": reject_after_approve,
            "request_info_after_approve_count": request_info_after_approve,
            "latest_human_decision": latest.get("human_decision"),
            "latest_action_outcome": latest.get("action_outcome"),
            "recent_reasons": recent_reasons,
        }

    def update_vendor_profile_from_outcome(
        self,
        organization_id: str,
        vendor_name: str,
        *,
        ap_item_id: str,
        final_state: str,
        was_approved: bool,
        approval_override: bool = False,
        agent_recommendation: Optional[str] = None,
        human_decision: Optional[str] = None,
        amount: Optional[float] = None,
        invoice_date: Optional[str] = None,
        exception_code: Optional[str] = None,
    ) -> None:
        """Record an invoice outcome and recompute vendor profile statistics.

        Called after an AP item reaches a terminal state (posted_to_erp, rejected).
        This is how the vendor intelligence layer accumulates knowledge.
        """
        # 1. Write history row
        self.record_vendor_invoice(
            organization_id, vendor_name, ap_item_id,
            invoice_date=invoice_date,
            amount=amount,
            final_state=final_state,
            exception_code=exception_code,
            was_approved=was_approved,
            approval_override=approval_override,
            agent_recommendation=agent_recommendation,
            human_decision=human_decision,
        )

        # 2. Recompute statistics from full history
        history = self.get_vendor_invoice_history(organization_id, vendor_name, limit=200)
        if not history:
            return

        amounts = [h["amount"] for h in history if h.get("amount") is not None]
        approved_count = sum(1 for h in history if h.get("was_approved"))
        override_count = sum(1 for h in history if h.get("approval_override"))
        invoice_count = len(history)

        avg_amount = sum(amounts) / len(amounts) if amounts else None
        stddev = None
        if avg_amount is not None and len(amounts) > 1:
            variance = sum((a - avg_amount) ** 2 for a in amounts) / len(amounts)
            stddev = math.sqrt(variance)

        always_approved = (approved_count == invoice_count and invoice_count >= 3)
        override_rate = round(override_count / invoice_count, 4) if invoice_count else 0.0

        last_date = history[0].get("created_at") if history else None
        last_amount = history[0].get("amount") if history else None

        # Invoice day-of-month pattern
        days = []
        for h in history:
            d = h.get("invoice_date") or h.get("created_at") or ""
            try:
                days.append(datetime.fromisoformat(d[:10]).day)
            except Exception:
                pass
        typical_day = None
        if days:
            # mode: most common day
            from collections import Counter
            typical_day = Counter(days).most_common(1)[0][0]

        self.upsert_vendor_profile(
            organization_id, vendor_name,
            invoice_count=invoice_count,
            last_invoice_date=last_date,
            last_invoice_amount=last_amount,
            avg_invoice_amount=avg_amount,
            amount_stddev=stddev,
            typical_invoice_day=typical_day,
            always_approved=1 if always_approved else 0,
            approval_override_rate=override_rate,
        )
        logger.debug(
            "[VendorStore] Updated profile for %r: count=%d avg=%.2f always_approved=%s",
            vendor_name, invoice_count, avg_amount or 0, always_approved,
        )

    # ------------------------------------------------------------------ #
    # Payment lateness analysis                                           #
    # ------------------------------------------------------------------ #

    def get_vendor_payment_lateness(
        self, organization_id: str, vendor_name: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Check if vendor invoices were paid late (due_date < posted date).

        Returns recent invoice history rows with a computed ``was_late`` flag
        for each row that has both a due_date-like field and a created_at.
        """
        sql = self._prepare_sql(
            "SELECT ap_item_id, invoice_date, amount, final_state, created_at "
            "FROM vendor_invoice_history "
            "WHERE organization_id = ? AND vendor_name = ? AND final_state = 'posted_to_erp' "
            "ORDER BY created_at DESC LIMIT ?"
        )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (organization_id, vendor_name, limit))
                rows = [dict(r) for r in cur.fetchall()]

            # Compute was_late: invoice_date (proxy for due) < created_at (proxy for pay)
            for row in rows:
                inv_date = row.get("invoice_date") or ""
                posted_date = row.get("created_at") or ""
                was_late = False
                try:
                    if inv_date and posted_date:
                        inv_dt = datetime.fromisoformat(inv_date[:10])
                        post_dt = datetime.fromisoformat(posted_date[:10])
                        # If posted more than 30 days after invoice date, consider late
                        was_late = (post_dt - inv_dt).days > 30
                except (ValueError, TypeError):
                    pass
                row["was_late"] = was_late

            return rows
        except Exception as exc:
            logger.warning("[VendorStore] get_vendor_payment_lateness failed: %s", exc)
            return []

    # ------------------------------------------------------------------ #
    # Bulk vendor profile listing                                         #
    # ------------------------------------------------------------------ #

    def list_vendor_profiles(
        self,
        organization_id: str,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Return all vendor profiles for an organization.

        Used by the Gmail extension's vendor-suggestion surface to fuzzy
        match an extracted vendor name against the customer's known
        vendor master. The bulk dict accessor is keyed by name; this one
        returns a list ordered by recency so callers can iterate without
        re-sorting.
        """
        sql = self._prepare_sql(
            "SELECT * FROM vendor_profiles "
            "WHERE organization_id = ? "
            "ORDER BY updated_at DESC LIMIT ?"
        )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (organization_id, limit))
                rows = cur.fetchall()
        except Exception as exc:
            logger.warning("[VendorStore] list_vendor_profiles failed: %s", exc)
            return []

        profiles: List[Dict[str, Any]] = []
        for row in rows:
            parsed = dict(row)
            for key, default in (
                ("vendor_aliases", []),
                ("sender_domains", []),
                ("anomaly_flags", []),
                ("metadata", {}),
                ("director_names", []),
            ):
                decoded = _loads(parsed.get(key))
                parsed[key] = decoded if decoded is not None else default
            profiles.append(parsed)
        return profiles

    # ------------------------------------------------------------------ #
    # vendor_onboarding_sessions (Phase 3.1.a)                             #
    # ------------------------------------------------------------------ #
    #
    # All state-machine transitions go through
    # transition_onboarding_session_state. Direct UPDATEs to the state
    # column are forbidden by convention — they bypass the
    # VendorOnboardingState validator and corrupt the audit timeline.

    _ONBOARDING_SESSION_JSON_COLUMNS = ("metadata",)

    @staticmethod
    def _decode_onboarding_session_row(row: Any) -> Dict[str, Any]:
        parsed = dict(row)
        for key in VendorStore._ONBOARDING_SESSION_JSON_COLUMNS:
            decoded = _loads(parsed.get(key))
            parsed[key] = decoded if decoded is not None else {}
        # Normalize is_active to a Python bool for downstream code that
        # may pass the dict back into JSON / API responses.
        if "is_active" in parsed:
            parsed["is_active"] = bool(parsed["is_active"])
        return parsed

    def create_vendor_onboarding_session(
        self,
        organization_id: str,
        vendor_name: str,
        invited_by: str,
        initial_state: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Open a new onboarding session for a vendor in INVITED state.

        Fails (returns ``None``) if there is already an active session
        for ``(organization_id, vendor_name)``. The caller is expected
        to either resume the existing session or close it before
        opening a new one.

        ``initial_state`` defaults to ``invited`` and is validated
        against :class:`VendorOnboardingState`. Tests use this hook to
        construct sessions in arbitrary starting states without going
        through the full chase loop.
        """
        from clearledgr.core.vendor_onboarding_states import (
            VALID_STATE_VALUES,
            VendorOnboardingState,
        )

        existing = self.get_active_onboarding_session(organization_id, vendor_name)
        if existing is not None:
            logger.info(
                "[VendorStore] cannot open onboarding session for %r/%r — active session %s in state %r",
                organization_id, vendor_name, existing.get("id"), existing.get("state"),
            )
            return None

        state_value = (initial_state or VendorOnboardingState.INVITED.value).strip().lower()
        if state_value not in VALID_STATE_VALUES:
            logger.warning(
                "[VendorStore] refusing to create onboarding session in unknown state %r",
                state_value,
            )
            return None

        session_id = str(uuid.uuid4())
        now = _now()
        sql = self._prepare_sql(
            """
            INSERT INTO vendor_onboarding_sessions (
                id, organization_id, vendor_name, state, is_active,
                invited_at, invited_by, last_activity_at,
                chase_count, metadata, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
        )
        try:
            with self.connect() as conn:
                conn.execute(
                    sql,
                    (
                        session_id, organization_id, vendor_name,
                        state_value, 1, now, invited_by, now,
                        0, "{}", now, now,
                    ),
                )
                conn.commit()
        except Exception as exc:
            logger.warning(
                "[VendorStore] create_vendor_onboarding_session failed: %s", exc
            )
            return None

        created = self.get_onboarding_session_by_id(session_id)
        # DESIGN_THESIS.md §3 Developer Platform — fire `vendor.invited`
        # on session creation. Fire-and-forget on the running loop so
        # the INSERT commit is never blocked on webhook delivery.
        if created is not None:
            try:
                import asyncio
                from clearledgr.services.webhook_delivery import emit_vendor_invited_webhook
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(
                        emit_vendor_invited_webhook(
                            organization_id=organization_id,
                            session_id=session_id,
                            vendor_name=vendor_name,
                            session_data=created,
                        )
                    )
                except RuntimeError:
                    # No running loop (sync caller / tests) — skip cleanly.
                    pass
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "[VendorStore] vendor.invited webhook scheduling failed (non-fatal): %s", exc
                )
        return created

    def get_onboarding_session_by_id(
        self, session_id: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch a session by primary key, regardless of active flag."""
        sql = self._prepare_sql(
            "SELECT * FROM vendor_onboarding_sessions WHERE id = ?"
        )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (session_id,))
                row = cur.fetchone()
                if row is None:
                    return None
                return self._decode_onboarding_session_row(row)
        except Exception as exc:
            logger.warning("[VendorStore] get_onboarding_session_by_id failed: %s", exc)
            return None

    def get_active_onboarding_session(
        self, organization_id: str, vendor_name: str
    ) -> Optional[Dict[str, Any]]:
        """Return the currently active session for the vendor, if any."""
        sql = self._prepare_sql(
            "SELECT * FROM vendor_onboarding_sessions "
            "WHERE organization_id = ? AND vendor_name = ? AND is_active = 1 "
            "ORDER BY invited_at DESC LIMIT 1"
        )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (organization_id, vendor_name))
                row = cur.fetchone()
                if row is None:
                    return None
                return self._decode_onboarding_session_row(row)
        except Exception as exc:
            logger.warning(
                "[VendorStore] get_active_onboarding_session failed: %s", exc
            )
            return None

    def list_pending_onboarding_sessions(
        self,
        organization_id: Optional[str] = None,
        states: Optional[List[str]] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """List all active, non-terminal sessions matching the filters.

        ``organization_id`` is optional so the chase loop can scan all
        organizations in a single query. ``states`` defaults to the
        pre-active states from
        :data:`VendorOnboardingState.PRE_ACTIVE_STATES` — pass an
        explicit list to query escalated, ready_for_erp, or any other
        slice.
        """
        from clearledgr.core.vendor_onboarding_states import (
            PRE_ACTIVE_STATES,
        )

        target_states = [s.lower().strip() for s in (states or [s.value for s in PRE_ACTIVE_STATES])]
        if not target_states:
            return []

        placeholders = ", ".join("?" for _ in target_states)
        clauses = ["is_active = 1", f"state IN ({placeholders})"]
        params: List[Any] = list(target_states)
        if organization_id:
            clauses.append("organization_id = ?")
            params.append(organization_id)
        params.append(limit)

        sql = self._prepare_sql(
            "SELECT * FROM vendor_onboarding_sessions WHERE "
            + " AND ".join(clauses)
            + " ORDER BY last_activity_at ASC LIMIT ?"
        )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, params)
                rows = cur.fetchall()
        except Exception as exc:
            logger.warning(
                "[VendorStore] list_pending_onboarding_sessions failed: %s", exc
            )
            return []

        return [self._decode_onboarding_session_row(r) for r in rows]

    def list_completed_onboarding_sessions(
        self,
        organization_id: str,
        *,
        since_iso: Optional[str] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """List vendor-onboarding sessions that reached ``active``.

        DESIGN_THESIS §11 success-metric support. The sibling
        ``list_pending_onboarding_sessions`` only returns sessions
        still in flight (is_active=1) — measuring activation
        latency needs the completed ones, where the terminal
        transition to ``active`` flips is_active to 0 and writes
        ``erp_activated_at``.

        ``since_iso`` restricts the window to sessions whose
        ``erp_activated_at`` is at or after the cutoff (ISO-8601
        UTC). Defaults to None — returns all completed sessions
        for the org, caller-capped by ``limit``.
        """
        from clearledgr.core.vendor_onboarding_states import VendorOnboardingState

        clauses = [
            "organization_id = ?",
            "state = ?",
            "erp_activated_at IS NOT NULL",
        ]
        params: List[Any] = [organization_id, VendorOnboardingState.ACTIVE.value]
        if since_iso:
            clauses.append("erp_activated_at >= ?")
            params.append(since_iso)
        params.append(limit)

        sql = self._prepare_sql(
            "SELECT * FROM vendor_onboarding_sessions WHERE "
            + " AND ".join(clauses)
            + " ORDER BY erp_activated_at DESC LIMIT ?"
        )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, params)
                rows = cur.fetchall()
        except Exception as exc:
            logger.warning(
                "[VendorStore] list_completed_onboarding_sessions failed: %s", exc
            )
            return []

        return [self._decode_onboarding_session_row(r) for r in rows]

    def transition_onboarding_session_state(
        self,
        session_id: str,
        target_state: str,
        actor_id: str,
        reason: Optional[str] = None,
        metadata_patch: Optional[Dict[str, Any]] = None,
        emit_audit: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Move a session through the state machine.

        Validates the transition via
        :func:`vendor_onboarding_states.transition_or_raise`. On success
        the canonical timestamp columns are stamped (e.g. transitioning
        into ``bank_verified`` sets ``bank_verified_at``), the session
        is marked inactive on terminal states, and an
        ``vendor_onboarding_state_transition`` audit event is emitted.

        Returns the updated session dict, or ``None`` if the session is
        not found. Raises
        :class:`IllegalVendorOnboardingTransitionError` for an illegal
        edge — the caller decides whether to translate that into a 409
        Conflict or a 500 internal error.
        """
        from clearledgr.core.vendor_onboarding_states import (
            TERMINAL_STATES,
            VendorOnboardingState,
            normalize_state,
            transition_or_raise,
        )

        session = self.get_onboarding_session_by_id(session_id)
        if session is None:
            logger.info(
                "[VendorStore] transition_onboarding_session_state: session %s not found",
                session_id,
            )
            return None

        current = session.get("state") or ""
        target = normalize_state(target_state)
        transition_or_raise(current, target, session_id=session_id)

        now = _now()
        updates: Dict[str, Any] = {
            "state": target,
            "last_activity_at": now,
            "updated_at": now,
        }

        # Stamp the canonical timestamp column for each state we land
        # in. This means callers don't need to remember which side-table
        # column corresponds to which state — the state machine layer
        # handles the bookkeeping.
        if target == VendorOnboardingState.BANK_VERIFY.value:
            updates["kyc_submitted_at"] = now
        elif target == VendorOnboardingState.BANK_VERIFIED.value:
            # The old micro-deposit path wrote microdeposit_initiated_at/by
            # here before landing in BANK_VERIFIED. With that intermediate
            # state removed, stamp bank_submitted_at on this edge (the
            # vendor's submission IS the bank-submitted moment) and
            # bank_verified_at for the verification timestamp.
            updates["bank_submitted_at"] = updates.get("bank_submitted_at") or now
            updates["bank_verified_at"] = now
        elif target == VendorOnboardingState.ACTIVE.value:
            updates["erp_activated_at"] = now
            updates["completed_at"] = now
        elif target == VendorOnboardingState.BLOCKED.value:
            updates["escalated_at"] = now
            if reason:
                updates["escalated_reason"] = reason
        elif target == VendorOnboardingState.CLOSED_UNSUCCESSFUL.value:
            updates["rejected_at"] = now
            updates["rejected_by"] = actor_id
            if reason:
                updates["rejection_reason"] = reason
        elif target == VendorOnboardingState.CLOSED_UNSUCCESSFUL.value:
            updates["abandoned_at"] = now

        # Terminal states deactivate the session so the next
        # create_vendor_onboarding_session call will succeed.
        try:
            terminal = VendorOnboardingState(target) in TERMINAL_STATES
        except ValueError:
            terminal = False
        if terminal:
            updates["is_active"] = 0

        if metadata_patch:
            current_meta = session.get("metadata") or {}
            if not isinstance(current_meta, dict):
                current_meta = {}
            current_meta.update(metadata_patch)
            updates["metadata"] = json.dumps(current_meta)

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        params = list(updates.values()) + [session_id]
        sql = self._prepare_sql(
            f"UPDATE vendor_onboarding_sessions SET {set_clause} WHERE id = ?"
        )

        # Box invariant — state and audit share a transaction. The
        # thesis §7.6 "audit trail as evidence of trust" guarantee
        # fails if a Box can reach a new state without a
        # corresponding audit event. Previously the state UPDATE
        # committed in its own transaction and the audit INSERT ran
        # afterwards in a separate connection — a DB blip between
        # the two produced an untracked state change. Now both
        # writes share one cursor + one commit so they are atomic
        # in both SQLite and Postgres.
        import uuid as _uuid
        audit_sql = self._prepare_sql(
            "INSERT INTO audit_events "
            "(id, box_id, box_type, event_type, prev_state, new_state, "
            " actor_type, actor_id, payload_json, decision_reason, "
            " organization_id, source, ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        audit_payload = {
            "session_id": session_id,
            "vendor_name": session.get("vendor_name"),
            "from_state": current,
            "to_state": target,
            "reason": reason,
        }
        # Decision-reason carries the same one-line narrative the
        # audit feed consumers filter on. Structured fields
        # (prev_state / new_state / payload_json) are the preferred
        # read path.
        audit_decision_reason = (
            f"Vendor onboarding session {session_id}: {current} -> {target}"
            + (f" — {reason}" if reason else "")
        )

        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, params)
                if emit_audit:
                    cur.execute(
                        audit_sql,
                        (
                            str(_uuid.uuid4()),
                            session_id,                      # box_id
                            "vendor_onboarding_session",     # box_type
                            "vendor_onboarding_state_transition",
                            str(current or ""),
                            target,
                            "user" if actor_id and actor_id != "agent" else "agent",
                            actor_id or "agent",
                            json.dumps(audit_payload),
                            audit_decision_reason,
                            session.get("organization_id") or "",
                            "vendor_onboarding_state_machine",
                            now,
                        ),
                    )
                conn.commit()
        except Exception as exc:
            logger.warning(
                "[VendorStore] transition_onboarding_session_state failed "
                "(state + audit rolled back together): %s", exc
            )
            return None

        updated = self.get_onboarding_session_by_id(session_id)

        # Auto-revoke all live onboarding tokens when the session
        # reaches a terminal state. Until now, a forwarded magic-link
        # remained authentication-valid until its TTL (14 days) even
        # after the vendor finished onboarding. The session-level
        # ``is_active`` check in portal_auth already blocks access
        # post-termination, but revoking the token closes the hole at
        # the token layer so defense-in-depth holds even if a future
        # refactor bypasses the session check.
        if terminal and hasattr(self, "revoke_session_tokens"):
            try:
                self.revoke_session_tokens(
                    session_id,
                    revoked_by=actor_id or "system",
                    reason=f"session_terminal:{target}",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[VendorStore] auto-revoke tokens on terminal failed "
                    "(non-fatal): %s",
                    exc,
                )

        # DESIGN_THESIS.md §3 Developer Platform — fire the public
        # vendor.* webhook for transitions the external surface cares
        # about (kyc_complete / bank_verified / activated / suspended).
        # Fire-and-forget on the running loop; internal transitions
        # with no mapped event no-op inside the helper.
        try:
            import asyncio
            from clearledgr.services.webhook_delivery import emit_vendor_state_change_webhook
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    emit_vendor_state_change_webhook(
                        organization_id=session.get("organization_id") or "",
                        session_id=session_id,
                        vendor_name=session.get("vendor_name") or "",
                        new_state=target,
                        prev_state=str(current),
                        session_data=updated,
                    )
                )
            except RuntimeError:
                pass
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "[VendorStore] vendor.* webhook scheduling failed (non-fatal): %s", exc
            )

        return updated

    def record_onboarding_chase(
        self,
        session_id: str,
        chase_type: str,
    ) -> Optional[Dict[str, Any]]:
        """Stamp last_chase_at and increment chase_count after a chase send.

        Does NOT advance the state machine — chases happen in the same
        state. The chase loop calls this after a successful email
        dispatch so the next tick can decide whether to chase again.
        """
        session = self.get_onboarding_session_by_id(session_id)
        if session is None:
            return None

        now = _now()
        new_count = int(session.get("chase_count") or 0) + 1
        sql = self._prepare_sql(
            "UPDATE vendor_onboarding_sessions "
            "SET last_chase_at = ?, chase_count = ?, updated_at = ? "
            "WHERE id = ?"
        )
        try:
            with self.connect() as conn:
                conn.execute(sql, (now, new_count, now, session_id))
                conn.commit()
        except Exception as exc:
            logger.warning(
                "[VendorStore] record_onboarding_chase failed: %s", exc
            )
            return None

        if hasattr(self, "append_audit_event"):
            try:
                self.append_audit_event(
                    {
                        "ap_item_id": "",
                        "event_type": "vendor_onboarding_chase_sent",
                        "actor_type": "agent",
                        "actor_id": "agent",
                        "reason": (
                            f"Vendor onboarding chase #{new_count} ({chase_type}) "
                            f"for session {session_id}"
                        ),
                        "metadata": {
                            "session_id": session_id,
                            "vendor_name": session.get("vendor_name"),
                            "chase_type": chase_type,
                            "chase_count": new_count,
                            "current_state": session.get("state"),
                        },
                        "organization_id": session.get("organization_id") or "",
                        "source": "vendor_onboarding_state_machine",
                    }
                )
            except Exception as audit_exc:
                logger.warning(
                    "[VendorStore] onboarding chase audit failed (non-fatal): %s",
                    audit_exc,
                )

        return self.get_onboarding_session_by_id(session_id)

    def attach_erp_vendor_id(
        self,
        session_id: str,
        erp_vendor_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Persist the ERP vendor ID returned by the create_vendor dispatcher.

        Called by Phase 3.1.e when the agent successfully writes the
        vendor to the customer's ERP vendor master. Must be called
        BEFORE the state machine transitions to ``active`` so the audit
        trail captures both the ERP ID and the state change in the
        right order.
        """
        if not erp_vendor_id:
            return None
        session = self.get_onboarding_session_by_id(session_id)
        if session is None:
            return None
        now = _now()
        sql = self._prepare_sql(
            "UPDATE vendor_onboarding_sessions "
            "SET erp_vendor_id = ?, updated_at = ? "
            "WHERE id = ?"
        )
        try:
            with self.connect() as conn:
                conn.execute(sql, (erp_vendor_id, now, session_id))
                conn.commit()
        except Exception as exc:
            logger.warning("[VendorStore] attach_erp_vendor_id failed: %s", exc)
            return None
        return self.get_onboarding_session_by_id(session_id)

    # ------------------------------------------------------------------
    # §3 Multi-Entity: Vendor Entity Overrides
    # ------------------------------------------------------------------

    def get_vendor_entity_override(
        self, vendor_profile_id: str, entity_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get entity-specific vendor overrides (payment terms, bank details)."""
        self.initialize()
        sql = self._prepare_sql(
            "SELECT * FROM vendor_entity_overrides WHERE vendor_profile_id = ? AND entity_id = ?"
        )
        try:
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (vendor_profile_id, entity_id))
                row = cur.fetchone()
            return dict(row) if row else None
        except Exception:
            return None

    def set_vendor_entity_override(
        self, vendor_profile_id: str, entity_id: str, organization_id: str, **fields
    ) -> Dict[str, Any]:
        """Create or update entity-specific vendor overrides."""
        self.initialize()
        allowed = {"payment_terms", "bank_details_encrypted", "default_currency"}
        safe = {k: v for k, v in fields.items() if k in allowed}
        now = datetime.now(timezone.utc).isoformat()

        existing = self.get_vendor_entity_override(vendor_profile_id, entity_id)
        if existing:
            if not safe:
                return existing
            set_clause = ", ".join(f"{k} = ?" for k in safe)
            sql = self._prepare_sql(
                f"UPDATE vendor_entity_overrides SET {set_clause}, updated_at = ? WHERE vendor_profile_id = ? AND entity_id = ?"
            )
            params = (*safe.values(), now, vendor_profile_id, entity_id)
            with self.connect() as conn:
                conn.execute(sql, params)
                conn.commit()
            return self.get_vendor_entity_override(vendor_profile_id, entity_id) or {}

        override_id = f"VEO-{uuid.uuid4().hex}"
        cols = ["id", "vendor_profile_id", "entity_id", "organization_id", "created_at", "updated_at"]
        vals = [override_id, vendor_profile_id, entity_id, organization_id, now, now]
        for k, v in safe.items():
            cols.append(k)
            vals.append(v)
        placeholders = ", ".join("?" for _ in cols)
        sql = self._prepare_sql(
            f"INSERT INTO vendor_entity_overrides ({', '.join(cols)}) VALUES ({placeholders})"
        )
        with self.connect() as conn:
            conn.execute(sql, tuple(vals))
            conn.commit()
        return self.get_vendor_entity_override(vendor_profile_id, entity_id) or {}

    def get_vendor_for_entity(
        self, organization_id: str, vendor_name: str, entity_id: str
    ) -> Dict[str, Any]:
        """§3: Returns merged vendor profile — parent-level KYC + entity-level overrides."""
        profile = self.get_vendor_profile(vendor_name, organization_id)
        if not profile:
            return {}
        override = self.get_vendor_entity_override(profile.get("id", ""), entity_id)
        if override:
            if override.get("payment_terms"):
                profile["payment_terms"] = override["payment_terms"]
            if override.get("bank_details_encrypted"):
                profile["bank_details_encrypted"] = override["bank_details_encrypted"]
            if override.get("default_currency"):
                profile["default_currency"] = override["default_currency"]
            profile["entity_override"] = override
        return profile
