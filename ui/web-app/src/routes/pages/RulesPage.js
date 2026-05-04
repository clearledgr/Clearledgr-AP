/**
 * Rules — Module 3 (workspace approval rules).
 *
 * The leader's rule editor: list active + paused + archived rules,
 * create/edit/clone with a JSON-mode body editor + schema validation,
 * apply one of the four starter templates, run a test invoice through
 * the rule set with full trace, browse version history, and one-click
 * revert.
 *
 * Per spec §118 the visual drag-drop builder is deferred to v1.5.
 * This is the JSON-mode v1 surface.
 */
import { h } from 'preact';
import { useCallback, useEffect, useMemo, useState } from 'preact/hooks';
import htm from 'htm';
import { fmtDateTime } from '../route-helpers.js';

const html = htm.bind(h);


// ─── Top-level page ─────────────────────────────────────────────────

export default function RulesPage({ api, toast }) {
  const [rules, setRules] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(false);
  const [editor, setEditor] = useState(null);  // {mode, rule}
  const [tester, setTester] = useState(false);
  const [versionsForRule, setVersionsForRule] = useState(null);
  // Module 3 spec line 121: rule list "sortable, filterable by entity,
  // workflow, trigger type". Backend already accepts these query params
  // (workspace_rules.py:108-129); this is just the UI surface.
  const [filterStatus, setFilterStatus] = useState('all');     // all|active|paused|archived
  const [filterEntity, setFilterEntity] = useState('');         // free-text entity_id
  const [filterWorkflow, setFilterWorkflow] = useState('all');  // all|ap|...

  const loadRules = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ include_inactive: 'true' });
      if (filterEntity.trim()) params.set('entity_id', filterEntity.trim());
      if (filterWorkflow !== 'all') params.set('workflow', filterWorkflow);
      const resp = await api(`/api/workspace/rules?${params.toString()}`);
      let rows = resp?.rules || [];
      if (filterStatus !== 'all') {
        rows = rows.filter((r) => String(r.status || '').toLowerCase() === filterStatus);
      }
      setRules(rows);
    } catch (exc) {
      toast(`Failed to load rules: ${String(exc?.message || exc)}`, 'error');
    } finally {
      setLoading(false);
    }
  }, [api, toast, filterStatus, filterEntity, filterWorkflow]);

  const loadTemplates = useCallback(async () => {
    try {
      const resp = await api('/api/workspace/rules/templates');
      setTemplates(resp?.templates || []);
    } catch {
      // Non-fatal — template gallery just stays empty.
    }
  }, [api]);

  useEffect(() => {
    loadRules();
    loadTemplates();
  }, [loadRules, loadTemplates]);

  const onApplyTemplate = useCallback((tpl) => {
    setEditor({
      mode: 'create',
      rule: {
        name: tpl.name,
        description: tpl.description,
        priority: tpl.priority,
        conditions: tpl.conditions,
        actions: tpl.actions,
        status: 'active',
      },
    });
  }, []);

  return html`
    <div class="cl-rules">
      <header class="cl-rules-header">
        <div>
          <h1>Approval rules</h1>
          <p class="cl-rules-sub">
            Teach the agent how to route AP invoices. Rules evaluate first;
            anything that doesn't match falls through to the deterministic
            policy cascade.
          </p>
        </div>
        <div class="cl-rules-header-actions">
          <button class="btn btn-secondary" onClick=${() => setTester(true)}>
            Test mode
          </button>
          <button class="btn btn-primary" onClick=${() => setEditor({
            mode: 'create',
            rule: {
              name: '',
              description: '',
              priority: 100,
              conditions: { all_of: [] },
              actions: [],
              status: 'active',
            },
          })}>
            New rule
          </button>
        </div>
      </header>

      <${TemplatesGallery}
        templates=${templates}
        onApply=${onApplyTemplate}
      />

      <section class="cl-rules-filters" aria-label="Rule filters">
        <label class="cl-rules-filter">
          <span>Status</span>
          <select value=${filterStatus} onChange=${(e) => setFilterStatus(e.target.value)}>
            <option value="all">All</option>
            <option value="active">Active</option>
            <option value="paused">Paused</option>
            <option value="archived">Archived</option>
          </select>
        </label>
        <label class="cl-rules-filter">
          <span>Workflow</span>
          <select value=${filterWorkflow} onChange=${(e) => setFilterWorkflow(e.target.value)}>
            <option value="all">All</option>
            <option value="ap">AP</option>
          </select>
        </label>
        <label class="cl-rules-filter">
          <span>Entity</span>
          <input
            type="text"
            placeholder="entity id or blank"
            value=${filterEntity}
            onInput=${(e) => setFilterEntity(e.target.value)} />
        </label>
        ${(filterStatus !== 'all' || filterEntity || filterWorkflow !== 'all') ? html`
          <button type="button" class="btn btn-tertiary btn-sm" onClick=${() => {
            setFilterStatus('all');
            setFilterEntity('');
            setFilterWorkflow('all');
          }}>Clear</button>
        ` : null}
      </section>

      <${RuleList}
        rules=${rules}
        loading=${loading}
        onEdit=${(r) => setEditor({ mode: 'edit', rule: r })}
        onClone=${(r) => setEditor({
          mode: 'create',
          rule: { ...r, id: undefined, name: `${r.name} (copy)` },
        })}
        onArchive=${async (r) => {
          if (!window.confirm(`Archive rule '${r.name}'?`)) return;
          try {
            await api(`/api/workspace/rules/${r.id}`, { method: 'DELETE' });
            toast('Rule archived.', 'success');
            await loadRules();
          } catch (exc) {
            toast(`Archive failed: ${String(exc?.message || exc)}`, 'error');
          }
        }}
        onVersions=${(r) => setVersionsForRule(r)}
      />

      ${editor ? html`
        <${RuleEditorDialog}
          api=${api}
          toast=${toast}
          mode=${editor.mode}
          rule=${editor.rule}
          onClose=${() => setEditor(null)}
          onSaved=${async () => {
            setEditor(null);
            await loadRules();
          }}
        />
      ` : null}

      ${tester ? html`
        <${TestModeDialog}
          api=${api}
          onClose=${() => setTester(false)}
        />
      ` : null}

      ${versionsForRule ? html`
        <${VersionsDialog}
          api=${api}
          toast=${toast}
          rule=${versionsForRule}
          onClose=${() => setVersionsForRule(null)}
          onReverted=${async () => {
            setVersionsForRule(null);
            await loadRules();
          }}
        />
      ` : null}
    </div>
  `;
}


