import { createContext, h } from 'preact';
import { useCallback, useContext, useEffect, useMemo, useState } from 'preact/hooks';
import { html } from '../utils/htm.js';
import { api } from '../api/client.js';
import { useOrgId } from './BootstrapContext.js';

/**
 * Multi-entity context for the workspace.
 *
 * Cowrywise (and most enterprise customers) have multiple legal
 * entities under one org — Africa entity, US entity, holding
 * company, etc. The data model carries `entity_id` on every AP
 * item; queries can filter by it. The workspace surfaces this with
 * a topbar switcher that defaults to "All entities" but lets the
 * user scope to a specific one. Selection is persisted across
 * reloads via localStorage so the customer's view stays put.
 *
 * Pages that want to honour the active entity read it via
 * useActiveEntity() and append `&entity_id=X` to their fetches.
 * Pages that don't care (settings, status, etc.) just don't read
 * the hook.
 */

const EntityContext = createContext({
  entities: [],
  activeEntityId: null,
  setActiveEntityId: () => {},
  loading: false,
});

const STORAGE_KEY = 'clearledgr_active_entity_id';

function readPersisted() {
  try {
    return localStorage.getItem(STORAGE_KEY) || null;
  } catch { return null; }
}

function writePersisted(value) {
  try {
    if (value) localStorage.setItem(STORAGE_KEY, value);
    else localStorage.removeItem(STORAGE_KEY);
  } catch { /* private mode etc. */ }
}

export function EntityProvider({ children }) {
  const orgId = useOrgId();
  const [entities, setEntities] = useState([]);
  const [loading, setLoading] = useState(true);
  const [activeEntityId, setActiveEntityIdState] = useState(() => readPersisted());

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api(`/api/workspace/entities?organization_id=${encodeURIComponent(orgId)}`, { retry: false })
      .then((data) => {
        if (cancelled) return;
        const list = Array.isArray(data?.entities) ? data.entities : (Array.isArray(data) ? data : []);
        setEntities(list);
        // If the persisted entity isn't in the fetched list (changed
        // org, deleted entity), drop it back to "All entities".
        if (activeEntityId && !list.some((e) => String(e.id) === String(activeEntityId))) {
          setActiveEntityIdState(null);
          writePersisted(null);
        }
      })
      .catch(() => {
        if (cancelled) return;
        setEntities([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgId]);

  const setActiveEntityId = useCallback((id) => {
    const value = id || null;
    setActiveEntityIdState(value);
    writePersisted(value);
  }, []);

  const value = useMemo(() => ({
    entities, activeEntityId, setActiveEntityId, loading,
  }), [entities, activeEntityId, setActiveEntityId, loading]);

  return html`<${EntityContext.Provider} value=${value}>${children}<//>`;
}

export function useEntities() {
  return useContext(EntityContext);
}

export function useActiveEntity() {
  const { entities, activeEntityId } = useContext(EntityContext);
  if (!activeEntityId) return null;
  return entities.find((e) => String(e.id) === String(activeEntityId)) || null;
}
