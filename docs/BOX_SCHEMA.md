# Box schema — the portable workflow record

> "We believe in protocols, not platforms."
> — *The Back Office Runtime Manifesto*

A Box is the persistent home of one workflow instance. State, ownership,
dependencies, exceptions, and a complete audit history live on it.
The Box record is **the operator's**, not Solden's — this document is
the contract that makes that promise enforceable.

This file specifies the **export shape** returned by:

```
GET /api/workspace/ap-items/{ap_item_id}/export
```

It is also the shape Solden commits to preserving across releases.
A third party reading the export must be able to reconstruct the
workflow record without any Solden code running. That is what
"removable" means in the manifesto.

## Versioning

The export carries `box_schema_version` at its root. The current
version is **`1.0`**.

| Change type | Version bump | Examples |
|---|---|---|
| Additive (new keys) | none | New field on `box.fields`, new column surfaced on a history event |
| Semantic (existing key changes meaning) | minor (1.0 → 1.1) | An existing field's set of allowed values changes |
| Breaking (key removed, type changed, structure reshaped) | major (1.0 → 2.0) | `history` becomes a paginated cursor instead of an array |

Consumers MUST:
* Read `box_schema_version` and version-gate any breaking-change handling.
* Tolerate unknown keys (additive changes are backwards-compatible).
* Treat the document as opaque beyond the documented fields.

Solden MAY:
* Add new top-level keys without a version bump.
* Add new fields to any nested object without a version bump.
* Never silently change the semantics of an existing field — that
  always bumps the minor version.

## Top-level shape

```jsonc
{
  "box_schema_version": "1.0",
  "exported_at": "2026-05-14T13:42:01.123456+00:00",
  "exported_by": "operator@example.com",

  "box": {
    "type": "ap_item",
    "id": "AP-abc123",
    "organization_id": "org-clearledgr-internal",
    "entity_id": "ent-acme-eu",
    "state": "approved",
    "created_at": "2026-05-13T09:14:22+00:00",
    "updated_at": "2026-05-14T11:08:45+00:00",
    "fields": { /* see § Box fields */ }
  },

  "history":    [ /* see § History event */ ],
  "exceptions": [ /* see § Exception */ ],
  "outcome":    null | { /* see § Outcome */ },

  "links": {
    "parent_box":  null | { "type": "ap_item", "id": "AP-..." },
    "child_boxes": [    { "type": "bank_match", "id": "BM-..." } ]
  }
}
```

### Top-level fields

| Field | Type | Required | Notes |
|---|---|---|---|
| `box_schema_version` | string | yes | Semantic version. Compare with `1.0`. |
| `exported_at` | ISO-8601 string | yes | UTC. The moment Solden produced the document. |
| `exported_by` | string | yes | Email of the operator who triggered the export. Empty string is permitted for system exports. |
| `box` | object | yes | The Box metadata + state. See § Box. |
| `history` | array | yes | Append-only timeline of audit events. Oldest first. |
| `exceptions` | array | yes | Currently-open exceptions. Empty array when the Box is clean. |
| `outcome` | object \| null | yes | Terminal outcome. `null` while the Box is open. |
| `links` | object | yes | Parent/child Box relationships. |

## Box

The `box` object identifies the workflow instance.

| Field | Type | Notes |
|---|---|---|
| `type` | string | BoxType name. Currently `"ap_item"` or `"bank_match"`. Future BoxTypes register their name in `solden.core.box_registry`. |
| `id` | string | Stable Box identifier. Opaque; treat as a string. |
| `organization_id` | string | The owning tenant. Always populated. |
| `entity_id` | string \| null | The owning entity inside the tenant (multi-entity organizations). Null when the Box is not entity-scoped. |
| `state` | string | The current state in the BoxType's state machine. For `ap_item` see `solden.core.ap_states.APState`. |
| `created_at` | ISO-8601 string | When the Box was opened. |
| `updated_at` | ISO-8601 string | When the Box was last written. |
| `fields` | object | Domain-specific fields. Stable per BoxType but additive — see § Box fields. |

### Box fields (`ap_item`)

The `fields` block contains every persisted column on the underlying
`ap_items` row except the identifiers surfaced at the parent `box`
level. Consumers should treat unknown keys as additive.

Stable keys callers can rely on:

