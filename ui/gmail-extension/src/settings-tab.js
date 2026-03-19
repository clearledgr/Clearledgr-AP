/**
 * Clearledgr Settings Tab — injected into Gmail's native Settings page.
 * Follows Streak's pattern: "Streak Settings" tab appears alongside
 * General, Labels, Inbox, etc.
 *
 * This is NOT an InboxSDK API — it's custom DOM injection that watches
 * for Gmail's settings page to load, then injects our tab.
 */

const SETTINGS_TAB_ID = 'clearledgr-settings-tab';
const SETTINGS_CONTENT_ID = 'clearledgr-settings-content';

let settingsObserver = null;
let injected = false;

export function watchForSettingsPage(queueManager) {
  if (settingsObserver) return;

  // Gmail settings page is at #settings — watch for hash changes
  const checkAndInject = () => {
    const hash = window.location.hash || '';
    if (hash.includes('settings') && !injected) {
      // Wait for Gmail to render the settings tabs
      setTimeout(() => injectSettingsTab(queueManager), 500);
    }
  };

  window.addEventListener('hashchange', checkAndInject);
  checkAndInject();

  // Also watch for DOM changes in case settings loads dynamically
  settingsObserver = new MutationObserver(() => {
    if (injected) return;
    const tabBar = document.querySelector('.fY, [role="tablist"]');
    if (tabBar && window.location.hash.includes('settings')) {
      injectSettingsTab(queueManager);
    }
  });
  settingsObserver.observe(document.body, { childList: true, subtree: true });
}

function injectSettingsTab(queueManager) {
  // Find Gmail's settings tab bar
  const tabBar = document.querySelector('.fY');
  if (!tabBar) return;
  if (document.getElementById(SETTINGS_TAB_ID)) return; // already injected
  injected = true;

  // Create our tab
  const tab = document.createElement('a');
  tab.id = SETTINGS_TAB_ID;
  tab.className = 'f0';
  tab.href = '#settings/clearledgr';
  tab.textContent = 'Clearledgr';
  tab.style.cssText = 'cursor:pointer;';
  tab.addEventListener('click', (e) => {
    e.preventDefault();
    // Deactivate all other tabs
    tabBar.querySelectorAll('a').forEach(a => a.classList.remove('f1'));
    tab.classList.add('f1');
    showSettingsContent(queueManager);
  });
  tabBar.appendChild(tab);

  // If user navigated directly to #settings/clearledgr, activate our tab
  if (window.location.hash.includes('settings/clearledgr')) {
    tabBar.querySelectorAll('a').forEach(a => a.classList.remove('f1'));
    tab.classList.add('f1');
    showSettingsContent(queueManager);
  }
}

