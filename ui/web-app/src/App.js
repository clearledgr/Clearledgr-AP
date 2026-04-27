import { Router, Route, Switch } from 'wouter-preact';
import { html } from './utils/htm.js';
import { AppShell } from './shell/AppShell.js';
import { BootstrapProvider } from './shell/BootstrapContext.js';
import { ToastProvider } from './shell/Toast.js';
import { AuthGate } from './auth/AuthGate.js';
import { LoginPage } from './auth/LoginPage.js';
import { InviteAcceptPage } from './auth/InviteAcceptPage.js';
import { OnboardingGate } from './shell/OnboardingGate.js';
import { EntityProvider } from './shell/EntityContext.js';
import { OnboardingPage } from './routes/pages/OnboardingPage.js';
import { HomePage } from './routes/pages/HomePage.js';
import { StatusPage } from './routes/pages/StatusPage.js';
import { PrivacyPage, TermsPage, RequestDemoPage } from './auth/LegalPages.js';
import { PlaceholderPage } from './pages/PlaceholderPage.js';

import { PipelineRoute } from './routes/pages/PipelineRoute.js';
import { ReviewRoute } from './routes/pages/ReviewRoute.js';
import { ExceptionsRoute } from './routes/pages/ExceptionsRoute.js';
import { VendorsRoute } from './routes/pages/VendorsRoute.js';
import { ReconciliationRoute } from './routes/pages/ReconciliationRoute.js';
import { ActivityRoute } from './routes/pages/ActivityRoute.js';
import { ConnectionsRoute } from './routes/pages/ConnectionsRoute.js';
import { SettingsRoute } from './routes/pages/SettingsRoute.js';
import { TemplatesRoute } from './routes/pages/TemplatesRoute.js';
import { HealthRoute } from './routes/pages/HealthRoute.js';
import { PlanRoute } from './routes/pages/PlanRoute.js';

export function App() {
  return html`
    <${Router}>
      <${Switch}>
        <${Route} path="/login"><${LoginPage} /><//>
        <${Route} path="/signup/accept"><${InviteAcceptPage} /><//>
        <${Route} path="/privacy"><${PrivacyPage} /><//>
        <${Route} path="/terms"><${TermsPage} /><//>
        <${Route} path="/request-demo"><${RequestDemoPage} /><//>
        <${Route}>
          <${AuthGate}>
            <${BootstrapProvider}>
              <${EntityProvider}>
              <${ToastProvider}>
                <${OnboardingGate}>
                <${AppShell}>
                  <${Switch}>
                    <${Route} path="/onboarding"><${OnboardingPage} /><//>
                    <${Route} path="/"><${HomePage} /><//>
                    <${Route} path="/plan"><${PlanRoute} /><//>
                    <${Route} path="/pipeline"><${PipelineRoute} /><//>
                    <${Route} path="/review"><${ReviewRoute} /><//>
                    <${Route} path="/exceptions"><${ExceptionsRoute} /><//>
                    <${Route} path="/vendors"><${VendorsRoute} /><//>
                    <${Route} path="/vendors/:name">
                      ${(params) => html`<${VendorsRoute} initialVendor=${params.name} />`}
                    <//>
                    <${Route} path="/reconciliation"><${ReconciliationRoute} /><//>
                    <${Route} path="/activity"><${ActivityRoute} /><//>
                    <${Route} path="/connections"><${ConnectionsRoute} /><//>
                    <${Route} path="/settings"><${SettingsRoute} /><//>
                    <${Route} path="/settings/:section">
                      ${(params) => html`<${SettingsRoute} routeId=${params.section} />`}
                    <//>
                    <${Route} path="/templates"><${TemplatesRoute} /><//>
                    <${Route} path="/health"><${HealthRoute} /><//>
                    <${Route} path="/status"><${StatusPage} /><//>
                    <${Route} path="/items/:id">
                      ${(params) => html`<${PlaceholderPage} title=${`Item ${params.id}`} lift="record-route" />`}
                    <//>
                    <${Route}><${PlaceholderPage} title="Page not found" /><//>
                  <//>
                <//>
                <//>
              <//>
              <//>
            <//>
          <//>
        <//>
      <//>
    <//>
  `;
}
