"""Tests for clearledgr.core.secrets — dev/prod secret loading."""

import hashlib
import platform

import pytest


class TestRequireSecret:
    def test_returns_env_var_when_set(self, monkeypatch):
        monkeypatch.setenv("TEST_SECRET_1", "my-value")
        from clearledgr.core import secrets
        secrets._generated_cache.clear()
        assert secrets.require_secret("TEST_SECRET_1") == "my-value"

    def test_raises_in_production_when_missing(self, monkeypatch):
        monkeypatch.setenv("ENV", "production")
        monkeypatch.delenv("MISSING_PROD_SECRET", raising=False)
        from clearledgr.core import secrets
        secrets._generated_cache.clear()
        with pytest.raises(RuntimeError, match="MISSING_PROD_SECRET"):
            secrets.require_secret("MISSING_PROD_SECRET")

    def test_raises_in_staging(self, monkeypatch):
        monkeypatch.setenv("ENV", "staging")
        monkeypatch.delenv("MISSING_STAGING_SECRET", raising=False)
        from clearledgr.core import secrets
        secrets._generated_cache.clear()
        with pytest.raises(RuntimeError):
            secrets.require_secret("MISSING_STAGING_SECRET")

    def test_generates_deterministic_value_in_dev(self, monkeypatch):
        monkeypatch.setenv("ENV", "dev")
        monkeypatch.delenv("DEV_TEST_SECRET", raising=False)
        from clearledgr.core import secrets
        secrets._generated_cache.clear()

        val = secrets.require_secret("DEV_TEST_SECRET")
        expected = hashlib.sha256(
            f"{platform.node()}:DEV_TEST_SECRET".encode()
        ).hexdigest()
        assert val == expected
        assert len(val) == 64  # SHA-256 hex digest

    def test_caches_dev_value_across_calls(self, monkeypatch):
        monkeypatch.setenv("ENV", "dev")
        monkeypatch.delenv("CACHE_TEST_SECRET", raising=False)
        from clearledgr.core import secrets
        secrets._generated_cache.clear()

        val1 = secrets.require_secret("CACHE_TEST_SECRET")
        val2 = secrets.require_secret("CACHE_TEST_SECRET")
        assert val1 == val2
        assert "CACHE_TEST_SECRET" in secrets._generated_cache

    def test_different_names_produce_different_values(self, monkeypatch):
        monkeypatch.setenv("ENV", "dev")
        monkeypatch.delenv("SECRET_A", raising=False)
        monkeypatch.delenv("SECRET_B", raising=False)
        from clearledgr.core import secrets
        secrets._generated_cache.clear()

        a = secrets.require_secret("SECRET_A")
        b = secrets.require_secret("SECRET_B")
        assert a != b

    def test_env_var_takes_precedence_over_cache(self, monkeypatch):
        monkeypatch.setenv("ENV", "dev")
        from clearledgr.core import secrets
        secrets._generated_cache["PRECEDENCE_SECRET"] = "cached-val"
        monkeypatch.setenv("PRECEDENCE_SECRET", "env-val")
        assert secrets.require_secret("PRECEDENCE_SECRET") == "env-val"


class TestOptionalSecret:
    def test_returns_value_when_set(self, monkeypatch):
        monkeypatch.setenv("OPT_SECRET", "opt-value")
        from clearledgr.core.secrets import optional_secret
        assert optional_secret("OPT_SECRET") == "opt-value"

    def test_returns_empty_string_by_default(self, monkeypatch):
        monkeypatch.delenv("MISSING_OPT_SECRET", raising=False)
        from clearledgr.core.secrets import optional_secret
        assert optional_secret("MISSING_OPT_SECRET") == ""

    def test_returns_custom_default(self, monkeypatch):
        monkeypatch.delenv("CUSTOM_DEFAULT_SECRET", raising=False)
        from clearledgr.core.secrets import optional_secret
        assert optional_secret("CUSTOM_DEFAULT_SECRET", default="fallback") == "fallback"


class TestIsProduction:
    @pytest.mark.parametrize("env_val", ["production", "prod", "staging", "stage"])
    def test_production_envs(self, monkeypatch, env_val):
        monkeypatch.setenv("ENV", env_val)
        from clearledgr.core.secrets import _is_production
        assert _is_production() is True

    @pytest.mark.parametrize("env_val", ["dev", "development", "test", "local", ""])
    def test_non_production_envs(self, monkeypatch, env_val):
        monkeypatch.setenv("ENV", env_val)
        from clearledgr.core.secrets import _is_production
        assert _is_production() is False

    def test_missing_env_defaults_to_dev(self, monkeypatch):
        monkeypatch.delenv("ENV", raising=False)
        from clearledgr.core.secrets import _is_production
        assert _is_production() is False
