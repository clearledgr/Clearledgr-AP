import { Router, Route, Switch } from 'wouter-preact';
import { html } from './utils/htm.js';
import { AppShell } from './shell/AppShell.js';
import { AuthGate } from './auth/AuthGate.js';
import { LoginPage } from './auth/LoginPage.js';
import { PipelinePage } from './pages/PipelinePage.js';
import { PlaceholderPage } from './pages/PlaceholderPage.js';

export function App() {
  return html`
    <${Router}>
      <${Switch}>
        <${Route} path="/login"><${LoginPage} /><//>
        <${Route}>
          <${AuthGate}>
            <${AppShell}>
              <${Switch}>
                <${Route} path="/">
                  <${PlaceholderPage} title="Home" lift="ui/gmail-extension/src/routes/pages/PlanPage.js" />
                <//>
                <${Route} path="/pipeline">
                  <${PipelinePage} />
                <//>
                <${Route} path="/review">
                  <${PlaceholderPage} title="Review queue" lift="ui/gmail-extension/src/routes/pages/ReviewPage.js" />
                <//>
                <${Route} path="/exceptions">
                  <${PlaceholderPage} title="Exceptions" lift="ui/gmail-extension/src/routes/pages/ExceptionsPage.js" />
                <//>
                <${Route} path="/vendors">
                  <${PlaceholderPage} title="Vendors" lift="ui/gmail-extension/src/routes/pages/VendorsPage.js" />
                <//>
                <${Route} path="/reconciliation">
                  <${PlaceholderPage} title="Reconciliation" lift="ui/gmail-extension/src/routes/pages/ReconciliationPage.js" />
                <//>
                <${Route} path="/activity">
                  <${PlaceholderPage} title="Activity" lift="ui/gmail-extension/src/routes/pages/ActivityPage.js" />
                <//>
                <${Route} path="/connections">
                  <${PlaceholderPage} title="Connections" lift="ui/gmail-extension/src/routes/pages/ConnectionsPage.js" />
                <//>
                <${Route} path="/settings">
                  <${PlaceholderPage} title="Settings" lift="ui/gmail-extension/src/routes/pages/SettingsPage.js" />
                <//>
                <${Route} path="/items/:id" component=${({ params }) =>
                  html`<${PlaceholderPage} title=${`Item ${params.id}`} lift="record-route" />`
                } />
                <${Route}>
                  <${PlaceholderPage} title="Page not found" />
                <//>
              <//>
            <//>
          <//>
        <//>
      <//>
    <//>
  `;
}
