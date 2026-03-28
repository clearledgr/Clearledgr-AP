const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

async function importModule(relativePath) {
  const absolute = path.resolve(__dirname, '..', relativePath);
  return import(`${pathToFileURL(absolute).href}?t=${Date.now()}`);
}

test('entity routing config normalizes legal entities and matcher rules from settings', async () => {
  const { getEntityRoutingConfig } = await importModule('src/routes/pages/CompanyPage.js');

  assert.deepEqual(getEntityRoutingConfig({}), {
    entities: [],
    rules: [],
  });

  assert.deepEqual(
    getEntityRoutingConfig({
      entity_routing: {
        entities: [
          { entity_code: 'US-01', entity_name: 'Acme US', entity_id: 'us-1' },
          { entity_code: '', entity_name: '', entity_id: '' },
        ],
        rules: [
          {
            entity_code: 'US-01',
            sender_domains: ['acme.com'],
            vendor_contains: ['google workspace'],
            currencies: ['usd'],
            priority: 10,
          },
          {
            entity_code: '',
            sender_domains: [],
          },
        ],
      },
    }),
    {
      entities: [
        { entity_id: 'us-1', entity_code: 'US-01', entity_name: 'Acme US' },
      ],
      rules: [
        {
          entity_id: '',
          entity_code: 'US-01',
          entity_name: '',
          sender_domains: ['acme.com'],
          vendor_contains: ['google workspace'],
          subject_contains: [],
          currencies: ['USD'],
          priority: 10,
        },
      ],
    },
  );
});
