(function() {
  const BaseClient = window.ClearledgrClients && window.ClearledgrClients.BaseClient;

  class ExceptionClient extends BaseClient {
    constructor() {
      super('ExceptionClient');
    }

    async run(ctx) {
      const matchResult = ctx.state.matchResult;
      if (!matchResult || matchResult.found) return;

      const parsedData = ctx.state.parsedData || {};
      const autoRoute = ctx.settings?.autoRouteExceptions;

      if (!autoRoute) {
        this.log(
          ctx,
          'attention',
          'Exception requires review',
          'Auto-route is off; use "Flag" to create a task.'
        );
        return;
      }

      this.log(
        ctx,
        'action',
        'Routing exception to Slack',
        'Creating approval task.'
      );

      try {
        const task = ctx.services?.ensureExceptionTask
          ? await ctx.services.ensureExceptionTask(parsedData, matchResult)
          : null;
        ctx.state.exceptionTask = task;

        if (task) {
          const statusLabel = task.status ? task.status.replace(/_/g, ' ') : 'open';
          this.log(
            ctx,
            'result',
            'Exception queued for review',
            `Task status: ${statusLabel}.`
          );
        } else {
          this.log(
            ctx,
            'attention',
            'Exception routing skipped',
            'Task already exists or could not be created.'
          );
        }
      } catch (error) {
        console.error('[Clearledgr] Auto-route failed:', error);
        this.log(
          ctx,
          'error',
          'Failed to route exception',
          error?.message || 'Unknown error'
        );
      }
    }
  }

  window.ClearledgrClients = window.ClearledgrClients || {};
  window.ClearledgrClients.ExceptionClient = ExceptionClient;
})();
