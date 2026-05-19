"""Tests for the LLM narration layer on ProactiveInsights.

Same separation as agent_anomaly_detection: rules decide WHICH
insights surface (purely deterministic if/then on spending data); the
narrator rewrites the operator-facing copy with business context. The
narrator is wrapped to preserve the rule output verbatim on any
failure, so callers cannot regress when the gateway is unavailable.

Pinned invariants:
  - Narration NEVER adds, removes, or reorders insights.
  - insight_id is preserved verbatim — downstream dedup keys still work.
  - Rule output is returned untouched on gateway raise / parse error /
    empty response / id mismatch.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from solden.services.proactive_insights import Insight, narrate_insights


def _make_insight(insight_id="spike_acme", **kwargs):
    defaults = dict(
        insight_id=insight_id,
        category="spending",
        severity="warning",
        title="Spending spike: Acme",
        description="$12,000.00 is 1100% higher than typical $1,000.00",
        data={"current": 12000.0, "average": 1000.0, "change_pct": 1100.0},
        recommendations=["Verify this increase is expected", "Check for price changes"],
    )
    defaults.update(kwargs)
    return Insight(**defaults)


class TestNarrationFallbacks:
    def test_empty_input_returns_empty(self):
        out = asyncio.run(narrate_insights([]))
        assert out == []

    def test_returns_input_unchanged_when_gateway_raises(self, monkeypatch):
        original = _make_insight()
        fake_gateway = SimpleNamespace(call=AsyncMock(side_effect=RuntimeError("no api key")))
        monkeypatch.setattr(
            "solden.core.llm_gateway.get_llm_gateway", lambda: fake_gateway,
        )
        out = asyncio.run(narrate_insights([original]))
        assert len(out) == 1
        assert out[0].insight_id == "spike_acme"
        assert out[0].title == "Spending spike: Acme"
        assert out[0].description.startswith("$12,000.00")

    def test_returns_input_unchanged_on_garbage_json(self, monkeypatch):
        original = _make_insight()
        fake_resp = SimpleNamespace(content="absolutely not json")
        fake_gateway = SimpleNamespace(call=AsyncMock(return_value=fake_resp))
        monkeypatch.setattr(
            "solden.core.llm_gateway.get_llm_gateway", lambda: fake_gateway,
        )
        out = asyncio.run(narrate_insights([original]))
        assert out[0].title == original.title
        assert out[0].description == original.description

    def test_returns_input_unchanged_on_empty_response(self, monkeypatch):
        original = _make_insight()
        fake_resp = SimpleNamespace(content="")
        fake_gateway = SimpleNamespace(call=AsyncMock(return_value=fake_resp))
        monkeypatch.setattr(
            "solden.core.llm_gateway.get_llm_gateway", lambda: fake_gateway,
        )
        out = asyncio.run(narrate_insights([original]))
        assert out[0].title == original.title


class TestNarrationHappyPath:
    def test_rewrites_title_description_and_recommendations(self, monkeypatch):
        original = _make_insight()
        fake_resp = SimpleNamespace(content=(
            '{'
            '"insights": ['
            '  {'
            '    "id": "spike_acme",'
            '    "title": "Acme — 11x typical: likely annual renewal",'
            '    "description": "Acme usually bills around $1,000/month; the $12,000 invoice is consistent with an annual licence renewal landing alongside monthly support.",'
            '    "recommendations": ["Compare line items to the prior monthly invoice", '
            '       "Check Acme contract for an annual-renewal clause", '
            '       "If renewal: confirm budget allocation"]'
            '  }'
            ']}'
        ))
        fake_gateway = SimpleNamespace(call=AsyncMock(return_value=fake_resp))
        monkeypatch.setattr(
            "solden.core.llm_gateway.get_llm_gateway", lambda: fake_gateway,
        )

        out = asyncio.run(narrate_insights([original]))
        assert len(out) == 1
        assert out[0].insight_id == "spike_acme"  # id preserved
        assert out[0].title == "Acme — 11x typical: likely annual renewal"
        assert "annual licence renewal" in out[0].description
        assert "Compare line items" in out[0].recommendations[0]
        # Severity / category / data carry through untouched
        assert out[0].severity == "warning"
        assert out[0].category == "spending"
        assert out[0].data == {"current": 12000.0, "average": 1000.0, "change_pct": 1100.0}

    def test_strips_code_fence_wrapper(self, monkeypatch):
        original = _make_insight()
        fake_resp = SimpleNamespace(content=(
            '```json\n'
            '{"insights": [{"id": "spike_acme", "title": "T", "description": "D", "recommendations": ["R"]}]}'
            '\n```'
        ))
        fake_gateway = SimpleNamespace(call=AsyncMock(return_value=fake_resp))
        monkeypatch.setattr(
            "solden.core.llm_gateway.get_llm_gateway", lambda: fake_gateway,
        )
        out = asyncio.run(narrate_insights([original]))
        assert out[0].title == "T"
        assert out[0].description == "D"

    def test_preserves_insights_with_missing_rewrites(self, monkeypatch):
        # Three insights in; LLM only rewrites one (drops the other two
        # entirely, as it sometimes does on near-redundant inputs). The
        # other two MUST come back verbatim — we never lose insights.
        i1 = _make_insight(insight_id="i_one", title="T1", description="D1")
        i2 = _make_insight(insight_id="i_two", title="T2", description="D2")
        i3 = _make_insight(insight_id="i_three", title="T3", description="D3")

        fake_resp = SimpleNamespace(content=(
            '{"insights": [{"id": "i_two", "title": "Rewritten T2", "description": "Rewritten D2", "recommendations": ["R"]}]}'
        ))
        fake_gateway = SimpleNamespace(call=AsyncMock(return_value=fake_resp))
        monkeypatch.setattr(
            "solden.core.llm_gateway.get_llm_gateway", lambda: fake_gateway,
        )

        out = asyncio.run(narrate_insights([i1, i2, i3]))
        assert len(out) == 3
        assert [o.insight_id for o in out] == ["i_one", "i_two", "i_three"]
        assert out[0].title == "T1"  # untouched
        assert out[1].title == "Rewritten T2"  # rewritten
        assert out[2].title == "T3"  # untouched

    def test_id_mismatch_falls_back_to_rule_copy(self, monkeypatch):
        # LLM returns an id that wasn't in the input — drop the rewrite,
        # keep the rule copy. Common when the LLM hallucinates ids.
        original = _make_insight(insight_id="real_id")
        fake_resp = SimpleNamespace(content=(
            '{"insights": [{"id": "hallucinated_id", "title": "X", "description": "Y", "recommendations": []}]}'
        ))
        fake_gateway = SimpleNamespace(call=AsyncMock(return_value=fake_resp))
        monkeypatch.setattr(
            "solden.core.llm_gateway.get_llm_gateway", lambda: fake_gateway,
        )

        out = asyncio.run(narrate_insights([original]))
        assert out[0].insight_id == "real_id"
        assert out[0].title == original.title  # rule copy preserved


class TestActionRegistryEntry:
    def test_narrate_insight_action_registered(self):
        from solden.core.llm_gateway import ACTION_REGISTRY, LLMAction

        assert LLMAction.NARRATE_INSIGHT in ACTION_REGISTRY
        cfg = ACTION_REGISTRY[LLMAction.NARRATE_INSIGHT]
        # Cheap tier — narration is operator-facing copy, not a routing
        # input. Sonnet would be money on the floor.
        assert cfg.model_tier == "haiku"
        assert cfg.max_output_tokens <= 1000
        assert cfg.timeout_seconds <= 15
