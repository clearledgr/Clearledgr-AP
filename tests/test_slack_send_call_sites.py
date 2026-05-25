"""Two silently-broken Slack send call-sites, fixed.

- trust_arc._send_slack_message passed org_id as the `blocks` positional to
  _post_slack_blocks (signature (blocks, text, ..., organization_id)) -> TypeError,
  swallowed. Milestones never sent.
- slack_digest.send_digest read runtime['channel']/['token'] but
  resolve_slack_runtime returns approval_channel/bot_token -> always early-returned.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from solden.services import slack_digest, trust_arc  # noqa: E402


def test_trust_arc_passes_blocks_and_org_correctly(monkeypatch):
    captured = {}

    async def fake_post(blocks, text, preferred_channel=None, organization_id=None):
        captured.update(blocks=blocks, text=text, organization_id=organization_id)
        return {"ok": True}

    monkeypatch.setattr(
        "solden.services.slack_notifications._post_slack_blocks", fake_post
    )
    ok = asyncio.run(trust_arc._send_slack_message("orgA", "hello", blocks=[{"b": 1}]))
    assert ok is True
    assert captured["blocks"] == [{"b": 1}]   # not org_id
    assert captured["text"] == "hello"
    assert captured["organization_id"] == "orgA"


def test_slack_digest_uses_per_org_token_and_channel(monkeypatch):
    sent = {}

    async def fake_build(org_id):
        return {"org_id": org_id, "blocks": [{"type": "section"}], "summary": {}}

    def fake_runtime(org_id):
        return {"connected": True, "bot_token": f"tok-{org_id}", "approval_channel": f"#ap-{org_id}"}

    class _Resp:
        def json(self):
            return {"ok": True}

    class _Client:
        async def post(self, url, json=None, headers=None, timeout=None):
            sent["channel"] = json["channel"]
            sent["auth"] = headers["Authorization"]
            return _Resp()

    monkeypatch.setattr(slack_digest, "build_digest", fake_build)
    monkeypatch.setattr("solden.services.slack_api.resolve_slack_runtime", fake_runtime)
    monkeypatch.setattr(slack_digest, "get_http_client", lambda: _Client())

    ok = asyncio.run(slack_digest.send_digest("orgA"))
    assert ok is True
    assert sent["channel"] == "#ap-orgA"
    assert sent["auth"] == "Bearer tok-orgA"
