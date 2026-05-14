"""Solden CLI — operator + ops-engineering tooling.

Entry: ``python -m clearledgr.cli`` or the ``solden`` script in
``scripts/solden`` which is a thin shim around the same module.

V0.1 scope (Sprint 1 of the ModernRelay-inspired roadmap, 2026-05-10):
operator-local tool, talks directly to the database via the existing
``ClearledgrDB`` facade. No remote/API mode yet — that's a later
sprint when we want SaaS customers to manage their own tenants.

Subcommand groups:

* ``tenants`` — list / inspect organizations
* ``audit``   — export audit events for a tenant + window
* ``policy``  — show / list / replay AP policy versions
* ``migrations`` — schema version + pending migrations
* ``health``  — DB connectivity + schema sanity check

Output formats: table (default, human-readable) and ``--json`` for
scripting. All commands accept ``--db-url`` to override
``DATABASE_URL`` from the environment, useful when the CLI is run
on a developer laptop against a staging DB.
"""
from __future__ import annotations