// ─── Templates gallery ──────────────────────────────────────────────

function TemplatesGallery({ templates, onApply }) {
  if (!templates || templates.length === 0) return null;
  return html`
    <section class="cl-rules-templates" aria-label="Starter templates">
      <h2>Starter templates</h2>
      <p class="muted">
        Tap a template to start a new rule pre-filled with the body. Adjust priority,
        recipients, or conditions and save.
      </p>
      <div class="cl-rules-templates-grid">
        ${templates.map((tpl) => html`
          <div class="cl-rules-template-card" key=${tpl.id}>
            <h3>${tpl.name}</h3>
            <p class="muted">${tpl.description}</p>
            <button class="btn btn-secondary btn-sm" onClick=${() => onApply(tpl)}>
              Use this template
            </button>
          </div>
        `)}
      </div>
    </section>
  `;
}


// ─── Rule list ──────────────────────────────────────────────────────

function RuleList({ rules, loading, onEdit, onClone, onArchive, onVersions }) {
  if (loading && rules.length === 0) {
    return html`<p class="muted" style="padding:24px 0">Loading rules…</p>`;
  }
  if (!loading && rules.length === 0) {
    return html`
      <section class="cl-rules-empty">
        <h3>No rules yet</h3>
        <p>Pick a starter template above, or click "New rule" to build one from scratch.</p>
      </section>
    `;
  }

  return html`
    <section class="cl-rules-list-card">
      <header class="cl-rules-chart-head">
        <h3>Active + paused rules</h3>
        <span class="cl-rules-chart-meta">${rules.length} total</span>
      </header>
      <table class="cl-rules-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Priority</th>
            <th>Conditions</th>
            <th>Actions</th>
            <th>Status</th>
            <th>Updated</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${rules.map((r) => html`
            <tr key=${r.id}>
              <td>
                <strong>${r.name}</strong>
                ${r.description ? html`<div class="muted" style="font-size:12px">${r.description}</div>` : null}
              </td>
              <td class="cl-rules-num">${r.priority}</td>
              <td class="muted">${summarizeConditions(r.conditions)}</td>
              <td class="muted">${summarizeActions(r.actions)}</td>
              <td>
                <span class=${`cl-record-chip cl-record-chip-${statusTone(r.status)}`}>
                  ${r.status}
                </span>
              </td>
              <td class="muted">${r.updated_at ? fmtDateTime(r.updated_at) : '—'}</td>
              <td style="text-align:right">
                <button class="btn btn-tertiary btn-sm" onClick=${() => onEdit(r)}>Edit</button>
                <button class="btn btn-tertiary btn-sm" onClick=${() => onClone(r)}>Clone</button>
                <button class="btn btn-tertiary btn-sm" onClick=${() => onVersions(r)}>History</button>
                ${r.status !== 'archived' ? html`
                  <button class="btn btn-tertiary btn-sm" onClick=${() => onArchive(r)}>Archive</button>
                ` : null}
              </td>
            </tr>
          `)}
        </tbody>
      </table>
    </section>
  `;
}


