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

import { RecordsRoute } from './routes/pages/RecordsRoute.js';
import { RecordDetailRoute } from './routes/pages/RecordDetailRoute.js';
import { ReportsRoute } from './routes/pages/ReportsRoute.js';
import { RulesRoute } from './routes/pages/RulesRoute.js';
import { VendorDetailRoute } from './routes/pages/VendorDetailRoute.js';
import { ExceptionsRoute } from './routes/pages/ExceptionsRoute.js';
import { VendorsRoute } from './routes/pages/VendorsRoute.js';
import { ProcurementRoute } from './routes/pages/ProcurementRoute.js';
import { WorkflowsRoute } from './routes/pages/WorkflowsRoute.js';
import { ActivityRoute } from './routes/pages/ActivityRoute.js';
import { AuditLogRoute } from './routes/pages/AuditLogRoute.js';
import { ConnectionsRoute } from './routes/pages/ConnectionsRoute.js';
import { SettingsRoute } from './routes/pages/SettingsRoute.js';
import { HealthRoute } from './routes/pages/HealthRoute.js';
import { PlanRoute } from './routes/pages/PlanRoute.js';
import { ApiKeysRoute } from './routes/pages/ApiKeysRoute.js';

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
                    <${Route} path="/records"><${RecordsRoute} /><//>
                    <${Route} path="/exceptions"><${ExceptionsRoute} /><//>
                    <${Route} path="/vendors"><${VendorsRoute} /><//>
                    <${Route} path="/procurement"><${ProcurementRoute} /><//>
                    <${Route} path="/workflows"><${WorkflowsRoute} /><//>
                    <${Route} path="/vendors/:name">
                      ${(params) => html`<${VendorDetailRoute} vendorName=${decodeURIComponent(params.name || '')} />`}
                    <//>
                    <${Route} path="/activity"><${ActivityRoute} /><//>
                    <${Route} path="/audit"><${AuditLogRoute} /><//>
                    <${Route} path="/reports"><${ReportsRoute} /><//>
                    <${Route} path="/rules"><${RulesRoute} /><//>
                    <${Route} path="/connections"><${ConnectionsRoute} /><//>
                    <${Route} path="/settings"><${SettingsRoute} /><//>
                    <${Route} path="/settings/:section">
                      ${(params) => html`<${SettingsRoute} routeId=${params.section} />`}
                    <//>
                    <${Route} path="/api-keys"><${ApiKeysRoute} /><//>
                    <${Route} path="/health"><${HealthRoute} /><//>
                    <${Route} path="/status"><${StatusPage} /><//>
                    <${Route} path="/records/:id">
                      ${(params) => html`<${RecordDetailRoute} recordId=${params.id} />`}
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
