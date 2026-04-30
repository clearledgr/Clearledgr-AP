"""Tests for org-level autonomy threshold overrides.

Validates that:
- _load_org_autonomy_thresholds falls back to defaults for missing orgs
- Org-level overrides are merged correctly
- Partial overrides only affect specified keys
- Malformed / non-dict settings fall back to defaults
- autonomy_action_thresholds() respects org_id or returns defaults
"""
import copy
from unittest.mock import MagicMock, patch


from clearledgr.services.finance_runtime_autonomy import (
    _AUTONOMY_ACTION_THRESHOLDS,
    _load_org_autonomy_thresholds,
    autonomy_action_thresholds,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _defaults():
    """Return a deep copy of the module-level defaults for comparison."""
    return copy.deepcopy(_AUTONOMY_ACTION_THRESHOLDS)


def _mock_db_with_settings(settings_json):
    """Return a mock DB whose get_organization returns the given settings."""
    mock_db = MagicMock()
    mock_db.get_organization.return_value = {"settings_json": settings_json}
    return mock_db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_org_returns_defaults():
    """When the org does not exist in the DB, defaults are returned."""
    mock_db = MagicMock()
    mock_db.get_organization.return_value = None

    with patch(
        "clearledgr.core.database.get_db",
        return_value=mock_db,
    ):
        result = _load_org_autonomy_thresholds("org-missing")

    defaults = _defaults()
    for action in defaults:
        assert action in result
        for key, value in defaults[action].items():
            assert result[action][key] == value


def test_with_override():
    """Org settings override specific keys while preserving the rest."""
    settings = {
        "autonomy_thresholds": {
            "auto_approve": {
                "min_recent_invoice_count": 10,
            }
        }
    }
    mock_db = _mock_db_with_settings(settings)

    with patch(
        "clearledgr.core.database.get_db",
        return_value=mock_db,
    ):
        result = _load_org_autonomy_thresholds("org-custom")

    # Overridden key
    assert result["auto_approve"]["min_recent_invoice_count"] == 10
    # Non-overridden keys preserved
    defaults = _defaults()
    assert result["auto_approve"]["min_shadow_action_match_rate"] == defaults["auto_approve"]["min_shadow_action_match_rate"]
    # Other actions untouched
    assert result["route_low_risk_for_approval"] == defaults["route_low_risk_for_approval"]
    assert result["post_to_erp"] == defaults["post_to_erp"]


def test_partial_override():
    """Override one key of one action; all other actions and keys are unchanged."""
    settings = {
        "autonomy_thresholds": {
            "post_to_erp": {
                "min_post_verification_rate": 0.95,
            }
        }
    }
    mock_db = _mock_db_with_settings(settings)

    with patch(
        "clearledgr.core.database.get_db",
        return_value=mock_db,
    ):
        result = _load_org_autonomy_thresholds("org-partial")

    defaults = _defaults()
    assert result["post_to_erp"]["min_post_verification_rate"] == 0.95
    assert result["post_to_erp"]["require_zero_post_mismatches"] == defaults["post_to_erp"]["require_zero_post_mismatches"]
    assert result["auto_approve"] == defaults["auto_approve"]
    assert result["route_low_risk_for_approval"] == defaults["route_low_risk_for_approval"]


def test_malformed_settings():
    """If settings_json is unparseable, return defaults."""
    mock_db = _mock_db_with_settings("{{not valid json")

    with patch(
        "clearledgr.core.database.get_db",
        return_value=mock_db,
    ):
        result = _load_org_autonomy_thresholds("org-bad-json")

    defaults = _defaults()
    for action in defaults:
        assert action in result


def test_settings_not_dict():
    """If settings_json is a list instead of dict, return defaults."""
    mock_db = _mock_db_with_settings([1, 2, 3])

    with patch(
        "clearledgr.core.database.get_db",
        return_value=mock_db,
    ):
        result = _load_org_autonomy_thresholds("org-list")

    defaults = _defaults()
    for action in defaults:
        assert action in result
        for key, value in defaults[action].items():
            assert result[action][key] == value


def test_action_thresholds_with_org_id():
    """autonomy_action_thresholds('org-1') loads overrides from the org."""
    settings = {
        "autonomy_thresholds": {
            "auto_approve": {
                "min_recent_invoice_count": 8,
            }
        }
    }
    mock_db = _mock_db_with_settings(settings)

    with patch(
        "clearledgr.core.database.get_db",
        return_value=mock_db,
    ):
        result = autonomy_action_thresholds("org-1")

    assert result["auto_approve"]["min_recent_invoice_count"] == 8
    # Tuples are converted to lists in the public function
    assert isinstance(result["auto_approve"]["allowed_drift_risks"], list)


def test_action_thresholds_without_org_id():
    """autonomy_action_thresholds() with no org_id returns global defaults."""
    result = autonomy_action_thresholds()

    defaults = _defaults()
    assert set(result.keys()) == set(defaults.keys())
    for action in defaults:
        for key, value in defaults[action].items():
            expected = list(value) if isinstance(value, tuple) else value
            assert result[action][key] == expected
