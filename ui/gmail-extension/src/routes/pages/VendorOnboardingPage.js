/**
 * Vendor Onboarding Pipeline — Kanban board (DESIGN_THESIS.md §9, §6.7).
 *
 * Four thesis-defined stages: Invited → KYC → Bank Verify → Active.
 * Each column shows vendor onboarding cards with vendor name, contact,
 * stage, and days elapsed. Clicking a card opens the vendor detail view.
 *
 * This page reads onboarding session data from the backend API
 * (GET /api/vendors/{name}/onboarding/session) and renders it as a
 * Kanban board identical in structure to the AP Invoices pipeline.
 */
import { h } from 'preact';
import { useCallback, useEffect, useMemo, useState } from 'preact/hooks';
import htm from 'htm';
import { fmtDateTime } from '../route-helpers.js';
import InviteVendorModal from '../../components/InviteVendorModal.js';

const html = htm.bind(h);

const ONBOARDING_STAGES = [
  { key: 'invited',             label: 'Invited',     states: ['invited'], color: '#9CA3AF' },
  { key: 'kyc',                 label: 'KYC',         states: ['kyc'], color: '#D97706' },
  { key: 'bank_verify',         label: 'Bank Verify', states: ['bank_verify', 'bank_verified', 'ready_for_erp'], color: '#2563EB' },
  { key: 'active',              label: 'Active',      states: ['active'], color: '#10B981' },
];

const SECONDARY_STATES = {
  blocked:             { label: 'Blocked',             color: '#DC2626' },
  closed_unsuccessful: { label: 'Closed unsuccessful', color: '#6B7280' },
};

function daysElapsed(isoDate) {
  if (!isoDate) return 0;
  try {
    const d = new Date(isoDate);
    return Math.max(0, Math.floor((Date.now() - d.getTime()) / 86400000));
  } catch { return 0; }
}

function StateBadge({ state }) {
  const secondary = SECONDARY_STATES[state];
  if (secondary) {
    return html`<span style="
      font-size:10px;font-weight:700;padding:2px 8px;border-radius:999px;
      background:${secondary.color}20;color:${secondary.color};text-transform:uppercase;
    ">${secondary.label}</span>`;
  }
  return null;
}

