import assert from 'node:assert/strict';
import { beforeEach, describe, it, mock } from 'node:test';
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
});

describe('store.update', () => {
  it('patches state and notifies listeners', () => {
    const listener = mock.fn();
    const unsubscribe = store.subscribe(listener);
    store.update({ scanStatus: { state: 'scanning' } });
    unsubscribe();
    assert.equal(store.scanStatus.state, 'scanning');
    assert.equal(listener.mock.calls.length, 1);
  });

  it('unsubscribe stops notifications', () => {
    const listener = mock.fn();
    const unsubscribe = store.subscribe(listener);
    unsubscribe();
    store.update({ scanStatus: { state: 'idle' } });
    assert.equal(listener.mock.calls.length, 0);
  });
});

describe('store.findItemByThreadId', () => {
  it('finds by thread_id', () => {
    store.queueState = [{ id: '1', thread_id: 'abc' }, { id: '2', thread_id: 'def' }];
    assert.equal(store.findItemByThreadId('def')?.id, '2');
  });

  it('returns null for missing', () => {
    store.queueState = [{ id: '1', thread_id: 'abc' }];
    assert.equal(store.findItemByThreadId('zzz'), null);
    assert.equal(store.findItemByThreadId(null), null);
  });
});

describe('store.findItemById', () => {
  it('finds by id', () => {
    store.queueState = [{ id: 'item-1' }, { id: 'item-2' }];
    assert.equal(store.findItemById('item-2')?.id, 'item-2');
  });

  it('finds by invoice_key', () => {
    store.queueState = [{ id: 'x', invoice_key: 'inv-99' }];
    assert.equal(store.findItemById('inv-99')?.id, 'x');
  });

  it('returns null for missing', () => {
    assert.equal(store.findItemById('nope'), null);
  });
});

describe('store.getPrimaryItem', () => {
  it('prefers selectedItemId', () => {
    store.queueState = [{ id: 'a' }, { id: 'b' }];
    store.selectedItemId = 'b';
    assert.equal(store.getPrimaryItem()?.id, 'b');
  });

  it('falls back to currentThreadId', () => {
    store.queueState = [{ id: 'a', thread_id: 't1' }, { id: 'b', thread_id: 't2' }];
    store.currentThreadId = 't2';
    assert.equal(store.getPrimaryItem()?.id, 'b');
  });

  it('falls back to first item', () => {
    store.queueState = [{ id: 'first' }];
    assert.equal(store.getPrimaryItem()?.id, 'first');
  });

  it('returns null for empty queue', () => {
    assert.equal(store.getPrimaryItem(), null);
  });
});

describe('store.getPrimaryItemIndex', () => {
  it('returns correct index', () => {
    store.queueState = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];
    store.selectedItemId = 'b';
    assert.equal(store.getPrimaryItemIndex(), 1);
  });

  it('returns -1 for no item', () => {
    assert.equal(store.getPrimaryItemIndex(), -1);
  });
});

describe('store.selectItemByOffset', () => {
  it('moves to next item', () => {
    const listener = mock.fn();
    const unsubscribe = store.subscribe(listener);
    store.queueState = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];
    store.selectedItemId = 'a';
    store.selectItemByOffset(1);
    unsubscribe();
    assert.equal(store.selectedItemId, 'b');
    assert.equal(listener.mock.calls.length > 0, true);
  });

  it('clamps at start', () => {
    store.queueState = [{ id: 'a' }, { id: 'b' }];
    store.selectedItemId = 'a';
    store.selectItemByOffset(-1);
    assert.equal(store.selectedItemId, 'a');
  });

  it('clamps at end', () => {
    store.queueState = [{ id: 'a' }, { id: 'b' }];
    store.selectedItemId = 'b';
    store.selectItemByOffset(1);
    assert.equal(store.selectedItemId, 'b');
  });

  it('does nothing for empty queue', () => {
    store.selectItemByOffset(1);
    assert.equal(store.selectedItemId, null);
  });
});
