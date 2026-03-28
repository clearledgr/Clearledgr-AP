import { h } from 'preact';
import htm from 'htm';
import { hasCapability, useAction } from '../route-helpers.js';

const html = htm.bind(h);

const ENTITY_ROW_ID_SEEDS = [
  { code: 'cl-entity-code-0', name: 'cl-entity-name-0', id: 'cl-entity-id-0' },
  { code: 'cl-entity-code-1', name: 'cl-entity-name-1', id: 'cl-entity-id-1' },
  { code: 'cl-entity-code-2', name: 'cl-entity-name-2', id: 'cl-entity-id-2' },
  { code: 'cl-entity-code-3', name: 'cl-entity-name-3', id: 'cl-entity-id-3' },
];

const ENTITY_RULE_ROW_ID_SEEDS = [
  {
    entityCode: 'cl-entity-rule-entity-code-0',
    entityName: 'cl-entity-rule-entity-name-0',
    entityId: 'cl-entity-rule-entity-id-0',
    senderDomains: 'cl-entity-rule-sender-domains-0',
    currencies: 'cl-entity-rule-currencies-0',
    vendorMatchers: 'cl-entity-rule-vendor-matchers-0',
    subjectMatchers: 'cl-entity-rule-subject-matchers-0',
    priority: 'cl-entity-rule-priority-0',
  },
  {
    entityCode: 'cl-entity-rule-entity-code-1',
    entityName: 'cl-entity-rule-entity-name-1',
    entityId: 'cl-entity-rule-entity-id-1',
    senderDomains: 'cl-entity-rule-sender-domains-1',
    currencies: 'cl-entity-rule-currencies-1',
    vendorMatchers: 'cl-entity-rule-vendor-matchers-1',
    subjectMatchers: 'cl-entity-rule-subject-matchers-1',
    priority: 'cl-entity-rule-priority-1',
  },
  {
    entityCode: 'cl-entity-rule-entity-code-2',
    entityName: 'cl-entity-rule-entity-name-2',
    entityId: 'cl-entity-rule-entity-id-2',
    senderDomains: 'cl-entity-rule-sender-domains-2',
    currencies: 'cl-entity-rule-currencies-2',
    vendorMatchers: 'cl-entity-rule-vendor-matchers-2',
    subjectMatchers: 'cl-entity-rule-subject-matchers-2',
    priority: 'cl-entity-rule-priority-2',
  },
  {
    entityCode: 'cl-entity-rule-entity-code-3',
    entityName: 'cl-entity-rule-entity-name-3',
    entityId: 'cl-entity-rule-entity-id-3',
    senderDomains: 'cl-entity-rule-sender-domains-3',
    currencies: 'cl-entity-rule-currencies-3',
    vendorMatchers: 'cl-entity-rule-vendor-matchers-3',
    subjectMatchers: 'cl-entity-rule-subject-matchers-3',
    priority: 'cl-entity-rule-priority-3',
  },
];

function parseCommaList(value) {
  return String(value || '')
    .split(',')
    .map((entry) => entry.trim())
    .filter(Boolean);
}

function getEntityRowDomIds(index) {
  return ENTITY_ROW_ID_SEEDS[index] || {
    code: `cl-entity-code-${index}`,
    name: `cl-entity-name-${index}`,
    id: `cl-entity-id-${index}`,
  };
}

function getEntityRuleRowDomIds(index) {
  return ENTITY_RULE_ROW_ID_SEEDS[index] || {
    entityCode: `cl-entity-rule-entity-code-${index}`,
    entityName: `cl-entity-rule-entity-name-${index}`,
    entityId: `cl-entity-rule-entity-id-${index}`,
    senderDomains: `cl-entity-rule-sender-domains-${index}`,
    currencies: `cl-entity-rule-currencies-${index}`,
    vendorMatchers: `cl-entity-rule-vendor-matchers-${index}`,
    subjectMatchers: `cl-entity-rule-subject-matchers-${index}`,
    priority: `cl-entity-rule-priority-${index}`,
  };
}

