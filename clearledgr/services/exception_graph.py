"""Exception graph for the operator UI.

Sprint 3-B (ModernRelay-inspired roadmap, item #5). Where the
flat exception queue (``api/box_exceptions_admin.py:list_exceptions``)
shows exceptions as a list, the graph view renders them as nodes
+ edges so an operator can see at a glance:

* Vendor X has 3 exceptions across 3 invoices, all this week,
  all match-tolerance failures → likely a vendor-side data drift
  (one cause, three symptoms).
* AP item Y has exceptions of two different types (vendor-master
  miss + budget overrun) → joint cause needs investigation.
* The exception cluster around a recently-onboarded vendor
  suggests onboarding gaps that produce downstream exceptions.

Data shape:

* **Nodes**: exception rows + AP items they're attached to + vendor
  profiles for those AP items + organizations / pipelines (when
  meaningful for the corpus). Each node has a ``type``, ``id``,
  ``label``, and a typed ``payload``.
* **Edges**: ``exception → ap_item`` (the exception lives ON the
  AP item), ``ap_item → vendor`` (linked by vendor_name normalized),
  ``exception → exception`` (same vendor + same type within a
  rolling window suggests a shared cause).

Pure-function over data fetched from existing stores; the API
endpoint in ``api/box_exceptions_admin.py`` calls into here.
Tenant-scoped — every node has ``organization_id`` and the caller
gates via ``require_org`` before invoking.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from clearledgr.services.fuzzy_matching import normalize_vendor


# ─── Node + edge model ─────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class GraphNode:
    """One node in the exception graph.

    ``id`` is unique within the graph payload (typed prefix:
    ``exc_<id>``, ``ap_<id>``, ``vendor_<normalized_name>``). ``type``
    matches the prefix without the underscore. ``label`` is what the
    UI renders on the node; ``payload`` carries the structured fields
    a click-through panel needs (severity, amount, raised_at, etc.).
    """
    id: str
    type: str  # 'exception' | 'ap_item' | 'vendor'
    label: str
    payload: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class GraphEdge:
    """Directed edge between two nodes.

    ``kind`` describes the relationship: ``raised_on`` (exception →
    ap_item), ``billed_by`` (ap_item → vendor), ``shares_cause_with``
    (exception → exception, derived from same-vendor + same-type
    within a window).
    ``weight`` is a UI-rendering hint — thicker for stronger
    relationships (a direct ``raised_on`` is 1.0; ``shares_cause_with``
    is decayed by time gap between exceptions).
    """
    source: str
    target: str
    kind: str
    weight: float
    payload: Dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


# Time window for inferring "same cause" across exceptions on the
# same vendor. 7 days catches the common case (a vendor changes
# their bank details and three invoices in the same week trip the
# match-tolerance gate) without merging unrelated exceptions
# spread across months.
_SAME_CAUSE_WINDOW_DAYS = 7


def build_exception_graph(
    *,
    exceptions: Sequence[Dict[str, Any]],
    ap_items: Sequence[Dict[str, Any]],
    organization_id: str,
    same_cause_window_days: int = _SAME_CAUSE_WINDOW_DAYS,
) -> Dict[str, Any]:
    """Build the graph payload for one tenant's unresolved exceptions.

    Returns a dict with ``nodes`` and ``edges`` keys, ready to JSON-
    encode for the frontend. Caller is responsible for tenant-
    scoping the inputs (every row in ``exceptions`` and ``ap_items``
    must belong to ``organization_id``).

    Algorithm:

    1. For each exception, emit an ``exception`` node + an edge to
       the AP item it's raised on.
    2. For each unique AP item referenced by exceptions, emit an
       ``ap_item`` node + an edge to its vendor.
    3. For each unique vendor (normalized name), emit a ``vendor``
       node.
    4. For exceptions sharing the same vendor + exception_type
       within ``same_cause_window_days``, emit
       ``shares_cause_with`` edges (decayed weight by time gap).

    Empty inputs return ``{"nodes": [], "edges": []}``.
    """
    if not exceptions:
        return {"nodes": [], "edges": [],
                "organization_id": organization_id,
                "stats": _empty_stats()}

    ap_by_id = {str(item["id"]): dict(item) for item in ap_items if item.get("id")}

    nodes: Dict[str, GraphNode] = {}
    edges: List[GraphEdge] = []

    for exc in exceptions:
        exc_node = _exception_to_node(exc)
        nodes[exc_node.id] = exc_node

        ap_id = str(exc.get("box_id") or "").strip()
        if not ap_id:
            continue
        ap_record = ap_by_id.get(ap_id)
        ap_node = _ap_item_to_node(ap_id, ap_record)
        nodes.setdefault(ap_node.id, ap_node)

        edges.append(GraphEdge(
            source=exc_node.id,
            target=ap_node.id,
            kind="raised_on",
            weight=1.0,
            payload={"exception_type": exc.get("exception_type"),
                     "severity": exc.get("severity")},
        ))

        # Vendor edge — only if the AP item carries a vendor name.
        if ap_record:
            vendor_name = str(ap_record.get("vendor_name") or "").strip()
            if vendor_name:
                vendor_node = _vendor_to_node(vendor_name)
                nodes.setdefault(vendor_node.id, vendor_node)
                edges.append(GraphEdge(
                    source=ap_node.id,
                    target=vendor_node.id,
                    kind="billed_by",
                    weight=1.0,
                    payload={"vendor_name": vendor_name},
                ))

    # Same-cause edges between exceptions: same vendor + same type
    # within the window. The graph is undirected at the UI level
    # but we model both directions explicitly so the rendering layer
    # doesn't need to deduplicate.
    cause_edges = _infer_shares_cause_edges(
        exceptions, ap_by_id, same_cause_window_days,
    )
    edges.extend(cause_edges)

    return {
        "organization_id": organization_id,
        "nodes": [n.to_dict() for n in nodes.values()],
        "edges": [e.to_dict() for e in edges],
        "stats": _stats(exceptions, edges, nodes),
    }


# ─── Node constructors ─────────────────────────────────────────────


def _exception_to_node(exc: Dict[str, Any]) -> GraphNode:
    exc_id = str(exc.get("id") or "")
    severity = str(exc.get("severity") or "medium")
    exc_type = str(exc.get("exception_type") or "unknown")
    return GraphNode(
        id=f"exc_{exc_id}",
        type="exception",
        label=_humanize_exception_type(exc_type),
        payload={
            "exception_id": exc_id,
            "exception_type": exc_type,
            "severity": severity,
            "reason": exc.get("reason") or "",
            "raised_at": exc.get("raised_at"),
            "raised_by": exc.get("raised_by"),
            "box_id": exc.get("box_id"),
            "box_type": exc.get("box_type"),
            "metadata": _decode_metadata(exc.get("metadata_json") or exc.get("metadata") or {}),
        },
    )


def _ap_item_to_node(ap_id: str, record: Optional[Dict[str, Any]]) -> GraphNode:
    payload: Dict[str, Any] = {"ap_item_id": ap_id}
    label = ap_id
    if record:
        payload.update({
            "vendor_name": record.get("vendor_name"),
            "amount": _coerce_float(record.get("amount")),
            "currency": record.get("currency"),
            "invoice_number": record.get("invoice_number"),
            "state": record.get("state"),
            "received_at": record.get("created_at") or record.get("received_at"),
        })
        invoice_no = str(record.get("invoice_number") or "").strip()
        vendor = str(record.get("vendor_name") or "").strip()
        if vendor and invoice_no:
            label = f"{vendor} · {invoice_no}"
        elif vendor:
            label = vendor
        elif invoice_no:
            label = invoice_no
    return GraphNode(
        id=f"ap_{ap_id}",
        type="ap_item",
        label=label,
        payload=payload,
    )


def _vendor_to_node(vendor_name: str) -> GraphNode:
    normalized = normalize_vendor(vendor_name) or vendor_name.lower()
    return GraphNode(
        id=f"vendor_{normalized}",
        type="vendor",
        label=vendor_name,
        payload={
            "vendor_name": vendor_name,
            "normalized_name": normalized,
        },
    )


# ─── Same-cause inference ──────────────────────────────────────────


def _infer_shares_cause_edges(
    exceptions: Sequence[Dict[str, Any]],
    ap_by_id: Dict[str, Dict[str, Any]],
    window_days: int,
) -> List[GraphEdge]:
    """Connect exceptions that look like symptoms of one cause.

    Heuristic: same vendor (via the AP item's vendor_name normalized)
    AND same ``exception_type`` AND raised within ``window_days``
    of each other. Weight decays linearly with time gap so
    exceptions hours apart connect strongly and ones a week apart
    connect weakly.

    Conservative on purpose — false-positive edges look like
    over-eager visualization noise. If the heuristic isn't strong
    enough to suggest an investigation, no edge.
    """
    if window_days <= 0 or len(exceptions) < 2:
        return []

    # Bucket by (normalized_vendor, exception_type). Each bucket
    # internally connects all pairs within the window.
    buckets: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for exc in exceptions:
        ap_id = str(exc.get("box_id") or "").strip()
        ap_record = ap_by_id.get(ap_id)
        if not ap_record:
            continue
        vendor_norm = normalize_vendor(str(ap_record.get("vendor_name") or ""))
        if not vendor_norm:
            continue
        exc_type = str(exc.get("exception_type") or "")
        key = (vendor_norm, exc_type)
        buckets.setdefault(key, []).append(exc)

    edges: List[GraphEdge] = []
    window = timedelta(days=window_days)
    for (vendor_norm, exc_type), members in buckets.items():
        if len(members) < 2:
            continue
        # Pairwise within the window — N² is fine; per-vendor +
        # per-type buckets stay small.
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                ts_a = _parse_iso(a.get("raised_at"))
                ts_b = _parse_iso(b.get("raised_at"))
                if ts_a is None or ts_b is None:
                    continue
                gap = abs(ts_a - ts_b)
                if gap > window:
                    continue
                # Linear weight decay: 1.0 at gap=0, 0.0 at gap=window.
                weight = max(0.0, 1.0 - gap.total_seconds() / window.total_seconds())
                if weight <= 0.0:
                    continue
                edges.append(GraphEdge(
                    source=f"exc_{a.get('id')}",
                    target=f"exc_{b.get('id')}",
                    kind="shares_cause_with",
                    weight=round(weight, 3),
                    payload={
                        "shared_vendor": vendor_norm,
                        "shared_type": exc_type,
                        "gap_hours": round(gap.total_seconds() / 3600, 1),
                    },
                ))
    return edges


# ─── Stats + helpers ───────────────────────────────────────────────


def _stats(
    exceptions: Sequence[Dict[str, Any]],
    edges: Sequence[GraphEdge],
    nodes: Dict[str, GraphNode],
) -> Dict[str, Any]:
    by_severity: Dict[str, int] = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    for exc in exceptions:
        sev = str(exc.get("severity") or "medium")
        by_severity[sev] = by_severity.get(sev, 0) + 1

    cause_clusters = _count_cause_clusters(edges)
    return {
        "exception_count": len(exceptions),
        "by_severity": by_severity,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "cause_cluster_count": cause_clusters,
    }


def _empty_stats() -> Dict[str, Any]:
    return {
        "exception_count": 0,
        "by_severity": {"low": 0, "medium": 0, "high": 0, "critical": 0},
        "node_count": 0,
        "edge_count": 0,
        "cause_cluster_count": 0,
    }


def _count_cause_clusters(edges: Iterable[GraphEdge]) -> int:
    """How many same-cause groupings the graph contains.

    A cluster is a connected component over ``shares_cause_with``
    edges — same union-find pattern as
    ``vendor_dedup.detect_duplicates_via_rrf`` but scoped to the
    cause-edge subgraph.
    """
    edges_list = [e for e in edges if e.kind == "shares_cause_with"]
    if not edges_list:
        return 0
    parent: Dict[str, str] = {}

    def _find(x: str) -> str:
        if parent.setdefault(x, x) != x:
            parent[x] = _find(parent[x])
        return parent[x]

    def _union(a: str, b: str) -> None:
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[ra] = rb

    for edge in edges_list:
        _union(edge.source, edge.target)

    # Count distinct roots that have >= 2 members.
    members_by_root: Dict[str, int] = {}
    for node in list(parent.keys()):
        root = _find(node)
        members_by_root[root] = members_by_root.get(root, 0) + 1
    return sum(1 for count in members_by_root.values() if count >= 2)


def _humanize_exception_type(exc_type: str) -> str:
    """Render snake_case exception_type → Title Case for the UI.
    Keeps shipped ``exception_type`` strings in audit logs intact.
    """
    if not exc_type:
        return "Exception"
    return " ".join(word.capitalize() for word in exc_type.replace("-", "_").split("_"))


def _decode_metadata(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _coerce_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
