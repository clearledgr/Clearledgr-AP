"""Concrete :class:`MatchEngine` implementations.

Each module registers its engine at import time. Importing this
package eagerly (from ``main.py``) ensures every engine is in the
registry before any orchestration call.
"""
from clearledgr.services.match_engines import ap_three_way  # noqa: F401
from clearledgr.services.match_engines import bank_reconciliation  # noqa: F401
