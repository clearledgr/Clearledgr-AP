"""Spec-driven LLM extraction for declarative Box types (Phase E).

Proves a tenant-declared Box type gets the agent's reading capability — the
model extracts the spec's declared llm_fields from raw text — without any
bespoke Python, and that the prompt is spec-driven (not invoice-shaped).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from solden.core import workflow_spec  # noqa: E402
from solden.core.workflow_spec import WorkflowSpec  # noqa: E402
from solden.services import box_extraction  # noqa: E402

ORG = "orgExtract"

CONTRACT_SPEC = WorkflowSpec(
    box_type="vendor_contract",
    url_slug="vendor-contracts",
    states=("draft", "in_review", "approved"),
    initial_state="draft",
    terminal_states=("approved",),
    transitions={"draft": {"in_review"}, "in_review": {"approved"}},
    action_states={"submit": "in_review", "approve": "approved"},
    fields=("counterparty", "value", "renewal_date"),
    llm_fields=(
        {"name": "counterparty", "type": "string", "description": "the other party"},
        {"name": "value", "type": "number", "description": "annual contract value"},
        {"name": "renewal_date", "type": "date", "description": "renewal date ISO"},
    ),
    domain_hint="You process software vendor contracts.",
)


@pytest.fixture(autouse=True)
def _register():
    workflow_spec.register_spec(CONTRACT_SPEC)
    try:
        yield
    finally:
        workflow_spec.unregister_spec("vendor_contract")


class _Resp:
    def __init__(self, content):
        self.content = content


def test_extract_box_fields_returns_declared_fields(monkeypatch):
    import solden.core.llm_gateway as gw
    captured = {}

    def fake_call_sync(action, messages, **kwargs):
        captured["action"] = action
        captured["org"] = kwargs.get("organization_id")
        captured["prompt"] = messages[0]["content"]
        # extra key 'sneaky' must be filtered out
        return _Resp(
            '{"counterparty":"Globex","value":50000,'
            '"renewal_date":"2027-01-01","sneaky":"x"}'
        )

    monkeypatch.setattr(gw.get_llm_gateway(), "call_sync", fake_call_sync, raising=True)

    out = box_extraction.extract_box_fields(
        "vendor_contract", ORG,
        text="MSA with Globex, $50,000/yr, renews 2027-01-01",
    )
    assert out == {"counterparty": "Globex", "value": 50000, "renewal_date": "2027-01-01"}
    assert captured["action"] == gw.LLMAction.EXTRACT_BOX_FIELDS
    assert captured["org"] == ORG
    # prompt is spec-driven (declared fields), NOT invoice-shaped
    assert "counterparty" in captured["prompt"]
    assert "renewal_date" in captured["prompt"]
    assert "invoice_number" not in captured["prompt"]


def test_extract_returns_empty_without_llm_fields(monkeypatch):
    import solden.core.llm_gateway as gw
    spec = WorkflowSpec(
        box_type="plain_task", url_slug="plain-tasks",
        states=("open", "done"), initial_state="open", terminal_states=("done",),
        transitions={"open": {"done"}}, action_states={"finish": "done"},
    )
    workflow_spec.register_spec(spec)
    try:
        def boom(*a, **k):
            raise AssertionError("gateway must not be called when no llm_fields")
        monkeypatch.setattr(gw.get_llm_gateway(), "call_sync", boom, raising=True)
        assert box_extraction.extract_box_fields("plain_task", ORG, text="anything") == {}
    finally:
        workflow_spec.unregister_spec("plain_task")


def test_extract_empty_source_skips_model(monkeypatch):
    import solden.core.llm_gateway as gw

    def boom(*a, **k):
        raise AssertionError("gateway must not be called for empty source")
    monkeypatch.setattr(gw.get_llm_gateway(), "call_sync", boom, raising=True)
    assert box_extraction.extract_box_fields("vendor_contract", ORG, text="   ") == {}


def test_extract_missing_org_fails_loud():
    from solden.core.org_utils import OrgIdMissing
    with pytest.raises(OrgIdMissing):
        box_extraction.extract_box_fields("vendor_contract", "", text="x")


def test_invalid_llm_field_rejected_at_validation():
    bad = WorkflowSpec(
        box_type="bad_llm", url_slug="bad-llm",
        states=("a", "b"), initial_state="a", terminal_states=("b",),
        transitions={"a": {"b"}}, action_states={"go": "b"},
        llm_fields=({"name": "Bad Name", "type": "string"},),  # not snake_case
    )
    assert any("snake_case" in e for e in workflow_spec.validate_spec(bad))
    bad_type = WorkflowSpec(
        box_type="bad_llm2", url_slug="bad-llm2",
        states=("a", "b"), initial_state="a", terminal_states=("b",),
        transitions={"a": {"b"}}, action_states={"go": "b"},
        llm_fields=({"name": "ok", "type": "wormhole"},),  # bad type
    )
    assert any("must be one of" in e for e in workflow_spec.validate_spec(bad_type))
