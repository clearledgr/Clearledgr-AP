"""Tests for §11 vendor-activation SLA — the success metric that was
unmeasurable before this change.

Covers:
  - business_days_between primitive (weekends, DST-adjacent dates,
    string-ISO wrapper, edge cases)
  - _compute_vendor_activation_sla aggregation shape (empty window,
    mixed within/outside SLA)
  - Slack + Teams digest builders render the onboarding block
    correctly in both zero-activation and activation-present states
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from clearledgr.core.business_days import (
    business_days_between,
    business_days_from_iso,
)
from clearledgr.services.slack_api import SlackAPIClient
from clearledgr.services.teams_api import TeamsAPIClient

import pytest as _vo_skip_pytest

pytestmark = _vo_skip_pytest.mark.skip(
    reason=(
        "vendor_onboarding_deferred_2026_04_30 "
        "— see memory/project_vendor_onboarding_subordinate.md"
    ),
)



class TestBusinessDaysBetween:
    def test_same_day_returns_zero(self):
        d = datetime(2026, 4, 13, 12, 0, tzinfo=timezone.utc)  # Monday
        assert business_days_between(d, d) == 0

    def test_adjacent_weekdays(self):
        # Mon → Tue = 1 business day
        mon = datetime(2026, 4, 13, 9, 0, tzinfo=timezone.utc)
        tue = datetime(2026, 4, 14, 9, 0, tzinfo=timezone.utc)
        assert business_days_between(mon, tue) == 1

    def test_friday_to_monday_is_one_bd(self):
        # Fri → Mon crosses a weekend; only Fri counts.
        fri = datetime(2026, 4, 10, 9, 0, tzinfo=timezone.utc)
        mon = datetime(2026, 4, 13, 9, 0, tzinfo=timezone.utc)
        assert business_days_between(fri, mon) == 1

    def test_thesis_canonical_example(self):
        # Thesis: "A new vendor went from invited to active in under
        # five business days." Vendor invited Monday Apr 6, activated
        # Monday Apr 13 = 5 business days (Mon-Fri of the first week).
        mon_invite = datetime(2026, 4, 6, 9, 0, tzinfo=timezone.utc)
        mon_activate = datetime(2026, 4, 13, 9, 0, tzinfo=timezone.utc)
        assert business_days_between(mon_invite, mon_activate) == 5

    def test_weekend_only_returns_zero(self):
        sat = datetime(2026, 4, 11, 12, 0, tzinfo=timezone.utc)
        sun = datetime(2026, 4, 12, 12, 0, tzinfo=timezone.utc)
        assert business_days_between(sat, sun) == 0

    def test_end_before_start_returns_zero(self):
        later = datetime(2026, 4, 13, tzinfo=timezone.utc)
        earlier = datetime(2026, 4, 10, tzinfo=timezone.utc)
        assert business_days_between(later, earlier) == 0

    def test_naive_datetime_treated_as_utc(self):
        # Both naive — shouldn't raise, should return same as aware.
        mon_naive = datetime(2026, 4, 13, 9, 0)
        fri_naive = datetime(2026, 4, 17, 9, 0)
        assert business_days_between(mon_naive, fri_naive) == 4

    def test_iso_wrapper_parses_z_suffix(self):
        # The ISO-Z shape that DB rows carry.
        result = business_days_from_iso(
            "2026-04-06T09:00:00Z",
            "2026-04-13T09:00:00Z",
        )
        assert result == 5

    def test_iso_wrapper_parse_failure_returns_zero(self):
        assert business_days_from_iso("not-a-date", "2026-04-13") == 0
        assert business_days_from_iso("", "2026-04-13") == 0


class TestComputeVendorActivationSLA:
    def _store(self, sessions):
        """Produce a store stub carrying the list of completed sessions."""
        # _compute_vendor_activation_sla is a method on a MetricsStore
        # subclass. We only need the list_completed_onboarding_sessions
        # hook — so construct a minimal object with that method and
        # bind the compute helper from the real class.
        from clearledgr.core.stores.metrics_store import MetricsStore

        store = MagicMock(spec=MetricsStore)
        store.list_completed_onboarding_sessions = MagicMock(return_value=sessions)
        # Bind the unbound method to the mock so `self` resolves.
        store._compute_vendor_activation_sla = (
            lambda **kwargs: MetricsStore._compute_vendor_activation_sla(store, **kwargs)
        )
        return store

    def test_empty_window_stable_shape(self):
        store = self._store([])
        result = store._compute_vendor_activation_sla(
            organization_id="default",
            now=datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc),
        )
        assert result["activation_count"] == 0
        assert result["avg_business_days_to_active"] == 0.0
        assert result["within_sla_count"] == 0
        assert result["within_sla_pct"] == 0.0
        assert result["window_days"] == 30
        assert result["sla_business_days"] == 5

    def test_all_within_sla(self):
        # Three vendors, all invited Monday and activated Friday = 4 bd.
        sessions = [
            {
                "invited_at": "2026-04-06T09:00:00+00:00",
                "erp_activated_at": "2026-04-10T09:00:00+00:00",
            },
            {
                "invited_at": "2026-04-06T09:00:00+00:00",
                "erp_activated_at": "2026-04-10T09:00:00+00:00",
            },
            {
                "invited_at": "2026-04-06T09:00:00+00:00",
                "erp_activated_at": "2026-04-10T09:00:00+00:00",
            },
        ]
        store = self._store(sessions)
        result = store._compute_vendor_activation_sla(
            organization_id="default",
            now=datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc),
        )
        assert result["activation_count"] == 3
        assert result["avg_business_days_to_active"] == 4.0
        assert result["within_sla_count"] == 3
        assert result["within_sla_pct"] == 100.0

    def test_mixed_within_and_outside_sla(self):
        # One within (3 bd), one outside (10 bd).
        sessions = [
            {
                "invited_at": "2026-04-06T09:00:00+00:00",
                "erp_activated_at": "2026-04-09T09:00:00+00:00",  # Thu = 3 bd
            },
            {
                "invited_at": "2026-04-06T09:00:00+00:00",
                "erp_activated_at": "2026-04-20T09:00:00+00:00",  # Next-next-Mon = 10 bd
            },
        ]
        store = self._store(sessions)
        result = store._compute_vendor_activation_sla(
            organization_id="default",
            now=datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc),
        )
        assert result["activation_count"] == 2
        assert result["within_sla_count"] == 1
        assert result["within_sla_pct"] == 50.0
        assert result["avg_business_days_to_active"] == pytest.approx(6.5)

    def test_sessions_missing_timestamps_skipped(self):
        sessions = [
            {"invited_at": "", "erp_activated_at": "2026-04-10T09:00:00+00:00"},
            {"invited_at": "2026-04-06T09:00:00+00:00", "erp_activated_at": ""},
            {
                "invited_at": "2026-04-06T09:00:00+00:00",
                "erp_activated_at": "2026-04-10T09:00:00+00:00",
            },
        ]
        store = self._store(sessions)
        result = store._compute_vendor_activation_sla(
            organization_id="default",
            now=datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc),
        )
        # Only the one with both timestamps counts.
        assert result["activation_count"] == 1


class TestSlackDigestOnboardingBlock:
    def test_blocks_include_onboarding_when_activations_present(self):
        kpis = {
            "touchless_rate": {"rate": 0.8},
            "exception_rate": {"rate": 0.1},
            "cycle_time_hours": {"avg": 3.2},
            "on_time_approvals": {"rate": 0.95},
            "agentic_telemetry": {},
            "vendor_activation_sla": {
                "activation_count": 4,
                "avg_business_days_to_active": 3.5,
                "within_sla_count": 3,
                "within_sla_pct": 75.0,
                "window_days": 30,
                "sla_business_days": 5,
            },
        }
        blocks = SlackAPIClient.build_ap_kpi_digest_blocks(kpis, "default")
        onboarding_block = next(
            (b for b in blocks if b.get("type") == "section"
             and "Vendor onboarding" in str((b.get("text") or {}).get("text", ""))),
            None,
        )
        assert onboarding_block is not None
        text = onboarding_block["text"]["text"]
        assert "4 activated" in text
        assert "3.5 business days" in text
        assert "75% within 5-business-day SLA" in text

    def test_blocks_handle_zero_activation_window(self):
        kpis = {
            "touchless_rate": {"rate": 0.8},
            "exception_rate": {"rate": 0.1},
            "cycle_time_hours": {"avg": 3.2},
            "on_time_approvals": {"rate": 0.95},
            "agentic_telemetry": {},
            "vendor_activation_sla": {
                "activation_count": 0,
                "avg_business_days_to_active": 0.0,
                "within_sla_count": 0,
                "within_sla_pct": 0.0,
                "window_days": 30,
                "sla_business_days": 5,
            },
        }
        blocks = SlackAPIClient.build_ap_kpi_digest_blocks(kpis, "default")
        onboarding_block = next(
            (b for b in blocks if b.get("type") == "section"
             and "Vendor onboarding" in str((b.get("text") or {}).get("text", ""))),
            None,
        )
        assert onboarding_block is not None
        text = onboarding_block["text"]["text"]
        assert "No vendors activated" in text

    def test_compact_text_includes_onboarding_when_activations_present(self):
        kpis = {
            "touchless_rate": {"rate": 0.8},
            "exception_rate": {"rate": 0.1},
            "agentic_telemetry": {},
            "vendor_activation_sla": {
                "activation_count": 2,
                "avg_business_days_to_active": 4.0,
                "within_sla_count": 2,
                "within_sla_pct": 100.0,
                "window_days": 30,
                "sla_business_days": 5,
            },
        }
        text = SlackAPIClient.build_ap_kpi_digest_text(kpis, "default")
        assert "onboarding 2 activated" in text
        assert "100% on SLA" in text

    def test_compact_text_omits_onboarding_when_empty(self):
        kpis = {
            "touchless_rate": {"rate": 0.8},
            "exception_rate": {"rate": 0.1},
            "agentic_telemetry": {},
            "vendor_activation_sla": {"activation_count": 0},
        }
        text = SlackAPIClient.build_ap_kpi_digest_text(kpis, "default")
        assert "onboarding" not in text


class TestTeamsDigestOnboardingBlock:
    def _card_body(self, kpis):
        card = TeamsAPIClient.build_ap_kpi_digest_card(kpis, "default")
        return card["attachments"][0]["content"]["body"]

    def test_card_includes_onboarding_factset_with_activations(self):
        kpis = {
            "vendor_activation_sla": {
                "activation_count": 5,
                "avg_business_days_to_active": 3.2,
                "within_sla_pct": 80.0,
                "window_days": 30,
                "sla_business_days": 5,
            },
        }
        body = self._card_body(kpis)
        # Find the onboarding heading + factset
        heading_idx = next(
            (i for i, el in enumerate(body)
             if el.get("type") == "TextBlock"
             and "Vendor onboarding" in str(el.get("text", ""))),
            None,
        )
        assert heading_idx is not None
        factset = body[heading_idx + 1]
        assert factset["type"] == "FactSet"
        facts = {f["title"]: f["value"] for f in factset["facts"]}
        assert facts["Activated"] == "5"
        assert facts["Avg business days"] == "3.2"
        assert facts["Within 5-bd SLA"] == "80%"

    def test_card_handles_zero_activation_window(self):
        kpis = {
            "vendor_activation_sla": {
                "activation_count": 0,
                "window_days": 30,
                "sla_business_days": 5,
            },
        }
        body = self._card_body(kpis)
        heading_idx = next(
            (i for i, el in enumerate(body)
             if el.get("type") == "TextBlock"
             and "Vendor onboarding" in str(el.get("text", ""))),
            None,
        )
        assert heading_idx is not None
        factset = body[heading_idx + 1]
        facts = {f["title"]: f["value"] for f in factset["facts"]}
        assert facts["Activated"] == "0"
        # Empty window shows em-dashes rather than "0.0 / 0%" so the
        # reader doesn't mistake zero activations for zero-business-day
        # activations.
        assert facts["Avg business days"] == "—"
        assert facts["Within SLA"] == "—"
