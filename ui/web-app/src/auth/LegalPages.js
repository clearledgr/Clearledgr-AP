import { html } from '../utils/htm.js';

/**
 * Self-hosted legal pages that satisfy the GA-readiness requirement
 * for enterprise security questionnaires AND the login-form fineprint
 * links until the standalone marketing site at soldenai.com is up.
 *
 * Both pages are intentionally minimal — they're placeholder copy
 * vetted against the GDPR / SOC2 / DPA expectations every enterprise
 * security review will check for. Replace with the legally-reviewed
 * versions once Mo's lawyer has signed off; the routes stay the same
 * so external links don't break.
 */

const COMMON_HEADER = (title, eyebrow) => html`
  <header class="cl-legal-header">
    <div class="cl-legal-eyebrow">${eyebrow}</div>
    <h1 class="cl-legal-title">${title}</h1>
    <p class="cl-legal-meta">Last updated: April 2026</p>
  </header>
`;

export function PrivacyPage() {
  return html`
    <main class="cl-legal-shell">
      ${COMMON_HEADER('Privacy Policy', 'Legal')}
      <section class="cl-legal-body">
        <h2>What we collect</h2>
        <p>
          Solden collects only the data you explicitly connect:
          invoice content from Gmail or your ERP, vendor records,
          approval-routing metadata, and your team's identities and
          roles. We do not collect inbox contents beyond what your
          installed extension scope grants for invoice processing.
        </p>

        <h2>How we use it</h2>
        <p>
          Your data is used solely to provide AP coordination services
          to your organization. We do not sell, share, or use your
          data for advertising. Aggregated, de-identified statistics
          may inform product improvements.
        </p>

        <h2>Where it lives</h2>
        <p>
          Solden operates on managed cloud infrastructure; the specific
          hosting provider is named in our sub-processor list below.
          Customer data is encrypted in transit (TLS 1.3) and at rest
          (AES-256). Database backups are retained for 30 days and
          encrypted with the same keys.
        </p>

        <h2>Sub-processors</h2>
        <p>
          We use vetted sub-processors for hosting, error tracking, and the
          optional integrations a customer connects. A current list, with each
          provider and what it processes, is available on request and in our
          Data Processing Agreement.
        </p>

        <h2>Your rights</h2>
        <p>
          You can request export or deletion of your organization's
          data at any time by emailing <a href="mailto:privacy@soldenai.com">privacy@soldenai.com</a>.
          Requests are honored within 30 days per GDPR Article 17.
        </p>

        <h2>Contact</h2>
        <p>
          Privacy questions: <a href="mailto:privacy@soldenai.com">privacy@soldenai.com</a><br />
          Data Protection Officer: <a href="mailto:dpo@soldenai.com">dpo@soldenai.com</a>
        </p>
      </section>
      <a class="cl-legal-back" href="/">← Back to workspace</a>
    </main>
  `;
}

export function TermsPage() {
  return html`
    <main class="cl-legal-shell">
      ${COMMON_HEADER('Terms of Service', 'Legal')}
      <section class="cl-legal-body">
        <h2>Service</h2>
        <p>
          Solden provides an AP coordination layer for finance
          teams: invoice intake, validation, approval routing, ERP
          posting, and vendor management. Service is provided on a
          subscription basis with terms agreed in your order form.
        </p>

        <h2>Account & access</h2>
        <p>
          You're responsible for maintaining the confidentiality of
          your credentials and for all activity under your account.
          Notify <a href="mailto:security@soldenai.com">security@soldenai.com</a>
          immediately if you suspect unauthorized access.
        </p>

        <h2>Acceptable use</h2>
        <p>
          You agree not to (a) reverse-engineer the service, (b) use
          it to process data you don't have legal rights to, (c)
          interfere with service availability for other customers.
        </p>

        <h2>Service levels</h2>
        <p>
          Production targets: 99.5% monthly availability. Status and
          incident history is published at the operations status page.
          Scheduled maintenance windows are announced 48h in advance.
        </p>

        <h2>Termination</h2>
        <p>
          You may terminate at any time per your order form. We will
          provide a complete data export in CSV/JSON within 14 days
          of termination request. Data is permanently deleted 30 days
          after termination unless a legal hold applies.
        </p>

        <h2>Liability</h2>
        <p>
          To the maximum extent permitted by law, Solden's
          liability is limited to the fees paid in the 12 months
          preceding the claim. Solden does not assume liability
          for ERP-side or banking-side errors that result from
          actions you explicitly authorized.
        </p>

        <h2>Governing law</h2>
        <p>
          These terms are governed by English law. Disputes that
          cannot be resolved in good faith are subject to the
          exclusive jurisdiction of the courts of England and Wales.
        </p>

        <h2>Contact</h2>
        <p>
          Legal: <a href="mailto:legal@soldenai.com">legal@soldenai.com</a>
        </p>
      </section>
      <a class="cl-legal-back" href="/">← Back to workspace</a>
    </main>
  `;
}

export function RequestDemoPage() {
  return html`
    <main class="cl-legal-shell cl-legal-shell-demo">
      ${COMMON_HEADER("Get started with Solden.", 'Welcome')}
      <section class="cl-legal-body">
        <p class="cl-legal-lead">
          Solden is set up with you. We connect your systems and configure
          your workflows before you go live.
        </p>
        <p>
          New here? <a href="https://soldenai.com/contact.html">Request a demo</a>
          and we'll take it from there.
        </p>
        <p>
          Already have an invite link from your admin? Open it directly to set
          your password.
        </p>
      </section>
      <a class="cl-legal-back" href="/login">← Back to sign in</a>
    </main>
  `;
}
