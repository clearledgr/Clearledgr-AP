"""Workflow hooks — conditions, the WASM sandbox, and the dispatcher.

Two tiers of customer logic for declarative Box types:

  * ``expressions`` — a safe, no-sandbox condition language over box data.
    Covers the common case (guards like ``amount > 10000``) with zero
    untrusted-code-execution risk. Always available.
  * ``sandbox`` — a capability-gated, resource-limited WASM runtime for
    full customer code hooks. Gated behind ``FEATURE_WORKFLOW_HOOKS`` and an
    adversarial security review before it is enabled for any tenant.

The ``dispatcher`` runs the configured hooks on a transition and turns their
output into an allow/deny decision plus a whitelisted data patch and a list of
built-in effects to apply.
"""
from __future__ import annotations
