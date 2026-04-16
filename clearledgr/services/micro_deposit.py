"""Micro-deposit verification service — Phase 3.1.d.

DESIGN_THESIS.md §9 Stage 3 — Bank Verify: *"Agent initiates two
micro-deposits to the vendor's provided IBAN. Vendor confirms the exact
amounts via portal. Account is validated."*

This module implements the generation, storage, and verification of
micro-deposit amounts. It does NOT orchestrate the actual ACH/wire
transfer — V1 uses the manual model where the customer's AP Manager
initiates the real deposits from their bank, and the vendor confirms
the amounts via the portal form.

Flow
====

1. **Initiation** — the AP Manager calls the customer-side endpoint.
   ``MicroDepositService.initiate()`` generates two cryptographically
   random amounts (0.01–0.99 each), encrypts them with Fernet, and
   stores them on the session metadata. A Slack card is posted to the
   finance channel showing the amounts + vendor name + IBAN (masked)
   so the AP Manager can initiate the transfer from their bank.

2. **Vendor confirmation** — the vendor enters the two amounts in the
   portal form. ``MicroDepositService.verify()`` decrypts the expected
   amounts, compares them order-independently with a tolerance of ±0.01
   (to handle rounding across currencies), and returns the result.

3. **Success** — if the amounts match, the service transitions the
   onboarding session from ``microdeposit_pending`` to
   ``bank_verified``.

4. **Failure + retry** — on mismatch, ``attempt_count`` increments. After
   3 failed attempts the session is kicked back to ``awaiting_bank`` so
   the vendor can re-enter their IBAN (they may have a typo). A Slack
   alert notifies the AP Manager.

5. **Lockout** — after the kick-back to ``awaiting_bank``, the vendor
   can re-submit bank details, which will require a fresh initiation
   of micro-deposits (the old amounts are invalidated).

Security
========

* Expected amounts are Fernet-encrypted in session metadata — never
  plaintext in the DB.
* The portal form accepts amounts as strings and this module
  validates + compares them. The amounts are never reflected back
  to the vendor — they see only "Correct" or "Incorrect".
* Audit events record whether verification succeeded or failed plus
  the attempt count, never the amounts themselves (§19 discipline).
"""
from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_AMOUNT_TOLERANCE = 0.015  # ±1.5p handles cross-currency rounding

# Per-session serialization for verify(). The attempt counter is stored
# in JSON metadata and updated via read-modify-write, so two concurrent
# POSTs would both observe the same pre-increment value and both write
# +1 — letting an attacker burst parallel guesses past the lockout. We
# guard the critical section with a Redis SETNX lock (cross-process)
# plus an in-process threading.Lock fallback (single-process / tests).

_VERIFY_LOCK_TTL_SECONDS = 5
_VERIFY_LOCK_WAIT_SECONDS = 2.0
_VERIFY_PROC_LOCKS: Dict[str, threading.Lock] = {}
_VERIFY_PROC_LOCKS_GUARD = threading.Lock()


def _proc_lock_for(session_id: str) -> threading.Lock:
    with _VERIFY_PROC_LOCKS_GUARD:
        lock = _VERIFY_PROC_LOCKS.get(session_id)
        if lock is None:
            lock = threading.Lock()
            _VERIFY_PROC_LOCKS[session_id] = lock
        return lock


def _redis_client():
    try:
        from clearledgr.services import rate_limit
        return rate_limit._redis_client
    except Exception:
        return None


def _try_acquire_redis_lock(client, key: str) -> Optional[str]:
    if client is None:
        return None
    token = secrets.token_urlsafe(16)
    try:
        ok = client.set(key, token, nx=True, ex=_VERIFY_LOCK_TTL_SECONDS)
        return token if ok else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("[microdeposit] redis SETNX failed for %s: %s", key, exc)
        return None


_REDIS_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


def _release_redis_lock(client, key: str, token: str) -> None:
    if client is None or not token:
        return
    try:
        client.eval(_REDIS_RELEASE_SCRIPT, 1, key, token)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[microdeposit] redis release failed for %s: %s", key, exc)


