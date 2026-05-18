"""Coverage for /v1/webhooks (plan §Step 9).

Focus: the public-API contract — secret-on-create-only, redaction
on read, https-only validation, event-name allowlist. End-to-end
delivery tests live with the integration suite (need a real Postgres
+ httpx mock).
"""

from __future__ import annotations

from clearledgr.api.v1_webhooks import (
    _ALLOWED_EVENTS,
    _generate_secret,
    _redact_secret,
    _shape_subscription,
    _validate_events,
    _validate_https,
)


# ─── Secret generation ──────────────────────────────────────────


def test_secret_has_brand_prefix() -> None:
    """Brand prefix ``whsec_`` lets receivers identify Solden secrets
    in their config without needing a separate label field."""
    s = _generate_secret()
    assert s.startswith("whsec_")


def test_secret_is_high_entropy() -> None:
    """Two consecutive calls must return different values — uses
    ``secrets.token_urlsafe`` under the hood, but explicit coverage
    here is cheap and catches the worst-case regression where someone
    swaps it for a deterministic generator."""
    assert _generate_secret() != _generate_secret()


def test_secret_is_long_enough() -> None:
    """32 bytes of url-safe entropy → ~43 chars after the prefix."""
    s = _generate_secret()
    assert len(s) >= len("whsec_") + 40


# ─── Secret redaction ───────────────────────────────────────────


def test_redact_shows_only_last_four() -> None:
    """The preview is meant to disambiguate keys in a list — not to
    reveal enough to reconstruct the secret."""
    s = "whsec_aaaaaaaaaaaaaaaaaaaaaaaaaaaa1234"
    redacted = _redact_secret(s)
    assert redacted.endswith("1234")
    assert "aaaa" not in redacted


def test_redact_handles_empty_secret() -> None:
    assert _redact_secret("") == ""


def test_redact_handles_short_secret() -> None:
    """A short value (shouldn't happen in practice, but defensive)
    redacts entirely rather than echoing the whole thing back."""
    assert _redact_secret("whsec_") == "whsec_***"


# ─── HTTPS validation ──────────────────────────────────────────


def test_https_url_passes() -> None:
    assert _validate_https("https://example.com/hook")


def test_http_url_rejected() -> None:
    """Plaintext webhooks leak signed payloads — block at the
    boundary, not at delivery time."""
    assert not _validate_https("http://example.com/hook")


def test_localhost_https_passes() -> None:
    """Local dev sometimes points at https://localhost — fine."""
    assert _validate_https("https://localhost:8443/hook")


def test_case_insensitive() -> None:
    assert _validate_https("HTTPS://example.com/hook")


# ─── Event-name allowlist ──────────────────────────────────────


def test_known_events_pass() -> None:
    assert (
        _validate_events(["invoice.received", "invoice.approved"]) is None
    )


def test_wildcard_passes() -> None:
    assert _validate_events(["*"]) is None


def test_typo_returns_the_bad_name() -> None:
    """A typo'd event registers a hook that never fires — catch at
    the API boundary and tell the caller exactly which name was wrong."""
    bad = _validate_events(["invoice.aproved"])  # missing 'p'
    assert bad == "invoice.aproved"


def test_one_typo_in_a_valid_list_still_rejects() -> None:
    bad = _validate_events(["invoice.received", "nope.fake"])
    assert bad == "nope.fake"


def test_billing_budget_event_is_in_allowlist() -> None:
    """Customer-facing runaway-spend guard — important enough that we
    explicitly check it isn't accidentally dropped from the allowlist."""
    assert "billing.llm_budget_exceeded" in _ALLOWED_EVENTS


def test_webhook_test_event_is_in_allowlist() -> None:
    """The test-fire endpoint emits webhook.test — it must be a
    valid subscribe-able event so the customer's receiver doesn't
    reject the test as 'unknown event'."""
    assert "webhook.test" in _ALLOWED_EVENTS


# ─── Subscription shaping ──────────────────────────────────────


def _row(secret: str = "whsec_supersecret123") -> dict:
    return {
        "id": "wh_abc123",
        "url": "https://example.com/hook",
        "event_types": ["invoice.approved"],
        "description": "Approval relay",
        "is_active": True,
        "secret": secret,
        "created_at": "2026-05-18T00:00:00Z",
        "updated_at": "2026-05-18T00:00:00Z",
    }


def test_shape_hides_secret_by_default() -> None:
    """The default read path never reveals the full secret. Even a
    caller with webhooks:manage scope only sees the redacted preview
    after creation."""
    out = _shape_subscription(_row())
    assert out["secret"] is None
    assert out["secret_preview"].endswith("t123")


def test_shape_reveals_secret_on_create() -> None:
    """``reveal_secret=True`` is the create + rotate path — the
    customer captures the value once, never again."""
    out = _shape_subscription(_row(), reveal_secret=True)
    assert out["secret"] == "whsec_supersecret123"
    assert out["secret_preview"].endswith("t123")


def test_shape_coerces_is_active() -> None:
    """SQLite returns 0/1 for booleans; the public API speaks JSON
    bool — coerce at the boundary."""
    row = _row()
    row["is_active"] = 0
    assert _shape_subscription(row)["is_active"] is False
    row["is_active"] = 1
    assert _shape_subscription(row)["is_active"] is True


def test_shape_handles_missing_optional_fields() -> None:
    """A stub row with only id should not 500 the shaper."""
    out = _shape_subscription({"id": "wh_x"})
    assert out["id"] == "wh_x"
    assert out["event_types"] == []
    assert out["description"] == ""
    assert out["is_active"] is False
    assert out["secret_preview"] == ""
