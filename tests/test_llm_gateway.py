"""Tests for LLM Gateway — Agent Design Specification §7."""
from __future__ import annotations

import pytest

from clearledgr.core.llm_gateway import (
    ACTION_REGISTRY,
    ActionConfig,
    LLMAction,
    LLMGateway,
    build_system_prompt,
    DEFAULT_SYSTEM_PROMPT_SECTIONS,
)


class TestActionRegistry:
    """§7.1: Action registry enforces the DET/LLM boundary."""

    def test_all_spec_llm_actions_registered(self):
        """The 5 spec-defined LLM actions must be in the registry."""
        spec_actions = {
            "classify_email",
            "extract_invoice_fields",
            "generate_exception_reason",
            "classify_vendor_response",
            "draft_vendor_response",
        }
        registered = {a.value for a in ACTION_REGISTRY}
        assert spec_actions.issubset(registered)

    def test_spec_token_budgets(self):
        """§7.3: Token budgets must match spec values."""
        spec_budgets = {
            LLMAction.CLASSIFY_EMAIL: 2000,
            LLMAction.EXTRACT_INVOICE_FIELDS: 4000,
            LLMAction.GENERATE_EXCEPTION: 1000,
            LLMAction.CLASSIFY_VENDOR: 2000,
            LLMAction.DRAFT_VENDOR_RESPONSE: 3000,
        }
        for action, expected_budget in spec_budgets.items():
            assert ACTION_REGISTRY[action].max_output_tokens == expected_budget

    def test_every_action_has_config(self):
        """Every LLMAction enum value must have a config entry."""
        for action in LLMAction:
            assert action in ACTION_REGISTRY
            assert isinstance(ACTION_REGISTRY[action], ActionConfig)

    def test_action_tiers_are_valid(self):
        """Model tier must be 'haiku' or 'sonnet'."""
        for action, config in ACTION_REGISTRY.items():
            assert config.model_tier in ("haiku", "sonnet")


class TestSystemPromptTemplate:
    """§7.2: 4-section system prompt template."""

    def test_has_four_sections(self):
        """Template has exactly Role, Output format, Constraints, Guardrail reminder."""
        assert set(DEFAULT_SYSTEM_PROMPT_SECTIONS.keys()) == {
            "role", "output_format", "constraints", "guardrail_reminder",
        }

    def test_role_matches_spec(self):
        """Role section content matches spec verbatim."""
        role = DEFAULT_SYSTEM_PROMPT_SECTIONS["role"]
        assert "precise finance data extraction" in role
        assert "accounts payable" in role
        assert "accuracy is critical" in role

    def test_build_system_prompt_uses_all_sections(self):
        """Default build combines all 4 sections."""
        prompt = build_system_prompt()
        for section_content in DEFAULT_SYSTEM_PROMPT_SECTIONS.values():
            assert section_content in prompt

    def test_build_system_prompt_allows_override(self):
        """Sections can be overridden individually."""
        custom_role = "Custom role text"
        prompt = build_system_prompt(role=custom_role)
        assert custom_role in prompt
        assert DEFAULT_SYSTEM_PROMPT_SECTIONS["role"] not in prompt


class TestBoundaryEnforcement:
    """§7: The boundary is enforced in code."""

    def test_gateway_rejects_unregistered_actions(self):
        """Non-registered actions must be rejected with ValueError."""
        import asyncio
        gateway = LLMGateway(api_key="test-key")
        with pytest.raises(ValueError, match="not registered"):
            asyncio.run(gateway.call(
                "not_a_real_action",  # type: ignore
                messages=[{"role": "user", "content": "test"}],
            ))

    def test_registry_is_frozen(self):
        """ACTION_REGISTRY is the canonical source — don't mutate."""
        # This is a convention — we can't enforce immutability on a dict
        # but we can verify the config objects are frozen dataclasses.
        for config in ACTION_REGISTRY.values():
            with pytest.raises((AttributeError, Exception)):
                config.max_output_tokens = 999  # should fail on frozen dataclass
