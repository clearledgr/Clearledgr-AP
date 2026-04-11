/**
 * Clearledgr AP — Google Workspace Add-on
 * DESIGN_THESIS.md §6.9
 *
 * "The Add-on is for approvals only. It is not a full Clearledgr experience
 * on mobile. An AP Clerk processing invoices needs the full Chrome extension
 * on a desktop. A CFO approving a £200,000 payment from their phone needs
 * the Add-on. Both needs are real. The product serves both without conflating them."
 *
 * Surfaces a lightweight panel in the native Gmail app (iOS/Android)
 * showing: invoice amount, match status, and an Approve or Reject button.
 * Nothing else. Loads fast, works on a phone, requires no additional auth.
 */

var API_BASE = PropertiesService.getScriptProperties().getProperty('CLEARLEDGR_API_URL') || 'https://api.clearledgr.com';

// ==================== TRIGGER: Gmail message opened ====================

function onGmailMessage(e) {
  var messageId = e.gmail.messageId;
  var accessToken = e.gmail.accessToken;
  var userEmail = Session.getActiveUser().getEmail();

  // Look up AP item by Gmail message/thread ID
  var item = _fetchApItem(messageId, userEmail);

  if (!item) {
    // Not a Clearledgr invoice — show nothing
    return CardService.newCardBuilder()
      .setHeader(CardService.newCardHeader().setTitle('Clearledgr'))
      .addSection(
        CardService.newCardSection()
          .addWidget(CardService.newTextParagraph().setText('No invoice linked to this email.'))
      )
      .build();
  }

  return _buildApprovalCard(item);
}

// ==================== APPROVAL CARD ====================

function _buildApprovalCard(item) {
  var vendor = item.vendor_name || 'Unknown vendor';
  var amount = _formatAmount(item.amount, item.currency);
  var invoiceNum = item.invoice_number || '—';
  var state = (item.state || '').replace(/_/g, ' ');
  var matchStatus = item.match_status || '—';
  var dueDate = (item.due_date || '').substring(0, 10);
  var matchPassed = (item.state === 'needs_approval' || item.state === 'pending_approval');
  var isException = (item.state === 'needs_info' || item.state === 'failed_post');

  // Match icons
  var poIcon = (item.po_number) ? '✓' : '✗';
  var matchIcon = (matchStatus === 'passed') ? '✓' : (matchStatus === 'exception') ? '⚠' : '—';

  var card = CardService.newCardBuilder()
    .setHeader(
      CardService.newCardHeader()
        .setTitle(vendor)
        .setSubtitle(amount)
    );

  // Invoice section
  var invoiceSection = CardService.newCardSection()
    .setHeader('Invoice')
    .addWidget(CardService.newKeyValue()
      .setTopLabel('Amount')
      .setContent(amount))
    .addWidget(CardService.newKeyValue()
      .setTopLabel('Invoice #')
      .setContent(invoiceNum))
    .addWidget(CardService.newKeyValue()
      .setTopLabel('Status')
      .setContent(state));

  if (dueDate) {
    invoiceSection.addWidget(CardService.newKeyValue()
      .setTopLabel('Due date')
      .setContent(dueDate));
  }

  card.addSection(invoiceSection);

  // Match section
  var matchSection = CardService.newCardSection()
    .setHeader('3-Way Match')
    .addWidget(CardService.newTextParagraph()
      .setText('PO ' + poIcon + '  GRN ' + matchIcon + '  Invoice ' + matchIcon));

  if (item.exception_reason) {
    matchSection.addWidget(CardService.newTextParagraph()
      .setText('⚠ ' + item.exception_reason));
  }

  card.addSection(matchSection);

  // Action buttons
  var actionSection = CardService.newCardSection();

  if (matchPassed) {
    actionSection.addWidget(
      CardService.newTextButton()
        .setText('Approve')
        .setBackgroundColor('#16A34A')
        .setTextButtonStyle(CardService.TextButtonStyle.FILLED)
        .setOnClickAction(
          CardService.newAction()
            .setFunctionName('onApprove')
            .setParameters({ 'ap_item_id': item.id, 'invoice_id': invoiceNum })
        )
    );
  }

  if (isException) {
    actionSection.addWidget(
      CardService.newTextButton()
        .setText('Override and Approve')
        .setBackgroundColor('#CA8A04')
        .setTextButtonStyle(CardService.TextButtonStyle.FILLED)
        .setOnClickAction(
          CardService.newAction()
            .setFunctionName('onOverrideApprove')
            .setParameters({ 'ap_item_id': item.id, 'invoice_id': invoiceNum })
        )
    );
  }

  actionSection.addWidget(
    CardService.newTextButton()
      .setText('Reject')
      .setTextButtonStyle(CardService.TextButtonStyle.TEXT)
      .setOnClickAction(
        CardService.newAction()
          .setFunctionName('onReject')
          .setParameters({ 'ap_item_id': item.id, 'invoice_id': invoiceNum })
      )
  );

  card.addSection(actionSection);

  return card.build();
}

