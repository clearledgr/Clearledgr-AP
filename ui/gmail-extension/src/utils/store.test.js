import { describe, it, expect, vi, beforeEach } from 'vitest';
import store from './store.js';

beforeEach(() => {
  store.queueState = [];
  store.selectedItemId = null;
  store.currentThreadId = null;
  store.scanStatus = {};
  store.auditState = { itemId: null, loading: false, events: [] };
  store.contextUiState = { itemId: null, loading: false, error: '' };
  store.activeContextTab = 'email';
  store.rowDecorated = new Set();
  store._listeners?.clear?.();
});

describe('store.update', () => {
  it('patches state and notifies listeners', () => {
    const listener = vi.fn();
    store.subscribe(listener);
    store.update({ scanStatus: { state: 'scanning' } });
    expect(store.scanStatus.state).toBe('scanning');
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('unsubscribe stops notifications', () => {
    const listener = vi.fn();
    const unsub = store.subscribe(listener);
    unsub();
    store.update({ scanStatus: { state: 'idle' } });
    expect(listener).not.toHaveBeenCalled();
  });
});

describe('store.findItemByThreadId', () => {
  it('finds by thread_id', () => {
    store.queueState = [{ id: '1', thread_id: 'abc' }, { id: '2', thread_id: 'def' }];
    expect(store.findItemByThreadId('def')?.id).toBe('2');
  });
  it('returns null for missing', () => {
    store.queueState = [{ id: '1', thread_id: 'abc' }];
    expect(store.findItemByThreadId('zzz')).toBeNull();
    expect(store.findItemByThreadId(null)).toBeNull();
  });
});

describe('store.findItemById', () => {
  it('finds by id', () => {
    store.queueState = [{ id: 'item-1' }, { id: 'item-2' }];
    expect(store.findItemById('item-2')?.id).toBe('item-2');
  });
  it('finds by invoice_key', () => {
    store.queueState = [{ id: 'x', invoice_key: 'inv-99' }];
    expect(store.findItemById('inv-99')?.id).toBe('x');
  });
  it('returns null for missing', () => {
    expect(store.findItemById('nope')).toBeNull();
  });
});

describe('store.getPrimaryItem', () => {
  it('prefers selectedItemId', () => {
    store.queueState = [{ id: 'a' }, { id: 'b' }];
    store.selectedItemId = 'b';
    expect(store.getPrimaryItem()?.id).toBe('b');
  });
  it('falls back to currentThreadId', () => {
    store.queueState = [{ id: 'a', thread_id: 't1' }, { id: 'b', thread_id: 't2' }];
    store.currentThreadId = 't2';
    expect(store.getPrimaryItem()?.id).toBe('b');
  });
  it('falls back to first item', () => {
    store.queueState = [{ id: 'first' }];
    expect(store.getPrimaryItem()?.id).toBe('first');
  });
  it('returns null for empty queue', () => {
    expect(store.getPrimaryItem()).toBeNull();
  });
});

describe('store.getPrimaryItemIndex', () => {
  it('returns correct index', () => {
    store.queueState = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];
    store.selectedItemId = 'b';
    expect(store.getPrimaryItemIndex()).toBe(1);
  });
  it('returns -1 for no item', () => {
    expect(store.getPrimaryItemIndex()).toBe(-1);
  });
});

describe('store.selectItemByOffset', () => {
  it('moves to next item', () => {
    const listener = vi.fn();
    store.subscribe(listener);
    store.queueState = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];
    store.selectedItemId = 'a';
    store.selectItemByOffset(1);
    expect(store.selectedItemId).toBe('b');
    expect(listener).toHaveBeenCalled();
  });
  it('clamps at start', () => {
    store.queueState = [{ id: 'a' }, { id: 'b' }];
    store.selectedItemId = 'a';
    store.selectItemByOffset(-1);
    expect(store.selectedItemId).toBe('a');
  });
  it('clamps at end', () => {
    store.queueState = [{ id: 'a' }, { id: 'b' }];
    store.selectedItemId = 'b';
    store.selectItemByOffset(1);
    expect(store.selectedItemId).toBe('b');
  });
  it('does nothing for empty queue', () => {
    store.selectItemByOffset(1);
    expect(store.selectedItemId).toBeNull();
  });
});
