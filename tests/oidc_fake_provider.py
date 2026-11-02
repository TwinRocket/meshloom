"""A small OpenID Connect provider on loopback, for ``tests/test_auth_oidc.py``.

It serves discovery, JWKS, the token endpoint (authorization code, PKCE S256, client
secret by HTTP Basic or in the form) and userinfo over plain http on ``127.0.0.1``,
so the instance reaches it through the real egress guard (``allow_private_network``).
The browser's visit to the authorization endpoint is simulated by ``authorize()``,
which reads the parameters of the URL the instance redirected to and returns the
code the provider would send back. Each test can change the claims of the next ID
token (``claims``), or sign it with another key or algorithm (``sign_with``).
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from joserfc import jwt
from joserfc.jwk import RSAKey

CLIENT_ID = "meshloom-test"
CLIENT_SECRET = "s3cr3t-value-for-tests"


@dataclass
class _Grant:
    client_id: str
    redirect_uri: str
    nonce: str
    challenge: str
    claims: dict[str, Any]


@dataclass
class FakeProvider:
    port: int = 0
    key: RSAKey = field(default_factory=lambda: RSAKey.generate_key(2048, parameters={"kid": "k1"}))
    # Claims of the next ID token, over the defaults (sub, iss, aud, nonce, times).
    claims: dict[str, Any] = field(default_factory=lambda: {"sub": "user-1"})
    userinfo: dict[str, Any] | None = None
    # Sign with this key instead (a token the published keys do not verify).
    sign_with: RSAKey | None = None
    grants: dict[str, _Grant] = field(default_factory=dict)
    token_requests: list[dict[str, Any]] = field(default_factory=list)

    @property
    def issuer(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def discovery(self) -> dict[str, Any]:
        return {
            "issuer": self.issuer,
            "authorization_endpoint": f"{self.issuer}/auth",
            "token_endpoint": f"{self.issuer}/token",
            "jwks_uri": f"{self.issuer}/jwks",
            "userinfo_endpoint": f"{self.issuer}/userinfo",
            "response_types_supported": ["code"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "code_challenge_methods_supported": ["S256"],
        }

    def authorize(self, location: str) -> tuple[str, str, str]:
        """The provider's answer to the browser: (callback path, code, state)."""
        params = {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}
        assert params["response_type"] == "code"
        assert params["code_challenge_method"] == "S256"
        assert "openid" in params["scope"].split()
        code = secrets.token_urlsafe(16)
        self.grants[code] = _Grant(
            client_id=params["client_id"],
            redirect_uri=params["redirect_uri"],
            nonce=params["nonce"],
            challenge=params["code_challenge"],
            claims=dict(self.claims),
        )
        return urlsplit(params["redirect_uri"]).path, code, params["state"]

    def id_token(self, grant: _Grant) -> str:
        now = int(time.time())
        claims = {
            "iss": self.issuer,
            "aud": grant.client_id,
            "nonce": grant.nonce,
            "iat": now,
            "exp": now + 300,
            **grant.claims,
        }
        claims = {k: v for k, v in claims.items() if v is not None}
        key = self.sign_with or self.key
        return jwt.encode({"alg": "RS256", "kid": "k1"}, claims, key)

    def token(self, form: dict[str, str], authorization: str | None) -> tuple[int, dict]:
        self.token_requests.append({"form": dict(form), "basic": authorization is not None})
        client_id, client_secret = form.get("client_id"), form.get("client_secret")
        if authorization and authorization.startswith("Basic "):
            raw = base64.b64decode(authorization[6:]).decode()
            user, _, password = raw.partition(":")
            client_id, client_secret = unquote(user), unquote(password)
        if client_id != CLIENT_ID or client_secret != CLIENT_SECRET:
            return 401, {"error": "invalid_client"}
        grant = self.grants.pop(form.get("code", ""), None)
        if grant is None or form.get("grant_type") != "authorization_code":
            return 400, {"error": "invalid_grant"}
        if form.get("redirect_uri") != grant.redirect_uri:
            return 400, {"error": "invalid_grant"}
        verifier = form.get("code_verifier", "")
        digest = hashlib.sha256(verifier.encode()).digest()
        if base64.urlsafe_b64encode(digest).rstrip(b"=").decode() != grant.challenge:
            return 400, {"error": "invalid_grant"}
        return 200, {
            "access_token": "at-" + secrets.token_urlsafe(8),
            "token_type": "Bearer",
            "id_token": self.id_token(grant),
        }


def _handler(provider: FakeProvider) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:  # quiet
            pass

        def _json(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/.well-known/openid-configuration":
                self._json(200, provider.discovery())
            elif path == "/jwks":
                self._json(200, {"keys": [provider.key.as_dict(private=False)]})
            elif path == "/userinfo":
                if provider.userinfo is None:
                    self._json(404, {})
                else:
                    self._json(200, provider.userinfo)
            else:
                self._json(404, {})

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
            if urlsplit(self.path).path != "/token":
                self._json(404, {})
                return
            status, body = provider.token(form, self.headers.get("Authorization"))
            self._json(status, body)

    return Handler


@contextmanager
def running_provider() -> Iterator[FakeProvider]:
    provider = FakeProvider()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(provider))
    provider.port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield provider
    finally:
        server.shutdown()
        server.server_close()