@dataclass
class MicroDepositInitResult:
    """Outcome of initiating micro-deposits for a session."""

    success: bool
    amounts: Optional[Tuple[float, float]] = None  # only set on success
    error: Optional[str] = None


@dataclass
class MicroDepositVerifyResult:
    """Outcome of a vendor's verification attempt."""

    success: bool
    verified: bool = False
    attempt_number: int = 0
    locked_out: bool = False
    error: Optional[str] = None


def _generate_amounts() -> Tuple[float, float]:
    """Generate two distinct random amounts in [0.01, 0.99].

    Uses ``secrets.randbelow`` for cryptographic randomness. The two
    amounts are guaranteed to be different so the vendor can't get
    lucky by entering the same number twice.
    """
    a1 = (secrets.randbelow(99) + 1) / 100.0  # 0.01–0.99
    a2 = a1
    while a2 == a1:
        a2 = (secrets.randbelow(99) + 1) / 100.0
    return round(a1, 2), round(a2, 2)


def _amounts_match(
    expected: Tuple[float, float],
    submitted: Tuple[float, float],
) -> bool:
    """Order-independent comparison with tolerance.

    The vendor can enter the amounts in either order. We compare both
    orderings and accept if either matches within tolerance.
    """
    e1, e2 = expected
    s1, s2 = submitted
    # Try both orderings.
    order_a = abs(e1 - s1) <= _AMOUNT_TOLERANCE and abs(e2 - s2) <= _AMOUNT_TOLERANCE
    order_b = abs(e1 - s2) <= _AMOUNT_TOLERANCE and abs(e2 - s1) <= _AMOUNT_TOLERANCE
    return order_a or order_b


