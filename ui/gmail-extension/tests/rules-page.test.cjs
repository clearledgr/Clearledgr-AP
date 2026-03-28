const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

async function importModule(relativePath) {
  const absolute = path.resolve(__dirname, '..', relativePath);
  return import(`${pathToFileURL(absolute).href}?t=${Date.now()}`);
}

test('approval automation config falls back to defaults and respects policy payload', async () => {
  const { getApprovalAutomationConfig } = await importModule('src/routes/pages/RulesPage.js');

  assert.deepEqual(getApprovalAutomationConfig({}, null), {
    reminderHours: 4,
    escalationHours: 24,
    escalationChannel: '',
  });

  assert.deepEqual(
    getApprovalAutomationConfig(
      {
        approval_automation: {
          reminder_hours: 6,
          escalation_hours: 18,
          escalation_channel: '#finance-escalations',
        },
      },
      null,
    ),
    {
      reminderHours: 6,
      escalationHours: 18,
      escalationChannel: '#finance-escalations',
    },
  );

  assert.deepEqual(
    getApprovalAutomationConfig(
      {
        approval_automation: {
          reminder_hours: 6,
          escalation_hours: 18,
        },
      },
      {
        reminder_hours: 8,
        escalation_hours: 4,
        escalation_channel: '#ops',
      },
    ),
    {
      reminderHours: 8,
      escalationHours: 8,
      escalationChannel: '#ops',
    },
  );
});