// ─── Editor dialog ──────────────────────────────────────────────────

function RuleEditorDialog({ api, toast, mode, rule, onClose, onSaved }) {
  const [name, setName] = useState(rule.name || '');
  const [description, setDescription] = useState(rule.description || '');
  const [priority, setPriority] = useState(rule.priority ?? 100);
  const [status, setStatus] = useState(rule.status || 'active');
  const [conditionsText, setConditionsText] = useState(
    JSON.stringify(rule.conditions || { all_of: [] }, null, 2),
  );
  const [actionsText, setActionsText] = useState(
    JSON.stringify(rule.actions || [], null, 2),
  );
  const [changeNote, setChangeNote] = useState('');
  const [saving, setSaving] = useState(false);
  const [conflicts, setConflicts] = useState(null);
  const [validationErrors, setValidationErrors] = useState(null);

  const parseBody = useCallback(() => {
    let conditions;
    let actions;
    try {
      conditions = JSON.parse(conditionsText || '{}');
    } catch (exc) {
      throw new Error(`conditions JSON: ${exc.message}`);
    }
    try {
      actions = JSON.parse(actionsText || '[]');
    } catch (exc) {
      throw new Error(`actions JSON: ${exc.message}`);
    }
    return { conditions, actions };
  }, [conditionsText, actionsText]);

  const onSave = useCallback(async (force = false) => {
    setSaving(true);
    setConflicts(null);
    setValidationErrors(null);
    let parsed;
    try {
      parsed = parseBody();
    } catch (exc) {
      toast(String(exc.message || exc), 'error');
      setSaving(false);
      return;
    }
    const payload = {
      name, description, priority: Number(priority) || 100,
      status, force,
      conditions: parsed.conditions, actions: parsed.actions,
    };
    if (mode === 'edit' && changeNote) payload.change_note = changeNote;

    try {
      const url = mode === 'edit'
        ? `/api/workspace/rules/${rule.id}`
        : '/api/workspace/rules';
      await api(url, {
        method: mode === 'edit' ? 'PUT' : 'POST',
        body: JSON.stringify(payload),
      });
      toast(`Rule ${mode === 'edit' ? 'updated' : 'created'}.`, 'success');
      onSaved();
    } catch (exc) {
      const detail = exc?.response?.detail || exc?.detail || null;
      if (detail?.code === 'rule_validation_failed' && Array.isArray(detail.errors)) {
        setValidationErrors(detail.errors);
        toast('Rule body has schema errors. See the editor.', 'error');
      } else if (detail?.code === 'rule_conflict' && Array.isArray(detail.conflicts)) {
        setConflicts(detail.conflicts);
        toast('Rule conflicts with existing rules. Review before saving.', 'error');
      } else {
        toast(`Save failed: ${String(exc?.message || exc)}`, 'error');
      }
    } finally {
      setSaving(false);
    }
  }, [api, mode, rule, name, description, priority, status, parseBody, changeNote, toast, onSaved]);

  return html`
    <${Modal} onClose=${onClose} title=${mode === 'edit' ? `Edit "${rule.name}"` : 'New rule'}>
      <div class="cl-rules-editor">
        <div class="cl-rules-editor-row">
          <label style="flex:2">
            <span class="muted">Name</span>
            <input
              type="text"
              value=${name}
              onInput=${(e) => setName(e.target.value)}
              disabled=${saving}
              style="width:100%"
            />
          </label>
          <label style="width:120px">
            <span class="muted">Priority</span>
            <input
              type="number" min="0" max="9999"
              value=${priority}
              onInput=${(e) => setPriority(e.target.value)}
              disabled=${saving}
              style="width:100%"
            />
          </label>
          <label style="width:140px">
            <span class="muted">Status</span>
            <select value=${status} onChange=${(e) => setStatus(e.target.value)} disabled=${saving}>
              <option value="active">Active</option>
              <option value="paused">Paused</option>
              <option value="archived">Archived</option>
            </select>
          </label>
        </div>

        <label>
          <span class="muted">Description</span>
          <input
            type="text"
            value=${description}
            onInput=${(e) => setDescription(e.target.value)}
            placeholder="Short note for the next operator who reads this"
            disabled=${saving}
            style="width:100%"
          />
        </label>

        <${JsonEditor}
          label="Conditions (JSON)"
          value=${conditionsText}
          onInput=${setConditionsText}
          disabled=${saving}
          rows=${10}
        />

        <${JsonEditor}
          label="Actions (JSON)"
          value=${actionsText}
          onInput=${setActionsText}
          disabled=${saving}
          rows=${6}
        />

        ${mode === 'edit' ? html`
          <label>
            <span class="muted">Change note (optional)</span>
            <input
              type="text"
              value=${changeNote}
              onInput=${(e) => setChangeNote(e.target.value)}
              placeholder="Why this change?"
              disabled=${saving}
              style="width:100%"
            />
          </label>
        ` : null}

        ${validationErrors ? html`
          <div class="cl-rules-issue cl-rules-issue-error">
            <strong>Schema errors</strong>
            <ul>
              ${validationErrors.map((e, i) => html`
                <li key=${i}><code>${e.path}</code>: ${e.message}</li>
              `)}
            </ul>
          </div>
        ` : null}

        ${conflicts ? html`
          <div class="cl-rules-issue cl-rules-issue-warn">
            <strong>Conflicts with existing rules</strong>
            <ul>
              ${conflicts.map((c, i) => html`
                <li key=${i}>
                  <strong>${c.kind}</strong>: ${c.note}
                </li>
              `)}
            </ul>
            <p class="muted">
              You can save anyway — the rule will go in with these conflicts on record.
            </p>
            <button class="btn btn-secondary" onClick=${() => onSave(true)} disabled=${saving}>
              Save anyway
            </button>
          </div>
        ` : null}
      </div>

      <div class="cl-rules-editor-foot">
        <button class="btn btn-tertiary" onClick=${onClose} disabled=${saving}>Cancel</button>
        <button class="btn btn-primary" onClick=${() => onSave(false)} disabled=${saving}>
          ${saving ? 'Saving…' : (mode === 'edit' ? 'Save changes' : 'Create rule')}
        </button>
      </div>
    <//>
  `;
}


