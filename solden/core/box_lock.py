"""Per-box advisory locks (Postgres ``pg_try_advisory_lock``) used to
serialise critical sections that touch a single Box across concurrent
processes.

The lock keys are derived from ``(organization_id, box_id)`` so:

  * Two orgs operating on the same ``box_id`` value (eg the literal
    ``"AP-1"``) never collide — different first-int4 keys.
  * Same-org-same-box collisions are exactly what we want — the
    second caller waits or aborts, depending on which acquire flavour
    it used.

This module exists because both the runtime ``CoordinationEngine`` (plan
execution) and the legacy ``InvoiceWorkflowService`` (e.g. the approval-
dispatch outbox in ``_send_for_approval``) need the same primitive.
Inlining it twice was a maintainability hazard; the helpers were extracted
here so both paths share one implementation.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)


def box_lock_keys(organization_id: str, box_id: str) -> Tuple[int, int]:
    """Two-int4 key for ``pg_try_advisory_lock(int, int)``.

    The first int hashes ``organization_id`` and the second hashes
    ``box_id``. Postgres advisory-lock keys are signed int4; blake2b
    gives us a stable hash that we then reduce to int4 range with
    sign conversion so the keys fit the two-arg overload.
    """
    org_bytes = (organization_id or "").encode("utf-8")
    box_bytes = (box_id or "").encode("utf-8")
    org_hash = int.from_bytes(
        hashlib.blake2b(org_bytes, digest_size=4).digest(), "big",
    )
    box_hash = int.from_bytes(
        hashlib.blake2b(box_bytes, digest_size=4).digest(), "big",
    )
    if org_hash >= 2**31:
        org_hash -= 2**32
    if box_hash >= 2**31:
        box_hash -= 2**32
    return (org_hash, box_hash)


def acquire_box_lock(
    db: Any, organization_id: str, box_id: str,
) -> Tuple[Optional[Any], str]:
    """Try to acquire the per-box advisory lock.

    Returns ``(connection, status)``:

    * ``(conn, "acquired")`` — lock held by this caller on ``conn``;
      caller MUST call :func:`release_box_lock` to free it. The
      connection is checked out of the pool for the lock's lifetime;
      other DB calls on the same engine use independent pool
      connections, so the lock-conn doesn't bottleneck normal work.
    * ``(None, "held")`` — another caller holds the lock; abort and
      let the holder finish.
    * ``(None, "no_infra")`` — pool unavailable (test mock, sqlite
      shim, or a transient pool failure). Caller should fail-open or
      bail per its own correctness model. Locking is a serialisation
      hint, not the only line of defence — the financial-write
      backstops (idempotency keys, optimistic-lock writes) catch the
      highest-stakes duplicate-write hazards even without the lock.
    """
    if not box_id:
        return (None, "no_infra")
    pool = getattr(db, "_pg_pool", None)
    if pool is None:
        return (None, "no_infra")

    conn = None
    try:
        for _attempt in range(3):
            candidate = pool.getconn()
            if not candidate.closed:
                conn = candidate
                break
            try:
                pool.putconn(candidate)
            except Exception:
                try:
                    candidate.close()
                except Exception:
                    pass
        if conn is None:
            logger.warning(
                "[box_lock] could not obtain lock connection for org=%s box=%s",
                organization_id, box_id,
            )
            return (None, "no_infra")

        keys = box_lock_keys(organization_id, box_id)
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s, %s)", keys)
            row = cur.fetchone()
            acquired = bool(row and (
                row[0] if isinstance(row, (list, tuple)) else next(iter(row.values()))
            ))
        conn.commit()
        if acquired:
            return (conn, "acquired")
        try:
            pool.putconn(conn)
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
        return (None, "held")
    except Exception as exc:
        logger.warning(
            "[box_lock] advisory lock acquisition failed for org=%s box=%s: %s",
            organization_id, box_id, exc,
        )
        if conn is not None:
            try:
                pool.putconn(conn)
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
        return (None, "no_infra")


def release_box_lock(
    db: Any, conn: Optional[Any], organization_id: str, box_id: str,
) -> None:
    """Release a per-box advisory lock previously acquired via
    :func:`acquire_box_lock`. Always returns the connection to the
    pool, even if the unlock RPC fails."""
    if conn is None:
        return
    pool = getattr(db, "_pg_pool", None)
    keys = box_lock_keys(organization_id, box_id) if box_id else None
    try:
        if keys is not None:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s, %s)", keys)
            conn.commit()
    except Exception as exc:
        logger.warning(
            "[box_lock] advisory unlock failed for org=%s box=%s: %s",
            organization_id, box_id, exc,
        )
    finally:
        if pool is not None:
            try:
                pool.putconn(conn)
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
