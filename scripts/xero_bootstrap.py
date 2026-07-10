"""
xero_bootstrap.py - One-time Xero OAuth2 consent, run on a PC (not the NAS).

Xero only allows plain-HTTP redirect URIs on localhost, so the browser consent
step has to happen here. The script captures the initial token set, lets you
pick the organisation (Demo Company vs the real org), and writes the token
file the app then keeps alive by refreshing.

Setup (once, at https://developer.xero.com/app/manage):
  1. New app -> type "Web app".
  2. Add redirect URI:  http://localhost:8400/callback
  3. Note the client id and generate a client secret.

Usage:
    python scripts/xero_bootstrap.py [--out docker-mnt/config/xero_token.json]

Re-running this script re-authorizes from scratch, which is the fix whenever
the app reports the connection expired (refresh token unused for 60+ days).
NOTE: once the app has refreshed the token, any older copy of the token file
is dead (Xero rotates refresh tokens) - always re-run this script rather than
re-copying an old file.
"""

import argparse
import base64
import json
import os
import secrets
import sys
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests

AUTHORIZE_URL = "https://login.xero.com/identity/connect/authorize"
TOKEN_URL = "https://identity.xero.com/connect/token"
CONNECTIONS_URL = "https://api.xero.com/connections"
# Apps created on/after 2 March 2026 must use granular scopes: the broad
# accounting.transactions scope is rejected with invalid_scope.
# accounting.invoices covers creating and emailing invoices;
# accounting.settings.read is needed to look up branding themes by name.
SCOPES = "offline_access accounting.invoices accounting.contacts accounting.settings.read"
REDIRECT_PORT = 8400
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"
UK_TZ = ZoneInfo("Europe/London")


def get_credentials() -> tuple[str, str]:
    """Client id/secret from env, .env file, or interactive prompt"""
    try:
        from dotenv import load_dotenv  # pylint: disable=import-outside-toplevel

        load_dotenv(".env")
    except ImportError:
        pass

    client_id = os.getenv("XERO_CLIENT_ID") or input("Xero client ID: ").strip()
    client_secret = os.getenv("XERO_CLIENT_SECRET") or input("Xero client secret: ").strip()
    if not client_id or not client_secret:
        sys.exit("Client ID and secret are required.")
    return client_id, client_secret


class _CallbackHandler(BaseHTTPRequestHandler):
    """One-shot handler that captures the OAuth code from the redirect"""

    result = {}

    def do_GET(self):  # pylint: disable=invalid-name
        """Record the query params from Xero's redirect and show a done page"""
        query = parse_qs(urlparse(self.path).query)
        _CallbackHandler.result = {k: v[0] for k, v in query.items()}
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h2>Authorised - close this tab and return to the terminal.</h2>")

    def log_message(self, format, *args):  # pylint: disable=redefined-builtin
        pass  # keep the terminal clean


def get_auth_code(client_id: str) -> str:
    """Open the Xero consent page and wait for the redirect"""
    state = secrets.token_urlsafe(16)
    url = AUTHORIZE_URL + "?" + urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPES,
            "state": state,
        }
    )

    print("\nOpening browser for Xero consent...")
    print(f"If it does not open, visit:\n  {url}\n")
    webbrowser.open(url)

    server = HTTPServer(("localhost", REDIRECT_PORT), _CallbackHandler)
    server.timeout = 300
    print(f"Waiting for redirect on {REDIRECT_URI} (5 minute timeout)...")
    server.handle_request()
    server.server_close()

    result = _CallbackHandler.result
    if "error" in result:
        sys.exit(f"Xero returned an error: {result['error']}")
    if result.get("state") != state:
        sys.exit("State mismatch - possible tampering, aborting.")
    if "code" not in result:
        sys.exit("Timed out waiting for the redirect.")
    return result["code"]


def exchange_code(client_id: str, client_secret: str, code: str) -> dict:
    """Swap the auth code for the initial token set"""
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp = requests.post(
        TOKEN_URL,
        headers={"Authorization": f"Basic {basic}"},
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI},
        timeout=30,
    )
    if resp.status_code != 200:
        sys.exit(f"Token exchange failed [{resp.status_code}]: {resp.text}")
    return resp.json()


def pick_tenant(access_token: str) -> dict:
    """List authorised organisations and let the user choose one"""
    resp = requests.get(
        CONNECTIONS_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=30
    )
    if resp.status_code != 200:
        sys.exit(f"Failed to list Xero connections [{resp.status_code}]: {resp.text}")

    tenants = resp.json()
    if not tenants:
        sys.exit("No organisations authorised - did you select one on the consent page?")

    print("\nAuthorised organisations:")
    for i, t in enumerate(tenants, 1):
        print(f"  {i}. {t.get('tenantName')} ({t.get('tenantType')})")

    if len(tenants) == 1:
        choice = 1
        print("Only one organisation - selecting it.")
    else:
        choice = int(input(f"Pick the organisation to use [1-{len(tenants)}]: "))

    return tenants[choice - 1]


def main():
    """Run the consent flow and write the token file"""
    parser = argparse.ArgumentParser(description="One-time Xero OAuth bootstrap")
    parser.add_argument(
        "--out",
        default="docker-mnt/config/xero_token.json",
        help="Where to write the token file (default: docker-mnt/config/xero_token.json)",
    )
    args = parser.parse_args()

    client_id, client_secret = get_credentials()
    code = get_auth_code(client_id)
    tokens = exchange_code(client_id, client_secret, code)
    tenant = pick_tenant(tokens["access_token"])

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "access_token": tokens["access_token"],
                "refresh_token": tokens["refresh_token"],
                "expires_at": (
                    datetime.now(UK_TZ) + timedelta(seconds=tokens["expires_in"] - 30)
                ).isoformat(),
                "tenant_id": tenant["tenantId"],
                "tenant_name": tenant.get("tenantName", ""),
                "last_refreshed": datetime.now(UK_TZ).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"\nToken written to {out_path}")
    print(f"Connected organisation: {tenant.get('tenantName')}")
    print(
        "\nNext steps:\n"
        "  1. Ensure XERO_ENABLED=True, XERO_CLIENT_ID and XERO_CLIENT_SECRET are set in\n"
        "     the app's .env / .env.production.\n"
        "  2. If the app runs on the NAS, make sure the token file is in\n"
        "     docker-mnt/config/ and restart the container.\n"
        "  3. Check the Admin page shows Xero as connected."
    )


if __name__ == "__main__":
    main()
