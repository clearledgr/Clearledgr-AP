"""Coverage for the exception graph builder (Sprint 3-B).

Pure-function tests over ``services/exception_graph.build_exception_
graph``. The API endpoint in ``api/box_exceptions_admin.py`` calls
into the builder; the integration test for the endpoint lives in
``test_box_exceptions_admin_api.py`` (already covers auth + tenant
isolation). This file pins the algorithmic shape: nodes /
edges / cause-clustering / weight decay.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from clearledgr.services.exception_graph import (
    build_exception_graph,
)


def _exc(**kwargs):
    """Test factory for an exception row. Sensible defaults; override
    via kwargs."""
    base = {
        "id": "e1",
        "box_id": "ap1",
        "box_type": "ap_item",
        "organization_id": "org-graph-test",
        "exception_type": "match_tolerance_exceeded",
        "severity": "high",
        "reason": "tolerance breach",
        "metadata_json": "{}",
        "raised_at": "2026-05-10T12:00:00Z",
        "raised_by": "agent",
        "raised_actor_type": "agent",
    }
    base.update(kwargs)
    return base


def _ap(**kwargs):
    base = {
        "id": "ap1",
        "organization_id": "org-graph-test",
        "vendor_name": "Acme Logistics",
        "amount": 1500.0,
        "currency": "USD",
        "invoice_number": "INV-001",
        "state": "needs_approval",
        "created_at": "2026-05-09T10:00:00Z",
    }
    base.update(kwargs)
    return base


# ─── Empty / minimal cases ─────────────────────────────────────────


def test_empty_exceptions_returns_empty_graph():
    graph = build_exception_graph(
        exceptions=[], ap_items=[], organization_id="org",
    )
    assert graph["nodes"] == []
    assert graph["edges"] == []
    assert graph["stats"]["exception_count"] == 0


def test_single_exception_emits_three_nodes_and_two_edges():
    """One exception → exception node + ap_item node + vendor node;
    raised_on edge + billed_by edge.
    """
    graph = build_exception_graph(
        exceptions=[_exc()],
        ap_items=[_ap()],
        organization_id="org-graph-test",
    )
    types = {n["type"] for n in graph["nodes"]}
    assert types == {"exception", "ap_item", "vendor"}
    edge_kinds = sorted(e["kind"] for e in graph["edges"])
    assert edge_kinds == ["billed_by", "raised_on"]


def test_exception_node_payload_carries_severity_and_reason():
    graph = build_exception_graph(
        exceptions=[_exc(severity="critical", reason="iban changed")],
        ap_items=[_ap()],
        organization_id="org-graph-test",
    )
    exc_node = next(n for n in graph["nodes"] if n["type"] == "exception")
    assert exc_node["payload"]["severity"] == "critical"
    assert exc_node["payload"]["reason"] == "iban changed"


def test_ap_item_node_label_is_vendor_plus_invoice_number():
    graph = build_exception_graph(
        exceptions=[_exc()],
        ap_items=[_ap(vendor_name="Stripe Inc", invoice_number="INV-99")],
        organization_id="org-graph-test",
    )
    ap_node = next(n for n in graph["nodes"] if n["type"] == "ap_item")
    assert "Stripe" in ap_node["label"]
    assert "INV-99" in ap_node["label"]


def test_missing_ap_record_still_emits_exception_and_ap_nodes():
    """Synthetic vendor-onboarding exceptions can lack a backing
    AP item. Builder must still produce an exception node + an
    ap_item placeholder node + their connecting edge — the vendor
    node is correctly dropped because there's nothing to link.
    """
    graph = build_exception_graph(
        exceptions=[_exc(box_id="ap-orphan")],
        ap_items=[],
        organization_id="org-graph-test",
    )
    types = {n["type"] for n in graph["nodes"]}
    assert types == {"exception", "ap_item"}
    assert all(e["kind"] == "raised_on" for e in graph["edges"])


def test_no_box_id_drops_no_edges():
    """Synthetic onboarding exceptions sometimes have empty box_id.
    Builder emits the exception node alone with no edges out.
    """
    graph = build_exception_graph(
        exceptions=[_exc(box_id="")],
        ap_items=[],
        organization_id="org-graph-test",
    )
    assert len(graph["nodes"]) == 1
    assert graph["nodes"][0]["type"] == "exception"
    assert graph["edges"] == []


# ─── Vendor node deduplication ─────────────────────────────────────


def test_two_ap_items_same_vendor_emit_one_vendor_node():
    """Two AP items billed by the same vendor (after normalization)
    share one vendor node — the whole point of the graph is to
    surface that they're connected.
    """
    graph = build_exception_graph(
        exceptions=[
            _exc(id="e1", box_id="ap1"),
            _exc(id="e2", box_id="ap2"),
        ],
        ap_items=[
            _ap(id="ap1", vendor_name="ACME LOGISTICS"),
            _ap(id="ap2", vendor_name="Acme Logistics, Inc."),
        ],
        organization_id="org-graph-test",
    )
    vendor_nodes = [n for n in graph["nodes"] if n["type"] == "vendor"]
    assert len(vendor_nodes) == 1


def test_two_ap_items_different_vendors_emit_two_vendor_nodes():
    graph = build_exception_graph(
        exceptions=[
            _exc(id="e1", box_id="ap1"),
            _exc(id="e2", box_id="ap2"),
        ],
        ap_items=[
            _ap(id="ap1", vendor_name="Acme Logistics"),
            _ap(id="ap2", vendor_name="Stripe Inc"),
        ],
        organization_id="org-graph-test",
    )
    vendor_nodes = [n for n in graph["nodes"] if n["type"] == "vendor"]
    assert len(vendor_nodes) == 2


# ─── shares_cause_with edges ───────────────────────────────────────


def test_shares_cause_edge_when_same_vendor_and_type_within_window():
    """Two exceptions on the same vendor with the same type within
    the time window connect. This is the load-bearing UX claim —
    operators see "vendor X has 3 exceptions, all match-tolerance,
    all this week" without having to mentally aggregate.
    """
    base = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    graph = build_exception_graph(
        exceptions=[
            _exc(id="e1", box_id="ap1",
                 raised_at=base.isoformat(),
                 exception_type="match_tolerance_exceeded"),
            _exc(id="e2", box_id="ap2",
                 raised_at=(base + timedelta(hours=4)).isoformat(),
                 exception_type="match_tolerance_exceeded"),
        ],
        ap_items=[
            _ap(id="ap1", vendor_name="Acme Logistics"),
            _ap(id="ap2", vendor_name="ACME LOGISTICS"),  # same after normalize
        ],
        organization_id="org-graph-test",
    )
    cause_edges = [e for e in graph["edges"] if e["kind"] == "shares_cause_with"]
    assert len(cause_edges) == 1
    assert 0.9 < cause_edges[0]["weight"] <= 1.0  # ~4 hours of 7 days = high weight


def test_no_shares_cause_when_outside_window():
    base = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    graph = build_exception_graph(
        exceptions=[
            _exc(id="e1", box_id="ap1", raised_at=base.isoformat()),
            _exc(id="e2", box_id="ap2",
                 raised_at=(base + timedelta(days=20)).isoformat()),
        ],
        ap_items=[
            _ap(id="ap1", vendor_name="Acme Logistics"),
            _ap(id="ap2", vendor_name="Acme Logistics"),
        ],
        organization_id="org-graph-test",
    )
    cause_edges = [e for e in graph["edges"] if e["kind"] == "shares_cause_with"]
    assert cause_edges == []


def test_no_shares_cause_when_different_exception_type():
    """Different exception_type means different symptom — don't
    suggest a shared cause even if the vendor matches.
    """
    base = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    graph = build_exception_graph(
        exceptions=[
            _exc(id="e1", box_id="ap1", raised_at=base.isoformat(),
                 exception_type="match_tolerance_exceeded"),
            _exc(id="e2", box_id="ap2",
                 raised_at=(base + timedelta(hours=2)).isoformat(),
                 exception_type="budget_exceeded"),
        ],
        ap_items=[
            _ap(id="ap1", vendor_name="Acme"),
            _ap(id="ap2", vendor_name="Acme"),
        ],
        organization_id="org-graph-test",
    )
    cause_edges = [e for e in graph["edges"] if e["kind"] == "shares_cause_with"]
    assert cause_edges == []


def test_shares_cause_weight_decays_with_time_gap():
    base = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    near = build_exception_graph(
        exceptions=[
            _exc(id="e1", box_id="ap1", raised_at=base.isoformat(),
                 exception_type="match_tolerance_exceeded"),
            _exc(id="e2", box_id="ap2",
                 raised_at=(base + timedelta(hours=1)).isoformat(),
                 exception_type="match_tolerance_exceeded"),
        ],
        ap_items=[
            _ap(id="ap1", vendor_name="Acme"),
            _ap(id="ap2", vendor_name="Acme"),
        ],
        organization_id="org-graph-test",
    )
    far = build_exception_graph(
        exceptions=[
            _exc(id="e1", box_id="ap1", raised_at=base.isoformat(),
                 exception_type="match_tolerance_exceeded"),
            _exc(id="e2", box_id="ap2",
                 raised_at=(base + timedelta(days=6)).isoformat(),
                 exception_type="match_tolerance_exceeded"),
        ],
        ap_items=[
            _ap(id="ap1", vendor_name="Acme"),
            _ap(id="ap2", vendor_name="Acme"),
        ],
        organization_id="org-graph-test",
    )
    near_w = next(e for e in near["edges"] if e["kind"] == "shares_cause_with")["weight"]
    far_w = next(e for e in far["edges"] if e["kind"] == "shares_cause_with")["weight"]
    assert near_w > far_w


def test_three_same_cause_exceptions_form_one_cluster():
    base = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    graph = build_exception_graph(
        exceptions=[
            _exc(id="e1", box_id="ap1", raised_at=base.isoformat(),
                 exception_type="match_tolerance_exceeded"),
            _exc(id="e2", box_id="ap2",
                 raised_at=(base + timedelta(hours=1)).isoformat(),
                 exception_type="match_tolerance_exceeded"),
            _exc(id="e3", box_id="ap3",
                 raised_at=(base + timedelta(hours=2)).isoformat(),
                 exception_type="match_tolerance_exceeded"),
        ],
        ap_items=[
            _ap(id="ap1", vendor_name="Acme"),
            _ap(id="ap2", vendor_name="Acme"),
            _ap(id="ap3", vendor_name="Acme"),
        ],
        organization_id="org-graph-test",
    )
    cause_edges = [e for e in graph["edges"] if e["kind"] == "shares_cause_with"]
    # Triangle: 3 pairs of same-vendor + same-type within window.
    assert len(cause_edges) == 3
    assert graph["stats"]["cause_cluster_count"] == 1


def test_two_independent_cause_clusters_count_separately():
    base = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    graph = build_exception_graph(
        exceptions=[
            _exc(id="e1", box_id="ap1", raised_at=base.isoformat(),
                 exception_type="match_tolerance_exceeded"),
            _exc(id="e2", box_id="ap2",
                 raised_at=(base + timedelta(hours=1)).isoformat(),
                 exception_type="match_tolerance_exceeded"),
            # Different vendor; separate cluster.
            _exc(id="e3", box_id="ap3", raised_at=base.isoformat(),
                 exception_type="budget_exceeded"),
            _exc(id="e4", box_id="ap4",
                 raised_at=(base + timedelta(hours=1)).isoformat(),
                 exception_type="budget_exceeded"),
        ],
        ap_items=[
            _ap(id="ap1", vendor_name="Acme"),
            _ap(id="ap2", vendor_name="Acme"),
            _ap(id="ap3", vendor_name="Stripe"),
            _ap(id="ap4", vendor_name="Stripe"),
        ],
        organization_id="org-graph-test",
    )
    assert graph["stats"]["cause_cluster_count"] == 2


# ─── Stats ─────────────────────────────────────────────────────────


def test_stats_counts_severity_correctly():
    graph = build_exception_graph(
        exceptions=[
            _exc(id="e1", severity="critical"),
            _exc(id="e2", severity="high", box_id="ap2"),
            _exc(id="e3", severity="high", box_id="ap3"),
            _exc(id="e4", severity="medium", box_id="ap4"),
        ],
        ap_items=[
            _ap(id="ap1"),
            _ap(id="ap2"),
            _ap(id="ap3"),
            _ap(id="ap4"),
        ],
        organization_id="org-graph-test",
    )
    assert graph["stats"]["exception_count"] == 4
    assert graph["stats"]["by_severity"]["critical"] == 1
    assert graph["stats"]["by_severity"]["high"] == 2
    assert graph["stats"]["by_severity"]["medium"] == 1
    assert graph["stats"]["by_severity"]["low"] == 0


def test_node_count_includes_all_three_node_types():
    graph = build_exception_graph(
        exceptions=[
            _exc(id="e1", box_id="ap1"),
            _exc(id="e2", box_id="ap2"),
        ],
        ap_items=[
            _ap(id="ap1", vendor_name="Acme"),
            _ap(id="ap2", vendor_name="Stripe"),
        ],
        organization_id="org-graph-test",
    )
    # 2 exceptions + 2 ap_items + 2 vendors = 6
    assert graph["stats"]["node_count"] == 6


# ─── Stable id generation ──────────────────────────────────────────


def test_node_ids_are_typed_and_stable():
    """Node ids carry a type prefix so the frontend can route clicks
    to the right click-through panel without re-classifying. The ids
    must be stable across rebuilds for the same input.
    """
    graph_a = build_exception_graph(
        exceptions=[_exc(id="abc")],
        ap_items=[_ap(id="ap1", vendor_name="Stripe")],
        organization_id="org-graph-test",
    )
    graph_b = build_exception_graph(
        exceptions=[_exc(id="abc")],
        ap_items=[_ap(id="ap1", vendor_name="Stripe")],
        organization_id="org-graph-test",
    )
    assert sorted(n["id"] for n in graph_a["nodes"]) == sorted(n["id"] for n in graph_b["nodes"])
    types = {n["id"].split("_", 1)[0] for n in graph_a["nodes"]}
    assert types == {"exc", "ap", "vendor"}
