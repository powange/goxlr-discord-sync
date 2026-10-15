"""Discord OAuth2: token exchange, refresh, and the browser fallback flow."""

import asyncio
import http.server
import logging
import secrets
import threading
import time
import urllib.parse
import webbrowser

import requests

from . import secure_store
from .i18n import tr

log = logging.getLogger(__name__)

API = "https://discord.com/api/v10"
SCOPES = ["rpc", "rpc.voice.read", "rpc.voice.write", "identify"]
HTTP_TIMEOUT = 15
REFRESH_MARGIN = 300  # refresh tokens that expire within 5 minutes


class AuthError(Exception):
    """The user has to authorize the application (again)."""


class TransientAuthError(Exception):
    """Network or Discord-side failure: retry later, do not prompt the user."""


def _token_request(data: dict) -> dict:
    try:
        response = requests.post(f"{API}/oauth2/token", data=data, timeout=HTTP_TIMEOUT)
    except requests.RequestException as e:
        raise TransientAuthError(f"Cannot reach Discord: {e.__class__.__name__}") from e
    if response.status_code == 429 or response.status_code >= 500:
        raise TransientAuthError(f"Discord answered HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code != 200 or "access_token" not in body:
        reason = body.get("error_description") or body.get("error") or f"HTTP {response.status_code}"
        raise AuthError(f"Discord refused the token request: {reason}")
    body["expires_at"] = time.time() + int(body.get("expires_in") or 0)
    return body


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str | None) -> dict:
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "authorization_code",
        "code": code,
    }
    if redirect_uri:
        data["redirect_uri"] = redirect_uri
    return _token_request(data)


def refresh_token(client_id: str, client_secret: str, refresh: str) -> dict:
    return _token_request({
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh,
    })


def check_credentials(client_id: str, client_secret: str) -> tuple[bool, str]:
    """Validate an application ID/secret pair without any user interaction."""
    try:
        response = requests.post(
            f"{API}/oauth2/token",
            data={"grant_type": "client_credentials", "scope": "identify"},
            auth=(client_id, client_secret),
            timeout=HTTP_TIMEOUT,
        )
    except requests.RequestException as e:
        return False, tr("Cannot reach Discord ({error}). Check your internet connection.", error=e.__class__.__name__)
    if response.status_code == 200:
        return True, tr("The Client ID and Client Secret are valid.")
    if response.status_code == 401:
        return False, tr("Discord rejected this Client ID / Client Secret pair. Copy both values again.")
    return False, tr("Discord answered HTTP {status}. Try again in a moment.", status=response.status_code)


def token_is_fresh(token: dict | None, now: float | None = None) -> bool:
    if not token or not token.get("access_token"):
        return False
    expires_at = token.get("expires_at")
    if not isinstance(expires_at, (int, float)):
        return False  # tokens saved by older versions: refresh them
    return expires_at - REFRESH_MARGIN > (now if now is not None else time.time())


class TokenManager:
    """Keeps a valid access token, refreshing it only when needed."""

    def __init__(self, store=secure_store):
        self.store = store

    def clear(self) -> None:
        self.store.set_token(None)

    async def access_token(self, client_id: str, client_secret: str, force_refresh: bool = False) -> str | None:
        """A usable access token, or None when the user has to authorize.

        Raises TransientAuthError when Discord cannot be reached.
        """
        token = self.store.get_token()
        if token and not force_refresh and token_is_fresh(token):
            return token["access_token"]
        if not token or not token.get("refresh_token"):
            return None
        try:
            new_token = await asyncio.to_thread(refresh_token, client_id, client_secret, token["refresh_token"])
        except AuthError as e:
            log.warning("Token refresh refused: %s", e)
            self.clear()
            return None
        self.store.set_token(new_token)
        log.info("Discord token refreshed")
        return new_token["access_token"]

    async def exchange(self, client_id: str, client_secret: str, code: str, redirect_uris: list[str | None]) -> str:
        """Exchange an authorization code, trying each redirect URI in turn."""
        last_error: Exception | None = None
        for uri in redirect_uris:
            try:
                token = await asyncio.to_thread(exchange_code, client_id, client_secret, code, uri)
            except AuthError as e:
                last_error = e
                continue
            self.store.set_token(token)
            return token["access_token"]
        raise last_error or AuthError("No redirect URI to try")


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    expected_state = ""
    timeout = 5  # an idle browser connection must not block the wait loop
    result: dict = {}

    def do_GET(self):  # noqa: N802 (http.server API)
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        params = urllib.parse.parse_qs(parsed.query)
        state = params.get("state", [""])[0]
        if not secrets.compare_digest(state.encode(), type(self).expected_state.encode()):
            self._reply(400, "Invalid request", "This authorization link is not the one GoXLR Discord Sync opened.")
            return
        if "error" in params:
            type(self).result["error"] = params.get("error_description", params["error"])[0]
            self._reply(200, "Authorization cancelled", "You can close this window.")
        elif "code" in params:
            type(self).result["code"] = params["code"][0]
            self._reply(200, "Authorization successful", "You can close this window and return to GoXLR Discord Sync.")
        else:
            self._reply(400, "Invalid request", "No authorization code received.")

    def _reply(self, status: int, title: str, text: str) -> None:
        body = (
            "<!doctype html><meta charset='utf-8'><title>GoXLR Discord Sync</title>"
            "<body style='font-family:system-ui,sans-serif;text-align:center;padding-top:60px'>"
            f"<h1>{title}</h1><p>{text}</p></body>"
        ).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002
        pass


def browser_authorize(client_id: str, redirect_uri: str, port: int, timeout: float = 180,
                      cancel: threading.Event | None = None) -> str:
    """Blocking browser OAuth flow (run it in a thread). Returns the code."""

    class Handler(_CallbackHandler):
        expected_state = secrets.token_urlsafe(24)
        result: dict = {}

    try:
        server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        raise AuthError(f"Port {port} is already in use, cannot receive the Discord callback") from e
    server.timeout = 0.5
    query = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": Handler.expected_state,
        "prompt": "consent",
    })
    try:
        log.info("Opening the browser for Discord authorization")
        webbrowser.open(f"https://discord.com/oauth2/authorize?{query}")
        deadline = time.monotonic() + timeout
        while not Handler.result and time.monotonic() < deadline:
            if cancel is not None and cancel.is_set():
                raise AuthError("Authorization cancelled")
            server.handle_request()
    finally:
        server.server_close()
    if "code" in Handler.result:
        return Handler.result["code"]
    if "error" in Handler.result:
        raise AuthError(f"Authorization denied: {Handler.result['error']}")
    raise AuthError(f"No answer from the browser within {int(timeout)} seconds")
