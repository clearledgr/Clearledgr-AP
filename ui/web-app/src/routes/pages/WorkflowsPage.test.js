import { describe, it, expect, vi } from 'vitest';
import { h } from 'preact';
import { render, screen, fireEvent, waitFor } from '@testing-library/preact';
import WorkflowsPage from './WorkflowsPage.js';

function makeApi(specs = [], boxes = []) {
  const calls = [];
  const api = vi.fn(async (path, opts = {}) => {
    calls.push({ path, opts });
    const method = (opts.method || 'GET').toUpperCase();
    if (path === '/api/workspace/workflow-specs' && method === 'GET') {
      return { workflow_specs: specs };
    }
    if (path === '/api/workspace/workflow-specs/validate' && method === 'POST') {
      return { valid: true, errors: [] };
    }
    if (path === '/api/workspace/workflow-specs' && method === 'POST') {
      return { box_type: opts.body.box_type, version: 1, status: 'draft' };
    }
    if (path.endsWith('/activate')) return { status: 'active' };
    if (path.startsWith('/api/workspace/workflows/') && method === 'GET') {
      return { boxes };
    }
    return {};
  });
  return { api, calls };
}

function mount(api) {
  return render(h(WorkflowsPage, { api, orgId: 'org', toast: () => {} }));
}

describe('WorkflowsPage', () => {
  it('shows the empty state when no specs exist', async () => {
    const { api } = makeApi([]);
    mount(api);
    await waitFor(() => expect(screen.getByText('No workflow types yet')).toBeTruthy());
  });

  it('lists existing specs with an activate action on drafts', async () => {
    const { api } = makeApi([
      { box_type: 'contract_review', version: 1, status: 'draft', spec_json: {} },
    ]);
    mount(api);
    await waitFor(() => expect(screen.getByText('contract_review')).toBeTruthy());
    expect(screen.getByText('Activate')).toBeTruthy();
  });

  it('add states as chips, then validate posts the built spec', async () => {
    const { api, calls } = makeApi([]);
    mount(api);
    await waitFor(() => screen.getByText('No workflow types yet'));
    // Comma-paste adds multiple state chips in one go.
    fireEvent.input(document.querySelector('.wf-states'), {
      target: { value: 'draft, approved' },
    });
    fireEvent.click(screen.getByText('Add'));
    // Chips rendered.
    await waitFor(() => expect(document.querySelector('.wf-state-chips')).toBeTruthy());
    fireEvent.click(screen.getByText('Validate'));
    await waitFor(() => {
      expect(calls.some(
        (c) => c.path === '/api/workspace/workflow-specs/validate'
          && (c.opts.method || '').toUpperCase() === 'POST'
          && Array.isArray(c.opts.body?.states)
          && c.opts.body.states.length === 2,
      )).toBe(true);
    });
  });

  it('save draft posts a new spec', async () => {
    const { api, calls } = makeApi([]);
    mount(api);
    await waitFor(() => screen.getByText('No workflow types yet'));
    const typeInput = document.querySelector('.wf-builder input');
    fireEvent.input(typeInput, { target: { value: 'contract_review' } });
    fireEvent.input(document.querySelector('.wf-states'), {
      target: { value: 'draft, approved' },
    });
    fireEvent.click(screen.getByText('Add'));
    fireEvent.click(screen.getByText('Save draft'));
    await waitFor(() => {
      expect(calls.some(
        (c) => c.path === '/api/workspace/workflow-specs'
          && (c.opts.method || '').toUpperCase() === 'POST'
          && c.opts.body?.box_type === 'contract_review'
          && c.opts.body?.states?.length === 2,
      )).toBe(true);
    });
  });

  it('applies a template and fills states', async () => {
    const { api } = makeApi([]);
    mount(api);
    await waitFor(() => screen.getByText('No workflow types yet'));
    fireEvent.click(screen.getByText('Approval'));
    await waitFor(() => {
      const chips = document.querySelector('.wf-state-chips');
      expect(chips).toBeTruthy();
      expect(chips.textContent).toContain('pending_approval');
    });
  });

  it('activate posts to the activate endpoint', async () => {
    const { api, calls } = makeApi([
      { box_type: 'contract_review', version: 2, status: 'draft', spec_json: {} },
    ]);
    mount(api);
    await waitFor(() => screen.getByText('Activate'));
    fireEvent.click(screen.getByText('Activate'));
    await waitFor(() => {
      expect(calls.some(
        (c) => c.path === '/api/workspace/workflow-specs/contract_review/versions/2/activate'
          && (c.opts.method || '').toUpperCase() === 'POST',
      )).toBe(true);
    });
  });

  it('view boxes loads boxes for an active type', async () => {
    const { api, calls } = makeApi(
      [{ box_type: 'contract_review', version: 1, status: 'active', spec_json: { action_states: { approve: 'approved' } } }],
      [{ id: 'CR-1', state: 'draft' }],
    );
    mount(api);
    await waitFor(() => screen.getByText('View boxes'));
    fireEvent.click(screen.getByText('View boxes'));
    await waitFor(() => {
      expect(calls.some((c) => c.path === '/api/workspace/workflows/contract_review')).toBe(true);
    });
    await waitFor(() => expect(screen.getByText('CR-1')).toBeTruthy());
  });
});
