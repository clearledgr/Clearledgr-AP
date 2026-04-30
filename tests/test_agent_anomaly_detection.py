"""Tests for the anomaly detection layer.

Two layers, tested separately:

  - Rules: ``detect_volume_anomalies`` flags z-score outliers
    deterministically. The output is the gate-input the cascade reads.
  - LLM augmentation: ``explain_volume_anomaly`` rewrites the generic
    rule suggestion into a context-aware operator explanation. It's
    advisory copy only — never gates a decision and is wrapped to
    return the rule output untouched on any failure (no API key,
    gateway timeout, JSON parse error).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from clearledgr.services.agent_anomaly_detection import (
    detect_volume_anomalies,
    explain_volume_anomaly,
)


class TestRuleDetection:
    def test_spike_flagged(self):
        result = detect_volume_anomalies(
            current_volume=10000.0,
            historical_volumes=[1000.0, 1100.0, 950.0, 1050.0, 1000.0],
        )
        assert result["is_anomaly"] is True
        assert result["anomaly_type"] == "spike"
        assert result["z_score"] > 2.0

    def test_drop_flagged(self):
        result = detect_volume_anomalies(
            current_volume=10.0,
            historical_volumes=[1000.0, 1100.0, 950.0, 1050.0, 1000.0],
        )
        assert result["is_anomaly"] is True
        assert result["anomaly_type"] == "drop"
        assert result["z_score"] < -2.0

    def test_normal_value_not_flagged(self):
        result = detect_volume_anomalies(
            current_volume=1020.0,
            historical_volumes=[1000.0, 1100.0, 950.0, 1050.0, 1000.0],
        )
        assert result["is_anomaly"] is False

    def test_insufficient_history_returns_no_anomaly(self):
        result = detect_volume_anomalies(
            current_volume=1000.0,
            historical_volumes=[1000.0, 1100.0],  # < 3 rows
        )
        assert result["is_anomaly"] is False
        assert result["reason"] == "insufficient_history"


class TestExplainAnomaly:
    def test_returns_input_unchanged_when_not_anomaly(self):
        rule_result = {"is_anomaly": False, "reason": "no_variance"}
        out = asyncio.run(
            explain_volume_anomaly(
                rule_result,
                vendor_name="Acme",
                invoice_amount=1000.0,
                recent_amounts=[1000.0, 1100.0],
            )
        )
        assert out == rule_result
        assert "llm_explanation" not in out

    def test_returns_input_unchanged_when_gateway_raises(self, monkeypatch):
        rule_result = {
            "is_anomaly": True,
            "anomaly_type": "spike",
            "z_score": 3.5,
            "current_volume": 10000.0,
            "average_volume": 1000.0,
            "confidence": 1.0,
            "suggestion": "Volume spike detected (z-score: 3.50). Verify data completeness.",
        }

        # Gateway raises — augmenter must preserve the rule output.
        fake_gateway = SimpleNamespace(call=AsyncMock(side_effect=RuntimeError("no api key")))
        monkeypatch.setattr(
            "clearledgr.core.llm_gateway.get_llm_gateway", lambda: fake_gateway,
        )

        out = asyncio.run(
            explain_volume_anomaly(
                rule_result,
                vendor_name="Acme",
                invoice_amount=10000.0,
                recent_amounts=[1000.0, 1100.0, 950.0, 1050.0, 1000.0],
            )
        )
        assert out == rule_result

    def test_enriches_with_llm_explanation_on_success(self, monkeypatch):
        rule_result = {
            "is_anomaly": True,
            "anomaly_type": "spike",
            "z_score": 4.2,
            "current_volume": 12000.0,
            "average_volume": 1000.0,
            "confidence": 1.0,
            "suggestion": "Volume spike detected (z-score: 4.20). Verify data completeness.",
        }
        fake_resp = SimpleNamespace(content=(
            '{'
            '"explanation": "Cisco invoices typically run 950-1100; the 12,000 spike '
            'suggests an annual licence renewal landed alongside monthly support.",'
            '"likely_causes": ["annual licence renewal", "additional seats added", '
            '"merged purchase orders"],'
            '"next_step": "Compare line items to the prior monthly invoice"'
            '}'
        ))
        fake_gateway = SimpleNamespace(call=AsyncMock(return_value=fake_resp))
        monkeypatch.setattr(
            "clearledgr.core.llm_gateway.get_llm_gateway", lambda: fake_gateway,
        )

        out = asyncio.run(
            explain_volume_anomaly(
                rule_result,
                vendor_name="Cisco",
                invoice_amount=12000.0,
                recent_amounts=[1000.0, 1100.0, 950.0, 1050.0, 1000.0],
                currency="USD",
            )
        )
        assert out["is_anomaly"] is True  # rule decision untouched
        assert "annual licence renewal" in out["llm_explanation"]
        assert out["likely_causes"] == [
            "annual licence renewal",
            "additional seats added",
            "merged purchase orders",
        ]
        assert "Compare line items" in out["next_step"]
        # The operator-facing suggestion is replaced with the contextual one;
        # the original rule suggestion is preserved under rule_suggestion for audit.
        assert out["suggestion"] == out["llm_explanation"]
        assert out["rule_suggestion"].startswith("Volume spike detected")

    def test_strips_code_fence_wrapper(self, monkeypatch):
        # Haiku occasionally returns ```json ... ``` despite the
        # JSON-only instruction. The wrapper must not break parsing.
        rule_result = {
            "is_anomaly": True,
            "anomaly_type": "drop",
            "z_score": -2.8,
            "current_volume": 100.0,
            "average_volume": 1000.0,
            "confidence": 1.0,
            "suggestion": "Volume drop detected. Verify data sources.",
        }
        fake_resp = SimpleNamespace(content=(
            '```json\n'
            '{"explanation": "10x drop suggests partial-month billing or credit applied.",'
            ' "likely_causes": ["credit memo applied", "partial period"],'
            ' "next_step": "Check for an associated credit note"}'
            '\n```'
        ))
        fake_gateway = SimpleNamespace(call=AsyncMock(return_value=fake_resp))
        monkeypatch.setattr(
            "clearledgr.core.llm_gateway.get_llm_gateway", lambda: fake_gateway,
        )

        out = asyncio.run(
            explain_volume_anomaly(
                rule_result,
                vendor_name="Acme",
                invoice_amount=100.0,
                recent_amounts=[1000.0, 1100.0, 950.0],
            )
        )
        assert "10x drop" in out["llm_explanation"]
        assert "credit memo applied" in out["likely_causes"]

    def test_garbage_json_response_preserves_rule_output(self, monkeypatch):
        rule_result = {
            "is_anomaly": True,
            "anomaly_type": "spike",
            "z_score": 3.0,
            "average_volume": 1000.0,
            "current_volume": 5000.0,
            "confidence": 1.0,
            "suggestion": "Volume spike detected.",
        }
        fake_resp = SimpleNamespace(content="not actually json at all")
        fake_gateway = SimpleNamespace(call=AsyncMock(return_value=fake_resp))
        monkeypatch.setattr(
            "clearledgr.core.llm_gateway.get_llm_gateway", lambda: fake_gateway,
        )

        out = asyncio.run(
            explain_volume_anomaly(
                rule_result,
                vendor_name="Acme",
                invoice_amount=5000.0,
                recent_amounts=[1000.0, 1100.0, 950.0],
            )
        )
        assert out == rule_result


class TestActionRegistryEntry:
    def test_explain_anomaly_action_registered(self):
        from clearledgr.core.llm_gateway import ACTION_REGISTRY, LLMAction

        assert LLMAction.EXPLAIN_ANOMALY in ACTION_REGISTRY
        cfg = ACTION_REGISTRY[LLMAction.EXPLAIN_ANOMALY]
        # Cheap tier, short output — augmentation is a footnote, not a
        # decision. Sonnet here would be money on the floor.
        assert cfg.model_tier == "haiku"
        assert cfg.max_output_tokens <= 1000
        assert cfg.timeout_seconds <= 15