function showSettingsContent(queueManager) {
  // Find Gmail's settings content area
  const contentArea = document.querySelector('.Bk, .nH[role="main"] .nH');
  if (!contentArea) return;

  // Remove existing Clearledgr content if any
  const existing = document.getElementById(SETTINGS_CONTENT_ID);
  if (existing) existing.remove();

  // Hide Gmail's native settings content
  const nativeContent = contentArea.querySelector(':scope > div:not(#' + SETTINGS_CONTENT_ID + ')');

  const container = document.createElement('div');
  container.id = SETTINGS_CONTENT_ID;
  container.style.cssText = `
    padding: 20px 28px; max-width: 680px;
    font-family: 'Google Sans', Roboto, Arial, sans-serif; font-size: 14px; color: #202124;
  `;

  const orgId = String(queueManager?.runtimeConfig?.organizationId || 'default');
  const backendUrl = String(queueManager?.runtimeConfig?.backendUrl || 'http://127.0.0.1:8010').replace(/\/+$/, '');

  container.innerHTML = `
    <h2 style="font-size:18px;font-weight:500;margin:0 0 24px;color:#202124">Clearledgr Settings</h2>

    <div style="margin-bottom:24px">
      <h3 style="font-size:14px;font-weight:500;margin:0 0 12px;color:#202124">Invoice Processing</h3>
      <table style="border-collapse:collapse;width:100%">
        <tr style="border-bottom:1px solid #e0e0e0">
          <td style="padding:12px 0;width:280px;color:#5f6368">Auto-approve confidence threshold</td>
          <td style="padding:12px 0">
            <select id="cl-setting-threshold" style="padding:6px 12px;border:1px solid #dadce0;border-radius:4px;font-size:14px">
              <option value="0.99">99% (strict)</option>
              <option value="0.95" selected>95% (recommended)</option>
              <option value="0.90">90% (permissive)</option>
              <option value="0.80">80% (lenient)</option>
              <option value="0">Off — manual approval only</option>
            </select>
          </td>
        </tr>
        <tr style="border-bottom:1px solid #e0e0e0">
          <td style="padding:12px 0;color:#5f6368">Max auto-approve amount</td>
          <td style="padding:12px 0">
            <input id="cl-setting-max-amount" type="number" placeholder="No limit"
              style="padding:6px 12px;border:1px solid #dadce0;border-radius:4px;font-size:14px;width:160px" />
          </td>
        </tr>
        <tr style="border-bottom:1px solid #e0e0e0">
          <td style="padding:12px 0;color:#5f6368">Require PO match</td>
          <td style="padding:12px 0">
            <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
              <input id="cl-setting-require-po" type="checkbox" /> Require purchase order match before auto-approval
            </label>
          </td>
        </tr>
      </table>
    </div>

    <div style="margin-bottom:24px">
      <h3 style="font-size:14px;font-weight:500;margin:0 0 12px;color:#202124">Notifications</h3>
      <table style="border-collapse:collapse;width:100%">
        <tr style="border-bottom:1px solid #e0e0e0">
          <td style="padding:12px 0;width:280px;color:#5f6368">Invoice detected</td>
          <td style="padding:12px 0">
            <label style="cursor:pointer"><input id="cl-notif-detected" type="checkbox" checked /> Browser notification</label>
          </td>
        </tr>
        <tr style="border-bottom:1px solid #e0e0e0">
          <td style="padding:12px 0;color:#5f6368">Approval needed</td>
          <td style="padding:12px 0">
            <label style="cursor:pointer"><input id="cl-notif-approval" type="checkbox" checked /> Browser notification</label>
          </td>
        </tr>
        <tr style="border-bottom:1px solid #e0e0e0">
          <td style="padding:12px 0;color:#5f6368">ERP post result</td>
          <td style="padding:12px 0">
            <label style="cursor:pointer"><input id="cl-notif-erp" type="checkbox" checked /> Browser notification</label>
          </td>
        </tr>
        <tr style="border-bottom:1px solid #e0e0e0">
          <td style="padding:12px 0;color:#5f6368">Exception flagged</td>
          <td style="padding:12px 0">
            <label style="cursor:pointer"><input id="cl-notif-exception" type="checkbox" checked /> Browser notification</label>
          </td>
        </tr>
      </table>
    </div>

    <div style="margin-bottom:24px">
      <h3 style="font-size:14px;font-weight:500;margin:0 0 12px;color:#202124">Labels</h3>
      <table style="border-collapse:collapse;width:100%">
        <tr style="border-bottom:1px solid #e0e0e0">
          <td style="padding:12px 0;width:280px;color:#5f6368">Show invoice status labels on inbox rows</td>
          <td style="padding:12px 0">
            <label style="cursor:pointer"><input id="cl-setting-row-labels" type="checkbox" checked /> Enabled</label>
          </td>
        </tr>
        <tr style="border-bottom:1px solid #e0e0e0">
          <td style="padding:12px 0;color:#5f6368">Show vendor + amount on inbox rows</td>
          <td style="padding:12px 0">
            <label style="cursor:pointer"><input id="cl-setting-row-detail" type="checkbox" checked /> Enabled</label>
          </td>
        </tr>
      </table>
    </div>

    <div style="margin-bottom:24px">
      <h3 style="font-size:14px;font-weight:500;margin:0 0 12px;color:#202124">Connection</h3>
      <table style="border-collapse:collapse;width:100%">
        <tr style="border-bottom:1px solid #e0e0e0">
          <td style="padding:12px 0;width:280px;color:#5f6368">Organization</td>
          <td style="padding:12px 0;color:#202124">${orgId}</td>
        </tr>
        <tr style="border-bottom:1px solid #e0e0e0">
          <td style="padding:12px 0;color:#5f6368">Backend URL</td>
          <td style="padding:12px 0;color:#202124">${backendUrl}</td>
        </tr>
      </table>
    </div>

    <div>
      <button id="cl-settings-save" style="
        padding:8px 24px; background:#1a73e8; color:#fff; border:none; border-radius:4px;
        font-size:14px; font-weight:500; cursor:pointer;
      ">Save Changes</button>
      <span id="cl-settings-status" style="margin-left:12px;color:#188038;font-size:13px;display:none">Settings saved</span>
    </div>
  `;

  contentArea.prepend(container);

  // Load current settings
  loadCurrentSettings(backendUrl, orgId, queueManager);

  // Save handler
  document.getElementById('cl-settings-save')?.addEventListener('click', () => {
    saveSettings(backendUrl, orgId, queueManager);
  });
}

