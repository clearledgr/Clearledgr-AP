(function() {
  const BaseClient = window.ClearledgrClients && window.ClearledgrClients.BaseClient;

  class MatchingClient extends BaseClient {
    constructor() {
      super('MatchingClient');
    }

    async run(ctx) {
      const parsedData = ctx.state.parsedData || {};

      this.log(
        ctx,
        'action',
        'Checking matching engine',
        'Matching extracted finance fields against connected sources.'
      );

      const checkBackendHealth = ctx.services?.checkBackendHealth;
      const matchInvoiceViaAPI = ctx.services?.matchInvoiceViaAPI;
      const getVendorInsightsViaAPI = ctx.services?.getVendorInsightsViaAPI;

      const isBackendUp = checkBackendHealth ? await checkBackendHealth() : false;
      let matchResult = { found: false, confidence: 0, reason: 'Backend offline' };
      let vendorInsights = null;

      if (isBackendUp) {
        this.log(
          ctx,
          'observation',
          'Matching engine available',
          'Searching connected transaction sources.'
        );

        try {
          const matchResponse = await matchInvoiceViaAPI({
            vendor: parsedData.vendor,
            amountRaw: parsedData.amountRaw,
            currency: parsedData.currency,
            invoiceNumber: parsedData.invoiceNumber,
            invoiceDate: parsedData.invoiceDate,
            emailType: parsedData.emailType
          });

          if (matchResponse?.matched) {
            matchResult = {
              found: true,
              confidence: Math.round((matchResponse.confidence || 0.9) * 100),
              matchedAmount: parsedData.amount,
              matchedVendor: parsedData.vendor,
              source: matchResponse.matched_transaction?.source || 'Bank Statement',
              transactionId: matchResponse.matched_transaction?.id
            };
            this.log(
              ctx,
              'result',
              'Match found',
              `Confidence: ${matchResult.confidence}% | Source: ${matchResult.source}`
            );
          } else {
            matchResult = {
              found: false,
              confidence: findMatch(parsedData).confidence,
              reason: matchResponse?.message || 'No matching transaction found'
            };
            this.log(ctx, 'attention', 'No matching transaction found', matchResult.reason);
          }

          if (getVendorInsightsViaAPI) {
            vendorInsights = await getVendorInsightsViaAPI(parsedData.vendor);
          }
        } catch (matchError) {
          console.error('[Clearledgr] Match API error:', matchError);
          this.log(
            ctx,
            'attention',
            'Matching unavailable',
            'Using local fallback scoring.'
          );
          matchResult = findMatch(parsedData);
        }
      } else {
        this.log(ctx, 'attention', 'Backend offline', 'Using local fallback scoring.');
        matchResult = findMatch(parsedData);
      }

      ctx.state.matchResult = matchResult;
      ctx.state.vendorInsights = vendorInsights;
    }
  }

  function findMatch(parsedData) {
    if (!parsedData.amountRaw) {
      return { found: false, confidence: 0, reason: 'No amount detected' };
    }

    const hasInvoice = !!parsedData.invoiceNumber;
    const hasVendor = parsedData.vendor !== 'Unknown Vendor';
    const hasDate = !!parsedData.invoiceDate;

    let dataQuality = 40;
    if (hasInvoice) dataQuality += 25;
    if (hasVendor) dataQuality += 20;
    if (hasDate) dataQuality += 15;

    return {
      found: false,
      confidence: dataQuality,
      reason: 'Connect to backend for transaction matching',
      dataQuality: {
        hasAmount: true,
        hasVendor,
        hasInvoice,
        hasDate
      }
    };
  }

  window.ClearledgrClients = window.ClearledgrClients || {};
  window.ClearledgrClients.MatchingClient = MatchingClient;
})();
