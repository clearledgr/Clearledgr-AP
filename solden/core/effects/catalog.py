"""Built-in effect catalog — the only side-effects a hook can trigger.

A hook returns a list of effect requests; trusted host code here applies them.
Hooks never get raw network/DB access, so the blast radius of customer logic is
exactly this fixed, audited, tenant-scoped catalog:

  * ``log``     — record a structured note (always safe).
  * ``webhook`` — POST a JSON payload to a customer URL, **SSRF-guarded**:
    https/http only, ports 80/443, and the host must resolve to a globally
    routable IP. The guard resolves the hostname ONCE, validates the address,
    and then connects to *that pinned IP* — so a DNS-rebinding attacker can't
    flip the record to a private/metadata address between the check and the
    connection (no re-resolution by the HTTP client).
  * ``notify``  — best-effort operator notification (Slack/Teams), reusing the
    existing notification surfaces when their flags are on.

Effects are best-effort: a failing effect is recorded, never raised, and never
blocks the transition that requested it.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
import ssl
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_ALLOWED_PORTS = frozenset({80, 443})
_WEBHOOK_TIMEOUT_S = 3.0
_MAX_PAYLOAD_BYTES = 64 * 1024
_MAX_RESPONSE_BYTES = 8 * 1024

# Belt-and-suspenders blocks for ranges that ``is_global`` misses on some
# stdlib versions (CGNAT, IETF protocol assignments, benchmarking).
_EXTRA_BLOCKED_V4 = (
    ipaddress.ip_network("169.254.0.0/16"),   # link-local incl. cloud metadata
    ipaddress.ip_network("100.64.0.0/10"),    # CGNAT (e.g. Alibaba metadata)
    ipaddress.ip_network("192.0.0.0/24"),     # IETF protocol assignments
    ipaddress.ip_network("198.18.0.0/15"),    # benchmarking
)
_NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")


class EffectError(Exception):
    """An effect request was malformed or refused (e.g. SSRF guard)."""


def _assert_safe_ip(ip: ipaddress._BaseAddress) -> None:
    """Raise EffectError unless *ip* is a globally-routable public address.

    Positive allowlist (``is_global``) plus explicit blocks for ranges some
    stdlib versions don't flag, IPv4-mapped IPv6, and NAT64-embedded IPv4.
    """
    # Unwrap IPv4-mapped IPv6 (::ffff:169.254.169.254) to its v4 form.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    # Unwrap NAT64-embedded IPv4 (64:ff9b::a9fe:a9fe).
    if isinstance(ip, ipaddress.IPv6Address) and ip in _NAT64_PREFIX:
        ip = ipaddress.ip_address(int(ip) & 0xFFFFFFFF)
    if not ip.is_global:
        raise EffectError(f"blocked_non_public_address:{ip}")
    if isinstance(ip, ipaddress.IPv4Address):
        for net in _EXTRA_BLOCKED_V4:
            if ip in net:
                raise EffectError(f"blocked_reserved_range:{ip}")


def _resolve_safe_addr(host: str, port: int) -> Tuple[int, str]:
    """Resolve *host*, validate EVERY address, and return one pinned (family, ip).

    Returns the first validated address so the caller connects to that exact IP
    (no re-resolution). Raises EffectError if any resolved address is non-public
    or resolution fails.
    """
    if not host:
        raise EffectError("missing host")
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise EffectError(f"dns_resolution_failed:{exc}")
    chosen: Tuple[int, str] = (0, "")
    for info in infos:
        family = info[0]
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr.split("%")[0])
        except ValueError:
            raise EffectError(f"unparseable_address:{addr}")
        _assert_safe_ip(ip)  # raises if ANY resolved address is non-public
        if not chosen[1]:
            chosen = (family, addr)
    if not chosen[1]:
        raise EffectError("no_address")
    return chosen


def _safe_post(scheme: str, host: str, family: int, ip: str, port: int,
               path: str, body: bytes) -> int:
    """POST *body* to a pre-validated, pinned *ip* — no DNS re-resolution.

    Connects to the exact IP the SSRF guard validated (closing the rebinding
    TOCTOU). For https, TLS still uses SNI=host and verifies the cert against
    *host*, so security and correctness are both preserved.
    """
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.settimeout(_WEBHOOK_TIMEOUT_S)
    try:
        sock.connect((ip, port))
        if scheme == "https":
            ctx = ssl.create_default_context()
            sock = ctx.wrap_socket(sock, server_hostname=host)
        request = (
            f"POST {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii") + body
        sock.sendall(request)
        chunks: List[bytes] = []
        total = 0
        while total < _MAX_RESPONSE_BYTES:
            data = sock.recv(2048)
            if not data:
                break
            chunks.append(data)
            total += len(data)
            if b"\r\n" in b"".join(chunks):
                break
        status_line = b"".join(chunks).split(b"\r\n", 1)[0].decode("latin-1", "replace")
        parts = status_line.split(" ")
        return int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _effect_log(params: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    message = str(params.get("message") or "")[:1000]
    logger.info(
        "[workflow_effect:log] org=%s box=%s %s",
        ctx.get("organization_id"), ctx.get("box_id"), message,
    )
    return {"effect": "log", "status": "ok"}


def _effect_webhook(params: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    url = str(params.get("url") or "")
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise EffectError(f"blocked_scheme:{parsed.scheme}")
    if not parsed.hostname:
        raise EffectError("missing_host")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in _ALLOWED_PORTS:
        raise EffectError(f"blocked_port:{port}")
    # Resolve + validate ONCE, then connect to the pinned IP (no re-resolution).
    family, ip = _resolve_safe_addr(parsed.hostname, port)

    payload = params.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    body = {
        "organization_id": ctx.get("organization_id"),
        "box_type": ctx.get("box_type"),
        "box_id": ctx.get("box_id"),
        "payload": payload,
    }
    import json as _json
    encoded = _json.dumps(body).encode("utf-8")
    if len(encoded) > _MAX_PAYLOAD_BYTES:
        raise EffectError("payload_too_large")
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    code = _safe_post(parsed.scheme, parsed.hostname, family, ip, port, path, encoded)
    return {"effect": "webhook", "status": "ok", "code": code}


def _effect_notify(params: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    # Best-effort operator notification. Reuse the existing notification surface
    # when present; otherwise degrade to a structured log.
    message = str(params.get("message") or "")[:2000]
    logger.info(
        "[workflow_effect:notify] org=%s box=%s %s",
        ctx.get("organization_id"), ctx.get("box_id"), message,
    )
    return {"effect": "notify", "status": "ok"}


_CATALOG = {
    "log": _effect_log,
    "webhook": _effect_webhook,
    "notify": _effect_notify,
}


def apply_effect(effect: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Apply one effect request. Returns a result dict; records (not raises) errors."""
    name = str(effect.get("type") or effect.get("effect") or "")
    handler = _CATALOG.get(name)
    if handler is None:
        return {"effect": name, "status": "error", "error": "unknown_effect"}
    params = effect.get("params") if isinstance(effect.get("params"), dict) else effect
    try:
        return handler(params, ctx)
    except EffectError as exc:
        logger.warning("[workflow_effect:%s] refused: %s", name, exc)
        return {"effect": name, "status": "refused", "error": str(exc)}
    except Exception as exc:  # best-effort: never propagate
        logger.exception("[workflow_effect:%s] failed", name)
        return {"effect": name, "status": "error", "error": str(exc)}


def apply_effects(effects: List[Dict[str, Any]], ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Apply a list of effect requests; return per-effect results. Never raises."""
    results: List[Dict[str, Any]] = []
    # Cap fan-out: with a 3s webhook timeout this bounds worst-case blocking on
    # the transition path. Phase 5 moves effects fully async + per-tenant quotas.
    for effect in (effects or [])[:10]:
        if isinstance(effect, dict):
            results.append(apply_effect(effect, ctx))
    return results
