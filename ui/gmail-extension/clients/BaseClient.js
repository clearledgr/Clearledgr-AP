(function() {
  class BaseClient {
    constructor(name) {
      this.name = name || 'Client';
    }

    async run() {
      throw new Error('Client run() not implemented');
    }

    log(ctx, type, title, detail) {
      if (ctx && typeof ctx.log === 'function') {
        ctx.log(type, title, detail);
      }
    }

    halt(ctx, reason) {
      if (!ctx) return;
      ctx.halt = true;
      if (reason) ctx.haltReason = reason;
    }
  }

  window.ClearledgrClients = window.ClearledgrClients || {};
  window.ClearledgrClients.BaseClient = BaseClient;
})();
