"""Coverage for branchable AP policy (Sprint 2).

End-to-end tests against the real Postgres test DB so the new
migration v81 (branch_id column + policy_branches table + partial
unique index) gets exercised on every run.

Flow per test:
  1. Seed an org via ``ensure_organization``.
  2. Create an active main version via ``set_policy``.
  3. Open a branch, commit, diff, replay or merge or abandon.
  4. Assert the resulting state.

Tests intentionally use ``"org-branch-test"`` style ids — never the
literal ``"org-test"`` / ``"_unprovisioned"`` sentinels (see M19/M20
tenancy walls).
"""
from __future__ import annotations

import json

import pytest

from solden.core import database as db_module
from solden.services.policy_service import (
    PolicyBranchNotFound,
    PolicyService,
    PolicyVersionNotFound,
)


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    return inst


@pytest.fixture()
def svc(db):
    org_id = "org-branch-test"
    inst = db_module.get_db()
    inst.ensure_organization(org_id, organization_name="Branch Test Co")
    return PolicyService(organization_id=org_id)


@pytest.fixture()
def base_main_version(svc):
    """Create an initial active version on main so branches have
    something to fork from. Approval-thresholds shape so we can
    exercise the linter / replay paths in follow-up tests if needed.
    """
    return svc.set_policy(
        kind="approval_thresholds",
        content={
            "thresholds": [
                {"label": "low", "min_amount": 0, "max_amount": 1000,
                 "approvers": ["ap@x.com"]},
                {"label": "high", "min_amount": 1000, "max_amount": None,
                 "approvers": ["cfo@x.com"]},
            ],
        },
        actor="seed",
        description="initial active main version",
    )


# ─── create / list ─────────────────────────────────────────────────


def test_create_branch_off_active_main_when_no_base_supplied(svc, base_main_version):
    branch = svc.create_branch(
        "approval_thresholds",
        name="raise-thresholds-q3",
        actor="cfo@x.com",
    )
    assert branch.status == "open"
    assert branch.base_version_id == base_main_version.id
    assert branch.head_version_id == base_main_version.id  # no commits yet
    assert branch.policy_kind == "approval_thresholds"
    assert branch.organization_id == "org-branch-test"


def test_create_branch_off_specific_version(svc, base_main_version):
    # Add a second main version so there's a version newer than base.
    svc.set_policy(
        kind="approval_thresholds",
        content={"thresholds": [{"label": "tier1", "min_amount": 0,
                                  "max_amount": None, "approvers": ["x@y.com"]}]},
        actor="seed",
    )
    branch = svc.create_branch(
        "approval_thresholds",
        name="off-base-explicit",
        actor="cfo@x.com",
        base_version_id=base_main_version.id,
    )
    assert branch.base_version_id == base_main_version.id
    assert branch.head_version_id == base_main_version.id


def test_create_branch_rejects_reserved_main_name(svc, base_main_version):
    with pytest.raises(ValueError, match="reserved"):
        svc.create_branch("approval_thresholds", name="main", actor="x")


def test_create_branch_rejects_empty_name(svc, base_main_version):
    with pytest.raises(ValueError, match="required"):
        svc.create_branch("approval_thresholds", name="   ", actor="x")


def test_create_branch_unique_name_for_open_branches(svc, base_main_version):
    svc.create_branch("approval_thresholds", name="dup", actor="x")
    # Opening a second branch with the same name + status should fail
    # via the partial unique index in v81. The exception type comes
    # from psycopg; we catch broadly to keep the test driver-agnostic.
    with pytest.raises(Exception):
        svc.create_branch("approval_thresholds", name="dup", actor="y")


def test_list_branches_filtered_by_status(svc, base_main_version):
    open_b = svc.create_branch("approval_thresholds", name="open-1", actor="x")
    abandoned_b = svc.create_branch("approval_thresholds", name="to-abandon", actor="x")
    svc.abandon_branch(abandoned_b.id, actor="x")

    open_only = svc.list_branches(kind="approval_thresholds", status="open")
    open_ids = {b.id for b in open_only}
    assert open_b.id in open_ids
    assert abandoned_b.id not in open_ids

    abandoned = svc.list_branches(kind="approval_thresholds", status="abandoned")
    assert any(b.id == abandoned_b.id for b in abandoned)


# ─── commit ────────────────────────────────────────────────────────


