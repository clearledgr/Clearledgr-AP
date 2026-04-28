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
import './styles/pages.css';
import './styles/templates.css';
import './styles/billing.css';
import './styles/mobile.css';

const rootEl = document.getElementById('app');
if (!rootEl) {
  throw new Error('Root element #app not found');
}
render(html`<${App} />`, rootEl);
