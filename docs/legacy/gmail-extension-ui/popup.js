document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('open-clearledgr').addEventListener('click', () => {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (tabs[0]) {
        chrome.tabs.sendMessage(tabs[0].id, { action: 'OPEN_CLEARLEDGR' });
        window.close(); // Close popup
      }
    });
  });

  document.getElementById('settings-btn').addEventListener('click', () => {
    if (chrome.runtime.openOptionsPage) {
      chrome.runtime.openOptionsPage();
    } else {
      window.open(chrome.runtime.getURL('options.html'));
    }
  });
});
