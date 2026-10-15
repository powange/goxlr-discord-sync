import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest
import requests

from goxlr_discord_sync import auth


class MemoryStore:
    def __init__(self, token=None):
        self.token = token

    def get_token(self):
        return self.token

    def set_token(self, token):
        self.token = token


def test_token_is_fresh():
    now = 1000.0
    assert auth.token_is_fresh({"access_token": "a", "expires_at": now + 3600}, now)
    assert not auth.token_is_fresh({"access_token": "a", "expires_at": now + 60}, now)
    assert not auth.token_is_fresh({"access_token": "a"}, now)  # saved by version 1.x
    assert not auth.token_is_fresh(None, now)


async def test_fresh_token_is_reused_without_http(monkeypatch):
    monkeypatch.setattr(auth, "refresh_token", lambda *a: pytest.fail("no refresh expected"))
    store = MemoryStore({"access_token": "a", "refresh_token": "r", "expires_at": time.time() + 3600})
    assert await auth.TokenManager(store).access_token("id", "secret") == "a"


async def test_expired_token_is_refreshed(monkeypatch):
    monkeypatch.setattr(auth, "refresh_token", lambda cid, sec, ref: {"access_token": "b", "refresh_token": "r2",
                                                                        "expires_at": time.time() + 3600})
    store = MemoryStore({"access_token": "a", "refresh_token": "r", "expires_at": 0})
    assert await auth.TokenManager(store).access_token("id", "secret") == "b"
    assert store.token["refresh_token"] == "r2"


async def test_refused_refresh_clears_token(monkeypatch):
    def refuse(*a):
        raise auth.AuthError("invalid_grant")

    monkeypatch.setattr(auth, "refresh_token", refuse)
    store = MemoryStore({"access_token": "a", "refresh_token": "r", "expires_at": 0})
    assert await auth.TokenManager(store).access_token("id", "secret") is None
    assert store.token is None


async def test_network_error_keeps_token(monkeypatch):
    def offline(*a):
        raise auth.TransientAuthError("offline")

    monkeypatch.setattr(auth, "refresh_token", offline)
    store = MemoryStore({"access_token": "a", "refresh_token": "r", "expires_at": 0})
    with pytest.raises(auth.TransientAuthError):
        await auth.TokenManager(store).access_token("id", "secret")
    assert store.token is not None


async def test_exchange_tries_redirect_uris_in_order(monkeypatch):
    tried = []

    def exchange(cid, sec, code, uri):
        tried.append(uri)
        if uri is not None:
            raise auth.AuthError("redirect_uri mismatch")
        return {"access_token": "t", "expires_at": time.time() + 10}

    monkeypatch.setattr(auth, "exchange_code", exchange)
    store = MemoryStore()
    assert await auth.TokenManager(store).exchange("id", "s", "c", ["http://x", None]) == "t"
    assert tried == ["http://x", None]
    assert store.token["access_token"] == "t"


class FakeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (FakeResponse(500, {}), auth.TransientAuthError),
        (FakeResponse(429, {}), auth.TransientAuthError),
        (FakeResponse(400, {"error": "invalid_grant"}), auth.AuthError),
    ],
)
def test_token_request_errors(monkeypatch, response, error):
    monkeypatch.setattr(auth.requests, "post", lambda *a, **k: response)
    with pytest.raises(error):
        auth.refresh_token("id", "s", "r")


def test_token_request_network_error(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("no route")

    monkeypatch.setattr(auth.requests, "post", boom)
    with pytest.raises(auth.TransientAuthError):
        auth.refresh_token("id", "s", "r")


def test_token_request_success_sets_expiry(monkeypatch):
    monkeypatch.setattr(auth.requests, "post", lambda *a, **k: FakeResponse(
        200, {"access_token": "a", "refresh_token": "r", "expires_in": 604800}))
    token = auth.refresh_token("id", "s", "r")
    assert token["expires_at"] > time.time() + 600000


def _free_port():
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _browser_simulator(monkeypatch, make_query):
    """Replace webbrowser.open by a client that calls the redirect URL."""

    def fake_open(url):
        state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]

        def visit():
            for query in make_query(state):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?{query}", timeout=5).read()
                except urllib.error.HTTPError:
                    pass

        threading.Thread(target=visit, daemon=True).start()
        return True

    port = _free_port()
    monkeypatch.setattr(auth.webbrowser, "open", fake_open)
    return port


def test_browser_flow_checks_state(monkeypatch):
    port = _browser_simulator(monkeypatch, lambda state: ["code=forged&state=wrong", f"code=good&state={state}"])
    assert auth.browser_authorize("id", f"http://127.0.0.1:{port}/callback", port, timeout=5) == "good"


def test_browser_flow_reports_denial(monkeypatch):
    port = _browser_simulator(monkeypatch, lambda state: [f"error=access_denied&state={state}"])
    with pytest.raises(auth.AuthError, match="denied"):
        auth.browser_authorize("id", f"http://127.0.0.1:{port}/callback", port, timeout=5)


def test_browser_flow_times_out(monkeypatch):
    port = _browser_simulator(monkeypatch, lambda state: [])
    with pytest.raises(auth.AuthError, match="No answer"):
        auth.browser_authorize("id", f"http://127.0.0.1:{port}/callback", port, timeout=1)


def test_browser_flow_port_in_use(monkeypatch):
    import socket

    monkeypatch.setattr(auth.webbrowser, "open", lambda url: True)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        with pytest.raises(auth.AuthError, match="already in use"):
            auth.browser_authorize("id", "http://x", port, timeout=1)
