import { describe, it, expect, vi } from 'vitest';
import { h } from 'preact';
import { render, screen, fireEvent } from '@testing-library/preact';
import htm from 'htm';
import ActionDialog from './ActionDialog.js';

const html = htm.bind(h);

describe('ActionDialog', () => {
  it('renders nothing when not visible', () => {
    const { container } = render(html`<${ActionDialog} visible=${false} config=${{}} onClose=${() => {}} />`);
    expect(container.innerHTML).toBe('');
  });

  it('renders dialog when visible', () => {
    render(html`<${ActionDialog} visible=${true} config=${{ title: 'Reject invoice', label: 'Reason' }} onClose=${() => {}} />`);
    expect(screen.getByText('Reject invoice')).toBeTruthy();
    expect(screen.getByText('Reason')).toBeTruthy();
  });

  it('renders reason chips', () => {
    render(html`<${ActionDialog} visible=${true} config=${{ actionType: 'reject', title: 'Reject' }} onClose=${() => {}} />`);
    expect(screen.getByText('Duplicate invoice')).toBeTruthy();
    expect(screen.getByText('Incorrect amount')).toBeTruthy();
  });

  it('calls onClose with null on cancel', () => {
    const onClose = vi.fn();
    render(html`<${ActionDialog} visible=${true} config=${{ title: 'Test' }} onClose=${onClose} />`);
    fireEvent.click(screen.getByText('Cancel'));
    expect(onClose).toHaveBeenCalledWith(null);
  });

  it('calls onClose with value on confirm', () => {
    const onClose = vi.fn();
    render(html`<${ActionDialog} visible=${true} config=${{ title: 'Test', required: false }} onClose=${onClose} />`);
    const input = document.querySelector('.cl-action-dialog-input');
    fireEvent.input(input, { target: { value: 'my reason' } });
    fireEvent.click(screen.getByText('Confirm'));
    expect(onClose).toHaveBeenCalledWith('my reason');
  });

  it('does not confirm with empty value when required', () => {
    const onClose = vi.fn();
    render(html`<${ActionDialog} visible=${true} config=${{ title: 'Test', required: true }} onClose=${onClose} />`);
    fireEvent.click(screen.getByText('Confirm'));
    expect(onClose).not.toHaveBeenCalled();
  });

  it('clicking chip appends to input value', () => {
    render(html`<${ActionDialog} visible=${true} config=${{ actionType: 'reject', title: 'Reject' }} onClose=${() => {}} />`);
    fireEvent.click(screen.getByText('Duplicate invoice'));
    const input = document.querySelector('.cl-action-dialog-input');
    expect(input.value).toBe('Duplicate invoice');
  });

  it('closes on backdrop click', () => {
    const onClose = vi.fn();
    render(html`<${ActionDialog} visible=${true} config=${{ title: 'Test' }} onClose=${onClose} />`);
    const backdrop = document.querySelector('.cl-action-dialog');
    fireEvent.click(backdrop);
    expect(onClose).toHaveBeenCalledWith(null);
  });

  it('renders custom confirm/cancel labels', () => {
    render(html`<${ActionDialog} visible=${true} config=${{ title: 'Test', confirmLabel: 'Do it', cancelLabel: 'Nope' }} onClose=${() => {}} />`);
    expect(screen.getByText('Do it')).toBeTruthy();
    expect(screen.getByText('Nope')).toBeTruthy();
  });

  it('renders hint text for required vs optional', () => {
    const { rerender } = render(html`<${ActionDialog} visible=${true} config=${{ title: 'Test', required: true }} onClose=${() => {}} />`);
    expect(screen.getByText(/required/)).toBeTruthy();
    rerender(html`<${ActionDialog} visible=${true} config=${{ title: 'Test', required: false }} onClose=${() => {}} />`);
    expect(screen.getByText(/Optional/)).toBeTruthy();
  });
});