def test_commit_to_branch_advances_head_and_tags_branch_id(svc, base_main_version):
    branch = svc.create_branch("approval_thresholds", name="raise-q3", actor="x")

    new_content = {
        "thresholds": [
            {"label": "low", "min_amount": 0, "max_amount": 5000,  # raised
             "approvers": ["ap@x.com"]},
            {"label": "high", "min_amount": 5000, "max_amount": None,
             "approvers": ["cfo@x.com"]},
        ],
    }
    new_version = svc.commit_to_branch(
        branch.id, new_content, actor="cfo@x.com",
        description="raise low ceiling for Q3 testing",
    )
    assert new_version.branch_id == branch.id
    assert new_version.parent_version_id == base_main_version.id

    # Branch head pointer advanced.
    refreshed = svc.get_branch(branch.id)
    assert refreshed.head_version_id == new_version.id
    assert refreshed.base_version_id == base_main_version.id  # base immutable


def test_commit_idempotent_on_same_content(svc, base_main_version):
    branch = svc.create_branch("approval_thresholds", name="noop-test", actor="x")
    same_content = base_main_version.content
    result = svc.commit_to_branch(branch.id, same_content, actor="x")
    # Same content hash → returns the existing head (which is the base
    # version, since this is the branch's first commit).
    assert result.id == base_main_version.id

    # Branch head should still point at base.
    refreshed = svc.get_branch(branch.id)
    assert refreshed.head_version_id == base_main_version.id


def test_commit_rejected_on_closed_branch(svc, base_main_version):
    branch = svc.create_branch("approval_thresholds", name="closed-test", actor="x")
    svc.abandon_branch(branch.id, actor="x")
    with pytest.raises(ValueError, match="status='abandoned'"):
        svc.commit_to_branch(branch.id, {"thresholds": []}, actor="x")


def test_branch_versions_do_not_become_active_main(svc, base_main_version):
    """Critical invariant: committing to a branch must NOT shift
    ``get_active`` to the branch's head. Production routing keeps
    reading main until merge.
    """
    branch = svc.create_branch("approval_thresholds", name="experiment", actor="x")
    svc.commit_to_branch(
        branch.id,
        {"thresholds": [{"label": "experimental", "min_amount": 0,
                         "max_amount": None, "approvers": ["x@y.com"]}]},
        actor="x",
    )
    # ``get_active`` should still return the original main version.
    active = svc.get_active("approval_thresholds")
    assert active.id == base_main_version.id
    assert active.branch_id is None


# ─── diff ──────────────────────────────────────────────────────────


def test_diff_shows_branch_vs_main(svc, base_main_version):
    branch = svc.create_branch("approval_thresholds", name="diff-test", actor="x")
    new_content = {
        "thresholds": [
            {"label": "low", "min_amount": 0, "max_amount": 9999,
             "approvers": ["ap@x.com"]},
        ],
    }
    svc.commit_to_branch(branch.id, new_content, actor="x")
    diff = svc.diff_branch(branch.id)
    assert diff["changed"] is True
    assert diff["main_version_id"] == base_main_version.id
    assert diff["branch_head_content"] == new_content


def test_diff_unchanged_when_branch_head_matches_main(svc, base_main_version):
    branch = svc.create_branch("approval_thresholds", name="noop-diff", actor="x")
    diff = svc.diff_branch(branch.id)
    assert diff["changed"] is False


# ─── merge ─────────────────────────────────────────────────────────


def test_merge_branch_creates_new_main_version_and_marks_branch_merged(svc, base_main_version):
    branch = svc.create_branch("approval_thresholds", name="merge-me", actor="x")
    new_content = {
        "thresholds": [
            {"label": "low", "min_amount": 0, "max_amount": 2500,
             "approvers": ["ap@x.com"]},
            {"label": "high", "min_amount": 2500, "max_amount": None,
             "approvers": ["cfo@x.com"]},
        ],
    }
    svc.commit_to_branch(branch.id, new_content, actor="cfo@x.com")

    merged_version = svc.merge_branch(branch.id, actor="cfo@x.com",
                                       description="approved Q3 thresholds")
    # New version sits on main (no branch_id), parents to old main head.
    assert merged_version.branch_id is None
    assert merged_version.parent_version_id == base_main_version.id
    assert merged_version.content == new_content

    # Active main has shifted to the merged version.
    active = svc.get_active("approval_thresholds")
    assert active.id == merged_version.id
    assert active.content_hash == merged_version.content_hash

    # Branch is closed with merged metadata.
    branch_after = svc.get_branch(branch.id)
    assert branch_after.status == "merged"
    assert branch_after.merged_into_version_id == merged_version.id
    assert branch_after.merged_by == "cfo@x.com"
    assert branch_after.merged_at


