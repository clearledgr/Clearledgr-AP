import { useEffect } from 'preact/hooks';
import { useLocation } from 'wouter-preact';
import { useBootstrap } from './BootstrapContext.js';

/**
 * If the user's org hasn't completed onboarding, redirect into the
 * wizard at /onboarding from any other route. Skipped:
 *   - The wizard route itself (so it actually renders)
 *   - The connections / settings routes (so the wizard's "Set up"
 *     buttons can deep-link there without bouncing back)
 *   - /signup/accept (for invite-accept flows that haven't yet
 *     produced a bootstrap)
 *
 * The gate is opt-in via bootstrap.onboarding.completed === false.
 * If bootstrap is unloaded or the field is missing, render through
 * (no redirect) — better to show something than to deadlock.
 */
const ONBOARDING_PASSTHROUGH = new Set([
  '/onboarding',
  '/connections',
  '/settings',
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
