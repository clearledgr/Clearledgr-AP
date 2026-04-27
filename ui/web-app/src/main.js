import { render } from 'preact';
import { html } from './utils/htm.js';
import { App } from './App.js';
import './styles/shell.css';
import './styles/onboarding.css';
import './styles/home.css';
import './styles/canvas.css';
import './styles/legal.css';
import './styles/footer.css';
import './styles/entity.css';
import './styles/cmdk.css';

const rootEl = document.getElementById('app');
if (!rootEl) {
  throw new Error('Root element #app not found');
}
render(html`<${App} />`, rootEl);
