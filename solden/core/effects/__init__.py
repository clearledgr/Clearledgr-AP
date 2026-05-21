"""Built-in effects a workflow hook may request.

Hooks are pure: they cannot touch the database, the network, or other tenants.
Instead a hook *requests* effects (notify, webhook, ...) and trusted host code
here applies them, tenant-scoped and guarded. This keeps the dangerous I/O on
the trusted side of the sandbox boundary.
"""
from __future__ import annotations
