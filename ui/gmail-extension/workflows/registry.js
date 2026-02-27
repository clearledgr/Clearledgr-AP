(function() {
  const workflows = {
    invoice_exception_v1: {
      id: 'invoice_exception_v1',
      name: 'Invoice Exception Workflow',
      plan: 'Classify AP email, extract invoice fields, run policy checks, route approvals/exceptions, and reconcile ERP outcomes.',
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