async function loadCurrentSettings(backendUrl, orgId, queueManager) {
  try {
    const result = await queueManager.backendFetch(`${backendUrl}/settings/${encodeURIComponent(orgId)}`);
    if (!result?.ok) return;
    const settings = await result.json();

    const threshold = settings.auto_approve_threshold ?? 0.95;
    const el = document.getElementById('cl-setting-threshold');
    if (el) el.value = String(threshold);

    const maxAmount = settings.max_auto_approve_amount;
    const amountEl = document.getElementById('cl-setting-max-amount');
    if (amountEl && maxAmount) amountEl.value = String(maxAmount);

    const requirePo = settings.require_po_match ?? false;
    const poEl = document.getElementById('cl-setting-require-po');
    if (poEl) poEl.checked = requirePo;
  } catch (_) { /* settings load failed — use defaults */ }

  // Load notification prefs from chrome storage
  try {
    const stored = await chrome.storage.sync.get(['cl_notif_prefs']);
    const prefs = stored.cl_notif_prefs || {};
    ['detected', 'approval', 'erp', 'exception'].forEach(key => {
      const el = document.getElementById(`cl-notif-${key}`);
      if (el && prefs[key] !== undefined) el.checked = prefs[key];
    });
  } catch (_) { /* ignore */ }
}

async function saveSettings(backendUrl, orgId, queueManager) {
  const status = document.getElementById('cl-settings-status');

  try {
    // Save processing settings to backend
    const threshold = parseFloat(document.getElementById('cl-setting-threshold')?.value || '0.95');
    const maxAmountRaw = document.getElementById('cl-setting-max-amount')?.value;
    const maxAmount = maxAmountRaw ? parseFloat(maxAmountRaw) : null;
    const requirePo = document.getElementById('cl-setting-require-po')?.checked || false;

    await queueManager.backendFetch(`${backendUrl}/settings/${encodeURIComponent(orgId)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        auto_approve_threshold: threshold,
        max_auto_approve_amount: maxAmount,
        require_po_match: requirePo,
      }),
    });

    // Save notification prefs to chrome storage
    const notifPrefs = {};
    ['detected', 'approval', 'erp', 'exception'].forEach(key => {
      const el = document.getElementById(`cl-notif-${key}`);
      if (el) notifPrefs[key] = el.checked;
    });
    await chrome.storage.sync.set({ cl_notif_prefs: notifPrefs });

    // Save label prefs to chrome storage
    const labelPrefs = {
      rowLabels: document.getElementById('cl-setting-row-labels')?.checked ?? true,
      rowDetail: document.getElementById('cl-setting-row-detail')?.checked ?? true,
    };
    await chrome.storage.sync.set({ cl_label_prefs: labelPrefs });

    if (status) {
      status.style.display = 'inline';
      setTimeout(() => { status.style.display = 'none'; }, 3000);
    }
  } catch (err) {
    if (status) {
      status.textContent = 'Failed to save';
      status.style.color = '#d93025';
      status.style.display = 'inline';
      setTimeout(() => { status.style.display = 'none'; status.textContent = 'Settings saved'; status.style.color = '#188038'; }, 3000);
    }
  }
}
