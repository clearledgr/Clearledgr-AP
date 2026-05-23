"""Regression fence: model calls must not bypass the bounded LLM gateway.

llm_multimodal once had a Mistral fallback that issued a raw requests.post to
api.mistral.ai, escaping ACTION_REGISTRY caps / budget / truncation. This test
scans the module source so a future raw-HTTP model call to a provider endpoint
trips here instead of silently bypassing the gateway.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_llm_multimodal_has_no_raw_provider_http_call():
    src = (ROOT / "solden" / "services" / "llm_multimodal.py").read_text()
    # No direct HTTP client calls (gateway is the only sanctioned path).
    assert "requests.post" not in src, "raw requests.post bypasses the LLM gateway"
    assert "httpx" not in src, "raw httpx bypasses the LLM gateway"
    # No provider API endpoints hardcoded outside the gateway.
    assert not re.search(r"api\.(mistral|openai)\.", src), "raw provider endpoint"
    assert "_call_mistral" not in src, "the gateway-bypassing Mistral path is gone"
