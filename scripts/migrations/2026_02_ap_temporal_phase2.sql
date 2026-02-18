-- Clearledgr AP v1 Phase 2 migration (Temporal + Agent runtime metadata)

ALTER TABLE ap_items ADD COLUMN IF NOT EXISTS workflow_id TEXT;
ALTER TABLE ap_items ADD COLUMN IF NOT EXISTS run_id TEXT;
ALTER TABLE ap_items ADD COLUMN IF NOT EXISTS approval_surface TEXT DEFAULT 'hybrid';
ALTER TABLE ap_items ADD COLUMN IF NOT EXISTS approval_policy_version TEXT;
ALTER TABLE ap_items ADD COLUMN IF NOT EXISTS post_attempted_at TEXT;

ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS source TEXT;
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS correlation_id TEXT;
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS workflow_id TEXT;
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS run_id TEXT;
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS decision_reason TEXT;

ALTER TABLE approvals ADD COLUMN IF NOT EXISTS source_channel TEXT;
ALTER TABLE approvals ADD COLUMN IF NOT EXISTS source_message_ref TEXT;
ALTER TABLE approvals ADD COLUMN IF NOT EXISTS decision_idempotency_key TEXT;
ALTER TABLE approvals ADD COLUMN IF NOT EXISTS decision_payload TEXT;

CREATE INDEX IF NOT EXISTS idx_ap_items_org_state_updated
  ON ap_items(organization_id, state, updated_at);

CREATE INDEX IF NOT EXISTS idx_audit_org_event_ts
  ON audit_events(organization_id, event_type, ts);

CREATE UNIQUE INDEX IF NOT EXISTS idx_approvals_item_decision_key
  ON approvals(ap_item_id, decision_idempotency_key);