| Key | Type | Notes |
|---|---|---|
| `vendor_name` | string | Free-text vendor name. |
| `amount` | number | Decimal amount. Use `currency` for the unit. |
| `currency` | string | ISO-4217 code (e.g. `"EUR"`). |
| `invoice_number` | string | Vendor's invoice number. |
| `invoice_date` | ISO-8601 date string | Invoice issue date. |
| `due_date` | ISO-8601 date string | Vendor's stated due date. |
| `gl_account` | string \| null | Posted GL account, when classified. |
| `erp_reference` | string \| null | The ERP system's record id once the bill is posted. |
| `thread_id` | string \| null | Email thread id (when sourced from Gmail/Outlook). |
| `last_error` | string \| null | Most recent error during processing. |
| `owner_id` | string \| null | Current explicit owner (canonical user id). Null when no human action is currently required. |
| `owner_email` | string \| null | Human-readable owner identifier (mirrors `owner_id`). |
| `owner_assigned_at` | ISO-8601 string \| null | When the current owner was assigned. |
| `owner_source` | string \| null | How the owner was determined: `auto`, `delegate`, `manual`, or `escalation`. |

All other keys are domain-specific extensions; consumers should
preserve them but not depend on them.

### Box fields (`bank_match`)

A `bank_match` Box is AP-subordinate — every Box has a non-null
`parent_ap_item_id` pointing to the AP item it reconciles, surfaced
in the export at `links.parent_box`. Stable keys on `fields`:

| Key | Type | Notes |
|---|---|---|
| `parent_ap_item_id` | string | AP item this match is hanging off of. |
| `payment_confirmation_id` | string \| null | The payment-side row we're matching against. |
| `bank_statement_line_id` | string \| null | The bank-statement-side row. |
| `confidence` | number \| null | 0.0 to 1.0. Populated by the matcher. |
| `proposed_by` | string | Source of the proposal (e.g. `bank_reconciliation_matcher`). |
| `proposed_at` | ISO-8601 string | When the match was proposed. |
| `decided_by` | string \| null | Operator who accepted/rejected. Null while still `proposed`. |
| `decided_at` | ISO-8601 string \| null | When the decision was recorded. |
| `rejection_reason` | string \| null | Free-text. Only populated when state is `rejected`. |
| `metadata_json` | object | Matcher-specific structured fields. |

`bank_match` states: `proposed`, `accepted` (terminal), `rejected`
(terminal). See `solden.core.bank_match_states.BankMatchState`.

## History event

Each element of `history` is one append-only audit event. Hash-chain
fields are preserved so an offline verifier can confirm the export
has not been tampered with after extraction.

```jsonc
{
  "id": "EVT-abc123def",
  "ts": "2026-05-14T11:08:45+00:00",
  "event_type": "state_transition",
  "prev_state": "validated",
  "new_state": "needs_approval",
  "actor_type": "agent",
  "actor_id": "finance-agent-v1",
  "decision_reason": "amount above auto-approval threshold",
  "policy_version": "v1",
  "governance_verdict": "should_execute",
  "agent_confidence": 0.92,
  "source": "coordination_engine",
  "correlation_id": "corr-7f3e",
  "workflow_id": "wf-abc",
  "run_id": "run-001",
  "payload": { /* event-specific structured body */ },
  "external_refs": { "slack_ts": "1715688525.001", "gmail_id": "..." },
  "idempotency_key": "txn-7f3e-2026-05-14",
  "entity_id": "ent-acme-eu",

  "prev_hash": "sha256:...",
  "hash": "sha256:...",
  "chain_seq": 5
}
```

### Event field reference