def test_merge_noop_closes_branch_without_new_version(svc, base_main_version):
    """If a branch's head content matches current main, merging
    closes the branch but doesn't inflate the version chain.
    """
    branch = svc.create_branch("approval_thresholds", name="noop-merge", actor="x")
    result = svc.merge_branch(branch.id, actor="cfo@x.com")
    # No new version — returns existing main active.
    assert result.id == base_main_version.id

    branch_after = svc.get_branch(branch.id)
    assert branch_after.status == "merged"
    assert branch_after.merged_into_version_id == base_main_version.id


def test_merge_rejected_on_closed_branch(svc, base_main_version):
    branch = svc.create_branch("approval_thresholds", name="merge-closed", actor="x")
    svc.abandon_branch(branch.id, actor="x")
    with pytest.raises(ValueError, match="status='abandoned'"):
        svc.merge_branch(branch.id, actor="x")


# ─── abandon ───────────────────────────────────────────────────────


def test_abandon_branch_keeps_versions_in_audit_trail(svc, base_main_version):
    branch = svc.create_branch("approval_thresholds", name="experiment-failed", actor="x")
    new_version = svc.commit_to_branch(
        branch.id,
        {"thresholds": [{"label": "x", "min_amount": 0, "max_amount": None,
                         "approvers": ["a@b.com"]}]},
        actor="x",
    )
    abandoned = svc.abandon_branch(branch.id, actor="x")
    assert abandoned.status == "abandoned"
    assert abandoned.abandoned_by == "x"
    assert abandoned.abandoned_at

    # The branch's commits are still queryable for audit purposes.
    fetched_version = svc.get_version(new_version.id)
    assert fetched_version.branch_id == branch.id


def test_abandon_then_create_with_same_name_succeeds(svc, base_main_version):
    """Reusing a branch name after abandoning should work — the
    partial unique index only enforces uniqueness for ``open`` rows.
    """
    first = svc.create_branch("approval_thresholds", name="reused", actor="x")
    svc.abandon_branch(first.id, actor="x")
    second = svc.create_branch("approval_thresholds", name="reused", actor="y")
    assert second.id != first.id
    assert second.status == "open"


# ─── error paths ───────────────────────────────────────────────────


def test_get_branch_missing_raises(svc):
    with pytest.raises(PolicyBranchNotFound):
        svc.get_branch("PB-does-not-exist")


def test_create_branch_with_bad_base_version_raises(svc, base_main_version):
    with pytest.raises(PolicyVersionNotFound):
        svc.create_branch(
            "approval_thresholds", name="bad-base", actor="x",
            base_version_id="PV-not-real",
        )


def test_create_branch_rejects_kind_mismatch(svc, base_main_version):
    """Forking an ``approval_thresholds`` branch from a
    ``confidence_gate`` version should fail — branches are
    kind-scoped.
    """
    cg_version = svc.set_policy(
        kind="confidence_gate",
        content={"critical_field_confidence_threshold": 0.9},
        actor="seed",
    )
    with pytest.raises(ValueError, match="for kind"):
        svc.create_branch(
            "approval_thresholds", name="cross-kind", actor="x",
            base_version_id=cg_version.id,
        )


# ─── tenant isolation ──────────────────────────────────────────────


def test_branch_isolated_per_org(db):
    """Two orgs each open a branch with the same name — should not
    collide thanks to the partial unique index being scoped to
    ``(organization_id, policy_kind, name)``.
    """
    db.ensure_organization("org-iso-a", organization_name="A")
    db.ensure_organization("org-iso-b", organization_name="B")
    a = PolicyService(organization_id="org-iso-a")
    b = PolicyService(organization_id="org-iso-b")
    a.set_policy(kind="approval_thresholds",
                 content={"thresholds": [{"label": "x", "min_amount": 0,
                                          "max_amount": None, "approvers": ["q@x.com"]}]},
                 actor="seed")
    b.set_policy(kind="approval_thresholds",
                 content={"thresholds": [{"label": "x", "min_amount": 0,
                                          "max_amount": None, "approvers": ["q@y.com"]}]},
                 actor="seed")

    a_branch = a.create_branch("approval_thresholds", name="shared-name", actor="actor-a")
    b_branch = b.create_branch("approval_thresholds", name="shared-name", actor="actor-b")
    assert a_branch.id != b_branch.id

    # Cross-org get_branch must fail.
    with pytest.raises(PolicyBranchNotFound):
        a.get_branch(b_branch.id)
