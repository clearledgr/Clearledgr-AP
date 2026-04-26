/**
 * Templates Page — reusable AP reply templates for vendor and approver follow-up.
 */
import { h } from 'preact';
import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import htm from 'htm';
import { useAction } from '../route-helpers.js';
import store from '../../utils/store.js';
import {
  buildReplyTemplatePreferencePatch,
  buildReplyTemplatePrefill,
  createReplyTemplate,
  getAllReplyTemplates,
  getBootstrappedReplyTemplatePreferences,
  getPersonalReplyTemplates,
  getStarterReplyTemplates,
  normalizeReplyTemplatePreferences,
  readReplyTemplatePreferences,
  removeReplyTemplate,
  updateReplyTemplate,
  writeReplyTemplatePreferences,
} from '../reply-templates.js';

const html = htm.bind(h);

const SAMPLE_ITEM = {
  vendor_name: 'Northwind Office Supply',
  invoice_number: 'INV-1042',
  amount: 1280.45,
  currency: 'USD',
  due_date: '2026-03-24',
  po_number: 'PO-2841',
  state: 'needs_info',
  next_action: 'Reply with the missing PO and corrected invoice date.',
  exception_code: 'po_missing_reference',
  subject: 'Invoice INV-1042',
  sender: 'ap@northwind.test',
};

function templateRef(template = {}) {
  return `${template.scope || 'user'}:${template.id || ''}`;
}

function blankEditor() {
  return {
    name: '',
    description: '',
    audience: 'vendor',
    subjectTemplate: '',
    bodyTemplate: '',
  };
}

function buildEditorFromTemplate(template = {}) {
  return {
    name: String(template?.name || ''),
    description: String(template?.description || ''),
    audience: String(template?.audience || 'vendor') === 'internal' ? 'internal' : 'vendor',
    subjectTemplate: String(template?.subjectTemplate || ''),
    bodyTemplate: String(template?.bodyTemplate || ''),
  };
}

function buildDraft(editor = {}) {
  return {
    name: String(editor?.name || '').trim(),
    description: String(editor?.description || '').trim(),
    audience: String(editor?.audience || 'vendor') === 'internal' ? 'internal' : 'vendor',
    subjectTemplate: String(editor?.subjectTemplate || ''),
    bodyTemplate: String(editor?.bodyTemplate || ''),
  };
}

function TemplateRow({ template, selected, onSelect }) {
  return html`<button class=${`templates-row ${selected ? 'is-selected' : ''}`} onClick=${onSelect}>
    <div class="templates-row-top">
      <strong class="templates-row-title">${template.name}</strong>
      <div class="templates-row-tags">
        <span class="templates-pill">${template.scope === 'starter' ? 'Starter' : 'Personal'}</span>
        <span class="templates-pill muted">${template.audience === 'internal' ? 'Internal' : 'Vendor'}</span>
      </div>
    </div>
    <div class="templates-row-detail">${template.description || 'No description'}</div>
  </button>`;
}