// ─── Test mode dialog ───────────────────────────────────────────────

function TestModeDialog({ api, onClose }) {
  const [invoiceText, setInvoiceText] = useState(JSON.stringify({
    amount: 500,
    currency: 'GBP',
    vendor_name: 'Test Vendor Inc',
    department: 'engineering',
    workflow: 'ap',
  }, null, 2));
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [err, setErr] = useState(null);

  const onRun = useCallback(async () => {
    setRunning(true);
    setErr(null);
    setResult(null);
    let invoice;
    try {
      invoice = JSON.parse(invoiceText || '{}');
    } catch (exc) {
      setErr(`Invoice JSON: ${exc.message}`);
      setRunning(false);
      return;
    }
    try {
      const resp = await api('/api/workspace/rules/test', {
        method: 'POST',
        body: JSON.stringify({ invoice }),
      });
      setResult(resp);
    } catch (exc) {
      setErr(String(exc?.message || exc));
    } finally {
      setRunning(false);
    }
  }, [api, invoiceText]);

  return html`
    <${Modal} onClose=${onClose} title="Test mode">
      <div class="cl-rules-editor">
        <p class="muted">
          Paste a synthetic invoice context below and click Run. Every active rule
          for this org evaluates against it and we show the full trace —
          which clauses matched, which didn't, and which rule (if any) fired first.
        </p>

        <label>
          <span class="muted">Invoice context (JSON)</span>
          <textarea
            value=${invoiceText}
            onInput=${(e) => setInvoiceText(e.target.value)}
            disabled=${running}
            rows="10"
            class="cl-rules-json"
          ></textarea>
        </label>

        <button class="btn btn-primary" onClick=${onRun} disabled=${running}>
          ${running ? 'Running…' : 'Run'}
        </button>

        ${err ? html`<p class="cl-rules-issue cl-rules-issue-error">${err}</p>` : null}

        ${result ? html`
          <div class="cl-rules-test-result">
            <h3>Result</h3>
            ${result.result.matched_rule_id ? html`
              <p>
                Matched <strong>${result.result.matched_rule_name}</strong>
                (${result.result.matched_rule_id})
              </p>
              <pre><code>${JSON.stringify(result.result.actions, null, 2)}</code></pre>
            ` : html`
              <p class="muted">No rule matched — falls through to the cascade.</p>
            `}

            <h3>Trace</h3>
            <ol class="cl-rules-trace">
              ${(result.result.trace || []).map((rt, idx) => html`
                <li key=${idx} class=${rt.matched ? 'is-matched' : ''}>
                  <strong>${rt.rule_name}</strong>
                  <span class="muted">priority ${rt.priority}</span>
                  ${rt.matched
                    ? html`<span class="cl-record-chip cl-record-chip-success">matched</span>`
                    : html`<span class="muted">${rt.skipped_reason || 'no match'}</span>`}
                  ${(rt.all_of || []).length > 0 ? html`
                    <details>
                      <summary class="muted">all_of (${rt.all_of.length})</summary>
                      <ul>
                        ${rt.all_of.map((c, i) => html`
                          <li key=${i}>
                            <code>${c.field} ${c.op} ${JSON.stringify(c.expected)}</code>
                            → actual <code>${JSON.stringify(c.actual)}</code>
                            ${c.matched
                              ? html`<span style="color:#14532D">✓</span>`
                              : html`<span style="color:#991B1B">✗</span>`}
                          </li>
                        `)}
                      </ul>
                    </details>
                  ` : null}
                </li>
              `)}
            </ol>
          </div>
        ` : null}
      </div>
    <//>
  `;
}


