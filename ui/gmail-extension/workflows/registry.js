(function() {
  const workflows = {
    invoice_exception_v1: {
      id: 'invoice_exception_v1',
      name: 'Invoice Exception Workflow',
      plan: 'Classify email, extract finance fields, match transactions, categorize, route exceptions.',
      clients: [
        'ClassificationClient',
        'ExtractionClient',
        'MatchingClient',
        'CategorizationClient',
        'ExceptionClient'
      ]
    }
  };

  function get(id) {
    return workflows[id] || null;
  }

  function list() {
    return Object.keys(workflows).map(key => workflows[key]);
  }

  window.ClearledgrWorkflowRegistry = { get, list, workflows };
})();