class MicroDepositService:
    """Orchestrates the micro-deposit generation, storage, and verification."""

    def __init__(self, db: Any = None):
        from clearledgr.core.database import get_db
        self.db = db or get_db()

    def initiate(
        self,
        session_id: str,
        actor_id: str,
    ) -> MicroDepositInitResult:
        """Generate + encrypt + store two micro-deposit amounts.

        Transitions the session to ``microdeposit_pending`` if not
        already there. Fails if the session doesn't exist or is in
        a terminal state.
        """
        session = self.db.get_onboarding_session_by_id(session_id)
        if session is None:
            return MicroDepositInitResult(success=False, error="session_not_found")
        if not session.get("is_active"):
            return MicroDepositInitResult(success=False, error="session_not_active")

        amounts = _generate_amounts()
        # Encrypt the amounts as a JSON pair.
        amounts_json = json.dumps([amounts[0], amounts[1]])
        encrypted = self.db._encrypt_secret(amounts_json)
        if encrypted is None:
            return MicroDepositInitResult(success=False, error="encryption_failed")

        metadata_patch = {
            "microdeposit_expected_encrypted": encrypted,
            "microdeposit_attempt_count": 0,
            "microdeposit_locked_out": False,
        }

        # Transition to microdeposit_pending if we're in awaiting_bank.
        # If already in microdeposit_pending (re-initiation after lockout
        # kick-back), stay there — the transition is a no-op but we
        # still update the metadata with fresh amounts.
        current_state = session.get("state") or ""
        if current_state == "awaiting_bank":
            from clearledgr.core.vendor_onboarding_states import (
                VendorOnboardingState,
            )
            self.db.transition_onboarding_session_state(
                session_id,
                VendorOnboardingState.MICRODEPOSIT_PENDING.value,
                actor_id=actor_id,
                metadata_patch=metadata_patch,
            )
        elif current_state == "microdeposit_pending":
            # Re-initiation: update metadata only (fresh amounts).
            # No state transition — we're already in the right state.
            self._update_session_metadata(session_id, metadata_patch)
        else:
            return MicroDepositInitResult(
                success=False,
                error=f"invalid_state_for_initiation:{current_state}",
            )

        # Audit event — amounts NOT logged.
        try:
            self.db.append_ap_audit_event(
                {
                    "ap_item_id": "",
                    "event_type": "vendor_microdeposit_initiated",
                    "actor_type": "user",
                    "actor_id": actor_id,
                    "reason": (
                        f"Micro-deposits initiated for session {session_id} "
                        f"by {actor_id}"
                    ),
                    "metadata": {
                        "session_id": session_id,
                        "vendor_name": session.get("vendor_name"),
                    },
                    "organization_id": session.get("organization_id") or "",
                    "source": "micro_deposit_service",
                }
            )
        except Exception:
            pass

        return MicroDepositInitResult(success=True, amounts=amounts)

    def verify(
        self,
        session_id: str,
        submitted_amount_one: float,
        submitted_amount_two: float,
        actor_id: str = "vendor_portal",
    ) -> MicroDepositVerifyResult:
        """Compare vendor-submitted amounts against expected.

        Serialized per-session via Redis SETNX (with an in-process lock
        fallback) so parallel POSTs cannot race the read-modify-write of
        the attempt counter and slip past the lockout.
        """
        proc_lock = _proc_lock_for(session_id)
        acquired_proc = proc_lock.acquire(timeout=_VERIFY_LOCK_WAIT_SECONDS)
        if not acquired_proc:
            return MicroDepositVerifyResult(
                success=False, error="verification_busy_try_again"
            )
        redis = _redis_client()
        redis_key = f"microdeposit_verify:{session_id}"
        redis_token: Optional[str] = None
        if redis is not None:
            deadline = time.monotonic() + _VERIFY_LOCK_WAIT_SECONDS
            while time.monotonic() < deadline:
                redis_token = _try_acquire_redis_lock(redis, redis_key)
                if redis_token:
                    break
                time.sleep(0.05)
            if not redis_token:
                proc_lock.release()
                return MicroDepositVerifyResult(
                    success=False, error="verification_busy_try_again"
                )
        try:
            return self._verify_locked(
                session_id,
                submitted_amount_one,
                submitted_amount_two,
                actor_id,
            )
        finally:
            if redis_token is not None:
                _release_redis_lock(redis, redis_key, redis_token)
            proc_lock.release()

    def _verify_locked(
        self,
        session_id: str,
        submitted_amount_one: float,
        submitted_amount_two: float,
        actor_id: str,
    ) -> MicroDepositVerifyResult:
        session = self.db.get_onboarding_session_by_id(session_id)
        if session is None:
            return MicroDepositVerifyResult(success=False, error="session_not_found")
        if not session.get("is_active"):
            return MicroDepositVerifyResult(success=False, error="session_not_active")

        meta = session.get("metadata") or {}
        encrypted = meta.get("microdeposit_expected_encrypted")
        if not encrypted:
            return MicroDepositVerifyResult(
                success=False, error="no_microdeposit_initiated"
            )

        # Check lockout.
        if meta.get("microdeposit_locked_out"):
            return MicroDepositVerifyResult(
                success=False, locked_out=True, error="locked_out"
            )

        # Decrypt expected amounts.
        decrypted = self.db._decrypt_secret(str(encrypted))
        if decrypted is None:
            return MicroDepositVerifyResult(
                success=False, error="decryption_failed"
            )
        try:
            expected = json.loads(decrypted)
            expected_tuple = (float(expected[0]), float(expected[1]))
        except (json.JSONDecodeError, IndexError, TypeError, ValueError):
            return MicroDepositVerifyResult(
                success=False, error="corrupt_expected_amounts"
            )

        submitted = (
            round(float(submitted_amount_one), 2),
            round(float(submitted_amount_two), 2),
        )

        attempt_count = int(meta.get("microdeposit_attempt_count") or 0) + 1

        if _amounts_match(expected_tuple, submitted):
            # Success — transition to bank_verified.
            from clearledgr.core.vendor_onboarding_states import (
                VendorOnboardingState,
            )
            self.db.transition_onboarding_session_state(
                session_id,
                VendorOnboardingState.BANK_VERIFIED.value,
                actor_id=actor_id,
                metadata_patch={
                    "microdeposit_attempt_count": attempt_count,
                    "microdeposit_verified": True,
                },
            )
            self._audit_verification(session, attempt_count, verified=True)
            return MicroDepositVerifyResult(
                success=True, verified=True, attempt_number=attempt_count
            )

        # Mismatch — increment counter, check lockout.
        locked_out = attempt_count >= _MAX_ATTEMPTS
        metadata_patch = {
            "microdeposit_attempt_count": attempt_count,
        }
        if locked_out:
            metadata_patch["microdeposit_locked_out"] = True
            metadata_patch["microdeposit_expected_encrypted"] = None  # invalidate
            # Kick back to awaiting_bank so vendor can re-enter IBAN.
            from clearledgr.core.vendor_onboarding_states import (
                VendorOnboardingState,
            )
            self.db.transition_onboarding_session_state(
                session_id,
                VendorOnboardingState.AWAITING_BANK.value,
                actor_id=actor_id,
                reason=f"Micro-deposit verification failed after {_MAX_ATTEMPTS} attempts",
                metadata_patch=metadata_patch,
            )
        else:
            # Update metadata only — stay in microdeposit_pending. We
            # cannot use transition_onboarding_session_state here because
            # the state machine doesn't allow self-transitions. Instead
            # we update the session metadata directly.
            self._update_session_metadata(session_id, metadata_patch)

        self._audit_verification(
            session, attempt_count, verified=False, locked_out=locked_out
        )

        return MicroDepositVerifyResult(
            success=True,
            verified=False,
            attempt_number=attempt_count,
            locked_out=locked_out,
        )

    def _update_session_metadata(
        self, session_id: str, metadata_patch: Dict[str, Any]
    ) -> None:
        """Update session metadata without a state transition.

        Used when we need to persist metadata (attempt counters, etc.)
        but don't want to change the onboarding state. The state
        machine doesn't allow self-transitions by design, so this is
        the only way to update metadata in-place.
        """
        import json as _json

        session = self.db.get_onboarding_session_by_id(session_id)
        if session is None:
            return
        current_meta = session.get("metadata") or {}
        if not isinstance(current_meta, dict):
            current_meta = {}
        current_meta.update(metadata_patch)

        now = datetime.now(timezone.utc).isoformat()
        sql = self.db._prepare_sql(
            "UPDATE vendor_onboarding_sessions "
            "SET metadata = ?, updated_at = ? WHERE id = ?"
        )
        try:
            with self.db.connect() as conn:
                conn.execute(sql, (_json.dumps(current_meta), now, session_id))
                conn.commit()
        except Exception as exc:
            logger.warning(
                "[MicroDepositService] _update_session_metadata failed: %s", exc
            )

    def _audit_verification(
        self,
        session: Dict[str, Any],
        attempt_number: int,
        verified: bool,
        locked_out: bool = False,
    ) -> None:
        try:
            self.db.append_ap_audit_event(
                {
                    "ap_item_id": "",
                    "event_type": "vendor_microdeposit_verification",
                    "actor_type": "vendor",
                    "actor_id": "vendor_portal",
                    "reason": (
                        f"Micro-deposit verification "
                        f"{'succeeded' if verified else 'failed'} "
                        f"(attempt {attempt_number})"
                        + (f" — locked out after {_MAX_ATTEMPTS} attempts" if locked_out else "")
                    ),
                    "metadata": {
                        "session_id": session.get("id"),
                        "vendor_name": session.get("vendor_name"),
                        "verified": verified,
                        "attempt_number": attempt_number,
                        "locked_out": locked_out,
                    },
                    "organization_id": session.get("organization_id") or "",
                    "source": "micro_deposit_service",
                }
            )
        except Exception:
            pass


def get_micro_deposit_service(db: Any = None) -> MicroDepositService:
    return MicroDepositService(db=db)
