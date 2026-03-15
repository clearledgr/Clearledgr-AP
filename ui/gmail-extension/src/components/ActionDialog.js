/** ActionDialog — focus-trapped reason sheet modal */
import { h } from 'preact';
import { useState, useEffect, useRef, useCallback } from 'preact/hooks';
import htm from 'htm';
import { getReasonSheetDefaults } from '../utils/formatters.js';

const html = htm.bind(h);

/**
 * Usage:
 *   const [dialog, openDialog] = useActionDialog();
 *   // In JSX: <${ActionDialog} ...dialog />
 *   // To open: const reason = await openDialog({ actionType: 'reject', title: 'Reject invoice' });
 */

export function useActionDialog() {
  const [state, setState] = useState({ visible: false, config: {}, resolve: null });

  const open = useCallback((config = {}) => {
    return new Promise((resolve) => {
      setState({ visible: true, config, resolve });
    });
  }, []);

  const close = useCallback((value) => {
    setState(prev => {
      prev.resolve?.(value);
      return { visible: false, config: {}, resolve: null };
    });
  }, []);

  return [{ ...state, onClose: close }, open];
}

export default function ActionDialog({ visible, config, onClose }) {
  const inputRef = useRef(null);
  const dialogRef = useRef(null);
  const [value, setValue] = useState('');

  const {
    actionType = 'generic',
    title = 'Add context',
    label = 'Reason',
    placeholder = '',
    defaultValue = '',
    confirmLabel = 'Confirm',
    cancelLabel = 'Cancel',
    required: requiredProp,
    chips: chipsProp,
  } = config || {};

  const defaults = getReasonSheetDefaults(actionType);
  const chipList = Array.isArray(chipsProp) && chipsProp.length ? chipsProp : defaults.chips;
  const isRequired = requiredProp !== undefined ? Boolean(requiredProp) : Boolean(defaults.required);

  useEffect(() => {
    if (visible) {
      setValue(defaultValue || '');
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [visible, defaultValue]);

  const handleConfirm = useCallback(() => {
    const trimmed = value.trim();
    if (isRequired && !trimmed) {
      inputRef.current?.focus();
      return;
    }
    onClose?.(trimmed);
  }, [value, isRequired, onClose]);

  const handleCancel = useCallback(() => onClose?.(null), [onClose]);

  const handleKeyDown = useCallback((e) => {
    if (e.key === 'Escape') { e.preventDefault(); handleCancel(); }
    else if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleConfirm(); }
  }, [handleCancel, handleConfirm]);

  const handleChip = useCallback((chip) => {
    setValue(prev => {
      const existing = prev.trim();
      return existing ? `${existing}; ${chip}` : chip;
    });
    inputRef.current?.focus();
  }, []);

  const handleBackdrop = useCallback((e) => {
    if (e.target === dialogRef.current) handleCancel();
  }, [handleCancel]);

  // Focus trap
  const handleDialogKeyDown = useCallback((e) => {
    if (e.key !== 'Tab') return;
    const focusable = dialogRef.current?.querySelectorAll('button:not([disabled]), input, [tabindex]:not([tabindex="-1"])');
    if (!focusable?.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }, []);

  if (!visible) return null;

  return html`
    <div ref=${dialogRef} class="cl-action-dialog" aria-hidden="false"
      onClick=${handleBackdrop} onKeyDown=${handleDialogKeyDown}>
      <div class="cl-action-dialog-card" role="dialog" aria-modal="true" aria-labelledby="cl-dialog-title">
        <div class="cl-action-dialog-title" id="cl-dialog-title">${title}</div>
        <label class="cl-action-dialog-label" id="cl-dialog-label">${label}</label>
        <div class="cl-action-dialog-chips">
          ${chipList.map((chip, i) => html`
            <button key=${i} type="button" class="cl-action-chip"
              onClick=${() => handleChip(chip)}>${chip}</button>
          `)}
        </div>
        <input ref=${inputRef} class="cl-action-dialog-input" type="text"
          value=${value} onInput=${e => setValue(e.target.value)}
          onKeyDown=${handleKeyDown} placeholder=${placeholder}
          aria-labelledby="cl-dialog-label" />
        <div class="cl-action-dialog-hint">
          ${isRequired ? 'A reason is required for this action.' : 'Optional note. Choose a quick reason or write your own.'}
        </div>
        <div class="cl-action-dialog-actions">
          <button class="cl-btn cl-btn-secondary cl-action-dialog-cancel" onClick=${handleCancel}>${cancelLabel}</button>
          <button class="cl-btn cl-action-dialog-confirm" onClick=${handleConfirm}>${confirmLabel}</button>
        </div>
      </div>
    </div>
  `;
}