export default function TemplatesPage({ api, bootstrap, toast, orgId, userEmail, navigate }) {
  const templateScope = useMemo(() => ({ orgId, userEmail }), [orgId, userEmail]);
  const [templatePrefs, setTemplatePrefs] = useState(() => readReplyTemplatePreferences(templateScope));
  const [selectedRef, setSelectedRef] = useState('');
  const [editor, setEditor] = useState(blankEditor);
  const bootstrapTemplatePrefs = getBootstrappedReplyTemplatePreferences(bootstrap);
  const syncReadyRef = useRef(false);
  const syncTimerRef = useRef(null);
  const lastSyncedPrefsRef = useRef('');

  useEffect(() => {
    setTemplatePrefs(readReplyTemplatePreferences(templateScope));
  }, [templateScope]);

  useEffect(() => {
    const local = readReplyTemplatePreferences(templateScope);
    const remote = bootstrapTemplatePrefs ? normalizeReplyTemplatePreferences(bootstrapTemplatePrefs) : null;
    const next = remote ? writeReplyTemplatePreferences(templateScope, remote) : local;
    setTemplatePrefs(next);
    lastSyncedPrefsRef.current = JSON.stringify(normalizeReplyTemplatePreferences(next));
    syncReadyRef.current = true;
  }, [bootstrapTemplatePrefs, templateScope]);

  useEffect(() => {
    if (!syncReadyRef.current) return undefined;
    const serialized = JSON.stringify(normalizeReplyTemplatePreferences(templatePrefs));
    if (serialized === lastSyncedPrefsRef.current) return undefined;
    if (syncTimerRef.current) clearTimeout(syncTimerRef.current);
    syncTimerRef.current = setTimeout(() => {
      void api('/api/user/preferences', {
        method: 'PATCH',
        body: JSON.stringify({
          organization_id: orgId,
          patch: buildReplyTemplatePreferencePatch(templatePrefs),
        }),
        silent: true,
      }).then(() => {
        lastSyncedPrefsRef.current = serialized;
      }).catch(() => {});
    }, 500);
    return () => {
      if (syncTimerRef.current) clearTimeout(syncTimerRef.current);
    };
  }, [api, orgId, templatePrefs]);

  const starterTemplates = useMemo(() => getStarterReplyTemplates(), []);
  const personalTemplates = useMemo(() => getPersonalReplyTemplates(templatePrefs), [templatePrefs]);
  const allTemplates = useMemo(() => getAllReplyTemplates(templatePrefs), [templatePrefs]);
  const currentTemplate = useMemo(
    () => allTemplates.find((template) => templateRef(template) === selectedRef) || null,
    [allTemplates, selectedRef],
  );

  useEffect(() => {
    if (!allTemplates.length) return;
    if (currentTemplate) return;
    const preferred = personalTemplates[0] || starterTemplates[0] || allTemplates[0];
    if (preferred) setSelectedRef(templateRef(preferred));
  }, [allTemplates, currentTemplate, personalTemplates, starterTemplates]);

  useEffect(() => {
    if (!currentTemplate) {
      setEditor(blankEditor());
      return;
    }
    setEditor(buildEditorFromTemplate(currentTemplate));
  }, [selectedRef]);

  const preview = useMemo(
    () => buildReplyTemplatePrefill(buildDraft(editor), SAMPLE_ITEM, { issue_summary: 'PO reference is still missing.' }),
    [editor],
  );

  const [saveTemplate, savingTemplate] = useAction(async () => {
    const draft = buildDraft(editor);
    if (!draft.name || !draft.bodyTemplate) {
      toast?.('Add a template name and body first.', 'warning');
      return;
    }
    let nextPrefs;
    if (currentTemplate?.scope === 'user') {
      nextPrefs = updateReplyTemplate(templateScope, currentTemplate.id, draft);
      toast?.(`Template "${draft.name}" updated.`, 'success');
    } else {
      nextPrefs = createReplyTemplate(templateScope, draft);
      const created = getPersonalReplyTemplates(nextPrefs).slice(-1)[0];
      if (created) setSelectedRef(templateRef(created));
      toast?.(`Template "${draft.name}" saved as personal.`, 'success');
    }
    setTemplatePrefs(nextPrefs);
  });

  const [deleteTemplate, deletingTemplate] = useAction(async () => {
    if (!currentTemplate || currentTemplate.scope !== 'user') {
      toast?.('Only personal templates can be deleted.', 'warning');
      return;
    }
    const nextPrefs = removeReplyTemplate(templateScope, currentTemplate.id);
    setTemplatePrefs(nextPrefs);
    setSelectedRef('');
    toast?.('Template removed.', 'success');
  });

  const [openComposePreview, previewingCompose] = useAction(async () => {
    try {
      await store.composeWithPrefill(preview);
      toast?.('Draft opened in Gmail compose.', 'success');
    } catch {
      toast?.('Could not open Gmail compose preview.', 'error');
    }
  });

  return html`
    <div class="secondary-banner">
      <div class="secondary-banner-copy">
        <h3>Reply templates</h3>
        <p class="muted">Start from a shared starter, save a personal version, and preview the exact Gmail draft before you use it.</p>
      </div>
      <div class="secondary-banner-actions">
        <button class="btn-secondary btn-sm" onClick=${() => { setSelectedRef(''); setEditor(blankEditor()); }}>New personal template</button>
        <button class="btn-primary btn-sm" onClick=${() => navigate('clearledgr/invoices')}>Back to Invoices</button>
      </div>
    </div>

    <div class="templates-shell">
      <div class="templates-sidebar">
        <div class="panel templates-library-card">
          <div class="templates-section-head">
            <div>
              <h3 style="margin:0 0 4px">Template library</h3>
              <p class="muted" style="margin:0">Start from a shared starter or edit one of your own.</p>
            </div>
          </div>

          <div class="templates-library-section">
            <div class="templates-section-kicker">
              <span>Starter</span>
              <span>${starterTemplates.length}</span>
            </div>
            <div class="templates-list">
              ${starterTemplates.map((template) => html`
                <${TemplateRow}
                  key=${templateRef(template)}
                  template=${template}
                  selected=${selectedRef === templateRef(template)}
                  onSelect=${() => setSelectedRef(templateRef(template))}
                />
              `)}
            </div>
          </div>

          <div class="templates-library-divider"></div>

          <div class="templates-library-section">
            <div class="templates-section-kicker">
              <span>Personal</span>
              <span>${personalTemplates.length}</span>
            </div>
            ${personalTemplates.length === 0
              ? html`<div class="templates-empty-copy">No personal templates yet. Save a starter or create one from scratch.</div>`
              : html`<div class="templates-list">
                  ${personalTemplates.map((template) => html`
                    <${TemplateRow}
                      key=${templateRef(template)}
                      template=${template}
                      selected=${selectedRef === templateRef(template)}
                      onSelect=${() => setSelectedRef(templateRef(template))}
                    />
                  `)}
                </div>`}
          </div>
        </div>

        <div class="panel templates-fields-card">
          <div class="templates-section-head compact">
            <div>
              <h3 style="margin:0 0 4px">Available fields</h3>
              <p class="muted" style="margin:0">Use these placeholders in the subject or body.</p>
            </div>
          </div>
          <div class="templates-token-cloud">
            ${['vendor_name', 'invoice_number', 'amount', 'due_date', 'po_number', 'state_label', 'next_action', 'issue_summary', 'subject'].map((token) => html`
              <span key=${token} class="templates-token">{{${token}}}</span>
            `)}
          </div>
        </div>
      </div>

      <div class="templates-main">
        <div class="panel templates-editor-card">
          <div class="templates-editor-head">
            <div>
              <div class="templates-editor-kicker">
                ${currentTemplate?.scope === 'user'
                  ? 'Personal template'
                  : currentTemplate
                    ? 'Starter template'
                    : 'New personal template'}
              </div>
              <h3 style="margin:0 0 4px">${currentTemplate?.scope === 'user' ? 'Edit template' : currentTemplate ? 'Save starter as personal' : 'Create template'}</h3>
              <p class="muted" style="margin:0">
                ${currentTemplate?.scope === 'user'
                  ? 'Changes save to your personal template library for this Gmail workspace.'
                  : 'Starter templates are read-only. Save a personal copy before editing.'}
              </p>
            </div>
            ${currentTemplate?.scope === 'user'
              ? html`<button class="btn-danger btn-sm" onClick=${deleteTemplate} disabled=${deletingTemplate}>${deletingTemplate ? 'Deleting…' : 'Delete'}</button>`
              : null}
          </div>

          <div class="templates-meta-strip">
            <span class="templates-pill">${currentTemplate?.scope === 'user' ? 'Personal' : currentTemplate ? 'Starter' : 'Draft'}</span>
            <span class="templates-pill muted">${editor.audience === 'internal' ? 'Internal' : 'Vendor'}</span>
          </div>

          <div class="templates-form-grid">
            <label class="templates-field">
              <span class="templates-field-label">Template name</span>
              <input value=${editor.name} onInput=${(event) => setEditor((current) => ({ ...current, name: event.target.value }))} placeholder="Vendor PO request" />
            </label>
            <label class="templates-field">
              <span class="templates-field-label">Audience</span>
              <select value=${editor.audience} onChange=${(event) => setEditor((current) => ({ ...current, audience: event.target.value }))}>
                <option value="vendor">Vendor</option>
                <option value="internal">Internal</option>
              </select>
            </label>
          </div>

          <label class="templates-field">
            <span class="templates-field-label">Description</span>
            <input value=${editor.description} onInput=${(event) => setEditor((current) => ({ ...current, description: event.target.value }))} placeholder="When to use this template" />
          </label>

          <label class="templates-field">
            <span class="templates-field-label">Subject template</span>
            <input value=${editor.subjectTemplate} onInput=${(event) => setEditor((current) => ({ ...current, subjectTemplate: event.target.value }))} placeholder="Re: {{subject}}" />
          </label>

          <label class="templates-field">
            <span class="templates-field-label">Body template</span>
            <textarea value=${editor.bodyTemplate} onInput=${(event) => setEditor((current) => ({ ...current, bodyTemplate: event.target.value }))} placeholder="Hi {{vendor_name}},&#10;&#10;..." />
          </label>

          <div class="toolbar-actions" style="margin-top:14px">
            <button class="btn-primary" onClick=${saveTemplate} disabled=${savingTemplate}>${savingTemplate ? 'Saving…' : currentTemplate?.scope === 'user' ? 'Update template' : 'Save as personal'}</button>
            <button class="btn-secondary" onClick=${openComposePreview} disabled=${previewingCompose}>${previewingCompose ? 'Opening…' : 'Open compose preview'}</button>
          </div>
        </div>

        <div class="panel templates-preview-card">
          <div class="templates-section-head compact">
            <div>
              <h3 style="margin:0 0 4px">Compose preview</h3>
              <p class="muted" style="margin:0">Sample invoice context shows how the draft will read in Gmail.</p>
            </div>
          </div>

          <div class="templates-mail-preview">
            <div class="templates-mail-row">
              <span class="templates-mail-label">To</span>
              <span class="templates-mail-value">${preview.to || '(set recipient when used)'}</span>
            </div>
            <div class="templates-mail-row">
              <span class="templates-mail-label">Subject</span>
              <span class="templates-mail-value">${preview.subject || '—'}</span>
            </div>
            <div class="templates-mail-body">
              <pre>${preview.body || '—'}</pre>
            </div>
          </div>
        </div>
      </div>
    </div>
  `;
}