export default function VendorOnboardingPage({ bootstrap, api, navigate, toast }) {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [inviteOpen, setInviteOpen] = useState(false);
  const orgId = bootstrap?.organization_id || bootstrap?.orgId || 'default';

  const loadSessions = useCallback(({ silent } = {}) => {
    if (!api) return Promise.resolve();
    if (!silent) setLoading(true);
    return api(
      `/api/ops/vendor-onboarding/sessions?organization_id=${encodeURIComponent(orgId)}&limit=200`,
      { silent: true },
    )
      .then((data) => {
        setSessions(Array.isArray(data?.sessions) ? data.sessions : []);
      })
      .catch(() => {
        // Fallback: if the endpoint doesn't exist yet, show empty state.
        setSessions([]);
      })
      .finally(() => setLoading(false));
  }, [api, orgId]);

  useEffect(() => { void loadSessions(); }, [loadSessions]);

  const stageGroups = useMemo(() => {
    const groups = {};
    ONBOARDING_STAGES.forEach((stage) => { groups[stage.key] = []; });
    groups._secondary = [];

    sessions.forEach((session) => {
      const state = String(session.state || '').toLowerCase();
      if (SECONDARY_STATES[state]) {
        groups._secondary.push(session);
        return;
      }
      const matched = ONBOARDING_STAGES.find((s) => s.states.includes(state));
      if (matched) {
        groups[matched.key].push(session);
      } else {
        // Unknown state — put in Invited column as fallback.
        groups.invited.push(session);
      }
    });
    return groups;
  }, [sessions]);

  return html`
    <div class="topbar" style="padding:16px 20px 12px;display:flex;align-items:flex-start;justify-content:space-between;gap:16px">
      <div>
        <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.05em;color:#5C6B7A;margin-bottom:4px">Vendor Onboarding</div>
        <h2 style="margin:0;font-size:20px;color:#0A1628">Onboarding pipeline</h2>
        <p class="muted" style="margin:4px 0 0;font-size:13px">Track vendors from invite to ERP activation.</p>
      </div>
      <button
        class="btn"
        style="background:#00D67E;color:#0A1628;border:1px solid #00D67E;padding:8px 14px;border-radius:6px;font-size:13px;font-weight:600;cursor:pointer;white-space:nowrap"
        onClick=${() => setInviteOpen(true)}
      >Invite vendor</button>
    </div>
    ${inviteOpen ? html`<${InviteVendorModal}
      api=${api}
      orgId=${orgId}
      toast=${toast}
      onClose=${() => setInviteOpen(false)}
      onSuccess=${() => { void loadSessions({ silent: true }); }}
    />` : ''}

    ${loading
      ? html`<div class="muted" style="text-align:center;padding:48px 0;font-size:13px">Loading onboarding sessions…</div>`
      : html`
        <div style="display:flex;gap:12px;overflow-x:auto;padding:12px 20px 20px;min-height:400px">
          ${ONBOARDING_STAGES.map((stage) => {
            const items = stageGroups[stage.key] || [];
            return html`
              <div key=${stage.key} style="
                min-width:220px;max-width:260px;flex:1;
                background:#F7F9FB;border-radius:10px;
                display:flex;flex-direction:column;
              ">
                <div style="
                  padding:10px 14px;border-bottom:2px solid ${stage.color};
                  display:flex;align-items:center;justify-content:space-between;
                ">
                  <strong style="font-size:13px;color:#0A1628">${stage.label}</strong>
                  <span style="
                    font-size:11px;font-weight:700;padding:2px 8px;border-radius:999px;
                    background:${stage.color}20;color:${stage.color};
                  ">${items.length}</span>
                </div>
                <div style="padding:8px;flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:8px">
                  ${items.length === 0
                    ? html`<div class="muted" style="font-size:12px;text-align:center;padding:24px 8px">No vendors</div>`
                    : items.map((session) => html`
                      <div
                        key=${session.id}
                        style="
                          background:#fff;border:1px solid #E5EBF0;border-radius:8px;
                          padding:10px 12px;cursor:pointer;
                        "
                        onClick=${() => navigate && navigate('clearledgr/vendor/' + encodeURIComponent(session.vendor_name))}
                      >
                        <strong style="font-size:13px;color:#0A1628;display:block;margin-bottom:4px">
                          ${session.vendor_name || 'Unknown vendor'}
                        </strong>
                        <div class="muted" style="font-size:11px;margin-bottom:2px">
                          ${(session.metadata?.invite_email_to) || 'No contact email'}
                        </div>
                        <div class="muted" style="font-size:11px;display:flex;justify-content:space-between;align-items:center">
                          <span>${daysElapsed(session.invited_at)}d elapsed</span>
                          ${session.chase_count > 0 ? html`<span>${session.chase_count} chase${session.chase_count > 1 ? 's' : ''}</span>` : ''}
                        </div>
                        <${StateBadge} state=${session.state} />
                      </div>
                    `)}
                </div>
              </div>
            `;
          })}
        </div>

        ${stageGroups._secondary.length > 0 ? html`
          <div style="padding:0 20px 20px">
            <div style="font-size:13px;font-weight:600;color:#5C6B7A;margin-bottom:8px">
              Escalated, rejected, and abandoned (${stageGroups._secondary.length})
            </div>
            <div style="display:flex;gap:8px;flex-wrap:wrap">
              ${stageGroups._secondary.map((session) => html`
                <div
                  key=${session.id}
                  style="background:#fff;border:1px solid #E5EBF0;border-radius:8px;padding:8px 12px;cursor:pointer;min-width:200px"
                  onClick=${() => navigate && navigate('clearledgr/vendor/' + encodeURIComponent(session.vendor_name))}
                >
                  <strong style="font-size:12px">${session.vendor_name}</strong>
                  <span style="margin-left:8px"><${StateBadge} state=${session.state} /></span>
                </div>
              `)}
            </div>
          </div>
        ` : ''}

        ${sessions.length === 0 ? html`
          <div class="muted" style="text-align:center;padding:48px 20px;font-size:13px">
            <div style="margin-bottom:12px">No vendor onboarding sessions yet.</div>
            <button
              class="btn"
              style="background:#00D67E;color:#0A1628;border:1px solid #00D67E;padding:8px 14px;border-radius:6px;font-size:13px;font-weight:600;cursor:pointer"
              onClick=${() => setInviteOpen(true)}
            >Invite your first vendor</button>
          </div>
        ` : ''}
      `}
  `;
}
