/**
 * Workflows Page — the no-code builder for declarative Box types (Level 2).
 *
 * An admin defines a workflow type from data alone: add states as chips, mark
 * terminal states, wire transitions by clicking, and name actions. Start from
 * a template or from scratch. Validate, save a draft, activate a version. Once
 * active, create boxes and drive them through their declared actions — all
 * against /api/workspace/workflow-specs and /api/workspace/workflows.
 */
import { h } from 'preact';
import { useEffect, useMemo, useState } from 'preact/hooks';
import htm from 'htm';
import { useAction } from '../route-helpers.js';
import { EmptyState, LoadingSkeleton, ErrorRetry } from '../../components/StatePrimitives.js';

const html = htm.bind(h);

const csv = (s) => String(s || '').split(',').map((x) => x.trim()).filter(Boolean);
const slugify = (s) => String(s || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

const STATUS_TONE = {
  draft: { bg: '#F4F4F5', fg: '#52525B', bd: '#D4D4D8' },
  active: { bg: '#ECFDF5', fg: '#0A663E', bd: '#86EFAC' },
  archived: { bg: '#FEF2F2', fg: '#B91C1C', bd: '#FECACA' },
};
// Active/selected chip tone (terminal toggle, transition target on).
const ON = 'background:var(--cl-teal-50,#E6FAF7);color:var(--cl-teal-700,#0A6E64);border-color:var(--cl-teal-300,#7FD8CE);font-weight:600';

function statusChip(status) {
  const t = STATUS_TONE[status] || STATUS_TONE.draft;
  return html`<span class="secondary-chip" style=${`background:${t.bg};color:${t.fg};border-color:${t.bd}`}>${status}</span>`;
}

// Starter templates: prefill the whole graph, then customize.
const TEMPLATES = [
  {
    label: 'Approval',
    box_type: 'approval_request', url_slug: 'approval-requests',
    states: ['draft', 'pending_approval', 'approved', 'rejected'],
    initial_state: 'draft',
    terminal_states: ['approved', 'rejected'],
    transitions: { draft: ['pending_approval'], pending_approval: ['approved', 'rejected'] },
    action_states: { submit: 'pending_approval', approve: 'approved', reject: 'rejected' },
  },
  {
    label: 'Review',
    box_type: 'document_review', url_slug: 'document-reviews',
    states: ['draft', 'in_review', 'changes_requested', 'approved'],
    initial_state: 'draft',
    terminal_states: ['approved'],
    transitions: { draft: ['in_review'], in_review: ['approved', 'changes_requested'], changes_requested: ['in_review'] },
    action_states: { submit: 'in_review', approve: 'approved', request_changes: 'changes_requested', resubmit: 'in_review' },
  },
  {
    label: 'Intake → Triage → Done',
    box_type: 'request', url_slug: 'requests',
    states: ['new', 'triaged', 'in_progress', 'done', 'wont_do'],
    initial_state: 'new',
    terminal_states: ['done', 'wont_do'],
    transitions: { new: ['triaged'], triaged: ['in_progress', 'wont_do'], in_progress: ['done'] },
    action_states: { triage: 'triaged', start: 'in_progress', complete: 'done', dismiss: 'wont_do' },
  },
];

export default function WorkflowsPage({ api, orgId, toast }) {
  const [specs, setSpecs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);

  // Builder state.
  const [boxType, setBoxType] = useState('');
  const [urlSlug, setUrlSlug] = useState('');
  const [states, setStates] = useState([]);          // chips
  const [newState, setNewState] = useState('');
  const [initialState, setInitialState] = useState('');
  const [terminal, setTerminal] = useState({});       // { state: bool }
  const [transMap, setTransMap] = useState({});        // { state: [targets] }
  const [actions, setActions] = useState([{ action: '', target: '' }]);
  const [errors, setErrors] = useState([]);

  // Boxes panel.
  const [selectedType, setSelectedType] = useState('');
  const [boxes, setBoxes] = useState([]);

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

  // ── Builder mutations ──────────────────────────────────────────────
  const addState = () => {
    const incoming = csv(newState);              // supports pasting "a, b, c"
    if (!incoming.length) return;
    setStates((prev) => {
      const seen = new Set(prev);
      const next = [...prev];
      for (const s of incoming) if (!seen.has(s)) { seen.add(s); next.push(s); }
      if (!initialState && next.length) setInitialState(next[0]);
      return next;
    });
    setNewState('');
  };

  const removeState = (s) => {
    setStates((prev) => prev.filter((x) => x !== s));
    setTerminal(({ [s]: _drop, ...rest }) => rest);
    setTransMap((prev) => {
      const next = {};
      for (const [src, tgts] of Object.entries(prev)) {
        if (src === s) continue;
        next[src] = tgts.filter((t) => t !== s);
      }
      return next;
    });
    setInitialState((cur) => (cur === s ? '' : cur));
    setActions((prev) => prev.map((a) => (a.target === s ? { ...a, target: '' } : a)));
  };

  const toggleTerminal = (s) => setTerminal((prev) => ({ ...prev, [s]: !prev[s] }));

  const toggleTransition = (src, tgt) => setTransMap((prev) => {
    const cur = prev[src] || [];
    const next = cur.includes(tgt) ? cur.filter((t) => t !== tgt) : [...cur, tgt];
    return { ...prev, [src]: next };
  });

  const applyTemplate = (t) => {
    setStates(t.states);
    setInitialState(t.initial_state);
    const term = {};
    t.terminal_states.forEach((s) => { term[s] = true; });
    setTerminal(term);
    setTransMap({ ...t.transitions });
    setActions(Object.entries(t.action_states).map(([action, target]) => ({ action, target })));
    if (!boxType.trim()) { setBoxType(t.box_type); setUrlSlug(t.url_slug); }
    setErrors([]);
  };

  const buildSpec = () => {
    const transitions = {};
    for (const s of states) {
      const t = transMap[s] || [];
      if (t.length) transitions[s] = t;
    }
    const action_states = {};
    for (const { action, target } of actions) {
      if (action && target) action_states[action.trim()] = target;
    }
    return {
      box_type: boxType.trim(),
      url_slug: (urlSlug.trim() || slugify(boxType)),
      states,
      initial_state: initialState,
      terminal_states: states.filter((s) => terminal[s]),
      transitions,
      action_states,
      fields: [],
    };
  };

  const [validateSpec, validating] = useAction(async () => {
    const res = await api('/api/workspace/workflow-specs/validate', { method: 'POST', body: buildSpec() });
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
    <div class="secondary-banner">
      <div class="secondary-banner-copy">
        <h3>Workflows</h3>
        <p class="muted">Define custom workflow types — states, transitions, and actions — with no code. Activate one, then run boxes through it on the same runtime as AP and Procurement.</p>
      </div>
    </div>

    <div class="panel wf-builder">
      <div class="panel-head compact"><h3>New workflow type</h3></div>

      <div style="margin-bottom:16px">
        <div class="muted" style="font-weight:500; margin-bottom:8px">Start from a template (optional)</div>
        <div class="secondary-card-tags">
          ${TEMPLATES.map((t) => html`
            <button key=${t.label} class="btn-ghost btn-sm" onClick=${() => applyTemplate(t)}>${t.label}</button>`)}
        </div>
      </div>

      <div class="secondary-form-grid">
        <label>Type name (snake_case)
          <input value=${boxType} onInput=${(e) => { setBoxType(e.target.value); if (!urlSlug) setUrlSlug(''); }} placeholder="contract_review" />
        </label>
        <label>URL slug
          <input value=${urlSlug} onInput=${(e) => setUrlSlug(e.target.value)} placeholder=${slugify(boxType) || 'contract-reviews'} />
        </label>
      </div>

      <div style="margin-top:18px">
        <div class="muted" style="font-weight:500; margin-bottom:8px">States</div>
        <div class="secondary-inline-actions" style="margin-bottom:10px">
          <input
            class="wf-states"
            style="height:36px;padding:0 12px;border:1px solid var(--cl-border);border-radius:var(--cl-radius-sm);font-size:13px;min-width:240px"
            value=${newState}
            onInput=${(e) => setNewState(e.target.value)}
            onKeyDown=${(e) => { if (e.key === 'Enter') { e.preventDefault(); addState(); } }}
            placeholder="add a state, e.g. draft" />
          <button class="btn-secondary btn-sm" onClick=${addState}>Add</button>
        </div>
        ${states.length === 0
          ? html`<p class="muted" style="font-size:12px;margin:0">No states yet. Add a few (or pick a template) to wire up the rest.</p>`
          : html`<div class="secondary-card-tags wf-state-chips">
              ${states.map((s) => html`
                <span key=${s} class="secondary-chip" style="gap:6px">
                  ${s}
                  <button onClick=${() => removeState(s)} title="Remove state"
                    style="border:none;background:none;cursor:pointer;color:inherit;font-size:14px;line-height:1;padding:0">×</button>
                </span>`)}
            </div>`}
      </div>

      ${states.length > 0 && html`
        <div style="margin-top:18px; display:grid; gap:18px;">
          <div class="secondary-form-grid">
            <label>Initial state
              <select value=${initialState} onChange=${(e) => setInitialState(e.target.value)}>
                <option value="">— pick —</option>
                ${states.map((s) => html`<option value=${s}>${s}</option>`)}
              </select>
            </label>
          </div>

          <div>
            <div class="muted" style="font-weight:500; margin-bottom:8px">Terminal states <span style="font-weight:400">(click to toggle — a box stops here)</span></div>
            <div class="secondary-card-tags">
              ${states.map((s) => html`
                <button key=${s} class="secondary-chip" style=${`cursor:pointer;${terminal[s] ? ON : ''}`}
                  onClick=${() => toggleTerminal(s)}>${s}${terminal[s] ? ' ✓' : ''}</button>`)}
            </div>
          </div>

          <div>
            <div class="muted" style="font-weight:500; margin-bottom:8px">Transitions <span style="font-weight:400">(click the states each state can move to)</span></div>
            <div style="display:grid; gap:10px">
              ${states.filter((s) => !terminal[s]).map((src) => html`
                <div key=${src} style="display:flex; gap:10px; align-items:center; flex-wrap:wrap">
                  <span class="secondary-chip" style="font-weight:600; min-width:110px; justify-content:center">${src} →</span>
                  ${states.filter((t) => t !== src).map((tgt) => {
                    const on = (transMap[src] || []).includes(tgt);
                    return html`<button key=${tgt} class="secondary-chip" style=${`cursor:pointer;${on ? ON : ''}`}
                      onClick=${() => toggleTransition(src, tgt)}>${tgt}</button>`;
                  })}
                </div>`)}
            </div>
          </div>

          <div>
            <div class="muted" style="font-weight:500; margin-bottom:8px">Actions <span style="font-weight:400">(a button operators click → the state it moves to)</span></div>
            <div class="secondary-form-stack">
              ${actions.map((a, i) => html`
                <div key=${i} class="secondary-form-grid" style="gap:8px">
                  <label>Action name
                    <input placeholder="approve" value=${a.action} onInput=${(e) => setActions(actions.map((x, j) => j === i ? { ...x, action: e.target.value } : x))} />
                  </label>
                  <label>Target state
                    <select value=${a.target} onChange=${(e) => setActions(actions.map((x, j) => j === i ? { ...x, target: e.target.value } : x))}>
                      <option value="">— pick —</option>
                      ${states.map((s) => html`<option value=${s}>${s}</option>`)}
                    </select>
                  </label>
                </div>`)}
              <div class="secondary-inline-actions">
                <button class="btn-ghost btn-sm" onClick=${() => setActions([...actions, { action: '', target: '' }])}>+ Add action</button>
                ${actions.length > 1 && html`<button class="btn-ghost btn-sm" onClick=${() => setActions(actions.slice(0, -1))}>Remove last</button>`}
              </div>
            </div>
          </div>
        </div>`}

      ${errors.length > 0 && html`
        <ul class="wf-errors" style="margin:16px 0 0; padding-left:18px">
          ${errors.map((e) => html`<li key=${e} class="form-error" style="list-style:disc">${e}</li>`)}
        </ul>`}

      <div class="secondary-inline-actions" style="margin-top:18px">
        <button class="btn-secondary" disabled=${validating} onClick=${validateSpec}>${validating ? 'Validating…' : 'Validate'}</button>
        <button class="btn-primary" disabled=${saving} onClick=${saveDraft}>${saving ? 'Saving…' : 'Save draft'}</button>
      </div>
    </div>

    <div class="panel">
      <div class="panel-head compact"><h3>Your workflow types</h3></div>
      ${specs.length === 0
        ? html`<${EmptyState} title="No workflow types yet" description="Define one above to get started." />`
        : html`
          <div class="secondary-card-list">
            ${specs.map((s) => html`
              <div key=${s.box_type + ':' + s.version} class="secondary-card">
                <div class="secondary-card-head">
                  <div class="secondary-card-copy">
                    <strong class="secondary-card-title">${s.box_type}</strong>
                    <div class="secondary-card-meta">version ${s.version}</div>
                    <div class="secondary-card-tags">${statusChip(s.status)}</div>
                  </div>
                </div>
                <div class="secondary-card-actions">
                  ${s.status === 'draft' && html`<button class="btn-primary btn-sm" onClick=${() => activate(s.box_type, s.version)}>Activate</button>`}
                  ${s.status === 'active' && html`<button class="btn-secondary btn-sm" onClick=${() => viewBoxes(s.box_type)}>View boxes</button>`}
                </div>
              </div>`)}
          </div>`}
    </div>

    ${selectedType && html`
      <div class="panel">
        <div class="panel-head compact" style="display:flex; justify-content:space-between; align-items:center">
          <h3>${selectedType} boxes</h3>
          <button class="btn-primary btn-sm" onClick=${createBox}>New box</button>
        </div>
        ${boxes.length === 0
          ? html`<${EmptyState} title="No boxes" description="Create one to start." />`
          : html`
            <div class="secondary-card-list">
              ${boxes.map((b) => html`
                <div key=${b.id} class="secondary-card">
                  <div class="secondary-card-head">
                    <div class="secondary-card-copy">
                      <strong class="secondary-card-title">${b.id}</strong>
                      <div class="secondary-card-tags"><span class="secondary-chip">${b.state}</span></div>
                    </div>
                  </div>
                  <div class="secondary-card-actions">
                    ${selectedActions.map(([action]) => html`
                      <button class="btn-secondary btn-sm" onClick=${() => actOnBox(b.id, action)}>${action}</button>`)}
                  </div>
                </div>`)}
            </div>`}
      </div>`}
  `;
}
