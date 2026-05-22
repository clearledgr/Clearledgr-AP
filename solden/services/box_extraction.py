"""Spec-driven field extraction for declarative Box types.

The AP path has a bespoke, invoice-shaped extraction pipeline. Declarative Box
types instead declare *what to read* on their ``WorkflowSpec.llm_fields`` (each a
``{name, type, description}`` descriptor). This builds a generic extraction
prompt from those descriptors and runs it through the same LLM gateway — the one
place the model is invoked — so a tenant-declared workflow gets the agent's
reading capability without any bespoke Python.

The model is used here only in its legitimate "read unstructured input" role: it
returns the declared fields as JSON; it makes no routing/governance decision.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Cap source text so a runaway paste/OCR dump can't blow the input budget.
_MAX_SOURCE_CHARS = 20_000


def extract_box_fields(
    box_type: str,
    organization_id: str,
    *,
    text: str,
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Extract a declarative Box type's declared ``llm_fields`` from *text*.

    Returns a dict of {declared_field_name: value} (only keys the spec declares),
    or ``{}`` if the type declares no ``llm_fields``, there's no source text, or
    the model call fails. Org-scoped: the spec is resolved per organization.
    """
    from solden.core.org_utils import assert_org_id
    org = assert_org_id(organization_id, context="extract_box_fields")

    from solden.core.workflow_spec import resolve_spec
    try:
        spec = resolve_spec(box_type, org)
    except Exception:
        spec = None
    if spec is None:
        return {}

    llm_fields = [f for f in (getattr(spec, "llm_fields", ()) or []) if f.get("name")]
    if not llm_fields:
        return {}

    src = str(text or "").strip()
    if not src:
        return {}
    src = src[:_MAX_SOURCE_CHARS]

    prompt = _build_prompt(spec, llm_fields, src)
    role = _build_role(spec)

    from solden.core.llm_gateway import (
        LLMAction,
        build_system_prompt,
        get_llm_gateway,
    )
    gateway = get_llm_gateway()
    try:
        resp = gateway.call_sync(
            LLMAction.EXTRACT_BOX_FIELDS,
            messages=[{"role": "user", "content": prompt}],
            system_prompt=build_system_prompt(role=role),
            organization_id=org,
        )
    except Exception as exc:
        logger.warning(
            "extract_box_fields gateway call failed for box_type=%s org=%s: %s",
            box_type, org, exc,
        )
        return {}

    return _parse(getattr(resp, "content", ""), llm_fields)


def _build_role(spec: Any) -> str:
    """System-prompt role, parametrized by the spec's domain hint (not AP)."""
    base = "You are a precise data extraction assistant."
    tail = (
        " Your outputs are used in automated back-office workflows where "
        "accuracy is critical."
    )
    domain = str(getattr(spec, "domain_hint", "") or "").strip()
    return f"{base} {domain}{tail}" if domain else f"{base}{tail}"


def _build_prompt(spec: Any, llm_fields: List[Dict[str, Any]], src: str) -> str:
    lines: List[str] = []
    for f in llm_fields:
        name = str(f.get("name") or "").strip()
        if not name:
            continue
        ftype = str(f.get("type") or "string").strip()
        desc = str(f.get("description") or "").strip()
        suffix = f" — {desc}" if desc else ""
        lines.append(f'  "{name}": <{ftype} or null>{suffix}')
    schema = "{\n" + ",\n".join(lines) + "\n}"
    return (
        f"Extract the following fields for a '{spec.box_type}' record from the "
        f"source text. Return ONLY a JSON object with exactly these keys, using "
        f"null for any field not present in the source (do not guess):\n"
        f"{schema}\n\n"
        f'Source:\n"""\n{src}\n"""'
    )


def _parse(content: Any, llm_fields: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Parse the model's JSON, keeping only declared field names."""
    names = {str(f.get("name")).strip() for f in llm_fields if f.get("name")}
    raw = str(content or "").strip()
    if not raw:
        return {}
    # Strip a ```json … ``` fence if present.
    if raw.startswith("```"):
        raw = raw.strip("`")
        newline = raw.find("\n")
        if newline != -1 and raw[:newline].strip().lower() in ("json", ""):
            raw = raw[newline + 1:]
    obj: Any
    try:
        obj = json.loads(raw)
    except Exception:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return {}
        try:
            obj = json.loads(match.group(0))
        except Exception:
            return {}
    if not isinstance(obj, dict):
        return {}
    return {k: v for k, v in obj.items() if k in names}