| Field | Type | Notes |
|---|---|---|
| `id` | string | Stable event id. |
| `ts` | ISO-8601 string | When the event was recorded. UTC. |
| `event_type` | string | Discriminator. `state_transition`, `override_approved`, `agent_action`, etc. New types are additive. |
| `prev_state` / `new_state` | string \| null | For `state_transition` events. Null for non-state events. |
| `actor_type` | string | `agent`, `user`, `system`, `webhook`. |
| `actor_id` | string | Email or identifier of the actor. |
| `decision_reason` | string \| null | Free-text reason. |
| `policy_version` | string \| null | Version of the policy that authorized the transition. `"v1"` is the current AP policy version; see `solden.core.ap_states.CURRENT_AP_POLICY_VERSION`. |
| `governance_verdict` | string \| null | `should_execute`, `vetoed`, or a structured-policy verdict label. |
| `agent_confidence` | number \| null | 0.0 to 1.0. Present on agent-authored events. |
| `source` | string \| null | Code path that wrote the event (free-form; useful for support). |
| `correlation_id` | string \| null | Joins related events across Boxes (e.g. a batch run). |
| `workflow_id` / `run_id` | string \| null | Plan + run identifiers for agent-driven events. |
| `payload` | object | Structured body. Shape varies by `event_type`; consumers should treat unknown keys as additive. |
| `external_refs` | object | References to external systems: Slack timestamps, Gmail thread ids, ERP record ids, etc. |
| `idempotency_key` | string \| null | Set when the writer guarded against duplicate delivery. |
| `entity_id` | string \| null | Entity scope of the event. |
| `prev_hash` / `hash` / `chain_seq` | string \| number \| null | Cryptographic hash-chain links. `hash` of row N is computed from `prev_hash` (= `hash` of row N-1) plus the row's content. `chain_seq` is monotonic per (organization_id). An offline verifier can recompute these to prove the export has not been tampered with. |

## Exception

An open exception on the Box. Empty array if the Box is clean.

```jsonc
{
  "id": "EXC-abc",
  "exception_type": "duplicate_invoice",
  "severity": "warning" | "blocker" | "info",
  "reason": "Same vendor + invoice number already exists",
  "metadata": { /* exception-specific structured fields */ },
  "raised_at":    "2026-05-14T10:00:00+00:00",
  "resolved_at":  null,
  "resolved_by":  null,
  "resolution_note": null
}
```

Once an exception is resolved, `resolved_at`, `resolved_by`, and
optionally `resolution_note` are populated. Resolved exceptions are
included in the export so the historical context isn't lost.

## Outcome

The terminal outcome of the Box. `null` while the Box is open.

```jsonc
{
  "outcome": "posted" | "reversed" | "closed" | "rejected",
  "completed_at": "2026-05-14T12:00:00+00:00",
  "completed_by": "agent" | "user@example.com",
  "metadata": { /* outcome-specific structured fields */ }
}
```

For `ap_item` Boxes, terminal states are `posted_to_erp`, `reversed`,
`closed`, and `rejected`. See
`solden.core.ap_states.TERMINAL_STATES` for the authoritative
list.

## Links

Parent/child Box relationships. Currently used for child Box types
that reference an `ap_item` parent (`bank_match` is the first such
type — Phase 4 of the manifesto-truthing pass).

```jsonc
{
  "parent_box": null | { "type": "ap_item", "id": "AP-..." },
  "child_boxes": [
    { "type": "bank_match", "id": "BM-..." }
  ]
}
```

For an `ap_item` export, `parent_box` is always `null` and
`child_boxes` may be empty.

## Tenant isolation

A caller can only export Boxes their organization owns. Cross-tenant
requests return **404 Not Found**, never **403 Forbidden** — the API
will not disclose the existence of a Box owned by a different tenant.

## Reconstructability

A third party with one or more Box exports can reconstruct the
workflow record without Solden present:

1. **Identify the Box.** `box.type` + `box.id` are unique within
   `box.organization_id`.
2. **Replay history.** Events in `history` are ordered oldest-first.
   Apply transitions in order to reach the current `box.state`.
3. **Verify integrity.** Recompute the hash chain from `prev_hash` +
   row content and confirm each `hash` matches. A break means the
   export was modified after extraction.
4. **Reconstruct links.** Follow `links.child_boxes[*].id` and pull
   their exports for the full sub-workflow context.

This is the architectural test for the manifesto's promise that
components remain whole if Solden is removed.

## Implementation reference

| Concern | Source |
|---|---|
| Export endpoint | `clearledgr/api/box_export.py` |
| Audit funnel (`policy_version`, hash chain, idempotency) | `clearledgr/core/stores/ap_store.py::append_audit_event` |
| State machine | `clearledgr/core/ap_states.py` |
| Box type registry | `clearledgr/core/box_registry.py` |
| Schema migrations | `clearledgr/core/migrations.py` (v83 adds `policy_version`) |

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-05-14 | Initial published shape covering `ap_item` Boxes. |
