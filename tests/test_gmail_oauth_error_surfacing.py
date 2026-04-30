"""Verify that ``exchange_code_for_tokens`` surfaces Google's actual
error reason (``invalid_grant``, ``redirect_uri_mismatch``,
``invalid_client``, etc.) instead of swallowing it inside
``raise_for_status()``. Without this contract, the diagnostic chain
during a sign-in failure is:

  Extension shows: "code_exchange_failed: 400 Bad Request"
  Backend logs:    "400 Bad Request for url 'https://oauth2.googleapis.com/token'"
  Real cause:      ?

— which is exactly the loop we hit when an operator first tried to
sign in to the prod extension. The fix attaches Google's response
body (parsed when JSON, raw text otherwise) to the raised error and
the warning log, so the extension's red error banner from Phase 3.4
displays an actionable reason.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest


class _FakeResponse:
    """Stand-in for httpx.Response that lets tests dictate status/body."""

    def __init__(self, status_code: int, body: Any, *, text: str = "", headers: dict | None = None) -> None:
        self.status_code = status_code
        self._body = body
        self.text = text or (body if isinstance(body, str) else "")
        # ``exchange_code_for_tokens`` reads ``response.headers`` for its
        # diagnostic log. Default to an empty dict so the test fixture
        # stays usable without per-test setup.
        self.headers = headers if headers is not None else {}

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _FakeHttpClient:
    """Drop-in for `get_http_client()` that records the post payload
    and returns a canned response. Tests assert on both the request
    Google would have received AND the error Clearledgr surfaces.
    """

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.posts = []

    async def post(self, url, *, data=None, **kwargs):
        self.posts.append({"url": url, "data": data})
        return self._response

    async def get(self, *args, **kwargs):  # not exercised in the error path
        raise AssertionError("token-exchange error path should not call GET")


def _patch_oauth_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "fake-client.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "GOCSPX-fake")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://api.test/gmail/callback")
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-token-encryption")


@pytest.mark.asyncio
async def test_exchange_code_surfaces_google_error_description(monkeypatch, caplog):
    """When Google returns 400 with a JSON body
    {"error":"invalid_grant","error_description":"Bad Request"},
    the raised error must include both the code and the description.
    """
    _patch_oauth_env(monkeypatch)
    fake_response = _FakeResponse(
        status_code=400,
        body={"error": "invalid_grant", "error_description": "Bad Request"},
    )
    client = _FakeHttpClient(fake_response)
    # ``clearledgr/services/logging.py`` sets ``propagate=False`` on the
    # ``clearledgr`` logger when imported, which means warnings from
    # child loggers (``clearledgr.services.gmail_api``) never reach the
    # root logger. pytest's ``caplog`` captures via root, so the assertion
    # below would silently fail in any test ordering where that module
    # is already imported. Attach our own list-handler directly to the
    # gmail_api logger to bypass propagation entirely.
    import logging as _logging
    captured_records: list = []

    class _CaptureHandler(_logging.Handler):
        def emit(self, record):
            captured_records.append(record)

    capture = _CaptureHandler(level=_logging.WARNING)
    with patch("clearledgr.services.gmail_api.get_http_client", return_value=client):
        from clearledgr.services import gmail_api
        gm_logger = _logging.getLogger(gmail_api.__name__)
        prior_level = gm_logger.level
        gm_logger.setLevel(_logging.WARNING)
        gm_logger.addHandler(capture)
        try:
            with pytest.raises(RuntimeError) as exc_info:
                await gmail_api.exchange_code_for_tokens(
                    "fake-code",
                    redirect_uri="https://ext.test/redirect",
                )
        finally:
            gm_logger.removeHandler(capture)
            gm_logger.setLevel(prior_level)

    err = str(exc_info.value)
    assert "google_token_exchange_400" in err
    assert "invalid_grant" in err
    assert "Bad Request" in err
    # Worker log must include the redirect URI so post-mortem grep
    # can correlate the failure with the OAuth flow that triggered it.
    formatted_msgs = [r.getMessage() for r in captured_records]
    assert any("redirect_uri='https://ext.test/redirect'" in m for m in formatted_msgs)
    assert any("invalid_grant" in m for m in formatted_msgs)


@pytest.mark.asyncio
async def test_exchange_code_surfaces_redirect_uri_mismatch(monkeypatch):
    """The single most common operator-visible failure is
    redirect_uri_mismatch — the OAuth client doesn't list the
    extension's chrome-extension-derived redirect URI. Pinned
    independently because the description copy is the most
    immediately actionable.
    """
    _patch_oauth_env(monkeypatch)
    fake_response = _FakeResponse(
        status_code=400,
        body={
            "error": "redirect_uri_mismatch",
            "error_description": "The redirect URI in the request did not match a registered redirect URI.",
        },
    )
    client = _FakeHttpClient(fake_response)
    with patch("clearledgr.services.gmail_api.get_http_client", return_value=client):
        from clearledgr.services import gmail_api
        with pytest.raises(RuntimeError) as exc_info:
            await gmail_api.exchange_code_for_tokens(
                "fake-code",
                redirect_uri="https://wrong.test/redirect",
            )
    err = str(exc_info.value)
    assert "redirect_uri_mismatch" in err
    assert "did not match" in err.lower()


@pytest.mark.asyncio
async def test_exchange_code_falls_back_to_response_text_when_body_isnt_json(monkeypatch):
    """Some 4xx/5xx paths from Google return plain text or HTML. We
    still want SOMETHING in the raised error so the worker logs aren't
    a black hole.
    """
    _patch_oauth_env(monkeypatch)
    fake_response = _FakeResponse(
        status_code=502,
        body=ValueError("not json"),
        text="Bad Gateway: upstream gateway timeout",
    )
    client = _FakeHttpClient(fake_response)
    with patch("clearledgr.services.gmail_api.get_http_client", return_value=client):
        from clearledgr.services import gmail_api
        with pytest.raises(RuntimeError) as exc_info:
            await gmail_api.exchange_code_for_tokens("fake-code", redirect_uri="https://ext.test/")
    err = str(exc_info.value)
    assert "google_token_exchange_502" in err
    assert "Bad Gateway" in err


@pytest.mark.asyncio
async def test_exchange_code_sends_redirect_uri_unchanged(monkeypatch):
    """Regression: the redirect URI used for the token exchange MUST
    match exactly what was used in the auth request. Ours is the
    chrome.identity.getRedirectURL() value the extension passed in —
    not the env-var fallback. Pin so a future refactor that "helpfully"
    rewrites redirects can't reintroduce the silent mismatch.
    """
    _patch_oauth_env(monkeypatch)
    fake_response = _FakeResponse(
        status_code=400,
        body={"error": "invalid_grant", "error_description": "expected"},
    )
    client = _FakeHttpClient(fake_response)
    with patch("clearledgr.services.gmail_api.get_http_client", return_value=client):
        from clearledgr.services import gmail_api
        with pytest.raises(RuntimeError):
            await gmail_api.exchange_code_for_tokens(
                "fake-code",
                redirect_uri="https://ioccdcmiojkcpjcmikikdimnjcpihegd.chromiumapp.org/",
            )
    assert len(client.posts) == 1
    assert client.posts[0]["url"] == gmail_api.OAUTH_TOKEN_URL
    payload = client.posts[0]["data"]
    assert payload["redirect_uri"] == "https://ioccdcmiojkcpjcmikikdimnjcpihegd.chromiumapp.org/"
    assert payload["grant_type"] == "authorization_code"
    assert payload["code"] == "fake-code"
