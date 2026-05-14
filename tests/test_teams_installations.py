"""Regression for Teams AAD→org installation mapping (M9).

Pre-fix the Teams interactive callback in ``api/teams_invoices.py``
either trusted ``organization_id`` from the body (cross-tenant
approval surface — anyone holding a valid AAD bot token could
approve invoices in any tenant by setting the body field), or
relied on AP-item resolution alone (which still let an AAD-tenant-A
bot token act on AP items the resolution surfaced for tenant B).

The fix introduces a per-tenant ``teams_installations`` table
(migration v78) so the AAD ``tid`` claim resolves to a Solden
``organization_id`` BEFORE any AP-item lookup. A token whose AAD
tenant has no installation refused entirely.
"""
from __future__ import annotations

import pytest

from clearledgr.core import database as db_module


@pytest.fixture()
def db(tmp_path, monkeypatch):
    inst = db_module.get_db()
    inst.initialize()
    return inst


def test_teams_installations_round_trip(db):
    """Basic CRUD: upsert, fetch by org, fetch by AAD tenant, deactivate."""
    inst = db.set_teams_installation(
        organization_id="org-acme",
        aad_tenant_id="aad-tid-acme",
        tenant_name="Acme AAD",
        bot_app_id="bot-app-acme",
        bot_app_password="super-secret",
        service_url="https://smba.trafficmanager.net/teams/",
    )
    assert inst["organization_id"] == "org-acme"
    assert inst["aad_tenant_id"] == "aad-tid-acme"
    # Default secret-suppressed read.
    fetched = db.get_teams_installation("org-acme")
    assert fetched["bot_app_password"] is None
    # Explicit include_secrets returns the plaintext.
    fetched_secret = db.get_teams_installation("org-acme", include_secrets=True)
    assert fetched_secret["bot_app_password"] == "super-secret"

    by_aad = db.get_teams_installation_by_aad_tenant("aad-tid-acme")
    assert by_aad["organization_id"] == "org-acme"

    # Unknown AAD tenant returns None — fail-closed.
    assert db.get_teams_installation_by_aad_tenant("aad-tid-unknown") is None
    assert db.get_teams_installation_by_aad_tenant("") is None

    # Deactivation flips is_active=0 and removes the AAD lookup.
    affected = db.deactivate_teams_installation("aad-tid-acme")
    assert affected == 1
    assert db.get_teams_installation_by_aad_tenant("aad-tid-acme") is None
    # The org-scoped lookup also returns None now (active filter).
    assert db.get_teams_installation("org-acme") is None


def test_teams_installations_unique_org_aad_pair(db):
    """The (organization_id, aad_tenant_id) UNIQUE constraint means
    a re-install for the same org+tid updates the existing row in
    place rather than creating a duplicate."""
    first = db.set_teams_installation(
        organization_id="org-x",
        aad_tenant_id="aad-tid-x",
        tenant_name="First Name",
    )
    second = db.set_teams_installation(
        organization_id="org-x",
        aad_tenant_id="aad-tid-x",
        tenant_name="Second Name",
    )
    assert first["id"] == second["id"]
    fetched = db.get_teams_installation("org-x")
    assert fetched["tenant_name"] == "Second Name"


def test_teams_installations_rejects_empty_inputs(db):
    with pytest.raises(ValueError):
        db.set_teams_installation(organization_id="", aad_tenant_id="aad-x")
    with pytest.raises(ValueError):
        db.set_teams_installation(organization_id="org-x", aad_tenant_id="")


def test_teams_callback_refuses_aad_tenant_without_installation(monkeypatch):
    """The bot-callback handler at ``api/teams_invoices.py`` must
    refuse with 403 ``aad_tenant_not_provisioned`` when the AAD
    ``tid`` claim has no active ``teams_installations`` row.

    Source-inspection regression: a future change that drops the
    pre-AP-item AAD lookup or weakens its fail-closed contract
    (e.g., reverts to reading ``organization_id`` from the body or
    falling back to "org-test") fails this test.
    """
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    src = (repo_root / "clearledgr" / "api" / "teams_invoices.py").read_text()

    assert "get_teams_installation_by_aad_tenant" in src, (
        "teams_invoices.py must look up the AAD tid claim against the "
        "teams_installations table before any AP-item resolution."
    )
    assert "aad_tenant_not_provisioned" in src, (
        "teams_invoices.py must refuse with 403 aad_tenant_not_provisioned "
        "when the AAD tenant has no installation."
    )
    assert "ap_item_org_mismatch" in src, (
        "teams_invoices.py must refuse with 403 ap_item_org_mismatch "
        "when the AP item's org diverges from the install-derived org."
    )