// ─── Versions dialog ────────────────────────────────────────────────

function VersionsDialog({ api, toast, rule, onClose, onReverted }) {
  const [versions, setVersions] = useState([]);
  const [loading, setLoading] = useState(false);
  const [reverting, setReverting] = useState(false);

  const loadVersions = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await api(`/api/workspace/rules/${rule.id}/versions`);
      setVersions(resp?.versions || []);
    } catch (exc) {
      toast(`Failed to load versions: ${String(exc?.message || exc)}`, 'error');
    } finally {
      setLoading(false);
    }
  }, [api, rule.id, toast]);

  useEffect(() => { loadVersions(); }, [loadVersions]);

  const onRevert = useCallback(async (versionNumber) => {
    if (!window.confirm(`Revert to version ${versionNumber}? Creates a new version with that body.`)) {
      return;
    }
    setReverting(true);
    try {
      await api(`/api/workspace/rules/${rule.id}/revert/${versionNumber}`, {
        method: 'POST',
      });
      toast(`Reverted to v${versionNumber}.`, 'success');
      onReverted();
    } catch (exc) {
      toast(`Revert failed: ${String(exc?.message || exc)}`, 'error');
    } finally {
      setReverting(false);
    }
  }, [api, rule.id, toast, onReverted]);

  return html`
    <${Modal} onClose=${onClose} title=${`Versions — ${rule.name}`}>
      ${loading ? html`<p class="muted">Loading…</p>` : null}
      ${!loading && versions.length === 0 ? html`
        <p class="muted">No versions recorded yet.</p>
      ` : null}
      ${versions.length > 0 ? html`
        <ol class="cl-rules-versions">
          ${versions.map((v) => html`
            <li key=${v.version_number}>
              <div class="cl-rules-version-head">
                <strong>v${v.version_number}</strong>
                <span class="muted">${v.changed_at ? fmtDateTime(v.changed_at) : ''}</span>
                ${v.changed_by ? html`<span class="muted">by ${v.changed_by}</span>` : null}
              </div>
              ${v.change_note ? html`<p class="muted">${v.change_note}</p>` : null}
              <details>
                <summary class="muted">Body</summary>
                <pre><code>${JSON.stringify({
                  name: v.name,
                  priority: v.priority,
                  status: v.status,
                  conditions: v.conditions,
                  actions: v.actions,
                }, null, 2)}</code></pre>
              </details>
              <button
                class="btn btn-tertiary btn-sm"
                onClick=${() => onRevert(v.version_number)}
                disabled=${reverting}
              >
                Revert to this version
              </button>
            </li>
          `)}
        </ol>
      ` : null}
    <//>
  `;
}


// ─── Modal primitive ────────────────────────────────────────────────

