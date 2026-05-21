/**
 * Workflows Page — the no-code builder for declarative Box types (Level 2).
 *
 * An admin defines a workflow type from data alone: states, transitions, and
 * actions. Validate against the backend, save a draft, activate a version.
 * Once a type is active, create boxes of it and drive them through their
 * declared actions — all against the generic /api/workspace/workflow-specs and
 * /api/workspace/workflows endpoints. No deploy, no code.
 *
 * NOTE: written to the established page patterns; pending browser QA.
 */
import { h } from 'preact';
import { useEffect, useMemo, useState } from 'preact/hooks';
import htm from 'htm';
import { useAction } from '../route-helpers.js';
import { EmptyState, LoadingSkeleton, ErrorRetry } from '../../components/StatePrimitives.js';

const html = htm.bind(h);

const csv = (s) => String(s || '').split(',').map((x) => x.trim()).filter(Boolean);

export default function WorkflowsPage({ api, orgId, toast }) {
  const [specs, setSpecs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);

  // Builder state.
  const [boxType, setBoxType] = useState('');
  const [urlSlug, setUrlSlug] = useState('');
  const [statesText, setStatesText] = useState('');
  const [initialState, setInitialState] = useState('');
  const [terminal, setTerminal] = useState({});
  const [transText, setTransText] = useState({});
  const [actions, setActions] = useState([{ action: '', target: '' }]);
  const [errors, setErrors] = useState([]);

  // Boxes panel.
  const [selectedType, setSelectedType] = useState('');
  const [boxes, setBoxes] = useState([]);

  const states = useMemo(() => csv(statesText), [statesText]);

  const loadSpecs = async ({ silent = false } = {}) => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await api('/api/workspace/workflow-specs', { silent });
      setSpecs(Array.isArray(data?.workflow_specs) ? data.workflow_specs : []);
    } catch (exc) {
      setSpecs([]);
      setLoadError(exc?.message || 'Could not load workflows.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void loadSpecs({ silent: true }); }, [api, orgId]);

  const buildSpec = () => {
    const transitions = {};
    for (const s of states) {
      const t = csv(transText[s]);
      if (t.length) transitions[s] = t;
    }
    const action_states = {};
    for (const { action, target } of actions) {
      if (action && target) action_states[action.trim()] = target;
    }
    return {
      box_type: boxType.trim(),
      url_slug: (urlSlug.trim() || boxType.trim().replace(/_/g, '-')),
      states,
      initial_state: initialState,
      terminal_states: states.filter((s) => terminal[s]),
      transitions,
      action_states,
      fields: [],
    };
  };

  const [validateSpec, validating] = useAction(async () => {
    const res = await api('/api/workspace/workflow-specs/validate', {
      method: 'POST', body: buildSpec(),
    });
    setErrors(res?.errors || []);
    toast?.(res?.valid ? 'Spec is valid.' : 'Spec has errors.', res?.valid ? 'success' : 'error');
  });

  const [saveDraft, saving] = useAction(async () => {
    try {
      const row = await api('/api/workspace/workflow-specs', { method: 'POST', body: buildSpec() });
      setErrors([]);
      toast?.(`Saved ${row.box_type} v${row.version} (draft).`, 'success');
      await loadSpecs();
    } catch (exc) {
      toast?.(exc?.message || 'Could not save spec.', 'error');
    }
  });

  const activate = async (bt, version) => {
    try {
      await api(`/api/workspace/workflow-specs/${encodeURIComponent(bt)}/versions/${version}/activate`, { method: 'POST', body: {} });
      toast?.(`Activated ${bt} v${version}.`, 'success');
      await loadSpecs();
    } catch (exc) {
      toast?.(exc?.message || 'Could not activate.', 'error');
    }
  };

  const activeSpecFor = (bt) => specs.find((s) => s.box_type === bt && s.status === 'active');

  const viewBoxes = async (bt) => {
    setSelectedType(bt);
    try {
      const data = await api(`/api/workspace/workflows/${encodeURIComponent(bt)}`, { silent: true });
      setBoxes(Array.isArray(data?.boxes) ? data.boxes : []);
    } catch (exc) {
      setBoxes([]);
      toast?.(exc?.message || 'Could not load boxes.', 'error');
    }
  };

  const createBox = async () => {
    try {
      await api(`/api/workspace/workflows/${encodeURIComponent(selectedType)}`, { method: 'POST', body: { data: {} } });
      toast?.('Box created.', 'success');
      await viewBoxes(selectedType);
    } catch (exc) {
      toast?.(exc?.message || 'Could not create box.', 'error');
    }
  };

  const actOnBox = async (boxId, action) => {
    try {
      await api(`/api/workspace/workflows/${encodeURIComponent(selectedType)}/${encodeURIComponent(boxId)}/${encodeURIComponent(action)}`, { method: 'POST', body: { reason: '' } });
      toast?.(`${action} done.`, 'success');
      await viewBoxes(selectedType);
    } catch (exc) {
      toast?.(exc?.message || `Could not ${action}.`, 'error');
    }
  };

  if (loading) {
    return html`<div class="panel"><${LoadingSkeleton} rows=${5} label="Loading workflows" /></div>`;
  }
  if (loadError) {
    return html`<div class="panel"><${ErrorRetry} message="Couldn't load workflows." detail=${loadError} onRetry=${() => loadSpecs()} /></div>`;
  }

  const selectedActions = selectedType
    ? Object.entries(activeSpecFor(selectedType)?.spec_json?.action_states || {})
    : [];

  return html`
    <div class="panel">
      <div class="panel__header"><h1>Workflows</h1></div>

      <section class="wf-builder" style="border:1px solid var(--cl-border, #ddd); border-radius:8px; padding:16px; margin-bottom:20px;">
        <h2>New workflow type</h2>
        <div style="display:flex; gap:8px; flex-wrap:wrap;">
          <label>Type name (snake_case)
            <input class="input" value=${boxType} onInput=${(e) => setBoxType(e.target.value)} placeholder="contract_review" />
          </label>
          <label>URL slug
            <input class="input" value=${urlSlug} onInput=${(e) => setUrlSlug(e.target.value)} placeholder="contract-reviews" />
          </label>
          <label>States (comma-separated)
            <input class="input wf-states" value=${statesText} onInput=${(e) => setStatesText(e.target.value)} placeholder="draft, in_review, approved, rejected" />
          </label>
        </div>

        ${states.length > 0 && html`
          <div style="margin-top:12px;">
            <label>Initial state
              <select class="input" value=${initialState} onChange=${(e) => setInitialState(e.target.value)}>
                <option value="">— pick —</option>
                ${states.map((s) => html`<option value=${s}>${s}</option>`)}
              </select>
            </label>
            <div style="margin-top:8px;"><strong>Terminal states:</strong>
              ${states.map((s) => html`
                <label style="margin-left:8px;">
                  <input type="checkbox" checked=${!!terminal[s]} onChange=${(e) => setTerminal({ ...terminal, [s]: e.target.checked })} /> ${s}
                </label>`)}
            </div>
            <div style="margin-top:8px;"><strong>Transitions</strong>
              ${states.map((s) => html`
                <div key=${s} style="display:flex; gap:6px; align-items:center; margin:4px 0;">
                  <span style="min-width:120px;">${s} →</span>
                  <input class="input" placeholder="next states (comma)" value=${transText[s] || ''} onInput=${(e) => setTransText({ ...transText, [s]: e.target.value })} />
                </div>`)}
            </div>
            <div style="margin-top:8px;"><strong>Actions</strong>
              ${actions.map((a, i) => html`
                <div key=${i} style="display:flex; gap:6px; margin:4px 0;">
                  <input class="input" placeholder="action (e.g. approve)" value=${a.action} onInput=${(e) => setActions(actions.map((x, j) => j === i ? { ...x, action: e.target.value } : x))} />
                  <select class="input" value=${a.target} onChange=${(e) => setActions(actions.map((x, j) => j === i ? { ...x, target: e.target.value } : x))}>
                    <option value="">→ target</option>
                    ${states.map((s) => html`<option value=${s}>${s}</option>`)}
                  </select>
                </div>`)}
              <button class="btn btn--sm" onClick=${() => setActions([...actions, { action: '', target: '' }])}>+ action</button>
            </div>
          </div>`}

        ${errors.length > 0 && html`
          <ul class="wf-errors" style="color:var(--cl-danger,#b00); margin-top:10px;">
            ${errors.map((e) => html`<li key=${e}>${e}</li>`)}
          </ul>`}

        <div style="margin-top:12px; display:flex; gap:8px;">
          <button class="btn" disabled=${validating} onClick=${validateSpec}>Validate</button>
          <button class="btn btn--primary" disabled=${saving} onClick=${saveDraft}>${saving ? 'Saving…' : 'Save draft'}</button>
        </div>
      </section>

      <h2>Your workflow types</h2>
      ${specs.length === 0
        ? html`<${EmptyState} title="No workflow types yet" description="Define one above to get started." />`
        : html`
          <table class="data-table">
            <thead><tr><th>Type</th><th>Version</th><th>Status</th><th>Actions</th></tr></thead>
            <tbody>
              ${specs.map((s) => html`
                <tr key=${s.box_type + ':' + s.version}>
                  <td>${s.box_type}</td>
                  <td>v${s.version}</td>
                  <td><span class="badge">${s.status}</span></td>
                  <td>
                    ${s.status === 'draft' && html`<button class="btn btn--sm" onClick=${() => activate(s.box_type, s.version)}>Activate</button>`}
                    ${s.status === 'active' && html`<button class="btn btn--sm" onClick=${() => viewBoxes(s.box_type)}>View boxes</button>`}
                  </td>
                </tr>`)}
            </tbody>
          </table>`}

      ${selectedType && html`
        <section class="wf-boxes" style="margin-top:20px;">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <h2>${selectedType} boxes</h2>
            <button class="btn btn--primary btn--sm" onClick=${createBox}>New box</button>
          </div>
          ${boxes.length === 0
            ? html`<${EmptyState} title="No boxes" description="Create one to start." />`
            : html`
              <table class="data-table">
                <thead><tr><th>ID</th><th>State</th><th>Actions</th></tr></thead>
                <tbody>
                  ${boxes.map((b) => html`
                    <tr key=${b.id}>
                      <td>${b.id}</td>
                      <td><span class="badge">${b.state}</span></td>
                      <td>
                        ${selectedActions.map(([action]) => html`
                          <button class="btn btn--sm" onClick=${() => actOnBox(b.id, action)}>${action}</button>`)}
                      </td>
                    </tr>`)}
                </tbody>
              </table>`}
        </section>`}
    </div>
  `;
}