export function getEntityRoutingConfig(settings = {}) {
  const routing = settings?.entity_routing && typeof settings.entity_routing === 'object'
    ? settings.entity_routing
    : {};
  const entities = Array.isArray(routing.entities)
    ? routing.entities
      .map((entry) => ({
        entity_id: String(entry?.entity_id || '').trim(),
        entity_code: String(entry?.entity_code || '').trim(),
        entity_name: String(entry?.entity_name || '').trim(),
      }))
      .filter((entry) => entry.entity_id || entry.entity_code || entry.entity_name)
    : [];
  const rules = Array.isArray(routing.rules)
    ? routing.rules
      .map((entry) => ({
        entity_id: String(entry?.entity_id || '').trim(),
        entity_code: String(entry?.entity_code || '').trim(),
        entity_name: String(entry?.entity_name || '').trim(),
        sender_domains: Array.isArray(entry?.sender_domains) ? entry.sender_domains.map((value) => String(value || '').trim()).filter(Boolean) : [],
        vendor_contains: Array.isArray(entry?.vendor_contains) ? entry.vendor_contains.map((value) => String(value || '').trim()).filter(Boolean) : [],
        subject_contains: Array.isArray(entry?.subject_contains) ? entry.subject_contains.map((value) => String(value || '').trim()).filter(Boolean) : [],
        currencies: Array.isArray(entry?.currencies) ? entry.currencies.map((value) => String(value || '').trim().toUpperCase()).filter(Boolean) : [],
        priority: Number.isFinite(Number(entry?.priority)) ? Number(entry.priority) : 100,
      }))
      .filter((entry) => (
        (entry.entity_id || entry.entity_code || entry.entity_name)
        && (entry.sender_domains.length || entry.vendor_contains.length || entry.subject_contains.length || entry.currencies.length)
      ))
    : [];
  return { entities, rules };
}

