const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

async function importModule(relativePath) {
  const absolute = path.resolve(__dirname, '..', relativePath);
  return import(`${pathToFileURL(absolute).href}?t=${Date.now()}`);
}

test('reply templates stay user and org scoped and build Gmail compose prefill', async () => {
  const {
    buildReplyTemplatePrefill,
    createReplyTemplate,
    defaultReplyTemplatePreferences,
    getAllReplyTemplates,
    readReplyTemplatePreferences,
  } = await importModule('src/routes/reply-templates.js');

  const storage = new Map();
  global.window = {
    localStorage: {
      getItem(key) { return storage.has(key) ? storage.get(key) : null; },
      setItem(key, value) { storage.set(key, String(value)); },
    },
  };

  const scope = { orgId: 'org-eu-1', userEmail: 'ops@clearledgr.com' };
  const otherScope = { orgId: 'org-eu-1', userEmail: 'backup@clearledgr.com' };

  assert.deepEqual(readReplyTemplatePreferences(scope), defaultReplyTemplatePreferences());

  const nextPrefs = createReplyTemplate(scope, {
    name: 'Vendor chaser',
    description: 'Ask for missing PO details.',
    audience: 'vendor',
    subjectTemplate: 'Re: {{subject}}',
    bodyTemplate: 'Hi {{vendor_name}}, we still need {{issue_summary}} for invoice {{invoice_number}}.',
  });

  const localTemplates = getAllReplyTemplates(nextPrefs);
  const customTemplate = localTemplates.find((template) => template.scope === 'user');
  assert.equal(Boolean(customTemplate), true);
  assert.equal(readReplyTemplatePreferences(otherScope).customTemplates.length, 0);

  const prefill = buildReplyTemplatePrefill(customTemplate, {
    vendor_name: 'Northwind',
    invoice_number: 'INV-55',
    amount: 500,
    currency: 'USD',
    subject: 'Invoice INV-55',
    sender: 'ap@northwind.test',
  }, {
    issue_summary: 'the matching PO number',
  });

  assert.equal(prefill.to, 'ap@northwind.test');
  assert.equal(prefill.subject, 'Re: Invoice INV-55');
  assert.equal(prefill.body.includes('Northwind'), true);
  assert.equal(prefill.body.includes('INV-55'), true);

  delete global.window;
});

test('reply template server preference patch stays under gmail_extension', async () => {
  const {
    buildReplyTemplatePreferencePatch,
    getBootstrappedReplyTemplatePreferences,
  } = await importModule('src/routes/reply-templates.js');

  const bootstrap = {
    current_user: {
      preferences: {
        gmail_extension: {
          reply_templates: {
            customTemplates: [
              {
                id: 'tmpl-1',
                name: 'Vendor status',
                audience: 'vendor',
                subjectTemplate: 'Re: {{subject}}',
                bodyTemplate: 'Status: {{state_label}}',
              },
            ],
          },
        },
      },
    },
  };

  const remote = getBootstrappedReplyTemplatePreferences(bootstrap);
  const patch = buildReplyTemplatePreferencePatch(remote);

  assert.equal(Array.isArray(remote.customTemplates), true);
  assert.equal(patch.gmail_extension.reply_templates.customTemplates[0].id, 'tmpl-1');
  assert.equal(patch.gmail_extension.reply_templates.customTemplates[0].name, 'Vendor status');
});
