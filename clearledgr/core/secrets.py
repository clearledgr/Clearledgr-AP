"""Centralized secret loading with dev/prod behavior.

In dev mode (ENV != "production"), missing secrets get a random fallback
and a warning. In production, missing required secrets crash on startup.
"""

import logging
import os
import secrets as _secrets_mod

logger = logging.getLogger(__name__)

_generated_cache: dict[str, str] = {}


def _is_production() -> bool:
    # Treat staging as production-like for secret enforcement.
    return os.getenv("ENV", "dev").lower() in ("production", "prod", "staging", "stage")


def require_secret(name: str) -> str:
    """Return the value of an environment variable, or raise in production.

    In dev mode a random token is generated once and cached for the process
    lifetime so that callers get a stable value within a single run.
    """
    val = os.getenv(name)
    if val:
        return val

    if _is_production():
        raise RuntimeError(
            f"Required secret {name!r} is not set. "
            "Set it as an environment variable before starting in production."
        )

    # Dev mode: generate a stable random value per process
    if name not in _generated_cache:
        _generated_cache[name] = _secrets_mod.token_urlsafe(32)
        logger.warning(
            "DEV MODE: Generated random value for %s — "
            "set this env var to silence this warning.",
            name,
        )
    return _generated_cache[name]


def optional_secret(name: str, *, default: str = "") -> str:
    """Return the value of an env var, falling back to *default* silently."""
    return os.getenv(name, default)
