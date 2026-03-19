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
  return html`<button
    class="alt"
    onClick=${onSelect}
    style="
      display:block;width:100%;padding:12px 14px;text-align:left;
      border-radius:var(--radius-md);
      border:1px solid ${selected ? 'var(--accent)' : 'var(--border)'};
      background:${selected ? 'var(--accent-soft)' : 'var(--surface)'};
    "
  >
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:4px">
      <strong style="font-size:13px">${template.name}</strong>
      <span class="muted" style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.02em">
        ${template.scope === 'starter' ? 'Starter' : 'Personal'}
      </span>
      <span class="muted" style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.02em">
        ${template.audience === 'internal' ? 'Internal' : 'Vendor'}
      </span>
    </div>
    <div class="muted" style="font-size:12px;line-height:1.45">${template.description || 'No description'}</div>
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
      void api('/api/admin/user/preferences', {
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
    <div class="panel">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap">
        <div>
          <h3 style="margin:0 0 6px">AP reply templates</h3>
          <p class="muted" style="margin:0;max-width:620px">
            Keep vendor info requests, approval nudges, rejection notes, and payment-status replies consistent without leaving Gmail.
          </p>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="alt" onClick=${() => { setSelectedRef(''); setEditor(blankEditor()); }}>New personal template</button>
          <button class="alt" onClick=${() => navigate('clearledgr/home')}>Back to Home</button>
        </div>
      </div>
    </div>

    <div style="display:grid;grid-template-columns:minmax(260px,0.75fr) minmax(0,1.25fr);gap:20px">
      <div style="display:flex;flex-direction:column;gap:20px">
        <div class="panel">
          <h3 style="margin-top:0">Starter templates</h3>
          <div style="display:flex;flex-direction:column;gap:8px">
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

        <div class="panel">
          <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px">
            <h3 style="margin:0">Personal templates</h3>
            <span class="muted" style="font-size:12px">${personalTemplates.length} saved</span>
          </div>
          ${personalTemplates.length === 0
            ? html`<p class="muted" style="margin:0">No personal templates yet. Start from a starter template or create one from scratch.</p>`
            : html`<div style="display:flex;flex-direction:column;gap:8px">
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

        <div class="panel">
          <h3 style="margin-top:0">Available fields</h3>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            ${['vendor_name', 'invoice_number', 'amount', 'due_date', 'po_number', 'state_label', 'next_action', 'issue_summary', 'subject'].map((token) => html`
              <span key=${token} style="padding:5px 10px;border-radius:999px;border:1px solid var(--border);background:var(--bg);font-size:12px;font-family:var(--font-mono)">{{${token}}}</span>
            `)}
          </div>
        </div>
      </div>

      <div style="display:flex;flex-direction:column;gap:20px">
        <div class="panel">
          <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:12px">
            <div>
              <h3 style="margin:0 0 4px">${currentTemplate?.scope === 'user' ? 'Edit personal template' : currentTemplate ? 'Save starter as personal' : 'Create personal template'}</h3>
              <p class="muted" style="margin:0">
                ${currentTemplate?.scope === 'user'
                  ? 'Changes sync to your user preferences and stay available across Gmail sessions.'
                  : 'Starter templates are read-only defaults. Save a personal copy before editing.'}
              </p>
            </div>
            ${currentTemplate?.scope === 'user'
              ? html`<button class="alt" onClick=${deleteTemplate} disabled=${deletingTemplate}>${deletingTemplate ? 'Deleting…' : 'Delete'}</button>`
              : null}
          </div>

          <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">
            <label style="display:flex;flex-direction:column;gap:6px">
              <span class="muted" style="font-size:12px">Template name</span>
              <input value=${editor.name} onInput=${(event) => setEditor((current) => ({ ...current, name: event.target.value }))} placeholder="Vendor PO request" />
            </label>
            <label style="display:flex;flex-direction:column;gap:6px">
              <span class="muted" style="font-size:12px">Audience</span>
              <select value=${editor.audience} onChange=${(event) => setEditor((current) => ({ ...current, audience: event.target.value }))}>
                <option value="vendor">Vendor</option>
                <option value="internal">Internal</option>
              </select>
            </label>
          </div>

          <label style="display:flex;flex-direction:column;gap:6px;margin-bottom:12px">
            <span class="muted" style="font-size:12px">Description</span>
            <input value=${editor.description} onInput=${(event) => setEditor((current) => ({ ...current, description: event.target.value }))} placeholder="When to use this template" />
          </label>

          <label style="display:flex;flex-direction:column;gap:6px;margin-bottom:12px">
            <span class="muted" style="font-size:12px">Subject template</span>
            <input value=${editor.subjectTemplate} onInput=${(event) => setEditor((current) => ({ ...current, subjectTemplate: event.target.value }))} placeholder="Re: {{subject}}" />
          </label>

          <label style="display:flex;flex-direction:column;gap:6px">
            <span class="muted" style="font-size:12px">Body template</span>
            <textarea value=${editor.bodyTemplate} onInput=${(event) => setEditor((current) => ({ ...current, bodyTemplate: event.target.value }))} placeholder="Hi {{vendor_name}},&#10;&#10;..." />
          </label>

          <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:14px">
            <button onClick=${saveTemplate} disabled=${savingTemplate}>${savingTemplate ? 'Saving…' : currentTemplate?.scope === 'user' ? 'Update template' : 'Save as personal'}</button>
            <button class="alt" onClick=${openComposePreview} disabled=${previewingCompose}>${previewingCompose ? 'Opening…' : 'Open compose preview'}</button>
          </div>
        </div>

        <div class="panel">
          <h3 style="margin-top:0">Preview</h3>
          <p class="muted" style="margin:0 0 12px">Preview uses sample invoice context so you can check wording before using the template from a real record.</p>
          <div style="display:flex;flex-direction:column;gap:10px">
            <div style="padding:12px 14px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--bg)">
              <div class="muted" style="font-size:12px;margin-bottom:4px">To</div>
              <div style="font-weight:600">${preview.to || '(set recipient when used)'}</div>
            </div>
            <div style="padding:12px 14px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--bg)">
              <div class="muted" style="font-size:12px;margin-bottom:4px">Subject</div>
              <div style="font-weight:600">${preview.subject || '—'}</div>
            </div>
            <div style="padding:12px 14px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--bg)">
              <div class="muted" style="font-size:12px;margin-bottom:6px">Body</div>
              <pre style="margin:0;white-space:pre-wrap;font-family:var(--font);font-size:13px;line-height:1.6;color:var(--ink)">${preview.body || '—'}</pre>
            </div>
          </div>
        </div>
      </div>
    </div>
  `;
}
