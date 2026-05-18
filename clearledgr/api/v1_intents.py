"""Public /v1/intents/* router — customer-side agent intent dispatch.

This is the same intent runtime the JWT-authenticated workspace uses
([clearledgr/api/agent_intents.py](agent_intents.py)), re-exposed under
the public ``/v1`` namespace with API-key authentication.

Endpoints:

* ``POST /v1/intents/execute`` — dispatch a typed intent. Commits
  state, writes a sha256-chained audit row with
  ``actor_type="agent"`` and ``agent_version`` from the API key.
  Requires scope ``write:ap_items`` for v1 (the AP-shaped intent
  vocabulary). Returns the runtime's response payload.
* ``POST /v1/intents/preview`` — same input, no side effects. Useful
  for "what would happen if I called execute?" — agents do dry-run
  before commit. Requires scope ``read:ap_items``.
* ``GET /v1/intents`` — list every intent the caller is authorised
  to execute, plus their JSON schemas. Discovery surface for agents.
  Requires authentication only (no scope).

Error envelopes are typed: ``{error_code, message, request_id?}``.
Authentication failures route through ``AuthorizationDenied`` and the
global handler in main.py, which writes an ``authorization_denied``
row before responding.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from clearledgr.api.v1_auth import AgentIdentity, require_agent_key
from clearledgr.services.agent_command_dispatch import build_channel_runtime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/intents", tags=["v1-intents"])


# ─── Request / response models ─────────────────────────────────────


class V1IntentRequest(BaseModel):
    """Shape an agent posts to /v1/intents/{execute,preview}.

    Note: ``organization_id`` is intentionally absent — it's derived
    from the API key. Cross-tenant requests are caught at auth time
    via ``require_agent_key``; pinning org server-side prevents
    the agent from accidentally addressing the wrong tenant.
    """

    intent: str = Field(
        ...,
        min_length=1,
        description="Canonical intent name, e.g. 'approve_invoice'",
    )
    input: Dict[str, Any] = Field(
        default_factory=dict,
        description="Intent-specific input payload (e.g. {'ap_item_id': '...'})",
    )
    idempotency_key: Optional[str] = Field(
        default=None,
        description=(
            "Caller-supplied idempotency key. If the same key is "
            "received again, the original response is returned and "
            "no side effect re-runs."
        ),
    )


# ─── Error envelope ────────────────────────────────────────────────


def _error_response(
    *,
    status_code: int,
    error_code: str,
    message: str,
    request: Optional[Request] = None,
) -> JSONResponse:
    body: Dict[str, Any] = {
        "error_code": error_code,
        "message": message,
    }
    rid = None
    if request is not None:
        rid = getattr(request.state, "correlation_id", None)
    if rid:
        body["request_id"] = rid
    return JSONResponse(status_code=status_code, content=body)


# ─── Runtime construction ─────────────────────────────────────────


def _runtime_for_agent(agent: AgentIdentity) -> Any:
    """Build a FinanceAgentRuntime for a /v1 caller.

    Goes through the canonical ``build_channel_runtime`` helper
    (also used by Slack, Teams, NetSuite, SAP), with
    ``actor_type="agent"`` and the key's ``agent_version`` set so
    every audit row this runtime writes carries the agent attribution.
    """
    actor_label = agent.actor_label
    return build_channel_runtime(
        organization_id=agent.organization_id,
        actor_id=actor_label,
        actor_email=f"agent:{actor_label}",
        fallback_actor="agent",
        actor_type="agent",
        agent_version=agent.agent_version,
    )


# ─── Endpoints ─────────────────────────────────────────────────────


@router.post("/preview")
async def preview_intent(
    payload: V1IntentRequest,
    request: Request,
    agent: AgentIdentity = Depends(require_agent_key("read:ap_items")),
):
    """Dry-run an intent. Returns what would happen without side
    effects. Agents call this before /execute when they want to
    confirm a plan with a human approver first."""
    runtime = _runtime_for_agent_simple(agent)
    try:
        result = runtime.preview_intent(payload.intent, payload.input)
        return {"ok": True, "preview": result}
    except Exception as exc:
        code, msg = _translate_runtime_error(exc)
        return _error_response(
            status_code=code, error_code=msg[0], message=msg[1], request=request
        )


@router.post("/execute")
async def execute_intent(
    payload: V1IntentRequest,
    request: Request,
    agent: AgentIdentity = Depends(require_agent_key("write:ap_items")),
):
    """Commit an intent. Writes an ``audit_events`` row with
    ``actor_type='agent'``, ``actor_id=<agent_id>``, and
    ``agent_version=<key.agent_version>``."""
    runtime = _runtime_for_agent_simple(agent)
    try:
        result = await runtime.execute_intent(
            payload.intent,
            payload.input,
            idempotency_key=payload.idempotency_key,
        )
        return {"ok": True, "result": result}
    except Exception as exc:
        code, msg = _translate_runtime_error(exc)
        return _error_response(
            status_code=code, error_code=msg[0], message=msg[1], request=request
        )


@router.get("")
async def list_intents(
    request: Request,
    agent: AgentIdentity = Depends(require_agent_key(None)),
):
    """Enumerate intents the caller is authorised to execute.

    For v1, this returns the same list the JWT-authenticated workspace
    sees (the runtime's ``supported_intents``). Future scope-aware
    filtering can narrow this per the agent's scopes.
    """
    runtime = _runtime_for_agent_simple(agent)
    return {
        "agent_id": agent.actor_label,
        "agent_version": agent.agent_version,
        "organization_id": agent.organization_id,
        "intents": sorted(list(runtime.supported_intents)),
    }


# ─── Runtime-error translation ────────────────────────────────────


def _translate_runtime_error(exc: Exception) -> tuple[int, tuple[str, str]]:
    """Map FinanceAgentRuntime exceptions to (http_status, (error_code, message)).

    The runtime raises a small set of typed exceptions for known
    failure modes (intent not found, state conflict, validation,
    etc.). Unknown exceptions become 500 with ``internal_error`` so
    no internal detail leaks through the public surface.
    """
    name = type(exc).__name__
    detail = str(exc) or name

    if name in ("LookupError", "NotFoundError"):
        return 404, ("not_found", detail)
    if name in ("PermissionError",):
        return 403, ("forbidden", detail)
    if name in ("ValueError", "ValidationError"):
        return 400, ("invalid_request", detail)
    if name in ("StateError", "IllegalTransitionError"):
        return 409, ("state_conflict", detail)
    # Anything else is opaque to the caller — log full detail server-side
    logger.exception("v1.intents runtime error: %s", exc)
    return 500, ("internal_error", "internal_error")
