(function() {
  const BaseClient = window.ClearledgrClients && window.ClearledgrClients.BaseClient;

  class CategorizationClient extends BaseClient {
    constructor() {
      super('CategorizationClient');
    }

    async run(ctx) {
      const parsedData = ctx.state.parsedData || {};

      this.log(
        ctx,
        'reasoning',
        'Assigning GL category',
        'Keyword and vendor heuristics.'
      );

      const accounts = resolveAccounts(ctx);
      const suggestion = suggestGLAccount(parsedData, accounts);
      ctx.state.glSuggestion = suggestion;

      this.log(
        ctx,
        'observation',
        'GL category suggested',
        `${suggestion.code} - ${suggestion.name} (${suggestion.confidence}% confidence)`
      );
    }
  }

  function resolveAccounts(ctx) {
    if (ctx.settings?.glAccounts?.length) return ctx.settings.glAccounts;
    if (ctx.services?.getDefaultSettings) {
      const defaults = ctx.services.getDefaultSettings();
      if (defaults?.glAccounts?.length) return defaults.glAccounts;
    }
    return getFallbackAccounts();
  }

  function suggestGLAccount(parsedData, accounts) {
    const vendor = (parsedData.vendor || '').toLowerCase();
    const subject = (parsedData.subject || '').toLowerCase();
    const combined = vendor + ' ' + subject;

    let bestMatch = { code: '6900', name: 'Other Expenses', confidence: 50 };
    let highestScore = 0;

    for (const account of accounts) {
      let score = 0;
      for (const keyword of account.keywords || []) {
        if (combined.includes(String(keyword).toLowerCase())) {
          score += 20;
        }
      }
      if (score > highestScore) {
        highestScore = score;
        bestMatch = { ...account, confidence: Math.min(50 + score, 95) };
      }
    }

    return bestMatch;
  }

  function getFallbackAccounts() {
    return [
      { code: '6000', name: 'Software & SaaS', keywords: ['software', 'subscription', 'saas', 'cloud', 'license'] },
      { code: '6100', name: 'Professional Services', keywords: ['consulting', 'legal', 'accounting', 'advisory'] },
      { code: '6200', name: 'Marketing & Advertising', keywords: ['marketing', 'advertising', 'ads', 'campaign', 'media'] },
      { code: '6300', name: 'Office Supplies', keywords: ['office', 'supplies', 'stationery', 'equipment'] },
      { code: '6400', name: 'Travel & Entertainment', keywords: ['travel', 'flight', 'hotel', 'meals', 'uber', 'airline'] },
      { code: '6500', name: 'Utilities', keywords: ['utility', 'electric', 'water', 'internet', 'phone', 'telecom'] }
    ];
  }

  window.ClearledgrClients = window.ClearledgrClients || {};
  window.ClearledgrClients.CategorizationClient = CategorizationClient;
})();