function Modal({ title, onClose, children }) {
  return html`
    <div class="cl-modal-backdrop" role="dialog" aria-modal="true" onClick=${onClose}>
      <div class="cl-modal" onClick=${(e) => e.stopPropagation()}>
        <header class="cl-modal-head">
          <h2>${title}</h2>
          <button class="btn btn-tertiary" onClick=${onClose} aria-label="Close">×</button>
        </header>
        <div class="cl-modal-body">${children}</div>
      </div>
    </div>
  `;
}


// ─── Helpers ───────────────────────────────────────────────────────

function summarizeConditions(conditions) {
  if (!conditions || typeof conditions !== 'object') return '—';
  const all = (conditions.all_of || []).length;
  const any = (conditions.any_of || []).length;
  if (!all && !any) return 'matches anything';
  const parts = [];
  if (all) parts.push(`${all} all_of`);
  if (any) parts.push(`${any} any_of`);
  return parts.join(' · ');
}

function summarizeActions(actions) {
  if (!Array.isArray(actions) || actions.length === 0) return '—';
  return actions.map((a) => a.type).join(' + ');
}

function statusTone(status) {
  if (status === 'active') return 'success';
  if (status === 'paused') return 'warning';
  return 'info';
}


// ─── Hand-rolled JSON syntax highlighting ──────────────────────────
//
// Module 3 spec line 121 calls for "JSON-mode rule editor with
// structured schema validation, syntax highlighting, and inline
// conflict detection." The validation + conflict detection are
// already wired (workspace_rules.py). This component adds the
// highlighting without pulling CodeMirror (60kB) or Monaco.
//
// Approach: an editable <textarea> sits transparent over a <pre>
// that mirrors the same text with colored spans. The textarea
// captures input + caret; the pre below shows the colors. Both
// scroll in lockstep. Robust enough for the small JSON blobs the
// rule editor handles.

function JsonEditor({ label, value, onInput, disabled, rows }) {
  const [parseError, setParseError] = useState('');

  // Validate as the user types. Errors render below the editor.
  // Empty / whitespace-only is fine; only non-empty strings get
  // parsed.
  const onTextInput = (e) => {
    const next = e.target.value;
    onInput(next);
    if (next.trim()) {
      try { JSON.parse(next); setParseError(''); }
      catch (err) { setParseError(String(err?.message || 'Invalid JSON')); }
    } else {
      setParseError('');
    }
  };

  const onScroll = (e) => {
    const pre = e.target.previousElementSibling;
    if (pre) { pre.scrollTop = e.target.scrollTop; pre.scrollLeft = e.target.scrollLeft; }
  };

  const highlighted = highlightJson(value || '');

  return html`
    <label class="cl-rules-json-editor">
      <span class="muted">${label}</span>
      <div class="cl-rules-json-stack">
        <pre class="cl-rules-json-highlight" aria-hidden="true" dangerouslySetInnerHTML=${{ __html: highlighted + '\n' }}></pre>
        <textarea
          value=${value}
          onInput=${onTextInput}
          onScroll=${onScroll}
          disabled=${disabled}
          rows=${rows}
          spellcheck="false"
          class="cl-rules-json cl-rules-json-textarea"
        ></textarea>
      </div>
      ${parseError ? html`<small class="cl-rules-json-error">JSON: ${parseError}</small>` : null}
    </label>
  `;
}

const _HTML_ENTITIES = { '&': '&amp;', '<': '&lt;', '>': '&gt;' };
function escapeHtml(s) {
  return String(s).replace(/[&<>]/g, (c) => _HTML_ENTITIES[c]);
}

// Tokenise JSON without a parser. We want to colour:
//   - strings (with key vs value distinction by trailing colon)
//   - numbers, booleans, null
//   - punctuation (, : { } [ ])
// The regex captures whole tokens; anything that doesn't match
// (whitespace, partial input) flows through unchanged.
function highlightJson(text) {
  const escaped = escapeHtml(text);
  return escaped.replace(
    /("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g,
    (match, str, colon, bool, num) => {
      if (str !== undefined) {
        const cls = colon ? 'cl-tok-key' : 'cl-tok-string';
        return `<span class="${cls}">${str}</span>${colon || ''}`;
      }
      if (bool) {
        const cls = bool === 'null' ? 'cl-tok-null' : 'cl-tok-bool';
        return `<span class="${cls}">${bool}</span>`;
      }
      if (num) {
        return `<span class="cl-tok-number">${num}</span>`;
      }
      return match;
    },
  );
}
