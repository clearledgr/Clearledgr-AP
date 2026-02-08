(function() {
  const BaseClient = window.ClearledgrClients && window.ClearledgrClients.BaseClient;

  class ClassificationClient extends BaseClient {
    constructor() {
      super('ClassificationClient');
    }

    async run(ctx) {
      this.log(
        ctx,
        'reasoning',
        'Scanning email locally',
        'Analyzing subject, sender, and body for finance signals.'
      );

      const emailType = classifyEmail(ctx.emailData, ctx.settings);
      ctx.state.emailType = emailType;

      this.log(ctx, 'observation', 'Email classified', `Type: ${emailType.type}`);

      if (emailType.type === 'non-finance' || emailType.type === 'ignored') {
        this.log(
          ctx,
          'result',
          'No finance workflow required',
          emailType.reason ? `Reason: ${emailType.reason}` : 'No action taken.'
        );
        this.halt(ctx, 'non-finance');
      }
    }
  }

  function classifyEmail(emailData, settings) {
    const subject = (emailData.subject || '').toLowerCase();
    const body = (emailData.bodyText || emailData.bodyHtml || emailData.body || '').toLowerCase();
    const sender = (emailData.senderEmail || '').toLowerCase();
    const combined = subject + ' ' + body;

    const domain = sender.split('@')[1] || '';
    if (settings?.ignoredDomains?.includes(domain)) {
      return { type: 'ignored', reason: 'Sender is in ignored list' };
    }

    const financePatterns = [
      /invoice/i, /payment/i, /receipt/i, /billing/i, /statement/i,
      /amount\s*(?:due|paid|owed)/i, /\$[\d,]+\.?\d*/i, /total\s*:/i,
      /purchase\s*order/i, /po\s*#?\s*\d+/i, /remittance/i, /wire\s*transfer/i,
      /ach\s*payment/i, /credit\s*memo/i, /debit\s*note/i
    ];

    const nonFinancePatterns = [
      /unsubscribe/i, /newsletter/i, /marketing/i, /promotional/i,
      /webinar/i, /event\s*invitation/i, /job\s*alert/i, /social\s*update/i,
      /password\s*reset/i, /verify\s*your\s*email/i, /welcome\s*to/i
    ];

    let financeScore = 0;
    let nonFinanceScore = 0;

    financePatterns.forEach(p => { if (p.test(combined)) financeScore++; });
    nonFinancePatterns.forEach(p => { if (p.test(combined)) nonFinanceScore++; });

    if (financeScore >= 2 || (financeScore >= 1 && nonFinanceScore === 0)) {
      if (/invoice/i.test(combined)) return { type: 'invoice', confidence: 0.9 };
      if (/receipt/i.test(combined)) return { type: 'receipt', confidence: 0.85 };
      if (/payment/i.test(combined)) return { type: 'payment', confidence: 0.85 };
      if (/statement/i.test(combined)) return { type: 'statement', confidence: 0.8 };
      return { type: 'financial', confidence: 0.7 };
    }

    if (nonFinanceScore >= 2 || financeScore === 0) {
      return { type: 'non-finance', reason: 'No financial indicators detected' };
    }

    return { type: 'unknown', confidence: 0.5, reason: 'Unclear email type' };
  }

  window.ClearledgrClients = window.ClearledgrClients || {};
  window.ClearledgrClients.ClassificationClient = ClassificationClient;
})();