export default function CompanyPage({ bootstrap, api, toast, orgId, onRefresh }) {
  const org = bootstrap?.organization || {};
  const settings = org?.settings && typeof org.settings === 'object' ? org.settings : {};
  const entityRouting = getEntityRoutingConfig(settings);
  const entityRows = [...entityRouting.entities];
  while (entityRows.length < 3) entityRows.push({ entity_id: '', entity_code: '', entity_name: '' });
  entityRows.push({ entity_id: '', entity_code: '', entity_name: '' });
  const ruleRows = [...entityRouting.rules];
  while (ruleRows.length < 3) {
    ruleRows.push({
      entity_id: '',
      entity_code: '',
      entity_name: '',
      sender_domains: [],
      vendor_contains: [],
      subject_contains: [],
      currencies: [],
      priority: 100,
    });
  }
  ruleRows.push({
    entity_id: '',
    entity_code: '',
    entity_name: '',
    sender_domains: [],
    vendor_contains: [],
    subject_contains: [],
    currencies: [],
    priority: 100,
  });
  const canManageCompany = hasCapability(bootstrap, 'manage_company');
  const [saveOrg, saving] = useAction(async () => {
    if (!canManageCompany) return;
    const nextEntities = entityRows.map((_, index) => ({
      entity_id: document.getElementById(getEntityRowDomIds(index).id)?.value?.trim() || '',
      entity_code: document.getElementById(getEntityRowDomIds(index).code)?.value?.trim() || '',
      entity_name: document.getElementById(getEntityRowDomIds(index).name)?.value?.trim() || '',
    })).filter((entry) => entry.entity_id || entry.entity_code || entry.entity_name);

    const nextRules = ruleRows.map((_, index) => ({
      entity_id: document.getElementById(getEntityRuleRowDomIds(index).entityId)?.value?.trim() || '',
      entity_code: document.getElementById(getEntityRuleRowDomIds(index).entityCode)?.value?.trim() || '',
      entity_name: document.getElementById(getEntityRuleRowDomIds(index).entityName)?.value?.trim() || '',
      sender_domains: parseCommaList(document.getElementById(getEntityRuleRowDomIds(index).senderDomains)?.value),
      vendor_contains: parseCommaList(document.getElementById(getEntityRuleRowDomIds(index).vendorMatchers)?.value),
      subject_contains: parseCommaList(document.getElementById(getEntityRuleRowDomIds(index).subjectMatchers)?.value),
      currencies: parseCommaList(document.getElementById(getEntityRuleRowDomIds(index).currencies)?.value).map((value) => value.toUpperCase()),
      priority: Math.max(1, Math.round(Number(document.getElementById(getEntityRuleRowDomIds(index).priority)?.value) || 100)),
    })).filter((entry) => (
      (entry.entity_id || entry.entity_code || entry.entity_name)
      && (entry.sender_domains.length || entry.vendor_contains.length || entry.subject_contains.length || entry.currencies.length)
    ));

    await api('/api/workspace/org/settings', {
      method: 'PATCH',
      body: JSON.stringify({
        organization_id: orgId,
        patch: {
          organization_name: document.getElementById('cl-org-name')?.value?.trim(),
          domain: document.getElementById('cl-org-domain')?.value?.trim(),
          integration_mode: document.getElementById('cl-org-mode')?.value,
          entity_routing: {
            entities: nextEntities,
            rules: nextRules,
          },
        },
      }),
    });
    toast('Company details saved.');
    onRefresh();
  });

  return html`
    <div class=${`secondary-banner ${canManageCompany ? '' : 'warning'}`}>
      <div class="secondary-banner-copy">
        <h3>${canManageCompany ? 'Keep the workspace record current' : 'Workspace record is visible here'}</h3>
        <p class="muted">${canManageCompany ? 'These settings tell Clearledgr which company and workspace this inbox belongs to, and how invoices should route across legal entities.' : 'You can review company details here, but only admins can change workspace settings.'}</p>
      </div>
      <div class="secondary-banner-actions">
        <button class="btn-primary" onClick=${saveOrg} disabled=${saving || !canManageCompany}>${saving ? 'Saving…' : 'Save company details'}</button>
      </div>
    </div>

    <div class="secondary-shell">
      <div class="secondary-main">
        <div class="panel">
          <h3 style="margin-top:0">Company details</h3>
          <div style="display:flex;flex-direction:column;gap:16px;margin-top:8px">
            <div><label>Company name</label><input id="cl-org-name" value=${org.name || ''} placeholder="Your company name" disabled=${!canManageCompany} /></div>
            <div><label>Domain</label><input id="cl-org-domain" value=${org.domain || ''} placeholder="company.com" disabled=${!canManageCompany} /></div>
            <div><label>Integration mode</label>
              <select id="cl-org-mode" disabled=${!canManageCompany}>
                <option value="shared" selected=${org.integration_mode === 'shared'}>Shared workspace</option>
                <option value="per_org" selected=${org.integration_mode === 'per_org'}>Per organization</option>
              </select>
            </div>
          </div>
        </div>

        <div class="panel">
          <h3 style="margin-top:0">Legal entities</h3>
          <p class="muted" style="margin:0 0 14px">List the entities Clearledgr can route invoices into. Use the code your ERP or finance team already uses.</p>
          <div style="display:flex;flex-direction:column;gap:12px">
            ${entityRows.map((entity, index) => {
              const ids = getEntityRowDomIds(index);
              return html`
              <div class="secondary-row" key=${ids.code}>
                <div class="secondary-row-copy" style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px">
                  <div>
                    <label>Entity code</label>
                    <input id=${ids.code} value=${entity.entity_code || ''} placeholder="US-01" disabled=${!canManageCompany} />
                  </div>
                  <div>
                    <label>Entity name</label>
                    <input id=${ids.name} value=${entity.entity_name || ''} placeholder="Acme US" disabled=${!canManageCompany} />
                  </div>
                  <div>
                    <label>Entity ID</label>
                    <input id=${ids.id} value=${entity.entity_id || ''} placeholder="subsidiary_123" disabled=${!canManageCompany} />
                  </div>
                </div>
              </div>
            `;
            })}
          </div>
        </div>

        <div class="panel">
          <h3 style="margin-top:0">Entity routing rules</h3>
          <p class="muted" style="margin:0 0 14px">Define how Clearledgr should match invoices to entities. If no rule matches and multiple entities exist, the invoice stays in manual entity review.</p>
          <div style="display:flex;flex-direction:column;gap:12px">
            ${ruleRows.map((rule, index) => {
              const ids = getEntityRuleRowDomIds(index);
              return html`
              <div class="secondary-row" key=${ids.entityCode}>
                <div class="secondary-row-copy" style="display:flex;flex-direction:column;gap:10px">
                  <div style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px">
                    <div>
                      <label>Entity code</label>
                      <input id=${ids.entityCode} value=${rule.entity_code || ''} placeholder="US-01" disabled=${!canManageCompany} />
                    </div>
                    <div>
                      <label>Entity name</label>
                      <input id=${ids.entityName} value=${rule.entity_name || ''} placeholder="Acme US" disabled=${!canManageCompany} />
                    </div>
                    <div>
                      <label>Entity ID</label>
                      <input id=${ids.entityId} value=${rule.entity_id || ''} placeholder="subsidiary_123" disabled=${!canManageCompany} />
                    </div>
                    <div>
                      <label>Priority</label>
                      <input id=${ids.priority} type="number" min="1" step="1" value=${String(rule.priority || 100)} disabled=${!canManageCompany} />
                    </div>
                  </div>
                  <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px">
                    <div>
                      <label>Sender domains</label>
                      <input id=${ids.senderDomains} value=${(rule.sender_domains || []).join(', ')} placeholder="booking.com, cowrywise.com" disabled=${!canManageCompany} />
                    </div>
                    <div>
                      <label>Currencies</label>
                      <input id=${ids.currencies} value=${(rule.currencies || []).join(', ')} placeholder="USD, GHS" disabled=${!canManageCompany} />
                    </div>
                  </div>
                  <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px">
                    <div>
                      <label>Vendor keywords</label>
                      <input id=${ids.vendorMatchers} value=${(rule.vendor_contains || []).join(', ')} placeholder="google workspace, aws" disabled=${!canManageCompany} />
                    </div>
                    <div>
                      <label>Subject keywords</label>
                      <input id=${ids.subjectMatchers} value=${(rule.subject_contains || []).join(', ')} placeholder="ghana, us entity" disabled=${!canManageCompany} />
                    </div>
                  </div>
                </div>
              </div>
            `;
            })}
          </div>
        </div>
      </div>

      <div class="secondary-side">
        <div class="panel">
          <h3 style="margin-top:0">Current workspace record</h3>
          <div class="secondary-stat-grid" style="margin-top:12px">
            <div class="secondary-stat-card">
              <strong>Organization ID</strong>
              <span>${org.id || orgId || '—'}</span>
            </div>
            <div class="secondary-stat-card">
              <strong>Domain</strong>
              <span>${org.domain || 'Not set'}</span>
            </div>
            <div class="secondary-stat-card">
              <strong>Mode</strong>
              <span>${org.integration_mode === 'per_org' ? 'Per organization' : 'Shared workspace'}</span>
            </div>
            <div class="secondary-stat-card">
              <strong>Entities</strong>
              <span>${entityRouting.entities.length || 'None set'}</span>
            </div>
            <div class="secondary-stat-card">
              <strong>Routing rules</strong>
              <span>${entityRouting.rules.length || 'None set'}</span>
            </div>
          </div>
        </div>

        <div class="panel">
          <h3 style="margin-top:0">Routing behavior</h3>
          <div class="secondary-note">
            ${entityRouting.entities.length > 1
              ? 'When more than one entity is configured, Clearledgr will route automatically only when a single rule match is clear.'
              : 'Add more than one legal entity when you need explicit cross-entity routing controls.'}
          </div>
        </div>
      </div>
    </div>
  `;
}
