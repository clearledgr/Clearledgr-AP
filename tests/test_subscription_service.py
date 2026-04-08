from __future__ import annotations

from clearledgr.services.subscription import UsageStats


def test_usage_stats_from_dict_accepts_legacy_ai_extractions_key():
    usage = UsageStats.from_dict(
        {
            "invoices_this_month": 12,
            "ai_extractions_this_month": 7,
            "unexpected_field": "ignored",
        }
    )

    assert usage.invoices_this_month == 12
    assert usage.ai_credits_this_month == 7
