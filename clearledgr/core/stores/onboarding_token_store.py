"""Onboarding token store mixin — Phase 3.1.b.

Manages one-time, single-session magic-link tokens that authorize an
unauthenticated vendor (or any third party) to act on a single
``vendor_onboarding_sessions`` row.

Token semantics
===============

* **Per-session, not per-action.** Each onboarding session has at most
  one active token. The vendor can revisit the link multiple times
  within a single attempt (KYC on Monday, bank details on Tuesday) —
  every revisit increments ``access_count`` but does not consume the
  token.

* **Auto-dies when the session terminates.** When a session reaches
  ``active`` / ``rejected`` / ``abandoned``, the token is revoked by
  the same code path that flips ``is_active = 0`` on the session.
  Bookmarking the link does not work — a vendor revisiting after the
  session has terminated gets a 410 Gone with a "contact your customer"
  message.

* **14-day default TTL.** Onboarding rarely takes longer than two
  weeks. Tokens that expire mid-onboarding can be re-issued via a
  separate customer-side API call without losing accumulated session
  progress (the session state machine is independent of the token
  lifecycle).

* **Plaintext token never persisted.** The raw token is returned ONCE
  from :meth:`generate_onboarding_token` — the caller embeds it in the
  email body and never sees it again. The DB stores only the SHA-256
  hash of the token, so a database read does not allow an attacker to
  recover live magic links. Validation hashes the inbound token and
  looks up by hash; comparison is constant-time via
  :func:`secrets.compare_digest`.

* **Single-table, app-enforced uniqueness.** The token table has a
  ``UNIQUE(token_hash)`` constraint plus an app-level rule that only
  one non-revoked token per session may exist at a time. The store
  enforces this by revoking any prior token for the same session
  before issuing a new one.

This mixin is composed into :class:`ClearledgrDB` alongside the other
store mixins. Direct SQL access to the ``vendor_onboarding_tokens``
table is forbidden — every read/write goes through this module so the
hashing, expiry, and audit semantics stay in one place.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


_TABLE_VENDOR_ONBOARDING_TOKENS = """
CREATE TABLE IF NOT EXISTS vendor_onboarding_tokens (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    vendor_name TEXT NOT NULL,
    session_id TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT 'full_onboarding',
    issued_at TEXT NOT NULL,
    issued_by TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_accessed_at TEXT,
    access_count INTEGER NOT NULL DEFAULT 0,
    revoked_at TEXT,
    revoked_by TEXT,
    revoke_reason TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    UNIQUE(token_hash)
)
"""

_DEFAULT_TTL_DAYS = 14
# 24 bytes → 192 bits of entropy → 32 chars after urlsafe base64.
# 128 bits is the computational-infeasibility floor for brute forcing;
# 192 gives a comfortable margin without bloating the link. The old 48
# byte (64 char) links read as "what is this" to non-technical vendors
# opening the invite email; this size matches Stripe/Airtable/Notion
# magic-link norms (~30-40 chars).
_TOKEN_BYTE_LENGTH = 24


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _hash_token(raw_token: str) -> str:
    """Return the canonical SHA-256 hex digest used for storage + lookup."""
    return hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()


def _decode_token_row(row: Any) -> Dict[str, Any]:
    parsed = dict(row)
    metadata = parsed.get("metadata")
    if isinstance(metadata, str):
        try:
            parsed["metadata"] = json.loads(metadata) if metadata.strip() else {}
        except Exception:
            parsed["metadata"] = {}
    elif metadata is None:
        parsed["metadata"] = {}
    return parsed


class OnboardingTokenStore:
    """Mixin providing one-time vendor onboarding token persistence."""

    VENDOR_ONBOARDING_TOKENS_TABLE_SQL = _TABLE_VENDOR_ONBOARDING_TOKENS

    # ------------------------------------------------------------------ #
    # Token lifecycle                                                     #
    # ------------------------------------------------------------------ #

    def generate_onboarding_token(
        self,
        session_id: str,
        issued_by: str,
        purpose: str = "full_onboarding",
        ttl_days: int = _DEFAULT_TTL_DAYS,
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Issue a fresh magic-link token for an onboarding session.

        Returns ``(raw_token, token_row)`` on success or ``None`` if the
        session does not exist or is not active. The raw token is the
        only time the unhashed value is exposed — the caller is
        expected to embed it directly in the magic-link URL and discard
        the local copy after sending the email.

        If a non-revoked token already exists for the session, it is
        revoked first so the new token becomes the unique active one.
        This keeps the application-level invariant "at most one live
        token per session" without an SQL UNIQUE constraint that would
        block re-issue after expiry.
        """
        # Verify the session exists and is still active. We don't issue
        # tokens for terminal sessions — that would be a footgun the
        # customer-side API has to be defended from.
        if not hasattr(self, "get_onboarding_session_by_id"):
            logger.warning(
                "[OnboardingTokenStore] cannot issue token — VendorStore mixin missing"
            )
            return None
        session = self.get_onboarding_session_by_id(session_id)
        if session is None:
            logger.info("[OnboardingTokenStore] session %s not found", session_id)
            return None
        if not session.get("is_active"):
            logger.info(
                "[OnboardingTokenStore] session %s is not active — refusing to issue token",
                session_id,
            )
            return None

        # Revoke any prior live token for this session so we keep the
        # one-active-token-per-session invariant.
        self.revoke_session_tokens(
            session_id,
            revoked_by=issued_by,
            reason="superseded_by_new_token",
        )

        raw_token = secrets.token_urlsafe(_TOKEN_BYTE_LENGTH)
        token_id = str(uuid.uuid4())
        token_hash = _hash_token(raw_token)
        now_dt = _now()
        expires_dt = now_dt + timedelta(days=max(1, int(ttl_days)))

        sql = self._prepare_sql(
            """
            INSERT INTO vendor_onboarding_tokens (
                id, organization_id, vendor_name, session_id, token_hash,
                purpose, issued_at, issued_by, expires_at,
                last_accessed_at, access_count, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
        )
        try:
            with self.connect() as conn:
                conn.execute(
                    sql,
                    (
                        token_id,
                        session.get("organization_id") or "",
                        session.get("vendor_name") or "",
                        session_id,
                        token_hash,
                        purpose.strip().lower() or "full_onboarding",
                        now_dt.isoformat(),
                        issued_by,
                        expires_dt.isoformat(),
                        None,
                        0,
                        "{}",
                    ),
                )
                conn.commit()
        except Exception as exc:
            logger.warning(
                "[OnboardingTokenStore] generate_onboarding_token failed: %s", exc
            )
            return None

        token_row = self.get_onboarding_token_by_id(token_id)
        if token_row is None:
            return None

        # Audit event with NO token value — only field names + actor.
        if hasattr(self, "append_ap_audit_event"):
            try:
                self.append_ap_audit_event(
                    {
                        "ap_item_id": "",
                        "event_type": "vendor_onboarding_token_issued",
                        "actor_type": "user",
                        "actor_id": issued_by,
                        "reason": (
                            f"Onboarding token issued for session {session_id} "
                            f"(purpose={purpose}, ttl_days={ttl_days})"
                        ),
                        "metadata": {
                            "session_id": session_id,
                            "vendor_name": session.get("vendor_name"),
                            "token_id": token_id,
                            "purpose": purpose,
                            "expires_at": expires_dt.isoformat(),
                        },
                        "organization_id": session.get("organization_id") or "",
                        "source": "onboarding_token_store",
                    }
                )
            except Exception as audit_exc:
                logger.warning(
                    "[OnboardingTokenStore] audit emission failed (non-fatal): %s",
                    audit_exc,
                )

        return raw_token, token_row

    def validate_onboarding_token(
        self, raw_token: str
    ) -> Optional[Dict[str, Any]]:
        """Look up a token row by hashing the inbound raw token.

        Returns the token row when:
          - the hash matches an existing row
          - the row is not revoked
          - the row's ``expires_at`` is in the future

        Returns ``None`` for any failure mode (unknown / revoked /
        expired). The caller decides whether to translate that into 404
        or 410 — see :func:`clearledgr.core.portal_auth.require_portal_token`.

        Constant-time hash comparison is enforced via
        :func:`secrets.compare_digest`.
        """
        if not raw_token or not isinstance(raw_token, str):
            return None
        token_hash = _hash_token(raw_token)
        sql = self._prepare_sql(
            "SELECT * FROM vendor_onboarding_tokens WHERE token_hash = ?"
        )
        try:
            with self.connect() as conn:
                if self.use_postgres:
                    cur = conn.cursor()
                    cur.execute(sql, (token_hash,))
                    row = cur.fetchone()
                else:
                    conn.row_factory = __import__("sqlite3").Row
                    cur = conn.cursor()
                    cur.execute(sql, (token_hash,))
                    row = cur.fetchone()
                if row is None:
                    return None
                token_row = _decode_token_row(row)
        except Exception as exc:
            logger.warning("[OnboardingTokenStore] validate failed: %s", exc)
            return None

        # Constant-time hash check (defense in depth — the SQL lookup
        # already matched the hash, but if a future change moves to a
        # range scan or fuzzy lookup the comparison still has to hold).
        if not secrets.compare_digest(
            str(token_row.get("token_hash") or ""),
            token_hash,
        ):
            return None

        if token_row.get("revoked_at"):
            return None

        expires_at = str(token_row.get("expires_at") or "")
        if expires_at:
            try:
                expires_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if expires_dt.tzinfo is None:
                    expires_dt = expires_dt.replace(tzinfo=timezone.utc)
                if expires_dt < _now():
                    return None
            except (TypeError, ValueError):
                return None

        return token_row

    def get_onboarding_token_by_id(
        self, token_id: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch a token row by primary key. Includes revoked tokens."""
        sql = self._prepare_sql(
            "SELECT * FROM vendor_onboarding_tokens WHERE id = ?"
        )
        try:
            with self.connect() as conn:
                if self.use_postgres:
                    cur = conn.cursor()
                    cur.execute(sql, (token_id,))
                    row = cur.fetchone()
                else:
                    conn.row_factory = __import__("sqlite3").Row
                    cur = conn.cursor()
                    cur.execute(sql, (token_id,))
                    row = cur.fetchone()
                if row is None:
                    return None
                return _decode_token_row(row)
        except Exception as exc:
            logger.warning(
                "[OnboardingTokenStore] get_onboarding_token_by_id failed: %s", exc
            )
            return None

    def list_session_tokens(
        self, session_id: str, include_revoked: bool = False
    ) -> List[Dict[str, Any]]:
        """List every token issued for a session, newest first."""
        clauses = ["session_id = ?"]
        params: List[Any] = [session_id]
        if not include_revoked:
            clauses.append("revoked_at IS NULL")
        sql = self._prepare_sql(
            "SELECT * FROM vendor_onboarding_tokens WHERE "
            + " AND ".join(clauses)
            + " ORDER BY issued_at DESC"
        )
        try:
            with self.connect() as conn:
                if self.use_postgres:
                    cur = conn.cursor()
                    cur.execute(sql, params)
                    rows = cur.fetchall()
                else:
                    conn.row_factory = __import__("sqlite3").Row
                    cur = conn.cursor()
                    cur.execute(sql, params)
                    rows = cur.fetchall()
        except Exception as exc:
            logger.warning(
                "[OnboardingTokenStore] list_session_tokens failed: %s", exc
            )
            return []
        return [_decode_token_row(r) for r in rows]

    def record_onboarding_token_access(self, token_id: str) -> None:
        """Increment access_count + stamp last_accessed_at for a token.

        Called by the portal auth dependency on every successful
        request that bears a valid token. Failures are non-fatal — we
        don't want to break the vendor's onboarding flow because of an
        instrumentation write hiccup.
        """
        now = _now_iso()
        sql = self._prepare_sql(
            "UPDATE vendor_onboarding_tokens "
            "SET last_accessed_at = ?, access_count = access_count + 1 "
            "WHERE id = ?"
        )
        try:
            with self.connect() as conn:
                conn.execute(sql, (now, token_id))
                conn.commit()
        except Exception as exc:
            logger.debug(
                "[OnboardingTokenStore] record_access non-fatal failure: %s", exc
            )

    def revoke_onboarding_token(
        self,
        token_id: str,
        revoked_by: str,
        reason: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Revoke a single token. Returns the updated row, or None."""
        token_row = self.get_onboarding_token_by_id(token_id)
        if token_row is None:
            return None
        if token_row.get("revoked_at"):
            return token_row  # already revoked — idempotent
        now = _now_iso()
        sql = self._prepare_sql(
            "UPDATE vendor_onboarding_tokens "
            "SET revoked_at = ?, revoked_by = ?, revoke_reason = ? "
            "WHERE id = ?"
        )
        try:
            with self.connect() as conn:
                conn.execute(sql, (now, revoked_by, reason or "", token_id))
                conn.commit()
        except Exception as exc:
            logger.warning(
                "[OnboardingTokenStore] revoke_onboarding_token failed: %s", exc
            )
            return None

        if hasattr(self, "append_ap_audit_event"):
            try:
                self.append_ap_audit_event(
                    {
                        "ap_item_id": "",
                        "event_type": "vendor_onboarding_token_revoked",
                        "actor_type": "user" if revoked_by != "agent" else "agent",
                        "actor_id": revoked_by,
                        "reason": (
                            f"Onboarding token {token_id} revoked"
                            + (f" — {reason}" if reason else "")
                        ),
                        "metadata": {
                            "token_id": token_id,
                            "session_id": token_row.get("session_id"),
                            "vendor_name": token_row.get("vendor_name"),
                            "revoke_reason": reason or None,
                        },
                        "organization_id": token_row.get("organization_id") or "",
                        "source": "onboarding_token_store",
                    }
                )
            except Exception as audit_exc:
                logger.warning(
                    "[OnboardingTokenStore] revoke audit emission failed: %s",
                    audit_exc,
                )

        return self.get_onboarding_token_by_id(token_id)

    def revoke_session_tokens(
        self,
        session_id: str,
        revoked_by: str,
        reason: str = "",
    ) -> int:
        """Bulk-revoke every live token for a session.

        Used by:
          - :meth:`generate_onboarding_token` to maintain the
            one-active-token-per-session invariant on re-issue
          - The vendor onboarding state machine, on terminal transitions
            (called from Phase 3.1.e ERP activation)

        Returns the number of rows revoked.
        """
        live_tokens = self.list_session_tokens(session_id, include_revoked=False)
        revoked_count = 0
        for token in live_tokens:
            if self.revoke_onboarding_token(token["id"], revoked_by, reason) is not None:
                revoked_count += 1
        return revoked_count
