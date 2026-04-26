"""One-shot: create or reset a known admin user with email + password.

Use when Google OAuth is broken and you need to sign in immediately
via the SPA's email/password form. Idempotent — safe to re-run.

Usage:
    railway run --service api python scripts/seed_admin_user.py \
        EMAIL PASSWORD [ORG_ID]
"""
import sys

from clearledgr.core.auth import (
    ROLE_OWNER,
    create_user,
    get_user_by_email,
    hash_password,
)
from clearledgr.core.database import get_db


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: seed_admin_user.py EMAIL PASSWORD [ORG_ID]")
        return 2
    email = sys.argv[1].strip().lower()
    password = sys.argv[2]
    org_id = (sys.argv[3].strip() if len(sys.argv) > 3 else "default") or "default"

    db = get_db()
    db.initialize()
    # Ensure the org row exists; otherwise create_user/foreign-key paths
    # downstream complain.
    if hasattr(db, "ensure_organization"):
        db.ensure_organization(
            organization_id=org_id,
            organization_name=org_id.replace("-", " ").replace("_", " ").title(),
        )

    existing = get_user_by_email(email)
    if existing is None:
        user = create_user(
            email=email,
            password=password,
            name=email.split("@")[0].replace(".", " ").title(),
            organization_id=org_id,
            role=ROLE_OWNER,
        )
        print(f"created user id={user.id} email={user.email} role={user.role} org={user.organization_id}")
        return 0

    # User already exists — reset password + ensure owner role + active.
    db.update_user(
        existing.id,
        password_hash=hash_password(password),
        role=ROLE_OWNER,
        organization_id=org_id,
        is_active=True,
    )
    refreshed = get_user_by_email(email)
    print(
        f"updated user id={refreshed.id} email={refreshed.email} "
        f"role={refreshed.role} org={refreshed.organization_id} "
        f"(password reset)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
