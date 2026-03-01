document.addEventListener('DOMContentLoaded', loadSettings);
document.getElementById('settings-form').addEventListener('submit', saveSettings);

function loadSettings() {
  chrome.storage.sync.get(['apiKey', 'settings'], (result) => {
    if (result.apiKey) {
      document.getElementById('apiKey').value = result.apiKey;
    }
    
    if (result.settings) {
      document.getElementById('apiEndpoint').value = result.settings.apiEndpoint || 'https://api.clearledgr.com/v1';
      document.getElementById('autoMatch').checked = result.settings.autoMatch !== false;
      document.getElementById('notifications').checked = result.settings.notifications !== false;
    }
  });
}

function saveSettings(e) {
  e.preventDefault();
  
  const apiKey = document.getElementById('apiKey').value.trim();
  const apiEndpoint = document.getElementById('apiEndpoint').value.trim();
  const autoMatch = document.getElementById('autoMatch').checked;
  const notifications = document.getElementById('notifications').checked;

  if (!apiKey) {
    showStatus('API Key is required', 'error');
    return;
  }

  const settings = {
    apiEndpoint,
    autoMatch,
    notifications
  };

  chrome.storage.sync.set({ apiKey, settings }, () => {
    showStatus('Settings saved successfully', 'success');
    
    // Notify all tabs to update settings immediately
    chrome.tabs.query({}, (tabs) => {
      tabs.forEach(tab => {
        chrome.tabs.sendMessage(tab.id, { 
          action: 'SETTINGS_UPDATED', 
          settings 
        }).catch(() => {}); // Ignore errors for tabs without content script
      });
    });
  });
}

function showStatus(message, type) {
  const status = document.getElementById('status');
  status.textContent = message;
  status.className = `status ${type}`;
  
  setTimeout(() => {
    status.style.display = 'none';
  }, 3000);
}