// ==================== ACTION HANDLERS ====================

function onApprove(e) {
  var itemId = e.commonEventObject.parameters.ap_item_id;
  var result = _callApi('/api/ap/items/' + itemId + '/approve', 'POST', {
    reason: 'approved_via_mobile',
    source: 'workspace_addon',
  });

  var message = (result && result.status === 'approved')
    ? '✓ Approved. Posted to ERP.'
    : 'Approval failed: ' + (result ? result.reason || 'unknown' : 'API error');

  return _buildNotificationCard(message);
}

function onOverrideApprove(e) {
  var itemId = e.commonEventObject.parameters.ap_item_id;
  var result = _callApi('/api/ap/items/' + itemId + '/approve', 'POST', {
    reason: 'override_approved_via_mobile',
    source: 'workspace_addon',
    override: true,
  });

  var message = (result && (result.status === 'approved' || result.status === 'posted'))
    ? '✓ Override approved. Posted to ERP.'
    : 'Override failed: ' + (result ? result.reason || 'unknown' : 'API error');

  return _buildNotificationCard(message);
}

function onReject(e) {
  var itemId = e.commonEventObject.parameters.ap_item_id;
  var result = _callApi('/api/ap/items/' + itemId + '/reject', 'POST', {
    reason: 'rejected_via_mobile',
    source: 'workspace_addon',
  });

  var message = (result && result.status === 'rejected')
    ? 'Invoice rejected.'
    : 'Rejection failed: ' + (result ? result.reason || 'unknown' : 'API error');

  return _buildNotificationCard(message);
}

// ==================== API HELPERS ====================

function _fetchApItem(messageId, userEmail) {
  // Try to find an AP item linked to this Gmail message
  var result = _callApi(
    '/extension/worklist?organization_id=default&limit=500',
    'GET'
  );

  if (!result || !result.items) return null;

  // Match by message_id or thread_id
  for (var i = 0; i < result.items.length; i++) {
    var item = result.items[i];
    if (item.message_id === messageId || item.thread_id === messageId) {
      return item;
    }
  }

  return null;
}

function _callApi(path, method, payload) {
  var url = API_BASE + path;
  var token = ScriptApp.getOAuthToken();

  var options = {
    method: method || 'GET',
    headers: {
      'Authorization': 'Bearer ' + token,
      'Content-Type': 'application/json',
    },
    muteHttpExceptions: true,
  };

  if (payload) {
    options.payload = JSON.stringify(payload);
  }

  try {
    var response = UrlFetchApp.fetch(url, options);
    var code = response.getResponseCode();
    if (code >= 200 && code < 300) {
      return JSON.parse(response.getContentText());
    }
    Logger.log('API error: ' + code + ' ' + response.getContentText());
    return null;
  } catch (err) {
    Logger.log('API call failed: ' + err);
    return null;
  }
}

function _formatAmount(amount, currency) {
  if (!amount && amount !== 0) return 'N/A';
  var cur = currency || 'USD';
  var symbols = { 'GBP': '\u00a3', 'EUR': '\u20ac', 'USD': '$' };
  var symbol = symbols[cur] || cur + ' ';
  return symbol + Number(amount).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function _buildNotificationCard(message) {
  return CardService.newActionResponseBuilder()
    .setNotification(
      CardService.newNotification().setText(message)
    )
    .setNavigation(
      CardService.newNavigation().popToRoot()
    )
    .build();
}
