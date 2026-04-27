import { useEffect, useRef, useState } from 'preact/hooks';
import { html } from '../utils/htm.js';
import { useEntities } from './EntityContext.js';

/**
 * Topbar entity switcher dropdown.
 *
 * Renders nothing if the org has zero or one entity (no point
 * showing a control with one option). For multi-entity orgs,
 * shows the active entity name + chevron and reveals a panel
 * listing every entity with an "All entities" reset on top.
 */
export function EntitySwitcher() {
  const { entities, activeEntityId, setActiveEntityId, loading } = useEntities();
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(event) {
      if (wrapperRef.current && !wrapperRef.current.contains(event.target)) {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [open]);

  if (loading || entities.length <= 1) return null;

  const active = entities.find((e) => String(e.id) === String(activeEntityId));
  const activeName = active ? (active.name || active.code || 'Entity') : 'All entities';

  return html`
    <div class="cl-entity-switcher" ref=${wrapperRef}>
      <button
        class=${`cl-entity-trigger ${open ? 'is-open' : ''}`}
        onClick=${() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded=${open}>
        <span class="cl-entity-label">Entity</span>
        <span class="cl-entity-name">${activeName}</span>
        <span class="cl-entity-chevron" aria-hidden="true">▾</span>
      </button>
      ${open
        ? html`
            <div class="cl-entity-menu" role="menu">
              <button
                class=${`cl-entity-item ${!activeEntityId ? 'is-active' : ''}`}
                onClick=${() => { setActiveEntityId(null); setOpen(false); }}>
                <span class="cl-entity-item-name">All entities</span>
                <span class="cl-entity-item-meta">Aggregate view</span>
              </button>
              <div class="cl-entity-divider" aria-hidden="true"></div>
              ${entities.map((entity) => html`
                <button
                  key=${entity.id}
                  class=${`cl-entity-item ${String(entity.id) === String(activeEntityId) ? 'is-active' : ''}`}
                  onClick=${() => { setActiveEntityId(entity.id); setOpen(false); }}>
                  <span class="cl-entity-item-name">${entity.name || entity.code || 'Entity'}</span>
                  ${entity.code && entity.name && entity.code !== entity.name
                    ? html`<span class="cl-entity-item-meta">${entity.code}</span>`
                    : null}
                </button>
              `)}
            </div>
          `
        : null}
    </div>
  `;
}
