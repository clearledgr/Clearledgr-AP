/** BudgetPausedBanner — in-product surfacing of the LLM runaway-spend guard.
 *
 * When an organization crosses its monthly Claude cost hard cap, the
 * backend's LLM gateway refuses further calls (see
 * `clearledgr/core/llm_gateway.py::_enforce_budget_cap`). Without an
 * in-product banner the AP clerk / controller would just see the
 * agent stop working with no explanation. This component is the
 * explanation + (if the caller's role is CFO or OWNER) the path to
 * lift the pause without leaving Gmail.
 *
 * Design:
 *   - Zero render when status is null or status.paused === false.
 *   - One render when paused, tuned for the existing
 *     `.cl-ts-banner` class family used by WaitingBanner /
 *     FraudFlagsBanner / OverrideWindowBanner in ThreadSidebar, so
 *     it visually matches when stacked alongside them.
 *   - Role-gated override button: only renders when
 *     status.can_override === true. Lower-role users see
 *     guidance text pointing them at the CFO instead.
 *   - Dumb component. All side effects (dialog prompt, API call,
 *     refresh of status) live in the caller — the banner just
 *     calls onRequestOverride() when the user clicks Lift pause.
 */
import { h } from 'preact';
import htm from 'htm';

const html = htm.bind(h);

function formatUsd(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '$0';
  // Currency is always USD per the /llm-budget/status payload shape.
  return n >= 100
    ? `$${n.toFixed(0)}`
    : `$${n.toFixed(2)}`;
}

function formatPeriodEnd(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  } catch (_) { return ''; }
}

/**
 * @param {{
 *   status: {
 *     paused: boolean,
 *     paused_at?: string | null,
 *     cost_usd: number,
 *     cap_usd: number,
 *     period_start?: string,
 *     period_end?: string,
 *     can_override: boolean,
 *   } | null,
 *   onRequestOverride?: () => Promise<void> | void,
 *   pending?: boolean,
 * }} props
 */
export default function BudgetPausedBanner({ status, onRequestOverride, pending }) {
  if (!status || !status.paused) return null;

  const cost = formatUsd(status.cost_usd);
  const cap = formatUsd(status.cap_usd);
  const resumesOn = formatPeriodEnd(status.period_end);
  const canOverride = Boolean(status.can_override);

  return html`
    <div class="cl-ts-banner cl-budget-paused" role="status" aria-live="polite">
      <style>${BUDGET_PAUSED_BANNER_CSS}</style>
      <div class="cl-ts-banner-icon">!</div>
      <div class="cl-ts-banner-body">
        <div class="cl-ts-banner-title">Agent paused — monthly LLM cost cap reached</div>
        <div class="cl-ts-banner-detail">
          ${cost} of ${cap} spent this month.${resumesOn ? ` Resumes on ${resumesOn} at the latest.` : ''}
        </div>
        ${canOverride ? html`
          <button
            class="cl-budget-paused-action"
            type="button"
            disabled=${Boolean(pending)}
            onClick=${() => {
              if (!pending && typeof onRequestOverride === 'function') onRequestOverride();
            }}
          >${pending ? 'Lifting…' : 'Lift pause'}</button>
        ` : html`
          <div class="cl-budget-paused-hint">
            Ask your CFO or account owner to lift the pause.
          </div>
        `}
      </div>
    </div>
  `;
}

// CSS is injected alongside the component so it matches the existing
// banner family. Consumers that want to tune look-and-feel should
// update ThreadSidebar's `.cl-ts-banner` rules; these selectors are
// additive for the budget-paused variant only.
export const BUDGET_PAUSED_BANNER_CSS = `
.cl-ts-banner.cl-budget-paused {
  background: #FEF3C7;            /* amber-100: warning, not error */
  border: 1px solid #F59E0B;      /* amber-500 */
}
.cl-ts-banner.cl-budget-paused .cl-ts-banner-icon {
  background: #F59E0B;
  color: #78350F;
}
.cl-ts-banner.cl-budget-paused .cl-ts-banner-title {
  color: #78350F;                 /* amber-900 */
}
.cl-ts-banner.cl-budget-paused .cl-ts-banner-detail {
  color: #92400E;                 /* amber-800 */
}
.cl-budget-paused-action {
  margin-top: 8px;
  padding: 6px 14px;
  border: 1px solid #78350F;
  border-radius: 6px;
  background: #FFFBEB;
  color: #78350F;
  font: 600 12px/1.2 'DM Sans', sans-serif;
  cursor: pointer;
}
.cl-budget-paused-action:hover:not(:disabled) {
  background: #FEF3C7;
}
.cl-budget-paused-action:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.cl-budget-paused-hint {
  margin-top: 6px;
  font: 500 11px/1.3 'DM Sans', sans-serif;
  color: #92400E;
}
`;
