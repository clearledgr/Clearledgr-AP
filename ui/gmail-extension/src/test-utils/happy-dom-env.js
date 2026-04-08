import { Window } from 'happy-dom';
import { render } from 'preact';

const GLOBAL_KEYS = [
  'window',
  'self',
  'document',
  'navigator',
  'location',
  'history',
  'localStorage',
  'sessionStorage',
  'Node',
  'Text',
  'Element',
  'HTMLElement',
  'HTMLButtonElement',
  'HTMLInputElement',
  'Event',
  'CustomEvent',
  'MouseEvent',
  'KeyboardEvent',
  'FocusEvent',
  'InputEvent',
  'MutationObserver',
  'getComputedStyle',
  'requestAnimationFrame',
  'cancelAnimationFrame',
];

const originalDescriptors = new Map();
const mountedContainers = new Set();

let activeWindow = null;

function setGlobal(key, value) {
  if (!originalDescriptors.has(key)) {
    originalDescriptors.set(key, Object.getOwnPropertyDescriptor(globalThis, key) || null);
  }
  Object.defineProperty(globalThis, key, {
    configurable: true,
    writable: true,
    value,
  });
}

function restoreGlobals() {
  for (const key of GLOBAL_KEYS) {
    const descriptor = originalDescriptors.get(key) || null;
    if (descriptor) {
      Object.defineProperty(globalThis, key, descriptor);
    } else {
      delete globalThis[key];
    }
  }
}

export function installDom(url = 'https://mail.google.com/mail/u/0/#inbox') {
  activeWindow = new Window({ url });

  setGlobal('window', activeWindow);
  setGlobal('self', activeWindow);
  setGlobal('document', activeWindow.document);
  setGlobal('navigator', activeWindow.navigator);
  setGlobal('location', activeWindow.location);
  setGlobal('history', activeWindow.history);
  setGlobal('localStorage', activeWindow.localStorage);
  setGlobal('sessionStorage', activeWindow.sessionStorage);
  setGlobal('Node', activeWindow.Node);
  setGlobal('Text', activeWindow.Text);
  setGlobal('Element', activeWindow.Element);
  setGlobal('HTMLElement', activeWindow.HTMLElement);
  setGlobal('HTMLButtonElement', activeWindow.HTMLButtonElement);
  setGlobal('HTMLInputElement', activeWindow.HTMLInputElement);
  setGlobal('Event', activeWindow.Event);
  setGlobal('CustomEvent', activeWindow.CustomEvent);
  setGlobal('MouseEvent', activeWindow.MouseEvent);
  setGlobal('KeyboardEvent', activeWindow.KeyboardEvent);
  setGlobal('FocusEvent', activeWindow.FocusEvent);
  setGlobal('InputEvent', activeWindow.InputEvent);
  setGlobal('MutationObserver', activeWindow.MutationObserver);
  setGlobal('getComputedStyle', activeWindow.getComputedStyle.bind(activeWindow));
  setGlobal(
    'requestAnimationFrame',
    activeWindow.requestAnimationFrame
      ? activeWindow.requestAnimationFrame.bind(activeWindow)
      : ((callback) => setTimeout(callback, 0)),
  );
  setGlobal(
    'cancelAnimationFrame',
    activeWindow.cancelAnimationFrame
      ? activeWindow.cancelAnimationFrame.bind(activeWindow)
      : clearTimeout,
  );

  return activeWindow;
}

export function mount(vnode) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  mountedContainers.add(container);
  render(vnode, container);

  return {
    container,
    rerender(nextVNode) {
      render(nextVNode, container);
    },
    unmount() {
      render(null, container);
      container.remove();
      mountedContainers.delete(container);
    },
  };
}

export function click(element) {
  element.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
}

export function inputValue(element, value) {
  element.value = value;
  element.dispatchEvent(new window.Event('input', { bubbles: true, cancelable: true }));
}

export function keyDown(element, key, init = {}) {
  element.dispatchEvent(new window.KeyboardEvent('keydown', {
    key,
    bubbles: true,
    cancelable: true,
    ...init,
  }));
}

export async function flushTicks(count = 1) {
  for (let index = 0; index < count; index += 1) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}

export function getTextContent(node = document.body) {
  return String(node?.textContent || '').replace(/\s+/g, ' ').trim();
}

export async function uninstallDom() {
  for (const container of mountedContainers) {
    render(null, container);
    container.remove();
  }
  mountedContainers.clear();

  if (typeof activeWindow?.happyDOM?.close === 'function') {
    await activeWindow.happyDOM.close();
  }
  activeWindow = null;
  restoreGlobals();
}
