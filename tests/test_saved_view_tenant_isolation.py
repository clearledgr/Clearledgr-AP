"""delete_saved_view must be org-scoped.

Previously DELETE keyed on view_id only (no org filter), so an ops user in
one tenant could delete another tenant's saved view by id. The delete now
requires the owning organization_id.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from solden.core import database as db_module  # noqa: E402


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgA", organization_name="Acme")
    inst.ensure_organization("orgB", organization_name="Beta")
    return inst


def test_delete_saved_view_is_org_scoped(db):
    view = db.create_saved_view(
        organization_id="orgA", pipeline_id="p-test", name="Mine",
        filter_json={}, sort_json={}, show_in_inbox=False, created_by="u@orgA",
    )
    vid = view["id"]

    # orgB must NOT be able to delete orgA's view.
    assert db.delete_saved_view(vid, "orgB") is False
    assert db.get_saved_view(vid) is not None  # still present

    # orgA can delete its own.
    assert db.delete_saved_view(vid, "orgA") is True
    assert db.get_saved_view(vid) is None
