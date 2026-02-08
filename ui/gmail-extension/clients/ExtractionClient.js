(function() {
  const BaseClient = window.ClearledgrClients && window.ClearledgrClients.BaseClient;

  class ExtractionClient extends BaseClient {
    constructor() {
      super('ExtractionClient');
    }

    async run(ctx) {
      this.log(
        ctx,
        'reasoning',
        'Extracting finance signals',
        'Parsed locally and refined with backend extraction when available.'
      );

      const extractInvoiceViaAPI = ctx.services?.extractInvoiceViaAPI;
      const parser = window.ClearledgrEmailParsing;
      let parsedData = parser ? parser.parseFinancialData(ctx.emailData) : {};
      // Parse bank statement attachments into transactions for ingestion (CSV/PDF text)
      if (parser && ctx.emailData?.attachments?.length) {
        try {
          const txCsv = parser.parseAttachmentTransactions(ctx.emailData.attachments) || [];
          const txPdf = ctx.emailData.attachmentText
            ? parser.parseTextTransactions(ctx.emailData.attachmentText)
            : [];
          const allTx = [...txCsv, ...txPdf];
          if (allTx.length > 0) {
            ctx.state.parsedTransactions = allTx;
            parsedData.emailType = 'bank_statement';
            this.log(ctx, 'observation', 'Parsed attachment transactions', `Detected ${allTx.length} bank transactions from attachments.`);
          }
        } catch (err) {
          this.log(ctx, 'attention', 'Attachment transaction parsing failed', err.message || 'Could not parse attachments');
        }
      }
      parsedData.emailType = ctx.state.emailType?.type || parsedData.emailType || 'financial';

      const attachments = ctx.emailData?.attachments || [];
      let attachmentContext = null;
      let attachmentPayloads = [];
      const hasAttachments = attachments.length > 0;
      if (!hasAttachments) {
        this.log(
          ctx,
          'attention',
          'No attachments found',
          'Email has no downloadable attachments; only body text will be used.'
        );
      } else {
        const attachmentSummary = attachments
          .map((att) => `${att.name || att.filename || 'attachment'} (${att.mimeType || att.type || 'unknown'})`)
          .join('; ');
        this.log(
          ctx,
          'observation',
          'Attachments detected',
          attachmentSummary || 'Attachments present'
        );
      }
      const needsAttachment =
        !parsedData.amountRaw ||
        !parsedData.invoiceNumber ||
        parsedData.vendor === 'Unknown Vendor' ||
        (parsedData.amountScore !== undefined && parsedData.amountScore < 20) ||
        (parsedData.invoiceScore !== undefined && parsedData.invoiceScore < 55) ||
        (parsedData.vendorScore !== undefined && parsedData.vendorScore < 18) ||
        (parsedData.invoiceDateScore !== undefined && parsedData.invoiceDateScore < 40);

      if (parser?.extractAttachmentText && hasAttachments) {
        this.log(
          ctx,
          'reasoning',
          'Checking invoice attachments',
          needsAttachment
            ? 'Parsing attachments locally to fill missing fields.'
            : 'Parsing attachments for confirmation and enrichment.'
        );

        try {
          const attachmentResult = await parser.extractAttachmentText(attachments);
          if (attachmentResult?.text) {
            const attachmentParsed = parser.parseFinancialData({
              ...ctx.emailData,
              attachmentText: attachmentResult.text
            });
            parsedData = mergeParsedData(parsedData, attachmentParsed);
            parsedData.attachmentSource = attachmentResult.name;
            attachmentContext = {
              text: attachmentResult.text,
              name: attachmentResult.name,
              type: attachmentResult.type
            };

            const attachmentLabel = attachmentResult.type
              ? `${attachmentResult.type.toUpperCase()} attachment`
              : 'attachment';
            this.log(
              ctx,
              'observation',
              'Parsed attachment',
              `Extracted text from ${attachmentResult.name || attachmentLabel}.`
            );
          } else if (attachmentResult?.reason) {
            this.log(
              ctx,
              'observation',
              'Attachment skipped',
              attachmentResult.reason
            );
          }
        } catch (error) {
          this.log(
            ctx,
            'attention',
            'Attachment parsing failed',
            error.message || 'Unable to parse PDF attachments.'
          );
        }
      }

      if (parser?.extractAttachmentPayloads && hasAttachments) {
        try {
          attachmentPayloads = await parser.extractAttachmentPayloads(attachments, {
            maxCandidates: 1
          });
          const payloadSummary = attachmentPayloads.map((payload) => {
            const base64 = payload.contentBase64 || payload.content_base64 || '';
            const sizeBytes = base64 ? Math.round((base64.length * 3) / 4) : 0;
            const sizeLabel = sizeBytes ? `${Math.round(sizeBytes / 1024)} KB` : 'unknown size';
            return `${payload.filename || payload.name || 'attachment'} (${payload.contentType || payload.content_type || 'file'}, ${sizeLabel})`;
          });
          this.log(
            ctx,
            attachmentPayloads.length > 0 ? 'observation' : 'attention',
            attachmentPayloads.length > 0 ? 'Prepared attachment images' : 'Attachment payloads missing',
            payloadSummary.join('; ') || 'No eligible attachment payloads were prepared.'
          );
        } catch (error) {
          this.log(
            ctx,
            'attention',
            'Attachment upload skipped',
            error.message || 'Unable to prepare attachment payloads.'
          );
        }
      }

      if (extractInvoiceViaAPI) {
        this.log(
          ctx,
          'reasoning',
          'Consulting Clearledgr backend',
          'Using full email context for extraction.'
        );

        try {
          const apiResult = await extractInvoiceViaAPI(ctx.emailData, attachmentContext, attachmentPayloads);
          const extraction = apiResult?.extraction || apiResult?.result?.extraction || null;
          if (extraction) {
            parsedData = mergeParsedData(parsedData, mapApiExtraction(extraction));
            parsedData = sanitizeAmountFromInvoice(parsedData);
            parsedData.llmConfidence = Math.round((extraction.confidence || 0) * 100);
            parsedData.extractionSource = extraction.metadata?.source || 'backend';

            this.log(
              ctx,
              'observation',
              'Backend extraction complete',
              `Confidence: ${parsedData.llmConfidence || 0}%`
            );

            if (!parsedData.amount && !parsedData.amountRaw) {
              const attNote = attachmentPayloads.length
                ? `Sent ${attachmentPayloads.length} attachment payload(s) to backend`
                : 'No attachment payloads were sent';
              this.log(
                ctx,
                'attention',
                'Backend returned no amount',
                `${attNote}; invoice: ${parsedData.invoiceNumber || 'none'}`
              );
            }
          }
        } catch (error) {
          this.log(
            ctx,
            'attention',
            'Backend extraction unavailable',
            error.message || 'Continuing with local extraction.'
          );
        }
      }

      parsedData = sanitizeAmountFromInvoice(parsedData);
      ctx.state.parsedData = parsedData;

      const extractedParts = [];
      if (parsedData.vendor) extractedParts.push(`Vendor: ${parsedData.vendor}`);
      if (parsedData.amount) extractedParts.push(`Amount: ${parsedData.amount}`);
      if (parsedData.invoiceNumber) extractedParts.push(`Invoice: ${parsedData.invoiceNumber}`);
      if (parsedData.invoiceDate) extractedParts.push(`Date: ${parsedData.invoiceDate}`);
      if (parsedData.paymentTerms) extractedParts.push(`Terms: ${parsedData.paymentTerms}`);

      this.log(
        ctx,
        'observation',
        'Extracted finance fields',
        extractedParts.length > 0 ? extractedParts.join(' | ') : 'No structured finance fields found.'
      );
    }
  }

  function mergeParsedData(base, next) {
    const merged = { ...base };

    const baseVendorScore = base.vendorScore || 0;
    const nextVendorScore = next.vendorScore || 0;
    const vendorImproved =
      next.vendor &&
      next.vendor !== 'Unknown Vendor' &&
      (!base.vendor || base.vendor === 'Unknown Vendor' || nextVendorScore >= baseVendorScore + 5);

    if (vendorImproved) {
      merged.vendor = next.vendor;
      merged.vendorScore = next.vendorScore;
      merged.vendorSource = next.vendorSource || merged.vendorSource;
    }

    const baseAmountScore = base.amountScore || 0;
    const nextAmountScore = next.amountScore || 0;
    const amountImproved =
      next.amountRaw &&
      (!base.amountRaw || nextAmountScore >= baseAmountScore + 8);

    if (amountImproved) {
      merged.amountRaw = next.amountRaw;
      merged.amount = next.amount;
      merged.currency = next.currency;
      merged.amountScore = next.amountScore;
      merged.amountSource = next.amountSource || merged.amountSource;
    }

    const baseInvoiceScore = base.invoiceScore || 0;
    const nextInvoiceScore = next.invoiceScore || 0;
    const invoiceImproved =
      next.invoiceNumber &&
      (!base.invoiceNumber || nextInvoiceScore >= baseInvoiceScore + 6);

    if (invoiceImproved) {
      merged.invoiceNumber = next.invoiceNumber;
      merged.invoiceScore = next.invoiceScore;
      merged.invoiceSource = next.invoiceSource || merged.invoiceSource;
    }

    const baseDateScore = base.invoiceDateScore || 0;
    const nextDateScore = next.invoiceDateScore || 0;
    const dateImproved =
      next.invoiceDate &&
      (!base.invoiceDate || nextDateScore >= baseDateScore + 6);

    if (dateImproved) {
      merged.invoiceDate = next.invoiceDate;
      merged.invoiceDateScore = next.invoiceDateScore;
      merged.invoiceDateSource = next.invoiceDateSource || merged.invoiceDateSource;
    }

    if (!base.paymentTerms && next.paymentTerms) {
      merged.paymentTerms = next.paymentTerms;
    }

    return merged;
  }

  function normalizeDigits(value) {
    return String(value || '').replace(/\D/g, '');
  }

  function amountLooksLikeInvoice(amountValue, invoiceNumber) {
    if (!invoiceNumber || amountValue === null || amountValue === undefined) return false;
    const amountNumber = Number(amountValue);
    if (!Number.isFinite(amountNumber)) return false;
    const invoiceDigits = normalizeDigits(invoiceNumber);
    if (!invoiceDigits || invoiceDigits.length < 6) return false;
    const amountDigits = normalizeDigits(amountNumber);
    if (!amountDigits) return false;
    return amountDigits === invoiceDigits;
  }

  function sanitizeAmountFromInvoice(parsedData) {
    if (!parsedData) return parsedData;
    const amountNumber = Number(parsedData.amountRaw);

    if (amountLooksLikeInvoice(parsedData.amountRaw, parsedData.invoiceNumber)) {
      return {
        ...parsedData,
        amountRaw: null,
        amount: null,
        amountScore: 0,
        amountSource: 'sanitized'
      };
    }

    if (Number.isFinite(amountNumber)) {
      const dateYearMatch = String(parsedData.invoiceDate || '').match(/(20\d{2}|19\d{2})/);
      const dateYear = dateYearMatch ? Number(dateYearMatch[1]) : null;
      if (dateYear && amountNumber === dateYear) {
        return {
          ...parsedData,
          amountRaw: null,
          amount: null,
          amountScore: 0,
          amountSource: 'sanitized'
        };
      }
    }
    return parsedData;
  }

  function normalizeDigits(value) {
    return String(value || '').replace(/\D/g, '');
  }

  function isLikelyInvoiceNumberAsAmount(amountValue, invoiceNumber) {
    if (amountValue === null || amountValue === undefined || !invoiceNumber) return false;
    const amountNumber = Number(amountValue);
    if (!Number.isFinite(amountNumber)) return false;

    const invoiceDigits = normalizeDigits(invoiceNumber);
    if (!invoiceDigits || invoiceDigits.length < 6) return false;

    const amountDigits = normalizeDigits(amountNumber);
    if (!amountDigits) return false;

    if (amountDigits === invoiceDigits) return true;
    if (invoiceDigits.length >= 8 && amountNumber >= 10000000 && Number.isInteger(amountNumber)) {
      return true;
    }
    return false;
  }

  function mapApiExtraction(extraction) {
    const total = extraction.total || {};
    let amountValue = total.amount ?? extraction.total_amount ?? null;
    const currency = total.currency || extraction.currency || null;
    const invoiceNumber = extraction.invoice_number || null;

    if (isLikelyInvoiceNumberAsAmount(amountValue, invoiceNumber)) {
      amountValue = null;
    }

    return {
      vendor: extraction.vendor || null,
      vendorScore: extraction.vendor ? 95 : 0,
      vendorSource: 'llm',
      amountRaw: amountValue,
      amount: amountValue !== null ? formatAmount(amountValue, currency) : null,
      amountScore: amountValue !== null ? 95 : 0,
      amountSource: 'llm',
      currency,
      invoiceNumber,
      invoiceScore: invoiceNumber ? 90 : 0,
      invoiceSource: 'llm',
      invoiceDate: extraction.invoice_date || null,
      invoiceDateScore: extraction.invoice_date ? 90 : 0,
      invoiceDateSource: 'llm'
    };
  }

  function formatAmount(value, currency) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return null;
    const formatted = Number(value).toLocaleString('en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    });

    if (!currency) return formatted;
    return `${currency} ${formatted}`;
  }

  window.ClearledgrClients = window.ClearledgrClients || {};
  window.ClearledgrClients.ExtractionClient = ExtractionClient;
})();
