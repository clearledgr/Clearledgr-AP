import { useEffect } from 'preact/hooks';
import { useLocation } from 'wouter-preact';
import { useBootstrap } from './BootstrapContext.js';

/**
 * If the user's org hasn't completed onboarding, redirect into the
 * wizard at /onboarding from any other route. Skipped:
 *   - The wizard route itself (so it actually renders).
 *   - Configuration surfaces the wizard's "Set up" buttons deep-link
 *     into (/connections, /settings) — without these the deep-link
 *     bounces back to the wizard.
 *   - Admin/ops observation surfaces (/audit, /plan, /health). An
 *     admin owns the workspace and should be able to read the audit
 *     log, check billing, and look at integration health while still
 *     working through ERP setup. Forcing them back to the wizard for
 *     these is hostile to the very user who needs them most.
 *   - Public auth/legal pages (/signup/accept, /status, /privacy,
 *     /terms) so invite-accept flows + public deep links work pre-
 *     bootstrap.
 *
 * Coordination surfaces (/records, /exceptions, /vendors, /activity,
 * /reports, /rules) stay reachable pre-onboarding — the workspace
 * owner needs to inspect their own data while the wizard is still
 * incomplete. Empty-state messages do the teaching when there's
 * no data yet.
 *
 * The gate is opt-in via bootstrap.onboarding.completed === false.
 * If bootstrap is unloaded or the field is missing, render through
 * (no redirect) — better to show something than to deadlock.
 */
const ONBOARDING_PASSTHROUGH = new Set([
  '/',
  '/onboarding',
  '/connections',
  '/settings',
  // Admin / ops observation surfaces
  '/audit',
  '/plan',
  '/health',
  // Coordination surfaces — see header for rationale.
  '/home',
  '/records',
  '/exceptions',
  '/vendors',
  '/activity',
  '/reports',
  '/rules',
  '/api-keys',
  // Public + auth flows
  '/signup/accept',
  '/status',
  '/privacy',
  '/terms',
]);

function passthrough(pathname) {
  if (!pathname) return true;
  if (ONBOARDING_PASSTHROUGH.has(pathname)) return true;
  for (const prefix of ONBOARDING_PASSTHROUGH) {
    if (pathname.startsWith(`${prefix}/`)) return true;
  }
  return false;
}

export function OnboardingGate({ children }) {
  const bootstrap = useBootstrap();
  const [pathname, navigate] = useLocation();

  useEffect(() => {
    if (!bootstrap) return;
    const completed = bootstrap?.onboarding?.completed;
    if (completed === false && !passthrough(pathname)) {
      navigate('/onboarding', { replace: true });
    }
  }, [bootstrap, pathname, navigate]);

  return children;
}
