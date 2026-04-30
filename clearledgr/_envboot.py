"""Environment bootstrap.

Importing this module loads ``.env`` via python-dotenv. It exists so the
load can happen as the very first import of the entrypoint without
forcing a non-import statement (``load_dotenv()``) between the entrypoint's
imports — which would trigger E402 on every subsequent import line.

Usage at the top of an entrypoint::

    import clearledgr._envboot  # noqa: F401  -- side-effect import

The ``# noqa: F401`` is appropriate because the import IS the side
effect; the symbol itself is intentionally unused.
"""
from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()
