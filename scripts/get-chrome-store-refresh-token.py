#!/usr/bin/env python3
"""One-shot helper to mint a Chrome Web Store API refresh token.

Run once:

    python3 scripts/get-chrome-store-refresh-token.py \
        --client-id ... \
        --client-secret ...

It spins up a local HTTP server on 127.0.0.1, opens your browser to
Google's OAuth consent screen with the right scope, captures the
authorization code on the redirect, exchanges it for a refresh
token, and prints the four secrets you need to paste into GitHub
(repo Settings → Secrets and variables → Actions).

The OAuth client must be "Desktop app" type (the old OOB flow was
deprecated in 2022, so this uses loopback redirect — the modern
recommended path for desktop OAuth).

Sign in as the Google account that OWNS the Chrome Web Store
listing (typically founders@clearledgr.com), not your personal
account, otherwise the refresh token won't have permission to
publish.
"""
from __future__ import annotations

import argparse
import http.server
import json
import secrets as _secrets
import socket
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from typing import Optional


def _free_port() -> int:
    """Bind to port 0, let the OS pick, return the assigned number."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict:
    body = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        # Surface Google's actual error description so failures are
        # debuggable instead of "HTTP 400" with no detail.
        body_bytes = exc.read()
        try:
            body_json = json.loads(body_bytes)
            print(
                f"\nGoogle rejected the code exchange ({exc.code}):\n"
                f"  error: {body_json.get('error')}\n"
                f"  description: {body_json.get('error_description')}\n",
                file=sys.stderr,
            )
        except Exception:
            print(f"\nGoogle returned {exc.code}: {body_bytes!r}\n", file=sys.stderr)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-id", required=True, help="OAuth Desktop client_id")
    parser.add_argument("--client-secret", required=True, help="OAuth Desktop client_secret")
    parser.add_argument(
        "--extension-id",
        default="ioccdcmiojkcpjcmikikdimnjcpihegd",
        help="Chrome extension ID (default: Clearledgr's)",
    )
    args = parser.parse_args()

    port = _free_port()
    redirect_uri = f"http://127.0.0.1:{port}/"
    expected_state = _secrets.token_urlsafe(24)

    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": args.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/chromewebstore",
        "access_type": "offline",
        "prompt": "consent",
        "state": expected_state,
    })

    captured: dict = {}
    capture_event = threading.Event()

    class _CaptureHandler(http.server.BaseHTTPRequestHandler):
        # Silence the noisy default access logging.
        def log_message(self, *_args, **_kwargs):
            return

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            params = dict(urllib.parse.parse_qsl(parsed.query))
            captured.update(params)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            if "code" in params and params.get("state") == expected_state:
                self.wfile.write(
                    b"<html><body style='font-family:sans-serif;padding:48px'>"
                    b"<h2>Authorization captured.</h2>"
                    b"<p>You can close this tab. Return to your terminal.</p>"
                    b"</body></html>"
                )
            else:
                self.wfile.write(
                    b"<html><body style='font-family:sans-serif;padding:48px'>"
                    b"<h2>Authorization failed.</h2>"
                    b"<p>Check the terminal for the error message.</p>"
                    b"</body></html>"
                )
            capture_event.set()

    server = http.server.HTTPServer(("127.0.0.1", port), _CaptureHandler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    print(f"\nOpening browser to:\n  {auth_url}\n")
    print(f"Listening on {redirect_uri} for the redirect...\n")
    if not webbrowser.open(auth_url):
        print("(could not open the browser automatically — paste the URL above into one)\n")

    if not capture_event.wait(timeout=300):
        print("Timed out after 5 minutes. Re-run the script.", file=sys.stderr)
        server.server_close()
        return 1
    server.server_close()

    if "error" in captured:
        print(f"OAuth error: {captured.get('error')}: {captured.get('error_description', '')}", file=sys.stderr)
        return 1
    if captured.get("state") != expected_state:
        print("State mismatch — refusing to exchange the code.", file=sys.stderr)
        return 1
    code: Optional[str] = captured.get("code")
    if not code:
        print(f"No code in redirect: {captured}", file=sys.stderr)
        return 1

    print("Exchanging the authorization code for tokens...\n")
    tokens = _exchange_code(args.client_id, args.client_secret, code, redirect_uri)
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        # Most common cause: the user already granted this client and
        # Google declined to issue a fresh refresh_token. The auth
        # URL forces ``prompt=consent`` to mitigate, but if it still
        # happens, revoke at https://myaccount.google.com/permissions
        # and re-run.
        print(
            "No refresh_token returned. Revoke at "
            "https://myaccount.google.com/permissions and re-run.",
            file=sys.stderr,
        )
        print(f"Token response: {tokens}", file=sys.stderr)
        return 1

    print("=" * 72)
    print("SUCCESS. Add these four secrets at:")
    print("  https://github.com/clearledgr/Clearledgr-AP/settings/secrets/actions")
    print("=" * 72)
    print()
    print(f"  CHROME_EXTENSION_ID    = {args.extension_id}")
    print(f"  CHROME_CLIENT_ID       = {args.client_id}")
    print(f"  CHROME_CLIENT_SECRET   = {args.client_secret}")
    print(f"  CHROME_REFRESH_TOKEN   = {refresh_token}")
    print()
    print("Once added, the next push that bumps `version` in")
    print("ui/gmail-extension/manifest.json will auto-publish to the")
    print("Chrome Web Store via .github/workflows/publish-chrome-extension.yml.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
