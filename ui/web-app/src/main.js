import { render } from 'preact';
import { html } from './utils/htm.js';
import { App } from './App.js';
import './styles/shell.css';

const rootEl = document.getElementById('app');
if (!rootEl) {
  throw new Error('Root element #app not found');
}
render(html`<${App} />`, rootEl);
